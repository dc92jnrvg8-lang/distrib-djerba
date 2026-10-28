# SPDX-License-Identifier: GPL-3.0-or-later
"""Sauvegardes avant action et restauration (exécuté par le démon root).

Deux mécanismes, choisis automatiquement :

* **Btrfs** (système installé, sous-volume racine ``@``) : instantané en
  lecture seule du sous-volume racine **avant** l'action, puis **après** ;
  ``btrfs send --no-data -p avant après | btrfs receive --dump`` donne la
  liste exacte des chemins créés, modifiés, renommés et supprimés — aucun
  outil n'a besoin de déclarer ce qu'il touche. L'instantané « après » est
  supprimé dès la liste établie ; « avant » est conservé pour l'undo.
* **Fichiers** (session live sur overlay, ext4, xfs) : copie de ``/etc`` et
  ``/usr/local`` (et de ``/boot``, ESP comprise, pour les actions qui
  touchent l'amorçage) avec un manifeste d'empreintes ; la comparaison des
  manifestes donne les chemins changés. Les paquets installés hors de ces
  dossiers sont annulés par le delta dpkg (``core.transactions``).

La restauration est commune : pour chaque chemin touché, s'il existait avant
l'action il est recopié depuis la sauvegarde (copie reflink quand c'est
possible, puis renommage atomique), sinon il est supprimé. Les journaux, les
caches et les données de l'agent (dont ce journal de transactions) ne sont
jamais restaurés.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: chemins jamais restaurés (relatifs à la racine, sans « / » initial)
EXCLUDED = (
    "var/log/", "var/cache/", "var/tmp/", "tmp/", "var/spool/", "var/backups/",
    "var/lib/djerba-ai/", "var/lib/djerba-ai-runs/", "var/lib/systemd/", "var/lib/apt/lists/",
    "var/lib/dhcp/", "var/lib/NetworkManager/", "var/lib/sddm/", "var/lib/upower/", "var/lib/plocate/",
    "var/lib/clamav/", "var/lib/logrotate/", "var/lib/AccountsService/", "var/lib/polkit-1/",
    "home/", "root/.cache/", ".snapshots/", "swapfile", "@djerba",
)
CREATE = {"mkfile", "mkdir", "mknod", "mkfifo", "mksock", "symlink", "link"}
DELETE = {"unlink", "rmdir"}
#: opérations qui ne changent que les métadonnées (un dossier ainsi touché n'est pas restauré récursivement)
METADATA = {"utimes", "chmod", "chown", "set_xattr", "remove_xattr", "fileattr"}
STRUCTURAL = CREATE | DELETE | {"rename_from", "rename_to"}
#: noms temporaires d'orphelins utilisés par « btrfs send » (jamais des chemins réels du système)
SEND_ORPHAN = re.compile(r"(^|/)o\d+-\d+-\d+(/|$)")

Runner = Callable[..., subprocess.CompletedProcess]


class SnapshotError(Exception):
    """Sauvegarde ou restauration impossible."""


def _run(argv: list[str], timeout: int = 600, **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def excluded(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in EXCLUDED)


# ---------------------------------------------------------------- Liste des changements
@dataclass
class Changes:
    """Chemins touchés (relatifs) -> opérations observées."""

    paths: dict[str, set[str]] = field(default_factory=dict)

    def add(self, path: str, operation: str) -> None:
        if path:
            self.paths.setdefault(path, set()).add(operation)

    def filtered(self) -> "Changes":
        return Changes({path: ops for path, ops in self.paths.items()
                        if not excluded(path) and not SEND_ORPHAN.search(path)})

    def to_json(self) -> dict[str, list[str]]:
        return {path: sorted(ops) for path, ops in sorted(self.paths.items())}

    @classmethod
    def from_json(cls, data: dict[str, list[str]]) -> "Changes":
        return cls({path: set(ops) for path, ops in (data or {}).items()})

    def __bool__(self) -> bool:
        return bool(self.paths)


def _unescape(token: str) -> str:
    """Chemins de « btrfs receive --dump » : caractères spéciaux préfixés d'une barre oblique inverse."""
    out, index = [], 0
    while index < len(token):
        char = token[index]
        if char == "\\" and index + 1 < len(token):
            octal = re.match(r"[0-7]{3}", token[index + 1:index + 4])
            if octal:
                out.append(chr(int(octal.group(0), 8)))
                index += 4
                continue
            out.append(token[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _tokens(line: str) -> list[str]:
    tokens, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append("\\" + char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def _relative(path: str) -> str:
    """« ./instantané/etc/x » -> « etc/x »."""
    path = path[2:] if path.startswith("./") else path
    return path.split("/", 1)[1].strip("/") if "/" in path else ""


def parse_dump(text: str) -> Changes:
    changes = Changes()
    for line in text.splitlines():
        tokens = _tokens(line)
        if len(tokens) < 2:
            continue
        operation, path = tokens[0], _relative(_unescape(tokens[1]))
        options = dict(token.split("=", 1) for token in tokens[2:] if "=" in token)
        if operation in ("snapshot", "subvol"):
            continue
        if operation == "rename":
            changes.add(path, "rename_from")
            changes.add(_relative(_unescape(options.get("dest", ""))), "rename_to")
        else:
            changes.add(path, operation)
    return changes


# ---------------------------------------------------------------- Restauration commune
def _copy(source: Path, target: Path, runner: Runner = _run) -> None:
    """Copie fidèle (droits, propriétaire, liens, xattr), reflink si possible, remplacement atomique."""
    temporary = target.with_name(f".{target.name}.djerba-restore")
    if temporary.exists() or temporary.is_symlink():
        _remove(temporary)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = runner(["cp", "-a", "--reflink=auto", "--no-target-directory", str(source), str(temporary)])
    if result.returncode != 0:
        raise SnapshotError(f"copie impossible de {source} : {result.stderr.strip()[:200]}")
    if target.is_dir() and not target.is_symlink() and temporary.is_dir():
        old = target.with_name(f".{target.name}.djerba-old")
        os.replace(target, old)
        os.replace(temporary, target)
        _remove(old)
    else:
        if target.is_dir() and not target.is_symlink():
            _remove(target)
        os.replace(temporary, target)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _metadata(source: Path, target: Path) -> None:
    info = source.lstat()
    if not target.is_symlink():
        os.chmod(target, stat.S_IMODE(info.st_mode))
    os.lchown(target, info.st_uid, info.st_gid)


def restore(changes: Changes, before: Path, live: Path, runner: Runner = _run) -> dict:
    """Ramène chaque chemin touché à son état dans ``before`` ; renvoie le bilan."""
    restored, removed, errors = [], [], []
    paths = sorted(changes.filtered().paths.items())
    # Suppressions d'abord, les plus profondes en premier ; puis restaurations, les plus hautes en premier.
    to_remove = [path for path, _ops in paths if not (before / path).exists() and not (before / path).is_symlink()]
    for path in sorted(to_remove, key=lambda item: item.count("/"), reverse=True):
        target = live / path
        if target.exists() or target.is_symlink():
            try:
                _remove(target)
                removed.append(path)
            except OSError as exc:
                errors.append(f"{path} : {exc}")
    skip: list[str] = []
    for path, operations in sorted(paths, key=lambda item: item[0].count("/")):
        source = before / path
        if path in to_remove or any(path.startswith(parent + "/") for parent in skip):
            continue
        target = live / path
        try:
            if source.is_dir() and not source.is_symlink():
                if operations & STRUCTURAL or not target.is_dir() or target.is_symlink():
                    _copy(source, target, runner)
                    skip.append(path)  # sous-arbre restauré en entier
                else:
                    _metadata(source, target)  # dossier dont seuls les métadonnées ou le contenu direct ont changé
                restored.append(path)
            else:
                _copy(source, target, runner)
                restored.append(path)
        except (OSError, SnapshotError) as exc:
            errors.append(f"{path} : {exc}")
    return {"restored": restored, "removed": removed, "errors": errors}


def tree_digest(root: Path, ignore: tuple[str, ...] = ()) -> str:
    """Empreinte d'une arborescence (contenu, type, droits, propriétaire, liens) — pour les tests et la vérification."""
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root):
        dirs.sort()
        relative_dir = os.path.relpath(directory, root)
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = os.path.normpath(os.path.join(relative_dir, name))
            if any(relative == item.rstrip("/") or relative.startswith(item) for item in ignore):
                continue
            info = path.lstat()
            digest.update(f"{relative}\0{stat.S_IFMT(info.st_mode)}\0{stat.S_IMODE(info.st_mode)}\0"
                          f"{info.st_uid}\0{info.st_gid}\0".encode())
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


# ---------------------------------------------------------------- Btrfs
class BtrfsBackend:
    """Instantanés du sous-volume racine dans ``<racine Btrfs>/@djerba-snapshots``."""

    name = "btrfs"
    STORE = "@djerba-snapshots"

    def __init__(self, top: Path, source: str = "@", live: Path = Path("/"), runner: Runner = _run) -> None:
        self.top, self.source, self.live, self.runner = top, source.strip("/"), live, runner
        self.store = top / self.STORE

    @classmethod
    def detect(cls, mountpoint: Path = Path("/run/djerba-ai/btrfs"), runner: Runner = _run) -> "BtrfsBackend | None":
        """Racine en Btrfs : monte le sommet du volume (subvolid=5) une fois, sans toucher au système."""
        found = runner(["findmnt", "-n", "-o", "FSTYPE,SOURCE,FSROOT", "/"], timeout=10)
        fields = found.stdout.split()
        if found.returncode != 0 or len(fields) < 3 or fields[0] != "btrfs":
            return None
        device = re.sub(r"\[.*\]$", "", fields[1])
        mounted = runner(["findmnt", "-n", "-o", "TARGET", "--mountpoint", str(mountpoint)], timeout=10)
        if mounted.returncode != 0 or not mounted.stdout.strip():
            mountpoint.mkdir(parents=True, exist_ok=True)
            result = runner(["mount", "-o", "subvolid=5,noatime", device, str(mountpoint)], timeout=60)
            if result.returncode != 0:
                return None
        return cls(mountpoint, fields[2], Path("/"), runner)

    def _btrfs(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        result = self.runner(["btrfs", *args], timeout=timeout)
        if result.returncode != 0:
            raise SnapshotError(f"btrfs {args[0]} {args[1] if len(args) > 1 else ''} : "
                                f"{(result.stderr or result.stdout).strip()[:300]}")
        return result

    def snapshot_path(self, tx_id: str, suffix: str = "avant") -> Path:
        return self.store / f"{tx_id}-{suffix}"

    def prepare(self, tx_id: str, boot: bool = False) -> str:
        self.store.mkdir(exist_ok=True)
        self._btrfs("subvolume", "snapshot", "-r", str(self.top / self.source), str(self.snapshot_path(tx_id)))
        return str(self.snapshot_path(tx_id))

    def capture(self, tx_id: str) -> Changes:
        after = self.snapshot_path(tx_id, "apres")
        self._btrfs("subvolume", "snapshot", "-r", str(self.top / self.source), str(after))
        try:
            send = subprocess.Popen(["btrfs", "send", "--no-data", "-q", "-p", str(self.snapshot_path(tx_id)),
                                     str(after)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            dump = subprocess.run(["btrfs", "receive", "--dump"], stdin=send.stdout, capture_output=True,
                                  text=True, timeout=600)
            if send.stdout:
                send.stdout.close()
            send.wait(timeout=600)
            if send.returncode != 0 or dump.returncode != 0:
                raise SnapshotError(f"btrfs send/receive : {dump.stderr.strip()[:300]}")
            return parse_dump(dump.stdout).filtered()
        finally:
            self.runner(["btrfs", "subvolume", "delete", str(after)], timeout=300)

    def before_root(self, tx_id: str) -> Path:
        return self.snapshot_path(tx_id)

    def available(self, tx_id: str) -> bool:
        return self.snapshot_path(tx_id).is_dir()

    def discard(self, tx_id: str) -> None:
        if self.snapshot_path(tx_id).exists():
            self.runner(["btrfs", "subvolume", "delete", str(self.snapshot_path(tx_id))], timeout=300)


# ---------------------------------------------------------------- Fichiers
def manifest(root: Path, roots: tuple[str, ...]) -> dict[str, list]:
    entries: dict[str, list] = {}
    for top in roots:
        base = root / top
        if not base.exists():
            continue
        walked = [base] + [Path(directory) / name for directory, dirs, files in os.walk(base)
                           for name in sorted(dirs) + sorted(files)]
        for path in walked:
            relative = os.path.relpath(path, root)
            try:
                info = path.lstat()
            except OSError:
                continue
            kind = stat.S_IFMT(info.st_mode)
            if stat.S_ISSOCK(kind) or stat.S_ISFIFO(kind):
                continue
            content = ""
            if stat.S_ISLNK(kind):
                content = os.readlink(path)
            elif stat.S_ISREG(kind):
                try:
                    content = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    content = f"illisible:{info.st_size}:{info.st_mtime_ns}"
            entries[relative] = [kind, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, content]
    return entries


def compare(before: dict[str, list], after: dict[str, list]) -> Changes:
    changes = Changes()
    for path in before.keys() - after.keys():
        changes.add(path, "unlink")
    for path in after.keys() - before.keys():
        changes.add(path, "mkfile")
    for path in before.keys() & after.keys():
        if before[path] != after[path]:
            same_content = before[path][0] == after[path][0] and before[path][4] == after[path][4]
            changes.add(path, "chmod" if same_content else "write")
    return changes.filtered()


class FilesBackend:
    """Copie de /etc et /usr/local (et /boot pour l'amorçage) + manifeste d'empreintes."""

    name = "fichiers"
    ROOTS = ("etc", "usr/local")
    BOOT = ("boot",)

    def __init__(self, store: Path = Path("/var/lib/djerba-ai/tx"), live: Path = Path("/"),
                 runner: Runner = _run) -> None:
        self.store, self.live, self.runner = store, live, runner

    def _roots(self, tx_id: str) -> tuple[str, ...]:
        try:
            return tuple(json.loads((self.store / tx_id / "racines.json").read_text()))
        except (OSError, ValueError):
            return self.ROOTS

    def prepare(self, tx_id: str, boot: bool = False) -> str:
        roots = self.ROOTS + (self.BOOT if boot else ())
        directory = self.store / tx_id
        before = directory / "avant"
        before.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        for top in roots:
            source = self.live / top
            if source.exists():
                (before / top).parent.mkdir(parents=True, exist_ok=True)
                result = self.runner(["cp", "-a", "--reflink=auto", "--no-target-directory", str(source),
                                      str(before / top)], timeout=900)
                if result.returncode != 0:
                    raise SnapshotError(f"copie de /{top} impossible : {result.stderr.strip()[:200]}")
        (directory / "racines.json").write_text(json.dumps(list(roots)))
        (directory / "manifeste.json").write_text(json.dumps(manifest(self.live, roots)))
        return str(before)

    def capture(self, tx_id: str) -> Changes:
        roots = self._roots(tx_id)
        before = json.loads((self.store / tx_id / "manifeste.json").read_text())
        return compare(before, manifest(self.live, roots))

    def before_root(self, tx_id: str) -> Path:
        return self.store / tx_id / "avant"

    def available(self, tx_id: str) -> bool:
        return self.before_root(tx_id).is_dir()

    def discard(self, tx_id: str) -> None:
        if (self.store / tx_id).exists():
            shutil.rmtree(self.store / tx_id)


def detect(store: Path = Path("/var/lib/djerba-ai/tx")) -> "BtrfsBackend | FilesBackend":
    return BtrfsBackend.detect() or FilesBackend(store)
