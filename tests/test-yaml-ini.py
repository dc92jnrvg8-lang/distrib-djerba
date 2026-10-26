#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validateur syntaxique et structurel des fichiers YAML/INI de Djerba OS.

Exécuté avant toute compilation (lints locaux, CI, build.sh). Il garantit :

* que ``mount.conf`` est un YAML strict (pas de clés dupliquées, pas de
  tabulations) respectant le schéma ``mount.schema.yaml`` de Calamares 3.3 et
  déclarant les sous-volumes BTRFS ``/@`` (racine) et ``/@home`` (/home) ;
* que ``flathub.conf`` est un INI strict contenant la section
  ``[remote "flathub"]`` et l'URL officielle du dépôt ;
* que tous les autres YAML (Calamares, catalogue d'applications) et INI
  (SDDM, KDE, .desktop) livrés dans ``config/includes.chroot`` et les
  paquets Djerba (``packages/*/root``) sont syntaxiquement parfaits.

Code de retour : 0 si tout est valide, 1 sinon.
"""

from __future__ import annotations

import configparser
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHROOT = ROOT / "config" / "includes.chroot"
CALAMARES = CHROOT / "etc" / "calamares"
MODULES = CALAMARES / "modules"

MOUNT_CONF = MODULES / "mount.conf"
FSTAB_CONF = MODULES / "fstab.conf"
FLATHUB_CONF = CHROOT / "etc" / "flatpak" / "remotes.d" / "flathub.conf"
SETTINGS_CONF = CALAMARES / "settings.conf"

FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"

# Modules Calamares 3.3 livrés par le paquet Ubuntu 24.04 qui fonctionnent
# sans fichier de configuration dédié.
MODULES_WITHOUT_CONF = {
    "summary",
    "finished",
    "machineid",
    "localecfg",
    "hwclock",
    "initramfscfg",
    "umount",
    "keyboard",
    "djerbaapps",   # module Djerba sans configuration
    "netinstall",   # configuration générée depuis le catalogue (build.sh)
}


class ValidationError(Exception):
    """Erreur de validation lisible par un humain."""


class StrictLoader(yaml.SafeLoader):
    """SafeLoader refusant les clés dupliquées (silencieuses avec PyYAML)."""


def _construct_mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValidationError(
                f"clé dupliquée {key!r} (ligne {key_node.start_mark.line + 1})"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def load_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        if "\t" in line:
            raise ValidationError(f"tabulation interdite en YAML (ligne {number})")
    try:
        return yaml.load(text, Loader=StrictLoader)  # noqa: S506 - StrictLoader dérive de SafeLoader
    except yaml.YAMLError as exc:
        raise ValidationError(f"YAML invalide : {exc}") from exc


def load_ini(path: Path, strict: bool = True) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        interpolation=None, strict=strict, delimiters=("=",), comment_prefixes=("#", ";")
    )
    parser.optionxform = str  # conserve la casse (KDE, SDDM, .desktop)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle, source=str(path))
    except configparser.Error as exc:
        raise ValidationError(f"INI invalide : {exc}") from exc
    if not parser.sections():
        raise ValidationError("INI sans aucune section")
    return parser


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def expect_str_list(value: Any, where: str) -> None:
    expect(
        isinstance(value, list) and all(isinstance(item, str) for item in value),
        f"{where} doit être une liste de chaînes",
    )


# --------------------------------------------------------------------------
# mount.conf — schéma Calamares 3.3 (src/modules/mount/mount.schema.yaml)
# --------------------------------------------------------------------------
def check_mount_conf(path: Path) -> None:
    data = load_yaml(path)
    expect(isinstance(data, dict), "la racine doit être un dictionnaire")
    allowed = {"extraMounts", "btrfsSubvolumes", "btrfsSwapSubvol", "mountOptions"}
    unknown = set(data) - allowed
    expect(not unknown, f"clés inconnues du schéma mount : {sorted(unknown)}")

    for index, entry in enumerate(data.get("extraMounts", [])):
        where = f"extraMounts[{index}]"
        expect(isinstance(entry, dict), f"{where} doit être un dictionnaire")
        extra = set(entry) - {"device", "fs", "mountPoint", "options", "efi"}
        expect(not extra, f"{where} : clés inconnues {sorted(extra)}")
        expect(isinstance(entry.get("device"), str), f"{where}.device requis (chaîne)")
        expect(isinstance(entry.get("mountPoint"), str), f"{where}.mountPoint requis (chaîne)")
        if "fs" in entry:
            expect(isinstance(entry["fs"], str), f"{where}.fs doit être une chaîne")
        if "options" in entry:
            expect_str_list(entry["options"], f"{where}.options")
        if "efi" in entry:
            expect(isinstance(entry["efi"], bool), f"{where}.efi doit être un booléen")

    subvolumes = data.get("btrfsSubvolumes")
    expect(isinstance(subvolumes, list) and subvolumes, "btrfsSubvolumes requis et non vide")
    mapping: dict[str, str] = {}
    for index, entry in enumerate(subvolumes):
        where = f"btrfsSubvolumes[{index}]"
        expect(isinstance(entry, dict), f"{where} doit être un dictionnaire")
        expect(set(entry) == {"mountPoint", "subvolume"},
               f"{where} doit contenir exactement mountPoint et subvolume")
        expect(isinstance(entry["mountPoint"], str) and entry["mountPoint"].startswith("/"),
               f"{where}.mountPoint doit être un chemin absolu")
        expect(isinstance(entry["subvolume"], str) and entry["subvolume"].startswith("/@"),
               f"{where}.subvolume doit commencer par /@")
        expect(entry["mountPoint"] not in mapping, f"{where} : point de montage dupliqué")
        mapping[entry["mountPoint"]] = entry["subvolume"]
    expect(mapping.get("/") == "/@", "le sous-volume /@ doit être monté sur /")
    expect(mapping.get("/home") == "/@home", "le sous-volume /@home doit être monté sur /home")

    if "btrfsSwapSubvol" in data:
        expect(isinstance(data["btrfsSwapSubvol"], str), "btrfsSwapSubvol doit être une chaîne")

    options = data.get("mountOptions")
    expect(isinstance(options, list) and options, "mountOptions requis et non vide")
    by_fs: dict[str, dict] = {}
    for index, entry in enumerate(options):
        where = f"mountOptions[{index}]"
        expect(isinstance(entry, dict), f"{where} doit être un dictionnaire")
        extra = set(entry) - {"filesystem", "options", "ssdOptions", "hddOptions"}
        expect(not extra, f"{where} : clés inconnues {sorted(extra)}")
        expect(isinstance(entry.get("filesystem"), str), f"{where}.filesystem requis")
        for key in ("options", "ssdOptions", "hddOptions"):
            if key in entry:
                expect_str_list(entry[key], f"{where}.{key}")
        by_fs[entry["filesystem"]] = entry
    btrfs = by_fs.get("btrfs")
    expect(btrfs is not None, "mountOptions doit définir le système de fichiers btrfs")
    btrfs_all = set(btrfs.get("options", [])) | set(btrfs.get("ssdOptions", []))
    for required in ("compress=zstd", "ssd", "noatime", "discard=async"):
        expect(required in btrfs_all, f"option BTRFS manquante : {required}")


# --------------------------------------------------------------------------
# fstab.conf — schéma Calamares 3.3 (src/modules/fstab/fstab.schema.yaml)
# --------------------------------------------------------------------------
def check_fstab_conf(path: Path) -> None:
    data = load_yaml(path)
    expect(isinstance(data, dict), "la racine doit être un dictionnaire")
    unknown = set(data) - {"crypttabOptions", "tmpOptions"}
    expect(not unknown, f"clés inconnues du schéma fstab : {sorted(unknown)}")
    tmp = data.get("tmpOptions")
    expect(isinstance(tmp, dict) and "default" in tmp, "tmpOptions.default est requis")
    for name, entry in tmp.items():
        expect(name in {"default", "ssd"}, f"tmpOptions.{name} inconnu")
        expect(isinstance(entry, dict), f"tmpOptions.{name} doit être un dictionnaire")
        expect(set(entry) <= {"tmpfs", "options"}, f"tmpOptions.{name} : clés inconnues")
        if "tmpfs" in entry:
            expect(isinstance(entry["tmpfs"], bool), f"tmpOptions.{name}.tmpfs booléen")
        if "options" in entry:
            expect(isinstance(entry["options"], str), f"tmpOptions.{name}.options chaîne")


# --------------------------------------------------------------------------
# flathub.conf — INI strict
# --------------------------------------------------------------------------
def check_flathub_conf(path: Path) -> None:
    parser = load_ini(path)
    expect(parser.sections() == ['remote "flathub"'],
           "flathub.conf doit contenir uniquement la section [remote \"flathub\"]")
    section = parser['remote "flathub"']
    expect(section.get("url") == FLATHUB_URL, f"url attendue : {FLATHUB_URL}")


def check_settings(path: Path) -> None:
    data = load_yaml(path)
    expect(isinstance(data, dict), "la racine doit être un dictionnaire")
    for key in ("modules-search", "sequence", "branding"):
        expect(key in data, f"clé {key} requise")
    instances = {
        f"{item['module']}@{item['id']}": item["config"] for item in data.get("instances", [])
    }
    for step in data["sequence"]:
        expect(isinstance(step, dict) and len(step) == 1, "chaque étape doit être show: ou exec:")
        (kind, modules), = step.items()
        expect(kind in {"show", "exec"}, f"étape inconnue : {kind}")
        for module in modules:
            if module in instances:
                conf = MODULES / instances[module]
            else:
                conf = MODULES / f"{module}.conf"
            expect(conf.is_file() or module in MODULES_WITHOUT_CONF,
                   f"module {module} : configuration {conf.name} absente")


def check_generic_yaml(path: Path) -> None:
    data = load_yaml(path)
    expect(isinstance(data, dict), "la racine doit être un dictionnaire")


def check_generic_ini(path: Path) -> None:
    load_ini(path)


def check_systemd_unit(path: Path) -> None:
    # systemd autorise la répétition d'une clé (ExecStartPre=, After=…) :
    # syntaxe INI validée sans l'interdiction des doublons.
    parser = load_ini(path, strict=False)
    for section in ("Unit", "Service"):
        if not parser.has_section(section):
            raise ValidationError(f"section [{section}] absente")
    if not parser.has_option("Service", "ExecStart"):
        raise ValidationError("ExecStart absent de [Service]")


def main() -> int:
    checks: list[tuple[Path, Callable[[Path], None]]] = [
        (MOUNT_CONF, check_mount_conf),
        (FSTAB_CONF, check_fstab_conf),
        (FLATHUB_CONF, check_flathub_conf),
        (SETTINGS_CONF, check_settings),
    ]
    dedicated = {path for path, _ in checks}
    for path in sorted(MODULES.glob("*.conf")):
        if path not in dedicated:
            checks.append((path, check_generic_yaml))
    ini_globs = (
        "etc/sddm.conf.d/*.conf",
        "etc/xdg/autostart/*.desktop",
        "usr/share/applications/*.desktop",
        "etc/skel/.config/*rc",
    )
    for pattern in ini_globs:
        for path in sorted(CHROOT.glob(pattern)):
            checks.append((path, check_generic_ini))
    for pattern in ("packages/*/root/etc/xdg/autostart/*.desktop",
                    "packages/*/root/usr/share/applications/*.desktop",
                    "packages/*/root/etc/xdg/*rc"):
        for path in sorted(ROOT.glob(pattern)):
            checks.append((path, check_generic_ini))
    for path in sorted(ROOT.glob("packages/*/root/usr/lib/systemd/system/*.service")):
        checks.append((path, check_systemd_unit))
    for path in sorted(ROOT.glob("packages/*/root/usr/share/djerba/*.yaml")):
        checks.append((path, check_generic_yaml))

    failures = 0
    for path, check in checks:
        relative = path.relative_to(ROOT)
        if not path.is_file():
            print(f"[FAIL] {relative} : fichier introuvable")
            failures += 1
            continue
        try:
            check(path)
        except ValidationError as exc:
            print(f"[FAIL] {relative} : {exc}")
            failures += 1
        else:
            print(f"[ OK ] {relative}")
    print(f"\n{len(checks) - failures}/{len(checks)} fichiers valides")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
