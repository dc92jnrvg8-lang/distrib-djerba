// SPDX-License-Identifier: GPL-3.0-or-later
// Composant partagé de l'interface de Djerba (couleurs : palette adaptative de main.qml).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    radius: 22
    color: win.veil(0.06)
    border.width: 1
    border.color: win.veil(0.13)
}
