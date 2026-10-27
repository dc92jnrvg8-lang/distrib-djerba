# SPDX-License-Identifier: GPL-3.0-or-later
"""Instantané de la configuration du système, donné à l'agent à chaque requête.

L'agent « a en tête » la machine : version, matériel, session, modes Djerba
(anonyme, discret, jeu), applications du catalogue, services en échec,
espace disque, réseau, modèles disponibles. Collecte rapide (sans root),
mise en cache 60 s.
"""

from __future__ import annotations

import json
import os
import platform
import pwd
import shutil
import subprocess
import time
from pathlib import Path

_CACHE: dict = {"time": 0.0, "text": "", "data": {}}


def _run(argv: list[str], timeout: int = 8) -> str:
    if shutil.which(argv[0]) is None:
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _json(argv: list[str]) -> dict:
    try:
        return json.loads(_run(argv) or "{}")
    except ValueError:
        return {}


def _user() -> str:
    try:
        return os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def collect() -> dict:
    release = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                release[key] = value.strip('"')
    except OSError:
        pass
    cpu = ""
    try:
        cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith(("model name", "Model"))), "")
        mem_gb = round(int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                                if line.startswith("MemTotal"))) / 1048576, 1)
    except (OSError, StopIteration, ValueError):
        mem_gb = 0
    gpus = [line.split(": ", 1)[-1] for line in _run(["lspci"]).splitlines()
            if any(kind in line for kind in ("VGA", "3D controller", "Display controller"))]
    usage = shutil.disk_usage("/")
    catalog = _json(["djerba-apps", "list", "--json"])
    installed = [app["name"] for app in catalog.get("apps", []) if app.get("installed")]
    failed = [line.split()[0] for line in _run(["systemctl", "--failed", "--no-legend", "--plain"]).splitlines()
              if line.strip()]
    live = Path("/run/live/medium").exists() or "boot=live" in Path("/proc/cmdline").read_text()
    return {
        "system": release.get("PRETTY_NAME", "Djerba OS"),
        "base": "Debian 13 trixie",
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "live_session": live,
        "session": os.environ.get("XDG_SESSION_TYPE", "inconnue"),
        "desktop": "KDE Plasma 6",
        "user": _user(),
        "cpu": cpu, "cores": os.cpu_count(), "memory_gb": mem_gb, "gpus": gpus,
        "disk_root": {"total_gb": round(usage.total / 1e9, 1), "free_gb": round(usage.free / 1e9, 1),
                      "used_percent": round(usage.used * 100 / usage.total)},
        "anonymous_mode": _json(["djerba-anonsurf", "status", "--json"]).get("active", False),
        "undercover_mode": _json(["djerba-undercover", "status", "--json"]),
        "game_mode": _json(["djerba-gamemode", "status", "--json"]),
        "catalog_installed": installed,
        "apps_pending": _json(["djerba-apps", "status", "--json"]).get("pending", {}).get("install", []),
        "failed_units": failed,
        "network": _run(["nmcli", "networking", "connectivity"]) or "inconnu",
    }


def snapshot(force: bool = False) -> tuple[str, dict]:
    """Texte compact (pour le prompt système) et données brutes."""
    if not force and time.monotonic() - _CACHE["time"] < 60 and _CACHE["text"]:
        return _CACHE["text"], _CACHE["data"]
    try:
        data = collect()
    except Exception as exc:  # noqa: BLE001 - le contexte ne doit jamais empêcher l'agent de répondre
        text = f"Système : Djerba OS (base Debian 13). Contexte détaillé indisponible ({exc})."
        _CACHE.update(time=time.monotonic(), text=text, data={})
        return text, {}
    game = data["game_mode"]
    undercover = data["undercover_mode"]
    lines = [
        f"Système : {data['system']} (base {data['base']}), noyau {data['kernel']}, {data['architecture']}, "
        f"{'SESSION LIVE (rien n’est installé sur le disque)' if data['live_session'] else 'installé'}.",
        f"Bureau : {data['desktop']} ({data['session']}), utilisateur « {data['user']} ».",
        f"Matériel : {data['cpu']} ({data['cores']} cœurs), {data['memory_gb']} Go RAM, "
        f"GPU : {'; '.join(data['gpus']) or 'inconnu'}.",
        f"Disque / : {data['disk_root']['free_gb']} Go libres sur {data['disk_root']['total_gb']} Go "
        f"({data['disk_root']['used_percent']} % utilisés).",
        f"Réseau : {data['network']}. Mode anonyme (Tor) : {'ACTIF' if data['anonymous_mode'] else 'inactif'}. "
        f"Mode discret : {'actif (Windows ' + undercover.get('style', '') + ')' if undercover.get('active') else 'inactif'}. "
        f"Mode jeu : {'actif' if game.get('active') else 'inactif'}, profil {game.get('power_profile', '?')}.",
        f"Applications du catalogue installées : {', '.join(data['catalog_installed']) or 'aucune'}"
        + (f" ; en attente : {', '.join(data['apps_pending'])}" if data["apps_pending"] else "") + ".",
        f"Services en échec : {', '.join(data['failed_units']) or 'aucun'}.",
    ]
    text = "\n".join(lines)
    _CACHE.update(time=time.monotonic(), text=text, data=data)
    return text, data
