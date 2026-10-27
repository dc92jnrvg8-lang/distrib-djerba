# SPDX-License-Identifier: GPL-3.0-or-later
"""Boucle agentique de Djerba.

1. Le contexte système (core.context) et les faits mémorisés sont placés dans
   le prompt système : l'agent connaît la machine et ses réglages.
2. Les outils proposés au modèle sont choisis par pertinence (recherche
   lexicale + historique de réussite) ; l'outil ``tools_search`` permet au
   modèle d'en découvrir d'autres parmi tout le registre.
3. Le modèle appelle des outils ; toute action ``dangerous`` ou root est
   soumise à confirmation (puis à polkit pour root) ; les résultats lui sont
   renvoyés jusqu'à la réponse finale (8 étapes au plus).
4. Sans moteur de langage joignable, le mode dégradé propose directement les
   outils correspondant à la demande.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from djerba_ai.core import context
from djerba_ai.core.config import Config
from djerba_ai.core.executor import Executor
from djerba_ai.core.llm import LLMError, client_from_config
from djerba_ai.core.memory import Memory
from djerba_ai.tools import CATEGORY_LABELS, Registry, registry

MAX_STEPS = 8
ALWAYS = ("tools_search", "health_report", "web_search", "memory_remember", "memory_recall")

SYSTEM_PROMPT = """Tu es Djerba, l'assistant intégré de Djerba OS, une distribution Linux basée sur Debian 13, \
taillée pour le jeu, la vie privée et la simplicité. Tu administres réellement cet ordinateur grâce à tes outils.

Règles :
- Réponds en français, clairement et brièvement, sans jargon inutile ; propose des étapes concrètes.
- Pour observer ou agir, appelle un outil plutôt que de supposer ; n'invente jamais un résultat d'outil.
- Si aucun outil proposé ne convient, appelle tools_search pour en trouver un parmi les {count} disponibles.
- Les actions sensibles sont confirmées par l'utilisateur : explique en une phrase ce que tu vas faire.
- Avant une modification risquée du système installé, propose un instantané (snapshot_create).
- Pour une information récente ou hors de la machine, utilise web_search puis cite la source.
- Recommande ce qui convient à CETTE machine (matériel, session live ou installée, modes actifs).
- Pour créer une application ou un jeu, utilise app_generator ; pour un problème d'un outil Djerba, \
incident_analyze et patch_propose.
- Si aucune capacité ne convient même après tools_search, crée-la avec capability_create (code testé dans \
le bac à sable, intégré à chaud), puis utilise-la.

Configuration actuelle de la machine :
{context}

Ce que tu sais de l'utilisateur :
{facts}
"""

Event = Callable[[str, dict], None]
Confirm = Callable[[str, dict, dict], bool]


class Agent:
    def __init__(self, config: Config, registry: Registry, memory: Memory) -> None:
        self.config = config
        self.registry = registry
        self.memory = memory
        self.executor = Executor(registry, memory)

    def reload_plugins(self) -> list[str]:
        """Intégration à chaud des capacités générées approuvées."""
        self.config.load()
        self.registry = registry(include_plugins=True, approved=self.config.get("agent.plugins_approved"))
        self.executor.registry = self.registry
        return sorted(name for name in self.registry.tools if name.startswith("plugin."))

    # ---------------------------------------------------------------- Outils proposés
    def select_tools(self, request: str, extra: set[str] | None = None) -> list[str]:
        boost = self.memory.tool_scores()
        candidates = self.registry.search(request, limit=30)
        # Rang lexical, remonté par l'historique de réussite et les retours de l'utilisateur.
        ranked = sorted(candidates, key=lambda tool: candidates.index(tool) - 3 * boost.get(tool.name, 0))
        names = [tool.name for tool in ranked[:12]]
        for example in self.memory.examples(request):
            if example["tool"] not in names and self.registry.get(example["tool"]):
                names.append(example["tool"])
        for name in (*ALWAYS, *(extra or ())):
            if name not in names and self.registry.get(name):
                names.append(name)
        return names

    def _system_message(self) -> dict:
        text, _data = context.snapshot()
        facts = self.memory.facts(limit=20)
        facts_text = "\n".join(f"- {fact['subject']} : {fact['content']}" for fact in facts) or "- (rien pour l'instant)"
        return {"role": "system", "content": SYSTEM_PROMPT.format(count=len(self.registry), context=text,
                                                                  facts=facts_text)}

    # ---------------------------------------------------------------- Boucle
    def needs_confirmation(self, tool) -> bool:
        if tool.dangerous:
            return True
        return tool.requires_root and self.config.get("agent.autonomy") != "safe"

    def run(self, request: str, conversation: int, on_event: Event, confirm: Confirm) -> str:
        self.memory.add_message(conversation, "user", request)
        client = client_from_config(self.config, notify=lambda text: on_event("notice", {"text": text}))
        history = [{"role": row["role"], "content": row["content"]}
                   for row in self.memory.messages(conversation, limit=13)[:-1]
                   if row["role"] in ("user", "assistant")]
        messages: list[dict[str, Any]] = [self._system_message(), *history]
        examples = self.memory.examples(request)
        if examples:
            hints = "; ".join(f"« {row['request'][:80]} » → {row['tool']}" for row in examples)
            messages.append({"role": "system", "content": f"Demandes similaires réussies : {hints}."})
        messages.append({"role": "user", "content": request})
        active = set(self.select_tools(request))

        try:
            for _step in range(MAX_STEPS):
                tools = [self.registry.get(name).schema() for name in sorted(active)]
                on_event("thinking", {"tools": len(tools)})
                reply = client.chat(messages, tools, on_token=lambda piece: on_event("token", {"text": piece}))
                if not reply["tool_calls"]:
                    answer = reply["content"].strip() or "C'est fait."
                    self.memory.add_message(conversation, "assistant", answer)
                    on_event("final", {"text": answer})
                    return answer
                messages.append({"role": "assistant", "content": reply["content"],
                                 "tool_calls": [{"function": {"name": call["name"], "arguments": call["arguments"]}}
                                                for call in reply["tool_calls"]]})
                for call in reply["tool_calls"]:
                    result = self._call_tool(call["name"], call["arguments"], request, on_event, confirm)
                    if call["name"] == "tools_search" and result.get("success"):
                        active.update(item["name"] for item in result["data"].get("tools", []))
                    if call["name"] in ("capability_create", "plugin_generate", "plugin_approve") \
                            and result.get("success"):
                        active.update(self.reload_plugins())
                    messages.append({"role": "tool", "content": json.dumps(result, ensure_ascii=False)[:6000]})
            answer = "J'ai atteint la limite d'étapes pour cette demande ; reformulez-la ou découpez-la."
        except LLMError as exc:
            return self.degraded(request, conversation, on_event, str(exc))
        self.memory.add_message(conversation, "assistant", answer)
        on_event("final", {"text": answer})
        return answer

    def _call_tool(self, name: str, arguments: dict, request: str, on_event: Event, confirm: Confirm) -> dict:
        tool = self.registry.get(name)
        if tool is None:
            return {"success": False, "message": f"outil inconnu : {name} (utilise tools_search)", "data": {}}
        arguments = arguments if isinstance(arguments, dict) else {}
        on_event("tool_start", {"tool": name, "label": tool.description, "params": arguments,
                                "root": tool.requires_root})
        if self.needs_confirmation(tool) and not confirm(name, arguments, tool.describe()):
            result = {"success": False, "message": "action refusée par l'utilisateur", "data": {}}
        else:
            result = self.executor.execute(name, arguments, request=request)
        on_event("tool_result", {"tool": name, "result": result})
        return result

    # ---------------------------------------------------------------- Mode dégradé
    def degraded(self, request: str, conversation: int, on_event: Event, reason: str = "") -> str:
        matches = self.registry.search(request, limit=5)
        boost = self.memory.tool_scores()
        matches.sort(key=lambda tool: -boost.get(tool.name, 0))
        suggestions = [{"tool": tool.name, "description": tool.description,
                        "category": CATEGORY_LABELS.get(tool.category, tool.category),
                        "ready": not tool.required, "root": tool.requires_root} for tool in matches]
        intro = ("Le moteur d'IA n'est pas disponible"
                 + (f" ({reason})" if reason else "")
                 + ". Installez ou démarrez Ollama depuis l'onglet Modèles pour discuter librement.")
        if suggestions:
            body = "\n".join(f"• {item['description']} ({item['tool']})" for item in suggestions)
            answer = f"{intro}\n\nActions correspondant à votre demande :\n{body}"
        else:
            answer = f"{intro}\n\nAucune action ne correspond ; essayez des mots-clés comme « mise à jour », « jeu » ou « disque »."
        self.memory.add_message(conversation, "assistant", answer)
        on_event("suggestions", {"items": suggestions})
        on_event("final", {"text": answer, "degraded": True})
        return answer
