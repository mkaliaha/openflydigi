#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Macro authoring through Qt models, independent fixtures and fake hardware."""
from collections import deque
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_apex6_gui import QT, until
from flydigi import gpa6_actions as actions, gpa6_backup, gpa6_config, gpa6_macros, gpa6_profile, mapping
from flydigi.mock.apex6 import FakeApex6
from gui.app import App
from gui.models.profile import ProfileModel
from tests.qml_harness import TestPad

CASES = json.loads((Path(__file__).parent / "fixtures/apex6-macros.json").read_text())["cases"]


class MacroModelTests(unittest.TestCase):
    def setUp(self):
        self.model = ProfileModel()
        self.model.modelCode = "k6"
        self.model.setSlotCount(4)
        self.model.setActive(0)
        self.package = gpa6_backup.read_config(FakeApex6())
        self.load()
        self.editor = self.model.nativeMacros

    def load(self, blob=None):
        if blob is not None:
            self.package.mapping = gpa6_profile.MappingConfig(blob)
            version = gpa6_config.mapping_header(blob)["version"]
            self.package.source["format_versions"][1] = int(version.replace(".", ""))
        self.model.profileLoaded(0, gpa6_backup.pack_config(self.package), "Profile 1")

    def macro(self, macro_id=None, layer=None):
        return self.model.nativeConfig.mapping.macro(
            self.editor.macroId if macro_id is None else macro_id,
            layer=self.editor.layer if layer is None else layer)

    def set_steps(self, steps):
        editor = self.editor
        while editor.stepCount:
            editor.removeStep()
        for step in steps:
            editor.addStep()
            editor.delay, editor.event = step["delay"], 1 - step["event"]
            target = actions.decode_target(actions.encode_target(gpa6_macros.step_target(step)))
            editor.target = gpa6_macros.TARGET_TYPES.index(target["type"])
            for i, (field, _, _) in enumerate(actions.TARGETS[target["type"]][1]):
                choices = editor._targets._choices(field)
                value = [v for v, _ in choices].index(target[field]) if choices else target[field]
                editor.setField(i, value)

    def test_gui_edits_match_all_43_complete_vendor_profiles(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                self.load(bytes.fromhex(case["before_hex"]))
                args, editor = case["args"], self.editor
                editor.layer = args.get("layer", 0)
                ids = [m["macro_id"] for m in editor._read()["macros"]]
                if args["macro_id"] not in ids:
                    editor.create(args["macro_id"])
                else:
                    editor.current = ids.index(args["macro_id"])
                if case["operation"] == "clear":
                    editor.remove()
                else:
                    if "macro_type" in args:
                        editor.mode = gpa6_macros.MODES.index(args["macro_type"])
                    if "steps" in args:
                        self.set_steps(args["steps"])
                    if "interval" in args:
                        editor.interval = args["interval"]
                    if "name" in args:
                        editor.name = args["name"]
                    self.assertFalse(editor.validation)
                    editor.commit()
                self.assertFalse(editor.error)
                self.assertFalse(editor.draftDirty)
                actual = gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL")
                self.assertEqual(actual.hex(), case["after_hex"])

    def test_open_selection_and_legacy_calls_do_not_modify_profiles(self):
        self.load(bytes.fromhex(CASES[0]["before_hex"]))
        before = self.model._pack()
        for layer in range(4):
            self.editor.layer = layer
            self.editor.macroNames, self.editor.fields, self.editor.stepNames
        self.assertEqual(self.model._pack(), before)
        self.assertFalse(self.model.dirty)
        self.model.clear()
        self.model.modelCode = "k5"
        self.model.setActive(0)
        self.model.profileLoaded(0, mapping.pack_config(mapping.MappingConfig(TestPad().blobs[0])), "Legacy")
        before = self.model._pack()
        self.editor.create(3)
        self.editor.addStep()
        self.editor.createLayer()
        self.editor.remove()
        self.assertEqual(self.model._pack(), before)
        self.assertFalse(self.editor.editable)

    def test_balanced_draft_is_atomic_and_blocks_all_profile_writes(self):
        editor = self.editor
        editor.create(7)
        before = self.model._pack()
        sent, refused = [], []
        self.model.writeRequested.connect(lambda *a: sent.append(a))
        self.model.saveRefused.connect(refused.append)
        editor.setField(0, 1)  # Press B, but release A until its partner is edited.
        self.assertTrue(editor.draftDirty)
        self.assertTrue(editor.validation)
        editor.commit()
        self.assertEqual(self.model._pack(), before)
        self.model.write(False)
        self.model.write(True)
        self.assertEqual(len(refused), 2)
        self.assertFalse(sent)
        editor.step = 1
        editor.setField(0, 1)
        self.assertTrue(editor.canCommit)
        editor.commit()
        self.assertEqual([s["key"] for s in self.macro()["steps"]], ["b", "b"])
        self.assertFalse(editor.draftDirty)

    def test_dirty_draft_cannot_switch_or_overwrite_another_definition(self):
        editor = self.editor
        editor.create(7)
        editor.duplicate(9)
        editor.step = 1
        editor.delay = 123
        before = self.model._pack()
        editor.current = 0
        editor.layer = 3
        editor.remove()
        editor.create(12)
        editor.duplicate(13)
        self.assertEqual((editor.macroId, editor.layer), (9, 0))
        self.assertEqual(self.model._pack(), before)
        self.assertEqual(editor.delay, 123)
        editor.discard()
        self.assertEqual(editor.delay, 50)
        editor.current = 0
        editor.create(7)
        self.assertIn("already used", editor.error)
        self.assertEqual(self.model._pack(), before)

    def test_copy_limits_ids_layers_and_names(self):
        editor = self.editor
        editor.create(255)
        editor.name = "Комбо 🐾"
        editor.commit()
        editor.duplicate(7)
        self.assertEqual(self.macro()["name"], "Комбо 🐾")
        for i in range(8):
            editor.create(i + 30)
        self.assertFalse(editor.canAdd)
        self.assertEqual(len(editor.macroNames), 10)
        before = self.model._pack()
        editor.create(99)
        self.assertEqual(self.model._pack(), before)
        editor.layer = 3
        editor.createLayer()
        self.assertTrue(editor.canAdd)
        editor.create(255)
        self.assertEqual(len(editor.macroNames), 1)
        self.assertEqual(len(self.model.nativeConfig.mapping.macros(layer=0)), 10)
        self.model.nativeButtons.layer = 3
        self.assertTrue(self.model.nativeButtons.layerExists)

    def test_name_removal_utf8_limits_and_older_formats(self):
        editor = self.editor
        editor.create(0)
        editor.name = "Комбо 🐾"
        editor.commit()
        editor.name = ""
        editor.commit()
        self.assertIsNone(self.macro()["name"])
        before = self.model._pack()
        editor.name = "🐾" * 64
        self.assertTrue(editor.validation)
        editor.commit()
        self.assertEqual(self.model._pack(), before)
        editor.discard()
        self.load(bytes.fromhex(CASES[0]["before_hex"]))
        before = self.model._pack()
        self.assertFalse(editor.namesSupported)
        editor.name = "Not supported"
        self.assertFalse(editor.draftDirty)
        self.assertEqual(self.model._pack(), before)

    def test_unsupported_body_metadata_edits_preserve_exact_record(self):
        self.editor.create(7)
        config = self.model.nativeConfig.mapping
        data, _, records, _ = config._macro_layout(0)
        start, end = records[0][1:]
        raw = bytearray(data)
        raw[start + 1], raw[start + 5] = 99, 0xa5
        raw[start + 2:start + 4] = b"\xff\xff"
        raw[start + 6:start + 11] = b"\x80\x81\x82\x83\x84"
        self.load(gpa6_config.wrap_blob(raw))
        editor = self.editor
        self.assertFalse(editor.editable)
        self.assertEqual(editor.interval, 65535)
        self.assertIn("unknown:128", editor.stepNames[0])
        editor.fields
        before = self.model._pack()
        editor.mode = 0
        editor.delay = 4
        editor.target = 1
        editor.setField(0, 4)
        editor.addStep()
        editor.removeStep()
        self.assertEqual(self.model._pack(), before)
        editor.name = "Only name"
        editor.commit()
        changed = gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL")
        self.assertEqual(changed[:end], raw[:end])
        editor.interval = 77
        editor.commit()
        changed = gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL")
        self.assertEqual(changed[start + 4:end], raw[start + 4:end])
        self.assertEqual(self.macro()["interval"], 77)

    def test_nested_reference_deletion_is_refused_and_reserved_bytes_survive_edits(self):
        editor = self.editor
        editor.create(7)
        editor.create(8)
        config = self.model.nativeConfig.mapping
        data, _, records, _ = config._macro_layout(0)
        first, second = records[0][1], records[1][1]
        raw = bytearray(data)
        raw[first + 5] = 0xa5
        raw[first + 9:first + 11] = b"\xc3\x7f"
        raw[second + 7:second + 11] = bytes([30, 7, 0xa6, 0xb7])
        self.load(gpa6_config.wrap_blob(raw))
        self.assertEqual(editor.macroId, 7)
        before = self.model._pack()
        editor.remove()
        self.assertIn("referenced by another macro", editor.error)
        self.assertEqual(self.model._pack(), before)
        editor.step = 1
        editor.delay = 73
        editor.commit()
        changed = gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL")
        self.assertEqual(changed[first + 5], 0xa5)
        self.assertEqual(changed[first + 9:first + 11], b"\xc3\x7f")
        self.assertEqual(changed[second:], raw[second:])

    def test_ambiguous_macro_ids_show_issue_and_preserve_bytes(self):
        self.editor.create(7)
        self.editor.create(8)
        data, _, records, _ = self.model.nativeConfig.mapping._macro_layout(0)
        raw = bytearray(data)
        raw[records[1][1]] = 7
        self.load(gpa6_config.wrap_blob(raw))
        self.assertIn("duplicate", self.editor.readIssue)
        before = self.model._pack()
        self.editor.create(9)
        self.editor.remove()
        self.editor.fields
        self.assertEqual(self.model._pack(), before)
        self.model.nativeButtons.addAction()
        self.assertEqual(self.editor.readIssue, "duplicate macro IDs in selected layer")

    def test_timeline_bounds_reordering_modes_and_step_limit(self):
        editor = self.editor
        editor.create(7)
        editor.step = 1
        editor.delay = 65535
        self.assertTrue(editor.canCommit)
        editor.step = 0
        editor.delay = 1
        self.assertIn("duration", editor.validation)
        editor.delay = 0
        editor.moveStep(1)
        self.assertIn("release before press", editor.validation)
        editor.moveStep(-1)
        self.assertFalse(editor.validation)
        editor.mode = 3
        self.assertIn("press_hold", editor.validation)
        editor.step = 1
        editor.removeStep()
        self.assertFalse(editor.validation)
        editor.commit()
        self.assertEqual(self.macro()["type"], "press_hold")
        editor.mode = 0
        self.assertIn("missing a release", editor.validation)
        editor.discard()
        for _ in range(40):
            editor.addStep()
        self.assertEqual(editor.stepCount, 32)
        editor.discard()
        editor.removeStep()
        editor.commit()
        self.assertEqual(self.macro()["steps"], [])

    def test_delete_cleans_only_matching_layer_bindings_and_refreshes_editors(self):
        editor = self.editor
        editor.create(7)
        editor.layer = 1
        editor.createLayer()
        editor.create(7)
        config = self.model.nativeConfig.mapping.copy()
        target = dict(type="macro", macro_id=7)
        config.set_activators("a", [dict(type="simple", target=target), dict(type="timing_long", target="y")])
        config.set_activators("a", [dict(type="simple", target=target)], layer=1)
        config.set_trigger_zones("left", [dict(start=0, stop=255, target=target)])
        config.set_stick_zones("left", [dict(start=0, stop=255, dir="up", target=target)])
        self.model.setNativeMapping(config)
        editor.layer = 0
        editor.remove()
        config = self.model.nativeConfig.mapping
        self.assertEqual(config.activators("a"), [dict(type="timing_long", target=dict(type="btn_gamepad", id="y"))])
        self.assertEqual(config.activators("a", layer=1)[0]["target"], target)
        self.assertIsNotNone(config.macro(7, layer=1))
        self.assertFalse(self.model.nativeSticks.zoneNames)
        self.assertFalse(self.model.nativeTriggers.zoneNames)
        self.assertEqual(len(self.model.nativeButtons.actionNames), 1)

    def test_other_edits_and_late_ack_preserve_draft_and_new_macro_changes(self):
        editor = self.editor
        editor.create(7)
        self.model.write(False)
        slot, submitted = self.model._native_pending
        editor.step = 1
        editor.delay = 87
        self.model.nativeButtons.addAction()
        self.assertEqual(editor.delay, 87)
        self.assertTrue(editor.draftDirty)
        self.assertTrue(self.model.apex6Written(slot, submitted, False))
        self.assertTrue(self.model.dirty)
        editor.commit()
        self.assertTrue(self.model.dirty)
        self.assertEqual(self.macro()["steps"][1]["delay"], 87)
        self.assertTrue(self.model.nativeButtons.actionNames)

    def test_external_same_macro_change_refuses_draft_commit(self):
        editor = self.editor
        editor.create(7)
        editor.interval = 20
        config = self.model.nativeConfig.mapping.copy()
        config.set_macro(7, interval=30)
        self.model.setNativeMapping(config)
        self.assertIn("definition changed", editor.validation)
        editor.commit()
        self.assertEqual(self.macro()["interval"], 30)
        editor.discard()
        self.assertEqual(editor.interval, 30)


class MacroWorkerTests(unittest.TestCase):
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

    def test_combined_definition_and_bindings_apply_save_and_delete(self):
        profile = self.app.profile
        before = {ram: self.pad.ram(ram) for ram in (1, 4, 6)}
        editor = profile.nativeMacros
        editor.create(7)
        editor.name = "Combo"
        editor.mode = 2
        editor.interval = 123
        editor.commit()
        profile.nativeButtons.addAction()
        profile.nativeButtons.target = list(actions.TARGETS).index("macro")
        profile.nativeTriggers.addZone()
        profile.nativeTriggers.target = list(actions.TARGETS).index("macro")
        profile.write(False)
        until(lambda: not profile.writing)
        self.assertFalse(self.errors)
        self.assertFalse(profile.dirty)
        config = gpa6_profile.MappingConfig(self.pad.ram(5))
        self.assertEqual(config.macro(7)["name"], "Combo")
        self.assertEqual(config.macro(7)["interval"], 123)
        self.assertEqual(config.activators("a")[0]["target"]["macro_id"], 7)
        self.assertEqual(config.trigger_zones("left")[0]["target"]["macro_id"], 7)
        self.assertEqual({ram: self.pad.ram(ram) for ram in before}, before)
        profile.write(True)
        until(lambda: not profile.writing)
        self.pad.power_cycle()
        self.assertEqual(self.pad.ram(5), config.blob)
        editor.remove()
        profile.write(True)
        until(lambda: not profile.writing)
        config = gpa6_profile.MappingConfig(self.pad.ram(5))
        self.assertIsNone(config.macro(7))
        self.assertIsNone(config.activators("a"))
        self.assertFalse(config.trigger_zones("left"))
        self.assertFalse(self.errors)

    def test_draft_only_reconnect_preserves_it_and_explicit_reload_discards(self):
        profile = self.app.profile
        editor = profile.nativeMacros
        editor.create(7)
        profile.write(True)
        until(lambda: not profile.writing)
        self.assertFalse(profile.dirty)
        editor.step = 1
        editor.delay = 123
        self.assertTrue(profile.dirty)
        self.pad.change_profile(3)
        self.app._read_the_rest(keep_edits=True)
        until(lambda: profile.slots.active == 3)
        self.assertEqual(editor.delay, 123)
        self.assertTrue(editor.draftDirty)
        self.assertFalse(profile.canSaveToFlash)
        self.app.reload()
        until(lambda: profile.cfgId == 3 and profile.loaded)
        self.assertFalse(editor.draftDirty)
        self.assertEqual(editor.macroId, -1)
        self.assertFalse(profile.dirty)

    def test_failed_write_retains_definition_and_releases_pending_state(self):
        profile = self.app.profile
        profile.nativeMacros.create(7)
        before = self.pad.ram(5)
        self.pad.faults[0xa4] = deque(["error"])
        profile.write(False)
        until(lambda: not profile.writing)
        self.assertTrue(self.errors)
        self.assertTrue(profile.dirty)
        self.assertEqual(self.pad.ram(5), before)
        self.assertEqual(profile.nativeMacros.macroId, 7)


if __name__ == "__main__":
    unittest.main()
