# SPDX-License-Identifier: GPL-3.0-or-later
"""Sécurité : mode anonyme, DNS, pare-feu, antivirus, AppArmor, réseau."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from djerba_ai.tools.base import IFACE, command_tool, fail, function_tool, ok, run_command

C = "security"
HOME_PATH = r"^(~|/home/[^/\x00]+|/media/[^\x00]+|/run/media/[^\x00]+)(/[^\x00]*)?$"


def _anonsurf_status(_params: dict, _verbose: bool) -> dict:
    result = run_command(["djerba-anonsurf", "status", "--json"], timeout=20)
    try:
        state = json.loads(result.stdout)
    except ValueError:
        return fail("état du mode anonyme illisible")
    label = "actif" if state.get("active") else ("en connexion" if state.get("starting") else "inactif")
    return ok(f"Mode anonyme {label}", **state)


def _scan(params: dict, _verbose: bool) -> dict:
    path = Path(os.path.expanduser(params["path"])).resolve()
    if not path.exists() and params["path"] == "~/Téléchargements":
        downloads = run_command(["xdg-user-dir", "DOWNLOAD"], timeout=5).stdout.strip()
        path = Path(downloads or Path.home())
    if not path.exists():
        return fail(f"{path} introuvable")
    result = run_command(["clamscan", "-r", "-i", "--no-summary", str(path)], timeout=7200)
    if result.returncode == 0:
        return ok(f"Aucune menace détectée dans {path}.", infected=[])
    if result.returncode == 1:
        infected = [line for line in result.stdout.splitlines() if line.endswith("FOUND")]
        return ok(f"{len(infected)} fichier(s) infecté(s) dans {path} !", infected=infected)
    return fail(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "analyse impossible")


def _password_strength(params: dict, _verbose: bool) -> dict:
    password = params["password"]
    pools = [(r"[a-z]", 26), (r"[A-Z]", 26), (r"[0-9]", 10), (r"[^a-zA-Z0-9]", 33)]
    pool = sum(size for pattern, size in pools if re.search(pattern, password))
    entropy = len(password) * math.log2(pool) if pool else 0
    if re.search(r"(.)\1{2,}", password) or password.lower() in {"azerty", "password", "motdepasse", "123456"}:
        entropy = min(entropy, 20)
    level = ("très faible" if entropy < 28 else "faible" if entropy < 36 else "moyen" if entropy < 60
             else "fort" if entropy < 80 else "très fort")
    return ok(f"Mot de passe {level} (≈{entropy:.0f} bits). Analyse locale : rien n'est envoyé.",
              entropy_bits=round(entropy), level=level)


def _security_updates(_params: dict, _verbose: bool) -> dict:
    result = run_command(["apt", "list", "--upgradable"], timeout=60)
    packages = [line.split("/", 1)[0] for line in result.stdout.splitlines() if "-security" in line]
    if not packages:
        return ok("Aucune mise à jour de sécurité en attente.", packages=[])
    return ok(f"{len(packages)} mise(s) à jour de sécurité en attente.", packages=packages)


def _shred(params: dict, _verbose: bool) -> dict:
    path = Path(os.path.expanduser(params["path"])).resolve()
    home = Path.home().resolve()
    if home not in path.parents or not path.is_file() or path.is_symlink():
        return fail("seuls vos propres fichiers (dans votre dossier personnel) peuvent être détruits")
    result = run_command(["shred", "-u", "-z", "-n", "3", str(path)], timeout=1800)
    if result.returncode != 0:
        return fail(result.stderr.strip() or "destruction impossible")
    return ok(f"{path.name} détruit de façon irréversible. Sur SSD, l'effacement physique n'est pas garanti.")


TOOLS = [
    command_tool("anonsurf_start", C, "Active le mode anonyme : tout le trafic passe par Tor, coupe-circuit anti-fuite.",
                 ["djerba-anonsurf", "start"], requires_root=True, timeout=300,
                 keywords=("anonyme", "tor", "anonsurf", "vie privée", "cacher ip"), done="Mode anonyme actif."),
    command_tool("anonsurf_stop", C, "Désactive le mode anonyme et rétablit la connexion directe.",
                 ["djerba-anonsurf", "stop"], requires_root=True, timeout=120,
                 keywords=("anonyme", "tor", "désactiver"), done="Connexion directe rétablie."),
    function_tool("anonsurf_status", C, "État du mode anonyme (Tor, coupe-circuit).", _anonsurf_status,
                  keywords=("anonyme", "tor", "état"), binaries=("djerba-anonsurf",)),
    command_tool("anonsurf_new_identity", C, "Nouvelle identité Tor (nouveaux circuits, nouvelle IP).",
                 ["djerba-anonsurf", "change"], requires_root=True, keywords=("nouvelle identité", "changer ip", "tor"),
                 done="Nouvelle identité Tor."),
    command_tool("public_ip", C, "Adresse IP publique et passage par Tor.", ["djerba-anonsurf", "myip", "--json"],
                 keywords=("mon ip", "adresse ip", "ip publique"), timeout=60, done="Adresse IP publique."),
    command_tool("dns_status", C, "Serveurs DNS utilisés par interface (systemd-resolved).",
                 ["resolvectl", "status", "--no-pager"], keywords=("dns", "résolution", "serveur dns"),
                 done="Configuration DNS."),
    command_tool("dns_flush", C, "Vide le cache DNS.", ["resolvectl", "flush-caches"], requires_root=True,
                 keywords=("vider dns", "cache dns", "flush"), done="Cache DNS vidé."),
    command_tool("dns_query", C, "Résout un nom de domaine (diagnostic DNS).", ["resolvectl", "query", "{name}"],
                 parameters={"name": {"type": "string", "description": "nom de domaine",
                                      "pattern": r"^[A-Za-z0-9.-]{1,253}$"}},
                 required=("name",), keywords=("résoudre", "dns", "domaine", "site inaccessible"),
                 done="Résolution de {name}."),
    command_tool("nft_ruleset", C, "Règles nftables actives (pare-feu, coupe-circuit du mode anonyme).",
                 ["nft", "list", "ruleset"], requires_root=True, keywords=("nftables", "règles", "pare-feu"),
                 done="Règles nftables."),
    command_tool("ufw_status", C, "État et règles du pare-feu UFW.", ["ufw", "status", "verbose"],
                 requires_root=True, keywords=("pare-feu", "firewall", "ufw"), done="État du pare-feu."),
    command_tool("ufw_enable", C, "Active le pare-feu (entrées refusées par défaut).", ["ufw", "--force", "enable"],
                 requires_root=True, keywords=("activer pare-feu",), done="Pare-feu actif."),
    command_tool("ufw_disable", C, "Désactive le pare-feu (déconseillé).", ["ufw", "disable"], requires_root=True,
                 dangerous=True, keywords=("désactiver pare-feu",), done="Pare-feu désactivé."),
    command_tool("ufw_allow_port", C, "Autorise les connexions entrantes sur un port (ex. serveur de jeu).",
                 ["ufw", "allow", "{port}/{protocol}"],
                 parameters={"port": {"type": "integer", "description": "port", "minimum": 1, "maximum": 65535},
                             "protocol": {"type": "string", "description": "tcp ou udp", "enum": ["tcp", "udp"],
                                          "default": "tcp"}},
                 required=("port",), requires_root=True, dangerous=True,
                 keywords=("ouvrir port", "autoriser port", "serveur"), done="Port {port}/{protocol} ouvert."),
    command_tool("ufw_deny_port", C, "Refuse les connexions entrantes sur un port.",
                 ["ufw", "deny", "{port}/{protocol}"],
                 parameters={"port": {"type": "integer", "description": "port", "minimum": 1, "maximum": 65535},
                             "protocol": {"type": "string", "description": "tcp ou udp", "enum": ["tcp", "udp"],
                                          "default": "tcp"}},
                 required=("port",), requires_root=True, keywords=("fermer port", "bloquer port"),
                 done="Port {port}/{protocol} fermé."),
    command_tool("ufw_delete_rule", C, "Supprime une règle du pare-feu par son numéro (voir ufw_status numbered).",
                 ["ufw", "--force", "delete", "{number}"],
                 parameters={"number": {"type": "integer", "description": "numéro", "minimum": 1, "maximum": 999}},
                 required=("number",), requires_root=True, dangerous=True, keywords=("supprimer règle",),
                 done="Règle {number} supprimée."),
    function_tool("antivirus_scan", C, "Analyse antivirus ClamAV d'un dossier ou d'une clé USB.", _scan,
                  parameters={"path": {"type": "string", "description": "chemin", "default": "~/Téléchargements",
                                       "pattern": HOME_PATH}},
                  keywords=("antivirus", "virus", "analyser", "clamav", "malware", "clé usb"), binaries=("clamscan",)),
    command_tool("antivirus_update", C, "Met à jour les signatures antivirus maintenant.",
                 ["systemctl", "restart", "clamav-freshclam.service"], requires_root=True,
                 keywords=("signatures", "freshclam", "mettre à jour antivirus"), done="Mise à jour lancée."),
    command_tool("antivirus_version", C, "Version de ClamAV et date des signatures.", ["clamscan", "--version"],
                 keywords=("antivirus", "signatures", "version"), done="Version de l'antivirus."),
    command_tool("apparmor_status", C, "Profils AppArmor chargés et en mode strict.", ["aa-status"],
                 requires_root=True, keywords=("apparmor", "confinement", "profils"), done="État d'AppArmor."),
    command_tool("listening_ports", C, "Ports ouverts en écoute et programmes associés.",
                 ["ss", "-tulpnH"], requires_root=True, keywords=("ports ouverts", "écoute", "netstat", "ss"),
                 done="Ports en écoute."),
    command_tool("active_connections", C, "Connexions réseau établies.", ["ss", "-tunpH", "state", "established"],
                 keywords=("connexions", "réseau", "qui se connecte"), done="Connexions établies."),
    command_tool("auth_failures", C, "Échecs d'authentification récents (sudo, polkit, connexion).",
                 ["journalctl", "-b", "--no-pager", "-p", "warning", "SYSLOG_FACILITY=10", "-n", "50"],
                 requires_root=True, keywords=("intrusion", "mot de passe erroné", "authentification"),
                 done="Échecs d'authentification."),
    command_tool("admin_users", C, "Utilisateurs administrateurs (groupe sudo).", ["getent", "group", "sudo"],
                 keywords=("administrateurs", "sudo", "root"), done="Membres du groupe sudo."),
    command_tool("suid_binaries", C, "Programmes SUID/SGID de /usr (audit de sécurité).",
                 ["find", "/usr", "-xdev", "-perm", "/6000", "-type", "f"], requires_root=True, timeout=300,
                 keywords=("suid", "audit", "privilèges"), done="Programmes à privilèges."),
    function_tool("security_updates", C, "Mises à jour de sécurité Debian en attente.", _security_updates,
                  keywords=("sécurité", "failles", "correctifs", "cve"), binaries=("apt",)),
    command_tool("mac_randomize", C, "Rend aléatoire l'adresse MAC d'une interface (coupe la connexion).",
                 ["macchanger", "-r", "{interface}"],
                 parameters={"interface": {"type": "string", "description": "interface (ex. wlan0)", "pattern": IFACE}},
                 required=("interface",), requires_root=True, dangerous=True,
                 keywords=("adresse mac", "macchanger", "pistage"), done="Adresse MAC de {interface} changée."),
    command_tool("mac_restore", C, "Restaure l'adresse MAC d'origine d'une interface.",
                 ["macchanger", "-p", "{interface}"],
                 parameters={"interface": {"type": "string", "description": "interface", "pattern": IFACE}},
                 required=("interface",), requires_root=True, dangerous=True, keywords=("adresse mac",),
                 done="Adresse MAC d'origine restaurée."),
    function_tool("password_strength", C, "Évalue la robustesse d'un mot de passe, localement.", _password_strength,
                  parameters={"password": {"type": "string", "description": "mot de passe", "allow_dash": True}},
                  required=("password",), keywords=("mot de passe", "robustesse", "password")),
    function_tool("secure_delete", C, "Détruit un de vos fichiers de façon irréversible (shred).", _shred,
                  parameters={"path": {"type": "string", "description": "fichier", "pattern": HOME_PATH}},
                  required=("path",), dangerous=True, keywords=("effacer définitivement", "shred", "détruire"),
                  binaries=("shred",)),
    command_tool("unattended_upgrades_log", C, "Dernières mises à jour de sécurité automatiques.",
                 ["tail", "-n", "60", "/var/log/unattended-upgrades/unattended-upgrades.log"], requires_root=True,
                 keywords=("mises à jour automatiques", "unattended"), done="Journal des mises à jour automatiques."),
]
