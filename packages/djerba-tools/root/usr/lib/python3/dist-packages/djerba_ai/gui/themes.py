# SPDX-License-Identifier: GPL-3.0-or-later
"""Thèmes de l'interface de Djerba : fichiers de jetons de style, sans code.

Un thème est un objet JSON dont chaque clé est connue (``SCHEMA``) : couleurs
``#RRGGBB``, opacités entre 0 et 1, booléen ``dark``, nom. Toute autre clé,
tout autre type ou toute valeur hors bornes est refusé : un thème importé ne
peut contenir ni code ni QML. Le contraste des paires (texte, fond) utilisées
par l'interface est vérifié (``check_contrast``, WCAG 2.1 AA : 4,5:1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

THEMES_DIR = Path(__file__).resolve().parent / "themes"
COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
COLORS = ("night", "deep", "deepEnd", "surface", "field", "turquoise", "accentText", "textOnAccent", "aqua", "coral",
          "textOnCoral", "sand", "white", "muted", "veil")
SCHEMA = {"name": str, "dark": bool, "panelOpacity": float, "windowOpacity": float, **{key: str for key in COLORS}}
# Un nom « onXxx » serait pris par QML pour un gestionnaire de signal : la couleur n'y arriverait jamais (vu : noir).
assert not [key for key in SCHEMA if re.match(r"on[A-Z]", key)], "jeton nommé comme un gestionnaire de signal QML"
OPACITY = {"panelOpacity": (0.9, 1.0), "windowOpacity": (0.9, 1.0)}
# Paires (texte, fond) affichées par l'interface. Les fonds des panneaux sont mesurés tels qu'ils sont
# composés à l'écran (panneau à son opacité, sur la fin du dégradé de la fenêtre : cas le moins favorable).
PAIRS = [("white", "panel"), ("muted", "panel"), ("white", "field"), ("muted", "field"), ("white", "night"),
         ("muted", "night"), ("accentText", "panel"), ("textOnAccent", "turquoise"), ("textOnCoral", "coral"),
         ("aqua", "panel"), ("sand", "panel")]
AA = 4.5


class ThemeError(ValueError):
    """Thème invalide (clé inconnue ou manquante, type, couleur ou opacité hors bornes)."""


def validate(data: object) -> dict:
    if not isinstance(data, dict):
        raise ThemeError("un thème est un objet JSON")
    unknown = sorted(set(data) - set(SCHEMA))
    missing = sorted(set(SCHEMA) - set(data))
    if unknown or missing:
        raise ThemeError(f"clés inconnues : {unknown} ; manquantes : {missing}")
    for key, kind in SCHEMA.items():
        value = data[key]
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise ThemeError(f"{key} : type attendu {kind.__name__}")
        if key in COLORS and not COLOR.match(value):
            raise ThemeError(f"{key} : couleur #RRGGBB attendue")
        if key in OPACITY and not OPACITY[key][0] <= value <= OPACITY[key][1]:
            raise ThemeError(f"{key} : entre {OPACITY[key][0]} et {OPACITY[key][1]}")
        if key == "name" and not 1 <= len(value) <= 40:
            raise ThemeError("name : 1 à 40 caractères")
    return {key: (float(data[key]) if SCHEMA[key] is float else data[key]) for key in SCHEMA}


def load(name: str) -> dict:
    if not re.fullmatch(r"[a-z0-9-]{1,32}", name):
        raise ThemeError("nom de thème invalide")
    return validate(json.loads((THEMES_DIR / f"{name}.json").read_text(encoding="utf-8")))


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in rgb)


def blend(top: str, bottom: str, opacity: float) -> str:
    return _hex(tuple(a * opacity + b * (1 - opacity) for a, b in zip(_rgb(top), _rgb(bottom))))  # type: ignore[arg-type]


def contrast(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        def channel(value: float) -> float:
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        red, green, blue = (channel(value) for value in _rgb(color))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def check_contrast(theme: dict) -> list[dict]:
    """Contraste de chaque paire affichée ; ``ok`` faux sous 4,5:1."""
    colors = {**theme, "panel": blend(theme["surface"], theme["deepEnd"], theme["panelOpacity"])}
    rows = []
    for text, background in PAIRS:
        ratio = contrast(colors[text], colors[background])
        rows.append({"texte": text, "fond": background, "contraste": round(ratio, 2), "ok": ratio >= AA})
    return rows


def all_themes() -> dict[str, dict]:
    return {path.stem: load(path.stem) for path in sorted(THEMES_DIR.glob("*.json"))}
