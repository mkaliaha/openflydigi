#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Independent native activator fixtures, lossless edits and guarded writes."""
from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_actions as actions, gpa6_config, gpa6_profile as profile
from test_apex6_editing import captured_pad

FIXTURE = json.loads((ROOT / "tests/fixtures/apex6-activators.json").read_text())


def bare(blob):
    return gpa6_config.unwrap_blob(blob, b"XMUL")


def binding(kind="simple", target="b"):
    return {"type": kind, "target": target}


class ActivatorTests(unittest.TestCase):
    def setUp(self):
        self.original = gpa6_config.wrap_blob(bytes.fromhex(FIXTURE["cases"][0]["before_hex"]))
        self.config = profile.MappingConfig(self.original, cfg_id=2)

    def test_complete_edits_match_independent_vendor_encoder(self):
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                config = profile.MappingConfig(bytes.fromhex(case["before_hex"]))
                if case["operation"] == "bind":
                    config.set_activators(**case["args"])
                else:
                    config.ensure_layer(**case["args"])
                self.assertEqual(bare(config.blob).hex(), case["after_hex"])

    def test_read_copy_and_noop_setters_are_byte_exact(self):
        for blob in (self.original, bare(self.original), captured_pad().ram(5)):
            config = profile.MappingConfig(blob, cfg_id=2)
            copied = config.copy()
            for layer in copied.layer_ids():
                copied.ensure_layer(layer)
                for source in profile.BUTTONS:
                    copied.set_activators(source, copied.activators(source, layer=layer), layer=layer)
            self.assertEqual(copied.blob, blob)
            self.assertEqual(copied.cfg_id, 2)
            copied.set_activators("m1", [binding("toggle")])
            self.assertEqual(config.blob, blob)

    def test_reserved_entry_and_target_bytes_survive(self):
        data, layers = profile._layers(self.original)
        _, start, stop, _ = layers[0][3][0]
        raw = bytearray(data)
        raw[start + 2:start + 4] = b"\xa5\x5a"
        raw[stop - 2:stop] = b"\xab\xcd"
        config = profile.MappingConfig(gpa6_config.wrap_blob(raw))
        config.set_activators("m1", [binding("timing_long", "x")])
        expected = bytearray(raw)
        expected[start + 4] = 2
        expected[start + 6] = 2
        self.assertEqual(bare(config.blob), expected)

    def test_unrelated_unknown_actions_layers_zones_macros_and_tail_survive(self):
        data, layers, extensions = profile._layout(self.original)
        # Deliberately unknown activator and target on M2 must remain untouched.
        raw = bytearray(data)
        entry = layers[0][3][1]
        raw[entry[1] + 4:entry[1] + 9] = b"\x81\x82\x83\x84\x85"
        if extensions:
            raw[-2:-2] = b"\x70\x03xyz"
        else:
            raw.extend(b"\x70\x03xyz\xff\0")
        config = profile.MappingConfig(gpa6_config.wrap_blob(raw))
        self.assertEqual(config.activators("m2")[0]["type"], 0x81)
        self.assertEqual(config.activators("m2")[0]["target"]["hex"], "82838485")
        config.set_activators("m1", [binding("timing_single", "a"), binding("timing_long", "b"), binding("timing_double", "x")])
        after, new_layers, _ = profile._layout(config.blob)
        self.assertEqual(after[new_layers[0][2]:], bytes(raw)[layers[0][2]:])
        self.assertEqual(config.activators("m2")[0]["target"]["hex"], "82838485")
        # Appending a layer shifts only the tail and layer count.
        before = bare(config.blob)
        _, _, exts = profile._layout(before)
        boundary = exts[0][1]
        config.ensure_layer(3)
        after = bare(config.blob)
        expected = bytearray(before[:boundary] + b"\x03" + bytes(11) + before[boundary:])
        expected[24] += 1
        self.assertEqual(after, expected)

    def test_missing_empty_and_disabled_are_distinct(self):
        self.assertIsNone(self.config.activators("m4"))
        self.config.set_activators("m4", [])
        self.assertEqual(self.config.activators("m4"), [])
        self.config.set_activators("m4", [binding(target="none")])
        self.assertEqual(self.config.activators("m4")[0]["target"], {"type": "btn_gamepad", "id": "none"})
        self.config.set_activators("m4", None)
        self.assertEqual(self.config.blob, self.original)

    def test_references_are_checked_in_selected_layer(self):
        with self.assertRaisesRegex(ValueError, "layer does not exist"):
            self.config.set_activators("m1", [binding(target={"type": "layer_hold", "layer_id": 3})])
        self.config.ensure_layer(3)
        self.config.set_activators("m1", [binding(target={"type": "layer_hold", "layer_id": 3})])
        macro = binding(target={"type": "macro", "macro_id": 1})
        self.config.set_activators("m1", [macro])
        with self.assertRaisesRegex(ValueError, "macro must exist uniquely"):
            self.config.set_activators("m1", [macro], layer=1)

    def test_bad_inputs_fail_atomically(self):
        cases = [lambda: self.config.set_activators("lt", [binding()]),
                 lambda: self.config.set_activators("a", [binding()] * 6),
                 lambda: self.config.set_activators("a", [binding("unknown")]),
                 lambda: self.config.set_activators("a", [binding(), binding(target={"type": "btn_mouse", "mouse_id": 5})]),
                 lambda: self.config.set_activators("a", [binding()], layer=True),
                 lambda: self.config.set_activators("a", [binding()], layer=2),
                 lambda: self.config.set_activators("a", [{"type": "simple"}]),
                 lambda: self.config.ensure_layer(4),
                 lambda: self.config.ensure_layer(True)]
        for edit in cases:
            with self.assertRaises(ValueError):
                edit()
            self.assertEqual(self.config.blob, self.original)

    def test_invalid_targets_are_rejected_not_truncated(self):
        for target in ("default", True, {"type": "unknown", "hex": "00000000"},
                       {"type": "btn_gamepad", "id": 22}, {"type": "btn_gamepad", "id": True},
                       {"type": "btn_keyboard", "key_id": 256},
                       {"type": "btn_keyboard", "key_id": 4, "modifer": 1},
                       {"type": "simulate_mouse_move", "delta_x": -129, "delta_y": 0},
                       {"type": "turbo_gamepad", "button_id": "a", "turbo_interval_10ms": 0},
                       {"type": "turbo_gamepad", "button_id": "a", "turbo_interval_10ms": 65536},
                       {"type": "layer_hold", "layer_id": 4}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                actions.encode_target(target)

    def test_truncated_and_duplicate_sources_are_refused(self):
        raw, layers = profile._layers(self.original)
        duplicate = bytearray(raw)
        duplicate[layers[0][3][1][1]] = duplicate[layers[0][3][0][1]]
        for candidate in (duplicate, raw[:layers[0][2] - 1]):
            with self.assertRaises(gpa6.ProtocolError):
                profile.MappingConfig(candidate)

    def test_legacy_remap_keeps_replacement_and_default_semantics(self):
        self.config.set_activators("m1", [binding("timing_long"), binding("timing_double", "x")])
        result = profile.MappingConfig(profile.remap(self.config.blob, "m1", "y"))
        self.assertEqual(result.activators("m1"), [binding(target={"type": "btn_gamepad", "id": "y"})])
        result = profile.MappingConfig(profile.remap(result.blob, "m1", "default"))
        self.assertIsNone(result.activators("m1"))


class TransactionTests(unittest.TestCase):
    @staticmethod
    def edit(blob):
        config = profile.MappingConfig(blob)
        config.ensure_layer(1)
        config.set_activators("m1", [binding(target={"type": "layer_hold", "layer_id": 1})])
        config.set_activators("a", [binding("timing_single", "a"), binding("timing_long", "b"),
                                    binding("timing_double", "x")], layer=1)
        return config.blob

    def test_guarded_write_save_and_full_restoration(self):
        pad = captured_pad()
        before = {ram: pad.ram(ram) for ram in (1, 4, 5, 6)}
        after = gpa6_config.edit_ram(pad, 5, self.edit, save=True, expected_uid=pad.uid)
        self.assertEqual(after, pad.stored_configs[0][5])
        self.assertEqual(profile.MappingConfig(after).layer_ids(), [0, 1])
        self.assertEqual(len(profile.MappingConfig(after).activators("a", layer=1)), 3)
        for ram in (1, 4, 6):
            self.assertEqual(pad.ram(ram), before[ram])
        gpa6_config.edit_ram(pad, 5, lambda _: before[5], save=True)
        pad.power_cycle()
        self.assertEqual({ram: pad.ram(ram) for ram in before}, before)

    def test_lost_commit_ack_rolls_back_layer_and_activators(self):
        pad = captured_pad()
        before = pad.ram(5)
        chunks = (len(self.edit(before)) + 15) // 16
        pad.faults[0xa4] = deque([None] * (1 + chunks) + ["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            gpa6_config.edit_ram(pad, 5, self.edit)
        self.assertEqual(pad.ram(5), before)
        self.assertIsNone(pad.transfer)

    def test_invalid_reference_never_sends_write(self):
        pad = captured_pad()
        def invalid(blob):
            config = profile.MappingConfig(blob)
            config.set_activators("m1", [binding(target={"type": "layer_hold", "layer_id": 3})])
            return config.blob
        with self.assertRaises(ValueError):
            gpa6_config.edit_ram(pad, 5, invalid)
        self.assertFalse(any(command == 0xa4 for command, _ in pad.history))
        gpa6_config.edit_ram(pad, 5, self.edit)


class CommandTests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"), "--device", "mock:pad0", *args],
                              env={**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6"}, capture_output=True, text=True)

    def test_read_and_write_opt_in(self):
        result = self.run_command("bind", "m1")
        self.assertEqual(result.returncode, 0, result.stderr)
        for args in (("bind", "m1", "--clear"), ("bind", "m1", "--save"),
                     ("bind", "m1", "--action", "toggle", "b"), ("layer-add", "1")):
            result = self.run_command(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIn("require --experimental", result.stderr)

    def test_multiple_actions_and_explicit_save(self):
        result = self.run_command("--experimental", "bind", "m1", "--action", "timing_single", "a",
                                  "--action", "timing_long", "b", "--action", "timing_double", "x", "--save")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([a["type"] for a in json.loads(result.stdout)["activators"]],
                         ["timing_single", "timing_long", "timing_double"])
        self.assertIn("save acknowledged", result.stderr)

    def test_native_target_json_clear_and_layer_creation(self):
        result = self.run_command("--experimental", "bind", "m1", "--action", "pulse_press",
                                  '{"type":"btn_keyboard","key_id":4,"modifier":2}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["activators"][0]["target"]["modifier"], 2)
        result = self.run_command("--experimental", "bind", "m1", "--clear")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["activators"])
        result = self.run_command("--experimental", "layer-add", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(3, json.loads(result.stdout)["layers"])

    def test_bad_cli_inputs_fail(self):
        for args in (("--action", "longish", "b"), ("--action", "simple", "{broken"),
                     ("--clear", "--action", "simple", "b"),
                     tuple(["--action", "simple", "b"] * 6),
                     ("--action", "simple", "layer-hold:9")):
            result = self.run_command("--experimental", "bind", "m1", *args)
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
