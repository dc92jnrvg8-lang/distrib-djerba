# SPDX-License-Identifier: GPL-3.0-or-later
"""Démon utilisateur de Djerba (service systemd --user djerba-ai.service).

* surveillance du journal et incidents d'auto-réparation (notification) ;
* maintenance quotidienne à l'heure choisie : bilan de santé, notification
  si quelque chose demande votre attention ;
* mise à jour hebdomadaire du modèle de langage (ollama pull : seules les
  couches modifiées sont téléchargées) ;
* rien n'est modifié sur le système sans votre accord.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import threading
import time

from djerba_ai.core import health
from djerba_ai.core.config import STATE_HOME, Config
from djerba_ai.core.llm import LLMError, OllamaClient
from djerba_ai.core.memory import Memory
from djerba_ai.core.selfheal import JournalWatcher

STATE = STATE_HOME / "djerba-ai" / "daemon.json"


def notify(title: str, body: str, page: str = "") -> None:
    if shutil.which("notify-send") is None:
        return

    def worker() -> None:
        command = ["notify-send", "--app-name=Djerba", "--icon=djerba-ai", title, body]
        if page:
            command[1:1] = ["--action=open=Ouvrir Djerba", "--wait"]
        try:
            answer = subprocess.run(command, capture_output=True, text=True, timeout=3600).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return
        if answer == "open":
            subprocess.Popen(["djerba-ai", "--page", page], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    threading.Thread(target=worker, daemon=True).start()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def maintenance(config: Config, state: dict) -> None:
    today = time.strftime("%Y-%m-%d")
    if time.localtime().tm_hour < int(config.get("maintenance_hour")) or state.get("maintenance") == today:
        return
    state["maintenance"] = today
    save_state(state)
    result = health.report()
    attention = [item for item in result["items"] if item["level"] != "ok"]
    if attention:
        notify(f"Bilan Djerba : {result['score']}/100",
               "\n".join(f"• {item['title']}" for item in attention[:4]), page="system")

    if config.get("auto_update_model") and config.get("provider") == "ollama":
        week = time.strftime("%G-%V")
        if state.get("model_update") != week:
            client = OllamaClient(config.get("ollama_endpoint"), config.get("model"))
            if client.available():
                try:
                    client.pull(config.get("model"))
                    state["model_update"] = week
                    save_state(state)
                except LLMError:
                    pass


def main() -> int:
    config = Config()
    memory = Memory()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    watcher = None
    if config.get("self_heal"):
        def on_incident(incident_id: int, incident: dict) -> None:
            name = incident["target"].rsplit("/", 1)[-1] if incident["target"] else incident["source"]
            notify("Djerba a détecté un problème", f"{name} : {incident['message'][:160]}", page="selfheal")

        watcher = JournalWatcher(memory, on_incident)
        watcher.start()

    state = load_state()
    while not stop.wait(60):
        config.load()
        try:
            maintenance(config, state)
        except Exception as exc:  # noqa: BLE001 - la maintenance ne doit jamais arrêter le démon
            print(f"djerba-ai : maintenance : {exc}", flush=True)
    if watcher is not None:
        watcher.stop()
    return 0
