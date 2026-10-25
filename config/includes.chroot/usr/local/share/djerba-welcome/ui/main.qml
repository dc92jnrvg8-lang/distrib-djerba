// SPDX-License-Identifier: GPL-3.0-or-later
// Djerba OS — assistant d'accueil (Qt 6.4 / PyQt6).
// Verre dépoli (Qt5Compat.GraphicalEffects), particules d'écume
// (QtQuick.Particles) et animations pilotées par le moteur de rendu (60 i/s).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Particles
import Qt5Compat.GraphicalEffects

ApplicationWindow {
    id: win

    width: 1180
    height: 780
    minimumWidth: 960
    minimumHeight: 700
    visible: true
    title: qsTr("Bienvenue dans Djerba OS")
    color: palette_.night

    QtObject {
        id: palette_
        readonly property color night: "#070B18"
        readonly property color deep: "#0A1F3D"
        readonly property color lagoon: "#0E5E7E"
        readonly property color turquoise: "#00C2CB"
        readonly property color aqua: "#5EEAD4"
        readonly property color sand: "#F4E1C1"
        readonly property color coral: "#FF7A59"
        readonly property color white: "#F8FAFC"
        readonly property color muted: "#94A3B8"
    }

    font.family: "Inter"

    // ================================================================ Paysage
    Item {
        id: scenery
        anchors.fill: parent

        Item {
            id: sceneryContent
            anchors.fill: parent

            Rectangle {
                anchors.fill: parent
                gradient: Gradient {
                    GradientStop { position: 0.0; color: palette_.night }
                    GradientStop { position: 0.6; color: palette_.deep }
                    GradientStop { position: 1.0; color: palette_.lagoon }
                }
            }

            Image {
                id: wallpaper
                anchors.fill: parent
                source: backend.wallpaper
                fillMode: Image.PreserveAspectCrop
                asynchronous: false
                smooth: true
                visible: source !== ""
            }
        }

        // Flou calculé une seule fois (contenu statique) puis réutilisé par
        // chaque panneau de verre : aucun recalcul pendant les animations.
        FastBlur {
            id: blurredScenery
            anchors.fill: parent
            source: sceneryContent
            radius: 64
            cached: true
            visible: false
        }

        transformOrigin: Item.Center
        SequentialAnimation on scale {
            loops: Animation.Infinite
            NumberAnimation { from: 1.0; to: 1.07; duration: 26000; easing.type: Easing.InOutSine }
            NumberAnimation { from: 1.07; to: 1.0; duration: 26000; easing.type: Easing.InOutSine }
        }
    }

    // ================================================================ Écume
    ParticleSystem {
        id: foam
        anchors.fill: parent
    }

    ImageParticle {
        system: foam
        source: backend.assetsUrl + "particle.png"
        color: palette_.aqua
        colorVariation: 0.2
        alpha: 0.45
        alphaVariation: 0.3
        entryEffect: ImageParticle.Fade
    }

    Emitter {
        system: foam
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: parent.height * 0.3
        emitRate: 9
        lifeSpan: 12000
        lifeSpanVariation: 3000
        size: 16
        sizeVariation: 12
        endSize: 4
        velocity: AngleDirection {
            angle: 270
            angleVariation: 14
            magnitude: 36
            magnitudeVariation: 18
        }
    }

    Wander {
        system: foam
        anchors.fill: parent
        xVariance: 40
        pace: 30
    }

    // ================================================================ Composants
    component GlassPanel: Item {
        id: panel

        default property alias content: inner.data
        property real radius: 26
        property real tint: 0.10
        property int padding: 26

        ShaderEffectSource {
            id: backdrop
            anchors.fill: parent
            sourceItem: blurredScenery
            visible: false
            live: true
            sourceRect: {
                // Dépendances explicites : échelle animée du paysage et taille.
                scenery.scale
                panel.width
                panel.height
                var topLeft = panel.mapToItem(blurredScenery, 0, 0)
                var bottomRight = panel.mapToItem(blurredScenery, panel.width, panel.height)
                return Qt.rect(topLeft.x, topLeft.y, bottomRight.x - topLeft.x, bottomRight.y - topLeft.y)
            }
        }

        Rectangle {
            id: mask
            anchors.fill: parent
            radius: panel.radius
            visible: false
        }

        OpacityMask {
            anchors.fill: parent
            source: backdrop
            maskSource: mask
        }

        Rectangle {
            anchors.fill: parent
            radius: panel.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, panel.tint + 0.05) }
                GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, panel.tint - 0.05) }
            }
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.20)
        }

        // Reflet supérieur du verre.
        Rectangle {
            x: panel.radius
            y: 1
            width: parent.width - 2 * panel.radius
            height: 1
            color: Qt.rgba(1, 1, 1, 0.35)
        }

        Item {
            id: inner
            anchors.fill: parent
            anchors.margins: panel.padding
        }
    }

    component PrimaryButton: Button {
        id: control
        property color from: palette_.turquoise
        property color to: palette_.aqua
        property color labelColor: palette_.night
        implicitHeight: 56
        implicitWidth: Math.max(220, contentItem.implicitWidth + 56)
        font.pixelSize: 17
        font.weight: Font.DemiBold
        scale: down ? 0.97 : (hovered ? 1.03 : 1.0)
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        contentItem: Text {
            text: control.text
            font: control.font
            color: control.labelColor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Item {
            RectangularGlow {
                anchors.fill: fill
                glowRadius: control.hovered ? 22 : 12
                spread: 0.2
                color: control.from
                cornerRadius: fill.radius + glowRadius
                opacity: control.enabled ? 0.55 : 0.0
                Behavior on glowRadius { NumberAnimation { duration: 200 } }
            }
            Rectangle {
                id: fill
                anchors.fill: parent
                radius: 18
                opacity: control.enabled ? 1.0 : 0.4
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: control.from }
                    GradientStop { position: 1.0; color: control.to }
                }
            }
        }
    }

    component GhostButton: Button {
        id: ghost
        implicitHeight: 48
        implicitWidth: contentItem.implicitWidth + 44
        font.pixelSize: 15
        contentItem: Text {
            text: ghost.text
            font: ghost.font
            color: palette_.white
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 16
            color: Qt.rgba(1, 1, 1, ghost.down ? 0.20 : (ghost.hovered ? 0.14 : 0.07))
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, ghost.hovered ? 0.35 : 0.18)
            Behavior on color { ColorAnimation { duration: 140 } }
        }
    }

    // ================================================================ Contenu
    ColumnLayout {
        id: page
        anchors.fill: parent
        anchors.margins: 40
        spacing: 22

        opacity: 0
        transform: Translate { id: pageShift; y: 24 }
        Component.onCompleted: entrance.start()
        ParallelAnimation {
            id: entrance
            NumberAnimation { target: page; property: "opacity"; to: 1; duration: 700; easing.type: Easing.OutCubic }
            NumberAnimation { target: pageShift; property: "y"; to: 0; duration: 900; easing.type: Easing.OutExpo }
        }

        // ------------------------------------------------------------ En-tête
        RowLayout {
            Layout.fillWidth: true
            spacing: 20

            Image {
                id: logo
                Layout.preferredWidth: 76
                Layout.preferredHeight: 76
                source: backend.assetsUrl + "logo.svg"
                sourceSize.width: 152
                sourceSize.height: 152
                smooth: true
                SequentialAnimation on rotation {
                    loops: Animation.Infinite
                    NumberAnimation { from: -2; to: 2; duration: 3200; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 2; to: -2; duration: 3200; easing.type: Easing.InOutSine }
                }
            }

            ColumnLayout {
                spacing: 2
                Text {
                    text: qsTr("Bienvenue sur ") + "<b>Djerba</b> <font color='" + palette_.turquoise + "'>OS</font>"
                    textFormat: Text.StyledText
                    color: palette_.white
                    font.pixelSize: 36
                    font.weight: Font.Light
                }
                Text {
                    text: backend.edition + " · " + qsTr("Léger, sécurisé et taillé pour le jeu")
                    color: palette_.muted
                    font.pixelSize: 15
                }
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                Layout.preferredHeight: 34
                Layout.preferredWidth: netRow.implicitWidth + 28
                radius: 17
                color: Qt.rgba(1, 1, 1, 0.08)
                border.width: 1
                border.color: Qt.rgba(1, 1, 1, 0.16)
                Row {
                    id: netRow
                    anchors.centerIn: parent
                    spacing: 8
                    Rectangle {
                        width: 10
                        height: 10
                        radius: 5
                        anchors.verticalCenter: parent.verticalCenter
                        color: backend.online ? palette_.aqua : palette_.coral
                        SequentialAnimation on opacity {
                            loops: Animation.Infinite
                            NumberAnimation { from: 1.0; to: 0.35; duration: 900 }
                            NumberAnimation { from: 0.35; to: 1.0; duration: 900 }
                        }
                    }
                    Text {
                        text: backend.online ? qsTr("Connecté à Internet") : qsTr("Hors ligne")
                        color: palette_.white
                        font.pixelSize: 13
                    }
                }
            }
        }

        // ------------------------------------------------------------ Corps
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 22

            GlassPanel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                tint: 0.09

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 18

                    Text {
                        Layout.fillWidth: true
                        text: backend.isLive
                              ? qsTr("Votre île numérique vous attend.")
                              : qsTr("Votre système est prêt.")
                        color: palette_.white
                        font.pixelSize: 30
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: backend.isLive
                              ? qsTr("Vous utilisez Djerba OS depuis le média live : rien n'est modifié sur cet ordinateur. Installez-le en quelques minutes sur un disque BTRFS compressé, avec noyau à faible latence, GameMode et Flathub prêts à l'emploi.")
                              : qsTr("Retrouvez vos jeux et applications, installez de nouveaux logiciels depuis Flathub avec Discover, et profitez du noyau à faible latence optimisé pour le jeu.")
                        color: palette_.muted
                        font.pixelSize: 16
                        lineHeight: 1.3
                        wrapMode: Text.WordWrap
                    }

                    Item { Layout.fillHeight: true }

                    RowLayout {
                        spacing: 14
                        PrimaryButton {
                            visible: backend.isLive
                            enabled: !backend.installerRunning
                            text: backend.installerRunning ? qsTr("Installation en cours…") : qsTr("Installer Djerba OS")
                            onClicked: backend.launchInstaller()
                        }
                        PrimaryButton {
                            visible: !backend.isLive
                            from: palette_.coral
                            to: palette_.sand
                            text: qsTr("Explorer Flathub")
                            onClicked: backend.launchApp("discover")
                        }
                        GhostButton {
                            text: backend.isLive ? qsTr("Continuer à essayer") : qsTr("Fermer")
                            onClicked: win.close()
                        }
                    }
                }
            }

            GlassPanel {
                Layout.preferredWidth: 380
                Layout.fillHeight: true
                tint: 0.07

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8

                    Text {
                        text: qsTr("Votre machine")
                        color: palette_.white
                        font.pixelSize: 19
                        font.weight: Font.DemiBold
                    }

                    Repeater {
                        model: backend.systemInfo
                        delegate: Rectangle {
                            id: infoRow
                            Layout.fillWidth: true
                            Layout.preferredHeight: 50
                            radius: 14
                            color: Qt.rgba(1, 1, 1, hover.hovered ? 0.10 : 0.05)
                            Behavior on color { ColorAnimation { duration: 150 } }
                            opacity: 0
                            Component.onCompleted: appear.start()
                            NumberAnimation {
                                id: appear
                                target: infoRow
                                property: "opacity"
                                to: 1
                                duration: 500
                                easing.type: Easing.OutCubic
                            }
                            HoverHandler { id: hover }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 14
                                anchors.rightMargin: 14
                                spacing: 12
                                Image {
                                    Layout.preferredWidth: 28
                                    Layout.preferredHeight: 28
                                    source: "image://icon/" + modelData.icon
                                    sourceSize.width: 56
                                    sourceSize.height: 56
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    Text {
                                        text: modelData.label
                                        color: palette_.muted
                                        font.pixelSize: 12
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.value
                                        color: palette_.white
                                        font.pixelSize: 14
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }
                }
            }
        }

        // ------------------------------------------------------------ Accès rapide
        GlassPanel {
            Layout.fillWidth: true
            Layout.preferredHeight: 128
            padding: 18
            tint: 0.08

            RowLayout {
                anchors.fill: parent
                spacing: 16

                Repeater {
                    model: backend.quickApps
                    delegate: Rectangle {
                        id: tile
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 18
                        enabled: modelData.available
                        opacity: enabled ? 1.0 : 0.4
                        color: Qt.rgba(1, 1, 1, tileHover.hovered ? 0.13 : 0.05)
                        border.width: 1
                        border.color: Qt.rgba(1, 1, 1, tileHover.hovered ? 0.30 : 0.10)
                        scale: tileTap.pressed ? 0.96 : (tileHover.hovered ? 1.04 : 1.0)
                        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutBack } }
                        Behavior on color { ColorAnimation { duration: 160 } }

                        HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { id: tileTap; onTapped: backend.launchApp(modelData.id) }

                        Row {
                            anchors.centerIn: parent
                            spacing: 14
                            Image {
                                width: 44
                                height: 44
                                anchors.verticalCenter: parent.verticalCenter
                                source: "image://icon/" + modelData.icon
                                sourceSize.width: 88
                                sourceSize.height: 88
                            }
                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                Text {
                                    text: modelData.label
                                    color: palette_.white
                                    font.pixelSize: 16
                                    font.weight: Font.DemiBold
                                }
                                Text {
                                    text: modelData.available ? qsTr("Ouvrir") : qsTr("Indisponible")
                                    color: palette_.muted
                                    font.pixelSize: 12
                                }
                            }
                        }
                    }
                }
            }
        }

        // ------------------------------------------------------------ Pied de page
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            CheckBox {
                id: autostartBox
                checked: backend.autostart
                onToggled: backend.autostart = checked
                text: qsTr("Afficher au démarrage")
                font.pixelSize: 13
                contentItem: Text {
                    leftPadding: autostartBox.indicator.width + 10
                    text: autostartBox.text
                    font: autostartBox.font
                    color: palette_.muted
                    verticalAlignment: Text.AlignVCenter
                }
                indicator: Rectangle {
                    implicitWidth: 20
                    implicitHeight: 20
                    y: (autostartBox.height - height) / 2
                    radius: 6
                    color: autostartBox.checked ? palette_.turquoise : Qt.rgba(1, 1, 1, 0.08)
                    border.width: 1
                    border.color: Qt.rgba(1, 1, 1, 0.3)
                    Behavior on color { ColorAnimation { duration: 140 } }
                    Text {
                        anchors.centerIn: parent
                        text: "✓"
                        visible: autostartBox.checked
                        color: palette_.night
                        font.pixelSize: 13
                        font.bold: true
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Text {
                text: "<a href='project'>" + qsTr("Projet Djerba OS") + "</a>"
                textFormat: Text.StyledText
                linkColor: palette_.turquoise
                font.pixelSize: 13
                onLinkActivated: backend.openProject()
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }
    }

    // ================================================================ Notification
    Rectangle {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 28
        width: toastText.implicitWidth + 40
        height: 44
        radius: 22
        color: Qt.rgba(0.04, 0.12, 0.24, 0.92)
        border.width: 1
        border.color: palette_.turquoise
        opacity: 0
        visible: opacity > 0
        Text {
            id: toastText
            anchors.centerIn: parent
            color: palette_.white
            font.pixelSize: 14
        }
        SequentialAnimation {
            id: toastAnimation
            NumberAnimation { target: toast; property: "opacity"; to: 1; duration: 200 }
            PauseAnimation { duration: 3200 }
            NumberAnimation { target: toast; property: "opacity"; to: 0; duration: 400 }
        }
    }

    Connections {
        target: backend
        function onNotify(message) {
            toastText.text = message
            toastAnimation.restart()
        }
    }
}
