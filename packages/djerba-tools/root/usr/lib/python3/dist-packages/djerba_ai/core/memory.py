# SPDX-License-Identifier: GPL-3.0-or-later
"""Mémoire permanente de l'agent (SQLite chiffré, ~/.local/share/djerba/memory.db).

* conversations et messages ;
* faits retenus sur l'utilisateur et sa machine (« souviens-toi que… ») ;
* leçons apprises, préférences et retours d'expérience (table ``lessons``) ;
* historique des outils exécutés et retours de l'utilisateur : l'agent s'en
  sert pour mieux classer les outils et se donner des exemples (amélioration
  continue, sans réentraînement) ;
* incidents de l'auto-réparation, historique des réglages ;
* index sémantique et vectoriel (embeddings) ;
* **historique par moteur d'IA** (phase 4) : chaque conversation, réponse,
  action, fait et leçon porte le moteur qui l'a produit ; mémoire « isolée »
  ou « partagée » et rétention réglables par moteur ; recherche plein texte et
  sémantique, épinglage, export, résumé pour la reprise par un autre moteur,
  sauvegarde, restauration et import (Jarvis).

Schéma versionné (``PRAGMA user_version``) : migrations ordonnées, chacune
dans une transaction, précédées d'une sauvegarde automatique de la base.

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
import unicodedata
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from djerba_ai.core import dlp
from djerba_ai.core.config import MEMORY_DB
from djerba_ai.core.crypto import Box

VERSION = 3
#: schéma de base (version 2) ; les colonnes et tables suivantes sont ajoutées par les migrations
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
    "conversations": ("title", "summary"),
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
MEMORY_MODES = ("isole", "partage")
#: moteurs dont la mémoire est partagée par défaut (tout reste sur la machine) ; les autres sont isolés
SHARED_BY_DEFAULT = ("", "local")
EXPORT_FORMAT = "djerba-history/1"
KEEP_BACKUPS = 10


class HistoryError(Exception):
    """Opération d'historique impossible (format, intégrité, clé)."""


def fold(text: str) -> str:
    """Forme de comparaison : minuscules, sans accents, espaces normalisés."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain).strip()


def engine_of(label: str) -> str:
    """Identifiant du moteur à partir d'une étiquette « moteur:modèle »."""
    return (label or "").split(":", 1)[0][:40]


class Memory:
    def __init__(self, path: Path = MEMORY_DB, box: Box | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        existed = path.exists() and path.stat().st_size > 0
        self.lock = threading.RLock()
        self.box = box or Box()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA secure_delete = ON")  # contenu supprimé ou remplacé : écrasé sur le disque
        self.db.executescript(SCHEMA)
        path.chmod(0o600)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > VERSION:
            self.db.close()
            raise HistoryError(f"historique d'une version plus récente de Djerba ({version} > {VERSION})")
        if version < VERSION:
            self._migrate(backup=existed)

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

    def _migrate(self, backup: bool = True) -> None:
        """Applique dans l'ordre les migrations manquantes (une transaction chacune). Avant la première
        migration de schéma, copie intégrale de la base (déjà chiffrée : la migration v2, qui chiffre une
        base antérieure en clair, passe avant pour ne laisser aucune copie en clair)."""
        for version, step in ((2, self._migrate_v2), (3, self._migrate_v3)):
            current = self.db.execute("PRAGMA user_version").fetchone()[0]
            if current < version:
                if backup and version >= 3:
                    self.backup(automatic=f"avant-migration-v{current}")
                    backup = False
                step()

    def _migrate_v2(self) -> None:
        """Chiffre (et passe au DLP) les données d'une base antérieure en clair."""
        with self.transaction() as db:
            for table, encrypted in ENCRYPTED.items():
                present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
                columns = tuple(column for column in encrypted if column in present)  # colonnes de la v2
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
            db.execute("PRAGMA user_version = 2")
        # Aucune trace en clair ne doit subsister dans les pages libres ni dans le journal WAL.
        with self.lock:
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _migrate_v3(self) -> None:
        """Historique par moteur : clé de session, moteur, épinglage, résumé, mémoire par moteur."""
        with self.transaction() as db:
            for table, column in (("conversations", "key TEXT"), ("conversations", "engine TEXT NOT NULL DEFAULT ''"),
                                  ("conversations", "pinned INTEGER NOT NULL DEFAULT 0"),
                                  ("conversations", "summary TEXT"),
                                  ("conversations", "summary_upto INTEGER NOT NULL DEFAULT 0"),
                                  ("conversations", "source TEXT NOT NULL DEFAULT ''"),
                                  ("messages", "engine TEXT NOT NULL DEFAULT ''"),
                                  ("facts", "engine TEXT NOT NULL DEFAULT ''"),
                                  ("lessons", "engine TEXT NOT NULL DEFAULT ''"),
                                  ("tool_runs", "conversation INTEGER"),
                                  ("tool_runs", "engine TEXT NOT NULL DEFAULT ''"), ("tool_runs", "tx TEXT")):
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            for row in db.execute("SELECT id FROM conversations").fetchall():
                db.execute("UPDATE conversations SET key = ? WHERE id = ?", (uuid.uuid4().hex, row["id"]))
            # Une instruction à la fois : executescript validerait la transaction en cours.
            for statement in (
                    "CREATE UNIQUE INDEX IF NOT EXISTS conversations_key ON conversations(key)",
                    "CREATE UNIQUE INDEX IF NOT EXISTS conversations_source ON conversations(source) "
                    "WHERE source != ''",
                    "CREATE INDEX IF NOT EXISTS messages_engine ON messages(engine)",
                    "CREATE INDEX IF NOT EXISTS tool_runs_conversation ON tool_runs(conversation)",
                    "CREATE TABLE IF NOT EXISTS engine_memory (engine TEXT PRIMARY KEY, mode TEXT NOT NULL, "
                    "retention_days INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL)"):
                db.execute(statement)
            db.execute("PRAGMA user_version = 3")

    # ---------------------------------------------------------------- Sauvegarde et restauration
    def backup(self, destination: Path | None = None, automatic: str = "") -> Path:
        """Copie cohérente de la base (API de sauvegarde SQLite), fichier 0600, toujours chiffrée."""
        folder = self.path.parent / "backups"
        if destination is None:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            destination = folder / f"memory-{stamp}-{automatic or 'manuelle'}-{uuid.uuid4().hex[:6]}.db"
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = sqlite3.connect(destination)
        try:
            destination.chmod(0o600)
            with self.lock:
                self.db.backup(target)
        finally:
            target.close()
        if automatic:
            for old in sorted(folder.glob("memory-*.db"))[:-KEEP_BACKUPS]:
                old.unlink(missing_ok=True)
        return destination

    def restore(self, source: Path) -> dict[str, Any]:
        """Restaure une sauvegarde après contrôle d'intégrité, de version et de clé ; l'état courant est
        d'abord sauvegardé (restauration elle-même annulable)."""
        source = Path(source)
        if not source.is_file():
            raise HistoryError(f"sauvegarde introuvable : {source}")
        from djerba_ai.core.crypto import CryptoError
        candidate = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            candidate.row_factory = sqlite3.Row
            try:
                if candidate.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise HistoryError("sauvegarde corrompue (contrôle d'intégrité en échec)")
                version = candidate.execute("PRAGMA user_version").fetchone()[0]
                sample = candidate.execute("SELECT title FROM conversations WHERE title LIKE 'enc1:%' "
                                           "LIMIT 1").fetchone()
            except sqlite3.DatabaseError as exc:
                raise HistoryError(f"sauvegarde illisible : {exc}") from exc
            if version > VERSION:
                raise HistoryError(f"sauvegarde d'une version plus récente de Djerba ({version})")
            if sample is not None:
                try:
                    self.box.decrypt(sample["title"], aad="conversations.title")
                except CryptoError as exc:
                    raise HistoryError("sauvegarde chiffrée avec une autre clé : restauration refusée") from exc
            safety = self.backup(automatic="avant-restauration")
            with self.lock:
                candidate.backup(self.db)
        finally:
            candidate.close()
        with self.lock:
            if self.db.execute("PRAGMA user_version").fetchone()[0] < VERSION:
                self._migrate(backup=False)
            count = self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        return {"success": True, "restored": str(source), "safety": str(safety), "conversations": count,
                "message": f"historique restauré ({count} conversation(s)) ; état précédent sauvegardé : {safety}"}

    # ---------------------------------------------------------------- Mémoire par moteur
    def engine_memory(self, engine: str) -> dict[str, Any]:
        engine = engine_of(engine)
        with self.lock:
            row = self.db.execute("SELECT * FROM engine_memory WHERE engine = ?", (engine,)).fetchone()
        if row:
            return dict(row)
        return {"engine": engine, "mode": "partage" if engine in SHARED_BY_DEFAULT else "isole",
                "retention_days": 0, "updated": 0}

    def set_engine_memory(self, engine: str, mode: str | None = None, retention_days: int | None = None) -> dict:
        current = self.engine_memory(engine)
        mode = mode or current["mode"]
        if mode not in MEMORY_MODES:
            raise HistoryError(f"mode de mémoire inconnu : {mode} (isole ou partage)")
        days = current["retention_days"] if retention_days is None else max(0, int(retention_days))
        with self.transaction() as db:
            db.execute("INSERT INTO engine_memory(engine, mode, retention_days, updated) VALUES (?, ?, ?, ?) "
                       "ON CONFLICT(engine) DO UPDATE SET mode = excluded.mode, "
                       "retention_days = excluded.retention_days, updated = excluded.updated",
                       (current["engine"], mode, days, time.time()))
        return self.engine_memory(engine)

    def engine_memories(self) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM engine_memory ORDER BY engine").fetchall()]

    def _scope(self, engine: str | None) -> tuple[str, tuple]:
        """Filtre SQL de la mémoire visible par un moteur (None : toute la mémoire)."""
        if engine is None:
            return "1", ()
        engine = engine_of(engine)
        if self.engine_memory(engine)["mode"] == "isole":
            return "engine = ?", (engine,)
        return "engine IN ('', ?)", (engine,)

    def _owner(self, engine: str | None) -> str:
        """Propriétaire d'une écriture : le moteur s'il est isolé, la mémoire commune sinon."""
        if not engine:
            return ""
        engine = engine_of(engine)
        return engine if self.engine_memory(engine)["mode"] == "isole" else ""

    # ---------------------------------------------------------------- Conversations
    @staticmethod
    def open_session(tainted: bool = False, reason: str = "", channel: str = "agent") -> str:
        """Clé de conversation délivrée par le démon root (registre de provenance). Sans démon : clé locale,
        inconnue de root, donc présumée non fiable par la politique (fail-closed)."""
        from djerba_ai.core import ipc
        try:
            return str(ipc.call("session.open", {"channel": channel, "tainted": tainted, "reason": reason[:160]},
                                timeout=30)["session"])
        except (ipc.RPCError, KeyError, TypeError):
            return uuid.uuid4().hex

    def new_conversation(self, title: str, engine: str = "", source: str = "", created: float | None = None,
                         key: str | None = None, tainted: bool = False) -> int:
        now = time.time()
        key = key or self.open_session(tainted, f"conversation importée ({source})" if tainted else "",
                                       "import" if tainted else "agent")
        with self.transaction():
            return self._insert("conversations", {"title": title[:80] or "Discussion", "created": created or now,
                                                  "updated": created or now, "key": key,
                                                  "engine": engine_of(engine), "source": source[:120]})

    def conversation(self, conversation: int) -> dict | None:
        rows = self._rows("conversations", "SELECT * FROM conversations WHERE id = ?", (conversation,))
        return rows[0] if rows else None

    def conversations(self, limit: int = 50, engine: str | None = None) -> list[dict]:
        """Conversations (épinglées d'abord) ; ``engine`` : celles qu'un moteur a ouvertes ou alimentées."""
        if engine is None:
            return self._rows("conversations", "SELECT * FROM conversations ORDER BY pinned DESC, updated DESC "
                              "LIMIT ?", (limit,))
        engine = engine_of(engine)
        return self._rows("conversations", "SELECT * FROM conversations c WHERE c.engine = ? OR EXISTS "
                          "(SELECT 1 FROM messages m WHERE m.conversation = c.id AND m.engine = ?) "
                          "ORDER BY pinned DESC, updated DESC LIMIT ?", (engine, engine, limit))

    def engines_used(self, conversation: int) -> list[str]:
        with self.lock:
            rows = self.db.execute("SELECT DISTINCT engine FROM messages WHERE conversation = ? AND engine != '' "
                                   "ORDER BY id", (conversation,)).fetchall()
        return [row["engine"] for row in rows]

    def rename_conversation(self, conversation: int, title: str) -> bool:
        title = title.strip()[:80]
        if not title:
            raise HistoryError("titre vide")
        with self.transaction() as db:
            return db.execute("UPDATE conversations SET title = ? WHERE id = ?",
                              (self._protect("conversations", "title", title), conversation)).rowcount > 0

    def pin_conversation(self, conversation: int, pinned: bool = True) -> bool:
        with self.transaction() as db:
            return db.execute("UPDATE conversations SET pinned = ? WHERE id = ?",
                              (int(pinned), conversation)).rowcount > 0

    def add_message(self, conversation: int, role: str, content: str, tool: str | None = None,
                    engine: str = "", created: float | None = None) -> None:
        now = created or time.time()
        engine = engine_of(engine)
        with self.transaction() as db:
            self._insert("messages", {"conversation": conversation, "role": role, "content": content, "tool": tool,
                                      "created": now, "engine": engine})
            db.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, conversation))
            if engine:  # première réponse : le moteur d'origine de la conversation
                db.execute("UPDATE conversations SET engine = ? WHERE id = ? AND engine = ''", (engine, conversation))

    def messages(self, conversation: int, limit: int = 40) -> list[dict]:
        rows = self._rows("messages", "SELECT * FROM messages WHERE conversation = ? ORDER BY id DESC LIMIT ?",
                          (conversation, limit))
        return list(reversed(rows))

    def delete_conversation(self, conversation: int) -> None:
        with self.lock:
            refs = [str(row["id"]) for row in
                    self.db.execute("SELECT id FROM messages WHERE conversation = ?", (conversation,)).fetchall()]
        with self.transaction() as db:
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation,))
            db.execute("UPDATE tool_runs SET conversation = NULL WHERE conversation = ?", (conversation,))
        if refs:
            self.delete_embeddings("message", refs)

    def actions(self, conversation: int) -> list[dict]:
        """Actions menées dans une conversation (outil, moteur, résultat, transaction root éventuelle)."""
        return self._rows("tool_runs", "SELECT id, tool, params, success, message, engine, tx, created "
                          "FROM tool_runs WHERE conversation = ? ORDER BY id", (conversation,))

    # ---------------------------------------------------------------- Continuité entre moteurs
    def context(self, conversation: int, budget: int, max_messages: int = 40) -> tuple[str, list[dict]]:
        """(résumé, messages récents) pour un moteur dont l'historique tient en ``budget`` caractères :
        derniers échanges intégraux, plus anciens condensés (résumé extractif déterministe, chiffré, en
        cache). Le dernier message (la demande en cours) est exclu."""
        rows = [row for row in self.messages(conversation, limit=1000) if row["role"] in ("user", "assistant")]
        rows = rows[:-1]
        recent: list[dict] = []
        used = 0
        for row in reversed(rows):
            size = len(row["content"])
            if recent and (used + size > budget * 3 // 4 or len(recent) >= max_messages):
                break
            recent.insert(0, row)
            used += size
        older = rows[:len(rows) - len(recent)]
        if not older:
            return "", recent
        info = self.conversation(conversation) or {}
        if info.get("summary") and info.get("summary_upto") == older[-1]["id"]:
            return info["summary"], recent
        lines = []
        for row in older:
            text = re.sub(r"\s+", " ", row["content"]).strip()
            who = "Utilisateur" if row["role"] == "user" else f"Réponse ({row.get('engine') or 'Djerba'})"
            lines.append(f"- {who} : {text[:160]}{'…' if len(text) > 160 else ''}")
        # Toujours conservés : la demande d'origine et les actions menées (absentes des messages récents).
        actions = self.actions(conversation)
        footer = ("- Actions déjà menées : " + ", ".join(
            f"{row['tool']} ({'réussi' if row['success'] else 'échec'})" for row in actions[-12:])) if actions else ""
        limit = max(400, budget // 4)
        head, middle = lines[:1], lines[1:]

        def assemble() -> str:
            gap = ["- … (échanges intermédiaires omis)"] if len(middle) < len(lines) - 1 else []
            return "\n".join(head + gap + middle + ([footer] if footer else []))
        summary = assemble()
        while len(summary) > limit and middle:
            middle.pop(0)  # le milieu, du plus ancien au plus récent
            summary = assemble()
        summary = summary[:limit]
        with self.transaction() as db:
            db.execute("UPDATE conversations SET summary = ?, summary_upto = ? WHERE id = ?",
                       (self._protect("conversations", "summary", summary), older[-1]["id"], conversation))
        return summary, recent

    # ---------------------------------------------------------------- Recherche
    def search(self, query: str, engine: str | None = None, limit: int = 20) -> list[dict]:
        """Recherche plein texte (titres et messages, insensible à la casse et aux accents)."""
        needles = fold(query).split()
        if not needles:
            return []
        allowed = None if engine is None else {row["id"] for row in self.conversations(10000, engine)}
        titles = {row["id"]: row for row in self.conversations(10000)}
        hits: list[dict] = []
        for conversation_id, row in titles.items():
            if (allowed is None or conversation_id in allowed) and all(n in fold(row["title"]) for n in needles):
                hits.append(self._hit(row, None, row["title"], 2.0))
        for message in self._rows("messages", "SELECT * FROM messages ORDER BY id DESC LIMIT 20000"):
            info = titles.get(message["conversation"])
            if info is None or (allowed is not None and message["conversation"] not in allowed):
                continue
            folded = fold(message["content"])
            if all(n in folded for n in needles):
                hits.append(self._hit(info, message, message["content"], 1.0 + sum(folded.count(n) for n in needles)
                                      / 100))
        hits.sort(key=lambda hit: (-hit["score"], -hit["created"]))
        return hits[:limit]

    @staticmethod
    def _hit(conversation: dict, message: dict | None, text: str, score: float) -> dict:
        clean = re.sub(r"\s+", " ", text).strip()
        return {"conversation": conversation["id"], "title": conversation["title"], "engine":
                (message or {}).get("engine") or conversation.get("engine", ""), "role": (message or {}).get("role", ""),
                "message": (message or {}).get("id"), "snippet": clean[:200], "score": round(score, 3),
                "created": (message or conversation)["created"]}

    def index_history(self, embed: Callable[[list[str]], tuple[str, list[list[float]]]], batch: int = 32) -> int:
        """Indexe sémantiquement les messages non encore indexés (embeddings locaux)."""
        from djerba_ai.core.embeddings import pack
        known = self.embedding_refs("message")
        pending = [row for row in self._rows("messages", "SELECT id, content FROM messages ORDER BY id")
                   if str(row["id"]) not in known and row["content"].strip()]
        for start in range(0, len(pending), batch):
            chunk = pending[start:start + batch]
            model, vectors = embed([row["content"][:2000] for row in chunk])
            for row, vector in zip(chunk, vectors):
                self.upsert_embedding("message", str(row["id"]), row["content"][:400], model, pack(vector))
        return len(pending)

    def semantic_search(self, query: str, embed: Callable[[list[str]], tuple[str, list[list[float]]]],
                        engine: str | None = None, limit: int = 10) -> list[dict]:
        """Recherche sémantique (similarité cosinus) dans les messages indexés."""
        from djerba_ai.core.embeddings import cosine, unpack
        self.index_history(embed)
        model, vectors = embed([query])
        allowed = None if engine is None else {row["id"] for row in self.conversations(10000, engine)}
        titles = {row["id"]: row for row in self.conversations(10000)}
        scored = []
        for entry in self.embeddings("message", model):
            rows = self._rows("messages", "SELECT * FROM messages WHERE id = ?", (int(entry["ref"]),))
            if not rows or rows[0]["conversation"] not in titles:
                continue
            if allowed is not None and rows[0]["conversation"] not in allowed:
                continue
            scored.append(self._hit(titles[rows[0]["conversation"]], rows[0], rows[0]["content"],
                                    cosine(vectors[0], unpack(entry["vector"]))))
        scored.sort(key=lambda hit: -hit["score"])
        return scored[:limit]

    # ---------------------------------------------------------------- Export, import, rétention
    def export(self, conversation: int, fmt: str = "markdown") -> str:
        info = self.conversation(conversation)
        if info is None:
            raise HistoryError(f"conversation introuvable : {conversation}")
        messages = self.messages(conversation, limit=100000)
        actions = self.actions(conversation)
        if fmt == "json":
            return json.dumps({
                "format": EXPORT_FORMAT,
                "conversation": {key: info[key] for key in ("key", "title", "engine", "pinned", "created", "updated",
                                                             "source")},
                "messages": [{"role": row["role"], "content": row["content"], "engine": row["engine"],
                              "tool": row["tool"], "created": row["created"]} for row in messages],
                "actions": [{"tool": row["tool"], "success": bool(row["success"]), "message": row["message"],
                             "engine": row["engine"], "transaction": row["tx"], "created": row["created"]}
                            for row in actions]}, ensure_ascii=False, indent=2)
        if fmt != "markdown":
            raise HistoryError(f"format d'export inconnu : {fmt} (markdown ou json)")
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(info["created"]))
        engines = ", ".join(self.engines_used(conversation)) or info["engine"] or "—"
        lines = [f"# {info['title']}", "", f"*Moteurs : {engines} · créée le {when}*", ""]
        for row in messages:
            who = "Vous" if row["role"] == "user" else (row["engine"] or "Djerba")
            lines += [f"**{who}** : {row['content']}", ""]
        if actions:
            lines += ["## Actions", ""]
            lines += [f"- {'✔' if row['success'] else '✘'} `{row['tool']}` ({row['engine'] or '?'}) — "
                      f"{row['message']}" + (f" · transaction {row['tx']}" if row["tx"] else "") for row in actions]
        return "\n".join(lines).rstrip() + "\n"

    def import_history(self, data: Any, engine: str = "jarvis", source: str = "jarvis") -> dict[str, Any]:
        """Importe des conversations (contrat Jarvis §4 ou export Djerba) ; dédoublonnage par origine."""
        if isinstance(data, dict) and data.get("format") == EXPORT_FORMAT:
            info = data.get("conversation") or {}
            data = {"conversations": [{"id": info.get("key"), "title": info.get("title"), "created":
                                       info.get("created"), "messages": data.get("messages")}]}
        # Clés de sessions non fiables ouvertes par root lors d'un import par le relais (contenu externe) ;
        # import d'un fichier : session non fiable demandée ici.
        sessions = data.get("sessions") if isinstance(data, dict) and isinstance(data.get("sessions"), dict) else {}
        conversations = data.get("conversations") if isinstance(data, dict) else data
        if not isinstance(conversations, list):
            raise HistoryError("format d'historique inconnu : liste « conversations » attendue")
        imported = skipped = 0
        for item in conversations:
            if not isinstance(item, dict) or not isinstance(item.get("messages"), list):
                raise HistoryError("conversation invalide : « messages » attendu")
            origin = f"{source}:{item.get('id') or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16]}"
            with self.lock:
                if self.db.execute("SELECT 1 FROM conversations WHERE source = ?", (origin[:120],)).fetchone():
                    skipped += 1
                    continue
            created = float(item.get("created") or time.time())
            key = sessions.get(str(item.get("id")))
            conversation = self.new_conversation(str(item.get("title") or "Conversation importée"), engine=engine,
                                                 source=origin, created=created,
                                                 key=key if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{32}", key)
                                                 else None, tainted=True)
            for message in item["messages"]:
                role = message.get("role") if isinstance(message, dict) else None
                if role not in ("user", "assistant") or not isinstance(message.get("content"), str):
                    continue
                self.add_message(conversation, role, message["content"], engine=engine if role == "assistant" else "",
                                 created=float(message.get("created") or created))
            imported += 1
        return {"success": True, "imported": imported, "skipped": skipped,
                "message": f"{imported} conversation(s) importée(s), {skipped} déjà présente(s)"}

    def prune(self, now: float | None = None) -> int:
        """Rétention par moteur : supprime les conversations non épinglées plus anciennes que la durée
        réglée pour leur moteur d'origine (0 = conservation illimitée)."""
        now = now or time.time()
        doomed = []
        for setting in self.engine_memories():
            if setting["retention_days"] > 0:
                limit = now - setting["retention_days"] * 86400
                with self.lock:
                    doomed += [row["id"] for row in self.db.execute(
                        "SELECT id FROM conversations WHERE engine = ? AND pinned = 0 AND updated < ?",
                        (setting["engine"], limit)).fetchall()]
        for conversation in doomed:
            self.delete_conversation(conversation)
        return len(doomed)

    # ---------------------------------------------------------------- Faits
    def remember(self, subject: str, content: str, engine: str = "") -> int:
        with self.transaction():
            return self._insert("facts", {"subject": subject[:80], "content": content[:1000], "created": time.time(),
                                          "engine": self._owner(engine)})

    def facts(self, query: str = "", limit: int = 30, engine: str | None = None) -> list[dict]:
        """Faits visibles par ``engine`` (mémoire commune et/ou la sienne selon son mode ; None : tous)."""
        where, args = self._scope(engine)
        rows = self._rows("facts", f"SELECT * FROM facts WHERE {where} ORDER BY id DESC LIMIT 1000", args)
        if query:
            needle = query.lower()
            rows = [row for row in rows if needle in row["subject"].lower() or needle in row["content"].lower()]
        return rows[:limit]

    def forget(self, fact_id: int) -> bool:
        with self.transaction() as db:
            return db.execute("DELETE FROM facts WHERE id = ?", (fact_id,)).rowcount > 0

    # ---------------------------------------------------------------- Leçons et préférences
    def learn(self, kind: str, content: str, source: str = "", weight: float = 1.0, engine: str = "") -> int:
        if kind not in LESSON_KINDS:
            raise ValueError(f"type de leçon inconnu : {kind}")
        with self.transaction():
            return self._insert("lessons", {"kind": kind, "content": content[:600], "source": source[:200],
                                            "weight": weight, "created": time.time(), "engine": self._owner(engine)})

    def lessons(self, kind: str | None = None, limit: int = 20, engine: str | None = None) -> list[dict]:
        where, args = self._scope(engine)
        if kind:
            return self._rows("lessons", f"SELECT * FROM lessons WHERE kind = ? AND {where} "
                              "ORDER BY weight DESC, id DESC LIMIT ?", (kind, *args, limit))
        return self._rows("lessons", f"SELECT * FROM lessons WHERE {where} ORDER BY weight DESC, id DESC LIMIT ?",
                          (*args, limit))

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
    def record_run(self, tool: str, request: str, params: dict, success: bool, message: str,
                   conversation: int | None = None, engine: str = "", tx: str | None = None) -> int:
        with self.transaction():
            return self._insert("tool_runs", {"tool": tool, "request": request[:500],
                                              "params": json.dumps(params, ensure_ascii=False)[:2000],
                                              "success": int(success), "message": message[:500],
                                              "created": time.time(), "conversation": conversation,
                                              "engine": engine_of(engine), "tx": tx})

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
