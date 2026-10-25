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
        "body": "Un système léger et sécurisé, taillé pour le jeu, construit sur Ubuntu 24.04 LTS.",
        "accent": COLORS["turquoise"],
    },
    {
        "title": "Prêt pour le jeu",
        "body": "Noyau à faible latence, GameMode, Vulkan, Steam sur x86_64 et Flathub prêts à l’emploi.",
        "accent": COLORS["coral"],
    },
    {
        "title": "BTRFS et zstd",
        "body": "Sous-volumes @ et @home, compression transparente et options optimisées pour SSD.",
        "accent": COLORS["aqua"],
    },
    {
        "title": "Sécurité par défaut",
        "body": "Pare-feu UFW actif, profils AppArmor, noyau durci et mises à jour de sécurité automatiques.",
        "accent": COLORS["sand"],
    },
    {
        "title": "Plasma sous Wayland",
        "body": "Le bureau KDE Plasma sous Wayland, avec des effets de verre fluides à 60 images par seconde.",
        "accent": COLORS["turquoise"],
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
    """Logo Djerba OS : soleil couchant sur trois vagues, dans un carré de verre.

    Fragment SVG pour un viewBox 0 0 256 256 ; ``prefix`` rend les
    identifiants uniques lorsque le logo est inséré dans un autre document.
    """
    c = COLORS
    p = prefix
    return f"""
    <defs>
      <linearGradient id="{p}Glass" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.22"/>
        <stop offset="1" stop-color="#FFFFFF" stop-opacity="0.05"/>
      </linearGradient>
      <linearGradient id="{p}Edge" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.65"/>
        <stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0.10"/>
        <stop offset="1" stop-color="#FFFFFF" stop-opacity="0.35"/>
      </linearGradient>
      <linearGradient id="{p}Sun" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="{c['sand']}"/>
        <stop offset="1" stop-color="{c['coral']}"/>
      </linearGradient>
      <linearGradient id="{p}Wave1" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="{c['turquoise']}"/>
        <stop offset="1" stop-color="{c['aqua']}"/>
      </linearGradient>
      <linearGradient id="{p}Wave2" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="{c['lagoon']}"/>
        <stop offset="1" stop-color="{c['turquoise']}"/>
      </linearGradient>
      <clipPath id="{p}Clip">
        <rect x="16" y="16" width="224" height="224" rx="60"/>
      </clipPath>
    </defs>
    <rect x="16" y="16" width="224" height="224" rx="60" fill="{c['night']}" fill-opacity="0.55"/>
    <g clip-path="url(#{p}Clip)">
      <circle cx="128" cy="124" r="52" fill="url(#{p}Sun)"/>
      <path d="M16 142 C 52 122, 88 122, 128 140 S 204 158, 240 138 L 240 240 L 16 240 Z" fill="url(#{p}Wave2)" fill-opacity="0.95"/>
      <path d="M16 170 C 56 150, 92 152, 128 168 S 200 186, 240 164 L 240 240 L 16 240 Z" fill="url(#{p}Wave1)"/>
      <path d="M16 200 C 60 184, 96 186, 128 198 S 196 214, 240 196 L 240 240 L 16 240 Z" fill="{c['deep']}" fill-opacity="0.85"/>
      <path d="M40 182 C 70 172, 98 174, 126 184" fill="none" stroke="{c['white']}" stroke-opacity="0.55" stroke-width="4" stroke-linecap="round"/>
    </g>
    <rect x="16" y="16" width="224" height="224" rx="60" fill="url(#{p}Glass)"/>
    <rect x="17" y="17" width="222" height="222" rx="59" fill="none" stroke="url(#{p}Edge)" stroke-width="3"/>
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
    base = reset_dir(CHROOT / "usr" / "share" / "wallpapers" / "Djerba")
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
    log(f"GRUB -> {base.relative_to(ROOT)}")


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
    base = reset_dir(CHROOT / "usr" / "share" / "plymouth" / "themes" / "djerba")
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
    base = reset_dir(CHROOT / "usr" / "share" / "sddm" / "themes" / "djerba")
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
        "QtVersion=5\n",
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
def generate_welcome() -> None:
    assets = reset_dir(CHROOT / "usr" / "local" / "share" / "djerba-welcome" / "ui" / "assets")
    write_text(assets / "logo.svg", logo_document())
    soft_dot(64, COLORS["white"]).save(assets / "particle.png")
    icon = CHROOT / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "djerba-welcome.svg"
    write_text(icon, logo_document())
    log(f"assistant d'accueil -> {assets.relative_to(ROOT)}")


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

    wallpaper = generate_wallpaper(ctx)
    generate_grub(ctx)
    if not args.skip_plymouth:
        generate_plymouth(ctx)
    generate_sddm(ctx, wallpaper)
    generate_calamares(ctx)
    generate_welcome()
    log("identité visuelle générée")
    return 0


if __name__ == "__main__":
    sys.exit(main())
