// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

RowLayout {
    id: root
    required property Apex6ButtonsModel editor
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
        currentIndex: root.spec.selected
        wheelEnabled: false
        Layout.preferredWidth: Kirigami.Units.gridUnit * 12
        onActivated: (index) => root.editor.setField(root.fieldIndex, index)
    }
    Controls.SpinBox {
        objectName: "nativeFieldValue" + root.fieldIndex
        visible: root.spec.choices.length === 0
        from: root.spec.minimum
        to: root.spec.maximum
        value: root.spec.value
        stepSize: root.spec.step
        editable: true
        wheelEnabled: false
        onValueModified: root.editor.setField(root.fieldIndex, value)
    }
}
