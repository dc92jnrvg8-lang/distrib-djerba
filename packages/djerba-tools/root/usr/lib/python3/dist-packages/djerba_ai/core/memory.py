# SPDX-License-Identifier: GPL-3.0-or-later
"""Mémoire permanente de l'agent (SQLite chiffré, ~/.local/share/djerba/memory.db).

* conversations et messages ;
* faits retenus sur l'utilisateur et sa machine (« souviens-toi que… ») ;
* leçons apprises, préférences et retours d'expérience (table ``lessons``) ;
* historique des outils exécutés et retours de l'utilisateur : l'agent s'en
  sert pour mieux classer les outils et se donner des exemples (amélioration
  continue, sans réentraînement) ;
* incidents de l'auto-réparation, historique des réglages ;
* index sémantique et vectoriel (embeddings).

Sécurité :

* **DLP à deux niveaux** (core.dlp) sur toute chaîne écrite : un secret
  confirmé est masqué dans le texte et sa valeur chiffrée dans le coffre
  ``vault`` (jamais renvoyée au modèle) ;
* **chiffrement** AES-256-GCM (core.crypto) de chaque contenu textuel et de
  chaque vecteur, lié à sa table et sa colonne ; seuls identifiants,
  horodatages, noms d'outils et états restent en clair (tri, jointures) ;
* écritures **transactionnelles** (``BEGIN IMMEDIATE``), journal WAL,
  fichier 0600. Les bases antérieures en clair sont migrées à l'ouverture.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from djerba_ai.core import dlp
from djerba_ai.core.config import MEMORY_DB
from djerba_ai.core.crypto import Box

VERSION = 2
SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, conversation INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, tool TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY, subject TEXT NOT NULL, content TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, content TEXT NOT NULL, source TEXT, weight REAL NOT NULL DEFAULT 1,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS vault (
    id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, secret TEXT NOT NULL,
    origin TEXT, created REAL NOT NULL);
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
CREATE INDEX IF NOT EXISTS lessons_kind ON lessons(kind);
"""
#: colonnes textuelles chiffrées, par table
ENCRYPTED = {
    "conversations": ("title",),
    "messages": ("content",),
    "facts": ("subject", "content"),
    "lessons": ("content", "source"),
    "vault": ("secret", "origin"),
    "tool_runs": ("request", "params", "message"),
    "incidents": ("message", "patch"),
    "settings_history": ("value",),
    "embeddings": ("text",),
}
LESSON_KINDS = ("leçon", "préférence", "retour")


class Memory:
    def __init__(self, path: Path = MEMORY_DB, box: Box | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.box = box or Box()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA secure_delete = ON")  # contenu supprimé ou remplacé : écrasé sur le disque
        self.db.executescript(SCHEMA)
        path.chmod(0o600)
        if self.db.execute("PRAGMA user_version").fetchone()[0] < VERSION:
            self._migrate()

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # ---------------------------------------------------------------- Chiffrement et DLP
    def _protect(self, table: str, column: str, value: Any, origin: str = "") -> Any:
        """DLP (masquage + coffre) puis chiffrement d'une valeur textuelle."""
        if value is None:
            return None
        text = value if isinstance(value, str) else str(value)
        if table != "vault":
            report = dlp.analyze(text)
            for finding in report.secrets:
                self._vault(finding, origin or f"{table}.{column}")
            text = report.text
        return self.box.encrypt(text, aad=f"{table}.{column}")

    def _reveal(self, table: str, row: sqlite3.Row | dict) -> dict:
        data = dict(row)
        for column in ENCRYPTED.get(table, ()):
            if column in data and isinstance(data[column], str):
                data[column] = self.box.decrypt(data[column], aad=f"{table}.{column}")
        if table == "embeddings" and isinstance(data.get("vector"), bytes) and data["vector"][:1] == b"\x01":
            data["vector"] = self.box.decrypt_bytes(data["vector"][1:], aad="embeddings.vector")
        return data

    def _vault(self, finding: dlp.Finding, origin: str) -> None:
        fingerprint = hashlib.sha256(finding.value.encode()).hexdigest()
        self.db.execute("INSERT OR IGNORE INTO vault(fingerprint, kind, secret, origin, created) VALUES (?, ?, ?, ?, ?)",
                        (fingerprint, finding.kind, self.box.encrypt(finding.value, aad="vault.secret"),
                         self.box.encrypt(origin, aad="vault.origin"), time.time()))
        from djerba_ai.core import telemetry
        telemetry.inc("djerba_dlp_secrets_total", kind=finding.kind)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Transaction atomique (BEGIN IMMEDIATE … COMMIT, ROLLBACK en cas d'erreur)."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def _insert(self, table: str, values: dict) -> int:
        protected = {key: (self._protect(table, key, value) if key in ENCRYPTED.get(table, ()) else value)
                     for key, value in values.items()}
        columns = ", ".join(protected)
        marks = ", ".join("?" for _ in protected)
        return self.db.execute(f"INSERT INTO {table}({columns}) VALUES ({marks})", tuple(protected.values())).lastrowid

    def _rows(self, table: str, query: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [self._reveal(table, row) for row in self.db.execute(query, args).fetchall()]

    def _migrate(self) -> None:
        """Chiffre (et passe au DLP) les données d'une base antérieure en clair."""
        with self.transaction() as db:
            for table, columns in ENCRYPTED.items():
                for row in db.execute(f"SELECT id, {', '.join(columns)} FROM {table}").fetchall():
                    updates = {column: self._protect(table, column, row[column]) for column in columns
                               if isinstance(row[column], str) and not row[column].startswith("enc1:")}
                    if updates:
                        assignments = ", ".join(f"{column} = ?" for column in updates)
                        db.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*updates.values(), row["id"]))
            for row in db.execute("SELECT id, vector FROM embeddings").fetchall():
                if isinstance(row["vector"], bytes) and row["vector"][:1] != b"\x01":
                    db.execute("UPDATE embeddings SET vector = ? WHERE id = ?",
                               (b"\x01" + self.box.encrypt_bytes(row["vector"], aad="embeddings.vector"), row["id"]))
            db.execute(f"PRAGMA user_version = {VERSION}")
        # Aucune trace en clair ne doit subsister dans les pages libres ni dans le journal WAL.
        with self.lock:
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ---------------------------------------------------------------- Conversations
    def new_conversation(self, title: str) -> int:
        now = time.time()
        with self.transaction():
            return self._insert("conversations", {"title": title[:80] or "Discussion", "created": now, "updated": now})

    def conversations(self, limit: int = 50) -> list[dict]:
        return self._rows("conversations", "SELECT * FROM conversations ORDER BY updated DESC LIMIT ?", (limit,))

    def add_message(self, conversation: int, role: str, content: str, tool: str | None = None) -> None:
        now = time.time()
        with self.transaction() as db:
            self._insert("messages", {"conversation": conversation, "role": role, "content": content, "tool": tool,
                                      "created": now})
            db.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, conversation))

    def messages(self, conversation: int, limit: int = 40) -> list[dict]:
        rows = self._rows("messages", "SELECT * FROM messages WHERE conversation = ? ORDER BY id DESC LIMIT ?",
                          (conversation, limit))
        return list(reversed(rows))

    def delete_conversation(self, conversation: int) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation,))

    # ---------------------------------------------------------------- Faits
    def remember(self, subject: str, content: str) -> int:
        with self.transaction():
            return self._insert("facts", {"subject": subject[:80], "content": content[:1000], "created": time.time()})

    def facts(self, query: str = "", limit: int = 30) -> list[dict]:
        rows = self._rows("facts", "SELECT * FROM facts ORDER BY id DESC LIMIT 1000")
        if query:
            needle = query.lower()
            rows = [row for row in rows if needle in row["subject"].lower() or needle in row["content"].lower()]
        return rows[:limit]

    def forget(self, fact_id: int) -> bool:
        with self.transaction() as db:
            return db.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    # ---------------------------------------------------------------- Leçons et préférences
    def learn(self, kind: str, content: str, source: str = "", weight: float = 1.0) -> int:
        if kind not in LESSON_KINDS:
            raise ValueError(f"type de leçon inconnu : {kind}")
        with self.transaction():
            return self._insert("lessons", {"kind": kind, "content": content[:600], "source": source[:200],
                                            "weight": weight, "created": time.time()})

    def lessons(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        if kind:
            return self._rows("lessons", "SELECT * FROM lessons WHERE kind = ? ORDER BY weight DESC, id DESC LIMIT ?",
                              (kind, limit))
        return self._rows("lessons", "SELECT * FROM lessons ORDER BY weight DESC, id DESC LIMIT ?", (limit,))

    def forget_lesson(self, lesson_id: int) -> bool:
        with self.transaction() as db:
            return db.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,)).rowcount > 0

    # ---------------------------------------------------------------- Coffre DLP
    def vault_entries(self, limit: int = 100) -> list[dict]:
        """Métadonnées du coffre (jamais la valeur du secret)."""
        rows = self._rows("vault", "SELECT id, fingerprint, kind, origin, created FROM vault ORDER BY id DESC LIMIT ?",
                          (limit,))
        return [{**row, "fingerprint": row["fingerprint"][:12]} for row in rows]

    # ---------------------------------------------------------------- Apprentissage
    def record_run(self, tool: str, request: str, params: dict, success: bool, message: str) -> int:
        with self.transaction():
            return self._insert("tool_runs", {"tool": tool, "request": request[:500],
                                              "params": json.dumps(params, ensure_ascii=False)[:2000],
                                              "success": int(success), "message": message[:500],
                                              "created": time.time()})

    def feedback(self, run_id: int, value: int) -> None:
        value = max(-1, min(1, value))
        with self.transaction() as db:
            db.execute("UPDATE tool_runs SET feedback = ? WHERE id = ?", (value, run_id))
        if value < 0:
            run = self._rows("tool_runs", "SELECT tool, request FROM tool_runs WHERE id = ?", (run_id,))
            if run:
                self.learn("retour", f"« {run[0]['tool']} » n'a pas convenu pour : {run[0]['request'][:160]}",
                           source=f"retour utilisateur sur l'action {run_id}", weight=0.5)

    def tool_scores(self) -> dict[str, float]:
        """Bonus de classement par outil : réussites, retours positifs, échecs."""
        with self.lock:
            rows = self.db.execute("SELECT tool, SUM(success) AS ok, COUNT(*) AS n, SUM(feedback) AS fb "
                                   "FROM tool_runs GROUP BY tool").fetchall()
        return {row["tool"]: (row["ok"] / row["n"]) * min(row["n"], 10) / 10 + 0.5 * (row["fb"] or 0)
                for row in rows}

    def examples(self, request: str, limit: int = 3) -> list[dict]:
        """Requêtes passées proches ayant réussi (exemples pour l'agent)."""
        words = {word for word in re.findall(r"\w{4,}", request.lower())}
        if not words:
            return []
        candidates = self._rows("tool_runs", "SELECT tool, request, params FROM tool_runs WHERE success = 1 "
                                "AND feedback >= 0 ORDER BY id DESC LIMIT 400")
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
        with self.lock:
            runs = self.db.execute("SELECT COUNT(*) AS n, SUM(success) AS ok FROM tool_runs").fetchone()
            counts = {table: self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("facts", "lessons", "vault")}
        return {"tool_runs": runs["n"] or 0, "successes": runs["ok"] or 0, **counts}

    # ---------------------------------------------------------------- Incidents
    def record_incident(self, fingerprint: str, source: str, target: str, message: str) -> tuple[int, bool]:
        now = time.time()
        with self.transaction() as db:
            existing = db.execute("SELECT id FROM incidents WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if existing:
                db.execute("UPDATE incidents SET occurrences = occurrences + 1, updated = ?, message = ? WHERE id = ?",
                           (now, self._protect("incidents", "message", message[:4000]), existing["id"]))
                return existing["id"], False
            return self._insert("incidents", {"fingerprint": fingerprint, "source": source, "target": target,
                                              "message": message[:4000], "created": now, "updated": now}), True

    def incidents(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            return self._rows("incidents", "SELECT * FROM incidents WHERE status = ? ORDER BY updated DESC LIMIT ?",
                              (status, limit))
        return self._rows("incidents", "SELECT * FROM incidents ORDER BY updated DESC LIMIT ?", (limit,))

    def incident(self, incident_id: int) -> dict | None:
        rows = self._rows("incidents", "SELECT * FROM incidents WHERE id = ?", (incident_id,))
        return rows[0] if rows else None

    def update_incident(self, incident_id: int, status: str, patch: str | None = None) -> None:
        with self.transaction() as db:
            db.execute("UPDATE incidents SET status = ?, patch = COALESCE(?, patch), updated = ? WHERE id = ?",
                       (status, self._protect("incidents", "patch", patch), time.time(), incident_id))

    # ---------------------------------------------------------------- Réglages
    def record_setting(self, key: str, value: Any) -> None:
        with self.transaction():
            self._insert("settings_history", {"key": key, "value": json.dumps(value, ensure_ascii=False),
                                              "created": time.time()})

    def settings_history(self, limit: int = 50) -> list[dict]:
        return self._rows("settings_history", "SELECT * FROM settings_history ORDER BY id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- Index sémantique et vectoriel
    def upsert_embedding(self, kind: str, ref: str, text: str, model: str, vector: bytes) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO embeddings(kind, ref, text, model, vector, updated) VALUES (?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(kind, ref) DO UPDATE SET text = excluded.text, model = excluded.model, "
                       "vector = excluded.vector, updated = excluded.updated",
                       (kind, ref, self._protect("embeddings", "text", text[:4000]), model,
                        b"\x01" + self.box.encrypt_bytes(vector, aad="embeddings.vector"), time.time()))

    def embeddings(self, kind: str, model: str) -> list[dict]:
        return self._rows("embeddings", "SELECT ref, text, vector FROM embeddings WHERE kind = ? AND model = ?",
                          (kind, model))

    def embedding_refs(self, kind: str) -> dict[str, float]:
        with self.lock:
            rows = self.db.execute("SELECT ref, updated FROM embeddings WHERE kind = ?", (kind,)).fetchall()
        return {row["ref"]: row["updated"] for row in rows}

    def delete_embeddings(self, kind: str, refs: list[str]) -> None:
        with self.transaction() as db:
            db.executemany("DELETE FROM embeddings WHERE kind = ? AND ref = ?", [(kind, ref) for ref in refs])
