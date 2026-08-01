// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The dock's 162 LEDs, drawn as Space Station draws them.
//
// A wedge outline and 162 circles in a 450x420 box, row-major from the top row
// down — the same order `flydigi/charger.py` fills a frame in, so LED *i* is
// circle *i* and no translation happens anywhere between the sampler and here.
// Every number comes from the model, which takes it from `charger.wedge_centres`
// and `charger.WEDGE_OUTLINE`; nothing about the panel's shape is written twice.
//
// **Rectangles rather than a Canvas.** The dock plays up to 200 frames and the
// preview plays them at the real frame interval, so the hot path is "change 162
// colours, ten times a second". A Repeater builds 162 scene-graph nodes once and
// a frame change writes 162 colours into them; a Canvas would re-run 162 arc()
// calls in JavaScript and re-upload a texture on every tick. The outline is a
// Shape because it is static — it is built once and never touched again.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Shapes

Item {
    id: root

    // 162 colour strings, or empty for a dark panel. Empty is the honest state
    // before a picture is chosen: the dock is not off, but this page has nothing
    // to say about what it is showing — a read never returns frames.
    property var colours: []

    // [x0, y0, x1, y1, …] in view coordinates, from `App.dock.wedgeCentres`.
    property var centres: []
    property real viewWidth: 450
    property real viewHeight: 420
    property real ledRadius: 7.5
    property string outline: ""

    // Flydigi's own unlit shade, the one `charger.FALLBACK_COLOUR` carries.
    property color unlitColour: "#212225"
    property color edgeColour: "#2E3035"

    implicitWidth: root.viewWidth
    implicitHeight: root.viewHeight

    Item {
        id: stage

        // Laid out in the SVG's own coordinates and scaled to fit, so every
        // position below is the number that is in Flydigi's file. Uniform, so a
        // wide slot letterboxes rather than stretching the wedge into a shape
        // the dock does not have.
        width: root.viewWidth
        height: root.viewHeight
        anchors.centerIn: parent
        scale: Math.min(root.width / root.viewWidth, root.height / root.viewHeight)

        Shape {
            objectName: "ledWedgeOutline"
            anchors.fill: parent
            // Off by default in Qt 6 and worth the cost exactly once, on a
            // curve that is drawn a single time and then never repainted.
            preferredRendererType: Shape.CurveRenderer

            ShapePath {
                fillColor: "black"
                strokeColor: root.edgeColour
                strokeWidth: 1
                PathSvg { path: root.outline }
            }
        }

        Repeater {
            objectName: "ledWedgeDots"
            model: Math.floor(root.centres.length / 2)

            delegate: Rectangle {
                id: dot
                required property int index

                width: root.ledRadius * 2
                height: width
                radius: width / 2
                x: root.centres[2 * dot.index] - root.ledRadius
                y: root.centres[2 * dot.index + 1] - root.ledRadius
                // The one binding that re-evaluates per frame. `colours` is
                // replaced wholesale by the model rather than mutated, because
                // QML does not notice writes into a var array.
                color: dot.index < root.colours.length
                       ? root.colours[dot.index] : root.unlitColour
                border.width: 1
                border.color: root.edgeColour
            }
        }
    }
}
