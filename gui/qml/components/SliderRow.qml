// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// A labelled slider with its value shown, sized to sit in a FormCard.
//
// The slider position is bound to `value` and never assigned locally: the
// model is allowed to answer a move with a different number -- the backend
// swaps an inverted min/max window -- and the control has to follow it rather
// than fight it.

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard

FormCard.AbstractFormDelegate {
    id: root

    property string label: ""
    /// Optional second line under the label. Empty by default, and the label
    /// keeps its original single-line shape when it is, so the rows on pages
    /// that do not set one are unchanged.
    property string description: ""
    property int from: 0
    property int to: 255
    property int value: 0

    /// Emitted with the position the user dragged to, for the parent to push
    /// into the model. Never assign `value` from here.
    signal moved(int newValue)

    background: null
    hoverEnabled: false

    contentItem: RowLayout {
        spacing: Kirigami.Units.largeSpacing

        ColumnLayout {
            spacing: 0
            Layout.minimumWidth: Kirigami.Units.gridUnit * 7
            Layout.maximumWidth: Kirigami.Units.gridUnit * 12

            Controls.Label {
                text: root.label
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Controls.Label {
                text: root.description
                visible: root.description !== ""
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                color: Kirigami.Theme.disabledTextColor
                Layout.fillWidth: true
            }
        }

        Controls.Slider {
            objectName: root.objectName + "Slider"
            from: root.from
            to: root.to
            value: root.value
            stepSize: 1
            snapMode: Controls.Slider.SnapAlways
            Layout.fillWidth: true
            onMoved: root.moved(value)
        }

        Controls.Label {
            objectName: root.objectName + "Readout"
            text: root.value
            horizontalAlignment: Text.AlignRight
            Layout.minimumWidth: Kirigami.Units.gridUnit * 2
        }
    }
}
