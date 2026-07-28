// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// What the system needs for auto mode, and the switches that provide it.
//
// The daemon runs on the host rather than in here. It has to see the host's
// process table to notice a game starting, which this app -- in a distrobox
// now and a Flatpak later -- cannot promise. Installing its unit and starting
// it is unprivileged and reaches the host's own service manager, so both
// buttons below do what they say from wherever this is running.
//
// The udev rules are the exception, and the only thing here that asks for a
// password. They are offered only when a check actually fails, because asking
// for root when nothing is broken is how a checklist teaches people to click
// through it.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

Kirigami.ScrollablePage {
    id: page
    objectName: "setupPage"
    title: "Setup"

    actions: [
        Kirigami.Action {
            objectName: "recheckAction"
            text: "Check again"
            icon.name: "view-refresh"
            enabled: !App.setup.busy
            onTriggered: App.setup.refresh()
        }
    ]

    // The first reading is taken when the page is first built rather than at
    // startup: it runs subprocesses, and nothing needs the answer until
    // somebody opens this page.
    Component.onCompleted: App.setup.refresh()

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Kirigami.InlineMessage {
            objectName: "setupSummary"
            Layout.fillWidth: true
            visible: App.setup.loaded
            type: App.setup.ready ? Kirigami.MessageType.Positive
                                  : Kirigami.MessageType.Warning
            text: App.setup.ready
                  ? "Everything auto mode needs is in place."
                  : "Something below needs attention before auto mode will work."
        }

        Kirigami.FormLayout {
            Layout.fillWidth: true

            Kirigami.Separator {
                Kirigami.FormData.isSection: true
                Kirigami.FormData.label: "The daemon"
            }

            Controls.Label {
                objectName: "daemonExplanation"
                text: "Watches for a game starting and does what that game's "
                      + "Auto setting says. Runs on the host, outside this app."
                wrapMode: Text.WordWrap
                color: Kirigami.Theme.disabledTextColor
                font: Kirigami.Theme.smallFont
                Layout.maximumWidth: Kirigami.Units.gridUnit * 24
            }

            Controls.Button {
                objectName: "installUnitButton"
                Kirigami.FormData.label: "Service:"
                text: App.setup.unitInstalled ? "Reinstall" : "Install"
                icon.name: "install"
                enabled: !App.setup.busy
                onClicked: App.setup.installUnit()
            }

            Controls.Switch {
                objectName: "runningSwitch"
                Kirigami.FormData.label: "Running now:"
                checked: App.setup.running
                enabled: !App.setup.busy && App.setup.unitInstalled
                onToggled: App.setup.setRunning(checked)
            }

            Controls.Switch {
                objectName: "startAtLoginSwitch"
                Kirigami.FormData.label: "Start at login:"
                checked: App.setup.startAtLogin
                enabled: !App.setup.busy && App.setup.unitInstalled
                // Separate from "running now" on purpose: turning this on does
                // not start it, and starting it does not sign you up for every
                // login afterwards. systemd keeps the two apart and so does this.
                onToggled: App.setup.setStartAtLogin(checked)
            }

            Kirigami.Separator {
                Kirigami.FormData.isSection: true
                Kirigami.FormData.label: "Device access"
            }

            Controls.Button {
                objectName: "installRulesButton"
                Kirigami.FormData.label: "udev rules:"
                text: "Install (asks for your password)"
                icon.name: "security-high"
                enabled: !App.setup.busy
                visible: App.setup.rulesNeeded
                onClicked: App.setup.installRules()
            }

            Controls.Label {
                objectName: "rulesNotNeeded"
                Kirigami.FormData.label: "udev rules:"
                text: "Not needed — the devices are already reachable."
                color: Kirigami.Theme.disabledTextColor
                visible: !App.setup.rulesNeeded && App.setup.loaded
            }
        }

        Kirigami.Separator { Layout.fillWidth: true }

        Repeater {
            objectName: "checkList"
            model: App.setup.checks

            delegate: RowLayout {
                id: checkRow

                required property int index
                required property string checkId
                required property string label
                required property string checkState
                required property string detail

                objectName: "check_" + checkRow.checkId
                Layout.fillWidth: true
                spacing: Kirigami.Units.largeSpacing

                Kirigami.Icon {
                    source: {
                        if (checkRow.checkState === "ok") return "dialog-ok";
                        if (checkRow.checkState === "fail") return "dialog-error";
                        return "dialog-information";
                    }
                    implicitWidth: Kirigami.Units.iconSizes.small
                    implicitHeight: Kirigami.Units.iconSizes.small
                }

                ColumnLayout {
                    spacing: 0
                    Layout.fillWidth: true

                    Controls.Label {
                        text: checkRow.label
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Controls.Label {
                        text: checkRow.detail
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }

        Controls.BusyIndicator {
            objectName: "setupBusy"
            running: App.setup.busy
            visible: App.setup.busy
            Layout.alignment: Qt.AlignHCenter
        }

        Item { Layout.fillHeight: true }
    }
}
