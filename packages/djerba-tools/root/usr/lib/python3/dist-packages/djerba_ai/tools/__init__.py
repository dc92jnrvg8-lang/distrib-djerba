# SPDX-License-Identifier: GPL-3.0-or-later
"""Registre des outils de l'agent Djerba, construit par introspection.

Chaque sous-paquet de catégorie (maintenance, performance, security, desktop,
apps, hardware, agentic, system, devtools, os) est parcouru : toute sous-classe
concrète de ``BaseTool`` portant un nom, définie dans un module ou déclarée
dans sa liste ``TOOLS``, est enregistrée.

Les greffons de l'utilisateur (~/.local/share/djerba/plugins/*.py) ne sont
chargés que dans le processus utilisateur, s'ils ont été approuvés, et ne
peuvent jamais demander les droits root.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import pkgutil
import re
import unicodedata
from pathlib import Path

from djerba_ai.tools.base import BaseTool

CATEGORIES = ("maintenance", "performance", "security", "desktop", "apps", "hardware",
              "agentic", "system", "devtools", "os")
CATEGORY_LABELS = {
    "maintenance": "Maintenance", "performance": "Performances", "security": "Sécurité",
    "desktop": "Bureau", "apps": "Applications", "hardware": "Matériel", "agentic": "Agent",
    "system": "Système", "devtools": "Studio", "os": "Fichiers et paquets",
}
PLUGIN_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "djerba" / "plugins"


class Registry:
    def __init__(self, tools: dict[str, BaseTool]) -> None:
        self.tools = tools

    def __len__(self) -> int:
        return len(self.tools)

    def get(self, name: str) -> BaseTool | None:
        return self.tools.get(name)

    def by_category(self, category: str) -> list[BaseTool]:
        return sorted((tool for tool in self.tools.values() if tool.category == category), key=lambda t: t.name)

    def search(self, query: str, limit: int = 12) -> list[BaseTool]:
        """Recherche lexicale pondérée (noms, mots-clés, descriptions ; FR/EN, sans accents)."""
        words = tokens(query)
        if not words:
            return []
        normalized_query = " ".join(words)
        scored = []
        for tool in self.tools.values():
            index = self._index(tool)
            score = 0.0
            for word in words:
                if word in index["name"]:
                    score += 3.0
                if word in index["keywords"]:
                    score += 2.5
                if word in index["description"]:
                    score += 1.5
            for phrase in index["phrases"]:
                if phrase and phrase in normalized_query:
                    score += 4.0
            if score:
                scored.append((score, tool.name, tool))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [tool for _score, _name, tool in scored[:limit]]

    def _index(self, tool: BaseTool) -> dict:
        cached = getattr(tool, "_search_index", None)
        if cached is None:
            cached = {
                "name": set(tokens(tool.name.replace("_", " ").replace(".", " "))),
                "keywords": set(tokens(" ".join(tool.keywords))),
                "description": set(tokens(tool.description)),
                "phrases": [" ".join(tokens(keyword)) for keyword in tool.keywords if len(tokens(keyword)) > 1],
            }
            tool._search_index = cached
        return cached


STOPWORDS = {
    "le", "la", "les", "l", "de", "du", "des", "d", "un", "une", "et", "ou", "a", "au", "aux", "en", "pour", "sur",
    "avec", "sans", "dans", "par", "est", "sont", "qui", "que", "quoi", "mon", "ma", "mes", "ton", "ta", "tes",
    "sa", "ses", "ce", "cet", "cette", "ces", "je", "tu", "il", "elle", "on", "nous", "vous", "me", "moi",
    "te", "se", "y", "ne", "pas", "plus", "si", "il", "un", "on", "peux", "peut", "veux", "voudrais", "fais", "fait", "faire", "stp",
    "svp", "merci", "the", "my", "to", "of", "and", "is", "please", "can", "est-ce", "comment", "quel", "quelle",
}


def tokens(text: str) -> list[str]:
    """Mots normalisés : minuscules, sans accents, mots vides retirés, racine de 6 lettres."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", plain)
    return [word[:6] for word in words if word not in STOPWORDS and len(word) > 1]


def _collect(module) -> list[type[BaseTool]]:
    found: list[type[BaseTool]] = list(getattr(module, "TOOLS", []))
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, BaseTool) and obj.__module__ == module.__name__ and obj.name \
                and not inspect.isabstract(obj):
            found.append(obj)
    return found


def builtin_tools() -> dict[str, BaseTool]:
    tools: dict[str, BaseTool] = {}
    for category in CATEGORIES:
        package = importlib.import_module(f"djerba_ai.tools.{category}")
        modules = [package]
        for info in pkgutil.iter_modules(package.__path__):
            modules.append(importlib.import_module(f"djerba_ai.tools.{category}.{info.name}"))
        for module in modules:
            for cls in _collect(module):
                if cls.name in tools:
                    if type(tools[cls.name]) is cls:
                        continue
                    raise RuntimeError(f"outil en double : {cls.name}")
                tool = cls()
                if tool.category != category:
                    raise RuntimeError(f"{cls.name} : catégorie {tool.category!r} ≠ {category!r}")
                tools[cls.name] = tool
    return tools


VENV = Path(os.environ.get("DJERBA_AI_VENV", "/usr/share/djerba-ai/venv"))


def _venv_site_packages() -> None:
    """Rend importables les dépendances installées dans le venv interne (PEP 668)."""
    import sys
    for path in sorted(VENV.glob("lib/python3*/site-packages")):
        if str(path) not in sys.path:
            sys.path.append(str(path))


def load_plugins(approved: list[str]) -> tuple[dict[str, BaseTool], list[str]]:
    """Charge les greffons approuvés de l'utilisateur (jamais root)."""
    tools: dict[str, BaseTool] = {}
    errors: list[str] = []
    if approved:
        _venv_site_packages()
    for name in approved:
        path = PLUGIN_DIR / f"{name}.py"
        if not re.fullmatch(r"[a-z0-9_]{1,48}", name) or not path.is_file():
            errors.append(f"{name} : greffon introuvable")
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"djerba_user_plugin_{name}", path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            for cls in _collect(module):
                tool = cls()
                tool.requires_root = False
                tool.dangerous = True  # capacité générée : confirmation à chaque exécution
                tool.category = "devtools"
                tool.name = f"plugin.{tool.name}"
                tools[tool.name] = tool
        except Exception as exc:  # noqa: BLE001 - un greffon défaillant ne bloque pas l'agent
            errors.append(f"{name} : {exc}")
    return tools, errors


_REGISTRY: Registry | None = None


def registry(include_plugins: bool = False, approved: list[str] | None = None) -> Registry:
    """Registre des outils intégrés (mis en cache), avec greffons en option."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry(builtin_tools())
    if not include_plugins:
        return _REGISTRY
    plugins, _errors = load_plugins(approved or [])
    return Registry({**_REGISTRY.tools, **plugins})
