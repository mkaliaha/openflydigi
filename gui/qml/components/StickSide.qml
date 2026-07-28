// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// One stick's response curve: the controls, and a plot of what they compile to.

import QtQuick
import QtQuick.Layouts
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

        FormCard.FormComboBoxDelegate {
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

                Canvas {
                    id: plot
                    objectName: "curvePlot_" + root.sideName
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 8

                    // Repaint whenever the compiled curve moves. The bank is a
                    // list, so bind to it rather than to the controls -- that
                    // way a preset change and a slider drag both land here.
                    readonly property var bank: root.side.bank
                    onBankChanged: requestPaint()
                    onWidthChanged: requestPaint()
                    onHeightChanged: requestPaint()

                    onPaint: {
                        const ctx = getContext("2d");
                        ctx.reset();
                        const w = width, h = height;
                        const points = bank;
                        if (!points || points.length < 2)
                            return;

                        // The straight line, for comparison. Without it a curve
                        // reads as "some shape" rather than "faster than linear
                        // here, slower there".
                        ctx.strokeStyle = Kirigami.Theme.disabledTextColor;
                        ctx.lineWidth = 1;
                        ctx.setLineDash([3, 3]);
                        ctx.beginPath();
                        ctx.moveTo(0, h);
                        ctx.lineTo(w, 0);
                        ctx.stroke();

                        // Stored values are biased by 50: 50 is no output and
                        // 150 is full, so subtract the bias before plotting.
                        ctx.setLineDash([]);
                        ctx.strokeStyle = Kirigami.Theme.highlightColor;
                        ctx.lineWidth = 2;
                        ctx.beginPath();
                        for (let i = 0; i < points.length; ++i) {
                            const x = w * i / (points.length - 1);
                            const y = h - h * Math.max(0, Math.min(100, points[i] - 50)) / 100;
                            if (i === 0)
                                ctx.moveTo(x, y);
                            else
                                ctx.lineTo(x, y);
                        }
                        ctx.stroke();
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
