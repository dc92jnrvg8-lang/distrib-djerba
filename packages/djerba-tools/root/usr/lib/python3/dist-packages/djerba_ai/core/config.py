# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration centrale de l'agent Djerba : /etc/djerba/config.toml.

* Le fichier TOML appartient à root, en mode 0600 : il contient les clés
  d'API. Seul le démon djerba-ai-sysd l'écrit (méthode ``config.set``,
  autorisée par polkit), de façon atomique.
* Une vue publique sans secret, /etc/djerba/config.public.json (0644), est
  régénérée à chaque écriture : l'interface la surveille (synchronisation en
  temps réel) et les processus utilisateur la lisent sans privilège. Les clés
  secrètes y sont remplacées par un indicateur « *_set ».
* Chaque modification acceptée est journalisée (journal d'audit côté root,
  historique des réglages dans la mémoire SQLite côté utilisateur).

Les clés sont pointées (« llm.provider », « shortcuts.spotlight »…) et
validées par le schéma SCHEMA, côté client comme côté démon.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("DJERBA_CONFIG_DIR", "/etc/djerba"))
CONFIG_TOML = CONFIG_DIR / "config.toml"
CONFIG_PUBLIC = CONFIG_DIR / "config.public.json"

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
DATA_DIR = DATA_HOME / "djerba"
GENERATED_APPS = DATA_DIR / "generated_apps"
PLUGINS_DIR = DATA_DIR / "plugins"
MEMORY_DB = DATA_DIR / "memory.db"

SYSD_SOCKET = Path(os.environ.get("DJERBA_AI_SYSD_SOCKET", "/run/djerba-ai/sysd.sock"))

#: modèles conseillés selon la mémoire vive (Ollama)
RECOMMENDED_MODELS = [
    {"name": "qwen2.5:3b", "label": "Qwen 2.5 3B", "min_ram_gb": 6, "size": "1,9 Go",
     "note": "rapide, appels d'outils fiables ; pour 8 Go de RAM"},
    {"name": "llama3.2:3b", "label": "Llama 3.2 3B", "min_ram_gb": 6, "size": "2,0 Go",
     "note": "léger, bon en français courant"},
    {"name": "qwen2.5:7b", "label": "Qwen 2.5 7B", "min_ram_gb": 12, "size": "4,7 Go",
     "note": "recommandé à partir de 16 Go de RAM"},
    {"name": "qwen2.5-coder:7b", "label": "Qwen 2.5 Coder 7B", "min_ram_gb": 12, "size": "4,7 Go",
     "note": "meilleur pour le Studio (génération d'applications)"},
    {"name": "llama3.1:8b", "label": "Llama 3.1 8B", "min_ram_gb": 12, "size": "4,9 Go",
     "note": "polyvalent"},
]

HOST = r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,252})$"
URL = r"^https?://[A-Za-z0-9.:\[\]-]+(:\d{1,5})?(/[^\s\x00]*)?$"
MODEL = r"^[A-Za-z0-9._:/-]{1,120}$"
SHORTCUT = r"^((Meta|Ctrl|Alt|Shift)\+){0,4}[A-Za-z0-9]{1,12}$"

#: schéma : clé pointée -> spécification
SCHEMA: dict[str, dict[str, Any]] = {
    # Moteur cognitif
    "llm.provider": {"type": "string", "default": "local", "enum": ["local", "tailscale", "cloud"]},
    # Moteur actif du registre (core.engines) ; vide : llm.provider (local, tailscale, cloud).
    "llm.engine": {"type": "string", "default": "", "pattern": r"^([a-z0-9][a-z0-9-]{1,31})?$"},
    "llm.fallback_to_local": {"type": "boolean", "default": True},
    "llm.remote_timeout": {"type": "integer", "default": 5, "minimum": 1, "maximum": 60},
    "llm.context_window": {"type": "integer", "default": 8192, "minimum": 2048, "maximum": 131072},
    "llm.embedding_model": {"type": "string", "default": "nomic-embed-text", "pattern": MODEL},
    "llm.local.endpoint": {"type": "string", "default": "http://127.0.0.1:11434", "pattern": URL},
    "llm.local.model": {"type": "string", "default": "", "pattern": r"^[A-Za-z0-9._:/-]{0,120}$"},
    "llm.tailscale.host": {"type": "string", "default": "", "pattern": r"^([A-Za-z0-9][A-Za-z0-9.-]{0,252})?$"},
    "llm.tailscale.port": {"type": "integer", "default": 11434, "minimum": 1, "maximum": 65535},
    "llm.tailscale.model": {"type": "string", "default": "qwen2.5:32b", "pattern": MODEL},
    "llm.cloud.service": {"type": "string", "default": "openai", "enum": ["openai", "gemini", "custom"]},
    # HTTPS obligatoire, sauf serveur local compatible OpenAI (llama.cpp, LM Studio…).
    "llm.cloud.endpoint": {"type": "string", "default": "",
                           "pattern": r"^(https://[^\s\x00]{3,300}|http://(127\.0\.0\.1|localhost)(:\d{1,5})?(/[^\s\x00]*)?)?$"},
    "llm.cloud.model": {"type": "string", "default": "gpt-4o-mini", "pattern": MODEL},
    "llm.cloud.api_key": {"type": "string", "default": "", "secret": True, "pattern": r"^[\x21-\x7e]{0,400}$"},
    # Politique d'autonomie (core/policy.py) : modifiable seulement par policy.set_mode / policy.set_caps
    # (authentification administrateur), jamais par config.set ni par l'agent.
    "policy.mode": {"type": "string", "default": "autonome", "enum": ["prudent", "autonome", "total"]},
    "policy.engine_caps": {"type": "list", "default": [], "pattern": r"^[a-z][a-z0-9_-]{0,30}:[0-3]$"},
    # Agent
    "agent.autonomy": {"type": "string", "default": "ask", "enum": ["ask", "safe"]},
    "agent.web_search": {"type": "boolean", "default": True},
    "agent.self_heal": {"type": "boolean", "default": True},
    "agent.auto_extend": {"type": "boolean", "default": True},
    "agent.auto_update_model": {"type": "boolean", "default": True},
    "agent.maintenance_hour": {"type": "integer", "default": 12, "minimum": 0, "maximum": 23},
    "agent.plugins_approved": {"type": "list", "default": [], "pattern": r"^[a-z0-9_]{1,48}$"},
    # Raccourcis globaux (KGlobalAccel)
    "shortcuts.spotlight": {"type": "string", "default": "Meta+Space", "pattern": SHORTCUT},
    "shortcuts.clipboard": {"type": "string", "default": "Meta+Shift+C", "pattern": SHORTCUT},
    # Système
    "system.sandbox_timeout": {"type": "integer", "default": 15, "minimum": 5, "maximum": 15},
    "system.sandbox_attempts": {"type": "integer", "default": 3, "minimum": 1, "maximum": 3},
    "system.watchdog": {"type": "boolean", "default": True},
    "system.disk_alert_percent": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
    "system.audit_max_mb": {"type": "integer", "default": 10, "minimum": 1, "maximum": 500},
    # Bac à sable hybride (namespaces, seccomp-bpf, cgroups v2)
    "sandbox.backend": {"type": "string", "default": "auto", "enum": ["auto", "systemd", "unshare", "gvisor"]},
    "sandbox.memory_mb": {"type": "integer", "default": 512, "minimum": 128, "maximum": 4096},
    "sandbox.cpu_percent": {"type": "integer", "default": 50, "minimum": 10, "maximum": 100},
    # Sécurité : IDS, confiance zéro réseau
    "security.ids": {"type": "boolean", "default": True},
    "security.ids_block_minutes": {"type": "integer", "default": 60, "minimum": 5, "maximum": 1440},
    "security.network_grants": {"type": "list", "default": [], "pattern": r"^(app|plugin):[a-z0-9][a-z0-9_-]{0,47}$"},
    # Djerba comme serveur d'outils (MCP) pour un assistant distant (Jarvis), lié à l'adresse Tailscale
    "mcp.enabled": {"type": "boolean", "default": False},
    "mcp.port": {"type": "integer", "default": 8765, "minimum": 1024, "maximum": 65535},
    # Bus de contexte et télémétrie
    "context.interval": {"type": "integer", "default": 5, "minimum": 2, "maximum": 60},
    "context.window_titles": {"type": "boolean", "default": True},
    "telemetry.enabled": {"type": "boolean", "default": True},
    "telemetry.port": {"type": "integer", "default": 9464, "minimum": 1024, "maximum": 65535},
    # Interface
    "ui.onboarded": {"type": "boolean", "default": False},
}

#: Réglages de politique et de sécurité (invariant 01) : seul un humain authentifié (polkit, mot de passe
#: administrateur) les change ; jamais un outil, un moteur, un greffon ni une session de l'agent. Le démon
#: root refuse toute demande qui porte une origine d'agent. Liste surveillée par tests/check-guards.py.
PROTECTED_PREFIXES = ("policy.", "llm.", "mcp.", "security.", "sandbox.", "telemetry.")
PROTECTED_KEYS = ("agent.plugins_approved", "agent.auto_extend", "agent.autonomy", "system.sandbox_timeout",
                  "system.sandbox_attempts", "system.watchdog", "system.audit_max_mb")
#: exceptions explicites (modèle Ollama local : choix sans effet sur la sécurité ni sur les coûts) ;
#: toute nouvelle exception est signalée par tests/check-guards.py
AGENT_SETTABLE = ("llm.local.model",)


def protected(key: str) -> bool:
    """Réglage réservé à l'humain authentifié."""
    return (key in PROTECTED_KEYS or key.startswith(PROTECTED_PREFIXES)) and key not in AGENT_SETTABLE


CLOUD_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    # API compatible OpenAI de Google Gemini (chat, appels d'outils, flux).
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


class ConfigError(Exception):
    """Clé inconnue ou valeur invalide."""


def memory_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return 8.0


def default_model() -> str:
    return "qwen2.5:7b" if memory_gb() >= 14 else "qwen2.5:3b"


def validate(key: str, value: Any) -> Any:
    """Valide et normalise une valeur selon le schéma."""
    spec = SCHEMA.get(key)
    if spec is None:
        raise ConfigError(f"réglage inconnu : {key}")
    kind = spec["type"]
    if kind == "boolean":
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "oui", "yes", "on", "vrai")
        value = bool(value)
    elif kind == "integer":
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} : entier attendu") from exc
        if not spec.get("minimum", value) <= value <= spec.get("maximum", value):
            raise ConfigError(f"{key} : entre {spec['minimum']} et {spec['maximum']}")
    elif kind == "list":
        if not isinstance(value, list) or not all(isinstance(item, str) and re.fullmatch(spec["pattern"], item)
                                                  for item in value):
            raise ConfigError(f"{key} : liste invalide")
        value = sorted(set(value))
    else:
        value = str(value)
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            raise ConfigError(f"{key} : format invalide")
    if "enum" in spec and value not in spec["enum"]:
        raise ConfigError(f"{key} : valeur parmi {', '.join(spec['enum'])}")
    return value


def defaults() -> dict[str, Any]:
    values = {key: spec["default"] for key, spec in SCHEMA.items()}
    values["llm.local.model"] = default_model()
    return values


def flatten(tree: dict, prefix: str = "") -> dict[str, Any]:
    flat = {}
    for key, value in tree.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, dotted))
        else:
            flat[dotted] = value
    return flat


def nest(flat: dict[str, Any]) -> dict:
    tree: dict = {}
    for dotted, value in flat.items():
        node = tree
        *parents, leaf = dotted.split(".")
        for parent in parents:
            node = node.setdefault(parent, {})
        node[leaf] = value
    return tree


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)  # chaîne TOML de base (échappements compatibles)


def to_toml(flat: dict[str, Any]) -> str:
    """Sérialise la configuration (tables imbriquées, valeurs simples)."""
    lines = ["# Djerba OS — configuration de l'agent IA Djerba.",
             "# Fichier géré par djerba-ai-sysd (root, 0600) : modifiez-le depuis Djerba → Réglages.", ""]
    tables: dict[str, list[tuple[str, Any]]] = {}
    for dotted in SCHEMA:
        table, leaf = dotted.rsplit(".", 1)
        tables.setdefault(table, []).append((leaf, flat.get(dotted, SCHEMA[dotted]["default"])))
    for table, entries in tables.items():
        lines.append(f"[{table}]")
        lines.extend(f"{leaf} = {_toml_value(value)}" for leaf, value in entries)
        lines.append("")
    return "\n".join(lines)


def public_view(flat: dict[str, Any]) -> dict[str, Any]:
    view = {}
    for key, value in flat.items():
        if SCHEMA.get(key, {}).get("secret"):
            view[f"{key}_set"] = bool(value)
        else:
            view[key] = value
    return view


class Store:
    """Accès root au fichier TOML (utilisé par djerba-ai-sysd et le postinst)."""

    def __init__(self, toml_path: Path = CONFIG_TOML, public_path: Path = CONFIG_PUBLIC) -> None:
        self.toml_path = toml_path
        self.public_path = public_path

    def load(self) -> dict[str, Any]:
        values = defaults()
        try:
            stored = flatten(tomllib.loads(self.toml_path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError):
            stored = {}
        for key, value in stored.items():
            try:
                values[key] = validate(key, value)
            except ConfigError:
                continue
        if not values["llm.local.model"]:
            values["llm.local.model"] = default_model()
        return values

    def save(self, values: dict[str, Any]) -> None:
        self.toml_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        temporary = self.toml_path.with_name(".config.toml.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(to_toml(values))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.toml_path)
        public = self.public_path.with_name(".config.public.json.tmp")
        public.write_text(json.dumps(public_view(values), ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(public, 0o644)
        os.replace(public, self.public_path)

    def set(self, key: str, value: Any) -> tuple[Any, Any]:
        values = self.load()
        old = values.get(key)
        values[key] = validate(key, value)
        self.save(values)
        return old, values[key]

    def ensure(self) -> None:
        """Crée (ou répare) le fichier et sa vue publique, droits compris."""
        self.save(self.load())


USER_PREFIX = "ui."  # préférences d'interface : propres à chaque utilisateur


class Config:
    """Vue utilisateur de la configuration (lecture de la vue publique).

    L'écriture passe par le démon root (polkit org.djerba.ai.configure) ;
    sans démon joignable (développement, tests), un repli local par
    utilisateur est utilisé : ~/.config/djerba-ai/config.json (0600).
    """

    FALLBACK = CONFIG_HOME / "djerba-ai" / "config.json"

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        values = defaults()
        system = CONFIG_PUBLIC.is_file()
        for source in (CONFIG_PUBLIC, self.FALLBACK):
            try:
                stored = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for key, value in stored.items():
                # Le repli par utilisateur ne masque jamais la configuration système,
                # sauf pour les préférences d'interface (ui.*).
                if source == self.FALLBACK and system and not key.startswith(USER_PREFIX):
                    continue
                if key in SCHEMA or key.endswith("_set"):
                    values[key] = value
        if not values.get("llm.local.model"):
            values["llm.local.model"] = default_model()
        # Les secrets ne sont jamais lisibles côté utilisateur (seul l'indicateur *_set l'est).
        self.values = {key: value for key, value in values.items() if not SCHEMA.get(key, {}).get("secret")}

    def get(self, key: str) -> Any:
        return self.values.get(key, SCHEMA.get(key, {}).get("default"))

    def set(self, key: str, value: Any) -> Any:
        value = validate(key, value)
        from djerba_ai.core import ipc
        if protected(key) and ipc.ORIGIN.get():
            raise ConfigError(f"{key} est un réglage protégé : seul un humain le change, avec authentification "
                              "administrateur (Réglages de Djerba)")
        if key.startswith(USER_PREFIX):
            applied = self._set_fallback(key, value)
            self._record(key, value)
            self.values[key] = applied
            return applied
        try:
            request: dict[str, Any] = {"key": key, "value": value}
            if ipc.ORIGIN.get():
                request["origin"] = ipc.ORIGIN.get()
            result = ipc.call("config.set", request, timeout=120)
            applied = result.get("value", value)
        except ipc.RPCError as exc:
            if exc.code == ipc.NOT_AUTHORIZED:
                raise ConfigError("modification refusée (autorisation administrateur requise)") from exc
            applied = self._set_fallback(key, value)
        self._record(key, value)
        self.load()
        if key not in ("llm.cloud.api_key",):
            self.values[key] = applied
        return applied

    def _set_fallback(self, key: str, value: Any) -> Any:
        if SCHEMA[key].get("secret"):
            raise ConfigError("le démon djerba-ai-sysd est requis pour enregistrer une clé d'API")
        if CONFIG_PUBLIC.is_file() and not key.startswith(USER_PREFIX):
            # Configuration système présente : un réglage local serait ignoré.
            raise ConfigError("service djerba-ai-sysd injoignable : réglage non enregistré")
        try:
            stored = json.loads(self.FALLBACK.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        stored[key] = value
        self.FALLBACK.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.FALLBACK.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(stored, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.FALLBACK)
        return value

    @staticmethod
    def _record(key: str, value: Any) -> None:
        try:
            from djerba_ai.core.memory import Memory
            memory = Memory()
            memory.record_setting(key, "••••" if SCHEMA[key].get("secret") else value)
            memory.close()
        except Exception:  # noqa: BLE001 - l'historique ne doit jamais bloquer un réglage
            pass

    def public(self) -> dict:
        return {key: value for key, value in self.values.items() if not SCHEMA.get(key, {}).get("secret")}
