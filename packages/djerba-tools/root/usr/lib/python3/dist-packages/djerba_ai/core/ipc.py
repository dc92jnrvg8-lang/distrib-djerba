# SPDX-License-Identifier: GPL-3.0-or-later
"""IPC JSON-RPC 2.0 sur socket UNIX (une requête par ligne, une réponse par ligne).

Client du démon root djerba-ai-sysd. Le démon identifie l'appelant par
SO_PEERCRED (pid, uid, gid) et vérifie l'autorisation polkit de ce processus :
aucun secret ne transite par la socket.
"""

from __future__ import annotations

import itertools
import json
import socket
import struct
from pathlib import Path
from typing import Any

from djerba_ai.core.config import SYSD_SOCKET

MAX_MESSAGE = 4 * 1024 * 1024
_ids = itertools.count(1)


class RPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# Codes d'erreur JSON-RPC (et codes propres à Djerba, -32000…-32099).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
NOT_AUTHORIZED = -32001
TOOL_FAILED = -32002


def peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    """(pid, uid, gid) du processus à l'autre bout de la socket."""
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


def read_message(stream) -> dict | None:
    line = stream.readline(MAX_MESSAGE + 1)
    if not line:
        return None
    if len(line) > MAX_MESSAGE:
        raise RPCError(INVALID_REQUEST, "message trop long")
    try:
        message = json.loads(line)
    except ValueError as exc:
        raise RPCError(PARSE_ERROR, "JSON invalide") from exc
    if not isinstance(message, dict):
        raise RPCError(INVALID_REQUEST, "objet JSON attendu")
    return message


def write_message(stream, message: dict) -> None:
    stream.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
    stream.flush()


def call(method: str, params: dict | None = None, timeout: float = 7200,
         socket_path: Path = SYSD_SOCKET) -> Any:
    """Appelle une méthode du démon root ; lève RPCError en cas d'erreur."""
    request_id = next(_ids)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
    except OSError as exc:
        raise RPCError(INTERNAL_ERROR, f"service djerba-ai-sysd injoignable ({exc.strerror})") from exc
    with client, client.makefile("rwb") as stream:
        write_message(stream, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        response = read_message(stream)
    if response is None:
        raise RPCError(INTERNAL_ERROR, "connexion fermée par djerba-ai-sysd")
    if "error" in response:
        error = response["error"]
        raise RPCError(int(error.get("code", INTERNAL_ERROR)), str(error.get("message", "erreur")))
    return response.get("result")
