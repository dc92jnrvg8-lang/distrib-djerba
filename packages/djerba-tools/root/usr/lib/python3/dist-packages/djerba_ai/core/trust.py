# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérification de l'appelant du démon root (au-delà de SO_PEERCRED).

SO_PEERCRED donne, au niveau du noyau, le pid, l'uid et le gid du processus
connecté. Pour toute méthode sensible, le démon exige en plus que ce
processus soit un point d'entrée **signé** de Djerba :

1. exécutable : l'interpréteur Python du système (``/usr/bin/python3.X``,
   appartenant à root) — pas une copie, pas un autre interpréteur ;
2. mode isolé ``-I`` : ni PYTHONPATH, ni site-packages de l'utilisateur, ni
   dossier courant ne peuvent substituer un module ;
3. script : un point d'entrée connu (``/usr/bin/djerba-ai``), appartenant à
   root, non modifiable par un autre utilisateur, dont l'empreinte SHA-256
   figure dans le manifeste signé du paquet
   (/usr/share/djerba-ai/trusted-callers.sha256, généré à la construction) ;
4. environnement : aucune injection de bibliothèque (LD_PRELOAD, LD_AUDIT,
   LD_LIBRARY_PATH).

Un appelant root (uid 0) est déjà maître du système : il est accepté. Les
méthodes de pure consultation (ping, métriques, configuration publique)
restent ouvertes. Cette vérification est une défense en profondeur :
l'autorité finale reste polkit (mot de passe administrateur selon l'action).
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

MANIFEST = Path(os.environ.get("DJERBA_TRUST_MANIFEST", "/usr/share/djerba-ai/trusted-callers.sha256"))
INTERPRETER = re.compile(r"^/usr/bin/python3(\.\d+)?$")
FORBIDDEN_ENV = (b"LD_PRELOAD=", b"LD_AUDIT=", b"LD_LIBRARY_PATH=")
#: méthodes ouvertes à tout processus (aucun effet, aucune donnée sensible)
OPEN_METHODS = {"sys.ping", "sys.metrics", "config.get"}


class Untrusted(Exception):
    pass


def manifest(path: Path = MANIFEST) -> dict[str, str]:
    entries = {}
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
                entries[parts[1].lstrip("*")] = parts[0]
    except OSError:
        pass
    return entries


def _owned_by_root_and_locked(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return False
    return info.st_uid == 0 and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


def _script(argv: list[str]) -> tuple[str, bool]:
    """(script exécuté, option -I présente) d'une ligne de commande Python."""
    isolated = False
    for index, arg in enumerate(argv[1:], 1):
        if arg in ("-c", "-m"):
            return "", isolated
        if arg.startswith("-") and arg != "-":
            isolated = isolated or (not arg.startswith("--") and "I" in arg[1:])
            continue
        return arg, isolated
    return "", isolated


def check(pid: int, uid: int, proc: Path = Path("/proc"), trusted: dict[str, str] | None = None) -> None:
    """Lève Untrusted si le processus n'est pas un point d'entrée de confiance."""
    if uid == 0:
        return
    base = proc / str(pid)
    try:
        exe = os.path.realpath(base / "exe")
        argv = [part.decode(errors="replace") for part in (base / "cmdline").read_bytes().split(b"\0") if part]
        environ = (base / "environ").read_bytes().split(b"\0")
    except OSError as exc:
        raise Untrusted("processus appelant illisible") from exc
    if not INTERPRETER.match(exe) or not _owned_by_root_and_locked(Path(exe)):
        raise Untrusted(f"exécutable non autorisé : {exe}")
    script, isolated = _script(argv)
    if not isolated:
        raise Untrusted("interpréteur sans le mode isolé -I")
    trusted = manifest() if trusted is None else trusted
    expected = trusted.get(script)
    if expected is None:
        raise Untrusted(f"script non signé : {script or '(code en ligne)'}")
    path = Path(script)
    if not _owned_by_root_and_locked(path) or not _owned_by_root_and_locked(path.parent):
        raise Untrusted(f"{script} modifiable par un autre utilisateur que root")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise Untrusted(f"{script} ne correspond pas à l'empreinte du paquet")
    if any(item.startswith(FORBIDDEN_ENV) for item in environ):
        raise Untrusted("injection de bibliothèque dans l'environnement (LD_*)")


def verify(pid: int, uid: int) -> tuple[bool, str]:
    try:
        check(pid, uid)
    except Untrusted as exc:
        return False, str(exc)
    return True, ""
