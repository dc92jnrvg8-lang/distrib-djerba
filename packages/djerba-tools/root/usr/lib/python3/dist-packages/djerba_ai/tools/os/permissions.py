# SPDX-License-Identifier: GPL-3.0-or-later
"""Permissions : chmod (utilisateur ou root) et chown (root), chemins vitaux refusés."""

from __future__ import annotations

import grp
import os
import pwd
import stat
from pathlib import Path

from djerba_ai.tools.base import ToolError, function_tool, ok
from djerba_ai.tools.os.guard import PATH, check_mutable, resolve

MODE = {"type": "string", "description": "mode octal (ex. 644, 0755) ou symbolique (u+x, go-w, a+r)",
        "pattern": r"^(0?[0-7]{3,4}|([ugoa]*[-+=][rwxXst]*)(,[ugoa]*[-+=][rwxXst]*)*)$"}
P_PATH = {"type": "string", "description": "chemin du fichier ou du dossier", "pattern": PATH}
RECURSIVE = {"type": "boolean", "default": False, "description": "appliquer à tout le contenu du dossier"}
NAME = r"^[a-z_][a-z0-9_-]{0,31}\$?$"
BITS = {"r": 4, "w": 2, "x": 1}
WHO = {"u": 6, "g": 3, "o": 0}


def apply_mode(current: int, spec: str, is_dir: bool) -> int:
    if spec.isdigit():
        mode = int(spec, 8)
        if mode & 0o6000 and not stat.S_ISDIR(current):
            raise ToolError("setuid/setgid refusés par Djerba")
        return mode
    mode = stat.S_IMODE(current)
    for clause in spec.split(","):
        operator = next(char for char in clause if char in "+-=")
        who, perms = clause.split(operator, 1)
        targets = [WHO[char] for char in (who or "a").replace("a", "ugo")]
        if "s" in perms:
            raise ToolError("setuid/setgid refusés par Djerba")
        value = sum(BITS.get(char, 0) for char in perms)
        if "X" in perms and (is_dir or mode & 0o111):
            value |= 1
        for shift in targets:
            if operator == "+":
                mode |= value << shift
            elif operator == "-":
                mode &= ~(value << shift)
            else:
                mode = (mode & ~(7 << shift)) | (value << shift)
        if "t" in perms:
            mode = mode | stat.S_ISVTX if operator != "-" else mode & ~stat.S_ISVTX
    return mode


def _walk(path: Path, recursive: bool):
    yield path
    if recursive and path.is_dir() and not path.is_symlink():
        for root, dirs, files in os.walk(path):
            for name in (*dirs, *files):
                child = Path(root) / name
                if not child.is_symlink():
                    yield child


def _chmod(params: dict, _verbose: bool) -> dict:
    path = check_mutable(resolve(params["path"]))
    count = 0
    for item in _walk(path, params["recursive"]):
        info = item.lstat()
        os.chmod(item, apply_mode(info.st_mode, params["mode"], stat.S_ISDIR(info.st_mode)))
        count += 1
    final = stat.filemode(path.stat().st_mode)
    return ok(f"Droits de {path} : {final}" + (f" ({count} éléments)." if count > 1 else "."),
              path=str(path), mode=final, count=count)


def _chown(params: dict, _verbose: bool) -> dict:
    path = check_mutable(resolve(params["path"]))
    try:
        uid = pwd.getpwnam(params["user"]).pw_uid if params.get("user") else -1
        gid = grp.getgrnam(params["group"]).gr_gid if params.get("group") else -1
    except KeyError as exc:
        raise ToolError(f"utilisateur ou groupe inconnu : {exc.args[0]}") from exc
    if uid == -1 and gid == -1:
        raise ToolError("indiquez un utilisateur et/ou un groupe")
    count = 0
    for item in _walk(path, params["recursive"]):
        os.chown(item, uid, gid, follow_symlinks=False)
        count += 1
    owner = f"{params.get('user') or ''}:{params.get('group') or ''}".strip(":")
    return ok(f"{path} appartient maintenant à {owner}" + (f" ({count} éléments)." if count > 1 else "."),
              path=str(path), count=count)


C = "os"
TOOLS = [
    function_tool("os_chmod", C, "Change les droits d'un fichier ou dossier de l'utilisateur (chmod).", _chmod,
                  parameters={"path": P_PATH, "mode": MODE, "recursive": RECURSIVE}, required=("path", "mode"),
                  keywords=("droits", "permissions", "chmod", "exécutable", "rendre exécutable")),
    function_tool("os_sys_chmod", C, "Change les droits d'un fichier système (chmod, en administrateur).", _chmod,
                  parameters={"path": P_PATH, "mode": MODE, "recursive": RECURSIVE}, required=("path", "mode"),
                  requires_root=True, dangerous=True, keywords=("chmod root", "droits système")),
    function_tool("os_sys_chown", C, "Change le propriétaire et/ou le groupe (chown, en administrateur).", _chown,
                  parameters={"path": P_PATH,
                              "user": {"type": "string", "pattern": NAME, "description": "nouveau propriétaire"},
                              "group": {"type": "string", "pattern": NAME, "description": "nouveau groupe"},
                              "recursive": RECURSIVE},
                  required=("path",), requires_root=True, dangerous=True,
                  keywords=("propriétaire", "chown", "owner", "groupe")),
]
