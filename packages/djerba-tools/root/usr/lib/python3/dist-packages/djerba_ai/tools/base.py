# SPDX-License-Identifier: GPL-3.0-or-later
"""Socle des outils de l'agent Djerba.

Chaque capacité système est un outil (``BaseTool``) : nom unique, catégorie,
description, schéma de paramètres (sous-ensemble de JSON Schema), besoin de
droits root et besoin de confirmation. ``execute`` renvoie toujours
``{"success": bool, "message": str, "data": dict}``.

Sécurité :
* aucun outil n'invoque de shell : les commandes sont des listes d'arguments,
  chaque paramètre devient au plus un argument entier ;
* chaque paramètre est validé (type, motif, énumération, bornes) avant
  exécution, côté utilisateur **et** côté démon root ;
* les outils ``requires_root`` ne s'exécutent que dans le démon
  djerba-ai-sysd, après autorisation polkit ; les greffons créés par
  l'utilisateur ne peuvent jamais être root.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Any, Callable

Result = dict[str, Any]


class ToolError(Exception):
    """Paramètres invalides ou exécution impossible."""


def ok(message: str, **data: Any) -> Result:
    return {"success": True, "message": message, "data": data}


def fail(message: str, **data: Any) -> Result:
    return {"success": False, "message": message, "data": data}


# Motifs de paramètres réutilisés par les catalogues d'outils.
PACKAGE = r"^[a-z0-9][a-z0-9+.-]{0,99}(:[a-z0-9]+)?$"
UNIT = r"^[A-Za-z0-9@_.:-]{1,120}\.(service|socket|timer|target|path|mount)$"
FLATPAK_REF = r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){2,}$"
DEVICE = r"^/dev/[A-Za-z0-9/_-]{1,64}$"
IFACE = r"^[A-Za-z0-9_.-]{1,15}$"
MAC = r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
SLUG = r"^[a-z0-9][a-z0-9-]{0,47}$"
HOSTNAME = r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$"
SAFE_TEXT = r"^[^\x00]{1,2000}$"


class BaseTool(ABC):
    """Classe de base de tous les outils."""

    name: str = ""
    category: str = ""
    description: str = ""
    requires_root: bool = False
    #: action à faire confirmer par l'utilisateur (suppression, redémarrage…)
    dangerous: bool = False
    #: outil root qui modifie le système : exécuté dans une transaction annulable
    #: (sauvegarde préalable, vérification, undo) ; False pour la lecture seule
    mutates: bool = True
    #: {"nom": {"type": "string"|"integer"|"boolean", "description": …,
    #:          "enum": […], "pattern": …, "minimum": …, "maximum": …, "default": …}}
    parameters: dict[str, dict[str, Any]] = {}
    required: tuple[str, ...] = ()
    #: mots-clés (français et anglais) pour la recherche d'outils
    keywords: tuple[str, ...] = ()
    #: binaires nécessaires (diagnostic « djerba-ai doctor »)
    binaries: tuple[str, ...] = ()

    @abstractmethod
    def execute(self, params: dict, verbose: bool = False) -> Result:
        """Retourne : {"success": bool, "message": str, "data": dict}"""

    # ---------------------------------------------------------------- Schéma
    def schema(self) -> dict:
        """Description au format « tools » d'Ollama / OpenAI."""
        properties = {}
        for key, spec in self.parameters.items():
            prop = {"type": spec.get("type", "string"), "description": spec.get("description", "")}
            if "enum" in spec:
                prop["enum"] = list(spec["enum"])
            properties[key] = prop
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": properties, "required": list(self.required)},
            },
        }

    def missing_binaries(self) -> list[str]:
        return [binary for binary in self.binaries if shutil.which(binary) is None]

    # ---------------------------------------------------------------- Validation
    def validate(self, params: dict | None) -> dict:
        params = dict(params or {})
        unknown = set(params) - set(self.parameters)
        if unknown:
            raise ToolError(f"paramètre(s) inconnu(s) : {', '.join(sorted(unknown))}")
        cleaned: dict[str, Any] = {}
        for key, spec in self.parameters.items():
            if key not in params or params[key] in (None, ""):
                if key in self.required:
                    raise ToolError(f"paramètre requis : {key}")
                if "default" in spec:
                    cleaned[key] = spec["default"]
                continue
            cleaned[key] = self._coerce(key, spec, params[key])
        return cleaned

    @staticmethod
    def _coerce(key: str, spec: dict, value: Any) -> Any:
        kind = spec.get("type", "string")
        if kind == "integer":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{key} : entier attendu") from exc
            if "minimum" in spec and value < spec["minimum"]:
                raise ToolError(f"{key} : minimum {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                raise ToolError(f"{key} : maximum {spec['maximum']}")
        elif kind == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "oui", "yes", "on", "vrai")
            value = bool(value)
        else:
            value = str(value)
            if "\x00" in value or len(value) > spec.get("max_length", 2000):
                raise ToolError(f"{key} : valeur invalide")
            if value.startswith("-") and not spec.get("allow_dash", False):
                raise ToolError(f"{key} : une valeur ne peut pas commencer par « - »")
            if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
                raise ToolError(f"{key} : format invalide")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"{key} : valeur attendue parmi {', '.join(map(str, spec['enum']))}")
        return value

    def run(self, params: dict | None = None, verbose: bool = False) -> Result:
        """Valide puis exécute ; ne lève jamais d'exception."""
        try:
            cleaned = self.validate(params)
            missing = self.missing_binaries()
            if missing:
                return fail(f"outil indisponible : {', '.join(missing)} n'est pas installé", missing=missing)
            return self.execute(cleaned, verbose=verbose)
        except ToolError as exc:
            return fail(str(exc))
        except subprocess.TimeoutExpired:
            return fail("délai d'exécution dépassé")
        except OSError as exc:
            return fail(f"erreur système : {exc}")

    def describe(self) -> dict:
        return {
            "name": self.name, "category": self.category, "description": self.description,
            "requires_root": self.requires_root, "dangerous": self.dangerous,
            "parameters": self.parameters, "required": list(self.required),
            "available": not self.missing_binaries(),
        }


def run_command(argv: list[str], timeout: int = 120, env: dict | None = None,
                input_text: str | None = None) -> subprocess.CompletedProcess:
    environment = dict(os.environ, LC_ALL="C.UTF-8", **(env or {}))
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=environment,
                          input=input_text, check=False)


def launch_detached(argv: list[str]) -> bool:
    """Lance une application graphique sans attendre qu'elle se ferme."""
    if shutil.which(argv[0]) is None:
        return False
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def clip(text: str, limit: int = 6000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n[…]\n" + text[-limit // 2:]


class CommandTool(BaseTool):
    """Outil défini par une commande : chaque élément de ``argv`` peut contenir
    ``{parametre}`` ; un élément qui ne contient qu'un paramètre vide est omis."""

    argv: tuple[str, ...] = ()
    timeout: int = 120
    env: dict[str, str] = {}
    success_codes: tuple[int, ...] = (0,)
    #: transforme la sortie standard en données structurées
    parser: Callable[[str], Any] | None = None
    #: message de succès (format avec les paramètres)
    done: str = "Terminé."

    def build_argv(self, params: dict) -> list[str]:
        argv: list[str] = []
        for part in self.argv:
            if re.fullmatch(r"\{(\w+)\}", part):
                value = params.get(part[1:-1])
                if value in (None, ""):
                    continue
                argv.append(str(value))
            else:
                try:
                    argv.append(part.format(**params) if "{" in part else part)
                except KeyError as exc:
                    raise ToolError(f"paramètre requis : {exc.args[0]}") from exc
        return argv

    def execute(self, params: dict, verbose: bool = False) -> Result:
        argv = self.build_argv(params)
        result = run_command(argv, timeout=self.timeout, env=self.env)
        output = clip(result.stdout or "")
        errors = clip(result.stderr or "", 2000)
        data: dict[str, Any] = {"output": output, "exit_code": result.returncode}
        if verbose:
            data["command"] = argv
            data["stderr"] = errors
        if result.returncode not in self.success_codes:
            return fail(errors.splitlines()[-1] if errors else f"échec (code {result.returncode})", **data)
        if self.parser is not None:
            try:
                data["parsed"] = type(self).parser(output)
            except (ValueError, IndexError, KeyError):
                pass
        return ok(self.done.format(**params) if params else self.done, **data)


def command_tool(name: str, category: str, description: str, argv: list[str], *,
                 parameters: dict | None = None, required: tuple[str, ...] = (),
                 requires_root: bool = False, dangerous: bool = False, keywords: tuple[str, ...] = (),
                 mutates: bool = True, binaries: tuple[str, ...] | None = None, timeout: int = 120, done: str = "Terminé.",
                 success_codes: tuple[int, ...] = (0,), env: dict | None = None,
                 parser: Callable[[str], Any] | None = None) -> type[CommandTool]:
    """Fabrique une classe d'outil à partir d'une commande (catalogues déclaratifs)."""
    attributes = {
        "name": name, "category": category, "description": description, "argv": tuple(argv),
        "parameters": parameters or {}, "required": required, "requires_root": requires_root,
        "dangerous": dangerous, "keywords": keywords, "timeout": timeout, "done": done, "mutates": mutates,
        "success_codes": success_codes, "env": env or {},
        "binaries": binaries if binaries is not None else (argv[0],),
        "parser": staticmethod(parser) if parser else None,
    }
    class_name = "".join(part.capitalize() for part in re.split(r"[_.]", name)) + "Tool"
    return type(class_name, (CommandTool,), attributes)


class FunctionTool(BaseTool):
    """Outil défini par une fonction Python ``handler(params, verbose) -> Result``."""

    handler: Callable[[dict, bool], Result]

    def execute(self, params: dict, verbose: bool = False) -> Result:
        return type(self).handler(params, verbose)


def function_tool(name: str, category: str, description: str, handler: Callable[[dict, bool], Result], *,
                  parameters: dict | None = None, required: tuple[str, ...] = (),
                  requires_root: bool = False, dangerous: bool = False, mutates: bool = True,
                  keywords: tuple[str, ...] = (), binaries: tuple[str, ...] = ()) -> type[FunctionTool]:
    attributes = {
        "name": name, "category": category, "description": description, "handler": staticmethod(handler),
        "parameters": parameters or {}, "required": required, "requires_root": requires_root,
        "dangerous": dangerous, "keywords": keywords, "binaries": binaries, "mutates": mutates,
    }
    class_name = "".join(part.capitalize() for part in re.split(r"[_.]", name)) + "Tool"
    return type(class_name, (FunctionTool,), attributes)
