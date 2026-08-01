// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Macros stored in the open profile.
//
// The pad plays these itself: a macro's steps live in the profile blob and the
// firmware runs them, so one bound here keeps working with this application
// closed, on a machine that has never heard of it. That is why the page edits
// a profile rather than offering a "run" button.
//
// Recording reads the pad's ordinary gamepad node, which is why the page is
// blocked while another program holds the pad -- see the message at the top.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "macrosPage"
    title: "Macros"

    readonly property bool blocked: App.device.thirdParty

    actions: [
        Kirigami.Action {
            objectName: "recordAction"
            text: "Record a macro"
            icon.name: "media-record"
            enabled: App.profile.loaded && App.profile.macros.canAdd
                     && !page.blocked && !App.profile.macros.recording
            onTriggered: recordDialog.open()
        },
        // Building one is the other half of the same job, and the reason it is
        // a separate action rather than a mode of the recorder: a macro nobody
        // can play on the pad -- a button held for exactly 40 ms, a sequence
        // faster than hands go -- has to be typed, and one that is easier
        // played than typed should be recorded.
        Kirigami.Action {
            objectName: "buildAction"
            text: "Build a macro"
            icon.name: "list-add"
            enabled: App.profile.loaded && App.profile.macros.canAdd
                     && App.profile.macros.canAddStep
                     && !page.blocked && !App.profile.macros.recording
            onTriggered: buildDialog.open()
        }
    ]

    footer: ProfileFooter {}

    ColumnLayout {
        spacing: 0

        // Not a warning about something that might go wrong: with another
        // driver holding the pad the gamepad node stops reporting, so a
        // recording made now would be silently empty. The page says so instead
        // of handing back nothing.
        Kirigami.InlineMessage {
            objectName: "macrosThirdParty"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: page.blocked
            type: Kirigami.MessageType.Warning
            text: "Other software is allowed to take the pad over"
                  + (App.device.controlBy !== ""
                     ? " (" + App.device.controlBy + ")" : "")
                  + ", which switches off the controller report this page "
                  + "records from. Turn it off on the Controller page to "
                  + "record or edit a macro."
        }

        // Protocol 3.2 keeps macros somewhere else entirely — their own store,
        // behind three commands nothing here has ever sent to hardware. Every
        // macro measurement behind this page was made against the older layout
        // on an Apex 5, so a Vader 5 owner is the first person to find out
        // whether this works, and is told so rather than left to wonder why a
        // macro did nothing.
        Kirigami.InlineMessage {
            objectName: "macrosExperimental"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: App.profile.loaded && App.profile.macros.experimental
            type: Kirigami.MessageType.Warning
            text: "Experimental on this pad. Its profiles use protocol 3.2, "
                  + "which stores macros separately from the profile — a path "
                  + "built from Flydigi's own software and never tested on the "
                  + "hardware it is for. Everything here may work exactly as it "
                  + "reads, or a macro may simply never play. Save a backup of "
                  + "the profile first."
        }

        Kirigami.PlaceholderMessage {
            objectName: "macrosPlaceholder"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.gridUnit
            visible: !App.profile.loaded
            icon.name: "input-gaming"
            text: "No profile open"
            explanation: "Pick a profile on the Controller page. Reading one "
                         + "makes the pad re-seat its trigger motors, so it "
                         + "only happens when you ask."
        }

        Kirigami.PlaceholderMessage {
            objectName: "macrosEmpty"
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.gridUnit
            visible: App.profile.loaded && App.profile.macros.count === 0
            icon.name: "media-record"
            text: "No macros in this profile"
            explanation: "A macro is a sequence of button presses the pad "
                         + "plays back on its own. Record one and bind it to a "
                         + "paddle — it keeps working with this window closed."
        }

        Repeater {
            model: App.profile.loaded ? App.profile.macros : null

            // A whole card per macro rather than rows in one card: a macro has
            // a heading, three controls and a step list, and the rows of two
            // different macros next to each other read as one long form.
            delegate: ColumnLayout {
                id: macroEntry

                required property int index
                required property string label
                required property int typeIndex
                required property int interval
                required property int stepCount
                required property int duration
                required property bool foreign
                required property var heldKeys
                required property string name

                spacing: 0
                Layout.fillWidth: true

                FormCard.FormHeader {
                    title: macroEntry.label
                }

                FormCard.FormCard {
                    enabled: !page.blocked

                    FormComboBox {
                        objectName: "macroType_" + macroEntry.index
                        text: "When pressed"
                        description: "Once, over and over while the key is "
                                     + "held, or started and stopped by "
                                     + "separate presses."
                        model: App.profile.macros.typeNames
                        currentIndex: macroEntry.typeIndex
                        onActivated: App.profile.macros.setType(macroEntry.index,
                                                                currentIndex)
                    }

                    FormCard.FormDelegateSeparator {}

                    SliderRow {
                        objectName: "macroInterval_" + macroEntry.index
                        label: "Repeat every"
                        description: "The gap before it plays again. Only used "
                                     + "by the two repeating modes."
                        from: 0
                        to: App.profile.macros.intervalMax
                        value: macroEntry.interval
                        // The pad stores this in units of 10 ms, so anything
                        // finer than that would be a slider position the blob
                        // cannot hold.
                        enabled: macroEntry.typeIndex > 0
                        onMoved: (newValue) => App.profile.macros.setInterval(
                                     macroEntry.index, Math.round(newValue / 10) * 10)
                    }

                    FormCard.FormDelegateSeparator {}

                    // Only from protocol 3.2, whose store keeps twenty bytes
                    // per macro. The v3.1 page has nowhere to put one and the
                    // backend drops it silently, so the field is absent rather
                    // than present and ignored.
                    FormCard.FormTextFieldDelegate {
                        objectName: "macroName_" + macroEntry.index
                        visible: App.profile.macros.namesSupported
                        label: "Name"
                        text: macroEntry.name
                        onEditingFinished: App.profile.macros.setName(
                                               macroEntry.index, text)
                    }

                    FormCard.FormDelegateSeparator {
                        visible: App.profile.macros.namesSupported
                    }

                    FormCard.FormButtonDelegate {
                        objectName: "macroEdit_" + macroEntry.index
                        text: "Edit steps"
                        description: macroEntry.stepCount + " step"
                                     + (macroEntry.stepCount === 1 ? "" : "s")
                                     + ", " + macroEntry.duration + " ms"
                        icon.name: "document-edit"
                        enabled: !macroEntry.foreign
                        onClicked: {
                            App.profile.macros.beginEdit(macroEntry.index);
                            stepDialog.open();
                        }
                    }

                    // Reading a macro is permissive and writing one is strict,
                    // so a macro from other software can press a key this
                    // application cannot store. Every step edit rewrites the
                    // whole page, so one such step makes the macro unwritable
                    // -- and an editor that did not check would refuse every
                    // save with a complaint about a step nobody touched.
                    FormCard.FormTextDelegate {
                        objectName: "macroForeign_" + macroEntry.index
                        visible: macroEntry.foreign
                        text: "Written by other software"
                        description: "This macro presses a key this application "
                                     + "cannot store. The pad reports it and "
                                     + "plays it, but writing it back would be "
                                     + "refused, so it cannot be edited here. "
                                     + "Delete it and record a new one to "
                                     + "replace it."
                    }

                    FormCard.FormTextDelegate {
                        objectName: "macroHeld_" + macroEntry.index
                        visible: macroEntry.heldKeys.length > 0
                        text: "Ends with " + macroEntry.heldKeys.join(", ")
                              + " still pressed"
                        description: "The pad plays exactly what is stored, so "
                                     + "it goes on holding that down after the "
                                     + "macro finishes — with this window "
                                     + "closed. Open the steps to release it."
                    }

                    FormCard.FormDelegateSeparator {}

                    FormCard.FormButtonDelegate {
                        objectName: "macroDelete_" + macroEntry.index
                        text: "Delete this macro"
                        description: "Gives " + macroEntry.label
                                     + " back to what the shell says it is."
                        icon.name: "edit-delete"
                        onClicked: App.profile.macros.remove(macroEntry.index)
                    }
                }
            }
        }

        FormCard.FormCard {
            Layout.topMargin: Kirigami.Units.largeSpacing
            visible: App.profile.loaded

            FormCard.FormTextDelegate {
                objectName: "macroBudget"
                text: App.profile.macros.count + " of "
                      + App.profile.macros.slots + " macros, "
                      + App.profile.macros.stepsUsed + " of "
                      + App.profile.macros.stepBudget + " steps"
                // Both numbers come off the open profile rather than from a
                // constant: protocol 3.2 doubles them, so a pad that has ten
                // slots must not be shown five.
                description: "One profile holds " + App.profile.macros.slots
                             + " macros and " + App.profile.macros.stepBudget
                             + " steps between them — that is the size of the "
                             + (App.profile.macros.experimental
                                ? "store the pad keeps them in."
                                : "page the pad keeps them in.")
            }
        }
    }

    // Building one, which is recording with the pad left out of it.
    Kirigami.Dialog {
        id: buildDialog
        objectName: "buildDialog"
        title: "Build a macro"
        preferredWidth: Kirigami.Units.gridUnit * 24
        standardButtons: Kirigami.Dialog.NoButton

        customFooterActions: [
            Kirigami.Action {
                objectName: "buildCreateAction"
                text: "Create"
                icon.name: "list-add"
                onTriggered: {
                    App.profile.macros.build(buildKeyPicker.currentIndex);
                    buildDialog.close();
                    // `build` leaves the new macro open for editing, so the
                    // step editor is where this lands rather than back on a
                    // card holding a macro that presses A once.
                    if (App.profile.macros.editingRow >= 0)
                        stepDialog.open();
                }
            },
            Kirigami.Action {
                objectName: "buildCancelAction"
                text: "Cancel"
                icon.name: "dialog-cancel"
                onTriggered: buildDialog.close()
            }
        ]

        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            Controls.Label {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.largeSpacing
                wrapMode: Text.WordWrap
                text: "Pick the key that will run it. It starts as one tap of "
                      + "A, which you then edit into what you want — so this is "
                      + "the way to write a macro nobody could play by hand, "
                      + "like a button held for exactly 40 ms."
            }

            Controls.ComboBox {
                id: buildKeyPicker
                objectName: "buildKeyPicker"
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                Layout.bottomMargin: Kirigami.Units.largeSpacing
                model: App.profile.macros.triggerKeys
                // A stray notch here would build the macro onto a key nobody
                // chose. Same defect as the recorder's picker below.
                wheelEnabled: false
            }
        }
    }

    // The step editor itself. A dialog rather than more card: a macro can run
    // to 128 steps, and a page that grew by one row per step would bury the
    // three controls above it.
    Kirigami.Dialog {
        id: stepDialog
        objectName: "stepDialog"
        title: "Steps — " + App.profile.macros.editingLabel
        preferredWidth: Kirigami.Units.gridUnit * 34
        // No `preferredHeight`: Kirigami.Dialog derives its own y from its
        // height, and pinning both puts that binding into a loop it reports on
        // every open. It scrolls its content past the window height by itself,
        // which is what a 128-step macro needs.
        standardButtons: Kirigami.Dialog.NoButton

        customFooterActions: [
            Kirigami.Action {
                objectName: "stepAddAction"
                text: "Add a tap"
                icon.name: "list-add"
                enabled: App.profile.macros.canAddStep
                onTriggered: App.profile.macros.addStep(-1)
            },
            Kirigami.Action {
                objectName: "stepBalanceAction"
                text: "Release what is held"
                icon.name: "edit-clear-all"
                enabled: App.profile.macros.stepEditor.warning !== ""
                onTriggered: App.profile.macros.balance()
            },
            Kirigami.Action {
                objectName: "stepDoneAction"
                text: "Done"
                icon.name: "dialog-ok"
                onTriggered: stepDialog.close()
            }
        ]

        // A mirrored property rather than a Connections, for the reason the
        // recorder's dialog gives below. -1 means the macro being edited is
        // gone -- deleted from the card behind this, or dropped because its key
        // was remapped on the Buttons page -- and a dialog editing nothing has
        // nothing to show.
        readonly property int editing: App.profile.macros.editingRow
        onEditingChanged: if (editing < 0) stepDialog.close()
        onClosed: App.profile.macros.endEdit()

        ColumnLayout {
            spacing: Kirigami.Units.smallSpacing

            Kirigami.InlineMessage {
                objectName: "stepWarning"
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing
                visible: App.profile.macros.stepEditor.warning !== ""
                type: Kirigami.MessageType.Warning
                text: App.profile.macros.stepEditor.warning
            }

            Controls.Label {
                objectName: "stepBudget"
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.smallSpacing
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                color: Kirigami.Theme.disabledTextColor
                text: App.profile.macros.stepEditor.count + " step"
                      + (App.profile.macros.stepEditor.count === 1 ? "" : "s")
                      + ", " + App.profile.macros.stepEditor.totalMs
                      + " ms per pass — " + App.profile.macros.stepsUsed
                      + " of " + App.profile.macros.stepBudget
                      + " steps used across this profile"
            }

            Kirigami.PlaceholderMessage {
                objectName: "stepEmpty"
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.gridUnit
                visible: App.profile.macros.stepEditor.count === 0
                icon.name: "media-record"
                text: "No steps"
                explanation: "A macro with no steps is a key that does nothing. "
                             + "Add a tap and edit it."
            }

            Repeater {
                // The model is the one MacroStepsModel, handed out `constant`
                // and never replaced. A list property rebuilt per read and
                // notified by the edit itself would destroy the delegate under
                // the pointer on the first change -- which is exactly what the
                // Triggers page's knobs used to do.
                model: App.profile.macros.stepEditor

                delegate: MacroStepRow {
                    // Reached through `model` rather than as required
                    // properties per role, because this delegate *is* the row
                    // type and already declares `keyIndex`, `delay` and the
                    // rest -- redeclaring them here would be a name collision
                    // with the properties being assigned.
                    required property int index
                    required property var model

                    objectName: "macroStep_" + index
                    Layout.fillWidth: true
                    Layout.leftMargin: Kirigami.Units.smallSpacing
                    Layout.rightMargin: Kirigami.Units.smallSpacing

                    stepNumber: index + 1
                    keyIndex: model.keyIndex
                    eventIndex: model.eventIndex
                    eventLabel: model.eventLabel
                    delay: model.delay
                    held: model.held
                    tick: App.profile.macros.tickMs
                    keyNames: App.profile.macros.stepKeys
                    eventNames: App.profile.macros.stepEvents

                    onKeyChosen: (i) => App.profile.macros.setStepKey(index, i)
                    onEventChosen: (i) => App.profile.macros.setStepEvent(index, i)
                    onDelayChosen: (ms) => App.profile.macros.setStepDelay(index, ms)
                    onInsertRequested: App.profile.macros.addStep(index)
                    onRemoveRequested: App.profile.macros.removeStep(index)
                }
            }
        }
    }

    Kirigami.Dialog {
        id: recordDialog
        objectName: "recordDialog"
        title: "Record a macro"
        preferredWidth: Kirigami.Units.gridUnit * 24
        standardButtons: Kirigami.Dialog.NoButton

        customFooterActions: [
            Kirigami.Action {
                objectName: "recordStartAction"
                text: App.profile.macros.recording ? "Stop" : "Start recording"
                icon.name: App.profile.macros.recording
                           ? "media-playback-stop" : "media-record"
                onTriggered: {
                    if (App.profile.macros.recording)
                        App.stopMacroRecording();
                    else
                        App.profile.macros.record(keyPicker.currentIndex);
                }
            },
            Kirigami.Action {
                objectName: "recordCancelAction"
                text: "Close"
                icon.name: "dialog-close"
                enabled: !App.profile.macros.recording
                onTriggered: recordDialog.close()
            }
        ]

        // Recording ends by itself after half a minute, or when Stop is
        // pressed; either way the steps are in the profile by the time this
        // changes, so the dialog has nothing left to show.
        //
        // A mirrored property rather than a Connections on the model: the
        // generated qmltypes give the model QAbstractListModel as its
        // superclass and qmllint cannot follow that back to QObject, so it
        // refuses the target assignment. This says the same thing and type
        // checks.
        readonly property bool recording: App.profile.macros.recording
        onRecordingChanged: if (!recording) recordDialog.close()

        ColumnLayout {
            spacing: Kirigami.Units.largeSpacing

            Controls.Label {
                Layout.fillWidth: true
                Layout.margins: Kirigami.Units.largeSpacing
                wrapMode: Text.WordWrap
                text: App.profile.macros.recording
                      ? "Recording — play the sequence on the pad. Anything "
                        + "still held down is released at the end."
                      : "Pick the key that will run it, press Start, then play "
                        + "the sequence on the pad. Paddles are the usual "
                        + "choice: they can run a macro, and a macro cannot "
                        + "press one, since nothing on the host can receive it."
            }

            Controls.ComboBox {
                id: keyPicker
                objectName: "macroKeyPicker"
                Layout.fillWidth: true
                Layout.leftMargin: Kirigami.Units.largeSpacing
                Layout.rightMargin: Kirigami.Units.largeSpacing
                Layout.bottomMargin: Kirigami.Units.largeSpacing
                model: App.profile.macros.triggerKeys
                enabled: !App.profile.macros.recording
                // Scrolling here would move the picker rather than the dialog,
                // and Start recording passes `currentIndex` straight to
                // `record()` -- so a stray notch records the macro onto a key
                // nobody chose, with nothing on screen saying so.
                wheelEnabled: false
            }
        }
    }
}
