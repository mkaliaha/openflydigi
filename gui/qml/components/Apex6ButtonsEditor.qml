// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

ColumnLayout {
    id: root
    readonly property Apex6ButtonsModel editor: App.profile.nativeButtons
    spacing: Kirigami.Units.largeSpacing
    visible: App.profile.loaded

    Kirigami.InlineMessage {
        Layout.fillWidth: true
        visible: root.editor.error !== ""
        text: root.editor.error
        type: Kirigami.MessageType.Error
        objectName: "nativeMappingError"
    }

    FormCard.FormCard {
        FormComboBox {
            objectName: "nativeLayer"
            text: "Mapping layer"
            description: "Use a Hold Shift or Toggle Shift action on the base layer to activate another layer."
            model: root.editor.layerNames
            currentIndex: root.editor.layer
            onActivated: (index) => root.editor.layer = index
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeCreateLayer"
            visible: !root.editor.layerExists
            text: "Create this layer"
            icon.name: "list-add"
            onClicked: root.editor.createLayer()
        }
        FormComboBox {
            objectName: "nativeSource"
            text: "Button"
            enabled: root.editor.layerExists
            model: root.editor.sourceNames
            currentIndex: root.editor.source
            onActivated: (index) => root.editor.source = index
        }
        FormCard.FormTextDelegate {
            text: root.editor.bindingState
            description: "Changes stay in this editor until you Apply or Save profile."
        }
    }

    FormCard.FormCard {
        visible: root.editor.layerExists
        FormCard.FormTextDelegate {
            visible: !root.editor.editable
            text: "Contains an unsupported action"
            description: "The stored actions are preserved. Reset this button to remove its override before creating a replacement."
        }
        FormComboBox {
            objectName: "nativeAction"
            visible: root.editor.actionNames.length > 0
            text: "Action"
            model: root.editor.actionNames
            currentIndex: root.editor.action
            onActivated: (index) => root.editor.action = index
        }
        FormComboBox {
            objectName: "nativeActivator"
            visible: root.editor.actionNames.length > 0
            enabled: root.editor.editable
            text: "When"
            model: root.editor.activatorNames
            currentIndex: root.editor.activator
            onActivated: (index) => root.editor.activator = index
        }
        FormComboBox {
            objectName: "nativeTarget"
            visible: root.editor.actionNames.length > 0
            enabled: root.editor.editable
            text: "Output"
            model: root.editor.targetNames
            currentIndex: root.editor.target
            onActivated: (index) => root.editor.target = index
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: root.editor.actionNames.length > 0 && root.editor.editable
            Apex6ActionField { Layout.fillWidth: true; editor: root.editor; fieldIndex: 0 }
            Apex6ActionField { Layout.fillWidth: true; editor: root.editor; fieldIndex: 1 }
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeAddAction"
            text: "Add action"
            icon.name: "list-add"
            enabled: root.editor.editable && root.editor.actionNames.length < 5
            onClicked: root.editor.addAction()
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeRemoveAction"
            text: "Remove selected action"
            icon.name: "list-remove"
            enabled: root.editor.editable && root.editor.actionNames.length > 0
            onClicked: root.editor.removeAction()
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeResetButton"
            text: "Reset this button"
            description: "Remove this button's override from the selected layer."
            icon.name: "edit-undo"
            onClicked: root.editor.resetButton()
        }
    }

    FormCard.FormHeader { title: "Press timing · all layers" }
    FormCard.FormCard {
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            RowLayout {
                Controls.Label { text: "Long press (ms)"; Layout.fillWidth: true }
                Controls.SpinBox {
                    objectName: "nativeLongPress"
                    from: 1
                    to: 65535
                    value: root.editor.longPress
                    editable: true
                    wheelEnabled: false
                    onValueModified: root.editor.longPress = value
                }
            }
            RowLayout {
                Controls.Label { text: "Double press (ms)"; Layout.fillWidth: true }
                Controls.SpinBox {
                    objectName: "nativeDoublePress"
                    from: 1
                    to: 65535
                    value: root.editor.doublePress
                    editable: true
                    wheelEnabled: false
                    onValueModified: root.editor.doublePress = value
                }
            }
        }
        FormCard.FormTextDelegate {
            text: "Output depends on controller mode"
            description: "Keyboard, mouse and consumer outputs depend on the controller's connection mode. Their IDs and modifier masks use the native mapping values."
        }
    }
}
