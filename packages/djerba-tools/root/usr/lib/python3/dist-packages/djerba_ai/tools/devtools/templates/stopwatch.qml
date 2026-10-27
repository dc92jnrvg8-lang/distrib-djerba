// SPDX-License-Identifier: GPL-3.0-or-later
// Chronomètre — généré par le Studio Djerba (QML / Qt 6).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    width: 420
    height: 560
    visible: true
    title: "Chronomètre — Djerba Studio"
    color: "#070B18"

    property int elapsed: 0          // millisecondes
    property double startedAt: 0
    property bool running: false
    property var laps: []

    function format(ms) {
        var minutes = Math.floor(ms / 60000)
        var seconds = Math.floor(ms / 1000) % 60
        var hundredths = Math.floor(ms / 10) % 100
        return (minutes < 10 ? "0" : "") + minutes + ":" + (seconds < 10 ? "0" : "") + seconds
                + "," + (hundredths < 10 ? "0" : "") + hundredths
    }

    Timer {
        interval: 16; repeat: true; running: window.running
        onTriggered: window.elapsed = Date.now() - window.startedAt
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 24

        Item {
            Layout.alignment: Qt.AlignHCenter
            width: 260; height: 260
            Canvas {
                id: ring
                anchors.fill: parent
                property real progress: (window.elapsed % 60000) / 60000
                onProgressChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.lineWidth = 12
                    ctx.strokeStyle = "rgba(255,255,255,0.08)"
                    ctx.beginPath(); ctx.arc(width / 2, height / 2, 116, 0, 2 * Math.PI); ctx.stroke()
                    ctx.strokeStyle = "#00C2CB"
                    ctx.lineCap = "round"
                    ctx.beginPath()
                    ctx.arc(width / 2, height / 2, 116, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * progress)
                    ctx.stroke()
                }
            }
            Text {
                anchors.centerIn: parent
                text: window.format(window.elapsed)
                color: "#F8FAFC"; font.pixelSize: 44; font.weight: Font.Light; font.family: "Inter"
            }
        }

        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 16
            Button {
                text: window.running ? "Tour" : "Remise à zéro"
                enabled: window.running || window.elapsed > 0
                onClicked: {
                    if (window.running) {
                        window.laps = [window.format(window.elapsed)].concat(window.laps)
                    } else {
                        window.elapsed = 0
                        window.laps = []
                    }
                }
            }
            Button {
                text: window.running ? "Pause" : (window.elapsed > 0 ? "Reprendre" : "Démarrer")
                highlighted: true
                onClicked: {
                    if (!window.running)
                        window.startedAt = Date.now() - window.elapsed
                    window.running = !window.running
                }
            }
        }

        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: window.laps
            delegate: Text {
                required property string modelData
                required property int index
                text: "Tour " + (window.laps.length - index) + " — " + modelData
                color: "#94A3B8"; font.pixelSize: 16; font.family: "Inter"
                height: 30
            }
        }
    }
}
