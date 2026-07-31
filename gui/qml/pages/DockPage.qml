// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The CD2 charging dock: its four switches and its lighting.
//
// Whichever dock the picker has selected — there can be more than one, and they
// are told apart by uid, so nothing here says "the dock".
//
// The switches take effect the moment they move and are read back afterwards,
// like the pad's own device settings and for the same reason: an ack carries the
// command id and nothing about what it changed. Lighting is different — the dock
// plays frames rather than generating them, so an effect is about 24 kB computed
// here and uploaded in 487 packets, which is a button and a progress bar rather
// than a switch.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import QtQuick.Dialogs as Dialogs
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

Kirigami.ScrollablePage {
    id: page
    objectName: "dockPage"
    title: "Dock"

    // Which swatch the colour dialog is editing.
    property int editingColour: -1

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Kirigami.PlaceholderMessage {
            objectName: "noDock"
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 3
            visible: App.devices.dockCount === 0
            icon.name: "battery-full-charging"
            text: "No charging dock"
            explanation: "A CD2 has to be plugged into the computer, not only "
                       + "into power. With one attached, pick it in the sidebar."
        }

        Kirigami.PlaceholderMessage {
            objectName: "dockNotSelected"
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 3
            visible: App.devices.dockCount > 0 && !App.dock.present
            icon.name: "battery-full-charging"
            text: "Reading the dock…"
            explanation: App.dock.error !== "" ? App.dock.error : ""
        }

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present
            title: "Device"
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormTextDelegate {
                objectName: "dockModel"
                text: App.dock.model !== "" ? App.dock.model : "Charging dock"
                description: {
                    const bits = [];
                    if (App.dock.nickname !== "")
                        bits.push("named “" + App.dock.nickname + "”");
                    if (App.dock.firmware !== "")
                        bits.push("firmware " + App.dock.firmware);
                    bits.push(App.dock.dockedState);
                    return bits.join(" · ");
                }
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dockUid"
                text: "Uid"
                // The name this dock keeps across reconnects, and what
                // `flydigi-charger --device` takes.
                description: App.dock.uid !== "" ? App.dock.uid : "not reported"
            }
        }

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present
            title: "Switches"
        }

        // Said rather than left to be inferred. These four take effect as they
        // move -- they are standalone commands with nothing to commit, exactly
        // like the pad's own device settings -- while the lighting below has an
        // Apply button, because an effect is 24 kB of frames and several
        // seconds. One page doing both without saying so reads as a page whose
        // top half you forgot to apply.
        Controls.Label {
            objectName: "dockSwitchNote"
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            visible: App.dock.present
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.disabledTextColor
            text: "These take effect as you move them, and what each one shows "
                + "is what the dock reported afterwards — not what was asked for."
        }

        // Not a rule this enforces -- both switches are set as asked, because a
        // page that silently turned one off would be lying about the device's
        // state. What it does is say which one wins, at the moment the two are
        // on together, rather than leaving a switch that visibly does nothing.
        Kirigami.InlineMessage {
            objectName: "dockSleepConflict"
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            visible: App.dock.present && App.dock.sleepWhenCharging
                     && (App.dock.ledSync || App.dock.showAnimationWhenCharging)
            type: Kirigami.MessageType.Information
            text: {
                const shadowed = [];
                if (App.dock.ledSync)
                    shadowed.push("Lighting sync");
                if (App.dock.showAnimationWhenCharging)
                    shadowed.push("Power display");
                return "“Sleep while docked” wins over "
                     + shadowed.join(" and ")
                     + " for as long as a pad is in the dock, since both "
                     + "devices' lighting is off then. Space Station forces "
                     + "these apart in its own window for the same reason.";
            }
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormSwitchDelegate {
                objectName: "dockSleepWhenCharging"
                // Named for what it does. Flydigi's own label is "Intelligent
                // start", which says nothing about taking two devices' lighting
                // down -- and that is the whole of what you notice.
                text: "Sleep while docked"
                description: "Both the pad and the dock go dark while a pad "
                           + "sits in it. Flydigi call this “Intelligent start”."
                checked: App.dock.sleepWhenCharging
                onToggled: App.dock.sleepWhenCharging = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dockLedSync"
                text: "Lighting sync"
                description: "keep the dock's lighting in step with the pad's — "
                           + "the two arrange it between themselves, with nothing "
                           + "host-side in the loop"
                checked: App.dock.ledSync
                onToggled: App.dock.ledSync = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dockCloseWithSystem"
                text: "Close when shut down"
                description: "go dark when the host powers off"
                checked: App.dock.closeWithSystem
                onToggled: App.dock.closeWithSystem = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dockPowerDisplay"
                text: "Power display"
                // Space Station forces this and Intelligent start apart in its
                // own UI. Nothing in the firmware does, so nothing here does.
                description: "play the charge animation while a pad is docked"
                checked: App.dock.showAnimationWhenCharging
                onToggled: App.dock.showAnimationWhenCharging = checked
            }
        }

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present
            title: "Lighting"
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormComboBoxDelegate {
                objectName: "dockModeBox"
                text: "Effect"
                description: "162 LEDs, computed here and uploaded as frames"
                model: App.dock.modeNames
                currentIndex: App.dock.modeIndex
                onActivated: (index) => App.dock.modeIndex = index
            }

            FormCard.FormDelegateSeparator {}

            FormCard.AbstractFormDelegate {
                objectName: "dockColourRow"
                background: null
                hoverEnabled: false
                visible: App.dock.coloursUsed > 0

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: App.dock.coloursUsed === 1 ? "Colour" : "Colours"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Repeater {
                        model: App.dock.coloursUsed

                        delegate: Controls.AbstractButton {
                            id: swatch
                            required property int index

                            objectName: "dockColourSwatch" + swatch.index
                            implicitWidth: Kirigami.Units.gridUnit * 3
                            implicitHeight: Kirigami.Units.gridUnit * 1.75
                            hoverEnabled: true

                            readonly property string colour:
                                swatch.index < App.dock.colours.length
                                ? App.dock.colours[swatch.index] : "#000000"

                            Controls.ToolTip.visible: swatch.hovered
                            Controls.ToolTip.text: "Click to change this colour ("
                                                   + swatch.colour + ")"

                            onClicked: {
                                page.editingColour = swatch.index;
                                colourDialog.selectedColor = swatch.colour;
                                colourDialog.open();
                            }

                            background: Rectangle {
                                color: swatch.colour
                                radius: Kirigami.Units.smallSpacing
                                border.width: 1
                                border.color: Kirigami.Theme.disabledTextColor
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.coloursUsed > 0
            }

            FormCard.AbstractFormDelegate {
                objectName: "dockBrightnessRow"
                background: null
                hoverEnabled: false

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: "Brightness"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Controls.Slider {
                        objectName: "dockBrightness"
                        Layout.fillWidth: true
                        from: 1
                        to: 100
                        stepSize: 1
                        value: App.dock.brightness
                        onMoved: App.dock.brightness = value
                    }

                    Controls.Label {
                        text: App.dock.brightness
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 2
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.periodMax > App.dock.periodMin
            }

            FormCard.AbstractFormDelegate {
                objectName: "dockIntervalRow"
                background: null
                hoverEnabled: false
                // A mode with one allowed value has nothing to offer here.
                visible: App.dock.periodMax > App.dock.periodMin

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: "Frame interval"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Controls.Slider {
                        objectName: "dockInterval"
                        Layout.fillWidth: true
                        from: App.dock.periodMin
                        to: App.dock.periodMax
                        stepSize: 1
                        value: App.dock.period
                        onMoved: App.dock.period = value
                    }

                    Controls.Label {
                        // Flydigi's own number, and bigger is slower — said
                        // rather than inverted, because their UI says it too.
                        text: App.dock.period + " (bigger is slower)"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 9
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.usesDirection
            }

            FormCard.FormComboBoxDelegate {
                objectName: "dockDirectionBox"
                text: "Direction"
                visible: App.dock.usesDirection
                model: App.dock.directionNames
                currentIndex: App.dock.directionIndex
                onActivated: (index) => App.dock.directionIndex = index
            }

            FormCard.FormDelegateSeparator {}

            // In the card rather than in a page footer, because it applies this
            // card and nothing else. A footer button on a page whose other half
            // writes immediately would be claiming to apply the switches too.
            FormCard.AbstractFormDelegate {
                objectName: "dockApplyRow"
                background: null
                hoverEnabled: false

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        objectName: "dockHint"
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        wrapMode: Text.WordWrap
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        text: App.dock.busy
                            ? "Uploading — the dock plays frames, so this is "
                              + "about 24 kB going over in packets."
                            : "The dock has no effect generator: applying "
                              + "computes every frame here and uploads the lot, "
                              + "which takes a few seconds."
                    }

                    Controls.ProgressBar {
                        objectName: "dockProgress"
                        visible: App.dock.busy
                        from: 0
                        to: 1
                        value: App.dock.progress
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 8
                    }

                    Controls.Button {
                        objectName: "dockApplyButton"
                        text: "Apply lighting"
                        icon.name: "dialog-ok-apply"
                        enabled: !App.dock.busy
                        onClicked: App.dock.apply()
                    }
                }
            }
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormTextDelegate {
                objectName: "dockDefaultNote"
                text: "Two of Flydigi's effects are missing"
                description: "“Default” is not computed by Space Station either "
                           + "— it uploads a file its installer ships, which "
                           + "this project does not have. “Custom” needs frames "
                           + "from an image, which is not built yet."
            }
        }
    }

    Dialogs.ColorDialog {
        id: colourDialog
        objectName: "dockColourDialog"
        title: "Dock lighting colour"
        onAccepted: {
            if (page.editingColour >= 0)
                App.dock.setColour(page.editingColour, selectedColor);
            page.editingColour = -1;
        }
    }
}
