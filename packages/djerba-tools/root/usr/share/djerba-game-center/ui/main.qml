// SPDX-License-Identifier: GPL-3.0-or-later
// Djerba Game Center — réglages jeu (Qt 6.8). Même langage visuel que
// l'assistant : nuit/turquoise, cartes de verre, interrupteurs animés.
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    width: 1100
    height: 760
    minimumWidth: 900
    minimumHeight: 620
    visible: true
    title: "Djerba Game Center"
    color: "#070B18"
    font.family: "Inter"

    readonly property color night: "#070B18"
    readonly property color turquoise: "#00C2CB"
    readonly property color aqua: "#5EEAD4"
    readonly property color coral: "#FF7A59"
    readonly property color sand: "#F4E1C1"
    readonly property color white: "#F8FAFC"
    readonly property color muted: "#94A3B8"
    property int page: 0
    property string protonMessage: ""
    property int protonPercent: 0

    background: Rectangle {
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#070B18" }
            GradientStop { position: 0.55; color: "#0A1F3D" }
            GradientStop { position: 1.0; color: "#1A0F2E" }
        }
        // Halo pulsant, plus vif quand le mode jeu est actif.
        Rectangle {
            width: parent.width * 0.9; height: width; radius: width / 2
            x: parent.width * 0.45; y: -height * 0.45
            color: backend.gameMode ? win.coral : win.turquoise
            opacity: 0.10
            Behavior on color { ColorAnimation { duration: 600 } }
            SequentialAnimation on opacity {
                loops: Animation.Infinite
                NumberAnimation { to: 0.16; duration: 2600; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.08; duration: 2600; easing.type: Easing.InOutSine }
            }
        }
    }

    component Card: Rectangle {
        default property alias content: inner.data
        radius: 20
        color: Qt.rgba(1, 1, 1, 0.06)
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 0.12)
        implicitHeight: inner.childrenRect.height + 36
        Item { id: inner; anchors.fill: parent; anchors.margins: 18 }
    }

    component Toggle: Item {
        id: toggle
        property bool checked: false
        signal toggled()
        implicitWidth: 58; implicitHeight: 32
        Rectangle {
            anchors.fill: parent; radius: height / 2
            color: toggle.checked ? win.turquoise : Qt.rgba(1, 1, 1, 0.14)
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.25)
            Behavior on color { ColorAnimation { duration: 180 } }
        }
        Rectangle {
            width: parent.height - 8; height: width; radius: width / 2; y: 4
            x: toggle.checked ? parent.width - width - 4 : 4
            color: win.white
            Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
        }
        TapHandler { onTapped: toggle.toggled() }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }

    component OptionRow: RowLayout {
        id: optionRow
        property string title
        property string description
        property string icon
        default property alias trailing: slot.data
        spacing: 16
        width: parent ? parent.width : 0
        Image {
            Layout.preferredWidth: 36; Layout.preferredHeight: 36
            source: "image://icon/" + optionRow.icon
            sourceSize.width: 72; sourceSize.height: 72
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text { text: optionRow.title; color: win.white; font.pixelSize: 16; font.weight: Font.DemiBold }
            Text { Layout.fillWidth: true; text: optionRow.description; color: win.muted; font.pixelSize: 13; wrapMode: Text.WordWrap }
        }
        Item { id: slot; implicitWidth: childrenRect.width; implicitHeight: childrenRect.height }
    }

    component Chip: Rectangle {
        id: chip
        property string label
        property bool selected: false
        signal clicked()
        radius: 14
        implicitHeight: 36
        implicitWidth: chipText.implicitWidth + 28
        color: selected ? Qt.rgba(0, 0.76, 0.8, 0.28) : Qt.rgba(1, 1, 1, chipHover.hovered ? 0.12 : 0.06)
        border.width: 1
        border.color: selected ? win.turquoise : Qt.rgba(1, 1, 1, 0.18)
        Behavior on color { ColorAnimation { duration: 150 } }
        Text { id: chipText; anchors.centerIn: parent; text: chip.label; color: win.white; font.pixelSize: 13 }
        HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: chip.clicked() }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 22

        // ------------------------------------------------------------ Navigation
        ColumnLayout {
            Layout.preferredWidth: 230
            Layout.fillHeight: true
            spacing: 8

            Text { text: "Game Center"; color: win.white; font.pixelSize: 26; font.weight: Font.Bold }
            Text { text: "Djerba OS"; color: win.turquoise; font.pixelSize: 14; Layout.bottomMargin: 18 }

            // Gros bouton « mode jeu »
            Rectangle {
                id: boost
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                radius: 24
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: backend.gameMode ? win.coral : Qt.rgba(1, 1, 1, 0.08) }
                    GradientStop { position: 1; color: backend.gameMode ? win.sand : Qt.rgba(1, 1, 1, 0.04) }
                }
                border.width: 1
                border.color: backend.gameMode ? win.sand : Qt.rgba(1, 1, 1, 0.2)
                scale: boostTap.pressed ? 0.96 : (boostHover.hovered ? 1.02 : 1.0)
                Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutBack } }
                HoverHandler { id: boostHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { id: boostTap; onTapped: backend.toggleGameMode() }
                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 4
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: backend.gameMode ? "MODE JEU ACTIF" : "ACTIVER LE MODE JEU"
                        color: backend.gameMode ? win.night : win.white
                        font.pixelSize: 15; font.weight: Font.Bold; font.letterSpacing: 1.2
                    }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: backend.gameMode ? "Performances maximales" : "Un clic, toute la puissance"
                        color: backend.gameMode ? win.night : win.muted
                        font.pixelSize: 12
                    }
                }
            }

            Item { Layout.preferredHeight: 10 }
            Repeater {
                model: ["Performances", "MangoHud", "Proton", "Lanceurs"]
                delegate: Rectangle {
                    required property string modelData
                    required property int index
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    radius: 14
                    color: win.page === index ? Qt.rgba(0, 0.76, 0.8, 0.22) : Qt.rgba(1, 1, 1, navHover.hovered ? 0.08 : 0)
                    border.width: win.page === index ? 1 : 0
                    border.color: Qt.rgba(0, 0.76, 0.8, 0.6)
                    Behavior on color { ColorAnimation { duration: 160 } }
                    HoverHandler { id: navHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: win.page = index }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        x: 16
                        text: modelData
                        color: win.white
                        font.pixelSize: 15
                        font.weight: win.page === index ? Font.DemiBold : Font.Normal
                    }
                }
            }
            Item { Layout.fillHeight: true }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: win.muted; font.pixelSize: 12
                text: backend.gamemoded ? "GameMode prêt : ajoutez « gamemoderun %command% » aux options de lancement Steam."
                                        : "GameMode n'est pas installé."
            }
        }

        // ------------------------------------------------------------ Pages
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.page

            // Performances
            ColumnLayout {
                spacing: 14
                Text { text: "Performances"; color: win.white; font.pixelSize: 28; font.weight: Font.DemiBold }
                Card {
                    Layout.fillWidth: true
                    ColumnLayout {
                        width: parent.width
                        spacing: 12
                        OptionRow {
                            title: "Profil d'énergie du processeur"
                            description: "Actuel : " + backend.powerProfile + ". « Performance » maximise fréquence et réactivité (power-profiles-daemon)."
                            icon: "speedometer"
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            Repeater {
                                model: [{ id: "performance", label: "Performance" }, { id: "balanced", label: "Équilibré" }, { id: "power-saver", label: "Économie" }]
                                delegate: Chip {
                                    required property var modelData
                                    label: modelData.label
                                    selected: backend.powerProfile === modelData.id
                                    opacity: backend.profiles.indexOf(modelData.id) >= 0 ? 1 : 0.4
                                    onClicked: backend.setProfile(modelData.id)
                                }
                            }
                        }
                    }
                }
                Card {
                    Layout.fillWidth: true
                    OptionRow {
                        title: "Effets visuels de KWin"
                        description: "Flou, contraste et animations de fenêtres. Les couper libère du GPU pour les jeux."
                        icon: "preferences-desktop-effects"
                        Toggle { checked: backend.effects; onToggled: backend.setEffects(!backend.effects) }
                    }
                }
                Card {
                    Layout.fillWidth: true
                    OptionRow {
                        title: "Latence minimale en plein écran"
                        description: "Autorise le tearing pour les jeux en plein écran (Wayland) : l'image est envoyée sans attendre la synchronisation verticale."
                        icon: "video-display"
                        Toggle { checked: backend.tearing; onToggled: backend.setTearing(!backend.tearing) }
                    }
                }
                Item { Layout.fillHeight: true }
            }

            // MangoHud
            ColumnLayout {
                spacing: 14
                Text { text: "MangoHud"; color: win.white; font.pixelSize: 28; font.weight: Font.DemiBold }
                Card {
                    Layout.fillWidth: true
                    OptionRow {
                        title: "Superposition pour tous les jeux"
                        description: "Images/s, temps d'image, températures et charge (Vulkan et OpenGL), à la prochaine connexion. Pour un seul jeu Steam : « mangohud %command% »."
                        icon: "utilities-system-monitor"
                        Toggle { checked: backend.mangohud; onToggled: backend.setMangohud(!backend.mangohud) }
                    }
                }
                Card {
                    Layout.fillWidth: true
                    ColumnLayout {
                        width: parent.width
                        spacing: 10
                        Text { text: "Affichage"; color: win.white; font.pixelSize: 16; font.weight: Font.DemiBold }
                        Row {
                            spacing: 8
                            Chip { label: "Compact"; selected: backend.mangohudPreset === "compact"; onClicked: backend.setMangohudPreset("compact") }
                            Chip { label: "Complet"; selected: backend.mangohudPreset === "complet"; onClicked: backend.setMangohudPreset("complet") }
                        }
                        Text { text: "Limite d'images par seconde"; color: win.white; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 8 }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            Repeater {
                                model: [0, 30, 60, 90, 120, 144, 165, 240]
                                delegate: Chip {
                                    required property int modelData
                                    label: modelData === 0 ? "Aucune" : modelData + " i/s"
                                    selected: backend.fpsLimit === modelData
                                    onClicked: backend.setFpsLimit(modelData)
                                }
                            }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }

            // Proton
            ColumnLayout {
                spacing: 14
                Text { text: "Proton"; color: win.white; font.pixelSize: 28; font.weight: Font.DemiBold }
                Text {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: win.muted; font.pixelSize: 14
                    text: "Steam fournit Proton. GE-Proton ajoute des correctifs (codecs vidéo, jeux récents) ; il s'installe dans votre dossier Steam, archive vérifiée par somme SHA-512."
                }
                Card {
                    Layout.fillWidth: true
                    ColumnLayout {
                        width: parent.width
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: "Installer la dernière version de GE-Proton"; color: win.white; font.pixelSize: 16; font.weight: Font.DemiBold }
                            Button {
                                text: backend.busy ? "Installation…" : "Installer"
                                enabled: !backend.busy
                                onClicked: backend.installProtonGe()
                            }
                        }
                        ProgressBar {
                            Layout.fillWidth: true
                            visible: backend.busy
                            from: 0; to: 100; value: win.protonPercent
                        }
                        Text { Layout.fillWidth: true; visible: win.protonMessage !== ""; text: win.protonMessage; color: win.muted; font.pixelSize: 12; elide: Text.ElideRight }
                    }
                }
                Text { text: "Couches installées"; color: win.turquoise; font.pixelSize: 15; font.weight: Font.DemiBold }
                Repeater {
                    model: backend.protonVersions
                    delegate: Card {
                        required property var modelData
                        Layout.fillWidth: true
                        RowLayout {
                            width: parent.width
                            Text { Layout.fillWidth: true; text: modelData.name; color: win.white; font.pixelSize: 15 }
                            Button { text: "Supprimer"; onClicked: backend.removeProton(modelData.name) }
                        }
                    }
                }
                Text {
                    visible: backend.protonVersions.length === 0
                    text: "Aucune couche additionnelle : Steam utilise ses propres versions de Proton."
                    color: win.muted; font.pixelSize: 13
                }
                Item { Layout.fillHeight: true }
            }

            // Lanceurs
            ColumnLayout {
                spacing: 14
                Text { text: "Lanceurs"; color: win.white; font.pixelSize: 28; font.weight: Font.DemiBold }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    rowSpacing: 12; columnSpacing: 12
                    Repeater {
                        model: backend.launchers
                        delegate: Rectangle {
                            id: tile
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 86
                            radius: 18
                            opacity: modelData.available ? 1 : 0.55
                            color: Qt.rgba(1, 1, 1, tileHover.hovered ? 0.12 : 0.06)
                            border.width: 1
                            border.color: Qt.rgba(1, 1, 1, 0.14)
                            scale: tileTap.pressed ? 0.97 : 1
                            Behavior on scale { NumberAnimation { duration: 120 } }
                            HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { id: tileTap; onTapped: backend.launch(tile.modelData.id) }
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 14; spacing: 14
                                Image { Layout.preferredWidth: 44; Layout.preferredHeight: 44; source: "image://icon/" + tile.modelData.icon; sourceSize.width: 88; sourceSize.height: 88 }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    Text { text: tile.modelData.label; color: win.white; font.pixelSize: 16; font.weight: Font.DemiBold }
                                    Text {
                                        Layout.fillWidth: true; elide: Text.ElideRight; color: win.muted; font.pixelSize: 12
                                        text: tile.modelData.available ? tile.modelData.description : "Non installé — l'installer depuis Applications"
                                    }
                                }
                            }
                        }
                    }
                }
                Item { Layout.fillHeight: true }
            }
        }
    }

    Rectangle {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 24
        width: Math.min(parent.width - 48, toastText.implicitWidth + 40)
        height: toastText.implicitHeight + 22
        radius: 18
        color: Qt.rgba(0.04, 0.12, 0.24, 0.95)
        border.width: 1; border.color: win.turquoise
        opacity: 0
        visible: opacity > 0
        Text { id: toastText; anchors.centerIn: parent; width: parent.width - 32; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: win.white; font.pixelSize: 14 }
        SequentialAnimation {
            id: toastAnimation
            NumberAnimation { target: toast; property: "opacity"; to: 1; duration: 200 }
            PauseAnimation { duration: 4000 }
            NumberAnimation { target: toast; property: "opacity"; to: 0; duration: 400 }
        }
    }

    Connections {
        target: backend
        function onNotify(message) { toastText.text = message; toastAnimation.restart() }
        function onProtonProgress(message, percent) { win.protonMessage = message; win.protonPercent = percent }
    }
}
