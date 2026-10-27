# SPDX-License-Identifier: GPL-3.0-or-later
"""Réglages de l'agent Djerba (~/.config/djerba-ai/config.json, mode 0600)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
CONFIG_FILE = CONFIG_HOME / "djerba-ai" / "config.json"
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

DEFAULTS: dict[str, Any] = {
    "provider": "ollama",                  # ollama | openai
    "ollama_endpoint": "http://127.0.0.1:11434",
    "openai_endpoint": "",                 # API compatible OpenAI (llama.cpp, LM Studio, service distant)
    "openai_api_key": "",
    "model": "",                           # vide : choisi selon la RAM au premier démarrage
    "autonomy": "ask",                     # ask : confirmer toute action ; safe : actions sûres directes
    "web_search": True,
    "self_heal": True,
    "auto_update_model": True,
    "maintenance_hour": 12,
    "plugins_approved": [],
    "context_window": 8192,
}


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


class Config:
    def __init__(self, path: Path = CONFIG_FILE) -> None:
        self.path = path
        self.values: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                self.values.update({key: value for key, value in stored.items() if key in DEFAULTS})
        except (OSError, ValueError):
            pass
        if not self.values.get("model"):
            self.values["model"] = default_model()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.values, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, self.path)

    def get(self, key: str) -> Any:
        return self.values.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        self.values[key] = value
        self.save()

    def public(self) -> dict:
        """Réglages sans secret (affichage, contexte de l'agent)."""
        values = dict(self.values)
        if values.get("openai_api_key"):
            values["openai_api_key"] = "••••"
        return values
