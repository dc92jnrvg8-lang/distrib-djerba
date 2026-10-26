#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba OS — applique les applications choisies dans l'installateur.

La page « Applications » est un module netinstall généré depuis le catalogue
/usr/share/djerba/apps.yaml : chaque application y figure sous la forme d'un
pseudo-paquet « djerba-app:<id> ». Ce module, placé avant le module
« packages » dans la séquence :

1. retire ces pseudo-paquets des opérations de paquets (le module
   « packages » ne doit pas tenter de les installer avec apt) ;
2. écrit la sélection dans le système cible ;
3. donne au chroot l'accès DNS du système live ;
4. lance « djerba-apps apply-selection --installer » dans la cible, qui
   retire les applications préinstallées décochées, installe les paquets APT
   si le réseau est disponible et met le reste en file d'attente pour le
   premier démarrage.
"""

import json
import os
import shutil
import subprocess

import libcalamares
from libcalamares.utils import target_env_process_output

PREFIX = "djerba-app:"
SELECTION = "/var/lib/djerba/apps/selection.json"


_STATUS = "Préparation des applications choisies…"


def pretty_name():
    return "Applications de Djerba OS"


def pretty_status_message():
    return _STATUS


def split_selection(operations):
    """Sépare les pseudo-paquets djerba-app:<id> des vraies opérations."""
    selected = set()
    cleaned = []
    for operation in operations or []:
        new_operation = {}
        for key, value in operation.items():
            if isinstance(value, list):
                kept = []
                for item in value:
                    name = item if isinstance(item, str) else item.get("package", "")
                    if name.startswith(PREFIX):
                        selected.add(name[len(PREFIX):])
                    else:
                        kept.append(item)
                new_operation[key] = kept
            else:
                new_operation[key] = value
        cleaned.append(new_operation)
    return sorted(selected), cleaned


def provide_dns(root):
    """Le résolveur de la cible (systemd-resolved) ne tourne pas dans le chroot :
    son fichier de stub, dans le /run vierge monté par Calamares, reçoit la
    configuration DNS du système live."""
    source = "/etc/resolv.conf"
    if not os.path.exists(source):
        return
    for relative in ("run/systemd/resolve/stub-resolv.conf", "run/systemd/resolve/resolv.conf"):
        target = os.path.join(root, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)


def run():
    gs = libcalamares.globalstorage
    root = gs.value("rootMountPoint")
    if not root:
        return ("Applications", "Point de montage de la cible introuvable.")

    selected, cleaned = split_selection(gs.value("packageOperations"))
    gs.insert("packageOperations", cleaned)
    libcalamares.utils.debug(f"djerbaapps : sélection {selected}")

    state = os.path.join(root, SELECTION.lstrip("/"))
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "w", encoding="utf-8") as handle:
        json.dump({"selected": selected}, handle, ensure_ascii=False, indent=2)

    provide_dns(root)

    def on_line(line):
        global _STATUS
        try:
            event = json.loads(line)
        except ValueError:
            libcalamares.utils.debug(f"djerba-apps : {line}")
            return
        message = event.get("message")
        if message:
            _STATUS = message
        total = event.get("total") or 0
        if total:
            libcalamares.job.setprogress(min(1.0, event.get("done", 0) / total))

    try:
        target_env_process_output(["djerba-apps", "apply-selection", SELECTION,
                                   "--installer", "--json-progress"], on_line)
    except subprocess.CalledProcessError as error:
        # Les applications sont un confort : leur échec ne doit pas faire
        # échouer l'installation du système. Elles restent proposées par
        # l'assistant au premier démarrage.
        libcalamares.utils.warning(f"djerba-apps a échoué : {error}")
    libcalamares.job.setprogress(1.0)
    return None
