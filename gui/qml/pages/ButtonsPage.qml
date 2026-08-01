// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Button remapping for the open profile.
//
// A list grouped by where the buttons sit on the shell, rather than the flat
// 23-row table this replaces -- and deliberately not a picture of a controller,
// since Flydigi's service agreement claims their interface design and artwork.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "buttonsPage"
    title: "Buttons"

    actions: [
        Kirigami.Action {
            objectName: "resetAllAction"
            text: "Reset all to default"
            icon.name: "edit-undo"
            enabled: App.profile.loaded
            onTriggered: App.profile.resetAll()
        }
    ]

    footer: ProfileFooter {}

    // Remapping is the pad's own key handling, and another driver holding the
    // pad takes that over -- so the controls are shut off rather than left to
    // write a key table nothing is currently reading.
    Kirigami.InlineMessage {
        objectName: "buttonsThirdParty"
        parent: page.overlay
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Kirigami.Units.largeSpacing
        z: 10
        visible: App.device.thirdParty
        type: Kirigami.MessageType.Warning
        text: "Other software is allowed to take the pad over"
              + (App.device.controlBy !== ""
                 ? " (" + App.device.controlBy + ")" : "")
              + ", so it is handling the buttons rather than the pad. Turn it "
              + "off on the Controller page to remap them."
    }

    ListView {
        id: keyList
        objectName: "keyList"
        enabled: !App.device.thirdParty
        // Null, not merely hidden. KeyMapModel reports a constant 23 rows and
        // invents an identity mapping when no config is open, so a view bound
        // to it with nothing to show renders 23 editable rows of fiction.
        model: App.profile.loaded ? App.profile.keys : null
        currentIndex: -1
        reuseItems: true

        // Inside the view rather than beside it. ScrollablePage reparents its
        // one Flickable child into the ScrollView and hides everything else
        // (`scrollingArea.visible = false`), so a placeholder that is a sibling
        // of this list lives in the hidden half and can never be drawn -- which
        // is what a pad asleep at startup used to get: a blank page under a
        // footer saying it was reading. An empty view leaves contentHeight at
        // zero, which sizes the content item to the viewport, so centring in it
        // centres on screen.
        Kirigami.PlaceholderMessage {
            objectName: "buttonsPlaceholder"
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: !App.profile.loaded
            icon.name: "input-gaming"
            text: "No profile open"
            explanation: "Pick a profile on the Controller page. Reading one "
                         + "makes the pad re-seat its trigger motors, so it "
                         + "only happens when you ask."
        }

        section.property: "cluster"
        section.criteria: ViewSection.FullString
        section.delegate: Kirigami.ListSectionHeader {
            required property string section
            width: ListView.view.width
            text: section
        }

        delegate: Controls.ItemDelegate {
            id: keyRow

            required property int index
            required property string key
            required property string label
            required property int targetIndex
            required property int turbo
            required property int turboMode
            required property bool isRemapped
            required property bool isEditable
            required property string special

            objectName: "keyRow_" + key
            width: ListView.view.width
            hoverEnabled: true
            highlighted: false
            down: false

            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing

                ColumnLayout {
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 8
                    spacing: 0

                    Controls.Label {
                        objectName: "keyLabel_" + keyRow.key
                        text: keyRow.label
                        font.bold: keyRow.isRemapped
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    // **What the key actually does, when it is not a remap.**
                    // A key bound to a macro or a keystroke has no entry in the
                    // target list, so the combo beside it falls back to
                    // "(default)" -- which reads as "this key does what the
                    // shell says" about a key that runs a macro. The row said
                    // only "not editable here" and the tooltip named both
                    // possibilities, because nothing on the row could tell them
                    // apart. `special` can.
                    Controls.Label {
                        objectName: "keySpecial_" + keyRow.key
                        text: keyRow.special === "macro" ? "runs a macro"
                              : keyRow.special === "keyboard" ? "sends a keystroke"
                              : "not editable here"
                        visible: !keyRow.isEditable
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                    }
                }

                // **The wheel belongs to the list, not to the controls on a
                // row.** `org.kde.desktop` sets `wheelEnabled: true` on ComboBox
                // and SpinBox where Qt's own default is false, so scrolling with
                // the pointer over one of these rewrites a key mapping instead
                // of moving the list -- silently, since a remap needs no
                // confirmation. Off, the event goes unaccepted and reaches the
                // view. See components/FormComboBox.qml for the same defect in
                // the FormCard delegates, which cannot be told this directly.
                Controls.ComboBox {
                    objectName: "target_" + keyRow.key
                    model: App.profile.keys.targets
                    currentIndex: keyRow.targetIndex
                    enabled: keyRow.isEditable
                    wheelEnabled: false
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 8
                    onActivated: App.profile.keys.setTarget(keyRow.index, currentIndex)

                    Controls.ToolTip.visible: hovered && !keyRow.isEditable
                    Controls.ToolTip.text: keyRow.special === "macro"
                        ? "This key runs a macro. Edit it on the Macros page — "
                          + "deleting it there gives the key back, and remapping "
                          + "a key here would drop its macro, since the pad "
                          + "would otherwise send the new binding and play the "
                          + "old macro underneath it."
                        : keyRow.special === "keyboard"
                        ? "This key sends a keystroke, which Flydigi's own "
                          + "software types on the host rather than the pad. "
                          + "Nothing here can reproduce that, so the binding is "
                          + "shown and left alone."
                        : "This key is not editable here."
                }

                Controls.SpinBox {
                    objectName: "turbo_" + keyRow.key
                    from: 0
                    to: App.profile.keys.turboMax
                    value: keyRow.turbo
                    enabled: keyRow.isEditable
                    editable: true
                    wheelEnabled: false
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 6
                    // "0" means off rather than "repeat zero times a second".
                    textFromValue: (value) => value === 0 ? "off" : value + " Hz"
                    valueFromText: (text) => text === "off" ? 0 : parseInt(text) || 0
                    onValueModified: App.profile.keys.setTurbo(keyRow.index, value)

                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: "How many times a second the key repeats while turbo is on"
                }

                Controls.ComboBox {
                    objectName: "turboMode_" + keyRow.key
                    model: App.profile.keys.turboModes
                    currentIndex: keyRow.turboMode
                    // Turbo mode means nothing without a frequency, so it stays
                    // out of the way until one is set.
                    enabled: keyRow.isEditable && keyRow.turbo > 0
                    wheelEnabled: false
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    onActivated: App.profile.keys.setTurboMode(keyRow.index, currentIndex)
                }
            }
        }
    }
}
