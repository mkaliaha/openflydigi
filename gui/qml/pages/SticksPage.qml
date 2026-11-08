// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Profile response curves. Legacy controls compile to a nine-point table
// (flydigi.mapping.stick_bank); the native editor stores exact points and zones.

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "sticksPage"
    title: "Sticks"

    footer: ProfileFooter {}

    ColumnLayout {
        spacing: 0

        Kirigami.PlaceholderMessage {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 4
            objectName: "sticksPlaceholder"
            visible: !App.profile.loaded
            icon.name: "input-gaming"
            text: "No profile open"
            explanation: "Pick a profile on the Controller page."
        }

        Loader {
            Layout.fillWidth: true
            active: App.profile.apex6 && App.profile.loaded
            visible: active
            sourceComponent: Apex6AnalogEditor { editor: App.profile.nativeSticks }
        }

        FormCard.FormHeader {
            visible: App.profile.loaded && !App.profile.apex6
            title: "Left stick"
        }

        StickSide {
            objectName: "leftStick"
            visible: App.profile.loaded && !App.profile.apex6
            side: App.profile.sticks.left
            sideName: "left"
        }

        FormCard.FormHeader {
            visible: App.profile.loaded && !App.profile.apex6
            title: "Right stick"
        }

        StickSide {
            objectName: "rightStick"
            visible: App.profile.loaded && !App.profile.apex6
            side: App.profile.sticks.right
            sideName: "right"
        }

        FormCard.FormCard {
            visible: App.profile.loaded && !App.profile.apex6
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormTextDelegate {
                text: "Where this applies"
                description: "Stored in the profile and applied by the pad "
                             + "itself, so it works in every game with nothing "
                             + "running — including ones with no dead-zone "
                             + "setting of their own."
            }
        }
    }
}
