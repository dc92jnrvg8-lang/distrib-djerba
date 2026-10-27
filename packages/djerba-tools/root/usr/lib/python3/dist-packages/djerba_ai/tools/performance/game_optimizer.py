# SPDX-License-Identifier: GPL-3.0-or-later
"""Optimiseur de jeu : bascule toute la session en mode « latence minimale ».

* Processeur : profil « performance » de power-profiles-daemon (gouverneur et
  EPP pilotés de façon cohérente) ; sans power-profiles-daemon, l'agent peut
  enchaîner avec l'outil root ``cpupower_governor`` (cpupower).
* Compositeur : en session X11, KWin est suspendu par D-Bus
  (org.kde.KWin /Compositor suspend) ; sous Wayland, KWin *est* le serveur
  d'affichage et ne peut pas être suspendu : les effets coûteux sont coupés et
  le tearing est autorisé en plein écran (même bénéfice de latence ; les jeux
  plein écran y profitent déjà du « direct scanout »).
* Restauration exacte de l'état précédent (``mode=off``) : profil, effets,
  tearing et reprise du compositeur X11.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from djerba_ai.tools.base import BaseTool, fail, ok, run_command

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "djerba-ai" / "game-optimizer.json"


def _x11() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "") == "x11"


def _compositor(action: str) -> bool:
    result = run_command(["dbus-send", "--session", "--type=method_call", "--dest=org.kde.KWin",
                          "/Compositor", f"org.kde.kwin.Compositing.{action}"], timeout=10)
    return result.returncode == 0


def _gamemode(*arguments: str) -> tuple[bool, str]:
    result = run_command(["djerba-gamemode", *arguments], timeout=60)
    return result.returncode == 0, (result.stdout + result.stderr).strip()


class GameOptimizer(BaseTool):
    name = "game_optimizer"
    category = "performance"
    description = ("Mode jeu extrême : processeur en performance, compositeur KWin suspendu (X11) ou effets "
                   "coupés et tearing autorisé (Wayland) ; mode=off restaure tout.")
    parameters = {
        "mode": {"type": "string", "description": "on, off ou status", "enum": ["on", "off", "status"],
                 "default": "on"},
    }
    keywords = ("jeu", "gaming", "fps", "latence", "performance", "optimiser jeu", "boost", "compositeur")
    binaries = ("djerba-gamemode", "dbus-send")

    def execute(self, params: dict, verbose: bool = False) -> dict:
        mode = params["mode"]
        if mode == "status":
            done, output = _gamemode("status", "--json")
            state = json.loads(output) if done and output.startswith("{") else {}
            state["compositor_suspended"] = STATE.exists() and json.loads(STATE.read_text()).get("suspended", False)
            state["session"] = "x11" if _x11() else "wayland"
            return ok("Mode jeu actif" if state.get("active") else "Mode jeu inactif", **state)

        if mode == "on":
            done, output = _gamemode("on")
            if not done:
                return fail(f"mode jeu : {output}")
            suspended = _x11() and _compositor("suspend")
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({"suspended": suspended}), encoding="utf-8")
            notes = []
            if not shutil.which("powerprofilesctl"):
                notes.append("power-profiles-daemon absent : utilisez l'outil cpupower_governor (root)")
            message = ("Mode jeu activé : profil performance, " +
                       ("compositeur KWin suspendu." if suspended
                        else "effets coupés et tearing autorisé (Wayland : compositeur non suspendable)."))
            return ok(message, compositor_suspended=suspended, notes=notes,
                      **({"details": output} if verbose else {}))

        resumed = False
        if STATE.exists():
            try:
                if json.loads(STATE.read_text()).get("suspended"):
                    resumed = _compositor("resume")
            except ValueError:
                pass
            STATE.unlink(missing_ok=True)
        done, output = _gamemode("off")
        if not done:
            return fail(f"restauration : {output}")
        return ok("Mode normal restauré" + (" (compositeur repris)." if resumed else "."),
                  compositor_resumed=resumed, **({"details": output} if verbose else {}))
