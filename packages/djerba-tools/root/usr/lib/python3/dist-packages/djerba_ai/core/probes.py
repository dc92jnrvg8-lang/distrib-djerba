# SPDX-License-Identifier: GPL-3.0-or-later
"""Sondes de santé déterministes, relevées avant et après chaque action.

Le succès d'une action n'est jamais « l'IA dit que c'est réparé » : on
compare des mesures objectives. Une **régression** (unité en échec qui ne
l'était pas, dpkg devenu incohérent, réseau ou résolveur perdus, unité ciblée
arrêtée) déclenche l'annulation automatique de la transaction.

Toutes les sondes sont locales (aucun accès Internet) et bornées dans le
temps : une sonde qui ne répond pas est notée « inconnue », jamais comme une
régression.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[..., subprocess.CompletedProcess]


def _run(argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env={"LC_ALL": "C.UTF-8", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def failed_units(runner: Runner = _run) -> list[str] | None:
    result = runner(["systemctl", "list-units", "--state=failed", "--plain", "--no-legend", "--no-pager"])
    if result.returncode != 0:
        return None
    return sorted(line.split()[0] for line in result.stdout.splitlines() if line.split())


def dpkg_consistent(runner: Runner = _run) -> bool | None:
    if not Path("/var/lib/dpkg/status").exists():
        return None
    result = runner(["dpkg", "--audit"], timeout=60)
    if result.returncode not in (0, 1):
        return None
    return not result.stdout.strip()


def unit_active(unit: str, runner: Runner = _run) -> bool | None:
    result = runner(["systemctl", "is-active", unit])
    state = result.stdout.strip()
    if state in ("active", "reloading", "activating"):
        return True
    if state in ("inactive", "failed", "deactivating"):
        return False
    return None


def default_route(runner: Runner = _run) -> bool | None:
    result = runner(["ip", "-o", "route", "show", "default"])
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def resolver_ok() -> bool | None:
    """Résolveur local configuré : lien vers le stub de systemd-resolved ou fichier non vide."""
    path = Path(os.environ.get("DJERBA_RESOLV_CONF", "/etc/resolv.conf"))
    try:
        return path.is_symlink() and path.exists() or (path.is_file() and bool(path.read_text().strip()))
    except OSError:
        return None


def collect(targets: tuple[str, ...] = (), runner: Runner = _run) -> dict:
    """Instantané de santé ; ``targets`` : unités dont l'état doit être préservé."""
    return {
        "failed_units": failed_units(runner),
        "dpkg": dpkg_consistent(runner),
        "network_manager": unit_active("NetworkManager.service", runner),
        "route": default_route(runner),
        "resolver": resolver_ok(),
        "targets": {unit: unit_active(unit, runner) for unit in targets},
    }


def regressions(before: dict, after: dict) -> list[str]:
    """Différences qui signalent une dégradation (une valeur inconnue n'est jamais une régression)."""
    found: list[str] = []
    old, new = before.get("failed_units"), after.get("failed_units")
    if old is not None and new is not None:
        for unit in sorted(set(new) - set(old)):
            # Les unités transitoires de l'agent (Ghost Runner, applications) ne concernent pas le système.
            if not unit.startswith(("djerba-ghost-", "djerba-app-", "run-")):
                found.append(f"l'unité {unit} est en échec")
    if before.get("dpkg") is True and after.get("dpkg") is False:
        found.append("le gestionnaire de paquets est devenu incohérent (dpkg --audit)")
    if before.get("network_manager") is True and after.get("network_manager") is False:
        found.append("NetworkManager s'est arrêté")
    if before.get("route") is True and after.get("route") is False:
        found.append("la route réseau par défaut a disparu")
    if before.get("resolver") is True and after.get("resolver") is False:
        found.append("la résolution de noms n'est plus configurée")
    for unit, state in (after.get("targets") or {}).items():
        if (before.get("targets") or {}).get(unit) is True and state is False:
            found.append(f"l'unité {unit} ne fonctionne plus")
    return found
