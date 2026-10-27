# SPDX-License-Identifier: GPL-3.0-or-later
"""Garde-fous de chemins pour les capacités « os »."""

from __future__ import annotations

import os
from pathlib import Path

from djerba_ai.tools.base import ToolError

#: jamais modifiables par l'agent, ni eux ni leur contenu
PROTECTED_TREES = ("/proc", "/sys", "/dev", "/boot", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64",
                   "/var/lib/dpkg", "/var/lib/apt", "/var/log/djerba", "/etc/djerba", "/etc/sudoers.d",
                   "/etc/polkit-1", "/etc/pam.d", "/etc/security", "/run/djerba-ai", "/var/lib/djerba-ai")
#: modifiables seulement en dessous (jamais le dossier lui-même)
PROTECTED_ROOTS = ("/", "/etc", "/home", "/root", "/var", "/run", "/opt", "/srv", "/mnt", "/media", "/tmp")
PROTECTED_FILES = ("/etc/passwd", "/etc/shadow", "/etc/group", "/etc/gshadow", "/etc/sudoers", "/etc/fstab",
                   "/etc/crypttab", "/etc/hostname", "/etc/hosts")
#: seuls endroits où l'écriture root de fichiers est permise
ROOT_WRITABLE = ("/etc", "/usr/local", "/opt", "/srv")
TEXT_MAX = 2 * 1024 * 1024
PATH = r"^[^\x00]{1,4096}$"


def resolve(path: str, *, must_exist: bool = True) -> Path:
    if os.geteuid() == 0 and not path.startswith("/"):
        raise ToolError(f"{path} : chemin absolu requis pour une action administrateur")
    expanded = Path(os.path.expanduser(path))
    if not expanded.is_absolute():
        expanded = Path.home() / expanded
    resolved = Path(os.path.realpath(expanded))
    if must_exist and not resolved.exists() and not resolved.is_symlink():
        raise ToolError(f"{path} : introuvable")
    return resolved


def _inside(path: Path, tree: str) -> bool:
    return path == Path(tree) or Path(tree) in path.parents


def check_mutable(path: Path) -> Path:
    """Refuse toute modification d'un chemin vital (après résolution des liens)."""
    text = str(path)
    if text in PROTECTED_ROOTS or text in PROTECTED_FILES or any(_inside(path, tree) for tree in PROTECTED_TREES):
        raise ToolError(f"{text} : chemin vital du système, modification refusée")
    home = Path.home()
    if os.geteuid() != 0 and path == home:
        raise ToolError("le dossier personnel lui-même ne peut pas être modifié ainsi")
    return path


def check_root_writable(path: Path) -> Path:
    check_mutable(path)
    if not any(_inside(path, tree) and path != Path(tree) for tree in ROOT_WRITABLE):
        raise ToolError(f"{path} : écriture root permise seulement sous {', '.join(ROOT_WRITABLE)}")
    return path
