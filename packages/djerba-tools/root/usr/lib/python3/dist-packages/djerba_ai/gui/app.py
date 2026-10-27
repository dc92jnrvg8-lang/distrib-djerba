# SPDX-License-Identifier: GPL-3.0-or-later
"""Application graphique de Djerba (PyQt6 + QML, orbe en Qt Quick 3D).

L'agent tourne dans un fil d'exécution séparé ; l'interface reçoit le flux
(texte, appels d'outils, demandes de confirmation) par des signaux Qt. Les
confirmations bloquent l'agent jusqu'à la réponse de l'utilisateur.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QSize, Qt, QThread, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QIcon, QPixmap
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtQuick import QQuickImageProvider

from djerba_ai import __version__
from djerba_ai.core import context, health, ipc
from djerba_ai.core.agent import Agent
from djerba_ai.core.config import RECOMMENDED_MODELS, Config, memory_gb
from djerba_ai.core.llm import LLMError, OllamaClient, client_from_config
from djerba_ai.core.memory import Memory
from djerba_ai.tools import CATEGORIES, CATEGORY_LABELS, registry

QML_DIR = Path(os.environ.get("DJERBA_AI_QML", Path(__file__).with_name("qml")))
PAGES = ("chat", "system", "selfheal", "studio", "tools", "models")


class IconProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Pixmap)

    def requestPixmap(self, icon_id: str, requested: QSize) -> tuple[QPixmap, QSize]:
        size = requested if requested.isValid() and not requested.isEmpty() else QSize(64, 64)
        pixmap = QIcon.fromTheme(icon_id, QIcon.fromTheme("djerba-os")).pixmap(size)
        if pixmap.isNull():
            pixmap = QPixmap(size)
            pixmap.fill(Qt.GlobalColor.transparent)
        return pixmap, pixmap.size()


class Task(QThread):
    """Exécute une fonction hors du fil de l'interface."""

    done = pyqtSignal(object)

    def __init__(self, function, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.function = function

    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:  # noqa: BLE001 - toute erreur est rendue à l'interface
            result = {"success": False, "message": str(exc)}
        self.done.emit(result)


class Backend(QObject):
    # Discussion
    messageAdded = pyqtSignal(str, str, arguments=["role", "text"])
    tokenAppended = pyqtSignal(str, arguments=["text"])
    toolStarted = pyqtSignal(str, str, bool, arguments=["tool", "label", "root"])
    toolFinished = pyqtSignal(str, bool, str, int, arguments=["tool", "success", "message", "runId"])
    confirmRequested = pyqtSignal(int, str, str, str, bool, arguments=["requestId", "tool", "label", "params", "root"])
    suggestions = pyqtSignal("QVariantList", arguments=["items"])
    busyChanged = pyqtSignal()
    conversationsChanged = pyqtSignal()
    # État
    engineChanged = pyqtSignal()
    healthChanged = pyqtSignal()
    incidentsChanged = pyqtSignal()
    appsChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    progress = pyqtSignal(str, int, arguments=["message", "percent"])
    notify = pyqtSignal(str, arguments=["message"])

    def __init__(self, start_page: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = Config()
        self.memory = Memory()
        self.registry = registry(include_plugins=True, approved=self.config.get("plugins_approved"))
        self.agent = Agent(self.config, self.registry, self.memory)
        self._start_page = start_page if start_page in PAGES else "chat"
        self._conversation = 0
        self._busy = False
        self._engine = {"available": False, "installed": [], "ollama_present": False}
        self._health: dict = {"score": 0, "items": []}
        self._incidents: list = []
        self._patches: dict = {"deployed": {}, "proposals": []}
        self._apps: list = []
        self._tasks: set[Task] = set()
        self._confirm_events: dict[int, tuple[threading.Event, list]] = {}
        self._next_request = 1
        QTimer.singleShot(0, self.refreshEngine)
        QTimer.singleShot(200, self.refreshHealth)
        QTimer.singleShot(400, self.refreshIncidents)
        QTimer.singleShot(600, self.refreshApps)

    # ---------------------------------------------------------------- Utilitaires
    def _background(self, function, callback=None) -> None:
        task = Task(function, self)
        self._tasks.add(task)

        def finished(result) -> None:
            self._tasks.discard(task)
            if callback:
                callback(result)

        task.done.connect(finished)
        task.start()

    # ---------------------------------------------------------------- Propriétés
    @pyqtProperty(str, constant=True)
    def version(self) -> str:
        return __version__

    @pyqtProperty(str, constant=True)
    def startPage(self) -> str:
        return self._start_page

    @pyqtProperty(bool, constant=True)
    def softwareRendering(self) -> bool:
        return os.environ.get("QT_QUICK_BACKEND") == "software"

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @pyqtProperty(int, constant=True)
    def toolCount(self) -> int:
        return len(self.registry)

    @pyqtProperty("QVariantList", constant=True)
    def categories(self) -> list:
        return [{"id": c, "label": CATEGORY_LABELS[c], "count": len(self.registry.by_category(c))} for c in CATEGORIES]

    @pyqtProperty(bool, notify=engineChanged)
    def engineAvailable(self) -> bool:
        return bool(self._engine["available"])

    @pyqtProperty(bool, notify=engineChanged)
    def ollamaPresent(self) -> bool:
        return bool(self._engine["ollama_present"])

    @pyqtProperty(str, notify=settingsChanged)
    def model(self) -> str:
        return str(self.config.get("model"))

    @pyqtProperty(str, notify=settingsChanged)
    def provider(self) -> str:
        return str(self.config.get("provider"))

    @pyqtProperty("QVariantList", notify=engineChanged)
    def installedModels(self) -> list:
        return self._engine["installed"]

    @pyqtProperty("QVariantList", constant=True)
    def recommendedModels(self) -> list:
        ram = memory_gb()
        return [dict(model, fits=model["min_ram_gb"] <= ram) for model in RECOMMENDED_MODELS]

    @pyqtProperty(int, constant=True)
    def memoryGb(self) -> int:
        return round(memory_gb())

    @pyqtProperty("QVariantMap", notify=settingsChanged)
    def settings(self) -> dict:
        return self.config.public()

    @pyqtProperty("QVariantList", notify=conversationsChanged)
    def conversations(self) -> list:
        return [{"id": row["id"], "title": row["title"]} for row in self.memory.conversations(30)]

    @pyqtProperty(int, notify=conversationsChanged)
    def conversation(self) -> int:
        return self._conversation

    @pyqtProperty(int, notify=healthChanged)
    def healthScore(self) -> int:
        return int(self._health.get("score", 0))

    @pyqtProperty("QVariantList", notify=healthChanged)
    def healthItems(self) -> list:
        return self._health.get("items", [])

    @pyqtProperty(str, notify=healthChanged)
    def systemSummary(self) -> str:
        return self._health.get("context", "")

    @pyqtProperty("QVariantList", notify=incidentsChanged)
    def incidents(self) -> list:
        return self._incidents

    @pyqtProperty("QVariantList", notify=incidentsChanged)
    def patches(self) -> list:
        deployed = self._patches.get("deployed", {})
        return [{"name": name, **info} for name, info in deployed.items()]

    @pyqtProperty("QVariantList", notify=appsChanged)
    def generatedApps(self) -> list:
        return self._apps

    @pyqtProperty("QVariantMap", constant=True)
    def learning(self) -> dict:
        return self.memory.stats()

    # ---------------------------------------------------------------- Discussion
    @pyqtSlot()
    def newConversation(self) -> None:
        self._conversation = 0
        self.conversationsChanged.emit()

    @pyqtSlot(int)
    def openConversation(self, conversation_id: int) -> None:
        self._conversation = conversation_id
        self.conversationsChanged.emit()
        for row in self.memory.messages(conversation_id, limit=60):
            if row["role"] in ("user", "assistant"):
                self.messageAdded.emit(row["role"], row["content"])

    @pyqtSlot(int)
    def deleteConversation(self, conversation_id: int) -> None:
        self.memory.delete_conversation(conversation_id)
        if self._conversation == conversation_id:
            self._conversation = 0
        self.conversationsChanged.emit()

    @pyqtSlot(str)
    def send(self, text: str) -> None:
        text = text.strip()
        if not text or self._busy:
            return
        if not self._conversation:
            self._conversation = self.memory.new_conversation(text[:60])
            self.conversationsChanged.emit()
        self._busy = True
        self.busyChanged.emit()
        self.messageAdded.emit("user", text)
        conversation = self._conversation

        def on_event(kind: str, payload: dict) -> None:
            if kind == "token":
                self.tokenAppended.emit(payload["text"])
            elif kind == "tool_start":
                self.toolStarted.emit(payload["tool"], payload["label"], payload["root"])
            elif kind == "tool_result":
                result = payload["result"]
                self.toolFinished.emit(payload["tool"], bool(result.get("success")), str(result.get("message", "")),
                                       int(result.get("data", {}).get("run_id", 0)))
            elif kind == "suggestions":
                self.suggestions.emit(payload["items"])
            elif kind == "final":
                self.messageAdded.emit("final", payload["text"])

        def confirm(tool: str, params: dict, description: dict) -> bool:
            event, answer = threading.Event(), [False]
            request_id = self._next_request
            self._next_request += 1
            self._confirm_events[request_id] = (event, answer)
            self.confirmRequested.emit(request_id, tool, description.get("description", ""),
                                       json.dumps(params, ensure_ascii=False), bool(description.get("requires_root")))
            event.wait()
            self._confirm_events.pop(request_id, None)
            return answer[0]

        def work():
            return self.agent.run(text, conversation, on_event, confirm)

        def finished(result) -> None:
            if isinstance(result, dict) and result.get("success") is False:
                self.messageAdded.emit("final", f"Une erreur interne m'a interrompu : {result.get('message')}")
            self._busy = False
            self.busyChanged.emit()
            self.conversationsChanged.emit()
            QTimer.singleShot(500, self.refreshHealth)

        self._background(work, finished)

    @pyqtSlot()
    def shutdown(self) -> None:
        """Fermeture : refuse les confirmations en attente et attend les tâches."""
        for event, answer in list(self._confirm_events.values()):
            answer[0] = False
            event.set()
        for task in list(self._tasks):
            task.wait(5000)

    @pyqtSlot(int, bool)
    def answerConfirmation(self, request_id: int, accepted: bool) -> None:
        entry = self._confirm_events.get(request_id)
        if entry:
            entry[1][0] = accepted
            entry[0].set()

    @pyqtSlot(int, bool)
    def feedback(self, run_id: int, useful: bool) -> None:
        if run_id:
            self.memory.feedback(run_id, 1 if useful else -1)
            self.notify.emit("Merci, Djerba en tiendra compte.")

    @pyqtSlot(str)
    def runTool(self, name: str) -> None:
        tool = self.registry.get(name)
        if tool is None:
            return
        if tool.required:
            self.send(f"Utilise l'outil {name} ({tool.description})")
            return
        self.toolStarted.emit(name, tool.description, tool.requires_root)
        self._background(lambda: self.agent.executor.execute(name, {}, request=f"[manuel] {name}"),
                         lambda result: self.toolFinished.emit(name, bool(result.get("success")),
                                                               str(result.get("message", "")),
                                                               int(result.get("data", {}).get("run_id", 0))))

    @pyqtSlot(str, result="QVariantList")
    def searchTools(self, query: str) -> list:
        tools = self.registry.search(query, limit=40) if query.strip() else \
            sorted(self.registry.tools.values(), key=lambda t: (t.category, t.name))
        return [{"name": t.name, "description": t.description, "category": CATEGORY_LABELS.get(t.category, t.category),
                 "root": t.requires_root, "dangerous": t.dangerous, "ready": not t.required,
                 "available": not t.missing_binaries()} for t in tools]

    # ---------------------------------------------------------------- Moteur et modèles
    @pyqtSlot()
    def refreshEngine(self) -> None:
        def work():
            client = client_from_config(self.config)
            available = client.available()
            installed = []
            if available:
                try:
                    installed = client.models()
                except LLMError:
                    installed = []
            return {"available": available, "installed": installed,
                    "ollama_present": Path("/usr/local/bin/ollama").exists() or Path("/usr/bin/ollama").exists()}

        def done(result) -> None:
            self._engine = result
            self.engineChanged.emit()

        self._background(work, done)

    @pyqtSlot(str)
    def setModel(self, name: str) -> None:
        self.config.set("model", name)
        self.settingsChanged.emit()
        self.notify.emit(f"Djerba utilise maintenant {name}.")

    @pyqtSlot(str)
    def pullModel(self, name: str) -> None:
        client = OllamaClient(self.config.get("ollama_endpoint"), name)

        def work():
            client.pull(name, lambda status, percent: self.progress.emit(f"{name} : {status}", percent))
            return {"success": True}

        def done(result) -> None:
            if result.get("success"):
                self.setModel(name)
                self.progress.emit("", 100)
            else:
                self.notify.emit(f"Téléchargement impossible : {result.get('message')}")
            self.refreshEngine()

        self.progress.emit(f"{name} : préparation", 0)
        self._background(work, done)

    @pyqtSlot()
    def installOllama(self) -> None:
        self.progress.emit("Installation d'Ollama (téléchargement de l'archive officielle)…", -1)

        def done(result) -> None:
            self.progress.emit("", 100)
            self.notify.emit(result.get("message", "") if isinstance(result, dict) else str(result))
            self.refreshEngine()

        def work():
            try:
                return ipc.call("sys.install_ollama", {})
            except ipc.RPCError as exc:
                return {"success": False, "message": str(exc)}

        self._background(work, done)

    @pyqtSlot(str, "QVariant")
    def setSetting(self, key: str, value) -> None:
        try:
            if key == "maintenance_hour":
                value = int(value)
            self.config.set(key, value)
        except (KeyError, ValueError):
            return
        self.settingsChanged.emit()
        if key in ("provider", "ollama_endpoint", "openai_endpoint", "openai_api_key", "model"):
            self.refreshEngine()

    # ---------------------------------------------------------------- Système et auto-réparation
    @pyqtSlot()
    def refreshHealth(self) -> None:
        def work():
            result = health.report()
            result["context"], _data = context.snapshot(force=True)
            return result

        def done(result) -> None:
            self._health = result
            self.healthChanged.emit()

        self._background(work, done)

    @pyqtSlot()
    def refreshIncidents(self) -> None:
        def work():
            incidents = [{key: row[key] for key in ("id", "source", "target", "message", "status", "occurrences")}
                         for row in self.memory.incidents()]
            try:
                patches = ipc.call("sys.list_patches", {}, timeout=10)
            except ipc.RPCError:
                patches = {"deployed": {}, "proposals": []}
            return {"incidents": incidents, "patches": patches}

        def done(result) -> None:
            self._incidents = result["incidents"]
            self._patches = result["patches"]
            self.incidentsChanged.emit()

        self._background(work, done)

    def _agent_tool(self, name: str, params: dict, refresh=None) -> None:
        self.progress.emit(self.registry.get(name).description, -1)

        def done(result) -> None:
            self.progress.emit("", 100)
            self.notify.emit(str(result.get("message", "")))
            if refresh:
                refresh()

        self._background(lambda: self.agent.executor.execute(name, params, request=f"[interface] {name}"), done)

    @pyqtSlot(int)
    def analyzeIncident(self, incident_id: int) -> None:
        self.send(f"Analyse l'incident n°{incident_id} (outil incident_analyze) et explique-moi la solution.")

    @pyqtSlot(int)
    def proposePatch(self, incident_id: int) -> None:
        self._agent_tool("patch_propose", {"id": incident_id}, self.refreshIncidents)

    @pyqtSlot(str)
    def deployPatch(self, patch_id: str) -> None:
        self._agent_tool("patch_deploy", {"patch_id": patch_id}, self.refreshIncidents)

    @pyqtSlot(str)
    def rollbackPatch(self, name: str) -> None:
        self._agent_tool("patch_rollback", {"name": name}, self.refreshIncidents)

    @pyqtSlot(int, result="QVariantMap")
    def incidentPatch(self, incident_id: int) -> dict:
        incident = self.memory.incident(incident_id)
        try:
            return json.loads(incident["patch"]) if incident and incident.get("patch") else {}
        except ValueError:
            return {}

    # ---------------------------------------------------------------- Studio
    @pyqtSlot()
    def refreshApps(self) -> None:
        def done(result) -> None:
            self._apps = result.get("data", {}).get("apps", [])
            self.appsChanged.emit()

        self._background(lambda: self.registry.get("apps_generated_list").run({}), done)

    @pyqtSlot(str, str)
    def generateApp(self, instruction: str, kind: str) -> None:
        if instruction.strip():
            self._agent_tool("app_generator", {"instruction": instruction, "kind": kind or "auto"}, self.refreshApps)

    @pyqtSlot(str)
    def runApp(self, slug: str) -> None:
        self.registry.get("app_run").run({"slug": slug, "live": False})

    @pyqtSlot(str)
    def runAppLive(self, slug: str) -> None:
        self.registry.get("app_run").run({"slug": slug, "live": True})

    @pyqtSlot(str)
    def openAppFolder(self, slug: str) -> None:
        self.registry.get("app_open_folder").run({"slug": slug})

    @pyqtSlot(str, str)
    def iterateApp(self, slug: str, instruction: str) -> None:
        if instruction.strip():
            self._agent_tool("app_iterate", {"slug": slug, "instruction": instruction}, self.refreshApps)

    @pyqtSlot(str)
    def deleteApp(self, slug: str) -> None:
        self._agent_tool("app_delete", {"slug": slug}, self.refreshApps)


def main(argv: list[str]) -> int:
    page = argv[argv.index("--page") + 1] if "--page" in argv and argv.index("--page") + 1 < len(argv) else "chat"
    QGuiApplication.setApplicationName("djerba-ai")
    QGuiApplication.setApplicationDisplayName("Djerba")
    QGuiApplication.setDesktopFileName("djerba-ai")
    app = QGuiApplication(argv)
    app.setWindowIcon(QIcon.fromTheme("djerba-ai", QIcon.fromTheme("djerba-os")))
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        QIcon.setThemeName("breeze-dark")
    backend = Backend(page, app)
    app.aboutToQuit.connect(backend.shutdown)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", IconProvider())
    engine.rootContext().setContextProperty("backend", backend)
    main_qml = QML_DIR / "main.qml"
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    if not engine.rootObjects():
        print(f"djerba-ai : impossible de charger {main_qml}", file=sys.stderr)
        return 1
    if os.environ.get("DJERBA_AI_DEV") == "1":
        # Rechargement à chaud de l'interface de Djerba pendant son développement.
        from PyQt6.QtCore import QFileSystemWatcher
        watcher = QFileSystemWatcher([str(path) for path in QML_DIR.glob("*.qml")], app)

        def reload(_path: str) -> None:
            for root in engine.rootObjects():
                root.deleteLater()
            engine.clearComponentCache()
            engine.load(QUrl.fromLocalFile(str(main_qml)))
            watcher.addPaths([str(path) for path in QML_DIR.glob("*.qml")])

        watcher.fileChanged.connect(reload)
    return app.exec()
