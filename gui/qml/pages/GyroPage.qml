// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The gyro driving a stick, stored in the open profile.
//
// The pad does this itself, which is the whole point of it: gyro aim in any
// game with nothing running on the host, where Linux otherwise has Steam Input
// and nothing else.

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "gyroPage"
    title: "Gyro"

    footer: ProfileFooter {}

    ColumnLayout {
        spacing: 0

        Kirigami.PlaceholderMessage {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 4
            objectName: "gyroPlaceholder"
            visible: !App.profile.loaded
            icon.name: "input-gaming"
            text: "No profile open"
            explanation: "Pick a profile on the Controller page."
        }

        FormCard.FormCard {
            // A profile set up in Space Station to move the host's pointer. The
            // byte is real and this app cannot honour it, so it says so instead
            // of showing Off and quietly meaning it.
            visible: App.profile.loaded && App.profile.motion.isMouse

            FormCard.FormTextDelegate {
                objectName: "gyroIsMouse"
                text: "Set to move the mouse"
                description: "This profile points the gyro at the mouse "
                             + "pointer, which the pad does not do by itself — "
                             + "Flydigi's Windows service moves the pointer and "
                             + "the pad only stores the note. Choosing a stick "
                             + "below replaces it."
            }
        }

        FormCard.FormCard {
            visible: App.profile.loaded

            FormComboBox {
                objectName: "gyroTarget"
                text: "Mapped to"
                description: "Tilting the pad moves this stick."
                model: App.profile.motion.targetNames
                currentIndex: App.profile.motion.target
                onActivated: (index) => App.profile.motion.target = index
            }

            FormCard.FormDelegateSeparator {
                visible: App.profile.motion.enabled
            }

            // Flydigi's own warning, and it is the reason this is off by
            // default rather than a free extra.
            FormCard.FormTextDelegate {
                objectName: "gyroPollingWarning"
                visible: App.profile.motion.enabled
                text: "Lowers the polling rate"
                description: "The pad's own interface says so too: while gyro "
                             + "mapping is on, the controller reports less "
                             + "often."
            }

            FormCard.FormDelegateSeparator {
                visible: App.profile.motion.enabled
            }

            FormCard.FormTextDelegate {
                objectName: "gyroUseMode"
                visible: App.profile.motion.enabled
                text: "Motion mode"
                description: App.profile.motion.useMode
                             + " — follows the stick you picked, as it does in "
                             + "Flydigi's own software, which offers no way to "
                             + "set it separately."
            }
        }

        FormCard.FormCard {
            visible: App.profile.loaded && App.profile.motion.enabled
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormComboBox {
                objectName: "gyroEnableType"
                text: "Turned on by"
                model: App.profile.motion.enableTypeNames
                currentIndex: App.profile.motion.enableType
                onActivated: (index) => App.profile.motion.enableType = index
            }

            FormCard.FormDelegateSeparator {}

            FormComboBox {
                objectName: "gyroKey"
                text: "Enable key"
                // The factory blob holds Left trigger here, so a fresh profile
                // shows a button nobody chose. Saying which it is beats hiding
                // it behind a "(none)" that would be a lie.
                description: "There is no always-on setting: the gyro is bound "
                             + "to a button either way. A paddle is the usual "
                             + "choice — nothing else can reach one."
                model: App.profile.motion.keyNames
                currentIndex: App.profile.motion.key
                onActivated: (index) => App.profile.motion.key = index
            }

            FormCard.FormDelegateSeparator {}

            // Only under "While held": the profile format carries a change to
            // byte 7 in that branch alone, so the control would not write in
            // toggle mode. Space Station reveals its own second-key row on the
            // same condition.
            FormComboBox {
                objectName: "gyroSecondKey"
                visible: App.profile.motion.holdMode
                text: "Second enable key"
                description: "A second button that also switches the gyro on. "
                             + "The pad honours it on its own."
                model: App.profile.motion.keyNames
                currentIndex: App.profile.motion.secondKey
                onActivated: (index) => App.profile.motion.secondKey = index
            }

            // The factory's own state: byte 7 ships holding D-pad Up, the pad
            // acts on it, and in toggle mode the format gives nothing to change
            // it with. Naming it beats a control that would not write.
            FormCard.FormTextDelegate {
                objectName: "gyroStrandedKey"
                visible: App.profile.motion.strandedKey !== ""
                text: "Second enable key: " + App.profile.motion.strandedKey
                description: "This button also switches the gyro on, and a "
                             + "factory profile arrives with one set. It can "
                             + "only be changed under “While held” — the "
                             + "profile format carries no change to it "
                             + "otherwise."
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "gyroNoKey"
                visible: !App.profile.motion.hasKey
                text: "Nothing turns this on"
                description: "The gyro is mapped but has no enable key, so it "
                             + "will never move the stick. Pick a button above."
            }
        }

        FormCard.FormCard {
            visible: App.profile.loaded && App.profile.motion.enabled
            Layout.topMargin: Kirigami.Units.largeSpacing

            SliderRow {
                objectName: "gyroSensitivity"
                label: "Sensitivity"
                description: "How far the stick goes for a given tilt"
                from: 0
                to: App.profile.motion.maximum
                value: App.profile.motion.sensitivity
                onMoved: (v) => App.profile.motion.sensitivity = v
            }

            FormCard.FormDelegateSeparator {}

            SliderRow {
                objectName: "gyroDeadZone"
                label: "Dead zone offset"
                // Not a dead zone: it cancels the game's. Flydigi's tooltip is
                // "counters the game's built-in deadzone and increases
                // sensitivity to fine movements", and their own default is 15.
                description: "Cancels a game's own dead zone rather than adding "
                             + "one, so small tilts still register. 0 suits a "
                             + "game whose dead zone you can already turn off."
                from: 0
                to: App.profile.motion.maximum
                value: App.profile.motion.deadZone
                onMoved: (v) => App.profile.motion.deadZone = v
            }
        }

        FormCard.FormCard {
            visible: App.profile.loaded
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormTextDelegate {
                text: "Where this applies"
                description: "Stored in the profile and applied by the pad "
                             + "itself, so it works in every game with nothing "
                             + "running — including ones with no gyro support "
                             + "of their own. The stick it drives is the one "
                             + "the game already reads."
            }
        }
    }
}
