// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtTest
import Apex5
import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Apex6Gyro"
    when: windowShown
    width: 950
    height: 600
    visible: true

    Component { id: gyro; GyroPage { anchors.fill: parent } }

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

    function setNumber(page, name, value) {
        let control = findChild(page, name);
        verify(control, name);
        control.value = value;
        control.valueModified();
    }

    function keyIndex(label) {
        return App.profile.nativeGyro.keyNames.indexOf(label);
    }

    function test_controls_apply_and_save_native_profile() {
        let page = createTemporaryObject(gyro, suite);
        verify(page);
        waitForRendering(page);
        verify(!App.profile.dirty);
        verify(!findChild(page, "gyroTarget").visible);
        choose(page, "nativeGyroTarget", 2);
        choose(page, "nativeGyroKey", keyIndex("M4"));
        choose(page, "nativeGyroSecondKey", keyIndex("Left trigger"));
        choose(page, "nativeGyroEnableType", 1);
        let invert = findChild(page, "nativeGyroInvertY");
        invert.checked = true;
        invert.toggled();
        choose(page, "nativeGyroMidpointCount", 2);
        setNumber(page, "nativeGyroCurveY1", 70);
        findChild(page, "applyButton").clicked();
        tryCompare(App.profile, "writing", false, 5000);
        compare(App.profile.dirty, false);
        let stored = Fixture.nativeGyro();
        compare(stored.target, 2);
        compare(stored.keys, ["m4", "lt"]);
        compare(stored.enable_type, 0); // Shared facade's toggle ID.
        compare(stored.invert_y, true);
        compare(stored.curve.points.length, 2);
        compare(stored.curve.points[0][1], 70);
        findChild(page, "saveButton").clicked();
        tryVerify(() => Fixture.nativeSaveCount() === 1, 5000);
        tryCompare(App.profile, "writing", false, 5000);
    }

    function test_target_resets_curve_and_off_preserves_it() {
        let page = createTemporaryObject(gyro, suite);
        verify(page);
        waitForRendering(page);
        choose(page, "nativeGyroTarget", 2);
        choose(page, "nativeGyroMidpointCount", 2);
        choose(page, "nativeGyroTarget", 3);
        compare(findChild(page, "nativeGyroMidpointCount").currentIndex, 0);
        compare(findChild(page, "nativeGyroCurveY1").value, 51);
        verify(findChild(page, "nativeGyroMouseNote").visible);
        verify(findChild(page, "nativeGyroMouseRadius").visible);
        setNumber(page, "nativeGyroMouseRadius", 120);
        setNumber(page, "nativeGyroCurveY1", 90);
        choose(page, "nativeGyroTarget", 0);
        verify(!findChild(page, "nativeGyroKey").visible);
        compare(App.profile.nativeGyro.nodes[1].y, 90);
        choose(page, "nativeGyroTarget", 1);
        compare(findChild(page, "nativeGyroCurveX1").value, 145);
        compare(App.profile.nativeGyro.mouseRadius, 120);
        verify(!findChild(page, "nativeGyroMouseRadius").visible);
    }

    function test_rejected_key_restores_binding_and_second_key_works_in_toggle_mode() {
        let page = createTemporaryObject(gyro, suite);
        verify(page);
        waitForRendering(page);
        choose(page, "nativeGyroTarget", 2);
        choose(page, "nativeGyroKey", keyIndex("M1"));
        choose(page, "nativeGyroEnableType", 1);
        verify(findChild(page, "nativeGyroSecondKey").visible);
        verify(findChild(page, "nativeGyroSecondKey").enabled);
        choose(page, "nativeGyroSecondKey", keyIndex("Right trigger"));
        choose(page, "nativeGyroKey", 0);
        compare(findChild(page, "nativeGyroKey").currentIndex, keyIndex("M1"));
        verify(findChild(page, "nativeGyroError").visible);
        choose(page, "nativeGyroSecondKey", 0);
        compare(App.profile.nativeGyro.secondKey, 0);
        verify(!findChild(page, "nativeGyroError").visible);
        App.profile.nativeGyro.invertX = true;
        compare(findChild(page, "nativeGyroInvertX").checked, true);
        App.profile.nativeGyro.invertX = false;
        compare(findChild(page, "nativeGyroInvertX").checked, false);
    }

    function test_actual_keyboard_input_and_wheel_scroll() {
        let page = createTemporaryObject(gyro, suite);
        verify(page);
        waitForRendering(page);
        App.profile.nativeGyro.target = 2;
        let input = findChild(page, "nativeGyroCurveY1");
        verify(input);
        input.contentItem.forceActiveFocus();
        keyClick(Qt.Key_A, Qt.ControlModifier);
        keyClick(Qt.Key_2);
        keyClick(Qt.Key_0);
        keyClick(Qt.Key_0);
        keyClick(Qt.Key_Return);
        compare(App.profile.nativeGyro.nodes[1].y, 200);
        let flickable = page.flickable;
        flickable.contentY = Math.max(0, flickable.contentHeight - flickable.height);
        wait(50);
        const before = flickable.contentY;
        verify(before > 0);
        const where = input.mapToItem(flickable, input.width / 2, input.height / 2);
        verify(where.y > 0 && where.y < flickable.height);
        mouseWheel(flickable, where.x, where.y, 0, 120);
        compare(App.profile.nativeGyro.nodes[1].y, 200);
        tryVerify(() => flickable.contentY < before, 2000);
    }

    function test_navigation_and_legacy_switch() {
        let page = createTemporaryObject(gyro, suite);
        verify(page);
        waitForRendering(page);
        choose(page, "nativeGyroTarget", 2);
        choose(page, "nativeGyroKey", keyIndex("M4"));
        page.destroy();
        page = createTemporaryObject(gyro, suite);
        verify(page);
        waitForRendering(page);
        compare(findChild(page, "nativeGyroTarget").currentIndex, 2);
        compare(findChild(page, "nativeGyroKey").currentIndex, keyIndex("M4"));
        Fixture.useLegacy();
        tryCompare(App.profile, "apex6", false, 5000);
        tryCompare(App.profile, "loaded", true, 5000);
        tryCompare(App.lighting, "loaded", true, 5000);
        tryCompare(App.settings, "loaded", true, 5000);
        tryVerify(() => findChild(page, "gyroTarget").visible, 5000);
        verify(!findChild(page, "nativeGyroTarget").visible);
    }
}
