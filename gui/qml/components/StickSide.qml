// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// One stick's response curve: the controls, and a plot of what they compile to.

import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

ColumnLayout {
    id: root

    required property var side
    required property string sideName

    spacing: 0

    FormCard.FormCard {
        // A stick bound to keyboard or mouse is not running a curve at all, and
        // the pad stores a sentinel where the dead zone would be. Offering the
        // controls anyway would write into a block nothing is reading.
        visible: !root.side.isStick

        FormCard.FormTextDelegate {
            objectName: "notAStick_" + root.sideName
            text: "Mapped to something else"
            description: "This stick is bound to a keyboard, mouse or d-pad "
                         + "action, so it has no response curve. That mapping "
                         + "is not editable here yet."
        }
    }

    FormCard.FormCard {
        visible: root.side.isStick

        FormComboBox {
            objectName: "curveType_" + root.sideName
            text: "Sensitivity curve"
            description: "Instant is quicker off centre, Delay slower. Editing "
                         + "anything below makes it Custom."
            model: App.profile.sticks.presetNames
            currentIndex: root.side.curveType
            onActivated: (index) => root.side.curveType = index
        }

        FormCard.FormDelegateSeparator {}

        // The plot, not a decoration: the polyline above is what you edit, but
        // these nine points are the only part of it the pad plays, so this is
        // the honest picture of what the stick will do.
        FormCard.AbstractFormDelegate {
            background: null
            Layout.fillWidth: true

            contentItem: ColumnLayout {
                spacing: Kirigami.Units.smallSpacing

                Controls.Label {
                    text: "What the pad will play"
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.disabledTextColor
                }

                // **A Shape rather than a Canvas.** A Canvas is a texture the
                // GUI thread paints in JavaScript and re-uploads; a Shape is
                // scene-graph geometry, and the nine points below are ten line
                // segments. The plot is bound to the compiled curve and the
                // curve is recompiled on every step of a slider drag, so the
                // redraw is on the drag path rather than a one-off.
                //
                // **It sits in a plain Item rather than being the layout item
                // itself.** A Shape's implicit size is the bounding box of its
                // paths, and the paths are laid out in the item's own width and
                // height, so a Shape that the layout sizes from its contents is
                // a binding loop. This box carries the size and the plot is
                // told what it is.
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 8

                    Shape {
                        id: plot
                        objectName: "curvePlot_" + root.sideName
                        anchors.fill: parent
                        // As on the dock's wedge outline. The default geometry
                        // renderer leans on the window having multisampling,
                        // which this app never asks for, and a diagonal line is
                        // the whole of what this control draws.
                        preferredRendererType: Shape.CurveRenderer

                        // Redraw whenever the compiled curve moves. The bank is
                        // a list, so bind to it rather than to the controls --
                        // that way a preset change and a slider drag both land
                        // here.
                        readonly property var bank: root.side.bank

                        // The same points in item coordinates. Stored values are
                        // biased by 50: 50 is no output and 150 is full, so
                        // subtract the bias before plotting.
                        readonly property var curve: {
                            const points = plot.bank;
                            if (!points || points.length < 2)
                                return [];
                            const out = [];
                            for (let i = 0; i < points.length; ++i)
                                out.push(Qt.point(
                                    plot.width * i / (points.length - 1),
                                    plot.height - plot.height * Math.max(
                                        0, Math.min(100, points[i] - 50)) / 100));
                            return out;
                        }

                        // The straight line, for comparison. Without it a curve
                        // reads as "some shape" rather than "faster than linear
                        // here, slower there".
                        ShapePath {
                            strokeColor: Kirigami.Theme.disabledTextColor
                            strokeWidth: 1
                            fillColor: "transparent"
                            strokeStyle: ShapePath.DashLine
                            // `dashPattern` counts stroke widths where Canvas's
                            // `setLineDash` counted pixels; at a stroke width of
                            // 1 they are the same three on, three off.
                            dashPattern: [3, 3]
                            capStyle: ShapePath.FlatCap

                            PathPolyline {
                                // Empty whenever the curve is. A reference line
                                // on its own is a plot of nothing, and drawing
                                // nothing is what the Canvas did before a
                                // profile was open.
                                path: plot.curve.length > 1
                                      ? [Qt.point(0, plot.height),
                                         Qt.point(plot.width, 0)]
                                      : []
                            }
                        }

                        ShapePath {
                            strokeColor: Kirigami.Theme.highlightColor
                            strokeWidth: 2
                            fillColor: "transparent"
                            // Canvas's defaults, which are not ShapePath's:
                            // butt caps and mitred joins.
                            capStyle: ShapePath.FlatCap
                            joinStyle: ShapePath.MiterJoin

                            PathPolyline { path: plot.curve }
                        }
                    }
                }
            }
        }
    }

    FormCard.FormCard {
        visible: root.side.isStick
        Layout.topMargin: Kirigami.Units.largeSpacing

        SliderRow {
            objectName: "center_" + root.sideName
            label: "Dead zone"
            description: "How far the stick moves before anything is sent"
            from: 0
            to: App.profile.sticks.maximum
            value: root.side.center
            onMoved: (v) => root.side.center = v
        }

        FormCard.FormDelegateSeparator {}

        SliderRow {
            objectName: "edge_" + root.sideName
            label: "Outer dead zone"
            description: "How far short of the stop the stick already reads full"
            from: 0
            to: App.profile.sticks.maximum
            value: root.side.edge
            onMoved: (v) => root.side.edge = v
        }

        FormCard.FormDelegateSeparator {}

        FormCard.FormSwitchDelegate {
            objectName: "circular_" + root.sideName
            text: "Circular range"
            // Not a neutral preference. Confirmed on hardware: circular clamps
            // a full diagonal to the unit circle, which is about 0.71 per axis,
            // and a game that tests each axis against a threshold then stops
            // responding on the diagonal while the stick is hard over.
            description: "Limits diagonals to the same travel as straight "
                         + "pushes. Some games test each axis separately and "
                         + "will stop responding on the diagonal."
            checked: root.side.circular
            onToggled: root.side.circular = checked
        }
    }
}
