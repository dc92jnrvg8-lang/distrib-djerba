# SPDX-License-Identifier: GPL-3.0-or-later
"""Registre des moteurs d'IA : n'importe quel moteur devient un agent complet de l'OS.

Plusieurs moteurs se configurent en parallèle — Ollama local, Ollama sur une
autre machine (Tailscale, réseau local), **Jarvis** (assistant de
l'utilisateur sur une autre machine, contrat : docs/JARVIS-CONTRAT.md),
OpenAI, Google Gemini, Anthropic, et tout serveur compatible OpenAI — chacun
avec un nom, une icône, une couleur, un rôle, un profil de capacité mesuré et
ses règles de confidentialité.

Les **outils appartiennent à Djerba** : quel que soit le moteur, il ne fait
que proposer des appels d'outils, validés par schéma puis décidés par la
politique du démon root (core.policy). Seule la qualité du raisonnement
change d'un moteur à l'autre.

Stockage :

* ``/etc/djerba/engines.json`` (root, 0600) : moteurs **et secrets** (jeton
  de Jarvis, clés d'API), écrit seulement par le démon root ;
* ``/etc/djerba/engines.public.json`` (0644) : vue sans secret, lue par la
  session (``secret_set`` indique seulement qu'un secret est enregistré).

Les moteurs « historiques » de la configuration (``llm.local.*``,
``llm.tailscale.*``, ``llm.cloud.*``) apparaissent automatiquement sous les
identifiants ``local``, ``tailscale`` et ``cloud``.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from djerba_ai.core import llm
from djerba_ai.core.config import CLOUD_ENDPOINTS, CONFIG_DIR, CONFIG_HOME

ENGINES_FILE = Path(os.environ.get("DJERBA_ENGINES_FILE", str(CONFIG_DIR / "engines.json")))
PUBLIC_FILE = ENGINES_FILE.with_name("engines.public.json")
USER_FALLBACK = CONFIG_HOME / "djerba-ai" / "engines.json"
DEFAULT_ENDPOINTS = {**CLOUD_ENDPOINTS, "anthropic": "https://api.anthropic.com/v1"}
DEFAULT_MODELS = {"openai": "gpt-4o-mini", "gemini": "gemini-2.0-flash", "anthropic": "claude-sonnet-5-5"}
#: moteurs relayés par le démon root (secret requis ou API distante authentifiée)
RELAYED = {"jarvis", "openai", "gemini", "anthropic", "openai_compat"}
KIND_LABELS = {"ollama": "Ollama", "jarvis": "Jarvis", "openai": "OpenAI", "gemini": "Google Gemini",
               "anthropic": "Anthropic", "openai_compat": "API compatible OpenAI"}


class EngineError(Exception):
    """Moteur inconnu, invalide ou injoignable."""


# ---------------------------------------------------------------- Vue de la session
def builtin(config) -> list[dict]:
    """Moteurs issus de la configuration historique (llm.*)."""
    engines = [{"id": "local", "kind": "ollama", "name": "Ollama local", "icon": "computer", "color": "#2DD4BF",
                "role": "Moteur local : rien ne quitte la machine", "endpoint": config.get("llm.local.endpoint"),
                "model": config.get("llm.local.model"), "context_window": int(config.get("llm.context_window")),
                "share": {"contexte": True, "memoire": True}, "builtin": True, "secret_set": False}]
    if config.get("llm.tailscale.host"):
        engines.append({"id": "tailscale", "kind": "ollama", "name": "Machine distante (Tailscale)",
                        "icon": "network-server", "color": "#38BDF8", "role": "Ollama sur une machine du tailnet",
                        "endpoint": f"http://{config.get('llm.tailscale.host')}:{config.get('llm.tailscale.port')}",
                        "model": config.get("llm.tailscale.model"),
                        "context_window": int(config.get("llm.context_window")),
                        "share": {"contexte": True, "memoire": False}, "builtin": True, "secret_set": False})
    if config.get("llm.cloud.api_key_set") or config.get("llm.provider") == "cloud":
        service = config.get("llm.cloud.service")
        engines.append({"id": "cloud", "kind": {"custom": "openai_compat"}.get(service, service),
                        "name": KIND_LABELS.get(service, "API en ligne"), "icon": "cloud", "color": "#A78BFA",
                        "role": "API en ligne (clé lue par root)", "endpoint": config.get("llm.cloud.endpoint"),
                        "model": config.get("llm.cloud.model"), "context_window": 128000,
                        "share": {"contexte": True, "memoire": False}, "builtin": True,
                        "secret_set": bool(config.get("llm.cloud.api_key_set"))})
    return engines


def registered() -> list[dict]:
    """Moteurs ajoutés par l'utilisateur (vue publique du démon, ou repli par utilisateur sans démon)."""
    for path in (PUBLIC_FILE, USER_FALLBACK):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return [item for item in data.get("engines", []) if isinstance(item, dict) and item.get("id")]
    return []


def catalog(config) -> list[dict]:
    seen, engines = set(), []
    for engine in registered() + builtin(config):
        if engine["id"] not in seen:
            seen.add(engine["id"])
            engines.append(engine)
    return engines


def find(engine_id: str, config) -> dict | None:
    return next((engine for engine in catalog(config) if engine["id"] == engine_id), None)


def active_id(config) -> str:
    """Moteur actif : llm.engine, sinon le fournisseur historique (local, tailscale, cloud)."""
    return str(config.get("llm.engine") or config.get("llm.provider") or "local")


def make_provider(engine: dict, config, timeout: float = 5) -> llm.BaseLLMProvider:
    """Moteur -> fournisseur : Ollama en direct, les autres relayés par le démon root."""
    kind = engine.get("kind")
    if engine.get("id") == "cloud" and engine.get("builtin"):
        return llm.CloudAPIProvider(config.get("llm.cloud.service"), config.get("llm.cloud.model"), timeout)
    if kind == "ollama":
        parsed = urllib.parse.urlparse(str(engine.get("endpoint") or "http://127.0.0.1:11434"))
        # « tailscale » reste distant même sur la boucle locale (relais local vers le tailnet) : contrôle de
        # santé, délais et repli sur le modèle local s'appliquent.
        if (parsed.hostname or "") in llm.LOCAL_HOSTS and engine.get("id") != "tailscale":
            provider: llm.BaseLLMProvider = llm.LocalOllamaProvider(
                str(engine.get("endpoint")), str(engine.get("model", "")), int(engine.get("context_window", 8192)))
        else:
            provider = llm.RemoteTailscaleProvider(parsed.hostname or "", parsed.port or 11434,
                                                   str(engine.get("model", "")),
                                                   int(engine.get("context_window", 8192)), timeout)
        provider.label = str(engine.get("name", provider.label))
        provider.kind = "local" if engine.get("id") == "local" else "tailscale"
        provider.engine_id = str(engine["id"])  # type: ignore[attr-defined]
        provider.engine = engine  # type: ignore[attr-defined]
        return provider
    if kind in RELAYED:
        return llm.RelayProvider(engine, timeout)
    raise EngineError(f"type de moteur inconnu : {kind}")


# ---------------------------------------------------------------- Démon root
def private_address(url: str) -> bool:
    """Adresse du réseau privé : boucle locale, Tailscale (100.64.0.0/10, *.ts.net), réseau local."""
    host = urllib.parse.urlparse(url).hostname or ""
    if host.endswith(".ts.net") or host in ("localhost",):
        return True
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not (ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")):
            return False
    return bool(addresses)


class EngineStore:
    """Registre root (secrets compris) et sa vue publique."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ENGINES_FILE
        self.public = self.path.with_name("engines.public.json")

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("engines", [])
        data.setdefault("secrets", {})
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(".engines.json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)
        public = {"engines": [dict(engine, secret_set=bool(data["secrets"].get(engine["id"])))
                              for engine in data["engines"]]}
        temporary = self.public.with_name(".engines.public.json.tmp")
        temporary.write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o644)
        os.replace(temporary, self.public)

    def list(self) -> list[dict]:
        data = self.load()
        return [dict(engine, secret_set=bool(data["secrets"].get(engine["id"]))) for engine in data["engines"]]

    def get(self, engine_id: str) -> tuple[dict, str]:
        data = self.load()
        for engine in data["engines"]:
            if engine["id"] == engine_id:
                return engine, str(data["secrets"].get(engine_id, ""))
        raise EngineError(f"moteur inconnu : {engine_id}")

    def save(self, engine: dict, secret: str = "") -> dict:
        if engine["id"] in ("local", "tailscale", "cloud"):
            raise EngineError("identifiant réservé aux moteurs de la configuration (local, tailscale, cloud)")
        if engine["kind"] == "jarvis" and engine.get("endpoint") and not private_address(engine["endpoint"]):
            raise EngineError("Jarvis doit être joignable sur le réseau privé (Tailscale ou réseau local)")
        data = self.load()
        previous = next((item for item in data["engines"] if item["id"] == engine["id"]), {})
        merged = {**engine, "profile": previous.get("profile", {})}
        data["engines"] = [item for item in data["engines"] if item["id"] != engine["id"]] + [merged]
        if secret:
            data["secrets"][engine["id"]] = secret
        self._write(data)
        return dict(merged, secret_set=bool(data["secrets"].get(engine["id"])))

    def remove(self, engine_id: str) -> None:
        data = self.load()
        data["engines"] = [item for item in data["engines"] if item["id"] != engine_id]
        data["secrets"].pop(engine_id, None)
        self._write(data)

    def set_profile(self, engine_id: str, profile: dict) -> None:
        data = self.load()
        for engine in data["engines"]:
            if engine["id"] == engine_id:
                engine["profile"] = profile
        self._write(data)

    def measured_cap(self, engine_id: str) -> int:
        for engine in self.load()["engines"]:
            if engine["id"] == engine_id:
                cap = (engine.get("profile") or {}).get("cap")
                return int(cap) if isinstance(cap, int) else 3
        return 3


def engine_request(engine: dict, secret: str, messages: list[dict], tools: list[dict],
                   on_token: Callable[[str], None] | None, timeout: float = 600,
                   json_mode: bool = False) -> dict:
    """Requête réelle vers un moteur relayé (exécutée par le démon root, seul à lire le secret)."""
    kind = engine["kind"]
    endpoint = str(engine.get("endpoint") or DEFAULT_ENDPOINTS.get(kind, ""))
    model = str(engine.get("model") or DEFAULT_MODELS.get(kind, ""))
    if not endpoint:
        raise EngineError("aucune adresse configurée pour ce moteur")
    if kind == "anthropic":
        if not secret:
            raise EngineError("aucune clé d'API enregistrée pour ce moteur")
        return llm.anthropic_request(endpoint, secret, model, messages, tools, on_token, timeout)
    if kind == "jarvis":
        # Contrat Jarvis (docs/JARVIS-CONTRAT.md) : API compatible OpenAI sous /v1, jeton d'appairage.
        return llm.cloud_request("jarvis", endpoint.rstrip("/") + "/v1", secret, model or "jarvis", messages,
                                 tools, on_token, timeout, json_mode)
    if not secret and kind != "openai_compat":
        raise EngineError("aucune clé d'API enregistrée pour ce moteur")
    return llm.cloud_request(kind, endpoint, secret, model, messages, tools, on_token, timeout, json_mode)


def engine_health(engine: dict, secret: str, timeout: float = 10) -> dict:
    kind = engine["kind"]
    endpoint = str(engine.get("endpoint") or DEFAULT_ENDPOINTS.get(kind, "")).rstrip("/")
    try:
        if kind == "jarvis":
            with llm._request(f"{endpoint}/v1/health", timeout=timeout,
                              headers={"Authorization": f"Bearer {secret}"}) as response:
                data = json.load(response)
            return {"ok": True, "detail": f"connecté ({data.get('name', 'Jarvis')})"}
        if kind == "anthropic":
            with llm._request(f"{endpoint}/models", timeout=timeout,
                              headers={"x-api-key": secret, "anthropic-version": llm.ANTHROPIC_VERSION}) as response:
                count = len(json.load(response).get("data", []))
            return {"ok": True, "detail": f"connecté ({count} modèles)"}
        models = llm.cloud_models(endpoint, secret, timeout)
        return {"ok": True, "detail": f"connecté ({len(models)} modèles)"}
    except (llm.LLMError, ValueError, OSError) as exc:
        return {"ok": False, "detail": str(exc)}


def pair_jarvis(address: str, code: str, client: str = "") -> dict:
    """Appairage (contrat Jarvis) : échange du code à 6 chiffres affiché par Jarvis contre un jeton."""
    if not private_address(address):
        raise EngineError("Jarvis doit être joignable sur le réseau privé (Tailscale ou réseau local)")
    payload = {"code": code, "client": "djerba-os", "name": client or socket.gethostname()}
    try:
        with llm._request(f"{address.rstrip('/')}/v1/pair", payload, timeout=15) as response:
            data = json.load(response)
    except (llm.LLMError, ValueError) as exc:
        raise EngineError(f"appairage refusé : {exc}") from exc
    token = str(data.get("token", ""))
    if not re.fullmatch(r"[\x21-\x7e]{16,400}", token):
        raise EngineError("réponse d'appairage invalide (jeton absent)")
    return {"token": token, "name": str(data.get("name", "Jarvis"))[:40],
            "model": str(data.get("model", "jarvis"))[:120], "expires": data.get("expires")}


# ---------------------------------------------------------------- Profil de capacité
BENCH_TOOL = {"type": "function", "function": {
    "name": "time_status", "description": "Heure, fuseau horaire et synchronisation NTP.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}


def measure(provider: llm.BaseLLMProvider, chat: Callable[..., dict] | None = None) -> dict:
    """Mini-banc à l'ajout d'un moteur : appel d'outil, JSON, vitesse, code. Résultat en langage simple
    et plafond de niveau proposé à la politique (un moteur peu fiable reste utile pour les tâches simples)."""
    chat = chat or provider.chat
    score, details = 0, {}
    started = time.monotonic()
    try:
        reply = chat([{"role": "user", "content": "Quelle heure est-il sur cet ordinateur ? Utilise l'outil."}],
                     [BENCH_TOOL], None)
        details["outil"] = any(call.get("name") == "time_status" for call in reply.get("tool_calls") or [])
    except llm.LLMError as exc:
        return {"reachable": False, "label": "Injoignable", "cap": 0, "detail": str(exc)}
    elapsed = time.monotonic() - started
    try:
        reply = chat([{"role": "user", "content": 'Réponds uniquement par ce JSON exact : {"ok": true, "n": 3}'}],
                     None, None)
        match = re.search(r"\{.*\}", reply.get("content", ""), re.DOTALL)
        details["json"] = bool(match) and json.loads(match.group(0)) == {"ok": True, "n": 3}
    except (llm.LLMError, ValueError):
        details["json"] = False
    try:
        reply = chat([{"role": "user", "content": "Écris uniquement une fonction Python add(a, b) qui renvoie "
                                                  "a + b, sans explication."}], None, None)
        code = re.sub(r"^```(?:python)?|```$", "", reply.get("content", "").strip(), flags=re.MULTILINE)
        compile(code, "<banc>", "exec")
        details["code"] = bool(re.search(r"def\s+add\s*\(\s*a\s*,\s*b\s*\)", code))
    except (llm.LLMError, SyntaxError, ValueError):
        details["code"] = False
    score = sum(1 for key in ("outil", "json", "code") if details.get(key))
    if details.get("outil") and score == 3:
        label, cap = "Très adapté", 3
    elif details.get("outil"):
        label, cap = "Adapté", 2
    else:
        label, cap = "Limité : réparations simples seulement", 1
    return {"reachable": True, "label": label, "cap": cap, "score": score, "latency_s": round(elapsed, 2),
            "measured": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **details}
