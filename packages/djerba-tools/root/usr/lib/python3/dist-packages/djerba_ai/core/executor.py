# SPDX-License-Identifier: GPL-3.0-or-later
"""Exécution des outils côté utilisateur.

Les outils ordinaires s'exécutent dans le processus de l'utilisateur ; les
outils ``requires_root`` sont transmis au démon djerba-ai-sysd (polkit). Toute
exécution est journalisée dans la mémoire (apprentissage de l'agent).
"""

from __future__ import annotations

from typing import Any

from djerba_ai.core import ipc
from djerba_ai.tools import Registry
from djerba_ai.tools.base import BaseTool, fail


class Executor:
    def __init__(self, registry: Registry, memory=None) -> None:
        self.registry = registry
        self.memory = memory

    def tool(self, name: str) -> BaseTool | None:
        return self.registry.get(name)

    def execute(self, name: str, params: dict | None = None, request: str = "",
                verbose: bool = False) -> dict[str, Any]:
        tool = self.registry.get(name)
        if tool is None:
            return fail(f"outil inconnu : {name}")
        if tool.requires_root:
            try:
                cleaned = tool.validate(params)  # refus immédiat d'un paramètre invalide
                result = ipc.call("sys.exec_tool", {"name": name, "params": cleaned, "verbose": verbose})
            except ipc.RPCError as exc:
                result = fail("autorisation refusée" if exc.code == ipc.NOT_AUTHORIZED else str(exc))
            except Exception as exc:  # noqa: BLE001 - erreur de validation (ToolError)
                result = fail(str(exc))
        else:
            result = tool.run(params, verbose=verbose)
        if self.memory is not None:
            run_id = self.memory.record_run(name, request, params or {}, bool(result.get("success")),
                                            str(result.get("message", "")))
            result.setdefault("data", {})["run_id"] = run_id
        return result
