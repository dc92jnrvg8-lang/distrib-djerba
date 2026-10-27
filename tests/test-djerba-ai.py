#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de l'agent Djerba (djerba_ai), sans réseau ni modèle réel.

* registre : au moins 220 capacités, noms uniques, schémas valides ;
* sécurité : paramètres hostiles refusés, aucun shell, un paramètre = un argument ;
* mémoire SQLite : discussions, souvenirs, apprentissage, incidents ;
* agent : boucle d'appels d'outils face à un faux serveur Ollama (HTTP local),
  confirmation refusée, mode dégradé sans moteur ;
* démon root : JSON-RPC sur socket UNIX réelle, SO_PEERCRED, autorisation ;
* bac à sable des correctifs (root uniquement, DJERBA_AI_ROOT_TESTS=1) :
  correctif cassé annulé, correctif valide déployé puis retiré ;
* Studio : génération hors ligne (Snake PyQt6, calculatrice HTML5,
  chronomètre QML), dépôt Git et lanceur du menu ;
* interface : chargement réel du QML (Qt hors écran).

Usage : python3 tests/test-djerba-ai.py   (depuis la racine du dépôt)
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = ROOT / "packages" / "djerba-tools" / "root"
sys.path.insert(0, str(PACKAGE_ROOT / "usr" / "lib" / "python3" / "dist-packages"))
os.environ["PATH"] = f"{PACKAGE_ROOT / 'usr' / 'bin'}:{os.environ['PATH']}"

# Environnement utilisateur isolé (configuration, mémoire, Studio).
TEMP_HOME = Path(tempfile.mkdtemp(prefix="djerba-ai-test-"))
for variable, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                      ("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache")):
    os.environ[variable] = str(TEMP_HOME / sub)
os.environ["HOME"] = str(TEMP_HOME)

from djerba_ai.core import ipc, sysd  # noqa: E402
from djerba_ai.core.agent import Agent  # noqa: E402
from djerba_ai.core.config import Config  # noqa: E402
from djerba_ai.core.memory import Memory  # noqa: E402
from djerba_ai.tools import CATEGORIES, registry  # noqa: E402
from djerba_ai.tools.base import CommandTool, ToolError  # noqa: E402


class FakeOllama(http.server.BaseHTTPRequestHandler):
    """Faux serveur Ollama : premier tour = appel d'outil, second = réponse."""

    script: list[dict] = []
    requests: list[dict] = []

    def log_message(self, *_args) -> None:
        pass

    def _send(self, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/api/version":
            self._send(b'{"version": "0.0-test"}')
        elif self.path == "/api/tags":
            self._send(json.dumps({"models": [{"name": "test:1b", "size": 1e9, "details": {}}]}).encode())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        step = FakeOllama.script.pop(0) if FakeOllama.script else {"content": "Terminé."}
        lines = []
        if step.get("content"):
            for word in step["content"].split(" "):
                lines.append({"message": {"role": "assistant", "content": word + " "}, "done": False})
        lines.append({"message": {"role": "assistant", "content": "",
                                  "tool_calls": step.get("tool_calls", [])}, "done": True})
        self._send("\n".join(json.dumps(line) for line in lines).encode(), "application/x-ndjson")


def start_fake_ollama() -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


class RegistryTests(unittest.TestCase):
    def test_count_and_categories(self) -> None:
        reg = registry()
        self.assertGreaterEqual(len(reg), 220)
        for category in CATEGORIES:
            self.assertGreaterEqual(len(reg.by_category(category)), 15, category)

    def test_schemas(self) -> None:
        for tool in registry().tools.values():
            schema = tool.schema()
            json.dumps(schema)
            self.assertTrue(tool.description, tool.name)
            for name in tool.required:
                self.assertIn(name, tool.parameters, tool.name)
            self.assertRegex(tool.name, r"^[a-z][a-z0-9_]{2,40}$")

    def test_game_optimizer_contract(self) -> None:
        tool = registry().get("game_optimizer")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.category, "performance")
        result = tool.run({"mode": "status"})
        self.assertEqual(set(result), {"success", "message", "data"})

    def test_search_relevance(self) -> None:
        reg = registry()
        cases = {"active le mode anonyme": "anonsurf_start", "installe steam": "steam_install",
                 "crée un jeu snake": "app_generator", "mon disque est plein": "disk_usage",
                 "le son est trop fort": "volume_set"}
        for query, expected in cases.items():
            self.assertIn(expected, [tool.name for tool in reg.search(query, 3)], query)


class SecurityTests(unittest.TestCase):
    def test_hostile_parameters_rejected(self) -> None:
        reg = registry()
        hostile = [("apt_show", {"package": "-oAPT::Get=x"}), ("apt_show", {"package": "vim; rm -rf /"}),
                   ("service_restart", {"unit": "../../etc/passwd"}), ("smart_health", {"device": "/etc/shadow"}),
                   ("ufw_allow_port", {"port": 70000}), ("apt_show", {"package": "x", "extra": 1}),
                   ("hostname_set", {"name": "$(reboot)"})]
        for name, params in hostile:
            with self.assertRaises(ToolError, msg=f"{name} {params}"):
                reg.get(name).validate(params)

    def test_parameter_is_single_argument(self) -> None:
        tool = registry().get("apt_search")
        argv = tool.build_argv(tool.validate({"query": "jeu de course"}))
        self.assertEqual(argv[-1], "jeu de course")

    def test_root_tools_never_from_plugins(self) -> None:
        plugins = Path(os.environ["XDG_DATA_HOME"]) / "djerba" / "plugins"
        plugins.mkdir(parents=True, exist_ok=True)
        (plugins / "escalade.py").write_text(
            "from djerba_ai.tools.base import BaseTool, ok\n"
            "class Escalade(BaseTool):\n    name = 'escalade'\n    category = 'system'\n"
            "    description = 'test'\n    requires_root = True\n"
            "    def execute(self, params, verbose=False):\n        return ok('x')\n")
        reg = registry(include_plugins=True, approved=["escalade"])
        tool = reg.get("plugin.escalade")
        self.assertIsNotNone(tool)
        self.assertFalse(tool.requires_root)
        dispatcher = sysd.Dispatcher(authorizer=lambda *_: True)
        with self.assertRaises(ipc.RPCError):
            dispatcher.handle("sys.exec_tool", {"name": "plugin.escalade"}, os.getpid(), os.getuid())

    def test_command_tools_do_not_use_a_shell(self) -> None:
        for tool in registry().tools.values():
            if isinstance(tool, CommandTool):
                self.assertNotIn(tool.argv[0], ("sh", "bash", "dash", "zsh"), tool.name)


class MemoryTests(unittest.TestCase):
    def test_memory(self) -> None:
        memory = Memory(Path(tempfile.mkdtemp()) / "memory.db")
        self.addCleanup(memory.close)
        conversation = memory.new_conversation("Test")
        memory.add_message(conversation, "user", "bonjour")
        self.assertEqual(memory.messages(conversation)[0]["content"], "bonjour")
        memory.remember("jeu", "joue surtout à Elden Ring")
        self.assertEqual(len(memory.facts("Elden")), 1)
        run_id = memory.record_run("disk_usage", "mon disque est plein ce matin", {}, True, "ok")
        memory.feedback(run_id, 1)
        self.assertGreater(memory.tool_scores()["disk_usage"], 0)
        self.assertEqual(memory.examples("le disque est plein encore")[0]["tool"], "disk_usage")
        first, new = memory.record_incident("abc", "djerba-apps", "/usr/bin/djerba-apps", "Traceback 1")
        second, again = memory.record_incident("abc", "djerba-apps", "/usr/bin/djerba-apps", "Traceback 2")
        self.assertTrue(new)
        self.assertFalse(again)
        self.assertEqual(first, second)
        self.assertEqual(memory.incident(first)["occurrences"], 2)


class AgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config()
        self.memory = Memory(Path(tempfile.mkdtemp()) / "memory.db")
        self.agent = Agent(self.config, registry(), self.memory)
        self.addCleanup(self.memory.close)

    def test_tool_loop(self) -> None:
        self.config.set("ollama_endpoint", start_fake_ollama())
        FakeOllama.script = [
            {"content": "", "tool_calls": [{"function": {"name": "os_info", "arguments": {}}}]},
            {"content": "Vous utilisez Djerba OS."},
        ]
        events = []
        answer = self.agent.run("quelle version du système ?", self.memory.new_conversation("t"),
                                lambda kind, payload: events.append((kind, payload)), lambda *_: True)
        kinds = [kind for kind, _ in events]
        self.assertIn("tool_start", kinds)
        result = next(payload["result"] for kind, payload in events if kind == "tool_result")
        self.assertTrue(result["success"], result)
        self.assertIn("Djerba OS", answer)
        sent = FakeOllama.requests[-1]
        self.assertEqual(sent["messages"][-1]["role"], "tool")
        self.assertIn("Configuration actuelle", sent["messages"][0]["content"])
        self.assertTrue(any(tool["function"]["name"] == "tools_search" for tool in sent["tools"]))

    def test_dangerous_action_needs_confirmation(self) -> None:
        self.config.set("ollama_endpoint", start_fake_ollama())
        FakeOllama.script = [
            {"tool_calls": [{"function": {"name": "kill_process", "arguments": {"pid": 999999}}}]},
            {"content": "D'accord, je n'ai rien fait."},
        ]
        asked = []
        events = []
        self.agent.run("ferme le processus 999999", self.memory.new_conversation("t"),
                       lambda kind, payload: events.append((kind, payload)),
                       lambda tool, params, description: asked.append(tool) or False)
        self.assertEqual(asked, ["kill_process"])
        result = next(payload["result"] for kind, payload in events if kind == "tool_result")
        self.assertIn("refusée", result["message"])

    def test_degraded_mode(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.config.set("ollama_endpoint", f"http://127.0.0.1:{port}")
        events = []
        answer = self.agent.run("active le mode anonyme", self.memory.new_conversation("t"),
                                lambda kind, payload: events.append((kind, payload)), lambda *_: False)
        self.assertIn("n'est pas disponible", answer)
        suggestions = next(payload["items"] for kind, payload in events if kind == "suggestions")
        self.assertIn("anonsurf_start", [item["tool"] for item in suggestions])


class SysdTests(unittest.TestCase):
    def serve(self, allow: bool) -> tuple[str, sysd.Server]:
        path = str(Path(tempfile.mkdtemp()) / "sysd.sock")
        server = sysd.Server(None, path, sysd.Dispatcher(authorizer=lambda pid, uid, action: allow))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        return path, server

    def test_protocol_and_authorization(self) -> None:
        path, server = self.serve(allow=False)
        try:
            pong = ipc.call("sys.ping", {}, socket_path=Path(path), timeout=10)
            self.assertEqual(pong["uid"], os.getuid())
            with self.assertRaises(ipc.RPCError) as denied:
                ipc.call("sys.exec_tool", {"name": "apt_clean"}, socket_path=Path(path), timeout=10)
            self.assertEqual(denied.exception.code, ipc.NOT_AUTHORIZED)
            with self.assertRaises(ipc.RPCError) as unknown:
                ipc.call("sys.exec_tool", {"name": "os_info"}, socket_path=Path(path), timeout=10)
            self.assertEqual(unknown.exception.code, ipc.METHOD_NOT_FOUND)
            with self.assertRaises(ipc.RPCError) as missing:
                ipc.call("sys.nope", {}, socket_path=Path(path), timeout=10)
            self.assertEqual(missing.exception.code, ipc.METHOD_NOT_FOUND)
        finally:
            server.shutdown()
            server.server_close()

    def test_authorized_root_tool_returns_result(self) -> None:
        path, server = self.serve(allow=True)
        try:
            result = ipc.call("sys.exec_tool", {"name": "swappiness_set", "params": {"value": 9999}},
                              socket_path=Path(path), timeout=10)
            self.assertFalse(result["success"])
            self.assertIn("maximum", result["message"])
        finally:
            server.shutdown()
            server.server_close()

    def test_malformed_request(self) -> None:
        path, server = self.serve(allow=True)
        try:
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(path)
                client.sendall(b"pas du json\n")
                reply = json.loads(client.makefile().readline())
            self.assertEqual(reply["error"]["code"], ipc.PARSE_ERROR)
        finally:
            server.shutdown()
            server.server_close()


@unittest.skipUnless(os.geteuid() == 0 and os.environ.get("DJERBA_AI_ROOT_TESTS") == "1",
                     "bac à sable : root et DJERBA_AI_ROOT_TESTS=1 requis (conteneur de construction)")
class PatchSandboxTests(unittest.TestCase):
    TARGET = Path("/usr/bin/djerba-selftest")
    ORIGINAL = "#!/usr/bin/env bash\nset -euo pipefail\nif [[ \"${1:-}\" == --help ]]; then echo aide; exit 0; fi\n" \
               "echo \"$((1 / 0))\"\n"

    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp())
        sysd.STATE_DIR, sysd.SANDBOX = base, base / "sandbox"
        sysd.DEPLOYED = base / "deployed.json"
        sysd.OVERRIDE_DIR = base / "override"
        sysd.OVERRIDE_DIR.mkdir()
        self.TARGET.write_text(self.ORIGINAL)
        self.TARGET.chmod(0o755)

    def tearDown(self) -> None:
        self.TARGET.unlink(missing_ok=True)

    def test_broken_patch_is_rolled_back(self) -> None:
        result = sysd.test_patch(str(self.TARGET), "#!/usr/bin/env bash\nif then fi\n", "test")
        self.assertFalse(result["success"])
        self.assertTrue(result["rolled_back"])
        mirror = sysd.SANDBOX / "usr" / "bin" / self.TARGET.name
        self.assertEqual(mirror.read_text(), self.ORIGINAL)

    def test_valid_patch_deploy_and_rollback(self) -> None:
        fixed = self.ORIGINAL.replace("$((1 / 0))", "ok")
        result = sysd.test_patch(str(self.TARGET), fixed, "division par zéro")
        self.assertTrue(result["success"], result)
        deployed = sysd.deploy_patch(result["patch_id"])
        override = sysd.OVERRIDE_DIR / self.TARGET.name
        self.assertTrue(deployed["success"])
        self.assertEqual(override.read_text(), fixed)
        self.assertEqual(subprocess.run([str(override)], capture_output=True, text=True).stdout.strip(), "ok")
        sysd.rollback_patch(self.TARGET.name)
        self.assertFalse(override.exists())


@unittest.skipUnless(shutil.which("git") and __import__("importlib").util.find_spec("PyQt6"),
                     "git et PyQt6 requis")
class StudioTests(unittest.TestCase):
    def setUp(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        Config().set("ollama_endpoint", f"http://127.0.0.1:{port}")  # aucun moteur : modèles intégrés

    def generate(self, instruction: str, kind: str) -> dict:
        result = registry().get("app_generator").run({"instruction": instruction})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["data"]["kind"], kind)
        app_dir = Path(result["data"]["path"])
        self.assertTrue((app_dir / "requirements.txt").is_file())
        log = subprocess.run(["git", "-C", str(app_dir), "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertIn("Création", log)
        launcher = Path(result["data"]["launcher"]).read_text()
        self.assertIn(f"Exec=djerba-ai run-app {result['data']['slug']}", launcher)
        return result

    def test_offline_templates(self) -> None:
        self.generate("Crée un jeu Snake en PyQt6", "pyqt6")
        self.generate("Crée une calculatrice HTML5", "html")
        self.generate("Fais-moi un chronomètre", "qml")
        listed = registry().get("apps_generated_list").run({})
        self.assertEqual(len(listed["data"]["apps"]), 3)

    def test_unknown_request_without_engine(self) -> None:
        result = registry().get("app_generator").run({"instruction": "Crée un traitement de texte"})
        self.assertFalse(result["success"])
        self.assertIn("Moteur d'IA indisponible", result["message"])


@unittest.skipUnless(__import__("importlib").util.find_spec("PyQt6"), "PyQt6 requis")
class InterfaceTests(unittest.TestCase):
    def test_qml_loads(self) -> None:
        script = (
            "import sys\n"
            "from PyQt6.QtCore import QTimer, QUrl\n"
            "from PyQt6.QtGui import QGuiApplication\n"
            "from PyQt6.QtQml import QQmlApplicationEngine\n"
            "from djerba_ai.gui import app as gui\n"
            "application = QGuiApplication(sys.argv)\n"
            "backend = gui.Backend('chat', application)\n"
            "engine = QQmlApplicationEngine()\n"
            "engine.addImageProvider('icon', gui.IconProvider())\n"
            "engine.rootContext().setContextProperty('backend', backend)\n"
            "errors = []\n"
            "engine.warnings.connect(lambda ws: errors.extend(w.toString() for w in ws))\n"
            "engine.load(QUrl.fromLocalFile(str(gui.QML_DIR / 'main.qml')))\n"
            "root = engine.rootObjects()\n"
            "def finish():\n"
            "    for index in range(6):\n"
            "        root[0].setProperty('page', index)\n"
            "        application.processEvents()\n"
            "    print('\\n'.join(errors))\n"
            "    application.exit(1 if errors or not root else 0)\n"
            "QTimer.singleShot(2500, finish)\n"
            "sys.exit(application.exec())\n")
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                           PYTHONPATH=str(PACKAGE_ROOT / "usr" / "lib" / "python3" / "dist-packages"))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
                                env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
