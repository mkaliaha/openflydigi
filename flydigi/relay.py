# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Mapping between the Apex 5 (evdev) and a virtual DualSense.

Kept in the library rather than the CLI so it can be unit tested and reused by
a GUI later.

`PadLink` at the bottom is the other half of the same job: holding the physical
pad in a way that survives it leaving the USB bus, which it does every time it
falls asleep.
"""
import os
import threading
import time

from . import ds5, effects, evdev, motion
from .device import Controller, DeviceNotFound

FLYDIGI_VID, FLYDIGI_PID = 0x37D7, 0x2501

# xpad exposes no BTN_TL2/TR2, so the digital L2/R2 come from the analog axes.
TRIGGER_DIGITAL_THRESHOLD = 0.12

# Positional mapping: Xbox layout -> DualSense layout.
#
# Use BTN_X / BTN_Y, not the BTN_NORTH / BTN_WEST aliases: on Xbox-layout pads
# those aliases are positionally inverted (see flydigi/evdev.py). Mapping by the
# compass names put Y on Square instead of Triangle.
FACE_BUTTONS = [
    (evdev.BTN_SOUTH, ds5.CROSS, 0),     # A (bottom)  -> Cross (bottom)
    (evdev.BTN_EAST, ds5.CIRCLE, 0),     # B (right)   -> Circle (right)
    (evdev.BTN_X, ds5.SQUARE, 0),        # X (left)    -> Square (left)
    (evdev.BTN_Y, ds5.TRIANGLE, 0),      # Y (top)     -> Triangle (top)
    (evdev.BTN_TL, ds5.L1, 1),
    (evdev.BTN_TR, ds5.R1, 1),
    (evdev.BTN_START, ds5.OPTIONS, 1),
    (evdev.BTN_THUMBL, ds5.L3, 1),
    (evdev.BTN_THUMBR, ds5.R3, 1),
    (evdev.BTN_MODE, ds5.PS_HOME, 2),
]

# The Apex 5 has no touchpad. Games that use it almost always bind the same
# function to View/Back on an Xbox pad, and Create is effectively unused on PC,
# so SELECT drives touchpad-click and Create sits behind a chord.
#
# Deliberately not using BTN_MODE (the Flydigi/guide button) in the chord: the
# controller firmware uses long-press on it for mode switching, and Steam claims
# it for the overlay. SELECT+START have no such conflicts.
CHORD_CREATE = (evdev.BTN_SELECT, evdev.BTN_START)

SIDE_ID = {"left": 1, "right": 2}

# DualSense trigger effect -> Flydigi effect.
#
# Transcribed from Flydigi's own PS5DataManager.ProcessDataWithResult rather
# than guessed. Theirs is not a general conversion: it recognises the specific
# byte patterns particular games emit and maps each to a hand-tuned effect, so
# the odd-looking constants below are deliberate and should not be "cleaned up".
#
# Two behaviours worth preserving:
#   * An unrecognised effect type yields None -- the trigger is left as it is.
#     Flydigi leaves their mode byte at 0xFF (invalid) and applies nothing.
#     Falling back to "normal" instead would clear effects the game never asked
#     to clear.
#   * Left and right are genuinely asymmetric. Right type 37 pattern-matches in
#     detail; left type 37 is a single mapping. This mirrors the original.
#
# Parameter indices below are into the 10-byte effect parameter block, which is
# data[12..21] for the right trigger and data[23..32] for the left, matching
# ds5.parse_output().

INVALID_MODE = 0xFF


def _pad5(values):
    out = [int(v) & 0xFF for v in values[:5]]
    return out + [0] * (5 - len(out))


def translate_ds5(effect, left_motor=0):
    """Map a DualSense trigger effect to (mode, params) or None if unhandled.

    `left_motor` is the last motor_left value seen, which Flydigi stashes and
    reuses for one left-trigger case.
    """
    p = list(effect.params) + [0] * 10          # tolerate short blocks
    t = effect.type

    if effect.side == "right":
        if t == 1:
            return 1, _pad5([p[0], p[1]])
        if t == 2:
            return 3, _pad5([p[0], p[1], p[2]])
        if t == 5:
            return 0, _pad5([])
        if t == 6:
            return 2, _pad5([p[2], p[1], p[1], p[0]])
        if t == 33:
            if p[0] == 0xFF and p[1] == 3 and p[2] == 0xFF:
                return 1, _pad5([110, 50, 0])
            if p[0] == 0:
                return 1, _pad5([120, 1])
            if p[0] == 0xFF and p[1] == 3:
                return 1, _pad5([1, 64])
            return 1, _pad5([1, 1])
        if t == 37:
            if p[0] == 20:
                if p[2] == 2:
                    return 3, _pad5([70, 20, 20, 0])
                if p[2] == 6:
                    return 3, _pad5([70, 60, 20, 0])
                if p[2] == 1:
                    return 3, _pad5([20, 10, 20, 0])
                if p[2] == 3:
                    return 3, _pad5([50, 30, 1, 0, 1])
                return 2, _pad5([50, 1, 10, 10, 10])
            if p[0] == 12:
                return 3, _pad5([70, 0, 12, 0])
            if p[0] == 36 and p[2] <= 6:
                return 3, _pad5([10, 36, 10 + p[2] * 10, 0])
            if p[0] == 68:
                return 3, _pad5([70, 50, 68, 0])
            if p[0] == 4 and p[1] == 1 and p[2] in (5, 7):
                return 3, _pad5([80, 200, 90, 0])
            if p[0] == 64 and p[1] == 1 and p[2] == 3:
                return 3, _pad5([120, 150, 60, 0])
            # includes the p[0]==72,p[1]==0,p[2]==4 case, identical to default
            return 3, _pad5([64, p[0], 0, p[2], 1])
        if t == 38:
            return 2, _pad5([255 - p[0], 1, ((p[1] + 1) * 30) & 0xFF, p[8]])
        return None

    # left trigger, from Flydigi's switch on data[22]
    if t == 1:
        return 1, _pad5([p[0], p[1]])
    if t == 2:
        return 3, _pad5([p[0], p[1], p[2]])
    if t == 5:
        return 0, _pad5([])
    if t == 6:
        return 2, _pad5([p[2], p[1], p[1], p[0]])
    if t == 33:
        if p[0] == 0:
            out = [120, 1, 0, 0, 0]
        elif p[0] == 252 or (p[0] == 192 and p[1] == 3):
            out = [1, 96, 0, 0, 0]
        else:
            out = [0, 1, 0, 0, 0]
        # these two run after the branch above and may override it
        if p[1] == 3:
            out[0], out[1] = 140, (p[5] + 1) & 0xFF
        if p[0] == 128:
            out[0], out[1] = 128, p[4]
        return 1, _pad5(out)
    if t == 37:
        return 3, _pad5([64, p[0], 0, p[2], 1])
    if t == 38:
        if p[0] == 240 and p[1] == 3 and p[3] == 0:
            return 1, _pad5([30, left_motor])
        if p[0] == 0xFF and p[1] == 3 and p[3] == 0xFF:
            return None                      # Flydigi marks this invalid
        if p[2] == 0:
            strength = ((p[1] + 1) * 30) & 0xFF
        else:
            strength = max(p[2], p[3], p[4], p[5])
        return 2, _pad5([255 - p[0], 1, strength, p[8]])
    return None


def translate(effect, left_motor=0):
    """As translate_ds5, but returning (side, mode, params) or None."""
    mapped = translate_ds5(effect, left_motor)
    if mapped is None:
        return None
    mode, params = mapped
    return SIDE_ID[effect.side], mode, params


def build_state(reader, state, select_is_touchpad=True):
    """Fold current evdev state into a DualSense input state."""
    state.lx = int(reader.axis(evdev.ABS_X, 0, 255, 128))
    state.ly = int(reader.axis(evdev.ABS_Y, 0, 255, 128))
    state.rx = int(reader.axis(evdev.ABS_RX, 0, 255, 128))
    state.ry = int(reader.axis(evdev.ABS_RY, 0, 255, 128))
    l2 = reader.axis(evdev.ABS_Z, 0.0, 1.0, 0.0)
    r2 = reader.axis(evdev.ABS_RZ, 0.0, 1.0, 0.0)
    state.l2 = int(l2 * 255)
    state.r2 = int(r2 * 255)

    hat_x = reader.axes.get(evdev.ABS_HAT0X, 0)
    hat_y = reader.axes.get(evdev.ABS_HAT0Y, 0)
    state.hat = ds5.hat_from_dpad(
        (hat_x > 0) - (hat_x < 0), (hat_y > 0) - (hat_y < 0))

    for code, mask, group in FACE_BUTTONS:
        state.set(mask, group, reader.pressed(code))

    state.set(ds5.L2, 1, l2 > TRIGGER_DIGITAL_THRESHOLD)
    state.set(ds5.R2, 1, r2 > TRIGGER_DIGITAL_THRESHOLD)

    # While the chord is held, suppress the buttons that compose it so the game
    # sees only Create -- otherwise SELECT+START would also fire Options.
    chord = all(reader.pressed(code) for code in CHORD_CREATE)
    select_held = reader.pressed(evdev.BTN_SELECT)
    state.set(ds5.CREATE, 1, chord)
    if chord:
        state.set(ds5.OPTIONS, 1, False)
    state.set(ds5.TOUCHPAD, 2,
              bool(select_held and not chord) if select_is_touchpad else False)
    return state


def release(state):
    """Let go of every input, leaving everything that is not input alone.

    What the game is fed while the pad is away. Not cosmetic: a pad that
    leaves the bus mid-press -- a yanked cable, a dongle knocked out of a dock
    -- would otherwise hold that button and that stick for as long as it stays
    away, and the game would spend the whole time walking into a wall.

    Battery and sequence number are untouched. They are not input, and the
    last reading is still the best answer to what the battery was.
    """
    state.lx = state.ly = state.rx = state.ry = ds5.AXIS_NEUTRAL
    state.l2 = state.r2 = 0
    state.hat = ds5.HAT_NEUTRAL
    state.buttons0 = state.buttons1 = state.buttons2 = 0
    state.gyro = [0, 0, 0]
    state.accel = [0, 0, 0]
    return state


class PadLink:
    """The physical Apex 5, held loosely enough to survive it going away.

    A sleeping Apex 5 does not go quiet on HID: it leaves the USB bus, taking
    its evdev and hidraw nodes with it, and comes back under different node
    numbers when a button wakes it (see `device.find_device`). A relay that
    opens both once and reads them for the length of a play session therefore
    dies the first time the pad is put down -- and it takes the virtual
    DualSense with it, because the process serving that pad is the one that
    just hit ENODEV. Losing the pad for a nap is a nuisance; losing the
    controller the game is holding is a lost session.

    So the physical pad is modelled as something that may be absent. Every
    read that touches it can fail, a failure means "gone" rather than "fatal",
    and the way back is to look for the nodes again. Nothing here is
    privileged -- reopening a hidraw node needs no more than opening it did --
    which is why it keeps working long after the relay has dropped back to the
    invoking user.

    The virtual side is deliberately not this class's business. It stays
    attached throughout, and `release` above is what the caller feeds the game
    in the meantime.

    Threads. The caller's main loop selects on these descriptors, so it is the
    only thing allowed to close one: a worker closing an fd out from under a
    `select` is a use-after-free with an fd number for a pointer. A worker that
    fails a write therefore sets `broken` and leaves it at that; `poll` does
    the closing. `lock` guards use of the vendor node, which the caller shares
    with its rumble thread.
    """

    # How long to wait before looking for the nodes again. The pad is woken by
    # a button press, so this is a human-scale wait, not a race: a second of
    # scanning /dev/input every time is cheap and reconnects fast enough that
    # the wake feels immediate.
    RETRY_INTERVAL = 1.0

    # A dead node normally announces itself by failing a read. This is the
    # backstop for the case where nothing reads it -- an idle pad with motion
    # off produces no traffic at all, so without this a disconnect would go
    # unnoticed until the next button press, which will never come.
    CHECK_INTERVAL = 2.0

    def __init__(self, want_ctrl=True, want_motion=True, log=None,
                 clock=time.monotonic):
        self.want_ctrl = want_ctrl
        self.want_motion = want_motion
        self.reader = None
        self.ctrl = None
        self.name = None
        self.motion_on = False
        # Bumped every time a node is newly opened, so a caller can tell "the
        # same pad I had last iteration" from "a pad that has just arrived and
        # remembers nothing" -- the pad drops live trigger effects when it
        # sleeps, so they have to be sent again.
        self.generation = 0
        self.drops = 0
        self.broken = False
        self.lock = threading.RLock()
        self._log = log or (lambda _msg: None)
        self._now = clock
        self._next_try = 0.0
        self._next_check = 0.0

    @property
    def connected(self):
        """Whether input is reaching us. The vendor node is a bonus, not this."""
        return self.reader is not None

    def fds(self):
        """What to select on. Empty while the pad is away, which is legal."""
        out = []
        if self.reader is not None:
            out.append(self.reader.fileno())
        if self.motion_on and self.ctrl is not None:
            out.append(self.ctrl.fd)
        return out

    # -- opening ------------------------------------------------------------

    def open(self):
        """Open whatever is present. Returns (connected, problem or None).

        Partial success is success: the gamepad node and the vendor node come
        back separately after a reconnect, and input reaching the game matters
        more than effects reaching the pad. Whatever is missing is retried by
        `poll`, so a vendor node that shows up a second late is picked up
        rather than written off for the session.
        """
        with self.lock:
            problem = None
            if self.reader is None:
                reader, name, problem = self._open_reader()
                if reader is None:
                    return False, problem
                self.reader, self.name = reader, name
                self.generation += 1
                self._next_check = self._now() + self.CHECK_INTERVAL
            if self.want_ctrl and self.ctrl is None:
                ctrl, problem = self._open_ctrl()
                if ctrl is not None:
                    self.ctrl = ctrl
                    self.generation += 1
                    if self.want_motion:
                        # Half a second of blocking, on a reconnect, on the
                        # loop that owes the host a report every 4 ms. Left
                        # inline anyway: it happens once per reconnect, the
                        # host tolerates a gap in input reports where it would
                        # not tolerate a gap in the socket, and moving it to a
                        # thread would mean two threads racing to be the first
                        # to talk to a pad that has just woken.
                        self.motion_on = motion.enable(self.ctrl)
            return True, problem

    def _open_reader(self):
        """(reader, name, problem). Resolved by id every time, never cached."""
        path, name = evdev.find_device(vendor=FLYDIGI_VID, product=FLYDIGI_PID,
                                       axes=True)
        if not path:
            path, name = evdev.find_device(name="Apex", axes=True)
        if not path:
            return None, None, (
                "Apex 5 gamepad not found in /dev/input -- is it connected? "
                "It leaves the USB bus entirely when it sleeps, so a button "
                "press may be all it needs.")
        # Checked rather than left to fail on open: this runs after the relay
        # has given root back, so an unreadable node means the session's own
        # permissions are not enough -- which is exactly what the udev rules
        # are for. As root it would open regardless and the rules would look
        # unnecessary right up until DS mode was started any other way.
        if not os.access(path, os.R_OK):
            return None, None, (
                f"{path} is not readable as uid {os.getuid()}.\n"
                f"Install the udev rules: tools/apex5-setup install-rules")
        try:
            return evdev.Reader(path), name, None
        except OSError as exc:
            # Lost between the scan and the open -- it sleeps when it likes.
            return None, None, f"cannot open {path}: {exc}"

    def _open_ctrl(self):
        try:
            return Controller(), None
        except DeviceNotFound:
            return None, "vendor interface not found; effects will not be applied"
        except OSError as exc:
            # Permission, most likely, and for the same reason as above. Not
            # fatal: input still reaches the game, only the Apex 5's own motors
            # and triggers go unwritten.
            return None, (f"vendor interface not usable ({exc}); effects will "
                          f"not be applied -- tools/apex5-setup install-rules")

    # -- reading ------------------------------------------------------------

    def read_input(self):
        """True when a full evdev frame arrived. A read error means gone."""
        reader = self.reader
        if reader is None:
            return False
        try:
            return reader.read()
        except OSError:
            self.broken = True
            return False

    def read_motion(self):
        """One pending vendor report -- ("motion", gyro, accel), ("info", d).

        Not `motion.read_report`, which cannot tell "nothing pending" from
        "the pad left the bus", and here that difference is the whole point: a
        removed hidraw node polls readable forever, so swallowing its ENODEV
        spins this loop on a full core instead of noticing the disconnect.
        """
        with self.lock:
            ctrl = self.ctrl
            if ctrl is None:
                return None
            try:
                data = os.read(ctrl.fd, 64)
            except BlockingIOError:
                return None
            except OSError:
                self.broken = True
                return None
        parsed = motion.parse(data)
        if parsed:
            return ("motion",) + parsed
        info = motion.parse_info(data)
        if info:
            return ("info", info)
        return None

    def request_info(self):
        """Ask for battery and connection type, if there is anything to ask."""
        with self.lock:
            if self.ctrl is None or not self.motion_on:
                return
            try:
                motion.request_info(self.ctrl)
            except OSError:
                self.broken = True

    # -- losing and finding it again ----------------------------------------

    def poll(self, now=None):
        """Keep the link honest. True when it changed hands either way.

        Meant to be called every iteration of the caller's loop: it does real
        work at most once a second, and the rest of the time it is two
        comparisons. A True means the descriptor set has changed and the caller
        must rebuild what it selects on -- and, if `connected` is now true,
        that this pad remembers nothing it was told before.
        """
        now = self._now() if now is None else now
        changed = False
        if self.broken:
            changed = self.drop("read failed", now)
        elif self.reader is not None and now >= self._next_check:
            self._next_check = now + self.CHECK_INTERVAL
            if not self._node_present():
                changed = self.drop("its node vanished", now)

        wanted = self.reader is None or (self.want_ctrl and self.ctrl is None)
        if wanted and now >= self._next_try:
            self._next_try = now + self.RETRY_INTERVAL
            before = (self.reader, self.ctrl)
            self.open()
            if (self.reader, self.ctrl) != before:
                changed = True
                self._log(f"pad back: {self.name} ({self.reader.path})"
                          f"{'' if self.ctrl is None else ', ' + self.ctrl.path}"
                          f"{', motion on' if self.motion_on else ''}")
        return changed

    def _node_present(self):
        """Whether our gamepad node is still the device we opened.

        The name is compared, not merely the node's existence: numbering is
        reused, so `event23` coming back as somebody's webcam would otherwise
        read as the pad still being here.
        """
        node = os.path.basename(self.reader.path)
        try:
            with open(f"/sys/class/input/{node}/device/name") as handle:
                return handle.read().strip() == self.name
        except OSError:
            return False

    def drop(self, reason, now=None):
        """Let go of the nodes. False if there was nothing to let go of."""
        now = self._now() if now is None else now
        with self.lock:
            self.broken = False
            if self.reader is None and self.ctrl is None:
                return False
            self.drops += 1
            self._log(f"pad gone ({reason}) -- the virtual DualSense stays "
                      f"attached; waiting for it to come back")
            self._close_nodes()
            # It is provably not there, so there is nothing to gain by
            # scanning for it in the same millisecond.
            self._next_try = now + self.RETRY_INTERVAL
            return True

    def _close_nodes(self):
        for handle in (self.reader, self.ctrl):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        self.reader = self.ctrl = self.name = None
        self.motion_on = False

    def close(self, restore=True):
        """Give the pad back the way we found it, if it is still here at all.

        Every restoring write is allowed to fail: `close` runs on the way out
        of a relay that may be shutting down *because* the pad left, and a pad
        that is not on the bus cannot be handed anything.
        """
        with self.lock:
            if restore and self.ctrl is not None:
                if self.motion_on:
                    try:
                        motion.disable(self.ctrl)
                    except OSError:
                        pass
                try:
                    effects.rumble(self.ctrl, 0, 0, wait=0.0)
                except OSError:
                    pass
                try:
                    effects.clear_all(self.ctrl)
                except OSError:
                    pass
            self._close_nodes()
