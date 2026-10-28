# SPDX-License-Identifier: GPL-3.0-or-later
"""djerba-ai-sysd — démon root de l'agent Djerba (JSON-RPC sur socket UNIX).

Activé par socket (/run/djerba-ai/sysd.sock), il s'arrête après quelques
minutes d'inactivité. Chaîne de contrôle de chaque requête :

1. **analyse déterministe** : enveloppe JSON-RPC 2.0 et paramètres validés par
   des schémas Pydantic stricts (core.schemas) ; tout champ inconnu, type
   approximatif ou méthode absente est rejeté avant tout traitement ;
2. **identité noyau** : SO_PEERCRED (pid, uid, gid) ; tout processus d'une
   application du Studio ou d'une tâche de fond (cgroups djerba-app-*,
   djerba-ghost-*) est refusé ;
3. **appelant de confiance** (core.trust) : interpréteur système en mode
   isolé exécutant un point d'entrée signé de Djerba — jamais un script
   quelconque de l'utilisateur ;
4. **autorisation polkit** pour *ce processus* (mot de passe administrateur,
   mémorisé quelques minutes pour org.djerba.ai.exec ; jamais pour un
   déploiement de correctif) ;
5. **audit immuable** (core.audit) : la demande est inscrite *avant*
   exécution, le résultat ensuite, dans un journal chaîné en ajout seul.

Le code non fiable (correctifs, capacités générées et leurs tests) ne
s'exécute que dans le runtime hybride (core.sandbox : espaces de noms,
seccomp-bpf, cgroups v2, gVisor si présent).

Méthodes :
  sys.ping                       version (sans autorisation)
  sys.metrics                    télémétrie du démon (requêtes, refus, latences)
  sys.sandbox_probe {backend}    vérifie l'isolation réelle du bac à sable
  sys.exec_tool {name, params, verbose}
                                 exécute un outil *intégré* marqué requires_root
                                 (les greffons utilisateur ne sont jamais chargés ici)
  sys.test_patch {target, content, reason}
                                 bac à sable Git /var/lib/djerba-ai/sandbox : le
                                 correctif proposé pour un script /usr/bin/djerba-*
                                 est vérifié (syntaxe, shellcheck, exécution de
                                 --help sous l'utilisateur nobody, comportement
                                 identique à l'original) ; en cas d'échec,
                                 « git reset --hard » annule tout
  sys.deploy_patch {patch_id}    déploiement atomique dans /usr/local/bin (prioritaire
                                 dans le PATH, sans toucher aux fichiers du paquet)
  sys.rollback_patch {name}      retire un correctif déployé
  sys.list_patches               correctifs proposés et déployés
  sys.install_ollama             installe le moteur Ollama officiel (archive vérifiée
                                 par SHA-256) et son service systemd
  sys.test_plugin {name, content, tests, requirements}
                                 auto-extension : la capacité écrite par l'agent et ses
                                 tests Pytest sont vérifiés dans le bac à sable (syntaxe,
                                 pyflakes, chargement, tests : 15 s au plus par test),
                                 dépendances dans un venv temporaire ; en cas d'échec,
                                 git reset --hard, venv supprimé, dossier purgé
  sys.venv_install {packages}    dépendances Python dans le venv interne
                                 /usr/share/djerba-ai/venv (jamais de pip global, PEP 668)
  config.get / config.set {key, value}
                                 /etc/djerba/config.toml (0600) et sa vue publique
  llm.cloud_chat {messages, tools}
                                 requête OpenAI / Gemini avec la clé d'API lue ici :
                                 les jetons sont relayés en notifications « llm.token »
  llm.cloud_health, llm.cloud_models
  audit.tail {lines}             fin du journal d'audit (/var/log/djerba/audit.log)
  audit.verify                   contrôle de la chaîne de hachages du journal
  security.status                IDS (alertes, adresses bloquées), AppArmor, accès réseau
  security.unblock {ip}          débloque une adresse bloquée par l'IDS
  security.apparmor_load {kind, name, network}
                                 génère et charge le profil AppArmor d'un code généré
  session.report {desktop, component, detail}
                                 consigne une défaillance de session graphique (extension,
                                 shell, adaptateur) dans /var/log/djerba/<bureau>-crash.log
  tx.list {limit, since} / tx.show {id}
                                 journal de transactions (core.transactions)
  tx.undo {id, force} / tx.undo_since {since}
                                 annulation depuis la sauvegarde (instantané Btrfs ou copie)

Toute méthode qui modifie le système (outil root qui n'est pas en lecture
seule, correctifs, Ollama, venv, réglages, AppArmor) s'exécute dans une
transaction : sauvegarde préalable, vérification par sondes, annulation
automatique en cas de régression, moteur d'IA et session d'origine consignés.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from djerba_ai import __version__
from djerba_ai.core import audit, ipc, llm, sandbox, telemetry, trust
from djerba_ai.core.config import CLOUD_ENDPOINTS, SCHEMA, ConfigError, Store, protected, public_view
from djerba_ai.core.config import validate as validate_setting
from djerba_ai.core.schemas import parse_rpc

STATE_DIR = Path("/var/lib/djerba-ai")
SANDBOX = STATE_DIR / "sandbox"
DEPLOYED = STATE_DIR / "deployed.json"
OVERRIDE_DIR = Path("/usr/local/bin")
TARGET = re.compile(r"^/usr/bin/djerba-[a-z0-9-]{1,40}$")
IDLE_TIMEOUT = 300

ACTION_EXEC = "org.djerba.ai.exec"
ACTION_PATCH = "org.djerba.ai.patch"
ACTION_DEPLOY = "org.djerba.ai.deploy"
ACTION_SETUP = "org.djerba.ai.setup"
ACTION_CONFIGURE = "org.djerba.ai.configure"
ACTION_CLOUD = "org.djerba.ai.cloud"
ACTION_AUDIT = "org.djerba.ai.audit"
ACTION_EXTEND = "org.djerba.ai.extend"
ACTION_UNDO = "org.djerba.ai.undo"
#: confirmation hors bande (mode « prudent ») et action irréversible (mode « total »)
ACTION_CONFIRM = "org.djerba.ai.confirm"
ACTION_CRITICAL = "org.djerba.ai.critical"
#: changement de la politique elle-même (mode, plafonds, reprise après pause)
ACTION_POLICY = "org.djerba.ai.policy"
#: outils dont l'unité ciblée doit rester active (sonde « après » de la transaction)
TARGETED_TOOLS = {"service_restart", "service_enable"}
#: outils qui touchent l'amorçage (sauvegarde de /boot et de l'ESP sans Btrfs)
BOOT_TOOLS = {"grub_timeout", "grub_update", "initramfs_update", "apt_full_upgrade", "os_pkg_update"}

VENV = Path(os.environ.get("DJERBA_AI_VENV", "/usr/share/djerba-ai/venv"))
PLUGIN_NAME = re.compile(r"^[a-z0-9_]{3,48}$")
REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(\[[A-Za-z0-9,_-]+\])?"
                         r"((==|>=|<=|~=|!=|<|>)[A-Za-z0-9.*+!-]{1,40})?$")
APP_SCOPE = re.compile(r"/djerba-(?:app|ghost)-[^/\n]*\.(?:scope|service)")
#: méthodes sans effet ni donnée sensible : non journalisées
QUIET = {"sys.ping", "sys.metrics", "sys.list_patches", "config.get", "audit.tail", "audit.verify", "security.status",
         "llm.cloud_health", "llm.cloud_models", "session.report", "tx.list", "tx.show",
         "policy.check", "policy.status", "engines.list", "llm.engine_chat", "llm.engine_health",
         "mcp.verify", "mcp.list"}

Authorizer = Callable[[int, int, str], bool]
Emit = Callable[[str, dict], None]


def sandbox_timeout() -> int:
    """Durée maximale de toute exécution dans le bac à sable (15 s par défaut)."""
    return int(Store().load().get("system.sandbox_timeout", 15))


def isolated(pid: int) -> bool:
    """Vrai si le processus appartient à une application isolée du Studio."""
    try:
        return bool(APP_SCOPE.search(Path(f"/proc/{pid}/cgroup").read_text()))
    except OSError:
        return True  # processus disparu ou illisible : refus par prudence


def log(message: str) -> None:
    print(f"djerba-ai-sysd: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- polkit
def process_start_time(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return int(stat.rsplit(")", 1)[1].split()[19])


def polkit_authorize(pid: int, uid: int, action: str) -> bool:
    """CheckAuthorization pour le processus appelant (interaction autorisée)."""
    try:
        import dbus  # python3-dbus
        bus = dbus.SystemBus()
        authority = dbus.Interface(bus.get_object("org.freedesktop.PolicyKit1",
                                                  "/org/freedesktop/PolicyKit1/Authority"),
                                   "org.freedesktop.PolicyKit1.Authority")
        subject = ("unix-process", {"pid": dbus.UInt32(pid), "start-time": dbus.UInt64(process_start_time(pid)),
                                    "uid": dbus.Int32(uid)})
        is_authorized, _challenge, _details = authority.CheckAuthorization(
            subject, action, dbus.Dictionary({}, signature="ss"), dbus.UInt32(1), "", timeout=300)
        return bool(is_authorized)
    except Exception as exc:  # noqa: BLE001 - toute erreur polkit vaut refus
        log(f"polkit : {exc}")
        return False


# ---------------------------------------------------------------- Bac à sable des correctifs
def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(SANDBOX), *arguments], capture_output=True, text=True, check=check,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(STATE_DIR), "LC_ALL": "C.UTF-8",
                               "GIT_AUTHOR_NAME": "Djerba", "GIT_AUTHOR_EMAIL": "djerba@localhost",
                               "GIT_COMMITTER_NAME": "Djerba", "GIT_COMMITTER_EMAIL": "djerba@localhost"})


def ensure_sandbox() -> None:
    SANDBOX.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (SANDBOX / ".git").is_dir():
        git("init", "-q", "-b", "main")
        (SANDBOX / "README").write_text("Bac à sable des correctifs de l'agent Djerba (géré automatiquement).\n")
        git("add", "README")
        git("commit", "-q", "-m", "Initialisation du bac à sable")


def effective(target: str) -> Path:
    override = OVERRIDE_DIR / Path(target).name
    return override if override.exists() else Path(target)


def interpreter(content: str) -> str:
    first = content.splitlines()[0] if content else ""
    if "python3" in first:
        return "python3"
    if "bash" in first or first.startswith("#!/bin/sh"):
        return "bash"
    raise ValueError("langage non pris en charge (bash ou python3 attendu)")


NOBODY = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--no-new-privs", "--inh-caps=-all"]


def sandbox_settings() -> dict:
    values = Store().load()
    return {"backend": values.get("sandbox.backend", "auto"), "memory_mb": int(values.get("sandbox.memory_mb", 512)),
            "cpu_percent": int(values.get("sandbox.cpu_percent", 50)),
            "timeout": int(values.get("system.sandbox_timeout", 15))}


def run_sandboxed(argv: list[str], work: Path, env: dict | None = None, timeout: int | None = None) -> tuple[int, str]:
    """Exécute dans le runtime hybride (namespaces, seccomp-bpf, cgroups v2)."""
    settings = sandbox_settings()
    runtime = sandbox.Sandbox(settings["backend"], settings["memory_mb"], settings["cpu_percent"])
    outcome = runtime.run(argv, work, env or {}, timeout or settings["timeout"])
    return outcome.code, outcome.output[-4000:]


def smoke(language: str, path: Path) -> tuple[int, str]:
    """Exécute « --help » d'une copie du script dans un dossier jetable du bac à sable."""
    work = sandbox.workdir("smoke")
    try:
        copy = work / "script"
        shutil.copyfile(path, copy)
        copy.chmod(0o755)
        code, output = run_sandboxed([language, str(copy), "--help"], work)
    finally:
        sandbox.purge(work)
    return code, output[-500:]


def check_content(language: str, path: Path, reference: Path) -> list[dict]:
    checks = []

    def record(name: str, success: bool, detail: str = "") -> None:
        checks.append({"check": name, "success": success, "detail": detail[-800:]})

    if language == "bash":
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, timeout=sandbox_timeout())
        record("syntaxe bash", result.returncode == 0, result.stderr)
        if shutil.which("shellcheck"):
            result = subprocess.run(["shellcheck", "-S", "error", str(path)], capture_output=True, text=True,
                                    timeout=sandbox_timeout())
            record("shellcheck (erreurs)", result.returncode == 0, result.stdout)
    else:
        result = subprocess.run(["python3", "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(path)],
                                capture_output=True, text=True, timeout=sandbox_timeout())
        record("syntaxe python", result.returncode == 0, result.stderr)
    if all(check["success"] for check in checks):
        expected, _ = smoke(language, reference)
        observed, output = smoke(language, path)
        record("--help sans privilèges, comme l'original", observed == expected,
               f"code {observed} (attendu {expected}) : {output}")
    return checks


def test_patch(target: str, content: str, reason: str) -> dict:
    if not TARGET.match(target) or not Path(target).is_file():
        raise ipc.RPCError(ipc.INVALID_PARAMS, "seuls les scripts /usr/bin/djerba-* peuvent être corrigés")
    if len(content) > 512 * 1024 or "\x00" in content:
        raise ipc.RPCError(ipc.INVALID_PARAMS, "contenu invalide")
    language = interpreter(content)
    ensure_sandbox()
    name = Path(target).name
    mirror = SANDBOX / "usr" / "bin" / name
    mirror.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(effective(target), mirror)
    git("add", str(mirror.relative_to(SANDBOX)))
    if git("diff", "--cached", "--quiet", check=False).returncode != 0:
        git("commit", "-q", "-m", f"Version en service de {name}")
    # Les vérifications s'exécutent sur des copies lisibles par « nobody »
    # (le bac à sable lui-même reste en 0700).
    workdir = Path(tempfile.mkdtemp(prefix="djerba-patch-"))
    workdir.chmod(0o755)
    reference = workdir / "reference"
    candidate = workdir / "candidate"
    shutil.copyfile(mirror, reference)
    mirror.write_text(content, encoding="utf-8")
    mirror.chmod(0o755)
    shutil.copyfile(mirror, candidate)
    reference.chmod(0o755)
    candidate.chmod(0o755)
    try:
        checks = check_content(language, candidate, reference)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    diff = git("diff", "--", str(mirror.relative_to(SANDBOX)), check=False).stdout
    if not all(check["success"] for check in checks):
        git("reset", "-q", "--hard", "HEAD")
        return {"success": False, "rolled_back": True, "checks": checks, "diff": diff[-20000:]}
    patch_id = time.strftime("p%Y%m%d%H%M%S")
    git("add", str(mirror.relative_to(SANDBOX)))
    git("commit", "-q", "-m", f"Correctif {patch_id} pour {name} : {reason[:200]}")
    git("tag", patch_id)
    return {"success": True, "patch_id": patch_id, "target": target, "checks": checks, "diff": diff[-20000:]}


def read_deployed() -> dict:
    try:
        return json.loads(DEPLOYED.read_text())
    except (OSError, ValueError):
        return {}


def deploy_patch(patch_id: str) -> dict:
    if not re.fullmatch(r"p\d{14}", patch_id):
        raise ipc.RPCError(ipc.INVALID_PARAMS, "identifiant de correctif invalide")
    ensure_sandbox()
    files = git("show", "--name-only", "--pretty=format:", patch_id, check=False).stdout.split()
    files = [path for path in files if path.startswith("usr/bin/djerba-")]
    if len(files) != 1:
        raise ipc.RPCError(ipc.INVALID_PARAMS, "correctif introuvable")
    name = Path(files[0]).name
    content = git("show", f"{patch_id}:{files[0]}").stdout
    temporary = OVERRIDE_DIR / f".{name}.djerba-tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary, 0, 0)
    temporary.chmod(0o755)
    os.replace(temporary, OVERRIDE_DIR / name)
    deployed = read_deployed()
    deployed[name] = {"patch": patch_id, "time": int(time.time()),
                      "sha256": hashlib.sha256(content.encode()).hexdigest()}
    DEPLOYED.write_text(json.dumps(deployed, indent=2))
    log(f"correctif {patch_id} déployé pour {name}")
    return {"success": True, "message": f"{name} corrigé ({patch_id}) ; retour arrière possible à tout moment.",
            "path": str(OVERRIDE_DIR / name)}


def rollback_patch(name: str) -> dict:
    deployed = read_deployed()
    if name not in deployed:
        raise ipc.RPCError(ipc.INVALID_PARAMS, f"aucun correctif déployé pour {name}")
    (OVERRIDE_DIR / name).unlink(missing_ok=True)
    del deployed[name]
    DEPLOYED.write_text(json.dumps(deployed, indent=2))
    return {"success": True, "message": f"{name} : version du paquet rétablie."}


def list_patches() -> dict:
    proposals = []
    if (SANDBOX / ".git").is_dir():
        for tag in git("tag", "--sort=-creatordate", check=False).stdout.split()[:50]:
            subject = git("log", "-1", "--pretty=%s", tag, check=False).stdout.strip()
            proposals.append({"patch_id": tag, "subject": subject})
    history = []
    if (SANDBOX / ".git").is_dir():
        for line in git("log", "-30", "--pretty=%h\t%cr\t%s", check=False).stdout.splitlines():
            commit, when, subject = (line.split("\t", 2) + ["", ""])[:3]
            history.append({"commit": commit, "when": when, "subject": subject})
    return {"deployed": read_deployed(), "proposals": proposals, "history": history}


# ---------------------------------------------------------------- Ollama
OLLAMA_RELEASE = "https://api.github.com/repos/ollama/ollama/releases/latest"
OLLAMA_UNIT = """[Unit]
Description=Ollama (moteur de modèles de langage local, utilisé par l'agent Djerba)
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3
Environment=OLLAMA_HOST=127.0.0.1:11434

[Install]
WantedBy=multi-user.target
"""


def install_ollama() -> dict:
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
    if not arch:
        raise ipc.RPCError(ipc.TOOL_FAILED, f"architecture {platform.machine()} non prise en charge par Ollama")
    headers = {"User-Agent": "djerba-ai", "Accept": "application/vnd.github+json"}
    with urllib.request.urlopen(urllib.request.Request(OLLAMA_RELEASE, headers=headers), timeout=30) as response:
        release = json.load(response)
    assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
    archive = next((name for name in (f"ollama-linux-{arch}.tar.zst", f"ollama-linux-{arch}.tgz") if name in assets),
                   None)
    if not archive or "sha256sum.txt" not in assets:
        raise ipc.RPCError(ipc.TOOL_FAILED, "publication Ollama incomplète")
    with urllib.request.urlopen(urllib.request.Request(assets["sha256sum.txt"], headers=headers), timeout=30) as response:
        sums = dict(reversed(line.split()) for line in response.read().decode().splitlines() if len(line.split()) == 2)
    expected = sums.get(archive) or sums.get(f"./{archive}")
    if not expected:
        raise ipc.RPCError(ipc.TOOL_FAILED, "somme SHA-256 absente")
    cache = Path("/var/cache/djerba-ai")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / archive
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(assets[archive], headers=headers), timeout=60) as response, \
            open(target, "wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise ipc.RPCError(ipc.TOOL_FAILED, "somme SHA-256 incorrecte : archive rejetée")
    subprocess.run(["tar", "-C", "/usr/local", "-xf", str(target)], check=True)
    target.unlink(missing_ok=True)
    if subprocess.run(["getent", "passwd", "ollama"], capture_output=True).returncode != 0:
        subprocess.run(["useradd", "-r", "-s", "/usr/sbin/nologin", "-U", "-m", "-d", "/usr/share/ollama",
                        "-c", "Ollama", "ollama"], check=True)
    for group in ("render", "video"):
        subprocess.run(["usermod", "-aG", group, "ollama"], capture_output=True)
    Path("/etc/systemd/system/ollama.service").write_text(OLLAMA_UNIT)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "ollama.service"], check=True)
    version = subprocess.run(["/usr/local/bin/ollama", "--version"], capture_output=True, text=True).stdout.strip()
    return {"success": True, "message": f"Ollama installé ({version or release.get('tag_name', '')}).",
            "version": release.get("tag_name", "")}


# ---------------------------------------------------------------- Auto-extension
PLUGIN_DISCOVERY = """
import importlib, json, sys
from djerba_ai.tools.base import BaseTool
module = importlib.import_module(sys.argv[1])
tools = [obj for obj in vars(module).values()
         if isinstance(obj, type) and issubclass(obj, BaseTool) and obj.__module__ == module.__name__ and obj.name]
assert tools, "aucune classe BaseTool définie"
names = []
for cls in tools:
    tool = cls()
    assert not tool.requires_root, "une capacité générée ne peut pas être root"
    tool.schema()
    names.append(cls.name)
print(json.dumps(names))
"""
TEST_TIMEOUT = 15  # secondes par test (pytest-timeout)


def count_tests(source: str) -> int:
    """Nombre de tests Pytest (fonctions test_*, y compris dans les classes Test*)."""
    import ast
    tree = ast.parse(source)
    count = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            count += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += sum(1 for item in node.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test"))
    return count


def temporary_venv(work: Path, requirements: list[str]) -> tuple[Path, str]:
    """venv jetable pour les dépendances d'une capacité : créé et rempli sous « nobody »,
    roues binaires uniquement (aucun code d'installation exécuté)."""
    venv = work / ".venv"
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(work), "LC_ALL": "C.UTF-8", "PIP_NO_CACHE_DIR": "1"}
    steps = [[*NOBODY, sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
             [*NOBODY, str(venv / "bin" / "python3"), "-m", "pip", "install", "--disable-pip-version-check",
              "--no-input", "--require-virtualenv", "--only-binary=:all:", *requirements]]
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True, timeout=600, env=environment, cwd=work)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip()[-800:])
    return venv / "bin" / "python3", f"{len(requirements)} dépendance(s) installée(s) dans un venv temporaire"


def test_plugin(uid: int, name: str, content: str, tests: str = "", requirements: list[str] | None = None) -> dict:
    """Pipeline d'assurance qualité d'une capacité écrite par l'agent.

    syntaxe → pyflakes → tests Pytest présents → (venv temporaire) → découverte
    des capacités → Pytest (15 s par test) ; le tout dans le bac à sable hybride.
    Échec : git reset --hard, nettoyage du dépôt, venv et dossier de travail
    supprimés, mémoire libérée. Succès : capacité et tests consignés dans Git.
    """
    import gc
    if not PLUGIN_NAME.match(name) or not name[0].isalpha():
        raise ipc.RPCError(ipc.INVALID_PARAMS, "nom de capacité invalide")
    if len(content) > 200 * 1024 or "\x00" in content or "\x00" in tests:
        raise ipc.RPCError(ipc.INVALID_PARAMS, "contenu invalide")
    requirements = list(requirements or [])
    ensure_sandbox()
    relative = Path("plugins") / str(uid) / f"{name}.py"
    test_relative = Path("plugins") / str(uid) / "tests" / f"test_{name}.py"
    for path, text in ((relative, content), (test_relative, tests)):
        (SANDBOX / path).parent.mkdir(parents=True, exist_ok=True)
        (SANDBOX / path).write_text(text, encoding="utf-8")
    checks: list[dict] = []

    def record(check: str, success: bool, detail: str = "") -> bool:
        checks.append({"check": check, "success": success, "detail": detail[-1500:]})
        return success

    def ok_so_far() -> bool:
        return all(check["success"] for check in checks)

    tools: list[str] = []
    count = 0
    backend = ""
    work: Path | None = None
    try:
        for label, source in (("syntaxe python (capacité)", content), ("syntaxe python (tests)", tests)):
            try:
                compile(source, label, "exec")
                record(label, True)
            except SyntaxError as exc:
                record(label, False, f"ligne {exc.lineno} : {exc.msg}")
        if ok_so_far():
            count = count_tests(tests)
            record("tests Pytest fournis", count > 0,
                   f"{count} test(s)" if count else "au moins une fonction test_* est obligatoire")
        if ok_so_far():
            try:
                lint = subprocess.run([sys.executable, "-m", "pyflakes", str(SANDBOX / relative),
                                       str(SANDBOX / test_relative)], capture_output=True, text=True,
                                      timeout=sandbox_timeout())
                record("pyflakes", lint.returncode == 0, lint.stdout + lint.stderr)
            except subprocess.TimeoutExpired:
                record("pyflakes", False, "délai dépassé")
        if ok_so_far():
            work = sandbox.workdir("plugin")
            for source, target in ((content, f"{name}.py"), (tests, f"test_{name}.py")):
                (work / target).write_text(source, encoding="utf-8")
                (work / target).chmod(0o644)
            python = sys.executable
            if requirements:
                try:
                    venv_python, detail = temporary_venv(work, requirements)
                    python = str(venv_python)
                    record("dépendances (venv temporaire)", True, detail)
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    record("dépendances (venv temporaire)", False, str(exc))
        if ok_so_far() and work is not None:
            package_root = str(Path(__file__).resolve().parent.parent.parent)
            env = {"PYTHONPATH": f"{work}:{package_root}", "PYTHONDONTWRITEBYTECODE": "1"}
            settings = sandbox_settings()
            runtime = sandbox.Sandbox(settings["backend"], settings["memory_mb"], settings["cpu_percent"])
            outcome = runtime.run([python, "-B", "-c", PLUGIN_DISCOVERY, name], work, env, settings["timeout"])
            backend = outcome.backend
            if record(f"chargement de la capacité ({outcome.backend})", outcome.code == 0, outcome.output):
                try:
                    tools = json.loads(outcome.output.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    record("liste des capacités", False, outcome.output)
        if ok_so_far() and work is not None:
            budget = min(TEST_TIMEOUT * (count + 1), 300)
            outcome = runtime.run([python, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                   f"--timeout={TEST_TIMEOUT}", "--timeout-method=signal", "-o",
                                   "console_output_style=classic", f"test_{name}.py"], work, env, budget)
            record(f"tests Pytest ({count}, {TEST_TIMEOUT} s max chacun, {outcome.backend})", outcome.code == 0,
                   outcome.output)
    finally:
        if work is not None:
            sandbox.purge(work)  # venv temporaire et fichiers de travail supprimés
        gc.collect()
    if not ok_so_far():
        git("reset", "-q", "--hard", "HEAD")
        git("clean", "-q", "-fdx", "--", "plugins")
        return {"success": False, "rolled_back": True, "checks": checks}
    git("add", str(relative), str(test_relative))
    git("commit", "-q", "--allow-empty", "-m", f"Capacité {name} (uid {uid}) : {', '.join(tools)} — {count} test(s)")
    return {"success": True, "checks": checks, "tools": tools, "content": content, "tests": tests,
            "tests_passed": count, "backend": backend, "requirements": requirements}


def venv_install(packages: list[str]) -> dict:
    """Installe des paquets Python dans le venv interne (jamais avec un pip global)."""
    if not packages or len(packages) > 20 or not all(isinstance(item, str) and REQUIREMENT.match(item)
                                                     for item in packages):
        raise ipc.RPCError(ipc.INVALID_PARAMS, "noms de paquets Python invalides")
    python = VENV / "bin" / "python3"
    if not python.exists():
        subprocess.run(["/usr/bin/python3", "-m", "venv", "--system-site-packages", str(VENV)], check=True,
                       capture_output=True, timeout=600)
    # Roues binaires uniquement : aucun script d'installation n'est exécuté en root.
    result = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                             "--require-virtualenv", "--only-binary=:all:", *packages], capture_output=True, text=True,
                            timeout=1800)
    if result.returncode != 0:
        return {"success": False, "message": (result.stderr or result.stdout).strip().splitlines()[-1][:300]}
    return {"success": True, "message": f"Installé dans {VENV} : {', '.join(packages)}", "venv": str(VENV)}


# ---------------------------------------------------------------- Moteurs en ligne
def cloud_settings() -> dict:
    values = Store().load()
    service = values["llm.cloud.service"]
    endpoint = values["llm.cloud.endpoint"] or CLOUD_ENDPOINTS.get(service, "")
    if not endpoint:
        raise ipc.RPCError(ipc.TOOL_FAILED, "aucune adresse d'API configurée")
    if not values["llm.cloud.api_key"]:
        raise ipc.RPCError(ipc.TOOL_FAILED, "aucune clé d'API enregistrée (Djerba → Réglages → Cerveau)")
    return {"service": service, "endpoint": endpoint, "key": values["llm.cloud.api_key"],
            "model": values["llm.cloud.model"], "timeout": max(int(values["llm.remote_timeout"]), 5)}


def cloud_chat(params: dict, emit: Emit) -> dict:
    settings = cloud_settings()
    messages = params.get("messages") or []
    tools = params.get("tools") or []
    if not isinstance(messages, list) or not isinstance(tools, list):
        raise ipc.RPCError(ipc.INVALID_PARAMS, "messages invalides")
    try:
        return llm.cloud_request(settings["service"], settings["endpoint"], settings["key"], settings["model"],
                                 messages, tools, lambda text: emit("llm.token", {"text": text}),
                                 timeout=max(settings["timeout"], 60))
    except llm.LLMError as exc:
        raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc


def cloud_health() -> dict:
    try:
        settings = cloud_settings()
        models = llm.cloud_models(settings["endpoint"], settings["key"], timeout=settings["timeout"])
    except (ipc.RPCError, llm.LLMError) as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": f"connecté ({len(models)} modèles)"}


# ---------------------------------------------------------------- Confinement AppArmor
def engines_change(method: str, params: dict) -> dict:
    """Registre des moteurs (secrets lus et écrits par root uniquement)."""
    from djerba_ai.core import engines as engine_registry
    store = engine_registry.EngineStore()
    try:
        if method == "engines.save":
            saved = store.save(dict(params["engine"]), str(params.get("secret", "")))
            return {"success": True, "message": f"moteur « {saved['name']} » enregistré", "engine": saved}
        if method == "engines.remove":
            store.remove(str(params["id"]))
            return {"success": True, "message": f"moteur {params['id']} retiré"}
        if method == "engines.set_profile":
            store.set_profile(str(params["id"]), dict(params["profile"]))
            return {"success": True, "message": "profil de capacité enregistré"}
        paired = engine_registry.pair_jarvis(str(params["address"]), str(params["code"]))
        spec = {"id": str(params.get("id", "jarvis")), "kind": "jarvis", "name": str(params.get("name") or
                paired["name"]), "icon": "user-available", "color": "#F59E0B",
                "role": "Assistant de l'utilisateur sur une autre machine", "endpoint": str(params["address"]),
                "model": paired["model"], "context_window": 32768, "share": {"contexte": True, "memoire": False}}
        saved = store.save(spec, paired["token"])
        return {"success": True, "message": f"Jarvis appairé : « {saved['name']} »", "engine": saved}
    except engine_registry.EngineError as exc:
        return {"success": False, "message": str(exc)}


SESSION_LOG_DIR = Path(os.environ.get("DJERBA_SESSION_LOG_DIR", "/var/log/djerba"))
SESSION_LOG_MAX = 1024 * 1024
SESSION_REPORTS_PER_HOUR = 30
_session_reports: dict[int, list[float]] = {}


def session_report(uid: int, desktop: str, component: str, detail: str) -> dict:
    """Défaillance d'une session graphique : /var/log/djerba/<bureau>-crash.log (root:adm, 0640).

    Le journal n'est pas inscriptible par les sessions : seul le démon root y écrit, pour
    un appelant de confiance, avec un débit borné (30 rapports par heure et par utilisateur)."""
    now = time.time()
    recent = [moment for moment in _session_reports.get(uid, []) if now - moment < 3600]
    if len(recent) >= SESSION_REPORTS_PER_HOUR:
        return {"success": False, "message": "trop de rapports : ignoré"}
    _session_reports[uid] = [*recent, now]
    path = SESSION_LOG_DIR / f"{desktop}-crash.log"
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", detail)[-8000:]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    body = "\n".join(f"  {line}" for line in text.splitlines() if line.strip())
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > SESSION_LOG_MAX:
        os.replace(path, path.with_name(path.name + ".1"))
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o640)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(f"{stamp} uid={uid} composant={component}\n{body}\n")
    if os.geteuid() == 0:
        try:
            os.chown(path, 0, grp.getgrnam("adm").gr_gid)
        except (KeyError, OSError):
            pass
    return {"success": True, "message": f"consigné dans {path}"}


def apparmor_load(uid: int, kind: str, name: str, network: bool) -> dict:
    """Profil du code généré par l'IA pour l'utilisateur uid : le dossier confiné est
    calculé ici (dossier personnel de uid), jamais fourni par l'appelant ; le réseau
    n'est permis que si l'autorisation figure dans la configuration système."""
    import pwd
    from djerba_ai.security import apparmor
    try:
        home = Path(pwd.getpwuid(uid).pw_dir)
    except KeyError as exc:
        raise ipc.RPCError(ipc.INVALID_PARAMS, f"utilisateur {uid} inconnu") from exc
    data = home / ".local" / "share" / "djerba"
    workdir = data / "generated_apps" / name if kind == "app" else data / "plugins"
    allowed = f"{kind}:{name}" in Store().load().get("security.network_grants", [])
    try:
        return apparmor.install(kind, name, str(workdir), network and allowed)
    except ValueError as exc:
        raise ipc.RPCError(ipc.INVALID_PARAMS, str(exc)) from exc


# ---------------------------------------------------------------- Serveur
Verifier = Callable[[int, int], tuple[bool, str]]


class Dispatcher:
    def __init__(self, authorizer: Authorizer = polkit_authorize, verifier: Verifier = trust.verify,
                 journal=None, policy=None) -> None:
        self.authorizer = authorizer
        self.verifier = verifier
        self._registry = None
        self._journal = journal
        self._policy = policy

    def policy(self):
        """Moteur de politique (core.policy) : la décision appartient au démon, pas à l'agent."""
        if self._policy is None:
            from djerba_ai.core.policy import Policy
            self._policy = Policy()
        return self._policy

    def origin(self, params: dict, uid: int):
        """Origine établie par root (core.provenance) ; registre illisible : origine non fiable, sans session
        (la politique refuse alors toute action au-delà du niveau 1)."""
        from djerba_ai.core.policy import Origin
        from djerba_ai.core.provenance import ProvenanceError
        try:
            return self.policy().resolve(params.get("origin"), uid)
        except ProvenanceError:
            return Origin(tainted=True)

    def gate(self, tool, arguments: dict, params: dict, pid: int, uid: int, method: str):
        """Décision de la politique (comptée), confirmation hors bande si requise ; refus consignés.

        Sans origine d'agent, la demande est présumée non fiable ; au-delà du niveau 1, elle n'aboutit que
        si un humain s'authentifie (polkit administrateur, sans rétention)."""
        from djerba_ai.core.policy import Origin
        origin = self.origin(params, uid)
        decision = self.policy().decide(tool, arguments, origin, commit=True)
        if not decision.allowed and origin.presumed and decision.level >= 2 and \
                self.authorizer(pid, uid, ACTION_POLICY):
            origin = Origin("humain", human=True)
            decision = self.policy().decide(tool, arguments, origin, commit=True)
        decision.engine, decision.session = origin.engine, origin.session
        name = tool if isinstance(tool, str) else tool.name
        if decision.allowed and decision.confirm:
            action = ACTION_CRITICAL if decision.admin else ACTION_CONFIRM
            if not self.authorizer(pid, uid, action):
                decision.allowed, decision.reason = False, "confirmation refusée par l'utilisateur"
        if not decision.allowed:
            telemetry.inc("djerba_policy_refusals_total", tool=name, level=str(decision.level))
            audit.record("refus politique", uid=uid, pid=pid, method=method, tool=name,
                         params={"niveau": decision.level, "mode": decision.mode, "moteur": origin.engine,
                                 "moteur_declare": origin.declared, "session": origin.session,
                                 "non_fiable": origin.tainted},
                         success=False, message=decision.reason)
        return decision

    @staticmethod
    def refused(decision) -> dict:
        return {"success": False, "message": f"Refusé par la politique de Djerba : {decision.reason}", "data": {},
                "policy": decision.to_dict()}

    def guarded(self, method: str, params: dict, pid: int, uid: int, action, tool: str = "",
                targets: tuple[str, ...] = (), boot: bool = False) -> Any:
        """Méthode qui modifie le système : politique, puis transaction annulable."""
        from djerba_ai.core.policy import METHOD_TOOLS
        decision = self.gate(METHOD_TOOLS.get(method, tool or method), params, params, pid, uid, method)
        if not decision.allowed:
            return self.refused(decision)
        result = self.transact(action, method, params, uid, tool=tool, targets=targets, boot=boot, decision=decision)
        if isinstance(result, dict):
            result["policy"] = decision.to_dict()
        return result

    def journal(self):
        """Journal de transactions (sauvegarde, vérification, undo) des actions qui modifient le système."""
        if self._journal is None:
            from djerba_ai.core import transactions
            self._journal = transactions.Journal()
        return self._journal

    def transact(self, action: Callable[[], Any], method: str, params: dict, uid: int, tool: str = "",
                 targets: tuple[str, ...] = (), boot: bool = False, decision=None) -> Any:
        """Transaction annulable, étiquetée par le moteur et la session établis par root (jamais par la
        déclaration du client)."""
        from djerba_ai.core.transactions import Origin
        recorded = self._audit_params(method, params)
        if isinstance(recorded, dict):
            recorded = {key: value for key, value in recorded.items() if key != "origin"}
        origin = Origin(getattr(decision, "engine", ""), getattr(decision, "session", ""))
        return self.journal().run(action, method=method, tool=tool, params=recorded, uid=uid, origin=origin,
                                  targets=targets, boot=boot)

    def registry(self):
        if self._registry is None:
            from djerba_ai.tools import registry
            self._registry = registry()
        return self._registry

    def session_method(self, method: str, params: dict, pid: int, uid: int) -> dict:
        """Sessions de provenance (core.provenance) : ouverture, état, signalement, effacement humain."""
        from djerba_ai.core.provenance import ProvenanceError
        registry = self.policy().provenance
        try:
            if method == "session.open":
                return {"session": registry.open(uid, str(params.get("channel", "agent")),
                                                 tainted=bool(params.get("tainted")),
                                                 reason=str(params.get("reason", "")))}
            key = str(params["session"])
            session = registry.get(key)
            if session is None or session["uid"] != uid:
                if method == "session.status":
                    return {"session": key, "known": False, "tainted": True,
                            "reasons": ["session inconnue du démon : présumée non fiable"]}
                raise ipc.RPCError(ipc.INVALID_PARAMS, "session inconnue")
            if method == "session.status":
                return {"session": key, "known": True, "tainted": session["tainted"], "reasons": session["reasons"],
                        "channel": session["channel"]}
            if method == "session.taint":
                registry.taint(key, str(params.get("reason", "")) or "signalé par l'agent")
                return {"success": True, "tainted": True}
            self.human_only(params, pid, uid)  # session.untaint : seul un humain authentifié efface le drapeau
            registry.untaint(key)
            audit.record("session de nouveau fiable", uid=uid, pid=pid, method=method, params={"session": key},
                         success=True, message="drapeau non fiable effacé par un humain authentifié")
            return {"success": True, "tainted": False}
        except ProvenanceError as exc:
            raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc

    def human_only(self, params: dict, pid: int, uid: int) -> None:
        """Invariant 01 : réservé à l'humain authentifié. Une demande qui porte une origine d'agent (moteur,
        session) est refusée même autorisée ; sinon authentification administrateur (polkit, sans rétention)."""
        if params.get("origin"):
            raise ipc.RPCError(ipc.NOT_AUTHORIZED, "réservé à l'humain : l'agent ne peut pas modifier ce réglage")
        self.require(pid, uid, ACTION_POLICY)

    def require(self, pid: int, uid: int, action: str) -> None:
        if not self.authorizer(pid, uid, action):
            raise ipc.RPCError(ipc.NOT_AUTHORIZED, "autorisation refusée")

    def _refuse(self, method: str, params: dict, pid: int, uid: int, message: str) -> None:
        telemetry.inc("djerba_sysd_denied_total", method=method)
        audit.record("refus", uid=uid, pid=pid, method=method, tool=str(params.get("name", "")),
                     params=self._audit_params(method, params), success=False, message=message)
        raise ipc.RPCError(ipc.NOT_AUTHORIZED, message)

    def dispatch(self, method: str, params: dict, pid: int, uid: int, emit: Emit | None = None) -> Any:
        """Point d'entrée : isolation, appelant de confiance, audit avant/après, traitement."""
        started = time.monotonic()
        telemetry.inc("djerba_sysd_requests_total", method=method)
        if isolated(pid):
            self._refuse(method, params, pid, uid, "les applications isolées et tâches de fond n'ont pas accès "
                                                   "au démon root")
        if method not in trust.OPEN_METHODS:
            try:
                trusted, reason = self.verifier(pid, uid)
            except Exception as exc:  # noqa: BLE001 - identité illisible : refus (fail-closed, invariant 31)
                trusted, reason = False, f"identité de l'appelant illisible ({exc})"
            if not trusted:
                self._refuse(method, params, pid, uid, f"appelant non fiable : {reason}")
        intent = None
        if method not in QUIET:
            # Registre immuable : la demande est consignée AVANT toute exécution.
            intent = audit.record("demande", uid=uid, pid=pid, method=method, tool=str(params.get("name", "")),
                                  params=self._audit_params(method, params))
        try:
            result = self.handle(method, params, pid, uid, emit or (lambda *_: None))
        except ipc.RPCError as exc:
            if exc.code == ipc.NOT_AUTHORIZED:
                telemetry.inc("djerba_sysd_denied_total", method=method)
            if method not in QUIET or exc.code == ipc.NOT_AUTHORIZED:
                audit.record("refus" if exc.code == ipc.NOT_AUTHORIZED else "erreur", uid=uid, pid=pid,
                             method=method, tool=str(params.get("name", "")), success=False, message=str(exc),
                             ref=intent.get("seq") if intent else None)
            raise
        finally:
            telemetry.observe("djerba_sysd_latency_seconds", time.monotonic() - started, method=method)
        if method not in QUIET:
            success = result.get("success", True) if isinstance(result, dict) else True
            message = result.get("message", "") if isinstance(result, dict) else ""
            audit.record("action", uid=uid, pid=pid, method=method, tool=str(params.get("name", "")),
                         success=success, message=message, duration_ms=int((time.monotonic() - started) * 1000),
                         ref=intent.get("seq") if intent else None)
        return result

    @staticmethod
    def _audit_params(method: str, params: dict) -> Any:
        if method == "config.set" and SCHEMA.get(str(params.get("key")), {}).get("secret"):
            return {"key": params.get("key"), "value": "[secret masqué]"}
        if method == "llm.cloud_chat":
            return {"messages": len(params.get("messages") or []), "tools": len(params.get("tools") or [])}
        if method in ("sys.test_patch", "sys.test_plugin"):
            return {key: value for key, value in params.items() if key != "content"}
        return params.get("params", params) if method == "sys.exec_tool" else params

    def handle(self, method: str, params: dict, pid: int, uid: int, emit: Emit) -> Any:
        if method == "sys.ping":
            return {"version": __version__, "uid": uid}
        if method == "sys.metrics":
            return telemetry.REGISTRY.snapshot()
        if method == "sys.sandbox_probe":
            self.require(pid, uid, ACTION_EXTEND)
            settings = sandbox_settings()
            backend = params.get("backend", "auto")
            try:
                return sandbox.probe(sandbox.Sandbox(settings["backend"] if backend == "auto" else backend,
                                                     settings["memory_mb"], settings["cpu_percent"]),
                                     settings["timeout"])
            except sandbox.SandboxError as exc:
                raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc
        if method == "audit.verify":
            self.require(pid, uid, ACTION_AUDIT)
            return audit.verify()
        if method == "security.status":
            self.require(pid, uid, ACTION_AUDIT)
            from djerba_ai.security import apparmor, ids
            return {**ids.status(), "apparmor": apparmor.enabled(),
                    "network_grants": Store().load().get("security.network_grants", [])}
        if method == "security.unblock":
            self.require(pid, uid, ACTION_CONFIGURE)
            from djerba_ai.security import ids
            try:
                done, detail = ids.unblock(str(params["ip"]))
            except ValueError as exc:
                raise ipc.RPCError(ipc.INVALID_PARAMS, "adresse invalide") from exc
            return {"success": done, "message": f"{params['ip']} débloquée" if done else f"échec : {detail}"}
        if method == "security.apparmor_load":
            self.require(pid, uid, ACTION_EXTEND)
            return self.guarded(method, params, pid, uid, lambda: apparmor_load(
                uid, str(params["kind"]), str(params["name"]), bool(params.get("network"))))
        if method == "sys.list_patches":
            return list_patches()
        if method == "sys.exec_tool":
            tool = self.registry().get(str(params.get("name", "")))
            if tool is None or not tool.requires_root:
                raise ipc.RPCError(ipc.METHOD_NOT_FOUND, "outil root inconnu")
            self.require(pid, uid, ACTION_EXEC)
            arguments = params.get("params") or {}
            decision = self.gate(tool, arguments, params, pid, uid, method)
            if not decision.allowed:
                return self.refused(decision)
            if not tool.mutates:
                result = tool.run(arguments, verbose=bool(params.get("verbose")))
            else:
                targets = (str(arguments["unit"]),) if tool.name in TARGETED_TOOLS and arguments.get("unit") else ()
                result = self.transact(lambda: tool.run(arguments, verbose=bool(params.get("verbose"))), method,
                                       params, uid, tool=tool.name, targets=targets, boot=tool.name in BOOT_TOOLS,
                                       decision=decision)
            if isinstance(result, dict) and decision.level:
                result["policy"] = decision.to_dict()
            return result
        if method == "sys.test_patch":
            self.require(pid, uid, ACTION_PATCH)
            return test_patch(str(params.get("target", "")), str(params.get("content", "")),
                              str(params.get("reason", "")))
        if method == "sys.deploy_patch":
            self.require(pid, uid, ACTION_DEPLOY)
            return self.guarded(method, params, pid, uid, lambda: deploy_patch(str(params.get("patch_id", ""))))
        if method == "sys.rollback_patch":
            self.require(pid, uid, ACTION_DEPLOY)
            return self.guarded(method, params, pid, uid, lambda: rollback_patch(str(params.get("name", ""))))
        if method == "sys.install_ollama":
            self.require(pid, uid, ACTION_SETUP)
            return self.guarded(method, params, pid, uid, install_ollama)
        if method == "sys.test_plugin":
            self.require(pid, uid, ACTION_EXTEND)
            return test_plugin(uid, str(params.get("name", "")), str(params.get("content", "")),
                               str(params.get("tests", "")), list(params.get("requirements") or []))
        if method == "sys.venv_install":
            self.require(pid, uid, ACTION_SETUP)
            return self.guarded(method, params, pid, uid, lambda: venv_install(list(params.get("packages") or [])))
        if method == "config.get":
            return public_view(Store().load())
        if method == "config.set":
            key = str(params.get("key", ""))
            if key.startswith("policy."):
                raise ipc.RPCError(ipc.NOT_AUTHORIZED, "la politique ne se modifie que par policy.set_mode ou "
                                                       "policy.set_caps (authentification administrateur)")
            if protected(key):
                self.human_only(params, pid, uid)
            else:
                self.require(pid, uid, ACTION_CONFIGURE)
            try:
                validate_setting(key, params.get("value"))
            except ConfigError as exc:
                raise ipc.RPCError(ipc.INVALID_PARAMS, str(exc)) from exc
            secret = SCHEMA[key].get("secret")

            def change() -> dict:
                _old, new = Store().set(key, params.get("value"))
                return {"success": True, "key": key, "value": None if secret else new, "message": f"{key} modifié"}
            return self.guarded(method, params, pid, uid, change)
        if method == "llm.cloud_chat":
            self.require(pid, uid, ACTION_CLOUD)
            return cloud_chat(params, emit)
        if method == "llm.cloud_health":
            self.require(pid, uid, ACTION_CLOUD)
            return cloud_health()
        if method == "llm.cloud_models":
            self.require(pid, uid, ACTION_CLOUD)
            settings = cloud_settings()
            try:
                return {"models": llm.cloud_models(settings["endpoint"], settings["key"])}
            except llm.LLMError as exc:
                raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc
        if method == "engines.list":
            from djerba_ai.core.engines import EngineStore
            return {"engines": EngineStore().list()}
        if method in ("engines.save", "engines.remove", "engines.set_profile", "engines.pair_jarvis"):
            self.human_only(params, pid, uid)  # où partent les conversations : choix humain
            return self.guarded(method, params, pid, uid, lambda: engines_change(method, params))
        if method == "engines.jarvis_history":
            self.require(pid, uid, ACTION_CLOUD)
            from djerba_ai.core import engines as engine_registry
            try:
                engine, secret = engine_registry.EngineStore().get(str(params["id"]))
                history = engine_registry.jarvis_history(engine, secret)
            except engine_registry.EngineError as exc:
                raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc
            # Contenu externe : chaque conversation importée reçoit une session non fiable tenue par root.
            from djerba_ai.core.provenance import ProvenanceError
            try:
                registry = self.policy().provenance
                history["sessions"] = {str(item.get("id", index)): registry.open(
                    uid, "import", tainted=True, reason=f"historique importé de {params['id']}")
                    for index, item in enumerate(history["conversations"]) if isinstance(item, dict)}
                return history
            except ProvenanceError as exc:
                raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc
        if method in ("llm.engine_chat", "llm.engine_health"):
            self.require(pid, uid, ACTION_CLOUD)
            from djerba_ai.core import engines as engine_registry
            try:
                engine, secret = engine_registry.EngineStore().get(str(params["id"]))
            except engine_registry.EngineError as exc:
                raise ipc.RPCError(ipc.INVALID_PARAMS, str(exc)) from exc
            if method == "llm.engine_health":
                return engine_registry.engine_health(engine, secret)
            try:
                return engine_registry.engine_request(engine, secret, list(params["messages"]),
                                                      list(params.get("tools") or []),
                                                      lambda text: (emit or (lambda *_: None))(
                                                          "llm.token", {"text": text}),
                                                      json_mode=bool(params.get("json_mode")))
            except (engine_registry.EngineError, llm.LLMError) as exc:
                raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc
        if method.startswith("mcp."):
            from djerba_ai.core.mcp import ClientStore
            store = ClientStore()
            if method == "mcp.pair_begin":
                self.human_only(params, pid, uid)
                return {"success": True, **store.begin(str(params["name"]), int(params.get("cap", 2)))}
            if method == "mcp.pair_complete":
                return store.complete(str(params["code"]), str(params.get("name", ""))) or {}
            if method == "mcp.verify":
                return store.verify(str(params["token"])) or {}
            if method == "mcp.list":
                self.require(pid, uid, ACTION_AUDIT)
                return {"clients": store.list()}
            self.human_only(params, pid, uid)
            found = store.revoke(str(params["client"]))
            return {"success": found, "message": f"accès de {params['client']} révoqué" if found else "inconnu"}
        if method == "policy.check":
            tool = self.registry().get(str(params["tool"])) or str(params["tool"])
            if params.get("commit"):
                return self.gate(tool, params.get("params") or {}, params, pid, uid, method).to_dict()
            return self.policy().decide(tool, params.get("params") or {}, self.origin(params, uid)).to_dict()
        if method in ("session.open", "session.status", "session.taint", "session.untaint"):
            return self.session_method(method, params, pid, uid)
        if method == "policy.status":
            return self.policy().status()
        if method in ("policy.set_mode", "policy.set_caps", "policy.resume"):
            # Authentification administrateur à chaque fois : l'agent ne peut pas changer sa propre politique.
            self.require(pid, uid, ACTION_POLICY)
            if method == "policy.resume":
                self.policy().resume()
                return {"success": True, "message": "agent réactivé"}
            key, value = (("policy.mode", str(params["mode"])) if method == "policy.set_mode"
                          else ("policy.engine_caps", list(params["caps"])))
            Store().set(key, value)
            return {"success": True, "message": f"{key} = {value}", "status": self.policy().status()}
        if method == "policy.pause":
            self.require(pid, uid, ACTION_UNDO)
            return {"success": True, "message": "agent en pause : plus aucune action de niveau ≥ 1",
                    "paused": self.policy().pause(str(params.get("reason", "")), uid)}
        if method in ("tx.list", "tx.show"):
            self.require(pid, uid, ACTION_AUDIT)
            if method == "tx.show":
                found = self.journal().show(str(params["id"]))
                if found is None:
                    raise ipc.RPCError(ipc.INVALID_PARAMS, "transaction inconnue")
                return found
            since = str(params["since"]).replace(" ", "T") if params.get("since") else None
            return {"transactions": self.journal().list(int(params.get("limit", 50)), since)}
        if method in ("tx.undo", "tx.undo_since", "tx.undo_session"):
            self.require(pid, uid, ACTION_UNDO)
            from djerba_ai.core.transactions import TransactionError
            try:
                if method == "tx.undo":
                    report = self.journal().undo(str(params["id"]), uid=uid, force=bool(params.get("force")))
                    return {"success": report["state"] != "annulation partielle", **report}
                if method == "tx.undo_session":
                    result = self.journal().undo_session(str(params["session"]), uid)
                    return {"success": not result["errors"], **result}
                result = self.journal().undo_since(str(params["since"]).replace(" ", "T"), uid=uid)
                return {"success": not result["errors"], **result}
            except TransactionError as exc:
                raise ipc.RPCError(ipc.TOOL_FAILED, str(exc)) from exc
        if method == "session.report":
            return session_report(uid, str(params["desktop"]), str(params["component"]), str(params["detail"]))
        if method == "audit.tail":
            self.require(pid, uid, ACTION_AUDIT)
            return {"entries": audit.tail(min(int(params.get("lines", 200)), 2000))}
        raise ipc.RPCError(ipc.METHOD_NOT_FOUND, f"méthode inconnue : {method}")


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.server.touch()
        pid, uid, _gid = ipc.peer_credentials(self.connection)
        while True:
            request_id = None
            try:
                message = ipc.read_message(self.rfile)
                if message is None:
                    return
                request_id = message.get("id") if isinstance(message.get("id"), (int, str)) else None
                request, model = parse_rpc(message)  # schéma strict : tout écart est rejeté
                params = model.model_dump()

                def emit(method: str, payload: dict) -> None:
                    ipc.write_message(self.wfile, {"jsonrpc": "2.0", "method": method, "params": payload})

                result = self.server.dispatcher.dispatch(request.method, params, pid, uid, emit)
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except ipc.RPCError as exc:
                response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": str(exc)}}
            except Exception as exc:  # noqa: BLE001 - le démon ne doit jamais tomber
                log(f"erreur interne : {exc!r}")
                response = {"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": ipc.INTERNAL_ERROR, "message": str(exc)[:300]}}
            try:
                ipc.write_message(self.wfile, response)
            except OSError:
                return
            finally:
                self.server.touch()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, sock: socket.socket | None, path: str | None, dispatcher: Dispatcher) -> None:
        self.dispatcher = dispatcher
        self.last_activity = time.monotonic()
        if sock is not None:
            super().__init__(path or "", Handler, bind_and_activate=False)
            self.socket.close()
            self.socket = sock
        else:
            super().__init__(path, Handler)

    def touch(self) -> None:
        self.last_activity = time.monotonic()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if os.geteuid() != 0:
        log("doit s'exécuter en root (service djerba-ai-sysd)")
        return 1
    if os.environ.get("LISTEN_FDS") == "1" and os.environ.get("LISTEN_PID") == str(os.getpid()):
        sock = socket.socket(fileno=3)
        server = Server(sock, None, Dispatcher())
    else:
        path = argv[0] if argv else str(ipc.SYSD_SOCKET)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).unlink(missing_ok=True)
        server = Server(None, path, Dispatcher())
        os.chmod(path, 0o666)
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    Store().ensure()  # crée /etc/djerba/config.toml (0600) et sa vue publique si besoin

    def idle_watch() -> None:
        while True:
            time.sleep(15)
            if time.monotonic() - server.last_activity > IDLE_TIMEOUT and threading.active_count() <= 2:
                log("inactif : arrêt (réactivé par la socket au prochain appel)")
                server.shutdown()
                return

    threading.Thread(target=idle_watch, daemon=True).start()
    log(f"prêt (version {__version__})")
    server.serve_forever(poll_interval=1)
    return 0
