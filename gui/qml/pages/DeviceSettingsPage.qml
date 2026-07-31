// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The pad's own settings, as opposed to a profile's.
//
// No apply/save footer, unlike every other editing page here: these are
// standalone commands that take effect as they land, and there is nothing to
// commit. What each control shows is what the pad reported after the write, not
// what was asked for -- a command-19 reply echoes the value and never the
// sub-id, so the read-back is the only thing that knows which setting moved.
//
// Rows the pad reports as unsupported are hidden rather than greyed. On this
// hardware that is motion debounce and audio, and a switch that the firmware
// ignores is worse than no switch.

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

Kirigami.ScrollablePage {
    id: page
    objectName: "deviceSettingsPage"
    title: "Device"

    // Nothing else asks for this block on a schedule, so the page asks once
    // when it is first built. It is one exchange, and `App.reload()` refreshes
    // it along with everything else.
    Component.onCompleted: App.requestSettings()

    ColumnLayout {
        spacing: 0

        Kirigami.InlineMessage {
            objectName: "settingsUnread"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: !App.settings.loaded
            type: Kirigami.MessageType.Information
            text: "Reading the controller's settings… If this stays up, press a "
                  + "button on the pad — it leaves the USB bus entirely when it sleeps."
        }

        FormCard.FormHeader {
            title: "On the pad"
            visible: App.settings.loaded
        }

        FormCard.FormCard {
            visible: App.settings.loaded

            FormCard.FormSwitchDelegate {
                objectName: "quickSwitchToggle"
                visible: App.settings.quickSwitchUsable
                text: "Switch profile from the pad"
                // The one setting here that gives a Linux user something they
                // cannot get any other way: four profiles, changed on the pad,
                // with nothing running on the host.
                description: "FN + A, B, X or Y selects one of the four profiles — "
                             + "no application needed, and it works in any game."
                checked: App.settings.quickSwitch
                onToggled: App.settings.quickSwitch = checked
            }

            FormCard.FormDelegateSeparator { visible: App.settings.quickSwitchUsable }

            FormCard.FormSpinBoxDelegate {
                id: sleepSpin

                objectName: "sleepMinutes"
                label: "Sleep after (minutes)"
                from: 0
                to: App.settings.sleepMax
                // The pad ships at 15 minutes and does not merely go quiet when
                // it sleeps: it leaves the USB bus, taking any applied-but-
                // unsaved config with it. 0 is never.
                value: App.settings.sleepMinutes

                // Written when the number settles, not on every step. Unlike
                // every other spin box in this application this one is a device
                // write -- holding the up arrow would otherwise queue a packet
                // and a full read-back per minute, and the model's own
                // read-back would land as another `valueChanged` and write
                // straight back. The delegate exposes no `valueModified`, so
                // the timer is what stands in for one.
                onValueChanged: sleepSettle.restart()

                Timer {
                    id: sleepSettle
                    interval: 600
                    // The guard is what stops the loop: a change that came from
                    // the pad already matches the model, and writing it again
                    // would be an echo rather than an edit.
                    onTriggered: if (sleepSpin.value !== App.settings.sleepMinutes)
                                     App.settings.sleepMinutes = sleepSpin.value
                }
            }

            FormCard.FormTextDelegate {
                objectName: "sleepExplanation"
                text: App.settings.sleepMinutes === 0
                      ? "The pad never sleeps on its own"
                      : "Sleeps after " + App.settings.sleepText + " idle"
                description: "A sleeping pad disconnects from USB, and anything "
                             + "applied but not saved is lost with it. Zero means never."
            }
        }

        FormCard.FormHeader {
            title: "Sticks"
            visible: App.settings.loaded
        }

        FormCard.FormCard {
            visible: App.settings.loaded

            FormCard.FormSwitchDelegate {
                objectName: "stickDebounceToggle"
                visible: App.settings.stickDebounceUsable
                text: "Stick debounce"
                description: "On, the sticks ignore the smallest movements and sit "
                             + "still at rest. Off reads subtle input better and "
                             + "jitters when untouched — and turns auto-calibration off."
                checked: App.settings.stickDebounce
                onToggled: App.settings.stickDebounce = checked
            }

            FormCard.FormDelegateSeparator { visible: App.settings.stickDebounceUsable }

            FormCard.FormSwitchDelegate {
                objectName: "autoCalibrationToggle"
                visible: App.settings.stickDebounceUsable
                text: "Auto-calibration"
                // Not our rule: Flydigi's own string for the debounce toggle
                // says turning it off disables this. Greyed rather than hidden,
                // so the dependency is visible instead of the row vanishing.
                enabled: App.settings.autoCalibrationUsable
                description: App.settings.stickDebounce
                             ? "The pad re-learns its stick centres as they wear."
                             : "Needs stick debounce on — the pad cannot calibrate "
                               + "against a signal it is not filtering."
                checked: App.settings.autoCalibration
                onToggled: App.settings.autoCalibration = checked
            }

            FormCard.FormDelegateSeparator { visible: App.settings.stickReboundUsable }

            FormCard.FormSwitchDelegate {
                objectName: "stickReboundToggle"
                visible: App.settings.stickReboundUsable
                text: "Rebound filter"
                description: "Suppresses the reverse spike a stick's own inertia "
                             + "makes when you let it snap back to centre."
                checked: App.settings.stickRebound
                onToggled: App.settings.stickRebound = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormComboBoxDelegate {
                objectName: "precisionCombo"
                text: "Resolution"
                description: "How finely the sticks are quantised. It does not "
                             + "rescale a profile's curve — measured: the stored "
                             + "bytes are identical at 10-bit and 12-bit."
                model: App.settings.precisionNames
                currentIndex: App.settings.precision
                onActivated: App.settings.precision = currentIndex
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormComboBoxDelegate {
                objectName: "sensitivityCombo"
                text: "Centre sensitivity"
                description: "How far off centre a stick has to move before the "
                             + "pad reports it. Space Station offers three of these "
                             + "seven; the pad distinguishes all seven."
                model: App.settings.sensitivityNames
                currentIndex: App.settings.sensitivity
                onActivated: App.settings.sensitivity = currentIndex
            }
        }

        FormCard.FormHeader {
            title: "Reported"
            visible: App.settings.loaded
        }

        FormCard.FormCard {
            visible: App.settings.loaded

            FormCard.FormTextDelegate {
                objectName: "reportRateRow"
                text: "Polling rate"
                // Shown and not offered. This pad answers 0, which is not in
                // Flydigi's map, and both its input endpoints already declare
                // the 1 ms interval that is a full-speed device's ceiling --
                // nothing to gain, a working rate to lose.
                description: App.settings.reportRateText
                             + " — the pad already polls at the fastest interval USB "
                             + "allows it, so there is no control here."
            }

            FormCard.FormDelegateSeparator { visible: App.settings.mappingSwitchUsable }

            FormCard.FormSwitchDelegate {
                objectName: "mappingSwitchToggle"
                visible: App.settings.mappingSwitchUsable
                text: "Mapping switch"
                // Honest about not knowing. The pad reports it supported and
                // on, and it has no interface string in any of the twelve
                // locales Space Station ships -- so there is nothing to copy the
                // wording from, and guessing a label would be worse than this.
                description: "Undocumented. The pad supports it and Flydigi's own "
                             + "application never names it, so what it changes is "
                             + "unknown — it is here because the pad has it."
                checked: App.settings.mappingSwitch
                onToggled: App.settings.mappingSwitch = checked
            }
        }

        FormCard.FormCard {
            visible: App.settings.loaded

            FormCard.FormTextDelegate {
                objectName: "screenSettingsPointer"
                text: "Looking for the display settings?"
                description: "The always-on picture and the status bar are part of "
                             + "this same block, and they are on the Screen page."
            }
        }
    }
}
