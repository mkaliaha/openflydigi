#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Independent zone encoder fixtures, preservation and guarded-write tests."""
from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_config as config, gpa6_profile as profile
from test_apex6_editing import captured_pad

FIXTURE = json.loads((ROOT / "tests/fixtures/apex6-zones.json").read_text())


def bare(blob):
    return config.unwrap_blob(blob, b"XMUL")


def zone(target="b", *, start=64, stop=255, direction=None):
    result = {"start": start, "stop": stop, "target": target}
    if direction is not None:
        result["dir"] = direction
    return result


def sections(blob):
    result = {}
    profile._layout(blob, zone_sections=result)
    return result


class ZoneTests(unittest.TestCase):
    def setUp(self):
        self.original = config.wrap_blob(bytes.fromhex(FIXTURE["cases"][0]["before_hex"]))
        self.config = profile.MappingConfig(self.original, cfg_id=2)

    def test_complete_edits_match_independent_vendor_encoder(self):
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                current = profile.MappingConfig(bytes.fromhex(case["before_hex"]))
                getattr(current, "set_" + case["control"] + "_zones")(**case["args"])
                self.assertEqual(bare(current.blob).hex(), case["after_hex"])

    def test_read_copy_and_noop_setters_preserve_wrapped_and_bare_profiles(self):
        for blob in (self.original, bare(self.original), captured_pad().ram(5)):
            current = profile.MappingConfig(blob, cfg_id=2)
            copied = current.copy()
            for layer in copied.layer_ids():
                for side in ("left", "right"):
                    copied.set_trigger_zones(side, copied.trigger_zones(side, layer=layer), layer=layer)
                    stick = copied.stick_zones(side, layer=layer)
                    copied.set_stick_zones(side, layer=layer, **(stick or {}))
            self.assertEqual(copied.cfg_id, 2)
            self.assertEqual(copied.blob, blob)
            copied.set_trigger_zones("left", [zone()])
            self.assertEqual(current.blob, blob)

    def test_only_selected_section_changes_in_each_layer_and_side(self):
        for control in ("trigger", "stick"):
            for layer in (0, 1):
                for side in ("left", "right"):
                    before = bare(self.original) + b"\x70\x03xyz\xff\0"  # v1.8 fixture has no TLVs
                    current = profile.MappingConfig(config.wrap_blob(before))
                    start, stop = sections(current.blob)[(layer, control, side)]
                    values = [zone(), zone("x", start=128)] if control == "trigger" else [zone(direction="down"), zone("x", direction="up_left")]
                    getattr(current, "set_" + control + "_zones")(side, values, layer=layer)
                    after = bare(current.blob)
                    new_start, new_stop = sections(current.blob)[(layer, control, side)]
                    self.assertEqual(after[:new_start], before[:start])
                    self.assertEqual(after[new_stop:], before[stop:])

    def test_shrinking_stick_zones_retains_all_unused_slots(self):
        self.config.set_stick_zones("left", [zone(direction=direction) for direction in profile.DIRECTIONS], angle_mode="8way")
        before = bare(self.config.blob)
        start, stop = sections(before)[(0, "stick", "left")]
        self.config.set_stick_zones("left", [zone(direction="up", start=200)])
        after = bare(self.config.blob)
        self.assertEqual(after[start + 10:stop], before[start + 10:stop])
        self.assertEqual(after[:start + 2], before[:start + 2])
        self.assertEqual(after[stop:], before[stop:])
        self.assertEqual(len(self.config.stick_zones("left")["zones"]), 1)

    def test_unused_padding_and_target_reserved_bytes_survive(self):
        before = bytearray(bare(self.original))
        start, stop = sections(before)[(0, "stick", "right")]
        before[start + 8:start + 10] = b"\xa5\x5a"
        before[start + 10:stop] = bytes(range(49))
        current = profile.MappingConfig(config.wrap_blob(before))
        current.set_stick_zones("right", [zone("x", direction="left")])
        after = bare(current.blob)
        self.assertEqual(after[start + 8:stop], before[start + 8:stop])
        t_start, t_stop = sections(after)[(0, "trigger", "left")]
        altered = bytearray(after)
        altered[t_start + 6:t_stop] = b"\x96\x69"
        current = profile.MappingConfig(config.wrap_blob(altered))
        current.set_trigger_zones("left", [zone("x")])
        self.assertEqual(bare(current.blob)[t_start + 6:t_stop], b"\x96\x69")

    def test_mode_only_preserves_unknown_direction_and_target(self):
        before = bytearray(bare(self.original))
        start, stop = sections(before)[(0, "stick", "left")]
        before[start + 5:start + 10] = b"\xfe\xf0\x01\x02\x03"
        current = profile.MappingConfig(config.wrap_blob(before))
        decoded = current.stick_zones("left")
        self.assertEqual(decoded["zones"][0]["dir"], 254)
        self.assertEqual(decoded["zones"][0]["target"]["hex"], "f0010203")
        current.set_stick_zones("left", angle_mode="4way_overlap")
        expected = bytearray(before)
        expected[start + 1] = 2
        self.assertEqual(bare(current.blob), expected)
        # A replacement with an unknown type fails atomically; clearing works.
        with self.assertRaises(ValueError):
            current.set_stick_zones("left", decoded["zones"])
        self.assertEqual(bare(current.blob), expected)
        current.set_stick_zones("left", [])
        self.assertIsNone(current.stick_zones("left"))

    def test_clear_sections_keeps_other_data_byte_exact(self):
        for control in ("trigger", "stick"):
            for side in ("left", "right"):
                current = self.config.copy()
                before = bare(current.blob)
                start, stop = sections(before)[(1, control, side)]
                getattr(current, "set_" + control + "_zones")(side, [], layer=1)
                self.assertEqual(bare(current.blob), before[:start] + b"\0" + before[stop:])

    def test_present_empty_sections_are_readable_and_can_be_cleared(self):
        raw = bare(self.original)
        for control in ("trigger", "stick"):
            start, stop = sections(raw)[(0, control, "left")]
            empty = b"\x01\0" if control == "trigger" else b"\x01\x02\0" + bytes(range(56))
            current = profile.MappingConfig(config.wrap_blob(raw[:start] + empty + raw[stop:]))
            self.assertEqual(current.trigger_zones("left") if control == "trigger" else current.stick_zones("left")["zones"], [])
            getattr(current, "set_" + control + "_zones")("left", [])
            self.assertEqual(bare(current.blob), raw[:start] + b"\0" + raw[stop:])

    def test_native_order_overlaps_repeated_directions_and_boundary_ranges(self):
        values = [zone(start=200, stop=255), zone("x", start=0, stop=0), zone("y", start=128)]
        self.config.set_trigger_zones("left", values)
        self.assertEqual([z["start"] for z in self.config.trigger_zones("left")], [200, 0, 128])
        for value in values:
            value["dir"] = "up"
        self.config.set_stick_zones("left", values)
        self.assertEqual([z["start"] for z in self.config.stick_zones("left")["zones"]], [200, 0, 128])

    def test_references_share_button_mapping_validation(self):
        for control in ("trigger", "stick"):
            setter = getattr(self.config, "set_" + control + "_zones")
            direction = "up" if control == "stick" else None
            for target in ({"type": "layer_hold", "layer_id": 3}, {"type": "macro", "macro_id": 99}):
                before = bytes(self.config.blob)
                with self.assertRaises(ValueError):
                    setter("left", [zone(target, direction=direction)])
                self.assertEqual(self.config.blob, before)
            setter("left", [zone({"type": "macro", "macro_id": 1}, direction=direction)])
            with self.assertRaises(ValueError):
                setter("left", [zone({"type": "macro", "macro_id": 1}, direction=direction)], layer=1)
            setter("left", [zone({"type": "layer_hold", "layer_id": 1}, direction=direction)])

    def test_invalid_edits_leave_original_bytes_unchanged(self):
        attempts = [lambda: self.config.set_trigger_zones("left", [zone()] * 5),
                    lambda: self.config.set_stick_zones("left", [zone(direction="up")] * 9),
                    lambda: self.config.set_trigger_zones("left", None),
                    lambda: self.config.set_trigger_zones("middle", []),
                    lambda: self.config.set_trigger_zones("left", [], layer=True),
                    lambda: self.config.set_trigger_zones("left", [], layer=3),
                    lambda: self.config.set_trigger_zones("left", [zone(), zone(start=True)]),
                    lambda: self.config.set_trigger_zones("left", [zone(stop=256)]),
                    lambda: self.config.set_trigger_zones("left", [zone(start=150, stop=100)]),
                    lambda: self.config.set_trigger_zones("left", [zone(direction="up")]),
                    lambda: self.config.set_stick_zones("left", [zone(direction="north")]),
                    lambda: self.config.set_stick_zones("left", [zone()]),
                    lambda: self.config.set_stick_zones("left", angle_mode="unknown"),
                    lambda: self.config.set_stick_zones("left", [], angle_mode="8way")]
        for edit in attempts:
            with self.assertRaises(ValueError):
                edit()
            self.assertEqual(self.config.blob, self.original)
        absent = profile.MappingConfig(captured_pad().ram(5))
        with self.assertRaises(ValueError):
            absent.set_stick_zones("left", angle_mode="8way")

    def test_truncation_bad_counts_and_bad_modes_are_refused(self):
        raw = bare(self.original)
        ts, _ = sections(raw)[(0, "trigger", "left")]
        ss, stop = sections(raw)[(0, "stick", "left")]
        for offset, value in ((ts, 2), (ts + 1, 5), (ss + 1, 3), (ss + 2, 9)):
            altered = bytearray(raw)
            altered[offset] = value
            with self.assertRaises(gpa6.ProtocolError):
                profile.MappingConfig(altered)
        with self.assertRaises(gpa6.ProtocolError):
            profile.MappingConfig(raw[:stop - 1])

    def test_button_and_response_edits_still_preserve_zones_after_resize(self):
        self.config.set_trigger_zones("left", [zone()] * 4)
        self.config.set_stick_zones("right", [zone(direction="right")] * 8)
        before = bare(self.config.blob)
        records = sections(before)
        original_sections = {key: before[a:b] for key, (a,b) in records.items()}
        self.config.set_activators("m1", [{"type":"toggle", "target":"b"}])
        self.config.set_stick("left", center=10)
        self.config.set_trigger_curve("right", zero=20, end=230)
        after = bare(self.config.blob)
        self.assertEqual({key: after[a:b] for key, (a,b) in sections(after).items()}, original_sections)


class TransactionTests(unittest.TestCase):
    @staticmethod
    def edit(blob):
        current = profile.MappingConfig(blob)
        current.set_trigger_zones("left", [zone(start=64, stop=127), zone("x", start=128)])
        current.set_stick_zones("right", [zone(direction=d) for d in profile.DIRECTIONS], angle_mode="8way")
        return current.blob

    def test_guarded_write_save_powercycle_and_full_restoration(self):
        pad = captured_pad()
        before = {ram: pad.ram(ram) for ram in (1,4,5,6)}
        written = config.edit_ram(pad, 5, self.edit, save=True, expected_uid=pad.uid)
        pad.power_cycle()
        self.assertEqual(pad.ram(5), written)
        self.assertEqual(len(profile.MappingConfig(written).stick_zones("right")["zones"]), 8)
        for ram in (1,4,6):
            self.assertEqual(pad.ram(ram), before[ram])
        config.edit_ram(pad, 5, lambda _: before[5], save=True)
        self.assertEqual({ram: pad.ram(ram) for ram in before}, before)

    def test_lost_commit_ack_restores_whole_original_profile(self):
        pad = captured_pad()
        before = pad.ram(5)
        chunks = (len(self.edit(before)) + 15) // 16
        pad.faults[0xa4] = deque([None] * (1 + chunks) + ["drop"])
        with self.assertRaises(gpa6.ReplyTimeout):
            config.edit_ram(pad, 5, self.edit)
        self.assertEqual(pad.ram(5), before)
        self.assertIsNone(pad.transfer)

    def test_invalid_zone_never_sends_write(self):
        pad = captured_pad()
        def invalid(blob):
            current = profile.MappingConfig(blob)
            current.set_trigger_zones("left", [zone(start=256)])
            return current.blob
        with self.assertRaises(ValueError):
            config.edit_ram(pad, 5, invalid)
        self.assertFalse(any(command == 0xa4 for command, _ in pad.history))
        config.edit_ram(pad, 5, self.edit)


class CommandTests(unittest.TestCase):
    def run_command(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"), "--device", "mock:pad0", *args],
                              env={**os.environ, "FLYDIGI_MOCK_BUS": "pad:k6"}, capture_output=True, text=True)

    def test_reads_need_no_opt_in_and_all_writes_do(self):
        for command in ("trigger-zones", "stick-zones"):
            result = self.run_command(command, "left")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), [] if command == "trigger-zones" else None)
            for options in (("--clear",), ("--save",),
                            (("--zone", "up", "1", "255", "a") if command == "stick-zones" else ("--zone", "1", "255", "a"))):
                result = self.run_command(command, "right", *options)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("require --experimental", result.stderr)
        self.assertEqual(self.run_command("stick-zones", "left", "--mode", "8way").returncode, 2)

    def test_cli_edit_readback_clear_and_save(self):
        result = self.run_command("--experimental", "trigger-zones", "left", "--zone", "64", "127", "b", "--zone", "128", "255", "x", "--save")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([z["target"]["id"] for z in json.loads(result.stdout)], ["b", "x"])
        self.assertIn("save acknowledged", result.stderr)
        result = self.run_command("--experimental", "stick-zones", "right", "--mode", "8way", "--zone", "up_left", "64", "255", '{"type":"btn_keyboard","key_id":26}')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual((value["angle_mode"], value["zones"][0]["dir"]), ("8way", "up_left"))
        for command in ("trigger-zones", "stick-zones"):
            result = self.run_command("--experimental", command, "left", "--clear")
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_arguments_are_rejected(self):
        cases = [("trigger-zones", "--zone", "-1", "255", "a"),
                 ("trigger-zones", "--zone", "100", "99", "a"),
                 ("trigger-zones", "--clear", "--zone", "0", "255", "a"),
                 ("stick-zones", "--clear", "--mode", "8way"),
                 ("stick-zones", "--zone", "north", "1", "255", "b"),
                 ("trigger-zones", *(["--zone", "1", "255", "a"] * 5)),
                 ("stick-zones", *(["--zone", "up", "1", "255", "a"] * 9))]
        for command, *args in cases:
            result = self.run_command("--experimental", command, "left", *args)
            self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
