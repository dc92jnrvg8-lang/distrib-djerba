// SPDX-License-Identifier: GPL-3.0-or-later
// Composant partagé de l'interface de Djerba (couleurs : palette adaptative de main.qml).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    radius: 22
    // Panneau opaque par défaut (jeton « surface ») : lisible même sans flou (X11, VM).
    color: Qt.rgba(win.surface.r, win.surface.g, win.surface.b, win.panelOpacity)
    border.width: 1
    border.color: win.veil(0.13)
}
