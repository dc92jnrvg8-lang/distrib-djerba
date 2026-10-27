# SPDX-License-Identifier: GPL-3.0-or-later
"""Profils AppArmor générés pour chaque capacité ou application créée par l'IA.

Chaque greffon (``djerba-plugin-<nom>``) et chaque application du Studio
(``djerba-app-<nom>``) reçoit un profil strict, écrit dans
/etc/apparmor.d/djerba-generated/ et chargé par le démon root :

* lecture seule du système (/usr), écriture limitée à son propre dossier ;
* secrets refusés explicitement (~/.ssh, ~/.gnupg, trousseaux, /etc/shadow,
  configuration et clés de Djerba, socket du démon root) ;
* aucune capacité noyau, ni montage, ni ptrace ;
* **aucun réseau** (inet, inet6, packet, netlink) tant que l'utilisateur ne
  l'a pas autorisé explicitement au tableau de bord (confiance zéro).

Le profil s'ajoute au bac à sable (seccomp, espaces de noms, cgroups) ; sur
un système où AppArmor n'est pas actif (session live), le confinement repose
sur ces seuls mécanismes.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

PROFILES_DIR = Path(os.environ.get("DJERBA_APPARMOR_DIR", "/etc/apparmor.d/djerba-generated"))
SECURITYFS = Path("/sys/kernel/security/apparmor")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

DENIED_SECRETS = """  deny @{HOME}/.ssh/** rwklx,
  deny @{HOME}/.gnupg/** rwklx,
  deny @{HOME}/.local/share/keyrings/** rwklx,
  deny @{HOME}/.local/share/djerba/memory.* rwklx,
  deny @{HOME}/.mozilla/** rwklx,
  deny @{HOME}/.config/chromium/** rwklx,
  deny /etc/shadow* rwklx,
  deny /etc/gshadow* rwklx,
  deny /etc/djerba/** rwklx,
  deny /var/lib/djerba-ai/** rwklx,
  deny /var/log/djerba/** rwklx,
  deny /run/djerba-ai/** rwklx,
  deny @{PROC}/sys/** w,
  deny @{PROC}/*/mem rw,"""

NO_NETWORK = """  deny network inet,
  deny network inet6,
  deny network packet,
  deny network netlink raw,"""

WITH_NETWORK = """  include <abstractions/nameservice>
  include <abstractions/ssl_certs>
  network inet stream,
  network inet6 stream,
  network inet dgram,
  network inet6 dgram,"""


def profile_name(kind: str, name: str) -> str:
    if kind not in ("plugin", "app") or not NAME.match(name):
        raise ValueError("profil AppArmor : type ou nom invalide")
    return f"djerba-{kind}-{name}"


def render(kind: str, name: str, workdir: str, network: bool = False) -> str:
    """Texte du profil (syntaxe AppArmor 4, abi 4.0)."""
    profile = profile_name(kind, name)
    if not workdir.startswith("/") or any(char in workdir for char in ' ,"\n{}'):
        raise ValueError("dossier de travail invalide pour un profil AppArmor")
    graphical = """  include <abstractions/fonts>
  include <abstractions/X>
  include <abstractions/wayland>
  include <abstractions/dri-enumerate>
  include <abstractions/mesa>
  include <abstractions/freedesktop.org>
  include <abstractions/dbus-session-strict>
  owner @{HOME}/.cache/** rwk,
  owner /run/user/*/** r,
""" if kind == "app" else ""
    return f"""# Profil généré par Djerba OS — {kind} « {name} » (réseau : {'autorisé' if network else 'refusé'}).
abi <abi/4.0>,
include <tunables/global>

profile {profile} flags=(attach_disconnected) {{
  include <abstractions/base>
  include <abstractions/python>
{graphical}  /usr/** r,
  /usr/bin/** ix,
  /usr/lib/** mr,
  /etc/** r,
  @{{PROC}}/@{{pid}}/** r,
  /tmp/** rwk,
  owner {workdir.rstrip('/')}/ r,
  owner {workdir.rstrip('/')}/** rwk,
  network unix,
{WITH_NETWORK if network else NO_NETWORK}
{DENIED_SECRETS}
  deny capability,
  deny mount,
  deny umount,
  deny pivot_root,
  deny ptrace,
}}
"""


def enabled() -> bool:
    """AppArmor actif dans le noyau (absent d'une session live Debian)."""
    try:
        return (SECURITYFS / "profiles").exists() and Path("/sys/module/apparmor/parameters/enabled").read_text() \
            .strip().upper().startswith("Y")
    except OSError:
        return False


def loaded(profile: str) -> bool:
    try:
        return any(line.split(" (")[0] == profile for line in (SECURITYFS / "profiles").read_text().splitlines())
    except OSError:
        return False


def check_syntax(text: str) -> tuple[bool, str]:
    """Analyse sans chargement (apparmor_parser -QK) ; (True, '') si l'outil est absent."""
    if shutil.which("apparmor_parser") is None:
        return True, "apparmor_parser absent : syntaxe non vérifiée"
    result = subprocess.run(["apparmor_parser", "-QK", "--skip-cache", "/dev/stdin"], input=text, text=True,
                            capture_output=True, timeout=30)
    return result.returncode == 0, result.stderr.strip()[-600:]


def install(kind: str, name: str, workdir: str, network: bool = False) -> dict:
    """Écrit et charge le profil (root) ; sans AppArmor actif, l'écrit seulement."""
    profile = profile_name(kind, name)
    text = render(kind, name, workdir, network)
    valid, detail = check_syntax(text)
    if not valid:
        return {"success": False, "profile": profile, "message": f"profil invalide : {detail}"}
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = PROFILES_DIR / profile
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o644)
    if not enabled():
        return {"success": True, "profile": profile, "loaded": False, "path": str(path),
                "message": "AppArmor inactif (session live ?) : profil écrit, confinement par le bac à sable"}
    result = subprocess.run(["apparmor_parser", "-r", "-W", str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return {"success": False, "profile": profile, "message": result.stderr.strip()[-600:]}
    return {"success": True, "profile": profile, "loaded": True, "path": str(path),
            "message": f"Profil AppArmor {profile} chargé (réseau {'autorisé' if network else 'refusé'})"}
