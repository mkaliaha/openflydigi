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

    function test_each_trigger_has_its_own_controls() {
        for (const side of ["left", "right"]) {
            verify(findChild(page, "effect_" + side), "no effect picker for " + side);
            verify(findChild(page, "start_" + side), "no start control for " + side);
            verify(findChild(page, "strength_" + side), "no resistance for " + side);
            verify(findChild(page, "deadZone_" + side), "no dead zone for " + side);
        }
    }

    function test_only_the_two_confirmed_effects_are_offered() {
        // The rest of the effect range is unverified on hardware and is left
        // out rather than guessed at in a UI.
        compare(App.profile.triggers.effectNames.length, 2,
                String(App.profile.triggers.effectNames));
    }

    function test_resistance_controls_wait_for_an_effect() {
        let side = App.profile.triggers.right;
        compare(side.effect, 0, "should start on 'off -- normal travel'");
        verify(!findChild(page, "start_right").enabled,
               "where resistance begins means nothing with no resistance");

        let combo = findChild(page, "effect_right");
        combo.currentIndex = 1;
        combo.activated(1);
        tryVerify(() => findChild(page, "start_right").enabled, 2000,
                  "choosing an effect should enable its parameters");
    }

    function test_start_and_strength_are_independent() {
        let combo = findChild(page, "effect_right");
        combo.currentIndex = 1;
        combo.activated(1);

        findChild(page, "start_right").moved(60);
        findChild(page, "strength_right").moved(200);
        compare(App.profile.triggers.right.start, 60, "start was lost");
        compare(App.profile.triggers.right.strength, 200, "strength was lost");
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
