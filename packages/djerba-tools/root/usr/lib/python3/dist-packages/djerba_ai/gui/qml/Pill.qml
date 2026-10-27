// SPDX-License-Identifier: GPL-3.0-or-later
// Composant partagé de l'interface de Djerba (couleurs : palette adaptative de main.qml).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: pill
    property string text
    property color tint: win.turquoise
    radius: 10
    implicitHeight: 22
    implicitWidth: pillText.implicitWidth + 16
    color: Qt.rgba(tint.r, tint.g, tint.b, 0.18)
    Text { id: pillText; anchors.centerIn: parent; text: pill.text; color: win.white; font.pixelSize: 11 }
}
