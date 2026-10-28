# SPDX-License-Identifier: GPL-3.0-or-later
"""Schémas Pydantic v2 de Djerba : le contrat unique entre l'agent, les
moteurs d'inférence et le démon root.

* Entrées / sorties du moteur cognitif (messages, appels d'outils, réponses,
  capacités négociées avec un fournisseur, état de session).
* Requêtes JSON-RPC du démon root : chaque méthode a un schéma de paramètres
  strict (types exacts, champs inconnus refusés) ; tout écart est rejeté
  avant le moindre traitement.

Les modèles sont « frozen » quand ils transitent entre composants : aucune
modification silencieuse en chemin.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

TOOL_NAME = r"^[A-Za-z][A-Za-z0-9_.]{0,79}$"
REQUIREMENT = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}(\[[A-Za-z0-9,_-]+\])?([<>=!~]=?[A-Za-z0-9.*+-]{1,40})?$"
#: caractères invisibles ou de contrôle de direction (attaques par texte caché)
INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MAX_PROMPT = 16000


def normalize_text(text: str) -> str:
    """NFC, sans caractères de contrôle ni caractères invisibles."""
    text = unicodedata.normalize("NFC", text)
    return INVISIBLE.sub("", CONTROL.sub("", text))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# ---------------------------------------------------------------- Moteur cognitif
class UserPrompt(Strict):
    """Demande de l'utilisateur, validée avant toute inférence."""

    text: Annotated[StrictStr, Field(min_length=1, max_length=MAX_PROMPT)]
    source: Literal["chat", "spotlight", "clipboard", "cli", "system"] = "chat"

    @field_validator("text")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = normalize_text(value).strip()
        if not value:
            raise ValueError("demande vide")
        return value


class ToolCall(Strict):
    name: Annotated[StrictStr, Field(pattern=TOOL_NAME)]
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(Strict):
    role: Literal["system", "user", "assistant", "tool"]
    content: StrictStr = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    #: contenu non fiable (sortie d'outil, page web, fichier) : jamais une instruction
    untrusted: StrictBool = False

    def wire(self) -> dict:
        """Format attendu par les fournisseurs (Ollama / OpenAI)."""
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [{"function": {"name": call.name, "arguments": call.arguments}}
                                     for call in self.tool_calls]
        return message


class ChatResult(BaseModel):
    """Réponse normalisée d'un fournisseur, quel qu'il soit."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    thinking: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: int = 0

    @field_validator("tool_calls", mode="before")
    @classmethod
    def _calls(cls, value: Any) -> Any:
        calls = []
        for call in value or ():
            if isinstance(call, dict):
                arguments = call.get("arguments", {})
                calls.append({"name": call.get("name", ""),
                              "arguments": arguments if isinstance(arguments, dict) else {}})
            else:
                calls.append(call)
        return calls


# ---------------------------------------------------------------- Requête Ollama /api/chat
class OllamaFunctionCall(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: Annotated[StrictStr, Field(pattern=TOOL_NAME)]
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def _arguments(cls, value: Any) -> Any:
        # Certains modèles renvoient les arguments sérialisés : Ollama attend un objet.
        if isinstance(value, str):
            try:
                value = json.loads(value or "{}")
            except ValueError:
                return {}
        return value if isinstance(value, dict) else {}


class OllamaToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    function: OllamaFunctionCall


class OllamaMessage(BaseModel):
    """Message tel qu'Ollama l'accepte : champs internes (untrusted…) retirés."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: StrictStr = ""
    images: list[StrictStr] | None = None
    tool_calls: list[OllamaToolCall] | None = None
    tool_name: StrictStr | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: Any) -> Any:
        return "" if value is None else value


class OllamaFunctionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[StrictStr, Field(pattern=TOOL_NAME)]
    description: StrictStr = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})

    @field_validator("parameters")
    @classmethod
    def _object(cls, value: dict) -> dict:
        if value.get("type") != "object" or not isinstance(value.get("properties", {}), dict):
            raise ValueError("les paramètres d'un outil doivent être un objet JSON Schema")
        return value


class OllamaToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["function"] = "function"
    function: OllamaFunctionSpec


class OllamaChatRequest(BaseModel):
    """Corps de POST /api/chat, validé avant l'envoi : un corps malformé peut faire
    fermer la connexion par le serveur sans réponse (panique côté moteur)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    messages: list[OllamaMessage] = Field(min_length=1, max_length=2000)
    #: toujours explicite (Ollama diffuse par défaut)
    stream: StrictBool
    tools: list[OllamaToolSpec] | None = None
    think: StrictBool | None = None
    keep_alive: StrictStr | StrictInt | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    def wire(self) -> dict:
        return self.model_dump(exclude_none=True)


class Capabilities(BaseModel):
    """Résultat de la poignée de main dynamique avec un fournisseur."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    reachable: bool
    latency_ms: int = 0
    context_window: int = 8192
    tools: bool = True
    thinking: bool = False
    parameters_b: float = 0.0
    #: profil d'invite adapté : compact (petit modèle ou lent), standard, étendu
    profile: Literal["compact", "standard", "extended"] = "standard"
    measured_at: float = Field(default_factory=time.time)


class SessionState(BaseModel):
    """État de session persistant (bascule à chaud sans perte de contexte)."""

    model_config = ConfigDict(extra="ignore")

    conversation: int
    messages: list[dict] = Field(default_factory=list)
    active_tools: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    tainted: bool = False
    step: int = 0
    updated: float = Field(default_factory=time.time)
    version: int = 1


# ---------------------------------------------------------------- JSON-RPC du démon root
Name = Annotated[StrictStr, Field(pattern=r"^[a-z0-9_.]{1,80}$")]


class NoParams(Strict):
    pass


class ExecToolParams(Strict):
    name: Name
    params: dict[str, Any] = Field(default_factory=dict)
    verbose: StrictBool = False


class TestPatchParams(Strict):
    target: Annotated[StrictStr, Field(pattern=r"^/usr/bin/djerba-[a-z0-9-]{1,40}$")]
    content: Annotated[StrictStr, Field(max_length=512 * 1024)]
    reason: Annotated[StrictStr, Field(max_length=500)] = ""

    @field_validator("target")
    @classmethod
    def _not_agent(cls, value: str) -> str:
        # L'agent lui-même n'est jamais « corrigé » : son intégrité est vérifiée par le démon.
        if value == "/usr/bin/djerba-ai":
            raise ValueError("djerba-ai ne peut pas être corrigé par l'auto-réparation")
        return value


class PatchIdParams(Strict):
    patch_id: Annotated[StrictStr, Field(pattern=r"^p\d{14}$")]


class PatchNameParams(Strict):
    name: Annotated[StrictStr, Field(pattern=r"^djerba-[a-z0-9-]{1,40}$")]


class TestPluginParams(Strict):
    name: Annotated[StrictStr, Field(pattern=r"^[a-z0-9_]{3,48}$")]
    content: Annotated[StrictStr, Field(max_length=200 * 1024)]
    tests: Annotated[StrictStr, Field(max_length=200 * 1024)] = ""
    requirements: list[Annotated[StrictStr, Field(pattern=REQUIREMENT)]] = Field(default_factory=list, max_length=10)


class VenvParams(Strict):
    packages: list[Annotated[StrictStr, Field(pattern=REQUIREMENT)]] = Field(min_length=1, max_length=20)


class ConfigSetParams(Strict):
    key: Annotated[StrictStr, Field(pattern=r"^[a-z_]+(\.[a-z_]+){1,3}$")]
    value: StrictStr | StrictInt | StrictBool | list[StrictStr]


class CloudChatParams(Strict):
    messages: list[dict[str, Any]] = Field(max_length=400)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class AuditTailParams(Strict):
    lines: Annotated[StrictInt, Field(ge=1, le=2000)] = 200


class SandboxProbeParams(Strict):
    backend: Literal["auto", "systemd", "unshare", "gvisor"] = "auto"


class UnblockParams(Strict):
    ip: Annotated[StrictStr, Field(pattern=r"^[0-9a-fA-F.:]{2,45}$")]


class ApparmorParams(Strict):
    kind: Literal["app", "plugin"]
    name: Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")]
    network: StrictBool = False


class SessionReportParams(Strict):
    desktop: Literal["gnome", "kde", "generic"]
    component: Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9_.@-]{0,63}$")]
    detail: Annotated[StrictStr, Field(min_length=1, max_length=16000)]


RPC_PARAMS: dict[str, type[Strict]] = {
    "sys.ping": NoParams,
    "sys.metrics": NoParams,
    "sys.list_patches": NoParams,
    "sys.exec_tool": ExecToolParams,
    "sys.test_patch": TestPatchParams,
    "sys.deploy_patch": PatchIdParams,
    "sys.rollback_patch": PatchNameParams,
    "sys.install_ollama": NoParams,
    "sys.test_plugin": TestPluginParams,
    "sys.venv_install": VenvParams,
    "sys.sandbox_probe": SandboxProbeParams,
    "config.get": NoParams,
    "config.set": ConfigSetParams,
    "llm.cloud_chat": CloudChatParams,
    "llm.cloud_health": NoParams,
    "llm.cloud_models": NoParams,
    "audit.tail": AuditTailParams,
    "audit.verify": NoParams,
    "security.status": NoParams,
    "security.unblock": UnblockParams,
    "security.apparmor_load": ApparmorParams,
    "session.report": SessionReportParams,
}


class RPCRequest(BaseModel):
    """Enveloppe JSON-RPC 2.0 (paramètres nommés uniquement)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    jsonrpc: Literal["2.0"]
    id: StrictInt | StrictStr | None = None
    method: Annotated[StrictStr, Field(max_length=64)]
    params: dict[str, Any] = Field(default_factory=dict)


def parse_rpc(message: Any) -> tuple[RPCRequest, Strict]:
    """Valide l'enveloppe puis les paramètres ; lève ipc.RPCError avec le code JSON-RPC adapté."""
    from pydantic import ValidationError

    from djerba_ai.core import ipc
    try:
        request = RPCRequest.model_validate(message)
    except ValidationError as exc:
        raise ipc.RPCError(ipc.INVALID_REQUEST, f"requête JSON-RPC invalide : {_first(exc)}") from exc
    model = RPC_PARAMS.get(request.method)
    if model is None:
        raise ipc.RPCError(ipc.METHOD_NOT_FOUND, f"méthode inconnue : {request.method}")
    try:
        params = model.model_validate(request.params)
    except ValidationError as exc:
        raise ipc.RPCError(ipc.INVALID_PARAMS, f"paramètres invalides : {_first(exc)}") from exc
    return request, params


def _first(exc) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    return f"{location} : {error.get('msg', '')}" if location else str(error.get("msg", ""))
