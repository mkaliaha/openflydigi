# SPDX-FileCopyrightText: 2026 tunk3r
#
# SPDX-License-Identifier: MIT

"""DualSense trigger effect decoding and voice-coil rendering."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flydigi import ds5_triggers as triggers
from flydigi.ds5_triggers import OFF, Renderer, decode


def zones(*indices):
    mask = sum(1 << i for i in indices)
    return [mask & 255, mask >> 8]


def packed(values):
    """Ten zone strengths 1..8 (0 = inactive) as the 0x21/0x26 layout."""
    mask = packed_value = 0
    for i, value in enumerate(values):
        if value:
            mask |= 1 << i
            packed_value |= (value - 1) << 3 * i
    return [mask & 255, mask >> 8] + list(packed_value.to_bytes(4, "little"))


def crossings(samples):
    return sum(1 for a, b in zip(samples, samples[1:]) if a < 0 <= b)


def render(renderer, positions, start, seconds):
    out = []
    for frame in range(int(seconds * 1000) // 8):
        samples, _ = renderer.render(positions, start + frame * .008)
        out += samples or [0.0] * 8
    return out


class DecodeTests(unittest.TestCase):
    def test_off_and_unknown(self):
        self.assertIs(decode(0x05, bytes(10)), OFF)
        self.assertIs(decode(0x00, bytes(10)), OFF)
        self.assertIsNone(decode(0xFC, bytes(10)))
        self.assertIs(decode(0x21, bytes(10)), OFF)
        self.assertIs(decode(0x25, bytes(zones(4) + [7] + [0] * 7)), OFF)

    def test_feedback_zones(self):
        effect = decode(0x21, bytes(packed([0, 0, 0, 5, 5, 5, 8, 8, 8, 8]) + [0] * 4))
        self.assertEqual(effect.kind, "resistance")
        self.assertEqual(effect.levels, [0, 0, 0, .625, .625, .625, 1, 1, 1, 1])

    def test_weapon_and_bow(self):
        effect = decode(0x25, bytes(zones(2, 6) + [7] + [0] * 7))
        self.assertEqual((effect.kind, effect.start, effect.end, effect.level), ("weapon", 52, 154, 1))
        bow = decode(0x22, bytes(zones(1, 8) + [(3 << 3) | 1, 0] + [0] * 6))
        self.assertEqual((bow.kind, bow.level, bow.snap), ("weapon", .25, .5))

    def test_vibration_galloping_machine(self):
        vib = decode(0x26, bytes(packed([0, 0, 4, 4, 4, 4, 4, 4, 4, 4]) + [0, 0, 40, 0]))
        self.assertEqual((vib.kind, vib.hz, vib.levels[1], vib.levels[2]), ("vibration", 40, 0, .5))
        gallop = decode(0x23, bytes(zones(0, 9) + [(1 << 3) | 4, 3] + [0] * 6))
        self.assertEqual((gallop.kind, gallop.first, gallop.second, gallop.hz), ("galloping", 1, 4, 3))
        machine = decode(0x24, bytes(zones(1, 9) + [(2 << 3) | 7, 60, 5] + [0] * 5))
        self.assertEqual((machine.kind, machine.level, machine.level_b, machine.hz, machine.period),
                         ("machine", 1, 2 / 7, 60, .5))

    def test_legacy_effects(self):
        self.assertEqual(decode(0x01, bytes((100, 255) + (0,) * 8)).level, 1)
        self.assertEqual(decode(0x11, bytes((100, 5) + (0,) * 8)).level, .5)
        weapon = decode(0x02, bytes((40, 160, 255) + (0,) * 7))
        self.assertEqual((weapon.start, weapon.end), (40, 160))
        self.assertIs(decode(0x02, bytes((160, 40, 255) + (0,) * 7)), OFF)
        vib = decode(0x06, bytes((30, 255, 0) + (0,) * 7))
        self.assertEqual((vib.hz, vib.level, vib.start), (30, 1, 0))


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.renderer = Renderer()

    def test_idle_disables_column(self):
        self.assertEqual(self.renderer.render((0, 0), 0), (None, None))

    def test_resistance_follows_position_and_is_bounded(self):
        effect = decode(0x21, bytes(packed([0, 0, 0, 0, 0, 8, 8, 8, 8, 8]) + [0] * 4))
        self.renderer.side("right").set(effect, 0)
        samples, selector = self.renderer.render((0, 50), 0)
        self.assertEqual((selector, max(map(abs, samples))), (1, 0))
        tone = render(self.renderer, (0, 200), 0, .2)
        self.assertGreater(max(tone), .4)
        self.assertLessEqual(max(map(abs, tone)), triggers.RESIST_MAX)
        self.assertIn(crossings(tone), (16, 17))  # 85 Hz over 200 ms

    def test_weapon_bursts_once_per_pull(self):
        self.renderer.side("left").set(decode(0x25, bytes(zones(2, 6) + [7] + [0] * 7)), 0)
        self.renderer.render((100, 0), 0)
        burst, selector = self.renderer.render((200, 0), .008)
        self.assertEqual(selector, 0)
        self.assertGreater(max(burst), .5)
        after = render(self.renderer, (200, 0), .04, .1)
        self.assertEqual(max(map(abs, after)), 0)
        # Easing off inside the zone does not re-arm; releasing below it does.
        self.renderer.render((100, 0), .2)
        self.renderer.render((200, 0), .208)
        self.assertAlmostEqual(self.renderer.left.burst_until, .008 + triggers.BURST_S)
        self.renderer.render((20, 0), .3)
        self.renderer.render((200, 0), .308)
        self.assertAlmostEqual(self.renderer.left.burst_until, .308 + triggers.BURST_S)

    def test_off_is_held_and_cancelled_by_new_effect(self):
        left = self.renderer.side("left")
        vib = decode(0x06, bytes((40, 255, 0) + (0,) * 7))
        left.set(vib, 0)
        left.set(OFF, .01)
        self.assertEqual(self.renderer.render((0, 0), .05)[1], 0)
        self.assertEqual(self.renderer.render((0, 0), .2), (None, None))
        left.set(vib, .3)
        left.set(OFF, .31)
        left.set(vib, .35)
        self.assertEqual(self.renderer.render((0, 0), .5)[1], 0)

    def test_both_active_sum_routes_both_and_stays_bounded(self):
        vib = decode(0x06, bytes((50, 255, 0) + (0,) * 7))
        self.renderer.side("left").set(vib, 0)
        self.renderer.side("right").set(vib, 0)
        tone = []
        for frame in range(25):
            samples, selector = self.renderer.render((0, 0), frame * .008)
            self.assertEqual(selector, 2)
            tone += samples
        self.assertAlmostEqual(max(map(abs, tone)), triggers.LIMIT)
        self.assertIn(crossings(tone), (9, 10))  # 50 Hz over 200 ms

    def test_galloping_and_machine_patterns(self):
        self.renderer.side("left").set(decode(0x23, bytes(zones(0, 9) + [(0 << 3) | 4, 2] + [0] * 6)), 0)
        tone = render(self.renderer, (100, 0), 0, 1)
        active = [i for i, x in enumerate(tone) if x]
        starts = [i for i in active if i - 1 not in active]
        self.assertEqual(len(starts), 4)  # two feet per cycle, 2 Hz
        self.renderer.clear()
        self.renderer.side("left").set(decode(0x24, bytes(zones(1, 9) + [(1 << 3) | 7, 50, 2] + [0] * 5)), 0)
        tone = render(self.renderer, (100, 0), 0, .4)
        self.assertGreater(max(tone[:200]), .6)
        self.assertLess(max(tone[200:400]), .15)
        self.assertEqual(render(self.renderer, (0, 0), 1, .1), [0.0] * 96)


if __name__ == "__main__":
    unittest.main()
