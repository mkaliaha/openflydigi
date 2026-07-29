// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// What is attached and how it is doing, for the window header.

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

    RowLayout {
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.largeSpacing
        Layout.bottomMargin: 0
        spacing: Kirigami.Units.smallSpacing

        Kirigami.Icon {
            source: "input-gaming"
            implicitWidth: Kirigami.Units.iconSizes.medium
            implicitHeight: Kirigami.Units.iconSizes.medium
            opacity: App.device.connected ? 1 : 0.5
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0

            Kirigami.Heading {
                objectName: "deviceSummary"
                level: 4
                text: App.device.connected ? "Apex 5" : "No controller"
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            // The pad reports charge in eight steps, not a percentage. Saying
            // "4/5" is honest about that; "80%" would not be. Five is full --
            // see gui/models/device.py, which said eight for a long time.
            Controls.Label {
                objectName: "batteryLabel"
                text: {
                    if (!App.device.connected)
                        return "press a button to wake it";
                    if (App.device.charging)
                        return "Charging";
                    return App.device.connectionType + " · battery "
                           + App.device.battery + "/" + App.device.batterySteps;
                }
                font: Kirigami.Theme.smallFont
                elide: Text.ElideRight
                Layout.fillWidth: true
                color: (App.device.connected && !App.device.charging
                        && App.device.battery <= 1)
                       ? Kirigami.Theme.negativeTextColor
                       : Kirigami.Theme.disabledTextColor
            }
        }
    }

    Controls.ItemDelegate {
        objectName: "reloadButton"
        Layout.fillWidth: true
        icon.name: "view-refresh"
        text: "Reload from pad"
        onClicked: App.reload()

        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: "Re-read the device info, the open profile and the lighting"
    }

    Kirigami.Separator {
        Layout.fillWidth: true
    }
}
