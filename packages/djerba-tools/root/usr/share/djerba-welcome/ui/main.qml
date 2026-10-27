// SPDX-License-Identifier: GPL-3.0-or-later
// Djerba OS — assistant d'accueil et de premier démarrage (Qt 6.8 / PyQt6).
// Verre dépoli (Qt5Compat.GraphicalEffects), particules d'écume
// (QtQuick.Particles), transitions de pages et animations pilotées par le
// moteur de rendu. En rendu logiciel, les effets de shader sont remplacés par
// un verre teinté (voir backend.softwareRendering).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Particles
import Qt5Compat.GraphicalEffects

ApplicationWindow {
    id: win

    width: 1240
    height: 800
    minimumWidth: 1040
    minimumHeight: 700
    visible: true
    title: qsTr("Bienvenue dans Djerba OS")
    color: palette_.night
    font.family: "Inter"

    readonly property var pageIds: ["home", "apps", "gaming", "privacy", "system"]
    property int currentPage: Math.max(0, pageIds.indexOf(backend.startPage))

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
                anchors.fill: parent
                source: backend.wallpaper
                fillMode: Image.PreserveAspectCrop
                smooth: true
                visible: source !== ""
            }
        }

        // Flou calculé une seule fois (contenu statique), échantillonné par
        // chaque panneau de verre. Absent en rendu logiciel.
        Loader {
            id: blurLoader
            anchors.fill: parent
            active: !backend.softwareRendering
            sourceComponent: FastBlur {
                source: sceneryContent
                radius: 64
                cached: true
                visible: false
            }
        }

        transformOrigin: Item.Center
        SequentialAnimation on scale {
            loops: Animation.Infinite
            NumberAnimation { from: 1.0; to: 1.06; duration: 28000; easing.type: Easing.InOutSine }
            NumberAnimation { from: 1.06; to: 1.0; duration: 28000; easing.type: Easing.InOutSine }
        }
    }

    // Écume (non prise en charge par le rendu logiciel).
    Loader {
        anchors.fill: parent
        active: !backend.softwareRendering
        sourceComponent: Item {
            ParticleSystem { id: foam; anchors.fill: parent }
            ImageParticle {
                system: foam
                source: backend.assetsUrl + "particle.png"
                color: palette_.aqua
                colorVariation: 0.2
                alpha: 0.4
                alphaVariation: 0.3
                entryEffect: ImageParticle.Fade
            }
            Emitter {
                system: foam
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.height * 0.3
                emitRate: 8
                lifeSpan: 12000
                lifeSpanVariation: 3000
                size: 16
                sizeVariation: 12
                endSize: 4
                velocity: AngleDirection { angle: 270; angleVariation: 14; magnitude: 34; magnitudeVariation: 16 }
            }
            Wander { system: foam; anchors.fill: parent; xVariance: 40; pace: 30 }
        }
    }

    // ================================================================ Composants
    component GlassPanel: Item {
        id: panel
        default property alias content: inner.data
        property real radius: 24
        property real tint: 0.09
        property int padding: 24

        Loader {
            anchors.fill: parent
            active: blurLoader.item !== null
            sourceComponent: Item {
                ShaderEffectSource {
                    id: backdrop
                    anchors.fill: parent
                    sourceItem: blurLoader.item
                    visible: false
                    live: true
                    sourceRect: {
                        scenery.scale
                        panel.width
                        panel.height
                        panel.x
                        panel.y
                        var a = panel.mapToItem(blurLoader.item, 0, 0)
                        var b = panel.mapToItem(blurLoader.item, panel.width, panel.height)
                        return Qt.rect(a.x, a.y, b.x - a.x, b.y - a.y)
                    }
                }
                Rectangle { id: mask; anchors.fill: parent; radius: panel.radius; visible: false }
                OpacityMask { anchors.fill: parent; source: backdrop; maskSource: mask }
            }
        }
        Rectangle {
            anchors.fill: parent
            radius: panel.radius
            visible: backend.softwareRendering
            color: Qt.rgba(0.03, 0.07, 0.15, 0.62)
        }
        Rectangle {
            anchors.fill: parent
            radius: panel.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, panel.tint + 0.05) }
                GradientStop { position: 1.0; color: Qt.rgba(1, 1, 1, panel.tint - 0.04) }
            }
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.20)
        }
        Rectangle {
            x: panel.radius; y: 1
            width: parent.width - 2 * panel.radius; height: 1
            color: Qt.rgba(1, 1, 1, 0.35)
        }
        Item { id: inner; anchors.fill: parent; anchors.margins: panel.padding }
    }

    component PrimaryButton: Button {
        id: control
        property color from: palette_.turquoise
        property color to: palette_.aqua
        property color labelColor: palette_.night
        implicitHeight: 50
        implicitWidth: Math.max(200, contentItem.implicitWidth + 48)
        font.pixelSize: 16
        font.weight: Font.DemiBold
        scale: down ? 0.97 : (hovered ? 1.03 : 1.0)
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        contentItem: Text {
            text: control.text; font: control.font; color: control.labelColor
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Item {
            RectangularGlow {
                visible: !backend.softwareRendering
                anchors.fill: fill
                glowRadius: control.hovered ? 20 : 10
                spread: 0.2
                color: control.from
                cornerRadius: fill.radius + glowRadius
                opacity: control.enabled ? 0.5 : 0.0
                Behavior on glowRadius { NumberAnimation { duration: 200 } }
            }
            Rectangle {
                id: fill
                anchors.fill: parent
                radius: 16
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
        implicitHeight: 44
        implicitWidth: contentItem.implicitWidth + 40
        font.pixelSize: 14
        contentItem: Text {
            text: ghost.text; font: ghost.font; color: ghost.enabled ? palette_.white : palette_.muted
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 14
            color: Qt.rgba(1, 1, 1, ghost.down ? 0.20 : (ghost.hovered ? 0.14 : 0.07))
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, ghost.hovered ? 0.35 : 0.18)
            Behavior on color { ColorAnimation { duration: 140 } }
        }
    }

    // Interrupteur animé.
    component Toggle: Item {
        id: toggle
        property bool checked: false
        property bool busy: false
        signal toggled()
        implicitWidth: 58
        implicitHeight: 32
        opacity: enabled ? 1.0 : 0.45
        Rectangle {
            anchors.fill: parent
            radius: height / 2
            color: toggle.checked ? palette_.turquoise : Qt.rgba(1, 1, 1, 0.14)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.25)
            Behavior on color { ColorAnimation { duration: 180 } }
        }
        Rectangle {
            width: parent.height - 8; height: width; radius: width / 2
            y: 4
            x: toggle.checked ? parent.width - width - 4 : 4
            color: palette_.white
            Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
            BusyIndicator {
                anchors.centerIn: parent
                width: parent.width; height: width
                running: toggle.busy
                visible: toggle.busy
            }
        }
        TapHandler { enabled: !toggle.busy; onTapped: toggle.toggled() }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }

    // Carte d'option : titre, description, contrôle à droite.
    component OptionCard: Rectangle {
        id: card
        property string title
        property string description
        property string icon
        default property alias trailing: trailingSlot.data
        Layout.fillWidth: true
        implicitHeight: Math.max(84, row.implicitHeight + 32)
        radius: 18
        color: Qt.rgba(1, 1, 1, hover.hovered ? 0.10 : 0.06)
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 0.12)
        Behavior on color { ColorAnimation { duration: 150 } }
        HoverHandler { id: hover }
        RowLayout {
            id: row
            anchors.fill: parent
            anchors.margins: 16
            spacing: 16
            Image {
                Layout.preferredWidth: 40; Layout.preferredHeight: 40
                source: card.icon ? "image://icon/" + card.icon : ""
                sourceSize.width: 80; sourceSize.height: 80
                visible: card.icon !== ""
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text { text: card.title; color: palette_.white; font.pixelSize: 16; font.weight: Font.DemiBold }
                Text {
                    Layout.fillWidth: true
                    text: card.description; color: palette_.muted; font.pixelSize: 13
                    wrapMode: Text.WordWrap; visible: text !== ""
                }
            }
            Item { id: trailingSlot; implicitWidth: childrenRect.width; implicitHeight: childrenRect.height }
        }
    }

    component SectionTitle: Text {
        color: palette_.white
        font.pixelSize: 28
        font.weight: Font.DemiBold
    }

    component Subtitle: Text {
        Layout.fillWidth: true
        color: palette_.muted
        font.pixelSize: 15
        wrapMode: Text.WordWrap
        lineHeight: 1.25
    }

    // ================================================================ Mise en page
    RowLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: 28
        spacing: 24
        opacity: 0
        Component.onCompleted: entrance.start()
        NumberAnimation { id: entrance; target: layout; property: "opacity"; to: 1; duration: 700; easing.type: Easing.OutCubic }

        // ------------------------------------------------------------ Barre latérale
        GlassPanel {
            Layout.preferredWidth: 250
            Layout.fillHeight: true
            padding: 18
            tint: 0.08

            ColumnLayout {
                anchors.fill: parent
                spacing: 6

                RowLayout {
                    spacing: 12
                    Layout.bottomMargin: 18
                    Image {
                        Layout.preferredWidth: 52; Layout.preferredHeight: 52
                        source: backend.assetsUrl + "logo.svg"
                        sourceSize.width: 104; sourceSize.height: 104
                        SequentialAnimation on rotation {
                            loops: Animation.Infinite
                            NumberAnimation { from: -2; to: 2; duration: 3200; easing.type: Easing.InOutSine }
                            NumberAnimation { from: 2; to: -2; duration: 3200; easing.type: Easing.InOutSine }
                        }
                    }
                    ColumnLayout {
                        spacing: 0
                        Text {
                            text: "<b>Djerba</b> <font color='" + palette_.turquoise + "'>OS</font>"
                            textFormat: Text.StyledText; color: palette_.white; font.pixelSize: 22
                        }
                        Text { text: backend.edition; color: palette_.muted; font.pixelSize: 12 }
                    }
                }

                Repeater {
                    model: [
                        { label: qsTr("Accueil"), icon: "go-home" },
                        { label: qsTr("Applications"), icon: "plasmadiscover" },
                        { label: qsTr("Jeu"), icon: "input-gaming" },
                        { label: qsTr("Confidentialité"), icon: "security-high" },
                        { label: qsTr("Système"), icon: "computer" }
                    ]
                    delegate: Rectangle {
                        id: navItem
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 46
                        radius: 14
                        color: win.currentPage === index ? Qt.rgba(0, 0.76, 0.8, 0.22)
                                                         : Qt.rgba(1, 1, 1, navHover.hovered ? 0.08 : 0.0)
                        border.width: win.currentPage === index ? 1 : 0
                        border.color: Qt.rgba(0, 0.76, 0.8, 0.6)
                        Behavior on color { ColorAnimation { duration: 180 } }
                        HoverHandler { id: navHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: win.currentPage = navItem.index }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            spacing: 12
                            Image {
                                Layout.preferredWidth: 22; Layout.preferredHeight: 22
                                source: "image://icon/" + navItem.modelData.icon
                                sourceSize.width: 44; sourceSize.height: 44
                            }
                            Text {
                                Layout.fillWidth: true
                                text: navItem.modelData.label
                                color: palette_.white
                                font.pixelSize: 15
                                font.weight: win.currentPage === navItem.index ? Font.DemiBold : Font.Normal
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                // État réseau
                RowLayout {
                    spacing: 8
                    Rectangle {
                        width: 10; height: 10; radius: 5
                        color: backend.anonActive ? palette_.sand : (backend.online ? palette_.aqua : palette_.coral)
                        SequentialAnimation on opacity {
                            loops: Animation.Infinite
                            NumberAnimation { from: 1.0; to: 0.35; duration: 900 }
                            NumberAnimation { from: 0.35; to: 1.0; duration: 900 }
                        }
                    }
                    Text {
                        text: backend.anonActive ? qsTr("Anonyme (Tor)") : (backend.online ? qsTr("Connecté") : qsTr("Hors ligne"))
                        color: palette_.white; font.pixelSize: 13
                    }
                }
                CheckBox {
                    id: autostartBox
                    checked: backend.autostart
                    onToggled: backend.autostart = checked
                    text: qsTr("Afficher au démarrage")
                    font.pixelSize: 12
                    contentItem: Text {
                        leftPadding: autostartBox.indicator.width + 8
                        text: autostartBox.text; font: autostartBox.font; color: palette_.muted
                        verticalAlignment: Text.AlignVCenter
                    }
                    indicator: Rectangle {
                        implicitWidth: 18; implicitHeight: 18
                        y: (autostartBox.height - height) / 2
                        radius: 5
                        color: autostartBox.checked ? palette_.turquoise : Qt.rgba(1, 1, 1, 0.08)
                        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.3)
                        Text { anchors.centerIn: parent; text: "✓"; visible: autostartBox.checked; color: palette_.night; font.pixelSize: 12; font.bold: true }
                    }
                }
            }
        }

        // ------------------------------------------------------------ Pages
        Item {
            id: pages
            Layout.fillWidth: true
            Layout.fillHeight: true

            Repeater {
                model: [homePage, appsPage, gamingPage, privacyPage, systemPage]
                delegate: Loader {
                    required property var modelData
                    required property int index
                    anchors.fill: parent
                    sourceComponent: modelData
                    active: true
                    visible: opacity > 0
                    opacity: win.currentPage === index ? 1 : 0
                    property real shift: win.currentPage === index ? 0 : (index < win.currentPage ? -40 : 40)
                    transform: Translate { x: shift }
                    Behavior on opacity { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
                    Behavior on shift { NumberAnimation { duration: 420; easing.type: Easing.OutExpo } }
                }
            }
        }
    }

    // ================================================================ Page Accueil
    Component {
        id: homePage
        ColumnLayout {
            spacing: 20

            GlassPanel {
                Layout.fillWidth: true
                Layout.preferredHeight: 300
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 14
                    Text {
                        text: qsTr("Bienvenue sur ") + "<b>Djerba</b> <font color='" + palette_.turquoise + "'>OS</font>"
                        textFormat: Text.StyledText; color: palette_.white; font.pixelSize: 36; font.weight: Font.Light
                    }
                    Subtitle {
                        text: backend.isLive
                              ? qsTr("Vous utilisez Djerba OS depuis le média live : rien n'est modifié sur cet ordinateur. Installez-le en quelques minutes : vous choisirez vos applications (Steam, Lutris, Heroic, antivirus…) pendant l'installation.")
                              : qsTr("Votre système est prêt. Installez vos jeux et applications, activez le mode jeu, ou protégez votre vie privée avec le mode anonyme et le mode discret.")
                    }
                    Item { Layout.fillHeight: true }
                    RowLayout {
                        spacing: 12
                        PrimaryButton {
                            visible: backend.isLive
                            enabled: !backend.installerRunning
                            text: backend.installerRunning ? qsTr("Installation en cours…") : qsTr("Installer Djerba OS")
                            onClicked: backend.launchInstaller()
                        }
                        PrimaryButton {
                            visible: !backend.isLive
                            text: qsTr("Choisir mes applications")
                            onClicked: win.currentPage = 1
                        }
                        GhostButton {
                            text: backend.isLive ? qsTr("Continuer à essayer") : qsTr("Fermer")
                            onClicked: win.close()
                        }
                    }
                }
            }

            // Premier démarrage : applications choisies dans l'installateur.
            GlassPanel {
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                visible: backend.pendingApps.length > 0
                tint: 0.12
                RowLayout {
                    anchors.fill: parent
                    spacing: 18
                    BusyIndicator { running: parent.visible; Layout.preferredWidth: 48; Layout.preferredHeight: 48 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("Finalisation : installation de vos applications")
                            color: palette_.white; font.pixelSize: 17; font.weight: Font.DemiBold
                        }
                        Subtitle {
                            text: backend.pendingMessage !== "" ? backend.pendingMessage
                                  : (backend.online ? qsTr("En attente : ") + backend.pendingApps.join(", ")
                                                    : qsTr("Connectez-vous à Internet : l'installation démarrera automatiquement."))
                        }
                    }
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: 2
                rowSpacing: 14
                columnSpacing: 14
                Repeater {
                    model: [
                        { title: qsTr("Applications"), text: qsTr("Steam, Lutris, Heroic, VLC, LibreOffice…"), icon: "plasmadiscover", page: 1 },
                        { title: qsTr("Mode jeu"), text: backend.gameModeActive ? qsTr("Actif") : qsTr("Performances maximales en un clic"), icon: "input-gaming", page: 2 },
                        { title: qsTr("Mode anonyme"), text: backend.anonActive ? qsTr("Actif : trafic via Tor") : qsTr("Tout le trafic via le réseau Tor"), icon: "security-high", page: 3 },
                        { title: qsTr("Mode discret"), text: backend.undercoverActive ? qsTr("Actif") : qsTr("Apparence façon Windows, réversible"), icon: "djerba-undercover-start", page: 3 }
                    ]
                    delegate: Rectangle {
                        id: tile
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 96
                        radius: 20
                        color: Qt.rgba(1, 1, 1, tileHover.hovered ? 0.12 : 0.06)
                        border.width: 1
                        border.color: Qt.rgba(1, 1, 1, tileHover.hovered ? 0.3 : 0.12)
                        scale: tileTap.pressed ? 0.97 : (tileHover.hovered ? 1.02 : 1.0)
                        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutBack } }
                        Behavior on color { ColorAnimation { duration: 160 } }
                        HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { id: tileTap; onTapped: win.currentPage = tile.modelData.page }
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 16
                            Image {
                                Layout.preferredWidth: 44; Layout.preferredHeight: 44
                                source: "image://icon/" + tile.modelData.icon
                                sourceSize.width: 88; sourceSize.height: 88
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                Text { text: tile.modelData.title; color: palette_.white; font.pixelSize: 17; font.weight: Font.DemiBold }
                                Text { Layout.fillWidth: true; text: tile.modelData.text; color: palette_.muted; font.pixelSize: 13; elide: Text.ElideRight }
                            }
                        }
                    }
                }
            }
            Item { Layout.fillHeight: true }
        }
    }

    // ================================================================ Page Applications
    Component {
        id: appsPage
        GlassPanel {
            id: appsPanel
            property var selection: ({})
            property int selectedCount: 0
            property bool removeMode: false
            property string progressMessage: ""
            property int progressDone: 0
            property int progressTotal: 0

            function toggle(app) {
                var next = Object.assign({}, selection)
                if (next[app.id]) delete next[app.id]; else next[app.id] = true
                selection = next
                selectedCount = Object.keys(next).length
            }
            function clear() { selection = ({}); selectedCount = 0 }

            Connections {
                target: backend
                function onAppsProgress(message, done, total) {
                    appsPanel.progressMessage = message
                    appsPanel.progressDone = done
                    appsPanel.progressTotal = total
                }
                function onAppsFinished(success, message) {
                    appsPanel.progressMessage = message
                    appsPanel.clear()
                    toastText.text = message
                    toastAnimation.restart()
                }
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 12

                RowLayout {
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        SectionTitle { text: qsTr("Applications") }
                        Subtitle { text: qsTr("Cochez les applications à installer ou à supprimer. Les applications Flatpak proviennent de Flathub.") }
                    }
                    GhostButton {
                        text: appsPanel.removeMode ? qsTr("Mode : supprimer") : qsTr("Mode : installer")
                        onClicked: { appsPanel.removeMode = !appsPanel.removeMode; appsPanel.clear() }
                    }
                }

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth

                    ColumnLayout {
                        width: parent.width
                        spacing: 18

                        Repeater {
                            model: backend.categories
                            delegate: ColumnLayout {
                                id: categoryBlock
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 8
                                Text { text: categoryBlock.modelData.name; color: palette_.turquoise; font.pixelSize: 15; font.weight: Font.DemiBold }
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 2
                                    rowSpacing: 10
                                    columnSpacing: 10
                                    Repeater {
                                        model: backend.apps.filter(function (a) { return a.category === categoryBlock.modelData.id })
                                        delegate: Rectangle {
                                            id: appCard
                                            required property var modelData
                                            readonly property bool eligible: appsPanel.removeMode ? modelData.installed : !modelData.installed
                                            readonly property bool chosen: appsPanel.selection[modelData.id] === true
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 92
                                            radius: 16
                                            opacity: eligible ? 1.0 : 0.55
                                            color: chosen ? Qt.rgba(0, 0.76, 0.8, 0.18) : Qt.rgba(1, 1, 1, cardHover.hovered && eligible ? 0.10 : 0.05)
                                            border.width: 1
                                            border.color: chosen ? palette_.turquoise : Qt.rgba(1, 1, 1, 0.12)
                                            Behavior on color { ColorAnimation { duration: 140 } }
                                            HoverHandler { id: cardHover; cursorShape: appCard.eligible ? Qt.PointingHandCursor : Qt.ArrowCursor }
                                            TapHandler { enabled: appCard.eligible && !backend.appsBusy; onTapped: appsPanel.toggle(appCard.modelData) }
                                            ToolTip.visible: cardHover.hovered && appCard.modelData.notice !== ""
                                            ToolTip.text: appCard.modelData.notice
                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 14
                                                spacing: 12
                                                Image {
                                                    Layout.preferredWidth: 40; Layout.preferredHeight: 40
                                                    source: "image://icon/" + appCard.modelData.icon
                                                    sourceSize.width: 80; sourceSize.height: 80
                                                }
                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 2
                                                    RowLayout {
                                                        Text { text: appCard.modelData.name; color: palette_.white; font.pixelSize: 15; font.weight: Font.DemiBold }
                                                        Rectangle {
                                                            visible: appCard.modelData.installed || appCard.modelData.pending
                                                            radius: 8; implicitHeight: 18; implicitWidth: badge.implicitWidth + 12
                                                            color: appCard.modelData.installed ? Qt.rgba(0.37, 0.92, 0.83, 0.2) : Qt.rgba(0.96, 0.88, 0.76, 0.2)
                                                            Text { id: badge; anchors.centerIn: parent; text: appCard.modelData.installed ? qsTr("installée") : qsTr("en attente"); color: palette_.white; font.pixelSize: 11 }
                                                        }
                                                        Text { text: appCard.modelData.backend === "flatpak" ? "Flatpak" : ""; color: palette_.muted; font.pixelSize: 11 }
                                                    }
                                                    Text {
                                                        Layout.fillWidth: true
                                                        text: appCard.modelData.description
                                                        color: palette_.muted; font.pixelSize: 12
                                                        wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                                                    }
                                                }
                                                Rectangle {
                                                    Layout.preferredWidth: 22; Layout.preferredHeight: 22
                                                    radius: 6
                                                    visible: appCard.eligible
                                                    color: appCard.chosen ? palette_.turquoise : "transparent"
                                                    border.width: 2
                                                    border.color: appCard.chosen ? palette_.turquoise : Qt.rgba(1, 1, 1, 0.4)
                                                    Text { anchors.centerIn: parent; text: "✓"; visible: appCard.chosen; color: palette_.night; font.bold: true }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Barre d'action et progression
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: backend.appsBusy || appsPanel.progressMessage !== ""
                        Text { Layout.fillWidth: true; text: appsPanel.progressMessage; color: palette_.white; font.pixelSize: 13; elide: Text.ElideRight }
                        ProgressBar {
                            Layout.fillWidth: true
                            visible: backend.appsBusy
                            from: 0; to: Math.max(1, appsPanel.progressTotal); value: appsPanel.progressDone
                            indeterminate: appsPanel.progressTotal === 0
                            background: Rectangle { implicitHeight: 8; radius: 4; color: Qt.rgba(1, 1, 1, 0.1) }
                            contentItem: Item {
                                Rectangle {
                                    width: parent.width * (appsPanel.progressTotal ? appsPanel.progressDone / appsPanel.progressTotal : 0.3)
                                    height: parent.height; radius: 4
                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop { position: 0; color: palette_.turquoise }
                                        GradientStop { position: 1; color: palette_.aqua }
                                    }
                                    Behavior on width { NumberAnimation { duration: 300 } }
                                }
                            }
                        }
                    }
                    Item { Layout.fillWidth: !(backend.appsBusy || appsPanel.progressMessage !== "") }
                    Text {
                        visible: !backend.online && !appsPanel.removeMode
                        text: qsTr("Connexion Internet requise")
                        color: palette_.coral; font.pixelSize: 13
                    }
                    PrimaryButton {
                        enabled: appsPanel.selectedCount > 0 && !backend.appsBusy && (backend.online || appsPanel.removeMode)
                        from: appsPanel.removeMode ? palette_.coral : palette_.turquoise
                        to: appsPanel.removeMode ? palette_.sand : palette_.aqua
                        text: (appsPanel.removeMode ? qsTr("Supprimer") : qsTr("Installer")) + " (" + appsPanel.selectedCount + ")"
                        onClicked: backend.changeApps(Object.keys(appsPanel.selection), appsPanel.removeMode)
                    }
                }
            }
        }
    }

    // ================================================================ Page Jeu
    Component {
        id: gamingPage
        GlassPanel {
            ColumnLayout {
                anchors.fill: parent
                spacing: 14
                RowLayout {
                    Layout.fillWidth: true
                    SectionTitle { Layout.fillWidth: true; text: qsTr("Jeu") }
                    PrimaryButton { text: qsTr("Ouvrir le Game Center"); onClicked: backend.openTool("gamecenter") }
                }
                Subtitle { text: qsTr("Noyau à préemption complète, GameMode, MangoHud, vkBasalt et pilotes Vulkan sont déjà installés. Le mode jeu maximise les performances de la session.") }

                OptionCard {
                    title: qsTr("Mode jeu")
                    description: (backend.gameModeActive ? qsTr("Actif") : qsTr("Inactif")) + qsTr(" — profil d'énergie : ") + backend.powerProfile + qsTr(". Coupe les effets visuels coûteux et passe le processeur en performances.")
                    icon: "input-gaming"
                    Toggle { checked: backend.gameModeActive; onToggled: backend.toggleGameMode() }
                }
                OptionCard {
                    title: qsTr("Superposition MangoHud")
                    description: qsTr("Affiche images/s, températures et charge dans les jeux Vulkan et OpenGL (effet à la prochaine connexion).")
                    icon: "utilities-system-monitor"
                    Toggle { checked: backend.mangohudEnabled; onToggled: backend.setMangohud(!backend.mangohudEnabled) }
                }

                Text { text: qsTr("Lanceurs"); color: palette_.turquoise; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.topMargin: 6 }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 3
                    rowSpacing: 10
                    columnSpacing: 10
                    Repeater {
                        model: backend.gameLaunchers
                        delegate: Rectangle {
                            id: launcher
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: 76
                            radius: 16
                            opacity: modelData.available ? 1.0 : 0.5
                            color: Qt.rgba(1, 1, 1, launcherHover.hovered ? 0.11 : 0.05)
                            border.width: 1
                            border.color: Qt.rgba(1, 1, 1, 0.12)
                            scale: launcherTap.pressed ? 0.97 : 1.0
                            Behavior on scale { NumberAnimation { duration: 120 } }
                            HoverHandler { id: launcherHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler {
                                id: launcherTap
                                onTapped: launcher.modelData.available ? backend.launchGame(launcher.modelData.id) : (win.currentPage = 1)
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 10
                                Image { Layout.preferredWidth: 36; Layout.preferredHeight: 36; source: "image://icon/" + launcher.modelData.icon; sourceSize.width: 72; sourceSize.height: 72 }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    Text { text: launcher.modelData.label; color: palette_.white; font.pixelSize: 14; font.weight: Font.DemiBold }
                                    Text {
                                        Layout.fillWidth: true
                                        text: launcher.modelData.available ? launcher.modelData.description : qsTr("Non installé — voir Applications")
                                        color: palette_.muted; font.pixelSize: 11; elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
                Subtitle {
                    Layout.topMargin: 6
                    text: qsTr("Astuce Steam : ajoutez « gamemoderun %command% » aux options de lancement d'un jeu pour activer GameMode, ou « mangohud %command% » pour la superposition.")
                    font.pixelSize: 13
                }
                Item { Layout.fillHeight: true }
            }
        }
    }

    // ================================================================ Page Confidentialité
    Component {
        id: privacyPage
        GlassPanel {
            ScrollView {
                anchors.fill: parent
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: parent.width
                    spacing: 14
                    SectionTitle { text: qsTr("Confidentialité et sécurité") }

                    OptionCard {
                        title: qsTr("Mode anonyme (Tor)")
                        description: backend.privacyBusy ? qsTr("Connexion au réseau Tor…")
                                     : backend.anonActive ? qsTr("Actif : tout le trafic passe par Tor ; DNS, IPv6 et UDP sont protégés. Si Tor s'arrête, rien ne sort.")
                                     : qsTr("Fait passer tout le trafic de l'ordinateur par le réseau Tor, avec un coupe-circuit contre les fuites (inspiré d'AnonSurf).")
                        icon: "security-high"
                        Toggle { checked: backend.anonActive; busy: backend.privacyBusy; onToggled: backend.toggleAnonSurf() }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        GhostButton { text: qsTr("Vérifier mon adresse IP"); onClicked: backend.checkIp() }
                        GhostButton { text: qsTr("Nouvelle identité"); enabled: backend.anonActive; onClicked: backend.newIdentity() }
                        Text {
                            Layout.fillWidth: true
                            visible: backend.publicIp !== ""
                            text: qsTr("IP publique : ") + backend.publicIp + (backend.publicIp.indexOf(".") > 0 || backend.publicIp.indexOf(":") > 0 ? (backend.viaTor ? qsTr(" — via Tor ✓") : qsTr(" — connexion directe")) : "")
                            color: backend.viaTor ? palette_.aqua : palette_.white
                            font.pixelSize: 13
                            elide: Text.ElideRight
                        }
                    }

                    OptionCard {
                        title: qsTr("Mode discret")
                        description: backend.undercoverActive ? qsTr("Actif : votre bureau ressemble à Windows. Revenez à tout moment à votre apparence habituelle.")
                                     : qsTr("Donne au bureau une apparence façon Windows pour travailler en public sans attirer l'attention (comme le mode undercover de Kali). Entièrement réversible.")
                        icon: "djerba-undercover-start"
                        RowLayout {
                            spacing: 8
                            GhostButton { text: qsTr("Style 10"); visible: !backend.undercoverActive; onClicked: backend.setUndercover("10") }
                            GhostButton { text: qsTr("Style 11"); visible: !backend.undercoverActive; onClicked: backend.setUndercover("11") }
                            GhostButton { text: qsTr("Revenir"); visible: backend.undercoverActive; onClicked: backend.setUndercover("off") }
                        }
                    }

                    OptionCard {
                        title: qsTr("Antivirus ClamAV")
                        description: qsTr("Analysez fichiers et clés USB ; les signatures se mettent à jour automatiquement (freshclam).")
                        icon: "clamtk"
                        GhostButton { text: qsTr("Ouvrir"); onClicked: backend.openTool("antivirus") }
                    }
                    OptionCard {
                        title: qsTr("Pare-feu")
                        description: qsTr("UFW est actif : toutes les connexions entrantes non sollicitées sont refusées.")
                        icon: "security-medium"
                        GhostButton { text: qsTr("Réglages"); onClicked: backend.openTool("firewall") }
                    }
                }
            }
        }
    }

    // ================================================================ Page Système
    Component {
        id: systemPage
        GlassPanel {
            ColumnLayout {
                anchors.fill: parent
                spacing: 12
                SectionTitle { text: qsTr("Système") }

                Repeater {
                    model: backend.systemInfo
                    delegate: Rectangle {
                        id: infoRow
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 54
                        radius: 14
                        color: Qt.rgba(1, 1, 1, rowHover.hovered ? 0.10 : 0.05)
                        Behavior on color { ColorAnimation { duration: 150 } }
                        HoverHandler { id: rowHover }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            anchors.rightMargin: 14
                            spacing: 12
                            Image { Layout.preferredWidth: 28; Layout.preferredHeight: 28; source: "image://icon/" + infoRow.modelData.icon; sourceSize.width: 56; sourceSize.height: 56 }
                            Text { Layout.preferredWidth: 130; text: infoRow.modelData.label; color: palette_.muted; font.pixelSize: 13 }
                            Text { Layout.fillWidth: true; text: infoRow.modelData.value; color: palette_.white; font.pixelSize: 14; elide: Text.ElideRight }
                        }
                    }
                }

                Item { Layout.fillHeight: true }
                RowLayout {
                    spacing: 10
                    GhostButton { text: qsTr("Mises à jour"); onClicked: backend.openTool("updates") }
                    GhostButton { text: qsTr("Thèmes globaux"); onClicked: backend.openTool("themes") }
                    GhostButton { text: qsTr("Paramètres"); onClicked: backend.openTool("settings") }
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
        color: Qt.rgba(0.04, 0.12, 0.24, 0.94)
        border.width: 1
        border.color: palette_.turquoise
        opacity: 0
        visible: opacity > 0
        Text { id: toastText; anchors.centerIn: parent; color: palette_.white; font.pixelSize: 14 }
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
