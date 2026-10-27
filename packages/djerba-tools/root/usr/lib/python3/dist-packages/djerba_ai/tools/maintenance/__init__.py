# SPDX-License-Identifier: GPL-3.0-or-later
"""Maintenance : paquets, disques, journaux, services, nettoyage."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from djerba_ai.tools.base import (DEVICE, PACKAGE, UNIT, clip, command_tool, fail, function_tool, ok,
                                  run_command)

C = "maintenance"
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none"}
LOCK = ["-o", "DPkg::Lock::Timeout=300"]


def _upgradable(output: str) -> list[dict]:
    rows = []
    for line in output.splitlines():
        if "/" in line and "[" in line:
            name, rest = line.split("/", 1)
            parts = rest.split()
            rows.append({"package": name, "version": parts[1] if len(parts) > 1 else "",
                         "security": "-security" in parts[0]})
    return rows


def _largest_dirs(params: dict, _verbose: bool) -> dict:
    base = Path(os.path.expanduser(params["path"])).resolve()
    if not base.is_dir():
        return fail(f"{base} n'est pas un dossier")
    sizes = []
    for entry in os.scandir(base):
        if entry.is_symlink():
            continue
        total = 0
        if entry.is_dir(follow_symlinks=False):
            for root, _dirs, files in os.walk(entry.path, onerror=lambda _e: None):
                for name in files:
                    try:
                        total += os.lstat(os.path.join(root, name)).st_blocks * 512
                    except OSError:
                        pass
        else:
            try:
                total = entry.stat(follow_symlinks=False).st_blocks * 512
            except OSError:
                continue
        sizes.append((total, entry.name))
    sizes.sort(reverse=True)
    top = [{"name": name, "size_mb": round(size / 1048576, 1)} for size, name in sizes[: params["count"]]]
    return ok(f"Éléments les plus volumineux de {base}", entries=top)


def _clean_user_cache(params: dict, _verbose: bool) -> dict:
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    targets = [cache / "thumbnails"]
    if params["all"]:
        targets = [entry for entry in cache.iterdir()] if cache.is_dir() else []
    freed = 0
    for target in targets:
        if not target.exists():
            continue
        for root, _dirs, files in os.walk(target):
            for name in files:
                try:
                    freed += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
    return ok(f"Cache nettoyé : {freed / 1048576:.1f} Mio libérés", freed_mb=round(freed / 1048576, 1))


def _boot_analysis(_params: dict, _verbose: bool) -> dict:
    total = run_command(["systemd-analyze", "time"], timeout=30)
    blame = run_command(["systemd-analyze", "blame", "--no-pager"], timeout=30)
    slowest = [line.strip() for line in blame.stdout.splitlines()[:10]]
    return ok(total.stdout.strip().splitlines()[0] if total.stdout else "Analyse du démarrage",
              slowest=slowest)


TOOLS = [
    command_tool("apt_update", C, "Met à jour les listes de paquets Debian (apt-get update).",
                 ["apt-get", "update", *LOCK], requires_root=True, env=APT_ENV, timeout=600,
                 keywords=("mise à jour", "dépôts", "update", "liste paquets"), done="Listes de paquets à jour."),
    command_tool("apt_upgradable", C, "Liste les paquets pouvant être mis à jour (dont mises à jour de sécurité).",
                 ["apt", "list", "--upgradable"], keywords=("mises à jour disponibles", "upgradable", "updates"),
                 parser=_upgradable, done="Paquets pouvant être mis à jour."),
    command_tool("apt_full_upgrade", C, "Installe toutes les mises à jour du système (apt-get full-upgrade).",
                 ["apt-get", "-y", *LOCK, "-o", "Dpkg::Options::=--force-confdef",
                  "-o", "Dpkg::Options::=--force-confold", "full-upgrade"],
                 requires_root=True, dangerous=True, env=APT_ENV, timeout=3600,
                 keywords=("mettre à jour le système", "upgrade", "mise à niveau"), done="Système à jour."),
    command_tool("apt_autoremove", C, "Supprime les paquets devenus inutiles et leur configuration.",
                 ["apt-get", "-y", *LOCK, "autoremove", "--purge"], requires_root=True, dangerous=True,
                 env=APT_ENV, timeout=900, keywords=("nettoyer", "inutiles", "autoremove"),
                 done="Paquets inutiles supprimés."),
    command_tool("apt_clean", C, "Vide le cache des paquets téléchargés (/var/cache/apt).",
                 ["apt-get", "clean"], requires_root=True, keywords=("cache apt", "libérer espace"),
                 done="Cache APT vidé."),
    command_tool("apt_fix_broken", C, "Répare des dépendances cassées (apt-get -f install).",
                 ["apt-get", "-y", *LOCK, "-f", "install"], requires_root=True, dangerous=True, env=APT_ENV,
                 timeout=1800, keywords=("réparer", "cassé", "broken", "dépendances"), done="Dépendances réparées."),
    command_tool("dpkg_configure_pending", C, "Termine la configuration de paquets interrompue (dpkg --configure -a).",
                 ["dpkg", "--configure", "-a"], requires_root=True, env=APT_ENV, timeout=1800,
                 keywords=("dpkg interrompu", "configure"), done="Configuration des paquets terminée."),
    command_tool("apt_search", C, "Recherche un paquet Debian par mot-clé.",
                 ["apt-cache", "search", "--names-only", "{query}"],
                 parameters={"query": {"type": "string", "description": "mot-clé", "pattern": r"^[\w.+ -]{2,60}$"}},
                 required=("query",), keywords=("chercher paquet", "search", "trouver logiciel"),
                 done="Résultats de la recherche."),
    command_tool("apt_show", C, "Affiche la description et la version d'un paquet Debian.",
                 ["apt-cache", "show", "--no-all-versions", "{package}"],
                 parameters={"package": {"type": "string", "description": "nom du paquet", "pattern": PACKAGE}},
                 required=("package",), keywords=("détails paquet", "version", "show"), done="Informations du paquet."),
    command_tool("apt_policy", C, "Indique la version installée et les dépôts d'origine d'un paquet.",
                 ["apt-cache", "policy", "{package}"],
                 parameters={"package": {"type": "string", "description": "nom du paquet", "pattern": PACKAGE}},
                 required=("package",), keywords=("dépôt", "priorité", "policy", "origine"), done="Politique APT."),
    command_tool("package_installed", C, "Vérifie si un paquet Debian est installé et sa version.",
                 ["dpkg-query", "-W", "-f=${{Package}} ${{Version}} ${{db:Status-Status}}\\n", "{package}"],
                 parameters={"package": {"type": "string", "description": "nom du paquet", "pattern": PACKAGE}},
                 required=("package",), success_codes=(0, 1), keywords=("installé", "installed", "version"),
                 done="État du paquet."),
    command_tool("fstrim_all", C, "Lance TRIM sur tous les SSD montés (fstrim -av).",
                 ["fstrim", "-av"], requires_root=True, timeout=900, keywords=("ssd", "trim", "optimiser disque"),
                 done="TRIM effectué."),
    command_tool("smart_scan", C, "Liste les disques analysables par SMART.",
                 ["smartctl", "--scan"], requires_root=True, keywords=("disques", "smart", "santé disque"),
                 done="Disques détectés."),
    command_tool("smart_health", C, "Bilan de santé SMART d'un disque (ex. /dev/sda, /dev/nvme0).",
                 ["smartctl", "-H", "-A", "{device}"],
                 parameters={"device": {"type": "string", "description": "périphérique", "pattern": DEVICE}},
                 required=("device",), requires_root=True, success_codes=(0, 4),
                 keywords=("santé disque", "smart", "panne disque", "ssd usé"), done="Bilan SMART de {device}."),
    command_tool("disk_usage", C, "Espace disque utilisé et libre par système de fichiers.",
                 ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"],
                 keywords=("espace disque", "disk space", "df", "plein"), done="Occupation des disques."),
    function_tool("largest_dirs", C, "Trouve les dossiers et fichiers les plus volumineux d'un emplacement.",
                  _largest_dirs,
                  parameters={"path": {"type": "string", "description": "dossier", "default": "~",
                                       "pattern": r"^[~/][^\x00]{0,500}$"},
                              "count": {"type": "integer", "description": "nombre", "default": 15,
                                        "minimum": 1, "maximum": 100}},
                  keywords=("gros fichiers", "volumineux", "place", "du")),
    command_tool("journal_errors", C, "Erreurs du journal système depuis le démarrage.",
                 ["journalctl", "-p", "err", "-b", "--no-pager", "-n", "{lines}", "-o", "short-iso"],
                 parameters={"lines": {"type": "integer", "description": "nombre de lignes", "default": 50,
                                       "minimum": 1, "maximum": 500}},
                 keywords=("erreurs", "journal", "logs", "plantage", "journalctl"), done="Erreurs récentes."),
    command_tool("journal_unit", C, "Journal d'un service systemd.",
                 ["journalctl", "-u", "{unit}", "-b", "--no-pager", "-n", "{lines}"],
                 parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT},
                             "lines": {"type": "integer", "description": "lignes", "default": 80,
                                       "minimum": 1, "maximum": 1000}},
                 required=("unit",), keywords=("logs service", "journal service"), done="Journal de {unit}."),
    command_tool("journal_vacuum", C, "Réduit le journal système aux N derniers jours.",
                 ["journalctl", "--vacuum-time={days}d"],
                 parameters={"days": {"type": "integer", "description": "jours conservés", "default": 14,
                                      "minimum": 1, "maximum": 365}},
                 requires_root=True, keywords=("nettoyer journal", "logs volumineux", "vacuum"),
                 done="Journal réduit à {days} jours."),
    command_tool("failed_units", C, "Services systemd en échec.",
                 ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
                 keywords=("services en échec", "failed", "panne service"), done="Services en échec."),
    command_tool("service_status", C, "État détaillé d'un service systemd.",
                 ["systemctl", "status", "--no-pager", "-n", "20", "{unit}"],
                 parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT}},
                 required=("unit",), success_codes=(0, 3), keywords=("état service", "status"),
                 done="État de {unit}."),
    command_tool("service_restart", C, "Redémarre un service système.",
                 ["systemctl", "restart", "{unit}"],
                 parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT}},
                 required=("unit",), requires_root=True, dangerous=True, keywords=("redémarrer service", "restart"),
                 done="{unit} redémarré."),
    command_tool("service_enable", C, "Active et démarre un service système.",
                 ["systemctl", "enable", "--now", "{unit}"],
                 parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT}},
                 required=("unit",), requires_root=True, dangerous=True, keywords=("activer service", "enable"),
                 done="{unit} activé."),
    command_tool("service_disable", C, "Désactive et arrête un service système.",
                 ["systemctl", "disable", "--now", "{unit}"],
                 parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT}},
                 required=("unit",), requires_root=True, dangerous=True, keywords=("désactiver service", "disable"),
                 done="{unit} désactivé."),
    function_tool("user_cache_clean", C, "Vide le cache de l'utilisateur (vignettes, ou tout ~/.cache).",
                  _clean_user_cache,
                  parameters={"all": {"type": "boolean", "description": "tout ~/.cache", "default": False}},
                  dangerous=True, keywords=("nettoyer cache", "vignettes", "libérer espace")),
    command_tool("flatpak_update", C, "Met à jour toutes les applications Flatpak du système.",
                 ["flatpak", "update", "--system", "-y", "--noninteractive"], requires_root=True, timeout=3600,
                 keywords=("mettre à jour flatpak", "flathub"), done="Applications Flatpak à jour."),
    command_tool("flatpak_unused", C, "Supprime les environnements Flatpak inutilisés.",
                 ["flatpak", "uninstall", "--system", "--unused", "-y", "--noninteractive"], requires_root=True,
                 dangerous=True, timeout=900, keywords=("runtimes inutiles", "nettoyer flatpak"),
                 done="Environnements inutilisés supprimés."),
    command_tool("btrfs_scrub_start", C, "Vérifie l'intégrité des données BTRFS (scrub) en arrière-plan.",
                 ["btrfs", "scrub", "start", "/"], requires_root=True, keywords=("intégrité", "btrfs", "scrub"),
                 done="Vérification BTRFS lancée."),
    command_tool("btrfs_scrub_status", C, "Progression et résultat de la vérification BTRFS.",
                 ["btrfs", "scrub", "status", "/"], requires_root=True, keywords=("scrub", "btrfs état"),
                 done="État de la vérification."),
    command_tool("btrfs_balance", C, "Rééquilibre légèrement BTRFS (blocs remplis à moins de 50 %).",
                 ["btrfs", "balance", "start", "-dusage=50", "-musage=50", "/"], requires_root=True,
                 dangerous=True, timeout=7200, keywords=("balance", "btrfs plein", "espace btrfs"),
                 done="Rééquilibrage terminé."),
    function_tool("boot_analysis", C, "Durée du démarrage et services les plus lents.", _boot_analysis,
                  keywords=("démarrage lent", "boot", "systemd-analyze"), binaries=("systemd-analyze",)),
]

# Utilisé par les rapports de santé (outil agentic.health_report).
__all__ = ["TOOLS", "clip"]
