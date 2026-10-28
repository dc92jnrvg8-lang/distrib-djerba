# SPDX-License-Identifier: GPL-3.0-or-later
"""Télémétrie native de l'agent (format Prometheus, sans dépendance).

* Chaque processus de Djerba (interface, démon utilisateur, démon root) tient
  ses compteurs, jauges et histogrammes en mémoire ; les processus
  utilisateur les déposent toutes les 5 s dans
  $XDG_RUNTIME_DIR/djerba-ai/metrics/<pid>.json (0600).
* Le démon utilisateur agrège ces fichiers (processus vivants seulement),
  ajoute l'empreinte de chaque processus (mémoire résidente, temps CPU) et
  les métriques du démon root (``sys.metrics``), puis les sert en local :

  - ``http://127.0.0.1:9464/metrics`` : Prometheus (et donc Grafana : tableau
    de bord fourni dans /usr/share/djerba-ai/monitoring/) ;
  - ``http://127.0.0.1:9464/health`` : état JSON pour Uptime Kuma
    (moniteur HTTP, mot-clé ``"status": "ok"``).

Le serveur n'écoute que sur la boucle locale ; rien n'est envoyé ailleurs.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)
HELP = {
    "djerba_llm_requests_total": ("counter", "Requêtes envoyées au moteur d'inférence"),
    "djerba_llm_latency_seconds": ("histogram", "Durée d'une réponse du moteur d'inférence"),
    "djerba_llm_fallbacks_total": ("counter", "Bascules automatiques sur le modèle local"),
    "djerba_tool_runs_total": ("counter", "Capacités exécutées"),
    "djerba_tool_latency_seconds": ("histogram", "Durée d'exécution d'une capacité"),
    "djerba_agent_turns_total": ("counter", "Tours de l'agent"),
    "djerba_agent_turn_seconds": ("histogram", "Durée d'un tour complet de l'agent"),
    "djerba_guard_flags_total": ("counter", "Indices d'injection de prompt détectés"),
    "djerba_dlp_secrets_total": ("counter", "Secrets interceptés par le DLP"),
    "djerba_process_resident_bytes": ("gauge", "Mémoire résidente d'un processus de Djerba"),
    "djerba_process_cpu_seconds_total": ("counter", "Temps CPU consommé par un processus de Djerba"),
    "djerba_context_age_seconds": ("gauge", "Âge du dernier instantané du bus de contexte"),
    "djerba_sysd_requests_total": ("counter", "Requêtes reçues par le démon root"),
    "djerba_sysd_denied_total": ("counter", "Requêtes refusées par le démon root"),
    "djerba_policy_refusals_total": ("counter", "Actions refusées par la politique d'autonomie"),
    "djerba_sysd_latency_seconds": ("histogram", "Durée de traitement d'une requête du démon root"),
    "djerba_up": ("gauge", "Le démon de télémétrie répond"),
}


def _key(name: str, labels: dict) -> str:
    return json.dumps([name, sorted((str(k), str(v)) for k, v in labels.items())])


class Registry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counters: dict[str, float] = {}
        self.gauges: dict[str, float] = {}
        self.histograms: dict[str, dict] = {}

    def inc(self, name: str, value: float = 1, **labels) -> None:
        key = _key(name, labels)
        with self.lock:
            self.counters[key] = self.counters.get(key, 0) + value

    def gauge(self, name: str, value: float, **labels) -> None:
        with self.lock:
            self.gauges[_key(name, labels)] = value

    def observe(self, name: str, value: float, **labels) -> None:
        key = _key(name, labels)
        with self.lock:
            histogram = self.histograms.setdefault(key, {"buckets": [0] * len(BUCKETS), "sum": 0.0, "count": 0})
            for index, bound in enumerate(BUCKETS):
                if value <= bound:
                    histogram["buckets"][index] += 1
            histogram["sum"] += value
            histogram["count"] += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {"counters": dict(self.counters), "gauges": dict(self.gauges),
                    "histograms": json.loads(json.dumps(self.histograms))}


REGISTRY = Registry()
inc, gauge, observe = REGISTRY.inc, REGISTRY.gauge, REGISTRY.observe


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "djerba-ai" / "metrics"


# ---------------------------------------------------------------- Dépôt par processus
_publisher: threading.Thread | None = None


def publish_forever(role: str, interval: float = 5) -> None:
    """Dépose périodiquement les métriques de ce processus (démarrage idempotent)."""
    global _publisher
    if _publisher is not None or os.geteuid() == 0:
        return

    def loop() -> None:
        while True:
            publish(role)
            time.sleep(interval)

    _publisher = threading.Thread(target=loop, daemon=True, name="djerba-telemetry")
    _publisher.start()


def publish(role: str) -> None:
    directory = runtime_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps({"pid": os.getpid(), "role": role, "time": time.time(), **REGISTRY.snapshot()})
        temporary = directory / f".{os.getpid()}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
        os.replace(temporary, directory / f"{os.getpid()}.json")
    except OSError:
        pass


# ---------------------------------------------------------------- Agrégation et exposition
def process_stats(pid: int) -> tuple[int, float] | None:
    """(mémoire résidente en octets, temps CPU en secondes) d'un processus."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        pages = int(Path(f"/proc/{pid}/statm").read_text().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    ticks = os.sysconf("SC_CLK_TCK")
    return pages * os.sysconf("SC_PAGE_SIZE"), (int(fields[11]) + int(fields[12])) / ticks


def collect(extra: Callable[[], dict | None] | None = None) -> list[dict]:
    """Instantanés de tous les processus vivants (+ métriques supplémentaires)."""
    snapshots = []
    directory = runtime_dir()
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not Path(f"/proc/{data.get('pid')}").exists():
            path.unlink(missing_ok=True)
            continue
        snapshots.append(data)
    if not any(item.get("pid") == os.getpid() for item in snapshots):
        snapshots.append({"pid": os.getpid(), "role": "daemon", **REGISTRY.snapshot()})
    if extra:
        try:
            more = extra()
        except Exception:  # noqa: BLE001 - une source absente n'empêche pas l'exposition
            more = None
        if more:
            snapshots.append(more)
    return snapshots


def _labels(pairs: list, **extra) -> str:
    items = [(k, v) for k, v in pairs] + sorted(extra.items())
    if not items:
        return ""
    escaped = (f'{k}="{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
               for k, v in items)
    return "{" + ",".join(escaped) + "}"


def render(snapshots: list[dict]) -> str:
    """Format d'exposition texte de Prometheus (0.0.4)."""
    series: dict[str, list[str]] = {}

    def add(name: str, line: str) -> None:
        series.setdefault(name, []).append(line)

    for snapshot in snapshots:
        process = {"role": snapshot.get("role", ""), "pid": snapshot.get("pid", "")}
        for key, value in snapshot.get("counters", {}).items():
            name, pairs = json.loads(key)
            add(name, f"{name}{_labels(pairs, **process)} {value}")
        for key, value in snapshot.get("gauges", {}).items():
            name, pairs = json.loads(key)
            add(name, f"{name}{_labels(pairs, **process)} {value}")
        for key, histogram in snapshot.get("histograms", {}).items():
            name, pairs = json.loads(key)
            for bound, count in zip(BUCKETS, histogram["buckets"]):
                add(name, f"{name}_bucket{_labels(pairs, le=bound, **process)} {count}")
            add(name, f"{name}_bucket{_labels(pairs, le='+Inf', **process)} {histogram['count']}")
            add(name, f"{name}_sum{_labels(pairs, **process)} {histogram['sum']}")
            add(name, f"{name}_count{_labels(pairs, **process)} {histogram['count']}")
        pid = snapshot.get("pid")
        if isinstance(pid, int):
            stats = process_stats(pid)
            if stats:
                add("djerba_process_resident_bytes", f"djerba_process_resident_bytes{_labels([], **process)} {stats[0]}")
                add("djerba_process_cpu_seconds_total",
                    f"djerba_process_cpu_seconds_total{_labels([], **process)} {stats[1]}")
    add("djerba_up", "djerba_up 1")
    lines = []
    for name in sorted(series):
        kind, text = HELP.get(name, ("untyped", name))
        lines += [f"# HELP {name} {text}", f"# TYPE {name} {kind}", *series[name]]
    return "\n".join(lines) + "\n"


def health(snapshots: list[dict], checks: dict[str, bool] | None = None) -> dict:
    checks = checks or {}
    status = "ok" if all(checks.values()) else "degraded"
    roles = sorted({str(item.get("role")) for item in snapshots})
    return {"status": status, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "processes": roles, "checks": checks}


class Exporter:
    """Serveur HTTP local /metrics et /health (fil d'exécution dédié)."""

    def __init__(self, port: int = 9464, extra: Callable[[], dict | None] | None = None,
                 checks: Callable[[], dict[str, bool]] | None = None, host: str = "127.0.0.1") -> None:
        exporter = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - API de http.server
                snapshots = collect(exporter.extra)
                if self.path.split("?")[0] == "/metrics":
                    body, kind, code = render(snapshots).encode(), "text/plain; version=0.0.4; charset=utf-8", 200
                elif self.path.split("?")[0] == "/health":
                    state = health(snapshots, exporter.checks() if exporter.checks else None)
                    body, kind = json.dumps(state, ensure_ascii=False).encode(), "application/json"
                    code = 200 if state["status"] == "ok" else 503
                else:
                    body, kind, code = b"Djerba : /metrics, /health\n", "text/plain; charset=utf-8", 404
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.extra = extra
        self.checks = checks
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]

    def start(self) -> "Exporter":
        threading.Thread(target=self.server.serve_forever, daemon=True, name="djerba-exporter").start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
