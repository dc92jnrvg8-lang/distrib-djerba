# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba comme serveur d'outils (MCP) pour un assistant distant, Jarvis par exemple.

Contrat : docs/JARVIS-CONTRAT.md §2. Serveur JSON-RPC 2.0 en HTTP (sans
flux) lancé par ``djerba-ai mcp-serve`` (service utilisateur
``djerba-ai-mcp.service``, actif seulement si ``mcp.enabled``), **lié à
l'adresse Tailscale** de la machine.

* ``POST /pair`` : échange d'un code d'appairage à 6 chiffres (affiché par
  ``djerba-ai mcp pair NOM``, valable 5 minutes, usage unique) contre un
  jeton ;
* ``POST /mcp`` et ``POST /rotate`` : **requêtes signées** (invariant 26) —
  ``Authorization: Djerba-HMAC client="…", ts="…", nonce="…", sig="…"`` avec
  ``sig = HMAC-SHA256(SHA-256(jeton), client\nts\nnonce\nSHA-256(corps))`` :
  le jeton ne circule plus après l'appairage ; horodatage à ±60 s, nonce à
  usage unique (anti-rejeu), corps intègre ; jeton valable 7 jours,
  renouvelé par ``/rotate`` (l'ancien est aussitôt invalide), révocable.

Chaque appel d'outil est exécuté par Djerba sous **sa** politique : session
de provenance tenue par root pour l'appelant (``mcp-<appelant>``), plafond
**niveau 1 au plus** (invariant 09), drapeau « non fiable » dès qu'il lit un
contenu externe ; journalisé et annulable. Les codes et clés sont gérés par
le démon root (``/etc/djerba/mcp-clients.json``, 0600) : le serveur de la
session ne fait que relayer. Il n'écoute que sur une adresse Tailscale
(100.64.0.0/10), vérifiée.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
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
#: plafond d'un appelant MCP (invariant 09) : niveau 1 au plus, jamais relevé
DEFAULT_CAP = 1
MAX_CAP = 1
#: durée de validité d'un jeton (renouvelé par /rotate)
TOKEN_SECONDS = 7 * 86400
#: écart d'horloge toléré et durée de mémoire des nonces
SKEW_SECONDS = 60
TAILSCALE_RANGE = ipaddress.ip_network("100.64.0.0/10")


#: échecs d'appairage récents, partagés par toutes les requêtes du démon (anti-force brute)
_FAILURES: deque = deque()
_STORE_LOCK = threading.Lock()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _signature(key: str, client: str, ts: int, nonce: str, body_sha256: str) -> str:
    return hmac.new(key.encode(), f"{client}\n{ts}\n{nonce}\n{body_sha256}".encode(), hashlib.sha256).hexdigest()


def sign(token: str, client: str, body: bytes, now: float | None = None) -> dict:
    """Signature d'une requête (côté appelant : Jarvis, tests). La clé est SHA-256(jeton)."""
    ts = int(now if now is not None else time.time())
    nonce = secrets.token_hex(16)
    return {"client": client, "ts": ts, "nonce": nonce,
            "sig": _signature(_hash(token), client, ts, nonce, hashlib.sha256(body).hexdigest())}


def header(signed: dict) -> str:
    return "Djerba-HMAC " + ", ".join(f'{key}="{signed[key]}"' for key in ("client", "ts", "nonce", "sig"))


def parse_header(value: str) -> dict | None:
    if not value.startswith("Djerba-HMAC "):
        return None
    fields = {}
    for part in value[12:].split(","):
        key, _sep, raw = part.strip().partition("=")
        fields[key] = raw.strip().strip('"')
    try:
        return {"client": fields["client"][:40], "ts": int(fields["ts"]), "nonce": fields["nonce"][:64],
                "sig": fields["sig"][:64]}
    except (KeyError, ValueError):
        return None


def tailscale_address(address: str) -> bool:
    """Vrai seulement pour une adresse IPv4 du réseau Tailscale (100.64.0.0/10)."""
    try:
        return ipaddress.ip_address(address) in TAILSCALE_RANGE
    except ValueError:
        return False


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
        cap = max(0, min(int(cap), MAX_CAP))
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
            data["clients"][client] = {"key": _hash(token), "created": now, "expires": now + TOKEN_SECONDS,
                                       "cap": min(int(pending["cap"]), MAX_CAP), "last_used": None, "nonces": {}}
            self._save(data)
        return {"token": token, "client": client, "expires_in": TOKEN_SECONDS}

    def verify_request(self, signed: dict, body: bytes | None = None, body_sha256: str | None = None) -> dict | None:
        """Vérifie une requête signée : appelant connu, jeton non expiré, horodatage à ±60 s, nonce jamais
        vu, signature du corps. Le nonce est consommé : une requête rejouée est refusée."""
        digest = body_sha256 if body_sha256 is not None else hashlib.sha256(body or b"").hexdigest()
        try:
            name, ts, nonce, sig = str(signed["client"]), int(signed["ts"]), str(signed["nonce"]), str(signed["sig"])
        except (KeyError, TypeError, ValueError):
            return None
        with self.lock:
            now = self.clock()
            data = self._load()
            client = data["clients"].get(name)
            if not client or "key" not in client or client.get("expires", 0) < now or abs(now - ts) > SKEW_SECONDS:
                return None
            expected = _signature(client["key"], name, ts, nonce, digest)
            if not hmac.compare_digest(expected, sig):
                return None
            nonces = {key: value for key, value in client.get("nonces", {}).items() if now - value <= 2 * SKEW_SECONDS}
            if nonce in nonces:
                return None  # rejeu
            nonces[nonce] = now
            client["nonces"], client["last_used"] = nonces, now
            self._save(data)
            return {"client": name, "cap": min(int(client.get("cap", DEFAULT_CAP)), MAX_CAP),
                    "expires_in": int(client["expires"] - now)}

    def rotate(self, name: str, signed: dict, body: bytes = b"", body_sha256: str | None = None) -> dict | None:
        """Nouveau jeton contre une requête signée avec l'actuel ; l'ancien est aussitôt invalide."""
        if signed.get("client") != name or self.verify_request(signed, body, body_sha256) is None:
            return None
        token = secrets.token_urlsafe(32)
        with self.lock:
            data = self._load()
            client = data["clients"].get(name)
            if client is None:
                return None
            now = self.clock()
            client.update({"key": _hash(token), "expires": now + TOKEN_SECONDS, "nonces": {}})
            self._save(data)
        return {"token": token, "client": name, "expires_in": TOKEN_SECONDS}

    def list(self) -> list[dict]:
        return [{"client": name, "created": client["created"], "last_used": client.get("last_used"),
                 "expires": client.get("expires", 0), "cap": min(int(client.get("cap", DEFAULT_CAP)), MAX_CAP)}
                for name, client in self._load()["clients"].items()]

    def revoke(self, name: str) -> bool:
        with self.lock:
            data = self._load()
            found = data["clients"].pop(name, None) is not None
            self._save(data)
        return found

    def cap(self, name: str) -> int:
        """Plafond de l'appelant : 0 s'il est inconnu, révoqué ou expiré ; jamais au-delà de 1."""
        client = self._load()["clients"].get(name)
        if not client or "key" not in client or client.get("expires", 0) < self.clock():
            return 0
        return min(int(client.get("cap", DEFAULT_CAP)), MAX_CAP)


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

    def authenticate(self, value: str, body: bytes) -> dict | None:
        """Requête signée vérifiée par le démon root, qui rend aussi la session de provenance de l'appelant."""
        signed = parse_header(value)
        if signed is None:
            return None
        try:
            result = self.rpc("mcp.verify", {**signed, "body_sha256": hashlib.sha256(body).hexdigest()}) or None
        except Exception:  # noqa: BLE001 - démon root injoignable : accès refusé
            return None
        return result if result and result.get("client") else None

    def rotate(self, value: str, body: bytes) -> tuple[int, dict]:
        signed = parse_header(value)
        if signed is None:
            return 401, {"error": "requête non signée"}
        try:
            result = self.rpc("mcp.rotate", {**signed, "body_sha256": hashlib.sha256(body).hexdigest()}) or {}
        except Exception:  # noqa: BLE001
            result = {}
        if not result.get("token"):
            return 401, {"error": "signature invalide, rejouée ou jeton expiré"}
        return 200, result

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
            # Session de provenance tenue par root pour cet appelant : son drapeau le suit d'un appel à l'autre.
            result = self.execute(name, arguments, {"engine": f"mcp-{client['client']}"[:80],
                                                    "session": str(client.get("session", "")), "tainted": False})
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
                raw = self.rfile.read(length)
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": "JSON invalide"})
                return
            if self.path == "/pair":
                self._send(*server.pair(body if isinstance(body, dict) else {}))
                return
            if self.path == "/rotate":
                self._send(*server.rotate(self.headers.get("Authorization", ""), raw))
                return
            if self.path != "/mcp":
                self._send(404, {"error": "inconnu"})
                return
            client = server.authenticate(self.headers.get("Authorization", ""), raw)
            if client is None:
                self._send(401, {"error": "requête non signée, rejouée, expirée ou appelant révoqué"})
                return
            response = server.handle(client, body if isinstance(body, dict) else {})
            self._send(202 if response is None else 200, response)

    return Handler


def bind_address(runner: Callable[..., Any] | None = None) -> str | None:
    """Adresse Tailscale (IPv4, 100.64.0.0/10) de la machine : le serveur n'écoute jamais ailleurs."""
    import subprocess
    runner = runner or subprocess.run
    try:
        output = runner(["/usr/bin/tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    addresses = [line.strip() for line in output.splitlines() if tailscale_address(line.strip())]
    return addresses[0] if addresses else None


def serve(config, host: str | None = None) -> int:
    from djerba_ai.core import ipc, policy
    from djerba_ai.core.executor import Executor
    from djerba_ai.tools import registry
    if not config.get("mcp.enabled"):
        print("djerba-ai mcp-serve : désactivé (réglage mcp.enabled)", flush=True)
        return 0
    host = host or bind_address()
    if not host or not tailscale_address(host):
        print("djerba-ai mcp-serve : aucune adresse Tailscale (100.64.0.0/10) : serveur non démarré", flush=True)
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
