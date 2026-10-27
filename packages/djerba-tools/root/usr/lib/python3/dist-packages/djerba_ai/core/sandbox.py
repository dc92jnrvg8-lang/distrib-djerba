# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime hybride du bac à sable (exécuté par le démon root djerba-ai-sysd).

Tout code non fiable — capacité générée et ses tests, correctif proposé,
commande arbitraire — s'exécute dans un environnement jetable combinant :

* **espaces de noms Linux** : réseau, IPC, nom d'hôte, PID, montages,
  utilisateurs (aucun accès au réseau, aux autres processus ni aux montages) ;
* **seccomp-bpf** : appels système dangereux refusés, sockets limitées au
  domaine UNIX ;
* **cgroups v2** : mémoire (MemoryMax, sans échange), processeur (CPUQuota),
  nombre de tâches (TasksMax), durée maximale ;
* **utilisateur jetable sans privilège**, système de fichiers en lecture
  seule hormis le dossier de travail.

Moteurs (« sandbox.backend ») :

* ``systemd`` (production) — service transitoire ``systemd-run --wait`` :
  DynamicUser, Private{Network,IPC,PIDs,Users,Devices,Tmp}, Protect*,
  RestrictNamespaces, SystemCallFilter, RestrictAddressFamilies=AF_UNIX,
  IPAddressDeny=any, MemoryMax, CPUQuota, TasksMax, RuntimeMaxSec ;
* ``unshare`` (repli sans systemd, ex. conteneur de construction) —
  unshare(1) (net, ipc, uts, pid, mount), cgroup v2 dédié si possible
  (sinon limites prlimit), utilisateur nobody (setpriv, no_new_privs) et
  filtre seccomp-bpf (core/seccomp_launch.py, libseccomp) ;
* ``gvisor`` — si ``runsc`` (gVisor) est installé : noyau applicatif
  « Sentry » entre le code et le noyau Linux, réservé aux instructions
  arbitraires les plus risquées (``isolation="max"``).

``auto`` choisit systemd si le système a démarré avec systemd, sinon unshare ;
``isolation="max"`` préfère gVisor quand il est présent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

LAUNCHER = Path(__file__).with_name("seccomp_launch.py")
CGROUP_ROOT = Path("/sys/fs/cgroup")
#: dossiers de travail jetables : hors de /var/lib/djerba-ai (0700), traversable (0711) par
#: l'utilisateur jetable, jamais listable
RUNS_DIR = Path(os.environ.get("DJERBA_SANDBOX_RUNS", "/var/lib/djerba-ai-runs"))
SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"

SYSCALL_DENY = ("@privileged @mount @debug @module @reboot @swap @clock @cpu-emulation @obsolete @raw-io "
                "@keyring")


@dataclass(frozen=True)
class Outcome:
    code: int
    output: str
    backend: str
    duration: float
    timed_out: bool = False


class SandboxError(Exception):
    pass


def systemd_available() -> bool:
    return Path("/run/systemd/system").is_dir() and shutil.which("systemd-run") is not None


def gvisor_available() -> bool:
    return shutil.which("runsc") is not None


def choose(preferred: str = "auto", isolation: str = "standard") -> str:
    if preferred == "gvisor" or (isolation == "max" and preferred == "auto" and gvisor_available()):
        if not gvisor_available():
            raise SandboxError("gVisor (runsc) n'est pas installé")
        return "gvisor"
    if preferred == "systemd" or (preferred == "auto" and systemd_available()):
        if not systemd_available():
            raise SandboxError("systemd n'est pas disponible")
        return "systemd"
    if shutil.which("unshare") is None or shutil.which("setpriv") is None:
        raise SandboxError("unshare et setpriv (util-linux) sont requis")
    return "unshare"


def workdir(prefix: str = "run") -> Path:
    """Dossier de travail jetable, accessible à l'utilisateur jetable."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUNS_DIR, 0o711)
    path = Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=RUNS_DIR))
    os.chmod(path, 0o1777)
    return path


def purge(path: Path) -> None:
    """Nettoyage récursif d'un dossier de travail (état propre après chaque exécution)."""
    shutil.rmtree(path, ignore_errors=True)


class Sandbox:
    def __init__(self, backend: str = "auto", memory_mb: int = 512, cpu_percent: int = 50,
                 tasks: int = 64) -> None:
        self.preferred = backend
        self.memory_mb = memory_mb
        self.cpu_percent = cpu_percent
        self.tasks = tasks

    # ------------------------------------------------------------ commandes
    def systemd_command(self, argv: list[str], work: Path, env: dict[str, str], timeout: int) -> list[str]:
        properties = [
            "DynamicUser=yes", "PrivateNetwork=yes", "PrivateIPC=yes", "PrivateTmp=yes", "PrivateDevices=yes",
            "PrivateUsers=yes", "ProtectSystem=strict", "ProtectHome=yes", "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes", "ProtectKernelLogs=yes", "ProtectControlGroups=yes", "ProtectClock=yes",
            "ProtectHostname=yes", "ProtectProc=invisible", "ProcSubset=pid", "RestrictNamespaces=yes",
            "RestrictRealtime=yes", "RestrictSUIDSGID=yes", "LockPersonality=yes", "NoNewPrivileges=yes",
            "CapabilityBoundingSet=", "AmbientCapabilities=", "SystemCallArchitectures=native",
            "SystemCallFilter=@system-service", f"SystemCallFilter=~{SYSCALL_DENY}", "SystemCallErrorNumber=EPERM",
            "RestrictAddressFamilies=AF_UNIX", "IPAddressDeny=any", "UMask=0077",
            f"MemoryMax={self.memory_mb}M", "MemorySwapMax=0", f"CPUQuota={self.cpu_percent}%",
            f"TasksMax={self.tasks}", f"RuntimeMaxSec={timeout}", f"ReadWritePaths={work}",
            f"WorkingDirectory={work}", "KeyringMode=private",
        ]
        command = ["systemd-run", "--wait", "--pipe", "--quiet", "--collect", "--service-type=exec",
                   f"--unit=djerba-sandbox-{os.getpid()}-{time.monotonic_ns()}",
                   "--description=Djerba : exécution en bac à sable"]
        for item in properties:
            command += ["-p", item]
        for key, value in {"PATH": SAFE_PATH, "LC_ALL": "C.UTF-8", "HOME": str(work), **env}.items():
            command += [f"--setenv={key}={value}"]
        return command + ["--", *argv]

    def unshare_command(self, argv: list[str], work: Path, env: dict[str, str], timeout: int,
                        cgroup: Path | None) -> tuple[list[str], dict[str, str]]:
        limits = ["prlimit", f"--cpu={timeout}", f"--nproc={self.tasks * 4}", "--core=0",
                  f"--fsize={512 * 1024 * 1024}"]
        if cgroup is None:
            limits.append(f"--as={max(self.memory_mb, 256) * 1024 * 1024 * 4}")
        command = ["unshare", "--net", "--ipc", "--uts", "--pid", "--fork", "--mount", "--mount-proc",
                   "--kill-child", "--",
                   *limits, "--",
                   "setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--no-new-privs",
                   "--inh-caps=-all", "--bounding-set=-all", "--",
                   sys.executable, "-I", str(LAUNCHER), "--", *argv]
        environment = {"PATH": SAFE_PATH, "LC_ALL": "C.UTF-8", "HOME": str(work), **env}
        return command, environment

    def gvisor_command(self, argv: list[str], work: Path, env: dict[str, str]) -> list[str]:
        environment = [f"--env={key}={value}" for key, value in {"PATH": SAFE_PATH, **env}.items()]
        return ["runsc", "--network=none", "--ignore-cgroups", f"--root={work}/.runsc", "do",
                f"--cwd={work}", "--quiet", *environment, "--", *argv]

    # ------------------------------------------------------------ cgroup v2 (repli)
    def _cgroup(self) -> Path | None:
        base = CGROUP_ROOT / "djerba-sandbox"
        try:
            if not (CGROUP_ROOT / "cgroup.controllers").exists():
                return None
            base.mkdir(exist_ok=True)
            controllers = (CGROUP_ROOT / "cgroup.subtree_control")
            try:
                controllers.write_text("+memory +pids +cpu")
                (base / "cgroup.subtree_control").write_text("+memory +pids +cpu")
            except OSError:
                pass
            group = base / f"run-{os.getpid()}-{time.monotonic_ns()}"
            group.mkdir()
            for name, value in (("memory.max", str(self.memory_mb * 1024 * 1024)), ("memory.swap.max", "0"),
                                ("pids.max", str(self.tasks)),
                                ("cpu.max", f"{self.cpu_percent * 1000} 100000")):
                try:
                    (group / name).write_text(value)
                except OSError:
                    pass
            return group
        except OSError:
            return None

    @staticmethod
    def _release(group: Path | None) -> None:
        if group is None:
            return
        try:
            (group / "cgroup.kill").write_text("1")
        except OSError:
            pass
        for _ in range(20):
            try:
                group.rmdir()
                return
            except OSError:
                time.sleep(0.05)

    # ------------------------------------------------------------ exécution
    def run(self, argv: list[str], work: Path, env: dict[str, str] | None = None, timeout: int = 15,
            isolation: str = "standard") -> Outcome:
        env = env or {}
        backend = choose(self.preferred, isolation)
        started = time.monotonic()
        group = None
        try:
            if backend == "systemd":
                command, environment, preexec = self.systemd_command(argv, work, env, timeout), None, None
            elif backend == "gvisor":
                command, environment, preexec = self.gvisor_command(argv, work, env), None, None
            else:
                group = self._cgroup()
                command, environment = self.unshare_command(argv, work, env, timeout, group)

                def preexec() -> None:
                    if group is not None:
                        (group / "cgroup.procs").write_text(str(os.getpid()))
            try:
                process = subprocess.run(command, cwd=work, env=environment, capture_output=True, text=True,
                                         timeout=timeout + 10, preexec_fn=preexec, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired as exc:
                output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                return Outcome(124, output + f"\ndélai dépassé ({timeout} s)", backend,
                               time.monotonic() - started, True)
            timed_out = process.returncode in (124, 137, -9) and time.monotonic() - started >= timeout - 0.5
            output = (process.stdout or "") + (process.stderr or "")
            if timed_out:
                output += f"\ndélai dépassé ({timeout} s)"
            return Outcome(process.returncode, output, backend, time.monotonic() - started, timed_out)
        finally:
            self._release(group)


PROBE = r"""
import json, os, socket, threading, time
results = {}
def check(name, function):
    try:
        function()
        results[name] = "autorisé"
    except BaseException as exc:
        results[name] = "bloqué (" + type(exc).__name__ + ")"
check("réseau (socket AF_INET)", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("1.1.1.1", 53)))
check("écriture dans /usr", lambda: open("/usr/djerba-probe", "w").write("x"))
check("lecture de /etc/shadow", lambda: open("/etc/shadow").read())
check("espace de noms utilisateur", lambda: os.unshare(os.CLONE_NEWUSER))
def many_threads():
    threads = []
    for _ in range(400):
        thread = threading.Thread(target=time.sleep, args=(2,))
        thread.start()
        threads.append(thread)
check("400 tâches simultanées", many_threads)
results["identité"] = "uid %d" % os.getuid()
check("écriture dans le dossier de travail", lambda: open("probe.txt", "w").write("x"))
print(json.dumps(results, ensure_ascii=False))
"""
#: résultat attendu de chaque sonde
EXPECTED = {"réseau (socket AF_INET)": False, "écriture dans /usr": False, "lecture de /etc/shadow": False,
            "espace de noms utilisateur": False, "400 tâches simultanées": False,
            "écriture dans le dossier de travail": True}


def probe(sandbox: Sandbox, timeout: int = 15) -> dict:
    """Vérifie l'isolation réelle : chaque sonde doit être bloquée (sauf le dossier de travail)."""
    import json
    work = workdir("probe")
    try:
        outcome = sandbox.run([sys.executable, "-I", "-c", PROBE], work, timeout=timeout)
    finally:
        purge(work)
    try:
        results = json.loads(outcome.output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"success": False, "backend": outcome.backend, "message": outcome.output[-800:], "results": {}}
    failures = [name for name, allowed in EXPECTED.items()
                if results.get(name, "").startswith("autorisé") != allowed]
    if results.get("identité") == "uid 0":
        failures.append("identité root")
    return {"success": not failures, "backend": outcome.backend, "results": results, "failures": failures,
            "message": ("Isolation vérifiée" if not failures else "Isolation insuffisante : " + ", ".join(failures))
            + f" ({outcome.backend})"}
