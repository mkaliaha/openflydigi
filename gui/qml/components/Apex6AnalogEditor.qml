// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

ColumnLayout {
    id: root
    required property Apex6AnalogModel editor
    spacing: Kirigami.Units.largeSpacing

    Kirigami.InlineMessage {
        objectName: "nativeAnalogError"
        Layout.fillWidth: true
        visible: root.editor.error !== ""
        text: root.editor.error
        type: Kirigami.MessageType.Error
    }

    FormCard.FormCard {
        FormComboBox {
            objectName: "nativeAnalogSide"
            text: root.editor.isStick ? "Stick" : "Trigger"
            model: ["Left", "Right"]
            Binding on currentIndex {
                value: root.editor.side
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.side = index
        }
        FormCard.FormTextDelegate {
            text: "Response · all layers"
            description: "Input and output use 0–255 units: 0 is released or centred, 255 is full travel. Changes stay in this editor until you Apply or Save profile."
        }
        FormCard.FormTextDelegate {
            visible: root.editor.responseIssue !== ""
            text: "Stored response cannot be edited"
            description: root.editor.responseIssue + ". Its bytes are preserved; zones can still be edited."
        }
    }

    FormCard.FormCard {
        visible: root.editor.responseEditable
        FormComboBox {
            objectName: "nativeResponsePreset"
            text: "Sensitivity curve"
            description: "Choosing a preset resets the input window and curve points. Edit the points below for a custom curve."
            model: root.editor.presetNames
            Binding on currentIndex {
                value: root.editor.preset
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.preset = index
        }
        FormCard.AbstractFormDelegate {
            background: null
            contentItem: ColumnLayout {
                Controls.Label {
                    text: "Curve preview · stored points"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Kirigami.Units.gridUnit * 8
                    Shape {
                        id: plot
                        objectName: "nativeResponsePlot"
                        anchors.fill: parent
                        preferredRendererType: Shape.CurveRenderer
                        readonly property var points: {
                            const nodes = root.editor.nodes;
                            if (nodes.length < 2)
                                return [];
                            const out = [Qt.point(0, height * (1 - nodes[0].y / 255))];
                            for (let node of nodes)
                                out.push(Qt.point(width * node.x / 255, height * (1 - node.y / 255)));
                            out.push(Qt.point(width, height * (1 - nodes[nodes.length - 1].y / 255)));
                            return out;
                        }
                        ShapePath {
                            strokeColor: Kirigami.Theme.disabledTextColor
                            strokeWidth: 1
                            strokeStyle: ShapePath.DashLine
                            fillColor: "transparent"
                            PathPolyline { path: [Qt.point(0, plot.height), Qt.point(plot.width, 0)] }
                        }
                        ShapePath {
                            strokeColor: Kirigami.Theme.highlightColor
                            strokeWidth: 2
                            fillColor: "transparent"
                            PathPolyline { path: plot.points }
                        }
                    }
                }
                Controls.Label {
                    text: "Input → 0–255 · Output ↑ 0–255"
                    font: Kirigami.Theme.smallFont
                }
                Controls.Label {
                    text: "Preview joins the stored points; it is not a measurement of controller output."
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.disabledTextColor
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
            }
        }
        FormComboBox {
            objectName: "nativeMidpointCount"
            text: "Interior points"
            model: ["None", "One", "Two"]
            Binding on currentIndex {
                value: root.editor.midpointCount
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.midpointCount = index
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            RowLayout {
                Controls.Label { text: "Point"; Layout.fillWidth: true }
                Controls.Label { text: "Input"; Layout.preferredWidth: Kirigami.Units.gridUnit * 6 }
                Controls.Label { text: "Output"; Layout.preferredWidth: Kirigami.Units.gridUnit * 6 }
            }
            Repeater {
                model: root.editor.nodes.length
                RowLayout {
                    id: pointRow
                    required property int index
                    readonly property var point: root.editor.nodes[index] || {label: "", x: 0, y: 0, minimum: 0, maximum: 255}
                    Layout.fillWidth: true
                    Controls.Label { text: pointRow.point.label; Layout.fillWidth: true }
                    Controls.SpinBox {
                        objectName: "nativeCurveX" + pointRow.index
                        Accessible.name: pointRow.point.label + " input"
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                        from: pointRow.point.minimum
                        to: pointRow.point.maximum
                        Binding on value {
                            value: pointRow.point.x
                            restoreMode: Binding.RestoreNone
                        }
                        editable: true
                        wheelEnabled: false
                        onValueModified: root.editor.setCoordinate(pointRow.index, 0, value)
                    }
                    Controls.SpinBox {
                        objectName: "nativeCurveY" + pointRow.index
                        Accessible.name: pointRow.point.label + " output"
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                        from: 0
                        to: 255
                        Binding on value {
                            value: pointRow.point.y
                            restoreMode: Binding.RestoreNone
                        }
                        editable: true
                        wheelEnabled: false
                        onValueModified: root.editor.setCoordinate(pointRow.index, 1, value)
                    }
                }
            }
        }
        FormCard.FormTextDelegate {
            text: "Input window"
            description: "Start input sets the inner dead zone; End input sets where full travel is reached. Start and End output set the values at those limits. Equal input positions create a step."
        }
        ModelSwitch {
            objectName: "nativeCircular"
            visible: root.editor.isStick
            text: "Circular range"
            description: "Limits diagonal travel to the circular boundary."
            value: root.editor.circular
            onMoved: (wanted) => root.editor.circular = wanted
        }
        ModelSwitch {
            objectName: "nativeInvertX"
            visible: root.editor.isStick
            text: "Invert horizontal direction"
            value: root.editor.invertX
            onMoved: (wanted) => root.editor.invertX = wanted
        }
        ModelSwitch {
            objectName: "nativeInvertY"
            visible: root.editor.isStick
            text: "Invert vertical direction"
            value: root.editor.invertY
            onMoved: (wanted) => root.editor.invertY = wanted
        }
    }

    FormCard.FormHeader { title: "Zone actions" }
    FormCard.FormCard {
        FormComboBox {
            objectName: "nativeZoneLayer"
            text: "Mapping layer"
            description: "Zones belong to this layer and the selected side. Bind Hold Shift or Toggle Shift on the Buttons page to activate a Shift layer."
            model: root.editor.layerNames
            Binding on currentIndex {
                value: root.editor.layer
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.layer = index
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeZoneCreateLayer"
            visible: !root.editor.layerExists
            text: "Create this layer"
            icon.name: "list-add"
            onClicked: root.editor.createLayer()
        }
        FormCard.FormTextDelegate {
            text: root.editor.zoneNames.length + " of " + root.editor.maximumZones + " zones used"
            description: root.editor.isStick
                ? "Each zone binds a direction and radius range (0–255) to an output. Overlapping zones and repeated directions are allowed."
                : "Each zone binds a travel range (0–255) to an output. Overlapping zones are allowed. These controls do not change trigger resistance or vibration."
        }
        FormCard.FormTextDelegate {
            visible: root.editor.layerExists && !root.editor.editable
            text: "Contains an unsupported zone"
            description: "Stored zones are preserved. Clear this side's zones in this layer before creating replacements."
        }
        FormComboBox {
            objectName: "nativeZoneAngle"
            visible: root.editor.isStick && root.editor.angle >= 0
            text: "Direction mode"
            model: root.editor.angleNames
            Binding on currentIndex {
                value: root.editor.angle
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.angle = index
        }
        FormComboBox {
            objectName: "nativeZone"
            visible: root.editor.zoneNames.length > 0
            text: "Zone"
            model: root.editor.zoneNames
            Binding on currentIndex {
                value: root.editor.zone
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.zone = index
        }
        FormComboBox {
            objectName: "nativeZoneDirection"
            visible: root.editor.isStick && root.editor.zoneNames.length > 0
            enabled: root.editor.editable
            text: "Direction"
            model: root.editor.directionNames
            Binding on currentIndex {
                value: root.editor.direction
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.direction = index
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: root.editor.zoneNames.length > 0
            enabled: root.editor.editable
            RowLayout {
                Controls.Label { text: "Range start"; Layout.fillWidth: true }
                Controls.SpinBox {
                    objectName: "nativeZoneStart"
                    from: 0
                    to: root.editor.stop
                    Binding on value {
                        value: root.editor.start
                        restoreMode: Binding.RestoreNone
                    }
                    editable: true
                    wheelEnabled: false
                    onValueModified: root.editor.start = value
                }
            }
            RowLayout {
                Controls.Label { text: "Range end"; Layout.fillWidth: true }
                Controls.SpinBox {
                    objectName: "nativeZoneStop"
                    from: root.editor.start
                    to: 255
                    Binding on value {
                        value: root.editor.stop
                        restoreMode: Binding.RestoreNone
                    }
                    editable: true
                    wheelEnabled: false
                    onValueModified: root.editor.stop = value
                }
            }
        }
        FormComboBox {
            objectName: "nativeZoneTarget"
            visible: root.editor.zoneNames.length > 0
            enabled: root.editor.editable
            text: "Output"
            model: root.editor.targetNames
            Binding on currentIndex {
                value: root.editor.target
                restoreMode: Binding.RestoreNone
            }
            onActivated: (index) => root.editor.target = index
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Kirigami.Units.largeSpacing
            visible: root.editor.zoneNames.length > 0 && root.editor.editable
            Apex6ActionField { Layout.fillWidth: true; editor: root.editor; fieldIndex: 0 }
            Apex6ActionField { Layout.fillWidth: true; editor: root.editor; fieldIndex: 1 }
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeAddZone"
            text: "Add zone"
            icon.name: "list-add"
            enabled: root.editor.editable && root.editor.zoneNames.length < root.editor.maximumZones
            onClicked: root.editor.addZone()
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeRemoveZone"
            text: "Remove selected zone"
            icon.name: "list-remove"
            enabled: root.editor.editable && root.editor.zoneNames.length > 0
            onClicked: root.editor.removeZone()
        }
        FormCard.FormButtonDelegate {
            objectName: "nativeClearZones"
            text: "Clear this side's zones"
            description: "Only removes zones from this side in the selected layer."
            icon.name: "edit-clear"
            enabled: root.editor.layerExists
            onClicked: root.editor.clearZones()
        }
    }
}
