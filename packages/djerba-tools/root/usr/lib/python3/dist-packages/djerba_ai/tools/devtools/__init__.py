# SPDX-License-Identifier: GPL-3.0-or-later
"""Studio : applications générées, itérations Git, greffons de l'agent, compilation."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from djerba_ai.tools.base import SLUG, command_tool, fail, function_tool, launch_detached, ok, run_command
from djerba_ai.tools.devtools.app_generator import (APPS_DIR, BUILTIN, CODE_BLOCK, LAUNCHERS, MAIN_FILE, ask_model,
                                                    git, validate, write_app)

C = "devtools"
PLUGINS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "djerba" / "plugins"
USER_FILE = r"^(~|/home/[^/\x00]+|/tmp)(/[^\x00]+)$"


def _app(slug: str) -> tuple[Path, str] | None:
    app_dir = APPS_DIR / slug
    marker = app_dir / ".djerba-app"
    if not marker.is_file():
        return None
    kind = dict(line.split("=", 1) for line in marker.read_text().splitlines() if "=" in line).get("kind", "pyqt6")
    return app_dir, kind


def _list(_params: dict, _verbose: bool) -> dict:
    apps = []
    for marker in sorted(APPS_DIR.glob("*/.djerba-app")) if APPS_DIR.is_dir() else []:
        fields = dict(line.split("=", 1) for line in marker.read_text().splitlines() if "=" in line)
        log = git(marker.parent, "log", "-1", "--pretty=%cr").stdout.strip()
        apps.append({"slug": marker.parent.name, "name": fields.get("name", marker.parent.name),
                     "kind": fields.get("kind", ""), "updated": log})
    return ok(f"{len(apps)} application(s) créée(s) avec le Studio", apps=apps)


def run_app(slug: str, live: bool = False) -> int:
    """Lance une application générée (utilisé par « djerba-ai run-app »)."""
    found = _app(slug)
    if found is None:
        print(f"djerba-ai : application {slug} introuvable", file=sys.stderr)
        return 1
    app_dir, kind = found
    if kind == "html":
        return subprocess.call(["xdg-open", str(app_dir / "index.html")])
    command = [sys.executable, str(app_dir / "main.py")]
    if not live:
        return subprocess.call(command, cwd=app_dir)

    # Rechargement à chaud : relance dès qu'un fichier source change.
    def snapshot() -> dict:
        return {path: path.stat().st_mtime for path in app_dir.rglob("*")
                if path.is_file() and ".git" not in path.parts}

    state = snapshot()
    process = subprocess.Popen(command, cwd=app_dir)
    try:
        while True:
            time.sleep(0.7)
            current = snapshot()
            if current != state:
                state = current
                print("djerba-ai : modification détectée, rechargement…", file=sys.stderr)
                process.terminate()
                process.wait(timeout=10)
                process = subprocess.Popen(command, cwd=app_dir)
            elif process.poll() is not None:
                return process.returncode
    except KeyboardInterrupt:
        process.terminate()
        return 0


def _run(params: dict, _verbose: bool) -> dict:
    if _app(params["slug"]) is None:
        return fail("application introuvable (voir apps_generated_list)")
    argv = ["djerba-ai", "run-app", params["slug"]] + (["--live"] if params["live"] else [])
    return ok("Application lancée." + (" Rechargement à chaud actif." if params["live"] else "")) \
        if launch_detached(argv) else fail("djerba-ai introuvable")


def _open_folder(params: dict, _verbose: bool) -> dict:
    found = _app(params["slug"])
    if found is None:
        return fail("application introuvable")
    return ok(f"Dossier ouvert : {found[0]}") if launch_detached(["xdg-open", str(found[0])]) \
        else fail("ouverture impossible")


def _delete(params: dict, _verbose: bool) -> dict:
    found = _app(params["slug"])
    if found is None:
        return fail("application introuvable")
    shutil.rmtree(found[0])
    (LAUNCHERS / f"djerba-{params['slug']}.desktop").unlink(missing_ok=True)
    return ok(f"Application {params['slug']} supprimée (dossier et lanceur).")


def _iterate(params: dict, _verbose: bool) -> dict:
    found = _app(params["slug"])
    if found is None:
        return fail("application introuvable")
    app_dir, kind = found
    current = (app_dir / MAIN_FILE[kind]).read_text(errors="replace")
    instruction = (f"Voici le code actuel :\n```\n{current[:60000]}\n```\nModifie-le ainsi : {params['instruction']}. "
                   f"Garde tout le reste fonctionnel.")
    code = ask_model(instruction, kind)
    if code is None:
        return fail("moteur d'IA indisponible : impossible de modifier l'application")
    write_app(app_dir, kind, code)
    valid, error = validate(kind, app_dir)
    if not valid:
        git(app_dir, "checkout", "--", ".")
        return fail(f"modification rejetée (version précédente conservée) : {error[-300:]}")
    git(app_dir, "add", "-A")
    git(app_dir, "commit", "-q", "-m", f"Modification : {params['instruction'][:200]}")
    return ok("Application modifiée et enregistrée dans son historique Git.")


def _history(params: dict, _verbose: bool) -> dict:
    found = _app(params["slug"])
    if found is None:
        return fail("application introuvable")
    log = git(found[0], "log", "--pretty=%h|%cr|%s", "-n", "30").stdout.splitlines()
    versions = [dict(zip(("commit", "when", "message"), line.split("|", 2))) for line in log]
    return ok(f"{len(versions)} version(s)", versions=versions)


def _revert(params: dict, _verbose: bool) -> dict:
    found = _app(params["slug"])
    if found is None:
        return fail("application introuvable")
    app_dir = found[0]
    if len(git(app_dir, "log", "--pretty=%h").stdout.split()) < 2:
        return fail("aucune version précédente")
    git(app_dir, "checkout", "HEAD~1", "--", ".")
    git(app_dir, "commit", "-q", "-am", "Retour à la version précédente")
    return ok("Version précédente rétablie (l'historique est conservé).")


def _export(params: dict, _verbose: bool) -> dict:
    found = _app(params["slug"])
    if found is None:
        return fail("application introuvable")
    target = Path.home() / f"{params['slug']}-{time.strftime('%Y%m%d')}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        archive.add(found[0], arcname=params["slug"],
                    filter=lambda info: None if "/.git" in info.name or info.name.endswith("/.git") else info)
    return ok(f"Archive créée : {target}", path=str(target))


def _templates(_params: dict, _verbose: bool) -> dict:
    return ok("Modèles intégrés, utilisables même sans moteur d'IA",
              templates=[{"name": item["name"], "kind": item["kind"], "keywords": list(item["keywords"])}
                         for item in BUILTIN])


# ---------------------------------------------------------------- Greffons
PLUGIN_PROMPT = """Écris un greffon Python pour l'agent Djerba : {instruction}

Contrat obligatoire :
```
from djerba_ai.tools.base import BaseTool, ok, fail, run_command

class MonOutil(BaseTool):
    name = "nom_en_snake_case"
    category = "devtools"
    description = "Ce que fait l'outil, en français."
    requires_root = False
    parameters = {{"param": {{"type": "string", "description": "…"}}}}
    required = ("param",)

    def execute(self, params: dict, verbose: bool = False) -> dict:
        # utiliser run_command([...]) (liste d'arguments, jamais de shell)
        return ok("message", donnees=...)  # ou fail("message")
```
Le greffon ne demande jamais les droits root et n'utilise que la bibliothèque standard.
Réponds UNIQUEMENT avec le fichier Python complet, dans un seul bloc de code.
"""

CHECK_PLUGIN = """
import importlib.util, sys
from djerba_ai.tools.base import BaseTool
spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
tools = [obj for obj in vars(module).values()
         if isinstance(obj, type) and issubclass(obj, BaseTool) and obj is not BaseTool and obj.name]
assert tools, "aucune classe BaseTool"
for cls in tools:
    tool = cls()
    assert not tool.requires_root, "un greffon ne peut pas être root"
    tool.schema()
print(",".join(cls.name for cls in tools))
"""


def _plugin_generate(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.config import Config
    from djerba_ai.core.llm import LLMError, client_from_config
    try:
        reply = client_from_config(Config()).chat([{"role": "user", "content": PLUGIN_PROMPT.format(
            instruction=params["instruction"])}])
    except LLMError as exc:
        return fail(f"moteur d'IA indisponible : {exc}")
    match = CODE_BLOCK.search(reply["content"])
    if not match:
        return fail("le modèle n'a pas fourni de greffon")
    PLUGINS.mkdir(parents=True, exist_ok=True)
    path = PLUGINS / f"{params['name']}.py"
    path.write_text(match.group(1), encoding="utf-8")
    check = run_command([sys.executable, "-c", CHECK_PLUGIN, str(path)], timeout=30)
    if check.returncode != 0:
        path.unlink(missing_ok=True)
        return fail("greffon invalide : " + (check.stderr.strip().splitlines() or ["erreur"])[-1])
    return ok(f"Greffon « {params['name']} » créé (outils : {check.stdout.strip()}). Relisez-le puis activez-le "
              f"avec plugin_enable.", path=str(path), tools=check.stdout.strip().split(","))


def _plugins(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.config import Config
    approved = set(Config().get("plugins_approved"))
    plugins = [{"name": path.stem, "enabled": path.stem in approved, "path": str(path)}
               for path in sorted(PLUGINS.glob("*.py"))] if PLUGINS.is_dir() else []
    return ok(f"{len(plugins)} greffon(s)", plugins=plugins)


def _plugin_toggle(enable: bool):
    def handler(params: dict, _verbose: bool) -> dict:
        from djerba_ai.core.config import Config
        config = Config()
        approved = [name for name in config.get("plugins_approved") if name != params["name"]]
        if enable:
            if not (PLUGINS / f"{params['name']}.py").is_file():
                return fail("greffon introuvable")
            approved.append(params["name"])
        config.set("plugins_approved", approved)
        return ok(f"Greffon {params['name']} {'activé' if enable else 'désactivé'} (prochaine discussion).")
    return handler


def _compile(language: str):
    def handler(params: dict, _verbose: bool) -> dict:
        compiler = {"c": "gcc", "rust": "rustc"}[language]
        if shutil.which(compiler) is None:
            package = {"c": "build-essential", "rust": "rustc"}[language]
            return fail(f"{compiler} n'est pas installé : installez le paquet {package} (outil apt_install)")
        source = Path(os.path.expanduser(params["source"])).resolve()
        if not source.is_file():
            return fail("fichier source introuvable")
        output = source.with_suffix("")
        argv = [compiler, "-O2", "-Wall", str(source), "-o", str(output)] if language == "c" \
            else [compiler, "-O", str(source), "-o", str(output)]
        result = run_command(argv, timeout=600)
        if result.returncode != 0:
            return fail("compilation échouée", errors=result.stderr[-3000:])
        return ok(f"Programme compilé : {output}", binary=str(output), warnings=result.stderr[-2000:])
    return handler


TOOLS = [
    function_tool("apps_generated_list", C, "Applications créées avec le Studio.", _list,
                  keywords=("mes applications", "studio", "créées")),
    function_tool("app_run", C, "Lance une application du Studio (option : rechargement à chaud).", _run,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG},
                              "live": {"type": "boolean", "description": "rechargement à chaud", "default": False}},
                  required=("slug",), keywords=("lancer mon application", "tester", "live reload")),
    function_tool("app_open_folder", C, "Ouvre le dossier du code d'une application du Studio.", _open_folder,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG}},
                  required=("slug",), keywords=("code source", "dossier")),
    function_tool("app_delete", C, "Supprime une application du Studio et son lanceur.", _delete,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG}},
                  required=("slug",), dangerous=True, keywords=("supprimer mon application",)),
    function_tool("app_iterate", C, "Modifie une application du Studio selon une consigne (nouvelle version Git).",
                  _iterate,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG},
                              "instruction": {"type": "string", "description": "modification",
                                              "pattern": r"^[^\x00]{3,2000}$"}},
                  required=("slug", "instruction"), keywords=("modifier", "ajouter fonction", "améliorer l'application")),
    function_tool("app_history", C, "Historique des versions d'une application du Studio.", _history,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG}},
                  required=("slug",), keywords=("versions", "historique git")),
    function_tool("app_revert", C, "Revient à la version précédente d'une application du Studio.", _revert,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG}},
                  required=("slug",), dangerous=True, keywords=("annuler modification", "version précédente")),
    function_tool("app_export", C, "Exporte une application du Studio en archive .tar.gz dans votre dossier.", _export,
                  parameters={"slug": {"type": "string", "description": "identifiant", "pattern": SLUG}},
                  required=("slug",), keywords=("exporter", "partager", "archive")),
    function_tool("studio_templates", C, "Modèles d'applications intégrés (Snake, calculatrice, chronomètre).",
                  _templates, keywords=("modèles", "exemples", "templates")),
    function_tool("plugin_generate", C, "Crée un nouveau greffon (outil) pour Djerba lui-même, à activer ensuite.",
                  _plugin_generate,
                  parameters={"name": {"type": "string", "description": "nom du fichier",
                                       "pattern": r"^[a-z0-9_]{3,48}$"},
                              "instruction": {"type": "string", "description": "ce que fait l'outil",
                                              "pattern": r"^[^\x00]{5,1000}$"}},
                  required=("name", "instruction"), keywords=("nouvel outil", "greffon", "plugin", "étendre djerba")),
    function_tool("plugins_list", C, "Greffons créés pour Djerba et leur état.", _plugins,
                  keywords=("greffons", "plugins")),
    function_tool("plugin_enable", C, "Active un greffon (après relecture).", _plugin_toggle(True),
                  parameters={"name": {"type": "string", "description": "greffon", "pattern": r"^[a-z0-9_]{3,48}$"}},
                  required=("name",), dangerous=True, keywords=("activer greffon",)),
    function_tool("plugin_disable", C, "Désactive un greffon.", _plugin_toggle(False),
                  parameters={"name": {"type": "string", "description": "greffon", "pattern": r"^[a-z0-9_]{3,48}$"}},
                  required=("name",), keywords=("désactiver greffon",)),
    function_tool("compile_c", C, "Compile un fichier C (gcc).", _compile("c"),
                  parameters={"source": {"type": "string", "description": "fichier .c",
                                         "pattern": r"^[^\x00]+\.c$"}},
                  required=("source",), keywords=("compiler", "gcc", "langage c")),
    function_tool("compile_rust", C, "Compile un fichier Rust (rustc).", _compile("rust"),
                  parameters={"source": {"type": "string", "description": "fichier .rs",
                                         "pattern": r"^[^\x00]+\.rs$"}},
                  required=("source",), keywords=("compiler", "rust", "rustc")),
    command_tool("shellcheck_file", C, "Analyse un script shell (shellcheck).", ["shellcheck", "-f", "gcc", "{path}"],
                 parameters={"path": {"type": "string", "description": "script", "pattern": USER_FILE}},
                 required=("path",), success_codes=(0, 1), keywords=("vérifier script", "bash", "shellcheck"),
                 done="Analyse shellcheck."),
    command_tool("python_check", C, "Vérifie la syntaxe d'un fichier Python.", ["python3", "-m", "py_compile", "{path}"],
                 parameters={"path": {"type": "string", "description": "fichier .py",
                                      "pattern": r"^(~|/home/[^/\x00]+|/tmp)(/[^\x00]+)\.py$"}},
                 required=("path",), keywords=("vérifier python", "syntaxe"), done="Syntaxe Python correcte."),
    command_tool("git_status", C, "État Git d'une application du Studio.",
                 ["git", "-C", "{path}", "status", "--short", "--branch"],
                 parameters={"path": {"type": "string", "description": "dossier", "pattern": USER_FILE}},
                 required=("path",), keywords=("git", "modifications"), done="État Git."),
]

