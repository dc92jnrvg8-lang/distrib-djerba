#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba OS — contrôles exécutés dans la session live, via la console série.

Usage : live-session-checks.py SOCKET JOURNAL [--network]

Se connecte au getty série (djerba / live), puis exécute chaque contrôle en
shell et lit son code de retour grâce à un marqueur unique. Sans --network,
la VM n'a pas d'accès Internet : seuls les contrôles hors ligne sont menés
(configuration DNS, démarrage de Tor sous AppArmor et retour arrière du mode
anonyme, mode discret, mode jeu, catalogue). Avec --network : résolution de
noms, HTTPS, Firefox et mode anonyme complet à travers Tor.
"""

from __future__ import annotations

import base64
import re
import socket
import sys
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r")

# Environnement de la session graphique de l'utilisateur live (uid 1000).
SESSION = ("export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 "
           "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus")

# Capacité d'exemple et ses tests Pytest, vérifiés par le bac à sable du démon root
# (runtime hybride systemd : namespaces, seccomp-bpf, cgroups v2 ; 15 s par test ; Git).
PLUGIN = base64.b64encode(b"""from djerba_ai.tools.base import BaseTool, ok


class Inverse(BaseTool):
    name = "texte_inverse"
    category = "devtools"
    description = "Inverse un texte."
    parameters = {"texte": {"type": "string", "description": "texte"}}
    required = ("texte",)

    def execute(self, params, verbose=False):
        return ok(params["texte"][::-1])
""").decode()
PLUGIN_TESTS = base64.b64encode(b"""import socket

from texte_inverse import Inverse


def test_inverse():
    assert Inverse().run({"texte": "abc"})["message"] == "cba"


def test_reseau_coupe():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2)
    except OSError:
        return
    raise AssertionError("reseau accessible")
""").decode()
RPC = "python3 -c 'import base64, sys; from djerba_ai.core import ipc; "

OFFLINE_CHECKS = [
    ("DNS : /etc/resolv.conf pointe vers le stub de systemd-resolved",
     "test \"$(readlink /etc/resolv.conf)\" = ../run/systemd/resolve/stub-resolv.conf", 30),
    ("DNS : module NSS resolve actif", "grep -qE '^hosts:.*resolve' /etc/nsswitch.conf", 30),
    ("NetworkManager et systemd-resolved actifs",
     "systemctl is-active --quiet NetworkManager systemd-resolved", 30),
    ("Résolution locale via resolved (_gateway / localhost)", "getent ahosts localhost >/dev/null", 30),
    ("Mode anonyme inactif au démarrage",
     "djerba-anonsurf status --json | grep -q '\"active\": false'", 30),
    ("Mode jeu : état JSON", "djerba-gamemode status --json | python3 -m json.tool >/dev/null", 60),
    ("Mode jeu : bascule et restauration",
     f"{SESSION}; djerba-gamemode on && djerba-gamemode status --json | grep -q '\"active\": true' "
     "&& djerba-gamemode off && djerba-gamemode status --json | grep -q '\"active\": false'", 120),
    ("Agent IA : capacité exécutée (os_info)", "djerba-ai run os_info | grep -q '\"success\": true'", 60),
    ("Agent IA : démon root activé à la demande (sys.ping)",
     "python3 -c 'from djerba_ai.core import ipc; assert ipc.call(\"sys.ping\", {}, timeout=30)[\"version\"]'", 60),
    ("Agent IA : diagnostic", "djerba-ai doctor | grep -q 'Démon root : disponible'", 90),
    ("Agent IA : démon utilisateur actif", f"{SESSION}; systemctl --user is-active --quiet djerba-ai.service", 30),
    ("Agent IA : Studio hors ligne (Snake)",
     "djerba-ai run app_generator '{\"instruction\": \"Crée un jeu Snake\"}' | grep -q '\"success\": true' "
     "&& test -f ~/.local/share/applications/djerba-snake.desktop", 120),
    ("Agent IA : configuration centrale lue par le démon (config.get)",
     RPC + "assert ipc.call(\"config.get\", {}, timeout=30)[\"llm.provider\"] == \"local\"'", 60),
    ("Agent IA : config.toml root 0600, vue publique 0644",
     "test \"$(stat -c '%U %a' /etc/djerba/config.toml)\" = 'root 600' "
     "&& test \"$(stat -c '%a' /etc/djerba/config.public.json)\" = 644", 30),
    ("Agent IA : réglage écrit par le démon et synchronisé (vue publique)",
     "sudo " + RPC + "ipc.call(\"config.set\", {\"key\": \"system.disk_alert_percent\", \"value\": 12})' "
     "&& grep -q '\"system.disk_alert_percent\": 12' /etc/djerba/config.public.json "
     "&& sudo grep -q 'disk_alert_percent = 12' /etc/djerba/config.toml "
     "&& sudo " + RPC + "ipc.call(\"config.set\", {\"key\": \"system.disk_alert_percent\", \"value\": 10})'", 90),
    ("Agent IA : journal d'audit JSON (/var/log/djerba/audit.log)",
     "sudo tail -n 1 /var/log/djerba/audit.log | python3 -c 'import json, sys; e = json.load(sys.stdin); "
     "assert e[\"method\"] == \"config.set\" and e[\"uid\"] == 0 and e[\"success\"]'", 30),
    ("Agent IA : capacité générée et ses tests Pytest vérifiés dans le bac à sable (15 s par test, Git)",
     "sudo " + RPC + f"r = ipc.call(\"sys.test_plugin\", {{\"name\": \"texte_inverse\", "
     f"\"content\": base64.b64decode(\"{PLUGIN}\").decode(), "
     f"\"tests\": base64.b64decode(\"{PLUGIN_TESTS}\").decode()}}, timeout=300); print(r); "
     "assert r[\"success\"] and r[\"tools\"] == [\"texte_inverse\"] and r[\"tests_passed\"] == 2, r' "
     "&& sudo git -C /var/lib/djerba-ai/sandbox log -1 --pretty=%s | grep -q texte_inverse", 180),
    ("Agent IA : application isolée (systemd-run --scope) refusée par le démon root",
     f"{SESSION}; systemd-run --user --scope --quiet --unit=djerba-app-test-$$ "
     + RPC + "ipc.call(\"sys.ping\", {}, timeout=30)' 2>&1 | grep -q 'applications isolées'", 60),
    ("Agent IA : recherche sémantique hors ligne",
     "mkdir -p /tmp/docs && echo \"Facture d'électricité de mars : 84 euros\" > /tmp/docs/facture.txt "
     "&& echo 'Recette du couscous au poisson' > /tmp/docs/recette.txt "
     "&& djerba-ai run os_index_build '{\"path\": \"/tmp/docs\"}' | grep -q '\"success\": true' "
     "&& djerba-ai run os_semantic_search '{\"query\": \"ma facture électricité\"}' "
     "| python3 -c 'import json, sys; r = json.load(sys.stdin); "
     "assert r[\"data\"][\"results\"][0][\"path\"].endswith(\"facture.txt\"), r'", 120),
    ("Agent IA : chemins vitaux refusés",
     "djerba-ai run os_path_delete '{\"path\": \"/etc\"}' | grep -q '\"success\": false'", 60),
    ("Agent IA : icône résidente et Spotlight (instance unique)",
     f"{SESSION}; for i in $(seq 90); do test -S /run/user/1000/djerba-ai/gui.sock && break; sleep 1; done; "
     "test -S /run/user/1000/djerba-ai/gui.sock && timeout 10 djerba-ai --spotlight "
     "&& timeout 10 djerba-ai --page settings", 150),
    ("Agent IA : bac à sable hybride réel (systemd : namespaces, seccomp-bpf, cgroups v2)",
     "sudo " + RPC + "r = ipc.call(\"sys.sandbox_probe\", {}, timeout=120); print(r); "
     "assert r[\"success\"] and r[\"backend\"] == \"systemd\", r'", 180),
    ("Agent IA : journal d'audit chaîné intègre",
     "sudo " + RPC + "r = ipc.call(\"audit.verify\", {}, timeout=60); print(r); "
     "assert r[\"success\"] and r[\"entries\"] > 0, r'", 60),
    ("Agent IA : appelant non signé refusé, point d'entrée signé accepté",
     RPC + "ipc.call(\"audit.tail\", {}, timeout=30)' 2>&1 | grep -q 'appelant non fiable' "
     "&& ! djerba-ai run audit_log 2>&1 | grep -q 'appelant non fiable'", 60),
    ("Agent IA : bus de contexte chiffré et horodaté",
     f"{SESSION}; for i in $(seq 30); do test -s /run/user/1000/djerba-ai/context.bin && break; sleep 1; done; "
     "! grep -q 'cpu_percent' /run/user/1000/djerba-ai/context.bin "
     "&& djerba-ai run context_now | grep -q 'Bus de contexte' "
     "|| { journalctl --user -u djerba-ai --no-pager -n 30; false; }", 60),
    ("Agent IA : télémétrie Prometheus et santé (Uptime Kuma)",
     "curl -fsS --noproxy '*' http://127.0.0.1:9464/metrics | grep -q '^djerba_up 1' "
     "&& curl -sS --noproxy '*' http://127.0.0.1:9464/health | grep -q '\"status\"'", 60),
    ("Agent IA : tâche de fond isolée (Ghost Runner)",
     f"{SESSION}; djerba-ai run ghost_run '{{\"tool\": \"os_index_build\", \"params\": "
     "\"{\\\"path\\\": \\\"/tmp/docs\\\"}\"}' | grep -q '\"success\": true' "
     "&& for i in $(seq 90); do journalctl --user --no-pager -o cat -u 'djerba-ghost-os-index-build-*' "
     "| grep -q 'Index de /tmp/docs' && break; sleep 1; done; "
     "journalctl --user --no-pager -o cat -u 'djerba-ghost-os-index-build-*' | grep -q 'Index de /tmp/docs'", 150),
    ("Store : catalogue JSON",
     "djerba-apps list --json | python3 -c 'import json,sys; assert len(json.load(sys.stdin)[\"apps\"]) >= 10'", 60),
    ("Mode discret : bascule Windows 11 puis restauration à l'identique",
     f"{SESSION}; md5sum ~/.config/kdeglobals > /tmp/before.md5; "
     "djerba-undercover on --style 11 --verbose && djerba-undercover status --json | grep -q '\"active\": true' "
     "&& grep -q '^LookAndFeelPackage=org.djerba.undercover11.desktop' ~/.config/kdeglobals "
     "&& djerba-undercover off --verbose && md5sum -c --quiet /tmp/before.md5 "
     "&& pgrep -u 1000 -x plasmashell >/dev/null", 300),
]

# Sans réseau : Tor ne peut pas se connecter, le mode anonyme doit s'annuler.
OFFLINE_ONLY = [
    ("Mode anonyme sans réseau : échec propre et retour arrière",
     "! sudo DJERBA_ANONSURF_TIMEOUT=40 djerba-anonsurf start --verbose; "
     "! sudo nft list table inet djerba_anonsurf >/dev/null 2>&1", 240),
    ("Tor démarre sous AppArmor avec la configuration Djerba",
     "sudo journalctl -b -u djerba-anonsurf-tor --no-pager | grep -q 'Bootstrapped'", 30),
    # Debian ne charge pas les profils AppArmor sur un média live (apparmor.service :
    # ConditionPathExists=!/run/live/overlay/work) ; ils le sont une fois installé.
    # Seul « label not found » (profil non chargé) est donc admis ici.
    ("Aucun refus AppArmor pour Tor (hors profil non chargé du live)",
     "d=$(sudo journalctl -b -k --no-pager | grep 'apparmor=\"DENIED\".*system_tor' "
     "| grep -v 'info=\"label not found\"' || true); printf '%s\\n' \"$d\" | tail -n 5; test -z \"$d\"", 30),
]

NETWORK_CHECKS = [
    ("Résolution DNS d'un nom Internet", "getent hosts deb.debian.org >/dev/null", 60),
    ("HTTPS (curl) vers deb.debian.org",
     "curl -fsS -o /dev/null --max-time 30 https://deb.debian.org/debian/", 90),
    ("Firefox charge une page web",
     "rm -f /tmp/ff.png; timeout 300 firefox --headless --screenshot /tmp/ff.png https://deb.debian.org/ "
     ">/tmp/ff.log 2>&1; test -s /tmp/ff.png", 360),
    ("Mode anonyme : connexion au réseau Tor", "sudo djerba-anonsurf start --verbose", 400),
    ("Mode anonyme : trafic sortant via Tor",
     "curl -fsS --max-time 60 https://check.torproject.org/api/ip | grep -q '\"IsTor\":true'", 120),
    ("Mode anonyme : DNS résolu par Tor", "getent hosts www.debian.org >/dev/null", 90),
    ("Mode anonyme : IPv6 bloqué (pas de fuite)",
     "! curl -6 -fsS --max-time 10 -o /dev/null https://www.debian.org/", 60),
    ("Mode anonyme : arrêt et connexion directe rétablie",
     "sudo djerba-anonsurf stop && curl -fsS -o /dev/null --max-time 30 https://deb.debian.org/debian/", 120),
    ("Aucun refus AppArmor pour Tor (hors profil non chargé du live)",
     "d=$(sudo journalctl -b -k --no-pager | grep 'apparmor=\"DENIED\".*system_tor' "
     "| grep -v 'info=\"label not found\"' || true); printf '%s\\n' \"$d\" | tail -n 5; test -z \"$d\"", 30),
]


class Console:
    def __init__(self, path: str, journal: str) -> None:
        self.journal = open(journal, "w", encoding="utf-8")
        for _ in range(600):
            try:
                self.sock = socket.socket(socket.AF_UNIX)
                self.sock.connect(path)
                break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("console série introuvable")
        self.sock.settimeout(1)
        self.buffer = ""
        self.before = ""

    def read_until(self, pattern: str, timeout: float) -> re.Match | None:
        deadline = time.monotonic() + timeout
        regex = re.compile(pattern)
        while time.monotonic() < deadline:
            match = regex.search(self.buffer)
            if match:
                self.before = self.buffer[:match.start()]
                self.buffer = self.buffer[match.end():]
                return match
            try:
                data = self.sock.recv(65536).decode(errors="replace")
            except socket.timeout:
                continue
            if not data:
                break
            data = ANSI.sub("", data)
            self.journal.write(data)
            self.journal.flush()
            self.buffer += data
        return None

    def send(self, text: str) -> None:
        self.sock.sendall((text + "\n").encode())


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    network = "--network" in sys.argv
    console = Console(sys.argv[1], sys.argv[2])
    if not console.read_until(r"djerba login:", 3000):
        print("[live] invite de connexion absente")
        return 1
    time.sleep(3)
    console.send("djerba")
    console.read_until(r"[Pp]assword:|[Mm]ot de passe", 60)
    console.send("live")
    if not console.read_until(r"\$ ", 120):
        print("[live] connexion refusée")
        return 1
    console.send("stty cols 250; export LANG=C.UTF-8 PS1='$ '")
    console.read_until(r"\$ ", 30)
    # La session graphique (autologin) et Plasma doivent être prêts.
    console.send("for i in $(seq 1 120); do pgrep -u 1000 -x plasmashell >/dev/null && break; sleep 5; done; "
                 "echo __READY_$((40+2))__")
    console.read_until(r"__READY_42__", 700)
    # Laisser la session se stabiliser (services de démarrage de Plasma).
    console.send("sleep 45; echo __SETTLED_$((1+1))__")
    console.read_until(r"__SETTLED_2__", 120)

    checks = OFFLINE_CHECKS + (NETWORK_CHECKS if network else OFFLINE_ONLY)
    failures = 0
    for index, (label, command, timeout) in enumerate(checks):
        console.send(f"( {command} ) >/tmp/check-{index}.log 2>&1; echo __RC_{index}=$?__")
        match = console.read_until(rf"__RC_{index}=(\d+)__", timeout)
        if match and match.group(1) == "0":
            print(f"[ OK ] {label}", flush=True)
            continue
        failures += 1
        print(f"[FAIL] {label}" + ("" if match else " (délai dépassé)"), flush=True)
        console.send(f"tail -n 25 /tmp/check-{index}.log; echo __LOG_{index}_DONE$((1))__")
        if console.read_until(rf"__LOG_{index}_DONE1__", 30):
            lines = [line for line in console.before.splitlines() if "__LOG_" not in line]
            for line in lines[-25:]:
                print(f"       {line}", flush=True)
    print(f"\n{len(checks) - failures}/{len(checks)} contrôles réussis "
          f"({'avec' if network else 'sans'} réseau)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
