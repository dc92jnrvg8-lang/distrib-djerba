# SPDX-License-Identifier: GPL-3.0-or-later
"""Exécution isolée des capacités générées (processus distinct, confiance zéro).

Usage :
    python3 -s -P plugin_runner.py describe CHEMIN_DU_GREFFON
    python3 -s -P plugin_runner.py run CHEMIN_DU_GREFFON NOM_DE_L_OUTIL < paramètres JSON

Toujours lancé par l'agent sous seccomp (sans réseau, sauf autorisation) et,
si chargé, sous le profil AppArmor du greffon : le code d'une capacité
générée n'est jamais chargé dans le processus de l'agent — ni pour la
décrire, ni pour l'exécuter — et n'en hérite d'aucune donnée.
"""

import importlib.util
import json
import sys


def load(path: str) -> list:
    from djerba_ai.tools.base import BaseTool
    spec = importlib.util.spec_from_file_location("djerba_isolated_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [cls for cls in vars(module).values() if isinstance(cls, type) and issubclass(cls, BaseTool)
            and cls.__module__ == module.__name__ and cls.name]


def main(argv: list[str]) -> int:
    mode, path = argv[0], argv[1]
    classes = load(path)
    if mode == "describe":
        print(json.dumps([{"name": cls.name, "description": cls.description, "parameters": cls.parameters,
                           "required": list(cls.required), "keywords": list(cls.keywords)} for cls in classes],
                         ensure_ascii=False, default=str))
        return 0
    tool_name = argv[2]
    matching = [cls for cls in classes if cls.name == tool_name]
    if not matching:
        print(json.dumps({"success": False, "message": f"capacité {tool_name} introuvable", "data": {}}))
        return 1
    tool = matching[0]()
    tool.requires_root = False
    params = json.loads(sys.stdin.read() or "{}")
    print(json.dumps(tool.run(params), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
