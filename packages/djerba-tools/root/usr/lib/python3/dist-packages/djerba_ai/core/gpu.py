# SPDX-License-Identifier: GPL-3.0-or-later
"""Détection du rendu graphique : accélération 3D ou repli 2D vectoriel.

Les pilotes d'affichage sans accélération 3D fiable (machines virtuelles,
framebuffer de secours) imposent le rendu logiciel de Qt Quick : l'orbe 3D
est alors remplacé par sa version 2D vectorielle.
"""

from __future__ import annotations

import os
from pathlib import Path

DRM = Path(os.environ.get("DJERBA_DRM_ROOT", "/sys/class/drm"))
SOFTWARE_DRIVERS = {"simpledrm", "efifb", "vesafb", "bochs", "bochs-drm", "cirrus", "cirrus-qemu", "qxl", "vmwgfx",
                    "virtio_gpu", "virtio-pci", "vboxvideo", "hyperv_drm", "hyperv_fb", "udl", "gm12u320", "vkms",
                    "mgag200", "ast"}


def drivers() -> list[str]:
    found = []
    for card in sorted(DRM.glob("card[0-9]*")):
        if "-" in card.name:  # connecteurs (card0-HDMI-A-1…)
            continue
        try:
            found.append(os.path.basename(os.readlink(card / "device" / "driver")))
        except OSError:
            continue
    return found


def detect() -> dict:
    if os.environ.get("LIBGL_ALWAYS_SOFTWARE") == "1" or os.environ.get("QT_QUICK_BACKEND") == "software":
        return {"accelerated": False, "drivers": drivers(), "reason": "rendu logiciel demandé"}
    found = drivers()
    accelerated = [name for name in found if name not in SOFTWARE_DRIVERS]
    if not found:
        return {"accelerated": False, "drivers": [], "reason": "aucun processeur graphique détecté"}
    if not accelerated:
        return {"accelerated": False, "drivers": found,
                "reason": f"pilote sans accélération 3D fiable ({', '.join(found)})"}
    return {"accelerated": True, "drivers": found, "reason": f"accélération 3D ({', '.join(accelerated)})"}
