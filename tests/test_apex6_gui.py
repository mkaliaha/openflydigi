#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop foundation against the native fake controller and real Qt models."""
from collections import deque
import os
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="apex6-gui-config-")
os.environ["FLYDIGI_MOCK_BUS"] = "pad:k6"
try:
    from PySide6.QtCore import QCoreApplication
except ImportError:
    print("PySide6 not installed -- skipping Apex 6 GUI tests")
    sys.exit(0)

from flydigi import gpa6, gpa6_actions, gpa6_backup, gpa6_config, gpa6_profile, identity
from flydigi.mock.apex6 import FakeApex6
from gui.app import App
from gui.models.lighting import LightingModel
from gui.models.profile import ProfileModel
from gui.models.apex6_buttons import SOURCES
from gui.worker import DeviceWorker
from tests.qml_harness import TestPad

QT = QCoreApplication.instance() or QCoreApplication([])


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QT.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Timed out waiting for worker/model result")


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakeApex6()
        self.app = App()
        self.app.start(False)
        self.worker = self.app.thread.worker
        self.worker._controller = lambda: self.pad
        self.errors = []
        self.worker.failed.connect(self.errors.append)
        self.app.reload()
        until(lambda: self.app.profile.loaded and self.app.settings.loaded and self.app.lighting.loaded)

    def tearDown(self):
        self.app.shutdown()
        QT.processEvents()

    def test_native_startup_preserves_active_slot_and_legacy_gate(self):
        self.assertTrue(self.app.profile.apex6)
        self.assertEqual(identity.SUPPORTED, ("k5", "f5"))
        self.assertEqual(self.app.device.batterySteps, 10)
        self.assertFalse(self.errors)
        self.assertFalse(any(cmd == 0xa2 for cmd, _ in self.pad.history))
        self.assertIsNone(self.app.profile.config)  # legacy editors are inert
        for operation in (self.worker.reset_all_profiles, self.worker.refresh_screen,
                          lambda: self.worker.apply_vibration({}),
                          lambda: self.worker.write_setting("quick_switch", 0),
                          lambda: self.worker.set_third_party(True)):
            self.pad.history.clear()
            operation()
            self.assertFalse(self.pad.history)

    def test_settings_use_native_values_and_verified_readback(self):
        model = self.app.settings
        self.assertEqual(model.precisionNames, [f"{n}-bit" for n in range(8, 13)])
        for prop, value, field, expected in (
            ("sleepMinutes", 120, "sleep_time", 120),
            ("quickSwitch", False, "quick_switch", False),
            ("autoCalibration", True, "stick_center_auto_adjust", True),
            ("mappingSwitch", True, "local_mapping", True),
            ("precision", 0, "stick_resolution", 8),
            ("reportRate", 1, "usb_report_rate_hz", 250),
            ("xboxHome", True, "xinput_home", True),
            ("thirdParty", True, "allow_third_party", True),
        ):
            self.worker.setting_written.connect(self._setting_done)
            self.setting_done = False
            setattr(model, prop, value)
            until(lambda: self.setting_done)
            self.worker.setting_written.disconnect(self._setting_done)
            self.assertEqual(gpa6_config.global_config(self.pad.ram(1), 22)[field], expected)
            self.assertEqual(getattr(model, prop), value)
        self.assertTrue(model.autoCalibrationUsable)
        self.assertFalse(model.stickDebounceUsable)
        self.assertFalse(self.errors)

    def _setting_done(self, *args):
        self.setting_done = True

    def test_failed_setting_restores_control_and_does_not_replay(self):
        old = self.app.settings.quickSwitch
        self.pad.faults[0xa0] = deque(["error"])
        self.app.settings.quickSwitch = not old
        until(lambda: bool(self.errors))
        self.assertEqual(self.app.settings.quickSwitch, old)
        self.assertEqual(sum(cmd == 0xa0 for cmd, _ in self.pad.history), 1)

    def test_setting_connection_failure_restores_optimistic_control(self):
        self.worker._drop()
        self.pad.present = False
        self.app.settings.quickSwitch = False
        until(lambda: bool(self.errors))
        self.assertTrue(self.app.settings.quickSwitch)

    def test_profile_switch_refreshes_its_lighting_and_save(self):
        blob = gpa6_config.encode_lighting({"base": {"mode": 1, "config": {
            "color": dict(r=12, g=34, b=56), "brightness": 25}}, "vibration": True})
        self.pad.configs[2][4] = blob
        self.app.profile.select(2)
        until(lambda: self.app.profile.loaded and self.app.profile.cfgId == 2 and self.app.lighting.effect == 1)
        self.assertEqual(self.pad.slot, 2)
        self.assertEqual(self.app.lighting.brightness, 25)
        self.assertTrue(self.app.lighting.gripSync)
        self.assertFalse(self.app.lighting.dirty)
        original = [dict(c) for c in self.pad.configs]
        self.pad.history.clear()
        self.app.profile.write(True)
        until(lambda: "save acknowledged" in self.app.device.status)
        self.assertEqual(self.pad.configs, original)
        self.assertEqual(sum(cmd == 0xa7 for cmd, _ in self.pad.history), 1)
        self.assertFalse(any(cmd == 0xa4 for cmd, _ in self.pad.history))

    def test_lighting_apply_then_profile_save_keeps_native_package_current(self):
        model = self.app.lighting
        model.effect = 2
        model.colours.setColour(0, "#102030")
        model.colours.add()
        model.brightness = 40
        model.speed = 72
        model.gripSync = True
        model.write(False)
        until(lambda: model.saveNeeded and not model.dirty)
        actual = gpa6_config.lighting_config(self.pad.ram(4))
        self.assertEqual(actual["base"]["mode"], 2)
        self.assertEqual(actual["base"]["config"]["speed"], 72)
        self.assertEqual(actual["base"]["config"]["colors"][0], dict(r=16, g=32, b=48))
        self.assertEqual(self.app.profile._edited.lighting, self.pad.ram(4))
        self.app.profile.write(True)
        until(lambda: "save acknowledged" in self.app.device.status)
        self.assertFalse(self.errors)

    def test_same_bytes_in_a_different_slot_do_not_accept_stale_lighting(self):
        model = self.app.lighting
        model.effect = 1
        before = self.pad.ram(4)
        self.pad.change_profile(1)
        model.write(True)
        until(lambda: bool(self.errors))
        self.assertEqual(self.pad.ram(4), before)
        self.assertTrue(model.dirty)
        self.assertFalse(any(cmd in (0xa4, 0xa7) for cmd, _ in self.pad.history))

    def test_changed_uuid_rejects_settings_and_lighting(self):
        self.pad.uid = "ff" * 16
        self.app.settings.quickSwitch = False
        until(lambda: bool(self.errors))
        self.assertTrue(self.app.settings.quickSwitch)
        self.assertFalse(any(cmd == 0xa0 for cmd, _ in self.pad.history))

    def test_concurrent_lighting_edit_is_not_overwritten(self):
        model = self.app.lighting
        model.effect = 2
        self.pad.configs[0][4] = gpa6_config.encode_lighting({
            "base": {"mode": 4, "config": {"brightness": 10, "speed": 30}}, "vibration": False})
        before = self.pad.ram(4)
        model.write(False)
        until(lambda: bool(self.errors))
        self.assertEqual(self.pad.ram(4), before)
        self.assertTrue(model.dirty)

    def test_reconnect_preserves_lighting_edits_and_reload_discards_them(self):
        model = self.app.lighting
        model.effect = 2
        model.speed = 88
        self.app._read_the_rest(keep_edits=True)
        self.app.requestInfo.emit()
        until(lambda: self.app.device.connected)
        self.assertTrue(model.dirty)
        self.assertEqual(model.speed, 88)
        self.app.reload()
        until(lambda: model.loaded and not model.dirty)
        self.assertEqual(model.effect, 0)

    def test_reconnect_reads_the_current_slot_instead_of_reselecting_cached_slot(self):
        self.pad.change_profile(3)
        self.pad.history.clear()
        self.app._read_the_rest(keep_edits=True)
        until(lambda: self.app.profile.cfgId == 3 and self.app.profile.loaded)
        self.assertEqual(self.pad.slot, 3)
        self.assertFalse(any(cmd == 0xa2 for cmd, _ in self.pad.history))

    def test_dirty_reconnect_to_other_slot_preserves_edit_but_disables_apply(self):
        self.app.lighting.effect = 2
        self.pad.change_profile(3)
        self.app._read_the_rest(keep_edits=True)
        until(lambda: self.app.profile.slots.active == 3)
        self.assertEqual(self.app.profile.cfgId, 0)
        self.assertTrue(self.app.lighting.dirty)
        self.assertFalse(self.app.lighting.canWrite)
        self.assertEqual(self.pad.slot, 3)

    def test_profile_save_refuses_external_edits(self):
        self.pad.configs[0][4] = gpa6_config.encode_lighting({
            "base": {"mode": 4, "config": {"brightness": 10, "speed": 30}}, "vibration": False})
        before = self.pad.ram(4)
        self.app.profile.write(True)
        until(lambda: bool(self.errors))
        self.assertEqual(self.pad.ram(4), before)
        self.assertFalse(any(cmd in (0xa4, 0xa7) for cmd, _ in self.pad.history))

    def test_save_acknowledgement_loss_is_not_replayed(self):
        self.pad.faults[0xa7] = deque(["drop"])
        self.app.profile.write(True)
        until(lambda: bool(self.errors))
        self.assertEqual(sum(cmd == 0xa7 for cmd, _ in self.pad.history), 1)
        self.assertNotIn("save acknowledged", self.app.device.status)

    def test_switching_protocols_clears_cached_native_state(self):
        self.app.lighting.effect = 2
        legacy = TestPad()
        self.worker._drop()
        self.worker._controller = lambda: legacy
        self.app._backend_changed("k5", "")
        self.app.reload()
        until(lambda: self.app.profile.loaded and self.app.lighting.loaded and self.app.settings.loaded)
        self.assertFalse(self.app.profile.apex6)
        self.assertFalse(self.app.lighting.apex6)
        self.assertFalse(self.app.settings.apex6)
        self.assertFalse(self.app.lighting.dirty)
        self.app.profile.title = "Apex Five"
        self.app.profile.write(False)
        until(lambda: not self.app.profile.dirty)
        from flydigi import mapping
        self.assertEqual(mapping.MappingConfig(legacy.blobs[0]).title, "Apex Five")

    def test_failed_profile_read_can_be_retried(self):
        self.pad.faults[0xa2] = deque(["error"])
        self.app.profile.select(2)
        until(lambda: bool(self.errors))
        self.app.profile.select(2)
        until(lambda: self.app.profile.loaded)
        self.assertEqual(self.pad.slot, 2)

    def test_native_button_apply_and_save_preserve_other_blocks(self):
        before = {ram: self.pad.ram(ram) for ram in (1, 4, 6)}
        editor = self.app.profile.nativeButtons
        editor.source = SOURCES.index("m4")
        editor.addAction()
        editor.activator = 2
        editor.setField(0, list(gpa6_actions.BUTTONS).index("b"))
        self.app.profile.write(False)
        until(lambda: not self.app.profile.writing)
        self.assertFalse(self.errors)
        self.assertFalse(self.app.profile.dirty)
        self.assertTrue(self.app.profile.saveNeeded)
        self.assertEqual(gpa6_profile.MappingConfig(self.pad.ram(5)).activators("m4"),
                         [{"type": "timing_long", "target": {"type": "btn_gamepad", "id": "b"}}])
        self.assertEqual({ram: self.pad.ram(ram) for ram in before}, before)
        self.app.profile.write(True)
        until(lambda: not self.app.profile.writing)
        self.assertFalse(self.errors)
        self.assertFalse(self.app.profile.saveNeeded)
        self.assertEqual(self.pad.stored_configs[0][5], self.pad.ram(5))

    def test_native_apply_refuses_stale_slot_uid_or_mapping(self):
        self.app.profile.nativeButtons.addAction()
        for change in ("slot", "uid", "mapping"):
            with self.subTest(change=change):
                original_uid = self.pad.uid
                original = self.pad.ram(5)
                if change == "slot":
                    self.pad.change_profile(1)
                elif change == "uid":
                    self.pad.uid = "ff" * 16
                else:
                    self.pad.configs[0][5] = gpa6_profile.timing(original, long_ms=4321)
                self.pad.history.clear()
                self.errors.clear()
                self.app.profile.write(False)
                until(lambda: not self.app.profile.writing)
                self.assertTrue(self.errors)
                self.assertTrue(self.app.profile.dirty)
                self.assertFalse(any(cmd in (0xa4, 0xa7) for cmd, _ in self.pad.history))
                self.pad.uid = original_uid
                self.pad.change_profile(0)
                self.pad.configs[0][5] = original

    def test_mapping_write_failure_preserves_edits_and_allows_retry(self):
        self.app.profile.nativeButtons.addAction()
        before = self.pad.ram(5)
        self.pad.faults[0xa4] = deque(["error"])
        self.app.profile.write(False)
        until(lambda: not self.app.profile.writing)
        self.assertTrue(self.errors)
        self.assertEqual(self.pad.ram(5), before)
        self.assertTrue(self.app.profile.dirty)
        self.errors.clear()
        self.app.profile.write(False)
        until(lambda: not self.app.profile.writing)
        self.assertFalse(self.errors)
        self.assertFalse(self.app.profile.dirty)

    def test_connection_failure_releases_pending_profile_write(self):
        self.app.profile.nativeButtons.addAction()
        self.worker._drop()
        self.pad.present = False
        self.app.profile.write(False)
        until(lambda: not self.app.profile.writing)
        self.assertTrue(self.errors)
        self.assertTrue(self.app.profile.dirty)

    def test_reconnect_keeps_mapping_edits_and_reload_discards_them(self):
        self.app.profile.nativeButtons.addAction()
        edited = self.app.profile._pack()
        self.pad.change_profile(3)
        self.app._read_the_rest(keep_edits=True)
        until(lambda: self.app.profile.slots.active == 3)
        self.assertEqual(self.app.profile._pack(), edited)
        self.assertFalse(self.app.profile.canSaveToFlash)
        self.assertTrue(self.app.profile.dirty)
        self.app.profile.write(False)
        self.assertFalse(self.app.profile.writing)
        self.app.reload()
        until(lambda: self.app.profile.cfgId == 3 and self.app.profile.loaded)
        self.assertFalse(self.app.profile.dirty)

    def test_lighting_acknowledgement_does_not_confirm_mapping_edits(self):
        self.app.profile.nativeButtons.addAction()
        edited_mapping = bytes(self.app.profile.nativeConfig.mapping.blob)
        self.app.lighting.effect = 2
        self.app.lighting.write(False)
        until(lambda: not self.app.lighting.dirty)
        self.assertTrue(self.app.profile.dirty)
        self.assertEqual(bytes(self.app.profile.nativeConfig.mapping.blob), edited_mapping)
        self.app.profile.write(False)
        until(lambda: not self.app.profile.writing)
        self.assertFalse(self.errors)
        self.assertFalse(self.app.profile.dirty)
        self.assertEqual(self.pad.ram(5), edited_mapping)


class ButtonModelTests(unittest.TestCase):
    def setUp(self):
        self.model = ProfileModel()
        self.model.modelCode = "k6"
        self.model.setSlotCount(4)
        self.model.setActive(0)
        self.package = gpa6_backup.read_config(FakeApex6())
        self.load(self.package)
        self.editor = self.model.nativeButtons

    def load(self, package):
        self.model.profileLoaded(0, gpa6_backup.pack_config(package), "Profile 1")

    def test_native_source_names_do_not_use_legacy_trigger_or_home_ids(self):
        self.assertEqual(len(self.editor.sourceNames), 20)
        self.assertFalse(set(SOURCES) & {"lt", "rt", "home", "c", "z"})
        self.assertIn("Select", self.editor.sourceNames)

    def test_layer_create_and_shift_binding(self):
        self.editor.layer = 3
        self.assertFalse(self.editor.layerExists)
        self.editor.addAction()
        self.assertFalse(self.model.dirty)
        self.editor.createLayer()
        self.assertTrue(self.editor.layerExists)
        self.editor.addAction()
        self.editor.layer = 0
        self.editor.source = SOURCES.index("m4")
        self.editor.addAction()
        self.editor.target = list(gpa6_actions.TARGETS).index("layer_hold")
        self.assertEqual(self.model.nativeConfig.mapping.activators("m4")[0]["target"],
                         {"type": "layer_hold", "layer_id": 3})
        self.assertEqual(self.editor.field(0)["choices"], ["Base", "Shift 3"])
        self.assertTrue(self.model.nativeConfig.mapping.activators("a", layer=3))

    def test_five_actions_empty_override_and_reset_are_distinct(self):
        self.editor.source = SOURCES.index("m4")
        for _ in range(6):
            self.editor.addAction()
        self.assertEqual(len(self.editor.actionNames), 5)
        for _ in range(5):
            self.editor.removeAction()
        self.assertEqual(self.model.nativeConfig.mapping.activators("m4"), [])
        self.editor.resetButton()
        self.assertIsNone(self.model.nativeConfig.mapping.activators("m4"))
        self.assertFalse(self.model.dirty)

    def test_each_typed_output_and_press_behavior(self):
        self.model.nativeConfig.mapping.ensure_layer(1)
        self.model.nativeConfig.mapping.set_macro(3, steps=[
            {"key": "a", "event": 1, "delay": 0},
            {"key": "a", "event": 0, "delay": 10}], name="Test")
        self.model.setNativeMapping(self.model.nativeConfig.mapping)
        self.editor.addAction()
        for index, kind in enumerate(gpa6_actions.TARGETS):
            self.editor.target = index
            self.assertFalse(self.editor.error, kind)
            self.assertEqual(self.model.nativeConfig.mapping.activators("a")[0]["target"]["type"], kind)
        for index, kind in enumerate(gpa6_actions.ACTIVATORS):
            self.editor.activator = index
            self.assertEqual(self.model.nativeConfig.mapping.activators("a")[0]["type"], kind)

    def test_missing_macro_and_invalid_values_are_atomic(self):
        self.editor.addAction()
        before = self.model._pack()
        self.editor.target = list(gpa6_actions.TARGETS).index("macro")
        self.assertIn("no macros", self.editor.error)
        self.assertEqual(self.model._pack(), before)
        self.editor.target = list(gpa6_actions.TARGETS).index("simulate_mouse_move")
        before = self.model._pack()
        self.editor.setField(0, -129)
        self.assertTrue(self.editor.error)
        self.assertEqual(self.model._pack(), before)
        self.editor.longPress = 0
        self.assertEqual(self.model._pack(), before)
        self.editor.target = list(gpa6_actions.TARGETS).index("turbo_gamepad")
        before = self.model._pack()
        self.editor.setField(1, 15)
        self.assertIn("multiple of 10", self.editor.error)
        self.assertEqual(self.model._pack(), before)
        self.editor.setField(1, 170)
        self.assertEqual(self.editor.field(1)["value"], 170)
        self.assertEqual(self.model.nativeConfig.mapping.activators("a")[0]["target"]["turbo_interval_10ms"], 17)

    def test_timings_are_profile_wide(self):
        self.editor.longPress = 987
        self.editor.doublePress = 234
        self.editor.layer = 2
        self.editor.longPress = 987
        self.assertEqual(self.editor.layer, 2)
        self.editor.createLayer()
        self.assertEqual((self.editor.longPress, self.editor.doublePress), (987, 234))

    def test_gui_edits_match_complete_independent_vendor_fixtures(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/apex6-activators.json").read_text())
        checked = 0
        for case in fixture["cases"]:
            args = case["args"]
            if case["operation"] != "bind" or args.get("source") not in SOURCES:
                continue
            entries = args["activators"]
            if not entries:  # Resets and empty overrides have separate coverage.
                continue
            self.package.mapping = gpa6_profile.MappingConfig(bytes.fromhex(case["before_hex"]))
            version = gpa6_config.mapping_header(self.package.mapping.blob)["version"]
            self.package.source["format_versions"][1] = int(version.replace(".", ""))
            self.load(self.package)
            self.editor.layer = args.get("layer", 0)
            self.editor.source = SOURCES.index(args["source"])
            while len(self.editor.actionNames) > len(entries):
                self.editor.removeAction()
            while len(self.editor.actionNames) < len(entries):
                self.editor.addAction()
            for action_index, entry in enumerate(entries):
                self.editor.action = action_index
                self.editor.activator = gpa6_actions.ACTIVATORS.index(entry["type"])
                target = gpa6_actions.decode_target(gpa6_actions.encode_target(entry["target"]))
                self.editor.target = list(gpa6_actions.TARGETS).index(target["type"])
                for i, (field, _, _) in enumerate(gpa6_actions.TARGETS[target["type"]][1]):
                    choices = self.editor._choices(field)
                    value = [v for v, _ in choices].index(target[field]) if choices else target[field]
                    if field == "turbo_interval_10ms":
                        value *= 10
                    self.editor.setField(i, value)
                self.assertFalse(self.editor.error, case["name"])
            self.assertEqual(gpa6_config.unwrap_blob(self.model.nativeConfig.mapping.blob, b"XMUL").hex(),
                             case["after_hex"], case["name"])
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_unknown_actions_are_read_only_until_explicit_reset(self):
        config = self.package.mapping
        config.set_activators("a", [{"type": "simple", "target": "b"}])
        raw, layers = gpa6_profile._layers(config.blob)
        raw = bytearray(raw)
        entry = next(e for e in layers[0][3] if e[0] == 0)
        raw[entry[1] + 4:entry[1] + 9] = b"\x81\x82\x83\x84\x85"
        config.blob[:] = gpa6_config.wrap_blob(raw)
        self.load(self.package)
        self.assertFalse(self.editor.editable)
        self.assertIn("Unknown", self.editor.actionNames[0])
        before = self.model._pack()
        self.editor.addAction()
        self.editor.removeAction()
        self.editor.activator = 2
        self.assertEqual(self.model._pack(), before)
        self.editor.source = SOURCES.index("m4")
        self.editor.addAction()
        self.assertEqual(self.model.nativeConfig.mapping.activators("a")[0]["type"], 0x81)
        self.editor.source = 0
        self.editor.resetButton()
        self.assertTrue(self.editor.editable)

    def test_pending_write_confirms_submitted_bytes_and_blocks_duplicates(self):
        requests = []
        self.model.writeRequested.connect(lambda *args: requests.append(args))
        self.editor.addAction()
        self.model.write(False)
        self.model.write(True)
        self.assertEqual(len(requests), 1)
        slot, blob, _, _ = requests[0]
        self.editor.activator = 2
        self.assertTrue(self.model.apex6Written(slot, blob, False))
        self.assertTrue(self.model.dirty)
        self.assertFalse(self.model.writing)
        self.assertEqual(gpa6_backup.unpack_config(self.model.slots.stored(0)).mapping.activators("a")[0]["type"], "simple")
        self.assertEqual(self.model.nativeConfig.mapping.activators("a")[0]["type"], "timing_long")

    def test_old_acknowledgement_after_device_switch_is_ignored(self):
        self.editor.addAction()
        self.model.write(False)
        slot, blob = self.model._native_pending
        self.model.clear()
        self.model.setActive(0)
        self.package.source["uid"] = "ff" * 16
        self.load(self.package)
        self.assertFalse(self.model.apex6Written(slot, blob, True))
        self.assertEqual(self.model.nativeConfig.source["uid"], "ff" * 16)


class ValueTests(unittest.TestCase):
    def test_k6_identity_on_a_legacy_interface_is_not_admitted(self):
        worker = DeviceWorker()
        pad = TestPad(device_type=150)
        errors = []
        worker.failed.connect(errors.append)
        with patch("gui.worker.registry.open_pad", return_value=pad):
            worker.load_profile(0)
        self.assertTrue(errors)
        self.assertEqual(pad.asked, [1])
        self.assertIsNone(worker._ctrl)

    def test_gui_native_selection_leaves_daemon_preference_alone(self):
        from flydigi import prefs, registry
        from gui.models.devices import DevicesModel
        saved = prefs.Prefs(Path(tempfile.mkdtemp()) / "prefs.json")
        saved.set_primary_pad("uid:legacy")
        model = DevicesModel(settings=saved)
        pad = FakeApex6()
        entry = registry.probe_pad(pad, registry.blank(pad.path, "pad"), deep=True)
        model.devicesReceived([entry])
        model.select(0)
        self.assertEqual(saved.primary_pad(), "uid:legacy")
        self.assertTrue(model.data(model.index(0, 0), model.SupportedRole))
        self.assertIn("experimental configuration", model.data(model.index(0, 0), model.DetailRole))

    def test_acknowledgement_keeps_edits_made_during_upload(self):
        pad = FakeApex6()
        model = LightingModel()
        model.apex6ConfigLoaded(pad.ram(4), pad.uid, 0)
        model.setActive(0)
        model.effect = 1
        submitted = bytes(model._edited.blob)
        model.brightness = 33
        model.apex6Written(submitted, False, pad.uid, 0)
        self.assertTrue(model.dirty)
        self.assertEqual(model.brightness, 33)
        model.apex6Written(submitted, True, "ff" * 16, 0)
        self.assertTrue(model.saveNeeded)

    def test_all_effects_and_eight_colours(self):
        pad = FakeApex6()
        model = LightingModel()
        model.apex6ConfigLoaded(pad.ram(4), pad.uid, 0)
        for mode in range(6):
            model.effect = mode
            while model.colours.canAdd:
                model.colours.add()
            decoded = gpa6_config.lighting_config(model._edited.blob)
            self.assertEqual(decoded["base"]["mode"], mode)
            self.assertEqual(model.colours.allowed, 8 if mode in (2, 3) else 1 if mode == 1 else 0)
            self.assertEqual(model.hasSpeed, mode in (2, 3, 4, 5))

    def test_legacy_worker_cannot_write_native_data_and_native_worker_cannot_send_legacy_screen(self):
        worker = DeviceWorker()
        pad = TestPad()
        worker._controller = lambda: pad
        failures = []
        worker.failed.connect(failures.append)
        worker.write_apex6_lighting(b"", b"", True, "ff" * 16, 0)
        self.assertTrue(failures)
        self.assertFalse(pad.asked)
        native = FakeApex6()
        worker._controller = lambda: native
        with patch("gui.worker.screen_ota.find_port", return_value=None):
            worker.upload_screen([], 100, False)
        self.assertFalse(any(cmd == 31 for cmd, _ in native.history))


if __name__ == "__main__":
    unittest.main()
