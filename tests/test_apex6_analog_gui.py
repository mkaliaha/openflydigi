#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native analog GUI edits against independent encoders and profile lifecycle."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_apex6_gui import QT, until
from flydigi import gpa6_actions as actions, gpa6_backup, gpa6_config, gpa6_profile, mapping
from flydigi.mock.apex6 import FakeApex6
from gui.app import App
from gui.models.profile import ProfileModel
from tests.qml_harness import TestPad

FIXTURES = Path(__file__).parent / "fixtures"


class AnalogModelTests(unittest.TestCase):
    def setUp(self):
        self.model = ProfileModel()
        self.model.modelCode = "k6"
        self.model.setSlotCount(4)
        self.model.setActive(0)
        self.package = gpa6_backup.read_config(FakeApex6())
        self.load()
        self.sticks, self.triggers = self.model.nativeSticks, self.model.nativeTriggers

    def load(self, blob=None):
        if blob is not None:
            self.package.mapping = gpa6_profile.MappingConfig(blob)
            version = gpa6_config.mapping_header(blob)["version"]
            self.package.source["format_versions"][1] = int(version.replace(".", ""))
        self.model.profileLoaded(0, gpa6_backup.pack_config(self.package), "Profile 1")

    def test_open_and_selection_are_lossless_and_legacy_is_inert(self):
        before = self.model._pack()
        for editor in (self.sticks, self.triggers):
            for side in (0, 1):
                editor.side = side
                self.assertEqual([(n["x"], n["y"]) for n in editor.nodes], [(0, 0), (255, 255)])
                for layer in range(4):
                    editor.layer = layer
                    editor.zoneNames
                    editor.fields
        self.assertEqual(self.model._pack(), before)
        self.assertFalse(self.model.dirty)
        self.model.clear()
        self.model.modelCode = "k5"
        legacy = mapping.MappingConfig(TestPad().blobs[0])
        self.model.setActive(0)
        self.model.profileLoaded(0, mapping.pack_config(legacy), "Legacy")
        before = self.model._pack()
        for editor in (self.sticks, self.triggers):
            editor.setCoordinate(0, 0, 50)
            editor.createLayer()
            editor.addZone()
            editor.clearZones()
            self.assertFalse(editor.responseEditable)
        self.assertEqual(self.model._pack(), before)

    def test_response_edits_match_24_complete_vendor_fixtures(self):
        cases = json.loads((FIXTURES / "apex6-config.json").read_text())["responseCurves"]
        for case in cases:
            with self.subTest(control=case["control"], side=case["side"], version=case["version"]):
                self.load(bytes.fromhex(case["before"]["wrapped_hex"]))
                editor = self.sticks if case["control"] == "stick" else self.triggers
                editor.side = ("left", "right").index(case["side"])
                curve = case["input"]["curve"]
                editor.midpointCount = 0
                editor.setCoordinate(0, 0, curve["start_x"])
                editor.setCoordinate(0, 1, curve["start_y"])
                editor.setCoordinate(1, 0, curve["end_x"])
                editor.setCoordinate(1, 1, curve["end_y"])
                editor.midpointCount = len(curve["mid_points"])
                for i, point in enumerate(curve["mid_points"], 1):
                    editor.setCoordinate(i, 0, point["x"])
                    editor.setCoordinate(i, 1, point["y"])
                if editor.isStick:
                    editor.invertX, editor.invertY, editor.circular = True, True, False
                self.assertFalse(editor.error)
                self.assertEqual(self.model.nativeConfig.mapping.blob.hex(), case["after"]["wrapped_hex"])

    def test_zones_match_88_complete_vendor_fixtures(self):
        cases = json.loads((FIXTURES / "apex6-zones.json").read_text())["cases"]
        for case in cases:
            with self.subTest(case=case["name"]):
                self.load(bytes.fromhex(case["before_hex"]))
                editor = self.sticks if case["control"] == "stick" else self.triggers
                args = case["args"]
                editor.side = ("left", "right").index(args["side"])
                editor.layer = args.get("layer", 0)
                if "zones" in args:
                    zones = args["zones"]
                    if not zones:
                        editor.clearZones()
                    else:
                        while len(editor.zoneNames) < len(zones):
                            editor.addZone()
                        while len(editor.zoneNames) > len(zones):
                            editor.zone = len(editor.zoneNames) - 1
                            editor.removeZone()
                        for i, zone in enumerate(zones):
                            editor.zone = i
                            # Expand the interval before setting either bound.
                            editor.stop = 255
                            editor.start, editor.stop = zone["start"], zone["stop"]
                            if editor.isStick:
                                editor.direction = gpa6_profile.DIRECTIONS.index(zone["dir"])
                            target = actions.decode_target(actions.encode_target(zone["target"]))
                            editor.target = list(actions.TARGETS).index(target["type"])
                            for field_index, (field, _, _) in enumerate(actions.TARGETS[target["type"]][1]):
                                choices = editor._targets._choices(field)
                                value = target[field]
                                if choices:
                                    value = [v for v, _ in choices].index(value)
                                elif field == "turbo_interval_10ms":
                                    value *= 10
                                editor.setField(field_index, value)
                if "angle_mode" in args:
                    editor.angle = gpa6_profile.ANGLE_MODES.index(args["angle_mode"])
                self.assertFalse(editor.error)
                actual = gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL").hex()
                self.assertEqual(actual, case["after_hex"])

    def test_layers_are_shared_and_response_does_not_follow_layer(self):
        seen = []
        self.model.nativeButtons.changed.connect(lambda: seen.append(True))
        self.sticks.layer = 3
        self.sticks.addZone()
        self.assertFalse(self.model.dirty)
        self.sticks.createLayer()
        self.assertTrue(seen)
        self.model.nativeButtons.layer = 3
        self.assertTrue(self.model.nativeButtons.layerExists)
        self.sticks.addZone()
        self.sticks.start = 80
        self.sticks.setCoordinate(0, 0, 30)
        self.sticks.layer = 0
        self.assertEqual(self.sticks.nodes[0]["x"], 30)
        self.assertEqual(self.sticks.zoneNames, [])
        self.sticks.side = 1
        self.assertEqual(self.sticks.nodes[0]["x"], 0)
        self.assertEqual(self.sticks.zoneNames, [])

    def test_invalid_edits_and_limits_preserve_bytes(self):
        for editor in (self.sticks, self.triggers):
            editor.midpointCount = 2
            before = self.model._pack()
            for index, axis, value in [(0, 0, 256), (1, 0, 254), (2, 1, -1)]:
                editor.setCoordinate(index, axis, value)
                self.assertTrue(editor.error)
                self.assertEqual(self.model._pack(), before)
            for _ in range(editor.maximumZones):
                editor.addZone()
            before = self.model._pack()
            editor.addZone()
            self.assertEqual(self.model._pack(), before)
            editor.start = 256
            self.assertTrue(editor.error)
            self.assertEqual(self.model._pack(), before)
            editor.stop = 0
            self.assertEqual(self.model._pack(), before)

    def test_steps_nonmonotonic_outputs_and_presets(self):
        self.sticks.midpointCount = 2
        self.sticks.setCoordinate(1, 0, 100)
        self.sticks.setCoordinate(2, 0, 100)
        self.sticks.setCoordinate(1, 1, 220)
        self.sticks.setCoordinate(2, 1, 20)
        self.assertFalse(self.sticks.error)
        self.assertEqual(self.sticks.preset, 3)
        self.sticks.invertX = True
        for preset in range(3):
            self.sticks.preset = preset
            self.assertEqual(self.sticks.preset, preset)
            self.assertTrue(self.sticks.invertX)
        self.triggers.midpointCount = 1
        self.triggers.setCoordinate(1, 1, 230)
        self.triggers.preset = 0
        self.assertEqual(self.triggers.midpointCount, 0)
        self.assertEqual([(n["x"], n["y"]) for n in self.triggers.nodes], [(0, 0), (255, 255)])

    def test_unsupported_response_is_preserved_while_other_side_and_zones_edit(self):
        self.package.mapping.set_stick("left", invert_x=True)
        raw, _, records = gpa6_profile._layout(self.package.mapping.blob)
        raw = bytearray(raw)
        _, start, stop = next(r for r in records if r[0] == 1)
        raw[start + 6] = 3  # Unknown midpoint count.
        self.load(gpa6_config.wrap_blob(raw))
        self.assertFalse(self.sticks.responseEditable)
        self.assertTrue(self.sticks.responseIssue)
        before = self.model._pack()
        self.sticks.preset = 0
        self.sticks.setCoordinate(0, 0, 20)
        self.assertEqual(self.model._pack(), before)
        self.sticks.addZone()
        self.sticks.side = 1
        self.sticks.setCoordinate(0, 0, 40)
        data, _, updated = gpa6_profile._layout(self.model.nativeConfig.mapping.blob)
        _, a, b = next(r for r in updated if r[0] == 1)
        self.assertEqual(data[a:b], raw[start:stop])

    def test_unknown_zone_is_read_only_and_angle_only_preserves_it(self):
        self.package.mapping.set_stick_zones("left", [dict(start=64, stop=255, dir="up", target="a")])
        sections = {}
        raw, _, _ = gpa6_profile._layout(self.package.mapping.blob, zone_sections=sections)
        start, stop = sections[(0, "stick", "left")]
        raw = bytearray(raw)
        raw[start + 5:start + 10] = b"\xfe\xf0\x01\x02\x03"
        self.load(gpa6_config.wrap_blob(raw))
        self.assertFalse(self.sticks.editable)
        self.assertIn("Unknown", self.sticks.zoneNames[0])
        before = self.model._pack()
        self.sticks.addZone()
        self.sticks.removeZone()
        self.sticks.start = 20
        self.assertEqual(self.model._pack(), before)
        self.sticks.angle = 2
        data = gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL")
        self.assertEqual(data[start + 2:stop], raw[start + 2:stop])
        self.sticks.clearZones()
        self.assertTrue(self.sticks.editable)
        self.assertEqual(self.sticks.zoneNames, [])

    def test_targets_bind_only_existing_layers_and_macros(self):
        self.triggers.addZone()
        before = self.model._pack()
        self.triggers.target = list(actions.TARGETS).index("macro")
        self.assertIn("no macros", self.triggers.error)
        self.assertEqual(self.model._pack(), before)
        self.sticks.layer = 2
        self.sticks.createLayer()
        self.triggers.target = list(actions.TARGETS).index("layer_hold")
        self.assertEqual(self.triggers.fields[0]["choices"], ["Base", "Shift 2"])
        self.assertEqual(self.triggers.fields[0]["selected"], 1)
        config = self.model.nativeConfig.mapping.copy()
        config.set_macro(7, steps=[{"delay": 0, "event": 1, "key": "a"},
                                   {"delay": 10, "event": 0, "key": "a"}])
        self.model.setNativeMapping(config)
        self.triggers.target = list(actions.TARGETS).index("macro")
        self.assertEqual(self.model.nativeConfig.mapping.trigger_zones("left")[0]["target"]["macro_id"], 7)

    def test_late_write_keeps_newer_analog_edits_and_clear_resets_selection(self):
        self.sticks.setCoordinate(0, 0, 15)
        self.model.write(False)
        slot, submitted = self.model._native_pending
        self.triggers.addZone()
        self.assertTrue(self.model.apex6Written(slot, submitted, False))
        self.assertTrue(self.model.dirty)
        self.assertTrue(self.triggers.zoneNames)
        self.sticks.side, self.sticks.layer = 1, 3
        self.model.clear()
        self.assertEqual((self.sticks.side, self.sticks.layer), (0, 0))
        self.assertFalse(self.sticks.responseEditable)
        self.assertEqual(self.triggers.zoneNames, [])


class AnalogWorkerTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakeApex6()
        self.app = App()
        self.app.start(False)
        self.app.thread.worker._controller = lambda: self.pad
        self.errors = []
        self.app.thread.worker.failed.connect(self.errors.append)
        self.app.reload()
        until(lambda: self.app.profile.loaded and self.app.lighting.loaded)

    def tearDown(self):
        self.app.shutdown()
        QT.processEvents()

    def test_combined_buttons_response_and_zones_apply_save_without_other_domains(self):
        before = {ram: self.pad.ram(ram) for ram in (1, 4, 6)}
        profile = self.app.profile
        profile.nativeButtons.addAction()
        profile.nativeSticks.setCoordinate(0, 0, 25)
        profile.nativeSticks.addZone()
        profile.nativeSticks.direction = 5
        profile.nativeTriggers.side = 1
        profile.nativeTriggers.midpointCount = 1
        profile.nativeTriggers.setCoordinate(1, 1, 200)
        profile.nativeTriggers.addZone()
        profile.write(False)
        until(lambda: not profile.writing)
        self.assertFalse(self.errors)
        self.assertFalse(profile.dirty)
        actual = gpa6_profile.MappingConfig(self.pad.ram(5))
        self.assertTrue(actual.activators("a"))
        self.assertEqual(actual.response_curve("stick", "left")["start"], (25, 0))
        self.assertEqual(actual.stick_zones("left")["zones"][0]["dir"], "up_right")
        self.assertEqual(actual.response_curve("trigger", "right")["points"], [(128, 200)])
        self.assertTrue(actual.trigger_zones("right"))
        self.assertEqual({ram: self.pad.ram(ram) for ram in before}, before)
        profile.write(True)
        until(lambda: not profile.writing)
        self.assertFalse(self.errors)
        self.pad.power_cycle()
        self.assertEqual(self.pad.ram(5), actual.blob)

    def test_analog_reconnect_keeps_edits_and_reload_reads_active_slot(self):
        profile = self.app.profile
        profile.nativeSticks.setCoordinate(0, 0, 25)
        profile.nativeTriggers.addZone()
        edited = profile._pack()
        self.pad.change_profile(3)
        self.app._read_the_rest(keep_edits=True)
        until(lambda: profile.slots.active == 3)
        self.assertEqual(profile._pack(), edited)
        self.assertFalse(profile.canSaveToFlash)
        self.app.reload()
        until(lambda: profile.cfgId == 3 and profile.loaded)
        self.assertFalse(profile.dirty)
        self.assertEqual(profile.nativeSticks.nodes[0]["x"], 0)
        self.assertEqual(profile.nativeTriggers.zoneNames, [])


if __name__ == "__main__":
    unittest.main()
