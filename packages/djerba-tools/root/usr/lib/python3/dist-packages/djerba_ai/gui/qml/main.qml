// SPDX-License-Identifier: GPL-3.0-or-later
// Djerba — application de l'agent IA de Djerba OS (Qt 6.8).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    width: 1280
    height: 840
    minimumWidth: 980
    minimumHeight: 660
    visible: backend.startVisible
    title: "Djerba"
    color: "transparent"
    font.family: "Inter"

    // Palette adaptative : suit le jeu de couleurs de Plasma (clair ou sombre).
    SystemPalette { id: system; colorGroup: SystemPalette.Active }
    // Sous GNOME, Qt ne suit pas toujours le thème : la préférence GSettings fait foi.
    readonly property bool dark: backend.followsDesktopTheme ? backend.prefersDark : system.window.hslLightness < 0.5
    readonly property color night: dark ? "#070B18" : "#F1F5F9"
    readonly property color deep: dark ? "#0A1F3D" : "#E0F2F1"
    readonly property color surface: dark ? "#0B1426" : "#FFFFFF"
    readonly property color turquoise: dark ? "#00C2CB" : "#0E9AA7"
    readonly property color aqua: dark ? "#5EEAD4" : "#0F766E"
    readonly property color coral: dark ? "#FF7A59" : "#C2410C"
    readonly property color sand: dark ? "#F4E1C1" : "#92400E"
    readonly property color white: dark ? "#F8FAFC" : "#0F172A"
    readonly property color muted: dark ? "#94A3B8" : "#475569"
    function veil(alpha) { return dark ? Qt.rgba(1, 1, 1, alpha) : Qt.rgba(0.06, 0.09, 0.16, alpha * 0.7) }
    function stateColor(state) {
        return state === "ok" ? "#2DD4BF" : state === "fallback" ? "#F59E0B" : state === "busy" ? "#38BDF8" : "#F43F5E"
    }
    readonly property var pages: ["chat", "dashboard", "system", "selfheal", "studio", "tools", "settings"]
    property int page: Math.max(0, pages.indexOf(backend.startPage))
    property string progressText: ""
    property int progressValue: 0

    // Fond translucide aux coins arrondis (flou de KWin derrière, si disponible).
    background: Rectangle {
        radius: 18
        opacity: 0.94
        gradient: Gradient {
            GradientStop { position: 0.0; color: win.night }
            GradientStop { position: 0.7; color: win.deep }
            GradientStop { position: 1.0; color: win.dark ? "#0E3A52" : "#CCFBF1" }
        }
    }

    onClosing: (close) => {
        // Mode résident : la fenêtre se range dans la zone de notification.
        if (backend.resident) { close.accepted = false; win.hide() }
    }

    Connections {
        target: backend
        function onShowMainRequested(page) {
            if (page !== "" && win.pages.indexOf(page) >= 0) win.page = win.pages.indexOf(page)
            win.show(); win.raise(); win.requestActivate()
        }
    }

    // ---------------------------------------------------------------- Mise en page
    RowLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 18

        // Barre latérale
        Glass {
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 6

                Loader {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 170
                    Layout.preferredHeight: 150
                    source: backend.softwareRendering ? "Orb2D.qml" : "Orb3D.qml"
                    onLoaded: item.active = Qt.binding(function () { return backend.busy })
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "Djerba"; color: win.white; font.pixelSize: 26; font.weight: Font.Bold
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 6
                    Rectangle {
                        width: 9; height: 9; radius: 5; color: win.stateColor(backend.state)
                        SequentialAnimation on opacity {
                            running: backend.state === "busy"; loops: Animation.Infinite
                            NumberAnimation { to: 0.3; duration: 500 } NumberAnimation { to: 1; duration: 500 }
                        }
                    }
                    Text {
                        text: backend.engineAvailable ? backend.model : "mode simplifié"
                        color: win.muted; font.pixelSize: 12
                        ToolTip.visible: stateHover.hovered; ToolTip.text: backend.stateText
                        HoverHandler { id: stateHover }
                    }
                }
                Item { Layout.preferredHeight: 10 }

                Repeater {
                    model: [
                        { label: "Discussion", icon: "dialog-messages" },
                        { label: "Tableau de bord", icon: "office-chart-line" },
                        { label: "Système", icon: "computer" },
                        { label: "Auto-réparation", icon: "tools-wizard" },
                        { label: "Studio", icon: "applications-development" },
                        { label: "Capacités (" + backend.toolCount + ")", icon: "configure" },
                        { label: "Cerveau et réglages", icon: "preferences-system" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 42
                        radius: 12
                        color: win.page === index ? Qt.rgba(0, 0.76, 0.8, 0.22) : win.veil(hover.hovered ? 0.07 : 0)
                        border.width: win.page === index ? 1 : 0
                        border.color: Qt.rgba(0, 0.76, 0.8, 0.6)
                        Behavior on color { ColorAnimation { duration: 150 } }
                        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: win.page = index }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 12; spacing: 10
                            Image { Layout.preferredWidth: 20; Layout.preferredHeight: 20; source: "image://icon/" + modelData.icon; sourceSize: Qt.size(40, 40) }
                            Text { Layout.fillWidth: true; text: modelData.label; color: win.white; font.pixelSize: 14 }
                        }
                    }
                }

                Text { text: "Discussions"; color: win.turquoise; font.pixelSize: 12; Layout.topMargin: 12; visible: win.page === 0 }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    visible: win.page === 0
                    model: backend.conversations
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width
                        height: 32
                        radius: 8
                        color: backend.conversation === modelData.id ? win.veil(0.1) : "transparent"
                        Text {
                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            text: modelData.title; color: win.muted; font.pixelSize: 12
                        }
                        TapHandler { onTapped: { chat.clear(); backend.openConversation(modelData.id) } }
                    }
                }
                Item { Layout.fillHeight: true; visible: win.page !== 0 }
                ActionButton { Layout.fillWidth: true; text: "+ Nouvelle discussion"; onClicked: { chat.clear(); backend.newConversation(); win.page = 0 } }
            }
        }

        // Pages
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.page

            // ======================================================== Discussion
            ChatPage { id: chat }

            // ======================================================== Tableau de bord
            DashboardPage { }

            // ======================================================== Système
            Glass {
                ScrollView {
                    anchors.fill: parent; anchors.margins: 22; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 14
                        RowLayout {
                            Layout.fillWidth: true
                            Title { Layout.fillWidth: true; text: "Santé du système" }
                            ActionButton { text: "Actualiser"; onClicked: backend.refreshHealth() }
                        }
                        RowLayout {
                            spacing: 22
                            Canvas {
                                id: gauge
                                Layout.preferredWidth: 150; Layout.preferredHeight: 150
                                property int score: backend.healthScore
                                onScoreChanged: requestPaint()
                                onPaint: {
                                    var ctx = getContext("2d"); ctx.reset()
                                    ctx.lineWidth = 12; ctx.lineCap = "round"
                                    ctx.strokeStyle = "rgba(255,255,255,0.08)"
                                    ctx.beginPath(); ctx.arc(75, 75, 62, 0, 2 * Math.PI); ctx.stroke()
                                    ctx.strokeStyle = score >= 80 ? "#5EEAD4" : (score >= 55 ? "#F4E1C1" : "#FF7A59")
                                    ctx.beginPath(); ctx.arc(75, 75, 62, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * score / 100); ctx.stroke()
                                }
                                Text { anchors.centerIn: parent; text: backend.healthScore; color: win.white; font.pixelSize: 40; font.weight: Font.Light }
                            }
                            Body { text: backend.systemSummary; color: win.white; font.pixelSize: 13 }
                        }
                        Repeater {
                            model: backend.healthItems
                            delegate: Glass {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: 64
                                RowLayout {
                                    anchors.fill: parent; anchors.margins: 14; spacing: 12
                                    Rectangle { width: 12; height: 12; radius: 6; color: modelData.level === "ok" ? win.aqua : (modelData.level === "warning" ? win.sand : win.coral) }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 2
                                        Text { Layout.fillWidth: true; text: modelData.title; color: win.white; font.pixelSize: 15; font.weight: Font.DemiBold }
                                        Text { Layout.fillWidth: true; text: modelData.detail; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                                    }
                                    ActionButton {
                                        visible: modelData.tool !== ""
                                        text: "Voir avec Djerba"
                                        onClicked: { win.page = 0; backend.send("Aide-moi à régler ce point : " + modelData.title + " (" + modelData.detail + ")") }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ======================================================== Auto-réparation
            Glass {
                ScrollView {
                    anchors.fill: parent; anchors.margins: 22; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 14
                        RowLayout {
                            Layout.fillWidth: true
                            Title { Layout.fillWidth: true; text: "Auto-réparation" }
                            ActionButton { text: "Actualiser"; onClicked: backend.refreshIncidents() }
                        }
                        Body {
                            text: "Djerba surveille le journal du système. Quand un outil de Djerba OS échoue, il propose un correctif, le vérifie dans un bac à sable (syntaxe, analyse, exécution sans privilèges) et l'annule automatiquement s'il échoue. Rien n'est déployé sans votre accord et votre mot de passe ; chaque correctif peut être annulé."
                        }
                        Text { visible: backend.incidents.length === 0; text: "Aucun incident détecté. Tout fonctionne."; color: win.aqua; font.pixelSize: 14 }
                        Repeater {
                            model: backend.incidents
                            delegate: Glass {
                                required property var modelData
                                Layout.fillWidth: true
                                implicitHeight: incidentColumn.implicitHeight + 28
                                ColumnLayout {
                                    id: incidentColumn
                                    anchors.fill: parent; anchors.margins: 14; spacing: 8
                                    RowLayout {
                                        Text { text: (modelData.target || modelData.source).replace("/usr/bin/", ""); color: win.white; font.pixelSize: 15; font.weight: Font.DemiBold }
                                        Pill { text: modelData.status; tint: modelData.status === "corrigé" ? win.aqua : (modelData.status === "correctif rejeté" ? win.coral : win.sand) }
                                        Pill { text: modelData.occurrences + " fois"; tint: win.muted }
                                    }
                                    Text { Layout.fillWidth: true; text: modelData.message; color: win.muted; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; maximumLineCount: 4; elide: Text.ElideRight; font.family: "monospace" }
                                    RowLayout {
                                        spacing: 8
                                        ActionButton { text: "Expliquer"; onClicked: { win.page = 0; backend.analyzeIncident(modelData.id) } }
                                        ActionButton { text: "Proposer un correctif"; enabled: modelData.target !== ""; onClicked: backend.proposePatch(modelData.id) }
                                        ActionButton {
                                            text: "Voir et déployer"
                                            primary: true
                                            visible: modelData.status === "correctif prêt"
                                            onClicked: { patchDialog.patch = backend.incidentPatch(modelData.id); patchDialog.open() }
                                        }
                                    }
                                }
                            }
                        }
                        Text { text: "Correctifs déployés"; color: win.turquoise; font.pixelSize: 15; visible: backend.patches.length > 0 }
                        Repeater {
                            model: backend.patches
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Text { Layout.fillWidth: true; text: modelData.name + " — " + modelData.patch; color: win.white; font.pixelSize: 13 }
                                ActionButton { text: "Annuler le correctif"; onClicked: backend.rollbackPatch(modelData.name) }
                            }
                        }
                    }
                }
            }

            // ======================================================== Studio
            StudioPage { id: studio }

            // ======================================================== Capacités
            Glass {
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 12
                    Title { text: backend.toolCount + " capacités" }
                    Flow {
                        Layout.fillWidth: true; spacing: 6
                        Repeater { model: backend.categories; delegate: Pill { required property var modelData; text: modelData.label + " · " + modelData.count } }
                    }
                    TextField {
                        id: toolSearch
                        placeholderTextColor: win.muted
                        Layout.fillWidth: true
                        placeholderText: "Rechercher une capacité (ex. « disque », « manette », « Tor »)…"
                        color: win.white
                        background: Rectangle { radius: 12; color: win.veil(0.08); border.color: win.veil(0.2) }
                        onTextChanged: toolList.model = backend.searchTools(text)
                    }
                    ListView {
                        id: toolList
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 6
                        model: backend.searchTools("")
                        delegate: Rectangle {
                            required property var modelData
                            width: ListView.view.width
                            height: 58
                            radius: 12
                            color: win.veil(0.05)
                            opacity: modelData.available ? 1 : 0.5
                            RowLayout {
                                anchors.fill: parent; anchors.margins: 10; spacing: 10
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    RowLayout {
                                        Text { text: modelData.name; color: win.white; font.pixelSize: 14; font.family: "monospace" }
                                        Pill { text: modelData.category; tint: win.muted }
                                        Pill { visible: modelData.root; text: "administrateur"; tint: win.sand }
                                        Pill { visible: modelData.dangerous; text: "confirmation"; tint: win.coral }
                                    }
                                    Text { Layout.fillWidth: true; text: modelData.description; color: win.muted; font.pixelSize: 12; elide: Text.ElideRight }
                                }
                                ActionButton { text: modelData.ready ? "Exécuter" : "Demander à Djerba"; enabled: modelData.available; onClicked: { win.page = 0; backend.runTool(modelData.name) } }
                            }
                        }
                    }
                }
            }

            // ======================================================== Cerveau et réglages
            SettingsPage { }
        }
    }

    Onboarding { id: onboarding }

    // ---------------------------------------------------------------- Dialogues
    Dialog {
        id: patchDialog
        property var patch: ({})
        anchors.centerIn: parent
        width: Math.min(parent.width - 80, 900)
        height: Math.min(parent.height - 80, 640)
        modal: true
        title: "Correctif " + (patch.patch_id || "")
        background: Rectangle { radius: 18; color: win.surface; border.color: win.turquoise }
        contentItem: ColumnLayout {
            spacing: 10
            Repeater {
                model: patchDialog.patch.checks || []
                delegate: Text { required property var modelData; text: (modelData.success ? "✓ " : "✗ ") + modelData.check; color: modelData.success ? win.aqua : win.coral; font.pixelSize: 13 }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                TextArea { readOnly: true; text: patchDialog.patch.diff || ""; color: win.white; font.family: "monospace"; font.pixelSize: 12; wrapMode: TextArea.NoWrap; background: Rectangle { color: win.veil(0.08); radius: 8 } }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ActionButton { text: "Fermer"; onClicked: patchDialog.close() }
                ActionButton { text: "Déployer (mot de passe administrateur)"; primary: true; onClicked: { backend.deployPatch(patchDialog.patch.patch_id); patchDialog.close() } }
            }
        }
    }

    Dialog {
        id: iterateDialog
        property string slug: ""
        anchors.centerIn: parent
        width: 560
        modal: true
        title: "Modifier « " + slug + " »"
        background: Rectangle { radius: 18; color: win.surface; border.color: win.turquoise }
        contentItem: ColumnLayout {
            TextArea { id: iterateText; placeholderTextColor: win.muted; Layout.fillWidth: true; Layout.preferredHeight: 120; placeholderText: "Ex. : ajoute un tableau des meilleurs scores"; color: win.white; wrapMode: TextArea.Wrap }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                ActionButton { text: "Annuler"; onClicked: iterateDialog.close() }
                ActionButton { text: "Appliquer"; primary: true; onClicked: { backend.iterateApp(iterateDialog.slug, iterateText.text); iterateText.text = ""; iterateDialog.close() } }
            }
        }
    }

    // Notification flottante
    Rectangle {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 26
        width: Math.min(parent.width - 60, toastText.implicitWidth + 40)
        height: toastText.implicitHeight + 22
        radius: 16
        color: win.surface
        border.color: win.turquoise
        opacity: 0
        visible: opacity > 0
        Text { id: toastText; anchors.centerIn: parent; width: parent.width - 30; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: win.white; font.pixelSize: 14 }
        SequentialAnimation {
            id: toastAnimation
            NumberAnimation { target: toast; property: "opacity"; to: 1; duration: 180 }
            PauseAnimation { duration: 4200 }
            NumberAnimation { target: toast; property: "opacity"; to: 0; duration: 400 }
        }
    }

    Connections {
        target: backend
        function onNotify(message) { if (message !== "") { toastText.text = message; toastAnimation.restart() } }
        function onProgress(message, percent) { win.progressText = message; win.progressValue = percent }
    }
}
