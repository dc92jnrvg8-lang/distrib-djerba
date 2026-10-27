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
    visible: true
    title: "Djerba"
    color: night
    font.family: "Inter"

    readonly property color night: "#070B18"
    readonly property color deep: "#0A1F3D"
    readonly property color turquoise: "#00C2CB"
    readonly property color aqua: "#5EEAD4"
    readonly property color coral: "#FF7A59"
    readonly property color sand: "#F4E1C1"
    readonly property color white: "#F8FAFC"
    readonly property color muted: "#94A3B8"
    readonly property var pages: ["chat", "system", "selfheal", "studio", "tools", "models"]
    property int page: Math.max(0, pages.indexOf(backend.startPage))
    property string progressText: ""
    property int progressValue: 0

    background: Rectangle {
        gradient: Gradient {
            GradientStop { position: 0.0; color: win.night }
            GradientStop { position: 0.7; color: win.deep }
            GradientStop { position: 1.0; color: "#0E3A52" }
        }
    }

    // ---------------------------------------------------------------- Composants
    component Glass: Rectangle {
        radius: 22
        color: Qt.rgba(1, 1, 1, 0.06)
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 0.13)
    }

    component Pill: Rectangle {
        id: pill
        property string text
        property color tint: win.turquoise
        radius: 10
        implicitHeight: 22
        implicitWidth: pillText.implicitWidth + 16
        color: Qt.rgba(tint.r, tint.g, tint.b, 0.18)
        Text { id: pillText; anchors.centerIn: parent; text: pill.text; color: win.white; font.pixelSize: 11 }
    }

    component Action: Button {
        id: action
        property bool primary: false
        property color accent: win.turquoise
        implicitHeight: 38
        font.pixelSize: 13
        contentItem: Text {
            text: action.text; font: action.font
            color: action.primary ? win.night : (action.enabled ? win.white : win.muted)
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 12
            color: action.primary ? (action.down ? Qt.darker(action.accent, 1.2) : action.accent)
                                  : Qt.rgba(1, 1, 1, action.down ? 0.18 : (action.hovered ? 0.13 : 0.07))
            opacity: action.enabled ? 1 : 0.45
            border.width: action.primary ? 0 : 1
            border.color: Qt.rgba(1, 1, 1, 0.18)
            Behavior on color { ColorAnimation { duration: 120 } }
        }
    }

    component Switch2: Item {
        id: sw
        property bool checked: false
        signal toggled()
        implicitWidth: 52; implicitHeight: 28
        Rectangle {
            anchors.fill: parent; radius: height / 2
            color: sw.checked ? win.turquoise : Qt.rgba(1, 1, 1, 0.15)
            Behavior on color { ColorAnimation { duration: 160 } }
        }
        Rectangle {
            width: parent.height - 8; height: width; radius: width / 2; y: 4
            x: sw.checked ? parent.width - width - 4 : 4
            color: win.white
            Behavior on x { NumberAnimation { duration: 200; easing.type: Easing.OutBack } }
        }
        TapHandler { onTapped: sw.toggled() }
    }

    component Title: Text { color: win.white; font.pixelSize: 26; font.weight: Font.DemiBold }
    component Body: Text { color: win.muted; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }

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
                    Rectangle { width: 9; height: 9; radius: 5; color: backend.engineAvailable ? win.aqua : win.coral }
                    Text {
                        text: backend.engineAvailable ? backend.model : "mode simplifié"
                        color: win.muted; font.pixelSize: 12
                    }
                }
                Item { Layout.preferredHeight: 10 }

                Repeater {
                    model: [
                        { label: "Discussion", icon: "dialog-messages" },
                        { label: "Système", icon: "computer" },
                        { label: "Auto-réparation", icon: "tools-wizard" },
                        { label: "Studio", icon: "applications-development" },
                        { label: "Capacités (" + backend.toolCount + ")", icon: "configure" },
                        { label: "Modèles et réglages", icon: "preferences-system" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 42
                        radius: 12
                        color: win.page === index ? Qt.rgba(0, 0.76, 0.8, 0.22) : Qt.rgba(1, 1, 1, hover.hovered ? 0.07 : 0)
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
                        color: backend.conversation === modelData.id ? Qt.rgba(1, 1, 1, 0.1) : "transparent"
                        Text {
                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                            verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            text: modelData.title; color: win.muted; font.pixelSize: 12
                        }
                        TapHandler { onTapped: { chat.clear(); backend.openConversation(modelData.id) } }
                    }
                }
                Item { Layout.fillHeight: true; visible: win.page !== 0 }
                Action { Layout.fillWidth: true; text: "+ Nouvelle discussion"; onClicked: { chat.clear(); backend.newConversation(); win.page = 0 } }
            }
        }

        // Pages
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.page

            // ======================================================== Discussion
            ChatPage { id: chat }

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
                            Action { text: "Actualiser"; onClicked: backend.refreshHealth() }
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
                                    Action {
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
                            Action { text: "Actualiser"; onClicked: backend.refreshIncidents() }
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
                                        Action { text: "Expliquer"; onClicked: { win.page = 0; backend.analyzeIncident(modelData.id) } }
                                        Action { text: "Proposer un correctif"; enabled: modelData.target !== ""; onClicked: backend.proposePatch(modelData.id) }
                                        Action {
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
                                Action { text: "Annuler le correctif"; onClicked: backend.rollbackPatch(modelData.name) }
                            }
                        }
                    }
                }
            }

            // ======================================================== Studio
            Glass {
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 22; spacing: 14
                    Title { text: "Studio" }
                    Body { text: "Décrivez une application ou un jeu : Djerba l'écrit, la teste, l'enregistre avec son historique et l'ajoute au menu. Hors ligne, les modèles « Snake », « calculatrice » et « chronomètre » restent disponibles." }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        TextField {
                            id: studioInput
                            placeholderTextColor: win.muted
                            Layout.fillWidth: true
                            placeholderText: "Ex. : Crée un jeu Snake en PyQt6, une calculatrice HTML5, une liste de tâches…"
                            color: win.white
                            background: Rectangle { radius: 12; color: Qt.rgba(1, 1, 1, 0.08); border.color: Qt.rgba(1, 1, 1, 0.2) }
                            onAccepted: generate.clicked()
                        }
                        ComboBox { id: kind; model: ["auto", "pyqt6", "qml", "html"]; implicitWidth: 110 }
                        Action { id: generate; text: "Créer"; primary: true; onClicked: backend.generateApp(studioInput.text, kind.currentText) }
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
                                        Action { text: "Lancer"; primary: true; onClicked: backend.runApp(modelData.slug) }
                                        Action { text: "Live"; onClicked: backend.runAppLive(modelData.slug) }
                                        Action { text: "Code"; onClicked: backend.openAppFolder(modelData.slug) }
                                        Action { text: "Modifier"; onClicked: { iterateDialog.slug = modelData.slug; iterateDialog.open() } }
                                        Action { text: "Supprimer"; onClicked: backend.deleteApp(modelData.slug) }
                                    }
                                }
                            }
                        }
                    }
                }
            }

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
                        background: Rectangle { radius: 12; color: Qt.rgba(1, 1, 1, 0.08); border.color: Qt.rgba(1, 1, 1, 0.2) }
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
                            color: Qt.rgba(1, 1, 1, 0.05)
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
                                Action { text: modelData.ready ? "Exécuter" : "Demander à Djerba"; enabled: modelData.available; onClicked: { win.page = 0; backend.runTool(modelData.name) } }
                            }
                        }
                    }
                }
            }

            // ======================================================== Modèles et réglages
            Glass {
                ScrollView {
                    anchors.fill: parent; anchors.margins: 22; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 14
                        RowLayout {
                            Layout.fillWidth: true
                            Title { Layout.fillWidth: true; text: "Moteur d'IA" }
                            Action { text: "Actualiser"; onClicked: backend.refreshEngine() }
                        }
                        Glass {
                            Layout.fillWidth: true
                            implicitHeight: engineColumn.implicitHeight + 28
                            ColumnLayout {
                                id: engineColumn
                                anchors.fill: parent; anchors.margins: 14; spacing: 8
                                Text {
                                    text: backend.engineAvailable ? "Moteur " + backend.provider + " connecté — modèle « " + backend.model + " »"
                                                                  : (backend.ollamaPresent ? "Ollama est installé mais ne répond pas (service arrêté ?)" : "Aucun moteur d'IA : Djerba fonctionne en mode simplifié.")
                                    color: backend.engineAvailable ? win.aqua : win.sand; font.pixelSize: 15; Layout.fillWidth: true; wrapMode: Text.WordWrap
                                }
                                Body { visible: !backend.ollamaPresent && !backend.engineAvailable; text: "Ollama exécute les modèles de langage localement : vos conversations ne quittent pas l'ordinateur. L'archive officielle est téléchargée depuis GitHub et vérifiée (SHA-256)." }
                                Action { visible: !backend.ollamaPresent && !backend.engineAvailable; text: "Installer Ollama"; primary: true; onClicked: backend.installOllama() }
                                ProgressBar { Layout.fillWidth: true; visible: win.progressText !== ""; from: 0; to: 100; value: win.progressValue; indeterminate: win.progressValue < 0 }
                                Text { visible: win.progressText !== ""; text: win.progressText; color: win.muted; font.pixelSize: 12 }
                            }
                        }
                        Text { text: "Modèles installés"; color: win.turquoise; font.pixelSize: 15 }
                        Text { visible: backend.installedModels.length === 0; text: "Aucun modèle installé."; color: win.muted; font.pixelSize: 13 }
                        Repeater {
                            model: backend.installedModels
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Text { Layout.fillWidth: true; text: modelData.name + "  ·  " + modelData.size_gb + " Go"; color: win.white; font.pixelSize: 14 }
                                Pill { visible: modelData.name === backend.model; text: "utilisé"; tint: win.aqua }
                                Action { visible: modelData.name !== backend.model; text: "Utiliser"; onClicked: backend.setModel(modelData.name) }
                            }
                        }
                        Text { text: "Modèles conseillés pour " + backend.memoryGb + " Go de RAM"; color: win.turquoise; font.pixelSize: 15 }
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
                                Action { text: "Télécharger"; enabled: backend.engineAvailable && backend.provider === "ollama"; onClicked: backend.pullModel(modelData.name) }
                            }
                        }

                        Title { text: "Réglages"; Layout.topMargin: 10 }
                        RowLayout {
                            Layout.fillWidth: true
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 0
                                Text { Layout.fillWidth: true; text: "Moteur"; color: win.white; font.pixelSize: 14 }
                                Text { Layout.fillWidth: true; text: "Ollama (local) ou API compatible OpenAI (llama.cpp, LM Studio, service en ligne)"; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                            }
                            ComboBox {
                                model: ["ollama", "openai"]
                                currentIndex: backend.provider === "openai" ? 1 : 0
                                onActivated: backend.setSetting("provider", currentText)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: backend.provider === "openai"
                            spacing: 8
                            TextField { Layout.fillWidth: true; placeholderTextColor: win.muted; placeholderText: "Adresse (ex. http://127.0.0.1:8080/v1)"; text: backend.settings.openai_endpoint || ""; color: win.white; onEditingFinished: backend.setSetting("openai_endpoint", text) }
                            TextField { Layout.preferredWidth: 220; placeholderTextColor: win.muted; placeholderText: "Clé d'API (facultative)"; echoMode: TextInput.Password; color: win.white; onEditingFinished: if (text !== "") backend.setSetting("openai_api_key", text) }
                            TextField { Layout.preferredWidth: 180; placeholderTextColor: win.muted; placeholderText: "Modèle"; text: backend.model; color: win.white; onEditingFinished: backend.setModel(text) }
                        }
                        Repeater {
                            model: [
                                { key: "autonomy", label: "Actions sûres sans confirmation", note: "Les actions sensibles (suppression, redémarrage…) restent toujours confirmées." },
                                { key: "web_search", label: "Recherche sur Internet", note: "Djerba peut chercher des informations récentes (DuckDuckGo)." },
                                { key: "self_heal", label: "Auto-réparation", note: "Surveillance du journal et proposition de correctifs (au prochain démarrage de session)." },
                                { key: "auto_update_model", label: "Mise à jour automatique du modèle", note: "Une fois par semaine, en arrière-plan." }
                            ]
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 0
                                    Text { Layout.fillWidth: true; text: modelData.label; color: win.white; font.pixelSize: 14 }
                                    Text { Layout.fillWidth: true; text: modelData.note; color: win.muted; font.pixelSize: 12; wrapMode: Text.WordWrap }
                                }
                                Switch2 {
                                    checked: modelData.key === "autonomy" ? backend.settings.autonomy === "safe" : backend.settings[modelData.key] === true
                                    onToggled: backend.setSetting(modelData.key, modelData.key === "autonomy" ? (checked ? "ask" : "safe") : !checked)
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { Layout.fillWidth: true; text: "Heure de la maintenance quotidienne"; color: win.white; font.pixelSize: 14 }
                            SpinBox { from: 0; to: 23; value: backend.settings.maintenance_hour || 12; onValueModified: backend.setSetting("maintenance_hour", value) }
                        }
                        Body { text: "Djerba " + backend.version + " · " + backend.learning.tool_runs + " actions réalisées, " + backend.learning.facts + " souvenir(s). Il apprend de vos retours (« Utile » / « Pas utile ») pour mieux choisir ses actions." }
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- Dialogues
    Dialog {
        id: patchDialog
        property var patch: ({})
        anchors.centerIn: parent
        width: Math.min(parent.width - 80, 900)
        height: Math.min(parent.height - 80, 640)
        modal: true
        title: "Correctif " + (patch.patch_id || "")
        background: Rectangle { radius: 18; color: "#0B1426"; border.color: win.turquoise }
        contentItem: ColumnLayout {
            spacing: 10
            Repeater {
                model: patchDialog.patch.checks || []
                delegate: Text { required property var modelData; text: (modelData.success ? "✓ " : "✗ ") + modelData.check; color: modelData.success ? win.aqua : win.coral; font.pixelSize: 13 }
            }
            ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                TextArea { readOnly: true; text: patchDialog.patch.diff || ""; color: win.white; font.family: "monospace"; font.pixelSize: 12; wrapMode: TextArea.NoWrap; background: Rectangle { color: Qt.rgba(0, 0, 0, 0.3); radius: 8 } }
            }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Action { text: "Fermer"; onClicked: patchDialog.close() }
                Action { text: "Déployer (mot de passe administrateur)"; primary: true; onClicked: { backend.deployPatch(patchDialog.patch.patch_id); patchDialog.close() } }
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
        background: Rectangle { radius: 18; color: "#0B1426"; border.color: win.turquoise }
        contentItem: ColumnLayout {
            TextArea { id: iterateText; placeholderTextColor: win.muted; Layout.fillWidth: true; Layout.preferredHeight: 120; placeholderText: "Ex. : ajoute un tableau des meilleurs scores"; color: win.white; wrapMode: TextArea.Wrap }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                Action { text: "Annuler"; onClicked: iterateDialog.close() }
                Action { text: "Appliquer"; primary: true; onClicked: { backend.iterateApp(iterateDialog.slug, iterateText.text); iterateText.text = ""; iterateDialog.close() } }
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
        color: Qt.rgba(0.04, 0.12, 0.24, 0.96)
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
