# SPDX-License-Identifier: GPL-3.0-or-later
"""Performances : processeur, mémoire, processus, KWin, cgroups, ZRAM."""

from __future__ import annotations

import glob
import os
from pathlib import Path

from djerba_ai.tools.base import command_tool, fail, function_tool, ok, run_command

C = "performance"


def _cpu_governor(_params: dict, _verbose: bool) -> dict:
    governors = sorted({Path(path).read_text().strip()
                        for path in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")})
    driver = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver")
    epp = Path("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
    available = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors")
    if not governors:
        return fail("fréquence processeur non pilotable (machine virtuelle ?)")
    return ok(f"Gouverneur : {', '.join(governors)}", governors=governors,
              driver=driver.read_text().strip() if driver.exists() else "",
              epp=epp.read_text().strip() if epp.exists() else "",
              available=available.read_text().split() if available.exists() else [])


def _cpu_frequencies(_params: dict, _verbose: bool) -> dict:
    values = []
    for path in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"),
                       key=lambda p: int(p.split("/cpu")[2].split("/")[0])):
        values.append(int(Path(path).read_text()) // 1000)
    if not values:
        return fail("fréquences indisponibles")
    return ok(f"Fréquence moyenne : {sum(values) // len(values)} MHz (max {max(values)} MHz)", mhz=values)


def _load(_params: dict, _verbose: bool) -> dict:
    one, five, fifteen, *_rest = Path("/proc/loadavg").read_text().split()
    cpus = os.cpu_count() or 1
    return ok(f"Charge : {one} (1 min), {five} (5 min), {fifteen} (15 min) pour {cpus} cœurs",
              load=[float(one), float(five), float(fifteen)], cpus=cpus)


def _io_schedulers(_params: dict, _verbose: bool) -> dict:
    schedulers = {}
    for path in glob.glob("/sys/block/*/queue/scheduler"):
        device = path.split("/")[3]
        if device.startswith(("loop", "ram", "zram", "sr")):
            continue
        current = next((word.strip("[]") for word in Path(path).read_text().split() if word.startswith("[")), "")
        schedulers[device] = current
    return ok("Ordonnanceurs d'E/S : " + ", ".join(f"{d}={s}" for d, s in sorted(schedulers.items())),
              schedulers=schedulers)


def _thermal(_params: dict, _verbose: bool) -> dict:
    readings = []
    for zone in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:
            value = int(Path(zone).read_text()) / 1000
        except (OSError, ValueError):
            continue
        base = zone[: -len("_input")]
        label = Path(base + "_label").read_text().strip() if Path(base + "_label").exists() else ""
        chip = Path(os.path.dirname(zone), "name").read_text().strip()
        readings.append({"sensor": f"{chip} {label}".strip(), "celsius": round(value, 1)})
    if not readings:
        return fail("aucun capteur de température exposé")
    hottest = max(readings, key=lambda r: r["celsius"])
    return ok(f"Température maximale : {hottest['celsius']} °C ({hottest['sensor']})", sensors=readings)


def _gpu(_params: dict, _verbose: bool) -> dict:
    result = run_command(["lspci", "-nnk"], timeout=20)
    gpus, current = [], None
    for line in result.stdout.splitlines():
        if not line.startswith(("\t", " ")):
            current = None
            if any(kind in line for kind in ("VGA compatible", "3D controller", "Display controller")):
                current = {"device": line.split(": ", 1)[-1], "driver": ""}
                gpus.append(current)
        elif current is not None and "Kernel driver in use:" in line:
            current["driver"] = line.split(":", 1)[1].strip()
    if not gpus:
        return fail("aucune carte graphique PCI détectée")
    vulkan = run_command(["vulkaninfo", "--summary"], timeout=30) if os.path.exists("/usr/bin/vulkaninfo") else None
    devices = [line.split("=", 1)[1].strip() for line in (vulkan.stdout.splitlines() if vulkan else [])
               if line.strip().startswith("deviceName")]
    return ok("Carte(s) graphique(s) : " + "; ".join(f"{g['device']} [{g['driver']}]" for g in gpus),
              gpus=gpus, vulkan_devices=devices)


TOOLS = [
    command_tool("power_profile_get", C, "Profil d'énergie actuel (performance, équilibré, économie).",
                 ["powerprofilesctl", "get"], keywords=("profil énergie", "batterie", "performance"),
                 done="Profil d'énergie."),
    command_tool("power_profile_set", C, "Change le profil d'énergie du processeur.",
                 ["powerprofilesctl", "set", "{profile}"],
                 parameters={"profile": {"type": "string", "description": "profil",
                                         "enum": ["performance", "balanced", "power-saver"]}},
                 required=("profile",), keywords=("mode performance", "économie d'énergie", "profil"),
                 done="Profil d'énergie : {profile}."),
    function_tool("cpu_governor", C, "Gouverneur de fréquence, pilote et préférence énergétique du processeur.",
                  _cpu_governor, keywords=("gouverneur", "governor", "cpufreq", "epp")),
    command_tool("cpupower_governor", C, "Force le gouverneur du processeur avec cpupower (sans power-profiles-daemon).",
                 ["cpupower", "frequency-set", "-g", "{governor}"],
                 parameters={"governor": {"type": "string", "description": "gouverneur",
                                          "enum": ["performance", "powersave", "schedutil", "ondemand"]}},
                 required=("governor",), requires_root=True, keywords=("cpupower", "gouverneur", "fréquence"),
                 done="Gouverneur : {governor}."),
    function_tool("cpu_frequencies", C, "Fréquence actuelle de chaque cœur.", _cpu_frequencies,
                  keywords=("fréquence", "mhz", "turbo", "cpu")),
    function_tool("load_average", C, "Charge moyenne du système.", _load, keywords=("charge", "load", "lent")),
    command_tool("memory_usage", C, "Mémoire vive et swap utilisées.", ["free", "-h", "-w"],
                 keywords=("mémoire", "ram", "swap"), done="Utilisation mémoire."),
    command_tool("zram_status", C, "État de la mémoire compressée ZRAM (algorithme, taux de compression).",
                 ["zramctl", "--output-all"], keywords=("zram", "swap compressé", "compression mémoire"),
                 done="État ZRAM."),
    command_tool("swappiness_get", C, "Valeur de vm.swappiness.", ["sysctl", "vm.swappiness"],
                 keywords=("swappiness", "swap"), done="vm.swappiness."),
    command_tool("swappiness_set", C, "Règle vm.swappiness jusqu'au prochain démarrage (180 recommandé avec ZRAM).",
                 ["sysctl", "-w", "vm.swappiness={value}"],
                 parameters={"value": {"type": "integer", "description": "0 à 200", "minimum": 0, "maximum": 200}},
                 required=("value",), requires_root=True, keywords=("swappiness",), done="vm.swappiness = {value}."),
    command_tool("drop_caches", C, "Libère le cache disque en mémoire (vm.drop_caches=3).",
                 ["sysctl", "-w", "vm.drop_caches=3"], requires_root=True,
                 keywords=("libérer mémoire", "cache mémoire"), done="Cache libéré."),
    command_tool("top_cpu", C, "Processus qui consomment le plus de processeur.",
                 ["ps", "-eo", "pid,user,%cpu,%mem,comm", "--sort=-%cpu", "--no-headers"],
                 keywords=("processus", "cpu élevé", "ralentit", "top"), done="Processus les plus gourmands (CPU)."),
    command_tool("top_memory", C, "Processus qui consomment le plus de mémoire.",
                 ["ps", "-eo", "pid,user,%mem,rss,comm", "--sort=-%mem", "--no-headers"],
                 keywords=("processus", "mémoire élevée", "ram pleine"), done="Processus les plus gourmands (RAM)."),
    command_tool("renice_process", C, "Baisse la priorité d'un de vos processus (nice 0 à 19).",
                 ["renice", "-n", "{niceness}", "-p", "{pid}"],
                 parameters={"pid": {"type": "integer", "description": "PID", "minimum": 2},
                             "niceness": {"type": "integer", "description": "0 à 19", "minimum": 0, "maximum": 19}},
                 required=("pid", "niceness"), keywords=("priorité", "nice", "renice"),
                 done="Priorité du processus {pid} : {niceness}."),
    command_tool("kill_process", C, "Termine un de vos processus (signal TERM).",
                 ["kill", "-TERM", "{pid}"],
                 parameters={"pid": {"type": "integer", "description": "PID", "minimum": 2}},
                 required=("pid",), dangerous=True, keywords=("tuer", "fermer programme", "bloqué", "kill"),
                 done="Processus {pid} terminé."),
    command_tool("kwin_effects", C, "Active ou coupe les effets coûteux de KWin (flou, contraste, animations).",
                 ["djerba-gamemode", "effects", "{state}"],
                 parameters={"state": {"type": "string", "description": "on ou off", "enum": ["on", "off"]}},
                 required=("state",), keywords=("effets", "flou", "animations", "kwin"), done="Effets KWin : {state}."),
    command_tool("kwin_tearing", C, "Autorise le tearing en plein écran (latence minimale des jeux).",
                 ["djerba-gamemode", "tearing", "{state}"],
                 parameters={"state": {"type": "string", "description": "on ou off", "enum": ["on", "off"]}},
                 required=("state",), keywords=("tearing", "latence", "vsync"), done="Tearing : {state}."),
    command_tool("kwin_compositor", C, "Suspend ou reprend le compositeur KWin (session X11 uniquement).",
                 ["dbus-send", "--session", "--type=method_call", "--dest=org.kde.KWin", "/Compositor",
                  "org.kde.kwin.Compositing.{action}"],
                 parameters={"action": {"type": "string", "description": "suspend ou resume",
                                        "enum": ["suspend", "resume"]}},
                 required=("action",), keywords=("compositeur", "compositor", "x11"), done="Compositeur : {action}."),
    command_tool("gamemode_status", C, "Indique si GameMode optimise actuellement un jeu.",
                 ["gamemoded", "-s"], keywords=("gamemode", "feral"), done="État de GameMode."),
    command_tool("mangohud_toggle", C, "Active ou désactive la superposition MangoHud pour tous les jeux.",
                 ["djerba-gamemode", "mangohud", "{state}"],
                 parameters={"state": {"type": "string", "description": "on ou off", "enum": ["on", "off"]}},
                 required=("state",), keywords=("mangohud", "fps", "overlay", "compteur"), done="MangoHud : {state}."),
    command_tool("fps_limit", C, "Limite les images par seconde des jeux via MangoHud (0 = aucune).",
                 ["djerba-gamemode", "mangohud", "fps-limit", "{fps}"],
                 parameters={"fps": {"type": "integer", "description": "images/s", "minimum": 0, "maximum": 1000}},
                 required=("fps",), keywords=("limiter fps", "fps cap", "images par seconde"),
                 done="Limite d'images/s : {fps}."),
    function_tool("io_schedulers", C, "Ordonnanceur d'E/S de chaque disque.", _io_schedulers,
                  keywords=("ordonnanceur", "scheduler", "disque lent")),
    function_tool("temperatures", C, "Températures du processeur, du GPU et des disques.", _thermal,
                  keywords=("température", "chauffe", "surchauffe", "ventilateur")),
    function_tool("gpu_info", C, "Carte(s) graphique(s), pilote et périphériques Vulkan.", _gpu,
                  keywords=("gpu", "carte graphique", "pilote", "vulkan", "nvidia", "amd"), binaries=("lspci",)),
    command_tool("cgroup_cpu_weight", C, "Ajuste la part de processeur d'une application de votre session (cgroups v2).",
                 ["systemctl", "--user", "set-property", "--runtime", "{unit}", "CPUWeight={weight}"],
                 parameters={"unit": {"type": "string", "description": "unité (app-….scope)",
                                      "pattern": r"^[A-Za-z0-9@_.:-]{1,200}\.(scope|service|slice)$"},
                             "weight": {"type": "integer", "description": "1 à 10000 (100 par défaut)",
                                        "minimum": 1, "maximum": 10000}},
                 required=("unit", "weight"), keywords=("cgroup", "priorité cpu", "partage processeur"),
                 done="CPUWeight={weight} pour {unit}."),
    command_tool("cgroup_top", C, "Consommation par groupe de contrôle (cgroups v2).",
                 ["systemd-cgtop", "-b", "-n", "1", "--depth=3"], keywords=("cgroups", "consommation", "cgtop"),
                 done="Consommation par cgroup."),
]

