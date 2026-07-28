# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Button remapping profiles stored in the controller's own memory.

Space Station calls these "configs"; the pad holds several and switches between
them with the Menu button. They persist in hardware, which is why remapping set
up on Windows keeps working on Linux -- the pad does the remapping itself, and
nothing on the host is involved.

A config is one flat byte blob, transferred in fixed-size packets. Only the
NewXInput variants of the commands are implemented here, which is what the
Apex 5 speaks:

    read   163  [cfgId, pkgSize]              -> multi-packet reply
    apply  162  [cfgId]                       switch the pad to this config
    write  164  [cfgId, startIdx, nPkts, pkgSize] then 165 [pktNum, data...]
    save   166  [versionLo, versionHi]        commit to flash

Unlike the trigger-effect commands, these are checksummed and the pad rejects
a packet whose checksum is wrong -- see `build`.

**Blob layout** (protocol version 3.x). Offsets are into the assembled blob,
not into any packet:

    0..2     protocol version, little endian
    2        package count: 79 for v3.0, 84 for v3.1+
    3..13    legacy LED config
    13..109  key table -- 32 entries of 3 bytes, indexed by key id
    109..123 joystick curves
    123..137 trigger travel curves
    137..145 motion
    145..154 grip vibration
    154..183 trigger motors
    183..185 wheel
    185..225 force trigger (2 x 20)
    225..227 data version, little endian
    230..768 macros
    770..790 title, UTF-16LE
    790..840 v3.1+: joystick extra, macro cycle, motion curve

Only the key table and the title are interpreted here. Everything else is
carried through byte-for-byte, so writing a config back cannot disturb settings
this code does not understand -- which matters, because a config also holds the
trigger and macro state.
"""
import struct

from . import blobs
from .blobs import PKG_SIZE, ProtocolError, build   # re-exported for callers

CMD_STATUS = 161
CMD_APPLY = 162
CMD_READ = 163
CMD_WRITE_START = 164
CMD_WRITE_PACK = 165
CMD_SAVE = 166

OFF_PROTO_VERSION = 0
OFF_PACKAGE_COUNT = 2
OFF_KEY_TABLE = 13
OFF_JOYSTICK_CURVE = 109   # 2 x 7: sensitivity curve per stick
OFF_TRIGGER_CURVE = 123    # 2 x 7: travel curve per trigger
OFF_GRIP_VIBRATION = 145   # 1 + 2 x 4: the grip motors
OFF_TRIGGER_MOTOR = 154    # 1 + 2 x 14: the trigger motors
OFF_FORCE_TRIGGER = 185    # 2 x 20: the adaptive-trigger effect

# The one effect that uses the block's rumble-binding half -- effects.py calls
# it MODE_VIBRATION. Named here rather than imported so this module stays a
# byte layout with no opinion about what the effects mean.
FORCE_TRIGGER_BIND_MODE = 5
OFF_DATA_VERSION = 225
OFF_TITLE = 770
OFF_JOYSTICK_EXTRA = 790   # 2 x 12: the 9-point bank, circularity and edge
TITLE_BYTES = 20

# The trigger motor's strength byte holds the percentage Flydigi's own slider
# shows (`SaveTriggerVibrationConfig` assigns it straight across), unlike the
# amplitude pair beside it, which is that slider's percent scaled to 0..255.
TRIGGER_MOTOR_SCALE_MAX = 100

CURVE_ENTRY = 7            # type, zero, p1.x, p1.y, p2.x, p2.y, end
JOYSTICK_EXTRA_ENTRY = 12  # type, bank[9], isRound, end
BANK_POINTS = 9

# JoystickSensitivityType. The last two are the enum's own names; Space Station
# labels them "Instant" and "Delay", which is what a UI should say.
CURVE_DEFAULT, CURVE_QUICK, CURVE_SLOW, CURVE_CUSTOM = 0, 1, 2, 3

# JoystickCircularityType.
SHAPE_RECTANGLE, SHAPE_CIRCULAR = 0, 1

# The interior breakpoints each preset stands for, from Space Station's own
# renderer. Custom is absent on purpose: picking it keeps whatever is there.
#
# Default's (63, 63) is the pad's value, not the app's -- their JavaScript
# hardcodes (64, 64) for every device that is not a k2. Both are the identity
# line, so the compiled bank is the same either way; 63 is used so a profile we
# reset matches a factory one byte for byte.
STICK_PRESETS = {
    CURVE_DEFAULT: ((63, 63), (127, 127)),
    CURVE_QUICK: ((64, 96), (127, 127)),      # Space Station labels it "Instant"
    CURVE_SLOW: ((64, 32), (127, 127)),       # ... and this one "Delay"
    CURVE_CUSTOM: None,
}

# A stick's `center` byte is forced to exactly this when the stick is mapped to
# something that is not a stick -- keyboard, mouse or d-pad. So 127 there is a
# sentinel meaning "not a joystick", not a dead zone of 127, and a UI that draws
# it as a number is drawing a lie. Space Station's own renderer guards the same
# way, treating anything over 100 as zero.
CENTER_NOT_A_STICK = 127

# `center` and `edge` each carry two opposite controls in one byte, and the sign
# picks which. They position the curve's start and end nodes, and the sign says
# which axis the node slides along:
#
#   center > 0  start node moves along x   input below it produces nothing
#                                          -- a dead zone
#   center < 0  start node moves up y      the smallest input already produces
#                                          `-center` -- Space Station calls this
#                                          "Offset", and it exists to cancel a
#                                          *game's* dead zone rather than add one
#   edge   > 0  end node pulls in along x  full output before full travel
#   edge   < 0  end node drops along y     full travel only reaches 100+edge,
#                                          i.e. an output ceiling
#
# So there is no such thing as a negative dead zone; the field simply is not a
# dead-zone field. Both halves are wanted. We write only the positive one,
# because the SDK's reader folds a byte over 127 to `127 - byte` while every one
# of its writers emits a plain two's-complement cast -- so the two disagree, and
# -20 written as 236 reads back as -109. Positive values encode identically
# under both readings; the rest is refused rather than guessed at.
BIPOLAR_MAX = 100

# Enable flags in this config are stored inverted: 0 means on, 0xFF means off.
ENABLED, DISABLED = 0, 0xFF

SIDE_LEFT, SIDE_RIGHT = 0, 1
SIDES = ("left", "right")

KEY_SLOTS = 32
KEY_ENTRY = 3

# Sentinels stored in the key table's target byte.
TARGET_IDENTITY = 255      # key does what it says on the shell
TARGET_MACRO = 32          # key runs a macro
TARGET_KEYBOARD = 254      # key sends keyboard/mouse, or is multi-function

# Turbo modes (the key table's second byte).
TURBO_OFF = 0
TURBO_WHILE_HELD = 1
TURBO_TOGGLE = 2

# ControllerKey ids. The key table is indexed by these, and a table entry's
# target byte is one of these too.
KEY_NAMES = {
    0: "up", 1: "right", 2: "down", 3: "left",
    4: "a", 5: "b", 6: "select", 7: "x", 8: "y", 9: "start",
    10: "lb", 11: "rb", 12: "lt", 13: "rt", 14: "thl", 15: "thr",
    16: "c", 17: "z",
    18: "m1", 19: "m2", 20: "m3", 21: "m4", 22: "m5", 23: "m6",
    24: "menu", 25: "turbo", 27: "home", 28: "back",
}
KEY_IDS = {name: key_id for key_id, name in KEY_NAMES.items()}

# Physical buttons on an Apex 5, in the order a UI should present them. The key
# table has 32 slots but most are unpopulated on this model.
APEX5_KEYS = [
    "a", "b", "x", "y",
    "up", "down", "left", "right",
    "lb", "rb", "lt", "rt",
    "thl", "thr",
    "select", "start", "home",
    "c", "z",
    "m1", "m2", "m3", "m4",
]

# What a key may be remapped *to*. Deliberately smaller than APEX5_KEYS: the
# extra buttons (M1-M4, and the C/Z pair by the bumpers) have no XInput
# equivalent, so a host cannot receive them. They are sources -- you map a
# paddle onto a real button, which is what the pad ships doing -- and offering
# them as targets would let someone map A to something nothing can see, which
# reads as "A stopped working".
EXTRA_KEYS = ["c", "z", "m1", "m2", "m3", "m4"]
XINPUT_TARGETS = [key for key in APEX5_KEYS if key not in EXTRA_KEYS]


def stick_nodes(center=0, edge=0, point1=(63, 63), point2=(127, 127)):
    """The four-node polyline a stick curve really is, in percent.

    `center` and `edge` position the two ends, and the sign picks which axis the
    node slides along -- see BIPOLAR_MAX. `point1` and `point2` are the interior
    breakpoints, stored on the blob's 0..127 scale and used here as percent.

    Straight segments, not a Bezier: Space Station's editor draws three `<line>`
    elements and samples them with a plain lerp.

    The interior points' x is remapped into whatever span the two ends leave,
    which is what Flydigi's `CalculatePoint` does:
    `center + (100 - center) * x / 100`. Without it the nodes stop being ordered
    as soon as the dead zone passes the first breakpoint -- a dead zone of 60
    puts the start node at x=60 while point1 sits at x=49.6, the segment between
    them runs backwards, and the lerp inverts: the curve comes out at *full*
    output exactly where it should be silent. With no dead zone the remap is the
    identity, so this changes nothing for the common case.
    """
    start = (center, 0) if center > 0 else (0, -center)
    end = (100 - edge, 100) if edge > 0 else (100, 100 + edge)
    scale = 100.0 / 127.0
    span = end[0] - start[0]

    if span <= 0:
        # Nothing left for the curve to happen across, so the breakpoints have
        # nowhere to be: what remains is a step from silent to full. Keeping
        # them would leave their y values stranded on a vertical segment and
        # answer for the whole travel.
        return [start, end]

    def interior(point):
        return (start[0] + span * (point[0] * scale) / 100.0, point[1] * scale)

    return [start, interior(point1), interior(point2), end]


def _along(nodes, x):
    """Where the polyline is at `x`, extrapolating past either end."""
    for index in range(len(nodes) - 1):
        (x0, y0), (x1, y1) = nodes[index], nodes[index + 1]
        # The last segment catches everything to its right, and the first
        # catches everything to its left, so a curve whose start node has been
        # pushed inward still has a value at x=0.
        if x <= x1 or index == len(nodes) - 2:
            if x0 == x1:
                # A vertical segment is a step, so which side of it x falls on
                # is the whole answer. Returning y1 unconditionally made a dead
                # zone of 100 report full output across the entire travel.
                return y0 if x < x0 else y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return nodes[-1][1]


def stick_bank(center=0, edge=0, point1=(63, 63), point2=(127, 127)):
    """Compile a stick curve into the nine points the pad actually plays.

    **This is the whole reason a stick UI cannot just write the fields it edits.**
    The pad has no curve evaluator: it plays the nine-point bank at offset 790
    and ignores the polyline at 109 entirely, which is confirmed on hardware --
    flattening the bank silences the stick, while flattening the polyline changes
    nothing at all. So `center`, `edge` and the two points are the *source form*,
    and this is the compiler that turns them into something the firmware acts on.
    Writing them without this is a slider that moves and does nothing.

    Nine samples at evenly spaced travel, biased by 50: the stored byte is
    `output_percent + 50`, so 50 is no output and 150 is full.

    Truncation, not rounding, and that is checked against the hardware rather
    than assumed: an untouched Apex 5 holds `50 62 75 87 100 112 125 137 150`,
    which this reproduces exactly. Space Station's own JavaScript rounds, and
    would write `50 63 75 88 ...` for the same curve -- so their app and the
    factory firmware disagree by a unit on four of the nine points. Matching the
    pad is what keeps "reset to default" from showing up as a change.
    """
    nodes = stick_nodes(center, edge, point1, point2)
    bank = []
    for index in range(BANK_POINTS):
        value = _along(nodes, 100.0 * index / (BANK_POINTS - 1))
        bank.append(int(max(-50, min(100, value))) + 50)
    return bank


def read_status(ctrl, wait=1.0, slots=4):
    """Which profile is active, and a version id for each.

    Cheap, and unlike `read_config` it has no side effect -- worth preferring
    wherever it will do. The version ids are each config's `data_version`
    field, so a caller can tell whether a cached copy is still current without
    reading the config at all. 0xFFFF means the slot has never been written.
    """
    for body in blobs.replies(ctrl, build(CMD_STATUS, b""), wait):
        if body[2] != CMD_STATUS:
            continue
        raw = body[5]
        # Slots are reported across two banks of four; the second bank reports
        # 4..7 for the same profiles.
        active = raw - 4 if 3 < raw <= 7 else (raw if raw <= 7 else 0)
        versions = [(body[7 + 2 * i] << 8) | body[6 + 2 * i] for i in range(slots)]
        return {"active": active, "versions": versions}
    return None


def read_config(ctrl, cfg_id, wait=1.5, retries=3):
    """Read one stored config off the pad.

    The reply is a run of packets carrying (total, index, cfgId, 20 bytes). The
    pad sends them back to back, so collect until the last index arrives rather
    than issuing one request per packet.

    **This switches the pad to the config being read** -- the firmware pages it
    in as the live one, audibly re-seating the trigger motors. Confirmed on
    hardware: after reading config 2, `read_status` reports 2 as active. A
    caller that does not intend to change what the user is playing with must
    read the status first and re-apply the original afterwards; see
    `read_config_preserving`.
    """
    blob = blobs.read_blob(ctrl, CMD_READ, cfg_id, f"config {cfg_id}",
                           wait=wait, retries=retries)
    return MappingConfig(blob, cfg_id)


def read_config_preserving(ctrl, cfg_id, wait=1.5):
    """Read a config and leave the pad on whatever it was using before.

    Reading switches the pad, which is not what someone browsing their profiles
    asked for. Returns (config, restored_to) so the caller can say what
    happened; restored_to is None when no restore was needed or possible.

    The desktop app deliberately does not use this. Command 166 commits
    whichever profile the pad is running, so an app that browses without
    switching cannot save what it is showing; it opens profiles the way Space
    Station does instead, leaving the pad on the one being edited. This stays
    for callers that really do want to look without disturbing anything.

    The restore is in a `finally` because the pad switches on the *first* read
    packet, before `read_blob` knows whether the whole config arrived -- so a
    read that raises has still moved the pad. Worse, a retry then launders it:
    the next `read_status` truthfully reports the browsed slot as active, the
    restore is skipped as unnecessary, and the call reports success having left
    the pad somewhere the caller never asked it to go. Which slot to go back to
    is therefore decided before the read, not after it.
    """
    status = read_status(ctrl)
    previous = status["active"] if status else None
    if previous is None or previous == cfg_id:
        return read_config(ctrl, cfg_id, wait=wait), None
    try:
        config = read_config(ctrl, cfg_id, wait=wait)
    finally:
        apply_config(ctrl, previous)
    return config, previous


def apply_config(ctrl, cfg_id, wait=0.5):
    """Switch the pad to a stored config."""
    for body in blobs.replies(ctrl, build(CMD_APPLY, bytes([cfg_id])), wait):
        if body[2] == CMD_APPLY:
            return True
    return False


def save_config(ctrl, version=0, wait=2.0):
    """Commit the working config to flash. Slow -- the pad takes seconds.

    `version` is the id `read_status` reports for the slot, and the same value
    the blob carries at OFF_DATA_VERSION. Flydigi's SDK gives this command a
    10 second timeout where every other command gets 500 ms, which is what a
    flash write looks like.

    The observed values (23224, 65078, 65535 for an untouched slot) look like
    random tags for change detection rather than a counter, so callers should
    pass the config's own `data_version` to leave it alone. Passing 0 -- the
    default -- overwrites the slot's id with zero, which is almost certainly
    not what you want; it is kept as the default only because nothing has yet
    confirmed what the pad does with the value.
    """
    payload = struct.pack("<H", version & 0xFFFF)
    for body in blobs.replies(ctrl, build(CMD_SAVE, payload), wait):
        if body[2] == CMD_SAVE:
            return True
    return False


def write_config(ctrl, cfg_id, config, old=None, wait=0.5):
    """Write a config to the pad, sending only the packets that changed.

    Flydigi diffs against the previously read config and transfers contiguous
    runs of changed packets. That is worth copying: a full config is 42 packets,
    and remapping one button touches one of them.

    Returns the number of packets sent. Call `save_config` afterwards to make
    it survive a power cycle.
    """
    return blobs.write_blob(ctrl, CMD_WRITE_START, CMD_WRITE_PACK, cfg_id,
                            config.blob, old.blob if old is not None else None,
                            wait=wait)


class MappingConfig:
    """One stored profile. Wraps the raw blob and edits it in place."""

    def __init__(self, blob, cfg_id=None):
        self.blob = bytearray(blob)
        self.cfg_id = cfg_id

    def copy(self):
        return MappingConfig(bytearray(self.blob), self.cfg_id)

    def packets(self, size=PKG_SIZE):
        return [bytes(self.blob[i : i + size]) for i in range(0, len(self.blob), size)]

    @property
    def proto_version(self):
        return struct.unpack_from("<H", self.blob, OFF_PROTO_VERSION)[0]

    @property
    def package_count(self):
        return self.blob[OFF_PACKAGE_COUNT]

    @property
    def data_version(self):
        return struct.unpack_from("<H", self.blob, OFF_DATA_VERSION)[0]

    @property
    def title(self):
        raw = bytes(self.blob[OFF_TITLE : OFF_TITLE + TITLE_BYTES])
        return raw.decode("utf-16-le", "replace").rstrip("￿\x00")

    @title.setter
    def title(self, value):
        raw = value.encode("utf-16-le")[:TITLE_BYTES]
        self.blob[OFF_TITLE : OFF_TITLE + TITLE_BYTES] = raw.ljust(TITLE_BYTES, b"\x00")

    def _entry(self, key):
        key_id = KEY_IDS[key] if isinstance(key, str) else key
        if not 0 <= key_id < KEY_SLOTS:
            raise KeyError(f"no key slot {key!r}")
        return OFF_KEY_TABLE + key_id * KEY_ENTRY, key_id

    def mapping(self, key):
        """What this physical key currently does.

        Returns (target, turbo_mode, turbo_frequency). `target` is a key name,
        or "macro" / "keyboard" for the two sentinels. A key that is not
        remapped reports itself, which is how the pad stores identity.
        """
        offset, key_id = self._entry(key)
        target, mode, frequency = self.blob[offset : offset + KEY_ENTRY]
        if target == TARGET_MACRO:
            return "macro", TURBO_OFF, 0
        if frequency > 0:
            return KEY_NAMES.get(target, target), mode, frequency
        if target == TARGET_KEYBOARD:
            return "keyboard", TURBO_OFF, 0
        # Anything above the key range means "unchanged", stored as 255.
        if target > TARGET_MACRO:
            target = key_id
        return KEY_NAMES.get(target, target), TURBO_OFF, 0

    def set_mapping(self, key, target, turbo_mode=TURBO_OFF, frequency=0):
        """Remap a physical key. `target` may be a key name, id, or None.

        None (or the key's own name) restores the default, which the pad stores
        as 255 rather than as the key's own id.
        """
        offset, key_id = self._entry(key)
        if target is None:
            target_id = TARGET_IDENTITY
        elif isinstance(target, str):
            if target == "macro":
                target_id = TARGET_MACRO
            elif target == "keyboard":
                target_id = TARGET_KEYBOARD
            else:
                target_id = KEY_IDS[target]
        else:
            target_id = target
        if target_id == key_id:
            target_id = TARGET_IDENTITY
        if frequency > 0:
            # Turbo needs a real target; identity has no id to repeat.
            if target_id in (TARGET_IDENTITY, TARGET_KEYBOARD):
                target_id = key_id
            self.blob[offset : offset + KEY_ENTRY] = bytes(
                [target_id, turbo_mode, min(255, frequency)])
        else:
            self.blob[offset : offset + KEY_ENTRY] = bytes([target_id, 0, 0])

    def mappings(self, keys=None):
        """Every populated key, as {name: (target, mode, frequency)}."""
        return {key: self.mapping(key) for key in (keys or APEX5_KEYS)}

    def remapped(self, keys=None):
        """Only the keys that differ from the default -- what a UI should mark."""
        out = {}
        for key in keys or APEX5_KEYS:
            target, mode, frequency = self.mapping(key)
            if target != key or frequency:
                out[key] = (target, mode, frequency)
        return out

    # -- grip vibration ---------------------------------------------------
    #
    # 9 bytes: a master switch, then per side (switch, min, max, scale). The
    # switches are inverted -- 0 is on. min/max bound how hard the motor is
    # allowed to run, so they are the intensity control; the pad clamps the
    # game's rumble into that window.

    def vibration(self, side):
        """(enabled, min, max, scale) for one grip motor."""
        base = OFF_GRIP_VIBRATION + 1 + self._side(side) * 4
        return (self.blob[base] == ENABLED, self.blob[base + 1],
                self.blob[base + 2], self.blob[base + 3])

    def set_vibration(self, side, enabled=None, minimum=None, maximum=None,
                      scale=None):
        base = OFF_GRIP_VIBRATION + 1 + self._side(side) * 4
        if enabled is not None:
            self.blob[base] = ENABLED if enabled else DISABLED
        if minimum is not None:
            self.blob[base + 1] = max(0, min(255, minimum))
        if maximum is not None:
            self.blob[base + 2] = max(0, min(255, maximum))
        if scale is not None:
            self.blob[base + 3] = max(0, min(255, scale))
        # The pad reads these as a window, so keep min <= max rather than
        # letting a slider produce an inverted range.
        if self.blob[base + 1] > self.blob[base + 2]:
            self.blob[base + 1], self.blob[base + 2] = (
                self.blob[base + 2], self.blob[base + 1])

    @property
    def vibration_enabled(self):
        return self.blob[OFF_GRIP_VIBRATION] == ENABLED

    @vibration_enabled.setter
    def vibration_enabled(self, value):
        self.blob[OFF_GRIP_VIBRATION] = ENABLED if value else DISABLED

    # -- adaptive triggers, stored per profile ----------------------------
    #
    # 20 bytes per side, laid out as
    #
    #   [0]      effect mode          [4..8]  bind params
    #   [1]      bind type            [9]     mixed border
    #   [2]      bind filter          [10..19] effect params
    #   [3]      bind scale
    #
    # This is the same effect vocabulary the live SetForceTrigger command uses
    # -- the difference is that this copy lives in the pad, so it applies with
    # no host process and no game integration. What each mode's parameters mean
    # is in flydigi/effects.py; this pair only moves bytes.
    #
    # The bind half is the rumble-to-trigger binding, and only the Vibration
    # effect uses it. It is kept across a mode change rather than cleared,
    # which is what Flydigi's own writer does -- it re-emits the whole block
    # from a record that held the old binding.

    def trigger_effect(self, side):
        """(mode, params) for one trigger's stored effect."""
        base = OFF_FORCE_TRIGGER + self._side(side) * 20
        return self.blob[base], list(self.blob[base + 10 : base + 20])

    def trigger_bind(self, side):
        """(filter, scale, params) -- the rumble binding half of the block."""
        base = OFF_FORCE_TRIGGER + self._side(side) * 20
        return (self.blob[base + 2], self.blob[base + 3],
                list(self.blob[base + 4 : base + 9]))

    def set_trigger_effect(self, side, mode, params=None, bind=None):
        """Store one trigger's effect.

        `params` of None leaves the parameter slots alone -- switching to an
        effect with no knobs should not throw away the numbers tuned for the
        one before it.
        """
        base = OFF_FORCE_TRIGGER + self._side(side) * 20
        mode = mode & 0xFF
        self.blob[base] = mode
        # Byte 1 is a bind type Flydigi writes as 2 for the Vibration effect
        # and 0 for every other, rather than as a setting of its own.
        self.blob[base + 1] = 2 if mode == FORCE_TRIGGER_BIND_MODE else 0
        if params is not None:
            values = list(params)[:10] + [0] * max(0, 10 - len(params))
            self.blob[base + 10 : base + 20] = bytes(
                max(0, min(255, int(v))) for v in values)
        if bind is not None:
            filt, scale, bind_params = bind
            self.blob[base + 2] = max(0, min(255, int(filt)))
            self.blob[base + 3] = max(0, min(255, int(scale)))
            values = list(bind_params)[:5] + [0] * max(0, 5 - len(bind_params))
            self.blob[base + 4 : base + 9] = bytes(
                max(0, min(255, int(v))) for v in values)

    # -- travel and sensitivity curves ------------------------------------
    #
    # Sticks and triggers share one 7-byte struct -- `type, zero, p1.x, p1.y,
    # p2.x, p2.y, end` -- but not one scale. A stick's curve runs to 127 and a
    # trigger's to 255, confirmed on hardware: the factory blob holds
    # `0 0 63 63 127 127 127` per stick and `0 0 0 0 255 255 255` per trigger.
    # Both are the identity line on their own scale, so a pad out of the box has
    # no curve at all. A single "0-100%" control mapped to bytes would cover
    # half the range on one of them.

    def _curve(self, base):
        return {
            "type": self.blob[base],
            "zero": self.blob[base + 1],
            "point1": (self.blob[base + 2], self.blob[base + 3]),
            "point2": (self.blob[base + 4], self.blob[base + 5]),
            "end": self.blob[base + 6],
        }

    def trigger_curve(self, side):
        """(type, zero, point1, point2, end) -- where the trigger's travel maps."""
        return self._curve(OFF_TRIGGER_CURVE + self._side(side) * CURVE_ENTRY)

    def set_trigger_curve(self, side, zero=None, end=None, mirror_points=True):
        """Move the trigger's travel window.

        `zero` is where the trigger starts registering and `end` where it reads
        full -- Space Station calls the pair "Stroke Setting" and offers them as
        one range slider.

        The two control points are mirrored onto the window by default, because
        that is the only combination Flydigi's own software produces:
        `ControllerRepository` sets `Point1 = (Start, Start)` and
        `Point2 = (End, End)` from the same two numbers, and the factory blob
        agrees -- `0 0 0 0 255 255 255` is exactly zero, (zero, zero),
        (end, end), end. Writing zero and end alone, which this used to do,
        leaves the points where they were and produces a blob no vendor tool
        would ever emit, with breakpoints stranded outside the window they are
        supposed to bound. `mirror_points=False` is for a caller deliberately
        shaping the curve rather than moving its ends.

        The pad reads the pair as a window, so they are sorted rather than left
        inverted. They are allowed to be equal: Space Station's range slider
        passes neither `pushable` nor `allowCross`, so dragging one handle onto
        the other is reachable and nothing downstream rejects it.
        """
        base = OFF_TRIGGER_CURVE + self._side(side) * CURVE_ENTRY
        if zero is not None:
            self.blob[base + 1] = max(0, min(255, int(zero)))
        if end is not None:
            self.blob[base + 6] = max(0, min(255, int(end)))
        if self.blob[base + 1] > self.blob[base + 6]:
            self.blob[base + 1], self.blob[base + 6] = (
                self.blob[base + 6], self.blob[base + 1])
        if mirror_points:
            low, high = self.blob[base + 1], self.blob[base + 6]
            self.blob[base + 2] = self.blob[base + 3] = low
            self.blob[base + 4] = self.blob[base + 5] = high

    # -- joystick curves ---------------------------------------------------
    #
    # Two blocks describe one stick. The core block at 109 is the four-node
    # polyline Space Station draws -- start, two breakpoints, end -- and the
    # extra block at 790 is the same curve resampled to nine evenly spaced
    # points, which is what a v3.1 pad actually plays. Flydigi writes both from
    # one source and never reconciles them, so this reads both and says when
    # they disagree rather than picking a winner.

    def joystick_curve(self, side):
        """The core curve block for one stick.

        `center` is reported raw. Above 100 it is not a number at all: the
        firmware stores exactly 127 there when the stick is mapped to keyboard,
        mouse or d-pad, so `is_stick` says whether the rest means anything.

        `end` is read-only -- see `set_joystick_curve`.
        """
        base = OFF_JOYSTICK_CURVE + self._side(side) * CURVE_ENTRY
        curve = self._curve(base)
        curve["center"] = curve.pop("zero")
        curve["is_stick"] = curve["center"] <= BIPOLAR_MAX
        return curve

    def joystick_shape(self, side):
        """The 9-point bank, circularity and edge for one stick.

        Returns None on a protocol older than 3.1, where the block does not
        exist. `bank` values are biased by 50, so 50 is no output and 150 is
        full; a straight line is evenly spaced between them. 0xFF means that
        point was never written -- unlike the core block, whose 7 bytes are
        always emitted in full, this one is pre-filled with 0xFF and only as
        many points as the host had are overwritten.
        """
        base = OFF_JOYSTICK_EXTRA + self._side(side) * JOYSTICK_EXTRA_ENTRY
        if len(self.blob) < base + JOYSTICK_EXTRA_ENTRY:
            return None
        return {
            "type": self.blob[base],
            "bank": list(self.blob[base + 1 : base + 1 + BANK_POINTS]),
            "circular": self.blob[base + 10] == SHAPE_CIRCULAR,
            "edge": self.blob[base + 11],
        }

    def set_joystick_curve(self, side, curve_type=None, center=None,
                           point1=None, point2=None):
        """Edit the core curve. `end` is deliberately not settable.

        Nothing in Flydigi's application ever assigns the core `end` byte -- the
        UI's "Edge" slider writes the *extra* block's trailing byte instead, a
        different protobuf field -- and their reader corrupts it above 127 by
        folding it to `127 - value` and casting straight back. The factory value
        is 127 on both sticks. So it is carried through untouched rather than
        exposed as a control whose stock value we would have to guess.

        Setting the type writes it into both blocks. The SDK regenerates the
        extra block's copy from this one on every write, so a blob where they
        disagree is a state no vendor tool produces.
        """
        base = OFF_JOYSTICK_CURVE + self._side(side) * CURVE_ENTRY
        if curve_type is not None:
            curve_type = int(curve_type)
            if not CURVE_DEFAULT <= curve_type <= CURVE_CUSTOM:
                raise ValueError(f"no sensitivity curve type {curve_type}")
            self.blob[base] = curve_type
            extra = OFF_JOYSTICK_EXTRA + self._side(side) * JOYSTICK_EXTRA_ENTRY
            if len(self.blob) >= extra + JOYSTICK_EXTRA_ENTRY:
                self.blob[extra] = curve_type
        if center is not None:
            self.blob[base + 1] = self._bipolar("center", center)
        for offset, point in ((base + 2, point1), (base + 4, point2)):
            if point is not None:
                x, y = point
                self.blob[offset] = max(0, min(127, int(x)))
                self.blob[offset + 1] = max(0, min(127, int(y)))

    def set_joystick_shape(self, side, bank=None, circular=None, edge=None):
        """Edit the 9-point bank, circularity and the outer node.

        `edge` is an outer dead zone only while it is positive -- see
        BIPOLAR_MAX for what its other half means and why we refuse it.
        """
        base = OFF_JOYSTICK_EXTRA + self._side(side) * JOYSTICK_EXTRA_ENTRY
        if len(self.blob) < base + JOYSTICK_EXTRA_ENTRY:
            raise ProtocolError(
                "this profile has no joystick extra block -- protocol 3.1 only")
        if bank is not None:
            bank = list(bank)
            # Exactly nine. Flydigi's writer loops over however many points it
            # was given with no bound, so a tenth lands on `isRound`, an
            # eleventh on `edge`, and a thirteenth starts overwriting the other
            # stick. Refusing is cheaper than reproducing that.
            if len(bank) != BANK_POINTS:
                raise ValueError(
                    f"the bank is exactly {BANK_POINTS} points, got {len(bank)}")
            self.blob[base + 1 : base + 1 + BANK_POINTS] = bytes(
                max(0, min(150, int(v))) for v in bank)
        if circular is not None:
            self.blob[base + 10] = SHAPE_CIRCULAR if circular else SHAPE_RECTANGLE
        if edge is not None:
            self.blob[base + 11] = self._bipolar("edge", edge)

    def stick(self, side):
        """Everything about one stick, both blocks, as one dict."""
        curve = self.joystick_curve(side)
        shape = self.joystick_shape(side) or {}
        return {
            "type": curve["type"],
            "center": curve["center"],
            "is_stick": curve["is_stick"],
            "point1": curve["point1"],
            "point2": curve["point2"],
            "end": curve["end"],
            "bank": shape.get("bank", []),
            "circular": shape.get("circular", False),
            "edge": shape.get("edge", 0),
        }

    def set_stick(self, side, curve_type=None, center=None, edge=None,
                  point1=None, point2=None, circular=None):
        """Edit a stick and recompile the bank from the result.

        The one entry point a UI should use. Both blocks are written: the bank
        because it is the only part of the curve the pad plays, and the polyline
        because it is the source form -- Space Station reads it back to redraw
        its own editor, and a profile carrying a bank with no matching polyline
        would open there showing a curve nobody drew.

        Editing any node moves the type to Custom, which is what Space Station
        does: a curve that no longer matches a preset must not go on claiming to
        be one. Pass `curve_type` to pick a preset instead, and its points are
        applied for you.
        """
        if curve_type is not None:
            curve_type = int(curve_type)
            if curve_type not in STICK_PRESETS:
                raise ValueError(f"no sensitivity curve preset {curve_type}")
            if curve_type != CURVE_CUSTOM:
                # Selecting a preset is selecting its whole shape, ends included
                # -- which is why Space Station zeroes both when you pick one.
                point1, point2 = STICK_PRESETS[curve_type]
                center = 0 if center is None else center
                edge = 0 if edge is None else edge

        # The two dead zones eat the same travel, so they cannot add up to more
        # than there is -- Space Station cross-clamps them the same way. Without
        # it, 60 and 60 leave the curve no span at all to rise across, and what
        # the pad gets is a step instead of a curve. Whichever is being set now
        # gives way, so moving one slider never silently moves the other.
        current = self.stick(side)
        if center is not None:
            held = current["edge"] if current["edge"] <= BIPOLAR_MAX else 0
            center = min(int(center), BIPOLAR_MAX - held)
        if edge is not None:
            held = current["center"] if current["is_stick"] else 0
            edge = min(int(edge), BIPOLAR_MAX - held)

        self.set_joystick_curve(side, curve_type=curve_type, center=center,
                                point1=point1, point2=point2)
        if edge is not None or circular is not None:
            self.set_joystick_shape(side, circular=circular, edge=edge)
        if curve_type is None and (center is not None or edge is not None
                                   or point1 is not None or point2 is not None):
            self.set_joystick_curve(side, curve_type=CURVE_CUSTOM)

        current = self.stick(side)
        self.set_joystick_shape(side, bank=stick_bank(
            center=current["center"] if current["is_stick"] else 0,
            edge=current["edge"] if current["edge"] <= BIPOLAR_MAX else 0,
            point1=current["point1"], point2=current["point2"]))

    @staticmethod
    def _bipolar(what, value):
        """Range-check one of the two signed-looking fields. See BIPOLAR_MAX."""
        value = int(value)
        if not 0 <= value <= BIPOLAR_MAX:
            raise ValueError(
                f"{what} must be 0..{BIPOLAR_MAX}; the negative half is refused "
                "because Flydigi's own reader and writer disagree about how to "
                "encode it, so a negative value does not survive their round trip")
        return value

    def trigger_motor(self, side):
        """One trigger's own vibration motor, as a dict.

        **The Apex 5 does not have these motors, and this block does nothing
        on it.** `GenerateControllerApex5` sets seven capability flags and
        `IsSupportTriggerVibration` is not among them, while Vader 3, 4 and 5
        all set it; `ConvertTriggerConfigBean` reads this block only when that
        flag is on, so Space Station never touches it on an Apex 5. Trigger
        haptics on this pad come out of the force triggers instead -- the
        effect vocabulary in flydigi/effects.py, where `Sniper` vibrates
        unaided and `Vibration` follows the grips.

        Kept because the block is real, the layout is confirmed against
        Flydigi's writer, and a Vader would use it. Nothing in the app calls
        it: an editor for it would be an editor for hardware that is not here.

        The block holds two 7-byte gears per side -- `type, min, max, filter,
        min_start, scale, min_time` -- of which Flydigi's software writes four
        fields of the first (`SaveTriggerVibrationConfig`) and never touches
        the second. Those four are what this exposes:

            enabled    the master switch
            minimum    ) the amplitude window: grip rumble above `maximum` acts
            maximum    ) as `maximum` and below `minimum` acts as `minimum`
            scale      overall strength, stored 1..100 rather than 0..255
            block      rumble below this leaves the trigger still

        `enabled` is **shared**: it comes from the single byte at
        OFF_TRIGGER_MOTOR, not from this side's block, so both triggers report
        and set the same switch. A UI that draws one enable per trigger will
        show two switches over one byte and let someone ask for left-on/
        right-off, which the pad cannot do.

        The other four are stored per side. Space Station edits `scale` and
        `filter` as one number and writes it to both sides -- its own tooltip
        says "adjusting one trigger syncs the other" -- so whether this pad's
        firmware reads the right side's copy at all is untested.
        """
        base = OFF_TRIGGER_MOTOR + 1 + self._side(side) * 14
        return {
            "enabled": self.blob[OFF_TRIGGER_MOTOR] == ENABLED,
            "minimum": self.blob[base + 1],
            "maximum": self.blob[base + 2],
            "block": self.blob[base + 3],
            "scale": self.blob[base + 5],
        }

    def set_trigger_motor(self, side, enabled=None, minimum=None, maximum=None,
                          scale=None, block=None):
        base = OFF_TRIGGER_MOTOR + 1 + self._side(side) * 14
        if enabled is not None:
            self.blob[OFF_TRIGGER_MOTOR] = ENABLED if enabled else DISABLED
        if minimum is not None:
            self.blob[base + 1] = max(0, min(255, minimum))
        if maximum is not None:
            self.blob[base + 2] = max(0, min(255, maximum))
        if block is not None:
            self.blob[base + 3] = max(0, min(255, block))
        if scale is not None:
            # 1..100, not 0..255: Flydigi stores this one as the percentage
            # their slider shows, while min/max are the same slider's percent
            # scaled to a byte. Clamping it at 255 would offer a range that is
            # two and a half times what the field means.
            self.blob[base + 5] = max(0, min(TRIGGER_MOTOR_SCALE_MAX, scale))
        # The window is read as a pair, so keep it the right way round rather
        # than letting a slider produce an inverted one -- as set_vibration does.
        if self.blob[base + 1] > self.blob[base + 2]:
            self.blob[base + 1], self.blob[base + 2] = (
                self.blob[base + 2], self.blob[base + 1])

    @staticmethod
    def _side(side):
        if isinstance(side, str):
            return SIDES.index(side)
        return SIDE_RIGHT if side else SIDE_LEFT

    def __repr__(self):
        version = f"{self.proto_version >> 8}.{self.proto_version & 0xF}"
        return (f"<MappingConfig cfg={self.cfg_id} v{version} "
                f"{len(self.blob)}B title={self.title!r}>")
