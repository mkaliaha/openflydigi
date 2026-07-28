// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Adaptive-trigger settings held in the profile, so they apply with no game
// integration and nothing running alongside.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Triggers"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        TriggersPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.reload();
        tryVerify(() => Fixture.profileReads >= 1, 5000, "no profile read arrived");
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the triggers page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        let seen = Fixture.profileReads;
        wait(150);
        tryVerify(() => Fixture.profileReads === seen, 2000,
                  "a read was still arriving between cases");
    }

    function pick(side, index) {
        let combo = findChild(page, "effect_" + side);
        combo.currentIndex = index;
        combo.activated(index);
    }

    function test_each_trigger_has_its_own_controls() {
        for (const side of ["left", "right"]) {
            verify(findChild(page, "effect_" + side), "no effect picker for " + side);
            verify(findChild(page, "deadZone_" + side), "no dead zone for " + side);
            pick(side, 1);                       // racing
            tryVerify(() => findChild(page, "param_start_" + side), 2000,
                      "no start control for " + side);
            verify(findChild(page, "param_resistance_" + side),
                   "no resistance for " + side);
        }
    }

    function test_all_six_effects_are_offered() {
        compare(App.profile.triggers.effectNames.length, 6,
                String(App.profile.triggers.effectNames));
    }

    function test_the_controls_follow_the_chosen_effect() {
        // Racing and Sniper share a start position and nothing else, so the
        // rows have to be replaced rather than enabled and disabled.
        compare(App.profile.triggers.right.effect, 0, "should start on General");
        verify(!findChild(page, "param_start_right"),
               "General has no start position to show");

        pick("right", 1);                        // racing
        tryVerify(() => findChild(page, "param_resistance_right"), 2000,
                  "racing brought no resistance control");
        verify(!findChild(page, "param_frequency_right"),
               "racing has no frequency");

        pick("right", 2);                        // sniper
        tryVerify(() => findChild(page, "param_frequency_right"), 2000,
                  "sniper brought no frequency control");
        verify(!findChild(page, "param_resistance_right"),
               "sniper has no resistance");
        verify(findChild(page, "param_match_input_right"),
               "sniper's match-input switch is missing");
    }

    function test_a_knob_writes_through_to_the_profile() {
        pick("right", 2);                        // sniper
        tryVerify(() => findChild(page, "param_frequency_right"), 2000);

        findChild(page, "param_start_right").moved(60);
        findChild(page, "param_frequency_right").moved(120);

        let values = {};
        for (const row of App.profile.triggers.right.effectParams)
            values[row.key] = row.value;
        compare(values.start, 60, "start was lost");
        compare(values.frequency, 120, "frequency was lost");
    }

    function test_a_switch_writes_through_too() {
        pick("right", 3);                        // recoil
        tryVerify(() => findChild(page, "param_match_input_right"), 2000);

        let match = findChild(page, "param_match_input_right");
        let before = match.checked;
        match.checked = !before;
        match.toggled();

        let values = {};
        for (const row of App.profile.triggers.right.effectParams)
            values[row.key] = row.value;
        compare(values.match_input, before ? 0 : 1, "the switch did not write through");
    }

    function test_dead_zone_writes_through() {
        findChild(page, "deadZone_left").moved(15);
        compare(App.profile.triggers.left.deadZone, 15);
        verify(App.profile.dirty, "editing a trigger is a change");
    }

    function test_the_motors_share_one_switch() {
        // The pad has a single byte for this -- see MappingConfig.trigger_motor.
        // Two switches over one byte would let someone ask for left-on/
        // right-off, watch the UI agree, and get both.
        for (const side of ["left", "right"])
            verify(!findChild(page, "motor_" + side),
                   "there should be no per-side motor switch for " + side);

        let motor = findChild(page, "triggerMotor");
        verify(motor, "no trigger motor switch at all");
        verify(!App.profile.triggers.motorEnabled, "should start off");

        motor.checked = true;
        motor.toggled();
        verify(App.profile.triggers.motorEnabled,
               "the motor switch did not write through");
        verify(App.profile.dirty, "toggling the motors is a change");
    }

    function test_an_edit_can_be_applied_to_the_pad() {
        findChild(page, "deadZone_right").moved(20);
        let apply = findChild(page, "applyButton");
        verify(apply.enabled, "an edit should enable apply");

        Pad.resetCounters();
        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        verify(Pad.packetsReceived > 0, "nothing reached the pad");
        compare(Pad.badChecksums, 0, "the pad rejected a packet");
    }
}
