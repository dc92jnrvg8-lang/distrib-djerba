# SPDX-License-Identifier: GPL-3.0-or-later
"""Embeddings locaux et recherche sémantique.

* Avec Ollama local : modèle d'embedding configuré (llm.embedding_model,
  « nomic-embed-text » par défaut) via /api/embed. Rien ne quitte la machine.
* Sans lui : vecteurs calculés localement par hachage de termes et de
  bigrammes de caractères (512 dimensions, pondération logarithmique) —
  moins fins, mais robustes aux fautes et aux variations de mots.

Les vecteurs sont normalisés : la similarité cosinus est un produit scalaire.
"""

from __future__ import annotations

import array
import hashlib
import math
import re
import unicodedata

HASH_DIMS = 512
HASH_MODEL = f"hash-{HASH_DIMS}"


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def hashed(text: str) -> list[float]:
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]{2,}", plain)
    features = [f"w:{word[:8]}" for word in words]
    for word in words:
        padded = f"#{word}#"
        features.extend(f"c:{padded[i:i + 3]}" for i in range(len(padded) - 2))
    vector = [0.0] * HASH_DIMS
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature] = counts.get(feature, 0) + 1
    for feature, count in counts.items():
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "little") % HASH_DIMS
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = (2.0 if feature.startswith("w:") else 1.0) * (1 + math.log(count))
        vector[index] += sign * weight
    return _normalize(vector)


def ollama_embed(endpoint: str, model: str, texts: list[str], timeout: float = 30) -> list[list[float]] | None:
    """Embeddings par Ollama (None si le modèle ou le service est indisponible)."""
    from djerba_ai.core.llm import LLMError, LocalOllamaProvider
    # Même transport que le dialogue : connexion persistante, reprises, adresse de repli.
    provider = LocalOllamaProvider(endpoint, model)
    try:
        with provider._send("POST", "/api/embed", {"model": model, "input": texts}, timeout=timeout) as response:
            vectors = response.json().get("embeddings")
        if not vectors or len(vectors) != len(texts):
            return None
        return [_normalize([float(value) for value in vector]) for vector in vectors]
    except (LLMError, OSError, ValueError):
        return None


def embed(texts: list[str], config=None) -> tuple[str, list[list[float]]]:
    """Retourne (nom du modèle, vecteurs) : Ollama si possible, sinon hachage local."""
    if config is not None:
        endpoint = config.get("llm.local.endpoint")
        model = config.get("llm.embedding_model")
        vectors = ollama_embed(endpoint, model, texts)
        if vectors is not None:
            return f"ollama:{model}", vectors
    return HASH_MODEL, [hashed(text) for text in texts]


def pack(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return values.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def chunks(text: str, size: int = 1200, overlap: int = 200) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= size:
        return [text] if text else []
    return [text[start:start + size] for start in range(0, len(text), size - overlap)]
