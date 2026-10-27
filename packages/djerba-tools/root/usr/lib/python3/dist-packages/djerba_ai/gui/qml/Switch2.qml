// SPDX-License-Identifier: GPL-3.0-or-later
// Composant partagé de l'interface de Djerba (couleurs : palette adaptative de main.qml).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: sw
    property bool checked: false
    signal toggled()
    implicitWidth: 52; implicitHeight: 28
    Rectangle {
        anchors.fill: parent; radius: height / 2
        color: sw.checked ? win.turquoise : win.veil(0.15)
        Behavior on color { ColorAnimation { duration: 160 } }
    }
    Rectangle {
        width: parent.height - 8; height: width; radius: width / 2; y: 4
        x: sw.checked ? parent.width - width - 4 : 4
        color: win.white
        Behavior on x { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
    }
    TapHandler { onTapped: sw.toggled() }
}
