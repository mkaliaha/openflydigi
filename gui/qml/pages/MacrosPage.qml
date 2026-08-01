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
                required property var steps

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

                    FormCard.FormTextDelegate {
                        objectName: "macroSteps_" + macroEntry.index
                        text: macroEntry.stepCount + " step"
                              + (macroEntry.stepCount === 1 ? "" : "s")
                              + ", " + macroEntry.duration + " ms"
                        description: macroEntry.steps.map(
                            step => "+" + step.delay + " ms  " + step.event
                                    + "  " + step.key).join("\n")
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
                description: "One profile holds " + App.profile.macros.slots
                             + " macros and " + App.profile.macros.stepBudget
                             + " steps between them — that is the size of the "
                             + "page the pad keeps them in."
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
