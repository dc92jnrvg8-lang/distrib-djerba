# SPDX-License-Identifier: GPL-3.0-or-later
"""Moteur cognitif agnostique de Djerba.

``BaseLLMProvider`` et ses trois implémentations :

* ``LocalOllamaProvider``     Ollama sur cette machine (127.0.0.1:11434) ;
* ``RemoteTailscaleProvider`` Ollama sur une machine distante du tailnet
                              (ex. Mac Mini M6 32 Go), avec contrôle de santé
                              Tailscale (pair en ligne) puis HTTP ;
* ``CloudAPIProvider``        OpenAI ou Google Gemini (API compatible OpenAI) :
                              la requête est faite par le démon root, qui seul
                              lit la clé d'API (/etc/djerba/config.toml, 0600) ;
                              la clé ne passe jamais par la session utilisateur.

``ProviderManager`` choisit le fournisseur actif (llm.provider) ; si un
fournisseur distant échoue ou ne répond pas dans le délai (llm.remote_timeout,
5 s), il bascule de façon transparente sur Ollama local et prévient
l'utilisateur (notification Plasma). ``warmup()`` charge le modèle en mémoire
(keep_alive) dès l'ouverture de Spotlight, avant même la frappe.

Tous exposent : ``available()``, ``health()``, ``models()``,
``chat(messages, tools, on_token)`` -> {"content", "tool_calls"}, ``warmup()``.
"""

from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Callable

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
Token = Callable[[str], None]


class LLMError(Exception):
    """Moteur injoignable ou réponse invalide."""


class ProviderTimeout(LLMError):
    """Le fournisseur n'a pas répondu dans le délai imparti."""


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlparse(url).hostname or ""
    # Un moteur local ou du tailnet ne doit jamais passer par un proxy.
    if host in LOCAL_HOSTS or host.startswith("100.") or host.endswith(".ts.net") or "." not in host:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _request(url: str, payload: dict | None = None, timeout: float = 30, headers: dict | None = None,
             method: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        return _opener(url).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LLMError(f"{exc.code} : {detail}") from exc
    except TimeoutError as exc:
        raise ProviderTimeout(f"délai dépassé ({url})") from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise ProviderTimeout(f"délai dépassé ({url})") from exc
        raise LLMError(f"moteur injoignable ({url}) : {reason}") from exc


# ---------------------------------------------------------------- Transport persistant (Ollama)
CONNECT_TIMEOUT = 5.0
#: délai maximal entre deux fragments du flux (le premier fragment, qui attend le
#: chargement du modèle en mémoire, a son propre délai : first_byte_timeout)
READ_TIMEOUT = 60.0
RETRIES = 3
BACKOFF = 0.5
#: fermetures de connexion sans réponse (connexion persistante périmée, moteur
#: redémarré, relais Tailscale coupé) : la requête est rejouée sur une connexion neuve
DISCONNECTED = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                http.client.BadStatusLine)
HEADERS = {"Content-Type": "application/json", "Accept": "application/x-ndjson, application/json",
           "Connection": "keep-alive", "User-Agent": "djerba-ai"}

_POOL: dict[tuple, list] = {}
_POOL_LOCK = threading.Lock()
_POOL_SIZE = 4


class Unreachable(LLMError):
    """Aucune connexion possible (refus, hôte absent) ou fermée sans réponse après les reprises."""

    def __init__(self, message: str, disconnected: bool = False) -> None:
        super().__init__(message)
        #: le serveur a accepté puis fermé la connexion sans répondre (vs. refus de connexion)
        self.disconnected = disconnected


def _http2_client():
    """Client HTTP/2 (httpx + h2) si disponible : négocié par ALPN sur les points d'accès TLS
    (tailscale serve, proxy inverse). Ollama lui-même parle HTTP/1.1 en clair (keep-alive)."""
    import importlib.util
    if importlib.util.find_spec("h2") is None:
        return None
    try:
        import httpx
    except ImportError:
        return None
    return httpx


class Transport:
    """Connexions HTTP persistantes (keep-alive) vers un moteur, réutilisées entre requêtes."""

    def __init__(self, base: str, connect_timeout: float = CONNECT_TIMEOUT, read_timeout: float = READ_TIMEOUT,
                 retries: int = RETRIES, backoff: float = BACKOFF) -> None:
        parsed = urllib.parse.urlparse(base)
        self.base = base.rstrip("/")
        self.scheme = parsed.scheme or "http"
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or (443 if self.scheme == "https" else 80)
        self.prefix = parsed.path.rstrip("/")
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout
        self.retries, self.backoff = max(1, retries), backoff
        self.key = (self.scheme, self.host, self.port)
        self.http2 = self.scheme == "https" and _http2_client() is not None

    # -- réserve de connexions
    def _checkout(self, fresh: bool):
        if not fresh:
            with _POOL_LOCK:
                idle = _POOL.get(self.key, [])
                if idle:
                    return idle.pop()
        factory = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = factory(self.host, self.port, timeout=self.connect_timeout)
        connection.connect()
        return connection

    def _checkin(self, connection) -> None:
        with _POOL_LOCK:
            idle = _POOL.setdefault(self.key, [])
            if len(idle) < _POOL_SIZE:
                idle.append(connection)
                return
        connection.close()

    def request(self, method: str, path: str, payload: dict | None = None,
                first_byte_timeout: float | None = None) -> "StreamResponse":
        if self.http2:
            return self._request_http2(method, path, payload, first_byte_timeout)
        body = json.dumps(payload).encode() if payload is not None else None
        url = f"{self.base}{path}"
        last: BaseException | None = None
        for attempt in range(self.retries):
            # Première tentative : connexion persistante si disponible ; ensuite, toujours neuve.
            try:
                connection = self._checkout(fresh=attempt > 0)
            except TimeoutError as exc:
                raise ProviderTimeout(f"connexion impossible en {self.connect_timeout:.0f} s ({url})") from exc
            except OSError as exc:
                raise Unreachable(f"moteur injoignable ({url}) : {exc}") from exc
            try:
                sock = connection.sock  # http.client l'oublie si le serveur annonce la fermeture
                sock.settimeout(first_byte_timeout or self.read_timeout)
                connection.request(method, f"{self.prefix}{path}", body=body, headers=HEADERS)
                response = connection.getresponse()
                sock.settimeout(self.read_timeout)
            except DISCONNECTED as exc:
                connection.close()
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff * (2 ** attempt))
                continue
            except TimeoutError as exc:
                connection.close()
                raise ProviderTimeout(f"aucune réponse en {first_byte_timeout or self.read_timeout:.0f} s ({url})") \
                    from exc
            except OSError as exc:
                connection.close()
                raise Unreachable(f"moteur injoignable ({url}) : {exc}") from exc
            if response.status >= 400:
                detail = response.read().decode(errors="replace")[:300]
                self._release(connection, response)
                raise LLMError(f"{response.status} : {detail}")
            return StreamResponse(self, connection, response, url)
        raise Unreachable(f"moteur injoignable ({url}) : {last or 'connexion fermée'} "
                          f"— {self.retries} tentatives", disconnected=True)

    def _release(self, connection, response) -> None:
        if response.will_close or not response.isclosed():
            connection.close()
        else:
            self._checkin(connection)

    def _request_http2(self, method: str, path: str, payload: dict | None,
                       first_byte_timeout: float | None) -> "StreamResponse":
        httpx = _http2_client()
        timeout = httpx.Timeout(connect=self.connect_timeout, read=max(first_byte_timeout or 0, self.read_timeout),
                                write=self.read_timeout, pool=self.connect_timeout)
        with _POOL_LOCK:
            client = _POOL.get(("httpx",) + self.key)
            if client is None:
                client = httpx.Client(http2=True, timeout=timeout, headers=HEADERS, trust_env=False)
                _POOL[("httpx",) + self.key] = client
        url = f"{self.base}{path}"
        last: BaseException | None = None
        for attempt in range(self.retries):
            try:
                request = client.build_request(method, url, json=payload, timeout=timeout)
                response = client.send(request, stream=True)
            except httpx.RemoteProtocolError as exc:
                last = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff * (2 ** attempt))
                continue
            except httpx.TimeoutException as exc:
                raise ProviderTimeout(f"délai dépassé ({url})") from exc
            except httpx.TransportError as exc:
                raise Unreachable(f"moteur injoignable ({url}) : {exc}") from exc
            if response.status_code >= 400:
                detail = response.read().decode(errors="replace")[:300]
                response.close()
                raise LLMError(f"{response.status_code} : {detail}")
            return StreamResponse(self, None, response, url)
        raise Unreachable(f"moteur injoignable ({url}) : {last or 'connexion fermée'} — {self.retries} tentatives",
                          disconnected=True)


class StreamResponse:
    """Réponse lue ligne à ligne (NDJSON) ; la connexion revient à la réserve une fois lue."""

    def __init__(self, transport: Transport, connection, response, url: str) -> None:
        self.transport, self.connection, self.response, self.url = transport, connection, response, url
        self.status = getattr(response, "status", None) or getattr(response, "status_code", 0)

    def __enter__(self) -> "StreamResponse":
        return self

    def __exit__(self, *exc) -> None:
        self.close(failed=exc[0] is not None)

    def close(self, failed: bool = False) -> None:
        if self.connection is None:
            self.response.close()
            return
        if failed:
            self.connection.close()
        else:
            self.transport._release(self.connection, self.response)
        self.connection = None

    def __iter__(self):
        try:
            if self.connection is None:
                for line in self.response.iter_lines():
                    yield line.encode() if isinstance(line, str) else line
                return
            while True:
                line = self.response.readline()
                if not line:
                    return
                yield line
        except TimeoutError as exc:
            raise ProviderTimeout(f"flux interrompu : rien reçu depuis {self.transport.read_timeout:.0f} s "
                                  f"({self.url})") from exc
        except (http.client.IncompleteRead, *DISCONNECTED) as exc:
            raise LLMError(f"flux interrompu par le moteur ({self.url}) : {exc}") from exc

    def read(self) -> bytes:
        return b"".join(self)

    def json(self) -> Any:
        return json.loads(self.read() or b"{}")


def local_candidates(port: int, original: str) -> list[str]:
    """Adresses de repli quand le moteur n'écoute pas sur la boucle locale
    (OLLAMA_HOST lié à l'adresse Tailscale ou du réseau local)."""
    hosts: list[str] = []
    if original != "127.0.0.1":
        hosts.append("127.0.0.1")
    if original != "::1":
        hosts.append("[::1]")
    if shutil.which("tailscale"):
        try:
            output = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3).stdout
            hosts += [line.strip() for line in output.splitlines() if line.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        output = subprocess.run(["ip", "-j", "-4", "addr", "show", "scope", "global"], capture_output=True,
                                text=True, timeout=3).stdout
        for interface in json.loads(output or "[]"):
            hosts += [item.get("local", "") for item in interface.get("addr_info", []) if item.get("local")]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    seen: list[str] = []
    for host in hosts:
        if host and host not in seen:
            seen.append(host)
    return [f"http://{host}:{port}" for host in seen]


def parse_openai_stream(response, on_token: Token | None) -> dict[str, Any]:
    """Lit un flux SSE « chat/completions » (OpenAI, Gemini compatible)."""
    content: list[str] = []
    partial: dict[int, dict] = {}
    for raw in response:
        line = raw.decode(errors="replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        choices = json.loads(data).get("choices") or [{}]
        delta = choices[0].get("delta", {})
        if delta.get("content"):
            content.append(delta["content"])
            if on_token:
                on_token(delta["content"])
        for call in delta.get("tool_calls") or []:
            slot = partial.setdefault(call.get("index", len(partial)), {"name": "", "arguments": ""})
            function = call.get("function", {})
            slot["name"] += function.get("name") or ""
            slot["arguments"] += function.get("arguments") or ""
    calls = []
    for slot in partial.values():
        try:
            arguments = json.loads(slot["arguments"] or "{}")
        except ValueError:
            arguments = {}
        calls.append({"name": slot["name"], "arguments": arguments})
    return {"content": "".join(content), "tool_calls": calls}


def openai_messages(messages: list[dict]) -> list[dict]:
    converted = []
    for index, message in enumerate(messages):
        if message["role"] == "tool":
            converted.append({"role": "tool", "tool_call_id": message.get("tool_call_id", f"call{index}"),
                              "content": message["content"]})
        elif message.get("tool_calls"):
            converted.append({"role": "assistant", "content": message.get("content") or None,
                              "tool_calls": [{"id": call.get("id", f"call{index}"), "type": "function",
                                              "function": {"name": call["function"]["name"],
                                                           "arguments": json.dumps(call["function"]["arguments"])}}
                                             for call in message["tool_calls"]]})
        else:
            converted.append({"role": message["role"], "content": message["content"]})
    # Les identifiants d'appel doivent correspondre entre l'assistant et l'outil.
    last_ids: list[str] = []
    for message in converted:
        if message.get("tool_calls"):
            last_ids = [call["id"] for call in message["tool_calls"]]
        elif message["role"] == "tool" and last_ids:
            message["tool_call_id"] = last_ids.pop(0)
    return converted


class BaseLLMProvider(ABC):
    kind = "base"
    label = ""

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    def available(self, timeout: float = 3) -> bool:
        """Le fournisseur répond-il ?"""

    def health(self, timeout: float = 5) -> dict[str, Any]:
        started = time.monotonic()
        ok = self.available(timeout)
        return {"provider": self.kind, "label": self.label, "ok": ok, "model": self.model,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "detail": "connecté" if ok else "injoignable"}

    @abstractmethod
    def models(self) -> list[dict]:
        """Modèles proposés par le fournisseur."""

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_token: Token | None = None) -> dict[str, Any]:
        """Réponse (en continu par on_token) : {"content": str, "tool_calls": [...]}."""

    def warmup(self) -> None:
        """Prépare le modèle (aucun effet par défaut)."""

    def pull(self, model: str, on_progress: Callable[[str, int], None] | None = None) -> None:
        raise LLMError("le téléchargement de modèles n'est possible qu'avec Ollama")


class LocalOllamaProvider(BaseLLMProvider):
    """Ollama : connexions persistantes (keep-alive), délais séparés (connexion 5 s,
    60 s entre deux fragments), reprise sur fermeture sans réponse (3 tentatives,
    attente exponentielle), corps /api/chat validé (Pydantic) et, pour un moteur
    local, repli sur l'adresse Tailscale ou du réseau local si Ollama n'écoute pas
    sur la boucle locale."""

    kind = "local"
    label = "Ollama local"
    #: adresses de repli déjà trouvées (point d'accès configuré -> point d'accès joignable)
    resolved: dict[str, str] = {}

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", model: str = "", context_window: int = 8192,
                 first_byte_timeout: float = 600) -> None:
        super().__init__(model)
        self.configured = endpoint.rstrip("/")
        self.endpoint = self.resolved.get(self.configured, self.configured)
        self.context_window = context_window
        self.first_byte_timeout = first_byte_timeout
        self.think = False
        self.fallback_hosts = self.kind == "local"
        self.read_timeout = READ_TIMEOUT
        self.backoff = BACKOFF

    def transport(self) -> Transport:
        return Transport(self.endpoint, read_timeout=self.read_timeout, backoff=self.backoff)

    def _send(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None,
              first_byte: float | None = None) -> StreamResponse:
        def transport() -> Transport:
            current = self.transport()
            if timeout is not None:
                current.read_timeout = timeout
                current.connect_timeout = min(current.connect_timeout, timeout)
            return current

        try:
            return transport().request(method, path, payload, first_byte)
        except Unreachable:
            if not (self.fallback_hosts and self.relocate()):
                raise
            return transport().request(method, path, payload, first_byte)

    def relocate(self) -> str | None:
        """Cherche Ollama sur les autres adresses de cette machine (boucle IPv6, Tailscale, réseau local)."""
        parsed = urllib.parse.urlparse(self.endpoint)
        if (parsed.hostname or "") not in LOCAL_HOSTS:
            return None
        for candidate in local_candidates(parsed.port or 11434, parsed.hostname or ""):
            if candidate == self.endpoint:
                continue
            try:
                with Transport(candidate, connect_timeout=2, read_timeout=3, retries=1).request(
                        "GET", "/api/version") as response:
                    response.read()
            except LLMError:
                continue
            self.endpoint = candidate
            LocalOllamaProvider.resolved[self.configured] = candidate
            return candidate
        return None

    def available(self, timeout: float = 3) -> bool:
        try:
            with self._send("GET", "/api/version", timeout=timeout) as response:
                response.read()
                return response.status == 200
        except LLMError:
            return False

    def models(self) -> list[dict]:
        with self._send("GET", "/api/tags", timeout=10) as response:
            data = response.json()
        return [{"name": model["name"], "size_gb": round(model.get("size", 0) / 1e9, 1),
                 "family": model.get("details", {}).get("family", ""),
                 "parameters": model.get("details", {}).get("parameter_size", "")}
                for model in data.get("models", [])]

    def warmup(self) -> None:
        # Requête vide : Ollama charge le modèle et le garde 15 min en mémoire.
        with self._send("POST", "/api/generate", {"model": self.model, "prompt": "", "keep_alive": "15m",
                                                  "stream": False}, first_byte=120) as response:
            response.read()

    def pull(self, model: str, on_progress: Callable[[str, int], None] | None = None) -> None:
        with self._send("POST", "/api/pull", {"model": model, "stream": True}, timeout=600) as response:
            for raw in response:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if "error" in event:
                    raise LLMError(event["error"])
                if on_progress:
                    total, done = event.get("total") or 0, event.get("completed") or 0
                    on_progress(event.get("status", ""), int(done * 100 / total) if total else 0)

    def delete(self, model: str) -> None:
        with self._send("DELETE", "/api/delete", {"model": model}, timeout=30) as response:
            response.read()

    def chat_payload(self, messages: list[dict], tools: list[dict] | None = None, stream: bool = True,
                     think: bool | None = None) -> dict[str, Any]:
        """Corps /api/chat validé : messages au format Ollama, « stream » explicite, outils conformes."""
        from pydantic import ValidationError

        from djerba_ai.core.schemas import OllamaChatRequest
        if not self.model:
            raise LLMError("aucun modèle configuré pour Ollama (llm.local.model)")
        try:
            request = OllamaChatRequest(model=self.model, messages=messages, stream=stream, tools=tools or None,
                                        think=(self.think or None) if think is None else (think or None),
                                        keep_alive="15m",
                                        options={"num_ctx": self.context_window, "temperature": 0.3})
        except ValidationError as exc:
            error = exc.errors()[0]
            where = ".".join(str(part) for part in error.get("loc", ()))
            raise LLMError(f"requête /api/chat invalide ({where}) : {error.get('msg', '')}") from exc
        return request.wire()

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_token: Token | None = None) -> dict[str, Any]:
        payload = self.chat_payload(messages, tools)
        try:
            return self._stream_chat(payload, on_token)
        except Unreachable as exc:
            if not exc.disconnected or ("tools" not in payload and "think" not in payload):
                raise
        # Le moteur ferme la connexion sans répondre à chaque tentative : certaines versions
        # d'Ollama s'interrompent sur les outils ou la réflexion d'un modèle qui ne les
        # prend pas en charge. Dernier essai avec une requête minimale.
        return self._stream_chat(self.chat_payload(messages, None, think=False), on_token)

    def _stream_chat(self, payload: dict, on_token: Token | None) -> dict[str, Any]:
        content, calls, thinking = [], [], []
        with self._send("POST", "/api/chat", payload, first_byte=self.first_byte_timeout) as response:
            for raw in response:
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except ValueError as exc:
                    raise LLMError(f"réponse du moteur illisible : {raw[:120]!r}") from exc
                if "error" in event:
                    raise LLMError(event["error"])
                message = event.get("message", {})
                if message.get("thinking"):
                    thinking.append(message["thinking"])
                piece = message.get("content", "")
                if piece:
                    content.append(piece)
                    if on_token:
                        on_token(piece)
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    arguments = function.get("arguments") or {}
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments or "{}")
                    calls.append({"name": function.get("name", ""), "arguments": arguments})
        return {"content": "".join(content), "tool_calls": calls, "thinking": "".join(thinking)}

    def show(self, timeout: float = 5) -> dict[str, Any]:
        """Fiche du modèle (contexte, taille, capacités) : /api/show."""
        with self._send("POST", "/api/show", {"model": self.model}, timeout=timeout) as response:
            return response.json()


# Compatibilité : nom historique.
OllamaClient = LocalOllamaProvider


class RemoteTailscaleProvider(LocalOllamaProvider):
    """Ollama sur une machine du tailnet (MagicDNS ou adresse 100.x)."""

    kind = "tailscale"
    label = "Mac Mini (Tailscale)"

    def __init__(self, host: str, port: int, model: str, context_window: int = 8192, timeout: float = 5) -> None:
        # Le délai « remote_timeout » (5 s) borne le contrôle de santé et la
        # connexion ; la génération elle-même (premier jeton d'un grand modèle
        # sur un long contexte) peut légitimement prendre plus longtemps.
        super().__init__(f"http://{host}:{port}", model, context_window, first_byte_timeout=120)
        self.host = host
        self.timeout = timeout

    def peer_status(self) -> dict[str, Any]:
        """État du pair selon « tailscale status --json » (si Tailscale est installé)."""
        if shutil.which("tailscale") is None:
            return {"tailscale": False}
        try:
            output = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True,
                                    timeout=self.timeout, check=False).stdout
            status = json.loads(output or "{}")
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"tailscale": True, "running": False}
        if status.get("BackendState") != "Running":
            return {"tailscale": True, "running": False}
        wanted = self.host.lower().rstrip(".")
        for peer in (status.get("Peer") or {}).values():
            names = {str(peer.get("HostName", "")).lower(), str(peer.get("DNSName", "")).lower().rstrip("."),
                     str(peer.get("DNSName", "")).lower().split(".")[0]}
            if wanted in names or wanted in peer.get("TailscaleIPs", []):
                return {"tailscale": True, "running": True, "found": True, "online": bool(peer.get("Online"))}
        return {"tailscale": True, "running": True, "found": False}

    def available(self, timeout: float | None = None) -> bool:
        if not self.host:
            return False
        peer = self.peer_status()
        if peer.get("running") is False or (peer.get("found") and not peer.get("online")):
            return False
        return super().available(timeout or self.timeout)

    def health(self, timeout: float = 5) -> dict[str, Any]:
        result = super().health(timeout)
        peer = self.peer_status()
        if not self.host:
            result["detail"] = "aucune machine configurée"
        elif peer.get("tailscale") is False:
            result["detail"] += " (Tailscale non installé : connexion directe tentée)"
        elif peer.get("running") is False:
            result["detail"] = "Tailscale arrêté sur cet ordinateur"
        elif peer.get("found") is False:
            result["detail"] = f"« {self.host} » introuvable dans le tailnet"
        elif not peer.get("online"):
            result["detail"] = f"« {self.host} » est hors ligne"
        result["peer"] = peer
        return result



class CloudAPIProvider(BaseLLMProvider):
    """OpenAI / Gemini via le démon root (la clé ne quitte jamais root)."""

    kind = "cloud"

    def __init__(self, service: str, model: str, timeout: float = 5) -> None:
        super().__init__(model)
        self.service = service
        self.timeout = timeout
        self.label = {"openai": "OpenAI", "gemini": "Google Gemini"}.get(service, "API compatible OpenAI")

    def _call(self, method: str, params: dict, on_event=None, timeout: float = 600):
        from djerba_ai.core import ipc
        try:
            if on_event:
                return ipc.call_stream(method, params, on_event, timeout=timeout)
            return ipc.call(method, params, timeout=timeout)
        except ipc.RPCError as exc:
            if "délai" in str(exc):
                raise ProviderTimeout(str(exc)) from exc
            raise LLMError(str(exc)) from exc

    def available(self, timeout: float = 5) -> bool:
        try:
            return bool(self._call("llm.cloud_health", {}, timeout=timeout + 5).get("ok"))
        except LLMError:
            return False

    def health(self, timeout: float = 5) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = self._call("llm.cloud_health", {}, timeout=timeout + 5)
        except LLMError as exc:
            result = {"ok": False, "detail": str(exc)}
        result.update(provider=self.kind, label=self.label, model=self.model,
                      latency_ms=int((time.monotonic() - started) * 1000))
        return result

    def models(self) -> list[dict]:
        return self._call("llm.cloud_models", {}, timeout=30).get("models", [])

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_token: Token | None = None) -> dict[str, Any]:
        def on_event(method: str, params: dict) -> None:
            if method == "llm.token" and on_token:
                on_token(params.get("text", ""))

        return self._call("llm.cloud_chat", {"messages": messages, "tools": tools or []}, on_event, timeout=600)


def cloud_request(service: str, endpoint: str, api_key: str, model: str, messages: list[dict],
                  tools: list[dict], on_token: Token | None, timeout: float) -> dict[str, Any]:
    """Requête « chat/completions » réelle (exécutée par le démon root)."""
    payload: dict[str, Any] = {"model": model, "messages": openai_messages(messages), "stream": True,
                               "temperature": 0.3}
    if tools:
        payload["tools"] = tools
    with _request(f"{endpoint.rstrip('/')}/chat/completions", payload, timeout=timeout,
                  headers={"Authorization": f"Bearer {api_key}"}) as response:
        return parse_openai_stream(response, on_token)


def cloud_models(endpoint: str, api_key: str, timeout: float = 10) -> list[dict]:
    with _request(f"{endpoint.rstrip('/')}/models", timeout=timeout,
                  headers={"Authorization": f"Bearer {api_key}"}) as response:
        data = json.load(response)
    return [{"name": str(model.get("id", "")).removeprefix("models/"), "size_gb": 0, "family": "", "parameters": ""}
            for model in data.get("data", [])]


class ProviderManager(BaseLLMProvider):
    """Fournisseur actif + repli transparent sur Ollama local."""

    def __init__(self, config, notify: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.notify = notify
        self.local = LocalOllamaProvider(config.get("llm.local.endpoint"), config.get("llm.local.model"),
                                         int(config.get("llm.context_window")))
        provider = config.get("llm.provider")
        timeout = float(config.get("llm.remote_timeout"))
        if provider == "tailscale":
            self.primary: BaseLLMProvider = RemoteTailscaleProvider(
                config.get("llm.tailscale.host"), int(config.get("llm.tailscale.port")),
                config.get("llm.tailscale.model"), int(config.get("llm.context_window")), timeout)
        elif provider == "cloud":
            self.primary = CloudAPIProvider(config.get("llm.cloud.service"), config.get("llm.cloud.model"), timeout)
        else:
            self.primary = self.local
        super().__init__(self.primary.model)
        self.kind = self.primary.kind
        self.label = self.primary.label
        self.active: BaseLLMProvider = self.primary
        self.fell_back = False

    @property
    def remote(self) -> bool:
        return self.primary is not self.local

    def _fallback(self, reason: str) -> bool:
        if not self.remote or not self.config.get("llm.fallback_to_local") or not self.local.available(2):
            return False
        self.active = self.local
        self.fell_back = True
        message = (f"{self.primary.label} indisponible ({reason}) : Djerba utilise le modèle local "
                   f"{self.local.model}.")
        if self.notify:
            self.notify(message)
        notify_desktop("Djerba : bascule sur le modèle local", message)
        return True

    def available(self, timeout: float = 3) -> bool:
        if self.primary.available(timeout):
            return True
        return self.remote and bool(self.config.get("llm.fallback_to_local")) and self.local.available(timeout)

    def health(self, timeout: float = 5) -> dict[str, Any]:
        result = self.primary.health(timeout)
        if self.remote:
            result["fallback"] = self.local.health(2) if self.config.get("llm.fallback_to_local") else None
        return result

    def models(self) -> list[dict]:
        return self.active.models()

    def warmup(self) -> None:
        try:
            if self.primary.available(float(self.config.get("llm.remote_timeout"))):
                self.primary.warmup()
            elif self.remote and self.config.get("llm.fallback_to_local"):
                self.local.warmup()
        except LLMError:
            pass

    def pull(self, model: str, on_progress: Callable[[str, int], None] | None = None) -> None:
        self.local.pull(model, on_progress)

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_token: Token | None = None) -> dict[str, Any]:
        self.active = self.primary
        if self.remote and not self.primary.available(float(self.config.get("llm.remote_timeout"))):
            if self._fallback("contrôle de santé en échec"):
                return self.local.chat(messages, tools, on_token)
            raise LLMError(f"{self.primary.label} injoignable")
        try:
            return self.primary.chat(messages, tools, on_token)
        except LLMError as exc:
            if self._fallback("délai dépassé" if isinstance(exc, ProviderTimeout) else str(exc)[:80]):
                return self.local.chat(messages, tools, on_token)
            raise


def notify_desktop(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(["notify-send", "--app-name=Djerba", "--icon=djerba-ai", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def client_from_config(config, notify: Callable[[str], None] | None = None) -> ProviderManager:
    return ProviderManager(config, notify)
