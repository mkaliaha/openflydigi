// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Building and editing a macro on the Macros page, through to the pad's blob.
//
// The page is instantiated here rather than reached through the real window,
// for the reason tst_buttons.qml gives: TestCase is itself an Item in a window
// QtQuickTest shows and activates, and a window a test makes for itself never
// becomes active under the offscreen platform, so clicks into it are dropped.
//
// **A dialog's contents are found through `contentItem`, not through the
// dialog.** `findChild(dialog, ...)` finds nothing however long it waits:
// Kirigami.Dialog holds its content in a control that is not a QObject child
// of the dialog, so the recursion never reaches it. `findChild(page, ...)` and
// `findChild(suite, ...)` fail for the same reason. Measured here, and it cost
// a debugging session -- `findChild(dialog.contentItem, ...)` is the one that
// works.
//
// **`opened` stays false in this environment** while the content is built and
// laid out regardless, because nothing activates the window under the offscreen
// platform. So a delegate can be found and driven, and waiting on `opened`
// first would wait forever.
//
// Values are driven through the same handlers a real click reaches --
// `activated`, `valueModified` -- which is the compromise tst_buttons.qml makes
// for its combo boxes and for the same reason.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Macros"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        MacrosPage {
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
        verify(page, "the macros page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        // A dialog left open outlives the page and would be found by the next
        // case, which is a confusing failure to read.
        App.profile.macros.endEdit();
    }

    function test_an_empty_profile_says_so_and_offers_both_ways_in() {
        verify(findChild(page, "macrosEmpty").visible,
               "a profile with no macros should say so");
        verify(findChild(page, "recordAction").enabled,
               "recording should be offered");
        verify(findChild(page, "buildAction").enabled,
               "building should be offered");
    }

    function test_building_a_macro_binds_the_key_and_opens_the_editor() {
        App.profile.macros.build(App.profile.macros.triggerKeys.indexOf("M1"));

        compare(App.profile.macros.count, 1, "no macro was built");
        compare(App.profile.macros.editingLabel, "M1",
                "the editor did not open on the new macro");
        compare(App.profile.macros.stepEditor.count, 2,
                "a built macro should start as a balanced tap");
        verify(App.profile.dirty, "building did not mark the profile dirty");
    }

    function test_the_step_rows_exist_and_write_through_to_the_pad() {
        App.profile.macros.build(App.profile.macros.triggerKeys.indexOf("M1"));
        let dialog = findChild(page, "stepDialog");
        verify(dialog, "the page has no step dialog");
        dialog.open();
        // Searched from the dialog, not from the page: Kirigami.Dialog builds
        // its contents into an overlay that is not under the page in the object
        // tree, so findChild(page, ...) never sees a step row however long it
        // waits.
        tryVerify(() => findChild(dialog.contentItem, "macroStep_0") !== null,
                  2000, "no delegate for the first step");

        let row = findChild(dialog.contentItem, "macroStep_0");
        let keyCombo = findChild(row, "macroStep_0Key");
        verify(keyCombo, "no key picker on the step row");

        // The handler a real click reaches. Assigning currentIndex alone only
        // moves the control; `activated` is what the page is listening to.
        let wanted = App.profile.macros.stepKeys.indexOf("X");
        verify(wanted >= 0, "the step key list has no X");
        keyCombo.currentIndex = wanted;
        keyCombo.activated(wanted);

        let delay = findChild(row, "macroStep_0Delay");
        verify(delay, "no delay control on the step row");
        delay.value = 155;
        delay.valueModified();

        // Waited on the model rather than on a binding, which tryVerify over a
        // closure never sees update.
        tryCompare(App.profile.macros.stepEditor, "count", 2, 2000);
        compare(App.profile.macros.stepEditor.totalMs, 200,
                "155 ms should have been floored to the 10 ms the pad stores");

        // Shut before applying: the dialog is modal, so a click aimed at the
        // page's footer lands on its overlay and nothing happens.
        dialog.close();
        tryVerify(() => !dialog.visible, 2000, "the dialog would not close");

        let apply = findChild(page, "applyButton");
        verify(apply.enabled, "an edit should enable apply");
        Pad.resetCounters();
        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");

        compare(Pad.targetOf(0, "m1"), "macro", "the pad did not get the binding");
        let stored = Pad.macroOf(0, "m1");
        compare(stored.length, 2, "the pad did not get both steps");
        compare(stored[0].key, "x", "the pad did not get the retargeted step");
        // The gap was typed on the first step, and 155 is not a multiple of the
        // 10 ms the pad stores, so what came back proves the quantising
        // survived the round trip rather than being cosmetic in the model.
        compare(stored[0].delay, 150, "the pad did not get the quantised gap");
        compare(stored[1].delay, 50, "the second step's gap should be untouched");
    }

    function test_a_macro_that_holds_a_key_is_named_on_the_card() {
        App.profile.macros.build(App.profile.macros.triggerKeys.indexOf("M1"));
        App.profile.macros.removeStep(1);

        let held = findChild(page, "macroHeld_0");
        verify(held, "the card has no held-key row");
        tryVerify(() => held.visible, 2000,
                  "a macro ending mid-press should be called out on the card");
        verify(held.text.indexOf("A") >= 0,
               "the card should name the key left down: " + held.text);

        App.profile.macros.balance();
        tryVerify(() => !held.visible, 2000,
                  "releasing what is held should clear the warning");
    }

    function test_deleting_the_edited_macro_closes_the_editor() {
        App.profile.macros.build(App.profile.macros.triggerKeys.indexOf("M1"));
        let dialog = findChild(page, "stepDialog");
        dialog.open();
        // `visible` rather than `opened`: nothing activates the window under
        // the offscreen platform, so the open transition never completes and
        // `opened` stays false even though the dialog is up and populated.
        tryVerify(() => dialog.visible, 2000, "the step dialog never opened");

        App.profile.macros.remove(0);
        compare(App.profile.macros.editingRow, -1,
                "the cursor should let go of a macro that is gone");
        tryVerify(() => !dialog.visible, 2000,
                  "the dialog should close when its macro is deleted");
    }

    function test_the_budget_counts_steps_not_just_macros() {
        App.profile.macros.build(App.profile.macros.triggerKeys.indexOf("M1"));
        let used = App.profile.macros.stepsUsed;
        App.profile.macros.addStep(-1);
        compare(App.profile.macros.stepsUsed, used + 2,
                "the shared step budget did not move with the step count");
    }
}
