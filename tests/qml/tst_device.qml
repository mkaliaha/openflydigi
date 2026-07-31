// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The device-settings page. Nothing here is part of a profile, so there is no
// apply/save footer -- a control writes as it is touched.
//
// Every assertion about a write goes through `Pad`, not through the model: the
// model shows a switch as moved the moment it is clicked, so asserting against
// it would pass whether or not anything reached the pad.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Device"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        DeviceSettingsPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the device page did not load");
        // The page asks for the block itself when it is built. Waiting on
        // `loaded` would not do: it stays true from the previous case, so the
        // wait would return before this case's read had landed.
        tryVerify(() => Fixture.settingsReads >= 1, 5000,
                  "the settings block never arrived");
        waitForRendering(page);
    }

    function cleanup() {
        // A read still in flight when a case ends would land during the next
        // one and overwrite what it had just written.
        let seen = Fixture.settingsReads;
        wait(150);
        tryVerify(() => Fixture.settingsReads === seen, 2000,
                  "a read was still arriving between cases");
    }

    function test_the_page_shows_what_the_pad_reported() {
        verify(App.settings.loaded, "the block should have arrived");
        verify(!findChild(page, "settingsUnread").visible,
               "the waiting message should be gone once the block is in");
        compare(App.settings.quickSwitch, true);
        compare(App.settings.sleepMinutes, 15);
        // Precision 2 is 10-bit, and 10-bit is second by resolution as well --
        // which is a coincidence of this one value, not the rule. See the
        // declaration-order trap in flydigi/settings.py.
        compare(App.settings.precisionNames[App.settings.precision], "10-bit");
        compare(App.settings.sensitivityNames[App.settings.sensitivity], "Middle");
    }

    function test_a_switch_reaches_the_pad() {
        let toggle = findChild(page, "quickSwitchToggle");
        verify(toggle, "no quick-switch toggle");
        verify(toggle.checked, "the fake pad ships with it on");
        let seen = Fixture.settingsReads;
        toggle.toggle();
        toggle.toggled();
        tryVerify(() => Fixture.settingsReads > seen, 5000, "the write was never answered");
        compare(Pad.settings["quick_switch"], false, "the pad still has it on");
        compare(App.settings.quickSwitch, false);
    }

    function test_the_sleep_time_is_written_as_minutes() {
        let spin = findChild(page, "sleepMinutes");
        verify(spin, "no sleep control");
        let seen = Fixture.settingsReads;
        spin.value = 45;
        tryVerify(() => Fixture.settingsReads > seen, 5000, "the write was never answered");
        compare(Pad.sleepMinutes, 45, "the pad kept the old sleep time");
        compare(App.settings.sleepText, "45 min");
    }

    function test_zero_minutes_reads_as_never() {
        let spin = findChild(page, "sleepMinutes");
        let seen = Fixture.settingsReads;
        spin.value = 0;
        tryVerify(() => Fixture.settingsReads > seen, 5000, "the write was never answered");
        compare(Pad.sleepMinutes, 0);
        compare(findChild(page, "sleepExplanation").text,
                "The pad never sleeps on its own");
    }

    function test_precision_writes_the_wire_value_not_the_row() {
        // The picker is ordered by resolution and the wire is in the order
        // Flydigi declared the enum, so the two disagree from 9-bit onwards.
        // Picking 12-bit must send 3, which is row 4.
        let combo = findChild(page, "precisionCombo");
        verify(combo, "no precision picker");
        let row = App.settings.precisionNames.indexOf("12-bit");
        compare(row, 4, "12-bit should sort fifth by resolution");
        let seen = Fixture.settingsReads;
        combo.currentIndex = row;
        combo.activated(row);
        tryVerify(() => Fixture.settingsReads > seen, 5000, "the write was never answered");
        compare(Pad.precision, 3, "12-bit is wire value 3, not row 4");
    }

    function test_sensitivity_writes_its_own_wire_value() {
        let combo = findChild(page, "sensitivityCombo");
        let seen = Fixture.settingsReads;
        combo.currentIndex = 0;
        combo.activated(0);
        tryVerify(() => Fixture.settingsReads > seen, 5000, "the write was never answered");
        compare(Pad.sensitivity, 14, "the most sensitive setting is 14, not 0");
    }

    function test_auto_calibration_follows_stick_debounce() {
        // Flydigi's own wording for the debounce toggle says turning it off
        // disables auto-calibration, so the row is greyed rather than left
        // looking live.
        let debounce = findChild(page, "stickDebounceToggle");
        let auto = findChild(page, "autoCalibrationToggle");
        verify(auto.enabled, "auto-calibration should be reachable to start with");
        let seen = Fixture.settingsReads;
        debounce.toggle();
        debounce.toggled();
        tryVerify(() => Fixture.settingsReads > seen, 5000, "the write was never answered");
        compare(Pad.settings["stick_debounce"], false);
        verify(!auto.enabled, "auto-calibration should be greyed with debounce off");
    }

    function test_the_unsupported_settings_have_no_controls() {
        // Motion debounce and audio come back unsupported on this pad, and a
        // switch the firmware ignores is worse than no switch.
        verify(!findChild(page, "motionDebounceToggle"), "motion debounce has no row");
        verify(!findChild(page, "audioToggle"), "audio has no row");
    }

    function test_the_report_rate_is_reported_and_not_offered() {
        let row = findChild(page, "reportRateRow");
        verify(row, "the polling rate should still be shown");
        verify(row.description.indexOf("default (0)") >= 0,
               "it should say what the pad answered: " + row.description);
    }
}
