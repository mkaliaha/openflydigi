// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Grip motor limits: the controls write through to the profile blob, and the
// window the pad clamps a game's rumble into stays the right way round.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Vibration"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        VibrationPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.reload();
        tryVerify(() => Fixture.profileReads >= 1, 5000, "no profile read arrived");
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the vibration page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        let seen = Fixture.profileReads;
        wait(150);
        tryVerify(() => Fixture.profileReads === seen, 2000,
                  "a read was still arriving between cases");
    }

    function test_the_master_switch_writes_through() {
        let master = findChild(page, "vibrationMaster");
        verify(master, "no master rumble switch");
        let before = App.profile.vibration.enabled;
        master.toggled();
        // The delegate drives the model from its own checked state, so set it
        // the way a click would and then tell it.
        master.checked = !before;
        master.toggled();
        compare(App.profile.vibration.enabled, !before,
                "the master switch did not reach the config");
        verify(App.profile.dirty, "toggling rumble is a change");
    }

    function test_each_grip_has_its_own_controls() {
        for (const side of ["left", "right"]) {
            verify(findChild(page, "enabled_" + side), "no enable switch for " + side);
            verify(findChild(page, "minimum_" + side), "no minimum for " + side);
            verify(findChild(page, "maximum_" + side), "no maximum for " + side);
            verify(findChild(page, "scale_" + side), "no strength for " + side);
        }
    }

    function test_a_slider_writes_through_to_the_config() {
        let row = findChild(page, "scale_left");
        row.moved(128);
        compare(App.profile.vibration.left.scale, 128,
                "the strength did not reach the config");
        verify(App.profile.dirty, "moving a slider is a change");
    }

    function test_the_window_cannot_be_inverted() {
        // The backend swaps min and max rather than storing a backwards
        // window, and the controls have to follow it rather than fight it.
        findChild(page, "minimum_left").moved(200);
        findChild(page, "maximum_left").moved(50);
        verify(App.profile.vibration.left.minimum <= App.profile.vibration.left.maximum,
               "min " + App.profile.vibration.left.minimum
               + " ended up above max " + App.profile.vibration.left.maximum);
    }

    function test_an_edit_can_be_applied_to_the_pad() {
        findChild(page, "scale_right").moved(64);
        let apply = findChild(page, "applyButton");
        verify(apply.enabled, "an edit should enable apply");

        Pad.resetCounters();
        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        verify(Pad.packetsReceived > 0, "nothing reached the pad");
        compare(Pad.badChecksums, 0, "the pad rejected a packet");
    }
}
