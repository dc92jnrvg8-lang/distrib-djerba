# SPDX-License-Identifier: GPL-3.0-or-later
"""Garde de session : l'intégration de Djerba ne fait jamais tomber le bureau.

* ``guarded`` exécute une étape d'intégration (adaptateur de bureau, source de
  la fenêtre active, raccourcis) : toute exception est consignée et l'étape
  passe en **mode dégradé silencieux** — la session continue sans elle.
* ``report`` consigne une défaillance : journal de l'utilisateur
  (~/.local/state/djerba-ai/<bureau>-crash.log) et, par le démon root,
  /var/log/djerba/<bureau>-crash.log (gnome-crash.log pour GNOME). Le journal
  système n'est jamais inscriptible par la session elle-même.
* ``collect_gnome`` est lancé par systemd quand GNOME Shell ou gnome-session
  échoue (OnFailure= des unités org.gnome.Shell@*.service,
  gnome-session-failed.target) : il joint les dernières lignes du journal de
  la session au rapport.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any, Callable

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "djerba-ai"
GNOME_UNITS = ("org.gnome.Shell@wayland.service", "org.gnome.Shell@x11.service",
               "gnome-session-manager@gnome.service", "gnome-session-failed.service")
INTERESTING = re.compile(r"error|fail|fatal|critical|segfault|core dump|abort|traceback|exception|"
                         r"not installed|JS ERROR|djerba", re.IGNORECASE)


def desktop_name(environ: dict | None = None) -> str:
    environ = os.environ if environ is None else environ
    desktop = ":".join(environ.get(key, "") for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP",
                                                         "DESKTOP_SESSION")).lower()
    if "gnome" in desktop or "ubuntu" in desktop:
        return "gnome"
    if "kde" in desktop or "plasma" in desktop:
        return "kde"
    return "generic"


def report(component: str, detail: str, desktop: str | None = None, rpc: bool = True) -> Path | None:
    """Consigne une défaillance ; ne lève jamais d'exception."""
    desktop = desktop or desktop_name()
    component = re.sub(r"[^a-z0-9_.@-]", "-", component.lower())[:64] or "session"
    local = None
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        local = STATE / f"{desktop}-crash.log"
        with local.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} composant={component}\n{detail.rstrip()}\n")
    except OSError:
        local = None
    print(f"djerba-ai : {desktop}/{component} en mode dégradé : {detail.strip().splitlines()[-1:] or ''}",
          flush=True)
    if rpc:
        try:
            from djerba_ai.core import ipc
            ipc.call("session.report", {"desktop": desktop, "component": component,
                                        "detail": detail[-16000:] or "(vide)"}, timeout=5)
        except Exception:  # noqa: BLE001 - démon root absent : le journal local suffit
            pass
    return local


def guarded(component: str, function: Callable[..., Any], *args: Any, default: Any = None,
            desktop: str | None = None, **kwargs: Any) -> Any:
    """Exécute une étape d'intégration ; en cas d'exception : rapport et valeur par défaut."""
    try:
        return function(*args, **kwargs)
    except Exception:  # noqa: BLE001 - une intégration défaillante ne doit jamais casser la session
        report(component, traceback.format_exc(), desktop)
        return default


def journal_excerpt(units: tuple[str, ...] = GNOME_UNITS, lines: int = 400) -> str:
    argv = ["journalctl", "--user", "-b", "--no-pager", "-o", "short-iso", "-n", str(lines)]
    for unit in units:
        argv += ["-u", unit]
    try:
        output = subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout
        system = subprocess.run(["journalctl", "-b", "--no-pager", "-o", "short-iso", "-n", str(lines),
                                 "_COMM=gnome-shell", "+", "_COMM=gnome-session-binary", "+",
                                 "_COMM=gnome-session-service", "+", "_COMM=gnome-session-c"],
                                capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    kept = [line for line in (output + system).splitlines() if INTERESTING.search(line)]
    return "\n".join(kept[-150:])


def collect_gnome(unit: str = "") -> int:
    """Appelé par systemd (OnFailure=) : rapport de plantage de la session GNOME."""
    excerpt = journal_excerpt()
    detail = (f"Unité en échec : {unit or 'session GNOME'}\n"
              f"Session : {os.environ.get('XDG_SESSION_TYPE', '?')}, "
              f"bureau : {os.environ.get('XDG_CURRENT_DESKTOP', '?')}\n{excerpt or '(journal indisponible)'}")
    report(unit.removesuffix(".service") or "gnome-session", detail, desktop="gnome")
    return 0
