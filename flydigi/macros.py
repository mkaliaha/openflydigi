# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Recording a macro by playing it on the pad.

The steps a macro is made of live in the profile blob -- `flydigi/mapping.py`
owns that layout. This is the other half: watching the pad and turning what
someone presses into those steps.

Input comes from the xpad evdev node, the same source every relay here uses.
That is the whole button set a step may contain: a step's key has to be one a
host can receive, and M1-M4 and C/Z have no XInput id, so a paddle is a key a
macro can be *bound* to and never a key it can *press*. Space Station records
off the vendor stream instead, which can see the paddles -- and then has the
same reason not to offer them as steps.

**Third-party control has to be off.** Whatever takes the pad over switches
`controller_data` off, and the evdev node is built from that report: the node
stays present and stops producing events. A recording made in that state is
silently empty, which is why the app blocks the page rather than the button.

Triggers are recorded as buttons. LT and RT arrive as absolute axes, so they
cross a threshold rather than being pressed, and a macro step has no room for
how far -- `MacroActionEvent` is press, release or hold.
"""
import select
import time

from . import evdev, mapping

# evdev code -> ControllerKey name, for everything a step may carry.
BUTTONS = {
    evdev.BTN_SOUTH: "a",
    evdev.BTN_EAST: "b",
    evdev.BTN_X: "x",
    evdev.BTN_Y: "y",
    evdev.BTN_TL: "lb",
    evdev.BTN_TR: "rb",
    evdev.BTN_SELECT: "select",
    evdev.BTN_START: "start",
    evdev.BTN_MODE: "home",
    evdev.BTN_THUMBL: "thl",
    evdev.BTN_THUMBR: "thr",
}

# The d-pad is one two-axis hat, so each axis carries two keys and the sign
# picks which.
HATS = {
    evdev.ABS_HAT0X: ("left", "right"),
    evdev.ABS_HAT0Y: ("up", "down"),
}

TRIGGERS = {evdev.ABS_Z: "lt", evdev.ABS_RZ: "rt"}

# Half travel. A trigger rests at 0 and a light touch is not a press; this is
# the same line Steam Input draws by default, and nothing about it is subtle
# enough to be worth a setting.
TRIGGER_THRESHOLD = 0.5


def find_pad():
    """The gamepad node, or (None, None).

    `axes=True` because the pad publishes keyboard, mouse and gamepad nodes
    under one vendor/product id and the keyboard sorts first -- matching on ids
    alone records from a node that never sends a gamepad event.
    """
    return evdev.find_device(name="apex", axes=True)


class Recorder:
    """Turns what is played on the pad into macro steps.

    Owns nothing it did not open: pass a `Reader` and it stays yours. `poll`
    blocks for at most `timeout` seconds so a caller can stop between calls
    rather than being stuck in a read -- which is what makes a Stop button in
    the app work at all.

    `clock` is injectable so a test can record a whole sequence without
    sleeping through it.
    """

    def __init__(self, reader, clock=time.monotonic):
        self.reader = reader
        self.clock = clock
        self.steps = []
        self._down = {}          # key name -> True while held
        self._last = None        # when the previous step happened

    def _emit(self, key, event, when):
        # The first step defines the start, so nothing waits before it. Every
        # other delay is measured from the step before, which is the form the
        # blob wants back.
        delay = 0 if self._last is None else int(round((when - self._last) * 1000))
        self.steps.append({"delay": max(0, delay), "key": key, "event": event})
        self._last = when

    def _change(self, key, down, when):
        if bool(self._down.get(key)) == bool(down):
            return
        self._down[key] = bool(down)
        self._emit(key, mapping.MACRO_PRESS if down else mapping.MACRO_RELEASE,
                   when)

    def poll(self, timeout=0.1):
        """Read what the pad has sent. True if any step was added."""
        ready, _, _ = select.select([self.reader.fileno()], [], [], timeout)
        if not ready:
            return False
        before = len(self.steps)
        self.reader.read()
        when = self.clock()
        for code, key in BUTTONS.items():
            self._change(key, self.reader.pressed(code), when)
        for code, (low, high) in HATS.items():
            value = self.reader.axes.get(code, 0)
            self._change(low, value < 0, when)
            self._change(high, value > 0, when)
        for code, key in TRIGGERS.items():
            self._change(key, self.reader.axis(code) >= TRIGGER_THRESHOLD, when)
        return len(self.steps) > before

    def finish(self):
        """Release anything still held, and return the steps.

        A recording that ends mid-press would store a key the pad presses and
        never lets go of -- the macro would play once and leave the button
        stuck down for the game. Everything still held is released at the
        moment recording stopped, in the order it went down.
        """
        when = self.clock()
        for key, down in list(self._down.items()):
            if down:
                self._change(key, False, when)
        return self.steps


def record(seconds, reader=None, clock=time.monotonic, should_stop=None):
    """Record for up to `seconds`, or until `should_stop()` says otherwise.

    Opens the pad's evdev node when no reader is given. Returns the steps.
    """
    own = reader is None
    if own:
        path, _name = find_pad()
        if path is None:
            raise OSError("no Apex 5 gamepad node -- is the pad awake?")
        reader = evdev.Reader(path)
    try:
        recorder = Recorder(reader, clock=clock)
        deadline = clock() + seconds
        while clock() < deadline:
            recorder.poll(timeout=0.05)
            if should_stop is not None and should_stop():
                break
        return recorder.finish()
    finally:
        if own:
            reader.close()


def describe(steps):
    """One line per step, for a CLI or a tooltip."""
    lines = []
    for step in steps:
        event = mapping.MACRO_EVENTS.get(step["event"], step["event"])
        lines.append(f"+{step['delay']:>5} ms  {event:<8} {step['key']}")
    return lines


def total_ms(steps):
    """How long a macro takes to play once."""
    return sum(max(0, step.get("delay", 0)) for step in steps)
