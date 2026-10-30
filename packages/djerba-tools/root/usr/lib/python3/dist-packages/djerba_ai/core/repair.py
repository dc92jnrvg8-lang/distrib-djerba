# SPDX-License-Identifier: GPL-3.0-or-later
"""Réparation de l'OS : chaque défaut détecté est corrigé par l'outil prévu pour lui.

Les corrections passent par le même chemin que les actions de l'IA
(``Executor`` → démon root ``sys.exec_tool`` : politique, journal de
transactions annulable, audit) ; elles ne dépendent pas d'un modèle d'IA.

Défauts corrigés :

* paquets à moitié installés (``dpkg --audit``) → ``dpkg_configure_pending``,
  puis ``apt_fix_broken`` si ``apt-get check`` échoue encore ;
* services système en échec → ``service_restart`` (une fois), puis vérifié ;
* disque presque plein (``DISK_FULL`` %) → ``apt_clean`` et
  ``journal_vacuum`` (14 jours) ;
* applications en attente d'installation → ``apps_retry_pending``.

Non corrigés automatiquement (signalés) : mises à jour (réseau, durée),
santé SMART, mémoire. Un défaut qui résiste à sa correction est rapporté
avec son état ; rien n'est réessayé en boucle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable

DISK_FULL = 90
MAX_SERVICES = 10

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def _disk_percent() -> int:
    usage = shutil.disk_usage("/")
    return usage.used * 100 // usage.total if usage.total else 0


def diagnose(runner: Runner = _run, disk: Callable[[], int] = _disk_percent) -> list[dict[str, Any]]:
    """Défauts réparables : [{"kind", "title", "detail", "actions": [(outil, paramètres)]}]."""
    defects: list[dict[str, Any]] = []
    audit = runner(["dpkg", "--audit"])
    broken = runner(["apt-get", "check", "-q"]).returncode != 0
    if audit.stdout.strip() or broken:
        actions: list[tuple[str, dict]] = [("dpkg_configure_pending", {})]
        if broken:
            actions.append(("apt_fix_broken", {}))
        detail = audit.stdout.strip().splitlines()[0] if audit.stdout.strip() else "dépendances cassées"
        defects.append({"kind": "paquets", "title": "Paquets à moitié installés", "detail": detail[:200],
                        "actions": actions})
    failed = runner(["systemctl", "--failed", "--no-legend", "--plain", "--type=service"])
    units = [line.split()[0] for line in failed.stdout.splitlines() if line.strip()][:MAX_SERVICES]
    for unit in units:
        defects.append({"kind": "service", "title": f"Service en échec : {unit}", "detail": unit,
                        "actions": [("service_restart", {"unit": unit})]})
    percent = disk()
    if percent >= DISK_FULL:
        defects.append({"kind": "disque", "title": "Disque presque plein", "detail": f"{percent} % utilisés",
                        "actions": [("apt_clean", {}), ("journal_vacuum", {"days": 14})]})
    pending = runner(["djerba-apps", "status", "--json"])
    try:
        waiting = json.loads(pending.stdout).get("install", []) if pending.returncode == 0 else []
    except (ValueError, AttributeError):
        waiting = []
    if waiting:
        defects.append({"kind": "applications", "title": "Applications en attente",
                        "detail": ", ".join(str(item) for item in waiting[:5]),
                        "actions": [("apps_retry_pending", {})]})
    return defects


def repair(executor, runner: Runner = _run, disk: Callable[[], int] = _disk_percent,
           dry_run: bool = False) -> dict[str, Any]:
    """Corrige les défauts ; retourne {"defects", "actions", "remaining"} (état après correction)."""
    defects = diagnose(runner, disk)
    actions: list[dict[str, Any]] = []
    if not dry_run:
        for defect in defects:
            for tool, params in defect["actions"]:
                result = executor.execute(tool, params, request=f"[réparation] {defect['title']}")
                transaction = result.get("transaction")
                actions.append({"defect": defect["title"], "tool": tool, "params": params,
                                "success": bool(result.get("success")),
                                "message": str(result.get("message", ""))[:300],
                                "transaction": transaction.get("id") if isinstance(transaction, dict) else None})
            if defect["kind"] == "service":
                state = runner(["systemctl", "is-active", defect["detail"]]).stdout.strip()
                actions[-1]["verified"] = state == "active"
    remaining = diagnose(runner, disk) if not dry_run else defects
    return {"defects": [{key: defect[key] for key in ("kind", "title", "detail")} for defect in defects],
            "actions": actions,
            "remaining": [{key: defect[key] for key in ("kind", "title", "detail")} for defect in remaining]}
