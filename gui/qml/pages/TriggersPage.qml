// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Adaptive-trigger settings held in the open profile, so no game is needed.

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "triggersPage"
    title: "Triggers"

    footer: ProfileFooter {}

    ColumnLayout {
        spacing: 0

        Kirigami.PlaceholderMessage {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 4
            objectName: "triggersPlaceholder"
            visible: !App.profile.loaded
            icon.name: "input-gaming"
            text: "No profile open"
            explanation: "Pick a profile on the Controller page."
        }

        FormCard.FormHeader {
            visible: App.profile.loaded
            title: "Trigger vibration motors"
        }

        FormCard.FormCard {
            visible: App.profile.loaded

            FormCard.FormSwitchDelegate {
                objectName: "triggerMotor"
                text: "Enabled"
                // One switch, because the pad has one byte for it.
                description: "Shared by both triggers"
                checked: App.profile.triggers.motorEnabled
                onToggled: App.profile.triggers.motorEnabled = checked
            }

            FormCard.FormDelegateSeparator {}

            SliderRow {
                objectName: "motorStrength"
                label: "Strength"
                // Stored as a percentage, unlike every other level here.
                description: "Shared by both triggers"
                enabled: App.profile.triggers.motorEnabled
                to: App.profile.triggers.motorStrengthMax
                value: App.profile.triggers.motorStrength
                onMoved: (newValue) => App.profile.triggers.motorStrength = newValue
            }

            FormCard.FormDelegateSeparator {}

            SliderRow {
                objectName: "motorThreshold"
                label: "Threshold"
                description: "Rumble below this leaves the triggers still"
                enabled: App.profile.triggers.motorEnabled
                value: App.profile.triggers.motorThreshold
                onMoved: (newValue) => App.profile.triggers.motorThreshold = newValue
            }
        }

        FormCard.FormHeader {
            visible: App.profile.loaded
            title: "Left trigger"
        }

        TriggerSide {
            objectName: "leftTrigger"
            visible: App.profile.loaded
            side: App.profile.triggers.left
            sideName: "left"
        }

        FormCard.FormHeader {
            visible: App.profile.loaded
            title: "Right trigger"
        }

        TriggerSide {
            objectName: "rightTrigger"
            visible: App.profile.loaded
            side: App.profile.triggers.right
            sideName: "right"
        }

        FormCard.FormCard {
            visible: App.profile.loaded
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormTextDelegate {
                text: "Where this applies"
                description: "Stored in the profile, so it works with no game "
                             + "integration and nothing running. A game that "
                             + "drives the triggers itself overrides it while "
                             + "it runs."
            }
        }
    }
}
