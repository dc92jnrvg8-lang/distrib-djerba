# SPDX-License-Identifier: GPL-3.0-or-later
"""Fichiers et dossiers : lecture, écriture, liste, création, copie, déplacement, suppression.

Ces capacités s'exécutent avec les droits de l'utilisateur ; « sys_file_write »
(fichiers de configuration sous /etc, /usr/local, /opt, /srv) passe par le
démon root et garde une sauvegarde « .djerba.bak ».
"""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

from djerba_ai.tools.base import ToolError, fail, function_tool, ok
from djerba_ai.tools.os.guard import PATH, TEXT_MAX, check_mutable, check_root_writable, resolve

P_PATH = {"type": "string", "description": "chemin (absolu, ou relatif au dossier personnel ; ~ accepté)",
          "pattern": PATH}
CONTENT = {"type": "string", "description": "contenu texte", "pattern": r"^[^\x00]*$", "max_length": TEXT_MAX}


def _read(params: dict, _verbose: bool) -> dict:
    path = resolve(params["path"])
    if not path.is_file():
        return fail(f"{path} n'est pas un fichier")
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(params["max_bytes"])
    if b"\x00" in raw[:4096]:
        return fail(f"{path} est un fichier binaire", size=size)
    text = raw.decode("utf-8", errors="replace")
    return ok(f"{path} ({size} octets){' — tronqué' if size > len(raw) else ''}.", path=str(path),
              size=size, content=text, truncated=size > len(raw))


def _write_text(path: Path, content: str, append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if append:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary = path.with_name(f".{path.name}.djerba-tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _write(params: dict, _verbose: bool) -> dict:
    path = check_mutable(resolve(params["path"], must_exist=False))
    if path.is_dir():
        return fail(f"{path} est un dossier")
    _write_text(path, params["content"], params["append"])
    return ok(f"{path} {'complété' if params['append'] else 'enregistré'} ({len(params['content'])} caractères).",
              path=str(path))


def _sys_write(params: dict, _verbose: bool) -> dict:
    path = check_root_writable(resolve(params["path"], must_exist=False))
    if path.is_dir():
        return fail(f"{path} est un dossier")
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".djerba.bak")
        shutil.copy2(path, backup)
    _write_text(path, params["content"], params["append"])
    return ok(f"{path} enregistré" + (f" (sauvegarde : {backup})." if backup else "."), path=str(path),
              backup=str(backup or ""))


def _entry(path: Path) -> dict:
    try:
        info = path.lstat()
    except OSError:
        return {"name": path.name, "type": "?"}
    kind = "dossier" if stat.S_ISDIR(info.st_mode) else "lien" if stat.S_ISLNK(info.st_mode) else "fichier"
    return {"name": path.name, "type": kind, "size": info.st_size, "mode": stat.filemode(info.st_mode),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(info.st_mtime))}


def _list(params: dict, _verbose: bool) -> dict:
    path = resolve(params["path"])
    if not path.is_dir():
        return fail(f"{path} n'est pas un dossier")
    children = sorted(path.iterdir(), key=lambda child: (not child.is_dir(), child.name.lower()))
    if not params["hidden"]:
        children = [child for child in children if not child.name.startswith(".")]
    entries = [_entry(child) for child in children[:500]]
    return ok(f"{path} : {len(children)} élément(s).", path=str(path), entries=entries,
              truncated=len(children) > 500)


def _mkdir(params: dict, _verbose: bool) -> dict:
    path = check_mutable(resolve(params["path"], must_exist=False))
    path.mkdir(parents=True, exist_ok=True)
    return ok(f"Dossier {path} prêt.", path=str(path))


def _copy(params: dict, _verbose: bool) -> dict:
    source = resolve(params["source"])
    target = check_mutable(resolve(params["target"], must_exist=False))
    if target.is_dir():
        target = target / source.name
    if target.exists() and not params["overwrite"]:
        return fail(f"{target} existe déjà")
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, dirs_exist_ok=params["overwrite"])
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return ok(f"{source} copié vers {target}.", source=str(source), target=str(target))


def _move(params: dict, _verbose: bool) -> dict:
    source = check_mutable(resolve(params["source"]))
    target = check_mutable(resolve(params["target"], must_exist=False))
    if target.is_dir():
        target = target / source.name
    if target.exists() and not params["overwrite"]:
        return fail(f"{target} existe déjà")
    shutil.move(source, target)
    return ok(f"{source} déplacé vers {target}.", source=str(source), target=str(target))


def _delete(params: dict, _verbose: bool) -> dict:
    path = check_mutable(resolve(params["path"]))
    if path.is_dir() and not path.is_symlink():
        if any(path.iterdir()) and not params["recursive"]:
            raise ToolError(f"{path} n'est pas vide (recursive=true pour tout supprimer)")
        shutil.rmtree(path)
    else:
        path.unlink()
    return ok(f"{path} supprimé.", path=str(path))


C = "os"
OVERWRITE = {"type": "boolean", "default": False, "description": "remplacer la cible si elle existe"}
TOOLS = [
    function_tool("os_file_read", C, "Lit un fichier texte.", _read,
                  parameters={"path": P_PATH, "max_bytes": {"type": "integer", "minimum": 1, "maximum": TEXT_MAX,
                                                            "default": 200000, "description": "taille lue au plus"}},
                  required=("path",), keywords=("lire", "afficher fichier", "cat", "contenu", "read file")),
    function_tool("os_file_write", C, "Crée, remplace ou complète un fichier texte de l'utilisateur.", _write,
                  parameters={"path": P_PATH, "content": CONTENT,
                              "append": {"type": "boolean", "default": False, "description": "ajouter à la fin"}},
                  required=("path", "content"), dangerous=True,
                  keywords=("écrire", "créer fichier", "enregistrer", "write file", "modifier fichier")),
    function_tool("os_sys_file_write", C, "Écrit un fichier système (sous /etc, /usr/local, /opt, /srv) avec "
                  "sauvegarde.", _sys_write,
                  parameters={"path": P_PATH, "content": CONTENT,
                              "append": {"type": "boolean", "default": False, "description": "ajouter à la fin"}},
                  required=("path", "content"), requires_root=True, dangerous=True,
                  keywords=("configuration système", "/etc", "fichier système")),
    function_tool("os_dir_list", C, "Liste le contenu d'un dossier.", _list,
                  parameters={"path": dict(P_PATH, default="~"),
                              "hidden": {"type": "boolean", "default": False,
                                         "description": "inclure les éléments cachés"}},
                  keywords=("lister", "dossier", "ls", "contenu du dossier", "list directory")),
    function_tool("os_dir_create", C, "Crée un dossier (et ses parents).", _mkdir,
                  parameters={"path": P_PATH}, required=("path",), keywords=("créer dossier", "mkdir")),
    function_tool("os_path_copy", C, "Copie un fichier ou un dossier.", _copy,
                  parameters={"source": P_PATH, "target": P_PATH, "overwrite": OVERWRITE},
                  required=("source", "target"), keywords=("copier", "dupliquer", "copy", "cp")),
    function_tool("os_path_move", C, "Déplace ou renomme un fichier ou un dossier.", _move,
                  parameters={"source": P_PATH, "target": P_PATH, "overwrite": OVERWRITE},
                  required=("source", "target"), dangerous=True, keywords=("déplacer", "renommer", "move", "mv")),
    function_tool("os_path_delete", C, "Supprime un fichier ou un dossier.", _delete,
                  parameters={"path": P_PATH, "recursive": {"type": "boolean", "default": False,
                                                            "description": "supprimer un dossier non vide"}},
                  required=("path",), dangerous=True, keywords=("supprimer", "effacer", "delete", "rm")),
]
