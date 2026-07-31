// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Every Flydigi device attached, and which pad and dock the window is showing.
//
// This is the page that makes the selection visible. The picker in the sidebar
// is the quick way to change it; this is where you can see *why* you would --
// two pads that both call themselves Apex 5 are told apart here by their uid,
// their node and whatever they have been nicknamed.

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

Kirigami.ScrollablePage {
    id: page
    title: "Devices"

    actions: [
        Kirigami.Action {
            objectName: "rescanDevices"
            text: "Look again"
            icon.name: "view-refresh"
            onTriggered: App.devices.refresh()
        }
    ]

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Kirigami.InlineMessage {
            objectName: "mockNotice"
            Layout.fillWidth: true
            visible: App.devices.hasMock
            type: Kirigami.MessageType.Information
            text: "Some of these are not real. FLYDIGI_MOCK_BUS is set, so the "
                + "devices marked “mock” are served from inside this process — "
                + "see flydigi/mock/."
        }

        Kirigami.PlaceholderMessage {
            objectName: "noDevices"
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 3
            visible: App.devices.count === 0
            icon.name: "input-gaming"
            text: "Nothing attached"
            explanation: "A sleeping Apex 5 leaves the USB bus entirely, so "
                       + "press a button on it. A dock has to be plugged into "
                       + "the computer, not only into power."
        }

        Repeater {
            model: App.devices

            delegate: Kirigami.AbstractCard {
                id: card
                required property int index
                required property string label
                required property string kind
                required property string path
                required property string selector
                required property string iconName
                required property bool mock
                required property bool supported
                required property int battery
                required property bool charging
                required property string firmware
                required property string nickname
                required property string model
                required property string uid
                required property string error

                Layout.fillWidth: true
                showClickFeedback: true
                onClicked: App.devices.select(card.index)

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Kirigami.Icon {
                        source: card.iconName
                        implicitWidth: Kirigami.Units.iconSizes.large
                        implicitHeight: Kirigami.Units.iconSizes.large
                        opacity: card.error === "" ? 1 : 0.5
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing

                            Kirigami.Heading {
                                level: 4
                                text: card.label
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            Kirigami.Chip {
                                visible: card.mock
                                text: "mock"
                                checkable: false
                                closable: false
                            }

                            Kirigami.Chip {
                                objectName: "selectedChip"
                                visible: card.index === App.devices.currentIndex
                                text: "showing"
                                checkable: false
                                closable: false
                            }
                        }

                        Controls.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            color: Kirigami.Theme.disabledTextColor
                            elide: Text.ElideRight
                            text: {
                                const bits = [card.model === "" ? "unrecognised"
                                                                : card.model,
                                              card.path];
                                if (card.firmware !== "")
                                    bits.push("firmware " + card.firmware);
                                if (card.kind === "pad" && card.battery >= 0)
                                    bits.push(card.charging
                                        ? "charging"
                                        : "battery " + card.battery + "/"
                                          + App.device.batterySteps);
                                return bits.join(" · ");
                            }
                        }

                        Controls.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            color: Kirigami.Theme.disabledTextColor
                            elide: Text.ElideRight
                            visible: card.uid !== ""
                            // The one name that survives a reconnect. Worth
                            // showing in full: it is what goes in a config file
                            // and what `--device` takes on the command line.
                            text: "uid " + card.uid
                        }

                        Controls.Label {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            font: Kirigami.Theme.smallFont
                            color: Kirigami.Theme.negativeTextColor
                            visible: card.error !== "" || !card.supported
                            text: card.error !== ""
                                ? card.error
                                : "This app does not drive it — writes are refused."
                        }
                    }

                    Controls.Button {
                        text: card.index === App.devices.currentIndex
                              ? "Showing" : "Show"
                        enabled: card.index !== App.devices.currentIndex
                        onClicked: App.devices.select(card.index)
                    }
                }
            }
        }

        Kirigami.Separator {
            Layout.fillWidth: true
            visible: App.devices.count > 0
        }

        Controls.Label {
            Layout.fillWidth: true
            visible: App.devices.count > 0
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.disabledTextColor
            // Said here because it is the one consequence of choosing a pad
            // that happens somewhere else entirely, and a person who has just
            // picked one should not have to find that out from a log.
            text: "The pad shown here is the one the background daemon drives "
                + "for routes that hold a single controller. Its game vibration "
                + "presets go to every attached pad that supports them."
        }
    }
}
