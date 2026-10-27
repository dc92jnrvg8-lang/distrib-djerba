# SPDX-License-Identifier: GPL-3.0-or-later
"""Couche d'abstraction cognitive de Djerba (Cognitive Abstraction Layer).

L'agent ne parle jamais directement à un moteur d'inférence : il passe par
``CognitiveEngine``, qui garantit :

* **Agnosticisme** — entrées et sorties normalisées par les schémas
  Pydantic v2 (``ChatMessage`` → fournisseur → ``ChatResult``) ; un appel
  d'outil mal formé est rejeté avant d'atteindre l'agent.
* **Durcissement préventif** — chaque demande passe par le schéma
  ``UserPrompt`` puis par ``guard.inspect_user`` ; chaque donnée externe
  (sortie d'outil, page web, fichier) par ``guard.wrap_untrusted``.
* **Poignée de main dynamique** — latence mesurée et capacités du modèle lues
  (``/api/show`` : taille, contexte, appels d'outils, réflexion) ; l'invite
  système hôte s'adapte (profil compact, standard ou étendu).
* **Persistance et bascule à chaud** — l'état de session (messages, outils
  actifs, contamination) est écrit de façon atomique et chiffrée, en arrière-
  plan, après chaque étape ; changer de moteur (manuellement ou par repli
  automatique) reprend exactement là où la session en était, sans reconnexion
  ni perte de contexte.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from djerba_ai.core import guard
from djerba_ai.core.config import STATE_HOME
from djerba_ai.core.crypto import Box, CryptoError, write_atomic
from djerba_ai.core.llm import (CloudAPIProvider, LLMError, LocalOllamaProvider, ProviderManager,
                                RemoteTailscaleProvider)
from djerba_ai.core.schemas import Capabilities, ChatMessage, ChatResult, SessionState, UserPrompt

SESSIONS_DIR = STATE_HOME / "djerba-ai" / "sessions"
HANDSHAKE_TTL = 600

#: paramètres d'invite par profil
PROFILES = {
    "compact": {"max_tools": 8, "history": 6, "context_lines": 12,
                "rules": "Réponds en trois phrases au plus. Un seul outil à la fois. Pas de long raisonnement."},
    "standard": {"max_tools": 14, "history": 12, "context_lines": 40, "rules": ""},
    "extended": {"max_tools": 22, "history": 24, "context_lines": 80,
                 "rules": "Tu peux enchaîner plusieurs outils et détailler ton plan avant d'agir."},
}

Event = Callable[[str, dict], None]


class PromptRejected(ValueError):
    """Demande invalide (vide, trop longue, illisible)."""


# ---------------------------------------------------------------- Poignée de main
def _parameters(value: str) -> float:
    match = re.search(r"([\d.]+)\s*([BbMm])", value or "")
    if not match:
        return 0.0
    number = float(match.group(1))
    return number / 1000 if match.group(2).lower() == "m" else number


def profile_for(context_window: int, parameters_b: float, latency_ms: int, kind: str) -> str:
    if kind == "cloud":
        return "extended"
    if (parameters_b and parameters_b < 5) or context_window <= 4096 or latency_ms > 2500:
        return "compact"
    if parameters_b >= 14 and context_window >= 16384:
        return "extended"
    return "standard"


def handshake(provider, timeout: float = 5) -> Capabilities:
    """Mesure la latence et lit les capacités du modèle d'un fournisseur."""
    started = time.monotonic()
    reachable = provider.available(timeout)
    latency = int((time.monotonic() - started) * 1000)
    kind = getattr(provider, "kind", "local")
    model = getattr(provider, "model", "")
    context_window = getattr(provider, "context_window", 128000 if kind == "cloud" else 8192)
    tools, thinking, parameters = True, False, 0.0
    if reachable and isinstance(provider, LocalOllamaProvider):
        try:
            info = provider.show(timeout)
        except LLMError:
            info = {}
        capabilities = info.get("capabilities")
        if isinstance(capabilities, list):
            tools = "tools" in capabilities
            thinking = "thinking" in capabilities
        parameters = _parameters(str(info.get("details", {}).get("parameter_size", "")))
        native = [value for key, value in (info.get("model_info") or {}).items()
                  if key.endswith(".context_length") and isinstance(value, int)]
        if native:
            context_window = min(context_window, native[0])
    return Capabilities(provider=kind, model=model, reachable=reachable, latency_ms=latency,
                        context_window=context_window, tools=tools, thinking=thinking, parameters_b=parameters,
                        profile=profile_for(context_window, parameters, latency, kind))


# ---------------------------------------------------------------- Sessions
class SessionStore:
    """États de session chiffrés, écrits atomiquement par un fil dédié.

    ``save`` ne bloque jamais la boucle de l'agent : l'état est confié à une
    file ; les écritures successives d'une même session sont fusionnées
    (seule la dernière compte). ``flush`` attend que tout soit sur disque
    (avant une bascule de moteur ou la fermeture).
    """

    def __init__(self, directory: Path = SESSIONS_DIR, box: Box | None = None) -> None:
        self.directory = directory
        self.box = box or Box()
        self.pending: dict[int, SessionState] = {}
        self.lock = threading.Lock()
        self.wake: queue.Queue = queue.Queue()
        self.idle = threading.Event()
        self.idle.set()
        threading.Thread(target=self._writer, daemon=True, name="djerba-sessions").start()

    def path(self, conversation: int) -> Path:
        return self.directory / f"{conversation}.session"

    def save(self, state: SessionState) -> None:
        with self.lock:
            self.pending[state.conversation] = state.model_copy(update={"updated": time.time()})
            self.idle.clear()
        self.wake.put(True)

    def flush(self, timeout: float = 10) -> bool:
        self.wake.put(True)
        return self.idle.wait(timeout)

    def _writer(self) -> None:
        while True:
            self.wake.get()
            while True:
                with self.lock:
                    if not self.pending:
                        self.idle.set()
                        break
                    conversation, state = self.pending.popitem()
                data = self.box.encrypt_bytes(state.model_dump_json().encode(), aad="session")
                try:
                    write_atomic(self.path(conversation), data)
                except OSError:
                    continue

    def load(self, conversation: int) -> SessionState | None:
        with self.lock:
            if conversation in self.pending:
                return self.pending[conversation]
        try:
            raw = self.box.decrypt_bytes(self.path(conversation).read_bytes(), aad="session")
            return SessionState.model_validate_json(raw)
        except (OSError, CryptoError, ValidationError, ValueError):
            return None

    def delete(self, conversation: int) -> None:
        with self.lock:
            self.pending.pop(conversation, None)
        self.path(conversation).unlink(missing_ok=True)


# ---------------------------------------------------------------- Moteur
class CognitiveEngine:
    def __init__(self, config, notify: Callable[[str], None] | None = None,
                 sessions: SessionStore | None = None) -> None:
        self.config = config
        self.notify = notify
        self.sessions = sessions
        self._manager: ProviderManager | None = None
        self._signature: tuple = ()
        self._handshakes: dict[tuple, Capabilities] = {}
        self.events: Event | None = None

    # ------------------------------------------------------------ fournisseurs
    def _settings_signature(self) -> tuple:
        keys = ("llm.provider", "llm.fallback_to_local", "llm.remote_timeout", "llm.context_window",
                "llm.local.endpoint", "llm.local.model", "llm.tailscale.host", "llm.tailscale.port",
                "llm.tailscale.model", "llm.cloud.service", "llm.cloud.model")
        return tuple(self.config.get(key) for key in keys)

    @property
    def manager(self) -> ProviderManager:
        """Gestionnaire de fournisseurs, reconstruit à chaud si la configuration change."""
        signature = self._settings_signature()
        if self._manager is None or signature != self._signature:
            previous = self._manager
            self._manager = ProviderManager(self.config, self._on_fallback)
            self._signature = signature
            if previous is not None:
                self._emit("hot_swap", {"from": previous.primary.label, "to": self._manager.primary.label})
        return self._manager

    def _on_fallback(self, message: str) -> None:
        self._emit("hot_swap", {"from": self.manager.primary.label, "to": self.manager.local.label,
                                "reason": message})
        if self.notify:
            self.notify(message)

    def _emit(self, kind: str, payload: dict) -> None:
        if self.events:
            self.events(kind, payload)

    def provider(self, name: str):
        """Fournisseur nommé (local, tailscale, cloud), hors repli."""
        timeout = float(self.config.get("llm.remote_timeout"))
        if name == "tailscale":
            return RemoteTailscaleProvider(self.config.get("llm.tailscale.host"),
                                           int(self.config.get("llm.tailscale.port")),
                                           self.config.get("llm.tailscale.model"),
                                           int(self.config.get("llm.context_window")), timeout)
        if name == "cloud":
            return CloudAPIProvider(self.config.get("llm.cloud.service"), self.config.get("llm.cloud.model"), timeout)
        return LocalOllamaProvider(self.config.get("llm.local.endpoint"), self.config.get("llm.local.model"),
                                   int(self.config.get("llm.context_window")))

    # ------------------------------------------------------------ poignée de main
    def capabilities(self, force: bool = False) -> Capabilities:
        active = self.manager.active
        key = (active.kind, active.model, getattr(active, "endpoint", ""))
        cached = self._handshakes.get(key)
        if cached and not force and time.time() - cached.measured_at < HANDSHAKE_TTL and cached.reachable:
            return cached
        timeout = float(self.config.get("llm.remote_timeout"))
        capabilities = handshake(active, timeout)
        self._handshakes[key] = capabilities
        if isinstance(active, LocalOllamaProvider):
            active.think = capabilities.thinking
        self._emit("handshake", capabilities.model_dump())
        return capabilities

    def profile(self, capabilities: Capabilities | None = None) -> dict:
        capabilities = capabilities or self.capabilities()
        return {"name": capabilities.profile, **PROFILES[capabilities.profile]}

    # ------------------------------------------------------------ ingestion sécurisée
    @staticmethod
    def ingest(text: str, source: str = "chat") -> tuple[UserPrompt, guard.Verdict]:
        try:
            prompt = UserPrompt(text=text, source=source)
        except ValidationError as exc:
            raise PromptRejected(exc.errors()[0].get("msg", "demande invalide")) from exc
        verdict = guard.inspect_user(prompt.text)
        return prompt.model_copy(update={"text": verdict.text}), verdict

    @staticmethod
    def untrusted(source: str, payload: dict | str) -> tuple[ChatMessage, guard.Verdict]:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        framed, verdict = guard.wrap_untrusted(source, text[:12000])
        return ChatMessage(role="tool", content=framed, untrusted=True), verdict

    # ------------------------------------------------------------ inférence
    def chat(self, messages: list[ChatMessage], tools: list[dict] | None = None,
             on_token: Callable[[str], None] | None = None) -> ChatResult:
        manager = self.manager
        capabilities = self.capabilities()
        started = time.monotonic()
        raw = manager.chat([message.wire() for message in messages],
                           tools if capabilities.tools else None, on_token)
        active = manager.active
        try:
            result = ChatResult.model_validate({**raw, "provider": active.kind, "model": active.model,
                                                "latency_ms": int((time.monotonic() - started) * 1000)})
        except ValidationError as exc:
            raise LLMError(f"réponse du moteur non conforme : {exc.errors()[0].get('msg')}") from exc
        from djerba_ai.core import telemetry
        telemetry.observe("djerba_llm_latency_seconds", result.latency_ms / 1000, provider=active.kind)
        telemetry.inc("djerba_llm_requests_total", provider=active.kind)
        if result.thinking:
            self._emit("thinking", {"text": result.thinking[-4000:]})
        return result

    def warmup(self) -> None:
        self.manager.warmup()

    def health(self) -> dict:
        return self.manager.health(5)

    # ------------------------------------------------------------ sessions
    def session(self, conversation: int) -> SessionState:
        state = self.sessions.load(conversation) if self.sessions else None
        return state or SessionState(conversation=conversation)

    def persist(self, state: SessionState) -> None:
        if self.sessions:
            active = self.manager.active
            self.sessions.save(state.model_copy(update={"provider": active.kind, "model": active.model}))

    def swap(self, provider: str) -> Capabilities:
        """Bascule à chaud : état écrit, nouveau fournisseur négocié, session conservée."""
        if self.sessions:
            self.sessions.flush()
        self.config.set("llm.provider", provider)
        self._handshakes.clear()
        return self.capabilities(force=True)
