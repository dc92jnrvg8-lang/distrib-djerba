#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Génère la page « Applications » de l'installateur depuis le catalogue.

Le module Calamares netinstall affiche un arbre de cases à cocher : une
branche par catégorie, une case par application, cochée selon le champ
« selected » du catalogue. Chaque application y est représentée par le
pseudo-paquet « djerba-app:<id> », intercepté par le module djerbaapps.
Seules les applications disponibles pour l'architecture construite sont
proposées.

Usage : tools/catalog-to-netinstall.py --arch amd64 [--catalog F] [--output F]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "packages" / "djerba-tools" / "root" / "usr" / "share" / "djerba" / "apps.yaml"
DEFAULT_OUTPUT = ROOT / "config" / "includes.chroot" / "etc" / "calamares" / "modules" / "netinstall.conf"

HEADER = """\
# SPDX-License-Identifier: GPL-3.0-or-later
# Djerba OS — page « Applications » de l'installateur (module netinstall).
# Fichier GÉNÉRÉ par tools/catalog-to-netinstall.py depuis
# /usr/share/djerba/apps.yaml : ne pas éditer.
"""


def build(catalog: dict, arch: str) -> dict:
    groups = []
    for category in catalog["categories"]:
        subgroups = []
        for app in catalog["apps"]:
            if app["category"] != category["id"]:
                continue
            architectures = app.get("architectures") or []
            if architectures and arch not in architectures:
                continue
            description = app.get("description", "")
            if app.get("notice"):
                description = f"{description} {app['notice']}"
            subgroups.append({
                "name": app["name"],
                "description": description,
                "selected": bool(app.get("selected", False)),
                "critical": False,
                "packages": [f"djerba-app:{app['id']}"],
            })
        if subgroups:
            groups.append({
                "name": category["name"],
                "description": category.get("description", ""),
                "expanded": True,
                "critical": False,
                "subgroups": subgroups,
            })
    return {
        "groupsUrl": "local",
        "required": False,
        "label": {
            "sidebar": "Applications",
            "sidebar[fr]": "Applications",
            "title": "Choisissez les applications à installer",
            "title[fr]": "Choisissez les applications à installer",
        },
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arch", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    catalog = yaml.safe_load(args.catalog.read_text(encoding="utf-8"))
    document = build(catalog, args.arch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(HEADER + "---\n" + yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
    count = sum(len(group["subgroups"]) for group in document["groups"])
    print(f"[installateur] {count} applications proposées pour {args.arch} -> {args.output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
