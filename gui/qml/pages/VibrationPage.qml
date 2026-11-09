// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Grip motor limits, stored in the open profile.

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "vibrationPage"
    title: "Vibration"

    footer: ProfileFooter {}

    ColumnLayout {
        spacing: 0

        Kirigami.PlaceholderMessage {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 4
            objectName: "vibrationPlaceholder"
            visible: !App.profile.loaded
            icon.name: "input-gaming"
            text: "No profile open"
            explanation: "Pick a profile on the Controller page."
        }

        Loader {
            Layout.fillWidth: true
            active: App.profile.apex6 && App.profile.loaded
            visible: active
            sourceComponent: Apex6VibrationEditor {}
        }

        FormCard.FormCard {
            visible: App.profile.loaded && !App.profile.apex6

            ModelSwitch {
                objectName: "vibrationMaster"
                text: "Rumble enabled"
                description: "The master switch for both grip motors"
                value: App.profile.vibration.enabled
                onMoved: (wanted) => App.profile.vibration.enabled = wanted
            }
        }

        FormCard.FormHeader {
            visible: App.profile.loaded && !App.profile.apex6
            title: "Left grip"
        }

        VibrationSide {
            objectName: "leftGrip"
            visible: App.profile.loaded && !App.profile.apex6
            side: App.profile.vibration.left
            sideName: "left"
        }

        FormCard.FormHeader {
            visible: App.profile.loaded && !App.profile.apex6
            title: "Right grip"
        }

        VibrationSide {
            objectName: "rightGrip"
            visible: App.profile.loaded && !App.profile.apex6
            side: App.profile.vibration.right
            sideName: "right"
        }

        FormCard.FormCard {
            visible: App.profile.loaded && !App.profile.apex6
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormTextDelegate {
                text: "How the window works"
                description: "Minimum and maximum bound how hard the motor may "
                             + "run; the pad squeezes whatever a game asks for "
                             + "into that range."
            }
        }
    }
}
