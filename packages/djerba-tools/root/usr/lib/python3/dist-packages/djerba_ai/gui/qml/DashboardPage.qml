// SPDX-License-Identifier: GPL-3.0-or-later
// Tableau de bord de contrôle : flux de pensées de l'agent, télémétrie vivante
// (bus de contexte et métriques), validations explicites des actions à haut risque.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Glass {
    id: dashboard

    component Metric: Rectangle {
        id: metric
        property string label
        property string value
        property color tint: win.turquoise
        Layout.fillWidth: true
        implicitHeight: 70
        radius: 14
        color: win.veil(0.05)
        border.color: win.veil(0.1)
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 2
            Text { text: metric.label; color: win.muted; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
            Text { text: metric.value; color: metric.tint; font.pixelSize: 20; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
        }
    }

    ListModel { id: thoughts }

    Connections {
        target: backend
        function onTraceAdded(kind, text, time, level) {
            thoughts.insert(0, { kind: kind, text: text, time: time, level: level })
            if (thoughts.count > 300) thoughts.remove(300, thoughts.count - 300)
        }
    }

    RowLayout {
        anchors.fill: parent; anchors.margins: 22; spacing: 18

        // ------------------------------------------------ Flux de pensées
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 3
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Title { text: "Flux de pensées"; Layout.fillWidth: true }
                ActionButton { text: "Effacer"; onClicked: thoughts.clear() }
            }
            Body { text: "Chaque étape de l'agent : poignée de main avec le moteur, contrôles anti-injection, capacités choisies, actions et leur niveau de risque, résultats, bascules de moteur et, pour les modèles de raisonnement, leur réflexion." }
            Text { visible: thoughts.count === 0; text: "Posez une question à Djerba : son raisonnement apparaîtra ici."; color: win.muted; font.pixelSize: 13 }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: thoughts
                delegate: Rectangle {
                    required property string kind
                    required property string text
                    required property string time
                    required property string level
                    width: ListView.view.width
                    implicitHeight: thoughtColumn.implicitHeight + 16
                    radius: 10
                    color: level === "élevé" ? Qt.rgba(0.96, 0.25, 0.37, 0.14) : level === "modéré" ? Qt.rgba(0.96, 0.62, 0.04, 0.12) : win.veil(0.04)
                    border.color: level === "élevé" ? "#F43F5E" : "transparent"
                    ColumnLayout {
                        id: thoughtColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 8
                        spacing: 2
                        RowLayout {
                            Text { text: time; color: win.muted; font.family: "monospace"; font.pixelSize: 11 }
                            Text { text: kind; color: win.turquoise; font.pixelSize: 12; font.weight: Font.DemiBold }
                            Pill { visible: level !== ""; text: "risque " + level; tint: level === "élevé" ? win.coral : level === "modéré" ? win.sand : win.aqua }
                        }
                        Text { Layout.fillWidth: true; text: parent.parent.text; color: win.white; font.pixelSize: 12; wrapMode: Text.WordWrap; maximumLineCount: kind === "réflexion du modèle" ? 12 : 5; elide: Text.ElideRight }
                    }
                }
            }
        }

        // ------------------------------------------------ Télémétrie et validations
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.preferredWidth: 2
            spacing: 10

            Title { text: "Télémétrie" }
            Text {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12
                color: backend.telemetry.live ? win.muted : win.sand
                text: backend.telemetry.live ? ("Bus de contexte : instantané d'il y a " + backend.telemetry.age + " s · fenêtre active : " + (backend.telemetry.window || "—") + " (" + backend.telemetry.window_status + ")")
                                              : "Bus de contexte inactif (démon djerba-ai.service arrêté ?)"
            }
            Canvas {
                id: sparkline
                Layout.fillWidth: true
                Layout.preferredHeight: 70
                property var points: backend.cpuHistory
                onPointsChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d"); ctx.reset()
                    ctx.strokeStyle = win.dark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)"
                    ctx.strokeRect(0.5, 0.5, width - 1, height - 1)
                    if (points.length < 2) return
                    ctx.strokeStyle = "#2DD4BF"; ctx.lineWidth = 2; ctx.beginPath()
                    for (var i = 0; i < points.length; ++i) {
                        var x = i * (width - 4) / 59 + 2, y = height - 4 - points[i] / 100 * (height - 8)
                        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                    }
                    ctx.stroke()
                }
                Text { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 6; text: "processeur, 3 min"; color: win.muted; font.pixelSize: 10 }
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 3; columnSpacing: 8; rowSpacing: 8
                Metric { label: "Processeur"; value: (backend.telemetry.cpu || 0) + " %" }
                Metric { label: "Mémoire"; value: (backend.telemetry.memory_used_gb || 0) + " / " + (backend.telemetry.memory_total_gb || 0) + " Go" }
                Metric { label: "Disque (Mo/s)"; value: "↓" + (backend.telemetry.disk_read_mbs || 0) + " ↑" + (backend.telemetry.disk_write_mbs || 0) }
                Metric { label: "Agent : mémoire"; value: (backend.telemetry.agent_rss_mb || 0) + " Mo" }
                Metric { label: "Latence moyenne du moteur"; value: (backend.telemetry.llm_latency_ms || 0) + " ms" }
                Metric { label: "Actions / requêtes"; value: (backend.telemetry.tool_runs || 0) + " / " + (backend.telemetry.llm_requests || 0) }
                Metric { label: "Alertes anti-injection"; value: String(backend.telemetry.guard_flags || 0); tint: (backend.telemetry.guard_flags || 0) > 0 ? win.coral : win.aqua }
                Metric { label: "Secrets interceptés (DLP)"; value: String(backend.telemetry.dlp_secrets || 0) }
                Metric { label: "Bascules de moteur"; value: String(backend.telemetry.fallbacks || 0) }
            }
            Text { Layout.fillWidth: true; text: "Prometheus / Grafana : " + (backend.telemetry.exporter || "") + " · Uptime Kuma : /health"; color: win.muted; font.pixelSize: 11; wrapMode: Text.WrapAnywhere }
            RowLayout {
                spacing: 8
                ActionButton { text: "Vérifier l'audit"; onClicked: backend.verifyAudit() }
                ActionButton { text: "Vérifier le bac à sable"; onClicked: backend.probeSandbox() }
            }

            Title { text: "Validations"; Layout.topMargin: 6 }
            Text { visible: backend.pendingValidations.length === 0; text: "Aucune action en attente."; color: win.muted; font.pixelSize: 13 }
            Repeater {
                model: backend.pendingValidations
                delegate: Rectangle {
                    id: pending
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: pendingColumn.implicitHeight + 20
                    radius: 12
                    color: modelData.risk === "élevé" ? Qt.rgba(0.96, 0.25, 0.37, 0.14) : Qt.rgba(0.96, 0.62, 0.04, 0.12)
                    border.color: modelData.risk === "élevé" ? "#F43F5E" : "#F59E0B"
                    ColumnLayout {
                        id: pendingColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10
                        spacing: 6
                        Text { Layout.fillWidth: true; text: pending.modelData.label + " (" + pending.modelData.tool + ")"; color: win.white; font.pixelSize: 13; font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
                        Text { Layout.fillWidth: true; text: "Risque " + pending.modelData.risk + (pending.modelData.root ? " · administrateur" : "") + " · " + pending.modelData.params; color: win.muted; font.pixelSize: 11; wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            TextField {
                                id: phrase
                                visible: pending.modelData.risk === "élevé"
                                Layout.fillWidth: true
                                placeholderText: "Tapez CONFIRMER"
                                placeholderTextColor: win.muted
                                color: win.white
                                background: Rectangle { radius: 8; color: win.veil(0.07); border.color: win.veil(0.2) }
                            }
                            Item { Layout.fillWidth: true; visible: pending.modelData.risk !== "élevé" }
                            ActionButton { text: "Refuser"; onClicked: backend.answerConfirmation(pending.modelData.id, false) }
                            ActionButton {
                                text: "Autoriser"; primary: true
                                enabled: pending.modelData.risk !== "élevé" || phrase.text.trim().toUpperCase() === "CONFIRMER"
                                onClicked: pending.modelData.risk === "élevé" ? backend.validateHighRisk(pending.modelData.id, phrase.text)
                                                                               : backend.answerConfirmation(pending.modelData.id, true)
                            }
                        }
                    }
                }
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: backend.validationHistory
                delegate: Text {
                    required property var modelData
                    width: ListView.view.width
                    text: modelData.time + " · " + modelData.tool + " (risque " + modelData.risk + ") : " + modelData.status
                    color: modelData.status === "autorisée" ? win.aqua : win.muted
                    font.pixelSize: 11; elide: Text.ElideRight
                }
            }
        }
    }
}
