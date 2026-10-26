#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Générateur de l'identité visuelle de Djerba OS.

Produit, à partir d'un unique jeu de jetons de design et des templates Jinja2
de ``assets_generator/templates`` :

* GRUB (EFI et BIOS) : ``config/bootloaders/grub-pc/`` — splash 1920×1080,
  thème ``live-theme/theme.txt``, surbrillance en verre 9 tranches,
  ``config.cfg`` (résolution, délai, console série) ;
* Plymouth : thème script ``djerba`` dont l'animation est rendue image par
  image depuis une scène QtQuick (``plymouth.qml.j2``) ;
* SDDM : thème ``djerba`` (QML Qt 5, verre dépoli, particules) ;
* Calamares : branding ``djerba`` (branding.desc, diaporama QML, QSS) ;
* Plasma : fond d'écran ``Djerba`` (3840×2160, 1920×1080, aperçu) ;
* Assistant d'accueil : logo, icône et texture de particule.

Toutes les sorties sont déterministes et écrites sous ``config/`` (ignorées
par git) ; le script est relancé à chaque construction par build.sh ou la CI.

Dépendances (APT uniquement) : python3-jinja2, python3-cairosvg, python3-pil,
python3-pyqt6, python3-pyqt6.qtquick, qml6-module-qtquick-shapes, fonts-inter.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import cairosvg
import jinja2
from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Path(__file__).resolve().parent / "templates"
# Sorties : identité durable empaquetée dans djerba-branding (installée sur
# le système live comme sur la cible) ; branding Calamares propre au live.
BRANDING = ROOT / "build" / "djerba-branding"
CHROOT = ROOT / "config" / "includes.chroot"

PRODUCT = "Djerba OS"
PROJECT_URL = "https://github.com/dc92jnrvg8-lang/distrib-djerba"
TAGLINE = "Léger · Sécurisé · Taillé pour le jeu"

# --------------------------------------------------------------------------
# Jetons de design — palette « lagon de Djerba »
# --------------------------------------------------------------------------
COLORS: dict[str, str] = {
    "night": "#070B18",      # nuit méditerranéenne (fond principal)
    "deep": "#0A1F3D",       # eau profonde
    "lagoon": "#0E5E7E",     # lagon
    "turquoise": "#00C2CB",  # accent principal
    "aqua": "#5EEAD4",       # écume
    "sand": "#F4E1C1",       # sable
    "coral": "#FF7A59",      # soleil couchant
    "white": "#F8FAFC",      # chaux des menzels
    "muted": "#94A3B8",      # texte secondaire
}

PLYMOUTH_FRAMES = 60
PLYMOUTH_RING = 360

SLIDES = [
    {
        "title": "Bienvenue sur Djerba OS",
        "body": "Un système léger, sûr et taillé pour le jeu, construit sur Debian 13 « trixie ».",
        "accent": COLORS["turquoise"],
    },
    {
        "title": "Vos applications, votre choix",
        "body": "Steam, Lutris, Heroic, Bottles, Discord, OBS… Les applications cochées s’installent "
                "dès le premier démarrage, avec une barre de progression dans l’assistant.",
        "accent": COLORS["coral"],
    },
    {
        "title": "Prêt pour le jeu",
        "body": "Noyau à préemption complète, GameMode, MangoHud, vkBasalt et Vulkan ; "
                "le mode jeu libère toutes les performances en un clic.",
        "accent": COLORS["aqua"],
    },
    {
        "title": "Mode anonyme et mode discret",
        "body": "Tout le trafic via Tor avec coupe-circuit anti-fuite, et une apparence façon "
                "Windows 10 ou 11 pour passer inaperçu en public.",
        "accent": COLORS["sand"],
    },
    {
        "title": "Sécurité par défaut",
        "body": "Pare-feu UFW, antivirus ClamAV, profils AppArmor et mises à jour de sécurité "
                "Debian automatiques.",
        "accent": COLORS["turquoise"],
    },
    {
        "title": "BTRFS et zstd",
        "body": "Sous-volumes @ et @home, compression transparente et options optimisées pour SSD.",
        "accent": COLORS["coral"],
    },
]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def hex_to_unit(value: str) -> tuple[float, float, float]:
    r, g, b = hex_to_rgb(value)
    return round(r / 255, 4), round(g / 255, 4), round(b / 255, 4)


@dataclass
class Context:
    version: str
    env: jinja2.Environment

    def template(self, name: str) -> jinja2.Template:
        return self.env.get_template(name)


def log(message: str) -> None:
    print(f"[assets] {message}", flush=True)


def write_text(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    path.chmod(mode)


def reset_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


# --------------------------------------------------------------------------
# Primitives vectorielles
# --------------------------------------------------------------------------
def logo_fragment(prefix: str = "lg") -> str:
    """Logo Djerba OS : monogramme « D » dont l'œil est une arche de menzel
    djerbien, ouverte sur un soleil couchant et le lagon, dans un carré de verre.

    Fragment SVG pour un viewBox 0 0 256 256 ; ``prefix`` rend les
    identifiants uniques lorsque le logo est inséré dans un autre document.
    """
    c = COLORS
    p = prefix
    # Arche en fer à cheval : piédroits verticaux, voûte légèrement outrepassée.
    arch = "M104 182 V118 A28 28 0 0 1 160 118 V182 Z"
    letter = "M80 46 H126 A82 82 0 0 1 126 210 H80 A14 14 0 0 1 66 196 V60 A14 14 0 0 1 80 46 Z"
    return f"""
    <defs>
      <linearGradient id="{p}Tile" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="{c['deep']}"/>
        <stop offset="0.55" stop-color="{c['night']}"/>
        <stop offset="1" stop-color="{c['lagoon']}"/>
      </linearGradient>
      <radialGradient id="{p}Halo" cx="0.5" cy="0.62" r="0.55">
        <stop offset="0" stop-color="{c['turquoise']}" stop-opacity="0.55"/>
        <stop offset="1" stop-color="{c['turquoise']}" stop-opacity="0"/>
      </radialGradient>
      <linearGradient id="{p}Letter" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="{c['white']}"/>
        <stop offset="1" stop-color="{c['sand']}"/>
      </linearGradient>
      <linearGradient id="{p}Sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="{c['coral']}"/>
        <stop offset="1" stop-color="{c['sand']}"/>
      </linearGradient>
      <linearGradient id="{p}Sea" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="{c['turquoise']}"/>
        <stop offset="1" stop-color="{c['aqua']}"/>
      </linearGradient>
      <linearGradient id="{p}Edge" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.70"/>
        <stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0.08"/>
        <stop offset="1" stop-color="#FFFFFF" stop-opacity="0.40"/>
      </linearGradient>
      <linearGradient id="{p}Shine" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.20"/>
        <stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0"/>
      </linearGradient>
      <clipPath id="{p}Arch"><path d="{arch}"/></clipPath>
    </defs>
    <rect x="16" y="16" width="224" height="224" rx="62" fill="url(#{p}Tile)"/>
    <rect x="16" y="16" width="224" height="224" rx="62" fill="url(#{p}Halo)"/>
    <path d="{letter} {arch}" fill="url(#{p}Letter)" fill-rule="evenodd"/>
    <g clip-path="url(#{p}Arch)">
      <rect x="100" y="86" width="64" height="100" fill="url(#{p}Sky)"/>
      <circle cx="132" cy="152" r="17" fill="{c['white']}" fill-opacity="0.92"/>
      <path d="M100 156 C 112 150, 122 150, 132 156 S 152 162, 164 156 V186 H100 Z" fill="url(#{p}Sea)"/>
      <path d="M100 168 C 112 162, 122 163, 132 168 S 152 174, 164 168 V186 H100 Z" fill="{c['lagoon']}"/>
      <path d="M108 162 C 116 159, 122 159, 128 162" fill="none" stroke="{c['white']}" stroke-opacity="0.7" stroke-width="2.5" stroke-linecap="round"/>
    </g>
    <rect x="16" y="16" width="224" height="112" rx="62" fill="url(#{p}Shine)"/>
    <rect x="17.5" y="17.5" width="221" height="221" rx="60.5" fill="none" stroke="url(#{p}Edge)" stroke-width="3"/>
    """


def logo_document(size: int = 256) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 256 256">{logo_fragment()}</svg>\n'
    )


def wave_paths(width: int, height: int) -> list[dict]:
    """Vagues sinusoïdales superposées dans le tiers inférieur de la scène."""
    waves = []
    count = 9
    palette = [COLORS["turquoise"], COLORS["aqua"], COLORS["lagoon"], COLORS["sand"]]
    for index in range(count):
        base_y = height * (0.66 + index * 0.034)
        amplitude = height * (0.018 + 0.010 * math.sin(index * 1.7) ** 2)
        wavelength = width * (0.42 + 0.06 * (index % 3))
        shift = index * 0.9
        steps = 48
        points = []
        for step in range(steps + 1):
            x = width * step / steps
            y = base_y + amplitude * math.sin(2 * math.pi * x / wavelength + shift)
            points.append((x, y))
        d = f"M{points[0][0]:.1f} {points[0][1]:.1f}"
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            cx = (x0 + x1) / 2
            d += f" Q{x0:.1f} {y0:.1f} {cx:.1f} {(y0 + y1) / 2:.1f}"
        d += f" T{points[-1][0]:.1f} {points[-1][1]:.1f}"
        waves.append(
            {
                "d": d,
                "color": palette[index % len(palette)],
                "opacity": 0.34 - index * 0.028,
                "stroke": max(1.0, height * (0.0032 - index * 0.00022)),
            }
        )
    return waves


def scene_svg(ctx: Context, width: int, height: int, *, menu_panel: bool,
              show_wordmark: bool, logo_scale: float, logo_y: float,
              show_logo: bool = True) -> str:
    panel = {
        "x": width * 0.28,
        "y": height * 0.47,
        "w": width * 0.44,
        "h": height * 0.36,
    }
    return ctx.template("grub-splash.png.j2").render(
        width=width,
        height=height,
        c=COLORS,
        waves=wave_paths(width, height),
        logo=logo_fragment("scene") if show_logo else "",
        logo_size=height * logo_scale,
        logo_y=height * logo_y,
        show_wordmark=show_wordmark,
        menu_panel=menu_panel,
        panel=panel,
        version=ctx.version,
        tagline=TAGLINE,
    )


def svg_to_png(svg: str, target: Path, width: int | None = None, height: int | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        write_to=str(target),
        output_width=width,
        output_height=height,
    )
    target.chmod(0o644)


def rounded_panel(size: tuple[int, int], radius: int, fill: tuple[int, int, int, int],
                  outline: tuple[int, int, int, int] | None = None, width: int = 2) -> Image.Image:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=fill,
                           outline=outline, width=width if outline else 0)
    return image


def soft_dot(size: int, color: str) -> Image.Image:
    """Point lumineux à bord doux (texture de particule)."""
    r, g, b = hex_to_rgb(color)
    image = Image.new("RGBA", (size, size), (r, g, b, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((size * 0.2, size * 0.2, size * 0.8, size * 0.8), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size * 0.12))
    image.putalpha(mask)
    return image


# --------------------------------------------------------------------------
# Fond d'écran Plasma
# --------------------------------------------------------------------------
def generate_wallpaper(ctx: Context) -> Path:
    base = reset_dir(BRANDING / "usr" / "share" / "wallpapers" / "Djerba")
    images = base / "contents" / "images"
    for width, height in ((3840, 2160), (1920, 1080)):
        svg = scene_svg(ctx, width, height, menu_panel=False, show_wordmark=False,
                        logo_scale=0.16, logo_y=0.40)
        svg_to_png(svg, images / f"{width}x{height}.png")
    preview = Image.open(images / "1920x1080.png").convert("RGB").resize((400, 225), Image.LANCZOS)
    preview.save(base / "contents" / "screenshot.png", optimize=True)
    metadata = {
        "KPlugin": {
            "Authors": [{"Name": "Djerba OS"}],
            "Id": "Djerba",
            "License": "CC-BY-SA-4.0",
            "Name": "Djerba",
            "Name[fr]": "Djerba",
        }
    }
    write_text(base / "metadata.json", json.dumps(metadata, indent=4, ensure_ascii=False))
    log(f"fond d'écran Plasma -> {base.relative_to(ROOT)}")
    return images / "1920x1080.png"


# --------------------------------------------------------------------------
# Plasma : Look-and-Feel « Djerba » (Breeze sombre + fond Djerba)
# --------------------------------------------------------------------------
LOOK_AND_FEEL_DEFAULTS = """\
[kdeglobals][General]
ColorScheme=BreezeDark
AccentColor={accent}

[kdeglobals][Icons]
Theme=breeze-dark

[kdeglobals][KDE]
widgetStyle=Breeze

[kdeglobals][WM]
activeBackground={night}
activeForeground={white}
inactiveBackground={night}
inactiveForeground={muted}

[kcminputrc][Mouse]
cursorTheme=breeze_cursors

[kwinrc][org.kde.kdecoration2]
library=org.kde.breeze
theme=Breeze

[plasmarc][Theme]
name=breeze-dark

[Wallpaper]
Image=Djerba
"""


# Disposition « Horizon » : barre des tâches flottante et centrée (menu et
# applications au centre, zone de notification à droite), à la manière de
# Windows 11, construite avec l'API de script native de Plasma 6.
HORIZON_LAYOUT = """\
// Djerba OS — disposition « Horizon » (générée par assets_generator).
var panel = new Panel;
panel.location = "bottom";
panel.height = 2 * Math.floor(gridUnit * 2.75 / 2);
panel.floating = true;

panel.addWidget("org.kde.plasma.panelspacer");

var kickoff = panel.addWidget("org.kde.plasma.kickoff");
kickoff.currentConfigGroup = ["Shortcuts"];
kickoff.writeConfig("global", "Alt+F1");
kickoff.currentConfigGroup = ["General"];
kickoff.writeConfig("icon", "djerba-welcome");

var tasks = panel.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
    "applications:org.kde.dolphin.desktop",
    "applications:firefox.desktop",
    "applications:org.kde.discover.desktop",
    "applications:steam.desktop",
    "applications:systemsettings.desktop"
]);

panel.addWidget("org.kde.plasma.panelspacer");
panel.addWidget("org.kde.plasma.systemtray");
panel.addWidget("org.kde.plasma.digitalclock");
panel.addWidget("org.kde.plasma.showdesktop");

var desktopsArray = desktopsForActivity(currentActivity());
for (var j = 0; j < desktopsArray.length; j++) {
    desktopsArray[j].wallpaperPlugin = "org.kde.image";
}
"""


def draw_taskbar_preview(image: Image.Image, centered: bool) -> Image.Image:
    """Ajoute une barre des tâches schématique à l'aperçu du thème global."""
    preview = image.convert("RGBA")
    overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = preview.size
    bar_h = round(height * 0.075)
    icon = round(bar_h * 0.56)
    gap = round(icon * 0.55)
    accent = (*hex_to_rgb(COLORS["turquoise"]), 255)
    box = (0, height - bar_h, width, height)
    draw.rectangle(box, fill=(10, 20, 40, 225))
    if centered:
        count = 6
        total = count * icon + (count - 1) * gap
        x = (width - total) // 2
    else:
        count = 6
        x = round(bar_h * 0.3)
    y = box[1] + (bar_h - icon) // 2
    for index in range(count):
        fill = accent if index == 0 else (255, 255, 255, 150)
        draw.rounded_rectangle((x, y, x + icon, y + icon), radius=round(icon * 0.25), fill=fill)
        x += icon + gap
    preview.alpha_composite(overlay)
    return preview.convert("RGB")


def build_look_and_feel(ctx: Context, wallpaper: Path, package_id: str, name: str,
                        description: str, layout: str | None, defaults: str | None = None,
                        centered: bool | None = None) -> None:
    """Paquet Look-and-Feel : les éléments absents (écran de verrouillage,
    écran de démarrage…) sont fournis nativement par le paquet de repli Breeze."""
    base = reset_dir(BRANDING / "usr" / "share" / "plasma" / "look-and-feel" / package_id)
    metadata = {
        "KPackageStructure": "Plasma/LookAndFeel",
        "KPlugin": {
            "Authors": [{"Name": "Djerba OS"}],
            "Category": "",
            "Description": description,
            "Id": package_id,
            "License": "GPL-3.0-or-later",
            "Name": name,
            "ServiceTypes": ["Plasma/LookAndFeel"],
            "Version": ctx.version,
            "Website": PROJECT_URL,
        },
        "X-Plasma-APIVersion": "2",
    }
    write_text(base / "metadata.json", json.dumps(metadata, indent=4, ensure_ascii=False))
    if defaults is None:
        accent = ",".join(str(v) for v in hex_to_rgb(COLORS["turquoise"]))
        defaults = LOOK_AND_FEEL_DEFAULTS.format(
            accent=accent,
            night=",".join(str(v) for v in hex_to_rgb(COLORS["night"])),
            white=",".join(str(v) for v in hex_to_rgb(COLORS["white"])),
            muted=",".join(str(v) for v in hex_to_rgb(COLORS["muted"])),
        )
    write_text(base / "contents" / "defaults", defaults)
    if layout is not None:
        write_text(base / "contents" / "layouts" / "org.kde.plasma.desktop-layout.js", layout)
    previews = base / "contents" / "previews"
    previews.mkdir(parents=True)
    image = draw_taskbar_preview(Image.open(wallpaper).resize((1280, 720), Image.LANCZOS),
                                 centered=(layout is not None) if centered is None else centered)
    image.resize((640, 360), Image.LANCZOS).save(previews / "preview.png", optimize=True)
    image.save(previews / "fullscreenpreview.jpg", quality=90)
    log(f"Look-and-Feel Plasma « {name} » -> {base.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Mode discret (djerba-undercover) : apparence claire façon Windows 10/11
# --------------------------------------------------------------------------
UNDERCOVER_ACCENT = "#0067C0"

UNDERCOVER_DEFAULTS = """\
[kdeglobals][General]
ColorScheme=BreezeLight
AccentColor=0,103,192

[kdeglobals][Icons]
Theme=breeze

[kdeglobals][KDE]
widgetStyle=Breeze

[kcminputrc][Mouse]
cursorTheme=breeze_cursors

[kwinrc][org.kde.kdecoration2]
library=org.kde.breeze
theme=Breeze

[plasmarc][Theme]
name=default

[Wallpaper]
Image=DjerbaDiscret
"""

UNDERCOVER_LAYOUT = """\
// Djerba OS — disposition du mode discret, style {style} (générée par assets_generator).
var panel = new Panel;
panel.location = "bottom";
panel.height = 2 * Math.floor(gridUnit * 2.6 / 2);
panel.floating = false;
{leading}
var kickoff = panel.addWidget("org.kde.plasma.kickoff");
kickoff.currentConfigGroup = ["Shortcuts"];
kickoff.writeConfig("global", "Alt+F1");
kickoff.currentConfigGroup = ["General"];
kickoff.writeConfig("icon", "djerba-undercover-start");

var tasks = panel.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
    "applications:org.kde.dolphin.desktop",
    "applications:firefox.desktop",
    "applications:org.kde.discover.desktop",
    "applications:systemsettings.desktop"
]);

panel.addWidget("org.kde.plasma.panelspacer");
panel.addWidget("org.kde.plasma.systemtray");
panel.addWidget("org.kde.plasma.digitalclock");
panel.addWidget("org.kde.plasma.showdesktop");

var desktopsArray = desktopsForActivity(currentActivity());
for (var j = 0; j < desktopsArray.length; j++) {{
    desktopsArray[j].wallpaperPlugin = "org.kde.image";
}}
"""


def undercover_wallpaper_svg(width: int, height: int) -> str:
    """Fond bleu clair abstrait (rubans translucides), sans motif déposé."""
    ribbons = []
    for index in range(7):
        t = index / 6
        start_y = height * (0.18 + 0.1 * t)
        control_y = height * (0.95 - 0.12 * t)
        end_y = height * (0.42 + 0.08 * t)
        stroke = height * (0.07 - 0.006 * index)
        opacity = 0.16 + 0.07 * (1 - t)
        ribbons.append(
            f'<path d="M {-width * 0.1:.0f} {start_y:.0f} C {width * 0.3:.0f} {control_y:.0f}, '
            f'{width * 0.62:.0f} {height * 0.05:.0f}, {width * 1.1:.0f} {end_y:.0f}" fill="none" '
            f'stroke="url(#ribbon)" stroke-width="{stroke:.0f}" stroke-linecap="round" '
            f'stroke-opacity="{opacity:.2f}"/>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#E6F0FB"/>
      <stop offset="0.55" stop-color="#6FA6E6"/>
      <stop offset="1" stop-color="#0B4FA0"/>
    </linearGradient>
    <linearGradient id="ribbon" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#FFFFFF"/>
      <stop offset="0.5" stop-color="#9CC8F5"/>
      <stop offset="1" stop-color="#1F6FD1"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.55"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#sky)"/>
  <ellipse cx="{width * 0.5}" cy="{height * 0.48}" rx="{width * 0.32}" ry="{height * 0.36}" fill="url(#glow)"/>
  {''.join(ribbons)}
</svg>
"""


def undercover_start_icon() -> str:
    """Icône de menu neutre : grille d'applications 3 × 3."""
    squares = []
    for row in range(3):
        for column in range(3):
            squares.append(
                f'<rect x="{6 + column * 20}" y="{6 + row * 20}" width="16" height="16" rx="4" '
                f'fill="{UNDERCOVER_ACCENT}"/>'
            )
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="68" height="68" viewBox="0 0 68 68">'
            + "".join(squares) + "</svg>\n")


def generate_undercover(ctx: Context) -> None:
    base = reset_dir(BRANDING / "usr" / "share" / "wallpapers" / "DjerbaDiscret")
    images = base / "contents" / "images"
    for width, height in ((3840, 2160), (1920, 1080)):
        svg_to_png(undercover_wallpaper_svg(width, height), images / f"{width}x{height}.png")
    Image.open(images / "1920x1080.png").convert("RGB").resize((400, 225), Image.LANCZOS).save(
        base / "contents" / "screenshot.png", optimize=True)
    write_text(base / "metadata.json", json.dumps({"KPlugin": {
        "Authors": [{"Name": "Djerba OS"}], "Id": "DjerbaDiscret",
        "License": "CC-BY-SA-4.0", "Name": "Djerba Discret"}}, indent=4, ensure_ascii=False))
    write_text(BRANDING / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
               / "djerba-undercover-start.svg", undercover_start_icon())
    wallpaper = images / "1920x1080.png"
    for style, leading in (("10", ""), ("11", 'panel.addWidget("org.kde.plasma.panelspacer");')):
        build_look_and_feel(
            ctx, wallpaper, f"org.djerba.undercover{style}.desktop", f"Discret (style {style})",
            "Djerba OS — mode discret : apparence claire façon Windows, réversible",
            layout=UNDERCOVER_LAYOUT.format(style=style, leading=leading),
            defaults=UNDERCOVER_DEFAULTS, centered=(style == "11"))
    log(f"mode discret -> {base.relative_to(ROOT)}")


def generate_look_and_feel(ctx: Context, wallpaper: Path) -> None:
    build_look_and_feel(ctx, wallpaper, "org.djerba.desktop", "Djerba",
                        "Djerba OS — lagon nocturne et verre dépoli", layout=None)
    build_look_and_feel(ctx, wallpaper, "org.djerba.horizon.desktop", "Djerba Horizon",
                        "Djerba OS — barre des tâches flottante et centrée, à la manière de Windows 11", layout=HORIZON_LAYOUT)


# --------------------------------------------------------------------------
# GRUB
# --------------------------------------------------------------------------
GRUB_CONFIG = """\
# Djerba OS — configuration GRUB du média live (générée par assets_generator).
set default=0
set timeout=5
set timeout_style=menu

if [ x$feature_default_font_path = xy ] ; then
    font=unicode
else
    font=$prefix/unicode.pf2
fi

if loadfont $font ; then
    set gfxmode=1920x1080,1600x900,1280x720,1024x768,auto
    set gfxpayload=keep
    if [ "${grub_platform}" = "efi" ] ; then
        insmod efi_gop
        if [ "${grub_cpu}" = "x86_64" -o "${grub_cpu}" = "i386" ] ; then
            insmod efi_uga
        fi
    fi
    if [ "${grub_cpu}" = "x86_64" -o "${grub_cpu}" = "i386" ] ; then
        insmod video_bochs
        insmod video_cirrus
    fi
else
    set gfxmode=auto
    insmod all_video
fi

insmod gfxterm
insmod png

source /boot/grub/theme.cfg

terminal_output gfxterm

# Console série (QEMU headless, serveurs) en complément de l'écran.
if serial --unit=0 --speed=115200 ; then
    terminal_input --append serial
    terminal_output --append serial
fi
"""


def grub_theme(ctx: Context) -> str:
    c = COLORS
    return f"""\
# Djerba OS — thème GRUB (généré par assets_generator, ne pas éditer).
title-text: ""
desktop-image: "../splash.png"
desktop-color: "{c['night']}"
terminal-font: "Unifont Regular 16"
terminal-left: "10%"
terminal-top: "10%"
terminal-width: "80%"
terminal-height: "80%"
terminal-border: "0"

+ boot_menu {{
    left = 30%
    top = 50%
    width = 40%
    height = 30%
    item_font = "Unifont Regular 16"
    item_color = "#CBD5E1"
    selected_item_font = "Unifont Regular 16"
    selected_item_color = "{c['night']}"
    selected_item_pixmap_style = "select_*.png"
    item_height = 40
    item_padding = 16
    item_spacing = 10
    icon_width = 0
    icon_height = 0
    item_icon_space = 0
    scrollbar = false
}}

+ progress_bar {{
    id = "__timeout__"
    left = 35%
    top = 86%
    width = 30%
    height = 6
    show_text = false
    bg_color = "#1E293B"
    fg_color = "{c['turquoise']}"
    border_color = "{c['night']}"
}}

+ label {{
    id = "__timeout__"
    left = 0
    top = 89%
    width = 100%
    height = 24
    align = "center"
    color = "{c['muted']}"
    font = "Unifont Regular 16"
    text = "@TIMEOUT_NOTIFICATION_MIDDLE@"
}}
"""


def generate_grub(ctx: Context) -> None:
    base = reset_dir(ROOT / "config" / "bootloaders" / "grub-pc")
    theme_dir = base / "live-theme"
    theme_dir.mkdir()
    svg = scene_svg(ctx, 1920, 1080, menu_panel=True, show_wordmark=True,
                    logo_scale=0.16, logo_y=0.20)
    svg_to_png(svg, base / "splash.png")
    write_text(theme_dir / "theme.txt", grub_theme(ctx))
    write_text(base / "config.cfg", GRUB_CONFIG)

    # Thème du système installé (GRUB_THEME, paquet djerba-branding).
    installed = reset_dir(BRANDING / "usr" / "share" / "grub" / "themes" / "djerba")
    svg_to_png(svg, installed / "background.png")
    write_text(installed / "theme.txt",
               grub_theme(ctx).replace('desktop-image: "../splash.png"', 'desktop-image: "background.png"'))

    # Surbrillance « verre » en 9 tranches (coins arrondis de 14 px).
    radius = 14
    tile = 2 * radius + 4
    accent = hex_to_rgb(COLORS["turquoise"])
    full = rounded_panel((tile, tile), radius, (*accent, 235), (255, 255, 255, 120), 2)
    boxes = {
        "nw": (0, 0, radius, radius),
        "n": (radius, 0, tile - radius, radius),
        "ne": (tile - radius, 0, tile, radius),
        "w": (0, radius, radius, tile - radius),
        "c": (radius, radius, tile - radius, tile - radius),
        "e": (tile - radius, radius, tile, tile - radius),
        "sw": (0, tile - radius, radius, tile),
        "s": (radius, tile - radius, tile - radius, tile),
        "se": (tile - radius, tile - radius, tile, tile),
    }
    for name, box in boxes.items():
        full.crop(box).save(theme_dir / f"select_{name}.png")
        full.crop(box).save(installed / f"select_{name}.png")
    log(f"GRUB -> {base.relative_to(ROOT)}, {installed.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Plymouth
# --------------------------------------------------------------------------
def render_qml_frames(qml_source: str, size: int, frames: int, target: Path) -> None:
    """Rend une scène QtQuick image par image avec un alpha exact.

    Le moteur logiciel de QtQuick ne produit pas de canal alpha lors d'une
    capture : chaque image est rendue sur fond noir puis sur fond blanc, et
    l'alpha est reconstitué par matting (alpha = 1 - (blanc - noir)).
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QColor, QGuiApplication, QImage
    from PyQt6.QtQuick import QQuickView, QQuickWindow, QSGRendererInterface

    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])

    qml_file = target / "_scene.qml"
    qml_file.write_text(qml_source, encoding="utf-8")
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(size, size)
    view.setSource(QUrl.fromLocalFile(str(qml_file)))
    if view.status() != QQuickView.Status.Ready:
        errors = "; ".join(error.toString() for error in view.errors())
        raise RuntimeError(f"scène QML Plymouth invalide : {errors}")
    view.show()
    root = view.rootObject()

    def grab(background: str) -> Image.Image:
        view.setColor(QColor(background))
        app.processEvents()
        qimage = view.grabWindow().convertToFormat(QImage.Format.Format_RGB888)
        width, height = qimage.width(), qimage.height()
        data = bytes(qimage.constBits().asarray(qimage.sizeInBytes()))
        stride = qimage.bytesPerLine()
        rows = b"".join(data[y * stride:y * stride + width * 3] for y in range(height))
        return Image.frombytes("RGB", (width, height), rows)

    for index in range(frames):
        root.setProperty("phase", index / frames)
        on_black = grab("#000000")
        on_white = grab("#FFFFFF")
        difference = ImageChops.subtract(on_white, on_black)
        channels = difference.split()
        spread = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        alpha = ImageChops.invert(spread)
        premultiplied = Image.merge("RGBa", (*on_black.split(), alpha))
        premultiplied.convert("RGBA").save(target / f"throbber-{index:02d}.png", optimize=True)
    view.close()
    qml_file.unlink()


PLYMOUTH_SCRIPT = """\
# Djerba OS — thème Plymouth (généré par assets_generator, ne pas éditer).
# Animation : {frames} images pré-rendues depuis plymouth.qml.j2, jouées à 25 i/s.

Window.SetBackgroundTopColor({top});
Window.SetBackgroundBottomColor({bottom});

screen_x = Window.GetX();
screen_y = Window.GetY();
screen_width = Window.GetWidth();
screen_height = Window.GetHeight();

unit = Math.Min(screen_width / 1920, screen_height / 1080);
if (unit < 0.45)
    unit = 0.45;

# ---------------------------------------------------------------- Fond
background_image = Image("background.png");
cover = Math.Max(screen_width / background_image.GetWidth(), screen_height / background_image.GetHeight());
background_scaled = background_image.Scale(background_image.GetWidth() * cover, background_image.GetHeight() * cover);
background_sprite = Sprite(background_scaled);
background_sprite.SetX(screen_x + (screen_width - background_scaled.GetWidth()) / 2);
background_sprite.SetY(screen_y + (screen_height - background_scaled.GetHeight()) / 2);
background_sprite.SetZ(-100);

centre_x = screen_x + screen_width / 2;
centre_y = screen_y + screen_height * 0.42;

# ---------------------------------------------------------------- Anneau animé
ring_size = Math.Int({ring} * unit);
frame_count = {frames};
for (i = 0; i < frame_count; i++)
{{
    if (i < 10)
        frame_name = "throbber-0" + i + ".png";
    else
        frame_name = "throbber-" + i + ".png";
    frames[i] = Image(frame_name).Scale(ring_size, ring_size);
}}
throbber_sprite = Sprite(frames[0]);
throbber_sprite.SetX(centre_x - ring_size / 2);
throbber_sprite.SetY(centre_y - ring_size / 2);
throbber_sprite.SetZ(10);

# ---------------------------------------------------------------- Logo et marque
logo_size = Math.Int(ring_size * 0.5);
logo_image = Image("logo.png").Scale(logo_size, logo_size);
logo_sprite = Sprite(logo_image);
logo_sprite.SetX(centre_x - logo_size / 2);
logo_sprite.SetY(centre_y - logo_size / 2);
logo_sprite.SetZ(20);

wordmark_source = Image("wordmark.png");
wordmark_image = wordmark_source.Scale(Math.Int(wordmark_source.GetWidth() * unit), Math.Int(wordmark_source.GetHeight() * unit));
wordmark_sprite = Sprite(wordmark_image);
wordmark_sprite.SetX(centre_x - wordmark_image.GetWidth() / 2);
wordmark_sprite.SetY(centre_y + ring_size / 2 + 24 * unit);
wordmark_sprite.SetZ(20);

# ---------------------------------------------------------------- Progression
track_width = Math.Int(360 * unit);
track_height = Math.Max(4, Math.Int(6 * unit));
track_image = Image("progress_track.png").Scale(track_width, track_height);
track_sprite = Sprite(track_image);
track_sprite.SetX(centre_x - track_width / 2);
track_sprite.SetY(screen_y + screen_height * 0.82);
track_sprite.SetZ(20);

fill_source = Image("progress_fill.png");
fill_sprite = Sprite(fill_source.Scale(1, track_height));
fill_sprite.SetX(centre_x - track_width / 2);
fill_sprite.SetY(screen_y + screen_height * 0.82);
fill_sprite.SetZ(21);

fun progress_callback(duration, progress)
{{
    if (progress > 1)
        progress = 1;
    width = Math.Int(track_width * progress);
    if (width < 1)
        width = 1;
    fill_sprite.SetImage(fill_source.Scale(width, track_height));
}}
Plymouth.SetBootProgressFunction(progress_callback);

# ---------------------------------------------------------------- Rafraîchissement
tick = 0;
fun refresh_callback()
{{
    tick++;
    throbber_sprite.SetImage(frames[Math.Int(tick / 2) % frame_count]);
    logo_sprite.SetOpacity(0.88 + 0.12 * Math.Sin(tick / 30));
}}
Plymouth.SetRefreshFunction(refresh_callback);

# ---------------------------------------------------------------- Messages
message_sprite = Sprite();
message_sprite.SetZ(30);
fun message_callback(text)
{{
    message_image = Image.Text(text, {muted}, 1, "Inter 12");
    message_sprite.SetImage(message_image);
    message_sprite.SetX(centre_x - message_image.GetWidth() / 2);
    message_sprite.SetY(screen_y + screen_height * 0.88);
}}
Plymouth.SetMessageFunction(message_callback);

# ---------------------------------------------------------------- Mot de passe (LUKS)
dialog_image = Image("dialog.png");
dialog_sprite = Sprite();
dialog_sprite.SetZ(50);
prompt_sprite = Sprite();
prompt_sprite.SetZ(51);
bullets_sprite = Sprite();
bullets_sprite.SetZ(51);

fun show_dialog(prompt, bullet_text)
{{
    dialog_scaled = dialog_image.Scale(Math.Int(dialog_image.GetWidth() * unit), Math.Int(dialog_image.GetHeight() * unit));
    dialog_sprite.SetImage(dialog_scaled);
    dialog_sprite.SetX(centre_x - dialog_scaled.GetWidth() / 2);
    dialog_sprite.SetY(centre_y + ring_size / 2 + 72 * unit);
    dialog_sprite.SetOpacity(1);

    prompt_image = Image.Text(prompt, {white}, 1, "Inter 12");
    prompt_sprite.SetImage(prompt_image);
    prompt_sprite.SetX(centre_x - prompt_image.GetWidth() / 2);
    prompt_sprite.SetY(dialog_sprite.GetY() + 28 * unit);
    prompt_sprite.SetOpacity(1);

    bullets_image = Image.Text(bullet_text, {accent}, 1, "Inter 18");
    bullets_sprite.SetImage(bullets_image);
    bullets_sprite.SetX(centre_x - bullets_image.GetWidth() / 2);
    bullets_sprite.SetY(dialog_sprite.GetY() + 78 * unit);
    bullets_sprite.SetOpacity(1);
}}

fun display_password_callback(prompt, bullets)
{{
    bullet_text = "";
    for (i = 0; i < bullets; i++)
        bullet_text += "●";
    if (bullets == 0)
        bullet_text = " ";
    show_dialog(prompt, bullet_text);
}}
Plymouth.SetDisplayPasswordFunction(display_password_callback);

fun display_question_callback(prompt, entry)
{{
    if (entry == "")
        entry = " ";
    show_dialog(prompt, entry);
}}
Plymouth.SetDisplayQuestionFunction(display_question_callback);

fun display_normal_callback()
{{
    dialog_sprite.SetOpacity(0);
    prompt_sprite.SetOpacity(0);
    bullets_sprite.SetOpacity(0);
}}
Plymouth.SetDisplayNormalFunction(display_normal_callback);

fun quit_callback()
{{
    throbber_sprite.SetOpacity(0);
    fill_sprite.SetOpacity(0);
    track_sprite.SetOpacity(0);
}}
Plymouth.SetQuitFunction(quit_callback);
"""


def generate_plymouth(ctx: Context) -> None:
    base = reset_dir(BRANDING / "usr" / "share" / "plymouth" / "themes" / "djerba")
    scene = scene_svg(ctx, 1920, 1080, menu_panel=False, show_wordmark=False,
                      logo_scale=0.0, logo_y=0.42, show_logo=False)
    svg_to_png(scene, base / "background.png")
    svg_to_png(logo_document(), base / "logo.png", 256, 256)

    wordmark = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="520" height="72" viewBox="0 0 520 72">'
        f'<text x="260" y="52" text-anchor="middle" font-family="Inter" font-size="44" letter-spacing="14" '
        f'fill="{COLORS["white"]}"><tspan font-weight="800">DJERBA</tspan>'
        f'<tspan font-weight="300" fill="{COLORS["turquoise"]}" dx="10">OS</tspan></text></svg>'
    )
    svg_to_png(wordmark, base / "wordmark.png")

    track = rounded_panel((360, 6), 3, (255, 255, 255, 40))
    track.save(base / "progress_track.png")
    fill = Image.new("RGBA", (360, 6))
    start, end = hex_to_rgb(COLORS["turquoise"]), hex_to_rgb(COLORS["aqua"])
    for x in range(360):
        t = x / 359
        colour = tuple(round(start[i] + (end[i] - start[i]) * t) for i in range(3))
        for y in range(6):
            fill.putpixel((x, y), (*colour, 255))
    fill.save(base / "progress_fill.png")
    rounded_panel((520, 128), 22, (255, 255, 255, 34), (255, 255, 255, 70), 2).save(base / "dialog.png")

    qml = ctx.template("plymouth.qml.j2").render(size=PLYMOUTH_RING, c=COLORS, ripples=3)
    render_qml_frames(qml, PLYMOUTH_RING, PLYMOUTH_FRAMES, base)

    def rgb_args(colour: str) -> str:
        return ", ".join(str(v) for v in hex_to_unit(colour))

    script = PLYMOUTH_SCRIPT.format(
        frames=PLYMOUTH_FRAMES,
        ring=PLYMOUTH_RING,
        top=rgb_args(COLORS["night"]),
        bottom=rgb_args(COLORS["deep"]),
        muted=rgb_args(COLORS["muted"]),
        white=rgb_args(COLORS["white"]),
        accent=rgb_args(COLORS["turquoise"]),
    )
    write_text(base / "djerba.script", script)
    write_text(
        base / "djerba.plymouth",
        "[Plymouth Theme]\n"
        "Name=Djerba OS\n"
        "Description=Djerba OS — lagon animé et verre dépoli\n"
        "ModuleName=script\n\n"
        "[script]\n"
        "ImageDir=/usr/share/plymouth/themes/djerba\n"
        "ScriptFile=/usr/share/plymouth/themes/djerba/djerba.script\n",
    )
    log(f"Plymouth ({PLYMOUTH_FRAMES} images) -> {base.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# SDDM
# --------------------------------------------------------------------------
def generate_sddm(ctx: Context, wallpaper: Path) -> None:
    base = reset_dir(BRANDING / "usr" / "share" / "sddm" / "themes" / "djerba")
    write_text(base / "Main.qml",
               ctx.template("sddm.qml.j2").render(c=COLORS, product=PRODUCT, version=ctx.version))
    shutil.copyfile(wallpaper, base / "background.png")
    write_text(base / "logo.svg", logo_document())
    soft_dot(64, COLORS["white"]).save(base / "particle.png")
    preview = Image.open(wallpaper).convert("RGB").resize((640, 360), Image.LANCZOS)
    preview.save(base / "preview.png", optimize=True)
    write_text(base / "theme.conf", "[General]\nbackground=background.png\ntype=image\n")
    write_text(
        base / "metadata.desktop",
        "[SddmGreeterTheme]\n"
        "Name=Djerba OS\n"
        "Description=Djerba OS — verre dépoli sur lagon animé\n"
        "Author=Djerba OS\n"
        "Copyright=(c) Djerba OS, GPL-3.0-or-later\n"
        "License=GPL-3.0-or-later\n"
        "Type=sddm-theme\n"
        f"Version={ctx.version}\n"
        f"Website={PROJECT_URL}\n"
        "Screenshot=preview.png\n"
        "MainScript=Main.qml\n"
        "ConfigFile=theme.conf\n"
        "Theme-Id=djerba\n"
        "Theme-API=2.0\n"
        "QtVersion=6\n",
    )
    log(f"SDDM -> {base.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Calamares
# --------------------------------------------------------------------------
def generate_calamares(ctx: Context) -> None:
    base = reset_dir(CHROOT / "usr" / "share" / "calamares" / "branding" / "djerba")
    template = ctx.template("calamares-theme.j2")
    variables = {
        "c": COLORS,
        "product": PRODUCT,
        "version": ctx.version,
        "url": PROJECT_URL,
        "slides": SLIDES,
    }
    outputs = {
        "branding_desc": "branding.desc",
        "show_qml": "show.qml",
        "stylesheet": "stylesheet.qss",
    }
    for block, filename in outputs.items():
        context = template.new_context(variables)
        content = "".join(template.blocks[block](context)).strip()
        write_text(base / filename, content)
    svg_to_png(logo_document(), base / "logo.png", 256, 256)
    svg_to_png(logo_document(), base / "icon.png", 128, 128)
    welcome = scene_svg(ctx, 960, 400, menu_panel=False, show_wordmark=True,
                        logo_scale=0.30, logo_y=0.30)
    svg_to_png(welcome, base / "welcome.png")
    log(f"Calamares -> {base.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Assistant d'accueil
# --------------------------------------------------------------------------
def generate_identity(ctx: Context) -> None:
    """Nom du système, logo et icônes (paquet djerba-branding)."""
    version = ctx.version
    os_release = (
        f'PRETTY_NAME="Djerba OS {version}"\n'
        'NAME="Djerba OS"\n'
        f'VERSION_ID="{version}"\n'
        f'VERSION="{version} (basé sur Debian 13 « trixie »)"\n'
        "VERSION_CODENAME=trixie\n"
        "DEBIAN_CODENAME=trixie\n"
        "ID=djerba\n"
        "ID_LIKE=debian\n"
        f'HOME_URL="{PROJECT_URL}"\n'
        f'SUPPORT_URL="{PROJECT_URL}/issues"\n'
        f'BUG_REPORT_URL="{PROJECT_URL}/issues"\n'
        "LOGO=djerba-os\n"
        'ANSI_COLOR="38;2;0;194;203"\n'
    )
    write_text(BRANDING / "usr" / "lib" / "os-release", os_release)

    icons = BRANDING / "usr" / "share" / "icons" / "hicolor"
    for name in ("djerba-os", "djerba-welcome"):
        write_text(icons / "scalable" / "apps" / f"{name}.svg", logo_document())
        for size in (48, 64, 128, 256):
            svg_to_png(logo_document(), icons / f"{size}x{size}" / "apps" / f"{name}.png", size, size)
    svg_to_png(logo_document(), BRANDING / "usr" / "share" / "pixmaps" / "djerba-os.png", 256, 256)

    assets = reset_dir(BRANDING / "usr" / "share" / "djerba" / "assets")
    write_text(assets / "logo.svg", logo_document())
    soft_dot(64, COLORS["white"]).save(assets / "particle.png")
    log(f"identité système et icônes -> {BRANDING.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=os.environ.get("DJERBA_VERSION", "dev"),
                        help="version affichée (défaut : $DJERBA_VERSION ou « dev »)")
    parser.add_argument("--skip-plymouth", action="store_true",
                        help="ne pas rendre l'animation Plymouth (aperçu rapide)")
    args = parser.parse_args()

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    ctx = Context(version=args.version, env=env)
    reset_dir(BRANDING)

    wallpaper = generate_wallpaper(ctx)
    generate_look_and_feel(ctx, wallpaper)
    generate_undercover(ctx)
    generate_grub(ctx)
    if not args.skip_plymouth:
        generate_plymouth(ctx)
    generate_sddm(ctx, wallpaper)
    generate_calamares(ctx)
    generate_identity(ctx)
    log("identité visuelle générée")
    return 0


if __name__ == "__main__":
    sys.exit(main())
