# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance unique de l'interface de Djerba (sans dépendance à Qt).

L'application résidente (icône de la zone de notification) écoute sur
$XDG_RUNTIME_DIR/djerba-ai/gui.sock (0600) ; un nouveau lancement
(« djerba-ai --spotlight » sur Meta+Espace, « --clipboard », « --page … »)
lui transmet sa demande et se termine aussitôt : la barre Spotlight s'ouvre
sans relancer Python ni Qt.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
SOCKET = RUNTIME / "djerba-ai" / "gui.sock"
GUI_FLAGS = ("--hud", "--spotlight", "--clipboard", "--tray", "--page", "--ask", "--ask-file", "--ask-selection")


def forward(argv: list[str], path: Path = SOCKET, timeout: float = 1.5) -> bool:
    """Transmet la demande à l'instance en cours ; False s'il n'y en a pas."""
    if not path.exists():
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(json.dumps({"argv": argv}).encode() + b"\n")
        return client.recv(16).startswith(b"ok")
    except OSError:
        return False
    finally:
        client.close()


def parse(argv: list[str]) -> dict:
    page = "chat"
    if "--page" in argv and argv.index("--page") + 1 < len(argv):
        page = argv[argv.index("--page") + 1]
    text = ""
    if "--hud" in argv:
        mode = "hud"
    elif "--ask" in argv and argv.index("--ask") + 1 < len(argv):
        mode, text = "ask", argv[argv.index("--ask") + 1][:16000]
    elif "--spotlight" in argv:
        mode = "spotlight"
    elif "--clipboard" in argv:
        mode = "clipboard"
    elif "--tray" in argv:
        mode = "tray"
    else:
        mode = "window"
    return {"mode": mode, "page": "settings" if page == "models" else page, "text": text}


def request_from_application(argv: list[str], stdin_text: str = "") -> list[str]:
    """Demandes venues des applications (Dolphin, Nautilus, Kate, Gedit) → « --ask TEXTE ».

    --ask-file ACTION CHEMIN…        action contextuelle sur des fichiers
    --ask-selection CONSIGNE NOM     consigne sur la sélection (lue sur l'entrée standard)
    """
    if "--ask-file" in argv:
        index = argv.index("--ask-file")
        action, paths = (argv[index + 1:index + 2] or [""])[0], argv[index + 2:index + 22]
        listing = "\n".join(f"- {path}" for path in paths)
        return ["--ask", f"{action} :\n{listing}"]
    if "--ask-selection" in argv:
        index = argv.index("--ask-selection")
        instruction = (argv[index + 1:index + 2] or ["Explique"])[0]
        name = (argv[index + 2:index + 3] or [""])[0]
        selection = stdin_text.strip()[:12000] or "(aucune sélection)"
        return ["--ask", f"{instruction}" + (f" (fichier {name})" if name else "") + f" :\n\n« {selection} »"]
    return argv
