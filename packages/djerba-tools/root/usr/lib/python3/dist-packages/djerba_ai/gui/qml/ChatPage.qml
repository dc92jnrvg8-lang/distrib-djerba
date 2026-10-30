// SPDX-License-Identifier: GPL-3.0-or-later
// Discussion avec Djerba : réponses en continu, cartes d'outils, confirmations.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: page
    radius: 22
    color: win.veil(0.06)
    border.width: 1
    border.color: win.veil(0.13)

    function clear() { messages.clear() }

    function lastIndexOf(kind, tool) {
        for (var i = messages.count - 1; i >= 0; --i) {
            var item = messages.get(i)
            if (item.kind === kind && (tool === undefined || item.tool === tool) && item.status === "running")
                return i
        }
        return -1
    }

    ListModel { id: messages }

    component ChatButton: Button {
        id: chatButton
        property bool primary: false
        implicitHeight: 34
        font.pixelSize: 13
        contentItem: Text {
            text: chatButton.text; font: chatButton.font
            color: chatButton.primary ? win.textOnAccent : (chatButton.enabled ? win.white : win.muted)
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 11
            color: chatButton.primary ? (chatButton.down ? Qt.darker(win.turquoise, 1.2) : win.turquoise)
                                      : win.veil(chatButton.down ? 0.18 : (chatButton.hovered ? 0.13 : 0.07))
            opacity: chatButton.enabled ? 1 : 0.45
            border.width: chatButton.primary ? 0 : 1
            border.color: win.veil(0.18)
        }
    }

    Connections {
        target: backend
        function onMessageAdded(role, text) {
            if (role === "final") {
                var last = messages.count - 1
                if (last >= 0 && messages.get(last).kind === "assistant" && messages.get(last).status === "streaming") {
                    messages.setProperty(last, "text", text)
                    messages.setProperty(last, "status", "done")
                } else {
                    messages.append({ kind: "assistant", text: text, status: "done", tool: "", detail: "", requestId: 0, runId: 0, root: false, risk: "" })
                }
                return
            }
            messages.append({ kind: role, text: text, status: "done", tool: "", detail: "", requestId: 0, runId: 0, root: false, risk: "" })
            list.positionViewAtEnd()
        }
        function onTokenAppended(text) {
            var last = messages.count - 1
            if (last >= 0 && messages.get(last).kind === "assistant" && messages.get(last).status === "streaming")
                messages.setProperty(last, "text", messages.get(last).text + text)
            else
                messages.append({ kind: "assistant", text: text, status: "streaming", tool: "", detail: "", requestId: 0, runId: 0, root: false, risk: "" })
            list.positionViewAtEnd()
        }
        function onToolStarted(tool, label, root) {
            var last = messages.count - 1
            if (last >= 0 && messages.get(last).kind === "assistant" && messages.get(last).status === "streaming") {
                if (messages.get(last).text.trim() === "") messages.remove(last)
                else messages.setProperty(last, "status", "done")
            }
            messages.append({ kind: "tool", text: label, status: "running", tool: tool, detail: "", requestId: 0, runId: 0, root: root, risk: "" })
            list.positionViewAtEnd()
        }
        function onToolFinished(tool, success, message, runId) {
            var index = page.lastIndexOf("tool", tool)
            if (index < 0) return
            messages.setProperty(index, "status", success ? "success" : "failure")
            messages.setProperty(index, "detail", message)
            messages.setProperty(index, "runId", runId)
        }
        function onConfirmRequested(requestId, tool, label, params, root, risk) {
            messages.append({ kind: "confirm", text: label, status: "running", tool: tool, detail: params, requestId: requestId, runId: 0, root: root, risk: risk })
            list.positionViewAtEnd()
        }
        function onSuggestions(items) {
            for (var i = 0; i < items.length; ++i)
                messages.append({ kind: "suggestion", text: items[i].description, status: "done", tool: items[i].tool, detail: items[i].category, requestId: 0, runId: 0, root: items[i].root, risk: "" })
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        // Bandeau : moteur absent
        Rectangle {
            Layout.fillWidth: true
            visible: !backend.engineAvailable
            radius: 14
            implicitHeight: 52
            color: Qt.rgba(1, 0.48, 0.35, 0.14)
            border.color: Qt.rgba(1, 0.48, 0.35, 0.5)
            RowLayout {
                anchors.fill: parent; anchors.margins: 12
                Text { Layout.fillWidth: true; text: "Moteur d'IA indisponible : Djerba propose directement les actions correspondant à vos demandes."; color: win.white; font.pixelSize: 13; wrapMode: Text.WordWrap }
                ChatButton { text: "Configurer"; onClicked: win.page = win.pages.indexOf("settings") }
            }
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 10
            model: messages

            // Écran d'accueil
            Column {
                anchors.centerIn: parent
                visible: messages.count === 0
                spacing: 16
                width: Math.min(parent.width - 40, 620)
                Text { width: parent.width; horizontalAlignment: Text.AlignHCenter; text: "Bonjour, je suis Djerba."; color: win.white; font.pixelSize: 30; font.weight: Font.Light }
                Text {
                    width: parent.width; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                    text: "Je connais votre système et je peux l'administrer pour vous : maintenance, jeu, vie privée, applications, matériel. Je vous demande toujours avant une action sensible."
                    color: win.muted; font.pixelSize: 14
                }
                Flow {
                    width: parent.width; spacing: 8
                    Repeater {
                        model: ["Fais un bilan de santé de mon PC", "Optimise mon PC pour jouer", "Active le mode anonyme",
                                "Installe Steam et Lutris", "Crée un jeu Snake", "Pourquoi mon PC est lent ?"]
                        delegate: Rectangle {
                            required property string modelData
                            radius: 14; height: 34; width: chipLabel.implicitWidth + 26
                            color: win.veil(chipHover.hovered ? 0.14 : 0.07)
                            border.color: Qt.rgba(0, 0.76, 0.8, 0.4)
                            Text { id: chipLabel; anchors.centerIn: parent; text: modelData; color: win.white; font.pixelSize: 13 }
                            HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: backend.send(modelData) }
                        }
                    }
                }
            }

            delegate: Item {
                id: row
                required property int index
                required property string kind
                required property string text
                required property string status
                required property string tool
                required property string detail
                required property int requestId
                required property int runId
                required property bool root
                required property string risk
                width: ListView.view.width
                height: bubble.height
                opacity: 0
                Component.onCompleted: appear.start()
                NumberAnimation { id: appear; target: row; property: "opacity"; to: 1; duration: 220 }

                Rectangle {
                    id: bubble
                    anchors.right: row.kind === "user" ? parent.right : undefined
                    width: row.kind === "user" ? Math.min(content.implicitWidth + 32, parent.width * 0.75) : parent.width * (row.kind === "assistant" ? 0.88 : 0.8)
                    height: content.implicitHeight + 24
                    radius: 16
                    color: row.kind === "user" ? Qt.rgba(0, 0.76, 0.8, 0.22)
                         : row.kind === "assistant" ? win.veil(0.07)
                         : row.kind === "confirm" ? Qt.rgba(0.96, 0.88, 0.76, 0.12)
                         : win.veil(0.04)
                    border.width: row.kind === "assistant" ? 0 : 1
                    border.color: row.kind === "confirm" ? win.sand
                                : row.status === "failure" ? win.coral
                                : win.veil(0.12)

                    ColumnLayout {
                        id: content
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 12
                        spacing: 6

                        // Texte (utilisateur, assistant)
                        TextEdit {
                            Layout.fillWidth: true
                            visible: row.kind === "user" || row.kind === "assistant"
                            text: row.text + (row.status === "streaming" ? " ▍" : "")
                            textFormat: row.kind === "assistant" ? TextEdit.MarkdownText : TextEdit.PlainText
                            color: win.white; font.pixelSize: 14
                            wrapMode: TextEdit.Wrap; readOnly: true; selectByMouse: true
                        }

                        // Carte d'outil
                        RowLayout {
                            visible: row.kind === "tool"
                            Layout.fillWidth: true
                            spacing: 10
                            BusyIndicator { running: row.status === "running"; visible: running; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
                            Text { visible: row.status !== "running"; text: row.status === "success" ? "✓" : "✗"; color: row.status === "success" ? win.aqua : win.coral; font.pixelSize: 18 }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: row.text + (row.root ? "  · administrateur" : ""); color: win.white; font.pixelSize: 13; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                Text { visible: row.detail !== ""; text: row.detail; color: win.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 6; elide: Text.ElideRight }
                            }
                            Text {
                                visible: row.runId > 0
                                text: "Utile"; color: win.aqua; font.pixelSize: 12; font.underline: upHover.hovered
                                HoverHandler { id: upHover; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: { backend.feedback(row.runId, true); messages.setProperty(row.index, "runId", 0) } }
                            }
                            Text {
                                visible: row.runId > 0
                                text: "Pas utile"; color: win.muted; font.pixelSize: 12; font.underline: downHover.hovered
                                HoverHandler { id: downHover; cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: { backend.feedback(row.runId, false); messages.setProperty(row.index, "runId", 0) } }
                            }
                        }

                        // Demande de confirmation
                        ColumnLayout {
                            visible: row.kind === "confirm"
                            Layout.fillWidth: true
                            Text { text: "Djerba souhaite : " + row.text; color: win.white; font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Text { visible: row.detail !== "{}"; text: "Paramètres : " + row.detail; color: win.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                            Text { visible: row.root; text: "Votre mot de passe administrateur sera demandé."; color: win.sand; font.pixelSize: 12 }
                            Text { visible: row.risk === "élevé"; text: "Action à haut risque : validation explicite requise (tapez CONFIRMER)."; color: win.coral; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            TextField {
                                id: phrase
                                visible: row.risk === "élevé" && row.status === "running"
                                Layout.preferredWidth: 220
                                placeholderText: "CONFIRMER"
                                placeholderTextColor: win.muted
                                color: win.white
                                background: Rectangle { radius: 8; color: win.veil(0.07); border.color: win.coral }
                            }
                            RowLayout {
                                visible: row.status === "running"
                                ChatButton {
                                    text: "Autoriser"; primary: true
                                    enabled: row.risk !== "élevé" || phrase.text.trim().toUpperCase() === "CONFIRMER"
                                    onClicked: {
                                        if (row.risk === "élevé") {
                                            if (!backend.validateHighRisk(row.requestId, phrase.text)) return
                                        } else {
                                            backend.answerConfirmation(row.requestId, true)
                                        }
                                        messages.setProperty(row.index, "status", "accepted")
                                    }
                                }
                                ChatButton { text: "Refuser"; onClicked: { messages.setProperty(row.index, "status", "refused"); backend.answerConfirmation(row.requestId, false) } }
                            }
                            Text { visible: row.status !== "running"; text: row.status === "accepted" ? "Autorisé" : "Refusé"; color: win.muted; font.pixelSize: 12 }
                        }

                        // Suggestion (mode simplifié)
                        RowLayout {
                            visible: row.kind === "suggestion"
                            Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: row.text; color: win.white; font.pixelSize: 13; wrapMode: Text.WordWrap }
                            ChatButton { text: "Exécuter"; onClicked: backend.runTool(row.tool) }
                        }
                    }
                }
            }
        }

        // Activité de l'agent : toujours visible pendant le travail (phase, durée, file d'attente, Arrêter).
        Rectangle {
            id: activityBar
            objectName: "activityBar"
            Layout.fillWidth: true
            visible: backend.busy
            implicitHeight: 44
            radius: 12
            color: win.field
            border.width: 1
            border.color: win.turquoise
            property int seconds: 0
            Timer {
                interval: 1000; repeat: true; triggeredOnStart: true
                running: backend.busy
                onTriggered: activityBar.seconds = Math.max(0, Math.round((Date.now() - backend.busySince) / 1000))
            }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 6; spacing: 10
                Row {
                    spacing: 4
                    Repeater {
                        model: 3
                        Rectangle {
                            required property int index
                            width: 7; height: 7; radius: 4; color: win.turquoise
                            SequentialAnimation on opacity {
                                running: backend.busy; loops: Animation.Infinite
                                PauseAnimation { duration: index * 160 }
                                NumberAnimation { to: 0.25; duration: 380 }
                                NumberAnimation { to: 1; duration: 380 }
                                PauseAnimation { duration: (2 - index) * 160 }
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: backend.activityText + (backend.busySince > 0 ? "  ·  " + activityBar.seconds + " s" : "")
                          + (backend.queued > 0 ? "  ·  " + backend.queued + " message(s) en attente" : "")
                    color: win.white; font.pixelSize: 13; elide: Text.ElideRight
                }
                Button {
                    id: stopButton
                    objectName: "stopButton"
                    text: "Arrêter"
                    enabled: backend.activity !== "s'arrête"
                    implicitHeight: 32
                    onClicked: backend.stop()
                    contentItem: Text {
                        text: stopButton.text; color: win.textOnCoral; font.pixelSize: 13; font.weight: Font.DemiBold
                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        radius: 9; implicitWidth: 92
                        color: stopButton.down ? Qt.darker(win.coral, 1.2) : win.coral
                        opacity: stopButton.enabled ? 1 : 0.6
                    }
                }
            }
        }

        // Saisie : toujours active ; pendant le travail, le message part en file d'attente.
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            ScrollView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(140, Math.max(48, input.implicitHeight + 8))
                TextArea {
                    id: input
                    placeholderText: backend.busy ? "Écrivez la suite : elle partira quand Djerba aura fini"
                                                  : "Demandez quelque chose à Djerba…  (Entrée pour envoyer, Maj+Entrée pour un saut de ligne)"
                    color: win.white; font.pixelSize: 14; wrapMode: TextArea.Wrap
                    placeholderTextColor: win.muted
                    background: Rectangle { radius: 14; color: win.field; border.color: input.activeFocus ? win.turquoise : win.veil(0.25) }
                    Keys.onReturnPressed: function (event) {
                        if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return }
                        backend.send(input.text); input.text = ""
                    }
                }
            }
            ChatButton {
                text: backend.busy ? "Mettre en file" : "Envoyer"
                enabled: input.text.trim() !== ""
                primary: true
                implicitHeight: 46
                implicitWidth: 130
                onClicked: { backend.send(input.text); input.text = "" }
            }
        }
    }
}
