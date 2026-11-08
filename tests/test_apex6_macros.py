#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Independent web encoder fixtures, lossless editing and macro transactions."""
from collections import deque
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
import runpy
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_config, gpa6_macros, gpa6_profile as profile, macros, mapping
from test_apex6_editing import captured_pad

FIXTURE = json.loads((ROOT / "tests/fixtures/apex6-macros.json").read_text())


def bare(blob):
    return gpa6_config.unwrap_blob(blob, b"XMUL")


def pair(key="a", delay=73):
    return [{"delay": 0, "event": mapping.MACRO_PRESS, "key": key},
            {"delay": delay, "event": mapping.MACRO_RELEASE, "key": key}]


def reference(macro_id):
    return {"type": "macro", "macro_id": macro_id}


class MacroTests(unittest.TestCase):
    def setUp(self):
        case = next(c for c in FIXTURE["cases"] if c["name"] == "1.9-0-press_once")
        self.original = gpa6_config.wrap_blob(bytes.fromhex(case["before_hex"]))
        self.config = profile.MappingConfig(self.original, cfg_id=2)

    def test_complete_profiles_match_independent_vendor_encoder(self):
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                config = profile.MappingConfig(bytes.fromhex(case["before_hex"]))
                (config.set_macro if case["operation"] == "set" else config.clear_macro)(**case["args"])
                self.assertEqual(bare(config.blob).hex(), case["after_hex"])
        self.assertEqual(self.config.macro_limits.slots, FIXTURE["limits"]["slots"])
        self.assertEqual(self.config.macro_limits.steps, FIXTURE["limits"]["steps"])
        self.assertEqual(self.config.macro_limits.interval_max, FIXTURE["limits"]["interval_max"])

    def test_native_sequence_validation_matches_web(self):
        for case in FIXTURE["validation"]:
            # Hold-mode conversion in the UI removes releases and rejects
            # sticks; normal timeline validation is independently exercised.
            if case["hold"]:
                continue
            with self.subTest(case=case["name"]):
                if case["error"]:
                    with self.assertRaises(ValueError):
                        self.config.set_macro(5, case["steps"])
                else:
                    self.config.set_macro(5, case["steps"])

    def test_shared_recorder_steps_helpers_and_key_aliases(self):
        class Reader:
            pass
        recorder = macros.Recorder(Reader(), clock=lambda: 1.2)
        recorder._change("select", True, 1)
        steps = recorder.finish()
        self.config.set_macro(5, steps)
        self.assertEqual(self.config.macro(5)["steps"], steps)
        self.assertEqual(macros.total_ms(self.config.macro(5)["steps"]), 200)
        self.assertEqual(len(macros.describe(self.config.macro(5)["steps"])), 2)
        for key in ("thl", "thr", "select", "lt", "rt"):
            self.config.set_macro(5, pair(key))
        self.assertEqual(self.config.macro(5)["steps"][1]["target"],
                         {"type": "simulate_linear_right", "linear_value": 0})
        # Home has no native gamepad target ID; never silently map it to A.
        with self.assertRaises(ValueError):
            self.config.set_macro(5, pair("home"))

    def test_read_copy_and_noop_preserve_bare_or_wrapped_profiles(self):
        for blob in (self.original, bare(self.original), captured_pad().ram(5)):
            config = profile.MappingConfig(blob, cfg_id=2)
            copy = config.copy()
            for layer in copy.layer_ids():
                for item in copy.macros(layer=layer):
                    copy.set_macro(item["macro_id"], layer=layer)
                copy.set_macros(copy.macros(layer=layer), layer=layer)
            self.assertEqual(copy.blob, blob)
            self.assertEqual(copy.cfg_id, 2)
            copy.set_macro(5, pair())
            self.assertEqual(config.blob, blob)

    def test_reserved_record_target_bytes_and_other_sections_survive(self):
        raw, _, records, _ = self.config._macro_layout(0)
        _, start, end = records[0]
        changed = bytearray(raw)
        changed[start + 5] = 0xa5
        changed[start + 9:start + 11] = b"\xc3\x7f"
        changed[-2:-2] = b"\x70\x03xyz"
        self.config = profile.MappingConfig(gpa6_config.wrap_blob(changed))
        self.config.set_macro(7, pair("x", 81))
        result = bare(self.config.blob)
        self.assertEqual(result[:start], changed[:start])
        self.assertEqual(result[end:], changed[end:])
        self.assertEqual(result[start + 5], 0xa5)
        self.assertEqual(result[start + 9:start + 11], b"\xc3\x7f")

    def test_unknown_stored_events_modes_and_targets_can_be_renamed_losslessly(self):
        data, _, records, _ = self.config._macro_layout(0)
        start = records[0][1]
        raw = bytearray(data)
        raw[start + 1] = 99
        raw[start + 6:start + 11] = b"\x80\x81\x82\x83\x84"
        self.config = profile.MappingConfig(gpa6_config.wrap_blob(raw))
        self.assertEqual(self.config.macro(7)["type"], 99)
        self.assertEqual(self.config.macro(7)["steps"][0]["event"], "unknown:128")
        self.config.set_macro(7, name="Only name")
        self.assertEqual(bare(self.config.blob)[:records[0][2]], raw[:records[0][2]])
        before = bytes(self.config.blob)
        self.config.set_macros(self.config.macros())
        self.assertEqual(self.config.blob, before)
        items = self.config.macros()
        items[0]["name"] = "Rename through shared list"
        self.config.set_macros(items)
        self.assertEqual(bare(self.config.blob)[:records[0][2]], raw[:records[0][2]])
        before = bytes(self.config.blob)
        with self.assertRaises(ValueError):
            self.config.set_macro(7, macro_type="press_once")
        self.assertEqual(self.config.blob, before)

    def test_limits_are_per_layer_and_per_macro(self):
        for i in range(9):
            self.config.set_macro(i + 20, pair() * 16)
        self.config.set_macro(7, pair())  # replacing at capacity is valid
        with self.assertRaisesRegex(ValueError, "slots"):
            self.config.set_macro(5, [])
        self.config.set_macro(5, pair() * 16, layer=1)
        self.assertEqual(len(self.config.macro(5, layer=1)["steps"]), 32)
        self.config.clear_macro(20)
        self.config.set_macro(5, [], macro_type="press_hold")

    def test_invalid_inputs_fail_atomically(self):
        bad = [dict(macro_id=True), dict(macro_id=256), dict(macro_id=5, layer=2),
               dict(macro_id=5, layer=True), dict(macro_id=5, macro_type=mapping.MACRO_ONCE),
               dict(macro_id=5, interval=10001), dict(macro_id=5, interval=True),
               dict(macro_id=5, steps=pair() * 17), dict(macro_id=5, steps=pair(delay=65536)),
               dict(macro_id=5, steps=[{**pair()[0], "delay": 65535}, pair()[1]]),
               dict(macro_id=5, steps=[{**pair()[0], "delay": -1}]),
               dict(macro_id=5, steps=[{**pair()[0], "delay": 0.5}]),
               dict(macro_id=5, steps=[{**pair()[0], "event": True}]),
               dict(macro_id=5, steps=[{**pair()[0], "target": "a"}]),
               dict(macro_id=5, steps=[{"delay": 0, "event": 1, "target": reference(7)}]),
               dict(macro_id=5, steps=[{"delay": 0, "event": 1, "key": []}]),
               dict(macro_id=5, name="x" * 256), dict(macro_id=5, name="\ud800"),
               dict(macro_id=5, name=42)]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.config.set_macro(**args)
            self.assertEqual(self.config.blob, self.original)

    def test_hold_and_empty_definition_semantics(self):
        self.config.set_macro(5, [pair()[0]], macro_type="press_hold")
        self.assertEqual(self.config.macro(5)["type"], "press_hold")
        before = bytes(self.config.blob)
        for steps in (pair(), [{"delay": 0, "event": 1, "target": {
                "type": "simulate_joystick_left", "axis_x": 1, "axis_y": 0}}]):
            with self.assertRaises(ValueError):
                self.config.set_macro(5, steps)
            self.assertEqual(self.config.blob, before)
        with self.assertRaisesRegex(ValueError, "missing a release"):
            self.config.set_macro(5, macro_type="press_once")
        self.config.set_macro(5, pair(), macro_type="press_once")
        self.config.set_macro(5, [])
        self.assertEqual(self.config.macro(5)["steps"], [])
        self.config.clear_macro(5)
        self.assertIsNone(self.config.macro(5))

    def test_names_preserve_profile_and_other_layers_and_obey_total_byte_limit(self):
        self.config.set_macro(5, pair(), name="Комбо 🐾", layer=1)
        self.config.set_macro(7, name="Changed")
        self.assertEqual(self.config.macro(5, layer=1)["name"], "Комбо 🐾")
        data, _, extensions = profile._layout(self.config.blob)
        self.assertEqual(gpa6_macros.names(data, extensions)[1], b"Keep profile")
        before = bytes(self.config.blob)
        with self.assertRaisesRegex(ValueError, "metadata limit"):
            self.config.set_macro(7, pair("x"), name="🐾" * 60)
        self.assertEqual(self.config.blob, before)
        self.config.set_macro(7, name="")
        self.assertIsNone(self.config.macro(7)["name"])
        v18 = profile.MappingConfig(bytes.fromhex(FIXTURE["cases"][0]["before_hex"]))
        with self.assertRaisesRegex(ValueError, "v1.9"):
            v18.set_macro(5, name="Not silently dropped")

    def test_names_can_create_a_missing_tail(self):
        data, _, extensions = profile._layout(self.original)
        config = profile.MappingConfig(data[:extensions[0][1]])
        config.set_macro(7, name="Name")
        self.assertEqual(config.macro(7)["name"], "Name")
        self.assertEqual(bare(config.blob)[-2:], b"\xff\0")

    def test_ambiguous_ids_names_and_truncated_metadata_are_refused(self):
        self.config.set_macro(5, pair())
        raw, _, records, extensions = self.config._macro_layout(0)
        duplicate = bytearray(raw)
        duplicate[records[-1][1]] = 7
        with self.assertRaisesRegex(gpa6.ProtocolError, "duplicate macro"):
            profile.MappingConfig(duplicate).set_macro(7, pair())
        meta = next(item for item in extensions if item[0] == 32)
        duplicate = raw[:meta[2]] + raw[meta[1]:meta[2]] + raw[meta[2]:]
        with self.assertRaisesRegex(gpa6.ProtocolError, "duplicate XMUL name"):
            profile.MappingConfig(duplicate).set_macro(5, name="Name")
        invalid = bytearray(raw)
        invalid[meta[1] + 2] = 255
        config = profile.MappingConfig(invalid)
        with self.assertRaises(gpa6.ProtocolError):
            config.set_macro(7, name="Name")
        self.assertEqual(config.blob, invalid)
        # Unrelated editors continue preserving opaque macro metadata.
        config.set_activators("a", [{"type": "simple", "target": "b"}])
        self.assertIn("decode_error", profile.describe(config.blob)["layers"][0]["macros"])

    def test_delete_removes_only_same_layer_references_including_stick_zones(self):
        c = self.config
        c.set_macro(7, pair(), layer=1)
        c.set_activators("m1", [{"type": "simple", "target": reference(7)}, {"type": "timing_long", "target": "x"}])
        c.set_activators("a", [{"type": "simple", "target": reference(7)}])
        c.set_activators("m1", [{"type": "simple", "target": reference(7)}], layer=1)
        c.set_trigger_zones("left", [{"start": 10, "stop": 30, "target": reference(7)}])
        c.set_stick_zones("right", [{"start": 10, "stop": 30, "dir": "up", "target": reference(7)},
                                    {"start": 40, "stop": 80, "dir": "down", "target": "y"}])
        other_before = c.macros(layer=1)
        c.clear_macro(7)
        self.assertIsNone(c.macro(7))
        self.assertIsNone(c.activators("a"))
        self.assertEqual(c.activators("m1"), [{"type": "timing_long", "target": {"type": "btn_gamepad", "id": "x"}}])
        self.assertEqual(c.trigger_zones("left"), [])
        self.assertEqual([z["dir"] for z in c.stick_zones("right")["zones"]], ["down"])
        self.assertEqual(c.macros(layer=1), other_before)
        self.assertEqual(c.activators("m1", layer=1)[0]["target"], reference(7))
        before = bytes(c.blob)
        c.clear_macro(7)
        self.assertEqual(c.blob, before)

    def test_delete_preserves_unknown_remaining_activators_and_refuses_nested_macros(self):
        c = self.config
        c.set_macro(5, pair())
        c.set_activators("a", [{"type": "simple", "target": reference(7)}, {"type": "simple", "target": "x"}])
        raw, layers = profile._layers(c.blob)
        entry = next(e for e in layers[0][3] if e[0] == 0)
        changed = bytearray(raw)
        changed[entry[2] - 5:entry[2]] = b"\x80\x81\x82\x83\x84"
        c = profile.MappingConfig(changed)
        c.clear_macro(7)
        self.assertEqual(c.activators("a")[0]["target"]["hex"], "81828384")
        c.set_macro(7, pair())
        raw, _, records, _ = c._macro_layout(0)
        start = next(start for mid, start, _ in records if mid == 5)
        changed = bytearray(raw)
        changed[start + 7:start + 11] = bytes([30, 7, 0, 0])
        c = profile.MappingConfig(changed)
        with self.assertRaisesRegex(ValueError, "referenced by another macro"):
            c.clear_macro(7)
        self.assertEqual(c.blob, changed)

    def test_set_macros_reorders_replaces_and_is_atomic(self):
        self.config.set_macro(5, pair("x"))
        items = self.config.macros()[::-1]
        self.config.set_macros(items)
        self.assertEqual([m["macro_id"] for m in self.config.macros()], [5, 7])
        before = bytes(self.config.blob)
        with self.assertRaises(ValueError):
            self.config.set_macros([{"macro_id": 7, "steps": pair("y")}, {"macro_id": 3, "steps": pair("bad")}])
        self.assertEqual(self.config.blob, before)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.config.set_macros(items + items)
        self.config.set_macros([])
        self.assertEqual(self.config.macros(), [])


class TransactionTests(unittest.TestCase):
    @staticmethod
    def edit(blob):
        config = profile.MappingConfig(blob)
        config.set_macro(5, pair(), name="Acceptance")
        config.set_activators("m1", [{"type": "simple", "target": reference(5)}])
        return config.blob

    def test_guarded_write_save_and_exact_restoration(self):
        pad = captured_pad()
        before = {ram: pad.ram(ram) for ram in (1, 4, 5, 6)}
        after = gpa6_config.edit_ram(pad, 5, self.edit, save=True, expected_uid=pad.uid)
        self.assertEqual(after, pad.stored_configs[0][5])
        for ram in (1, 4, 6):
            self.assertEqual(pad.ram(ram), before[ram])
        gpa6_config.edit_ram(pad, 5, lambda _: before[5], save=True)
        pad.power_cycle()
        self.assertEqual({ram: pad.ram(ram) for ram in before}, before)

    def test_lost_commit_ack_rolls_back_definition_name_and_binding(self):
        pad = captured_pad()
        before = pad.ram(5)
        chunks = (len(self.edit(before)) + 15) // 16
        pad.faults[0xa4] = deque([None] * (1 + chunks) + ["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            gpa6_config.edit_ram(pad, 5, self.edit)
        self.assertEqual(pad.ram(5), before)
        self.assertIsNone(pad.transfer)

    def test_invalid_macro_never_writes(self):
        pad = captured_pad()
        def invalid(blob):
            config = profile.MappingConfig(blob)
            config.set_macro(5, pair()[:1])
            return config.blob
        with self.assertRaises(ValueError):
            gpa6_config.edit_ram(pad, 5, invalid)
        self.assertFalse(any(command == 0xa4 for command, _ in pad.history))


class CommandTests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"), "--device", "mock:pad0", *args],
                              env={**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6"}, capture_output=True, text=True)

    def test_reads_and_all_write_forms_require_opt_in(self):
        for args in (("macros",), ("macro", "5")):
            result = self.run_command(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        for change in (("--empty",), ("--name", "New"), ("--mode", "press_hold"), ("--interval", "10"),
                       ("--save",), ("--clear",), ("--copy-from", "7"), ("--bind", "m1"),
                       ("--steps-file", "unused.json"), ("--step", "0", "press", "a")):
            result = self.run_command("macro", "5", *change)
            self.assertEqual(result.returncode, 2)
            self.assertIn("require --experimental", result.stderr)

    def test_create_and_bind_steps_in_one_transaction(self):
        result = self.run_command("--experimental", "macro", "5", "--step", "0", "press", "a", "--step", "73", "release", "a",
                                  "--name", "Example", "--bind", "m1", "--save")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["steps"], pair())
        self.assertIn("save acknowledged", result.stderr)

    def test_steps_file_and_native_targets(self):
        steps = [{"delay": 0, "event": 1, "target": {"type": "btn_keyboard", "key_id": 4, "modifier": 2}}]
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "macro.json"
            file.write_text(json.dumps(steps))
            result = self.run_command("--experimental", "macro", "5", "--steps-file", str(file), "--mode", "press_hold")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["steps"], steps)
            file.write_text('{}')
            result = self.run_command("--experimental", "macro", "5", "--steps-file", str(file))
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_rejects_invalid_edits_and_clear_conflicts(self):
        for change in (("--step", "0", "press", "a"), ("--step", "0", "hold", "a"),
                       ("--clear", "--bind", "a"), ("--interval", "10001"), ("--copy-from", "7")):
            result = self.run_command("--experimental", "macro", "5", *change)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_copy_update_and_delete_preserve_source_and_clean_binding(self):
        pad = captured_pad()
        main = runpy.run_path(str(ROOT / "tools/flydigi-apex6"), run_name="macro_cli_test")["main"]
        def run(*args):
            output = io.StringIO()
            with patch.dict(main.__globals__, {"open_selected": lambda _: pad}), \
                    patch.object(sys, "argv", ["flydigi-apex6", "--experimental", *args]), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 0)
            return json.loads(output.getvalue())
        original = run("macro", "5", "--step", "0", "press", "a", "--step", "73", "release", "a", "--name", "Source")
        copied = run("macro", "6", "--copy-from", "5", "--name", "Copy", "--bind", "m4")
        self.assertEqual(copied["steps"], original["steps"])
        self.assertEqual(copied["name"], "Copy")
        updated = run("macro", "6", "--interval", "123", "--mode", "press_loop_wait")
        self.assertEqual(updated["interval"], 123)
        self.assertEqual(updated["steps"], copied["steps"])
        self.assertIsNone(run("macro", "6", "--clear"))
        config = profile.MappingConfig(pad.ram(5))
        self.assertIsNone(config.activators("m4"))
        self.assertEqual(config.macro(5), original)


if __name__ == "__main__":
    unittest.main()
