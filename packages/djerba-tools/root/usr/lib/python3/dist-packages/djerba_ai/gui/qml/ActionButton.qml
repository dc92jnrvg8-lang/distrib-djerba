// SPDX-License-Identifier: GPL-3.0-or-later
// Composant partagé de l'interface de Djerba (couleurs : palette adaptative de main.qml).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: action
    property bool primary: false
    property color accent: win.turquoise
    implicitHeight: 38
    font.pixelSize: 13
    contentItem: Text {
        text: action.text; font: action.font
        color: action.primary ? win.night : (action.enabled ? win.white : win.muted)
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        radius: 12
        color: action.primary ? (action.down ? Qt.darker(action.accent, 1.2) : action.accent)
                              : win.veil(action.down ? 0.18 : (action.hovered ? 0.13 : 0.07))
        opacity: action.enabled ? 1 : 0.45
        border.width: action.primary ? 0 : 1
        border.color: win.veil(0.18)
        Behavior on color { ColorAnimation { duration: 120 } }
    }
}
