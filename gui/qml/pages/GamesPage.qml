// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Games with adaptive-trigger support, which route each takes, and whether it
// acts on its own.
//
// Only the pad-side route can be pushed onto the hardware from here; the others
// need a helper process running alongside the game. The Auto switch hands that
// decision to the daemon instead, which is installed from the Setup page and
// re-reads these toggles about a second after they change.

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
                id: searchField
                objectName: "gameSearch"
                placeholderText: "Search games…"
                Layout.fillWidth: true
                onTextChanged: {
                    App.games.search = text;
                    page.selectedRow = -1;
                }
            }

            // `wheelEnabled: false` here and on the per-row picker below.
            // `org.kde.desktop` turns the wheel on for ComboBox where Qt's own
            // default leaves it off, so a scroll with the pointer over one
            // silently changes the filter -- or, on a row, the route a game
            // launches with.
            //
            // The row picker then hands the wheel to the list under it. This
            // one has nothing to hand it to: a ScrollablePage's header sits
            // outside the flickable, so the list is not an ancestor and never
            // sees the event. Off, a wheel here scrolls nothing, which is the
            // right answer for a control that is not over the list.
            Controls.ComboBox {
                id: routeCombo
                objectName: "routeFilter"
                model: App.games.routeNames
                wheelEnabled: false
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

    // Two ways to have nothing to show, and they want different offers. No list
    // at all is fixed by fetching one; a filter matching nothing is the user's
    // own doing, and offering to re-download the list for a typo is a
    // non-sequitur that also hides the real cause.
    readonly property bool haveNoList: App.games.total === 0

    Kirigami.Action {
        id: fetchAction
        objectName: "updateListPlaceholderAction"
        text: "Update list"
        icon.name: "download"
        enabled: !App.fetchingGames
        onTriggered: App.fetchGameList()
    }

    Kirigami.Action {
        id: clearFiltersAction
        objectName: "clearFiltersAction"
        text: "Clear the filters"
        icon.name: "edit-clear-all"
        // The controls are the source of truth for what the filter is, so they
        // are cleared and left to drive the model, rather than the model being
        // reset behind a search field still showing the text that emptied it.
        onTriggered: {
            searchField.text = "";
            routeCombo.currentIndex = 0;
            App.games.route = App.games.routeNames[0];
            page.selectedRow = -1;
        }
    }

    ListView {
        id: gameList
        objectName: "gameList"
        model: App.games
        currentIndex: page.selectedRow

        // Ninety-four rows, each a handful of controls, and scrolling the page
        // is what this costs. Recycling them is safe here because the delegate
        // holds no state that outlives a row: every field it shows is a
        // required property bound to a role, `highlighted` is the attached
        // current-item flag, and there is no `Component.onCompleted` and no
        // property written from a handler. That is what `ListView.onReused`
        // would otherwise have to put back.
        reuseItems: true

        // Inside the view: ScrollablePage reparents the Flickable and hides
        // everything else, so a sibling placeholder is never drawn. See
        // ButtonsPage for the full story.
        Kirigami.PlaceholderMessage {
            objectName: "gamesPlaceholder"
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: App.games.count === 0
            icon.name: page.haveNoList ? "applications-games" : "edit-find"
            text: page.haveNoList ? "No game list yet" : "Nothing matches"
            explanation: page.haveNoList
                ? "\"Update list\" fetches it from Flydigi's public API — "
                  + "that is the only time this app contacts them."
                : "None of the " + App.games.total + " games in the list match "
                  + "the search and the route filter."
            helpfulAction: page.haveNoList ? fetchAction : clearFiltersAction
        }

        delegate: Controls.ItemDelegate {
            id: gameRow

            required property int index
            required property string name
            required property string routeLabel
            required property bool canApply
            required property bool auto
            required property var routeChoices
            required property int chosenRouteIndex
            required property bool ds5Mark
            required property bool canAuto

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

                // The row's one badge, and it appears only where the label does
                // not already carry the same fact -- a game that pairs the
                // DualSense flag with a mod or a preset. Marked rather than
                // offered as a choice: DualSense mode is one switch for the
                // whole system, on its own page.
                //
                // A "pad-side" badge used to sit here too, on precisely the
                // rows that already said "Preset — tunes the pad's
                // rumble-to-trigger bind" and whose footer button was enabled.
                Loader {
                    id: ds5Slot
                    objectName: "ds5Slot" + gameRow.index
                    active: gameRow.ds5Mark
                    visible: ds5Slot.active
                    sourceComponent: ds5Badge
                }

                // Only games that support more than one route get a choice; for
                // the rest the combo would be a control with one option, which
                // is furniture rather than information.
                //
                // **A Loader, because `visible: false` still builds it.** One
                // of the 94 entries offers a second route -- Fallout 4, with a
                // mod and a preset (`flydigi/prefs.py:53-71`) -- so the other 93
                // rows were each constructing a ComboBox, its popup and its
                // delegates for something that could not be opened. Those rows
                // are not left saying nothing: the line under the title already
                // names the route, on every row, which is why there is no
                // one-entry combo here in the first place.
                Loader {
                    id: routeSlot
                    objectName: "routeSlot" + gameRow.index
                    active: gameRow.routeChoices.length > 1
                    visible: routeSlot.active
                    sourceComponent: routePicker
                }

                // Hidden where there is no route to take. Fifteen games carry
                // only Flydigi's DualSense flag, and that is not something the
                // daemon does per game -- it is the DualSense switch, once, for
                // everything. A toggle here would be an offer nothing keeps.
                //
                // Not behind a Loader like the two above it, because most rows
                // do have a route: a Loader would build a Loader *and* a Switch
                // on the common path to save one on the rare one.
                Controls.Switch {
                    objectName: "autoSwitch" + gameRow.index
                    visible: gameRow.canAuto
                    checked: gameRow.auto
                    onToggled: App.games.setAutoAt(gameRow.index, checked)

                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: "Act on this game by itself when it "
                                           + "starts. Needs the daemon — see Setup."
                }
            }

            // The two rare controls, declared here rather than at page level so
            // they can see `gameRow`. A Component is a compilation unit, not an
            // instance: this is what the Loaders above build only where the row
            // has something to say.
            Component {
                id: ds5Badge

                Kirigami.Chip {
                    objectName: "ds5Chip" + gameRow.index
                    checkable: false
                    closable: false
                    text: "DualSense"
                }
            }

            Component {
                id: routePicker

                Controls.ComboBox {
                    objectName: "routeChoice" + gameRow.index
                    model: gameRow.routeChoices
                    currentIndex: gameRow.chosenRouteIndex
                    wheelEnabled: false
                    implicitWidth: Kirigami.Units.gridUnit * 9
                    // `activated` rather than `currentIndexChanged`: the latter
                    // also fires when the model reassigns the index after a
                    // save, which would write the value back and loop.
                    onActivated: App.games.setRouteIndexAt(gameRow.index,
                                                           currentIndex)
                }
            }
        }
    }
}
