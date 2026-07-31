// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The gyro page, through to the eight bytes the pad stores.
//
// Two things here are worth a case of their own rather than a model assertion.
// The first is that turning the mapping on also writes the motion mode, which
// no control on the page touches. The second is the factory's leftover enable
// keys: a fresh profile carries Left trigger and D-pad Up in those two bytes,
// so a page that showed nothing until you picked something would hide two
// buttons that are already bound.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Gyro"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        GyroPage {
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
        verify(page, "the gyro page did not load");
        waitForRendering(page);
    }

    function test_a_fresh_profile_has_the_gyro_off() {
        let picker = findChild(page, "gyroTarget");
        verify(picker, "no target picker");
        compare(picker.currentIndex, 0, "a factory profile should be off");
        verify(!App.profile.motion.enabled, "nothing should be mapped");

        // The controls are only there once something is mapped, and so is the
        // warning that goes with it.
        verify(!findChild(page, "gyroSensitivity").visible,
               "sensitivity should be hidden while the gyro is off");
        verify(!findChild(page, "gyroPollingWarning").visible,
               "no polling warning is due while the gyro is off");
    }

    function test_turning_it_on_says_what_it_costs() {
        let picker = findChild(page, "gyroTarget");
        picker.currentIndex = 2;
        picker.activated(2);
        tryVerify(() => App.profile.motion.enabled, 2000, "the target did not take");

        let warning = findChild(page, "gyroPollingWarning");
        tryVerify(() => warning.visible, 2000, "the polling warning never appeared");
        verify(warning.description.indexOf("less") >= 0,
               "the cost should be stated: " + warning.description);
    }

    function test_the_factory_enable_keys_are_shown_not_hidden() {
        let picker = findChild(page, "gyroTarget");
        picker.currentIndex = 2;
        picker.activated(2);
        tryVerify(() => App.profile.motion.enabled, 2000, "the target did not take");

        // Lt and Up, straight off the hardware. Neither was chosen by anyone,
        // and both are live the moment the mapping is.
        let key = findChild(page, "gyroKey");
        compare(key.currentText, "Left trigger",
                "the factory's first enable key should be visible");
        verify(!findChild(page, "gyroNoKey").visible,
               "something is bound, so the page should not say nothing is");

        // A factory profile is on Click, where the format writes no change to
        // byte 7 -- so the control is absent and the live key is named instead.
        let mode = findChild(page, "gyroEnableType");
        compare(mode.currentIndex, 0, "the factory profile should be on toggle");
        verify(!findChild(page, "gyroSecondKey").visible,
               "no second-key control is due in toggle mode");
        let stranded = findChild(page, "gyroStrandedKey");
        verify(stranded.visible, "the live second key should still be named");
        verify(stranded.text.indexOf("Up") >= 0,
               "and named as what it is: " + stranded.text);

        // Switching to hold is what makes it editable, in the app and in theirs.
        mode.currentIndex = 1;
        mode.activated(1);
        tryVerify(() => findChild(page, "gyroSecondKey").visible, 2000,
                  "the control should appear under While held");
        verify(!findChild(page, "gyroStrandedKey").visible,
               "and nothing is stranded once it can be edited");
    }

    function test_the_mode_follows_the_stick_and_reaches_the_pad() {
        let picker = findChild(page, "gyroTarget");
        picker.currentIndex = 1;              // left stick -- racing
        picker.activated(1);
        tryCompare(App.profile.motion, "useMode", "Racing", 2000,
                   "the left stick should have picked Racing");

        picker.currentIndex = 2;              // right stick -- aiming
        picker.activated(2);
        tryCompare(App.profile.motion, "useMode", "FPS", 2000,
                   "the right stick should have picked FPS");

        let key = findChild(page, "gyroKey");
        key.currentIndex = App.profile.motion.keyNames.indexOf("M1");
        key.activated(key.currentIndex);
        findChild(page, "gyroSensitivitySlider").value = 70;
        findChild(page, "gyroSensitivitySlider").moved();
        verify(App.profile.dirty, "editing the gyro is a change");

        Pad.resetCounters();
        mouseClick(findChild(page, "applyButton"));
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");

        let stored = Pad.motionOf(0);
        compare(stored.target, 2, "the pad did not get the right stick");
        compare(stored.useMode, 0, "the mode should have travelled with it");
        compare(stored.key, "m1", "the enable key did not reach the pad");
        compare(stored.sensitivity, 70, "the sensitivity did not reach the pad");
    }

    function test_clearing_the_factorys_leftover_key_reaches_the_pad() {
        let picker = findChild(page, "gyroTarget");
        picker.currentIndex = 2;
        picker.activated(2);

        // Only reachable under While held -- see the enable-type test above.
        let mode = findChild(page, "gyroEnableType");
        mode.currentIndex = 1;
        mode.activated(1);
        tryVerify(() => App.profile.motion.holdMode, 2000, "the mode did not take");

        let second = findChild(page, "gyroSecondKey");
        second.currentIndex = 0;              // (none)
        second.activated(0);
        tryCompare(App.profile.motion, "secondKey", 0, 2000,
                   "the second key was not cleared");

        Pad.resetCounters();
        mouseClick(findChild(page, "applyButton"));
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        compare(Pad.motionOf(0).key2, "",
                "the factory's D-pad Up should be gone from the pad");
    }
}
