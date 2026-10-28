# SPDX-License-Identifier: GPL-3.0-or-later
"""Journal de transactions du démon root : toute action qui modifie le système
est sauvegardée, tracée, vérifiée et annulable.

Déroulé d'une transaction (``Journal.run``) :

1. sondes de santé « avant » (``core.probes``) et liste des paquets ;
2. sauvegarde : instantané Btrfs ou copie de fichiers (``core.snapshots``) —
   si elle échoue, l'action n'est **pas** exécutée ;
3. exécution de l'action ;
4. relevé des chemins réellement touchés et du delta de paquets ;
5. sondes « après » : en cas de **régression**, ou si l'action a échoué en
   laissant des modifications partielles, la transaction est **annulée
   automatiquement** (tout ou rien), puis l'état est revérifié ;
6. enregistrement : identifiant, dates, uid, **moteur d'IA et session à
   l'origine**, méthode, outil, paramètres masqués, sauvegarde, chemins,
   paquets, sondes, régressions, état.

Annulation (``undo``, ``undo_since``) : restauration des chemins touchés
depuis la sauvegarde (et, sans Btrfs, inversion du delta de paquets par APT),
dans l'ordre antichronologique ; refusée si une transaction plus récente,
non annulée, a touché les mêmes chemins (il faut l'annuler d'abord).

Base : SQLite root 0600 ``/var/lib/djerba-ai/transactions.db`` (schéma
versionné, ``PRAGMA user_version``).
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from djerba_ai.core import probes, snapshots
from djerba_ai.core.sanitize import sanitize_value

DB = Path(os.environ.get("DJERBA_TX_DB", "/var/lib/djerba-ai/transactions.db"))
SCHEMA_VERSION = 1
KEEP = 60
KEEP_DAYS = 30
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C.UTF-8", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}

APPLIED = "appliquée"
NO_EFFECT = "sans effet"
FAILED = "échec"
UNDONE = "annulée"
AUTO_UNDONE = "annulée automatiquement"
PARTIAL = "annulation partielle"
EXPIRED = "expirée"
RUNNING = "en cours"

_LOCK = threading.RLock()


class TransactionError(Exception):
    """Transaction impossible (sauvegarde, annulation, conflit)."""


@dataclass(frozen=True)
class Origin:
    """Qui a demandé l'action : moteur d'IA et session (conversation)."""

    engine: str = ""
    session: str = ""


def _run(argv: list[str], timeout: int = 1800, env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def installed_packages(runner: Callable = _run) -> dict[str, str] | None:
    result = runner(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n"], timeout=120)
    if result.returncode != 0:
        return None
    packages = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[2].startswith("ii"):
            packages[fields[0]] = fields[1]
    return packages


def package_delta(before: dict[str, str] | None, after: dict[str, str] | None) -> dict:
    if before is None or after is None:
        return {}
    delta = {"installed": {name: after[name] for name in after.keys() - before.keys()},
             "removed": {name: before[name] for name in before.keys() - after.keys()},
             "changed": {name: [before[name], after[name]] for name in before.keys() & after.keys()
                         if before[name] != after[name]}}
    return {key: value for key, value in delta.items() if value}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _overlap(first: set[str], second: set[str]) -> set[str]:
    return {path for path in first for other in second
            if path == other or path.startswith(other + "/") or other.startswith(path + "/")}


class Journal:
    def __init__(self, db: Path | None = None, backend: Any = None, probe: Callable[..., dict] | None = None,
                 compare: Callable[[dict, dict], list[str]] = probes.regressions,
                 packages: Callable[[], dict[str, str] | None] | None = None, runner: Callable = _run) -> None:
        self.db = db or DB
        self._backend = backend
        self.probe = probe or (lambda targets=(): probes.collect(tuple(targets)))
        self.compare = compare
        self.packages = packages or (lambda: installed_packages(runner))
        self.runner = runner
        self._ensure()

    # ------------------------------------------------------------ base
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure(self) -> None:
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id TEXT PRIMARY KEY, started TEXT NOT NULL, finished TEXT, uid INTEGER,
                        engine TEXT, session TEXT, method TEXT, tool TEXT, params TEXT,
                        backend TEXT, backup TEXT, state TEXT NOT NULL, success INTEGER, message TEXT,
                        changes TEXT, packages TEXT, probes_before TEXT, probes_after TEXT,
                        regressions TEXT, undone_at TEXT, undo_report TEXT);
                    CREATE INDEX IF NOT EXISTS transactions_started ON transactions(started);
                """)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        try:
            os.chmod(self.db, 0o600)
        except OSError:
            pass

    @property
    def backend(self) -> Any:
        if self._backend is None:
            self._backend = snapshots.detect()
        return self._backend

    @staticmethod
    def new_id() -> str:
        return f"t{time.strftime('%Y%m%d%H%M%S')}{secrets.token_hex(2)}"

    def _update(self, tx_id: str, **fields: Any) -> None:
        encoded = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                   for key, value in fields.items()}
        assignments = ", ".join(f"{key} = ?" for key in encoded)
        with self._connect() as connection:
            connection.execute(f"UPDATE transactions SET {assignments} WHERE id = ?", (*encoded.values(), tx_id))

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        data = dict(row)
        for key in ("params", "changes", "packages", "probes_before", "probes_after", "regressions", "undo_report"):
            if data.get(key):
                try:
                    data[key] = json.loads(data[key])
                except ValueError:
                    pass
        data["success"] = None if data.get("success") is None else bool(data["success"])
        data["undoable"] = data["state"] == APPLIED
        return data

    def show(self, tx_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
        return self._decode(row) if row else None

    def list(self, limit: int = 50, since: str | None = None) -> list[dict]:
        query, args = "SELECT * FROM transactions", []
        if since:
            query += " WHERE started >= ?"
            args.append(since)
        query += " ORDER BY rowid DESC LIMIT ?"
        args.append(int(limit))
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return [self._decode(row) for row in rows]

    # ------------------------------------------------------------ exécution
    def run(self, action: Callable[[], Any], *, method: str, tool: str = "", params: Any = None, uid: int = 0,
            origin: Origin | None = None, targets: tuple[str, ...] = (), boot: bool = False) -> Any:
        """Exécute ``action`` dans une transaction ; ajoute ``transaction`` au résultat (dict)."""
        origin = origin or Origin()
        with _LOCK:
            tx_id = self.new_id()
            before = self.probe(targets)
            packages_before = self.packages()
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO transactions (id, started, uid, engine, session, method, tool, params, backend, state)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tx_id, _now(), uid, origin.engine[:80], str(origin.session)[:80], method, tool,
                     json.dumps(sanitize_value(params), ensure_ascii=False), self.backend.name, RUNNING))
            try:
                backup = self.backend.prepare(tx_id, boot=boot)
            except (snapshots.SnapshotError, OSError) as exc:
                message = f"Action non exécutée : sauvegarde préalable impossible ({exc})."
                self._update(tx_id, finished=_now(), state=FAILED, success=0, message=message)
                return {"success": False, "message": message, "data": {},
                        "transaction": {"id": tx_id, "state": FAILED, "undoable": False}}
            self._update(tx_id, backup=backup)
            try:
                result = action()
            except Exception as exc:  # noqa: BLE001 - toute erreur de l'action est consignée puis annulée
                result = {"success": False, "message": f"erreur : {exc}", "data": {}}
            succeeded = not isinstance(result, dict) or bool(result.get("success", True))
            try:
                changes = self.backend.capture(tx_id)
            except (snapshots.SnapshotError, OSError, ValueError) as exc:
                changes = None
                capture_error = str(exc)
            else:
                capture_error = ""
            delta = package_delta(packages_before, self.packages())
            after = self.probe(targets)
            found = self.compare(before, after)
            fields: dict[str, Any] = {"finished": _now(), "success": int(succeeded),
                                      "changes": changes.to_json() if changes is not None else None,
                                      "packages": delta, "probes_before": before, "probes_after": after,
                                      "regressions": found}
            changed = bool(changes) or bool(delta)
            if changes is None:
                fields.update(state=APPLIED if succeeded else FAILED,
                              message=f"modifications non relevées ({capture_error}) : annulation impossible")
                self._update(tx_id, **fields)
                state = fields["state"]
            elif not changed:
                fields.update(state=NO_EFFECT if succeeded else FAILED, message="aucune modification")
                self._update(tx_id, **fields)
                self.backend.discard(tx_id)
                state = fields["state"]
            else:
                fields.update(state=APPLIED, message="")
                self._update(tx_id, **fields)
                state = APPLIED
                reasons = found or ([] if succeeded else ["l'action a échoué en laissant des modifications"])
                if reasons:
                    report = self.undo(tx_id, uid=uid, automatic=True, force=True)
                    state = report["state"]
                    explanation = (f"Action annulée automatiquement : {', '.join(reasons)}. "
                                   f"{report['message']}")
                    if isinstance(result, dict):
                        result = {**result, "success": False,
                                  "message": f"{result.get('message', '')} — {explanation}".strip(" —")}
            self.prune()
            if isinstance(result, dict):
                result = {**result, "transaction": {"id": tx_id, "state": state, "undoable": state == APPLIED,
                                                    "backend": self.backend.name, "regressions": found}}
            return result

    # ------------------------------------------------------------ annulation
    def conflicts(self, tx_id: str) -> list[str]:
        """Transactions plus récentes, encore appliquées, qui ont touché les mêmes chemins."""
        target = self.show(tx_id)
        if not target or not target.get("changes"):
            return []
        paths = set(target["changes"])
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM transactions WHERE rowid > (SELECT rowid FROM transactions WHERE id = ?) "
                "AND state = ?", (tx_id, APPLIED)).fetchall()
        return [row["id"] for row in rows if _overlap(paths, set(json.loads(row["changes"] or "{}")))]

    def undo(self, tx_id: str, uid: int = 0, force: bool = False, automatic: bool = False) -> dict:
        with _LOCK:
            transaction = self.show(tx_id)
            if not transaction:
                raise TransactionError(f"transaction inconnue : {tx_id}")
            if transaction["state"] != APPLIED:
                raise TransactionError(f"transaction {tx_id} non annulable (état : {transaction['state']})")
            if transaction["backend"] != self.backend.name or not self.backend.available(tx_id):
                raise TransactionError(f"sauvegarde de {tx_id} indisponible (expirée ou autre système)")
            blocking = [] if force else self.conflicts(tx_id)
            if blocking:
                raise TransactionError(f"des transactions plus récentes ont modifié les mêmes fichiers : "
                                       f"{', '.join(blocking)} — annulez-les d'abord (undo --since)")
            changes = snapshots.Changes.from_json(transaction.get("changes") or {})
            report: dict[str, Any] = {"packages": []}
            if self.backend.name == snapshots.FilesBackend.name and transaction.get("packages"):
                report["packages"] = self._undo_packages(transaction["packages"])
            report.update(snapshots.restore(changes, self.backend.before_root(tx_id), self.backend.live))
            self.runner(["systemctl", "daemon-reload"], timeout=120)
            after = self.probe(())
            errors = report["errors"] + [step for step in report["packages"] if step.startswith("échec")]
            state = PARTIAL if errors else (AUTO_UNDONE if automatic else UNDONE)
            report["probes"] = after
            message = (f"Restauration partielle : {'; '.join(errors[:5])}" if errors else
                       f"{len(report['restored']) + len(report['removed'])} élément(s) restauré(s).")
            self._update(tx_id, state=state, undone_at=_now(), undo_report=report)
            return {"id": tx_id, "state": state, "message": message, "report": report}

    def _undo_packages(self, delta: dict) -> list[str]:
        steps = []
        installed = sorted(delta.get("installed", {}))
        if installed:
            result = self.runner(["apt-get", "-y", "purge", *installed], env=APT_ENV)
            steps.append(("retirés : " if result.returncode == 0 else "échec du retrait : ") + ", ".join(installed))
        wanted = {name: version for name, version in delta.get("removed", {}).items()}
        wanted.update({name: versions[0] for name, versions in delta.get("changed", {}).items()})
        if wanted:
            specs = [f"{name}={version}" for name, version in sorted(wanted.items())]
            result = self.runner(["apt-get", "-y", "--allow-downgrades", "install", *specs], env=APT_ENV)
            steps.append(("réinstallés : " if result.returncode == 0 else "échec de la réinstallation : ")
                         + ", ".join(specs))
        return steps

    def undo_since(self, since: str, uid: int = 0) -> dict:
        """Annule, du plus récent au plus ancien, toutes les transactions appliquées depuis ``since``."""
        results, errors = [], []
        for transaction in self.list(limit=1000, since=since):
            if transaction["state"] != APPLIED:
                continue
            try:
                results.append(self.undo(transaction["id"], uid=uid))
            except TransactionError as exc:
                errors.append(f"{transaction['id']} : {exc}")
        return {"undone": [item["id"] for item in results], "errors": errors,
                "message": f"{len(results)} transaction(s) annulée(s)"
                           + (f", {len(errors)} impossible(s)" if errors else "")}

    def prune(self, keep: int = KEEP, days: int = KEEP_DAYS) -> None:
        """Sauvegardes conservées : les ``keep`` dernières transactions et au plus ``days`` jours."""
        limit = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - days * 86400))
        with self._connect() as connection:
            rows = connection.execute("SELECT id, started FROM transactions WHERE state = ? ORDER BY rowid DESC",
                                      (APPLIED,)).fetchall()
        for index, row in enumerate(rows):
            if index >= keep or row["started"] < limit:
                self.backend.discard(row["id"])
                self._update(row["id"], state=EXPIRED)
