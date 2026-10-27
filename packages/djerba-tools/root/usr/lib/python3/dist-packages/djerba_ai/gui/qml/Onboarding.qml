// SPDX-License-Identifier: GPL-3.0-or-later
// Assistant de premier lancement : présentation, choix du cerveau, raccourcis, confidentialité.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Popup {
    id: wizard
    property int step: 0
    readonly property int steps: 4
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(parent.width - 80, 720)
    height: Math.min(parent.height - 80, 540)
    modal: true
    closePolicy: Popup.NoAutoClose
    visible: win.visible && backend.settings["ui.onboarded"] !== true
    padding: 28
    background: Rectangle { radius: 24; color: win.surface; border.color: win.turquoise; border.width: 1 }

    function finish() { backend.finishOnboarding(); wizard.close() }

    contentItem: ColumnLayout {
        spacing: 16
        RowLayout {
            spacing: 8
            Repeater {
                model: wizard.steps
                delegate: Rectangle {
                    required property int index
                    width: index === wizard.step ? 28 : 10; height: 10; radius: 5
                    color: index <= wizard.step ? win.turquoise : win.veil(0.2)
                    Behavior on width { NumberAnimation { duration: 200 } }
                }
            }
            Item { Layout.fillWidth: true }
            Text { text: "Passer"; color: win.muted; font.pixelSize: 13; font.underline: skipHover.hovered
                   HoverHandler { id: skipHover; cursorShape: Qt.PointingHandCursor }
                   TapHandler { onTapped: wizard.finish() } }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: wizard.step

            // 1. Bienvenue
            ColumnLayout {
                spacing: 12
                Title { text: "Bienvenue, je suis Djerba" }
                Body { text: "Je suis l'assistant intégré de Djerba OS. Je connais votre machine et je l'administre pour vous : installer une application, libérer de l'espace, optimiser un jeu, réparer un problème, créer une petite application…" }
                Body { text: "• Toute action sensible vous est demandée avant d'être faite ; les actions administrateur passent par polkit et sont journalisées (/var/log/djerba/audit.log).\n• Mes souvenirs restent sur cet ordinateur ; mots de passe, clés et jetons sont masqués avant tout enregistrement.\n• Il me faut un « cerveau » (modèle de langage) : choisissons-le." }
            }

            // 2. Cerveau
            ColumnLayout {
                spacing: 10
                Title { text: "Votre cerveau" }
                Repeater {
                    model: backend.brains
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: brainText.implicitHeight + 24
                        radius: 14
                        color: backend.provider === modelData.id ? Qt.rgba(0, 0.76, 0.8, 0.18) : win.veil(brainHover.hovered ? 0.08 : 0.04)
                        border.color: backend.provider === modelData.id ? win.turquoise : win.veil(0.12)
                        HoverHandler { id: brainHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: backend.setBrain(modelData.id) }
                        RowLayout {
                            id: brainText
                            anchors.fill: parent; anchors.margins: 12; spacing: 12
                            Rectangle { width: 12; height: 12; radius: 6; color: modelData.status === "ok" ? "#2DD4BF" : modelData.status === "down" ? "#F43F5E" : "#94A3B8" }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: modelData.label; color: win.white; font.pixelSize: 15; font.weight: Font.DemiBold }
                                Text { Layout.fillWidth: true; text: modelData.note + " — " + modelData.detail; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                            }
                        }
                    }
                }
                Body { text: "Les détails (machine Tailscale, clé d'API, modèle) se règlent ensuite dans « Cerveau et réglages ». Sans réponse du cerveau choisi, je bascule automatiquement sur le modèle local." }
            }

            // 3. Raccourcis
            ColumnLayout {
                spacing: 12
                Title { text: "Toujours à portée de main" }
                RowLayout {
                    spacing: 14
                    Rectangle { implicitWidth: 150; implicitHeight: 40; radius: 10; color: win.veil(0.08); Text { anchors.centerIn: parent; text: backend.shortcuts.spotlight; color: win.white; font.family: "monospace"; font.pixelSize: 15 } }
                    Body { text: "ouvre la barre Djerba au-dessus de vos fenêtres : posez une question, demandez une action." }
                }
                RowLayout {
                    spacing: 14
                    Rectangle { implicitWidth: 150; implicitHeight: 40; radius: 10; color: win.veil(0.08); Text { anchors.centerIn: parent; text: backend.shortcuts.clipboard; color: win.white; font.family: "monospace"; font.pixelSize: 15 } }
                    Body { text: "analyse le texte copié : expliquer, résumer, traduire, corriger." }
                }
                Body { text: "Je reste dans la zone de notification (icône à pastille : vert prêt, bleu au travail, orange repli local, rouge indisponible). Les raccourcis se modifient dans les réglages." }
            }

            // 4. Autonomie
            ColumnLayout {
                spacing: 12
                Title { text: "Dernières préférences" }
                RowLayout {
                    Layout.fillWidth: true
                    Body { text: "Recherche sur Internet pour les informations récentes" }
                    Switch2 { checked: backend.settings["agent.web_search"] === true; onToggled: backend.setSetting("agent.web_search", !checked) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Body { text: "Auto-réparation des outils de Djerba OS (correctifs toujours soumis à votre accord)" }
                    Switch2 { checked: backend.settings["agent.self_heal"] === true; onToggled: backend.setSetting("agent.self_heal", !checked) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Body { text: "Me laisser créer de nouvelles capacités quand il en manque une (testées dans le bac à sable)" }
                    Switch2 { checked: backend.settings["agent.auto_extend"] === true; onToggled: backend.setSetting("agent.auto_extend", !checked) }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            ActionButton { text: "Précédent"; visible: wizard.step > 0; onClicked: wizard.step-- }
            Item { Layout.fillWidth: true }
            ActionButton {
                primary: true
                text: wizard.step < wizard.steps - 1 ? "Suivant" : "Commencer"
                onClicked: wizard.step < wizard.steps - 1 ? wizard.step++ : wizard.finish()
            }
        }
    }
}
