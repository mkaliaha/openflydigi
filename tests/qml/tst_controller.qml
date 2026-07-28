// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The controller page: the profile slots, and renaming the open profile.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Controller"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        ControllerPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.reload();
        tryVerify(() => Fixture.profileReads >= 1, 5000, "no profile read arrived");
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the controller page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        let seen = Fixture.profileReads;
        wait(150);
        tryVerify(() => Fixture.profileReads === seen, 2000,
                  "a read was still arriving between cases");
    }

    function test_all_four_slots_are_listed() {
        compare(App.profile.slots.count, 4);
        for (let i = 0; i < 4; ++i)
            verify(findChild(page, "profileSlot" + i), "no row for slot " + i);
    }

    function test_only_the_open_slot_reports_itself_read() {
        // Reading a config makes the pad re-seat its trigger motors, so the
        // other three are deliberately left alone until asked for.
        verify(App.profile.loaded, "the first slot should be open");
        compare(Pad.reads.length, 1, "read more than one profile: " + Pad.reads);
    }

    function test_renaming_reaches_the_config_and_marks_it_dirty() {
        let field = findChild(page, "profileName");
        verify(field, "no name field -- renaming would be impossible");
        verify(!App.profile.dirty, "started dirty");

        field.text = "Racing";
        field.textEdited();

        compare(App.profile.title, "Racing", "the rename did not reach the model");
        verify(App.profile.dirty, "renaming should be a change");
    }

    function test_a_rename_can_be_applied_and_lands_on_the_pad() {
        let field = findChild(page, "profileName");
        field.text = "Racing";
        field.textEdited();

        let apply = findChild(page, "applyButton");
        verify(apply, "the controller page needs an apply button to be useful");
        verify(apply.enabled, "a rename should enable apply");

        Pad.resetCounters();
        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        compare(Pad.titleOf(0), "Racing", "the pad did not get the new name");
    }

    function test_the_name_is_capped_at_what_the_pad_stores() {
        // Ten characters: the pad keeps the title as 20 bytes of UTF-16 and
        // truncates silently, so the model refuses to hand it more.
        compare(App.profile.titleMaxChars, 10);
        let field = findChild(page, "profileName");
        field.text = "a name far too long for the pad";
        field.textEdited();
        compare(App.profile.title.length, 10,
                "the title was not capped: " + App.profile.title);
    }

    function test_opening_a_profile_leaves_the_pad_running_it() {
        // Reading switches the pad, and that is the point: the profile on
        // screen is the one in use, which is also what makes saving correct.
        compare(App.profile.slots.active, App.profile.cfgId,
                "the pad is not running the profile being edited");
        verify(App.profile.canSaveToFlash, "saving should be available");
        verify(!findChild(page, "activateButton"),
               "the switch button is redundant now and should be gone");
    }

    function test_saving_is_still_offered_after_applying() {
        // Both buttons used to be bound to `dirty`, so applying a change
        // greyed out the only way to keep it.
        let field = findChild(page, "profileName");
        field.text = "Racing";
        field.textEdited();

        let apply = findChild(page, "applyButton");
        let save = findChild(page, "saveButton");
        verify(apply.enabled && save.enabled, "an edit enables both");

        mouseClick(apply);
        tryCompare(App.profile, "dirty", false, 5000, "the write never completed");
        verify(!apply.enabled, "there is nothing left to apply");
        verify(save.enabled,
               "an applied change still has to be keepable -- it dies on sleep");

        mouseClick(save);
        tryCompare(App.profile, "saveNeeded", false, 5000,
                   "the save never completed");
        compare(Pad.savedCount, 4, "the save did not reach flash");
    }

    function test_the_save_button_is_not_eaten_by_a_mnemonic() {
        // A bare "&" in a button label is taken as a mnemonic, swallowed, and
        // shown as an underline on the next character: "Apply _ save".
        let save = findChild(page, "saveButton");
        verify(save.text.indexOf("&") === -1,
               "the label still contains an ampersand: " + save.text);
        verify(save.text.toLowerCase().indexOf("save") >= 0, save.text);
    }

    function test_the_name_follows_the_selected_profile() {
        let field = findChild(page, "profileName");
        compare(field.text, App.profile.title, "the field starts out of step");

        // Slot 1 has not been read, so there is no title to show -- the field
        // must not go on showing slot 0's.
        App.profile.select(1);
        tryCompare(field, "text", "", 3000,
                   "the name field kept the previous profile's name");
    }

    function test_backup_and_restore_wait_for_a_profile() {
        verify(findChild(page, "backupButton").enabled,
               "a read profile can be backed up");
        verify(findChild(page, "restoreButton").enabled,
               "a read profile can be restored over");
    }
}
