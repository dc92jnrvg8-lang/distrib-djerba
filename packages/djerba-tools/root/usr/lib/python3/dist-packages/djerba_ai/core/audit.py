# SPDX-License-Identifier: GPL-3.0-or-later
"""Registre d'audit immuable de l'agent Djerba : /var/log/djerba/audit.log.

* **Avant** chaque commande administrative, une entrée « demande » est écrite
  (appelant : uid, pid, exécutable ; méthode, outil, paramètres masqués) ;
  le résultat suit dans une entrée « action » qui y renvoie (``ref``).
* **Chaîne de hachages** : chaque ligne JSON porte son numéro de séquence,
  le hachage SHA-256 de la ligne précédente (``prev``) et le sien
  (``hash``, calculé sur la forme canonique de l'entrée). Modifier, insérer
  ou supprimer une ligne rompt la chaîne ; la tête de chaîne (dernier numéro
  et dernier hachage) est aussi conservée dans /var/lib/djerba-ai/audit.head,
  ce qui détecte une troncature de la fin du journal.
* **Ajout seul** : le fichier porte l'attribut « append-only » du noyau
  (chattr +a, FS_APPEND_FL) quand le système de fichiers le permet : même
  root ne peut ni le réécrire ni le tronquer sans retirer d'abord
  l'attribut, ce qui est lui-même tracé par la rotation.
* **Rotation** interne (system.audit_max_mb) : audit.log → audit.log.1 … .5,
  la chaîne continue d'un fichier à l'autre (entrée « rotation »).

Fichiers root:adm 0640 (lisibles par les administrateurs). ``verify`` contrôle
toute la chaîne.
"""

from __future__ import annotations

import fcntl
import grp
import hashlib
import json
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any

from djerba_ai.core.sanitize import sanitize_value

AUDIT_LOG = Path(os.environ.get("DJERBA_AUDIT_LOG", "/var/log/djerba/audit.log"))
HEAD = Path(os.environ.get("DJERBA_AUDIT_HEAD", "/var/lib/djerba-ai/audit.head"))
KEEP = 5
GENESIS = "0" * 64
FS_IOC_GETFLAGS = 0x80086601
FS_IOC_SETFLAGS = 0x40086602
FS_APPEND_FL = 0x00000020
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"seq": None, "hash": None}


def _executable(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def canonical(entry: dict) -> bytes:
    return json.dumps({key: value for key, value in entry.items() if key != "hash"}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode()


def digest(entry: dict) -> str:
    return hashlib.sha256(canonical(entry)).hexdigest()


# ---------------------------------------------------------------- Attribut « append-only »
def set_append_only(path: Path, enabled: bool) -> bool:
    """Active ou retire FS_APPEND_FL ; False si le système de fichiers ne le permet pas."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        flags = struct.unpack("i", fcntl.ioctl(fd, FS_IOC_GETFLAGS, struct.pack("i", 0)))[0]
        flags = flags | FS_APPEND_FL if enabled else flags & ~FS_APPEND_FL
        fcntl.ioctl(fd, FS_IOC_SETFLAGS, struct.pack("i", flags))
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def is_append_only(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        return bool(struct.unpack("i", fcntl.ioctl(fd, FS_IOC_GETFLAGS, struct.pack("i", 0)))[0] & FS_APPEND_FL)
    except OSError:
        return False
    finally:
        os.close(fd)


# ---------------------------------------------------------------- Fichiers
def files() -> list[Path]:
    """Fichiers du journal, du plus ancien au plus récent."""
    rotated = [AUDIT_LOG.with_name(f"{AUDIT_LOG.name}.{index}") for index in range(KEEP, 0, -1)]
    return [path for path in [*rotated, AUDIT_LOG] if path.exists()]


def _prepare(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        os.close(fd)
        if os.geteuid() == 0:
            try:
                group = grp.getgrnam("adm").gr_gid
                os.chown(path.parent, 0, group)
                os.chmod(path.parent, 0o750)
                os.chown(path, 0, group)
            except KeyError:
                pass
        os.chmod(path, 0o640)
    if os.geteuid() == 0 and not is_append_only(path):
        set_append_only(path, True)


def _last_entry() -> dict | None:
    for path in reversed(files()):
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 65536))
                lines = handle.read().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _chain_state() -> tuple[int, str]:
    if _STATE["seq"] is None:
        last = _last_entry()
        _STATE["seq"] = int(last.get("seq", 0)) if last else 0
        _STATE["hash"] = last.get("hash", GENESIS) if last else GENESIS
    return _STATE["seq"], _STATE["hash"]


def _write_head(seq: int, value: str) -> None:
    try:
        HEAD.parent.mkdir(parents=True, exist_ok=True)
        temporary = HEAD.with_name(".audit.head.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"seq": seq, "hash": value}))
        os.replace(temporary, HEAD)
    except OSError:
        pass


def _max_bytes() -> int:
    try:
        from djerba_ai.core.config import Store
        return int(Store().load().get("system.audit_max_mb", 10)) * 1024 * 1024
    except Exception:  # noqa: BLE001 - la configuration ne doit jamais bloquer l'audit
        return 10 * 1024 * 1024


def _rotate() -> None:
    for index in range(KEEP, 0, -1):
        source = AUDIT_LOG if index == 1 else AUDIT_LOG.with_name(f"{AUDIT_LOG.name}.{index - 1}")
        target = AUDIT_LOG.with_name(f"{AUDIT_LOG.name}.{index}")
        if source.exists():
            set_append_only(source, False)
            if index == KEEP and target.exists():
                set_append_only(target, False)
            os.replace(source, target)
            set_append_only(target, True)


# ---------------------------------------------------------------- Écriture
def _append(entry: dict) -> dict:
    seq, previous = _chain_state()
    entry = {"seq": seq + 1, "prev": previous, **entry}
    entry["hash"] = digest(entry)
    _prepare(AUDIT_LOG)
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _STATE.update(seq=entry["seq"], hash=entry["hash"])
    _write_head(entry["seq"], entry["hash"])
    return entry


def record(event: str, *, uid: int, pid: int, method: str = "", tool: str = "", params: Any = None,
           success: bool | None = None, message: str = "", duration_ms: int | None = None,
           ref: int | None = None) -> dict:
    entry = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "uid": uid,
        "pid": pid,
        "exe": _executable(pid),
        "method": method,
        "tool": tool,
        "params": sanitize_value(params) if params is not None else None,
        "success": success,
        "message": sanitize_value(str(message))[:500],
        "duration_ms": duration_ms,
        "ref": ref,
    }
    entry = {key: value for key, value in entry.items() if value not in (None, "")}
    with _LOCK:
        try:
            if AUDIT_LOG.exists() and AUDIT_LOG.stat().st_size > _max_bytes():
                _rotate()
                _append({"time": entry["time"], "event": "rotation", "uid": 0, "pid": os.getpid(),
                         "message": "rotation du journal d'audit"})
            return _append(entry)
        except OSError:
            return entry


# ---------------------------------------------------------------- Lecture et vérification
def tail(lines: int = 200) -> list[dict]:
    try:
        with AUDIT_LOG.open(encoding="utf-8") as handle:
            content = handle.readlines()[-lines:]
    except OSError:
        return []
    entries = []
    for line in content:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def verify() -> dict:
    """Contrôle la chaîne complète (tous les fichiers) et la tête de chaîne."""
    previous, expected_seq, count = None, None, 0
    for path in files():
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                where = f"{path.name}, ligne {number}"
                try:
                    entry = json.loads(line)
                except ValueError:
                    return {"success": False, "entries": count, "message": f"ligne illisible ({where})"}
                if entry.get("hash") != digest(entry):
                    return {"success": False, "entries": count, "message": f"entrée modifiée ({where})"}
                if previous is not None and entry.get("prev") != previous:
                    return {"success": False, "entries": count, "message": f"chaîne rompue ({where})"}
                if expected_seq is not None and entry.get("seq") != expected_seq:
                    return {"success": False, "entries": count,
                            "message": f"séquence discontinue ({where} : {entry.get('seq')} au lieu de {expected_seq})"}
                previous, expected_seq, count = entry["hash"], entry.get("seq", 0) + 1, count + 1
    try:
        head = json.loads(HEAD.read_text())
    except (OSError, ValueError):
        head = None
    if head and count and (head.get("hash") != previous or head.get("seq") != expected_seq - 1):
        return {"success": False, "entries": count,
                "message": f"fin du journal tronquée (dernière entrée attendue : n°{head.get('seq')})"}
    protected = is_append_only(AUDIT_LOG)
    return {"success": True, "entries": count, "append_only": protected,
            "message": f"Journal d'audit intègre : {count} entrée(s) chaînée(s)"
                       + (", attribut ajout seul actif." if protected else ".")}
