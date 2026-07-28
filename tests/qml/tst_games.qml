// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The games page: searching, filtering by route, and the one route that can
// actually be pushed onto the pad.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Games"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    readonly property var sampleGames: [
        {"enGameName": "Forza Horizon 6", "modDownLoadUrl": "x",
         "modName": "ForzaDualSense.exe", "processGameNames": ["forza.exe"]},
        {"enGameName": "Deathloop", "isPS5": true},
        {"enGameName": "Silksong", "isVibration": true}
    ]

    Component {
        id: pageComponent
        GamesPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        Fixture.seedGames(sampleGames);
        // The filter lives on the shared model, so a case that narrows it
        // would otherwise hand its leftovers to the next one.
        App.games.search = "";
        App.games.route = App.games.routeNames[0];
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the games page did not load");
        waitForRendering(page);
    }

    function test_every_game_is_listed() {
        compare(App.games.count, 3);
        let list = findChild(page, "gameList");
        verify(list, "no game list");
        compare(list.count, 3);
    }

    function test_typing_filters_the_list() {
        let search = findChild(page, "gameSearch");
        verify(search, "no search field");
        search.text = "death";
        tryVerify(() => App.games.count === 1, 2000,
                  "search left " + App.games.count + " games");
        compare(App.games.nameAt(0), "Deathloop");

        search.text = "";
        tryVerify(() => App.games.count === 3, 2000, "clearing the search did not restore the list");
    }

    function test_the_route_filter_narrows_to_one_route() {
        let filter = findChild(page, "routeFilter");
        verify(filter, "no route filter");
        let wanted = App.games.routeNames.indexOf("vibration");
        verify(wanted > 0, "no vibration route in the filter");
        filter.currentIndex = wanted;
        filter.activated(wanted);
        tryVerify(() => App.games.count === 1, 2000,
                  "route filter left " + App.games.count + " games");
        compare(App.games.nameAt(0), "Silksong");
    }

    function test_only_the_pad_side_route_can_be_applied() {
        verify(App.games.canApplyAt(App.games.count - 1) !== undefined);
        for (let row = 0; row < App.games.count; ++row) {
            let expected = App.games.nameAt(row) === "Silksong";
            compare(App.games.canApplyAt(row), expected,
                    App.games.nameAt(row) + " reported the wrong route");
        }
    }

    function test_the_preset_button_waits_for_a_pad_side_game() {
        let apply = findChild(page, "applyPresetButton");
        verify(apply, "no preset button");
        verify(!apply.enabled, "nothing is selected yet");

        page.selectedRow = App.games.count - 1;      // Silksong
        tryVerify(() => apply.enabled, 2000, "a pad-side game should offer the preset");

        page.selectedRow = 1;                        // Deathloop, a helper route
        tryVerify(() => !apply.enabled, 2000,
                  "a helper-route game cannot be pushed onto the pad");
    }

    function test_loading_a_preset_reaches_the_pad() {
        let apply = findChild(page, "applyPresetButton");
        page.selectedRow = App.games.count - 1;      // Silksong
        tryVerify(() => apply.enabled, 2000);

        Pad.resetCounters();
        mouseClick(apply);
        // The binding is written straight to the pad, so the only thing to
        // wait for is the pad having been spoken to without complaint.
        wait(400);
        compare(Pad.badChecksums, 0, "the pad rejected a packet");
    }

    function test_the_wording_does_not_oversell_the_preset() {
        // The vibration route is one bind tuned per game, not a per-game
        // integration, and the page must not imply otherwise.
        let filter = findChild(page, "routeFilter");
        let wanted = App.games.routeNames.indexOf("vibration");
        filter.currentIndex = wanted;
        filter.activated(wanted);
        tryVerify(() => App.games.count === 1, 2000);

        let detail = App.games.detailAt(0);
        verify(detail.indexOf("preset") >= 0, "should call it a preset: " + detail);
        verify(detail.indexOf("nothing runs") >= 0,
               "should say nothing runs alongside: " + detail);
    }
}
