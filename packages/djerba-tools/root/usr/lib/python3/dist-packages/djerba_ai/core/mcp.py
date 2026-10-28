# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba comme serveur d'outils (MCP) pour un assistant distant, Jarvis par exemple.

Contrat : docs/JARVIS-CONTRAT.md §2. Serveur JSON-RPC 2.0 en HTTP (sans
flux) lancé par ``djerba-ai mcp-serve`` (service utilisateur
``djerba-ai-mcp.service``, actif seulement si ``mcp.enabled``), **lié à
l'adresse Tailscale** de la machine.

* ``POST /pair`` : échange d'un code d'appairage à 6 chiffres (affiché par
  ``djerba-ai mcp pair NOM``, valable 5 minutes) contre un jeton ;
* ``POST /mcp`` (``Authorization: Bearer``) : ``initialize``,
  ``notifications/initialized``, ``tools/list``, ``tools/call``.

Chaque appel d'outil est exécuté par Djerba sous **sa** politique (origine
``mcp-<appelant>``), journalisé et annulable, comme pour n'importe quel
moteur. Les codes et jetons sont gérés par le démon root
(``/etc/djerba/mcp-clients.json``, 0600, jetons hachés) : le serveur de la
session ne fait que relayer.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from djerba_ai.core.config import CONFIG_DIR

CLIENTS_FILE = Path(os.environ.get("DJERBA_MCP_CLIENTS", str(CONFIG_DIR / "mcp-clients.json")))
PROTOCOL = "2025-06-18"
PAIR_SECONDS = 300
CALLS_PER_MINUTE = 60
PAIR_ATTEMPTS_PER_MINUTE = 5
DEFAULT_CAP = 2


#: échecs d'appairage récents, partagés par toutes les requêtes du démon (anti-force brute)
_FAILURES: deque = deque()
_STORE_LOCK = threading.Lock()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------- Démon root : appelants
class ClientStore:
    """Appelants appairés (jetons hachés) et codes en attente (root, 0600)."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = path or CLIENTS_FILE
        self.clock = clock
        self.lock = _STORE_LOCK
        self.failures = _FAILURES

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("pending", {})
        data.setdefault("clients", {})
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(".mcp-clients.json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def begin(self, name: str, cap: int = DEFAULT_CAP) -> dict:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.lock:
            data = self._load()
            now = self.clock()
            data["pending"] = {key: value for key, value in data["pending"].items() if value["expires"] > now}
            data["pending"][_hash(code)] = {"name": name, "expires": now + PAIR_SECONDS, "cap": cap}
            self._save(data)
        return {"code": code, "name": name, "expires_in": PAIR_SECONDS}

    def complete(self, code: str, name: str = "") -> dict | None:
        with self.lock:
            now = self.clock()
            while self.failures and now - self.failures[0] > 60:
                self.failures.popleft()
            if len(self.failures) >= PAIR_ATTEMPTS_PER_MINUTE:
                return None  # essai par force brute : le code ne peut plus être deviné
            data = self._load()
            pending = data["pending"].pop(_hash(code), None)
            if not pending or pending["expires"] < now:
                self.failures.append(now)
                return None
            client = pending["name"] or name
            token = secrets.token_urlsafe(32)
            data["clients"][client] = {"token": _hash(token), "created": self.clock(), "cap": pending["cap"],
                                       "last_used": None}
            self._save(data)
        return {"token": token, "client": client}

    def verify(self, token: str) -> dict | None:
        digest = _hash(token)
        with self.lock:
            data = self._load()
            for name, client in data["clients"].items():
                if secrets.compare_digest(client["token"], digest):
                    client["last_used"] = self.clock()
                    self._save(data)
                    return {"client": name, "cap": int(client.get("cap", DEFAULT_CAP))}
        return None

    def list(self) -> list[dict]:
        return [{"client": name, "created": client["created"], "last_used": client.get("last_used"),
                 "cap": client.get("cap", DEFAULT_CAP)} for name, client in self._load()["clients"].items()]

    def revoke(self, name: str) -> bool:
        with self.lock:
            data = self._load()
            found = data["clients"].pop(name, None) is not None
            self._save(data)
        return found

    def cap(self, name: str) -> int:
        client = self._load()["clients"].get(name)
        return int(client.get("cap", DEFAULT_CAP)) if client else 0


# ---------------------------------------------------------------- Session : serveur MCP
class Limiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute, self.clock = per_minute, clock
        self.windows: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self.lock:
            window = self.windows[key]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= self.per_minute:
                return False
            window.append(now)
            return True


class MCPServer:
    """Logique du serveur (indépendante du transport HTTP : testable directement)."""

    def __init__(self, registry, execute: Callable[[str, dict, dict], dict],
                 rpc: Callable[[str, dict], Any], levels: Callable[[Any], int] | None = None) -> None:
        self.registry = registry
        self.execute = execute
        self.rpc = rpc
        self.levels = levels or (lambda tool: 0)
        self.calls = Limiter(CALLS_PER_MINUTE)
        self.pairing = Limiter(PAIR_ATTEMPTS_PER_MINUTE)

    def pair(self, body: dict) -> tuple[int, dict]:
        if not self.pairing.allow("pair"):
            return 429, {"error": "trop d'essais d'appairage : réessayez dans une minute"}
        code = str(body.get("code", ""))
        if not (len(code) == 6 and code.isdigit()):
            return 400, {"error": "code à 6 chiffres attendu"}
        result = self.rpc("mcp.pair_complete", {"code": code, "name": str(body.get("name", ""))[:40]})
        if not result.get("token"):
            return 403, {"error": "code invalide ou expiré"}
        return 200, {"token": result["token"], "client": result["client"]}

    def authenticate(self, header: str) -> dict | None:
        if not header.startswith("Bearer "):
            return None
        try:
            return self.rpc("mcp.verify", {"token": header[7:].strip()[:200]}) or None
        except Exception:  # noqa: BLE001 - démon root injoignable : accès refusé
            return None

    def handle(self, client: dict, message: dict) -> dict | None:
        method = message.get("method")
        identifier = message.get("id")
        if "id" not in message:
            return None  # notification (notifications/initialized…)

        def reply(result: Any) -> dict:
            return {"jsonrpc": "2.0", "id": identifier, "result": result}

        def error(code: int, text: str) -> dict:
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": text}}

        if method == "initialize":
            return reply({"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "djerba-os", "version": "1"},
                          "instructions": "Capacités de Djerba OS, exécutées sous la politique de la machine."})
        if method == "ping":
            return reply({})
        if method == "tools/list":
            tools = []
            for tool in sorted(self.registry.tools.values(), key=lambda item: item.name):
                schema = tool.schema()["function"]
                tools.append({"name": tool.name, "description": f"{tool.description} (niveau "
                                                               f"{self.levels(tool)})",
                              "inputSchema": schema["parameters"]})
            return reply({"tools": tools})
        if method == "tools/call":
            if not self.calls.allow(client["client"]):
                return error(-32000, f"plafond atteint : {CALLS_PER_MINUTE} appels par minute")
            params = message.get("params") or {}
            name, arguments = str(params.get("name", "")), params.get("arguments") or {}
            if self.registry.get(name) is None or not isinstance(arguments, dict):
                return error(-32602, f"outil inconnu ou arguments invalides : {name}")
            result = self.execute(name, arguments, {"engine": f"mcp-{client['client']}"[:80], "session": "mcp",
                                                    "tainted": False})
            return reply({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                          "isError": not result.get("success", False)})
        return error(-32601, f"méthode inconnue : {method}")


def make_handler(server: MCPServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _send(self, status: int, payload: dict | None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - nom imposé par http.server
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 1024 * 1024)
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "JSON invalide"})
                return
            if self.path == "/pair":
                self._send(*server.pair(body if isinstance(body, dict) else {}))
                return
            if self.path != "/mcp":
                self._send(404, {"error": "inconnu"})
                return
            client = server.authenticate(self.headers.get("Authorization", ""))
            if client is None:
                self._send(401, {"error": "jeton absent, invalide ou révoqué"})
                return
            response = server.handle(client, body if isinstance(body, dict) else {})
            self._send(202 if response is None else 200, response)

    return Handler


def bind_address() -> str | None:
    """Adresse Tailscale (IPv4) de la machine : le serveur n'écoute jamais ailleurs."""
    import subprocess
    try:
        output = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    addresses = [line.strip() for line in output.splitlines() if line.strip()]
    return addresses[0] if addresses else None


def serve(config, host: str | None = None) -> int:
    from djerba_ai.core import ipc, policy
    from djerba_ai.core.executor import Executor
    from djerba_ai.tools import registry
    if not config.get("mcp.enabled"):
        print("djerba-ai mcp-serve : désactivé (réglage mcp.enabled)", flush=True)
        return 0
    host = host or bind_address()
    if not host:
        print("djerba-ai mcp-serve : aucune adresse Tailscale : serveur non démarré", flush=True)
        return 0
    reg = registry()
    executor = Executor(reg)
    rules = policy.Rules.load()

    def execute(name: str, arguments: dict, origin: dict) -> dict:
        tool = reg.get(name)
        if not tool.requires_root:
            decision = policy.check(tool, arguments, origin)
            if not decision.get("allowed"):
                return {"success": False, "message": f"Refusé par la politique de Djerba : {decision.get('reason')}"}
        executor.origin = origin
        return executor.execute(name, arguments, request="mcp")

    def level(tool) -> int:
        return policy.Policy(rules=rules, settings={}).level(tool)

    server = MCPServer(reg, execute, lambda method, params: ipc.call(method, params, timeout=30), level)
    httpd = ThreadingHTTPServer((host, int(config.get("mcp.port"))), make_handler(server))
    print(f"djerba-ai mcp-serve : à l'écoute sur {host}:{config.get('mcp.port')}", flush=True)
    httpd.serve_forever()
    return 0
