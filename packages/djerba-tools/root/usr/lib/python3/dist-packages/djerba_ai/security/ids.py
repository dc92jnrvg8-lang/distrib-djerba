# SPDX-License-Identifier: GPL-3.0-or-later
"""Détection d'intrusion et défense active (service root djerba-ai-ids).

Le journal système est suivi en continu (``journalctl -f -o json``) ; des
règles comportementales à fenêtre glissante repèrent les anomalies :

==============================  =========================================  ===========================
Règle                           Seuil                                      Réponse
==============================  =========================================  ===========================
force brute SSH                 5 échecs d'une même adresse en 5 min       blocage de l'adresse
balayage de ports               15 ports distincts refusés par le          blocage de l'adresse
                                pare-feu, d'une même adresse, en 60 s
force brute locale (sudo,       5 échecs d'authentification en 5 min       alerte
polkit, connexion)
confinement violé               refus AppArmor d'un profil djerba-*        alerte (code généré suspect)
==============================  =========================================  ===========================

Blocage ciblé : l'adresse entre dans un ensemble nftables à durée de vie
(table ``inet djerba_ids``, 60 min par défaut) ; jamais la boucle locale ni
les adresses de lien local. Chaque décision est inscrite au registre d'audit
immuable ; l'état (alertes, adresses bloquées) est publié pour le tableau de
bord dans /run/djerba-ai/ids.json, et une adresse se débloque depuis celui-ci.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

STATUS = Path(os.environ.get("DJERBA_IDS_STATUS", "/run/djerba-ai/ids.json"))
TABLE = "djerba_ids"
RULESET = f"""table inet {TABLE} {{
    set blocked4 {{ type ipv4_addr; flags timeout; }}
    set blocked6 {{ type ipv6_addr; flags timeout; }}
    chain input {{
        type filter hook input priority filter - 5; policy accept;
        ip saddr @blocked4 counter drop
        ip6 saddr @blocked6 counter drop
    }}
}}
"""

SSH_FAILURE = re.compile(r"(?:Failed password|Invalid user|authentication failure|Connection closed by authenticating"
                         r" user).*? from (?:invalid user \S+ )?(?P<ip>[0-9a-fA-F.:]+)", re.IGNORECASE)
SSH_FAILURE_ALT = re.compile(r"(?:Failed password|Invalid user).* (?P<ip>\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]{3,})"
                             r" port \d+")
FIREWALL_BLOCK = re.compile(r"\[UFW BLOCK\].*?SRC=(?P<ip>[0-9a-fA-F.:]+).*?DPT=(?P<port>\d+)")
LOCAL_AUTH_FAILURE = re.compile(r"(?:pam_unix\((?:sudo|polkit-1|login|sddm|gdm-password):auth\): authentication "
                                r"failure|incorrect password attempts|Authentication failure)", re.IGNORECASE)
APPARMOR_DENIED = re.compile(r'apparmor="DENIED"')
APPARMOR_PROFILE = re.compile(r'profile="(?P<profile>djerba-[^"]+)"')
APPARMOR_OPERATION = re.compile(r'operation="(?P<op>[^"]+)"')


@dataclass
class Decision:
    kind: str           # « blocage » ou « alerte »
    rule: str
    target: str
    message: str
    time: float = field(default_factory=time.time)


class Detector:
    """Règles à fenêtre glissante (sans effet de bord : testable)."""

    def __init__(self, ssh_threshold: int = 5, ssh_window: float = 300, scan_ports: int = 15,
                 scan_window: float = 60, auth_threshold: int = 5, auth_window: float = 300) -> None:
        self.ssh_threshold, self.ssh_window = ssh_threshold, ssh_window
        self.scan_ports, self.scan_window = scan_ports, scan_window
        self.auth_threshold, self.auth_window = auth_threshold, auth_window
        self.ssh: dict[str, deque] = defaultdict(deque)
        self.scans: dict[str, deque] = defaultdict(deque)
        self.auth: deque = deque()
        self.flagged: dict[str, float] = {}

    @staticmethod
    def blockable(ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return not (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast)

    def _once(self, key: str, now: float, cooldown: float = 600) -> bool:
        if key in self.flagged and now - self.flagged[key] < cooldown:
            return False
        self.flagged[key] = now
        return True

    def feed(self, message: str, now: float | None = None, identifier: str = "") -> list[Decision]:
        now = time.time() if now is None else now
        decisions: list[Decision] = []
        match = SSH_FAILURE.search(message) or SSH_FAILURE_ALT.search(message)
        if match and ("ssh" in identifier or "sshd" in message or "ssh" in message.lower()):
            ip = match.group("ip").strip(".")
            window = self.ssh[ip]
            window.append(now)
            while window and now - window[0] > self.ssh_window:
                window.popleft()
            if len(window) >= self.ssh_threshold and self.blockable(ip) and self._once(f"ssh:{ip}", now):
                decisions.append(Decision("blocage", "force brute SSH", ip,
                                          f"{len(window)} échecs de connexion SSH en {int(self.ssh_window / 60)} min"))
        match = FIREWALL_BLOCK.search(message)
        if match:
            ip, port = match.group("ip"), match.group("port")
            window = self.scans[ip]
            window.append((now, port))
            while window and now - window[0][0] > self.scan_window:
                window.popleft()
            ports = {item[1] for item in window}
            if len(ports) >= self.scan_ports and self.blockable(ip) and self._once(f"scan:{ip}", now):
                decisions.append(Decision("blocage", "balayage de ports", ip,
                                          f"{len(ports)} ports sondés en {int(self.scan_window)} s"))
        if LOCAL_AUTH_FAILURE.search(message):
            self.auth.append(now)
            while self.auth and now - self.auth[0] > self.auth_window:
                self.auth.popleft()
            if len(self.auth) >= self.auth_threshold and self._once("auth", now):
                decisions.append(Decision("alerte", "force brute locale", "session",
                                          f"{len(self.auth)} échecs d'authentification (sudo, polkit, connexion) "
                                          f"en {int(self.auth_window / 60)} min"))
        profile = APPARMOR_PROFILE.search(message) if APPARMOR_DENIED.search(message) else None
        if profile:
            operation = APPARMOR_OPERATION.search(message)
            op = operation.group("op") if operation else "?"
            if self._once(f"aa:{profile.group('profile')}:{op}", now, cooldown=120):
                decisions.append(Decision("alerte", "confinement violé", profile.group("profile"),
                                          f"opération « {op} » refusée par AppArmor à du code généré"))
        return decisions


# ---------------------------------------------------------------- nftables
def nft(*args: str, script: str | None = None) -> subprocess.CompletedProcess:
    argv = ["nft", *args] if script is None else ["nft", "-f", "-"]
    return subprocess.run(argv, input=script, capture_output=True, text=True, timeout=15)


def ensure_table() -> bool:
    if shutil.which("nft") is None:
        return False
    if nft("list", "table", "inet", TABLE).returncode == 0:
        return True
    return nft(script=RULESET).returncode == 0


def block(ip: str, minutes: int = 60) -> tuple[bool, str]:
    if not Detector.blockable(ip):
        return False, "adresse non bloquable (boucle locale, lien local…)"
    if not ensure_table():
        return False, "nftables indisponible"
    family = "blocked6" if ":" in ip else "blocked4"
    result = nft("add", "element", "inet", TABLE, family, "{", f"{ip} timeout {int(minutes)}m", "}")
    return result.returncode == 0, result.stderr.strip()


def unblock(ip: str) -> tuple[bool, str]:
    ipaddress.ip_address(ip)  # ValueError si l'adresse est invalide
    family = "blocked6" if ":" in ip else "blocked4"
    result = nft("delete", "element", "inet", TABLE, family, "{", ip, "}")
    return result.returncode == 0, result.stderr.strip()


def blocked() -> list[dict]:
    result = nft("-j", "list", "table", "inet", TABLE)
    if result.returncode != 0:
        return []
    entries = []
    try:
        for item in json.loads(result.stdout).get("nftables", []):
            data = item.get("set")
            if not data or data.get("name") not in ("blocked4", "blocked6"):
                continue
            for element in data.get("elem", []):
                value = element.get("elem", element) if isinstance(element, dict) else element
                address = value.get("val", value) if isinstance(value, dict) else value
                expires = value.get("expires", 0) if isinstance(value, dict) else 0
                entries.append({"ip": str(address), "expires_s": int(expires)})
    except (ValueError, AttributeError):
        return []
    return entries


# ---------------------------------------------------------------- Service
def publish(alerts: deque, state: str) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS.with_name(".ids.json.tmp")
    temporary.write_text(json.dumps({"time": time.time(), "state": state, "alerts": list(alerts)[-50:],
                                     "blocked": blocked()}, ensure_ascii=False))
    os.chmod(temporary, 0o644)
    os.replace(temporary, STATUS)


def status() -> dict:
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {"state": "inactif", "alerts": [], "blocked": blocked()}
    data["blocked"] = blocked()
    return data


def main() -> int:
    from djerba_ai.core import audit
    from djerba_ai.core.config import Store
    if os.geteuid() != 0:
        print("djerba-ai-ids : doit s'exécuter en root", file=sys.stderr)
        return 1
    values = Store().load()
    if not values.get("security.ids", True):
        print("djerba-ai-ids : désactivé (security.ids = false)", flush=True)
        return 0
    minutes = int(values.get("security.ids_block_minutes", 60))
    detector = Detector()
    alerts: deque = deque(maxlen=200)
    state = "actif" if ensure_table() else "actif (nftables indisponible : alertes seulement)"
    publish(alerts, state)
    process = subprocess.Popen(["journalctl", "-f", "-n", "0", "-o", "json"], stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    last_publish = time.monotonic()
    for line in process.stdout:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("MESSAGE")
        if not isinstance(message, str):
            continue
        for decision in detector.feed(message, identifier=str(entry.get("SYSLOG_IDENTIFIER", ""))):
            done, detail = (block(decision.target, minutes) if decision.kind == "blocage" else (True, ""))
            record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": decision.kind, "rule": decision.rule,
                      "target": decision.target, "message": decision.message, "applied": done}
            alerts.append(record)
            audit.record("ids", uid=0, pid=os.getpid(), method="security.ids", tool=decision.rule,
                         params={"cible": decision.target, "action": decision.kind}, success=done,
                         message=decision.message + (f" — {detail}" if detail else ""))
            print(f"djerba-ai-ids : {decision.kind} {decision.target} ({decision.rule}) : {decision.message}",
                  flush=True)
            publish(alerts, state)
        if time.monotonic() - last_publish > 60:
            publish(alerts, state)
            last_publish = time.monotonic()
    return 0
