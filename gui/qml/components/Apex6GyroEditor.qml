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
    required property Apex6GyroModel editor
    spacing: Kirigami.Units.largeSpacing

    Kirigami.InlineMessage {
        objectName: "nativeGyroError"
        Layout.fillWidth: true
        visible: root.editor.error !== ""
        text: root.editor.error
        type: Kirigami.MessageType.Error
    }

    FormCard.FormCard {
        FormCard.FormTextDelegate {
            text: "Gyro · all layers"
            description: "These settings apply throughout the profile. Changes stay in this editor until you Apply or Save profile."
        }
        FormCard.FormTextDelegate {
            objectName: "nativeGyroIssue"
            visible: root.editor.issue !== ""
            text: "Stored gyro settings cannot be edited"
            description: root.editor.issue + " The stored settings are kept unchanged."
        }
        FormComboBox {
            objectName: "nativeGyroTarget"
            enabled: root.editor.editable
            text: "Mapped to"
            description: "Changing to a stick or mouse starts with its default response curve. Off keeps the curve."
            model: root.editor.targetNames
            Binding on currentIndex {
                value: root.editor.target
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.target = index
        }
        FormCard.FormTextDelegate {
            objectName: "nativeGyroMouseNote"
            visible: root.editor.editable && root.editor.target === 3
            text: "Mouse configuration"
            description: "Mouse settings can be stored on the controller. Mouse output and radius have not been verified on Linux."
        }
        FormCard.FormTextDelegate {
            visible: root.editor.editable && root.editor.axes !== "imu_x / imu_y"
            text: "Custom axis routing: " + root.editor.axes
            description: "Kept unchanged while editing other settings. Selecting a target restores the standard horizontal and vertical axes."
        }
    }

    FormCard.FormCard {
        visible: root.editor.editable && root.editor.target !== 0
        FormCard.FormTextDelegate {
            objectName: "nativeGyroNoKey"
            visible: !root.editor.hasKey
            text: "Choose an activation key"
            description: "No button currently activates gyro mapping."
        }
        FormComboBox {
            objectName: "nativeGyroKey"
            text: "Primary activation key"
            description: "A primary key is required when changing activation keys."
            model: root.editor.keyNames
            Binding on currentIndex {
                value: root.editor.key
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.key = index
        }
        FormComboBox {
            objectName: "nativeGyroSecondKey"
            enabled: root.editor.key > 0
            text: "Second activation key"
            description: "Optional; available in both hold and toggle modes."
            model: root.editor.keyNames
            Binding on currentIndex {
                value: root.editor.secondKey
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.secondKey = index
        }
        FormComboBox {
            objectName: "nativeGyroEnableType"
            enabled: root.editor.hasKey
            text: "Turned on by"
            description: root.editor.enableType < 0 && root.editor.hasKey
                         ? "Stored keys use different modes. Choosing a mode applies it to all activation keys."
                         : "Applies to all activation keys, including any additional stored keys."
            model: root.editor.enableTypeNames
            Binding on currentIndex {
                value: root.editor.enableType
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.enableType = index
        }
        FormCard.FormTextDelegate {
            objectName: "nativeGyroAdditionalKeys"
            visible: root.editor.additionalKeys !== ""
            text: "Additional activation keys"
            description: root.editor.additionalKeys + ". These keys are preserved when changing the primary and second keys."
        }
        ModelSwitch {
            objectName: "nativeGyroInvertX"
            text: "Invert horizontal direction"
            value: root.editor.invertX
            onMoved: (wanted) => root.editor.invertX = wanted
        }
        ModelSwitch {
            objectName: "nativeGyroInvertY"
            text: "Invert vertical direction"
            value: root.editor.invertY
            onMoved: (wanted) => root.editor.invertY = wanted
        }
        FormCard.AbstractFormDelegate {
            visible: root.editor.target === 3
            background: null
            contentItem: RowLayout {
                Controls.Label {
                    text: "Mouse window radius · 0–255"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                Controls.SpinBox {
                    objectName: "nativeGyroMouseRadius"
                    Accessible.name: "Mouse window radius"
                    from: 0
                    to: 255
                    editable: true
                    wheelEnabled: false
                    Binding on value {
                        value: root.editor.mouseRadius
                        restoreMode: Binding.RestoreNone
                    }
                    onValueModified: root.editor.mouseRadius = value
                }
            }
        }
    }

    FormCard.FormCard {
        visible: root.editor.editable && root.editor.target !== 0
        FormCard.FormTextDelegate {
            text: "Response curve"
            description: "Input and output use 0–255 units. Edit the start, end and up to two interior points."
        }
        FormCard.FormTextDelegate {
            visible: root.editor.curveIssue !== ""
            text: "Stored curve cannot be edited"
            description: root.editor.curveIssue + ". Other gyro settings can still be edited."
        }
        Apex6CurveEditor {
            visible: root.editor.curveEditable
            namePrefix: "nativeGyro"
            nodes: root.editor.nodes
            midpointCount: root.editor.midpointCount
            onCountModified: (count) => root.editor.midpointCount = count
            onCoordinateModified: (index, axis, value) => root.editor.setCoordinate(index, axis, value)
        }
    }
}
