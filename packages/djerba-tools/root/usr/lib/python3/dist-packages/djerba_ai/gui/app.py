# SPDX-License-Identifier: GPL-3.0-or-later
"""Application graphique de Djerba (PyQt6 + QML, orbe en Qt Quick 3D).

Deux modes : la barre flottante « Spotlight » (Meta+Espace, presse-papiers
sur Meta+Maj+C) et le mode complet (discussion, Studio, réglages). L'
application reste résidente dans la zone de notification (icône d'état,
menu contextuel, notifications) et n'existe qu'en un exemplaire.

L'agent tourne dans un fil d'exécution séparé ; l'interface reçoit le flux
(texte, appels d'outils, demandes de confirmation) par des signaux Qt. Les
confirmations bloquent l'agent jusqu'à la réponse de l'utilisateur.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import (QFileSystemWatcher, QKeyCombination, QObject, QSize, Qt, QThread, QTimer, QUrl,
                          pyqtProperty, pyqtSignal, pyqtSlot)
from PyQt6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtQml import QQmlApplicationEngine
from PyQt6.QtQuick import QQuickImageProvider, QQuickWindow
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from djerba_ai import __version__
from djerba_ai.core import context, gpu, health, ipc, shortcuts
from djerba_ai.core.agent import Agent
from djerba_ai.core.config import CONFIG_PUBLIC, RECOMMENDED_MODELS, SCHEMA, Config, ConfigError, memory_gb
from djerba_ai.core.llm import (CloudAPIProvider, LLMError, LocalOllamaProvider, RemoteTailscaleProvider,
                                client_from_config)
from djerba_ai.core.memory import Memory
from djerba_ai.gui import instance
from djerba_ai.tools import CATEGORIES, CATEGORY_LABELS, registry

QML_DIR = Path(os.environ.get("DJERBA_AI_QML", Path(__file__).with_name("qml")))
PAGES = ("chat", "system", "selfheal", "studio", "tools", "settings")
MODEL_KEYS = {"local": "llm.local.model", "tailscale": "llm.tailscale.model", "cloud": "llm.cloud.model"}
BRAINS = (("local", "Ollama local", "Sur cet ordinateur : rien ne sort de la machine."),
          ("tailscale", "Mac Mini (Tailscale)", "Un ordinateur puissant de votre réseau privé Tailscale."),
          ("cloud", "API en ligne", "OpenAI ou Google Gemini : rapide, mais vos messages quittent la machine."))
STATE_COLORS = {"ok": "#2DD4BF", "fallback": "#F59E0B", "busy": "#38BDF8", "down": "#F43F5E"}


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
    # Mode résident, Spotlight, réglages
    showMainRequested = pyqtSignal(str, arguments=["page"])
    spotlightRequested = pyqtSignal(str, str, arguments=["text", "mode"])
    brainsChanged = pyqtSignal()
    auditChanged = pyqtSignal()
    historyChanged = pyqtSignal()
    stateChanged = pyqtSignal()
    systemNotice = pyqtSignal(str, str, arguments=["title", "message"])

    def __init__(self, start_page: str, start_visible: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = Config()
        self.memory = Memory()
        self.registry = registry(include_plugins=True, approved=self.config.get("agent.plugins_approved"))
        self.agent = Agent(self.config, self.registry, self.memory)
        self._start_page = start_page if start_page in PAGES else "chat"
        self._start_visible = start_visible
        self._resident = False
        self._brains: dict[str, dict] = {key: {"status": "checking", "detail": "vérification…"} for key, *_ in BRAINS}
        self._audit: list = []
        self._history: list = []
        self._gpu = gpu.detect()
        self._fell_back = False
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
        QTimer.singleShot(100, self.refreshBrains)
        QTimer.singleShot(200, self.refreshHealth)
        QTimer.singleShot(400, self.refreshIncidents)
        QTimer.singleShot(600, self.refreshApps)
        # Synchronisation en temps réel avec /etc/djerba (vue publique de config.toml).
        self._watcher = QFileSystemWatcher(self)
        self._watch_config()
        self._watcher.fileChanged.connect(self._config_changed)
        self._watcher.directoryChanged.connect(self._config_changed)
        self._brain_timer = QTimer(self, interval=30000, timeout=self.refreshBrains)
        self._brain_timer.start()

    def _watch_config(self) -> None:
        paths = [str(path) for path in (CONFIG_PUBLIC, CONFIG_PUBLIC.parent, Config.FALLBACK) if path.exists()]
        missing = [path for path in paths if path not in self._watcher.files() + self._watcher.directories()]
        if missing:
            self._watcher.addPaths(missing)

    def _config_changed(self, _path: str) -> None:
        self.config.load()
        self._watch_config()  # un remplacement atomique retire le fichier surveillé
        self.settingsChanged.emit()
        self.refreshBrains()

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
        return str(self.config.get(MODEL_KEYS[self.provider]))

    @pyqtProperty(str, notify=settingsChanged)
    def provider(self) -> str:
        return str(self.config.get("llm.provider"))

    @pyqtProperty(bool, constant=True)
    def startVisible(self) -> bool:
        return self._start_visible

    @pyqtProperty(bool, notify=stateChanged)
    def resident(self) -> bool:
        return self._resident

    @pyqtProperty("QVariantMap", constant=True)
    def gpuInfo(self) -> dict:
        return self._gpu

    @pyqtProperty("QVariantList", notify=brainsChanged)
    def brains(self) -> list:
        return [{"id": key, "label": label, "note": note, **self._brains[key]} for key, label, note in BRAINS]

    @pyqtProperty(str, notify=stateChanged)
    def state(self) -> str:
        """État global (icône de la zone de notification, pastille) : ok, busy, fallback, down."""
        if self._busy:
            return "busy"
        current = self._brains.get(self.provider, {}).get("status")
        if current == "ok":
            return "ok"
        if self._fell_back or (self.provider != "local" and self._brains["local"].get("status") == "ok"
                               and self.config.get("llm.fallback_to_local")):
            return "fallback"
        return "down" if current == "down" else "ok" if self._engine["available"] else "down"

    @pyqtProperty(str, notify=stateChanged)
    def stateText(self) -> str:
        label = dict((key, label) for key, label, _note in BRAINS)[self.provider]
        return {"busy": "Djerba réfléchit…", "ok": f"Prêt — {label}",
                "fallback": f"{label} injoignable : repli sur le modèle local",
                "down": "Aucun moteur d'IA joignable (mode simplifié)"}[self.state]

    @pyqtProperty("QVariantMap", notify=settingsChanged)
    def shortcuts(self) -> dict:
        return {"spotlight": self.config.get("shortcuts.spotlight"), "clipboard": self.config.get("shortcuts.clipboard")}

    @pyqtProperty("QVariantList", notify=historyChanged)
    def settingsHistory(self) -> list:
        return self._history

    @pyqtProperty("QVariantList", notify=auditChanged)
    def auditEntries(self) -> list:
        return self._audit

    @pyqtProperty("QVariantList", notify=incidentsChanged)
    def sandboxHistory(self) -> list:
        """Journal Git du bac à sable (/var/lib/djerba-ai/sandbox) : correctifs et capacités validés."""
        return self._patches.get("history", [])

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
        self.stateChanged.emit()
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
            elif kind == "notice":
                # Bascule transparente sur le modèle local (fournisseur distant injoignable).
                self._fell_back = True
                self.notify.emit(payload["text"])
                self.systemNotice.emit("Djerba : repli sur le modèle local", payload["text"])
                QTimer.singleShot(0, self.stateChanged.emit)

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
            self.stateChanged.emit()
            self.conversationsChanged.emit()
            QTimer.singleShot(500, self.refreshHealth)
            if self._fell_back:
                QTimer.singleShot(1000, self.refreshBrains)

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
            local = LocalOllamaProvider(self.config.get("llm.local.endpoint"), self.config.get("llm.local.model"))
            available = client_from_config(self.config).available()
            installed = []
            if local.available(2):
                try:
                    installed = local.models()
                except LLMError:
                    installed = []
            return {"available": available, "installed": installed,
                    "ollama_present": Path("/usr/local/bin/ollama").exists() or Path("/usr/bin/ollama").exists()}

        def done(result) -> None:
            self._engine = result
            self.engineChanged.emit()
            self.stateChanged.emit()

        self._background(work, done)

    @pyqtSlot()
    def refreshBrains(self) -> None:
        """État de connexion de chaque cerveau (pastilles du sélecteur)."""
        config = self.config
        timeout = float(config.get("llm.remote_timeout"))

        def work():
            providers = {
                "local": LocalOllamaProvider(config.get("llm.local.endpoint"), config.get("llm.local.model")),
                "tailscale": RemoteTailscaleProvider(config.get("llm.tailscale.host"),
                                                     int(config.get("llm.tailscale.port")),
                                                     config.get("llm.tailscale.model"), timeout=timeout),
                "cloud": CloudAPIProvider(config.get("llm.cloud.service"), config.get("llm.cloud.model"), timeout),
            }
            states = {}
            for key, provider in providers.items():
                if key == "tailscale" and not config.get("llm.tailscale.host"):
                    states[key] = {"status": "unset", "detail": "non configuré", "latency_ms": 0}
                    continue
                if key == "cloud" and not config.get("llm.cloud.api_key_set"):
                    states[key] = {"status": "unset", "detail": "clé d'API à renseigner", "latency_ms": 0}
                    continue
                result = provider.health(timeout)
                states[key] = {"status": "ok" if result.get("ok") else "down",
                               "detail": str(result.get("detail", "")), "latency_ms": result.get("latency_ms", 0)}
            return states

        def done(result) -> None:
            if isinstance(result, dict) and "local" in result:
                self._brains = result
                if result.get(self.provider, {}).get("status") == "ok":
                    self._fell_back = False
                self.brainsChanged.emit()
                self.stateChanged.emit()

        self._background(work, done)

    @pyqtSlot()
    def warmup(self) -> None:
        """Préchargement asynchrone du modèle (keep_alive) à l'ouverture de Spotlight."""
        self._background(lambda: client_from_config(self.config).warmup() or {"success": True})

    @pyqtSlot(str)
    def setModel(self, name: str) -> None:
        if self._apply_setting(MODEL_KEYS[self.provider], name):
            self.notify.emit(f"Djerba utilise maintenant {name}.")

    @pyqtSlot(str)
    def setBrain(self, provider: str) -> None:
        if provider in MODEL_KEYS and self._apply_setting("llm.provider", provider):
            self._fell_back = False
            self.notify.emit(f"Cerveau : {dict((k, l) for k, l, _n in BRAINS)[provider]}.")
            self.refreshEngine()
            self.refreshBrains()

    @pyqtSlot(str)
    def pullModel(self, name: str) -> None:
        client = LocalOllamaProvider(self.config.get("llm.local.endpoint"), name)

        def work():
            client.pull(name, lambda status, percent: self.progress.emit(f"{name} : {status}", percent))
            return {"success": True}

        def done(result) -> None:
            if result.get("success"):
                self._apply_setting("llm.local.model", name)
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

    # ---------------------------------------------------------------- Réglages
    def _apply_setting(self, key: str, value) -> bool:
        try:
            self.config.set(key, value)
        except (ConfigError, KeyError, ValueError, TypeError) as exc:
            self.notify.emit(f"Réglage refusé : {exc}")
            return False
        self.settingsChanged.emit()
        self.refreshHistory()
        return True

    @pyqtSlot(str, "QVariant")
    def setSetting(self, key: str, value) -> None:
        spec = SCHEMA.get(key)
        if spec is None or spec.get("secret"):
            return
        if spec["type"] == "integer":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return
        if self._apply_setting(key, value) and key.startswith("llm."):
            self.refreshEngine()
            self.refreshBrains()

    @pyqtSlot(str)
    def setApiKey(self, key: str) -> None:
        """Clé d'API en ligne : transmise au démon root, jamais relue par l'interface."""
        if key.strip() and self._apply_setting("llm.cloud.api_key", key.strip()):
            self.notify.emit("Clé d'API enregistrée (lisible uniquement par root).")
            self.refreshBrains()

    @pyqtSlot()
    def finishOnboarding(self) -> None:
        self._apply_setting("ui.onboarded", True)
        self.refreshBrains()

    @pyqtSlot(int, int, result=str)
    def keySequence(self, key: int, modifiers: int) -> str:
        """Séquence lisible (« Meta+Shift+C ») d'une touche saisie dans l'éditeur de raccourcis."""
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_Super_L,
                   Qt.Key.Key_Super_R, Qt.Key.Key_AltGr, 0, Qt.Key.Key_unknown):
            return ""
        combination = QKeyCombination(Qt.KeyboardModifier(modifiers & 0x1E000000), Qt.Key(key))
        return QKeySequence(combination).toString(QKeySequence.SequenceFormat.PortableText)

    @pyqtSlot(str, str)
    def applyShortcut(self, name: str, sequence: str) -> None:
        try:
            result = shortcuts.apply(name, sequence)
        except shortcuts.ShortcutError as exc:
            self.notify.emit(str(exc))
            return
        self._apply_setting(f"shortcuts.{name}", result["sequence"])
        self.notify.emit(result["message"])

    @pyqtSlot()
    def refreshHistory(self) -> None:
        self._history = [{"key": row["key"], "value": row["value"],
                          "time": time.strftime("%d/%m %H:%M", time.localtime(row["created"]))}
                         for row in self.memory.settings_history(40)]
        self.historyChanged.emit()

    @pyqtSlot()
    def refreshAudit(self) -> None:
        def work():
            try:
                return {"entries": ipc.call("audit.tail", {"lines": 150}, timeout=10).get("entries", [])}
            except ipc.RPCError as exc:
                return {"entries": [], "error": str(exc)}

        def done(result) -> None:
            entries = result.get("entries", [])
            entries.reverse()
            self._audit = [{"time": str(item.get("time", "")), "event": str(item.get("event", "")),
                            "what": str(item.get("tool") or item.get("method") or ""),
                            "uid": item.get("uid", ""), "success": item.get("success") is not False,
                            "message": str(item.get("message", ""))} for item in entries]
            if result.get("error") and not entries:
                self._audit = [{"time": "", "event": "indisponible", "what": "", "uid": "", "success": False,
                                "message": result["error"]}]
            self.auditChanged.emit()

        self._background(work, done)

    @pyqtSlot(result=str)
    def clipboardText(self) -> str:
        return (QGuiApplication.clipboard().text() or "")[:8000]

    @pyqtSlot(str)
    def openMain(self, page: str) -> None:
        self.showMainRequested.emit(page if page in PAGES else "")

    def dispatch(self, argv: list[str]) -> None:
        """Demande transmise au lancement ou par une autre instance."""
        request = instance.parse(argv)
        if request["mode"] == "spotlight":
            self.spotlightRequested.emit("", "ask")
        elif request["mode"] == "clipboard":
            self.spotlightRequested.emit(self.clipboardText(), "clipboard")
        elif request["mode"] == "window":
            self.showMainRequested.emit(request["page"] if request["page"] in PAGES else "")

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


def state_icon(state: str) -> QIcon:
    """Icône de la zone de notification : logo de Djerba et pastille d'état."""
    size = 64
    pixmap = QIcon.fromTheme("djerba-ai", QIcon.fromTheme("djerba-os")).pixmap(QSize(size, size))
    if pixmap.isNull():
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#00C2CB"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(6, 6, size - 12, size - 12)
        painter.end()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#0B1220"))
    painter.setBrush(QColor(STATE_COLORS.get(state, STATE_COLORS["ok"])))
    painter.drawEllipse(size - 26, size - 26, 22, 22)
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    """Icône intelligente : état dynamique, menu contextuel, notifications."""

    def __init__(self, backend: Backend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self.icon = QSystemTrayIcon(self)
        self.menu = QMenu()
        self.menu.addAction("Ouvrir Djerba", lambda: backend.openMain("chat"))
        self.spotlight = self.menu.addAction("Spotlight", lambda: backend.spotlightRequested.emit("", "ask"))
        self.clipboard = self.menu.addAction("Analyser le presse-papiers",
                                             lambda: backend.spotlightRequested.emit(backend.clipboardText(),
                                                                                     "clipboard"))
        self.menu.addSeparator()
        brains = self.menu.addMenu("Cerveau")
        self.group = QActionGroup(self)
        self.brain_actions: dict[str, QAction] = {}
        for key, label, _note in BRAINS:
            action = brains.addAction(label, lambda key=key: backend.setBrain(key))
            action.setCheckable(True)
            self.group.addAction(action)
            self.brain_actions[key] = action
        self.menu.addAction("Studio", lambda: backend.openMain("studio"))
        self.menu.addAction("Réglages", lambda: backend.openMain("settings"))
        self.menu.addAction("Santé du système", lambda: backend.openMain("system"))
        self.menu.addSeparator()
        self.menu.addAction("Quitter Djerba", QApplication.quit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        for signal in (backend.stateChanged, backend.brainsChanged, backend.settingsChanged):
            signal.connect(self.update)
        backend.systemNotice.connect(
            lambda title, message: self.icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 8000))
        self.icon.messageClicked.connect(lambda: backend.openMain("settings"))
        self.update()
        self.icon.show()

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.backend.openMain("")
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.backend.spotlightRequested.emit("", "ask")

    def update(self, *_args) -> None:
        state = self.backend.state
        self.icon.setIcon(state_icon(state))
        self.icon.setToolTip(f"Djerba — {self.backend.stateText}")
        current = self.backend.provider
        for key, action in self.brain_actions.items():
            action.setChecked(key == current)
            status = next((brain["status"] for brain in self.backend.brains if brain["id"] == key), "")
            label = dict((k, l) for k, l, _n in BRAINS)[key]
            action.setText(label + {"ok": "  ●", "down": "  ○ injoignable", "unset": "  (à configurer)"}
                           .get(status, ""))
        shortcuts_map = self.backend.shortcuts
        self.spotlight.setText(f"Spotlight\t{shortcuts_map['spotlight']}")
        self.clipboard.setText(f"Analyser le presse-papiers\t{shortcuts_map['clipboard']}")


def enable_blur(window: QQuickWindow) -> None:
    """Flou d'arrière-plan de KWin pour une fenêtre translucide.

    Sous X11 (et XWayland), la propriété _KDE_NET_WM_BLUR_BEHIND_REGION
    demande le flou à KWin. Sous Wayland natif, ce protocole n'est exposé que
    par KWindowSystem (C++), absent de PyQt6 : la fenêtre garde alors un fond
    teinté translucide.
    """
    if QGuiApplication.platformName() != "xcb" or shutil.which("xprop") is None:
        return
    subprocess.run(["xprop", "-id", str(int(window.winId())), "-f", "_KDE_NET_WM_BLUR_BEHIND_REGION", "32c",
                    "-set", "_KDE_NET_WM_BLUR_BEHIND_REGION", "0"], capture_output=True, check=False, timeout=5)


def listen(backend: Backend, app: QApplication) -> QLocalServer | None:
    """Instance unique : reçoit les demandes des lancements suivants."""
    instance.SOCKET.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(str(instance.SOCKET))
    if not server.listen(str(instance.SOCKET)):
        return None

    def accept() -> None:
        connection = server.nextPendingConnection()
        if connection is None:
            return

        def read() -> None:
            if not connection.canReadLine():
                return
            try:
                argv = json.loads(bytes(connection.readLine()).decode()).get("argv", [])
            except ValueError:
                argv = []
            connection.write(b"ok\n")
            connection.flush()
            connection.disconnectFromServer()
            backend.dispatch([str(arg) for arg in argv])

        connection.readyRead.connect(read)
        if connection.bytesAvailable():
            read()

    server.newConnection.connect(accept)
    return server


def main(argv: list[str]) -> int:
    if instance.forward(argv[1:]):
        return 0
    request = instance.parse(argv)
    QApplication.setApplicationName("djerba-ai")
    QApplication.setApplicationDisplayName("Djerba")
    QApplication.setDesktopFileName("djerba-ai")
    app = QApplication(argv)
    app.setWindowIcon(QIcon.fromTheme("djerba-ai", QIcon.fromTheme("djerba-os")))
    if not QIcon.themeName() or QIcon.themeName() == "hicolor":
        QIcon.setThemeName("breeze-dark")
    backend = Backend(request["page"], request["mode"] == "window", app)
    app.aboutToQuit.connect(backend.shutdown)
    server = listen(backend, app)
    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable() and os.environ.get("DJERBA_AI_NO_TRAY") != "1":
        tray = Tray(backend, app)
        backend._resident = True
        app.setQuitOnLastWindowClosed(False)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", IconProvider())
    engine.rootContext().setContextProperty("backend", backend)
    roots = []
    for name in ("main.qml", "Spotlight.qml"):
        engine.load(QUrl.fromLocalFile(str(QML_DIR / name)))
        if len(engine.rootObjects()) == len(roots):
            print(f"djerba-ai : impossible de charger {QML_DIR / name}", file=sys.stderr)
            return 1
        roots = engine.rootObjects()
    for root in roots:
        if isinstance(root, QQuickWindow):
            root.visibleChanged.connect(lambda visible, window=root: visible and enable_blur(window))
    if request["mode"] == "tray" and tray is None:
        backend.showMainRequested.emit("")  # pas de zone de notification : fenêtre normale
    else:
        QTimer.singleShot(0, lambda: backend.dispatch(argv[1:]))
    if os.environ.get("DJERBA_AI_DEV") == "1":
        # Rechargement à chaud de l'interface de Djerba pendant son développement.
        watcher = QFileSystemWatcher([str(path) for path in QML_DIR.glob("*.qml")], app)

        def reload(_path: str) -> None:
            for root in engine.rootObjects():
                root.deleteLater()
            engine.clearComponentCache()
            for name in ("main.qml", "Spotlight.qml"):
                engine.load(QUrl.fromLocalFile(str(QML_DIR / name)))
            watcher.addPaths([str(path) for path in QML_DIR.glob("*.qml")])

        watcher.fileChanged.connect(reload)
    code = app.exec()
    if server is not None:
        server.close()
    return code
