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

        // No trigger-motor controls here, deliberately. The profile blob has a
        // 29-byte trigger-vibration block at offset 154 and this pad does not
        // have the hardware for it: `GenerateControllerApex5` sets seven
        // capability flags and `IsSupportTriggerVibration` is not one of them,
        // while Vader 3, 4 and 5 all set it. Space Station reads and writes
        // that block only when the flag is on, so on an Apex 5 nobody touches
        // it. Trigger *haptics* do exist here -- they come out of the force
        // triggers above, via the effect vocabulary, not a separate motor.

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

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                text: "About the travel window"
                description: "It narrows the physical pull, not what the game "
                             + "reads: the trigger still reports its full range "
                             + "over whatever travel is left. Bringing the end "
                             + "in is a hair trigger, which this pad has no "
                             + "switch for. It applies under every effect, "
                             + "including General."
            }
        }
    }
}
