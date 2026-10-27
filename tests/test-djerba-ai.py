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
  chronomètre QML), dépôt Git et lanceur du menu, isolation systemd-run ;
* configuration centrale TOML (0600, vue publique sans secret, validation) ;
* nettoyage des secrets avant toute écriture (mémoire, audit) ;
* cerveaux : repli transparent sur Ollama local (distant injoignable ou lent),
  API en ligne relayée par le démon root (la clé ne quitte jamais root) ;
* journal d'audit JSON, refus des applications isolées ;
* capacités « os » (fichiers, permissions, chemins vitaux, recherche
  sémantique), auto-extension (3 tentatives) et bac à sable des greffons ;
* raccourcis KGlobalAccel (kglobalaccelrc), détection du rendu 3D / 2D ;
* interface : chargement réel du QML (Qt hors écran), Spotlight compris.

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
# Configuration système, journal d'audit, socket du démon et venv : tous temporaires.
SYSTEM_DIR = TEMP_HOME / "system"
os.environ["DJERBA_CONFIG_DIR"] = str(SYSTEM_DIR / "etc-djerba")
os.environ["DJERBA_AUDIT_LOG"] = str(SYSTEM_DIR / "log" / "audit.log")
os.environ["DJERBA_AI_SYSD_SOCKET"] = str(SYSTEM_DIR / "sysd.sock")
os.environ["DJERBA_AI_VENV"] = str(SYSTEM_DIR / "venv")
os.environ["XDG_RUNTIME_DIR"] = str(SYSTEM_DIR / "run")
(SYSTEM_DIR / "run").mkdir(parents=True, mode=0o700)

import time  # noqa: E402
import tomllib  # noqa: E402

from djerba_ai.core import audit, embeddings, extend, gpu, ipc, llm, shortcuts, sysd  # noqa: E402
from djerba_ai.core.agent import Agent  # noqa: E402
from djerba_ai.core.config import Config, ConfigError, Store, validate  # noqa: E402
from djerba_ai.core.sanitize import sanitize  # noqa: E402
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


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class SlowOllama(FakeOllama):
    """Serveur distant qui répond trop tard (au-delà du délai de 5 s… réduit à 1 s ici)."""

    def do_GET(self) -> None:
        time.sleep(3)
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass  # le client a déjà basculé sur le modèle local


class FakeOpenAI(http.server.BaseHTTPRequestHandler):
    """Faux service compatible OpenAI (réponses SSE), pour le relais du démon root."""

    authorizations: list[str] = []

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        FakeOpenAI.authorizations.append(self.headers.get("Authorization", ""))
        payload = json.dumps({"data": [{"id": "gpt-test"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        FakeOpenAI.authorizations.append(self.headers.get("Authorization", ""))
        self.rfile.read(int(self.headers["Content-Length"]))
        chunks = [{"choices": [{"delta": {"content": word}}]} for word in ("Bonjour ", "depuis ", "le ", "nuage.")]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())


def serve(handler) -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def reset_system_config() -> None:
    """Retire la configuration « système » temporaire (retour au repli par utilisateur)."""
    for name in ("config.toml", "config.public.json"):
        (Path(os.environ["DJERBA_CONFIG_DIR"]) / name).unlink(missing_ok=True)


class FakeConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def provider_config(**values) -> FakeConfig:
    config = FakeConfig({"llm.local.endpoint": "", "llm.local.model": "test:1b", "llm.context_window": 4096,
                         "llm.provider": "tailscale", "llm.remote_timeout": 1, "llm.fallback_to_local": True,
                         "llm.tailscale.host": "127.0.0.1", "llm.tailscale.port": closed_port(),
                         "llm.tailscale.model": "qwen2.5:32b", "llm.cloud.service": "openai",
                         "llm.cloud.model": "gpt-test"})
    config.update(values)
    return config


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
            dispatcher.dispatch("sys.exec_tool", {"name": "plugin.escalade"}, os.getpid(), os.getuid())

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
        self.config.set("llm.local.endpoint", start_fake_ollama())
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
        self.config.set("llm.local.endpoint", start_fake_ollama())
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
        self.config.set("llm.local.endpoint", f"http://127.0.0.1:{port}")
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
        Config().set("llm.local.endpoint", f"http://127.0.0.1:{port}")  # aucun moteur : modèles intégrés

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


class ConfigTests(unittest.TestCase):
    def test_toml_store_permissions_and_public_view(self) -> None:
        base = Path(tempfile.mkdtemp())
        store = Store(base / "config.toml", base / "config.public.json")
        store.ensure()
        store.set("llm.provider", "tailscale")
        store.set("llm.tailscale.host", "mac-mini")
        store.set("llm.cloud.api_key", "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789")
        self.assertEqual((base / "config.toml").stat().st_mode & 0o777, 0o600)
        self.assertEqual((base / "config.public.json").stat().st_mode & 0o777, 0o644)
        data = tomllib.loads((base / "config.toml").read_text())
        self.assertEqual(data["llm"]["provider"], "tailscale")
        self.assertEqual(data["llm"]["tailscale"]["host"], "mac-mini")
        self.assertEqual(data["shortcuts"]["spotlight"], "Meta+Space")
        self.assertEqual(data["shortcuts"]["clipboard"], "Meta+Shift+C")
        self.assertEqual(data["system"]["sandbox_timeout"], 15)
        self.assertEqual(data["system"]["disk_alert_percent"], 10)
        public = (base / "config.public.json").read_text()
        self.assertNotIn("sk-proj", public)
        self.assertTrue(json.loads(public)["llm.cloud.api_key_set"])
        self.assertEqual(store.load()["llm.cloud.api_key"], "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789")

    def test_validation(self) -> None:
        for key, value in (("llm.provider", "autre"), ("system.sandbox_timeout", 60), ("system.sandbox_attempts", 9),
                           ("llm.cloud.endpoint", "http://exemple.com/v1"), ("shortcuts.spotlight", "Meta+Space; x"),
                           ("inconnu.clé", 1)):
            with self.assertRaises(ConfigError, msg=f"{key}={value}"):
                validate(key, value)
        self.assertEqual(validate("llm.cloud.endpoint", "http://127.0.0.1:8080/v1"), "http://127.0.0.1:8080/v1")

    def test_user_view_falls_back_without_daemon(self) -> None:
        reset_system_config()
        config = Config()
        config.set("agent.web_search", False)
        self.assertFalse(Config().get("agent.web_search"))
        with self.assertRaises(ConfigError):
            config.set("llm.cloud.api_key", "sk-secret-sans-demon-0123456789")
        history = Memory().settings_history()
        self.assertEqual(history[0]["key"], "agent.web_search")


class SanitizeTests(unittest.TestCase):
    def test_secrets_are_masked(self) -> None:
        secrets = ["sk-proj-AbCdEfGhIjKlMnOpQrStUvWx0123456789", "AIzaSyA1234567890abcdefghijklmnopqrstuv",
                   "ghp_" + "a" * 36, "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
                   "tskey-auth-kAbCdEf1CNTRL-0123456789abcdef"]
        for secret in secrets:
            self.assertNotIn(secret, sanitize(f"ma clé est {secret} merci"), secret)
        self.assertNotIn("hunter2", sanitize("password=hunter2"))
        self.assertNotIn("hunter2", sanitize("mot de passe : hunter2"))
        self.assertNotIn("s3cr3t", sanitize("curl https://alice:s3cr3t@example.org/"))
        self.assertEqual(sanitize("rien de secret ici"), "rien de secret ici")

    def test_memory_never_stores_secrets(self) -> None:
        memory = Memory(Path(tempfile.mkdtemp()) / "memory.db")
        self.addCleanup(memory.close)
        conversation = memory.new_conversation("clé")
        memory.add_message(conversation, "user", "voici ma clé sk-proj-AbCdEfGhIjKlMnOpQrStUvWx0123456789")
        memory.remember("api", "token=ghp_" + "b" * 36)
        self.assertNotIn("sk-proj", memory.messages(conversation)[0]["content"])
        self.assertNotIn("ghp_", memory.facts()[0]["content"])


class ProviderTests(unittest.TestCase):
    def test_unreachable_remote_falls_back_to_local(self) -> None:
        FakeOllama.script = [{"content": "réponse locale"}]
        notices = []
        manager = llm.ProviderManager(provider_config(**{"llm.local.endpoint": start_fake_ollama()}), notices.append)
        self.assertIsInstance(manager.primary, llm.RemoteTailscaleProvider)
        reply = manager.chat([{"role": "user", "content": "bonjour"}])
        self.assertIn("réponse locale", reply["content"])
        self.assertTrue(manager.fell_back)
        self.assertEqual(len(notices), 1)
        self.assertIn("modèle local", notices[0])

    def test_slow_remote_falls_back_within_timeout(self) -> None:
        FakeOllama.script = [{"content": "repli rapide"}]
        slow = serve(SlowOllama)
        config = provider_config(**{"llm.local.endpoint": start_fake_ollama(),
                                    "llm.tailscale.port": int(slow.rsplit(":", 1)[1])})
        started = time.monotonic()
        reply = llm.ProviderManager(config).chat([{"role": "user", "content": "bonjour"}])
        self.assertIn("repli rapide", reply["content"])
        self.assertLess(time.monotonic() - started, 3)

    def test_no_fallback_when_disabled(self) -> None:
        config = provider_config(**{"llm.local.endpoint": start_fake_ollama(), "llm.fallback_to_local": False})
        with self.assertRaises(llm.LLMError):
            llm.ProviderManager(config).chat([{"role": "user", "content": "bonjour"}])

    def test_local_warmup_uses_keep_alive(self) -> None:
        FakeOllama.requests.clear()
        llm.LocalOllamaProvider(start_fake_ollama(), "test:1b").warmup()
        self.assertEqual(FakeOllama.requests[-1]["keep_alive"], "15m")

    def test_cloud_api_through_root_daemon(self) -> None:
        store = Store()
        store.set("llm.cloud.service", "custom")
        store.set("llm.cloud.endpoint", serve(FakeOpenAI) + "/v1")
        store.set("llm.cloud.api_key", "sk-test-0123456789abcdefghijklmnop")
        path = Path(os.environ["DJERBA_AI_SYSD_SOCKET"])
        path.unlink(missing_ok=True)
        server = sysd.Server(None, str(path), sysd.Dispatcher(authorizer=lambda *_: True))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        try:
            provider = llm.CloudAPIProvider("custom", "gpt-test")
            self.assertTrue(provider.health()["ok"])
            tokens = []
            reply = provider.chat([{"role": "user", "content": "bonjour"}], on_token=tokens.append)
            self.assertEqual(reply["content"], "Bonjour depuis le nuage.")
            self.assertEqual(len(tokens), 4)  # jetons relayés en continu par le démon
            self.assertIn("Bearer sk-test-0123456789abcdefghijklmnop", FakeOpenAI.authorizations)
            public = Config()
            self.assertTrue(public.get("llm.cloud.api_key_set"))
            self.assertNotIn("llm.cloud.api_key", public.values)
            log = Path(os.environ["DJERBA_AUDIT_LOG"]).read_text()
            self.assertNotIn("sk-test-0123456789", log)
        finally:
            server.shutdown()
            server.server_close()
            path.unlink(missing_ok=True)
            reset_system_config()


class AuditTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_system_config()

    def test_actions_and_refusals_are_logged(self) -> None:
        log = Path(os.environ["DJERBA_AUDIT_LOG"])
        refused = sysd.Dispatcher(authorizer=lambda *_: False)
        with self.assertRaises(ipc.RPCError):
            refused.dispatch("sys.exec_tool", {"name": "apt_clean", "params": {}}, os.getpid(), os.getuid())
        allowed = sysd.Dispatcher(authorizer=lambda *_: True)
        allowed.dispatch("sys.exec_tool", {"name": "swappiness_set", "params": {"value": 9999}},
                         os.getpid(), os.getuid())
        allowed.dispatch("config.set", {"key": "llm.cloud.api_key", "value": "sk-audit-0123456789abcdefghij"},
                         os.getpid(), os.getuid())
        entries = audit.tail(10)
        self.assertEqual(log.stat().st_mode & 0o777, 0o640)
        self.assertTrue(any(e["event"] == "refus" and e.get("tool") == "apt_clean" for e in entries))
        self.assertTrue(any(e["event"] == "action" and e.get("tool") == "swappiness_set"
                            and e["success"] is False for e in entries))
        self.assertNotIn("sk-audit", log.read_text())
        for entry in entries:
            self.assertEqual(entry["uid"], os.getuid())
            self.assertIn("time", entry)

    def test_isolated_apps_are_refused(self) -> None:
        self.assertTrue(sysd.APP_SCOPE.search("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                                              "djerba-app-snake-4242.scope\n"))
        self.assertFalse(sysd.APP_SCOPE.search("0::/user.slice/user-1000.slice/session-2.scope\n"))
        original = sysd.isolated
        sysd.isolated = lambda _pid: True
        try:
            with self.assertRaises(ipc.RPCError) as refused:
                sysd.Dispatcher(authorizer=lambda *_: True).dispatch("sys.ping", {}, os.getpid(), os.getuid())
            self.assertEqual(refused.exception.code, ipc.NOT_AUTHORIZED)
        finally:
            sysd.isolated = original
        self.assertEqual(audit.tail(1)[0]["event"], "refus")

    def test_generated_apps_run_in_isolated_scope(self) -> None:
        from djerba_ai.tools import devtools
        argv = devtools.isolation_command("snake", ["djerba-ai", "run-app", "snake"])
        self.assertEqual(argv[:3], ["systemd-run", "--user", "--scope"])
        self.assertTrue(any(arg.startswith("--unit=djerba-app-snake-") for arg in argv))
        self.assertTrue(sysd.APP_SCOPE.search(f"/{argv[5].split('=', 1)[1]}.scope"))
        os.environ["DJERBA_SECRET_TEST"] = "1"
        try:
            self.assertNotIn("DJERBA_SECRET_TEST", devtools.app_environment())
        finally:
            del os.environ["DJERBA_SECRET_TEST"]


class OsToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = registry()
        self.base = Path(tempfile.mkdtemp(dir=TEMP_HOME))

    def run_tool(self, name: str, **params) -> dict:
        return self.reg.get(name).run(params)

    def test_files_and_permissions(self) -> None:
        target = self.base / "notes" / "todo.txt"
        self.assertTrue(self.run_tool("os_file_write", path=str(target), content="acheter du pain\n")["success"])
        self.assertTrue(self.run_tool("os_file_write", path=str(target), content="réparer le vélo\n",
                                      append=True)["success"])
        self.assertIn("vélo", self.run_tool("os_file_read", path=str(target))["data"]["content"])
        listing = self.run_tool("os_dir_list", path=str(target.parent))["data"]["entries"]
        self.assertEqual([entry["name"] for entry in listing], ["todo.txt"])
        self.assertTrue(self.run_tool("os_chmod", path=str(target), mode="u+x,go-r")["success"])
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)
        self.assertTrue(self.run_tool("os_chmod", path=str(target), mode="644")["success"])
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)
        self.assertFalse(self.run_tool("os_chmod", path=str(target), mode="4755")["success"])
        copy = self.base / "copie.txt"
        self.assertTrue(self.run_tool("os_path_copy", source=str(target), target=str(copy))["success"])
        moved = self.base / "archive" / "copie.txt"
        self.run_tool("os_dir_create", path=str(moved.parent))
        self.assertTrue(self.run_tool("os_path_move", source=str(copy), target=str(moved.parent))["success"])
        self.assertTrue(moved.exists())
        self.assertFalse(self.run_tool("os_path_delete", path=str(target.parent))["success"])  # non vide
        self.assertTrue(self.run_tool("os_path_delete", path=str(target.parent), recursive=True)["success"])
        found = self.run_tool("os_file_search", pattern="*.txt", path=str(self.base))["data"]["files"]
        self.assertEqual(found, [str(moved)])

    def test_vital_paths_refused(self) -> None:
        for name, params in (("os_path_delete", {"path": "/etc"}), ("os_path_delete", {"path": "/usr/bin/ls"}),
                             ("os_chmod", {"path": "/etc/shadow", "mode": "777"}),
                             ("os_file_write", {"path": "/boot/grub/grub.cfg", "content": "x"}),
                             ("os_path_delete", {"path": str(TEMP_HOME / ".." / ".." / "proc")}),
                             ("os_sys_chown", {"path": "/", "user": "nobody"})):
            result = self.run_tool(name, **params)
            self.assertFalse(result["success"], f"{name} {params}")
        link = self.base / "lien-etc"
        link.symlink_to("/etc")
        self.assertIn("vital", self.run_tool("os_path_delete", path=str(link))["message"])

    def test_package_parameters(self) -> None:
        tool = self.reg.get("os_pkg_install")
        self.assertTrue(tool.requires_root)
        self.assertEqual(tool.validate({"name": "vlc"})["manager"], "apt")
        self.assertEqual(tool.validate({"name": "org.mozilla.firefox", "manager": "flatpak"})["manager"], "flatpak")
        for hostile in ("vlc; reboot", "-oAPT::x=1", "../../x", "$(id)"):
            with self.assertRaises(ToolError):
                tool.validate({"name": hostile})
        for name in ("os_pkg_install", "os_pkg_remove", "os_pkg_purge", "os_pkg_update", "os_sys_chown"):
            self.assertTrue(self.reg.get(name).requires_root, name)

    def test_semantic_search(self) -> None:
        Config().set("llm.local.endpoint", f"http://127.0.0.1:{closed_port()}")  # hachage local
        docs = self.base / "documents"
        docs.mkdir()
        (docs / "facture-edf.txt").write_text("Facture d'électricité EDF de mars : 84 euros, consommation 310 kWh.")
        (docs / "recette.md").write_text("Recette du couscous djerbien au poisson, semoule fine et harissa.")
        (docs / "sauvegarde.sh").write_text("#!/bin/sh\nrsync -a ~/Documents /media/disque/sauvegarde\n")
        index = self.run_tool("os_index_build", path=str(docs))
        self.assertTrue(index["success"], index)
        self.assertEqual(index["data"]["indexed"], 3)
        self.assertEqual(self.run_tool("os_index_build", path=str(docs))["data"]["unchanged"], 3)
        for query, expected in (("ma facture d'électricité", "facture-edf.txt"),
                                ("recette de couscous", "recette.md"), ("script de sauvegarde rsync", "sauvegarde.sh")):
            results = self.run_tool("os_semantic_search", query=query)["data"]["results"]
            self.assertTrue(results[0]["path"].endswith(expected), (query, results))
        self.assertEqual(len(embeddings.hashed("test")), 512)


class ExtendTests(unittest.TestCase):
    PLUGIN = ("from djerba_ai.tools.base import BaseTool, ok\n\n\n"
              "class Inverse(BaseTool):\n    name = 'texte_inverse'\n    category = 'devtools'\n"
              "    description = 'Inverse un texte.'\n"
              "    parameters = {'texte': {'type': 'string', 'description': 'texte'}}\n"
              "    required = ('texte',)\n\n"
              "    def execute(self, params, verbose=False):\n        return ok(params['texte'][::-1])\n\n"
              "    def selftest(self):\n        return self.run({'texte': 'abc'})['message'] == 'cba'\n")

    class Client:
        def __init__(self, replies: list[str]) -> None:
            self.replies = replies
            self.calls = 0

        def chat(self, messages, tools=None, on_token=None):
            self.calls += 1
            return {"content": self.replies.pop(0), "tool_calls": []}

    def test_three_attempts_then_hot_integration(self) -> None:
        verdicts = [{"success": False, "rolled_back": True, "checks": [{"check": "pyflakes", "success": False,
                                                                        "detail": "import inutilisé"}]},
                    {"success": False, "rolled_back": True, "checks": [{"check": "auto-test", "success": False}]},
                    {"success": True, "checks": [], "tools": ["texte_inverse"], "content": self.PLUGIN}]
        calls = []
        original = extend.ipc.call
        def fake_call(method, params, timeout=0):
            if method != "sys.test_plugin":  # réglages : pas de démon dans ce test (repli local)
                raise ipc.RPCError(ipc.INTERNAL_ERROR, "démon absent")
            calls.append((method, params))
            return verdicts.pop(0)

        extend.ipc.call = fake_call
        try:
            client = self.Client([f"```python\n{self.PLUGIN}```"] * 3)
            result = extend.create_capability("texte_inverse", "inverser un texte", client=client)
        finally:
            extend.ipc.call = original
        self.assertTrue(result["success"], result)
        self.assertEqual(result["attempts"], 3)
        self.assertEqual([method for method, _ in calls], ["sys.test_plugin"] * 3)
        self.assertIn("texte_inverse", Config().get("agent.plugins_approved"))
        agent = Agent(Config(), registry(), Memory(Path(tempfile.mkdtemp()) / "m.db"))
        self.assertIn("plugin.texte_inverse", agent.reload_plugins())
        tool = agent.registry.get("plugin.texte_inverse")
        self.assertTrue(tool.dangerous)
        self.assertFalse(tool.requires_root)
        self.assertEqual(tool.run({"texte": "Djerba"})["message"], "abrejD")

    def test_abort_after_three_failures(self) -> None:
        original = extend.ipc.call
        extend.ipc.call = lambda *_args, **_kwargs: {"success": False, "rolled_back": True, "checks": []}
        try:
            client = self.Client(["```python\nx = (\n```"] * 3)
            result = extend.create_capability("capacite_ratee", "rien", client=client)
        finally:
            extend.ipc.call = original
        self.assertFalse(result["success"])
        self.assertEqual(client.calls, 3)
        self.assertFalse((Path(os.environ["XDG_DATA_HOME"]) / "djerba" / "plugins" / "capacite_ratee.py").exists())


@unittest.skipUnless(os.geteuid() == 0 and os.environ.get("DJERBA_AI_ROOT_TESTS") == "1" and shutil.which("setpriv"),
                     "bac à sable : root et DJERBA_AI_ROOT_TESTS=1 requis (conteneur de construction)")
class PluginSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp())
        base.chmod(0o755)
        sysd.STATE_DIR, sysd.SANDBOX = base, base / "sandbox"

    def test_valid_plugin_committed(self) -> None:
        result = sysd.test_plugin(1000, "texte_inverse", ExtendTests.PLUGIN)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["tools"], ["texte_inverse"])
        log = subprocess.run(["git", "-C", str(sysd.SANDBOX), "log", "--oneline"], capture_output=True, text=True)
        self.assertIn("texte_inverse", log.stdout)

    def test_broken_and_endless_plugins_rolled_back(self) -> None:
        broken = sysd.test_plugin(1000, "cassee", "def x(:\n")
        self.assertFalse(broken["success"])
        self.assertTrue(broken["rolled_back"])
        endless = ExtendTests.PLUGIN.replace("return self.run({'texte': 'abc'})['message'] == 'cba'",
                                             "while True:\n            pass")
        started = time.monotonic()
        result = sysd.test_plugin(1000, "sans_fin", endless)
        self.assertFalse(result["success"])
        self.assertLess(time.monotonic() - started, 30)  # 15 s par exécution au plus
        self.assertFalse((sysd.SANDBOX / "plugins" / "1000" / "sans_fin.py").exists())


class ShortcutTests(unittest.TestCase):
    def test_kglobalaccelrc_and_key_codes(self) -> None:
        path = Path(tempfile.mkdtemp()) / "kglobalaccelrc"
        path.write_text("[services][org.kde.konsole.desktop]\n_launch=Ctrl+Alt+T\n")
        shortcuts.write_config("spotlight", "Meta+Space", path)
        shortcuts.write_config("clipboard", "Meta+Shift+C", path)
        shortcuts.write_config("spotlight", "Meta+J", path)
        self.assertEqual(shortcuts.read_config(path), {"spotlight": "Meta+J", "clipboard": "Meta+Shift+C"})
        self.assertIn("_launch=Ctrl+Alt+T", path.read_text())
        self.assertEqual(path.read_text().count("[services][djerba-ai-spotlight.desktop]"), 1)
        self.assertEqual(shortcuts.key_code("Meta+Space"), 0x10000020)
        self.assertEqual(shortcuts.key_code("Meta+Shift+C"), 0x12000043)
        for invalid in ("C", "Shift+C", "Meta+Meta+X", "Meta+Space; rm", ""):
            with self.assertRaises(shortcuts.ShortcutError, msg=invalid):
                shortcuts.validate(invalid)
        result = shortcuts.apply("spotlight", "Meta+Space", live=False)
        self.assertFalse(result["applied"])
        self.assertEqual(shortcuts.read_config()["spotlight"], "Meta+Space")

    def test_desktop_files_declare_default_shortcuts(self) -> None:
        apps = PACKAGE_ROOT / "usr" / "share" / "applications"
        self.assertIn("X-KDE-Shortcuts=Meta+Space", (apps / "djerba-ai-spotlight.desktop").read_text())
        self.assertIn("X-KDE-Shortcuts=Meta+Shift+C", (apps / "djerba-ai-clipboard.desktop").read_text())


class GpuTests(unittest.TestCase):
    def fake_drm(self, driver: str) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "drivers" / driver).mkdir(parents=True)
        (root / "card0" / "device").mkdir(parents=True)
        (root / "card0" / "device" / "driver").symlink_to(root / "drivers" / driver)
        (root / "card0-HDMI-A-1").mkdir()
        return root

    def test_detection(self) -> None:
        original, backend = gpu.DRM, os.environ.pop("QT_QUICK_BACKEND", None)
        try:
            gpu.DRM = self.fake_drm("amdgpu")
            self.assertTrue(gpu.detect()["accelerated"])
            gpu.DRM = self.fake_drm("simpledrm")
            self.assertFalse(gpu.detect()["accelerated"])
            gpu.DRM = Path(tempfile.mkdtemp())
            self.assertFalse(gpu.detect()["accelerated"])
        finally:
            gpu.DRM = original
            if backend is not None:
                os.environ["QT_QUICK_BACKEND"] = backend


class InstanceTests(unittest.TestCase):
    def test_forward_and_parse(self) -> None:
        from djerba_ai.gui import instance
        self.assertEqual(instance.parse(["--spotlight"])["mode"], "spotlight")
        self.assertEqual(instance.parse(["--page", "models"])["page"], "settings")
        path = Path(tempfile.mkdtemp()) / "gui.sock"
        self.assertFalse(instance.forward(["--spotlight"], path))
        received = []
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            server.listen(1)

            def answer() -> None:
                connection, _ = server.accept()
                with connection:
                    received.append(json.loads(connection.makefile().readline()))
                    connection.sendall(b"ok\n")

            thread = threading.Thread(target=answer)
            thread.start()
            self.assertTrue(instance.forward(["--clipboard"], path))
            thread.join(5)
        self.assertEqual(received, [{"argv": ["--clipboard"]}])


@unittest.skipUnless(__import__("importlib").util.find_spec("PyQt6"), "PyQt6 requis")
class InterfaceTests(unittest.TestCase):
    def test_qml_loads(self) -> None:
        script = (
            "import sys\n"
            "from PyQt6.QtCore import QTimer, QUrl\n"
            "from PyQt6.QtWidgets import QApplication\n"
            "from PyQt6.QtQml import QQmlApplicationEngine\n"
            "from djerba_ai.gui import app as gui\n"
            "application = QApplication(sys.argv)\n"
            "backend = gui.Backend('chat', True, application)\n"
            "application.aboutToQuit.connect(backend.shutdown)\n"
            "engine = QQmlApplicationEngine()\n"
            "engine.addImageProvider('icon', gui.IconProvider())\n"
            "engine.rootContext().setContextProperty('backend', backend)\n"
            "errors = []\n"
            "engine.warnings.connect(lambda ws: errors.extend(w.toString() for w in ws))\n"
            "for name in ('main.qml', 'Spotlight.qml'):\n"
            "    engine.load(QUrl.fromLocalFile(str(gui.QML_DIR / name)))\n"
            "root = engine.rootObjects()\n"
            "def finish():\n"
            "    main, spotlight = root[0], root[1]\n"
            "    for index in range(6):\n"
            "        main.setProperty('page', index)\n"
            "        application.processEvents()\n"
            "    backend.spotlightRequested.emit('texte copié', 'clipboard')\n"
            "    application.processEvents()\n"
            "    if not spotlight.property('visible'): errors.append('Spotlight non affiché')\n"
            "    backend.spotlightRequested.emit('', 'ask')\n"
            "    application.processEvents()\n"
            "    print('\\n'.join(errors))\n"
            "    application.exit(1 if errors or len(root) != 2 else 0)\n"
            "QTimer.singleShot(2500, finish)\n"
            "sys.exit(application.exec())\n")
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                           DJERBA_AI_NO_TRAY="1",
                           PYTHONPATH=str(PACKAGE_ROOT / "usr" / "lib" / "python3" / "dist-packages"))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
                                env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == "__main__":
    unittest.main(verbosity=2)
