# SPDX-License-Identifier: GPL-3.0-or-later
"""État de dpkg : détection des incohérences et réparation non bloquante.

Une transaction APT interrompue (coupure, conflit de fichiers « trying to
overwrite… », script de maintenance en échec) laisse dpkg dans un état où
plus aucune installation n'aboutit : Discover (PackageKit) reste alors bloqué
sur un écran vide. Ce module :

* **détecte** l'incohérence sans privilège (``dpkg --audit``, transaction
  interrompue dans /var/lib/dpkg/updates) en ignorant les transactions en
  cours (verrous de dpkg relevés dans /proc/locks, lisible par tous) ;
* **répare** en root, hors de toute transaction : attente du verrou,
  ``dpkg --configure -a``, ``apt-get -f install`` ; un conflit de fichiers
  avec un paquet Djerba (``djerba-*``) est résolu par ``--force-overwrite``
  sur la seule archive concernée — jamais pour un conflit entre paquets tiers,
  qui est signalé tel quel ;
* **prévient** l'utilisateur (veille de session, KDE comme GNOME) par une
  notification avec une action « Réparer » (pkexec, polkit).

Journal : /var/log/djerba/dpkg-repair.log.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

DPKG_DIR = Path(os.environ.get("DJERBA_DPKG_DIR", "/var/lib/dpkg"))
LOG = Path(os.environ.get("DJERBA_DPKG_REPAIR_LOG", "/var/log/djerba/dpkg-repair.log"))
PROC_LOCKS = Path(os.environ.get("DJERBA_PROC_LOCKS", "/proc/locks"))
REPAIR = "/usr/libexec/djerba-dpkg-repair"
SYSTEMD_RUNNING = Path(os.environ.get("DJERBA_SYSTEMD_RUN_DIR", "/run/systemd/system"))
OWN_PACKAGE = re.compile(r"^djerba-[a-z0-9-]+$")

ARCHIVE = re.compile(r"(?:error processing archive|erreur de traitement de l'archive) (?P<archive>/\S+\.deb)")
OVERWRITE = re.compile(r"(?:trying to overwrite|tentative de remplacement de) [«'‘\"]\s*(?P<path>[^'’»\"]+?)\s*['’»\"]"
                       r", (?:which is also in package|qui appartient aussi au paquet) (?P<owner>[a-z0-9][a-z0-9+.-]+)")
ENVIRONMENT = {"DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C.UTF-8", "APT_LISTCHANGES_FRONTEND": "none",
               "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
DPKG_OPTIONS = ["-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"]


def _run(argv: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=ENVIRONMENT)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


# ---------------------------------------------------------------- Détection (sans privilège)
def busy() -> bool:
    """Une transaction dpkg/APT est-elle en cours ? (verrous fcntl de /proc/locks)"""
    targets = set()
    for name in ("lock-frontend", "lock"):
        try:
            info = (DPKG_DIR / name).stat()
        except OSError:
            continue
        targets.add((os.major(info.st_dev), os.minor(info.st_dev), info.st_ino))
    try:
        lines = PROC_LOCKS.read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        match = re.search(r"\b([0-9a-f]+):([0-9a-f]+):(\d+)\b", line)
        if match and (int(match.group(1), 16), int(match.group(2), 16), int(match.group(3))) in targets:
            return True
    return False


def interrupted() -> bool:
    """Transaction interrompue : fichiers en attente dans /var/lib/dpkg/updates."""
    try:
        return any(entry.name.isdigit() for entry in (DPKG_DIR / "updates").iterdir())
    except OSError:
        return False


def audit(runner=_run) -> list[str]:
    """Paquets dans un état incohérent selon ``dpkg --audit``."""
    result = runner(["dpkg", "--audit"], timeout=120)
    packages = []
    for line in result.stdout.splitlines():
        match = re.match(r"^ ([a-z0-9][a-z0-9+.-]+)(?::[a-z0-9]+)?\s", line)
        if match and match.group(1) not in packages:
            packages.append(match.group(1))
    return packages


def check(runner=_run) -> dict:
    if busy():
        return {"healthy": True, "busy": True, "interrupted": False, "packages": [],
                "message": "Une installation est en cours."}
    pending = interrupted()
    packages = audit(runner)
    healthy = not pending and not packages
    if healthy:
        message = "Gestionnaire de paquets en bon état."
    elif packages:
        message = f"{len(packages)} paquet(s) dans un état incohérent : {', '.join(packages[:5])}"
    else:
        message = "Une installation a été interrompue."
    return {"healthy": healthy, "busy": False, "interrupted": pending, "packages": packages, "message": message}


def conflicts(output: str) -> list[dict]:
    """Conflits de fichiers « trying to overwrite » relevés dans une sortie dpkg/APT."""
    found, archive = [], ""
    for line in output.splitlines():
        match = ARCHIVE.search(line)
        if match:
            archive = match.group("archive")
        match = OVERWRITE.search(line)
        if match and archive:
            item = {"archive": archive, "path": match.group("path"), "owner": match.group("owner")}
            if item not in found:
                found.append(item)
    return found


# ---------------------------------------------------------------- Réparation (root)
def log(message: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
    except OSError:
        pass


def wait_idle(timeout: float = 600, interval: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while busy():
        if time.monotonic() > deadline:
            return False
        time.sleep(interval)
    return True


def repair(runner=_run, wait: float = 600) -> dict:
    """Ramène dpkg à un état cohérent ; renvoie le rapport des étapes."""
    report: dict = {"repaired": False, "steps": [], "conflicts": [], "forced": [], "message": ""}
    if not wait_idle(wait):
        report["message"] = "Une autre installation occupe le gestionnaire de paquets : réparation reportée."
        log(report["message"])
        return report

    def step(label: str, argv: list[str]) -> subprocess.CompletedProcess:
        result = runner(argv)
        output = (result.stdout or "") + (result.stderr or "")
        report["steps"].append({"step": label, "code": result.returncode, "output": output[-2000:]})
        log(f"{label} : code {result.returncode}")
        for item in conflicts(output):
            if item not in report["conflicts"]:
                report["conflicts"].append(item)
        return result

    step("dpkg --configure -a", ["dpkg", "--force-confdef", "--force-confold", "--configure", "-a"])
    apt = step("apt-get -f install", ["apt-get", *DPKG_OPTIONS, "-y", "-f", "install"])
    if apt.returncode != 0:
        # Seuls les conflits avec un fichier d'un paquet Djerba sont forcés : Djerba a livré
        # par le passé des fichiers que des paquets Debian revendiquent (kcm-about-distrorc).
        for item in report["conflicts"]:
            if OWN_PACKAGE.match(item["owner"]) and Path(item["archive"]).is_file() \
                    and item["archive"] not in report["forced"]:
                report["forced"].append(item["archive"])
                step(f"dpkg -i --force-overwrite {Path(item['archive']).name}",
                     ["dpkg", "--force-confdef", "--force-confold", "--force-overwrite", "-i", item["archive"]])
        if report["forced"]:
            step("apt-get -f install", ["apt-get", *DPKG_OPTIONS, "-y", "-f", "install"])
    step("dpkg --configure -a", ["dpkg", "--force-confdef", "--force-confold", "--configure", "-a"])
    final = check(runner)
    report["repaired"] = final["healthy"]
    foreign = [item for item in report["conflicts"] if not OWN_PACKAGE.match(item["owner"])]
    if final["healthy"]:
        report["message"] = "Gestionnaire de paquets réparé."
    elif foreign:
        report["message"] = ("Conflit entre paquets tiers non résolu automatiquement : "
                             + ", ".join(f"{item['path']} ({item['owner']})" for item in foreign[:3]))
    else:
        report["message"] = f"Réparation incomplète : {final['message']}"
    log(report["message"])
    return report


def auto(runner=_run) -> dict:
    """Mode automatique (minuterie, postinst) : n'agit que si l'état est réellement incohérent."""
    if not wait_idle(600):
        return {"repaired": False, "message": "occupé : réparation reportée"}
    state = check(runner)
    if state["healthy"]:
        return {"repaired": False, "message": state["message"]}
    log(f"incohérence détectée : {state['message']}")
    return repair(runner, wait=60)


def schedule(delay: int = 5, runner=_run) -> bool:
    """Crochet APT (DPkg::Post-Invoke, exécuté même après un échec de dpkg) : si l'état est
    incohérent, programme ``--auto`` hors transaction (le verrou d'APT est encore tenu ici)."""
    if os.geteuid() != 0 or not SYSTEMD_RUNNING.is_dir():
        return False
    if not interrupted() and not audit(runner):
        return False
    result = runner(["systemd-run", "--quiet", "--no-block", "--collect", f"--on-active={int(delay)}",
                     f"--unit=djerba-dpkg-repair-hook-{int(time.time())}", REPAIR, "--auto"], timeout=30)
    log("incohérence après une transaction APT : réparation programmée"
        if result.returncode == 0 else f"programmation impossible : {result.stderr.strip()[:200]}")
    return result.returncode == 0


# ---------------------------------------------------------------- Veille de session
def notify(title: str, body: str, actions: dict[str, str] | None = None, timeout: float = 3600) -> str:
    argv = ["notify-send", "-a", "Djerba OS", "-i", "system-software-update", "-u", "critical", "--wait"]
    for key, label in (actions or {}).items():
        argv.append(f"--action={key}={label}")
    try:
        result = subprocess.run([*argv, title, body], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def watch(interval: float = 20, runner=_run) -> None:
    """Session utilisateur : prévient (une fois par état) si dpkg reste incohérent."""
    strikes, announced = 0, ""
    while True:
        state = check(runner)
        if state["healthy"]:
            strikes, announced = 0, ""
        else:
            strikes += 1
            signature = json.dumps([state["interrupted"], state["packages"]])
            # Deux relevés consécutifs : une transaction qui se termine n'est pas signalée.
            if strikes >= 2 and signature != announced:
                announced = signature
                answer = notify("Gestionnaire de paquets à réparer",
                                f"{state['message']}. Les installations (Discover, Centre d'applications) "
                                "sont bloquées tant que l'état n'est pas réparé.",
                                {"repair": "Réparer", "later": "Plus tard"})
                if answer == "repair":
                    result = subprocess.run(["pkexec", REPAIR, "--repair", "--json"], capture_output=True,
                                            text=True)
                    try:
                        message = json.loads(result.stdout).get("message", "")
                    except ValueError:
                        message = "Réparation annulée." if result.returncode in (126, 127) else result.stderr[-200:]
                    notify("Gestionnaire de paquets", message or "Réparation terminée.", timeout=60)
                    announced = "" if result.returncode in (126, 127) else announced
        time.sleep(interval)
