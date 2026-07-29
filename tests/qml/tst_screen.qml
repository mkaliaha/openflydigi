// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The screen page. Its two halves cost wildly different amounts -- a display
// setting is one packet, an upload is minutes -- so most of what is worth
// checking here is that the expensive one cannot be started by accident and
// says what it will cost first.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Screen"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    SignalSpy {
        id: uploadSpy
        target: App.screen
        signalName: "uploadRequested"
    }

    SignalSpy {
        id: settingSpy
        target: App.screen
        signalName: "settingRequested"
    }

    Component {
        id: pageComponent
        ScreenPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.screen.clear();
        uploadSpy.clear();
        settingSpy.clear();
        App.reload();
        // The screen state arrives on the worker thread like everything else,
        // and the switches stay disabled until it has.
        tryVerify(() => Fixture.screenStatusReads >= 1, 5000,
                  "the screen state never arrived");
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the screen page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        // A read still in flight would land during the next case and be blamed
        // on whatever was running then. Same trap as the lighting suite.
        let seen = Fixture.screenStatusReads;
        wait(150);
        tryVerify(() => Fixture.screenStatusReads === seen, 2000,
                  "a read was still arriving between cases");
    }

    function test_nothing_can_be_sent_before_a_picture_is_chosen() {
        compare(App.screen.frameCount, 0);
        verify(!findChild(page, "screenUploadButton").enabled,
               "the send button should be dead with nothing loaded");
        verify(!findChild(page, "screenClearButton").enabled);
        compare(findChild(page, "screenPreview").visible, false);
        verify(findChild(page, "screenPreviewPlaceholder").visible);
    }

    function test_a_still_picture_loads_and_can_be_sent() {
        verify(App.screen.open(Fixture.testImage(1)), "the picture did not load");
        tryCompare(App.screen, "frameCount", 1);
        verify(!App.screen.animated, "one frame is not an animation");
        verify(findChild(page, "screenUploadButton").enabled);
        verify(findChild(page, "screenPreview").visible,
               "a loaded picture should show a preview");
        // The estimate is the whole reason the summary line exists.
        verify(App.screen.estimate.length > 0, "no estimate was offered");
    }

    function test_an_animation_reports_every_frame_it_found() {
        verify(App.screen.open(Fixture.testImage(4)));
        tryCompare(App.screen, "frameCount", 4);
        verify(App.screen.animated);
        verify(findChild(page, "screenInterval").enabled,
               "the frame interval should be editable for an animation");
        verify(findChild(page, "screenUploadButton").enabled);
    }

    function test_the_interval_is_only_editable_for_an_animation() {
        verify(App.screen.open(Fixture.testImage(1)));
        tryCompare(App.screen, "frameCount", 1);
        verify(!findChild(page, "screenInterval").enabled,
               "a still picture has no frame rate to set");
    }

    function test_changing_the_fit_re_encodes_rather_than_reloading() {
        verify(App.screen.open(Fixture.testImage(4)));
        tryCompare(App.screen, "frameCount", 4);
        let before = App.screen.previewSource;

        App.screen.fitMode = 1;
        tryCompare(App.screen, "fitMode", 1);
        compare(App.screen.frameCount, 4, "the frames should survive a fit change");
        // The preview is written to one path, so Qt would show a cached image
        // without something varying in the URL.
        verify(App.screen.previewSource !== before,
               "the preview did not change when the fit did");
    }

    function test_clearing_puts_the_page_back() {
        verify(App.screen.open(Fixture.testImage(1)));
        tryCompare(App.screen, "frameCount", 1);
        findChild(page, "screenClearButton").clicked();
        tryCompare(App.screen, "frameCount", 0);
        verify(!findChild(page, "screenUploadButton").enabled);
        verify(findChild(page, "screenPreviewPlaceholder").visible);
    }

    function test_the_display_switch_sends_the_off_screen_sub_command() {
        let toggle = findChild(page, "screenAlwaysOn");
        verify(toggle.enabled, "the switch should be live once the state is read");
        let wanted = !App.screen.alwaysOn;

        App.screen.setAlwaysOn(wanted);
        compare(settingSpy.count, 1);
        // Sub-id 9 is the always-on display, whatever the SDK calls the bit.
        compare(settingSpy.signalArguments[0][0], 9);
        compare(settingSpy.signalArguments[0][1], wanted);
        tryCompare(App.screen, "alwaysOn", wanted);
    }

    function test_the_status_bar_switch_is_a_different_sub_command() {
        App.screen.setStatusBarAlwaysOn(true);
        compare(settingSpy.count, 1);
        compare(settingSpy.signalArguments[0][0], 8);
        tryCompare(App.screen, "statusBarAlwaysOn", true);
        // And it left the display alone, which shares the same command.
        verify(!App.screen.busy);
    }

    // Nothing here presses Send. `upload()` is wired to the worker, and the
    // worker's screen path switches the pad into upgrade mode and then waits
    // half a minute for a serial device -- which stalls the worker thread and
    // fails every case after it. What the button does with a busy model is
    // covered headlessly in test_models.py, where no worker is attached.
}
