#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Profile packages/library and multi-domain failure recovery, without hardware."""
from collections import deque
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_backup as backup, gpa6_config, gpa6_haptics, gpa6_profile, identity
from test_apex6_editing import captured_pad
from flydigi.mock.pad import FakePad


def state(pad):
    return (pad.slot, bytes(pad.global_data), copy.deepcopy(pad.configs), copy.deepcopy(pad.maps))


def candidate(config):
    result = config.copy()
    result.mapping.blob = bytearray(gpa6_profile.remap(result.mapping.blob, "m4", "b"))
    result.lighting = gpa6_config.encode_lighting({"base": {"mode": 1,
        "config": {"color": {"r": 12, "g": 34, "b": 56}, "brightness": 25}}, "vibration": False})
    for side, scene in (("left", "pistol"), ("right", "rifle")):
        result.motors.set_trigger(side, 1, gpa6_haptics.scene_params(scene))
    result.name = "Test profile"
    return result


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.pad = captured_pad()
        self.config = backup.read_config(self.pad, name="Saved profile")

    def test_read_is_read_only_and_excludes_globals(self):
        self.assertEqual(set(self.config.blocks()), {4, 5, 6})
        self.assertTrue(all(cmd in (1, 4, 7, 0xa1, 0xa3) for cmd, _ in self.pad.history))
        for ram, raw in self.config.blocks().items():
            self.assertEqual(raw, self.pad.ram(ram))

    def test_packed_copy_is_independent_and_slot_is_not_source_slot(self):
        packed = backup.pack_config(self.config)
        restored = backup.unpack_config(packed, 3)
        self.assertEqual(restored.blocks(), self.config.blocks())
        self.assertEqual((restored.cfg_id, restored.mapping.cfg_id, restored.source["slot"]), (3, 3, 0))
        copied = restored.copy()
        copied.motors.blob[-1] ^= 1
        copied.source["firmware"][0] = 1
        self.assertNotEqual(copied.blocks(), restored.blocks())
        self.assertEqual(backup.pack_config(restored), packed)

    def test_independent_vendor_profiles_survive_pack_restore_and_recapture(self):
        count = 0
        for suite in ("gyro", "macros", "zones", "haptics"):
            cases = json.loads((ROOT / f"tests/fixtures/apex6-{suite}.json").read_text())["cases"]
            for index, case in enumerate(cases):
                with self.subTest(suite=suite, case=case.get("name", index)):
                    pad = captured_pad()
                    raw = bytes.fromhex(case["after"]["wrapped_hex"] if suite == "haptics" else case["after_hex"])
                    version = int(gpa6_config.mapping_header(raw)["version"].split(".")[1]) + 10
                    pad.versions = (22, version, 10)
                    # Make the baseline format agree without using the tested encoder.
                    pad.configs[0][5] = bytes.fromhex(case["before"]["wrapped_hex"] if suite == "haptics" else case["before_hex"])
                    config = backup.read_config(pad)
                    config.mapping = gpa6_profile.MappingConfig(raw)
                    restored = backup.unpack_config(backup.pack_config(config))
                    backup.write_config(pad, restored)
                    self.assertEqual(pad.ram(5), raw)
                    self.assertEqual(backup.read_config(pad).blocks(), restored.blocks())
                    count += 1
        self.assertEqual(count, 395)

    def test_unknown_mapping_tail_and_motor_reserved_bytes_survive(self):
        raw = gpa6_config.unwrap_blob(self.config.mapping.blob, b"XMUL")
        self.config.mapping.blob = bytearray(gpa6_config.wrap_blob(raw[:-2] + b"\x71\x03xyz\xff\0"))
        self.config.motors.blob[-3:] = b"abc"
        self.assertEqual(backup.unpack_config(backup.pack_config(self.config)).blocks(), self.config.blocks())

    def test_truncation_and_corruption_rejected(self):
        raw = backup.pack_config(self.config)
        for end in (0, 1, 10, len(raw) // 2, len(raw) - 2):
            with self.subTest(end=end), self.assertRaises(ValueError):
                backup.unpack_config(raw[:end])
        doc = json.loads(raw)
        for name in backup.DOMAINS:
            broken = copy.deepcopy(doc)
            broken["blocks"][name]["data_hex"] = "00" + broken["blocks"][name]["data_hex"][2:]
            with self.assertRaisesRegex(ValueError, "checksum"):
                backup.unpack_config(json.dumps(broken))

    def test_schema_metadata_limits_and_duplicate_keys_rejected(self):
        doc = json.loads(backup.pack_config(self.config))
        mutations = [lambda d: d.update(schema="future"), lambda d: d["blocks"].pop("motors"),
            lambda d: d["blocks"].update(global_data={}), lambda d: d["source"].update(uid="abc"),
            lambda d: d["source"].update(slot=True), lambda d: d["source"].update(format_versions=[22, 18, 10]),
            lambda d: d["source"].update(firmware=[]), lambda d: d.update(name=""),
            lambda d: d.update(name="x" * 121), lambda d: d.update(name="line\nbreak"),
            lambda d: d["blocks"]["mapping"].update(length=True), lambda d: d.update(blocks=None)]
        for change in mutations:
            broken = copy.deepcopy(doc)
            change(broken)
            with self.assertRaises(ValueError):
                backup.unpack_config(json.dumps(broken))
        with self.assertRaises(ValueError):
            backup.unpack_config(b" " * (backup.MAX_PACKAGE_SIZE + 1))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            backup.unpack_config('{"schema":"a","schema":"b"}')
        with self.assertRaises(ValueError):
            backup.unpack_config(json.dumps(gpa6_config.snapshot(self.pad)))
        with self.assertRaises(ValueError):
            backup.unpack_config(bytes(840))

    def test_valid_checksum_cannot_hide_invalid_domain(self):
        doc = json.loads(backup.pack_config(self.config))
        for name in backup.DOMAINS:
            broken = copy.deepcopy(doc)
            raw = bytes.fromhex(broken["blocks"][name]["data_hex"])
            raw = b"\xff" + raw[1:]
            broken["blocks"][name].update(data_hex=raw.hex(), sha256=hashlib.sha256(raw).hexdigest())
            with self.assertRaises(ValueError):
                backup.unpack_config(json.dumps(broken))


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.pad = captured_pad()
        self.original = backup.read_config(self.pad)
        self.wanted = candidate(self.original)
        self.initial = state(self.pad)
        self.pad.history = deque(maxlen=20000)

    def assertOriginal(self):
        self.assertEqual(state(self.pad), self.initial)

    def test_complete_restore_recovery_capture_save_and_roundtrip(self):
        recovery = []
        def capture(config):
            self.assertOriginal()
            recovery.append(backup.pack_config(config))
        result = backup.write_config(self.pad, self.wanted, save=True, expected_uid=self.pad.uid,
                                     expected_slot=0, before_write=capture)
        self.assertEqual(result.blocks(), self.wanted.blocks())
        self.assertEqual(len(recovery), 1)
        self.assertEqual(self.pad.global_data, self.initial[1])
        self.assertEqual(self.pad.configs[1:], self.initial[2][1:])
        self.assertEqual(self.pad.maps[1:], self.initial[3][1:])
        writes = [(cmd, payload) for cmd, payload in self.pad.history if cmd in (0xa4, 0xa7, 0x53)]
        self.assertEqual(writes[0:2], [(0x53, b"\0\0\0"), (0x53, b"\0\1\0")])
        self.assertEqual(writes[-1], (0xa7, b"\1\0"))
        self.assertTrue(all(payload[0] == 0 for cmd, payload in writes if cmd == 0x53))
        self.assertTrue(all(payload[1] in (4, 5) for cmd, payload in writes if cmd == 0xa4 and payload[0] == 0))
        backup.write_config(self.pad, backup.unpack_config(recovery[0]), expected_config=self.wanted, save=True)
        self.assertOriginal()

    def test_noop_does_not_write_or_create_recovery_but_explicit_save_works(self):
        def fail(_):
            raise AssertionError("no-op backup callback")
        backup.write_config(self.pad, self.original, before_write=fail)
        self.assertFalse(any(cmd in (0xa4, 0xa7, 0x53) for cmd, _ in self.pad.history))
        backup.write_config(self.pad, self.original, save=True)
        self.assertIn((0xa7, b"\1\0"), self.pad.history)

    def test_read_only_timeouts_retry_but_checks_still_reject_changes(self):
        self.pad.faults[4] = deque(["drop", "drop"])
        self.assertEqual(backup.read_config(self.pad).blocks(), self.original.blocks())
        # The first identity guard after recovery publication lost its reply
        # on hardware. Two lost replies must recover without replaying writes.
        def capture(_):
            self.pad.faults[4] = deque(["drop", "drop"])
        backup.write_config(self.pad, self.wanted, before_write=capture)
        self.assertEqual(backup.read_config(self.pad).blocks(), self.wanted.blocks())
        writes = [p for cmd, p in self.pad.history if cmd == 0x53]
        self.assertEqual(len(writes), 4)

    def test_read_timeout_retries_are_bounded_and_non_timeout_errors_are_not_retried(self):
        for fault, count in (("drop", 3), ("malformed", 1), ("packet_error", 1)):
            # Only unanswered queries are retried. Corrupt frames and explicit
            # firmware refusals must fail immediately.
            with self.subTest(fault=fault):
                self.pad.history.clear()
                self.pad.faults[4] = deque([fault] * 3)
                with self.assertRaises(ValueError):
                    backup.write_config(self.pad, self.wanted)
                self.assertEqual(sum(cmd == 4 for cmd, _ in self.pad.history), count)
                self.assertOriginal()
        self.pad.faults.clear()
        def malformed(*args, **kwargs):
            raise gpa6.ProtocolError('bad state')
        with patch.object(gpa6.Protocol, 'config_state', side_effect=malformed) as read:
            with self.assertRaises(gpa6.ProtocolError): backup.read_config(self.pad)
            self.assertEqual(read.call_count, 1)

    def test_read_retries_do_not_hide_an_identity_change(self):
        send = self.pad.send
        armed = []
        def capture(_):
            armed.append(True)
        def replace(report, **kwargs):
            if armed and report[3] == 4:
                armed.clear()
                self.pad.uid = 'ff' * 16
                return []
            return send(report, **kwargs)
        with patch.object(self.pad, 'send', side_effect=replace), self.assertRaises(identity.WrongDevice):
            backup.write_config(self.pad, self.wanted, before_write=capture)
        self.assertFalse(any(cmd in (0xa4, 0xa7, 0x53) for cmd, _ in self.pad.history))

    def test_guard_timeout_after_first_write_recovers_or_rolls_back_at_limit(self):
        for drops in (2, 3):
            pad = captured_pad()
            original = state(pad)
            wanted = candidate(backup.read_config(pad))
            send = pad.send
            fired = []
            def lose_read(report, **kwargs):
                result = send(report, **kwargs)
                if report[3] == 0x53 and not fired:
                    fired.append(True)
                    pad.faults[4] = deque(['drop'] * drops)
                return result
            with patch.object(pad, 'send', side_effect=lose_read):
                if drops == 3:
                    with self.assertRaises(gpa6.ReplyTimeout): backup.write_config(pad, wanted)
                    self.assertEqual(state(pad), original)
                else:
                    backup.write_config(pad, wanted)
                    self.assertEqual(backup.read_config(pad).blocks(), wanted.blocks())

    def test_source_uid_and_slot_are_provenance_not_destination(self):
        self.wanted.source["uid"] = "ff" * 16
        self.wanted.source["slot"] = 3
        backup.write_config(self.pad, self.wanted)
        self.assertEqual(self.pad.slot, 0)
        self.assertFalse(any(cmd == 0xa2 for cmd, _ in self.pad.history))

    def test_every_step_failure_before_or_after_apply_rolls_back_all_domains(self):
        for failure in range(1, 7):
            for after in (False, True):
                with self.subTest(step=failure, after=after):
                    pad = captured_pad()
                    original = state(pad)
                    config = candidate(backup.read_config(pad))
                    writes = [0]
                    def wrap(method):
                        def call(*args, **kwargs):
                            writes[0] += 1
                            fail = writes[0] == failure
                            if fail and not after:
                                raise gpa6.ReplyTimeout("injected pre-write failure")
                            result = method(*args, **kwargs)
                            if fail:
                                raise gpa6.ReplyTimeout("injected lost acknowledgment")
                            return result
                        return call
                    with patch.object(gpa6.Protocol, "write_ram", wrap(gpa6.Protocol.write_ram)), patch.object(
                            gpa6.Protocol, "configure_trigger", wrap(gpa6.Protocol.configure_trigger)):
                        with self.assertRaises(gpa6.ReplyTimeout):
                            backup.write_config(pad, config)
                    self.assertEqual(state(pad), original)

    def test_real_lost_motor_ack_and_mapping_commit_ack_roll_back(self):
        for kind in ("motor", "mapping"):
            pad = captured_pad()
            original = state(pad)
            config = candidate(backup.read_config(pad))
            send = pad.send
            fired = []
            def lose(report, **kwargs):
                result = send(report, **kwargs)
                if not fired and ((kind == "motor" and report[3] == 0x53)
                                  or (kind == "mapping" and report[3] == 0xa4 and report[5] == 2)):
                    fired.append(True)
                    return []
                return result
            with patch.object(pad, "send", side_effect=lose), self.assertRaises(gpa6.ReplyTimeout):
                backup.write_config(pad, config)
            self.assertTrue(fired)
            self.assertEqual(state(pad), original)

    def test_mapping_begin_refusal_restores_quiesced_triggers(self):
        self.pad.faults[0xa4] = deque(["error"])
        with self.assertRaises(gpa6.ProtocolError):
            backup.write_config(self.pad, self.wanted)
        self.assertOriginal()

    def test_concurrent_edit_to_any_domain_refuses_rollback(self):
        for domain in (1, 4, 5, 6):
            pad = captured_pad()
            config = candidate(backup.read_config(pad))
            send = pad.send
            writes = []
            def concurrent(report, **kwargs):
                result = send(report, **kwargs)
                if report[3] == 0x53:
                    writes.append(True)
                    if domain == 1:
                        pad.global_data[-1] = 42
                    elif domain == 6:
                        pad.maps[0][-1] = 42
                    else:
                        pad.configs[0][domain] += b"external"
                return result
            with patch.object(pad, "send", side_effect=concurrent), self.assertRaisesRegex(gpa6.ProtocolError, "rollback incomplete"):
                backup.write_config(pad, config)
            self.assertEqual(len(writes), 1)

    def test_identity_slot_or_format_change_never_writes_to_new_target(self):
        for change in ("uid", "slot", "versions"):
            pad = captured_pad()
            config = candidate(backup.read_config(pad))
            send = pad.send
            writes = []
            def race(report, **kwargs):
                result = send(report, **kwargs)
                if report[3] == 0x53:
                    writes.append(True)
                    if change == "uid": pad.uid = "ff" * 16
                    elif change == "slot": pad.change_profile(1)
                    else: pad.versions = (22, 18, 10)
                return result
            with patch.object(pad, "send", side_effect=race), self.assertRaisesRegex(gpa6.ProtocolError, "rollback incomplete"):
                backup.write_config(pad, config)
            self.assertEqual(len(writes), 1)

    def test_incompatible_metadata_and_nonrestorable_motor_bytes_preflight(self):
        changes = [lambda c: c.source.update(device_type=149),
                   lambda c: c.source.update(format_versions=[23, 19, 10]),
                   lambda c: c.source.update(features_raw=c.source["features_raw"] ^ 128),
                   lambda c: c.motors.blob.__setitem__(24, 99),
                   lambda c: c.motors.blob.__setitem__(63, 99),
                   lambda c: c.motors.blob.__setitem__(1, 7)]
        for change in changes:
            config = self.wanted.copy()
            change(config)
            with self.assertRaises(ValueError):
                backup.write_config(self.pad, config)
            self.assertOriginal()
        self.assertFalse(any(cmd in (0xa4, 0x53, 0xa7) for cmd, _ in self.pad.history))

    def test_noncanonical_source_or_destination_is_not_silently_normalized(self):
        for side in ("source", "destination"):
            pad = captured_pad()
            config = candidate(backup.read_config(pad))
            if side == "source": pad.maps[0][11] = 42
            else:
                config.motors.set_trigger("left", 0)
                config.motors.blob[11] = 42
            initial = state(pad)
            with self.assertRaises(gpa6.ProtocolError):
                backup.write_config(pad, config)
            self.assertEqual(state(pad), initial)

    def test_unchanged_opaque_trigger_can_be_backed_up_and_noop_restored(self):
        self.pad.maps[0][11] = 42
        config = backup.read_config(self.pad)
        backup.write_config(self.pad, config)
        self.assertEqual(self.pad.maps[0][11], 42)
        config.mapping.blob = bytearray(gpa6_profile.remap(config.mapping.blob, "m4", "a"))
        with self.assertRaises(gpa6.ProtocolError):
            backup.write_config(self.pad, config)

    def test_guards_and_failed_recovery_publication_prevent_writes(self):
        for kwargs in ({"expected_uid": "ff" * 16}, {"expected_slot": 3},
                       {"expected_config": self.wanted}):
            with self.assertRaises((ValueError, identity.WrongDevice)):
                backup.write_config(self.pad, self.wanted, **kwargs)
        with self.assertRaises(OSError):
            backup.write_config(self.pad, self.wanted, before_write=lambda c: (_ for _ in ()).throw(OSError("disk full")))
        self.assertOriginal()
        self.assertFalse(any(cmd in (0xa4, 0x53, 0xa7) for cmd, _ in self.pad.history))

    def test_mutation_during_recovery_callback_is_caught_before_write(self):
        def race(_):
            self.pad.global_data[-1] = 42
        with self.assertRaises(gpa6.ProtocolError):
            backup.write_config(self.pad, self.wanted, before_write=race)
        self.assertFalse(any(cmd in (0xa4, 0x53, 0xa7) for cmd, _ in self.pad.history))

    def test_save_failure_reports_uncertainty_without_reverting_applied_ram(self):
        self.pad.faults[0xa7] = deque(["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            backup.write_config(self.pad, self.wanted, save=True)
        self.assertEqual({ram: self.pad.ram(ram) for ram in (4, 5, 6)}, self.wanted.blocks())

    def test_keyboard_interrupt_also_rolls_back(self):
        write = gpa6.Protocol.write_ram
        calls = []
        def interrupt(*args, **kwargs):
            result = write(*args, **kwargs)
            if not calls:
                calls.append(True)
                raise KeyboardInterrupt()
            return result
        with patch.object(gpa6.Protocol, "write_ram", interrupt), self.assertRaises(KeyboardInterrupt):
            backup.write_config(self.pad, self.wanted)
        self.assertOriginal()

    def test_legacy_pad_is_refused_by_read_and_restore(self):
        pad = FakePad()
        with self.assertRaises(identity.WrongDevice): backup.read_config(pad)
        with self.assertRaises(identity.WrongDevice): backup.write_config(pad, self.wanted)


class LibraryTests(unittest.TestCase):
    def test_import_export_names_no_overwrite_and_bad_entry_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            library = backup.Library(Path(directory) / "library")
            self.assertEqual(library.list(), [])
            config = backup.read_config(captured_pad())
            entry = library.add(config, name="Game preset 🎮")
            output = Path(directory) / "export.json"
            library.export(entry, output)
            with self.assertRaises(FileExistsError): library.export(entry, output)
            imported = library.import_file(output, name="Copy")
            self.assertNotEqual(entry, imported)
            self.assertEqual(library.get(imported).blocks(), config.blocks())
            self.assertEqual(library.get(imported).name, "Copy")
            (library.directory / ("ff" * 16 + ".json")).write_text("broken")
            self.assertEqual(len(library.list()), 3)
            self.assertEqual(sum("error" in e for e in library.list()), 1)
            for invalid in ("../export", "abc", "", "../" + entry, entry.upper()):
                with self.assertRaises(ValueError): library.get(invalid)
                with self.assertRaises(ValueError): library.remove(invalid)
            library.remove(imported)
            self.assertEqual(len(library.list()), 2)

    def test_default_location_obeys_xdg(self):
        with patch.dict(os.environ, {"XDG_DATA_HOME": "/tmp/test-data"}):
            self.assertEqual(backup.Library().directory, Path("/tmp/test-data/openflydigi/apex6/profiles"))

    def test_cli_offline_library_and_guarded_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6", "XDG_DATA_HOME": directory}
            def run(*args):
                return subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"), *args],
                                      env=env, capture_output=True, text=True)
            file = str(Path(directory) / "profile.json")
            result = run("--device", "mock:pad0", "backup", file, "--name", "CLI backup")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(run("backup", file).returncode, 0)
            result = run("--device", "/nonexistent", "library", "import", file)
            self.assertEqual(result.returncode, 0, result.stderr)
            entry = json.loads(result.stdout)["id"]
            self.assertEqual(run("--device", "/nonexistent", "library", "show", entry).returncode, 0)
            self.assertEqual(run("--device", "/nonexistent", "library", "list").returncode, 0)
            self.assertNotEqual(run("restore", file).returncode, 0)
            self.assertNotEqual(run("library", "restore", entry).returncode, 0)
            config = backup.load_config(file)
            config.mapping.blob = bytearray(gpa6_profile.remap(config.mapping.blob, "m4", "b"))
            changed = str(Path(directory) / "changed.json")
            backup.export_config(changed, config)
            result = run("--experimental", "--device", "mock:pad0", "restore", changed, "--slot", "0", "--save")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["recovery_ids"]), 1)
            self.assertIn("Recovery profile:", result.stderr)
            result = run("--experimental", "--device", "mock:pad0", "library", "restore", entry)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["recovery_ids"], [])


if __name__ == "__main__":
    unittest.main()
