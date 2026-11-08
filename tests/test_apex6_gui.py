#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop foundation against the native fake controller and real Qt models."""
from collections import deque
import os
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

from flydigi import gpa6, gpa6_backup, gpa6_config, identity
from flydigi.mock.apex6 import FakeApex6
from gui.app import App
from gui.models.lighting import LightingModel
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
