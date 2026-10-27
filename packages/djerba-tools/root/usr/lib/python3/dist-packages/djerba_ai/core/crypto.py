# SPDX-License-Identifier: GPL-3.0-or-later
"""Chiffrement local des données de l'agent (AES-256-GCM, python3-cryptography).

Une clé aléatoire de 256 bits par utilisateur, créée au premier usage dans
~/.local/share/djerba/memory.key (0600, dossier 0700). Elle chiffre :

* les contenus de la mémoire (memory.db : messages, faits, leçons, coffre DLP,
  extraits indexés…) ;
* l'état des sessions (bascule à chaud entre moteurs) ;
* les instantanés du bus de contexte.

Format : ``enc1:`` + base64(nonce 96 bits ‖ texte chiffré ‖ étiquette). Une
donnée associée (« aad ») lie chaque valeur à son usage (table, colonne) :
une valeur copiée ailleurs ne se déchiffre pas.

Limite assumée : la clé est protégée par les droits du compte. Elle
protège la base copiée, sauvegardée ou lue par un autre utilisateur, pas
contre un logiciel malveillant exécuté sous le même compte.
"""

from __future__ import annotations

import base64
import os
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from djerba_ai.core.config import DATA_DIR

PREFIX = "enc1:"
KEY_FILE = DATA_DIR / "memory.key"
_LOCK = threading.Lock()
_CACHE: dict[Path, AESGCM] = {}


class CryptoError(Exception):
    pass


def _load(path: Path) -> AESGCM:
    with _LOCK:
        if path in _CACHE:
            return _CACHE[path]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            key = path.read_bytes()
        except FileNotFoundError:
            key = AESGCM.generate_key(bit_length=256)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
        if len(key) != 32:
            raise CryptoError(f"clé de chiffrement invalide : {path}")
        os.chmod(path, 0o600)
        _CACHE[path] = AESGCM(key)
        return _CACHE[path]


class Box:
    """Chiffre / déchiffre des chaînes avec la clé de l'utilisateur."""

    def __init__(self, key_file: Path = KEY_FILE) -> None:
        self.aead = _load(key_file)

    def encrypt(self, text: str, aad: str = "") -> str:
        nonce = os.urandom(12)
        sealed = self.aead.encrypt(nonce, text.encode("utf-8"), aad.encode() or None)
        return PREFIX + base64.b64encode(nonce + sealed).decode()

    def decrypt(self, value: str | None, aad: str = "") -> str:
        """Déchiffre ; une valeur en clair (données antérieures) est rendue telle quelle."""
        if not value or not value.startswith(PREFIX):
            return value or ""
        raw = base64.b64decode(value[len(PREFIX):])
        try:
            return self.aead.decrypt(raw[:12], raw[12:], aad.encode() or None).decode("utf-8")
        except InvalidTag as exc:
            raise CryptoError("donnée chiffrée altérée ou clé différente") from exc

    def encrypt_bytes(self, data: bytes, aad: str = "") -> bytes:
        nonce = os.urandom(12)
        return nonce + self.aead.encrypt(nonce, data, aad.encode() or None)

    def decrypt_bytes(self, data: bytes, aad: str = "") -> bytes:
        try:
            return self.aead.decrypt(data[:12], data[12:], aad.encode() or None)
        except InvalidTag as exc:
            raise CryptoError("donnée chiffrée altérée ou clé différente") from exc


def write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Écriture atomique (fichier temporaire, fsync, renommage)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
