// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The lighting page: choosing an effect rewrites the frames, and applying
// sends them. Lighting is its own config on the pad, so it has its own pair of
// write buttons rather than sharing the profile's.

import QtQuick
import QtTest
import Apex5

import "../../gui/qml/pages"

TestCase {
    id: suite
    name: "Lighting"
    when: windowShown
    width: 900
    height: 700
    visible: true

    property var page: null

    SignalSpy {
        id: writeSpy
        target: App.lighting
        signalName: "writeRequested"
    }

    Component {
        id: pageComponent
        LightingPage {
            anchors.fill: parent
        }
    }

    function init() {
        Pad.reset();
        Fixture.resetCounts();
        App.reload();
        // Waiting on `loaded` is not enough: it is still true from the
        // previous case, so the wait returns at once and the read in flight
        // overwrites the edit a moment later.
        tryVerify(() => Fixture.lightingReads >= 1, 5000,
                  "the lighting config never arrived");
        // A fresh page each time: assigning currentIndex on a combo box breaks
        // its binding to the model, so a shared page would carry that damage
        // into every later case.
        page = createTemporaryObject(pageComponent, suite);
        verify(page, "the lighting page did not load");
        waitForRendering(page);
    }

    function cleanup() {
        // Reads are answered on the worker thread. One still in flight when a
        // case ends would land during the next one and quietly replace the
        // config it had just edited, so let it arrive here instead.
        let seen = Fixture.lightingReads;
        wait(150);
        tryVerify(() => Fixture.lightingReads === seen, 2000,
                  "a read was still arriving between cases");
    }

    function test_a_freshly_read_config_has_nothing_to_apply() {
        verify(!App.lighting.dirty, "a config just read should match the pad");
        verify(!findChild(page, "lightingApplyButton").enabled);
        verify(!findChild(page, "lightingSaveButton").enabled);
    }

    function test_the_effect_picker_starts_on_keep() {
        // The stored mode byte uses Space Station's numbering, so on load we
        // cannot say which of our effects produced what is on the pad.
        let combo = findChild(page, "effectCombo");
        verify(combo, "no effect picker");
        compare(App.lighting.effect, 0, "should start on 'keep what is on the pad'");
        compare(combo.currentIndex, 0);
    }

    function test_choosing_an_effect_marks_it_dirty() {
        let combo = findChild(page, "effectCombo");
        let wanted = App.lighting.effectNames.indexOf("Static");
        verify(wanted > 0, "no Static effect");
        let beforeEffect = App.lighting.effect;
        let beforeDirty = App.lighting.dirty;
        combo.currentIndex = wanted;
        combo.activated(wanted);
        verify(App.lighting.dirty, "choosing an effect should change the frames"
               + " | beforeEffect=" + beforeEffect + " beforeDirty=" + beforeDirty
               + " wanted=" + wanted
               + " comboIndex=" + combo.currentIndex
               + " modelEffect=" + App.lighting.effect
               + " colours=" + App.lighting.colours.count
               + " allowed=" + App.lighting.colours.allowed
               + " loaded=" + App.lighting.loaded
               + " | " + App.lighting.info);
    }

    function test_applying_sends_the_frames_to_the_pad() {
        let combo = findChild(page, "effectCombo");
        let wanted = App.lighting.effectNames.indexOf("Static");
        combo.currentIndex = wanted;
        combo.activated(wanted);

        let apply = findChild(page, "lightingApplyButton");
        verify(apply.enabled, "an effect change should enable apply");

        Pad.resetCounters();
        writeSpy.clear();
        mouseClick(apply);
        compare(writeSpy.count, 1, "the button did not ask for a write");

        // Waited on the model, not on the button. `enabled` is a binding, and
        // tryVerify re-running a closure that reads it does not pick the new
        // value up -- it sits until it times out on a write that in fact
        // completed. tryCompare watches the property's notify signal instead.
        tryCompare(App.lighting, "dirty", false, 5000,
                   "the write never completed");
        verify(Pad.packetsReceived > 0, "nothing reached the pad");
        compare(Pad.badChecksums, 0, "the pad rejected a packet");
    }

    function test_a_single_colour_effect_allows_only_one_colour() {
        let combo = findChild(page, "effectCombo");
        let wanted = App.lighting.effectNames.indexOf("Static");
        combo.currentIndex = wanted;
        combo.activated(wanted);
        compare(App.lighting.colours.allowed, 1);
        verify(!App.lighting.colours.canAdd, "one colour is the limit here");
    }

    function test_a_multi_colour_effect_can_gain_and_lose_colours() {
        let combo = findChild(page, "effectCombo");
        let wanted = App.lighting.effectNames.indexOf("Breathing");
        combo.currentIndex = wanted;
        combo.activated(wanted);
        compare(App.lighting.colours.allowed, 5);

        let add = findChild(page, "addColour");
        verify(add && add.enabled, "should be able to add a colour");
        mouseClick(add);
        tryVerify(() => App.lighting.colours.count === 2, 2000, "no colour added");

        let remove = findChild(page, "removeColour");
        verify(remove && remove.enabled, "should be able to remove a colour");
        mouseClick(remove);
        tryVerify(() => App.lighting.colours.count === 1, 2000, "no colour removed");
        verify(!remove.enabled, "the last colour cannot be removed");
    }

    function test_narrowing_the_effect_trims_the_colours() {
        let combo = findChild(page, "effectCombo");
        let many = App.lighting.effectNames.indexOf("Breathing");
        combo.currentIndex = many;
        combo.activated(many);
        mouseClick(findChild(page, "addColour"));
        tryVerify(() => App.lighting.colours.count === 2, 2000);

        let one = App.lighting.effectNames.indexOf("Static");
        combo.currentIndex = one;
        combo.activated(one);
        compare(App.lighting.colours.count, 1, "the extra colour should be dropped");
    }

    function test_rainbow_and_off_use_no_colours() {
        let combo = findChild(page, "effectCombo");
        for (const name of ["Rainbow", "Off"]) {
            let index = App.lighting.effectNames.indexOf(name);
            combo.currentIndex = index;
            combo.activated(index);
            compare(App.lighting.colours.allowed, 0, name + " should use no colours");
        }
    }

    function test_a_swatch_reports_its_colour_as_hex() {
        let combo = findChild(page, "effectCombo");
        let wanted = App.lighting.effectNames.indexOf("Breathing");
        combo.currentIndex = wanted;
        combo.activated(wanted);

        App.lighting.colours.setColour(0, "#ff8000");
        let swatch = findChild(page, "colourSwatch0");
        verify(swatch, "no swatch for the first colour");
        tryVerify(() => swatch.colour === "#ff8000", 2000,
                  "the swatch holds " + swatch.colour);
        compare(Qt.colorEqual(swatch.background.color, "#ff8000"), true,
                "the swatch is not painted the colour it holds");
    }
}
