# SPDX-License-Identifier: GPL-3.0-or-later
"""djerba-ai-sysd — démon root de l'agent Djerba (JSON-RPC sur socket UNIX).

Activé par socket (/run/djerba-ai/sysd.sock), il s'arrête après quelques
minutes d'inactivité. Chaque connexion est identifiée par SO_PEERCRED, puis
l'action est autorisée par polkit pour *ce processus* (agent d'authentification
KDE, mot de passe administrateur, mémorisé quelques minutes pour
org.djerba.ai.exec ; jamais mémorisé pour un déploiement de correctif).

Méthodes :
  sys.ping                       version (sans autorisation)
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
"""

from __future__ import annotations

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
from djerba_ai.core import ipc

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

Authorizer = Callable[[int, int, str], bool]


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


def smoke(language: str, path: Path) -> tuple[int, str]:
    """Exécute « --help » sous l'utilisateur nobody, sans privilèges."""
    command = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--no-new-privs",
               "--inh-caps=-all", language, str(path), "--help"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, cwd="/",
                                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
                                     "LC_ALL": "C.UTF-8"})
        return result.returncode, (result.stdout + result.stderr)[-500:]
    except subprocess.TimeoutExpired:
        return 124, "délai dépassé"


def check_content(language: str, path: Path, reference: Path) -> list[dict]:
    checks = []

    def record(name: str, success: bool, detail: str = "") -> None:
        checks.append({"check": name, "success": success, "detail": detail[-800:]})

    if language == "bash":
        result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        record("syntaxe bash", result.returncode == 0, result.stderr)
        if shutil.which("shellcheck"):
            result = subprocess.run(["shellcheck", "-S", "error", str(path)], capture_output=True, text=True)
            record("shellcheck (erreurs)", result.returncode == 0, result.stdout)
    else:
        result = subprocess.run(["python3", "-c", "import ast,sys; ast.parse(open(sys.argv[1]).read())", str(path)],
                                capture_output=True, text=True)
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
    return {"deployed": read_deployed(), "proposals": proposals}


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


# ---------------------------------------------------------------- Serveur
class Dispatcher:
    def __init__(self, authorizer: Authorizer = polkit_authorize) -> None:
        self.authorizer = authorizer
        self._registry = None

    def registry(self):
        if self._registry is None:
            from djerba_ai.tools import registry
            self._registry = registry()
        return self._registry

    def require(self, pid: int, uid: int, action: str) -> None:
        if not self.authorizer(pid, uid, action):
            raise ipc.RPCError(ipc.NOT_AUTHORIZED, "autorisation refusée")

    def handle(self, method: str, params: dict, pid: int, uid: int) -> Any:
        if method == "sys.ping":
            return {"version": __version__, "uid": uid}
        if method == "sys.list_patches":
            return list_patches()
        if method == "sys.exec_tool":
            tool = self.registry().get(str(params.get("name", "")))
            if tool is None or not tool.requires_root:
                raise ipc.RPCError(ipc.METHOD_NOT_FOUND, "outil root inconnu")
            self.require(pid, uid, ACTION_EXEC)
            log(f"uid {uid} : {tool.name} {json.dumps(params.get('params') or {}, ensure_ascii=False)[:200]}")
            return tool.run(params.get("params") or {}, verbose=bool(params.get("verbose")))
        if method == "sys.test_patch":
            self.require(pid, uid, ACTION_PATCH)
            return test_patch(str(params.get("target", "")), str(params.get("content", "")),
                              str(params.get("reason", "")))
        if method == "sys.deploy_patch":
            self.require(pid, uid, ACTION_DEPLOY)
            return deploy_patch(str(params.get("patch_id", "")))
        if method == "sys.rollback_patch":
            self.require(pid, uid, ACTION_DEPLOY)
            return rollback_patch(str(params.get("name", "")))
        if method == "sys.install_ollama":
            self.require(pid, uid, ACTION_SETUP)
            return install_ollama()
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
                request_id = message.get("id")
                if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                    raise ipc.RPCError(ipc.INVALID_REQUEST, "requête JSON-RPC 2.0 attendue")
                params = message.get("params") or {}
                if not isinstance(params, dict):
                    raise ipc.RPCError(ipc.INVALID_PARAMS, "paramètres nommés attendus")
                result = self.server.dispatcher.handle(message["method"], params, pid, uid)
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
