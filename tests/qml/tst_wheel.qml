// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// A scroll over a control moves the page, and edits nothing.
//
// `org.kde.desktop` sets `wheelEnabled: true` on ComboBox and SpinBox, which is
// neither Qt's default nor what the Basic style does, and gui/main.py asks for
// that style. Left alone, a scroll with the pointer over one of these controls
// is swallowed by it and changes its value -- and since the value is bound to
// the model and `activated` writes back, the profile changes with it. On the
// Buttons page that silently rewrites a key mapping, which is a data-integrity
// bug rather than a scrolling annoyance. So both halves are asserted: the model
// did not move, and the page did.
//
// Real wheel events rather than a call to `increase()`, because the whole
// defect lives in event delivery: the control accepting the event is both what
// edits the value and what stops the Flickable ever seeing it.
//
// **Each case scrolls in a direction the control could act on.** A combo box
// already on its last entry, or a turbo box already at zero, clamps and changes
// nothing whichever style is in use, so a case built on one of those would pass
// against the defect. The key matters for the same reason: every key starts on
// `DEFAULT_TARGET` and one notch takes it to the first XInput target, which for
// A *is* A -- so the A row is the one place this remap is not a remap.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Wheel"
    when: windowShown
    width: 900
    height: 700
    visible: true

    Component {
        id: buttonsComponent
        ButtonsPage {
            anchors.fill: parent
        }
    }

    // Sized rather than filled, unlike the other page tests here: a form page
    // that fits in the window has nothing to scroll, and "the page moved" is
    // half of what these cases are for.
    Component {
        id: triggersComponent
        TriggersPage {
            width: 600
            height: 220
        }
    }

    Component {
        id: gyroComponent
        GyroPage {
            width: 600
            height: 220
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.reload();
        tryVerify(() => Fixture.profileReads >= 1, 5000, "no profile read arrived");
        verify(App.profile.loaded, "no profile was opened");
        verify(!App.profile.dirty, "started dirty");
    }

    // One notch, delivered where a pointer resting on the control would be.
    function scrollOver(control, flickable, delta) {
        let where = control.mapToItem(flickable, control.width / 2,
                                      control.height / 2);
        verify(where.y > 0 && where.y < flickable.height,
               "the control is not on screen to be scrolled over: y=" + where.y);
        mouseWheel(flickable, where.x, where.y, 0, delta);
    }

    function buttonsPage() {
        let page = createTemporaryObject(buttonsComponent, suite);
        verify(page, "the buttons page did not load");
        waitForRendering(page);
        return page;
    }

    // The list is the Flickable on this page, and a row has to have been built
    // before anything can be scrolled over it.
    function rowInView(page, key) {
        let list = findChild(page, "keyList");
        verify(list, "the buttons page has no key list");
        list.positionViewAtIndex(App.profile.keys.rowForKey(key), ListView.Center);
        wait(50);
        verify(list.contentHeight > list.height,
               "the key list is not long enough to scroll");
        return list;
    }

    function test_a_scroll_over_a_target_picker_moves_the_list_not_the_mapping() {
        let page = buttonsPage();
        // B, near the top, so the list has somewhere to go downwards.
        let list = rowInView(page, "b");
        let row = App.profile.keys.rowForKey("b");
        let before = App.profile.keys.targetAt(row);

        let combo = findChild(page, "target_b");
        verify(combo, "no target picker on the B row");
        verify(combo.enabled, "a disabled picker would ignore a wheel anyway");
        let from = list.contentY;
        scrollOver(combo, list, -120);

        compare(App.profile.keys.targetAt(row), before,
                "scrolling over the picker remapped the key");
        verify(!App.profile.dirty, "scrolling is not an edit");
        tryVerify(() => list.contentY > from, 2000,
                  "the wheel never reached the list");
    }

    function test_a_scroll_over_a_turbo_box_moves_the_list_not_the_rate() {
        let page = buttonsPage();
        // M1 is far enough down to scroll upwards from, which is the direction
        // that takes a turbo rate off zero rather than clamping at it.
        let list = rowInView(page, "m1");
        let row = App.profile.keys.rowForKey("m1");

        let spin = findChild(page, "turbo_m1");
        verify(spin, "no turbo box on the M1 row");
        verify(spin.enabled, "a disabled box would ignore a wheel anyway");
        let before = App.profile.keys.turboAt(row);
        let from = list.contentY;
        verify(from > 0, "the list has nowhere to scroll upwards");
        scrollOver(spin, list, 120);

        compare(App.profile.keys.turboAt(row), before,
                "scrolling over the box turned turbo on");
        verify(!App.profile.dirty, "scrolling is not an edit");
        tryVerify(() => list.contentY < from, 2000,
                  "the wheel never reached the list");
    }

    // The same again for a FormCard row, which needs a separate mechanism: the
    // ComboBox is private to `FormComboBoxDelegate` and cannot be told
    // `wheelEnabled: false` from the page, so components/FormComboBox.qml finds
    // it. A page still on the addon's delegate passes neither half of this.
    function test_a_scroll_over_a_form_row_moves_the_page_not_the_setting() {
        let page = createTemporaryObject(gyroComponent, suite);
        verify(page, "the gyro page did not load");
        waitForRendering(page);

        let flickable = page.flickable;
        verify(flickable, "the gyro page has no flickable");
        verify(flickable.contentHeight > flickable.height,
               "the gyro page is not long enough to scroll");

        let combo = findChild(page, "gyroTarget");
        verify(combo, "no target picker on the gyro page");
        verify(!App.profile.motion.enabled, "the gyro should start off");
        let before = combo.currentIndex;
        let from = flickable.contentY;
        scrollOver(combo, flickable, -120);

        verify(!App.profile.motion.enabled,
               "scrolling over the row turned the gyro on");
        compare(combo.currentIndex, before, "the picker moved under the wheel");
        verify(!App.profile.dirty, "scrolling is not an edit");
        tryVerify(() => flickable.contentY > from, 2000,
                  "the wheel never reached the page");
    }

    // The knobs, which were left out of the first pass on the strength of
    // `wheelEnabled` having no effect on a Slider under this style. It does
    // not -- and the conclusion drawn from that, that nothing could be done,
    // was wrong: the value moves, the profile changes, and the page stays put.
    function test_a_scroll_over_a_trigger_knob_moves_the_page_not_the_effect() {
        let page = createTemporaryObject(triggersComponent, suite);
        verify(page, "the triggers page did not load");
        waitForRendering(page);

        let flickable = page.flickable;
        verify(flickable, "the triggers page has no flickable");
        verify(flickable.contentHeight > flickable.height,
               "the triggers page is not long enough to scroll");

        let slider = findChild(page, "strokeStart_leftSlider");
        verify(slider, "no travel-start knob on the triggers page");
        const before = App.profile.triggers.left.strokeStart;
        // Downwards, so the value has somewhere to go: the knob starts at the
        // bottom of its range and scrolling it further would clamp and pass
        // whatever the style does.
        App.profile.triggers.left.strokeStart = 40;
        App.profile.resetAll();
        tryCompare(slider, "value", 40, 2000);

        let from = flickable.contentY;
        scrollOver(slider, flickable, -120);

        compare(App.profile.triggers.left.strokeStart, 40,
                "scrolling over the knob moved the trigger's travel window");
        compare(slider.value, 40, "the knob moved under the wheel");
        tryVerify(() => flickable.contentY > from, 2000,
                  "the wheel never reached the page");
    }
}
