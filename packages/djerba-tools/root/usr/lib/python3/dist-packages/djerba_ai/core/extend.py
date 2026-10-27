# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-extension : Djerba se crée une capacité quand aucune ne convient.

1. Le modèle écrit un greffon Python (classe ``BaseTool``, jamais root) avec
   un auto-test ``selftest()``.
2. Le démon root le vérifie dans le bac à sable Git /var/lib/djerba-ai/sandbox
   (syntaxe, pyflakes, chargement et auto-test sous l'utilisateur « nobody »,
   15 s au plus) ; un échec est annulé par « git reset --hard ».
3. Trois tentatives au plus, chacune avec le détail de l'échec précédent ;
   ensuite, abandon propre.
4. Validé, il est enregistré dans ~/.local/share/djerba/plugins/ et activé
   (si agent.auto_extend) : l'agent le charge à chaud. Une capacité générée
   est toujours soumise à confirmation avant chaque exécution.
"""

from __future__ import annotations

import re

from djerba_ai.core import ipc
from djerba_ai.core.config import PLUGINS_DIR, Config
from djerba_ai.core.llm import LLMError, client_from_config

CODE_BLOCK = re.compile(r"```(?:python|python3|py)?\s*\n(.*?)```", re.DOTALL)

PROMPT = """Écris une capacité Python pour l'agent Djerba de Djerba OS (Debian 13, KDE Plasma 6) : {instruction}

Contrat obligatoire (nom de classe libre) :
```
from djerba_ai.tools.base import BaseTool, ok, fail, run_command

class Capacite(BaseTool):
    name = "{name}"
    category = "devtools"
    description = "Ce que fait la capacité, en français, en une phrase."
    requires_root = False
    parameters = {{"param": {{"type": "string", "description": "…", "pattern": r"^[^\\\\x00]{{1,200}}$"}}}}
    required = ("param",)

    def execute(self, params: dict, verbose: bool = False) -> dict:
        # commandes : run_command([...]) avec une liste d'arguments, jamais de shell
        return ok("message", resultat=...)   # ou fail("message")

    def selftest(self) -> bool:
        # vérifie le fonctionnement sans effet de bord, sans réseau, en moins de 10 s
        return self.run({{...paramètres d'exemple...}})["success"] is True
```
Règles : bibliothèque standard uniquement ; aucun accès root ; aucune boucle infinie ; aucun
import inutilisé. Réponds UNIQUEMENT avec le fichier Python complet, dans un seul bloc de code.
"""


def create_capability(name: str, instruction: str, config: Config | None = None, client=None) -> dict:
    config = config or Config()
    if not re.fullmatch(r"[a-z0-9_]{3,48}", name):
        return {"success": False, "message": "nom de capacité invalide"}
    client = client or client_from_config(config)
    allowed = int(config.get("system.sandbox_attempts"))
    messages = [{"role": "system", "content": "Tu es un développeur Python rigoureux."},
                {"role": "user", "content": PROMPT.format(name=name, instruction=instruction)}]
    result: dict = {"success": False, "checks": []}
    attempt = 0
    for attempt in range(1, allowed + 1):
        try:
            reply = client.chat(messages)
        except LLMError as exc:
            return {"success": False, "message": f"moteur d'IA indisponible : {exc}"}
        match = CODE_BLOCK.search(reply["content"])
        if not match:
            feedback = "Réponse sans bloc de code Python."
        else:
            try:
                result = ipc.call("sys.test_plugin", {"name": name, "content": match.group(1)}, timeout=180)
            except ipc.RPCError as exc:
                return {"success": False, "message": str(exc)}
            if result.get("success"):
                break
            feedback = "\n".join(f"- {check['check']} : {'réussi' if check['success'] else 'ÉCHEC'} "
                                 f"{check.get('detail', '')[:500]}" for check in result.get("checks", []))
        messages += [{"role": "assistant", "content": reply["content"]},
                     {"role": "user", "content": f"Tentative {attempt}/{allowed} rejetée (annulée dans le bac à "
                                                 f"sable) :\n{feedback}\nCorrige et renvoie le fichier complet."}]
    if not result.get("success"):
        return {"success": False, "attempts": attempt, "checks": result.get("checks", []),
                "message": f"Capacité « {name} » non créée : {attempt} tentative(s) rejetée(s), tout a été annulé."}

    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    path = PLUGINS_DIR / f"{name}.py"
    path.write_text(result["content"], encoding="utf-8")
    path.chmod(0o600)
    enabled = bool(config.get("agent.auto_extend"))
    if enabled:
        approved = set(config.get("agent.plugins_approved")) | {name}
        config.set("agent.plugins_approved", sorted(approved))
    return {"success": True, "attempts": attempt, "tools": [f"plugin.{tool}" for tool in result.get("tools", [])],
            "enabled": enabled, "path": str(path), "checks": result.get("checks", []),
            "message": (f"Nouvelle capacité « {name} » vérifiée (tentative {attempt}/{allowed}) et "
                        + ("intégrée à chaud." if enabled else "enregistrée ; activez-la dans le Studio."))}
