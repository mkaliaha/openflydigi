#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Captured-wire regressions and transactional Apex 6 settings tests."""
from collections import deque
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import device, gpa6, gpa6_config as config, gpa6_profile as profile, identity, registry
from flydigi.mock.apex6 import FakeApex6
from flydigi.mock.pad import FakePad

HARDWARE = json.loads((ROOT / "tests/fixtures/apex6-hardware.json").read_text())
VENDOR = json.loads((ROOT / "tests/fixtures/apex6-config.json").read_text())


def captured_pad():
    pad = FakeApex6(features=0x9f)
    pad.global_data = bytearray.fromhex(HARDWARE["ram"]["1"]["hex"])
    pad.configs[0] = {ram: bytes.fromhex(HARDWARE["ram"][str(ram)]["hex"]) for ram in (4, 5)}
    pad.stored_configs[0] = dict(pad.configs[0])
    pad.maps[0] = bytearray.fromhex(HARDWARE["ram"]["6"]["hex"])
    return pad


class HardwareTests(unittest.TestCase):
    def test_connection_modes_use_captured_gpa6_values_not_legacy_labels(self):
        for key, mode, label in (("info_reply_hex", 1, "dongle"),
                                 ("wired_info_reply_hex", 0, "wired")):
            pad = captured_pad()
            with patch.object(pad, "send", return_value=[bytes.fromhex(HARDWARE[key])]):
                info = gpa6.Protocol(pad).info()
            self.assertEqual((info["connection_mode"], info["connect_type"]), (mode, label))

    def test_real_descriptor_identity_and_ram_domains(self):
        self.assertEqual(gpa6.report_layout(bytes.fromhex(HARDWARE["descriptor_hex"])),
                         gpa6.ReportLayout(0, 32))
        pad = captured_pad()
        original = pad.send
        def send(report, **kwargs):
            command = report[3]
            if command in (1, 7):
                return [bytes.fromhex(HARDWARE["info_reply_hex" if command == 1 else "versions_reply_hex"])]
            return original(report, **kwargs)
        with patch.object(pad, "send", side_effect=send):
            entry = registry.probe_pad(pad, registry.blank(pad.path, "pad"), deep=True)
            self.assertEqual((entry["firmware"], entry["battery"], entry["battery_steps"]), ("9.0.6.8", 10, 10))
            self.assertIn("battery 10/10", registry.describe(entry))
            self.assertEqual(entry["connect_type"], "dongle")
            self.assertEqual(gpa6.Protocol(pad).versions(), (22, 19, 10))
            snapshot = config.snapshot(pad)
        self.assertEqual(snapshot["blocks"]["lighting"]["ram_id"], 4)
        self.assertEqual(snapshot["blocks"]["mapping"]["ram_id"], 5)
        self.assertEqual(snapshot["blocks"]["lighting"]["decoded"]["base"],
                         {"mode": 4, "config": {"brightness": 25, "speed": 51}})
        self.assertEqual(snapshot["blocks"]["mapping"]["decoded"]["long_press_ms"], 300)
        for ram, block in HARDWARE["ram"].items():
            self.assertEqual(zlib.crc32(bytes.fromhex(block["hex"])), int(block["crc32"], 16))

    def test_duplicate_control_pacing_does_not_throttle_stream_or_legacy(self):
        ctrl = object.__new__(device.Controller)
        ctrl.fd, ctrl.path, ctrl.dialect = 123, "/dev/fake", "gpa6"
        ctrl._threads, ctrl._depth = threading.RLock(), 0
        ctrl.report_layout = gpa6.ReportLayout(0, 32)
        now, writes = [100.0], []
        def write(fd, packet):
            writes.append((packet[3], now[0]))
            return len(packet)
        def sleep(delay):
            now[0] += delay
        with patch.object(ctrl, "_drain"), patch.object(ctrl, "_flock"), \
                patch("flydigi.device.fcntl.flock"), \
                patch("flydigi.device.time.monotonic", side_effect=lambda: now[0]), \
                patch("flydigi.device.time.sleep", side_effect=sleep), \
                patch("flydigi.device.os.write", side_effect=write):
            for command in (4, 4, 7, 0x57, 0x57):
                ctrl.send(ctrl.report_layout.wrap(gpa6.frame(command)), wait=0)
            self.assertAlmostEqual(writes[1][1] - writes[0][1], .1)
            self.assertEqual([at for _, at in writes[1:]], [100.1] * 4)
            ctrl.dialect = "legacy"
            for _ in range(2):
                ctrl.send(device.build(4), wait=0)
            self.assertEqual(writes[-1][1], writes[-2][1])


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.pad = captured_pad()
        self.protocol = gpa6.Protocol(self.pad)

    def test_global_setters_preserve_other_fields(self):
        before = bytes(self.pad.global_data)
        self.assertEqual(config.set_setting(self.pad, "sleep_time", 30)["sleep_time"], 30)
        self.assertEqual(self.pad.global_data[:5], before[:5])
        self.assertEqual(self.pad.global_data[6:], before[6:])
        config.set_setting(self.pad, "sleep_time", 15)
        self.assertEqual(bytes(self.pad.global_data), before)
        for name, value in (("stick_resolution", 7), ("local_mapping", 1), ("sleep_time", -1), ("unknown", 1)):
            history = len(self.pad.history)
            with self.assertRaises(ValueError):
                config.set_setting(self.pad, name, value)
            self.assertEqual(len(self.pad.history), history)

    def test_global_lost_ack_rolls_back(self):
        before = bytes(self.pad.global_data)
        self.pad.faults[0xa0] = deque(["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            config.set_setting(self.pad, "sleep_time", 30)
        self.assertEqual(bytes(self.pad.global_data), before)

    def test_supported_global_fields_round_trip(self):
        before = bytes(self.pad.global_data)
        original = config.global_config(before, 22)
        values = {"sleep_time": 30, "quick_switch": True,
                  "stick_center_auto_adjust": False, "local_mapping": True,
                  "usb_report_rate_hz": 500, "stick_resolution": 10,
                  "allow_third_party": True, "xinput_home": True}
        for name, value in values.items():
            self.assertEqual(config.set_setting(self.pad, name, value)[name], value)
            config.set_setting(self.pad, name, original[name])
            self.assertEqual(bytes(self.pad.global_data), before)

    def test_apex5_is_refused_before_configuration_writes(self):
        pad = FakePad()
        with patch.object(pad, "send", wraps=pad.send) as send:
            for edit in (lambda: config.set_setting(pad, "sleep_time", 30),
                         lambda: config.edit_ram(pad, 5, lambda raw: raw)):
                with self.assertRaises(identity.WrongDevice):
                    edit()
            self.assertTrue(send.call_args_list)
            self.assertTrue(all(call.args[0][3] == 1 for call in send.call_args_list))

    def test_lighting_encoder_matches_independent_vendor_bytes(self):
        for case in VENDOR["lighting"]:
            self.assertEqual(config.encode_lighting(case["input"]).hex(), case["wrapped_hex"])

    def test_remap_preserves_tail_and_other_layers(self):
        before = self.pad.ram(5)
        mapped = profile.remap(before, "m1", "b")
        self.assertEqual(profile.describe(mapped)["layers"][0]["overrides"], {"m1": "b"})
        bare = config.unwrap_blob(before, b"XMUL")
        result = config.unwrap_blob(mapped, b"XMUL")
        self.assertEqual(result[36:45], bytes([16, 1, 0, 0, 0, 0, 1, 0, 0]))
        self.assertEqual(result[45:], bare[36:])
        self.assertEqual(profile.remap(mapped, "m1", "default"), before)
        # Build a second layer with its own mapping and leave its complete raw
        # bytes untouched when inserting an entry in layer zero.
        two = bytearray(bare[:40] + bytes([1, 0, 1, 0, 0, 0, 0, 0])
                        + bytes([17, 1, 0, 0, 0, 0, 2, 0, 0]) + bytes(4) + bare[40:])
        two[24] = 2
        edited = profile.remap(config.wrap_blob(two), "m1", "a")
        self.assertEqual(config.unwrap_blob(edited, b"XMUL")[49:], two[40:])
        self.assertEqual(profile.describe(edited)["layers"][1]["overrides"], {"m2": "x"})

    def test_timing_preserves_every_other_byte(self):
        before = self.pad.ram(5)
        after = profile.timing(before, long_ms=600)
        a, b = config.unwrap_blob(before, b"XMUL"), config.unwrap_blob(after, b"XMUL")
        self.assertEqual(b[:12] + b[14:], a[:12] + a[14:])
        self.assertEqual(profile.describe(after)["long_press_ms"], 600)

    def test_complex_mapping_edits_match_independent_vendor_encoder(self):
        for case in VENDOR["mappingEdits"]:
            before = bytes.fromhex(case["before"]["wrapped_hex"])
            self.assertEqual(profile.remap(before, "m1", "b").hex(), case["after"]["wrapped_hex"])
        for case in VENDOR["mapping"]:
            self.assertEqual(profile.describe(bytes.fromhex(case["wrapped_hex"]))["version"], case["version"])

    def test_malformed_mapping_never_reaches_a_write(self):
        for cut in (27, 31, 39, 41, 100):
            self.pad.configs[0][5] = config.wrap_blob(config.unwrap_blob(self.pad.ram(5), b"XMUL")[:cut])
            with self.assertRaises(gpa6.ProtocolError):
                config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "b"))
            self.assertFalse(any(cmd == 0xa4 for cmd, _ in self.pad.history))
            self.pad = captured_pad()

    def test_applied_edit_survives_selection_and_restart_without_explicit_save(self):
        before = self.pad.ram(5)
        after = config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "b"))
        self.assertEqual(self.pad.ram(5), after)
        self.protocol.select_slot(0)
        self.assertEqual(self.pad.ram(5), after)
        self.protocol.select_slot(1)
        self.protocol.select_slot(0)
        self.assertEqual(self.pad.ram(5), after)
        self.assertFalse(any(cmd == 0xa7 for cmd, _ in self.pad.history))
        self.pad.power_cycle()
        self.assertEqual(self.pad.ram(5), after)
        config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "b"), save=True)
        self.protocol.select_slot(0)
        self.assertEqual(self.pad.ram(5), after)
        self.assertEqual(self.pad.stored_configs[0][5], after)
        self.assertTrue(any(cmd == 0xa7 for cmd, _ in self.pad.history))
        config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "default"), save=True)
        self.pad.power_cycle()
        self.assertEqual(self.pad.ram(5), before)

    def test_ram_read_retries_a_lost_reply_without_skipping_data(self):
        before = self.pad.ram(5)
        self.pad.faults[0xa3] = deque(["drop", None, "drop"])
        self.assertEqual(self.protocol.read_ram(5), before)
        requests = [payload for cmd, payload in self.pad.history if cmd == 0xa3]
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[2], requests[3])

    def test_slot_verification_retries_reads_without_replaying_switch(self):
        self.pad.faults[0xa1] = deque(["drop", None])
        self.protocol.select_slot(1)
        self.assertEqual(self.pad.slot, 1)
        commands = [cmd for cmd, _ in self.pad.history]
        self.assertEqual(commands.count(0xa2), 1)
        self.assertEqual(commands.count(0xa1), 2)
        self.pad.faults[0xa1] = deque(["drop"] * 3)
        with self.assertRaises(gpa6.ReplyTimeout):
            self.protocol.select_slot(0)

    def test_commit_ack_loss_rolls_back_exact_candidate(self):
        before = self.pad.ram(5)
        wanted = profile.remap(before, "m1", "b")
        chunks = (len(wanted) + 15) // 16
        self.pad.faults[0xa4] = deque([None] * (1 + chunks) + ["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            config.edit_ram(self.pad, 5, lambda _: wanted)
        self.assertEqual(self.pad.ram(5), before)
        self.assertIsNone(self.pad.transfer)

    def test_lost_begin_aborts_without_commit(self):
        before = self.pad.ram(5)
        self.pad.faults[0xa4] = deque(["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "b"))
        self.assertEqual(self.pad.ram(5), before)
        self.assertIsNone(self.pad.transfer)

    def test_lost_chunk_ack_is_retried_without_duplicating_data(self):
        self.pad.faults[0xa4] = deque([None, "drop"])
        wanted = config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "b"))
        self.assertEqual(self.pad.ram(5), wanted)
        chunks = [payload for cmd, payload in self.pad.history if cmd == 0xa4 and payload[0] == 1]
        self.assertEqual(chunks[0], chunks[1])

    def test_stale_fingerprint_and_wrong_uuid_refuse_writes(self):
        before = self.pad.ram(5)
        with self.assertRaises(gpa6.ProtocolError):
            self.protocol.write_ram(5, before, expected=(0, 0, 0))
        with self.assertRaises(identity.WrongDevice):
            config.edit_ram(self.pad, 5, lambda b: b, expected_uid="f" * 32)
        self.assertFalse(any(cmd == 0xa4 for cmd, _ in self.pad.history))

    def test_failed_commit_does_not_overwrite_an_external_edit(self):
        external = profile.timing(self.pad.ram(5), double_ms=777)
        original = self.pad.send
        def send(report, **kwargs):
            replies = original(report, **kwargs)
            if report[3] == 0xa4 and report[5] == 2:
                self.pad.configs[0][5] = external
                return []
            return replies
        with patch.object(self.pad, "send", side_effect=send):
            with self.assertRaises(gpa6.ReplyTimeout):
                config.edit_ram(self.pad, 5, lambda b: profile.remap(b, "m1", "b"))
        self.assertEqual(self.pad.ram(5), external)


class CommandTests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"),
                               "--device", "mock:pad0", *args],
                              env={**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6"},
                              capture_output=True, text=True)

    def test_writes_require_opt_in(self):
        for args in (("map", "m1", "b"), ("timing", "--long-ms", "600"),
                     ("set-setting", "sleep_time", "30"), ("slot", "2"),
                     ("save",), ("light", "--save"), ("light", "--brightness", "10")):
            result = self.run_command(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIn("require --experimental", result.stderr)

    def test_invalid_color_is_a_clean_error_and_valid_edit_is_reported(self):
        result = self.run_command("--experimental", "light", "--mode", "solid", "--color", "00 0f ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("six hexadecimal digits", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        result = self.run_command("--experimental", "map", "m1", "b")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["layers"][0]["overrides"], {"m1": "b"})
        self.assertIn("retain edits without explicit save", result.stderr)


if __name__ == "__main__":
    unittest.main()
