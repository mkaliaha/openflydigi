// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtTest
import Apex5
import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Apex6"
    when: windowShown
    width: 950
    height: 850
    visible: true

    Component { id: controller; ControllerPage { anchors.fill: parent } }
    Component { id: settings; DeviceSettingsPage { anchors.fill: parent } }
    Component { id: lighting; LightingPage { anchors.fill: parent } }
    Component { id: buttons; ButtonsPage { anchors.fill: parent } }

    function init() {
        failOnWarning(/.*/);
        Fixture.useApex6();
        tryCompare(App.profile, "loaded", true, 5000);
        tryCompare(App.settings, "loaded", true, 5000);
        tryCompare(App.lighting, "loaded", true, 5000);
        verify(App.profile.apex6);
    }

    function cleanup() {
        Fixture.useLegacy();
        tryCompare(App.profile, "loaded", true, 5000);
        tryCompare(App.settings, "loaded", true, 5000);
        tryCompare(App.lighting, "loaded", true, 5000);
        verify(!App.profile.apex6);
    }

    function test_settings_write_native_values() {
        let page = createTemporaryObject(settings, suite);
        verify(page);
        waitForRendering(page);
        verify(!findChild(page, "sensitivityCombo").visible);
        verify(!findChild(page, "stickDebounceToggle").visible);
        verify(findChild(page, "reportRateCombo").visible);
        verify(findChild(page, "autoCalibrationToggle").enabled);
        let toggle = findChild(page, "quickSwitchToggle");
        let seen = Fixture.settingsReads;
        toggle.toggle();
        toggle.toggled();
        tryVerify(() => Fixture.settingsReads > seen, 5000);
        compare(Fixture.nativeSettings.quick_switch, false);
        let resolution = findChild(page, "precisionCombo");
        seen = Fixture.settingsReads;
        resolution.currentIndex = 1;
        resolution.activated(1);
        tryVerify(() => Fixture.settingsReads > seen, 5000);
        compare(Fixture.nativeSettings.stick_resolution, 9);
        let rate = findChild(page, "reportRateCombo");
        seen = Fixture.settingsReads;
        rate.currentIndex = 0;
        rate.activated(0);
        tryVerify(() => Fixture.settingsReads > seen, 5000);
        compare(Fixture.nativeSettings.usb_report_rate_hz, 125);
    }

    function test_profiles_switch_and_save_without_legacy_actions() {
        let page = createTemporaryObject(controller, suite);
        verify(page);
        waitForRendering(page);
        verify(!findChild(page, "profileName").enabled);
        verify(!findChild(page, "switchCopyButton").visible);
        verify(!findChild(page, "backupButton").visible);
        verify(!findChild(page, "resetAllButton").visible);
        let seen = Fixture.profileReads;
        mouseClick(findChild(page, "profileSlot2"));
        tryVerify(() => Fixture.profileReads > seen, 5000);
        compare(Fixture.nativeSlot, 2);
        compare(App.profile.cfgId, 2);
        let save = findChild(page, "saveButton");
        compare(save.text, "Save profile");
        verify(save.enabled);
        mouseClick(save);
        tryVerify(() => Fixture.nativeSaveCount() === 1, 5000);
    }

    function test_lighting_uses_native_effects_and_apply_save() {
        let page = createTemporaryObject(lighting, suite);
        verify(page);
        waitForRendering(page);
        compare(App.lighting.effectNames.length, 6);
        let combo = findChild(page, "effectCombo");
        combo.currentIndex = 2;
        combo.activated(2);
        compare(App.lighting.colours.allowed, 8);
        findChild(page, "brightness").moved(37);
        findChild(page, "speed").moved(61);
        verify(App.lighting.dirty);
        mouseClick(findChild(page, "lightingApplyButton"));
        tryCompare(App.lighting, "dirty", false, 5000);
        compare(Fixture.nativeLighting.base.mode, 2);
        compare(Fixture.nativeLighting.base.config.brightness, 37);
        compare(Fixture.nativeLighting.base.config.speed, 61);
        mouseClick(findChild(page, "lightingSaveButton"));
        tryCompare(App.lighting, "saveNeeded", false, 5000);
        compare(Fixture.nativeSaveCount(), 1);
    }

    function choose(page, name, index) {
        let control = findChild(page, name);
        verify(control, name);
        control.currentIndex = index;
        control.activated(index);
    }

    function test_buttons_layer_and_hold_shift_apply() {
        let page = createTemporaryObject(buttons, suite);
        verify(page);
        waitForRendering(page);
        verify(!App.profile.dirty); // Constructing the controls must not edit.
        choose(page, "nativeLayer", 1);
        verify(findChild(page, "nativeCreateLayer").visible);
        mouseClick(findChild(page, "nativeCreateLayer"));
        verify(App.profile.nativeButtons.layerExists);
        choose(page, "nativeSource", 0);
        findChild(page, "nativeAddAction").clicked();
        choose(page, "nativeFieldChoice0", 3); // Shift A -> Y
        choose(page, "nativeLayer", 0);
        choose(page, "nativeSource", 17); // M4
        findChild(page, "nativeAddAction").clicked();
        choose(page, "nativeTarget", 5); // Hold Shift
        compare(App.profile.nativeButtons.fields[0].choices.length, 2);
        compare(App.profile.nativeButtons.fields[0].selected, 1);
        let apply = findChild(page, "applyButton");
        verify(apply.visible && apply.enabled);
        mouseClick(apply);
        tryCompare(App.profile, "writing", false, 5000);
        compare(App.profile.dirty, false);
        compare(Fixture.nativeActions(0, "m4")[0].target.type, "layer_hold");
        compare(Fixture.nativeActions(0, "m4")[0].target.layer_id, 1);
        compare(Fixture.nativeActions(1, "a")[0].target.id, "y");
        mouseClick(findChild(page, "saveButton"));
        tryVerify(() => Fixture.nativeSaveCount() === 1, 5000);
    }

    function test_buttons_action_parameters_and_press_timing() {
        let page = createTemporaryObject(buttons, suite);
        verify(page);
        waitForRendering(page);
        findChild(page, "nativeAddAction").clicked();
        choose(page, "nativeActivator", 2); // Long press
        choose(page, "nativeTarget", 4); // Turbo
        choose(page, "nativeFieldChoice0", 1); // B
        let interval = findChild(page, "nativeFieldValue1");
        interval.value = 170;
        interval.valueModified();
        let timing = findChild(page, "nativeLongPress");
        timing.value = 750;
        timing.valueModified();
        compare(App.profile.nativeButtons.longPress, 750);
        verify(!interval.wheelEnabled);
        verify(!timing.wheelEnabled);
        choose(page, "nativeTarget", 7); // No macros: retain the existing action.
        verify(findChild(page, "nativeMappingError").visible);
        compare(App.profile.nativeButtons.target, 4);
        findChild(page, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(App.profile.dirty, false);
        let stored = Fixture.nativeActions(0, "a")[0];
        compare(stored.type, "timing_long");
        compare(stored.target.button_id, "b");
        compare(stored.target.turbo_interval_10ms, 17);
        findChild(page, "nativeRemoveAction").clicked();
        compare(App.profile.nativeButtons.actionNames.length, 0);
        verify(App.profile.nativeButtons.bindingState.indexOf("Empty override") >= 0);
        findChild(page, "nativeResetButton").clicked();
        compare(App.profile.nativeButtons.bindingState, "No override in this layer.");
    }
}
