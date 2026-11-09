// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtTest
import Apex5
import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Apex6Haptics"
    when: windowShown
    width: 950
    height: 600
    visible: true

    Component { id: vibration; VibrationPage { anchors.fill: parent } }
    Component { id: triggers; TriggersPage { anchors.fill: parent } }

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

    function toggle(page, side, enabled) {
        let control = findChild(page, "nativeFeedbackEnabled_" + side);
        verify(control);
        control.checked = enabled;
        control.toggled();
    }

    function test_grips_and_independent_feedback_apply_and_save() {
        let page = createTemporaryObject(vibration, suite);
        verify(page);
        waitForRendering(page);
        verify(!App.profile.dirty);
        verify(!findChild(page, "vibrationMaster").visible);
        findChild(page, "nativeFeedbackLevel_grips").moved(2);
        toggle(page, "left", true);
        findChild(page, "nativeFeedbackLevel_left").moved(3);
        verify(!App.profile.nativeHaptics.right.enabled);
        findChild(page, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(App.profile.dirty, false);
        compare(Fixture.nativeFeedback("grips").vibration.level, 2);
        compare(Fixture.nativeFeedback("left").vibration.enabled, true);
        compare(Fixture.nativeFeedback("left").vibration.level, 3);
        compare(Fixture.nativeFeedback("right").vibration.enabled, false);
        findChild(page, "saveButton").clicked();
        tryVerify(() => Fixture.nativeSaveCount() === 1, 5000);
        tryCompare(App.profile, "writing", false, 5000);
    }

    function test_scene_controls_and_normal_refresh_response_and_zones() {
        let page = createTemporaryObject(triggers, suite);
        verify(page);
        waitForRendering(page);
        verify(!App.profile.dirty);
        App.profile.nativeTriggers.setCoordinate(0, 0, 30);
        App.profile.nativeTriggers.addZone();
        choose(page, "nativeScene_left", 2); // Rifle
        choose(page, "nativeSceneTravel_left", 2);
        choose(page, "nativeSceneFrequency_left", 0);
        compare(App.profile.nativeHaptics.left.modeText, "Rifle");
        compare(App.profile.nativeTriggers.nodes[0].x, 30);
        findChild(page, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(Fixture.nativeFeedback("left").scene.scene, "rifle");
        compare(Fixture.nativeFeedback("left").scene.travel, 3);
        compare(Fixture.nativeFeedback("left").scene.frequency, 1);
        choose(page, "nativeScene_left", 5); // Pistol
        verify(!findChild(page, "nativeSceneFrequency_left").visible);
        compare(findChild(page, "nativeSceneTravel_left").currentIndex, 0);
        choose(page, "nativeScene_left", 0);
        compare(App.profile.nativeTriggers.nodes[0].x, 0);
        compare(App.profile.nativeTriggers.zoneNames.length, 0);
        verify(!findChild(page, "nativeSceneTravel_left").visible);
        findChild(page, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(App.profile.dirty, false);
        compare(Fixture.nativeFeedback("left").scene.mode, "normal");
    }

    function test_scene_and_feedback_bindings_follow_cross_page_edits() {
        let page = createTemporaryObject(vibration, suite);
        verify(page);
        waitForRendering(page);
        toggle(page, "left", true);
        compare(findChild(page, "nativeFeedbackEnabled_left").checked, true);
        App.profile.nativeHaptics.left.scene = 8;
        compare(findChild(page, "nativeFeedbackEnabled_left").checked, false);
        verify(findChild(page, "nativeFeedbackRestore_left").visible);
        findChild(page, "nativeFeedbackRestore_left").clicked();
        compare(App.profile.nativeHaptics.left.strengthZero, false);
        compare(App.profile.nativeHaptics.left.level, 1);
        compare(App.profile.nativeHaptics.left.scene, 8);
        toggle(page, "left", true);
        compare(App.profile.nativeHaptics.left.scene, 0);
        let scenes = createTemporaryObject(triggers, suite);
        verify(scenes);
        waitForRendering(scenes);
        choose(scenes, "nativeScene_right", 2);
        choose(scenes, "nativeSceneTravel_right", 2);
        App.profile.nativeHaptics.right.enabled = true;
        compare(findChild(scenes, "nativeScene_right").currentIndex, 0);
        compare(findChild(page, "nativeFeedbackEnabled_right").checked, true);
        App.profile.nativeHaptics.right.scene = 5;
        compare(findChild(scenes, "nativeScene_right").currentIndex, 5);
        compare(findChild(scenes, "nativeSceneTravel_right").currentIndex, 0);
    }

    function test_actual_slider_drag_keyboard_and_wheel_scroll() {
        let page = createTemporaryObject(vibration, suite);
        verify(page);
        waitForRendering(page);
        let slider = findChild(page, "nativeFeedbackLevel_gripsSlider");
        verify(slider);
        App.profile.nativeHaptics.grips.level = 1;
        mousePress(slider, 5, slider.height / 2);
        for (let x = 15; x < slider.width - 5; x += 10)
            mouseMove(slider, x, slider.height / 2, 5);
        mouseRelease(slider, slider.width - 5, slider.height / 2);
        compare(App.profile.nativeHaptics.grips.level, 5);
        App.profile.nativeHaptics.grips.level = 3;
        compare(slider.value, 3);
        slider.forceActiveFocus();
        keyClick(Qt.Key_Left);
        compare(App.profile.nativeHaptics.grips.level, 2);
        let bottom = findChild(page, "nativeFeedbackLevel_rightSlider");
        let flickable = page.flickable;
        flickable.contentY = Math.max(0, flickable.contentHeight - flickable.height);
        wait(50);
        const before = flickable.contentY;
        verify(before > 0, "The page must overflow before checking wheel scrolling");
        const level = App.profile.nativeHaptics.right.level;
        const where = bottom.mapToItem(flickable, bottom.width / 2, bottom.height / 2);
        verify(where.y > 0 && where.y < flickable.height);
        mouseWheel(flickable, where.x, where.y, 0, 120);
        compare(App.profile.nativeHaptics.right.level, level);
        tryVerify(() => flickable.contentY < before, 2000);
    }

    function test_navigation_preserves_edits_and_legacy_page_returns() {
        let page = createTemporaryObject(triggers, suite);
        verify(page);
        waitForRendering(page);
        choose(page, "nativeScene_right", 8);
        page.destroy();
        page = createTemporaryObject(triggers, suite);
        waitForRendering(page);
        compare(findChild(page, "nativeScene_right").currentIndex, 8);
        let feedback = createTemporaryObject(vibration, suite);
        waitForRendering(feedback);
        Fixture.useLegacy();
        tryCompare(App.profile, "apex6", false, 5000);
        tryCompare(App.profile, "loaded", true, 5000);
        tryCompare(App.lighting, "loaded", true, 5000);
        tryCompare(App.settings, "loaded", true, 5000);
        tryVerify(() => findChild(feedback, "vibrationMaster").visible, 5000);
        verify(findChild(feedback, "leftGrip").visible);
        verify(findChild(page, "leftTrigger").visible);
        verify(!App.profile.nativeHaptics.right.editable);
    }
}
