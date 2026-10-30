// SPDX-License-Identifier: GPL-3.0-or-later
// Barre flottante « Spotlight » (Meta+Espace) et analyse du presse-papiers (Meta+Maj+C).
import QtQuick
import QtQuick.Window
import QtQuick.Controls
import QtQuick.Layouts

Window {
    id: win
    width: 760
    height: Math.min(card.implicitHeight + 40, 620)
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
    color: "transparent"
    title: "Djerba Spotlight"
    visible: false

    // Même palette adaptative que la fenêtre principale (composants partagés).
    SystemPalette { id: system; colorGroup: SystemPalette.Active }
    // Sous GNOME, Qt ne suit pas toujours le thème : la préférence GSettings fait foi.
    readonly property string themeChoice: backend.settings["ui.theme"] || "auto"
    readonly property bool dark: themeChoice === "sombre" ? true : themeChoice === "clair" ? false
        : (backend.followsDesktopTheme ? backend.prefersDark : system.window.hslLightness < 0.5)
    // Mêmes jetons que la fenêtre principale (gui/themes/*.json, contraste AA vérifié par les tests).
    readonly property var theme: dark ? backend.themes["sombre"] : backend.themes["clair"]
    readonly property color night: theme.night
    readonly property color deep: theme.deep
    readonly property color deepEnd: theme.deepEnd
    readonly property color surface: theme.surface
    readonly property color field: theme.field
    readonly property color turquoise: theme.turquoise
    readonly property color accentText: theme.accentText
    readonly property color textOnAccent: theme.textOnAccent
    readonly property color aqua: theme.aqua
    readonly property color coral: theme.coral
    readonly property color textOnCoral: theme.textOnCoral
    readonly property color sand: theme.sand
    readonly property color white: theme.white
    readonly property color muted: theme.muted
    readonly property bool translucent: backend.settings["ui.translucent"] === true
    readonly property real panelOpacity: translucent ? 0.62 : theme.panelOpacity
    function veil(alpha) { return dark ? Qt.rgba(1, 1, 1, alpha) : Qt.rgba(0.06, 0.09, 0.16, alpha * 0.7) }

    property string mode: "ask"
    property string clipboard: ""
    property string answer: ""
    property bool active: false
    property var steps: []
    property int pendingRequest: -1
    property string pendingLabel: ""
    property string pendingRisk: ""

    function present(text, newMode) {
        mode = newMode
        clipboard = newMode === "clipboard" ? text : ""
        answer = ""; steps = []; pendingRequest = -1; active = false
        input.text = ""
        var screen = win.screen
        x = screen.virtualX + (screen.width - width) / 2
        y = screen.virtualY + screen.height / 5
        show(); raise(); requestActivate()
        input.forceActiveFocus()
        backend.warmup()   // préchargement du modèle (keep_alive) pendant la saisie
    }
    function ask(text) {
        if (text.trim() === "" || backend.busy) return
        answer = ""; steps = []; active = true
        backend.newConversation()
        backend.send(text)
    }

    Connections {
        target: backend
        function onSpotlightRequested(text, mode) { win.visible && mode === win.mode && mode === "ask" ? win.hide() : win.present(text, mode) }
        function onTokenAppended(text) { if (win.active) win.answer += text }
        function onMessageAdded(role, text) { if (win.active && role === "final") { win.answer = text; win.active = false } }
        function onToolStarted(tool, label, root) { if (win.active) win.steps = win.steps.concat([label]) }
        function onConfirmRequested(requestId, tool, label, params, root, risk) {
            if (win.visible) {
                win.pendingRequest = requestId; win.pendingRisk = risk
                win.pendingLabel = label + (root ? " (administrateur)" : "") + " — risque " + risk
            }
        }
    }

    onActiveFocusItemChanged: if (activeFocusItem === null && !backend.busy && pendingRequest < 0) hide()

    Rectangle {
        id: card
        anchors.fill: parent
        anchors.margins: 20
        implicitHeight: column.implicitHeight + 32
        radius: 22
        color: Qt.rgba(win.surface.r, win.surface.g, win.surface.b, 0.9)
        border.color: Qt.rgba(win.turquoise.r, win.turquoise.g, win.turquoise.b, 0.55)
        border.width: 1

        Keys.onEscapePressed: win.hide()

        ColumnLayout {
            id: column
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 16
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Rectangle {
                    width: 14; height: 14; radius: 7
                    color: backend.state === "ok" ? "#2DD4BF" : backend.state === "fallback" ? "#F59E0B" : backend.state === "busy" ? "#38BDF8" : "#F43F5E"
                    ToolTip.visible: dotHover.hovered; ToolTip.text: backend.stateText
                    HoverHandler { id: dotHover }
                    SequentialAnimation on opacity {
                        running: backend.busy; loops: Animation.Infinite
                        NumberAnimation { to: 0.3; duration: 450 } NumberAnimation { to: 1; duration: 450 }
                    }
                }
                TextField {
                    id: input
                    Layout.fillWidth: true
                    font.pixelSize: 20
                    color: win.white
                    placeholderTextColor: win.muted
                    placeholderText: win.mode === "clipboard" ? "Que faire de ce texte ?" : "Demandez à Djerba…"
                    background: Item {}
                    enabled: !backend.busy
                    Keys.onEscapePressed: win.hide()
                    onAccepted: win.ask(win.mode === "clipboard" ? text + "\n\n« " + win.clipboard + " »" : text)
                }
                Text {
                    text: "Studio ↗"; color: win.turquoise; font.pixelSize: 13; font.underline: studioHover.hovered
                    HoverHandler { id: studioHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: { backend.openMain("chat"); win.hide() } }
                }
            }

            // Presse-papiers
            Rectangle {
                visible: win.mode === "clipboard"
                Layout.fillWidth: true
                implicitHeight: Math.min(clipText.implicitHeight + 16, 110)
                radius: 12; color: win.veil(0.06); clip: true
                Text { id: clipText; anchors.fill: parent; anchors.margins: 8; text: win.clipboard || "(presse-papiers vide)"; color: win.muted; font.pixelSize: 12; wrapMode: Text.WrapAnywhere; elide: Text.ElideRight; maximumLineCount: 5 }
            }
            Flow {
                visible: win.mode === "clipboard" && win.answer === "" && !win.active
                Layout.fillWidth: true
                spacing: 8
                Repeater {
                    model: [["Expliquer", "Explique simplement ce texte"], ["Résumer", "Résume ce texte en quelques points"],
                            ["Traduire", "Traduis ce texte en français (ou en anglais s'il est déjà en français)"],
                            ["Corriger", "Corrige l'orthographe et la grammaire de ce texte"], ["Commande", "Explique ce que fait cette commande et si elle est sûre"]]
                    delegate: ActionButton { required property var modelData; text: modelData[0]; onClicked: win.ask(modelData[1] + " :\n\n« " + win.clipboard + " »") }
                }
            }

            // Étapes (outils) et confirmation
            Repeater {
                model: win.steps
                delegate: Text { required property var modelData; text: "› " + modelData; color: win.muted; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
            }
            RowLayout {
                visible: win.pendingRequest >= 0
                Layout.fillWidth: true
                spacing: 8
                Text { Layout.fillWidth: true; text: "Djerba souhaite : " + win.pendingLabel; color: win.pendingRisk === "élevé" ? win.coral : win.sand; font.pixelSize: 13; wrapMode: Text.WordWrap }
                TextField {
                    id: phrase
                    visible: win.pendingRisk === "élevé"
                    Layout.preferredWidth: 140
                    placeholderText: "CONFIRMER"
                    placeholderTextColor: win.muted
                    color: win.white
                    background: Rectangle { radius: 8; color: win.veil(0.07); border.color: win.coral }
                }
                ActionButton { text: "Refuser"; onClicked: { backend.answerConfirmation(win.pendingRequest, false); win.pendingRequest = -1 } }
                ActionButton {
                    text: "Autoriser"; primary: true
                    enabled: win.pendingRisk !== "élevé" || phrase.text.trim().toUpperCase() === "CONFIRMER"
                    onClicked: {
                        if (win.pendingRisk === "élevé") {
                            if (!backend.validateHighRisk(win.pendingRequest, phrase.text)) return
                        } else {
                            backend.answerConfirmation(win.pendingRequest, true)
                        }
                        win.pendingRequest = -1; phrase.text = ""
                    }
                }
            }

            // Réponse
            ScrollView {
                visible: win.answer !== ""
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(answerText.implicitHeight + 8, 360)
                clip: true
                TextArea {
                    id: answerText
                    readOnly: true
                    text: win.answer
                    textFormat: TextEdit.MarkdownText
                    wrapMode: TextEdit.Wrap
                    color: win.white
                    font.pixelSize: 14
                    background: Item {}
                    selectByMouse: true
                }
            }
            Text {
                Layout.fillWidth: true
                visible: win.answer === "" && !win.active && win.mode === "ask"
                text: "Entrée pour envoyer · Échap pour fermer · " + backend.stateText
                color: win.muted; font.pixelSize: 11
            }
        }
    }
}
