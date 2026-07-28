// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// One trigger's stored adaptive effect, dead zone and motor.

import QtQuick
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

FormCard.FormCard {
    id: root

    required property var side
    required property string sideName

    // "Off — normal travel" is index 0, and its parameters mean nothing.
    readonly property bool hasResistance: root.side.effect > 0

    FormCard.FormComboBoxDelegate {
        objectName: "effect_" + root.sideName
        text: "Effect"
        model: App.profile.triggers.effectNames
        currentIndex: root.side.effect
        onActivated: root.side.effect = currentIndex
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "start_" + root.sideName
        label: "Starts at"
        enabled: root.hasResistance
        value: root.side.start
        onMoved: (newValue) => root.side.start = newValue
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "strength_" + root.sideName
        label: "Resistance"
        enabled: root.hasResistance
        value: root.side.strength
        onMoved: (newValue) => root.side.strength = newValue
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "deadZone_" + root.sideName
        label: "Dead zone"
        value: root.side.deadZone
        onMoved: (newValue) => root.side.deadZone = newValue
    }
}
