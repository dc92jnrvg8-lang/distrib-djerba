# SPDX-License-Identifier: GPL-3.0-or-later
"""Couche d'abstraction du bureau (KDE Plasma 6 / GNOME Shell 46+).

L'agent ne connaît qu'une interface, ``DesktopAdapter`` ; chaque bureau en
fournit l'implémentation native :

============================  ==============================  ================================
Fonction                      KDE Plasma                      GNOME Shell
============================  ==============================  ================================
fenêtre active                script KWin → D-Bus             extension GNOME (Mutter) → D-Bus
raccourcis globaux            kglobalaccelrc + KGlobalAccel   GSettings (media-keys, clés perso)
thème clair / sombre          kdeglobals                      org.gnome.desktop.interface
intégrations                  Dolphin (menu de service),      Nautilus (python3-nautilus),
                              Kate (outils externes)          Gedit (greffon Python)
============================  ==============================  ================================

Le bureau est détecté à chaque ouverture de session (XDG_CURRENT_DESKTOP,
processus du shell) ; le démon utilisateur appelle ``sync`` : si
l'utilisateur a changé de bureau, raccourcis, intégrations et source de la
fenêtre active sont rétablis pour le nouveau, sans rien perdre — les
réglages de Djerba (/etc/djerba/config.toml) ne dépendent d'aucun bureau.

Les appels D-Bus vers le compositeur passent par des processus distincts
(dbus-send, gnome-extensions, gsettings) : jamais d'appel synchrone depuis
le processus qui sert lui-même le service D-Bus du bus de contexte.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from djerba_ai.core import shortcuts

SHARE = Path(os.environ.get("DJERBA_AI_SHARE", "/usr/share/djerba-ai"))
KWIN_SCRIPT = Path(os.environ.get("DJERBA_KWIN_SCRIPT", str(SHARE / "kwin" / "active-window.js")))
GNOME_EXTENSION = "djerba@djerba-os.org"
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "djerba-ai" / "desktop.json"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/djerba-{name}/"
COMMANDS = {"spotlight": "djerba-ai --hud", "clipboard": "djerba-ai --clipboard"}
LABELS = {"spotlight": "Djerba : anneau d'action (HUD)", "clipboard": "Djerba : presse-papiers"}


def run(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def to_gnome(sequence: str) -> str:
    """« Meta+Shift+C » → « <Super><Shift>c » (format des raccourcis GNOME)."""
    *mods, key = shortcuts.validate(sequence).split("+")
    names = {"Meta": "<Super>", "Ctrl": "<Control>", "Alt": "<Alt>", "Shift": "<Shift>"}
    keys = {"Space": "space", "Return": "Return", "Escape": "Escape", "Tab": "Tab", "Delete": "Delete"}
    return "".join(names[mod] for mod in mods) + keys.get(key, key.lower() if len(key) == 1 else key)


class DesktopAdapter(ABC):
    name = "generic"
    label = "Bureau inconnu"

    @abstractmethod
    def start_window_source(self) -> str:
        """Active la remontée de la fenêtre active vers org.djerba.ai.Context ; renvoie un état."""

    @abstractmethod
    def apply_shortcut(self, name: str, sequence: str) -> dict:
        """Enregistre et applique immédiatement un raccourci global."""

    def prefers_dark(self) -> bool | None:
        return None

    def install_integrations(self) -> list[str]:
        """Intégrations propres à l'utilisateur (applications du bureau) ; renvoie ce qui a été installé."""
        return []

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label}


# ---------------------------------------------------------------- KDE Plasma
class KdeAdapter(DesktopAdapter):
    name = "kde"
    label = "KDE Plasma"
    KATE_TOOLS = {
        "djerba-expliquer": ("Djerba : expliquer la sélection", "Explique ce code ou ce texte"),
        "djerba-tests": ("Djerba : écrire des tests", "Écris des tests unitaires pour ce code"),
        "djerba-corriger": ("Djerba : corriger le texte", "Corrige l'orthographe et la grammaire de ce texte"),
    }

    def start_window_source(self) -> str:
        if not KWIN_SCRIPT.is_file():
            return "script KWin absent"
        if shutil.which("dbus-send") is None:
            return "dbus-send absent"
        plugin = "djerba-active-window"
        base = ["dbus-send", "--session", "--print-reply", "--dest=org.kde.KWin", "/Scripting"]
        run([*base, "org.kde.kwin.Scripting.unloadScript", f"string:{plugin}"])
        loaded = run([*base, "org.kde.kwin.Scripting.loadScript", f"string:{KWIN_SCRIPT}", f"string:{plugin}"])
        if loaded.returncode != 0:
            return f"KWin injoignable ({loaded.stderr.strip()[:120]})"
        run([*base, "org.kde.kwin.Scripting.start"])
        return "actif (KWin)"

    def apply_shortcut(self, name: str, sequence: str) -> dict:
        return shortcuts.apply(name, sequence)

    def prefers_dark(self) -> bool | None:
        try:
            text = (CONFIG_HOME / "kdeglobals").read_text(errors="replace")
        except OSError:
            return None
        match = re.search(r"^ColorScheme=(.+)$", text, re.MULTILINE)
        return bool(match and re.search(r"dark|sombre|nuit|night", match.group(1), re.IGNORECASE))

    def install_integrations(self) -> list[str]:
        """Outils externes de Kate (~/.config/kate/externaltools/*.ini) ; Dolphin est servi par le paquet."""
        directory = CONFIG_HOME / "kate" / "externaltools"
        installed = []
        for slug, (label, instruction) in self.KATE_TOOLS.items():
            path = directory / f"{slug}.ini"
            if path.exists():
                continue
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "[General]\n"
                f"name={label}\nicon=djerba-ai\nexecutable=djerba-ai\n"
                f"arguments=--ask-selection \"{instruction}\" \"%{{Document:FileName}}\"\n"
                "input=%{Document:Selection:Text}\nworkingDir=%{Document:Path}\nmimetypes=\n"
                f"actionName=externaltool_{slug.replace('-', '')}\ncmdname={slug}\n"
                "save=None\nreload=false\noutput=Ignore\ntrigger=None\n", encoding="utf-8")
            installed.append(f"Kate : {label}")
        return installed


# ---------------------------------------------------------------- GNOME Shell
class GnomeAdapter(DesktopAdapter):
    name = "gnome"
    label = "GNOME Shell"

    @staticmethod
    def gsettings(*args: str) -> subprocess.CompletedProcess:
        return run(["gsettings", *args])

    def start_window_source(self) -> str:
        if shutil.which("gnome-extensions") is None:
            return "gnome-extensions absent"
        enabled = run(["gnome-extensions", "enable", GNOME_EXTENSION])
        if enabled.returncode != 0:
            return f"extension GNOME indisponible ({enabled.stderr.strip()[:120]})"
        info = run(["gnome-extensions", "info", GNOME_EXTENSION]).stdout
        if re.search(r"State:\s*(ACTIVE|ENABLED)", info):
            return "actif (Mutter)"
        # Sous Wayland, une extension nouvellement installée n'est chargée qu'à la session suivante.
        return "extension activée : effective à la prochaine ouverture de session"

    def apply_shortcut(self, name: str, sequence: str) -> dict:
        if name not in COMMANDS:
            raise shortcuts.ShortcutError(f"raccourci inconnu : {name}")
        binding = to_gnome(sequence)
        if self.gsettings("list-schemas").stdout.find(MEDIA_KEYS) < 0:
            return {"name": name, "sequence": sequence, "applied": False, "detail": "schéma GNOME absent",
                    "message": "GNOME n'est pas installé : raccourci non appliqué."}
        path = KEYBINDING_PATH.format(name=name)
        current = self.gsettings("get", MEDIA_KEYS, "custom-keybindings").stdout.strip()
        paths = re.findall(r"'([^']+)'", current)
        if path not in paths:
            paths.append(path)
            self.gsettings("set", MEDIA_KEYS, "custom-keybindings", "[" + ", ".join(f"'{p}'" for p in paths) + "]")
        schema = f"{MEDIA_KEYS}.custom-keybinding:{path}"
        for key, value in (("name", LABELS[name]), ("command", COMMANDS[name]), ("binding", binding)):
            result = self.gsettings("set", schema, key, value)
            if result.returncode != 0:
                return {"name": name, "sequence": sequence, "applied": False, "detail": result.stderr.strip(),
                        "message": f"Raccourci non appliqué : {result.stderr.strip()[:120]}"}
        self._release_conflict(binding)
        return {"name": name, "sequence": sequence, "applied": True, "detail": "",
                "message": f"Raccourci {sequence} ({binding}) appliqué à GNOME."}

    def _release_conflict(self, binding: str) -> None:
        """GNOME attribue Super+Espace au changement de disposition du clavier : on le libère
        (l'ancienne valeur est gardée pour la restauration, et Super+Maj+Espace reste disponible)."""
        schema, key = "org.gnome.desktop.wm.keybindings", "switch-input-source"
        current = self.gsettings("get", schema, key).stdout.strip()
        values = re.findall(r"'([^']+)'", current)
        if binding.lower() in (value.lower() for value in values):
            remember({"gnome_switch_input_source": current})
            kept = [value for value in values if value.lower() != binding.lower()]
            self.gsettings("set", schema, key, "[" + ", ".join(f"'{value}'" for value in kept) + "]")

    def prefers_dark(self) -> bool | None:
        value = self.gsettings("get", "org.gnome.desktop.interface", "color-scheme").stdout
        return "dark" in value if value else None

    def install_integrations(self) -> list[str]:
        """Greffon Gedit (copié pour l'utilisateur, activé) ; Nautilus est servi par le paquet."""
        installed = []
        source = SHARE / "integrations" / "gedit"
        target = DATA_HOME / "gedit" / "plugins"
        if source.is_dir() and shutil.which("gedit") and not (target / "djerba.plugin").exists():
            target.mkdir(parents=True, exist_ok=True)
            for item in source.iterdir():
                shutil.copy2(item, target / item.name)
            active = self.gsettings("get", "org.gnome.gedit.plugins", "active-plugins").stdout
            names = re.findall(r"'([^']+)'", active)
            if "djerba" not in names:
                names.append("djerba")
                self.gsettings("set", "org.gnome.gedit.plugins", "active-plugins",
                               "[" + ", ".join(f"'{n}'" for n in names) + "]")
            installed.append("Gedit : greffon Djerba")
        return installed


class GenericAdapter(DesktopAdapter):
    def start_window_source(self) -> str:
        return "bureau non pris en charge : fenêtre active indisponible"

    def apply_shortcut(self, name: str, sequence: str) -> dict:
        return {"name": name, "sequence": sequence, "applied": False, "detail": "bureau non pris en charge",
                "message": "Bureau non pris en charge : définissez le raccourci dans ses réglages "
                           f"(commande : {COMMANDS.get(name, 'djerba-ai')})."}


# ---------------------------------------------------------------- Détection et synchronisation
def _running(process: str) -> bool:
    return run(["pgrep", "-u", str(os.getuid()), "-x", process], timeout=5).returncode == 0


def detect(environ: dict | None = None, probe: bool = True) -> DesktopAdapter:
    environ = os.environ if environ is None else environ
    desktop = ":".join(environ.get(key, "") for key in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP",
                                                         "DESKTOP_SESSION")).lower()
    if "kde" in desktop or "plasma" in desktop:
        return KdeAdapter()
    if "gnome" in desktop or "ubuntu" in desktop:
        return GnomeAdapter()
    if probe:
        if _running("plasmashell") or _running("kwin_wayland"):
            return KdeAdapter()
        if _running("gnome-shell"):
            return GnomeAdapter()
    return GenericAdapter()


def remember(values: dict) -> dict:
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    state.update(values)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    return state


def sync(config, adapter: DesktopAdapter | None = None) -> dict:
    """À l'ouverture de session : applique raccourcis et intégrations au bureau courant."""
    adapter = adapter or detect()
    try:
        previous = json.loads(STATE.read_text()).get("desktop")
    except (OSError, ValueError):
        previous = None
    report = {"desktop": adapter.name, "switched": previous not in (None, adapter.name), "shortcuts": [],
              "integrations": adapter.install_integrations()}
    for name in COMMANDS:
        try:
            report["shortcuts"].append(adapter.apply_shortcut(name, str(config.get(f"shortcuts.{name}"))))
        except shortcuts.ShortcutError as exc:
            report["shortcuts"].append({"name": name, "applied": False, "message": str(exc)})
    remember({"desktop": adapter.name})
    return report
