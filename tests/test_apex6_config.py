#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Read-only GPA6 transfer/codec tests, independent of hardware and vendor files."""
from collections import deque
import json
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flydigi import device, gpa6, gpa6_config as config, identity, waveform
from flydigi.mock.apex6 import FakeApex6

FIXTURES = json.loads((ROOT / "tests/fixtures/apex6-config.json").read_text())


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakeApex6()
        self.protocol = gpa6.Protocol(self.pad)

    def mutate_reply(self, callback):
        original = self.pad._reply
        def reply(command, payload, v21=False):
            payload = bytearray(payload)
            if command == 0xa3:
                callback(payload)
            return original(command, payload, v21)
        return patch.object(self.pad, "_reply", side_effect=reply)

    def test_read_all_blocks_and_partial_chunks(self):
        for ram_id in config.RAM_BLOCKS:
            self.assertEqual(self.protocol.read_ram(ram_id), self.pad.ram(ram_id))
        for size in (0, 1, 15, 16, 17, 255, 4095, 4096):
            self.pad.configs[0][4] = bytes(i % 256 for i in range(size))
            self.assertEqual(self.protocol.read_ram(4), self.pad.ram(4))

    def test_full_size_padded_final_chunk(self):
        self.pad.configs[0][4] = b"payload"
        def padded(p):
            if p[0] == 0:
                p.extend(b"\xaa" * (16 - p[7]))
                p[7] = 16
        with self.mutate_reply(padded):
            self.assertEqual(self.protocol.read_ram(4), b"payload")

    def test_larger_blocks_fail_before_index_wrap(self):
        self.pad.configs[0][4] = bytes(4097)
        with self.assertRaisesRegex(gpa6.ProtocolError, "read limit"):
            self.protocol.read_ram(4)
        self.assertEqual(list(self.pad.history), [(0xa3, b"\x01\x04")])

    def test_invalid_ram_ids_do_not_send(self):
        for ram_id in (0, 2, 3, 255, 256, -1):
            with self.assertRaises(ValueError):
                self.protocol.read_ram(ram_id)
        self.assertFalse(self.pad.history)

    def test_chunk_echoes_status_and_size_must_match(self):
        for offset in range(8):
            def corrupt(p):
                if p[0] == 0:
                    p[offset] ^= 1
            with self.subTest(offset=offset), self.mutate_reply(corrupt):
                with self.assertRaises(gpa6.ProtocolError):
                    self.protocol.read_ram(6)

    def test_query_status_id_and_slot_are_checked(self):
        for offset, value in ((0, 0), (1, 2), (2, 4), (3, 255)):
            def corrupt(p):
                if p[0] == 1:
                    p[offset] = value
            with self.subTest(offset=offset), self.mutate_reply(corrupt):
                with self.assertRaises(gpa6.ProtocolError):
                    self.protocol.read_ram(6)

    def test_data_crc_detects_corruption_with_valid_packet_checksum(self):
        def corrupt(p):
            if p[0] == 0:
                p[8] ^= 1
        with self.mutate_reply(corrupt):
            with self.assertRaisesRegex(gpa6.ProtocolError, "CRC32"):
                self.protocol.read_ram(6)

    def test_fingerprint_changes_during_transfer(self):
        def changed(p):
            if p[0] == 0:
                self.pad.maps[0][-1] ^= 1
        # Four chunks would undo four flips; change after the first only.
        def first(p):
            if p[0] == 0 and p[6] == 0:
                changed(p)
        with self.mutate_reply(first):
            with self.assertRaisesRegex(gpa6.ProtocolError, "changed during"):
                self.protocol.read_ram(6)

    def test_profile_change_between_chunks_is_rejected(self):
        def switched(p):
            if p[0] == 0 and p[6] == 0:
                self.pad.change_profile(1)
        with self.mutate_reply(switched):
            with self.assertRaisesRegex(gpa6.ProtocolError, "slot"):
                self.protocol.read_ram(6)

    def test_lost_and_malformed_replies_release_claim(self):
        for fault, error in (("drop", gpa6.ReplyTimeout), ("malformed", gpa6.ProtocolError),
                             ("disconnect", OSError)):
            with self.subTest(fault=fault):
                self.pad.present = True
                self.pad.faults[0xa3] = deque([None, fault])
                with self.assertRaises(error):
                    self.protocol.read_ram(6)
                acquired = []
                def claim():
                    with self.pad.claim(timeout=0):
                        acquired.append(True)
                thread = threading.Thread(target=claim)
                thread.start()
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(acquired, [True])

    def test_compact_and_numbered_replies(self):
        original = self.pad._reply
        def compact(command, payload, v21=False):
            if command != 0xa3:
                return original(command, payload, v21)
            body = bytes((0x5a, 0xa5, command)) + payload
            return b"\x07" + body + bytes([sum(body[2:]) & 255])
        self.pad.report_layout = gpa6.ReportLayout(3, 64, (7,))
        self.protocol = gpa6.Protocol(self.pad)
        with patch.object(self.pad, "_reply", side_effect=compact):
            self.assertEqual(self.protocol.read_ram(1), self.pad.ram(1))


class CodecTests(unittest.TestCase):
    def test_vendor_lighting_and_envelope_fixtures(self):
        for fixture in FIXTURES["lighting"]:
            for field in ("hex", "wrapped_hex"):
                with self.subTest(mode=fixture["input"]["base"]["mode"], field=field):
                    self.assertEqual(config.lighting_config(bytes.fromhex(fixture[field])), fixture["input"])
            wrapped = bytes.fromhex(fixture["wrapped_hex"])
            self.assertEqual(zlib.crc32(wrapped), fixture["crc32"])

    def test_vendor_mapping_headers(self):
        for fixture in FIXTURES["mapping"]:
            for field in ("hex", "wrapped_hex"):
                self.assertEqual(config.mapping_header(bytes.fromhex(fixture[field])), {
                    "version": fixture["version"], "layer_count": 1,
                    "long_press_ms": 513, "double_press_ms": 257,
                    "decoded_scope": "header only"})

    def test_vendor_global_offsets(self):
        fixture = FIXTURES["global"]
        decoded = config.global_config(bytes.fromhex(fixture["hex"]), 22)
        fields = {"display_name": "displayName", "sleep_time": "sleepTime",
                  "quick_switch": "enableQuickSwitch", "stick_center_auto_adjust": "enableJsCenterAutoAdjust",
                  "local_mapping": "enableLocalMap", "usb_report_rate_hz": "usbReturnRateHz",
                  "stick_resolution": "jsResolution", "allow_third_party": "allowThirdparty",
                  "xinput_home": "enableXinputHome"}
        self.assertEqual({fields[k]: v for k, v in decoded.items()}, fixture["expected"])
        with self.assertRaises(gpa6.ProtocolError):
            config.global_config(bytes.fromhex(fixture["hex"]), 23)

    def test_envelope_length_crc_and_magic(self):
        good = bytes.fromhex(FIXTURES["mapping"][0]["wrapped_hex"])
        for offset in (0, 2, 4, 12):
            bad = bytearray(good)
            bad[offset] ^= 1
            with self.assertRaises(gpa6.ProtocolError):
                config.mapping_header(bad)

    def test_lighting_rejects_corruption_truncation_and_unknown_version(self):
        good = bytes.fromhex(FIXTURES["lighting"][1]["hex"])
        for length in range(len(good)):
            with self.assertRaises(gpa6.ProtocolError):
                config.lighting_config(good[:length])
        for offset in range(len(good)):
            bad = bytearray(good)
            bad[offset] ^= 1
            with self.assertRaises(gpa6.ProtocolError):
                config.lighting_config(bad)
        with self.assertRaises(gpa6.ProtocolError):
            config.lighting_config(good + b"\0")

    def test_lighting_unknown_payload_is_not_silently_dropped(self):
        for payload in (bytes((6, 0)), bytes((0, 2)), bytes((0, 0, 0)),
                        bytes((2, 70, 20, 9)) + bytes(28), bytes((1, 12))):
            header = b"LGHT" + struct.pack("<HI", 2, len(payload))
            data = header + struct.pack("<H", sum(header)) + payload + struct.pack("<H", sum(payload))
            with self.assertRaises(gpa6.ProtocolError):
                config.lighting_config(data)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakeApex6()

    def test_snapshot_uses_only_read_commands_for_every_sku(self):
        for sku in (149, 150, 152):
            self.pad.device_type, self.pad.features = sku, 0
            before = self.pad.runtime.copy()
            snapshot = config.snapshot(self.pad, expected_uid=self.pad.uid)
            self.assertEqual(snapshot["uid"], self.pad.uid)
            self.assertEqual(snapshot["active_slot"], 0)
            self.assertEqual(snapshot["info"]["device_type"], sku)
            self.assertEqual(self.pad.runtime, before)
            self.assertTrue(all(cmd in (1, 4, 7, 0xa1, 0xa3) for cmd, _ in self.pad.history))
            for name, block in snapshot["blocks"].items():
                self.assertEqual(bytes.fromhex(block["data_hex"]), self.pad.ram(block["ram_id"]))
                self.assertIn("decoded", block, name)

    def test_unknown_formats_retain_exact_raw_bytes(self):
        self.pad.versions = (23, 42, 3)
        self.pad.configs[0][4] = b"future mapping format"
        self.pad.configs[0][5] = b"future lighting format"
        self.pad.maps[0][0] = 3
        snapshot = config.snapshot(self.pad)
        for block in snapshot["blocks"].values():
            self.assertIn("decode_error", block)
            self.assertEqual(bytes.fromhex(block["data_hex"]), self.pad.ram(block["ram_id"]))

    def test_uuid_mismatch_before_and_after_read(self):
        with self.assertRaises(identity.WrongDevice):
            config.snapshot(self.pad, expected_uid="f" * 32)
        self.assertFalse(any(cmd == 0xa3 for cmd, _ in self.pad.history))
        original = self.pad.ram
        def replaced(ram_id):
            data = original(ram_id)
            self.pad.uid = "f" * 32
            return data
        with patch.object(self.pad, "ram", side_effect=replaced):
            with self.assertRaises(identity.WrongDevice):
                config.snapshot(self.pad)

    def test_profile_changes_are_rejected(self):
        original = self.pad.ram
        def changed(ram_id):
            self.pad.change_profile(1)
            return original(ram_id)
        with patch.object(self.pad, "ram", side_effect=changed):
            with self.assertRaisesRegex(gpa6.ProtocolError, "slot changed"):
                config.snapshot(self.pad)

    def test_recheck_blocks_read_earlier(self):
        original = self.pad._reply
        def changed(command, payload, v21=False):
            if command == 0xa3 and payload[0] == 0 and payload[2] == 6:
                self.pad.global_data[3] += 1
            return original(command, payload, v21)
        with patch.object(self.pad, "_reply", side_effect=changed):
            with self.assertRaisesRegex(gpa6.ProtocolError, "changed during snapshot"):
                config.snapshot(self.pad)

    def test_configuration_version_change_is_rejected(self):
        original = self.pad.ram
        def changed(ram_id):
            self.pad.versions = (23, 9, 2)
            return original(ram_id)
        with patch.object(self.pad, "ram", side_effect=changed):
            with self.assertRaisesRegex(gpa6.ProtocolError, "versions changed"):
                config.snapshot(self.pad)

    def test_a1_state_change_is_rejected_even_with_unchanged_active_ram(self):
        original = self.pad.ram
        def changed(ram_id):
            self.pad.maps[3][10] = 1  # change an inactive slot's CRC only
            return original(ram_id)
        with patch.object(self.pad, "ram", side_effect=changed):
            with self.assertRaisesRegex(gpa6.ProtocolError, "state/versions changed"):
                config.snapshot(self.pad)

    def test_active_output_worker_excludes_snapshot(self):
        errors = []
        def contender():
            try:
                config.snapshot(self.pad)
            except device.DeviceBusy:
                errors.append(True)
        with waveform.GripSession(self.pad, experimental=True):
            thread = threading.Thread(target=contender)
            thread.start()
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [True])

    def test_snapshot_publication_never_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "snapshot.json"
            data = config.snapshot(self.pad)
            config.write_snapshot(target, data)
            self.assertEqual(json.loads(target.read_text()), data)
            with self.assertRaises(FileExistsError):
                config.write_snapshot(target, {"changed": True})
            self.assertEqual(json.loads(target.read_text()), data)
            self.assertEqual(list(Path(directory).iterdir()), [target])
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                config.write_snapshot(link, {})
            with patch.object(config.os, "fsync", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    config.write_snapshot(Path(directory) / "failed.json", data)
            self.assertEqual(set(Path(directory).iterdir()), {target, link})

    def test_cli_mock_snapshot_without_output_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "snapshot.json"
            command = [sys.executable, str(ROOT / "tools/flydigi-apex6"),
                       "--device", "mock:pad0", "snapshot", str(target)]
            env = {**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6"}
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(target.read_text())["mock"])
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("already exists", result.stderr)

    def test_cli_interrupted_capture_does_not_publish_file(self):
        module = runpy.run_path(str(ROOT / "tools/flydigi-apex6"))
        main = module["main"]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "snapshot.json"
            self.pad.faults[0xa3] = deque([None, "drop"])
            with patch.dict(main.__globals__, {"open_selected": lambda _: self.pad}), \
                    patch.object(sys, "argv", ["flydigi-apex6", "snapshot", str(target)]), \
                    patch.object(self.pad, "close") as close:
                with self.assertRaises(gpa6.ReplyTimeout):
                    main()
                close.assert_called_once()
            self.assertFalse(list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
