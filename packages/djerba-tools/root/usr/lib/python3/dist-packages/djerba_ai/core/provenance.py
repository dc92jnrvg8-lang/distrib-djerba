# SPDX-License-Identifier: GPL-3.0-or-later
"""Provenance et identité tenues par le démon root (invariants 06, 09, 10, 11).

Le démon ne croit pas ce que le client déclare : il dérive l'identité des
canaux qu'il contrôle et présume « non fiable » tout le reste.

* **Sessions** : chaque conversation reçoit sa clé du démon
  (``session.open``), liée à l'uid de l'appelant (SO_PEERCRED) et à un canal
  (``agent``, ``mcp``, ``import``). Une clé inconnue, ou présentée par un
  autre uid, est traitée comme non fiable.
* **Moteur** : celui de la configuration système (``llm.engine`` /
  ``llm.provider``, que seul un humain authentifié change) ; pour le canal
  MCP, l'appelant authentifié par son jeton (``mcp-<client>``). Le moteur
  déclaré par le client n'est qu'une étiquette d'audit.
* **Drapeau « non fiable »** : posé par le démon quand une session lit un
  contenu dont il ne garantit pas la provenance (tout outil absent de la
  liste ``[provenance] fiables`` de la politique), à l'import d'un historique
  et sur signalement du client ; **monotone** : seul un humain authentifié
  l'efface (``session.untaint``, polkit administrateur). Il survit au
  changement de moteur, à la reprise de la conversation et au redémarrage.
* **Sorties réseau** : une session non fiable ne contacte plus que les
  destinations déjà jointes avant de le devenir.

Stockage : ``/var/lib/djerba-ai/provenance.db`` (SQLite, root, 0600). Toute
erreur de lecture ou d'écriture est remontée : la politique refuse alors
l'action (fail-closed, invariant 31).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

CHANNELS = ("agent", "mcp", "import")
_LOCK = threading.Lock()


class ProvenanceError(Exception):
    """Registre de provenance illisible ou incohérent : l'action modifiante est refusée."""


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists()
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, uid INTEGER NOT NULL, "
                               "channel TEXT NOT NULL, client TEXT NOT NULL DEFAULT '', created REAL NOT NULL, "
                               "tainted INTEGER NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '[]', "
                               "destinations TEXT NOT NULL DEFAULT '[]')")
            if fresh:
                os.chmod(self.path, 0o600)
            return connection
        except (sqlite3.Error, OSError) as exc:
            raise ProvenanceError(f"registre de provenance illisible ({self.path}) : {exc}") from exc

    def _query(self, query: str, args: tuple = ()) -> list[sqlite3.Row]:
        with _LOCK:
            connection = self._connect()
            try:
                return connection.execute(query, args).fetchall()
            except sqlite3.Error as exc:
                raise ProvenanceError(f"registre de provenance : {exc}") from exc
            finally:
                connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["tainted"] = bool(data["tainted"])
        data["reasons"] = json.loads(data["reasons"])
        data["destinations"] = json.loads(data["destinations"])
        return data

    # ------------------------------------------------------------ sessions
    def open(self, uid: int, channel: str = "agent", client: str = "", tainted: bool = False,
             reason: str = "") -> str:
        if channel not in CHANNELS:
            raise ProvenanceError(f"canal inconnu : {channel}")
        key = secrets.token_hex(16)
        reasons = [reason or "ouverte non fiable"] if tainted else []
        self._query("INSERT INTO sessions (key, uid, channel, client, created, tainted, reasons) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (key, uid, channel, client, time.time(), int(tainted),
                                                     json.dumps(reasons, ensure_ascii=False)))
        return key

    def client_session(self, uid: int, client: str) -> str:
        """Session persistante d'un appelant MCP (une par client : son drapeau le suit d'un appel à l'autre)."""
        rows = self._query("SELECT key FROM sessions WHERE channel = 'mcp' AND client = ?", (client,))
        return rows[0]["key"] if rows else self.open(uid, "mcp", client)

    def get(self, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        return self._decode(next(iter(self._query("SELECT * FROM sessions WHERE key = ?", (key,))), None))

    def taint(self, key: str, reason: str) -> None:
        """Monotone : le drapeau ne fait que se poser (raisons cumulées, 20 au plus)."""
        session = self.get(key)
        if session is None:
            return
        reasons = (session["reasons"] + [reason[:160]])[-20:] if reason not in session["reasons"] else \
            session["reasons"]
        self._query("UPDATE sessions SET tainted = 1, reasons = ? WHERE key = ?",
                    (json.dumps(reasons, ensure_ascii=False), key))

    def untaint(self, key: str) -> bool:
        """Réservé à l'humain authentifié (méthode session.untaint, polkit administrateur)."""
        if self.get(key) is None:
            return False
        self._query("UPDATE sessions SET tainted = 0, reasons = '[]' WHERE key = ?", (key,))
        return True

    def contact(self, key: str, host: str) -> None:
        """Destination réseau jointe par une session encore fiable (seule autorisée ensuite)."""
        session = self.get(key)
        if session is None or session["tainted"] or not host or host in session["destinations"]:
            return
        self._query("UPDATE sessions SET destinations = ? WHERE key = ?",
                    (json.dumps((session["destinations"] + [host])[-50:]), key))

    def forget(self, key: str) -> bool:
        return bool(self._query("DELETE FROM sessions WHERE key = ? RETURNING key", (key,)))
