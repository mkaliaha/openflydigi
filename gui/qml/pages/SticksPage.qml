// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Joystick response curves, stored in the open profile.
//
// The one setting on this pad that Linux has no other tool for, and the one
// where writing what you edited is not enough: the pad has no curve evaluator.
// It plays a nine-point table, and everything on this page is compiled into
// that table before it is written -- see flydigi.mapping.stick_bank.

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

        FormCard.FormHeader {
            visible: App.profile.loaded
            title: "Left stick"
        }

        StickSide {
            objectName: "leftStick"
            visible: App.profile.loaded
            side: App.profile.sticks.left
            sideName: "left"
        }

        FormCard.FormHeader {
            visible: App.profile.loaded
            title: "Right stick"
        }

        StickSide {
            objectName: "rightStick"
            visible: App.profile.loaded
            side: App.profile.sticks.right
            sideName: "right"
        }

        FormCard.FormCard {
            visible: App.profile.loaded
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
