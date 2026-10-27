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
GUI_FLAGS = ("--spotlight", "--clipboard", "--tray", "--page")


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
    if "--spotlight" in argv:
        mode = "spotlight"
    elif "--clipboard" in argv:
        mode = "clipboard"
    elif "--tray" in argv:
        mode = "tray"
    else:
        mode = "window"
    return {"mode": mode, "page": "settings" if page == "models" else page}
