// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The DualSense page. Nothing here starts a relay -- that needs an
// authentication and a kernel port -- so what is asserted is the page's own
// behaviour around the switch: that it refuses to offer one that cannot work,
// that it shows the launch option, and that it says the things a person needs
// to know before turning it on.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "DualSense"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    Component {
        id: pageComponent
        DualSensePage {
            anchors.fill: parent
        }
    }

    function init() {
        // The page does not arm the poll -- `Main.qml` does, from the section
        // that is open, because a page Kirigami keeps alive cannot tell when
        // nobody is looking at it any more. A test that shows the page on its
        // own therefore has to say so, exactly as the window would.
        App.dsmode.polling = true;
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the DualSense page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        App.dsmode.polling = false;
    }

    function test_the_switch_reflects_what_the_system_is_doing() {
        let toggle = findChild(page, "dsModeToggle");
        verify(toggle, "no DualSense switch");
        compare(toggle.checked, App.dsmode.running,
                "the switch and the system disagree");
    }

    function test_a_kernel_without_the_module_offers_no_switch_to_press() {
        // The one state where the feature cannot work at all. A switch that
        // does nothing is worse than a sentence saying why.
        let toggle = findChild(page, "dsModeToggle");
        compare(toggle.enabled, App.dsmode.available && !App.dsmode.busy);
        let warning = findChild(page, "dsUnavailable");
        verify(warning, "no message for a kernel without vhci-hcd");
        compare(warning.visible, !App.dsmode.available,
                "the warning and the module state disagree");
    }

    function test_the_page_warns_when_other_software_holds_the_pad() {
        // The relay reads sticks and buttons from evdev, which the third-party
        // handover switches off -- while motion keeps arriving, so the symptom
        // is a pad that tilts and does nothing else. Compare against the model
        // rather than the binding: a binding read once never updates.
        let warning = findChild(page, "dsThirdPartyConflict");
        verify(warning, "no message for the third-party conflict");
        compare(warning.visible, App.device.thirdParty,
                "the warning and the third-party flag disagree");
    }

    function test_the_module_row_says_which_of_three_states_it_is_in() {
        // Loaded, present but not loaded, or absent: they call for different
        // sentences, and "not loaded" is not a problem to fix in advance.
        let row = findChild(page, "dsModuleRow");
        verify(row, "no vhci-hcd row");
        if (App.dsmode.moduleLoaded)
            verify(row.description.indexOf("Loaded") >= 0, row.description);
        else if (App.dsmode.available)
            verify(row.description.indexOf("not loaded") >= 0, row.description);
        else
            verify(row.description.indexOf("Not in this kernel") >= 0,
                   row.description);
    }

    function test_the_launch_option_is_shown_and_copyable() {
        // Part of the feature, not a footnote: nothing can hide the physical
        // pad from a game that enumerates it, so a game sees two pads.
        let row = findChild(page, "dsLaunchOption");
        verify(row, "no launch-option row");
        verify(row.description.indexOf("SDL_GAMECONTROLLER_IGNORE_DEVICES") >= 0,
               row.description);
        verify(row.description.indexOf("0x37d7/0x2501") >= 0, row.description);
        verify(row.description.indexOf("%command%") >= 0, row.description);
        verify(findChild(page, "dsCopyLaunchOption"), "no way to copy it");
    }

    function test_the_launch_option_comes_from_the_backend() {
        // One source of truth. A vendor id restated in QML is the same defect
        // as the battery scale that was restated and wrong.
        let row = findChild(page, "dsLaunchOption");
        verify(row.description.indexOf(App.dsmode.ignoreDevices) === 0,
               row.description + " vs " + App.dsmode.ignoreDevices);
    }

    function test_the_page_warns_about_switching_mid_game() {
        // Games open their stream to the controller's audio device once, at
        // launch. Turning this on while a game runs gives it a pad it will use
        // and an endpoint it will never look for again.
        let row = findChild(page, "dsRestartNote");
        verify(row, "nothing says the game has to be restarted");
        verify(row.description.indexOf("restarted") >= 0, row.description);
    }

    function test_steam_input_is_called_out() {
        let row = findChild(page, "dsSteamInputNote");
        verify(row, "nothing says Steam Input has to be off");
        verify(row.description.indexOf("Xbox") >= 0, row.description);
    }

    function test_the_haptic_switch_is_locked_while_the_relay_runs() {
        // It is read once at startup, so offering to change it mid-session
        // would say something the running relay is not doing.
        let motors = findChild(page, "dsMotorsToggle");
        verify(motors, "no haptic-audio switch");
        compare(motors.enabled, !App.dsmode.running && !App.dsmode.busy);
        compare(motors.checked, App.dsmode.motors);
    }

    function test_the_pad_row_speaks_for_the_apex_5_and_not_for_ds_mode() {
        // Two independent things since the relay learned to outlive a sleeping
        // pad: the virtual DualSense stays attached while the Apex 5 leaves
        // the USB bus. This row is the only place that difference is visible,
        // so it must follow the pad and never the relay. Compared against the
        // model rather than the binding -- a binding read once never updates.
        let row = findChild(page, "dsPadRow");
        verify(row, "no row for the physical pad");
        if (App.dsmode.padConnected)
            verify(row.text.indexOf("feeding") >= 0, row.text);
        else
            verify(row.text.indexOf("asleep") >= 0, row.text);
    }

    function test_a_relay_that_never_reported_a_pad_is_not_called_asleep() {
        // Nothing is running in this test, so the counters are empty -- which
        // is exactly the shape a relay from before `pad=` in the status line
        // produces. Guessing "asleep" from a missing number would be a lie on
        // every one of those.
        compare(App.dsmode.padConnected, true,
                "an empty status must not read as a sleeping pad");
        compare(App.dsmode.padDrops, 0);
    }

    function test_the_running_section_is_hidden_when_nothing_runs() {
        let row = findChild(page, "dsStatusRow");
        // The delegate exists either way; what matters is that its card is not
        // shown, so an idle page does not display counters from nothing.
        if (!App.dsmode.running && !App.dsmode.busy)
            verify(!row.visible || !row.parent.visible,
                   "the running section should be hidden while it is not");
    }
}
