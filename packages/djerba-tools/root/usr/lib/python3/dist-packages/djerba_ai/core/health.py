# SPDX-License-Identifier: GPL-3.0-or-later
"""Bilan de santé du système (sans droits root), partagé par l'outil
health_report, la maintenance quotidienne et l'application."""

from __future__ import annotations

import shutil
import subprocess


def _run(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess | None:
    if shutil.which(argv[0]) is None:
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def report() -> dict:
    """Retourne {"score": 0-100, "items": [{"level": ok|warning|error, "title", "detail", "tool"}]}."""
    items = []

    def add(level: str, title: str, detail: str, tool: str = "") -> None:
        items.append({"level": level, "title": title, "detail": detail, "tool": tool})

    failed = _run(["systemctl", "--failed", "--no-legend", "--plain"])
    units = [line.split()[0] for line in (failed.stdout.splitlines() if failed else []) if line.strip()]
    if units:
        add("error", f"{len(units)} service(s) en échec", ", ".join(units[:6]), "failed_units")
    else:
        add("ok", "Services", "aucun service en échec")

    usage = shutil.disk_usage("/")
    percent = usage.used * 100 // usage.total
    level = "error" if percent >= 95 else "warning" if percent >= 85 else "ok"
    add(level, "Espace disque", f"{percent} % utilisés, {usage.free / 1e9:.1f} Go libres",
        "largest_dirs" if level != "ok" else "")

    upgradable = _run(["apt", "list", "--upgradable"])
    lines = [line for line in (upgradable.stdout.splitlines() if upgradable else []) if "/" in line]
    security = [line for line in lines if "-security" in line]
    if security:
        add("warning", f"{len(security)} mise(s) à jour de sécurité", "à installer rapidement", "apt_full_upgrade")
    elif lines:
        add("warning", f"{len(lines)} mise(s) à jour disponible(s)", "aucune n'est urgente", "apt_full_upgrade")
    else:
        add("ok", "Mises à jour", "système à jour (selon les dernières listes)")

    errors = _run(["journalctl", "-p", "err", "-b", "--no-pager", "-q", "-o", "cat"])
    count = len(errors.stdout.splitlines()) if errors and errors.returncode == 0 else 0
    if count > 20:
        add("warning", f"{count} erreurs dans le journal depuis le démarrage", "voir le détail", "journal_errors")
    else:
        add("ok", "Journal", f"{count} erreur(s) depuis le démarrage")

    memory = _run(["free", "-m"])
    if memory:
        fields = memory.stdout.splitlines()[1].split()
        total, available = int(fields[1]), int(fields[-1])
        level = "warning" if available < total * 0.1 else "ok"
        add(level, "Mémoire", f"{available} Mio disponibles sur {total} Mio", "top_memory" if level != "ok" else "")

    pending = _run(["djerba-apps", "status", "--json"])
    if pending and '"install": []' not in pending.stdout and '"install"' in pending.stdout:
        add("warning", "Applications en attente", "installation prévue dès que le réseau est disponible",
            "apps_retry_pending")

    penalty = sum(25 if item["level"] == "error" else 8 if item["level"] == "warning" else 0 for item in items)
    return {"score": max(0, 100 - penalty), "items": items}
