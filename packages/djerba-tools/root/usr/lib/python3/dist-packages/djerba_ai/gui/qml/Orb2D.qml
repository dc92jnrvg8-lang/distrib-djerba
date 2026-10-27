// SPDX-License-Identifier: GPL-3.0-or-later
// Orbe de Djerba en 2D (rendu logiciel, machines sans accélération 3D).
import QtQuick

Item {
    id: orb
    property bool active: false
    property real phase: 0
    NumberAnimation on phase { from: 0; to: Math.PI * 2; duration: orb.active ? 1600 : 6000; loops: Animation.Infinite }

    Canvas {
        anchors.fill: parent
        property real phase: orb.phase
        onPhaseChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cx = width / 2, cy = height / 2
            var r = Math.min(width, height) * 0.3 * (1 + (orb.active ? 0.05 : 0.02) * Math.sin(phase))
            var halo = ctx.createRadialGradient(cx, cy, r * 0.6, cx, cy, r * 1.6)
            halo.addColorStop(0, "rgba(0,194,203,0.35)")
            halo.addColorStop(1, "rgba(0,194,203,0)")
            ctx.fillStyle = halo
            ctx.beginPath(); ctx.arc(cx, cy, r * 1.6, 0, Math.PI * 2); ctx.fill()
            var body = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.35, r * 0.1, cx, cy, r)
            body.addColorStop(0, "#5EEAD4")
            body.addColorStop(0.55, "#0E5E7E")
            body.addColorStop(1, "#0A1F3D")
            ctx.fillStyle = body
            ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill()
            ctx.strokeStyle = "rgba(94,234,212,0.55)"
            ctx.lineWidth = 2
            ctx.beginPath()
            ctx.ellipse(cx - r * 1.3, cy - r * 0.35 + Math.sin(phase) * 4, r * 2.6, r * 0.7)
            ctx.stroke()
        }
    }
}
