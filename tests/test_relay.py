#!/usr/bin/env python3
"""Self-test for the Apex 5 -> DualSense mapping. No hardware required.

    python3 tests/test_relay.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import ds5, evdev, relay  # noqa: E402


class FakeReader:
    """Stands in for evdev.Reader with fixed button/axis state."""

    def __init__(self, keys=(), axes=None, analog=None):
        self.keys = {code: 1 for code in keys}
        self.axes = axes or {}
        self._analog = analog or {}

    def axis(self, code, lo=0.0, hi=1.0, default=0.0):
        if code in self._analog:
            frac = self._analog[code]
            return lo + frac * (hi - lo)
        return default

    def pressed(self, code):
        return bool(self.keys.get(code))


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


def main():
    results = []

    # Touchpad / Create mapping -- the one binding we invented, so worth pinning.
    state = relay.build_state(FakeReader([evdev.BTN_SELECT]), ds5.InputState())
    results.append(check("SELECT alone -> TOUCHPAD click",
                         bool(state.buttons2 & ds5.TOUCHPAD)))
    results.append(check("SELECT alone does not set CREATE",
                         not state.buttons1 & ds5.CREATE))

    state = relay.build_state(
        FakeReader([evdev.BTN_SELECT, evdev.BTN_START]), ds5.InputState())
    results.append(check("SELECT+START chord -> CREATE",
                         bool(state.buttons1 & ds5.CREATE)))
    results.append(check("chord suppresses TOUCHPAD",
                         not state.buttons2 & ds5.TOUCHPAD))
    results.append(check("chord suppresses OPTIONS (its own component)",
                         not state.buttons1 & ds5.OPTIONS,
                         f"buttons1={state.buttons1:#x}"))

    # The guide button stays a plain PS button -- deliberately not in any chord,
    # since firmware mode-switch and the Steam overlay both claim it.
    state = relay.build_state(FakeReader([evdev.BTN_MODE]), ds5.InputState())
    results.append(check("MODE -> PS button only",
                         state.buttons2 == ds5.PS_HOME and state.buttons1 == 0))

    state = relay.build_state(FakeReader([evdev.BTN_START]), ds5.InputState())
    results.append(check("START alone -> OPTIONS",
                         bool(state.buttons1 & ds5.OPTIONS)
                         and not state.buttons1 & ds5.CREATE))

    state = relay.build_state(FakeReader(), ds5.InputState())
    results.append(check("idle -> nothing pressed",
                         state.buttons0 == 0 and state.buttons1 == 0
                         and state.buttons2 == 0))

    # Face buttons map by physical position. The evdev compass aliases are
    # inverted for Xbox-layout pads (0x133 = X = left, 0x134 = Y = top), so
    # these pin the raw codes rather than the alias names.
    state = relay.build_state(FakeReader([0x133]), ds5.InputState())
    results.append(check("code 0x133 (Xbox X, left) -> SQUARE (left)",
                         bool(state.buttons0 & ds5.SQUARE)
                         and not state.buttons0 & ds5.TRIANGLE,
                         f"buttons0={state.buttons0:#x}"))
    state = relay.build_state(FakeReader([0x134]), ds5.InputState())
    results.append(check("code 0x134 (Xbox Y, top) -> TRIANGLE (top)",
                         bool(state.buttons0 & ds5.TRIANGLE)
                         and not state.buttons0 & ds5.SQUARE,
                         f"buttons0={state.buttons0:#x}"))
    state = relay.build_state(FakeReader([0x130]), ds5.InputState())
    results.append(check("code 0x130 (A, bottom) -> CROSS (bottom)",
                         bool(state.buttons0 & ds5.CROSS)))
    state = relay.build_state(FakeReader([0x131]), ds5.InputState())
    results.append(check("code 0x131 (B, right) -> CIRCLE (right)",
                         bool(state.buttons0 & ds5.CIRCLE)))

    # Digital L2/R2 derived from analog travel.
    state = relay.build_state(
        FakeReader(analog={evdev.ABS_Z: 0.5, evdev.ABS_RZ: 0.0}), ds5.InputState())
    results.append(check("half-pulled L2 sets digital L2 and analog value",
                         bool(state.buttons1 & ds5.L2) and state.l2 == 127,
                         f"l2={state.l2} buttons1={state.buttons1:#x}"))
    results.append(check("released R2 stays clear",
                         not state.buttons1 & ds5.R2 and state.r2 == 0))
    state = relay.build_state(
        FakeReader(analog={evdev.ABS_Z: 0.05}), ds5.InputState())
    results.append(check("light L2 touch below threshold stays digital-off",
                         not state.buttons1 & ds5.L2))

    # Dpad
    state = relay.build_state(
        FakeReader(axes={evdev.ABS_HAT0X: 1, evdev.ABS_HAT0Y: -1}), ds5.InputState())
    results.append(check("dpad up-right -> HAT_NE", state.hat == ds5.HAT_NE))

    # Effect translation, checked against Flydigi's PS5DataManager table.
    def eff(side, type_, params):
        return ds5.TriggerEffect(side, type_, bytes(list(params) + [0] * (10 - len(params))))

    cases = [
        # (name, side, type, params, expected (side_id, mode, params5))
        ("type 1 rigid -> mode 1, params passthrough",
         "right", 1, [90, 200], (2, 1, [90, 200, 0, 0, 0])),
        ("type 2 weapon -> mode 3 (not 2)",
         "right", 2, [10, 20, 150], (2, 3, [10, 20, 150, 0, 0])),
        ("type 5 off -> mode 0",
         "right", 5, [], (2, 0, [0, 0, 0, 0, 0])),
        ("type 6 vibration -> mode 2 with reordered params",
         "right", 6, [11, 22, 33], (2, 2, [33, 22, 22, 11, 0])),
        ("left type 6 reorders the same way",
         "left", 6, [11, 22, 33], (1, 2, [33, 22, 22, 11, 0])),
        ("right type 33, p0=0 -> preset 120/1",
         "right", 33, [0], (2, 1, [120, 1, 0, 0, 0])),
        ("right type 33, ff/03/ff -> preset 110/50 overrides",
         "right", 33, [255, 3, 255], (2, 1, [110, 50, 0, 0, 0])),
        ("right type 37, p0=20 p2=2 -> preset",
         "right", 37, [20, 0, 2], (2, 3, [70, 20, 20, 0, 0])),
        ("right type 37, p0=36 p2=4 -> stepped strength",
         "right", 37, [36, 0, 4], (2, 3, [10, 36, 50, 0, 0])),
        ("right type 37 default -> passthrough shape",
         "right", 37, [99, 0, 7], (2, 3, [64, 99, 0, 7, 1])),
        ("left type 37 is simple, unlike right",
         "left", 37, [99, 0, 7], (1, 3, [64, 99, 0, 7, 1])),
        ("right type 38 -> inverted p0",
         "right", 38, [10, 1], (2, 2, [245, 1, 60, 0, 0])),
    ]
    for name, side, type_, params, expected in cases:
        got = relay.translate(eff(side, type_, params))
        results.append(check(name, got == expected, f"got {got}, want {expected}"))

    # Rumble must register when only USE_RUMBLE_NOT_HAPTICS (0x02) is set, and
    # when trigger bits share the report -- not just on MOTOR (0x01) alone.
    def out(flag0):
        b = bytearray(64); b[0] = 0x02; b[1] = flag0; b[3] = 77; b[4] = 180
        return ds5.parse_output(bytes(b))["rumble"]
    results.append(check("rumble on MOTOR bit", out(0x01) == (180, 77)))
    results.append(check("rumble on USE_RUMBLE_NOT_HAPTICS alone", out(0x02) == (180, 77)))
    results.append(check("rumble alongside trigger bits", out(0x0D) == (180, 77)))
    results.append(check("no rumble when no motor flags", out(0x00) is None))

    # Unrecognised types must leave the trigger alone, not clear it.
    results.append(check("unknown type -> None (trigger untouched)",
                         relay.translate(eff("right", 0xEE, [])) is None))
    results.append(check("left type 38 ff/03/../ff marked invalid -> None",
                         relay.translate(eff("left", 38, [255, 3, 0, 255])) is None))

    # Left type 38 reuses the stashed motor value.
    got = relay.translate(eff("left", 38, [240, 3, 0, 0]), left_motor=77)
    results.append(check("left type 38 reuses stored motor_left",
                         got == (1, 1, [30, 77, 0, 0, 0]), str(got)))

    # Left type 33 overrides run after the main branch.
    got = relay.translate(eff("left", 33, [128, 0, 0, 0, 55]))
    results.append(check("left type 33 p0=128 override uses p[4]",
                         got == (1, 1, [128, 55, 0, 0, 0]), str(got)))

    # Short parameter blocks must not raise.
    results.append(check("short parameter block tolerated",
                         relay.translate(ds5.TriggerEffect("right", 1, b"\x05")) is not None))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
