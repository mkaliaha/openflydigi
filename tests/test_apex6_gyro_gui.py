#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native gyro GUI: independent vendor bytes and guarded profile writes."""
from collections import deque
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_apex6_gui import QT, until
from test_apex6_gyro import replace_record
from flydigi import gpa6_backup, gpa6_config, gpa6_profile, mapping
from flydigi.mock.apex6 import FakeApex6
from gui.app import App
from gui.models.apex6_gyro import KEYS, ENABLE_TYPES
from gui.models.profile import ProfileModel
from tests.qml_harness import TestPad

FIXTURE = json.loads((Path(__file__).parent / "fixtures/apex6-gyro.json").read_text())


class GyroModelTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileModel()
        self.profile.modelCode = "k6"
        self.profile.setSlotCount(4)
        self.profile.setActive(0)
        self.package = gpa6_backup.read_config(FakeApex6())
        self.load()
        self.editor = self.profile.nativeGyro

    def load(self):
        self.profile.profileLoaded(0, gpa6_backup.pack_config(self.package), "Profile 1")

    def test_open_is_lossless_and_legacy_controls_are_inert(self):
        before = self.profile._pack()
        for name in ("target", "key", "secondKey", "enableType", "nodes", "invertX", "invertY",
                     "mouseRadius", "additionalKeys", "axes", "curveIssue", "hasKey"):
            getattr(self.editor, name)
        self.assertTrue(self.editor.editable)
        self.assertEqual(self.profile._pack(), before)
        self.assertFalse(self.profile.dirty)
        self.profile.clear()
        self.profile.modelCode = "k5"
        self.profile.setActive(0)
        self.profile.profileLoaded(0, mapping.pack_config(mapping.MappingConfig(TestPad().blobs[0])), "Legacy")
        before = self.profile._pack()
        self.assertFalse(self.editor.editable)
        self.editor.target, self.editor.key, self.editor.invertX = 2, 3, True
        self.editor.setCoordinate(0, 0, 20)
        self.assertEqual(self.profile._pack(), before)

    def test_all_104_complete_vendor_fixtures_through_gui_controls(self):
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                self.package.mapping = gpa6_profile.MappingConfig(bytes.fromhex(case["before_hex"]))
                self.package.source["format_versions"][1] = 18 if case["name"].startswith("1.8") else 19
                self.load()
                args = case["args"]
                if case["operation"] == "motion":
                    if "target" in args:
                        self.editor.target = mapping.MOTION_TARGET_IDS[args["target"]]
                    if "keys" in args:
                        primary, secondary = ((args["keys"], None) if isinstance(args["keys"], str)
                                              else args["keys"])
                        self.editor.key = KEYS.index(primary)
                        self.editor.secondKey = KEYS.index(secondary)
                    if "enable_type" in args:
                        self.editor.enableType = ENABLE_TYPES.index(mapping.MOTION_ENABLE_TYPE_IDS[args["enable_type"]])
                    for field, prop in (("invert_x", "invertX"), ("invert_y", "invertY"),
                                        ("mouse_window_radius", "mouseRadius")):
                        if field in args:
                            setattr(self.editor, prop, args[field])
                else:
                    self.editor.midpointCount = 0
                    for index, point in ((0, args["start"]), (1, args["end"])):
                        for axis, value in enumerate(point):
                            self.editor.setCoordinate(index, axis, value)
                    self.editor.midpointCount = len(args["points"])
                    for index, point in enumerate(args["points"], 1):
                        for axis, value in enumerate(point):
                            self.editor.setCoordinate(index, axis, value)
                self.assertFalse(self.editor.error)
                actual = gpa6_config.unwrap_blob(self.profile.nativeConfig.mapping.blob, b"XMUL")
                self.assertEqual(actual.hex(), case["after_hex"])

    def test_hidden_slots_mixed_modes_and_unrelated_bytes_survive(self):
        self.package.mapping.set_motion(target="right stick", keys=("m1", "m2"))
        raw = bytearray(self.package.mapping._motion_record()[0])
        raw[5:11] = b"\x11\x22\x33\x44\x55\x66"
        raw[11] = 0xa0
        raw[17:21] = bytes([1, 19, 0, 23])
        raw[21:23] = b"\x03\x00"
        self.package.mapping = replace_record(self.package.mapping, raw)
        self.load()
        self.assertEqual(self.editor.enableType, -1)
        self.assertEqual(self.editor.additionalKeys, "M4 (toggle), Right trigger (hold)")
        self.assertEqual(self.editor.axes, "imu_z / none")
        self.editor.key = KEYS.index("lt")
        self.editor.secondKey = KEYS.index("m5")
        self.editor.invertX = True
        after = self.profile.nativeConfig.mapping._motion_record()[0]
        self.assertEqual(after[5:11], raw[5:11])
        self.assertEqual(after[11], 0xa1)
        self.assertEqual(after[17:23], raw[17:23])
        self.assertEqual(self.editor.enableType, -1)
        self.editor.enableType = 0
        self.assertEqual(self.editor.additionalKeys, "M4 (hold), Right trigger (hold)")
        self.editor.target = 3
        self.assertEqual(self.editor.axes, "imu_x / imu_y")
        self.assertEqual(self.editor.nodes[-1]["y"], 51)

    def test_prefix_layout_and_absent_extension_are_editable_without_normalization(self):
        self.package.mapping.set_motion(target="left stick", keys="m4")
        raw = self.package.mapping._motion_record()[0]
        self.package.mapping = replace_record(self.package.mapping, raw[11:] + raw[:11])
        self.load()
        self.editor.invertY = True
        self.editor.secondKey = KEYS.index("rt")
        self.assertEqual(self.profile.nativeConfig.mapping.motion()["layout"], "prefix_first")
        self.assertEqual(self.profile.nativeConfig.mapping.motion()["keys"], ("m4", "rt"))
        missing = next(c for c in FIXTURE["cases"] if c["name"] == "1.8-missing")
        self.package.mapping = gpa6_profile.MappingConfig(bytes.fromhex(missing["before_hex"]))
        self.package.source["format_versions"][1] = 18
        self.load()
        before = self.profile._pack()
        self.assertFalse(self.profile.nativeConfig.mapping.motion()["present"])
        self.assertTrue(self.editor.editable)
        self.assertEqual(self.profile._pack(), before)
        self.editor.target = 2
        self.assertTrue(self.profile.nativeConfig.mapping.motion()["present"])

    def test_no_keys_secondary_clear_and_invalid_edits(self):
        self.package.mapping.set_motion(target="right stick", keys=("m1", "rt"))
        self.load()
        self.editor.secondKey = 0
        self.assertEqual(self.profile.nativeConfig.mapping.motion()["keys"], ("m1", None))
        for edit in (lambda: setattr(self.editor, "key", 0),
                     lambda: setattr(self.editor, "mouseRadius", 256),
                     lambda: self.editor.setCoordinate(0, 0, 255)):
            before = self.profile._pack()
            edit()
            self.assertTrue(self.editor.error)
            self.assertEqual(self.profile._pack(), before)
        self.editor.invertY = True
        self.assertFalse(self.editor.error)
        raw = bytearray(self.package.mapping._motion_record()[0])
        raw[13:21] = bytes([0, 255]) * 4
        self.package.mapping = replace_record(self.package.mapping, raw)
        self.load()
        self.assertFalse(self.editor.hasKey)
        self.assertEqual(self.editor.enableType, -1)
        self.editor.key = KEYS.index("lt")
        self.assertTrue(self.editor.hasKey)
        self.assertEqual(self.editor.enableType, 0)

    def test_target_changes_reset_curve_but_off_and_same_target_keep_it(self):
        self.editor.target = 2
        self.editor.setCoordinate(1, 0, 200)
        self.editor.midpointCount = 2
        nodes = self.editor.nodes
        self.editor.target = 2
        self.assertEqual(self.editor.nodes, nodes)
        self.editor.target = 0
        self.assertEqual(self.editor.nodes, nodes)
        self.editor.target = 3
        self.assertEqual(self.editor.midpointCount, 0)
        self.assertEqual((self.editor.nodes[-1]["x"], self.editor.nodes[-1]["y"]), (255, 51))

    def test_unknown_duplicate_and_no_imu_records_are_read_only(self):
        self.package.mapping.set_motion(target=2)
        good = self.package.mapping.copy()
        data, _, extensions = gpa6_profile._layout(good.blob)
        _, a, b = next(item for item in extensions if item[0] == 5)
        invalid = (replace_record(good, bytes([255] * 24)),
                   replace_record(good, bytes(23)),
                   gpa6_profile.MappingConfig(data[:b] + data[a:b] + data[b:]))
        for config in invalid:
            self.package.mapping = config
            self.load()
            self.assertFalse(self.editor.editable)
            self.assertTrue(self.editor.issue)
            before = self.profile._pack()
            self.editor.target, self.editor.invertX, self.editor.key = 0, True, 2
            self.assertEqual(self.profile._pack(), before)
            self.profile.nativeButtons.addAction()
            self.assertTrue(self.profile.dirty)
            self.assertTrue(self.editor.issue)
        self.package.mapping = good
        self.package.source["features_raw"] &= ~1
        self.load()
        self.assertFalse(self.editor.editable)
        self.assertIn("does not report", self.editor.issue)

    def test_unordered_curve_blocks_only_curve_edits(self):
        self.package.mapping.set_motion(target=2)
        raw = bytearray(self.package.mapping._motion_record()[0])
        raw[0], raw[2] = 200, 100
        self.package.mapping = replace_record(self.package.mapping, raw)
        self.load()
        self.assertTrue(self.editor.editable)
        self.assertFalse(self.editor.curveEditable)
        self.assertTrue(self.editor.curveIssue)
        self.editor.invertX = True
        self.assertEqual(self.profile.nativeConfig.mapping._motion_record()[0][:11], raw[:11])

    def test_late_ack_keeps_new_edits_and_unrelated_mapping_edit_refreshes(self):
        self.editor.target = 2
        self.profile.write(False)
        slot, submitted = self.profile._native_pending
        self.editor.invertX = True
        self.assertTrue(self.profile.apex6Written(slot, submitted, False))
        self.assertTrue(self.profile.dirty)
        self.profile.nativeButtons.addAction()
        self.assertTrue(self.editor.invertX)
        self.assertEqual(self.editor.target, 2)
        self.profile.clear()
        self.assertFalse(self.editor.editable)
        self.assertFalse(self.profile.apex6Written(slot, submitted, True))


class GyroWorkerTests(unittest.TestCase):
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
        self.editor = self.profile.nativeGyro

    def tearDown(self):
        self.app.shutdown()
        QT.processEvents()

    def apply(self, save=False):
        self.profile.write(save)
        until(lambda: not self.profile.writing)

    def test_apply_save_reload_and_other_domains_unchanged(self):
        before = {ram: self.pad.ram(ram) for ram in (1, 4, 6)}
        self.editor.target = 2
        self.editor.key = KEYS.index("m4")
        self.editor.secondKey = KEYS.index("lt")
        self.editor.enableType = 1
        self.editor.invertY = True
        self.editor.midpointCount = 2
        wanted = bytes(self.profile.nativeConfig.mapping.blob)
        self.apply()
        self.assertFalse(self.errors)
        self.assertFalse(self.profile.dirty)
        self.assertEqual(self.pad.ram(5), wanted)
        self.assertEqual({ram: self.pad.ram(ram) for ram in before}, before)
        self.apply(True)
        self.pad.power_cycle()
        self.assertEqual(self.pad.ram(5), wanted)
        self.app.reload()
        until(lambda: self.profile.loaded and self.editor.target == 2)
        self.assertEqual(self.editor.key, KEYS.index("m4"))
        self.assertTrue(self.editor.invertY)
        self.assertEqual(self.editor.midpointCount, 2)

    def test_stale_uuid_slot_or_mapping_never_writes(self):
        self.editor.target = 2
        for change in ("mapping", "slot", "uid"):
            old_uid, old_mapping = self.pad.uid, self.pad.ram(5)
            if change == "mapping":
                self.pad.configs[0][5] = gpa6_profile.timing(old_mapping, long_ms=4321)
            elif change == "slot":
                self.pad.change_profile(1)
            else:
                self.pad.uid = "ff" * 16
            self.pad.history.clear()
            self.errors.clear()
            self.apply()
            self.assertTrue(self.errors, change)
            self.assertTrue(self.profile.dirty)
            self.assertFalse(any(cmd in (0xa4, 0xa7, 0x53) for cmd, _ in self.pad.history))
            self.pad.uid = old_uid
            self.pad.change_profile(0)
            self.pad.configs[0][5] = old_mapping

    def test_lost_commit_ack_rolls_back_and_retry_retains_edits(self):
        before = self.pad.ram(5)
        self.editor.target = 2
        self.editor.key = KEYS.index("rt")
        wanted = bytes(self.profile.nativeConfig.mapping.blob)
        chunks = (len(wanted) + 15) // 16
        self.pad.faults[0xa4] = deque([None] * (1 + chunks) + ["drop"])
        self.apply()
        self.assertTrue(self.errors)
        self.assertTrue(self.profile.dirty)
        self.assertEqual(self.pad.ram(5), before)
        self.errors.clear()
        self.apply()
        self.assertFalse(self.errors)
        self.assertEqual(self.pad.ram(5), wanted)

    def test_reconnect_keeps_gyro_edits_and_reload_follows_active_slot(self):
        self.editor.target = 2
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
        self.assertEqual(self.editor.target, self.profile.nativeConfig.mapping.motion()["target"])


if __name__ == "__main__":
    unittest.main()
