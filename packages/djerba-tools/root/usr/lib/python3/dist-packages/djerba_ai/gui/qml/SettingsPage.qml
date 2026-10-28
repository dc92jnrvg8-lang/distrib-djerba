// SPDX-License-Identifier: GPL-3.0-or-later
// Réglages de Djerba : cerveau (local, Tailscale, en ligne), modèles, raccourcis,
// agent et système. Source unique : /etc/djerba/config.toml (synchronisé en temps réel).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Glass {
    id: page
    readonly property var s: backend.settings

    component Field: TextField {
        Layout.fillWidth: true
        color: win.white
        placeholderTextColor: win.muted
        selectByMouse: true
        background: Rectangle { radius: 10; color: win.veil(0.07); border.color: parent.activeFocus ? win.turquoise : win.veil(0.18) }
    }
    component Row2: RowLayout {
        property alias label: rowLabel.text
        property alias note: rowNote.text
        default property alias content: slot.data
        Layout.fillWidth: true
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true; spacing: 0
            Text { id: rowLabel; Layout.fillWidth: true; color: win.white; font.pixelSize: 14 }
            Text { id: rowNote; Layout.fillWidth: true; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap; visible: text !== "" }
        }
        RowLayout { id: slot; spacing: 8 }
    }
    component Section: Text { color: win.turquoise; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 12 }

    ScrollView {
        anchors.fill: parent; anchors.margins: 22; clip: true; contentWidth: availableWidth
        ColumnLayout {
            width: parent.width
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                Title { Layout.fillWidth: true; text: "Cerveau et réglages" }
                ActionButton { text: "Assistant de démarrage"; onClicked: onboarding.open() }
                ActionButton { text: "Actualiser"; onClicked: { backend.refreshBrains(); backend.refreshEngine() } }
            }
            Body { text: "Réglages enregistrés dans /etc/djerba/config.toml (lisible par root uniquement) ; le mot de passe administrateur peut être demandé. Les secrets ne sont jamais affichés." }

            // ------------------------------------------------ Cerveau
            Section { text: "Cerveau" }
            Glass {
                Layout.fillWidth: true
                implicitHeight: brainColumn.implicitHeight + 28
                ColumnLayout {
                    id: brainColumn
                    anchors.fill: parent; anchors.margins: 14; spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Text { text: "Cerveau actif"; color: win.white; font.pixelSize: 14 }
                        ComboBox {
                            id: brainBox
                            Layout.fillWidth: true
                            model: backend.brains
                            textRole: "label"
                            currentIndex: Math.max(0, ["local", "tailscale", "cloud"].indexOf(backend.provider))
                            onActivated: (index) => backend.setBrain(backend.brains[index].id)
                            delegate: ItemDelegate {
                                required property var modelData
                                required property int index
                                width: brainBox.width
                                contentItem: RowLayout {
                                    spacing: 10
                                    Rectangle {
                                        width: 10; height: 10; radius: 5
                                        color: modelData.status === "ok" ? "#2DD4BF" : modelData.status === "down" ? "#F43F5E"
                                             : modelData.status === "checking" ? "#38BDF8" : "#94A3B8"
                                    }
                                    ColumnLayout {
                                        spacing: 0; Layout.fillWidth: true
                                        Text { text: modelData.label; color: win.white; font.pixelSize: 14 }
                                        Text { text: modelData.detail + (modelData.latency_ms ? " · " + modelData.latency_ms + " ms" : ""); color: win.muted; font.pixelSize: 11 }
                                    }
                                }
                                background: Rectangle { color: highlighted ? win.veil(0.12) : win.surface }
                                highlighted: brainBox.highlightedIndex === index
                            }
                            contentItem: RowLayout {
                                spacing: 10
                                Item { width: 2 }
                                Rectangle {
                                    // Pastille d'état de connexion en temps réel
                                    readonly property var current: backend.brains[brainBox.currentIndex] || ({})
                                    width: 12; height: 12; radius: 6
                                    color: current.status === "ok" ? "#2DD4BF" : current.status === "down" ? "#F43F5E"
                                         : current.status === "checking" ? "#38BDF8" : "#94A3B8"
                                    SequentialAnimation on opacity {
                                        running: (backend.brains[brainBox.currentIndex] || {}).status === "checking"; loops: Animation.Infinite
                                        NumberAnimation { to: 0.3; duration: 450 } NumberAnimation { to: 1; duration: 450 }
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: brainBox.displayText + "  —  " + ((backend.brains[brainBox.currentIndex] || {}).detail || "")
                                    color: win.white; font.pixelSize: 14; elide: Text.ElideRight
                                }
                            }
                            background: Rectangle { radius: 10; color: win.veil(0.07); border.color: win.veil(0.18) }
                        }
                    }
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12; color: win.muted
                        text: (backend.brains[brainBox.currentIndex] || {}).note || ""
                    }
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                        visible: backend.state === "fallback"
                        color: "#F59E0B"
                        text: "Le cerveau choisi ne répond pas : Djerba bascule automatiquement sur le modèle local (Ollama)."
                    }

                    // Local
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 8
                        visible: backend.provider === "local"
                        Row2 {
                            label: "Adresse d'Ollama"
                            Field { Layout.preferredWidth: 280; text: page.s["llm.local.endpoint"] || ""; onEditingFinished: backend.setSetting("llm.local.endpoint", text) }
                        }
                    }
                    // Tailscale
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 8
                        visible: backend.provider === "tailscale"
                        Row2 {
                            label: "Machine du tailnet"
                            note: "Nom MagicDNS (ex. mac-mini) ou adresse 100.x.y.z ; Ollama doit y écouter sur le réseau Tailscale."
                            Field { Layout.preferredWidth: 220; placeholderText: "mac-mini"; text: page.s["llm.tailscale.host"] || ""; onEditingFinished: backend.setSetting("llm.tailscale.host", text) }
                            SpinBox { from: 1; to: 65535; editable: true; value: page.s["llm.tailscale.port"] || 11434; onValueModified: backend.setSetting("llm.tailscale.port", value) }
                        }
                        Row2 {
                            label: "Modèle distant"
                            Field { Layout.preferredWidth: 220; text: page.s["llm.tailscale.model"] || ""; onEditingFinished: backend.setSetting("llm.tailscale.model", text) }
                        }
                    }
                    // En ligne
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 8
                        visible: backend.provider === "cloud"
                        Row2 {
                            label: "Service"
                            ComboBox {
                                model: ["openai", "gemini", "custom"]
                                currentIndex: Math.max(0, model.indexOf(page.s["llm.cloud.service"] || "openai"))
                                onActivated: backend.setSetting("llm.cloud.service", currentText)
                            }
                        }
                        Row2 {
                            label: "Modèle"
                            Field { Layout.preferredWidth: 220; text: page.s["llm.cloud.model"] || ""; onEditingFinished: backend.setSetting("llm.cloud.model", text) }
                        }
                        Row2 {
                            label: "Adresse personnalisée"
                            note: "Uniquement pour « custom » (API compatible OpenAI)."
                            visible: page.s["llm.cloud.service"] === "custom"
                            Field { Layout.preferredWidth: 280; text: page.s["llm.cloud.endpoint"] || ""; onEditingFinished: backend.setSetting("llm.cloud.endpoint", text) }
                        }
                        Row2 {
                            label: "Clé d'API"
                            note: page.s["llm.cloud.api_key_set"] ? "Une clé est enregistrée (masquée). Saisissez-en une nouvelle pour la remplacer."
                                                                 : "Aucune clé enregistrée. Elle est stockée par root (0600) et n'est jamais réaffichée."
                            Field { id: apiKey; Layout.preferredWidth: 260; echoMode: TextInput.Password; placeholderText: page.s["llm.cloud.api_key_set"] ? "••••••••" : "sk-… / AIza…" }
                            ActionButton { text: "Enregistrer"; primary: true; enabled: apiKey.text !== ""; onClicked: { backend.setApiKey(apiKey.text); apiKey.text = "" } }
                        }
                    }
                    Row2 {
                        label: "Repli automatique sur le modèle local"
                        note: "Si le cerveau distant ne répond pas en " + (page.s["llm.remote_timeout"] || 5) + " s, Djerba continue avec Ollama local et vous prévient."
                        Switch2 { checked: page.s["llm.fallback_to_local"] === true; onToggled: backend.setSetting("llm.fallback_to_local", !checked) }
                    }
                }
            }

            // ------------------------------------------------ Modèles locaux
            Section { text: "Modèles locaux (Ollama)" }
            Glass {
                Layout.fillWidth: true
                implicitHeight: modelColumn.implicitHeight + 28
                ColumnLayout {
                    id: modelColumn
                    anchors.fill: parent; anchors.margins: 14; spacing: 8
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 14
                        text: backend.installedModels.length > 0 ? "Ollama répond — modèle local « " + (page.s["llm.local.model"] || "") + " »"
                              : (backend.ollamaPresent ? "Ollama est installé mais ne répond pas (service arrêté ?)" : "Ollama n'est pas installé.")
                        color: backend.installedModels.length > 0 ? win.aqua : win.sand
                    }
                    Body { visible: !backend.ollamaPresent; text: "Ollama exécute les modèles localement : vos conversations ne quittent pas l'ordinateur. L'archive officielle est téléchargée depuis GitHub et vérifiée (SHA-256)." }
                    ActionButton { visible: !backend.ollamaPresent; text: "Installer Ollama"; primary: true; onClicked: backend.installOllama() }
                    ProgressBar { Layout.fillWidth: true; visible: win.progressText !== ""; from: 0; to: 100; value: win.progressValue; indeterminate: win.progressValue < 0 }
                    Text { visible: win.progressText !== ""; text: win.progressText; color: win.muted; font.pixelSize: 12 }
                    Repeater {
                        model: backend.installedModels
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: modelData.name + "  ·  " + modelData.size_gb + " Go"; color: win.white; font.pixelSize: 14 }
                            Pill { visible: modelData.name === page.s["llm.local.model"]; text: "utilisé"; tint: win.aqua }
                            ActionButton { visible: modelData.name !== page.s["llm.local.model"]; text: "Utiliser"; onClicked: backend.setSetting("llm.local.model", modelData.name) }
                        }
                    }
                    Text { text: "Conseillés pour " + backend.memoryGb + " Go de RAM"; color: win.muted; font.pixelSize: 13; Layout.topMargin: 6 }
                    Repeater {
                        model: backend.recommendedModels
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            opacity: modelData.fits ? 1 : 0.5
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 0
                                Text { Layout.fillWidth: true; text: modelData.label + " (" + modelData.size + ")"; color: win.white; font.pixelSize: 14 }
                                Text { Layout.fillWidth: true; text: modelData.note + (modelData.fits ? "" : " — mémoire insuffisante"); color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                            }
                            ActionButton { text: "Télécharger"; enabled: backend.ollamaPresent; onClicked: backend.pullModel(modelData.name) }
                        }
                    }
                }
            }

            // ------------------------------------------------ Raccourcis
            Section { text: "Raccourcis clavier" }
            Glass {
                Layout.fillWidth: true
                implicitHeight: shortcutColumn.implicitHeight + 28
                ColumnLayout {
                    id: shortcutColumn
                    anchors.fill: parent; anchors.margins: 14; spacing: 10
                    Body { text: "Cliquez sur un raccourci puis pressez la combinaison voulue (Échap pour annuler). Il est enregistré dans ~/.config/kglobalaccelrc et appliqué immédiatement à Plasma (KGlobalAccel)." }
                    Repeater {
                        model: [
                            { id: "spotlight", label: "Ouvrir la barre Spotlight", note: "Question rapide, depuis n'importe où ; le modèle est préchargé à l'ouverture." },
                            { id: "clipboard", label: "Analyser le presse-papiers", note: "Expliquer, résumer, traduire ou corriger le texte copié." }
                        ]
                        delegate: Row2 {
                            id: shortcutRow
                            required property var modelData
                            property bool capturing: false
                            property string captured: ""
                            label: modelData.label
                            note: modelData.note
                            Rectangle {
                                implicitWidth: 200; implicitHeight: 38; radius: 10
                                color: shortcutRow.capturing ? Qt.rgba(0, 0.76, 0.8, 0.2) : win.veil(0.07)
                                border.color: shortcutRow.capturing ? win.turquoise : win.veil(0.18)
                                focus: shortcutRow.capturing
                                Text {
                                    anchors.centerIn: parent
                                    text: shortcutRow.capturing ? (shortcutRow.captured || "Pressez les touches…") : (backend.shortcuts[shortcutRow.modelData.id] || "")
                                    color: win.white; font.pixelSize: 14; font.family: "monospace"
                                }
                                TapHandler { onTapped: { shortcutRow.capturing = true; shortcutRow.captured = ""; parent.forceActiveFocus() } }
                                Keys.onPressed: (event) => {
                                    if (!shortcutRow.capturing) return
                                    event.accepted = true
                                    if (event.key === Qt.Key_Escape) { shortcutRow.capturing = false; return }
                                    var sequence = backend.keySequence(event.key, event.modifiers)
                                    if (sequence === "") return
                                    shortcutRow.captured = sequence
                                    shortcutRow.capturing = false
                                    backend.applyShortcut(shortcutRow.modelData.id, sequence)
                                }
                            }
                        }
                    }
                }
            }

            // ------------------------------------------------ Agent
            Section { text: "Agent" }
            Row2 {
                label: "Autonomie"
                note: page.s["policy.mode"] === "prudent" ? "Prudent : chaque modification est confirmée par votre mot de passe."
                    : page.s["policy.mode"] === "total" ? "Total : même les actions irréversibles sont possibles (mot de passe administrateur à chaque fois)."
                    : "Autonome : Djerba répare et règle seul ; tout est sauvegardé avant et annulable."
                ComboBox {
                    model: ["Prudent", "Autonome", "Total"]
                    currentIndex: Math.max(0, ["prudent", "autonome", "total"].indexOf(page.s["policy.mode"] || "autonome"))
                    onActivated: (index) => backend.setPolicyMode(["prudent", "autonome", "total"][index])
                    Accessible.name: "Mode d'autonomie de l'agent"
                }
            }
            Repeater {
                model: [
                    { key: "agent.web_search", label: "Recherche sur Internet", note: "Djerba peut chercher des informations récentes (DuckDuckGo)." },
                    { key: "agent.self_heal", label: "Auto-réparation", note: "Surveillance du journal et proposition de correctifs." },
                    { key: "agent.auto_extend", label: "Auto-extension", note: "Si aucune capacité ne convient, Djerba en écrit une, la teste dans le bac à sable (3 essais, 15 s) et l'intègre, sans droits root et sans réseau sauf autorisation." },
                    { key: "agent.auto_update_model", label: "Mise à jour automatique du modèle local", note: "Une fois par semaine, en arrière-plan." }
                ]
                delegate: Row2 {
                    required property var modelData
                    label: modelData.label
                    note: modelData.note
                    Switch2 {
                        checked: page.s[modelData.key] === true
                        onToggled: backend.setSetting(modelData.key, !checked)
                    }
                }
            }
            Row2 {
                label: "Heure de la maintenance quotidienne"
                SpinBox { from: 0; to: 23; value: page.s["agent.maintenance_hour"] || 12; onValueModified: backend.setSetting("agent.maintenance_hour", value) }
            }

            // ------------------------------------------------ Système
            Section { text: "Système" }
            Row2 {
                label: "Délai du bac à sable (secondes)"
                note: "Durée maximale de chaque exécution de test (15 s au plus)."
                SpinBox { from: 1; to: 15; value: page.s["system.sandbox_timeout"] || 15; onValueModified: backend.setSetting("system.sandbox_timeout", value) }
            }
            Row2 {
                label: "Tentatives d'auto-correction"
                note: "Au-delà, tout est annulé (git reset --hard)."
                SpinBox { from: 1; to: 3; value: page.s["system.sandbox_attempts"] || 3; onValueModified: backend.setSetting("system.sandbox_attempts", value) }
            }
            Row2 {
                label: "Chien de garde"
                note: "Surveillance continue du système (disque, services, erreurs)."
                Switch2 { checked: page.s["system.watchdog"] === true; onToggled: backend.setSetting("system.watchdog", !checked) }
            }
            Row2 {
                label: "Alerte d'espace disque (%)"
                note: "Notification quand l'espace libre passe sous ce seuil."
                SpinBox { from: 1; to: 50; value: page.s["system.disk_alert_percent"] || 10; onValueModified: backend.setSetting("system.disk_alert_percent", value) }
            }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: win.muted; font.pixelSize: 12
                text: "Rendu : " + (backend.gpuInfo.accelerated ? "3D accéléré" : "2D vectoriel") + " (" + backend.gpuInfo.reason + ")"
            }

            // ------------------------------------------------ Historique
            Section { text: "Historique des réglages" }
            Text { visible: backend.settingsHistory.length === 0; text: "Aucune modification pour l'instant."; color: win.muted; font.pixelSize: 13 }
            Repeater {
                model: backend.settingsHistory
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Text { text: modelData.time; color: win.muted; font.pixelSize: 12; Layout.preferredWidth: 90 }
                    Text { Layout.fillWidth: true; text: modelData.key + " = " + modelData.value; color: win.white; font.pixelSize: 12; font.family: "monospace"; elide: Text.ElideRight }
                }
            }
            Body { text: "Djerba " + backend.version + " · " + backend.learning.tool_runs + " actions réalisées, " + backend.learning.facts + " souvenir(s)." }
        }
    }
    Component.onCompleted: backend.refreshHistory()
}
