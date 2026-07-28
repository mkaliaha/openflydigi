// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Games with adaptive-trigger support, and which route each takes.
//
// Only the pad-side route can be pushed onto the hardware from here. The others
// need a helper process running alongside the game, which this app names but
// does not start -- there is no daemon picking one automatically yet.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

Kirigami.ScrollablePage {
    id: page
    objectName: "gamesPage"
    title: "Games"

    property int selectedRow: -1

    actions: [
        Kirigami.Action {
            objectName: "updateListAction"
            text: App.fetchingGames ? "Updating…" : "Update list"
            icon.name: "download"
            enabled: !App.fetchingGames
            onTriggered: App.fetchGameList()
        }
    ]

    readonly property string selectedDetail: selectedRow < 0
                                             ? "" : App.games.detailAt(selectedRow)

    header: Controls.ToolBar {
        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing

            Kirigami.SearchField {
                objectName: "gameSearch"
                placeholderText: "Search games…"
                Layout.fillWidth: true
                onTextChanged: {
                    App.games.search = text;
                    page.selectedRow = -1;
                }
            }

            Controls.ComboBox {
                objectName: "routeFilter"
                model: App.games.routeNames
                Layout.minimumWidth: Kirigami.Units.gridUnit * 10
                onActivated: {
                    App.games.route = currentText;
                    page.selectedRow = -1;
                }
            }
        }
    }

    footer: Controls.ToolBar {
        position: Controls.ToolBar.Footer
        // Fixed height. The detail line swaps a few words for a paragraph when
        // a game is selected, and letting the footer grow with it moves the
        // button out from under the pointer mid-click.
        implicitHeight: Kirigami.Units.gridUnit * 4

        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing

            Controls.Label {
                objectName: "gameDetail"
                text: page.selectedDetail || (App.games.count + " games listed")
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                Layout.fillWidth: true
                Layout.fillHeight: true
                // Selecting a game swaps a short line for a long explanation.
                // Zero preferred width keeps that out of the layout's sizing,
                // so the button beside it does not move under the cursor.
                Layout.preferredWidth: 0
            }

            Controls.Button {
                objectName: "applyPresetButton"
                text: "Load preset onto pad"
                icon.name: "document-import"
                enabled: page.selectedRow >= 0 && App.games.canApplyAt(page.selectedRow)
                onClicked: App.applyGamePreset(page.selectedRow)
            }
        }
    }

    Kirigami.PlaceholderMessage {
        objectName: "gamesPlaceholder"
        anchors.centerIn: parent
        width: parent.width - Kirigami.Units.gridUnit * 4
        visible: App.games.count === 0
        icon.name: "applications-games"
        text: "No game list yet"
        explanation: "\"Update list\" fetches it from Flydigi's public API — "
                     + "that is the only time this app contacts them."
        helpfulAction: Kirigami.Action {
            objectName: "updateListPlaceholderAction"
            text: "Update list"
            icon.name: "download"
            enabled: !App.fetchingGames
            onTriggered: App.fetchGameList()
        }
    }

    ListView {
        id: gameList
        objectName: "gameList"
        model: App.games
        currentIndex: page.selectedRow
        visible: App.games.count > 0

        delegate: Controls.ItemDelegate {
            id: gameRow

            required property int index
            required property string name
            required property string routeLabel
            required property bool canApply

            objectName: "gameRow" + index
            width: ListView.view.width
            highlighted: ListView.isCurrentItem
            onClicked: page.selectedRow = gameRow.index

            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing

                ColumnLayout {
                    spacing: 0
                    Layout.fillWidth: true

                    Controls.Label {
                        text: gameRow.name
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }

                    Controls.Label {
                        text: gameRow.routeLabel
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }

                Kirigami.Chip {
                    visible: gameRow.canApply
                    checkable: false
                    closable: false
                    text: "pad-side"
                }

                // Says out loud what is not built yet. Every route except the
                // pad-side one needs a helper started next to the game, and
                // nothing here starts one -- there is no daemon watching for a
                // game to launch.
                Controls.Label {
                    text: "auto: not yet"
                    font: Kirigami.Theme.smallFont
                    color: Kirigami.Theme.disabledTextColor

                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: "Starting the right helper when a "
                                           + "game launches is not implemented yet"

                    HoverHandler {
                        id: autoHover
                    }
                    property bool hovered: autoHover.hovered
                }
            }
        }
    }
}
