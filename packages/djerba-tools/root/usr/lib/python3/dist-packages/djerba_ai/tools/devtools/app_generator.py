# SPDX-License-Identifier: GPL-3.0-or-later
"""Studio Djerba : création d'applications à la volée.

« Crée un jeu Snake en PyQt6 », « Crée une calculatrice HTML5 »… :

1. le code est écrit par le modèle de langage (ou, hors ligne, tiré des
   modèles intégrés : Snake PyQt6, calculatrice HTML5, chronomètre QML) ;
2. il est vérifié : compilation Python et lancement hors écran de quelques
   secondes, chargement QML sans erreur, structure HTML ; en cas d'échec, le
   modèle reçoit l'erreur et corrige une fois ;
3. il est enregistré dans ~/.local/share/djerba/generated_apps/<slug>/ avec
   un dépôt Git (historique, retour arrière), un requirements.txt et un README ;
4. un lanceur ~/.local/share/applications/djerba-<slug>.desktop l'ajoute au
   menu de KDE Plasma (exécuté par « djerba-ai run-app <slug> », avec
   rechargement à chaud en option).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from djerba_ai.tools.base import BaseTool, fail, ok

TEMPLATES = Path(__file__).with_name("templates")
APPS_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "djerba" / "generated_apps"
LAUNCHERS = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
MAIN_FILE = {"pyqt6": "main.py", "html": "index.html", "qml": "main.qml"}
ICONS = {"pyqt6": "applications-games", "html": "applications-internet", "qml": "applications-utilities"}

BUILTIN = [
    {"keywords": ("snake", "serpent"), "kind": "pyqt6", "file": "snake.py", "name": "Snake"},
    {"keywords": ("calculatrice", "calculator", "calcul"), "kind": "html", "file": "calculator.html",
     "name": "Calculatrice"},
    {"keywords": ("chronomètre", "chrono", "stopwatch", "minuteur"), "kind": "qml", "file": "stopwatch.qml",
     "name": "Chronomètre"},
]

QML_RUNNER = '''#!/usr/bin/env python3
"""Lanceur de l'application QML générée par le Studio Djerba."""
import sys
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

if "--help" in sys.argv:
    print("Application QML générée par le Studio Djerba.")
    sys.exit(0)
app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("main.qml"))))
if not engine.rootObjects():
    sys.exit(1)
sys.exit(app.exec())
'''

GENERATION_PROMPT = """Écris une application complète et soignée pour Djerba OS (Debian 13, KDE Plasma 6) : {instruction}

Contraintes techniques :
{constraints}
- interface en français, couleurs Djerba (fond #070B18, accent #00C2CB, texte #F8FAFC) ;
- code complet et fonctionnel, sans aucune partie à compléter.
Réponds UNIQUEMENT avec le contenu du fichier {main}, dans un seul bloc de code.
"""

CONSTRAINTS = {
    "pyqt6": "- un seul fichier Python 3 (main.py) utilisant uniquement PyQt6 et la bibliothèque standard ;\n"
             "- le programme affiche son aide et quitte avec le code 0 s'il reçoit --help ;\n"
             "- point d'entrée « if __name__ == \"__main__\": » qui crée QApplication et appelle app.exec().",
    "html": "- un seul fichier HTML5 autonome (CSS et JavaScript intégrés, aucune ressource externe ni CDN) ;\n"
            "- fonctionne hors ligne, au clavier et à la souris.",
    "qml": "- un seul fichier QML (main.qml) pour Qt 6 : racine ApplicationWindow visible ;\n"
           "- imports QtQuick, QtQuick.Controls, QtQuick.Layouts uniquement ;\n"
           "- aucune dépendance C++ ni Python.",
}

CODE_BLOCK = re.compile(r"```[a-zA-Z0-9]*\s*\n(.*?)```", re.DOTALL)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug[:40].strip("-") or "application"


def detect_kind(instruction: str) -> str:
    lowered = instruction.lower()
    if any(word in lowered for word in ("html", "web", "navigateur", "javascript")):
        return "html"
    if "qml" in lowered:
        return "qml"
    return "pyqt6"


def git(app_dir: Path, *arguments: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_AUTHOR_NAME="Djerba Studio", GIT_AUTHOR_EMAIL="studio@djerba.local",
               GIT_COMMITTER_NAME="Djerba Studio", GIT_COMMITTER_EMAIL="studio@djerba.local")
    return subprocess.run(["git", "-C", str(app_dir), *arguments], capture_output=True, text=True, env=env,
                          check=False)


class _StructureChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()

    def handle_starttag(self, tag, _attrs):
        self.tags.add(tag)


def validate(kind: str, app_dir: Path) -> tuple[bool, str]:
    """Vérifie l'application générée ; renvoie (succès, détail de l'erreur)."""
    main = app_dir / MAIN_FILE[kind]
    if kind == "html":
        parser = _StructureChecker()
        parser.feed(main.read_text(errors="replace"))
        missing = {"html", "body"} - parser.tags
        if missing:
            return False, f"balises manquantes : {', '.join(sorted(missing))}"
        if re.search(r"""<(script|link)[^>]+(src|href)=["']https?://""", main.read_text(errors="replace")):
            return False, "ressource externe interdite (l'application doit fonctionner hors ligne)"
        return True, ""
    script = app_dir / "main.py"
    compiled = subprocess.run([sys.executable, "-m", "py_compile", str(script)], capture_output=True, text=True)
    if compiled.returncode != 0:
        return False, compiled.stderr[-1500:]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
    try:
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=6,
                                cwd=app_dir, env=environment)
    except subprocess.TimeoutExpired:
        return True, ""  # toujours en cours d'exécution après 6 s : l'application tourne
    errors = result.stderr[-1500:]
    if result.returncode != 0 or "Traceback" in errors or re.search(r"qml:\d+|\.qml:\d+:\d+: ", errors):
        return False, errors or f"arrêt immédiat (code {result.returncode})"
    return True, ""


def write_launcher(slug: str, name: str, kind: str, comment: str, game: bool = False) -> Path:
    LAUNCHERS.mkdir(parents=True, exist_ok=True)
    path = LAUNCHERS / f"djerba-{slug}.desktop"
    clean = lambda value: re.sub(r"[\r\n]+", " ", value)[:200]
    path.write_text(
        "[Desktop Entry]\nType=Application\nVersion=1.0\n"
        f"Name={clean(name)}\nComment={clean(comment)}\n"
        f"Exec=djerba-ai run-app {slug}\nIcon={ICONS[kind]}\nTerminal=false\n"
        f"Categories={'Game' if game else 'Utility'};X-Djerba-Studio;\n",
        encoding="utf-8")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(LAUNCHERS)], capture_output=True, check=False)
    return path


def ask_model(instruction: str, kind: str, previous: str = "", error: str = "") -> str | None:
    from djerba_ai.core.config import Config
    from djerba_ai.core.llm import LLMError, client_from_config
    client = client_from_config(Config())
    prompt = GENERATION_PROMPT.format(instruction=instruction, constraints=CONSTRAINTS[kind], main=MAIN_FILE[kind])
    messages = [{"role": "system", "content": "Tu es un développeur expert en PyQt6, QML et HTML5."},
                {"role": "user", "content": prompt}]
    if previous:
        messages += [{"role": "assistant", "content": f"```\n{previous}\n```"},
                     {"role": "user", "content": f"Cette version échoue :\n{error}\nCorrige-la et renvoie le "
                                                 f"fichier complet dans un seul bloc de code."}]
    try:
        reply = client.chat(messages)
    except LLMError:
        return None
    match = CODE_BLOCK.search(reply["content"])
    return match.group(1) if match else None


def write_app(app_dir: Path, kind: str, code: str) -> None:
    (app_dir / MAIN_FILE[kind]).write_text(code, encoding="utf-8")
    if kind == "pyqt6":
        (app_dir / "main.py").chmod(0o755)
    if kind == "qml":
        (app_dir / "main.py").write_text(QML_RUNNER, encoding="utf-8")
        (app_dir / "main.py").chmod(0o755)


class AppGenerator(BaseTool):
    name = "app_generator"
    category = "devtools"
    description = ("Crée une application ou un jeu à partir d'une description (PyQt6, QML ou HTML5), la vérifie, "
                   "l'enregistre avec Git et l'ajoute au menu de Plasma.")
    parameters = {
        "instruction": {"type": "string", "description": "ce que doit faire l'application",
                        "pattern": r"^[^\x00]{4,2000}$"},
        "kind": {"type": "string", "description": "pyqt6, qml, html ou auto",
                 "enum": ["auto", "pyqt6", "qml", "html"], "default": "auto"},
        "name": {"type": "string", "description": "nom affiché (facultatif)", "pattern": r"^[^\x00\n]{1,60}$"},
    }
    required = ("instruction",)
    keywords = ("créer application", "créer jeu", "générer", "coder", "programme", "studio", "app", "snake")
    binaries = ("git",)

    def execute(self, params: dict, verbose: bool = False) -> dict:
        instruction = params["instruction"]
        kind = detect_kind(instruction) if params["kind"] == "auto" else params["kind"]
        template = next((item for item in BUILTIN if any(word in instruction.lower() for word in item["keywords"])),
                        None)
        name = params.get("name") or (template["name"] if template else instruction.split(".")[0][:40].capitalize())
        slug = slugify(name)
        app_dir = APPS_DIR / slug
        suffix = 2
        while app_dir.exists():
            app_dir = APPS_DIR / f"{slug}-{suffix}"
            suffix += 1
        slug = app_dir.name
        app_dir.mkdir(parents=True)

        code = ask_model(instruction, kind)
        origin = "modèle de langage"
        if code is None:
            if template is None:
                shutil.rmtree(app_dir)
                return fail("Moteur d'IA indisponible et aucun modèle intégré ne correspond (essayez « Snake », "
                            "« calculatrice » ou « chronomètre », ou démarrez Ollama).")
            kind = template["kind"]
            code = (TEMPLATES / template["file"]).read_text(encoding="utf-8")
            origin = "modèle intégré (hors ligne)"
        write_app(app_dir, kind, code)
        valid, error = validate(kind, app_dir)
        if not valid and origin == "modèle de langage":
            repaired = ask_model(instruction, kind, previous=code, error=error)
            if repaired:
                write_app(app_dir, kind, repaired)
                valid, error = validate(kind, app_dir)
        if not valid:
            shutil.rmtree(app_dir)
            return fail(f"L'application générée ne fonctionne pas : {error[-400:]}")

        requirements = "PyQt6  # fourni par Debian : python3-pyqt6\n" if kind in ("pyqt6", "qml") else ""
        (app_dir / "requirements.txt").write_text(requirements or "# aucune dépendance (HTML5 autonome)\n")
        (app_dir / "README.md").write_text(f"# {name}\n\nGénéré par le Studio Djerba ({origin}).\n\n"
                                           f"Demande : {instruction}\n\nLancer : djerba-ai run-app {slug}\n"
                                           f"Rechargement à chaud : djerba-ai run-app {slug} --live\n")
        (app_dir / ".djerba-app").write_text(f"kind={kind}\nname={name}\n")
        git(app_dir, "init", "-q", "-b", "main")
        git(app_dir, "add", "-A")
        git(app_dir, "commit", "-q", "-m", f"Création : {instruction[:200]}")
        game = any(word in instruction.lower() for word in ("jeu", "game", "snake", "tetris", "pong", "casse"))
        launcher = write_launcher(slug, name, kind, instruction, game)
        return ok(f"« {name} » est prêt ({origin}) et se trouve dans le menu des applications.",
                  slug=slug, path=str(app_dir), kind=kind, launcher=str(launcher))
