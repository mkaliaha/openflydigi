#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native feedback GUI: vendor fixtures, shared views and guarded writes."""
from collections import deque
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_apex6_gui import QT, until
import unittest

from flydigi import gpa6_backup, gpa6_config, gpa6_haptics, gpa6_profile, mapping
from flydigi.mock.apex6 import FakeApex6
from gui.app import App
from gui.models.profile import ProfileModel
from tests.qml_harness import TestPad

FIXTURES = json.loads((Path(__file__).parent / "fixtures/apex6-haptics.json").read_text())


class FeedbackModelTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileModel()
        self.profile.modelCode = "k6"
        self.profile.setSlotCount(4)
        self.profile.setActive(0)
        self.package = gpa6_backup.read_config(FakeApex6())
        self.load()
        self.editor = self.profile.nativeHaptics

    def load(self):
        self.profile.profileLoaded(0, gpa6_backup.pack_config(self.package), "Profile 1")

    def test_open_is_lossless_and_legacy_controls_are_inert(self):
        before = self.profile._pack()
        for side in (self.editor.grips, self.editor.left, self.editor.right):
            self.assertTrue(side.editable)
            for prop in ("level", "enabled", "scene", "modeText", "travel", "frequency", "adjustable"):
                getattr(side, prop)
        self.assertEqual(self.profile._pack(), before)
        self.assertFalse(self.profile.dirty)
        self.profile.clear()
        self.profile.modelCode = "k5"
        self.profile.setActive(0)
        self.profile.profileLoaded(0, mapping.pack_config(mapping.MappingConfig(TestPad().blobs[0])), "Legacy")
        before = self.profile._pack()
        for side in (self.editor.grips, self.editor.left, self.editor.right):
            self.assertFalse(side.editable)
            side.enabled, side.level, side.scene = True, 3, 2
        self.assertEqual(self.profile._pack(), before)

    def test_160_complete_vendor_vibration_fixtures(self):
        for case in FIXTURES["cases"]:
            with self.subTest(target=case["target"], version=case["version"], level=case["level"]):
                self.package.mapping = gpa6_profile.MappingConfig(bytes.fromhex(case["before"]["wrapped_hex"]))
                self.package.source["format_versions"][1] = int(case["version"].replace(".", ""))
                features = self.package.source["features_raw"] & ~128
                self.package.source["features_raw"] = features | (128 if case.get("haptic") else 0)
                self.load()
                side = getattr(self.editor, case["target"])
                side.level = case["level"]
                if "enabled" in case:
                    side.enabled = case["enabled"]
                self.assertFalse(self.editor.error)
                self.assertEqual(self.profile.nativeConfig.mapping.blob.hex(), case["after"]["wrapped_hex"])

    def test_60_vendor_scene_combinations_on_each_side(self):
        for case in FIXTURES["scenes"]:
            for name in ("left", "right"):
                with self.subTest(case=case, side=name):
                    self.load()
                    side = getattr(self.editor, name)
                    side.scene = case["id"] + 1
                    side.travel = case["travel"]
                    if case["frequency"] is not None:
                        side.frequency = case["frequency"]
                    self.assertFalse(self.editor.error)
                    self.assertEqual(self.profile.nativeConfig.motors.trigger(name)["params"].hex(), case["params_hex"])
                    self.assertEqual(side.travel, case["travel"])
                    self.assertEqual(side.hasFrequency, case["frequency"] is not None)
                    other = "right" if name == "left" else "left"
                    self.assertEqual(self.profile.nativeConfig.motors.trigger(other), self.package.motors.trigger(other))

    def test_scene_binding_transitions_refresh_response_and_preserve_shift_zones(self):
        analog = self.profile.nativeTriggers
        analog.setCoordinate(0, 0, 20)
        analog.addZone()
        analog.layer = 2
        analog.createLayer()
        analog.addZone()
        analog.layer = 0
        self.editor.left.enabled = True
        self.editor.left.level = 3
        self.editor.left.scene = 5  # Pistol
        self.assertFalse(self.editor.left.enabled)
        self.assertTrue(self.editor.left.strengthZero)
        self.assertEqual(self.profile.nativeConfig.mapping.vibration_curve("trigger", "left")["curve"]["end"], (255, 0))
        self.assertEqual(analog.nodes[0]["x"], 20)
        self.editor.left.scene = 0
        self.assertEqual(analog.nodes[0]["x"], 0)
        self.assertEqual(analog.zoneNames, [])
        analog.layer = 2
        self.assertEqual(len(analog.zoneNames), 1)
        self.editor.left.scene = 5
        self.editor.left.level = 2
        self.assertFalse(self.editor.left.strengthZero)
        self.assertEqual(self.editor.left.scene, 5)
        self.editor.left.enabled = True
        self.assertEqual(self.editor.left.modeText, "Trigger-bound feedback")
        self.editor.left.scene = 0  # Normal clears scenes, not a rumble binding.
        self.assertTrue(self.editor.left.enabled)
        self.editor.left.enabled = False
        self.assertEqual(self.editor.left.modeText, "Normal")

    def test_invalid_edits_preserve_both_domains_and_opaque_records_are_read_only(self):
        self.editor.left.scene = 2
        for prop, value in (("level", 6), ("travel", 0), ("frequency", 4)):
            before = self.profile._pack()
            setattr(self.editor.left, prop, value)
            self.assertTrue(self.editor.error)
            self.assertEqual(self.profile._pack(), before)
        self.package.motors.blob[11] = 42
        self.load()
        self.assertFalse(self.editor.left.editable)
        self.assertTrue(self.editor.left.issue)
        before = self.profile._pack()
        self.editor.left.scene, self.editor.left.enabled = 2, True
        self.assertEqual(self.profile._pack(), before)
        self.editor.right.scene = 5
        self.assertEqual(self.profile.nativeConfig.motors.blob[11], 42)

    def test_malformed_grip_does_not_block_trigger_controls_and_custom_scene_survives_reads(self):
        raw, _, _ = gpa6_profile._layout(self.package.mapping.blob)
        self.package.mapping = gpa6_profile.MappingConfig(gpa6_config.wrap_blob(raw[:-2] + b"\x08\x01x\xff\0"))
        self.package.motors.set_trigger("left", 1, bytes([99]) * 10)
        self.load()
        self.assertFalse(self.editor.grips.editable)
        self.assertTrue(self.editor.left.editable)
        self.assertEqual(self.editor.left.scene, -1)
        self.assertFalse(self.editor.left.adjustable)
        before = self.profile._pack()
        self.editor.left.travel = 3
        self.assertEqual(self.profile._pack(), before)
        self.editor.left.scene = 5
        self.assertEqual(self.editor.left.modeText, "Pistol")
        self.assertFalse(self.editor.grips.editable)

    def test_capability_gates_trigger_controls(self):
        self.package.source["features_raw"] &= ~16
        self.load()
        self.assertFalse(self.editor.left.editable)
        self.assertFalse(self.editor.right.editable)
        before = self.profile._pack()
        self.editor.left.scene, self.editor.right.enabled = 2, True
        self.assertEqual(self.profile._pack(), before)
        self.assertTrue(self.editor.grips.editable)

    def test_late_ack_and_lighting_reply_do_not_confirm_new_motor_edits(self):
        self.editor.left.scene = 5
        self.profile.write(False)
        slot, submitted = self.profile._native_pending
        self.editor.right.scene = 8
        self.assertTrue(self.profile.apex6Written(slot, submitted, False))
        self.assertTrue(self.profile.dirty)
        current = self.profile.nativeConfig
        self.profile.apex6LightingWritten(current.lighting, False, current.source["uid"], slot)
        self.assertTrue(self.profile.dirty)
        stored = gpa6_backup.unpack_config(self.profile.slots.stored(slot))
        self.assertNotEqual(stored.motors.blob, current.motors.blob)
        self.profile.clear()
        self.assertFalse(self.editor.left.editable)
        self.assertFalse(self.profile.apex6Written(slot, submitted, True))


class FeedbackWorkerTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakeApex6()
        self.app = App()
        self.app.start(False)
        self.worker = self.app.thread.worker
        self.worker._controller = lambda: self.pad
        self.errors = []
        self.worker.failed.connect(self.errors.append)
        self.app.reload()
        until(lambda: self.app.profile.loaded and self.app.lighting.loaded)
        self.profile = self.app.profile
        self.editor = self.profile.nativeHaptics

    def tearDown(self):
        self.app.shutdown()
        QT.processEvents()

    def apply(self, save=False):
        self.profile.write(save)
        until(lambda: not self.profile.writing)

    def test_combined_mapping_motors_apply_save_and_lighting_isolation(self):
        before = {ram: self.pad.ram(ram) for ram in (1, 4)}
        self.profile.nativeButtons.addAction()
        self.editor.grips.level = 2
        self.editor.left.scene = 2
        self.editor.left.frequency = 1
        self.editor.right.enabled = True
        wanted = self.profile.nativeConfig.blocks()
        self.apply()
        self.assertFalse(self.errors)
        self.assertFalse(self.profile.dirty)
        self.assertEqual({ram: self.pad.ram(ram) for ram in wanted}, wanted)
        self.assertEqual({ram: self.pad.ram(ram) for ram in before}, before)
        self.assertTrue(any(cmd == 0x53 and payload[0] == 0 for cmd, payload in self.pad.history))
        self.assertFalse(any(cmd == 0x53 and payload[0] == 1 for cmd, payload in self.pad.history))
        self.apply(True)
        self.assertFalse(self.errors)
        self.pad.power_cycle()
        self.assertEqual({ram: self.pad.ram(ram) for ram in wanted}, wanted)

    def test_motor_only_edit_and_lighting_acknowledgement(self):
        before = self.pad.ram(5)
        self.editor.right.enabled = True
        wanted_motors = bytes(self.profile.nativeConfig.motors.blob)
        self.app.lighting.effect = 2
        self.app.lighting.write(False)
        until(lambda: not self.app.lighting.dirty)
        self.assertTrue(self.profile.dirty)
        self.apply()
        self.assertFalse(self.errors)
        self.assertFalse(self.profile.dirty)
        self.assertEqual(self.pad.ram(5), before)
        self.assertEqual(self.pad.ram(6), wanted_motors)

    def test_stale_motor_mapping_slot_and_uuid_refused_before_writes(self):
        self.editor.left.scene = 5
        for change in ("motors", "mapping", "slot", "uid"):
            with self.subTest(change=change):
                old_uid, old_map, old_motor = self.pad.uid, self.pad.ram(5), self.pad.ram(6)
                if change == "motors":
                    self.pad.maps[0][1] = 2
                    self.pad.maps[0][2:5] = gpa6_haptics.BIND_PARAMS
                elif change == "mapping":
                    self.pad.configs[0][5] = gpa6_profile.timing(old_map, long_ms=4321)
                elif change == "slot":
                    self.pad.change_profile(1)
                else:
                    self.pad.uid = "ff" * 16
                self.pad.history.clear()
                self.errors.clear()
                self.apply()
                self.assertTrue(self.errors)
                self.assertTrue(self.profile.dirty)
                self.assertFalse(any(cmd in (0x53, 0xa4, 0xa7) for cmd, _ in self.pad.history))
                self.pad.uid = old_uid
                self.pad.change_profile(0)
                self.pad.configs[0][5] = old_map
                self.pad.maps[0][:] = old_motor

    def test_failed_mapping_and_motor_writes_rollback_and_keep_edits(self):
        self.editor.grips.level = 2
        self.editor.left.scene = 2
        before = {ram: self.pad.ram(ram) for ram in (1, 4, 5, 6)}
        for cmd, fault in ((0xa4, "error"), (0x53, "drop")):
            with self.subTest(cmd=cmd):
                self.pad.faults[cmd] = deque([fault])
                self.errors.clear()
                self.apply()
                self.assertTrue(self.errors)
                self.assertTrue(self.profile.dirty)
                self.assertEqual({ram: self.pad.ram(ram) for ram in before}, before)
        self.errors.clear()
        self.apply()
        self.assertFalse(self.errors)
        self.assertFalse(self.profile.dirty)

    def test_motor_only_reconnect_keeps_edits_and_reload_uses_active_slot(self):
        self.editor.right.enabled = True
        before = self.profile._pack()
        self.pad.change_profile(3)
        self.app._read_the_rest(keep_edits=True)
        until(lambda: self.profile.slots.active == 3)
        self.assertEqual(self.profile._pack(), before)
        self.assertFalse(self.profile.canSaveToFlash)
        self.profile.write(False)
        self.assertFalse(self.profile.writing)
        self.app.reload()
        until(lambda: self.profile.loaded and self.profile.cfgId == 3)
        self.assertFalse(self.profile.dirty)
        self.assertFalse(self.editor.right.enabled)

    def test_unsupported_motor_tail_and_lighting_cannot_enter_profile_write(self):
        old = self.profile._pack()
        for domain in ("lighting", "motors"):
            with self.subTest(domain=domain):
                config = gpa6_backup.unpack_config(old)
                if domain == "motors":
                    config.motors.blob[25] ^= 1
                else:
                    config.lighting = gpa6_config.encode_lighting({"base": {"mode": 1, "config": {
                        "color": {"r": 1, "g": 2, "b": 3}, "brightness": 50}}, "vibration": False})
                self.pad.history.clear()
                self.errors.clear()
                self.worker.write_profile(0, gpa6_backup.pack_config(config), old, False)
                until(lambda: bool(self.errors))
                self.assertFalse(any(cmd in (0x53, 0xa4, 0xa7) for cmd, _ in self.pad.history))


if __name__ == "__main__":
    unittest.main()
