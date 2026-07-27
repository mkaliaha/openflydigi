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

    # Face buttons must map positionally, not by label.
    state = relay.build_state(FakeReader([evdev.BTN_WEST]), ds5.InputState())
    results.append(check("BTN_WEST -> SQUARE", bool(state.buttons0 & ds5.SQUARE)))
    state = relay.build_state(FakeReader([evdev.BTN_NORTH]), ds5.InputState())
    results.append(check("BTN_NORTH -> TRIANGLE",
                         bool(state.buttons0 & ds5.TRIANGLE)))

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

    # Effect translation
    off = relay.translate(ds5.TriggerEffect("right", 0x00, bytes(10)))
    results.append(check("DS5 'off' -> Flydigi normal",
                         off == (2, 0, [0, 0, 0, 0]), str(off)))
    rigid = relay.translate(ds5.TriggerEffect("left", 0x01, bytes([90, 200] + [0] * 8)))
    results.append(check("DS5 rigid -> race with start/force",
                         rigid[0] == 1 and rigid[1] == 1 and rigid[2][:2] == [90, 200],
                         str(rigid)))
    weapon = relay.translate(ds5.TriggerEffect("right", 0x02, bytes([10, 20, 150] + [0] * 7)))
    results.append(check("DS5 weapon -> vibration mode",
                         weapon[1] == 2 and weapon[2][2] == 150, str(weapon)))
    unknown = relay.translate(ds5.TriggerEffect("right", 0xEE, bytes(10)))
    results.append(check("unknown effect type falls back to normal",
                         unknown[1] == 0, str(unknown)))
    zero = relay.translate(ds5.TriggerEffect("left", 0x01, bytes(10)))
    results.append(check("zero force clamped to at least 1",
                         zero[2][1] >= 1, str(zero)))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
