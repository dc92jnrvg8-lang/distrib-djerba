# SPDX-License-Identifier: GPL-3.0-or-later
"""Matériel : résumé, disques, USB/PCI, Bluetooth, manettes, batterie, réseau."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path

from djerba_ai.tools.base import IFACE, MAC, command_tool, fail, function_tool, ok, run_command

C = "hardware"


def _summary(_params: dict, _verbose: bool) -> dict:
    cpu = ""
    for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
        if line.startswith(("model name", "Model")):
            cpu = line.split(":", 1)[1].strip()
            break
    mem_kb = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                      if line.startswith("MemTotal")))
    gpus = [line.split(": ", 1)[-1] for line in run_command(["lspci"], timeout=20).stdout.splitlines()
            if any(kind in line for kind in ("VGA", "3D controller", "Display controller"))]
    disks = json.loads(run_command(["lsblk", "-J", "-d", "-o", "NAME,SIZE,MODEL,ROTA,TRAN"], timeout=20).stdout
                       or '{"blockdevices": []}')["blockdevices"]
    disks = [d for d in disks if not d["name"].startswith(("loop", "zram", "sr"))]
    return ok(f"{cpu or platform.machine()} · {round(mem_kb / 1048576)} Go RAM · "
              f"{'; '.join(gpus) or 'GPU non PCI'}",
              cpu=cpu, cores=os.cpu_count(), memory_gb=round(mem_kb / 1048576, 1), gpus=gpus, disks=disks,
              kernel=platform.release(), architecture=platform.machine())


def _gamepads(_params: dict, _verbose: bool) -> dict:
    devices, current = [], {}
    for line in Path("/proc/bus/input/devices").read_text(errors="replace").splitlines() + [""]:
        if not line.strip():
            if current.get("handlers") and re.search(r"\bjs\d+", current["handlers"]):
                devices.append({"name": current.get("name", ""), "handlers": current["handlers"]})
            current = {}
        elif line.startswith("N: Name="):
            current["name"] = line.split("=", 1)[1].strip('"')
        elif line.startswith("H: Handlers="):
            current["handlers"] = line.split("=", 1)[1].strip()
    if not devices:
        return ok("Aucune manette détectée (branchez-la ou appairez-la en Bluetooth).", gamepads=[])
    return ok(f"{len(devices)} manette(s) : " + ", ".join(d["name"] for d in devices), gamepads=devices)


def _battery(_params: dict, _verbose: bool) -> dict:
    batteries = [path for path in run_command(["upower", "-e"], timeout=10).stdout.split() if "battery_" in path]
    if not batteries:
        return ok("Aucune batterie (ordinateur de bureau).", batteries=[])
    details = []
    for battery in batteries:
        info = run_command(["upower", "-i", battery], timeout=10).stdout
        fields = {key.strip(): value.strip() for key, _, value in
                  (line.partition(":") for line in info.splitlines() if ":" in line)}
        details.append({"percentage": fields.get("percentage", ""), "state": fields.get("state", ""),
                        "time": fields.get("time to empty", fields.get("time to full", "")),
                        "capacity": fields.get("capacity", ""), "model": fields.get("model", "")})
    first = details[0]
    return ok(f"Batterie {first['percentage']} ({first['state']}{', ' + first['time'] if first['time'] else ''})",
              batteries=details)


TOOLS = [
    function_tool("hardware_summary", C, "Résumé du matériel : processeur, mémoire, GPU, disques.", _summary,
                  keywords=("matériel", "configuration", "pc", "specs", "hardware"), binaries=("lspci", "lsblk")),
    command_tool("block_devices", C, "Disques et partitions (taille, système de fichiers, montage).",
                 ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,MODEL"],
                 keywords=("disques", "partitions", "clé usb", "lsblk"), done="Disques et partitions."),
    command_tool("usb_devices", C, "Périphériques USB connectés.", ["lsusb"],
                 keywords=("usb", "périphériques", "branché"), done="Périphériques USB."),
    command_tool("pci_devices", C, "Périphériques PCI et pilotes utilisés.", ["lspci", "-nnk"],
                 keywords=("pci", "pilotes", "drivers", "carte"), done="Périphériques PCI."),
    command_tool("bluetooth_status", C, "État de l'adaptateur Bluetooth.", ["bluetoothctl", "show"],
                 keywords=("bluetooth",), done="Adaptateur Bluetooth."),
    command_tool("bluetooth_power", C, "Allume ou éteint le Bluetooth.", ["bluetoothctl", "power", "{state}"],
                 parameters={"state": {"type": "string", "description": "on ou off", "enum": ["on", "off"]}},
                 required=("state",), keywords=("bluetooth", "activer bluetooth"), done="Bluetooth : {state}."),
    command_tool("bluetooth_scan", C, "Recherche les appareils Bluetooth à proximité (15 s).",
                 ["bluetoothctl", "--timeout", "15", "scan", "on"], timeout=30,
                 keywords=("appairer", "manette bluetooth", "casque bluetooth", "rechercher"),
                 done="Recherche terminée (voir bluetooth_devices)."),
    command_tool("bluetooth_devices", C, "Appareils Bluetooth connus.", ["bluetoothctl", "devices"],
                 keywords=("bluetooth", "appareils"), done="Appareils Bluetooth."),
    command_tool("bluetooth_pair", C, "Appaire et fait confiance à un appareil Bluetooth (adresse MAC).",
                 ["bluetoothctl", "--timeout", "30", "pair", "{mac}"],
                 parameters={"mac": {"type": "string", "description": "adresse", "pattern": MAC}},
                 required=("mac",), timeout=60, keywords=("appairer", "jumeler", "pair"), done="{mac} appairé."),
    command_tool("bluetooth_connect", C, "Connecte un appareil Bluetooth appairé.",
                 ["bluetoothctl", "connect", "{mac}"],
                 parameters={"mac": {"type": "string", "description": "adresse", "pattern": MAC}},
                 required=("mac",), timeout=60, keywords=("connecter", "casque", "manette"), done="{mac} connecté."),
    command_tool("bluetooth_disconnect", C, "Déconnecte un appareil Bluetooth.",
                 ["bluetoothctl", "disconnect", "{mac}"],
                 parameters={"mac": {"type": "string", "description": "adresse", "pattern": MAC}},
                 required=("mac",), keywords=("déconnecter",), done="{mac} déconnecté."),
    function_tool("gamepads", C, "Manettes de jeu détectées (USB et Bluetooth).", _gamepads,
                  keywords=("manette", "gamepad", "joystick", "xbox", "dualsense", "controller")),
    function_tool("battery", C, "Niveau, état et usure de la batterie.", _battery,
                  keywords=("batterie", "autonomie", "charge"), binaries=("upower",)),
    command_tool("wifi_list", C, "Réseaux Wi-Fi disponibles.", ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                 keywords=("wifi", "réseaux sans fil"), done="Réseaux Wi-Fi."),
    command_tool("wifi_connect", C, "Se connecte à un réseau Wi-Fi.",
                 ["nmcli", "dev", "wifi", "connect", "{ssid}", "password", "{password}"],
                 parameters={"ssid": {"type": "string", "description": "nom du réseau", "pattern": r"^[^\x00]{1,32}$",
                                      "allow_dash": True},
                             "password": {"type": "string", "description": "mot de passe",
                                          "pattern": r"^[^\x00]{8,63}$", "allow_dash": True}},
                 required=("ssid", "password"), timeout=60, keywords=("wifi", "se connecter", "réseau"),
                 done="Connecté à {ssid}."),
    command_tool("network_devices", C, "Interfaces réseau et leur état.", ["nmcli", "-t", "device", "status"],
                 keywords=("réseau", "ethernet", "interfaces"), done="Interfaces réseau."),
    command_tool("network_connectivity", C, "Vérifie l'accès à Internet (NetworkManager).",
                 ["nmcli", "networking", "connectivity", "check"], timeout=30,
                 keywords=("internet", "connexion", "hors ligne"), done="Connectivité."),
    command_tool("rfkill_list", C, "Blocages radio (Wi-Fi, Bluetooth, mode avion).", ["rfkill", "list"],
                 keywords=("mode avion", "rfkill", "wifi bloqué"), done="État des radios."),
    command_tool("rfkill_unblock", C, "Débloque une radio (mode avion).", ["rfkill", "unblock", "{radio}"],
                 parameters={"radio": {"type": "string", "description": "wifi, bluetooth ou all",
                                       "enum": ["wifi", "bluetooth", "all"]}},
                 required=("radio",), keywords=("débloquer wifi", "mode avion"), done="{radio} débloqué."),
    command_tool("interface_details", C, "Adresses et état d'une interface réseau.", ["ip", "-br", "addr", "show", "{interface}"],
                 parameters={"interface": {"type": "string", "description": "interface", "pattern": IFACE}},
                 required=("interface",), keywords=("adresse ip locale", "interface"), done="Interface {interface}."),
]
