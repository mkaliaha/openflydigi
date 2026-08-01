#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the Apex 5 -> DualSense mapping. No hardware required.

    python3 tests/test_relay.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json                                        # noqa: E402
import tempfile                                     # noqa: E402

from flydigi import ds5, evdev, mock, relay  # noqa: E402


def open_ctrl_tests(results):
    """The real `PadLink._open_ctrl`, which `FakeLink` above replaces.

    Everything else in this file points that seam at `FakeBus`, so none of it
    says anything about what the method itself does -- and what it does now is
    take the identify read before handing back a handle the relay will write
    trigger effects and rumble to for a whole play session. An unpinned relay
    opens whichever Flydigi pad answered first, and every pad of this
    generation answers the same way.

    `hide_real` is not optional here. Mock devices enumerate *after* real ones,
    so on a desk with an Apex 5 plugged in this test would otherwise open it
    and assert against the developer's own hardware.
    """
    def with_bus(devices, body):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w") as fh:
            json.dump({"hide_real": True, "devices": devices}, fh)
        os.environ[mock.ENV] = path
        mock.reset()
        try:
            body()
        finally:
            os.environ.pop(mock.ENV, None)
            mock.reset()
            os.unlink(path)

    def a_pad_we_drive():
        link = relay.PadLink()
        ctrl, error = link._open_ctrl()
        results.append(check("an Apex 5 opens for the relay",
                             ctrl is not None and error is None, str(error)))
        results.append(check("and its force triggers are driven",
                             link.has_triggers))
        if ctrl is not None:
            ctrl.close()

    def a_pad_we_do_not():
        # A Vader relays perfectly well -- input, rumble, haptic audio to the
        # motors and the gyro are the same on both pads. What it has not got is
        # force triggers, so it is the trigger half that is switched off and
        # not the session, which is what `has_triggers` is for.
        link = relay.PadLink()
        ctrl, error = link._open_ctrl()
        results.append(check("a Vader opens for the relay too",
                             ctrl is not None and error is None, str(error)))
        results.append(check("but its trigger half is off",
                             not link.has_triggers))
        if ctrl is not None:
            ctrl.close()

    with_bus([{"kind": "pad", "code": "k5", "nickname": "Desk"}], a_pad_we_drive)
    with_bus([{"kind": "pad", "code": "f5", "nickname": "Vader"}], a_pad_we_do_not)


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


class FakeBus:
    """A pad that can leave the USB bus and come back, with a clock to match.

    The whole point of `PadLink` is what happens over seconds of wall-clock --
    a pad sleeps, a second passes, a retry finds it again -- so the tests drive
    time by hand rather than sleeping through it.
    """

    def __init__(self):
        self.present = True
        self.ctrl_present = True
        self.time = 100.0
        self.opens = 0
        self.closed = []

    def now(self):
        return self.time

    def tick(self, seconds):
        self.time += seconds


class FakeNode:
    """Either of the two descriptors, standing in for a Reader or Controller."""

    def __init__(self, bus, path, fd):
        self.bus = bus
        self.path = path
        self.fd = fd
        self.frames = 0

    def fileno(self):
        return self.fd

    def read(self):
        if not self.bus.present:
            raise OSError(19, "No such device")
        self.frames += 1
        return True

    def send(self, _buf, wait=0.0, until=None):
        if not self.bus.present:
            raise OSError(19, "No such device")
        # Enough of a reply for motion.enable to call itself successful.
        return [bytes([0x04, 0x5A, 0xA5, 17, 0, 1, 0])]

    def close(self):
        self.bus.closed.append(self.path)


class FakeLink(relay.PadLink):
    """PadLink with the two device-opening seams pointed at a FakeBus."""

    def __init__(self, bus, **kwargs):
        self.bus = bus
        kwargs.setdefault("clock", bus.now)
        super().__init__(**kwargs)

    def _open_reader(self):
        if not self.bus.present:
            return None, None, "not on the bus"
        self.bus.opens += 1
        return FakeNode(self.bus, "/dev/input/event9", 30), "Flydigi Apex 5", None

    def _open_ctrl(self):
        if not (self.bus.present and self.bus.ctrl_present):
            return None, "no vendor node"
        # The real one sets this from the identify read. Modelled as an Apex 5,
        # since that is what every test below is about; leaving it False would
        # quietly exercise the Vader path in tests that are not about it.
        self.has_triggers = True
        return FakeNode(self.bus, "/dev/hidraw7", 31), None

    def _node_present(self):
        return self.bus.present


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


def link_tests(results):
    """Everything about surviving the pad leaving the bus."""

    # `release` is what the game is fed while the pad is away.
    state = relay.build_state(
        FakeReader([evdev.BTN_SOUTH], analog={evdev.ABS_Z: 1.0}), ds5.InputState())
    state.battery_charge = 4
    state.battery_status = ds5.BATTERY_DISCHARGING
    relay.release(state)
    results.append(check("release clears held buttons",
                         state.buttons0 == state.buttons1 == state.buttons2 == 0))
    results.append(check("release centres the sticks and drops the triggers",
                         (state.lx, state.ly, state.rx, state.ry,
                          state.l2, state.r2)
                         == (ds5.AXIS_NEUTRAL,) * 4 + (0, 0)))
    results.append(check("release leaves the battery reading alone",
                         (state.battery_charge, state.battery_status)
                         == (4, ds5.BATTERY_DISCHARGING)))

    # A read error is a disconnection, not a fatal error.
    bus = FakeBus()
    link = FakeLink(bus, want_motion=False)
    connected, _problem = link.open()
    results.append(check("opens what is there", connected and link.connected))
    first_generation = link.generation

    bus.present = False
    results.append(check("a read on a pad that left the bus does not raise",
                         link.read_input() is False))
    results.append(check("...and marks it broken rather than dying", link.broken))
    results.append(check("poll turns broken into gone", link.poll() is True))
    results.append(check("gone means disconnected", not link.connected))
    results.append(check("gone means nothing to select on", link.fds() == []))
    results.append(check("the disconnection is counted", link.drops == 1))
    results.append(check("both descriptors were closed",
                         bus.closed == ["/dev/input/event9", "/dev/hidraw7"],
                         str(bus.closed)))

    # It does not thrash the filesystem while the pad is away.
    scans = bus.opens
    results.append(check("no retry before the interval is up", link.poll() is False))
    bus.tick(relay.PadLink.RETRY_INTERVAL + 0.01)
    results.append(check("still nothing to find while it is off the bus",
                         link.poll() is False))
    results.append(check("...though it did look", bus.opens == scans))

    # And it comes back on its own.
    bus.present = True
    bus.tick(relay.PadLink.RETRY_INTERVAL + 0.01)
    results.append(check("poll picks the pad back up", link.poll() is True))
    results.append(check("connected again", link.connected and link.ctrl is not None))
    results.append(check("a returning pad is a new generation, so a caller "
                         "knows to send its effects again",
                         link.generation > first_generation))
    results.append(check("reading works again", link.read_input() is True))

    # The backstop: a pad nobody is reading from still has to be noticed, or an
    # idle pad would look connected until a button press that never comes.
    bus.present = False
    bus.tick(relay.PadLink.CHECK_INTERVAL + 0.01)
    results.append(check("a vanished node is noticed with no read at all",
                         link.poll() is True and not link.connected))
    results.append(check("closing an absent pad does not raise",
                         link.close() is None))

    # Partial arrival: the gamepad node and the vendor node come back
    # separately, and input must not wait for effects.
    bus = FakeBus()
    bus.ctrl_present = False
    link = FakeLink(bus, want_motion=False)
    connected, problem = link.open()
    results.append(check("input alone counts as connected",
                         connected and link.ctrl is None))
    results.append(check("...and says what is missing", bool(problem), repr(problem)))
    generation = link.generation
    bus.ctrl_present = True
    bus.tick(relay.PadLink.RETRY_INTERVAL + 0.01)
    results.append(check("a late vendor node is picked up rather than written "
                         "off for the session",
                         link.poll() is True and link.ctrl is not None))
    results.append(check("...and counts as a new generation",
                         link.generation > generation))

    # Motion is re-enabled by the reopen, not left to the caller to remember.
    bus = FakeBus()
    link = FakeLink(bus, want_motion=True)
    link.open()
    results.append(check("motion enabled on open", link.motion_on))
    results.append(check("motion fd is watched", len(link.fds()) == 2))
    bus.present = False
    bus.tick(relay.PadLink.CHECK_INTERVAL + 0.01)
    link.poll()
    results.append(check("motion is off while the pad is away",
                         not link.motion_on and not link.connected))
    bus.present = True
    bus.tick(relay.PadLink.RETRY_INTERVAL + 0.01)
    link.poll()
    results.append(check("motion is enabled again on reconnect",
                         link.motion_on and link.connected))


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

    link_tests(results)
    open_ctrl_tests(results)

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
