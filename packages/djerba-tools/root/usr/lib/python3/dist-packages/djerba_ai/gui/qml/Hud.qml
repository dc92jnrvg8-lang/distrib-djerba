// SPDX-License-Identifier: GPL-3.0-or-later
// Anneau d'action de Djerba (Super+Espace) : actions contextuelles disposées en
// anneau autour d'un champ de saisie, selon la fenêtre active (KWin ou Mutter).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts

Window {
    id: win
    width: 720
    height: 720
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    color: "transparent"
    title: "Djerba — anneau d'action"
    visible: false

    SystemPalette { id: system; colorGroup: SystemPalette.Active }
    readonly property bool dark: backend.followsDesktopTheme ? backend.prefersDark : system.window.hslLightness < 0.5
    readonly property color night: dark ? "#070B18" : "#F1F5F9"
    readonly property color surface: dark ? "#0B1426" : "#FFFFFF"
    readonly property color turquoise: dark ? "#00C2CB" : "#0E9AA7"
    readonly property color aqua: dark ? "#5EEAD4" : "#0F766E"
    readonly property color coral: dark ? "#FF7A59" : "#C2410C"
    readonly property color sand: dark ? "#F4E1C1" : "#92400E"
    readonly property color white: dark ? "#F8FAFC" : "#0F172A"
    readonly property color muted: dark ? "#94A3B8" : "#475569"
    function veil(alpha) { return dark ? Qt.rgba(1, 1, 1, alpha) : Qt.rgba(0.06, 0.09, 0.16, alpha * 0.7) }

    property bool active: false
    property string answer: ""
    property real reveal: 0
    property int pendingRequest: -1
    property string pendingLabel: ""
    property string pendingRisk: ""
    readonly property real radius: 250

    function present() {
        answer = ""; active = false; pendingRequest = -1; input.text = ""
        backend.refreshHud()
        var screen = win.screen
        x = screen.virtualX + (screen.width - width) / 2
        y = screen.virtualY + (screen.height - height) / 2
        show(); raise(); requestActivate()
        input.forceActiveFocus()
        revealAnimation.restart()
        backend.warmup()
    }
    function ask(prompt) {
        if (prompt.trim() === "" || backend.busy) return
        answer = ""; active = true
        backend.newConversation()
        backend.send(prompt)
    }

    NumberAnimation { id: revealAnimation; target: win; property: "reveal"; from: 0; to: 1; duration: 320; easing.type: Easing.OutBack }

    Connections {
        target: backend
        function onHudRequested() { win.visible ? win.hide() : win.present() }
        function onTokenAppended(text) { if (win.active) win.answer += text }
        function onMessageAdded(role, text) { if (win.active && role === "final") { win.answer = text; win.active = false } }
        function onConfirmRequested(requestId, tool, label, params, root, risk) {
            if (win.visible) { win.pendingRequest = requestId; win.pendingRisk = risk; win.pendingLabel = label + " — risque " + risk }
        }
    }

    Item {
        anchors.fill: parent
        Keys.onEscapePressed: win.hide()

        // Disque de fond translucide
        Rectangle {
            anchors.centerIn: parent
            width: win.radius * 2 + 150; height: width; radius: width / 2
            color: Qt.rgba(win.surface.r, win.surface.g, win.surface.b, 0.55)
            border.color: Qt.rgba(win.turquoise.r, win.turquoise.g, win.turquoise.b, 0.35)
            scale: 0.85 + 0.15 * win.reveal
            opacity: win.reveal
        }

        // Actions en anneau
        Repeater {
            model: backend.hudActions
            delegate: Rectangle {
                id: bubble
                required property var modelData
                required property int index
                readonly property real angle: -Math.PI / 2 + index * 2 * Math.PI / Math.max(1, backend.hudActions.length)
                width: 132; height: 64; radius: 22
                x: parent.width / 2 + Math.cos(angle) * win.radius * win.reveal - width / 2
                y: parent.height / 2 + Math.sin(angle) * win.radius * win.reveal - height / 2
                opacity: win.answer === "" && !win.active ? win.reveal : 0.15
                color: hover.hovered ? Qt.rgba(0, 0.76, 0.8, 0.35) : Qt.rgba(win.surface.r, win.surface.g, win.surface.b, 0.92)
                border.color: modelData.source === "agent" ? win.aqua : Qt.rgba(win.turquoise.r, win.turquoise.g, win.turquoise.b, 0.6)
                Behavior on color { ColorAnimation { duration: 120 } }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: win.ask(bubble.modelData.prompt) }
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 6; spacing: 2
                    Image { Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 22; Layout.preferredHeight: 22; source: "image://icon/" + bubble.modelData.icon; sourceSize: Qt.size(44, 44) }
                    Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; text: bubble.modelData.label; color: win.white; font.pixelSize: 12; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                }
            }
        }

        // Centre : saisie, état, réponse
        Rectangle {
            id: core
            anchors.centerIn: parent
            width: 300; height: win.answer !== "" || win.active || win.pendingRequest >= 0 ? 340 : 170
            radius: 28
            color: Qt.rgba(win.surface.r, win.surface.g, win.surface.b, 0.96)
            border.color: win.turquoise
            Behavior on height { NumberAnimation { duration: 180 } }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14; spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Rectangle {
                        width: 12; height: 12; radius: 6
                        color: backend.state === "ok" ? "#2DD4BF" : backend.state === "fallback" ? "#F59E0B" : backend.state === "busy" ? "#38BDF8" : "#F43F5E"
                        SequentialAnimation on opacity { running: backend.busy; loops: Animation.Infinite; NumberAnimation { to: 0.3; duration: 450 } NumberAnimation { to: 1; duration: 450 } }
                    }
                    Text { Layout.fillWidth: true; text: backend.hudContext; color: win.muted; font.pixelSize: 11; elide: Text.ElideRight }
                }
                TextField {
                    id: input
                    Layout.fillWidth: true
                    font.pixelSize: 16
                    color: win.white
                    placeholderText: "Demandez à Djerba…"
                    placeholderTextColor: win.muted
                    enabled: !backend.busy
                    background: Rectangle { radius: 12; color: win.veil(0.07) }
                    Keys.onEscapePressed: win.hide()
                    onAccepted: win.ask(text)
                }
                RowLayout {
                    visible: win.pendingRequest >= 0
                    Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: win.pendingLabel; color: win.pendingRisk === "élevé" ? win.coral : win.sand; font.pixelSize: 11; wrapMode: Text.WordWrap }
                }
                RowLayout {
                    visible: win.pendingRequest >= 0
                    TextField { id: phrase; visible: win.pendingRisk === "élevé"; Layout.fillWidth: true; placeholderText: "CONFIRMER"; color: win.white; background: Rectangle { radius: 8; color: win.veil(0.07); border.color: win.coral } }
                    ActionButton { text: "Refuser"; onClicked: { backend.answerConfirmation(win.pendingRequest, false); win.pendingRequest = -1 } }
                    ActionButton {
                        text: "Autoriser"; primary: true
                        enabled: win.pendingRisk !== "élevé" || phrase.text.trim().toUpperCase() === "CONFIRMER"
                        onClicked: {
                            if (win.pendingRisk === "élevé") { if (!backend.validateHighRisk(win.pendingRequest, phrase.text)) return }
                            else backend.answerConfirmation(win.pendingRequest, true)
                            win.pendingRequest = -1; phrase.text = ""
                        }
                    }
                }
                ScrollView {
                    visible: win.answer !== "" || win.active
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        readOnly: true; text: win.answer || "…"; textFormat: TextEdit.MarkdownText; wrapMode: TextEdit.Wrap
                        color: win.white; font.pixelSize: 13; selectByMouse: true
                        background: Item {}
                    }
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "Studio ↗"; color: win.turquoise; font.pixelSize: 12; font.underline: studioHover.hovered
                    HoverHandler { id: studioHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: { backend.openMain("dashboard"); win.hide() } }
                }
            }
        }
    }
}
