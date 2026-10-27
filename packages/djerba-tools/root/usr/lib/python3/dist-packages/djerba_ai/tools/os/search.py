# SPDX-License-Identifier: GPL-3.0-or-later
"""Recherche de fichiers : par nom et sémantique (index local d'embeddings).

L'index est stocké dans ~/.local/share/djerba/memory.db (table embeddings,
type « file ») ; les vecteurs viennent d'Ollama (modèle d'embeddings choisi
dans la configuration) ou, à défaut, d'un hachage local de caractéristiques.
Le texte indexé est nettoyé de tout secret avant écriture. Rien ne sort de
la machine.
"""

from __future__ import annotations

import fnmatch
import os
import time
from pathlib import Path

from djerba_ai.core import embeddings
from djerba_ai.core.config import Config
from djerba_ai.core.memory import Memory
from djerba_ai.tools.base import SAFE_TEXT, fail, function_tool, ok
from djerba_ai.tools.os.guard import PATH, resolve

TEXT_SUFFIXES = {".txt", ".md", ".rst", ".org", ".py", ".sh", ".js", ".ts", ".qml", ".html", ".css", ".json",
                 ".toml", ".yaml", ".yml", ".ini", ".conf", ".cfg", ".csv", ".tex", ".c", ".h", ".cpp", ".rs",
                 ".go", ".java", ".desktop", ".service", ".xml", ".log", ".sql"}
SKIP_DIRS = {".git", ".cache", "node_modules", "__pycache__", ".venv", "venv", ".local", ".mozilla", ".steam",
             ".var", "Trash", ".npm", ".cargo", ".rustup", "target", "build", "dist"}
FILE_MAX = 512 * 1024
KIND = "file"


def walk(root: Path, limit: int):
    count = 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS and not name.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            yield Path(directory) / name
            count += 1
            if count >= limit:
                return


def _find(params: dict, _verbose: bool) -> dict:
    root = resolve(params["path"])
    pattern = params["pattern"]
    glob = any(char in pattern for char in "*?[")
    needle = pattern.lower()
    found = []
    for path in walk(root, 200000):
        if (fnmatch.fnmatch(path.name, pattern) if glob else needle in path.name.lower()):
            found.append(str(path))
            if len(found) >= params["limit"]:
                break
    return ok(f"{len(found)} fichier(s) trouvé(s) sous {root}.", files=found)


def build_index(root: Path, config: Config, memory: Memory, limit: int = 3000) -> dict:
    known = memory.embedding_refs(KIND)
    seen: set[str] = set()
    indexed = skipped = 0
    batch_refs: list[str] = []
    batch_texts: list[str] = []

    def flush() -> None:
        if not batch_texts:
            return
        model, vectors = embeddings.embed(batch_texts, config)
        for ref, text, vector in zip(batch_refs, batch_texts, vectors):
            memory.upsert_embedding(KIND, ref, text, model, embeddings.pack(vector))
        batch_refs.clear()
        batch_texts.clear()

    for path in walk(root, limit):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            info = path.stat()
        except OSError:
            continue
        if info.st_size > FILE_MAX or info.st_size == 0:
            continue
        prefix = f"{path}#"
        current = [ref for ref in known if ref.startswith(prefix)]
        seen.update(current)
        if current and all(known[ref] >= info.st_mtime for ref in current):
            skipped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        memory.delete_embeddings(KIND, current)
        for number, chunk in enumerate(embeddings.chunks(text)[:20]):
            ref = f"{path}#{number}"
            seen.add(ref)
            batch_refs.append(ref)
            batch_texts.append(f"{path.name} : {chunk}")
            if len(batch_texts) >= 32:
                flush()
        indexed += 1
    flush()
    root_prefix = f"{root}/"
    stale = [ref for ref in known if ref.startswith(root_prefix) and ref not in seen]
    memory.delete_embeddings(KIND, stale)
    return {"indexed": indexed, "unchanged": skipped, "removed": len(stale)}


def search(query: str, config: Config, memory: Memory, limit: int = 8) -> list[dict]:
    model, (vector,) = embeddings.embed([query], config)
    rows = memory.embeddings(KIND, model)
    scored = []
    for row in rows:
        scored.append((embeddings.cosine(vector, embeddings.unpack(row["vector"])), row))
    scored.sort(key=lambda item: -item[0])
    results, files = [], set()
    for score, row in scored:
        path = row["ref"].rsplit("#", 1)[0]
        if path in files:
            continue
        files.add(path)
        results.append({"path": path, "score": round(score, 3), "excerpt": row["text"][:300]})
        if len(results) >= limit:
            break
    return results


def _index(params: dict, _verbose: bool) -> dict:
    root = resolve(params["path"])
    if not root.is_dir():
        return fail(f"{root} n'est pas un dossier")
    start = time.monotonic()
    memory = Memory()
    try:
        stats = build_index(root, Config(), memory, params["limit"])
    finally:
        memory.close()
    return ok(f"Index de {root} à jour : {stats['indexed']} fichier(s) indexé(s), {stats['unchanged']} inchangé(s), "
              f"{stats['removed']} entrée(s) retirée(s) en {time.monotonic() - start:.1f} s.", **stats)


def _semantic(params: dict, _verbose: bool) -> dict:
    memory = Memory()
    try:
        config = Config()
        if not memory.embedding_refs(KIND):
            build_index(Path.home(), config, memory)
        results = search(params["query"], config, memory, params["limit"])
    finally:
        memory.close()
    if not results:
        return fail("aucun document indexé ne correspond (lancez os_index_build sur le dossier voulu)")
    return ok(f"{len(results)} document(s) pertinent(s).", results=results)


C = "os"
TOOLS = [
    function_tool("os_file_search", C, "Cherche des fichiers par nom (motif * ? ou fragment de nom).", _find,
                  parameters={"pattern": {"type": "string", "pattern": r"^[^\x00/]{1,200}$",
                                          "description": "nom, fragment ou motif (ex. *.pdf)"},
                              "path": {"type": "string", "pattern": PATH, "default": "~",
                                       "description": "dossier de départ"},
                              "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50,
                                        "description": "résultats au plus"}},
                  required=("pattern",), keywords=("trouver fichier", "chercher fichier", "find", "où est")),
    function_tool("os_index_build", C, "Indexe (ou met à jour) les documents texte d'un dossier pour la recherche "
                  "sémantique.", _index,
                  parameters={"path": {"type": "string", "pattern": PATH, "default": "~",
                                       "description": "dossier à indexer"},
                              "limit": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 3000,
                                        "description": "fichiers parcourus au plus"}},
                  keywords=("indexer", "index", "embeddings")),
    function_tool("os_semantic_search", C, "Retrouve des documents par le sens (« la facture d'électricité de "
                  "mars », « mon script de sauvegarde »).", _semantic,
                  parameters={"query": {"type": "string", "pattern": SAFE_TEXT, "description": "ce que vous cherchez"},
                              "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 8,
                                        "description": "résultats au plus"}},
                  required=("query",), keywords=("recherche sémantique", "retrouver document", "semantic search",
                                                 "chercher dans mes fichiers")),
]
