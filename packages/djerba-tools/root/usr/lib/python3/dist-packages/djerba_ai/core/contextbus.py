# SPDX-License-Identifier: GPL-3.0-or-later
"""Bus de contexte permanent : l'état vivant de l'OS, horodaté, chiffré.

Le démon utilisateur échantillonne en continu (toutes les 5 s par défaut) :

* processeur (taux d'occupation, charge), mémoire et échange, débits d'E/S
  disque et réseau (/proc) ;
* erreurs récentes du journal (``journalctl -p err``, toutes les 30 s) ;
* fenêtre active de KWin, poussée à chaque changement de focus par un script
  KWin (Wayland comme X11) vers le service D-Bus ``org.djerba.ai.Context``.

Chaque valeur porte son propre horodatage (UTC, milliseconde) et l'instantané
un numéro de séquence et une horloge monotone : l'agent sait de quand date
chaque information et ne confond jamais un état passé avec l'état présent
(un instantané de plus de deux minutes est déclaré périmé). Les titres de
fenêtres et lignes de journal passent par le DLP avant d'entrer dans le bus.

L'instantané est écrit de façon atomique et chiffrée (AES-256-GCM, clé de
l'utilisateur) dans $XDG_RUNTIME_DIR/djerba-ai/context.bin ; l'agent le lit et
l'injecte dans son invite système.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from djerba_ai.core.crypto import Box, CryptoError, write_atomic
from djerba_ai.core.sanitize import sanitize

STALE_AFTER = 120
KWIN_SCRIPT = Path(os.environ.get("DJERBA_KWIN_SCRIPT", "/usr/share/djerba-ai/kwin/active-window.js"))
DBUS_NAME = "org.djerba.ai.Context"


def stamp(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time(), timezone.utc).isoformat(timespec="milliseconds")


def bus_file() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "djerba-ai" / "context.bin"


# ---------------------------------------------------------------- Mesures /proc
def _cpu_times() -> tuple[int, int]:
    fields = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields), idle


def _disk_sectors() -> tuple[int, int]:
    """Octets lus et écrits par les disques entiers (/sys/block), hors périphériques virtuels."""
    try:
        disks = {name for name in os.listdir("/sys/block") if not name.startswith(("loop", "ram", "zram", "dm-", "sr"))}
    except OSError:
        disks = set()
    read = written = 0
    for line in Path("/proc/diskstats").read_text().splitlines():
        parts = line.split()
        if parts[2] in disks:
            read += int(parts[5])
            written += int(parts[9])
    return read * 512, written * 512


def _net_bytes() -> tuple[int, int]:
    rx = tx = 0
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, data = line.split(":", 1)
        if name.strip() == "lo":
            continue
        values = data.split()
        rx += int(values[0])
        tx += int(values[8])
    return rx, tx


def _memory() -> dict:
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        info[key] = int(value.split()[0]) * 1024
    return {"total": info.get("MemTotal", 0), "available": info.get("MemAvailable", 0),
            "swap_total": info.get("SwapTotal", 0), "swap_free": info.get("SwapFree", 0)}


class Sampler:
    """Mesures différentielles (débits, taux) entre deux échantillons."""

    def __init__(self) -> None:
        self.previous: dict | None = None

    def sample(self) -> dict:
        now = time.monotonic()
        cpu_total, cpu_idle = _cpu_times()
        disk_read, disk_written = _disk_sectors()
        rx, tx = _net_bytes()
        current = {"mono": now, "cpu": (cpu_total, cpu_idle), "disk": (disk_read, disk_written), "net": (rx, tx)}
        metrics = {"time": stamp(), "load": os.getloadavg()[0], "memory": _memory()}
        if self.previous:
            elapsed = max(now - self.previous["mono"], 1e-3)
            total = cpu_total - self.previous["cpu"][0]
            idle = cpu_idle - self.previous["cpu"][1]
            metrics["cpu_percent"] = round(100 * (1 - idle / total), 1) if total > 0 else 0.0
            metrics["disk_read_bps"] = int((disk_read - self.previous["disk"][0]) / elapsed)
            metrics["disk_write_bps"] = int((disk_written - self.previous["disk"][1]) / elapsed)
            metrics["net_rx_bps"] = int((rx - self.previous["net"][0]) / elapsed)
            metrics["net_tx_bps"] = int((tx - self.previous["net"][1]) / elapsed)
        self.previous = current
        return metrics


def journal_errors(limit: int = 5, since: str = "-15min") -> list[dict]:
    if shutil.which("journalctl") is None:
        return []
    entries = []
    for scope in ([], ["--user"]):
        try:
            output = subprocess.run(["journalctl", *scope, "-p", "err", "-n", str(limit), "--since", since,
                                     "-o", "json", "--no-pager"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in output.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("MESSAGE")
            if not isinstance(message, str):
                continue
            epoch = int(entry.get("__REALTIME_TIMESTAMP", "0")) / 1e6
            entries.append({"time": stamp(epoch), "epoch": epoch,
                            "source": str(entry.get("SYSLOG_IDENTIFIER") or entry.get("_COMM") or "?")[:40],
                            "message": sanitize(message)[:240]})
    entries.sort(key=lambda item: item["epoch"])
    return entries[-limit:]


# ---------------------------------------------------------------- Fenêtre active (KWin)
class WindowBridge:
    """Reçoit la fenêtre active de KWin par D-Bus (script KWin chargé au démarrage)."""

    def __init__(self, titles: bool = True) -> None:
        self.titles = titles
        self.window: dict = {}
        self.status = "inactif"

    def start(self) -> "WindowBridge":
        try:
            import dbus
            import dbus.mainloop.glib
            import dbus.service
            from gi.repository import GLib
        except ImportError:
            self.status = "python3-dbus / python3-gi absents"
            return self
        bridge = self
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SessionBus()
            name = dbus.service.BusName(DBUS_NAME, bus)

            class Context(dbus.service.Object):
                @dbus.service.method(DBUS_NAME, in_signature="ssi", out_signature="")
                def ActiveWindow(self, caption, resource, pid):  # noqa: N802 - nom D-Bus
                    bridge.window = {"time": stamp(), "application": sanitize(str(resource))[:80],
                                     "title": sanitize(str(caption))[:160] if bridge.titles else "",
                                     "pid": int(pid)}

            self._service = (name, Context(bus, "/Context"))
            threading.Thread(target=GLib.MainLoop().run, daemon=True, name="djerba-dbus").start()
            self.status = self._load_script(bus)
        except dbus.exceptions.DBusException as exc:
            self.status = f"D-Bus indisponible ({exc.get_dbus_name()})"
        return self

    @staticmethod
    def _load_script(bus) -> str:
        import dbus
        if not KWIN_SCRIPT.is_file():
            return "script KWin absent"
        try:
            scripting = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting"), "org.kde.kwin.Scripting")
            plugin = "djerba-active-window"
            if scripting.isScriptLoaded(plugin):
                scripting.unloadScript(plugin)
            scripting.loadScript(str(KWIN_SCRIPT), plugin)
            scripting.start()
        except dbus.exceptions.DBusException as exc:
            return f"KWin injoignable ({exc.get_dbus_name()})"
        return "actif"


# ---------------------------------------------------------------- Bus
class ContextBus:
    def __init__(self, interval: float = 5, titles: bool = True, box: Box | None = None,
                 path: Path | None = None) -> None:
        self.interval = interval
        self.box = box or Box()
        self.path = path or bus_file()
        self.sampler = Sampler()
        self.bridge = WindowBridge(titles)
        self.sequence = 0
        self.errors: list[dict] = []
        self.errors_checked = 0.0
        self.stop = threading.Event()

    def snapshot(self) -> dict:
        self.sequence += 1
        if time.monotonic() - self.errors_checked > 30:
            self.errors = journal_errors()
            self.errors_checked = time.monotonic()
        return {"sequence": self.sequence, "time": stamp(), "epoch": time.time(), "monotonic": time.monotonic(),
                "boot_id": _boot_id(), "metrics": self.sampler.sample(), "window": self.bridge.window,
                "window_status": self.bridge.status, "journal_errors": self.errors}

    def publish(self) -> dict:
        snapshot = self.snapshot()
        write_atomic(self.path, self.box.encrypt_bytes(json.dumps(snapshot).encode(), aad="context"))
        return snapshot

    def run(self) -> None:
        self.bridge.start()
        self.sampler.sample()
        while not self.stop.wait(self.interval):
            try:
                self.publish()
            except OSError:
                continue

    def start(self) -> "ContextBus":
        threading.Thread(target=self.run, daemon=True, name="djerba-contextbus").start()
        return self


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def read(path: Path | None = None, box: Box | None = None) -> dict | None:
    try:
        raw = (box or Box()).decrypt_bytes((path or bus_file()).read_bytes(), aad="context")
        snapshot = json.loads(raw)
    except (OSError, CryptoError, ValueError):
        return None
    if snapshot.get("boot_id") and snapshot["boot_id"] != _boot_id():
        return None  # instantané d'un démarrage précédent
    return snapshot


def _size(value: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= 1024
    return f"{value:.1f} Po"


def context_block(max_lines: int = 40, path: Path | None = None) -> str:
    """Texte injecté dans l'invite système (vide si le bus ne tourne pas)."""
    snapshot = read(path)
    if not snapshot:
        return ""
    age = max(0, int(time.time() - snapshot.get("epoch", 0)))
    metrics = snapshot.get("metrics", {})
    memory = metrics.get("memory", {})
    used = memory.get("total", 0) - memory.get("available", 0)
    lines = [f"Bus de contexte (instantané n°{snapshot['sequence']} du {snapshot['time']}, il y a {age} s"
             + (" — PÉRIMÉ : ne le présente pas comme l'état actuel" if age > STALE_AFTER else "") + ") :"]
    if "cpu_percent" in metrics:
        lines.append(f"- processeur {metrics['cpu_percent']} % (charge {metrics['load']:.2f}), mémoire "
                     f"{_size(used)} / {_size(memory.get('total', 0))}, E/S disque lecture "
                     f"{_size(metrics['disk_read_bps'])}/s écriture {_size(metrics['disk_write_bps'])}/s, réseau "
                     f"↓ {_size(metrics['net_rx_bps'])}/s ↑ {_size(metrics['net_tx_bps'])}/s")
    window = snapshot.get("window") or {}
    if window:
        lines.append(f"- fenêtre active : {window.get('application') or '?'}"
                     + (f" — « {window['title']} »" if window.get("title") else "") + f" (depuis {window['time']})")
    for error in snapshot.get("journal_errors", [])[-5:]:
        lines.append(f"- erreur du journal {error['time']} [{error['source']}] : {error['message']}")
    return "\n".join(lines[:max_lines])
