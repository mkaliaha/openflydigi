// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// One trigger's stored adaptive effect, dead zone and motor.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

FormCard.FormCard {
    id: root

    required property var side
    required property string sideName

    FormCard.FormComboBoxDelegate {
        objectName: "effect_" + root.sideName
        text: "Effect"
        model: App.profile.triggers.effectNames
        currentIndex: root.side.effect
        onActivated: root.side.effect = currentIndex
    }

    // Each effect brings its own controls -- Racing has two, Sniper five,
    // General none -- so the rows come from the model rather than being written
    // out here and enabled or disabled. A greyed-out row for a knob the chosen
    // effect does not have is a row that says nothing.
    Repeater {
        model: root.side.effectParams

        delegate: Loader {
            id: row

            required property var modelData

            Layout.fillWidth: true
            sourceComponent: row.modelData.kind === "switch" ? switchRow : sliderRow

            Component {
                id: sliderRow

                SliderRow {
                    objectName: "param_" + row.modelData.key + "_" + root.sideName
                    label: row.modelData.label
                    description: row.modelData.description
                    from: row.modelData.from
                    to: row.modelData.to
                    value: row.modelData.value
                    onMoved: (newValue) => root.side.setEffectParam(
                                 row.modelData.key, newValue)
                }
            }

            Component {
                id: switchRow

                FormCard.FormSwitchDelegate {
                    objectName: "param_" + row.modelData.key + "_" + root.sideName
                    text: row.modelData.label
                    description: row.modelData.description
                    checked: row.modelData.value !== 0
                    onToggled: root.side.setEffectParam(row.modelData.key,
                                                        checked ? 1 : 0)
                }
            }
        }
    }

    FormCard.FormDelegateSeparator {}

    SliderRow {
        objectName: "deadZone_" + root.sideName
        label: "Dead zone"
        value: root.side.deadZone
        onMoved: (newValue) => root.side.deadZone = newValue
    }
}
