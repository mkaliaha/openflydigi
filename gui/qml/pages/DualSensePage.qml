// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// DualSense mode: one switch for the whole system.
//
// Not a per-game setting, and deliberately not on the Games page. The other
// routes need per-game data -- which bind, which telemetry rules, which memory
// offsets -- and this one needs none: it presents a DualSense, and any
// DS5-aware game gets it, including games Flydigi has never heard of.
//
// The launch option is part of the feature rather than a footnote. Nothing can
// hide a physical pad from a game that enumerates it, so with this on a game
// sees two pads unless it is told to ignore one.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

Kirigami.ScrollablePage {
    id: page
    objectName: "dualSensePage"
    title: "DualSense"

    // Reads the process table, so it starts only when someone is looking.
    Component.onCompleted: App.dsmode.refresh()

    ColumnLayout {
        spacing: 0

        Kirigami.InlineMessage {
            objectName: "dsUnavailable"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: !App.dsmode.available
            type: Kirigami.MessageType.Error
            text: "This kernel has no vhci-hcd module, so a virtual USB device "
                  + "cannot be attached. Every distribution checked ships it; "
                  + "a custom or minimal kernel may not."
        }

        FormCard.FormHeader {
            title: "DualSense mode"
        }

        FormCard.FormCard {
            FormCard.FormSwitchDelegate {
                objectName: "dsModeToggle"
                text: "Present the pad as a DualSense"
                description: "Adds a virtual DualSense that the Apex 5 drives. "
                             + "Games get adaptive triggers, gyro and battery "
                             + "with no per-game setup — including games "
                             + "Flydigi has never heard of. Attaching a USB "
                             + "device needs your password once."
                checked: App.dsmode.running
                enabled: App.dsmode.available && !App.dsmode.busy
                onToggled: App.dsmode.setRunning(checked)
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dsMotorsToggle"
                text: "Reproduce haptic audio on the pad's motors"
                // The thing nothing else does. A DualSense has no rumble
                // motors -- its voice coils do both jobs -- and the richer PS5
                // haptics arrive as audio, not as motor values. This splits
                // that signal by frequency: low band to the big motor, high
                // band to the small one.
                description: "Games that write PS5 haptics send waveforms to "
                             + "the controller's audio device rather than motor "
                             + "values. Those are split by frequency and played "
                             + "on the Apex 5's two motors."
                checked: App.dsmode.motors
                // Read once at startup, so changing it mid-session would say
                // something the running relay is not doing.
                enabled: !App.dsmode.running && !App.dsmode.busy
                onToggled: App.dsmode.motors = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dsModuleRow"
                text: "vhci-hcd"
                description: App.dsmode.moduleLoaded
                             ? "Loaded."
                             : (App.dsmode.available
                                ? "Present but not loaded — turning the switch "
                                  + "on loads it."
                                : "Not in this kernel.")
            }
        }

        FormCard.FormHeader {
            visible: App.dsmode.running || App.dsmode.busy
            title: "Running"
        }

        FormCard.FormCard {
            visible: App.dsmode.running || App.dsmode.busy

            FormCard.FormTextDelegate {
                objectName: "dsStatusRow"
                text: App.dsmode.busy
                      ? "Working…"
                      : "Attached — " + App.dsmode.inputReports + " reports served"
                description: App.dsmode.detail
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dsGameRow"
                // The one number that answers "has the game actually taken
                // it": output reports only arrive from something driving the
                // pad. Zero with a game running means the game bound to the
                // Apex 5 instead, which is what the launch option below fixes.
                text: "A game is driving it"
                description: App.dsmode.outputReports > 0
                             ? App.dsmode.outputReports + " output reports received"
                             : "No output reports yet — a game that has bound "
                               + "to this pad sends them."
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dsHapticRow"
                text: "Haptic audio arriving"
                description: App.dsmode.hapticUrbs > 0
                             ? App.dsmode.hapticUrbs + " isochronous transfers"
                             : "None yet — only games using the PS5 haptic path "
                               + "write it, and it needs this pad rather than a "
                               + "uhid one."
            }
        }

        FormCard.FormHeader {
            title: "In the game"
        }

        FormCard.FormCard {
            FormCard.FormTextDelegate {
                objectName: "dsLaunchOption"
                text: "Steam launch options"
                description: App.dsmode.ignoreDevices + " %command%"
                trailing: Controls.Button {
                    objectName: "dsCopyLaunchOption"
                    text: "Copy"
                    icon.name: "edit-copy"
                    onClicked: {
                        launchOptionText.text = App.dsmode.ignoreDevices + " %command%";
                        launchOptionText.selectAll();
                        launchOptionText.copy();
                        App.device.status = "Launch option copied";
                    }
                }
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dsBothPadsNote"
                text: "Both pads are visible to a game"
                // Known and not fixable from here, so it is stated rather than
                // worked around: nothing can hide a physical pad from a game
                // that enumerates it.
                description: "With this on, a game sees the Apex 5 and the "
                             + "virtual DualSense. The launch option above "
                             + "tells it to ignore the Apex 5; it can also be "
                             + "set once for all games in Steam."
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dsRestartNote"
                text: "Turn this on before starting the game"
                // Observed: a game opens its stream to the controller's audio
                // device once, at launch. Switching DualSense mode on while it
                // is already running gives it a pad it will use, and an audio
                // endpoint it will never look for again -- so triggers work
                // and haptics stay silent, which looks like a broken feature
                // rather than a missed handshake.
                description: "Games bind to the DualSense's speakers when they "
                             + "launch. Switching this on mid-game leaves "
                             + "haptic audio silent until the game is "
                             + "restarted."
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dsSteamInputNote"
                text: "Turn Steam Input off for the game"
                description: "It masks the pad as an Xbox controller, which "
                             + "breaks DualSense semantics and the four-channel "
                             + "audio the haptics arrive on."
            }
        }

        // Off-screen, and only here so the copy button has something that can
        // put text on the clipboard: QML has no clipboard API of its own, and
        // a TextEdit is the usual way round it.
        TextEdit {
            id: launchOptionText
            objectName: "dsClipboardHelper"
            visible: false
        }
    }
}
