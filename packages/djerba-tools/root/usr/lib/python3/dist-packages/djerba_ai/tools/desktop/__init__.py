# SPDX-License-Identifier: GPL-3.0-or-later
"""Bureau : mode discret, thèmes, écrans (KScreen), son (PipeWire), session."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from djerba_ai.tools.base import command_tool, fail, function_tool, launch_detached, ok, run_command

C = "desktop"
OUTPUT = r"^[A-Za-z0-9_-]{1,40}$"
NAME = r"^[A-Za-z0-9 ._()-]{1,80}$"


def _undercover_status(_params: dict, _verbose: bool) -> dict:
    result = run_command(["djerba-undercover", "status", "--json"], timeout=20)
    try:
        state = json.loads(result.stdout)
    except ValueError:
        return fail("état du mode discret illisible")
    return ok(f"Mode discret {'actif (Windows ' + state['style'] + ')' if state.get('active') else 'inactif'}",
              **state)


def _volume(_params: dict, _verbose: bool) -> dict:
    result = run_command(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=10)
    match = re.search(r"([\d.]+)", result.stdout)
    if result.returncode != 0 or not match:
        return fail("volume indisponible (PipeWire)")
    percent = round(float(match.group(1)) * 100)
    return ok(f"Volume : {percent} %{' (muet)' if 'MUTED' in result.stdout else ''}", percent=percent,
              muted="MUTED" in result.stdout)


def _screenshot(params: dict, _verbose: bool) -> dict:
    pictures = Path(run_command(["xdg-user-dir", "PICTURES"], timeout=5).stdout.strip() or Path.home())
    target = pictures / f"capture-{time.strftime('%Y%m%d-%H%M%S')}.png"
    mode = {"ecran": "-f", "fenetre": "-a", "region": "-r"}[params["mode"]]
    result = run_command(["spectacle", "-b", "-n", mode, "-o", str(target)], timeout=60)
    if result.returncode != 0 or not target.exists():
        return fail("capture impossible")
    return ok(f"Capture enregistrée : {target}", path=str(target))


def _desktop_apps(params: dict, _verbose: bool) -> dict:
    query = params.get("query", "").lower()
    found = []
    directories = [Path("/usr/share/applications"), Path.home() / ".local/share/applications",
                   Path("/var/lib/flatpak/exports/share/applications")]
    for directory in directories:
        for entry in sorted(directory.glob("*.desktop")) if directory.is_dir() else []:
            name, hidden = "", False
            for line in entry.read_text(errors="replace").splitlines():
                if line.startswith("Name=") and not name:
                    name = line[5:]
                if line.strip() in ("NoDisplay=true", "Hidden=true"):
                    hidden = True
            if name and not hidden and (not query or query in name.lower() or query in entry.stem.lower()):
                found.append({"name": name, "id": entry.stem})
    return ok(f"{len(found)} application(s)", apps=found[:200])


def _open_app(params: dict, _verbose: bool) -> dict:
    desktop_id = params["app_id"]
    for directory in (Path("/usr/share/applications"), Path.home() / ".local/share/applications",
                      Path("/var/lib/flatpak/exports/share/applications")):
        if (directory / f"{desktop_id}.desktop").is_file():
            result = run_command(["kioclient", "exec", str(directory / f"{desktop_id}.desktop")], timeout=30)
            if result.returncode == 0:
                return ok(f"{desktop_id} lancé.")
            return fail(result.stderr.strip() or "lancement impossible")
    return fail(f"application {desktop_id} introuvable (voir desktop_apps)")


def _night_light(params: dict, _verbose: bool) -> dict:
    run_command(["kwriteconfig6", "--file", "kwinrc", "--group", "NightColor", "--key", "Active",
                 "--type", "bool", "true" if params["state"] == "on" else "false"], timeout=10)
    run_command(["dbus-send", "--session", "--type=method_call", "--dest=org.kde.KWin", "/KWin",
                 "org.kde.KWin.reconfigure"], timeout=10)
    return ok(f"Couleur de nuit : {params['state']}.")


def _keyboard(params: dict, _verbose: bool) -> dict:
    run_command(["kwriteconfig6", "--file", "kxkbrc", "--group", "Layout", "--key", "Use", "--type", "bool", "true"])
    run_command(["kwriteconfig6", "--file", "kxkbrc", "--group", "Layout", "--key", "LayoutList", params["layout"]])
    run_command(["dbus-send", "--session", "--type=signal", "/Layouts", "org.kde.keyboard.reloadConfig"])
    return ok(f"Disposition du clavier : {params['layout']}.")


TOOLS = [
    command_tool("undercover_on", C, "Mode discret : apparence Windows 10 ou 11, réversible (comme Kali).",
                 ["djerba-undercover", "on", "--style", "{style}"],
                 parameters={"style": {"type": "string", "description": "10 ou 11", "enum": ["10", "11"],
                                       "default": "11"}},
                 timeout=180, keywords=("mode discret", "windows", "undercover", "incognito", "cacher linux"),
                 done="Mode discret actif (Windows {style})."),
    command_tool("undercover_off", C, "Quitte le mode discret et restaure votre bureau à l'identique.",
                 ["djerba-undercover", "off"], timeout=180, keywords=("mode discret", "restaurer bureau"),
                 done="Bureau habituel restauré."),
    function_tool("undercover_status", C, "État du mode discret.", _undercover_status,
                  keywords=("mode discret", "état"), binaries=("djerba-undercover",)),
    command_tool("themes_list", C, "Thèmes globaux Plasma installés.", ["plasma-apply-lookandfeel", "--list"],
                 keywords=("thèmes", "apparence", "look and feel"), done="Thèmes globaux."),
    command_tool("theme_apply", C, "Applique un thème global Plasma (ex. org.djerba.desktop).",
                 ["plasma-apply-lookandfeel", "--apply", "{theme}"],
                 parameters={"theme": {"type": "string", "description": "identifiant",
                                       "pattern": r"^[A-Za-z0-9._-]{3,100}$"}},
                 required=("theme",), dangerous=True, keywords=("changer thème", "apparence"),
                 done="Thème {theme} appliqué."),
    command_tool("colorschemes_list", C, "Palettes de couleurs disponibles.", ["plasma-apply-colorscheme", "--list-schemes"],
                 keywords=("couleurs", "palette"), done="Palettes de couleurs."),
    command_tool("colorscheme_apply", C, "Applique une palette de couleurs (ex. BreezeDark).",
                 ["plasma-apply-colorscheme", "{scheme}"],
                 parameters={"scheme": {"type": "string", "description": "palette", "pattern": r"^[A-Za-z0-9_-]{2,60}$"}},
                 required=("scheme",), keywords=("couleurs", "mode sombre", "mode clair"),
                 done="Palette {scheme} appliquée."),
    command_tool("dark_mode", C, "Bascule le bureau en mode sombre ou clair.",
                 ["plasma-apply-colorscheme", "{scheme}"],
                 parameters={"scheme": {"type": "string", "description": "BreezeDark ou BreezeLight",
                                        "enum": ["BreezeDark", "BreezeLight"]}},
                 required=("scheme",), keywords=("mode sombre", "dark mode", "clair"), done="Palette {scheme}."),
    command_tool("wallpaper_set", C, "Change le fond d'écran.", ["plasma-apply-wallpaperimage", "{path}"],
                 parameters={"path": {"type": "string", "description": "image",
                                      "pattern": r"^/[^\x00]{1,500}\.(png|jpg|jpeg|webp|svg)$"}},
                 required=("path",), keywords=("fond d'écran", "wallpaper"), done="Fond d'écran changé."),
    function_tool("night_light", C, "Couleur de nuit (réduit la lumière bleue).", _night_light,
                  parameters={"state": {"type": "string", "description": "on ou off", "enum": ["on", "off"]}},
                  required=("state",), keywords=("lumière bleue", "nuit", "night color"),
                  binaries=("kwriteconfig6",)),
    command_tool("screens_list", C, "Écrans connectés, résolutions et fréquences (KScreen).",
                 ["kscreen-doctor", "-o"], keywords=("écrans", "résolution", "moniteur", "hz"),
                 env={"NO_COLOR": "1"}, done="Écrans."),
    command_tool("screen_mode", C, "Change la résolution et la fréquence d'un écran (ex. 1920x1080@144).",
                 ["kscreen-doctor", "output.{output}.mode.{mode}"],
                 parameters={"output": {"type": "string", "description": "sortie (ex. DP-1)", "pattern": OUTPUT},
                             "mode": {"type": "string", "description": "LxH@Hz",
                                      "pattern": r"^\d{3,5}x\d{3,5}@\d{2,3}$"}},
                 required=("output", "mode"), dangerous=True, keywords=("résolution", "144 hz", "fréquence"),
                 done="Mode {mode} appliqué à {output}."),
    command_tool("screen_scale", C, "Change la mise à l'échelle d'un écran (1, 1.25, 1.5, 2…).",
                 ["kscreen-doctor", "output.{output}.scale.{scale}"],
                 parameters={"output": {"type": "string", "description": "sortie", "pattern": OUTPUT},
                             "scale": {"type": "string", "description": "facteur",
                                       "enum": ["1", "1.25", "1.5", "1.75", "2", "2.5", "3"]}},
                 required=("output", "scale"), keywords=("échelle", "hidpi", "taille texte"),
                 done="Échelle {scale} pour {output}."),
    command_tool("screen_vrr", C, "Active la fréquence variable (FreeSync/VRR) d'un écran.",
                 ["kscreen-doctor", "output.{output}.vrrpolicy.{policy}"],
                 parameters={"output": {"type": "string", "description": "sortie", "pattern": OUTPUT},
                             "policy": {"type": "string", "description": "never, always ou automatic",
                                        "enum": ["never", "always", "automatic"]}},
                 required=("output", "policy"), keywords=("freesync", "vrr", "g-sync", "fréquence variable"),
                 done="VRR {policy} pour {output}."),
    function_tool("volume_get", C, "Volume de la sortie audio par défaut.", _volume,
                  keywords=("volume", "son"), binaries=("wpctl",)),
    command_tool("volume_set", C, "Règle le volume de la sortie audio (0 à 150 %).",
                 ["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", "{percent}%"],
                 parameters={"percent": {"type": "integer", "description": "pourcentage", "minimum": 0,
                                         "maximum": 150}},
                 required=("percent",), keywords=("volume", "son", "monter le son", "baisser le son", "trop fort", "pas assez fort"),
                 done="Volume : {percent} %."),
    command_tool("mute_toggle", C, "Coupe ou rétablit le son.", ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
                 keywords=("muet", "couper le son", "mute"), done="Son basculé."),
    command_tool("audio_devices", C, "Périphériques audio (sorties, entrées) et identifiants PipeWire.",
                 ["wpctl", "status"], keywords=("audio", "casque", "haut-parleurs", "micro", "pipewire"),
                 done="Périphériques audio."),
    command_tool("audio_default", C, "Choisit la sortie ou l'entrée audio par défaut (identifiant de wpctl status).",
                 ["wpctl", "set-default", "{id}"],
                 parameters={"id": {"type": "integer", "description": "identifiant", "minimum": 1}},
                 required=("id",), keywords=("sortie audio", "casque par défaut"), done="Périphérique {id} par défaut."),
    command_tool("notify", C, "Affiche une notification de bureau.",
                 ["notify-send", "--app-name=Djerba", "--icon=djerba-os", "{title}", "{body}"],
                 parameters={"title": {"type": "string", "description": "titre", "pattern": r"^[^\x00]{1,120}$"},
                             "body": {"type": "string", "description": "texte", "pattern": r"^[^\x00]{0,1000}$",
                                      "default": ""}},
                 required=("title",), keywords=("notification", "rappel"), done="Notification affichée."),
    function_tool("screenshot", C, "Capture d'écran (écran, fenêtre active ou région).", _screenshot,
                  parameters={"mode": {"type": "string", "description": "ecran, fenetre ou region",
                                       "enum": ["ecran", "fenetre", "region"], "default": "ecran"}},
                  keywords=("capture", "screenshot", "copie d'écran"), binaries=("spectacle", "xdg-user-dir")),
    function_tool("desktop_apps", C, "Liste les applications installées (menu).", _desktop_apps,
                  parameters={"query": {"type": "string", "description": "filtre", "pattern": NAME, "default": ""}},
                  keywords=("applications installées", "logiciels", "menu")),
    function_tool("open_app", C, "Lance une application par son identifiant de menu (ex. org.kde.dolphin).",
                  _open_app,
                  parameters={"app_id": {"type": "string", "description": "identifiant .desktop",
                                         "pattern": r"^[A-Za-z0-9._-]{2,120}$"}},
                  required=("app_id",), keywords=("ouvrir", "lancer", "démarrer application"), binaries=("kioclient",)),
    command_tool("lock_screen", C, "Verrouille l'écran.", ["loginctl", "lock-session"],
                 keywords=("verrouiller", "lock"), done="Écran verrouillé."),
    function_tool("keyboard_layout", C, "Change la disposition du clavier (ex. fr, us, be, ch).", _keyboard,
                  parameters={"layout": {"type": "string", "description": "disposition",
                                         "pattern": r"^[a-z]{2,3}(,[a-z]{2,3}){0,3}$"}},
                  required=("layout",), keywords=("clavier", "azerty", "qwerty", "disposition"),
                  binaries=("kwriteconfig6",)),
    function_tool("open_welcome", C, "Ouvre l'assistant Djerba OS sur une page.",
                  lambda params, _v: ok("Assistant ouvert.") if launch_detached(
                      ["djerba-welcome", "--page", params["page"]]) else fail("assistant introuvable"),
                  parameters={"page": {"type": "string", "description": "page",
                                       "enum": ["home", "apps", "gaming", "privacy", "system"], "default": "home"}},
                  keywords=("assistant", "bienvenue"), binaries=("djerba-welcome",)),
]

