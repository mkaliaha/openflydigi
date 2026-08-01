// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// One step of a macro: what it presses, whether it is a press or a release,
// and how long the pad waits before playing it.
//
// The same contract as [SliderRow](SliderRow.qml): read the value, report the
// signal, never assign the value back. The model quantises a delay to what the
// profile can actually store -- 10 ms on an Apex 5, 1 ms from protocol 3.2 --
// so typing 155 stores 150, and a control that kept showing its own number
// would be describing a macro the pad is not playing.
//
// **Plain `Controls`, not FormCard delegates.** A Repeater building FormCard
// delegates trips a null dereference in kirigami-addons' FormDelegateBackground
// (`control.parent.visibleChildren`, unguarded), which is why the Lighting and
// Dock pages put their repeated items inside one AbstractFormDelegate instead.
// This row lives in a dialog rather than a form, so it sidesteps that entirely.
//
// **Every control turns the wheel off.** `org.kde.desktop` sets
// `wheelEnabled: true` on ComboBox and SpinBox where Qt's own default is false,
// so a scroll with the pointer over one of these silently rewrites the macro --
// a different button pressed, or a different gap -- with no confirmation and no
// undo. See components/FormComboBox.qml for the full account.

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

RowLayout {
    id: root

    /// 1-based, for the label. The model is 0-based like every other.
    property int stepNumber: 0
    /// Index into `keyNames`, or -1 for a key this application cannot write.
    property int keyIndex: -1
    /// Index into `eventNames`, or -1 for an event it will not write.
    property int eventIndex: -1
    property int delay: 0
    /// Whether this step presses something the macro never releases.
    property bool held: false
    /// The stored time unit, so the spin box steps by something the blob holds.
    property int tick: 10
    property var keyNames: []
    property var eventNames: []
    /// What the event really is, for the ones not in `eventNames`.
    property string eventLabel: ""

    signal keyChosen(int index)
    signal eventChosen(int index)
    signal delayChosen(int ms)
    signal insertRequested()
    signal removeRequested()

    spacing: Kirigami.Units.smallSpacing

    Controls.Label {
        objectName: root.objectName + "Number"
        text: root.stepNumber
        horizontalAlignment: Text.AlignRight
        color: Kirigami.Theme.disabledTextColor
        Layout.minimumWidth: Kirigami.Units.gridUnit * 1.5
    }

    // Marks the press that is never released, rather than only saying so in
    // the banner above: on a long macro the banner names the key and the row
    // is what tells you where it is.
    Kirigami.Icon {
        objectName: root.objectName + "Held"
        source: "dialog-warning"
        visible: root.held
        implicitWidth: Kirigami.Units.iconSizes.small
        implicitHeight: Kirigami.Units.iconSizes.small

        Controls.ToolTip.visible: hoverHandler.hovered
        Controls.ToolTip.text: "This key is pressed here and never released, "
                               + "so the pad holds it after the macro ends."
        HoverHandler { id: hoverHandler }
    }

    Controls.ComboBox {
        objectName: root.objectName + "Key"
        model: root.keyNames
        currentIndex: root.keyIndex
        wheelEnabled: false
        Layout.fillWidth: true
        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
        onActivated: (index) => root.keyChosen(index)
    }

    Controls.ComboBox {
        objectName: root.objectName + "Event"
        model: root.eventNames
        currentIndex: root.eventIndex
        // A macro written by other software can carry an event this editor
        // does not offer -- hold, or one of the two stick pseudo-events. The
        // row says what it is instead of showing the first entry and quietly
        // proposing to change it.
        enabled: root.eventIndex >= 0
        displayText: root.eventIndex >= 0 ? currentText : root.eventLabel
        wheelEnabled: false
        Layout.minimumWidth: Kirigami.Units.gridUnit * 6
        onActivated: (index) => root.eventChosen(index)
    }

    Controls.SpinBox {
        objectName: root.objectName + "Delay"
        from: 0
        to: 8000
        stepSize: root.tick
        value: root.delay
        editable: true
        wheelEnabled: false
        Layout.minimumWidth: Kirigami.Units.gridUnit * 5
        textFromValue: (value) => value + " ms"
        valueFromText: (text) => parseInt(text) || 0
        onValueModified: root.delayChosen(value)

        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: "How long the pad waits before this step. "
                               + "Stored in units of " + root.tick + " ms."
    }

    Controls.ToolButton {
        objectName: root.objectName + "Insert"
        icon.name: "list-add"
        onClicked: root.insertRequested()

        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: "Add a tap after this step"
    }

    Controls.ToolButton {
        objectName: root.objectName + "Delete"
        icon.name: "edit-delete"
        onClicked: root.removeRequested()

        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: "Remove this step"
    }
}
