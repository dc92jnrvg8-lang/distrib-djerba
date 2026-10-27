// SPDX-License-Identifier: GPL-3.0-or-later
// Orbe de Djerba en Qt Quick 3D : sphère nacrée en rotation, halo de verre ;
// elle pulse quand l'agent réfléchit.
import QtQuick
import QtQuick3D

View3D {
    id: view
    property bool active: false
    environment: SceneEnvironment {
        clearColor: "transparent"
        backgroundMode: SceneEnvironment.Transparent
        antialiasingMode: SceneEnvironment.MSAA
        antialiasingQuality: SceneEnvironment.High
    }
    PerspectiveCamera { position: Qt.vector3d(0, 0, 320) }
    DirectionalLight { eulerRotation.x: -30; eulerRotation.y: -40; brightness: 1.4 }
    PointLight { position: Qt.vector3d(120, 140, 200); color: "#5EEAD4"; brightness: 2.2 }
    PointLight { position: Qt.vector3d(-160, -120, 120); color: "#FF7A59"; brightness: 1.2 }

    Node {
        id: core
        NumberAnimation on eulerRotation.y { from: 0; to: 360; duration: view.active ? 4000 : 16000; loops: Animation.Infinite }
        SequentialAnimation on scale {
            loops: Animation.Infinite
            running: view.active
            NumberAnimation { to: Qt.vector3d(1.08, 1.08, 1.08); duration: 600; easing.type: Easing.InOutSine }
            NumberAnimation { to: Qt.vector3d(1.0, 1.0, 1.0); duration: 600; easing.type: Easing.InOutSine }
        }
        Model {
            source: "#Sphere"
            scale: Qt.vector3d(1.5, 1.5, 1.5)
            materials: PrincipledMaterial {
                baseColor: "#0E5E7E"
                metalness: 0.6
                roughness: 0.18
                clearcoatAmount: 1.0
                emissiveFactor: view.active ? Qt.vector3d(0.0, 0.35, 0.38) : Qt.vector3d(0.0, 0.12, 0.14)
            }
        }
        Model {
            source: "#Sphere"
            scale: Qt.vector3d(1.85, 1.85, 1.85)
            materials: PrincipledMaterial {
                baseColor: "#5EEAD4"
                opacity: 0.12
                alphaMode: PrincipledMaterial.Blend
                roughness: 0.05
            }
        }
        Model {
            source: "#Cylinder"
            scale: Qt.vector3d(2.3, 0.02, 2.3)
            eulerRotation.x: 70
            materials: PrincipledMaterial { baseColor: "#00C2CB"; opacity: 0.35; alphaMode: PrincipledMaterial.Blend }
        }
    }
}
