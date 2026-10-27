# SPDX-License-Identifier: GPL-3.0-or-later
"""Raccourcis globaux de Djerba (Plasma 6 / KGlobalAccel).

Chaque raccourci lance un fichier .desktop (« Spotlight » et « presse-papiers »).
Le nouveau raccourci est écrit dans ~/.config/kglobalaccelrc (groupe
[services][<fichier>.desktop], clé _launch), puis appliqué immédiatement au
démon KGlobalAccel par D-Bus (org.kde.kglobalaccel /kglobalaccel :
doRegister + setForeignShortcut). Sans session Plasma joignable, il sera pris
en compte à la prochaine ouverture de session.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
KGLOBALACCELRC = CONFIG_HOME / "kglobalaccelrc"
SHORTCUTS = {
    "spotlight": {"desktop": "djerba-ai-spotlight.desktop", "label": "Djerba Spotlight",
                  "action": "Ouvrir la barre Djerba"},
    "clipboard": {"desktop": "djerba-ai-clipboard.desktop", "label": "Djerba — presse-papiers",
                  "action": "Analyser le presse-papiers"},
}
SEQUENCE = re.compile(r"^((Meta|Ctrl|Alt|Shift)\+){1,4}[A-Za-z0-9]{1,12}$|^F([1-9]|1[0-9]|2[0-4])$")
MODIFIERS = {"Shift": 0x02000000, "Ctrl": 0x04000000, "Alt": 0x08000000, "Meta": 0x10000000}
KEYS = {"Space": 0x20, "Return": 0x01000004, "Enter": 0x01000005, "Tab": 0x01000001, "Backspace": 0x01000003,
        "Escape": 0x01000000, "Insert": 0x01000006, "Delete": 0x01000007, "Home": 0x01000010,
        "End": 0x01000011, "Left": 0x01000012, "Up": 0x01000013, "Right": 0x01000014, "Down": 0x01000015,
        "PgUp": 0x01000016, "PgDown": 0x01000017, "Print": 0x01000009, "Pause": 0x01000008}


class ShortcutError(ValueError):
    pass


def validate(sequence: str) -> str:
    sequence = sequence.strip()
    if not SEQUENCE.fullmatch(sequence):
        raise ShortcutError(f"raccourci invalide : {sequence!r} (ex. Meta+Space, Meta+Shift+C)")
    *mods, key = sequence.split("+")
    if len(set(mods)) != len(mods) or key in MODIFIERS:
        raise ShortcutError(f"raccourci invalide : {sequence!r}")
    if not mods and not key.startswith("F"):
        raise ShortcutError("un raccourci global doit comporter une touche de modification (Meta, Ctrl, Alt)")
    if mods == ["Shift"]:
        raise ShortcutError("Maj seule ne suffit pas pour un raccourci global")
    return sequence


def key_code(sequence: str) -> int:
    """Combinaison Qt (modificateurs | touche), comme QKeySequence(...)[0].toCombined()."""
    *mods, key = sequence.split("+")
    code = sum(MODIFIERS[mod] for mod in mods)
    if key in KEYS:
        return code | KEYS[key]
    if re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key):
        return code | (0x01000030 + int(key[1:]) - 1)
    if len(key) == 1:
        return code | ord(key.upper())
    raise ShortcutError(f"touche non prise en charge : {key}")


# ---------------------------------------------------------------- kglobalaccelrc (format KConfig)
def _group_header(desktop: str) -> str:
    return f"[services][{desktop}]"


def read_config(path: Path = KGLOBALACCELRC) -> dict[str, str]:
    """Raccourcis Djerba actuellement enregistrés (nom → séquence)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found, current = {}, None
    headers = {_group_header(info["desktop"]): name for name, info in SHORTCUTS.items()}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            current = headers.get(stripped)
        elif current and stripped.startswith("_launch="):
            found[current] = stripped.split("=", 1)[1].split("\t")[0]
    return found


def write_config(name: str, sequence: str, path: Path = KGLOBALACCELRC) -> None:
    header = _group_header(SHORTCUTS[name]["desktop"])
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    output, inside, written = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if inside and not written:
                output.append(f"_launch={sequence}")
                written = True
            inside = stripped == header
        elif inside and stripped.startswith("_launch="):
            if not written:
                output.append(f"_launch={sequence}")
                written = True
            continue
        output.append(line)
    if inside and not written:
        output.append(f"_launch={sequence}")
        written = True
    if not written:
        if output and output[-1].strip():
            output.append("")
        output += [header, f"_launch={sequence}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".kglobalaccelrc.djerba-tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    os.replace(temporary, path)


# ---------------------------------------------------------------- D-Bus KGlobalAccel
def dbus_apply(name: str, sequence: str) -> str:
    """Applique le raccourci au démon KGlobalAccel ; renvoie '' ou la raison d'un échec."""
    try:
        import dbus
    except ImportError:
        return "python3-dbus absent"
    info = SHORTCUTS[name]
    action = dbus.Array([info["desktop"], "_launch", info["label"], info["action"]], signature="s")
    try:
        bus = dbus.SessionBus()
        accel = dbus.Interface(bus.get_object("org.kde.kglobalaccel", "/kglobalaccel"), "org.kde.KGlobalAccel")
        accel.doRegister(action)
        accel.setForeignShortcut(action, dbus.Array([key_code(sequence)], signature="i"))
    except dbus.exceptions.DBusException as exc:
        return exc.get_dbus_message() or str(exc)
    return ""


def apply(name: str, sequence: str, *, live: bool = True) -> dict:
    if name not in SHORTCUTS:
        raise ShortcutError(f"raccourci inconnu : {name}")
    sequence = validate(sequence)
    write_config(name, sequence)
    problem = dbus_apply(name, sequence) if live else "application immédiate non demandée"
    return {"name": name, "sequence": sequence, "applied": not problem, "detail": problem,
            "message": f"Raccourci {sequence} enregistré" + (" et actif." if not problem else
                                                            " ; il sera actif à la prochaine session.")}
