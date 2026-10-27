# SPDX-License-Identifier: GPL-3.0-or-later
"""Réseau en confiance zéro pour le code généré par l'IA.

Aucune application du Studio ni capacité générée n'accède à Internet par
défaut. L'accès se donne au cas par cas depuis le tableau de bord (saisie de
« CONFIRMER », puis mot de passe administrateur : la liste
``security.network_grants`` vit dans /etc/djerba/config.toml, écrite par le
démon root) et se retire à tout moment.

Application, par couches :

1. **seccomp-bpf** (toujours, même sans AppArmor) : sans autorisation, le
   processus est lancé par core/seccomp_launch.py, qui refuse toute socket
   autre que UNIX ;
2. **AppArmor** (système installé) : profil généré ``djerba-app-*`` /
   ``djerba-plugin-*`` avec ou sans règles réseau (security/apparmor.py),
   appliqué par ``aa-exec`` lorsqu'il est chargé.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from djerba_ai.core.sandbox import LAUNCHER

KINDS = ("app", "plugin")


def grant_key(kind: str, name: str) -> str:
    if kind not in KINDS:
        raise ValueError("type inconnu")
    return f"{kind}:{name}"


def granted(config, kind: str, name: str) -> bool:
    return grant_key(kind, name) in (config.get("security.network_grants") or [])


def wrap(argv: list[str], config, kind: str, name: str) -> list[str]:
    """Commande confinée : AppArmor si le profil est chargé, seccomp sans réseau sauf autorisation."""
    from djerba_ai.security import apparmor
    command = list(argv)
    if not granted(config, kind, name):
        command = [sys.executable, "-I", str(LAUNCHER), "--", *command]
    profile = apparmor.profile_name(kind, name)
    if shutil.which("aa-exec") and apparmor.loaded(profile):
        command = ["aa-exec", "-p", profile, "--", *command]
    return command


def package_root() -> str:
    return str(Path(__file__).resolve().parent.parent.parent)


def isolated_environment(extra_paths: list[str] | None = None) -> dict[str, str]:
    """Environnement minimal d'un greffon isolé (aucun secret de la session transmis) ;
    ses dépendances viennent du venv interne (PEP 668)."""
    keep = ("HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")
    environment = {key: os.environ[key] for key in keep if key in os.environ}
    environment.update(PATH="/usr/local/bin:/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join([package_root(), *(extra_paths or [])]))
    return environment


def request_profile(kind: str, name: str, config=None) -> str:
    """Demande au démon root de générer et charger le profil AppArmor (sans effet bloquant)."""
    from djerba_ai.core import ipc
    from djerba_ai.core.config import Config
    config = config or Config()
    try:
        result = ipc.call("security.apparmor_load", {"kind": kind, "name": name,
                                                     "network": granted(config, kind, name)}, timeout=60)
    except ipc.RPCError as exc:
        return f"profil AppArmor non chargé : {exc}"
    return str(result.get("message", ""))
