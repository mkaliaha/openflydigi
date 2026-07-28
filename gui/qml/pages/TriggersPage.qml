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

        FormCard.FormCard {
            visible: App.profile.loaded

            FormCard.FormSwitchDelegate {
                objectName: "triggerMotor"
                text: "Trigger vibration motors"
                // One switch, because the pad has one byte for it -- both
                // triggers share the enable, only the levels are per side.
                description: "Shared by both triggers"
                checked: App.profile.triggers.motorEnabled
                onToggled: App.profile.triggers.motorEnabled = checked
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
