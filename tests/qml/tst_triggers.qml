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
            verify(findChild(page, "strokeStart_" + side),
                   "no travel start for " + side);
            verify(findChild(page, "strokeEnd_" + side),
                   "no travel end for " + side);
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
        // Racing and mode 2 share a start position and nothing else, so the
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

        const side = App.profile.triggers.right;
        compare(side.paramValue("start"), 60, "start was lost");
        compare(side.paramValue("frequency"), 120, "frequency was lost");
    }

    function test_a_knob_follows_the_model_after_being_dragged() {
        // The other half of a working control, and the one the file's own
        // comment promises: "the model is allowed to answer a move with a
        // different number ... and the control has to follow it rather than
        // fight it". Surviving the drag is not enough if the handle then stops
        // listening -- and dragging a QQC2 Slider assigns its `value`, which is
        // exactly what breaks a declarative binding to it.
        //
        // Asserted by moving the knob from underneath: the model is written to
        // directly, with no pointer involved, and the control has to arrive at
        // the same number.
        pick("left", 2);                         // sniper
        tryVerify(() => findChild(page, "param_start_leftSlider"), 2000);
        waitForRendering(page);

        let slider = findChild(page, "param_start_leftSlider");
        const y = slider.height / 2;
        mousePress(slider, 2, y, Qt.LeftButton);
        for (let x = 4; x < slider.width - 2; x += Math.max(2, slider.width / 12))
            mouseMove(slider, x, y, 1, Qt.LeftButton);
        mouseRelease(slider, slider.width - 2, y, Qt.LeftButton);

        const side = App.profile.triggers.left;
        compare(slider.value, side.paramValue("start"),
                "the handle and the model disagree right after a drag");

        // Now move it from the model's side. Nothing touches the pointer.
        side.setEffectParam("start", 12);
        tryCompare(App.profile.triggers.left, "effect", 2);
        compare(side.paramValue("start"), 12, "the model did not take the write");
        tryCompare(slider, "value", 12, 2000,
                   "the knob stopped following the model after it was dragged");
    }

    function test_a_knob_survives_being_dragged() {
        // The case the two above cannot make. They call `moved()` on the
        // control, which reports a move that never involved the pointer -- so
        // they passed throughout while these knobs could not be dragged at
        // all. `effectParams` was a list rebuilt on every read and notified by
        // the signal a knob move emits, so the first move replaced the
        // Repeater's model, destroyed the delegate under the cursor, and took
        // the mouse grab with it. One `moved` per drag instead of one per step.
        //
        // So this drags: press, several moves, release, and counts what the
        // control reported. The threshold is deliberately far below the number
        // of steps sent -- what is being asserted is that the control survived
        // its own first update, not how Qt filters a drag into events.
        // The left trigger, because the right one's rows sit below the fold in
        // this window and a synthetic press outside it lands nowhere.
        pick("left", 2);                         // sniper
        tryVerify(() => findChild(page, "param_start_leftSlider"), 2000);

        let slider = findChild(page, "param_start_leftSlider");
        verify(slider, "the start knob has no slider");
        // `tryVerify` returns when the row *exists*, which is a frame before it
        // has been laid out -- long enough that the press landed at the row's
        // old size and position and reached nothing at all.
        waitForRendering(page);

        let moves = 0;
        slider.moved.connect(() => { moves += 1; });

        // The button has to be named on every move: QtTest's `mouseMove`
        // defaults to no buttons held, which a Slider reads as a hover and not
        // as a drag.
        const y = slider.height / 2;
        mousePress(slider, 2, y, Qt.LeftButton);
        for (let x = 4; x < slider.width - 2; x += Math.max(2, slider.width / 12))
            mouseMove(slider, x, y, 1, Qt.LeftButton);
        mouseMove(slider, slider.width - 2, y, 1, Qt.LeftButton);
        mouseRelease(slider, slider.width - 2, y, Qt.LeftButton);

        verify(moves > 1,
               "the knob reported " + moves + " moves across a drag, so the "
               + "delegate did not survive its own first update");
        verify(App.profile.triggers.left.paramValue("start") > 0,
               "the drag wrote nothing through to the profile");
    }

    function test_a_switch_writes_through_too() {
        pick("right", 3);                        // recoil
        tryVerify(() => findChild(page, "param_match_input_right"), 2000);

        let match = findChild(page, "param_match_input_right");
        let before = match.checked;
        match.checked = !before;
        match.toggled();

        compare(App.profile.triggers.right.paramValue("match_input"),
                before ? 0 : 1, "the switch did not write through");
    }

    function test_the_stroke_window_writes_through() {
        findChild(page, "strokeStart_left").moved(15);
        findChild(page, "strokeEnd_left").moved(200);
        compare(App.profile.triggers.left.strokeStart, 15);
        compare(App.profile.triggers.left.strokeEnd, 200);
        verify(App.profile.dirty, "editing a trigger is a change");
    }

    function test_the_stroke_window_cannot_be_dragged_inside_out() {
        // The backend swaps an inverted pair rather than storing it, so the
        // slider has to read back from the model. Dragging the start past the
        // end is reachable -- Space Station's own range slider passes neither
        // pushable nor allowCross -- so it has to mean something sane.
        findChild(page, "strokeEnd_right").moved(40);
        findChild(page, "strokeStart_right").moved(200);
        const side = App.profile.triggers.right;
        compare(side.strokeStart, 40, "start should have taken the lower value");
        compare(side.strokeEnd, 200, "end should have taken the higher value");
    }

    function test_no_trigger_motor_controls_are_drawn() {
        // The Apex 5 has no trigger vibration motors -- IsSupportTriggerVibration
        // is a Vader flag -- so the block at offset 154 is not editable here.
        // There was a switch over it for months.
        for (const name of ["triggerMotor", "motorStrength", "motorThreshold",
                            "amplitudeMin_left", "amplitudeMax_right"])
            verify(!findChild(page, name), name + " should not be on this page");
    }

    function test_an_edit_can_be_applied_to_the_pad() {
        findChild(page, "strokeStart_right").moved(20);
        let apply = findChild(page, "applyButton");
        verify(apply.enabled, "an edit should enable apply");

        Pad.resetCounters();
        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        verify(Pad.packetsReceived > 0, "nothing reached the pad");
        compare(Pad.badChecksums, 0, "the pad rejected a packet");
    }

    function test_applying_engages_the_effect_and_does_not_merely_store_it() {
        // The bug this guards: Apply wrote the effect into the profile blob and
        // sent nothing live, so picking Trigger lock stored a lock and left the
        // triggers loose. Asserted against what the pad is *running*, since the
        // blob looked correct throughout.
        pick("left", 4);                         // trigger lock
        tryCompare(App.profile.triggers.left, "effect", 4, 2000);

        Pad.resetCounters();
        mouseClick(findChild(page, "applyButton"));
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");

        // Side ids, not indices: 1 is left and 2 is right. Both are asserted
        // because Flydigi issue one command per trigger -- a single command
        // addressed to `Both` acks and does nothing. Waiting on the right one,
        // which goes out second: waiting on the left passes the moment the
        // first of the pair lands and says nothing about the second.
        tryVerify(() => Pad.liveEffects["2"] !== undefined, 3000,
                  "the right trigger was never sent an effect");
        verify(Pad.liveEffects["1"] !== undefined,
               "nothing was engaged on the left trigger");
        compare(Pad.liveEffects["1"][0], 4, "the left trigger is not locked");
    }
}
