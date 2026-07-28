// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The sticks page, all the way through to the bytes the pad will play.
//
// The assertion that matters here is not that a slider moved -- it is that the
// nine-point bank in the blob moved with it. The pad has no curve evaluator, so
// a page that wrote only the fields it edits would pass every "the model
// changed" test and do nothing whatsoever to the stick.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Sticks"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        SticksPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.reload();
        tryVerify(() => Fixture.profileReads >= 1, 5000, "no profile read arrived");
        verify(App.profile.loaded, "no profile was opened");
        verify(!App.profile.dirty, "started dirty");
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the sticks page did not load");
        waitForRendering(page);
    }

    function test_a_fresh_profile_is_on_the_default_curve() {
        let picker = findChild(page, "curveType_left");
        verify(picker, "no curve picker for the left stick");
        compare(picker.currentIndex, 0, "a factory profile should be on Default");
        compare(App.profile.sticks.left.center, 0);
        compare(App.profile.sticks.left.edge, 0);
        verify(!App.profile.sticks.left.circular, "factory is rectangular");
    }

    function test_the_plot_follows_the_compiled_curve() {
        let plot = findChild(page, "curvePlot_left");
        verify(plot, "no curve plot");
        compare(plot.bank.length, 9, "the bank is nine points");
        // The straight line the pad ships with.
        compare(plot.bank[0], 50);
        compare(plot.bank[8], 150);

        App.profile.sticks.left.center = 30;
        tryVerify(() => plot.bank[1] !== 62, 2000,
                  "the plot did not follow the edit: " + plot.bank);
        compare(plot.bank[8], 150, "full deflection should still reach full output");
    }

    function test_a_dead_zone_reaches_the_bytes_the_pad_plays() {
        let slider = findChild(page, "center_leftSlider");
        verify(slider, "no dead zone slider");
        slider.value = 25;
        slider.moved();
        compare(App.profile.sticks.left.center, 25);
        verify(App.profile.dirty, "editing a stick is a change");

        Pad.resetCounters();
        mouseClick(findChild(page, "applyButton"));
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");

        // The whole point: the bank in the pad's own blob, not the polyline.
        let bank = Pad.bankOf(0, "left");
        compare(bank.length, 9, "the pad did not get a bank");
        verify(bank[0] < 50, "a dead zone should pull the start of the curve down: "
                             + bank);
        compare(bank[8], 150, "the curve should still reach full output");
        compare(Pad.centerOf(0, "left"), 25, "the source form should be stored too");
    }

    function test_choosing_a_preset_rewrites_the_whole_curve() {
        App.profile.sticks.left.center = 40;
        let picker = findChild(page, "curveType_left");
        tryCompare(picker, "currentIndex", 3, 2000,
                   "editing a node should move the curve to Custom");

        picker.currentIndex = 0;
        picker.activated(0);
        tryCompare(App.profile.sticks.left, "center", 0, 2000,
                   "picking Default should clear the dead zone");
        compare(picker.currentIndex, 0, "and it should stay on Default");
    }

    function test_circularity_writes_through_and_says_what_it_costs() {
        let toggle = findChild(page, "circular_right");
        verify(toggle, "no circularity switch for the right stick");
        // It is the one field here the firmware applies itself, and it breaks
        // games that test axes separately -- so the page has to say so.
        verify(toggle.description.indexOf("diagonal") >= 0,
               "the cost should be stated: " + toggle.description);

        toggle.toggle();
        toggle.toggled();
        verify(App.profile.sticks.right.circular, "the switch did not write through");

        Pad.resetCounters();
        mouseClick(findChild(page, "applyButton"));
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        compare(Pad.circularOf(0, "right"), true, "the pad did not get it");
        compare(Pad.circularOf(0, "left"), false, "the other stick should be untouched");
    }
}
