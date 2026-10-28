# SPDX-License-Identifier: GPL-3.0-or-later
"""Système : instantanés BTRFS, GRUB, SDDM, nom d'hôte, heure, cgroups, alimentation."""

from __future__ import annotations

import platform
import re
import time
from pathlib import Path

from djerba_ai.tools.base import HOSTNAME, command_tool, fail, function_tool, ok, run_command

C = "system"
SNAPSHOTS = Path("/.snapshots")
SNAPSHOT_NAME = r"^[A-Za-z0-9_.-]{1,64}$"
USER = r"^[a-z_][a-z0-9_-]{0,31}$"


def _os_info(_params: dict, _verbose: bool) -> dict:
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    return ok(f"{release.get('PRETTY_NAME', 'Djerba OS')} · noyau {platform.release()} · "
              f"allumé depuis {int(uptime // 3600)} h {int(uptime % 3600 // 60)} min",
              os_release=release, kernel=platform.release(), uptime_s=int(uptime))


def _root_is_btrfs() -> bool:
    result = run_command(["findmnt", "-n", "-o", "FSTYPE", "/"], timeout=10)
    return result.stdout.strip() == "btrfs"


def _snapshot_create(params: dict, _verbose: bool) -> dict:
    if not _root_is_btrfs():
        return fail("la racine n'est pas en BTRFS (session live ou autre système de fichiers)")
    name = params.get("name") or time.strftime("djerba-%Y%m%d-%H%M%S")
    SNAPSHOTS.mkdir(mode=0o750, exist_ok=True)
    target = SNAPSHOTS / name
    if target.exists():
        return fail(f"l'instantané {name} existe déjà")
    result = run_command(["btrfs", "subvolume", "snapshot", "-r", "/", str(target)], timeout=120)
    if result.returncode != 0:
        return fail(result.stderr.strip() or "instantané impossible")
    return ok(f"Instantané en lecture seule créé : {target}", path=str(target))


def _snapshot_list(_params: dict, _verbose: bool) -> dict:
    if not SNAPSHOTS.is_dir():
        return ok("Aucun instantané.", snapshots=[])
    snapshots = []
    for entry in sorted(SNAPSHOTS.iterdir()):
        info = run_command(["btrfs", "subvolume", "show", str(entry)], timeout=20)
        created = next((line.split(":", 1)[1].strip() for line in info.stdout.splitlines()
                        if line.strip().startswith("Creation time")), "")
        snapshots.append({"name": entry.name, "created": created})
    return ok(f"{len(snapshots)} instantané(s)", snapshots=snapshots)


def _snapshot_delete(params: dict, _verbose: bool) -> dict:
    target = SNAPSHOTS / params["name"]
    if not target.is_dir():
        return fail(f"instantané {params['name']} introuvable")
    result = run_command(["btrfs", "subvolume", "delete", str(target)], timeout=300)
    if result.returncode != 0:
        return fail(result.stderr.strip() or "suppression impossible")
    return ok(f"Instantané {params['name']} supprimé.")


def _grub_timeout(params: dict, _verbose: bool) -> dict:
    path = Path("/etc/default/grub.d/90-djerba-user.cfg")
    lines = [line for line in (path.read_text().splitlines() if path.exists() else [])
             if not line.startswith("GRUB_TIMEOUT=")]
    lines.append(f"GRUB_TIMEOUT={params['seconds']}")
    path.write_text("# Réglages GRUB choisis par l'utilisateur (Djerba).\n" +
                    "\n".join(line for line in lines if not line.startswith("#")) + "\n")
    result = run_command(["update-grub"], timeout=300)
    if result.returncode != 0:
        return fail("update-grub a échoué : " + result.stderr.strip()[-300:])
    return ok(f"Menu de démarrage affiché {params['seconds']} s.")


def _grub_entries(_params: dict, _verbose: bool) -> dict:
    try:
        text = Path("/boot/grub/grub.cfg").read_text(errors="replace")
    except OSError:
        return fail("grub.cfg illisible (session live ?)")
    entries = re.findall(r"^\s*menuentry\s+'([^']+)'", text, flags=re.MULTILINE)
    return ok(f"{len(entries)} entrée(s) de démarrage", entries=entries)


def _autologin(params: dict, _verbose: bool) -> dict:
    path = Path("/etc/sddm.conf.d/90-djerba-autologin.conf")
    if not params["enabled"]:
        path.unlink(missing_ok=True)
        return ok("Connexion automatique désactivée.")
    user = params.get("user", "")
    if not user or run_command(["getent", "passwd", user], timeout=10).returncode != 0:
        return fail("utilisateur inconnu")
    path.write_text(f"# Djerba : connexion automatique.\n[Autologin]\nUser={user}\nSession=plasma\n")
    return ok(f"Connexion automatique de {user} dans Plasma (Wayland).")


def _users(_params: dict, _verbose: bool) -> dict:
    users = []
    for line in Path("/etc/passwd").read_text().splitlines():
        name, _x, uid, _gid, gecos, home, shell = line.split(":")
        if 1000 <= int(uid) < 60000:
            users.append({"user": name, "uid": int(uid), "name": gecos.split(",")[0], "home": home,
                          "shell": shell})
    return ok(f"{len(users)} compte(s) utilisateur", users=users)


TOOLS = [
    function_tool("os_info", C, "Version de Djerba OS, noyau et durée de fonctionnement.", _os_info,
                  keywords=("version", "système", "noyau", "uptime", "os")),
    function_tool("snapshot_create", C, "Crée un instantané BTRFS en lecture seule du système (avant une modification risquée).",
                  _snapshot_create,
                  parameters={"name": {"type": "string", "description": "nom (facultatif)", "pattern": SNAPSHOT_NAME}},
                  requires_root=True, mutates=False, keywords=("instantané", "snapshot", "sauvegarde système", "point de restauration"),
                  binaries=("btrfs", "findmnt")),
    function_tool("snapshot_list", C, "Instantanés BTRFS du système.", _snapshot_list, requires_root=True, mutates=False,
                  keywords=("instantanés", "snapshots", "points de restauration"), binaries=("btrfs",)),
    function_tool("snapshot_delete", C, "Supprime un instantané BTRFS.", _snapshot_delete,
                  parameters={"name": {"type": "string", "description": "nom", "pattern": SNAPSHOT_NAME}},
                  required=("name",), requires_root=True, mutates=False, dangerous=True, keywords=("supprimer instantané",),
                  binaries=("btrfs",)),
    command_tool("btrfs_usage", C, "Occupation détaillée du volume BTRFS.", ["btrfs", "filesystem", "usage", "-h", "/"],
                 requires_root=True, mutates=False, keywords=("btrfs", "espace", "usage"), done="Occupation BTRFS."),
    command_tool("grub_update", C, "Régénère la configuration de démarrage GRUB.", ["update-grub"],
                 requires_root=True, timeout=300, keywords=("grub", "démarrage", "update-grub"),
                 done="Configuration GRUB régénérée."),
    function_tool("grub_timeout", C, "Durée d'affichage du menu de démarrage.", _grub_timeout,
                  parameters={"seconds": {"type": "integer", "description": "secondes", "minimum": 0, "maximum": 60}},
                  required=("seconds",), requires_root=True, dangerous=True,
                  keywords=("grub", "menu de démarrage", "délai", "dual boot"), binaries=("update-grub",)),
    function_tool("grub_entries", C, "Entrées du menu de démarrage (systèmes détectés).", _grub_entries,
                  requires_root=True, mutates=False, keywords=("grub", "dual boot", "windows", "entrées")),
    function_tool("sddm_autologin", C, "Active ou désactive la connexion automatique.", _autologin,
                  parameters={"enabled": {"type": "boolean", "description": "activer"},
                              "user": {"type": "string", "description": "utilisateur", "pattern": USER}},
                  required=("enabled",), requires_root=True, dangerous=True,
                  keywords=("connexion automatique", "autologin", "sddm", "mot de passe au démarrage")),
    command_tool("sddm_theme", C, "Thème de l'écran de connexion.", ["grep", "-rh", "^Current=", "/etc/sddm.conf.d/"],
                 keywords=("écran de connexion", "sddm", "login"), done="Thème SDDM."),
    command_tool("hostname_set", C, "Change le nom de l'ordinateur sur le réseau.",
                 ["hostnamectl", "set-hostname", "{name}"],
                 parameters={"name": {"type": "string", "description": "nom", "pattern": HOSTNAME}},
                 required=("name",), requires_root=True, dangerous=True, keywords=("nom de l'ordinateur", "hostname"),
                 done="Nom de l'ordinateur : {name}."),
    command_tool("time_status", C, "Heure, fuseau horaire et synchronisation NTP.", ["timedatectl", "status"],
                 keywords=("heure", "fuseau", "date", "ntp"), done="Date et heure."),
    command_tool("timezone_set", C, "Change le fuseau horaire (ex. Europe/Paris, Africa/Tunis).",
                 ["timedatectl", "set-timezone", "{zone}"],
                 parameters={"zone": {"type": "string", "description": "fuseau",
                                      "pattern": r"^[A-Z][A-Za-z_]+(/[A-Za-z_+-]+){0,2}$"}},
                 required=("zone",), requires_root=True, keywords=("fuseau horaire", "timezone", "heure"),
                 done="Fuseau horaire : {zone}."),
    command_tool("locale_status", C, "Langue du système et clavier de la console.", ["localectl", "status"],
                 keywords=("langue", "locale", "clavier"), done="Langue et clavier."),
    command_tool("cgroup_tree", C, "Arborescence des groupes de contrôle (cgroups v2) et de leurs processus.",
                 ["systemd-cgls", "--no-pager", "-l"], keywords=("cgroups", "arborescence", "processus"),
                 done="Arborescence des cgroups."),
    command_tool("kernel_cmdline", C, "Paramètres de démarrage du noyau.", ["cat", "/proc/cmdline"],
                 keywords=("noyau", "paramètres", "cmdline", "preempt"), done="Paramètres du noyau."),
    command_tool("initramfs_update", C, "Régénère l'image de démarrage initramfs.", ["update-initramfs", "-u"],
                 requires_root=True, timeout=600, keywords=("initramfs", "démarrage"), done="initramfs régénéré."),
    function_tool("users_list", C, "Comptes utilisateurs de l'ordinateur.", _users,
                  keywords=("utilisateurs", "comptes", "users")),
    command_tool("reboot", C, "Redémarre l'ordinateur.", ["systemctl", "reboot"], dangerous=True,
                 keywords=("redémarrer", "reboot"), done="Redémarrage…"),
    command_tool("poweroff", C, "Éteint l'ordinateur.", ["systemctl", "poweroff"], dangerous=True,
                 keywords=("éteindre", "arrêter", "shutdown"), done="Arrêt…"),
    command_tool("suspend", C, "Met l'ordinateur en veille.", ["systemctl", "suspend"], dangerous=True,
                 keywords=("veille", "suspendre", "sleep"), done="Mise en veille…"),
]
