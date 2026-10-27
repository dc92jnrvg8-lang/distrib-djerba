# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-évolution : Djerba se crée une capacité quand aucune ne convient.

1. Le modèle écrit un module Python (classe ``BaseTool``, jamais root) **et**
   ses tests unitaires Pytest (``test_<nom>.py``).
2. Le démon root les vérifie dans le bac à sable Git /var/lib/djerba-ai/sandbox
   (syntaxe, pyflakes, chargement, tests Pytest avec 15 s au plus par test,
   dépendances éventuelles dans un venv temporaire) ; le code s'exécute dans
   le runtime hybride (namespaces, seccomp-bpf, cgroups v2). Un échec est
   annulé : git reset --hard, venv supprimé, dossier purgé.
3. Trois tentatives au plus, chacune avec le détail de l'échec précédent
   (sortie de Pytest comprise) ; ensuite, abandon propre.
4. Validé, le module est enregistré dans ~/.local/share/djerba/plugins/ (tests
   dans plugins/tests/), activé si agent.auto_extend, et l'agent le charge à
   chaud. Ses dépendances vont dans le venv interne (PEP 668). Une capacité
   générée reste soumise à confirmation avant chaque exécution.
"""

from __future__ import annotations

import ast
import re

from djerba_ai.core import ipc
from djerba_ai.core.config import PLUGINS_DIR, Config
from djerba_ai.core.llm import LLMError

CODE_BLOCK = re.compile(r"```(?:python|python3|py)?\s*\n(.*?)```", re.DOTALL)
NAME = re.compile(r"^[a-z][a-z0-9_]{2,47}$")

PROMPT = """Écris une capacité Python pour l'agent Djerba de Djerba OS (Debian 13, KDE Plasma 6) : {instruction}

Réponds avec EXACTEMENT deux blocs de code Python, dans cet ordre.

Bloc 1 — le module « {name}.py » (nom de classe libre) :
```python
from djerba_ai.tools.base import BaseTool, ok, fail, run_command

REQUIREMENTS = []  # paquets PyPI nécessaires (roues binaires), vide si la bibliothèque standard suffit


class Capacite(BaseTool):
    name = "{name}"
    category = "devtools"
    description = "Ce que fait la capacité, en français, en une phrase."
    requires_root = False
    parameters = {{"param": {{"type": "string", "description": "…"}}}}
    required = ("param",)

    def execute(self, params: dict, verbose: bool = False) -> dict:
        # commandes : run_command([...]) avec une liste d'arguments, jamais de shell
        return ok("message", resultat=...)   # ou fail("message")
```

Bloc 2 — ses tests Pytest « test_{name}.py » (au moins deux tests, sans réseau, sans effet de bord,
chacun en moins de 15 s) :
```python
from {name} import Capacite


def test_cas_nominal():
    resultat = Capacite().run({{"param": "…"}})
    assert resultat["success"] is True


def test_parametre_invalide():
    assert Capacite().run({{}})["success"] is False
```
Règles : aucun accès root, aucun réseau dans les tests, aucune boucle infinie, aucun import inutilisé.
"""


def requirements_of(module: str) -> list[str]:
    """Liste littérale REQUIREMENTS du module (sans l'exécuter)."""
    try:
        tree = ast.parse(module)
    except SyntaxError:
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "REQUIREMENTS" for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return []
            return [str(item) for item in value][:10] if isinstance(value, (list, tuple)) else []
    return []


def create_capability(name: str, instruction: str, config: Config | None = None, client=None) -> dict:
    config = config or Config()
    if not NAME.fullmatch(name):
        return {"success": False, "message": "nom de capacité invalide (lettres minuscules, chiffres, _)"}
    if client is None:
        from djerba_ai.core.engine import CognitiveEngine
        client = CognitiveEngine(config).manager
    allowed = int(config.get("system.sandbox_attempts"))
    messages = [{"role": "system", "content": "Tu es un développeur Python rigoureux qui écrit du code testé."},
                {"role": "user", "content": PROMPT.format(name=name, instruction=instruction)}]
    result: dict = {"success": False, "checks": []}
    attempt = 0
    for attempt in range(1, allowed + 1):
        try:
            reply = client.chat(messages)
        except LLMError as exc:
            return {"success": False, "message": f"moteur d'IA indisponible : {exc}"}
        blocks = CODE_BLOCK.findall(reply["content"])
        if len(blocks) < 2:
            feedback = "Il faut exactement deux blocs de code : le module puis ses tests Pytest."
        else:
            module, tests = blocks[0], blocks[1]
            try:
                result = ipc.call("sys.test_plugin", {"name": name, "content": module, "tests": tests,
                                                      "requirements": requirements_of(module)}, timeout=900)
            except ipc.RPCError as exc:
                return {"success": False, "message": str(exc)}
            if result.get("success"):
                break
            feedback = "\n".join(f"- {check['check']} : {'réussi' if check['success'] else 'ÉCHEC'} "
                                 f"{check.get('detail', '')[-900:]}" for check in result.get("checks", []))
        messages += [{"role": "assistant", "content": reply["content"]},
                     {"role": "user", "content": f"Tentative {attempt}/{allowed} rejetée (annulée dans le bac à "
                                                 f"sable) :\n{feedback}\nCorrige et renvoie les deux blocs complets."}]
    if not result.get("success"):
        return {"success": False, "attempts": attempt, "checks": result.get("checks", []),
                "message": f"Capacité « {name} » non créée : {attempt} tentative(s) rejetée(s), tout a été annulé."}

    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    path = PLUGINS_DIR / f"{name}.py"
    path.write_text(result["content"], encoding="utf-8")
    path.chmod(0o600)
    tests_dir = PLUGINS_DIR / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / f"test_{name}.py").write_text(result.get("tests", ""), encoding="utf-8")
    if result.get("requirements"):
        try:
            ipc.call("sys.venv_install", {"packages": result["requirements"]}, timeout=1800)
        except ipc.RPCError as exc:
            return {"success": False, "message": f"dépendances non installées dans le venv interne : {exc}"}
    enabled = bool(config.get("agent.auto_extend"))
    if enabled:
        approved = set(config.get("agent.plugins_approved")) | {name}
        config.set("agent.plugins_approved", sorted(approved))
    return {"success": True, "attempts": attempt, "tools": [f"plugin.{tool}" for tool in result.get("tools", [])],
            "enabled": enabled, "path": str(path), "checks": result.get("checks", []),
            "tests_passed": result.get("tests_passed", 0),
            "message": (f"Nouvelle capacité « {name} » : {result.get('tests_passed', 0)} test(s) Pytest réussi(s) "
                        f"dans le bac à sable (tentative {attempt}/{allowed}) ; "
                        + ("intégrée à chaud." if enabled else "enregistrée ; activez-la dans le Studio."))}
