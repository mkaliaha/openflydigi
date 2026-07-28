// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Remapping a key on the Buttons page, all the way through to the pad's blob.
//
// The page is instantiated here rather than reached through the real window.
// TestCase is itself an Item, in a window QtQuickTest shows and activates, so
// a synthetic click lands. A window a test creates for itself never becomes
// active under the offscreen platform and clicks into it are dropped, which
// shows up as an edit that applied but was never written -- intermittently,
// and on whichever test happened to click first.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Buttons"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        ButtonsPage {
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
        // createTemporaryObject cleans up even when an assertion fails, so a
        // broken case cannot leave a page behind to confuse the next one.
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the buttons page did not load");
        // A click is delivered to whatever is at the cursor position, so the
        // page has to have been laid out before a test can press anything.
        waitForRendering(page);
    }

    // Delegates only exist once the view has scrolled to them, so a test that
    // wants one has to ask for it first.
    function rowFor(key) {
        let list = findChild(page, "keyList");
        verify(list, "the buttons page has no key list");
        let index = App.profile.keys.rowForKey(key);
        verify(index >= 0, "no such key: " + key);
        list.positionViewAtIndex(index, ListView.Center);
        wait(50);
        let row = findChild(page, "keyRow_" + key);
        verify(row, "no delegate for " + key);
        return row;
    }

    function retarget(key, target) {
        let combo = findChild(rowFor(key), "target_" + key);
        verify(combo, "no target picker on the " + key + " row");
        let wanted = App.profile.keys.targets.indexOf(target);
        verify(wanted > 0, "the target list has no " + target);
        combo.currentIndex = wanted;
        combo.activated(wanted);          // the handler a real click reaches
    }

    function test_the_list_shows_every_key() {
        let list = findChild(page, "keyList");
        compare(list.count, App.profile.keys.count);
        verify(list.count > 20, "suspiciously few keys: " + list.count);
    }

    function test_a_row_reflects_the_model() {
        let label = findChild(rowFor("m1"), "keyLabel_m1");
        verify(label, "no label on the row");
        compare(label.text, "M1");
        verify(!label.font.bold, "an unmapped key should not be marked");
    }

    function test_apply_is_disabled_until_something_changes() {
        verify(!findChild(page, "applyButton").enabled, "nothing was edited yet");
        verify(!findChild(page, "saveButton").enabled, "nothing was edited yet");
    }

    function test_choosing_a_target_marks_the_key_remapped() {
        retarget("m1", "a");
        verify(App.profile.dirty, "editing did not mark the profile dirty");
        verify(findChild(rowFor("m1"), "keyLabel_m1").font.bold,
               "a remapped key should be marked");
    }

    function test_applying_writes_one_packet_and_the_pad_holds_it() {
        retarget("m1", "a");
        let apply = findChild(page, "applyButton");
        verify(apply.enabled, "apply should be enabled by an edit");

        Pad.resetCounters();
        mouseClick(apply);
        // Waited on the model, not on the button: `enabled` is a binding, and
        // a closure re-read by tryVerify does not pick up the rebind.
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");

        compare(Pad.packetsReceived, 1, "one remap should be one packet");
        compare(Pad.targetOf(0, "m1"), "a", "the pad did not get the remap");
    }

    function test_apply_and_save_reaches_flash() {
        let spin = findChild(rowFor("m2"), "turbo_m2");
        verify(spin, "no turbo control on the row");
        spin.value = 10;
        spin.valueModified();

        let save = findChild(page, "saveButton");
        verify(save.enabled, "a turbo edit should enable save");

        Pad.resetCounters();
        mouseClick(save);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");

        compare(Pad.savedCount, 4, "the save never reached flash");
        compare(Pad.turboOf(0, "m2"), 10, "the pad did not get the turbo rate");
    }

    function test_turbo_mode_waits_for_a_frequency() {
        let row = rowFor("m3");
        let mode = findChild(row, "turboMode_m3");
        verify(mode, "no turbo mode picker");
        verify(!mode.enabled, "turbo mode means nothing without a frequency");

        let spin = findChild(row, "turbo_m3");
        spin.value = 5;
        spin.valueModified();
        tryVerify(() => mode.enabled, 2000,
                  "setting a frequency should enable the mode picker");
    }

    function test_resetting_clears_every_remap() {
        Pad.seedRemap(0, "m4", "y");
        Fixture.resetCounts();
        App.reload();
        tryVerify(() => Fixture.profileReads >= 1, 5000, "the reread never landed");
        compare(App.profile.keys.targetAt(App.profile.keys.rowForKey("m4")), "y",
                "the seeded remap was not read back");

        App.profile.resetAll();
        verify(App.profile.dirty, "a reset that changed something is a change");

        let apply = findChild(page, "applyButton");
        Pad.resetCounters();
        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        compare(Pad.remapCount(0), 0, "the pad still holds a remap");
    }
}
