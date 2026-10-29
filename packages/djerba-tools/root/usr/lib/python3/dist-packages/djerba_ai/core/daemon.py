# SPDX-License-Identifier: GPL-3.0-or-later
"""Démon utilisateur de Djerba (service systemd --user djerba-ai.service).

* surveillance du journal et incidents d'auto-réparation (notification) ;
* maintenance quotidienne à l'heure choisie : bilan de santé, notification
  si quelque chose demande votre attention ;
* mise à jour hebdomadaire du modèle de langage (ollama pull : seules les
  couches modifiées sont téléchargées) ;
* chien de garde (system.watchdog) : espace disque sous le seuil d'alerte
  (system.disk_alert_percent, 10 % par défaut) et services en échec ;
* bus de contexte permanent (core.contextbus) : métriques de l'OS, erreurs du
  journal et fenêtre active, horodatées et chiffrées, pour l'agent ;
* télémétrie (core.telemetry) : http://127.0.0.1:9464/metrics (Prometheus,
  Grafana) et /health (Uptime Kuma), en local uniquement ;
* rien n'est modifié sur le système sans votre accord.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from djerba_ai.core import contextbus, health, ipc, telemetry
from djerba_ai.core.bootguard import NOTICE as BOOT_NOTICE
from djerba_ai.core.contextbus import ContextBus
from djerba_ai.core.telemetry import Exporter
from djerba_ai.core.config import STATE_HOME, Config
from djerba_ai.core.llm import LLMError, LocalOllamaProvider
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
    if time.localtime().tm_hour < int(config.get("agent.maintenance_hour")) or state.get("maintenance") == today:
        return
    state["maintenance"] = today
    save_state(state)
    result = health.report()
    attention = [item for item in result["items"] if item["level"] != "ok"]
    if attention:
        notify(f"Bilan Djerba : {result['score']}/100",
               "\n".join(f"• {item['title']}" for item in attention[:4]), page="system")

    if config.get("agent.auto_update_model"):
        week = time.strftime("%G-%V")
        if state.get("model_update") != week:
            client = LocalOllamaProvider(config.get("llm.local.endpoint"), config.get("llm.local.model"))
            if client.available():
                try:
                    client.pull(config.get("llm.local.model"))
                    state["model_update"] = week
                    save_state(state)
                except LLMError:
                    pass


def boot_notice(state: dict, path: Path = BOOT_NOTICE) -> dict | None:
    """Avis du rollback au démarrage (écrit par djerba-boot, root) : notifié une seule fois par avis."""
    try:
        notice = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(notice, dict) or not notice.get("id") or not notice.get("title"):
        return None
    seen = state.setdefault("boot_notices", [])
    if notice["id"] in seen:
        return None
    state["boot_notices"] = (seen + [str(notice["id"])])[-20:]
    save_state(state)
    notify(str(notice["title"])[:120], str(notice.get("body", ""))[:400], page="system")
    return notice


WATCHED_MOUNTS = ("/", "/home", "/var")


def watchdog(config: Config, state: dict) -> None:
    """Alerte (une fois par jour et par problème) : disque presque plein, service en échec."""
    if not config.get("system.watchdog"):
        return
    today = time.strftime("%Y-%m-%d")
    alerts = state.setdefault("alerts", {})
    threshold = int(config.get("system.disk_alert_percent"))
    seen = set()
    for mount in WATCHED_MOUNTS:
        try:
            usage = shutil.disk_usage(mount)
            device = os.stat(mount).st_dev
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        free = 100 * usage.free / usage.total if usage.total else 100
        key = f"disk:{mount}"
        if free < threshold and alerts.get(key) != today:
            alerts[key] = today
            notify("Espace disque faible", f"{mount} : {free:.0f} % libre ({usage.free / 1e9:.1f} Go). "
                   "Djerba peut faire le ménage.", page="system")
    try:
        output = subprocess.run(["systemctl", "--failed", "--no-legend", "--plain", "--type=service"],
                                capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        output = ""
    for line in output.splitlines():
        unit = line.split()[0] if line.split() else ""
        key = f"unit:{unit}"
        if unit and alerts.get(key) != today:
            alerts[key] = today
            notify("Service en échec", f"{unit} ne fonctionne pas. Ouvrez Djerba pour un diagnostic.", page="system")
    save_state(state)


def main() -> int:
    config = Config()
    memory = Memory()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    watcher = None
    if config.get("agent.self_heal"):
        def on_incident(incident_id: int, incident: dict) -> None:
            name = incident["target"].rsplit("/", 1)[-1] if incident["target"] else incident["source"]
            notify("Djerba a détecté un problème", f"{name} : {incident['message'][:160]}", page="selfheal")

        watcher = JournalWatcher(memory, on_incident)
        watcher.start()

    # Bureau courant (KDE Plasma ou GNOME) : raccourcis et intégrations rétablis à
    # chaque session, y compris après un changement de bureau.
    # Mode dégradé silencieux : une étape en échec est consignée (gnome-crash.log…) et ignorée.
    from djerba_ai.ui.adapter import safe_sync
    report = safe_sync(config)
    print(f"djerba-ai : bureau {report['desktop']}" + (" (changement de bureau)" if report.get("switched") else "")
          + (" (mode dégradé)" if report.get("degraded") else "")
          + "".join(f" ; {item}" for item in report.get("integrations", [])), flush=True)
    bus = ContextBus(float(config.get("context.interval")), bool(config.get("context.window_titles"))).start()
    exporter = None
    if config.get("telemetry.enabled"):
        def sysd_metrics() -> dict | None:
            try:
                return {"role": "sysd", **ipc.call("sys.metrics", {}, timeout=3)}
            except ipc.RPCError:
                return None

        def checks() -> dict[str, bool]:
            snapshot = contextbus.read()
            age = time.time() - snapshot["epoch"] if snapshot else 1e9
            telemetry.gauge("djerba_context_age_seconds", age if snapshot else -1)
            return {"context_bus": age < 4 * float(config.get("context.interval")) + 5,
                    "sysd": sysd_metrics() is not None}

        try:
            exporter = Exporter(int(config.get("telemetry.port")), sysd_metrics, checks).start()
        except OSError as exc:
            print(f"djerba-ai : télémétrie indisponible ({exc})", flush=True)
    telemetry.publish_forever("daemon")

    state = load_state()
    ticks = 0
    while not stop.wait(60):
        config.load()
        try:
            maintenance(config, state)
        except Exception as exc:  # noqa: BLE001 - la maintenance ne doit jamais arrêter le démon
            print(f"djerba-ai : maintenance : {exc}", flush=True)
        ticks += 1
        if ticks % 10 == 1:  # toutes les 10 minutes
            boot_notice(state)
            try:
                watchdog(config, state)
            except Exception as exc:  # noqa: BLE001 - idem
                print(f"djerba-ai : chien de garde : {exc}", flush=True)
    if watcher is not None:
        watcher.stop()
    bus.stop.set()
    if exporter is not None:
        exporter.stop()
    return 0
