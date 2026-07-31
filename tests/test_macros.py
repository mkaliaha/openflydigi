#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the macro recorder. No controller and no evdev node needed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import evdev, macros, mapping
from tests.fake_pad import blank_blob

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


class FakeReader:
    """A `Reader` that replays scripted frames.

    Real one or not, `Recorder.poll` selects on a descriptor first, so this
    keeps a pipe with one byte per frame -- otherwise the select would block
    for the timeout on every call and the test would take as long as the
    recording it is faking.
    """

    def __init__(self, frames):
        self.frames = list(frames)
        self.keys = {}
        self.axes = {}
        self._read_fd, self._write_fd = os.pipe()
        os.write(self._write_fd, b"." * len(self.frames))

    def fileno(self):
        return self._read_fd

    def read(self):
        if not self.frames:
            return False
        os.read(self._read_fd, 1)
        frame = self.frames.pop(0)
        for code, value in frame.items():
            if code in (evdev.ABS_HAT0X, evdev.ABS_HAT0Y, evdev.ABS_Z,
                        evdev.ABS_RZ):
                self.axes[code] = value
            else:
                self.keys[code] = value
        return True

    def pressed(self, code):
        return bool(self.keys.get(code))

    def axis(self, code, lo=0.0, hi=1.0, default=0.0):
        # Triggers are read as a 0..1 fraction of travel; the fake stores that
        # fraction directly rather than raw axis units.
        return self.axes.get(code, default)

    def close(self):
        os.close(self._read_fd)
        os.close(self._write_fd)


class FakeClock:
    """Monotonic seconds, advanced by hand."""

    def __init__(self, step=0.1):
        self.now = 0.0
        self.step = step

    def __call__(self):
        value = self.now
        self.now += self.step
        return value


def record(frames, step=0.1):
    reader = FakeReader(frames)
    clock = FakeClock(step)
    recorder = macros.Recorder(reader, clock=clock)
    try:
        for _ in range(len(frames)):
            recorder.poll(timeout=0.05)
        return recorder.finish()
    finally:
        reader.close()


def test_a_press_and_release_becomes_two_steps():
    steps = record([{evdev.BTN_SOUTH: 1}, {evdev.BTN_SOUTH: 0}], step=0.1)
    check("two steps", len(steps) == 2, str(steps))
    check("the first is a press",
          steps[0] == {"delay": 0, "key": "a", "event": mapping.MACRO_PRESS},
          str(steps[0]))
    check("the second is a release",
          steps[1]["event"] == mapping.MACRO_RELEASE and steps[1]["key"] == "a")
    check("and carries the gap in milliseconds", steps[1]["delay"] == 100,
          str(steps[1]["delay"]))


def test_nothing_waits_before_the_first_step():
    """A recording starts when the first button does, not when Record does."""
    steps = record([{}, {}, {evdev.BTN_EAST: 1}, {evdev.BTN_EAST: 0}], step=0.25)
    check("idle frames before the first press are dropped",
          len(steps) == 2, str(steps))
    check("the first step has no delay", steps[0]["delay"] == 0,
          str(steps[0]["delay"]))


def test_the_dpad_records_as_four_keys():
    steps = record([{evdev.ABS_HAT0Y: -1}, {evdev.ABS_HAT0Y: 0},
                    {evdev.ABS_HAT0X: 1}, {evdev.ABS_HAT0X: 0}])
    keys = [step["key"] for step in steps]
    check("the hat's sign picks the key", keys == ["up", "up", "right", "right"],
          str(keys))


def test_a_trigger_is_a_button_at_half_travel():
    steps = record([{evdev.ABS_Z: 0.2}, {evdev.ABS_Z: 0.8}, {evdev.ABS_Z: 0.1}])
    check("a light touch is not a press", len(steps) == 2, str(steps))
    check("crossing the threshold presses",
          steps[0]["key"] == "lt" and steps[0]["event"] == mapping.MACRO_PRESS)
    check("falling back releases", steps[1]["event"] == mapping.MACRO_RELEASE)


def test_a_key_still_held_is_released_at_the_end():
    """Otherwise the macro plays once and leaves the button down in the game."""
    steps = record([{evdev.BTN_SOUTH: 1}, {evdev.BTN_TL: 1}])
    events = [(step["key"], step["event"]) for step in steps]
    check("both presses and both releases are there", len(steps) == 4, str(events))
    check("releases come in the order the keys went down",
          events[2:] == [("a", mapping.MACRO_RELEASE), ("lb", mapping.MACRO_RELEASE)],
          str(events))


def test_a_recording_goes_straight_into_a_profile():
    steps = record([{evdev.BTN_SOUTH: 1}, {evdev.BTN_SOUTH: 0},
                    {evdev.BTN_EAST: 1}, {evdev.BTN_EAST: 0}], step=0.05)
    config = mapping.MappingConfig(blank_blob())
    config.set_macro("m1", steps, macro_type=mapping.MACRO_ONCE)
    stored = config.macros()[0]
    check("the macro is bound to the paddle", stored["key"] == "m1")
    check("every recorded step survives", len(stored["steps"]) == len(steps),
          f"{len(stored['steps'])} of {len(steps)}")
    check("50 ms gaps quantise to five ticks",
          [s["delay"] for s in stored["steps"]] == [0, 50, 50, 50],
          str([s["delay"] for s in stored["steps"]]))


def test_describe_and_duration():
    steps = record([{evdev.BTN_SOUTH: 1}, {evdev.BTN_SOUTH: 0}], step=0.1)
    check("one line per step", len(macros.describe(steps)) == 2)
    check("a line names the event and the key",
          "press" in macros.describe(steps)[0]
          and macros.describe(steps)[0].endswith("a"),
          macros.describe(steps)[0])
    check("the duration is the sum of the gaps", macros.total_ms(steps) == 100,
          str(macros.total_ms(steps)))


def main():
    for test in (test_a_press_and_release_becomes_two_steps,
                 test_nothing_waits_before_the_first_step,
                 test_the_dpad_records_as_four_keys,
                 test_a_trigger_is_a_button_at_half_travel,
                 test_a_key_still_held_is_released_at_the_end,
                 test_a_recording_goes_straight_into_a_profile,
                 test_describe_and_duration):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
