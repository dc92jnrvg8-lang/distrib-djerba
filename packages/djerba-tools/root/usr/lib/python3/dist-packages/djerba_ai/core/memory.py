# SPDX-License-Identifier: GPL-3.0-or-later
"""Mémoire locale de l'agent (SQLite, ~/.local/share/djerba/memory.db).

* conversations et messages ;
* faits retenus sur l'utilisateur et sa machine (« souviens-toi que… ») ;
* historique des outils exécutés et retours de l'utilisateur : l'agent s'en
  sert pour mieux classer les outils et pour se donner des exemples de
  requêtes réussies (amélioration continue, sans réentraînement) ;
* incidents détectés par l'auto-réparation ;
* historique des réglages ;
* index sémantique (vecteurs d'embeddings locaux : modèle d'embedding
  d'Ollama, ou repli par hachage de termes calculé localement).

Toute chaîne écrite passe par core.sanitize : clés d'API, mots de passe et
jetons sont masqués avant d'atteindre le disque.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from djerba_ai.core.config import MEMORY_DB
from djerba_ai.core.sanitize import sanitize

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, conversation INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, tool TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, subject TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tool_runs (
    id INTEGER PRIMARY KEY, tool TEXT NOT NULL, request TEXT, params TEXT, success INTEGER NOT NULL,
    message TEXT, feedback INTEGER DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, source TEXT NOT NULL, target TEXT,
    message TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'nouveau',
    patch TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings_history (
    id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, text TEXT NOT NULL, model TEXT NOT NULL,
    vector BLOB NOT NULL, updated REAL NOT NULL, UNIQUE(kind, ref));
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation);
CREATE INDEX IF NOT EXISTS embeddings_kind ON embeddings(kind, model);
CREATE INDEX IF NOT EXISTS tool_runs_tool ON tool_runs(tool);
"""


class Memory:
    def __init__(self, path: Path = MEMORY_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        path.chmod(0o600)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def _execute(self, query: str, args: tuple = ()) -> sqlite3.Cursor:
        # Filtre préventif : aucun secret n'est jamais écrit dans la base.
        args = tuple(sanitize(arg) if isinstance(arg, str) else arg for arg in args)
        with self.lock:
            cursor = self.db.execute(query, args)
            self.db.commit()
            return cursor

    def _rows(self, query: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute(query, args).fetchall()]

    # ---------------------------------------------------------------- Conversations
    def new_conversation(self, title: str) -> int:
        now = time.time()
        return self._execute("INSERT INTO conversations(title, created, updated) VALUES (?, ?, ?)",
                             (title[:80] or "Discussion", now, now)).lastrowid

    def conversations(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM conversations ORDER BY updated DESC LIMIT ?", (limit,))

    def add_message(self, conversation: int, role: str, content: str, tool: str | None = None) -> None:
        now = time.time()
        self._execute("INSERT INTO messages(conversation, role, content, tool, created) VALUES (?, ?, ?, ?, ?)",
                      (conversation, role, content, tool, now))
        self._execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, conversation))

    def messages(self, conversation: int, limit: int = 40) -> list[dict]:
        rows = self._rows("SELECT * FROM messages WHERE conversation = ? ORDER BY id DESC LIMIT ?",
                          (conversation, limit))
        return list(reversed(rows))

    def delete_conversation(self, conversation: int) -> None:
        self._execute("DELETE FROM conversations WHERE id = ?", (conversation,))

    # ---------------------------------------------------------------- Faits
    def remember(self, subject: str, content: str) -> int:
        return self._execute("INSERT INTO facts(subject, content, created) VALUES (?, ?, ?)",
                             (subject[:80], content[:1000], time.time())).lastrowid

    def facts(self, query: str = "", limit: int = 30) -> list[dict]:
        if not query:
            return self._rows("SELECT * FROM facts ORDER BY id DESC LIMIT ?", (limit,))
        like = f"%{query}%"
        return self._rows("SELECT * FROM facts WHERE subject LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT ?",
                          (like, like, limit))

    def forget(self, fact_id: int) -> bool:
        return self._execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    # ---------------------------------------------------------------- Apprentissage
    def record_run(self, tool: str, request: str, params: dict, success: bool, message: str) -> int:
        return self._execute(
            "INSERT INTO tool_runs(tool, request, params, success, message, created) VALUES (?, ?, ?, ?, ?, ?)",
            (tool, request[:500], json.dumps(params, ensure_ascii=False)[:2000], int(success), message[:500],
             time.time())).lastrowid

    def feedback(self, run_id: int, value: int) -> None:
        self._execute("UPDATE tool_runs SET feedback = ? WHERE id = ?", (max(-1, min(1, value)), run_id))

    def tool_scores(self) -> dict[str, float]:
        """Bonus de classement par outil : réussites, retours positifs, échecs."""
        rows = self._rows("SELECT tool, SUM(success) AS ok, COUNT(*) AS n, SUM(feedback) AS fb "
                          "FROM tool_runs GROUP BY tool")
        return {row["tool"]: (row["ok"] / row["n"]) * min(row["n"], 10) / 10 + 0.5 * (row["fb"] or 0)
                for row in rows}

    def examples(self, request: str, limit: int = 3) -> list[dict]:
        """Requêtes passées proches ayant réussi (exemples pour l'agent)."""
        words = {word for word in re.findall(r"\w{4,}", request.lower())}
        if not words:
            return []
        candidates = self._rows("SELECT tool, request, params FROM tool_runs WHERE success = 1 AND feedback >= 0 "
                                "ORDER BY id DESC LIMIT 400")
        scored = []
        for row in candidates:
            overlap = len(words & set(re.findall(r"\w{4,}", (row["request"] or "").lower())))
            if overlap >= 2:
                scored.append((overlap, row))
        scored.sort(key=lambda item: -item[0])
        seen, result = set(), []
        for _score, row in scored:
            if row["tool"] not in seen:
                seen.add(row["tool"])
                result.append(row)
            if len(result) >= limit:
                break
        return result

    def stats(self) -> dict[str, Any]:
        rows = self._rows("SELECT COUNT(*) AS n, SUM(success) AS ok FROM tool_runs")
        facts = self._rows("SELECT COUNT(*) AS n FROM facts")[0]["n"]
        return {"tool_runs": rows[0]["n"] or 0, "successes": rows[0]["ok"] or 0, "facts": facts}

    # ---------------------------------------------------------------- Incidents
    def record_incident(self, fingerprint: str, source: str, target: str, message: str) -> tuple[int, bool]:
        now = time.time()
        existing = self._rows("SELECT id FROM incidents WHERE fingerprint = ?", (fingerprint,))
        if existing:
            self._execute("UPDATE incidents SET occurrences = occurrences + 1, updated = ?, message = ? "
                          "WHERE id = ?", (now, message[:4000], existing[0]["id"]))
            return existing[0]["id"], False
        cursor = self._execute("INSERT INTO incidents(fingerprint, source, target, message, created, updated) "
                               "VALUES (?, ?, ?, ?, ?, ?)", (fingerprint, source, target, message[:4000], now, now))
        return cursor.lastrowid, True

    def incidents(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            return self._rows("SELECT * FROM incidents WHERE status = ? ORDER BY updated DESC LIMIT ?",
                              (status, limit))
        return self._rows("SELECT * FROM incidents ORDER BY updated DESC LIMIT ?", (limit,))

    def incident(self, incident_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        return rows[0] if rows else None

    def update_incident(self, incident_id: int, status: str, patch: str | None = None) -> None:
        self._execute("UPDATE incidents SET status = ?, patch = COALESCE(?, patch), updated = ? WHERE id = ?",
                      (status, patch, time.time(), incident_id))

    # ---------------------------------------------------------------- Réglages
    def record_setting(self, key: str, value: Any) -> None:
        self._execute("INSERT INTO settings_history(key, value, created) VALUES (?, ?, ?)",
                      (key, json.dumps(value, ensure_ascii=False), time.time()))

    def settings_history(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM settings_history ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- Index sémantique
    def upsert_embedding(self, kind: str, ref: str, text: str, model: str, vector: bytes) -> None:
        self._execute("INSERT INTO embeddings(kind, ref, text, model, vector, updated) VALUES (?, ?, ?, ?, ?, ?) "
                      "ON CONFLICT(kind, ref) DO UPDATE SET text = excluded.text, model = excluded.model, "
                      "vector = excluded.vector, updated = excluded.updated",
                      (kind, ref, text[:4000], model, vector, time.time()))

    def embeddings(self, kind: str, model: str) -> list[dict]:
        return self._rows("SELECT ref, text, vector FROM embeddings WHERE kind = ? AND model = ?", (kind, model))

    def embedding_refs(self, kind: str) -> dict[str, float]:
        return {row["ref"]: row["updated"] for row in self._rows("SELECT ref, updated FROM embeddings WHERE kind = ?",
                                                                 (kind,))}

    def delete_embeddings(self, kind: str, refs: list[str]) -> None:
        for ref in refs:
            self._execute("DELETE FROM embeddings WHERE kind = ? AND ref = ?", (kind, ref))
