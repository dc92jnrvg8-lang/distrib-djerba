// SPDX-License-Identifier: GPL-3.0-or-later
// Mode Studio : applications générées, historique des discussions, bac à sable
// et audit en direct des commandes privilégiées.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Glass {
    id: studio
    property int tab: 0

    component TabChip: Rectangle {
        id: chip
        property string text
        property int index
        implicitWidth: chipText.implicitWidth + 28; implicitHeight: 34; radius: 17
        color: studio.tab === index ? Qt.rgba(0, 0.76, 0.8, 0.25) : win.veil(chipHover.hovered ? 0.1 : 0.05)
        border.color: studio.tab === index ? win.turquoise : "transparent"
        Text { id: chipText; anchors.centerIn: parent; text: chip.text; color: win.white; font.pixelSize: 13 }
        HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: studio.tab = chip.index }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 22; spacing: 14
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Title { text: "Studio"; Layout.fillWidth: true }
            TabChip { text: "Applications"; index: 0 }
            TabChip { text: "Historique"; index: 1 }
            TabChip { text: "Bac à sable"; index: 2 }
            TabChip { text: "Audit en direct"; index: 3 }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: studio.tab

            // ------------------------------------------------ Applications
            ColumnLayout {
                spacing: 14
                Body { text: "Décrivez une application ou un jeu : Djerba l'écrit, la teste, l'enregistre avec son historique et l'ajoute au menu. Chaque application tourne isolée (scope systemd, sans accès au démon administrateur). Hors ligne, les modèles « Snake », « calculatrice » et « chronomètre » restent disponibles." }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    TextField {
                        id: studioInput
                        placeholderTextColor: win.muted
                        Layout.fillWidth: true
                        placeholderText: "Ex. : Crée un jeu Snake en PyQt6, une calculatrice HTML5, une liste de tâches…"
                        color: win.white
                        background: Rectangle { radius: 12; color: win.veil(0.08); border.color: win.veil(0.2) }
                        onAccepted: generate.clicked()
                    }
                    ComboBox { id: kind; model: ["auto", "pyqt6", "qml", "html"]; implicitWidth: 110 }
                    ActionButton { id: generate; text: "Créer"; primary: true; onClicked: backend.generateApp(studioInput.text, kind.currentText) }
                }
                GridView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    cellWidth: Math.floor(width / Math.max(1, Math.floor(width / 300)))
                    cellHeight: 150
                    model: backend.generatedApps
                    delegate: Item {
                        required property var modelData
                        width: GridView.view.cellWidth; height: 150
                        Glass {
                            anchors.fill: parent; anchors.margins: 6
                            ColumnLayout {
                                anchors.fill: parent; anchors.margins: 14; spacing: 6
                                RowLayout {
                                    Text { Layout.fillWidth: true; text: modelData.name; color: win.white; font.pixelSize: 16; font.weight: Font.DemiBold; elide: Text.ElideRight }
                                    Pill { text: modelData.kind }
                                }
                                Text { text: "modifiée " + modelData.updated; color: win.muted; font.pixelSize: 12 }
                                Item { Layout.fillHeight: true }
                                Flow {
                                    Layout.fillWidth: true; spacing: 6
                                    ActionButton { text: "Lancer"; primary: true; onClicked: backend.runApp(modelData.slug) }
                                    ActionButton { text: "Live"; onClicked: backend.runAppLive(modelData.slug) }
                                    ActionButton { text: "Code"; onClicked: backend.openAppFolder(modelData.slug) }
                                    ActionButton { text: "Modifier"; onClicked: { iterateDialog.slug = modelData.slug; iterateDialog.open() } }
                                    ActionButton { text: "Supprimer"; onClicked: backend.deleteApp(modelData.slug) }
                                }
                            }
                        }
                    }
                }
            }

            // ------------------------------------------------ Historique
            ColumnLayout {
                spacing: 10
                Body { text: "Vos discussions, par moteur d'IA (chiffrées sur cette machine, secrets masqués). Une conversation se reprend avec n'importe quel moteur ; « Annuler » défait toutes ses modifications du système." }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    ComboBox {
                        id: historyEngine
                        model: backend.historyEngines
                        textRole: "name"
                        valueRole: "id"
                        Accessible.name: "Filtrer par moteur d'IA"
                        onActivated: backend.filterHistory(currentValue, historySearch.text)
                    }
                    TextField {
                        id: historySearch
                        Layout.fillWidth: true
                        placeholderText: "Rechercher dans l'historique…"
                        Accessible.name: "Rechercher dans l'historique"
                        onAccepted: backend.filterHistory(historyEngine.currentValue || "", text)
                    }
                    ActionButton {
                        visible: (historyEngine.currentValue || "") !== ""
                        text: backend.historyEngines[historyEngine.currentIndex] && backend.historyEngines[historyEngine.currentIndex].mode === "isole" ? "Mémoire : isolée" : "Mémoire : partagée"
                        onClicked: backend.setEngineMemory(historyEngine.currentValue, text === "Mémoire : isolée" ? "partage" : "isole")
                    }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 6
                    model: backend.conversations
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width; height: modelData.snippet ? 66 : 48; radius: 12
                        color: win.veil(rowHover.hovered ? 0.1 : 0.05)
                        HoverHandler { id: rowHover }
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 10
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 2
                                TextField {
                                    id: titleField
                                    Layout.fillWidth: true
                                    text: (modelData.pinned ? "📌 " : "") + modelData.title
                                    readOnly: true
                                    color: win.white; font.pixelSize: 14
                                    background: null
                                    Accessible.name: "Titre de la conversation"
                                    onAccepted: { readOnly = true; backend.renameConversation(modelData.id, text.replace("📌 ", "")) }
                                }
                                Text { visible: modelData.snippet !== ""; Layout.fillWidth: true; text: modelData.snippet; color: win.white; opacity: 0.6; font.pixelSize: 12; elide: Text.ElideRight }
                            }
                            Pill { visible: modelData.engine !== ""; text: modelData.engine }
                            ActionButton { text: "Ouvrir"; onClicked: { chat.clear(); backend.openConversation(modelData.id); win.page = 0 } }
                            ActionButton { text: modelData.pinned ? "Désépingler" : "Épingler"; onClicked: backend.pinConversation(modelData.id, !modelData.pinned) }
                            ActionButton { text: "Renommer"; onClicked: { titleField.readOnly = false; titleField.text = modelData.title; titleField.forceActiveFocus(); titleField.selectAll() } }
                            ActionButton { text: "Exporter"; onClicked: backend.exportConversation(modelData.id, "markdown") }
                            ActionButton { text: "Annuler"; onClicked: backend.undoConversation(modelData.id) }
                            ActionButton { text: "Supprimer"; onClicked: backend.deleteConversation(modelData.id) }
                        }
                    }
                }
            }

            // ------------------------------------------------ Bac à sable
            ColumnLayout {
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Body { text: "Dépôt Git /var/lib/djerba-ai/sandbox : chaque correctif et chaque capacité générée y est vérifié (syntaxe, analyse, exécution sans privilèges, 15 s au plus, 3 essais). Un échec est annulé par « git reset --hard »." }
                    ActionButton { text: "Actualiser"; onClicked: backend.refreshIncidents() }
                }
                Text { visible: backend.sandboxHistory.length === 0; text: "Le bac à sable est vide."; color: win.muted; font.pixelSize: 13 }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: backend.sandboxHistory
                    delegate: RowLayout {
                        required property var modelData
                        width: ListView.view.width
                        spacing: 10
                        Text { text: modelData.commit; color: win.turquoise; font.family: "monospace"; font.pixelSize: 12 }
                        Text { Layout.fillWidth: true; text: modelData.subject; color: win.white; font.pixelSize: 13; elide: Text.ElideRight }
                        Text { text: modelData.when; color: win.muted; font.pixelSize: 12 }
                    }
                }
            }

            // ------------------------------------------------ Audit en direct
            ColumnLayout {
                id: auditTab
                spacing: 10
                Timer {
                    interval: 2000; repeat: true; triggeredOnStart: true
                    running: studio.tab === 3 && studio.visible && win.visible
                    onTriggered: backend.refreshAudit()
                }
                Body { text: "Journal /var/log/djerba/audit.log : chaque action administrateur demandée par Djerba (appelant, outil, paramètres masqués, résultat). Actualisé toutes les 2 secondes." }
                ListView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 3
                    model: backend.auditEntries
                    delegate: RowLayout {
                        required property var modelData
                        width: ListView.view.width
                        spacing: 10
                        Rectangle { width: 8; height: 8; radius: 4; color: modelData.success ? win.aqua : win.coral }
                        Text { text: modelData.time.substring(11, 19); color: win.muted; font.family: "monospace"; font.pixelSize: 12 }
                        Text { text: modelData.event; color: win.turquoise; font.pixelSize: 12; Layout.preferredWidth: 110; elide: Text.ElideRight }
                        Text { text: modelData.what; color: win.white; font.family: "monospace"; font.pixelSize: 12; Layout.preferredWidth: 190; elide: Text.ElideRight }
                        Text { Layout.fillWidth: true; text: modelData.message; color: win.muted; font.pixelSize: 12; elide: Text.ElideRight }
                    }
                }
            }
        }
    }
}
