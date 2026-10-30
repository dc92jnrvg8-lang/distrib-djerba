#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Djerba OS — captures de l'interface de l'agent (vraie interface QML, vrai backend).

Rendu hors écran (QT_QPA_PLATFORM=offscreen, rendu logiciel) : ni compositeur ni flou,
c'est-à-dire le cas défavorable (session X11 sans flou). La fenêtre est composée sur un
fond d'écran neutre, comme sur le bureau. Les discussions sont des données de
démonstration écrites dans un HOME jetable.

Usage : gui-captures.py DOSSIER [--theme sombre|clair] [--busy] [--size LxH] [--page N] [--name NOM]
"""

from __future__ import annotations

import argparse
import os
import time
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("DJERBA_AI_NO_TRAY", "1")

TITLES = ["pourquoi le filtre de couleur ne fonctionne pas", "installer redshift", "thème sombre plus doux",
          "jeu du pendu en français", "copier-coller entre le Mac et la VM", "nettoyer le disque",
          "mettre à jour le système", "réseau wifi lent", "icônes Windows 11", "sauvegarde de mes photos",
          "pourquoi Firefox est lent", "raccourci pour capture d'écran"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--theme", choices=("sombre", "clair"), default="sombre")
    parser.add_argument("--busy", action="store_true")
    parser.add_argument("--size", default="1280x840")
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--name", default="")
    arguments = parser.parse_args()
    home = Path(tempfile.mkdtemp(prefix="djerba-captures-"))
    (home / ".config").mkdir()
    scheme = "DjerbaSombre" if arguments.theme == "sombre" else "DjerbaClair"
    (home / ".config" / "kdeglobals").write_text(f"[General]\nColorScheme={scheme}\n")
    os.environ["HOME"] = str(home)
    os.environ.setdefault("QT_QPA_PLATFORMTHEME", "kde")
    os.environ.setdefault("XDG_CURRENT_DESKTOP", "KDE")

    from PyQt6.QtCore import QObject, QTimer, QUrl
    from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter
    from PyQt6.QtQml import QQmlApplicationEngine
    from PyQt6.QtWidgets import QApplication

    from djerba_ai.core.memory import Memory
    from djerba_ai.gui import app as gui

    memory = Memory()
    for title in TITLES:
        conversation = memory.new_conversation(title)
        memory.add_message(conversation, "user", title)
    application = QApplication(sys.argv[:1])
    backend = gui.Backend("chat", True, application)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("icon", gui.IconProvider())
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(gui.QML_DIR / "main.qml")))
    window = engine.rootObjects()[0]
    width, height = (int(value) for value in arguments.size.split("x"))
    window.setProperty("width", width)
    window.setProperty("height", height)
    window.setProperty("page", arguments.page)
    onboarding = window.findChild(QObject, "onboarding")
    if onboarding is not None:
        onboarding.setProperty("visible", False)  # écran d'accueil du premier lancement : hors sujet ici

    def capture() -> None:
        if arguments.busy:
            backend._busy = True
            backend._busy_since = time.time() - 12  # travail commencé il y a 12 s
            backend.busyChanged.emit()
            backend.stateChanged.emit()
            if hasattr(backend, "_activity"):
                backend._set_activity("agit", "Recherche sur Internet (DuckDuckGo)")
        QTimer.singleShot(1200, finish)

    def finish() -> None:
        frame = window.grabWindow()
        desktop = QImage(frame.size(), QImage.Format.Format_ARGB32)
        painter = QPainter(desktop)
        gradient = QLinearGradient(0, 0, frame.width(), frame.height())
        gradient.setColorAt(0, QColor("#4a6378"))
        gradient.setColorAt(1, QColor("#c9b48a"))
        painter.fillRect(desktop.rect(), gradient)  # fond d'écran : la fenêtre translucide laisse passer le bureau
        painter.drawImage(0, 0, frame)
        painter.end()
        arguments.output.mkdir(parents=True, exist_ok=True)
        name = arguments.name or f"{arguments.theme}-{'travail' if arguments.busy else 'repos'}-{arguments.size}"
        path = arguments.output / f"{name}.png"
        desktop.save(str(path))
        print(path)
        application.exit(0)

    QTimer.singleShot(2500, capture)
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
