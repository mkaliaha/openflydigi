// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// One grip motor's enable switch and its min/max/strength window.

import QtQuick
import org.kde.kirigamiaddons.formcard as FormCard

FormCard.FormCard {
    id: root

    // The per-side model object for this grip.
    required property var side
    required property string sideName

    ModelSwitch {
        objectName: "enabled_" + root.sideName
        text: "Enabled"
        value: root.side.enabled
        onMoved: (wanted) => root.side.enabled = wanted
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "minimum_" + root.sideName
        label: "Minimum"
        // The backend keeps min <= max by swapping them, so the slider reads
        // back from the model rather than trusting its own last position.
        value: root.side.minimum
        onMoved: (newValue) => root.side.minimum = newValue
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "maximum_" + root.sideName
        label: "Maximum"
        value: root.side.maximum
        onMoved: (newValue) => root.side.maximum = newValue
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "scale_" + root.sideName
        label: "Strength"
        value: root.side.scale
        onMoved: (newValue) => root.side.scale = newValue
    }
}
