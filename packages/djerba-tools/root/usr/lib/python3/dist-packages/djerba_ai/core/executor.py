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
        #: moteur d'IA et session à l'origine des actions (journal de transactions du démon root)
        self.origin: dict[str, str] = {}
        #: conversation en cours (actions liées à l'historique)
        self.conversation: int | None = None

    def _origin_params(self) -> dict[str, Any]:
        return {"engine": str(self.origin.get("engine", ""))[:80], "session": str(self.origin.get("session", ""))[:80],
                "tainted": bool(self.origin.get("tainted", False))}

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
                request_params: dict[str, Any] = {"name": name, "params": cleaned, "verbose": verbose}
                if self.origin:
                    request_params["origin"] = self._origin_params()
                result = ipc.call("sys.exec_tool", request_params)
            except ipc.RPCError as exc:
                result = fail("autorisation refusée" if exc.code == ipc.NOT_AUTHORIZED else str(exc))
            except Exception as exc:  # noqa: BLE001 - erreur de validation (ToolError)
                result = fail(str(exc))
        else:
            token = ipc.ORIGIN.set(self._origin_params()) if self.origin else None
            try:
                result = tool.run(params, verbose=verbose)
            finally:
                if token is not None:
                    ipc.ORIGIN.reset(token)
        if self.memory is not None:
            transaction = result.get("transaction")
            run_id = self.memory.record_run(name, request, params or {}, bool(result.get("success")),
                                            str(result.get("message", "")), conversation=self.conversation,
                                            engine=str(self.origin.get("engine", "")),
                                            tx=str(transaction["id"]) if isinstance(transaction, dict)
                                            and transaction.get("id") else None)
            result.setdefault("data", {})["run_id"] = run_id
        return result
