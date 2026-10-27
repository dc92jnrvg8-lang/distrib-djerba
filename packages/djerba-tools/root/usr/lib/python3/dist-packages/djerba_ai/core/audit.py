# SPDX-License-Identifier: GPL-3.0-or-later
"""Journal d'audit de l'agent Djerba : /var/log/djerba/audit.log.

Une ligne JSON par action critique traitée par le démon root (exécution
d'outil, réglage, correctif, extension, installation, refus d'autorisation,
connexion rejetée) : horodatage, appelant (uid, pid, exécutable), méthode,
outil, paramètres et résultat — secrets masqués. Fichier root:adm 0640
(lisible par les administrateurs), rotation par logrotate.
"""

from __future__ import annotations

import grp
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from djerba_ai.core.sanitize import sanitize_value

AUDIT_LOG = Path(os.environ.get("DJERBA_AUDIT_LOG", "/var/log/djerba/audit.log"))
_LOCK = threading.Lock()


def _executable(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


def _prepare() -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not AUDIT_LOG.exists():
        AUDIT_LOG.touch(mode=0o640)
        if os.geteuid() == 0:
            try:
                group = grp.getgrnam("adm").gr_gid
                os.chown(AUDIT_LOG.parent, 0, group)
                os.chmod(AUDIT_LOG.parent, 0o750)
                os.chown(AUDIT_LOG, 0, group)
            except KeyError:
                pass
        os.chmod(AUDIT_LOG, 0o640)


def record(event: str, *, uid: int, pid: int, method: str = "", tool: str = "", params: Any = None,
           success: bool | None = None, message: str = "", duration_ms: int | None = None) -> dict:
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
    }
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    line = json.dumps({key: value for key, value in entry.items() if value not in (None, "")},
                      ensure_ascii=False)
    with _LOCK:
        try:
            _prepare()
            with AUDIT_LOG.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
    return entry


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
