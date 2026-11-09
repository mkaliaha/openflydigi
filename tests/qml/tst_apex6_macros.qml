// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtTest
import Apex5
import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Apex6Macros"
    when: windowShown
    width: 950
    height: 850
    visible: true

    Component { id: macros; MacrosPage { anchors.fill: parent } }
    Component { id: buttons; ButtonsPage { anchors.fill: parent } }

    function init() {
        failOnWarning(/.*/);
        Fixture.useApex6();
        tryCompare(App.profile, "loaded", true, 5000);
        tryCompare(App.lighting, "loaded", true, 5000);
        tryCompare(App.settings, "loaded", true, 5000);
    }

    function cleanup() {
        Fixture.useLegacy();
        tryCompare(App.profile, "loaded", true, 5000);
        tryCompare(App.lighting, "loaded", true, 5000);
        tryCompare(App.settings, "loaded", true, 5000);
    }

    function choose(page, name, index) {
        let control = findChild(page, name);
        verify(control, name);
        control.currentIndex = index;
        control.activated(index);
    }

    function number(page, name, value) {
        let control = findChild(page, name);
        verify(control, name);
        control.value = value;
        control.valueModified();
        verify(!control.wheelEnabled);
    }

    function test_author_edit_bind_apply_save_delete() {
        let page = createTemporaryObject(macros, suite);
        verify(page);
        waitForRendering(page);
        verify(!App.profile.dirty);
        verify(!findChild(page, "recordAction").visible);
        verify(!findChild(page, "buildAction").visible);
        findChild(page, "nativeMacroCreate").clicked();
        let editor = App.profile.nativeMacros;
        compare(editor.macroId, 0);
        compare(editor.stepCount, 2);
        let field = findChild(page, "nativeMacroName");
        field.text = "Combo";
        field.textEdited();
        choose(page, "nativeMacroMode", 2);
        number(page, "nativeMacroInterval", 123);
        choose(page, "nativeFieldChoice0", 1); // Press B, release A is invalid.
        verify(findChild(page, "nativeMacroValidation").visible);
        verify(!findChild(page, "applyButton").enabled);
        verify(!findChild(page, "saveButton").enabled);
        verify(!findChild(page, "nativeMacroCommit").enabled);
        verify(!findChild(page, "nativeMacroLayer").enabled);
        choose(page, "nativeMacroStep", 1);
        choose(page, "nativeFieldChoice0", 1);
        number(page, "nativeMacroDelay", 75);
        compare(editor.validation, "");
        findChild(page, "nativeMacroCommit").clicked();
        verify(!editor.draftDirty);
        let binding = createTemporaryObject(buttons, suite);
        waitForRendering(binding);
        findChild(binding, "nativeAddAction").clicked();
        choose(binding, "nativeTarget", 7);
        compare(App.profile.nativeButtons.fields[0].choices[0], "Combo");
        findChild(binding, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(App.profile.dirty, false);
        let stored = Fixture.nativeMacros(0)[0];
        compare(stored.name, "Combo");
        compare(stored.type, "press_loop_wait");
        compare(stored.interval, 123);
        compare(stored.steps[0].key, "b");
        compare(stored.steps[1].delay, 75);
        compare(Fixture.nativeActions(0, "a")[0].target.macro_id, 0);
        findChild(binding, "saveButton").clicked();
        tryVerify(() => Fixture.nativeSaveCount() === 1, 5000);
        tryCompare(App.profile, "writing", false, 5000);
        binding.destroy();
        findChild(page, "nativeMacroDelete").clicked();
        compare(editor.macroId, -1);
        compare(App.profile.nativeButtons.actionNames.length, 0);
        findChild(page, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(Fixture.nativeMacros(0).length, 0);
        compare(Fixture.nativeActions(0, "a").length, 0);
    }

    function test_step_controls_layers_copy_and_discard() {
        let page = createTemporaryObject(macros, suite);
        verify(page);
        waitForRendering(page);
        choose(page, "nativeMacroLayer", 2);
        findChild(page, "nativeMacroCreateLayer").clicked();
        number(page, "nativeMacroNewId", 7);
        findChild(page, "nativeMacroCreate").clicked();
        let editor = App.profile.nativeMacros;
        compare(editor.macroId, 7);
        choose(page, "nativeMacroTarget", 1); // Keyboard
        number(page, "nativeFieldValue0", 4);
        number(page, "nativeFieldValue1", 2);
        choose(page, "nativeMacroStep", 1);
        choose(page, "nativeMacroTarget", 1);
        number(page, "nativeFieldValue0", 4);
        number(page, "nativeFieldValue1", 2);
        findChild(page, "nativeMacroCommit").clicked();
        number(page, "nativeMacroNewId", 19);
        findChild(page, "nativeMacroCopy").clicked();
        compare(editor.macroId, 19);
        compare(editor.target, 1);
        findChild(page, "nativeMacroAddStep").clicked();
        compare(editor.stepCount, 3);
        choose(page, "nativeMacroEvent", 1);
        findChild(page, "nativeMacroMoveUp").clicked();
        compare(editor.step, 1);
        findChild(page, "nativeMacroMoveDown").clicked();
        compare(editor.step, 2);
        verify(editor.validation !== "");
        findChild(page, "nativeMacroRemoveStep").clicked();
        compare(editor.stepCount, 2);
        compare(editor.draftDirty, false);
        number(page, "nativeMacroDelay", 99);
        findChild(page, "nativeMacroDiscard").clicked();
        compare(findChild(page, "nativeMacroDelay").value, 50);
        choose(page, "nativeMacroLayer", 0);
        compare(editor.macroNames.length, 0);
        choose(page, "nativeMacroLayer", 2);
        compare(editor.macroNames.length, 2);
        compare(editor.macroId, 7);
    }

    function test_keyboard_wheel_and_bindings_survive_selection() {
        let page = createTemporaryObject(macros, suite);
        verify(page);
        waitForRendering(page);
        findChild(page, "nativeMacroCreate").clicked();
        choose(page, "nativeMacroStep", 1);
        waitForRendering(page);
        wait(50); // The new definition expands the page before measuring it.
        let spin = findChild(page, "nativeMacroDelay");
        let flickable = page.flickable;
        let offset = spin.mapToItem(flickable.contentItem, 0, 0).y;
        flickable.contentY = Math.max(0, offset - flickable.height / 2);
        wait(50);
        mouseClick(spin.contentItem);
        keyClick(Qt.Key_A, Qt.ControlModifier);
        keyClick(Qt.Key_8);
        keyClick(Qt.Key_7);
        keyClick(Qt.Key_Return);
        compare(App.profile.nativeMacros.delay, 87);
        App.profile.nativeMacros.step = 0;
        compare(spin.value, 0);
        App.profile.nativeMacros.step = 1;
        compare(spin.value, 87);
        const before = flickable.contentY;
        const where = spin.mapToItem(flickable, spin.width / 2, spin.height / 2);
        verify(where.y > 0 && where.y < flickable.height);
        mouseWheel(flickable, where.x, where.y, 0, 120);
        compare(App.profile.nativeMacros.delay, 87);
        tryVerify(() => flickable.contentY < before, 2000);
        App.profile.nativeMacros.discard();
        compare(spin.value, 50);
    }

    function test_name_keyboard_edit_and_discard_preserve_binding() {
        let page = createTemporaryObject(macros, suite);
        verify(page);
        waitForRendering(page);
        findChild(page, "nativeMacroCreate").clicked();
        waitForRendering(page);
        wait(50);
        let nameField = findChild(page, "nativeMacroName");
        mouseClick(nameField);
        verify(nameField.activeFocus);
        keyClick(Qt.Key_C);
        keyClick(Qt.Key_A);
        keyClick(Qt.Key_T);
        compare(App.profile.nativeMacros.name.toLowerCase(), "cat");
        App.profile.nativeMacros.discard();
        compare(nameField.text, "");
    }

    function test_page_navigation_preserves_draft_and_controller_switch_clears_it() {
        let page = createTemporaryObject(macros, suite);
        verify(page);
        waitForRendering(page);
        findChild(page, "nativeMacroCreate").clicked();
        App.profile.nativeMacros.interval = 123;
        page.destroy();
        wait(0);
        page = createTemporaryObject(macros, suite);
        waitForRendering(page);
        compare(findChild(page, "nativeMacroInterval").value, 123);
        verify(!findChild(page, "applyButton").enabled);
        Fixture.useLegacy();
        tryCompare(App.profile, "loaded", true, 5000);
        verify(!App.profile.nativeMacros.draftDirty);
        verify(findChild(page, "recordAction").visible);
        verify(findChild(page, "buildAction").visible);
        Fixture.useApex6();
        tryCompare(App.profile, "loaded", true, 5000);
        waitForRendering(page);
        verify(findChild(page, "nativeMacroCreate").visible);
        compare(App.profile.nativeMacros.macroId, -1);
        verify(!App.profile.dirty);
    }
}
