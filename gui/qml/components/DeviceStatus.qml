// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// What is attached, how it is doing, and -- when there is more than one -- which
// of them the window is showing.
//
// **The status block is the picker.** A combo box above it would have named the
// device twice, once in the control and once in the heading under it, and the
// heading is the thing that has been there all along. So the block itself is
// the button: it shows the selected device and, with a second one attached,
// grows a chevron and opens a menu of them. One device leaves it looking and
// behaving exactly as it did before any of this existed, which is the state
// most desks are in.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

// Sits in the global drawer's header, so it has to read well in a narrow
// column rather than across a toolbar.
ColumnLayout {
    id: root
    spacing: Kirigami.Units.smallSpacing

    readonly property bool showingDock: App.devices.currentIsDock
    readonly property bool canChoose: App.devices.count > 1

    Controls.ItemDelegate {
        id: picker
        objectName: "devicePicker"
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.smallSpacing
        // Only a control when there is a choice to make. Left as a plain block
        // otherwise: a button that does nothing when pressed is worse than no
        // button, and it would invite the press on every single-pad desk.
        hoverEnabled: root.canChoose
        down: root.canChoose && (pressed || deviceMenu.visible)
        onClicked: {
            if (root.canChoose)
                deviceMenu.popup(0, height)
        }

        // Always a Rectangle, transparent when there is nothing to choose --
        // `background: condition ? aComponent : null` does not work, since a
        // Component is not an Item and the binding is typed as one.
        background: Rectangle {
            color: Kirigami.Theme.highlightColor
            opacity: !root.canChoose ? 0
                     : (deviceMenu.visible ? 0.2 : (picker.hovered ? 0.1 : 0))
            Behavior on opacity { NumberAnimation { duration: 100 } }
        }

        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                source: root.showingDock ? "battery-full-charging" : "input-gaming"
                implicitWidth: Kirigami.Units.iconSizes.medium
                implicitHeight: Kirigami.Units.iconSizes.medium
                opacity: (root.showingDock ? App.dock.present
                                           : App.device.connected) ? 1 : 0.5
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0

                Kirigami.Heading {
                    objectName: "deviceSummary"
                    level: 4
                    // The selected device's own name when there is one -- a
                    // nickname, if it has been given one -- rather than the
                    // model, since "Apex 5" twice is exactly what a picker
                    // exists to fix.
                    text: {
                        if (App.devices.currentLabel !== "")
                            return App.devices.currentLabel;
                        return App.device.connected ? "Apex 5" : "No controller";
                    }
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                // The pad reports charge in steps, not a percentage. Saying
                // "4/5" is honest about that; "80%" would not be. Five is full
                // -- see gui/models/device.py, which said eight for a long time.
                Controls.Label {
                    objectName: "batteryLabel"
                    text: {
                        if (root.showingDock)
                            return App.dock.present ? App.dock.dockedState
                                                    : "reading the dock…";
                        if (!App.device.connected)
                            return "press a button to wake it";
                        if (App.device.charging)
                            return "Charging";
                        return App.device.connectionType + " · battery "
                               + App.device.battery + "/"
                               + App.device.batterySteps;
                    }
                    font: Kirigami.Theme.smallFont
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    color: (!root.showingDock && App.device.connected
                            && !App.device.charging && App.device.battery <= 1)
                           ? Kirigami.Theme.negativeTextColor
                           : Kirigami.Theme.disabledTextColor
                }
            }

            Kirigami.Icon {
                objectName: "devicePickerChevron"
                visible: root.canChoose
                source: "arrow-down"
                implicitWidth: Kirigami.Units.iconSizes.small
                implicitHeight: Kirigami.Units.iconSizes.small
                opacity: 0.7
            }
        }

        Controls.ToolTip.visible: root.canChoose && hovered && !deviceMenu.visible
        Controls.ToolTip.text: "Choose which of the "
            + App.devices.count + " attached devices to show"

        Controls.Menu {
            id: deviceMenu
            objectName: "devicePickerMenu"

            // `Instantiator` rather than a `Repeater`: a Menu takes its items
            // through addItem/removeItem, and this is the pattern that keeps a
            // model-driven menu in step as devices come and go. A Repeater
            // parents its delegates into the menu's content item instead, which
            // renders but does not make them menu items.
            Instantiator {
                model: App.devices
                onObjectAdded: (index, object) => deviceMenu.insertItem(index, object)
                onObjectRemoved: (index, object) => deviceMenu.removeItem(object)

                delegate: Controls.MenuItem {
                    required property int index
                    required property string label
                    required property string detail
                    required property string iconName
                    required property bool mock

                    icon.name: iconName
                    text: label + (mock ? "  (mock)" : "")
                    checkable: true
                    checked: index === App.devices.currentIndex
                    onTriggered: App.devices.select(index)

                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: detail
                }
            }
        }
    }

    Controls.ItemDelegate {
        objectName: "reloadButton"
        Layout.fillWidth: true
        icon.name: "view-refresh"
        text: root.showingDock ? "Reload from dock" : "Reload from pad"
        onClicked: {
            // The bus as well as the device: pressing Reload with a pad that
            // has just been plugged in should find it, and the hotplug poll is
            // ten seconds wide.
            App.devices.refresh();
            if (root.showingDock)
                App.dock.reload();
            else
                App.reload();
        }

        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: root.showingDock
            ? "Re-read this dock's switches and lighting"
            : "Re-read the device info, the open profile and the lighting"
    }

    Kirigami.Separator {
        Layout.fillWidth: true
    }
}
