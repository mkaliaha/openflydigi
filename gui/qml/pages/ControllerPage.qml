// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Device overview and the four profile slots.
//
// Selecting a slot reads it, which is why the slots say whether they have been
// read: every config read makes the pad audibly re-seat its trigger motors, so
// this never reads all four just to fill a list.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import QtQuick.Dialogs as Dialogs
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "controllerPage"
    title: "Controller"

    // This page edits the profile too -- its name -- so it needs the same
    // apply and save the other profile pages have.
    footer: ProfileFooter {}

    // The conflict warning below reads DS mode's state, which is only current
    // once something has asked for it -- the model polls rather than being
    // pushed to. Arming that poll is `Main.qml`'s job and not this page's: see
    // the `polling` binding there for why a page cannot own it.

    ColumnLayout {
        spacing: 0

        FormCard.FormHeader {
            title: "Device"
        }

        FormCard.FormCard {
            FormCard.FormTextDelegate {
                objectName: "connectionRow"
                text: "Connection"
                description: App.device.connected
                             ? (App.device.connectionType || "connected")
                             : "not connected"
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "batteryRow"
                text: "Battery"
                description: App.device.charging
                             ? "Charging"
                             : App.device.battery + " of " + App.device.batterySteps
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormButtonDelegate {
                objectName: "reloadRow"
                text: "Reload from pad"
                description: "Re-reads device info, the open profile and the lighting"
                icon.name: "view-refresh"
                onClicked: App.reload()
            }
        }

        FormCard.FormHeader {
            title: "Profiles"
        }

        FormCard.FormCard {
            Repeater {
                model: App.profile.slots

                // A plain RadioDelegate, not FormCard.FormRadioDelegate. The
                // form delegates load parts of themselves asynchronously, and
                // creating them from a Repeater leaves those incubating when
                // the page goes away -- "items in the process of being created
                // at engine destruction". The same rule cost the lighting
                // swatches their form delegate.
                delegate: Controls.RadioDelegate {
                    id: slotRow

                    required property int index
                    required property string title
                    required property bool loaded
                    required property bool isActive
                    required property bool isCurrent
                    required property bool dirty

                    objectName: "profileSlot" + slotRow.index
                    width: parent ? parent.width : implicitWidth
                    checked: slotRow.isCurrent
                    onClicked: App.profile.select(slotRow.index)

                    contentItem: ColumnLayout {
                        spacing: 0

                        Controls.Label {
                            text: slotRow.title
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }

                        Controls.Label {
                            text: {
                                let bits = [];
                                if (slotRow.isActive)
                                    bits.push("running on the pad");
                                bits.push(slotRow.loaded ? "read" : "not read yet");
                                if (slotRow.dirty)
                                    bits.push("unsaved changes");
                                return bits.join(" — ");
                            }
                            font: Kirigami.Theme.smallFont
                            color: Kirigami.Theme.disabledTextColor
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        FormCard.FormHeader {
            title: "Selected profile"
        }

        FormCard.FormCard {
            FormCard.FormTextFieldDelegate {
                id: nameField

                objectName: "profileName"
                label: "Name"
                // No maximumLength here even though the pad has one. The field
                // would truncate on its own, the model truncates too, and the
                // two chase each other into a binding loop. The model is the
                // one that has to be right, so it is the only one that clips.
                enabled: App.profile.loaded
                placeholderText: "unnamed"

                // Deliberately not `text: App.profile.title`. The model caps
                // the title, so a binding and a setter chase each other
                // whenever the cap bites -- Qt reports it as a binding loop.
                // Assigning only when the two actually differ settles at once.

                // Pushed on `Qt.callLater` rather than assigned in the handler,
                // because `FormTextFieldDelegate.textEdited` is a bare signal
                // (no argument) that the delegate re-emits from its internal
                // TextField *before* its own `onTextChanged: root.text = text`
                // writeback has run. So `text` read from the handler is the
                // value from before this keystroke, and the field was one edit
                // behind for as long as this page has existed. Measured against
                // the pad: typing "123" stored "12", and deleting a character
                // stored the string that still had it.
                //
                // The dirty flag was the worse half. The model's setter returns
                // early when the title looks unchanged, so a stale value meant
                // the last edit did not mark the profile dirty either.
                //
                // callLater runs at the end of the current event-loop pass --
                // after the writeback, and long before any click on Apply.
                // The focus-out pair is not redundant with it: a button here
                // does not take focus from the field, so nothing else
                // guarantees a further edit to flush the last one.
                function pushToModel() {
                    App.profile.title = text;
                }

                onTextEdited: Qt.callLater(nameField.pushToModel)
                onEditingFinished: nameField.pushToModel()
                onAccepted: nameField.pushToModel()

                function syncFromModel() {
                    if (text !== App.profile.title)
                        text = App.profile.title;
                }

                Component.onCompleted: syncFromModel()

                Connections {
                    target: App.profile
                    function onTitleChanged() { nameField.syncFromModel(); }
                }
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormButtonDelegate {
                objectName: "switchCopyButton"
                text: "Copy to the Switch profile"
                // Named rather than described as "apply to Switch", because
                // what it copies into is a slot with a number the pad's own
                // screen shows.
                description: App.profile.switchSlot < 0
                             ? ""
                             : "The pad keeps four more profiles for Switch "
                               + "mode. This copies the open one into slot "
                               + App.profile.switchSlot + ". Nothing on a PC "
                               + "can read those back — only a Switch shows "
                               + "the result."
                icon.name: "edit-copy"
                enabled: App.profile.loaded
                onClicked: App.profile.copyToSwitch()
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormButtonDelegate {
                objectName: "resetButton"
                text: "Restore this profile to factory…"
                // Hidden rather than disabled on a model whose factory profile
                // this project has not got. Restoring one slot means *writing*
                // factory bytes -- the firmware's own reset has no per-slot
                // form -- so it needs the real bytes for the real model, and a
                // greyed-out button would invite the question with no answer.
                visible: App.devices.capabilities.factory_profile === true
                // The name is the part nobody expects to lose, so it is in the
                // description rather than only in the dialog: the title lives
                // in the profile blob, so a factory restore brings the factory
                // name back with it, which on this pad is Chinese.
                description: "Every mapping, the sticks, the triggers, the "
                             + "macros — and the name."
                icon.name: "edit-undo"
                enabled: App.profile.loaded
                onClicked: resetDialog.open()
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormButtonDelegate {
                objectName: "backupButton"
                text: "Back up this profile…"
                icon.name: "document-save-as"
                enabled: App.profile.loaded
                onClicked: backupDialog.open()
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormButtonDelegate {
                objectName: "restoreButton"
                text: "Restore from a file…"
                icon.name: "document-open"
                enabled: App.profile.loaded
                onClicked: restoreDialog.open()
            }
        }

        FormCard.FormCard {
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormButtonDelegate {
                objectName: "resetAllButton"
                text: "Restore all four profiles to factory…"
                // Its own card under the list rather than beside one profile's
                // name, because that is its scope. Flydigi put a per-profile
                // "restore default" on this command in their own UI and it
                // wipes the other three: 175 ignores the slot it is given.
                description: "The pad's own reset. It cannot be aimed at one "
                             + "profile — every slot goes back, names included."
                icon.name: "edit-clear-all"
                enabled: App.device.connected
                onClicked: resetAllDialog.open()
            }
        }

        // A device setting, not a profile one -- it survives switching profiles
        // and is not part of any of them.
        FormCard.FormHeader {
            visible: App.device.thirdPartyAvailable
            title: "Other software"
        }

        // The other half of the warning on the DualSense page. Turning this on
        // while the relay runs takes away the evdev input it reads, and the
        // symptom -- motion alive, sticks and buttons dead -- does not point
        // back here on its own.
        Kirigami.InlineMessage {
            objectName: "thirdPartyDsConflict"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: App.device.thirdPartyAvailable && App.dsmode.running
            type: Kirigami.MessageType.Warning
            text: "DualSense mode is on. Handing the pad to other software "
                  + "stops the controller input it relays, leaving games a "
                  + "DualSense with working motion and dead sticks and buttons."
        }

        FormCard.FormCard {
            // Hidden rather than disabled below the firmware this needs, which
            // is what Space Station does: a switch that cannot work is worse
            // than no switch.
            visible: App.device.thirdPartyAvailable

            ModelSwitch {
                objectName: "thirdPartyToggle"
                text: "Let other software take the pad over"
                // Deliberately not sold as a preference. It is a handover: the
                // pad hands itself to whoever asks, and Steam's native Flydigi
                // support is on the far side of it -- with this off Steam sees
                // a generic XInput pad, with it on an Apex 5. The cost is that
                // the taker also reconfigures how the pad reports, and the
                // remapping set up here stops being what games see.
                // The trade is measured, not guessed: with this on the pad
                // stops sending the ordinary gamepad report, so anything
                // reading that directly goes dead -- including this app's own
                // stick tools and the DualSense relay. Trigger effects, which
                // go over the vendor interface, keep working.
                description: "Steam and similar drive it directly and recognise "
                             + "it as an Apex 5. The ordinary gamepad input "
                             + "stops, so anything not going through them sees "
                             + "nothing; adaptive triggers still work."
                value: App.device.thirdParty
                onMoved: (wanted) => App.device.thirdParty = wanted
            }

            FormCard.FormDelegateSeparator { visible: App.device.controlBy !== "" }

            FormCard.FormTextDelegate {
                objectName: "controlByLabel"
                // The difference between "allowed" and "actually taken", which
                // is not something the switch position can express.
                visible: App.device.controlBy !== ""
                text: "Currently driven by " + App.device.controlBy
                description: "Steam identifies itself as SDL."
            }
        }
    }

    // Asked about rather than just done: 175 is a flash write with no undo, and
    // the casualty nobody predicts is the name, since the title is a field of
    // the profile blob like any other.
    Kirigami.PromptDialog {
        id: resetDialog
        objectName: "resetDialog"
        title: "Restore to factory?"
        subtitle: "Profile " + (App.profile.cfgId + 1) + " goes back to how it "
                  + "left the factory: every mapping, the sticks, the triggers, "
                  + "the macros — and its name, which becomes the factory one "
                  + "again. This is written to the pad's flash and cannot be "
                  + "undone.\n\nBack it up first if you want it back."
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [
            Kirigami.Action {
                text: "Restore to factory"
                icon.name: "edit-undo"
                onTriggered: {
                    App.profile.resetToFactory();
                    resetDialog.close();
                }
            }
        ]
    }

    // A separate dialog from the per-profile one, with a separate warning,
    // because the scope is the whole difference between them and a shared
    // dialog would have to hedge about which.
    Kirigami.PromptDialog {
        id: resetAllDialog
        objectName: "resetAllDialog"
        title: "Restore all four profiles?"
        subtitle: "This is the pad's own reset and it cannot be aimed at one "
                  + "profile — Flydigi's command takes a slot number and "
                  + "ignores it. All four go back to how they left the "
                  + "factory, names included, in flash, with no undo.\n\n"
                  + "To restore just one, use the button under its name."
        standardButtons: Kirigami.Dialog.Cancel
        customFooterActions: [
            Kirigami.Action {
                text: "Restore all four"
                icon.name: "edit-clear-all"
                onTriggered: {
                    App.profile.resetAllProfiles();
                    resetAllDialog.close();
                }
            }
        ]
    }

    Dialogs.FileDialog {
        id: backupDialog
        objectName: "backupDialog"
        title: "Back up profile"
        fileMode: Dialogs.FileDialog.SaveFile
        nameFilters: ["Profile dump (*.bin)"]
        onAccepted: App.profile.backup(selectedFile)
    }

    Dialogs.FileDialog {
        id: restoreDialog
        objectName: "restoreDialog"
        title: "Restore profile"
        fileMode: Dialogs.FileDialog.OpenFile
        nameFilters: ["Profile dump (*.bin)"]
        onAccepted: App.profile.restore(selectedFile)
    }
}
