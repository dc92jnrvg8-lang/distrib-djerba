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
import sys
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


class IsolatedPluginTool(BaseTool):
    """Mandataire d'une capacité générée : son code ne s'exécute jamais dans l'agent.

    Chaque appel lance security/plugin_runner.py dans un processus distinct,
    environnement minimal, sans réseau sauf autorisation (seccomp, AppArmor :
    security/netpolicy.py), 60 s au plus.
    """

    requires_root = False
    dangerous = True  # capacité générée : confirmation à chaque exécution
    category = "devtools"
    plugin = ""
    tool = ""
    path = ""

    def execute(self, params: dict, verbose: bool = False) -> dict:
        import json
        import subprocess
        from djerba_ai.security import netpolicy
        runner = Path(__file__).resolve().parent.parent / "security" / "plugin_runner.py"
        from djerba_ai.core.config import Config
        argv = netpolicy.wrap([sys.executable, "-s", "-P", str(runner), "run", self.path, self.tool], Config(),
                              "plugin", self.plugin)
        try:
            result = subprocess.run(argv, input=json.dumps(params), capture_output=True, text=True, timeout=60,
                                    env=netpolicy.isolated_environment(_venv_paths()))
        except subprocess.TimeoutExpired:
            return {"success": False, "message": "capacité générée : délai de 60 s dépassé", "data": {}}
        try:
            output = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"success": False, "message": f"capacité générée en échec : {result.stderr.strip()[-300:]}",
                    "data": {}}
        return output if isinstance(output, dict) else {"success": False, "message": "réponse invalide", "data": {}}


def _venv_paths() -> list[str]:
    return [str(path) for path in sorted(VENV.glob("lib/python3*/site-packages"))]


def describe_plugin(name: str, path: Path) -> list[dict]:
    """Description des capacités d'un greffon, obtenue dans un processus isolé."""
    import json
    import subprocess
    from djerba_ai.core.config import Config
    from djerba_ai.security import netpolicy
    runner = Path(__file__).resolve().parent.parent / "security" / "plugin_runner.py"
    argv = netpolicy.wrap([sys.executable, "-s", "-P", str(runner), "describe", str(path)], Config(), "plugin", name)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30,
                            env=netpolicy.isolated_environment(_venv_paths()))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-300:] or f"code {result.returncode}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def load_plugins(approved: list[str]) -> tuple[dict[str, BaseTool], list[str]]:
    """Mandataires des greffons approuvés de l'utilisateur (jamais root, jamais chargés dans l'agent)."""
    tools: dict[str, BaseTool] = {}
    errors: list[str] = []
    for name in approved:
        path = PLUGIN_DIR / f"{name}.py"
        if not re.fullmatch(r"[a-z0-9_]{1,48}", name) or not path.is_file():
            errors.append(f"{name} : greffon introuvable")
            continue
        try:
            for item in describe_plugin(name, path):
                attributes = {"name": f"plugin.{item['name']}", "description": str(item.get("description", "")),
                              "parameters": dict(item.get("parameters") or {}),
                              "required": tuple(item.get("required") or ()),
                              "keywords": tuple(item.get("keywords") or ()), "plugin": name, "tool": item["name"],
                              "path": str(path)}
                tool = type(f"Plugin{name.title().replace('_', '')}Tool", (IsolatedPluginTool,), attributes)()
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
