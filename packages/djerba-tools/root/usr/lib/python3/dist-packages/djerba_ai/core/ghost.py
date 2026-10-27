# SPDX-License-Identifier: GPL-3.0-or-later
"""Mode Ghost Runner : tâches de fond de l'agent dans des unités systemd dédiées.

Une tâche longue (indexation sémantique, téléchargement de modèle, analyse…)
ne tourne jamais dans l'interface : elle devient un service transitoire du
gestionnaire systemd de l'utilisateur, ``djerba-ghost-<tâche>-<horodatage>``,
avec son propre cgroup v2 :

* environnement neuf (celui du gestionnaire, pas celui de l'interface) ;
* quotas : mémoire (MemoryMax), poids processeur et E/S réduits, priorité
  basse (Nice=10), aucun nouveau privilège (NoNewPrivileges) ;
* journal propre (``journalctl --user -u <unité>``), arrêt à tout moment ;
* aucun accès au démon root : djerba-ai-sysd refuse tout processus d'un
  cgroup ``djerba-ghost-*`` (comme ceux des applications du Studio) — seules
  des capacités sans droits root peuvent tourner en fond.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

PREFIX = "djerba-ghost-"
UNIT = re.compile(r"^djerba-ghost-[a-z0-9-]{1,60}\.service$")


class GhostError(Exception):
    pass


def _systemctl(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def command(task: str, argv: list[str], memory_mb: int = 1024) -> tuple[str, list[str]]:
    slug = re.sub(r"[^a-z0-9-]+", "-", task.lower()).strip("-")[:40] or "tache"
    unit = f"{PREFIX}{slug}-{int(time.time())}"
    return unit + ".service", [
        "systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
        f"--description=Djerba (tâche de fond) : {task}",
        "-p", f"MemoryMax={memory_mb}M", "-p", "MemorySwapMax=0", "-p", "CPUWeight=20", "-p", "IOWeight=20",
        "-p", "Nice=10", "-p", "NoNewPrivileges=yes", "-p", "TasksMax=256", "-p", "RuntimeMaxSec=6h",
        "--", *argv]


def run(task: str, argv: list[str], memory_mb: int = 1024) -> str:
    if shutil.which("systemd-run") is None:
        raise GhostError("systemd-run est requis pour les tâches de fond")
    unit, full = command(task, argv, memory_mb)
    result = subprocess.run(full, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise GhostError((result.stderr or result.stdout).strip().splitlines()[-1] if
                         (result.stderr or result.stdout).strip() else "systemd-run a échoué")
    return unit


def run_tool(tool: str, params: dict) -> str:
    """Exécute une capacité (sans droits root) en tâche de fond."""
    djerba = shutil.which("djerba-ai") or "/usr/bin/djerba-ai"
    return run(tool, [djerba, "run", tool, json.dumps(params, ensure_ascii=False)])


def tasks() -> list[dict]:
    result = _systemctl("list-units", f"{PREFIX}*", "--all", "--no-legend", "--plain", "--no-pager")
    found = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4 and UNIT.match(parts[0]):
            found.append({"unit": parts[0], "load": parts[1], "active": parts[2], "state": parts[3],
                          "description": parts[4] if len(parts) > 4 else ""})
    return found


def logs(unit: str, lines: int = 60) -> str:
    if not UNIT.match(unit):
        raise GhostError("unité de tâche de fond invalide")
    return subprocess.run(["journalctl", "--user", "-u", unit, "-n", str(lines), "-o", "cat", "--no-pager"],
                          capture_output=True, text=True, timeout=15).stdout


def stop(unit: str) -> None:
    if not UNIT.match(unit):
        raise GhostError("unité de tâche de fond invalide")
    result = _systemctl("stop", unit)
    if result.returncode != 0:
        raise GhostError(result.stderr.strip() or "arrêt impossible")
