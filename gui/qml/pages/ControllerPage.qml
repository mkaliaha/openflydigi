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
                onTextEdited: App.profile.title = text

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

        // A device setting, not a profile one -- it survives switching profiles
        // and is not part of any of them.
        FormCard.FormHeader {
            visible: App.device.thirdPartyAvailable
            title: "Other software"
        }

        FormCard.FormCard {
            // Hidden rather than disabled below the firmware this needs, which
            // is what Space Station does: a switch that cannot work is worse
            // than no switch.
            visible: App.device.thirdPartyAvailable

            FormCard.FormSwitchDelegate {
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
                checked: App.device.thirdParty
                onToggled: App.device.thirdParty = checked
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
