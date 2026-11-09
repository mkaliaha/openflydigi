// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

ColumnLayout {
    id: root
    readonly property Apex6MacrosModel editor: App.profile.nativeMacros
    property int newId: editor.nextId
    spacing: Kirigami.Units.largeSpacing

    Kirigami.InlineMessage {
        objectName: "nativeMacroError"
        Layout.fillWidth: true
        visible: root.editor.error !== "" || root.editor.readIssue !== ""
        text: root.editor.error || root.editor.readIssue
        type: Kirigami.MessageType.Error
    }

    FormCard.FormCard {
        FormCard.FormTextDelegate {
            text: "Macros · played by the controller"
            description: "Create a definition here, then choose it as an output on the Buttons, Sticks or Triggers page in the same layer. Each layer holds up to 10 macros, with up to 32 steps each."
        }
        FormComboBox {
            objectName: "nativeMacroLayer"
            text: "Layer"
            model: root.editor.layerNames
            enabled: !root.editor.draftDirty
            Binding on currentIndex { value: root.editor.layer; restoreMode: Binding.RestoreNone }
            onActivated: (index) => root.editor.layer = index
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeMacroCreateLayer"
            visible: !root.editor.layerExists
            text: "Create this Shift layer"
            icon.name: "list-add"
            onClicked: root.editor.createLayer()
        }
        FormComboBox {
            objectName: "nativeMacroPicker"
            text: "Macro"
            model: root.editor.macroNames
            enabled: !root.editor.draftDirty && root.editor.macroNames.length > 0
            Binding on currentIndex { value: root.editor.current; restoreMode: Binding.RestoreNone }
            onActivated: (index) => root.editor.current = index
        }
        FormCard.FormTextDelegate {
            text: root.editor.macroNames.length + " of 10 macros in this layer"
            description: "IDs identify definitions independently of their button bindings. Creating or copying a macro does not bind it."
        }
        FormCard.AbstractFormDelegate {
            background: null
            contentItem: ColumnLayout {
                RowLayout {
                    Controls.Label { text: "New macro ID"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                    Controls.SpinBox {
                        objectName: "nativeMacroNewId"
                        from: 0
                        to: 255
                        value: root.newId
                        editable: true
                        wheelEnabled: false
                        enabled: root.editor.canAdd
                        onValueModified: root.newId = value
                    }
                }
                RowLayout {
                    Controls.Button {
                        objectName: "nativeMacroCreate"
                        text: "Create macro"
                        icon.name: "list-add"
                        enabled: root.editor.canAdd
                        onClicked: {
                            root.editor.create(root.newId);
                            root.newId = root.editor.nextId;
                        }
                    }
                    Controls.Button {
                        objectName: "nativeMacroCopy"
                        text: "Copy selected"
                        icon.name: "edit-copy"
                        enabled: root.editor.canAdd && root.editor.editable
                        onClicked: {
                            root.editor.duplicate(root.newId);
                            root.newId = root.editor.nextId;
                        }
                    }
                }
            }
        }
    }

    FormCard.FormCard {
        visible: root.editor.macroId >= 0
        FormCard.AbstractFormDelegate {
            visible: root.editor.namesSupported
            background: null
            contentItem: ColumnLayout {
                Controls.Label { text: "Name" }
                Controls.TextField {
                    objectName: "nativeMacroName"
                    Layout.fillWidth: true
                    placeholderText: "Optional name"
                    Binding on text { value: root.editor.name; restoreMode: Binding.RestoreNone }
                    onTextEdited: root.editor.name = text
                }
            }
        }
        FormComboBox {
            objectName: "nativeMacroMode"
            text: "Playback"
            description: "Hold outputs requires press steps only and cannot include stick movement. Other modes need matching releases; a stick returns to centre with a zero-position step."
            model: root.editor.modeNames
            enabled: root.editor.editable
            Binding on currentIndex { value: root.editor.mode; restoreMode: Binding.RestoreNone }
            onActivated: (index) => root.editor.mode = index
        }
        FormCard.AbstractFormDelegate {
            background: null
            contentItem: RowLayout {
                Controls.Label { text: "Repeat gap (ms)"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Controls.SpinBox {
                    objectName: "nativeMacroInterval"
                    from: 0
                    // Keep an out-of-range stored gap readable without
                    // silently clamping it. The model validates edited gaps.
                    to: Math.max(10000, root.editor.interval)
                    editable: true
                    wheelEnabled: false
                    Binding on value { value: root.editor.interval; restoreMode: Binding.RestoreNone }
                    onValueModified: root.editor.interval = value
                }
            }
        }
        FormCard.FormTextDelegate {
            visible: !root.editor.editable
            text: "Stored sequence is read-only"
            description: "This definition uses unsupported data (playback: " + root.editor.storedMode + "). Its body stays unchanged when you edit its name or repeat gap. Delete it to replace it."
        }
        FormCard.FormTextDelegate {
            visible: !root.editor.editable
            text: "Stored steps"
            description: root.editor.stepNames.join("\n")
        }
    }

    FormCard.FormCard {
        visible: root.editor.macroId >= 0 && root.editor.editable
        FormCard.FormTextDelegate {
            text: root.editor.stepCount + " of 32 steps · " + root.editor.duration + " ms"
            description: "Delay is the wait before each step, in 1 ms units. A sequence can last up to 65535 ms. Edit all matching presses and releases, then Update macro. An empty sequence does nothing."
        }
        FormComboBox {
            objectName: "nativeMacroStep"
            text: "Step"
            model: root.editor.stepNames
            enabled: root.editor.stepCount > 0
            Binding on currentIndex {
                value: root.editor.stepCount > 0 ? root.editor.step : -1
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.step = index
        }
        FormCard.AbstractFormDelegate {
            background: null
            contentItem: ColumnLayout {
                RowLayout {
                    Controls.Button {
                        objectName: "nativeMacroAddStep"
                        text: "Add step"
                        enabled: root.editor.stepCount < 32
                        onClicked: root.editor.addStep()
                    }
                    Controls.Button {
                        objectName: "nativeMacroRemoveStep"
                        text: "Remove step"
                        enabled: root.editor.stepCount > 0
                        onClicked: root.editor.removeStep()
                    }
                }
                RowLayout {
                    Controls.Button {
                        objectName: "nativeMacroMoveUp"
                        text: "Move earlier"
                        enabled: root.editor.step > 0
                        onClicked: root.editor.moveStep(-1)
                    }
                    Controls.Button {
                        objectName: "nativeMacroMoveDown"
                        text: "Move later"
                        enabled: root.editor.step + 1 < root.editor.stepCount
                        onClicked: root.editor.moveStep(1)
                    }
                }
            }
        }
        FormComboBox {
            objectName: "nativeMacroEvent"
            text: "Event"
            model: ["Press", "Release"]
            enabled: root.editor.stepCount > 0
            Binding on currentIndex { value: root.editor.event; restoreMode: Binding.RestoreNone }
            onActivated: (index) => root.editor.event = index
        }
        FormCard.AbstractFormDelegate {
            enabled: root.editor.stepCount > 0
            background: null
            contentItem: RowLayout {
                Controls.Label { text: "Delay before step (ms)"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Controls.SpinBox {
                    objectName: "nativeMacroDelay"
                    from: 0
                    to: 65535
                    editable: true
                    wheelEnabled: false
                    Binding on value { value: root.editor.delay; restoreMode: Binding.RestoreNone }
                    onValueModified: root.editor.delay = value
                }
            }
        }
        FormComboBox {
            objectName: "nativeMacroTarget"
            text: "Output"
            enabled: root.editor.stepCount > 0
            model: root.editor.targetNames
            Binding on currentIndex { value: root.editor.target; restoreMode: Binding.RestoreNone }
            onActivated: (index) => root.editor.target = index
        }
        FormCard.AbstractFormDelegate {
            visible: root.editor.stepCount > 0
            background: null
            contentItem: ColumnLayout {
                Apex6ActionField { editor: root.editor; fieldIndex: 0; Layout.fillWidth: true }
                Apex6ActionField { editor: root.editor; fieldIndex: 1; Layout.fillWidth: true }
            }
        }
    }

    Kirigami.InlineMessage {
        objectName: "nativeMacroValidation"
        Layout.fillWidth: true
        visible: root.editor.validation !== ""
        text: root.editor.validation
        type: Kirigami.MessageType.Warning
    }
    FormCard.FormCard {
        visible: root.editor.macroId >= 0
        FormCard.FormTextDelegate {
            text: root.editor.draftDirty ? "Draft changes" : "Definition in the open profile"
            description: "Update macro validates this draft and puts it in the open profile. Apply or Save profile then writes the profile to the controller. Reload discards local edits and drafts."
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeMacroCommit"
            text: "Update macro"
            icon.name: "dialog-ok"
            enabled: root.editor.canCommit
            onClicked: root.editor.commit()
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeMacroDiscard"
            text: "Discard draft"
            icon.name: "edit-undo"
            enabled: root.editor.draftDirty
            onClicked: root.editor.discard()
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeMacroDelete"
            text: "Delete this macro"
            description: "Also removes its button and stick/trigger-zone bindings in this layer. Other actions and layers stay intact."
            icon.name: "edit-delete"
            enabled: !root.editor.draftDirty
            onClicked: root.editor.remove()
        }
    }
}
