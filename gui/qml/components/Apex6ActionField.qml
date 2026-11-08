// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

RowLayout {
    id: root
    required property var editor
    required property int fieldIndex
    readonly property var spec: editor.fields[fieldIndex]
    visible: spec.visible
    spacing: Kirigami.Units.largeSpacing

    Controls.Label {
        text: root.spec.label
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Controls.ComboBox {
        objectName: "nativeFieldChoice" + root.fieldIndex
        visible: root.spec.choices.length > 0
        model: root.spec.choices
        Binding on currentIndex {
            value: root.spec.selected
            restoreMode: Binding.RestoreNone
        }
        wheelEnabled: false
        Layout.preferredWidth: Kirigami.Units.gridUnit * 12
        onActivated: (index) => root.editor.setField(root.fieldIndex, index)
    }
    Controls.SpinBox {
        objectName: "nativeFieldValue" + root.fieldIndex
        visible: root.spec.choices.length === 0
        from: root.spec.minimum
        to: root.spec.maximum
        Binding on value {
            value: root.spec.value
            restoreMode: Binding.RestoreNone
        }
        stepSize: root.spec.step
        editable: true
        wheelEnabled: false
        onValueModified: root.editor.setField(root.fieldIndex, value)
    }
}
