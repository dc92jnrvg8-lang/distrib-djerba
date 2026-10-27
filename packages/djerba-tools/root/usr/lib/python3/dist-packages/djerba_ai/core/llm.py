# SPDX-License-Identifier: GPL-3.0-or-later
"""Moteurs de langage de l'agent : Ollama (local) ou API compatible OpenAI.

Le modèle et le moteur se changent à tout moment (réglages ou outil
model_set). Les deux clients exposent la même interface :

* ``available()`` : le moteur répond-il ?
* ``models()`` : modèles disponibles ;
* ``chat(messages, tools, on_token)`` : réponse en continu, renvoie
  ``{"content": str, "tool_calls": [{"name": str, "arguments": dict}]}`` ;
* ``pull(model, on_progress)`` (Ollama) : téléchargement d'un modèle.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LLMError(Exception):
    """Moteur injoignable ou réponse invalide."""


def _opener(url: str) -> urllib.request.OpenerDirector:
    host = urllib.parse.urlparse(url).hostname or ""
    if host in LOCAL_HOSTS:
        # Un moteur local ne doit jamais passer par un proxy (ni par Tor).
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _request(url: str, payload: dict | None = None, timeout: float = 30, headers: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                              **(headers or {})})
    try:
        return _opener(url).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LLMError(f"{exc.code} : {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise LLMError(f"moteur injoignable ({url}) : {getattr(exc, 'reason', exc)}") from exc


class OllamaClient:
    kind = "ollama"

    def __init__(self, endpoint: str, model: str, context_window: int = 8192) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.context_window = context_window

    def available(self) -> bool:
        try:
            with _request(f"{self.endpoint}/api/version", timeout=3) as response:
                return response.status == 200
        except LLMError:
            return False

    def models(self) -> list[dict]:
        with _request(f"{self.endpoint}/api/tags", timeout=10) as response:
            data = json.load(response)
        return [{"name": model["name"], "size_gb": round(model.get("size", 0) / 1e9, 1),
                 "family": model.get("details", {}).get("family", ""),
                 "parameters": model.get("details", {}).get("parameter_size", "")}
                for model in data.get("models", [])]

    def pull(self, model: str, on_progress: Callable[[str, int], None] | None = None) -> None:
        with _request(f"{self.endpoint}/api/pull", {"model": model, "stream": True}, timeout=3600) as response:
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
        request = urllib.request.Request(f"{self.endpoint}/api/delete", data=json.dumps({"model": model}).encode(),
                                         method="DELETE", headers={"Content-Type": "application/json"})
        try:
            _opener(self.endpoint).open(request, timeout=30).close()
        except (urllib.error.URLError, OSError) as exc:
            raise LLMError(str(exc)) from exc

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model, "messages": messages, "stream": True,
            "options": {"num_ctx": self.context_window, "temperature": 0.3},
        }
        if tools:
            payload["tools"] = tools
        content, calls = [], []
        with _request(f"{self.endpoint}/api/chat", payload, timeout=600) as response:
            for raw in response:
                if not raw.strip():
                    continue
                event = json.loads(raw)
                if "error" in event:
                    raise LLMError(event["error"])
                message = event.get("message", {})
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
        return {"content": "".join(content), "tool_calls": calls}


class OpenAIClient:
    """API compatible OpenAI (llama.cpp server, LM Studio, vLLM, services en ligne)."""

    kind = "openai"

    def __init__(self, endpoint: str, model: str, api_key: str = "") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def available(self) -> bool:
        try:
            self.models()
            return True
        except (LLMError, ValueError, KeyError):
            return False

    def models(self) -> list[dict]:
        with _request(f"{self.endpoint}/models", timeout=10, headers=self.headers) as response:
            data = json.load(response)
        return [{"name": model["id"], "size_gb": 0, "family": "", "parameters": ""} for model in data.get("data", [])]

    def pull(self, model: str, on_progress: Callable[[str, int], None] | None = None) -> None:
        raise LLMError("le téléchargement de modèles n'est possible qu'avec Ollama")

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_token: Callable[[str], None] | None = None) -> dict[str, Any]:
        converted = []
        for message in messages:
            if message["role"] == "tool":
                converted.append({"role": "tool", "tool_call_id": message.get("tool_call_id", "call"),
                                  "content": message["content"]})
            elif message.get("tool_calls"):
                converted.append({"role": "assistant", "content": message.get("content") or None,
                                  "tool_calls": [{"id": call.get("id", f"call{i}"), "type": "function",
                                                  "function": {"name": call["function"]["name"],
                                                               "arguments": json.dumps(call["function"]["arguments"])}}
                                                 for i, call in enumerate(message["tool_calls"])]})
            else:
                converted.append({"role": message["role"], "content": message["content"]})
        payload: dict[str, Any] = {"model": self.model, "messages": converted, "stream": True, "temperature": 0.3}
        if tools:
            payload["tools"] = tools
        content: list[str] = []
        partial: dict[int, dict] = {}
        with _request(f"{self.endpoint}/chat/completions", payload, timeout=600, headers=self.headers) as response:
            for raw in response:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0].get("delta", {})
                if delta.get("content"):
                    content.append(delta["content"])
                    if on_token:
                        on_token(delta["content"])
                for call in delta.get("tool_calls") or []:
                    slot = partial.setdefault(call.get("index", 0), {"name": "", "arguments": ""})
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


def client_from_config(config) -> OllamaClient | OpenAIClient:
    if config.get("provider") == "openai" and config.get("openai_endpoint"):
        return OpenAIClient(config.get("openai_endpoint"), config.get("model"), config.get("openai_api_key"))
    return OllamaClient(config.get("ollama_endpoint"), config.get("model"), int(config.get("context_window")))
