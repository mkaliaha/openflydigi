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

    Kirigami.PlaceholderMessage {
        objectName: "buttonsPlaceholder"
        anchors.centerIn: parent
        width: parent.width - Kirigami.Units.gridUnit * 4
        visible: !App.profile.loaded
        icon.name: "input-gaming"
        text: "No profile open"
        explanation: "Pick a profile on the Controller page. Reading one makes "
                     + "the pad re-seat its trigger motors, so it only happens "
                     + "when you ask."
    }

    ListView {
        id: keyList
        objectName: "keyList"
        model: App.profile.keys
        visible: App.profile.loaded
        currentIndex: -1
        reuseItems: true

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

                    Controls.Label {
                        text: "not editable here"
                        visible: !keyRow.isEditable
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                    }
                }

                Controls.ComboBox {
                    objectName: "target_" + keyRow.key
                    model: App.profile.keys.targets
                    currentIndex: keyRow.targetIndex
                    enabled: keyRow.isEditable
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 8
                    onActivated: App.profile.keys.setTarget(keyRow.index, currentIndex)

                    Controls.ToolTip.visible: hovered && !keyRow.isEditable
                    Controls.ToolTip.text: "This key runs a macro or sends a "
                                           + "keystroke, which this app does not edit yet."
                }

                Controls.SpinBox {
                    objectName: "turbo_" + keyRow.key
                    from: 0
                    to: App.profile.keys.turboMax
                    value: keyRow.turbo
                    enabled: keyRow.isEditable
                    editable: true
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
                    Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    onActivated: App.profile.keys.setTurboMode(keyRow.index, currentIndex)
                }
            }
        }
    }
}
