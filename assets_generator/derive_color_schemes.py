#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dérive les schémas de couleurs Djerba des schémas Breeze (paquet Debian breeze, GPL-2.0-or-later).

Changements, et eux seuls :
* sélection et décorations (focus, survol) en turquoise foncé : texte blanc ≥ 4,5:1 (le turquoise vif
  #00C2CB ne donne que 2,2:1) ;
* effet « désactivé » moins agressif (ContrastAmount) : textes désactivés ≥ 3:1 ;
* schéma clair : ForegroundInactive un peu plus sombre (textes d'aide désactivés ≥ 3:1).

Valeurs retenues par mesure (/usr/libexec/djerba-contrast, 26/26 paires) :
    derive_color_schemes.py /usr/share/color-schemes/BreezeDark.colors DjerbaSombre.colors "Djerba Sombre" 0,122,130 0.35 0.1
    derive_color_schemes.py /usr/share/color-schemes/BreezeLight.colors DjerbaClair.colors "Djerba Clair" 0,115,122 0.2 0.1 100,110,121
"""

from __future__ import annotations

import configparser
import sys


def derive(source: str, target: str, name: str, selection: str, contrast_amount: float, intensity: float,
           inactive: str | None = None) -> None:
    scheme = configparser.RawConfigParser()
    scheme.optionxform = str  # type: ignore[assignment,method-assign]  # clés KConfig sensibles à la casse
    scheme.read(source)
    scheme["General"]["Name"] = name
    scheme["General"]["ColorScheme"] = name.replace(" ", "")
    for group in [section for section in scheme.sections() if section.startswith("Colors:")]:
        scheme[group]["DecorationFocus"] = selection
        scheme[group]["DecorationHover"] = selection
        if inactive and group != "Colors:Selection":
            scheme[group]["ForegroundInactive"] = inactive
    scheme["Colors:Selection"]["BackgroundNormal"] = selection
    scheme["Colors:Selection"]["ForegroundNormal"] = "255,255,255"
    scheme["ColorEffects:Disabled"]["ContrastAmount"] = str(contrast_amount)
    scheme["ColorEffects:Disabled"]["IntensityAmount"] = str(intensity)
    with open(target, "w", encoding="utf-8") as handle:
        scheme.write(handle, space_around_delimiters=False)


if __name__ == "__main__":
    if len(sys.argv) not in (7, 8):
        print(__doc__)
        sys.exit(2)
    derive(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]), float(sys.argv[6]),
           sys.argv[7] if len(sys.argv) == 8 else None)
