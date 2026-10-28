# SPDX-License-Identifier: GPL-3.0-or-later
"""Agent : outils, mémoire, apprentissage, auto-réparation, web, modèles, réglages."""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from djerba_ai.tools.base import command_tool, fail, function_tool, ok

C = "agentic"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; Djerba OS) djerba-ai/1.0"


_MEMORY = None


def _memory():
    """Connexion SQLite unique par processus."""
    global _MEMORY
    if _MEMORY is None:
        from djerba_ai.core.memory import Memory
        _MEMORY = Memory()
    return _MEMORY


def _config():
    from djerba_ai.core.config import Config
    return Config()


def _client():
    from djerba_ai.core.llm import client_from_config
    return client_from_config(_config())


# ---------------------------------------------------------------- Outils et mémoire
def _tools_search(params: dict, _verbose: bool) -> dict:
    from djerba_ai.tools import registry
    found = registry().search(params["query"], limit=params["limit"])
    return ok(f"{len(found)} outil(s) trouvé(s) ; ils sont maintenant utilisables.",
              tools=[{"name": tool.name, "description": tool.description, "root": tool.requires_root}
                     for tool in found])


def _tools_categories(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.tools import CATEGORIES, CATEGORY_LABELS, registry
    reg = registry()
    counts = {CATEGORY_LABELS[category]: len(reg.by_category(category)) for category in CATEGORIES}
    return ok(f"{len(reg)} capacités réparties en {len(counts)} catégories", categories=counts)


def _remember(params: dict, _verbose: bool) -> dict:
    fact_id = _memory().remember(params["subject"], params["content"])
    return ok("C'est noté, je m'en souviendrai.", id=fact_id)


def _recall(params: dict, _verbose: bool) -> dict:
    facts = _memory().facts(params.get("query", ""))
    return ok(f"{len(facts)} souvenir(s)", facts=[{"id": f["id"], "subject": f["subject"], "content": f["content"]}
                                                 for f in facts])


def _forget(params: dict, _verbose: bool) -> dict:
    return ok("Oublié.") if _memory().forget(params["id"]) else fail("souvenir introuvable")


def _conversations(_params: dict, _verbose: bool) -> dict:
    rows = _memory().conversations(20)
    return ok(f"{len(rows)} discussion(s)", conversations=[{"id": r["id"], "title": r["title"]} for r in rows])


def _feedback(params: dict, _verbose: bool) -> dict:
    _memory().feedback(params["run_id"], 1 if params["useful"] else -1)
    return ok("Merci : j'en tiendrai compte pour mieux choisir mes actions.")


def _learning(_params: dict, _verbose: bool) -> dict:
    memory = _memory()
    stats = memory.stats()
    scores = sorted(memory.tool_scores().items(), key=lambda item: -item[1])[:10]
    return ok(f"{stats['tool_runs']} actions exécutées ({stats['successes']} réussies), "
              f"{stats['facts']} souvenir(s)", **stats, favorite_tools=[name for name, _ in scores])


# ---------------------------------------------------------------- Auto-réparation
def _health() -> dict:
    from djerba_ai.core.health import report
    result = report()
    return ok(f"Score de santé : {result['score']}/100", **result)


def _incidents(params: dict, _verbose: bool) -> dict:
    rows = _memory().incidents(params.get("status") or None)
    return ok(f"{len(rows)} incident(s)", incidents=[
        {"id": r["id"], "source": r["source"], "target": r["target"], "status": r["status"],
         "occurrences": r["occurrences"], "message": r["message"][:300]} for r in rows])


def _incident_analyze(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.llm import LLMError
    incident = _memory().incident(params["id"])
    if incident is None:
        return fail("incident introuvable")
    prompt = (f"Erreur observée dans {incident['source']} ({incident['occurrences']} fois) :\n"
              f"{incident['message'][:3000]}\n\nExplique simplement la cause probable et la solution, en "
              f"3 à 6 phrases, pour un utilisateur de Djerba OS (Debian 13, KDE Plasma 6).")
    try:
        reply = _client().chat([{"role": "user", "content": prompt}])
    except LLMError as exc:
        return fail(f"moteur d'IA indisponible : {exc}")
    return ok(reply["content"].strip(), incident=incident["id"])


def _patch_propose(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.selfheal import propose_patch
    result = propose_patch(_memory(), _client(), params["id"])
    return (ok if result.get("success") else fail)(result.get("message", ""), **{
        key: value for key, value in result.items() if key in ("patch_id", "checks", "diff", "target")})


def _ipc(method: str, params: dict) -> dict:
    from djerba_ai.core import ipc
    try:
        result = ipc.call(method, params)
    except ipc.RPCError as exc:
        return fail(str(exc))
    if isinstance(result, dict) and "success" in result:
        return (ok if result["success"] else fail)(result.get("message", ""), **{
            key: value for key, value in result.items() if key not in ("success", "message")})
    return ok("Terminé.", result=result)


def _patch_deploy(params: dict, _verbose: bool) -> dict:
    result = _ipc("sys.deploy_patch", {"patch_id": params["patch_id"]})
    if result["success"]:
        memory = _memory()
        for incident in memory.incidents("correctif prêt"):
            if params["patch_id"] in (incident.get("patch") or ""):
                memory.update_incident(incident["id"], "corrigé")
    return result


# ---------------------------------------------------------------- Web
class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, _attrs):
        if tag in self.SKIP:
            self.depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "br", "li", "h1", "h2", "h3", "tr", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(self.parts))).strip()


def _fetch(url: str, data: bytes | None = None, timeout: int = 20) -> str:
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT,
                                                              "Accept-Language": "fr,en;q=0.7"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if "html" not in response.headers.get("Content-Type", "html") and "text" not in response.headers.get(
                "Content-Type", "text"):
            raise ValueError("contenu non textuel")
        return response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")


def _web_search(params: dict, _verbose: bool) -> dict:
    if not _config().get("agent.web_search"):
        return fail("la recherche web est désactivée dans les réglages de Djerba")
    try:
        page = _fetch("https://html.duckduckgo.com/html/",
                      urllib.parse.urlencode({"q": params["query"], "kl": "fr-fr"}).encode())
    except (OSError, ValueError) as exc:
        return fail(f"recherche impossible (hors ligne ?) : {exc}")
    results = []
    for match in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
                             r'class="result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL):
        href, title, snippet = match.groups()
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query)["uddg"][0])
        clean = lambda value: html.unescape(re.sub(r"<[^>]+>", "", value)).strip()
        results.append({"title": clean(title), "url": href, "snippet": clean(snippet)})
        if len(results) >= params["count"]:
            break
    if not results:
        return fail("aucun résultat")
    return ok(f"{len(results)} résultat(s) pour « {params['query']} » (DuckDuckGo)", results=results)


def _web_fetch(params: dict, _verbose: bool) -> dict:
    if not _config().get("agent.web_search"):
        return fail("l'accès web est désactivé dans les réglages de Djerba")
    try:
        page = _fetch(params["url"])
    except (OSError, ValueError) as exc:
        return fail(f"page inaccessible : {exc}")
    parser = _TextExtractor()
    parser.feed(page)
    text = parser.text()
    return ok(parser.title.strip() or params["url"], url=params["url"], text=text[:8000],
              truncated=len(text) > 8000)


# ---------------------------------------------------------------- Cerveau, modèles et réglages
PROVIDER_LABELS = {"local": "Ollama local", "tailscale": "machine distante (Tailscale)", "cloud": "API en ligne"}
MODEL_KEYS = {"local": "llm.local.model", "tailscale": "llm.tailscale.model", "cloud": "llm.cloud.model"}


def _engine_status(_params: dict, _verbose: bool) -> dict:
    config = _config()
    health = _client().health(5)
    provider = config.get("llm.provider")
    fallback = health.get("fallback") or {}
    return ok(f"Cerveau : {PROVIDER_LABELS[provider]} — {health.get('detail')} (modèle {health.get('model')}, "
              f"{health.get('latency_ms')} ms)" + (f" ; repli local {'prêt' if fallback.get('ok') else 'indisponible'}"
                                                    if fallback else ""),
              provider=provider, **health)


def _brain_switch(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.config import ConfigError
    config = _config()
    try:
        config.set("llm.provider", params["provider"])
        config.set("llm.engine", params["provider"])
        if params.get("model"):
            config.set(MODEL_KEYS[params["provider"]], params["model"])
    except ConfigError as exc:
        return fail(str(exc))
    health = _client().health(5)
    return (ok if health.get("ok") else fail)(
        f"Cerveau : {PROVIDER_LABELS[params['provider']]} — {health.get('detail')}", **health)


def _engines_list(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import engines
    config = _config()
    active = engines.active_id(config)
    items = [{"id": engine["id"], "nom": engine["name"], "type": engines.KIND_LABELS.get(engine["kind"], engine["kind"]),
              "modèle": engine.get("model", ""), "actif": engine["id"] == active,
              "capacité": (engine.get("profile") or {}).get("label", "non mesurée")}
             for engine in engines.catalog(config)]
    return ok(f"{len(items)} moteur(s) d'IA configuré(s) ; actif : {active}", engines=items)


def _engine_use(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import engines
    from djerba_ai.core.config import ConfigError
    config = _config()
    engine = engines.find(params["id"], config)
    if engine is None:
        return fail(f"moteur inconnu : {params['id']} (engines_list)")
    try:
        config.set("llm.engine", engine["id"])
    except ConfigError as exc:
        return fail(str(exc))
    return ok(f"Moteur actif : {engine['name']} — la conversation continue avec lui.")


def _model_list(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.llm import LLMError
    try:
        models = _client().models()
    except LLMError as exc:
        return fail(str(exc))
    return ok(f"{len(models)} modèle(s) disponible(s)", models=models)


def _model_recommend(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.config import RECOMMENDED_MODELS, memory_gb
    ram = memory_gb()
    fitting = [model for model in RECOMMENDED_MODELS if model["min_ram_gb"] <= ram]
    return ok(f"{ram:.0f} Go de RAM : {len(fitting)} modèle(s) adapté(s)", ram_gb=round(ram), models=fitting)


def _model_pull(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.llm import LLMError, LocalOllamaProvider
    try:
        LocalOllamaProvider(_config().get("llm.local.endpoint"), params["model"]).pull(params["model"])
    except LLMError as exc:
        return fail(str(exc))
    return ok(f"Modèle {params['model']} prêt.")


def _model_set(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.config import ConfigError
    config = _config()
    provider = params.get("provider") or config.get("llm.provider")
    try:
        config.set(MODEL_KEYS[provider], params["model"])
    except ConfigError as exc:
        return fail(str(exc))
    return ok(f"Modèle {params['model']} pour {PROVIDER_LABELS[provider]}.")


def _transactions_list(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import ipc
    try:
        items = ipc.call("tx.list", {"limit": int(params.get("limit", 20))}, timeout=60)["transactions"]
    except ipc.RPCError as exc:
        return fail(f"journal des modifications indisponible : {exc}")
    summary = [{"id": item["id"], "date": item["started"], "état": item["state"],
                "action": item.get("tool") or item["method"], "moteur": item.get("engine", ""),
                "fichiers": len(item.get("changes") or {}), "annulable": item["undoable"]} for item in items]
    return ok(f"{len(summary)} modification(s) récente(s) du système", transactions=summary)


def _transaction_undo(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import ipc
    try:
        if params.get("since"):
            result = ipc.call("tx.undo_since", {"since": params["since"]}, timeout=3600)
        elif not params.get("id"):
            return fail("préciser l'identifiant de la modification (transactions_list) ou une date (since)")
        else:
            result = ipc.call("tx.undo", {"id": params["id"]}, timeout=3600)
    except ipc.RPCError as exc:
        return fail(f"annulation impossible : {exc}")
    return ok(result["message"], **{key: value for key, value in result.items() if key != "message"}) \
        if result.get("success") else fail(result["message"])


def _settings_get(_params: dict, _verbose: bool) -> dict:
    return ok("Réglages de Djerba (/etc/djerba/config.toml)", **_config().public())


def _settings_set(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.config import SCHEMA, ConfigError
    if SCHEMA.get(params["key"], {}).get("secret"):
        return fail("une clé d'API se saisit dans Réglages → Cerveau, jamais dans la conversation")
    try:
        value = _config().set(params["key"], params["value"])
    except ConfigError as exc:
        return fail(str(exc))
    return ok(f"{params['key']} = {value}")


def _settings_history(_params: dict, _verbose: bool) -> dict:
    rows = _memory().settings_history(30)
    return ok(f"{len(rows)} modification(s) récente(s)", history=[
        {"key": row["key"], "value": row["value"], "time": row["created"]} for row in rows])


def _audit_tail(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import ipc
    try:
        entries = ipc.call("audit.tail", {"lines": params["lines"]}, timeout=30)["entries"]
    except ipc.RPCError as exc:
        return fail(str(exc))
    return ok(f"{len(entries)} entrée(s) du journal d'audit", entries=entries)


def _venv_install(params: dict, _verbose: bool) -> dict:
    packages = [item for item in re.split(r"[\s,]+", params["packages"]) if item]
    return _ipc("sys.venv_install", {"packages": packages})


def _capability_create(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.extend import create_capability
    result = create_capability(params["name"], params["instruction"])
    return (ok if result.get("success") else fail)(result.get("message", ""), **{
        key: value for key, value in result.items() if key not in ("success", "message")})


def _ghost_run(params: dict, _verbose: bool) -> dict:
    import json
    from djerba_ai.core import ghost
    from djerba_ai.tools import registry
    tool = registry().get(params["tool"])
    if tool is None:
        return fail(f"capacité inconnue : {params['tool']}")
    if tool.requires_root or tool.dangerous:
        return fail("une tâche de fond ne peut ni obtenir les droits root ni agir sans confirmation")
    try:
        arguments = json.loads(params.get("params") or "{}")
        tool.validate(arguments)
        unit = ghost.run_tool(tool.name, arguments)
    except (ValueError, ghost.GhostError) as exc:
        return fail(str(exc))
    return ok(f"« {tool.name} » tourne en tâche de fond ({unit}).", unit=unit)


def _ghost_list(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import ghost
    found = ghost.tasks()
    return ok(f"{len(found)} tâche(s) de fond", tasks=found)


def _ghost_logs(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import ghost
    try:
        return ok(f"Journal de {params['unit']}", output=ghost.logs(params["unit"]))
    except ghost.GhostError as exc:
        return fail(str(exc))


def _ghost_stop(params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import ghost
    try:
        ghost.stop(params["unit"])
    except ghost.GhostError as exc:
        return fail(str(exc))
    return ok(f"{params['unit']} arrêtée.")


def _context_now(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core import contextbus
    snapshot = contextbus.read()
    if not snapshot:
        return fail("bus de contexte inactif (démon djerba-ai.service arrêté ?)")
    return ok(contextbus.context_block(), snapshot=snapshot)


def _engine_handshake(_params: dict, _verbose: bool) -> dict:
    from djerba_ai.core.engine import CognitiveEngine
    capabilities = CognitiveEngine(_config()).capabilities(force=True)
    return (ok if capabilities.reachable else fail)(
        f"{capabilities.provider} « {capabilities.model} » : {capabilities.latency_ms} ms, contexte "
        f"{capabilities.context_window}, profil {capabilities.profile}", **capabilities.model_dump())


def _audit_verify(_params: dict, _verbose: bool) -> dict:
    result = _ipc("audit.verify", {})
    return result


def _lesson_record(params: dict, _verbose: bool) -> dict:
    lesson_id = _memory().learn(params["kind"], params["content"], source="agent")
    return ok(f"Leçon n°{lesson_id} retenue ({params['kind']}).", id=lesson_id)


def _lessons_list(params: dict, _verbose: bool) -> dict:
    rows = _memory().lessons(params.get("kind") or None, limit=params["limit"])
    return ok(f"{len(rows)} leçon(s)", lessons=rows)


def _dlp_vault(_params: dict, _verbose: bool) -> dict:
    entries = _memory().vault_entries()
    return ok(f"{len(entries)} secret(s) intercepté(s) et chiffré(s) dans le coffre (valeurs jamais affichées).",
              entries=entries)


UNIT_PATTERN = r"^djerba-ghost-[a-z0-9-]{1,60}\.service$"

MODEL = r"^[A-Za-z0-9._:/-]{2,100}$"

TOOLS = [
    function_tool("tools_search", C, "Cherche, parmi toutes les capacités de Djerba, celles qui conviennent à une tâche.",
                  _tools_search,
                  parameters={"query": {"type": "string", "description": "tâche à accomplir", "pattern": r"^[^\x00]{2,200}$"},
                              "limit": {"type": "integer", "description": "nombre", "default": 8,
                                        "minimum": 1, "maximum": 20}},
                  required=("query",), keywords=("outil", "capacité", "comment faire")),
    function_tool("tools_categories", C, "Nombre de capacités par catégorie.", _tools_categories,
                  keywords=("capacités", "que sais-tu faire", "aide")),
    function_tool("memory_remember", C, "Retient une information durable sur l'utilisateur ou sa machine.", _remember,
                  parameters={"subject": {"type": "string", "description": "sujet", "pattern": r"^[^\x00]{1,80}$"},
                              "content": {"type": "string", "description": "information", "pattern": r"^[^\x00]{1,1000}$"}},
                  required=("subject", "content"), keywords=("souviens-toi", "retiens", "mémorise", "préférence")),
    function_tool("memory_recall", C, "Retrouve les informations mémorisées.", _recall,
                  parameters={"query": {"type": "string", "description": "filtre", "pattern": r"^[^\x00]{0,80}$",
                                        "default": ""}},
                  keywords=("te souviens-tu", "mémoire", "rappelle")),
    function_tool("memory_forget", C, "Oublie une information mémorisée.", _forget,
                  parameters={"id": {"type": "integer", "description": "identifiant", "minimum": 1}},
                  required=("id",), dangerous=True, keywords=("oublie",)),
    function_tool("conversations_list", C, "Discussions précédentes avec Djerba.", _conversations,
                  keywords=("historique", "discussions")),
    function_tool("feedback", C, "Note l'utilité d'une action passée (améliore les choix de l'agent).", _feedback,
                  parameters={"run_id": {"type": "integer", "description": "identifiant d'exécution", "minimum": 1},
                              "useful": {"type": "boolean", "description": "utile ?"}},
                  required=("run_id", "useful"), keywords=("avis", "utile", "pas utile")),
    function_tool("learning_stats", C, "Ce que Djerba a appris : actions réussies, outils favoris, souvenirs.",
                  _learning, keywords=("apprentissage", "progrès", "statistiques")),
    function_tool("health_report", C, "Bilan de santé complet du système avec score et actions conseillées.",
                  lambda _p, _v: _health(),
                  keywords=("bilan", "santé", "diagnostic", "problème", "vérifier", "état du pc", "lent")),
    function_tool("incidents_list", C, "Incidents détectés par l'auto-réparation.", _incidents,
                  parameters={"status": {"type": "string", "description": "filtre",
                                         "enum": ["nouveau", "correctif prêt", "correctif rejeté", "corrigé"]}},
                  keywords=("incidents", "erreurs détectées", "auto-réparation", "crash")),
    function_tool("incident_analyze", C, "Explique la cause probable d'un incident et la solution.", _incident_analyze,
                  parameters={"id": {"type": "integer", "description": "incident", "minimum": 1}},
                  required=("id",), keywords=("analyser", "pourquoi", "cause", "plantage")),
    function_tool("patch_propose", C, "Génère un correctif pour l'outil Djerba en cause et le vérifie dans le bac à sable.",
                  _patch_propose,
                  parameters={"id": {"type": "integer", "description": "incident", "minimum": 1}},
                  required=("id",), keywords=("corriger", "réparer", "correctif", "patch")),
    function_tool("patch_list", C, "Correctifs proposés et déployés.", lambda _p, _v: _ipc("sys.list_patches", {}),
                  keywords=("correctifs", "patchs")),
    function_tool("patch_deploy", C, "Déploie un correctif vérifié (mot de passe administrateur demandé).",
                  _patch_deploy,
                  parameters={"patch_id": {"type": "string", "description": "identifiant", "pattern": r"^p\d{14}$"}},
                  required=("patch_id",), dangerous=True, keywords=("déployer", "appliquer correctif")),
    function_tool("patch_rollback", C, "Annule un correctif déployé et rétablit la version du paquet.",
                  lambda params, _v: _ipc("sys.rollback_patch", {"name": params["name"]}),
                  parameters={"name": {"type": "string", "description": "script", "pattern": r"^djerba-[a-z0-9-]{1,40}$"}},
                  required=("name",), dangerous=True, keywords=("annuler correctif", "rollback", "revenir")),
    function_tool("web_search", C, "Recherche sur Internet (DuckDuckGo) : informations récentes, guides, solutions.",
                  _web_search,
                  parameters={"query": {"type": "string", "description": "recherche", "pattern": r"^[^\x00]{2,200}$"},
                              "count": {"type": "integer", "description": "résultats", "default": 6,
                                        "minimum": 1, "maximum": 10}},
                  required=("query",), keywords=("internet", "chercher", "google", "web", "actualité", "tuto")),
    function_tool("web_fetch", C, "Lit le texte d'une page web.", _web_fetch,
                  parameters={"url": {"type": "string", "description": "adresse",
                                      "pattern": r"^https?://[^\s\x00]{3,2000}$"}},
                  required=("url",), keywords=("lire page", "site", "url")),
    function_tool("engine_status", C, "État du cerveau de Djerba (local, Tailscale ou en ligne) : santé, latence, repli.",
                  _engine_status, keywords=("moteur", "cerveau", "ollama", "modèle", "ia", "tailscale", "latence")),
    function_tool("engines_list", C, "Moteurs d'IA configurés (Ollama local ou distant, Jarvis, OpenAI, Gemini, "
                  "Anthropic…), moteur actif et capacité mesurée.", _engines_list,
                  keywords=("moteurs", "ia disponibles", "jarvis", "cerveaux", "engines")),
    function_tool("engine_use", C, "Bascule à chaud sur un autre moteur d'IA (la conversation est conservée).",
                  _engine_use,
                  parameters={"id": {"type": "string", "description": "identifiant du moteur (engines_list)",
                                     "pattern": r"^[a-z0-9][a-z0-9-]{1,31}$"}},
                  required=("id",), keywords=("changer de moteur", "utiliser jarvis", "basculer", "engine")),
    function_tool("brain_switch", C, "Change de cerveau : Ollama local, machine distante via Tailscale ou API en ligne.",
                  _brain_switch,
                  parameters={"provider": {"type": "string", "description": "local, tailscale ou cloud",
                                           "enum": ["local", "tailscale", "cloud"]},
                              "model": {"type": "string", "description": "modèle (facultatif)", "pattern": MODEL}},
                  required=("provider",), dangerous=True,
                  keywords=("changer de cerveau", "mac mini", "tailscale", "gemini", "openai", "cloud", "local")),
    function_tool("model_list", C, "Modèles disponibles sur le cerveau actif.", _model_list,
                  keywords=("modèles", "llm")),
    function_tool("model_recommend", C, "Modèles conseillés pour la mémoire de cette machine.", _model_recommend,
                  keywords=("quel modèle", "conseil modèle", "ram")),
    function_tool("model_pull", C, "Télécharge ou met à jour un modèle Ollama local (ex. qwen2.5:7b).", _model_pull,
                  parameters={"model": {"type": "string", "description": "modèle", "pattern": MODEL}},
                  required=("model",), dangerous=True, keywords=("télécharger modèle", "pull")),
    function_tool("model_set", C, "Choisit le modèle d'un cerveau (actif par défaut).", _model_set,
                  parameters={"model": {"type": "string", "description": "modèle", "pattern": MODEL},
                              "provider": {"type": "string", "description": "local, tailscale ou cloud",
                                           "enum": ["local", "tailscale", "cloud"]}},
                  required=("model",), keywords=("changer de modèle", "utiliser modèle")),
    function_tool("transactions_list", C, "Dernières modifications du système (journal de transactions : moteur "
                  "d'IA à l'origine, fichiers touchés, état, annulable ou non).", _transactions_list,
                  parameters={"limit": {"type": "integer", "description": "nombre", "minimum": 1, "maximum": 200}},
                  keywords=("historique des modifications", "qu'as-tu changé", "journal", "undo", "transactions")),
    function_tool("transaction_undo", C, "Annule une modification du système (par identifiant) ou toutes celles "
                  "depuis une date (restauration depuis la sauvegarde prise avant l'action).", _transaction_undo,
                  parameters={"id": {"type": "string", "description": "identifiant (t…)",
                                     "pattern": r"^t\d{14}[0-9a-f]{4}$"},
                              "since": {"type": "string", "description": "date AAAA-MM-JJ[THH:MM]",
                                        "pattern": r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?$"}},
                  keywords=("annuler", "undo", "revenir en arrière", "restaurer", "défaire")),
    function_tool("settings_get", C, "Réglages de Djerba (/etc/djerba/config.toml, sans les secrets).", _settings_get,
                  keywords=("réglages", "paramètres", "configuration de djerba")),
    function_tool("settings_set", C, "Modifie un réglage de Djerba (clé pointée, ex. agent.web_search).", _settings_set,
                  parameters={"key": {"type": "string", "description": "réglage",
                                      "pattern": r"^[a-z_]+(\.[a-z_]+){1,2}$"},
                              "value": {"type": "string", "description": "valeur", "pattern": r"^[^\x00]{0,300}$"}},
                  required=("key", "value"), dangerous=True,
                  keywords=("autonomie", "désactiver recherche web", "réglage", "raccourci")),
    function_tool("settings_history", C, "Historique des modifications de réglages.", _settings_history,
                  keywords=("historique réglages", "qui a changé")),
    function_tool("audit_log", C, "Dernières actions critiques du journal d'audit (/var/log/djerba/audit.log).",
                  _audit_tail,
                  parameters={"lines": {"type": "integer", "description": "nombre", "default": 50,
                                        "minimum": 1, "maximum": 500}},
                  keywords=("audit", "journal", "historique des actions", "qui a fait")),
    function_tool("lesson_record", C, "Retient une leçon apprise, une préférence ou un retour d'expérience "
                  "(après un échec résolu, une préférence exprimée…).", _lesson_record,
                  parameters={"kind": {"type": "string", "enum": ["leçon", "préférence", "retour"], "default": "leçon",
                                       "description": "type"},
                              "content": {"type": "string", "description": "la leçon, en une phrase",
                                          "pattern": r"^[^\x00]{5,600}$"}},
                  required=("content",), keywords=("retiens", "leçon", "préférence", "à l'avenir")),
    function_tool("lessons_list", C, "Leçons apprises, préférences et retours d'expérience mémorisés.", _lessons_list,
                  parameters={"kind": {"type": "string", "enum": ["", "leçon", "préférence", "retour"], "default": "",
                                       "description": "type (vide : tous)"},
                              "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20,
                                        "description": "nombre"}},
                  keywords=("leçons", "ce que tu as appris", "préférences")),
    function_tool("dlp_vault", C, "Secrets interceptés par la protection DLP (métadonnées seulement).", _dlp_vault,
                  keywords=("dlp", "secrets", "fuite", "coffre")),
    function_tool("ghost_run", C, "Lance une capacité en tâche de fond isolée (unité systemd djerba-ghost-*).",
                  _ghost_run,
                  parameters={"tool": {"type": "string", "description": "capacité (sans droits root)",
                                       "pattern": r"^[a-z0-9_.]{2,80}$"},
                              "params": {"type": "string", "description": "paramètres JSON", "default": "{}",
                                         "pattern": r"^[^\x00]{0,4000}$"}},
                  required=("tool",), keywords=("en arrière-plan", "tâche de fond", "background", "ghost")),
    function_tool("ghost_list", C, "Tâches de fond de Djerba (en cours et terminées).", _ghost_list,
                  keywords=("tâches de fond", "arrière-plan")),
    function_tool("ghost_logs", C, "Journal d'une tâche de fond.", _ghost_logs,
                  parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT_PATTERN}},
                  required=("unit",)),
    function_tool("ghost_stop", C, "Arrête une tâche de fond.", _ghost_stop,
                  parameters={"unit": {"type": "string", "description": "unité", "pattern": UNIT_PATTERN}},
                  required=("unit",), keywords=("arrêter la tâche",)),
    function_tool("context_now", C, "État vivant de l'OS (bus de contexte horodaté : CPU, mémoire, E/S, fenêtre "
                  "active, erreurs récentes).", _context_now,
                  keywords=("que se passe-t-il", "état actuel", "fenêtre active", "charge", "contexte")),
    function_tool("engine_handshake", C, "Mesure le moteur d'IA actif (latence, contexte, capacités, profil).",
                  _engine_handshake, keywords=("latence", "capacités du modèle", "handshake")),
    function_tool("audit_verify", C, "Vérifie l'intégrité du journal d'audit (chaîne de hachages).", _audit_verify,
                  keywords=("intégrité", "journal d'audit falsifié", "vérifier l'audit")),
    function_tool("venv_install", C, "Installe des paquets Python dans le venv interne de Djerba (jamais de pip global).",
                  _venv_install,
                  parameters={"packages": {"type": "string", "description": "paquets séparés par des virgules",
                                           "pattern": r"^[A-Za-z0-9._,=<>!~ \[\]-]{2,400}$"}},
                  required=("packages",), dangerous=True, keywords=("pip", "module python", "venv", "bibliothèque")),
    function_tool("capability_create", C,
                  "Crée une nouvelle capacité quand aucune ne convient : code écrit, testé dans le bac à sable "
                  "(3 essais, 15 s max) puis intégré à chaud.", _capability_create,
                  parameters={"name": {"type": "string", "description": "nom (snake_case)",
                                       "pattern": r"^[a-z0-9_]{3,48}$"},
                              "instruction": {"type": "string", "description": "ce que doit faire la capacité",
                                              "pattern": r"^[^\x00]{5,1500}$"}},
                  required=("name", "instruction"),
                  keywords=("nouvelle capacité", "je ne sais pas faire", "créer un outil", "étendre")),
    function_tool("ollama_install", C, "Installe le moteur d'IA local Ollama (archive officielle vérifiée).",
                  lambda _p, _v: _ipc("sys.install_ollama", {}), dangerous=True,
                  keywords=("installer ollama", "moteur ia", "ia locale")),
    command_tool("self_update", C, "Met à jour Djerba OS lui-même (paquets djerba-tools et djerba-branding).",
                 ["apt-get", "install", "-y", "--only-upgrade", "-o", "DPkg::Lock::Timeout=300",
                  "djerba-tools", "djerba-branding"], requires_root=True, dangerous=True, timeout=1800,
                 env={"DEBIAN_FRONTEND": "noninteractive"}, keywords=("mettre à jour djerba", "self update"),
                 done="Djerba OS est à jour."),
    command_tool("daemon_status", C, "État du service de surveillance et de maintenance de Djerba.",
                 ["systemctl", "--user", "status", "--no-pager", "-n", "10", "djerba-ai.service"],
                 success_codes=(0, 3), keywords=("surveillance", "service djerba"), done="Service djerba-ai."),
]

