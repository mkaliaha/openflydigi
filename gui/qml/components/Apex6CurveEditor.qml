// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard

ColumnLayout {
    id: root
    required property var nodes
    required property int midpointCount
    property string namePrefix: "native"
    signal countModified(int count)
    signal coordinateModified(int index, int axis, int value)
    Layout.fillWidth: true
    spacing: 0

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
                    objectName: root.namePrefix + "ResponsePlot"
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    readonly property var points: {
                        const nodes = root.nodes;
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
        objectName: root.namePrefix + "MidpointCount"
        text: "Interior points"
        model: ["None", "One", "Two"]
        Binding on currentIndex {
            value: root.midpointCount
            restoreMode: Binding.RestoreNone
        }
        onActivated: (index) => root.countModified(index)
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
            model: root.nodes.length
            RowLayout {
                id: pointRow
                required property int index
                readonly property var point: root.nodes[index] || {label: "", x: 0, y: 0, minimum: 0, maximum: 255}
                Layout.fillWidth: true
                Controls.Label { text: pointRow.point.label; Layout.fillWidth: true }
                Controls.SpinBox {
                    objectName: root.namePrefix + "CurveX" + pointRow.index
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
                    onValueModified: root.coordinateModified(pointRow.index, 0, value)
                }
                Controls.SpinBox {
                    objectName: root.namePrefix + "CurveY" + pointRow.index
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
                    onValueModified: root.coordinateModified(pointRow.index, 1, value)
                }
            }
        }
    }
}
