#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Independent encoder, lossless editing and transactional response checks."""
from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_config, gpa6_profile, mapping
from test_apex6_editing import captured_pad

VENDOR = json.loads((ROOT / "tests/fixtures/apex6-config.json").read_text())


def bare(blob):
    return gpa6_config.unwrap_blob(blob, b"XMUL")


def extensions(blob):
    data, _, records = gpa6_profile._layout(blob)
    return {kind: bytes(data[start:stop]) for kind, start, stop in records}


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.original = captured_pad().ram(5)
        self.config = gpa6_profile.MappingConfig(self.original, cfg_id=2)

    def test_native_edits_match_independent_vendor_encoder(self):
        for case in VENDOR["responseCurves"]:
            with self.subTest(version=case["version"], control=case["control"], side=case["side"], value=case["input"]):
                config = gpa6_profile.MappingConfig(bytes.fromhex(case["before"]["wrapped_hex"]))
                value = case["input"]["curve"]
                expected = {"start": (value["start_x"], value["start_y"]),
                            "end": (value["end_x"], value["end_y"]),
                            "points": [(p["x"], p["y"]) for p in value["mid_points"]]}
                config.set_response_curve(case["control"], case["side"], **expected)
                if case["control"] == "stick":
                    config.set_stick(case["side"], invert_x=True, invert_y=True, circular=False)
                self.assertEqual(config.blob.hex(), case["after"]["wrapped_hex"])
                self.assertEqual(config.response_curve(case["control"], case["side"]), expected)

    def test_read_copy_and_empty_setters_are_byte_exact(self):
        for blob in (self.original, bare(self.original),
                     *(bytes.fromhex(case["wrapped_hex"]) for case in VENDOR["mapping"])):
            config = gpa6_profile.MappingConfig(blob, cfg_id=2)
            copied = config.copy()
            self.assertEqual(copied.cfg_id, 2)
            for side in mapping.SIDES:
                for control in ("stick", "trigger"):
                    copied.response_curve(control, side)
                    copied.set_response_curve(control, side)
                copied.stick(side)
                copied.trigger_curve(side)
                copied.set_stick(side)
                copied.set_trigger_curve(side)
            self.assertEqual(copied.blob, blob)
            copied.set_stick("left", center=20)
            self.assertNotEqual(copied.blob, blob)
            self.assertEqual(config.blob, blob)

    def test_only_selected_extension_changes_with_complex_layers(self):
        for case in VENDOR["mappingEdits"]:
            before = bytes.fromhex(case["before"]["wrapped_hex"])
            # Keep two layers, mappings, macro, zones and names; insert unknown
            # records on both sides of the new response extension.
            data = bare(before)
            prefix = data[:-2] if extensions(before) else data
            before = gpa6_config.wrap_blob(prefix + b"\x70\x03abc\x71\x02xy\xff\0")
            config = gpa6_profile.MappingConfig(before)
            config.set_stick("left", center=20, circular=False)
            after = extensions(config.blob)
            self.assertEqual({key: value for key, value in after.items() if key != 1}, extensions(before))
            _, _, records = gpa6_profile._layout(before)
            self.assertEqual(bare(config.blob)[:records[0][1]], bare(before)[:records[0][1]])
            self.assertEqual(gpa6_profile.describe(config.blob)["layers"], gpa6_profile.describe(before)["layers"])

    def test_flags_reserved_bytes_and_unused_points_survive(self):
        data, _, records = gpa6_profile._layout(self.original)
        _, start, _ = next(record for record in records if record[0] == 1)
        altered = bytearray(data)
        altered[start + 2 + 5:start + 2 + 11] = b"\x01\x02\x03\x04\xa5\x5a"
        altered[start + 2 + 11] = 0xf8
        config = gpa6_profile.MappingConfig(gpa6_config.wrap_blob(altered))
        config.set_stick("left", circular=True, invert_x=True)
        after = bare(config.blob)
        self.assertEqual(after[start + 13], 0xfd)
        self.assertEqual(after[:start + 13] + after[start + 14:], altered[:start + 13] + altered[start + 14:])
        config.set_stick("left", center=10)
        self.assertEqual(bare(config.blob)[start + 7:start + 14], b"\x01\x02\x03\x04\xa5\x5a\xfd")
        config.set_response_curve("stick", "left", points=[(80, 90), (150, 170)])
        config.set_response_curve("stick", "left", points=[])
        self.assertEqual(bare(config.blob)[start + 7:start + 14], bytes([80, 90, 150, 170, 0xa5, 0x5a, 0xfd]))

    def test_stick_facade_matches_existing_ui_units_and_presets(self):
        for preset in mapping.STICK_PRESETS:
            if preset == mapping.CURVE_CUSTOM:
                continue
            self.config.set_stick("left", curve_type=preset)
            result = self.config.stick("left")
            self.assertEqual(result["type"], preset)
            self.assertEqual((result["center"], result["edge"]), (0, 0))
            self.assertTrue(result["bank_is_preview"])
            self.assertEqual(len(result["bank"]), mapping.BANK_POINTS)
        self.config.set_stick("left", curve_type=mapping.CURVE_DEFAULT)
        self.assertEqual(self.config.stick("left")["bank"], mapping.stick_bank())
        self.config.set_stick("left", center=20, edge=10, invert_y=True)
        result = self.config.stick("left")
        self.assertEqual((result["center"], result["edge"], result["type"]), (20, 10, mapping.CURVE_CUSTOM))
        self.assertTrue(result["invert_y"])
        self.assertEqual(result["curve"]["start"], (51, 0))
        self.assertEqual(self.config.stick("right")["curve"], {"start": (0, 0), "end": (255, 255), "points": []})
        self.config.set_stick("left", center=90, edge=0)
        self.assertEqual(self.config.stick("left")["center"], 90)

    def test_window_changes_rescale_stick_midpoints(self):
        self.config.set_stick("left", point1=(32, 90), point2=(96, 110))
        original_points = self.config.response_curve("stick", "left")["points"]
        self.config.set_stick("left", center=50, edge=10)
        curve = self.config.response_curve("stick", "left")
        self.assertTrue(all(curve["start"][0] <= x <= curve["end"][0] for x, _ in curve["points"]))
        self.assertEqual([y for _, y in curve["points"]], [y for _, y in original_points])
        self.config.set_stick("left", center=100)
        self.assertEqual(self.config.stick("left")["center"], 90)
        self.assertEqual(self.config.stick("left")["bank"][-1], 150)

    def test_trigger_facade_and_explicit_nonlinear_response(self):
        self.config.set_trigger_curve("right", zero=200, end=30)
        self.assertEqual(self.config.trigger_curve("right")["curve"],
                         {"start": (30, 0), "end": (200, 255), "points": []})
        self.config.set_response_curve("trigger", "right", points=[(50, 230)])
        self.config.set_trigger_curve("right", end=220, mirror_points=False)
        self.assertEqual(self.config.trigger_curve("right")["curve"]["points"], [(50, 230)])
        before = bytes(self.config.blob)
        with self.assertRaises(ValueError):
            self.config.set_trigger_curve("right", zero=80, mirror_points=False)
        self.assertEqual(self.config.blob, before)
        self.config.set_trigger_curve("right", zero=80)
        self.assertEqual(self.config.trigger_curve("right")["curve"]["points"], [])
        self.config.set_trigger_curve("right", zero=220)
        self.assertEqual(self.config.trigger_curve("right")["zero"], 220)

    def test_bad_inputs_fail_atomically(self):
        edits = [lambda: self.config.set_stick("middle", center=10),
                 lambda: self.config.set_stick("left", circular=1),
                 lambda: self.config.set_stick("left", invert_x=True, edge=101),
                 lambda: self.config.set_stick("left", center=True),
                 lambda: self.config.set_stick("left", center=60, edge=60),
                 lambda: self.config.set_stick("left", curve_type=4),
                 lambda: self.config.set_stick("left", point1=(128, 0)),
                 lambda: self.config.set_trigger_curve("left", end=-1),
                 lambda: self.config.set_response_curve("motor", "left", start=(0, 0)),
                 lambda: self.config.set_response_curve("stick", "left", points=[(10, 20)] * 3),
                 lambda: self.config.set_response_curve("stick", "left", points=[(100, 20), (50, 30)]),
                 lambda: self.config.set_response_curve("stick", "left", start=(255, 0), end=(200, 255)),
                 lambda: self.config.set_response_curve("stick", "left", start=(0.5, 0)),
                 lambda: self.config.set_response_curve("stick", "left", start=(0,))]
        for edit in edits:
            with self.assertRaises(ValueError):
                edit()
            self.assertEqual(self.config.blob, self.original)

    def test_ambiguous_or_unsupported_response_is_never_rewritten(self):
        original = bare(self.original)
        records = extensions(self.original)
        cases = [original[:-2] + records[1] + original[-2:],
                 original[:40] + b"\x01\x01\0" + original[54:]]
        bad_count = bytearray(original)
        bad_count[46] = 3
        cases.append(bad_count)
        for data in cases:
            blob = gpa6_config.wrap_blob(data)
            config = gpa6_profile.MappingConfig(blob)
            with self.assertRaises(gpa6.ProtocolError):
                config.set_stick("left", circular=True)
            self.assertEqual(config.blob, blob)
            self.assertIn("decode_error", gpa6_profile.describe(blob)["response"]["stick"]["left"])
            # An unfamiliar left curve must not destroy button-edit support.
            mapped = gpa6_profile.remap(blob, "m1", "b")
            self.assertEqual(bare(mapped)[49:], data[40:])


class TransactionTests(unittest.TestCase):
    @staticmethod
    def edit(blob):
        config = gpa6_profile.MappingConfig(blob)
        config.set_stick("left", center=15, invert_y=True)
        config.set_trigger_curve("right", zero=30, end=200)
        return config.blob

    def test_write_readback_save_and_exact_restoration(self):
        pad = captured_pad()
        before = {ram: pad.ram(ram) for ram in (1, 4, 5, 6)}
        after = gpa6_config.edit_ram(pad, 5, self.edit, save=True, expected_uid=pad.uid)
        self.assertEqual(after, pad.ram(5))
        self.assertEqual(gpa6_profile.MappingConfig(after).stick("left")["center"], 15)
        self.assertEqual(pad.stored_configs[0][5], after)
        for ram in (1, 4, 6):
            self.assertEqual(pad.ram(ram), before[ram])
        gpa6_config.edit_ram(pad, 5, lambda _: before[5], save=True)
        pad.power_cycle()
        self.assertEqual(pad.ram(5), before[5])

    def test_lost_commit_ack_restores_all_original_response_bytes(self):
        pad = captured_pad()
        before = pad.ram(5)
        wanted = self.edit(before)
        chunks = (len(wanted) + 15) // 16
        pad.faults[0xa4] = deque([None] * (1 + chunks) + ["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            gpa6_config.edit_ram(pad, 5, self.edit)
        self.assertEqual(pad.ram(5), before)
        self.assertIsNone(pad.transfer)

    def test_invalid_edit_sends_no_write_and_releases_claim(self):
        pad = captured_pad()
        before = pad.ram(5)
        def invalid(blob):
            config = gpa6_profile.MappingConfig(blob)
            config.set_stick("left", center=101)
            return config.blob
        with self.assertRaises(ValueError):
            gpa6_config.edit_ram(pad, 5, invalid)
        self.assertFalse(any(command == 0xa4 for command, _ in pad.history))
        self.assertEqual(pad.ram(5), before)
        gpa6_config.edit_ram(pad, 5, self.edit)


class CommandTests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"),
                               "--device", "mock:pad0", *args],
                              env={**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6"},
                              capture_output=True, text=True)

    def test_reads_need_no_opt_in_and_all_writes_do(self):
        for control in ("stick", "trigger"):
            result = self.run_command(control, "left")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["curve"]["start"], [0, 0])
            for options in (("--save",), ("--clear-points",), ("--curve-start", "10", "0")):
                result = self.run_command(control, "right", *options)
                self.assertEqual(result.returncode, 2)
                self.assertIn("require --experimental", result.stderr)
        for control, options in (("stick", ("--center", "10")), ("stick", ("--invert-x", "off")),
                                 ("trigger", ("--end", "200"))):
            self.assertEqual(self.run_command(control, "left", *options).returncode, 2)

    def test_cli_edits_native_and_existing_controls(self):
        cases = [("stick", "--center", "20", "--edge", "10", "--invert-y", "on"),
                 ("trigger", "--zero", "30", "--end", "200", "--save"),
                 ("trigger", "--curve-start", "13", "7", "--curve-end", "231", "247", "--point", "63", "88")]
        for control, *options in cases:
            result = self.run_command("--experimental", control, "left", *options)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["present"])
            self.assertIn("acknowledged" if "--save" in options else "verified", result.stderr)
        self.assertEqual(json.loads(result.stdout)["curve"], {"start": [13, 7], "end": [231, 247], "points": [[63, 88]]})

    def test_cli_rejects_invalid_or_conflicting_edits_cleanly(self):
        for options in (("--center", "101"), ("--point", "5", "8", "--clear-points"),
                        ("--preset", "instant", "--curve-start", "10", "0"),
                        ("--point", "90", "50", "--point", "20", "60")):
            result = self.run_command("--experimental", "stick", "left", *options)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
