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

Everything this module does not interpret is carried through byte-for-byte, so
writing a config back cannot disturb settings it does not understand.
"""
import random
import struct

from . import blobs, device
from .blobs import PKG_SIZE, ProtocolError, build   # re-exported for callers

CMD_STATUS = 161
CMD_APPLY = 162
CMD_READ = 163
CMD_WRITE_START = 164
CMD_WRITE_PACK = 165
CMD_SAVE = 166
CMD_SAVE_SWITCH = 171
CMD_RESET = 175

# The pad keeps **two** banks of four profiles, not four profiles. 0..3 are the
# XInput ones every other command in this module addresses; 4..7 are the
# Nintendo Switch ones, which the pad plays when it is in Switch mode and which
# nothing here used to touch. `ApplySwitchConfigAsync` in Flydigi's service is
# the tell -- it refuses any id outside `> 3 && < 8` -- and their `SaveConfig`
# accepts a live slot anywhere in 0..7.
SLOTS = 4
SWITCH_BANK = 4

OFF_PROTO_VERSION = 0
OFF_PACKAGE_COUNT = 2
OFF_KEY_TABLE = 13
OFF_JOYSTICK_CURVE = 109   # 2 x 7: sensitivity curve per stick
OFF_TRIGGER_CURVE = 123    # 2 x 7: travel curve per trigger
OFF_MOTION = 137           # 8: the gyro mapped to a stick
OFF_GRIP_VIBRATION = 145   # 1 + 2 x 4: the grip motors
OFF_TRIGGER_MOTOR = 154    # 1 + 2 x 14: the trigger motors
OFF_FORCE_TRIGGER = 185    # 2 x 20: the adaptive-trigger effect

# The one effect that uses the block's rumble-binding half -- effects.py calls
# it MODE_VIBRATION. Named here rather than imported so this module stays a
# byte layout with no opinion about what the effects mean.
FORCE_TRIGGER_BIND_MODE = 5
OFF_DATA_VERSION = 225
OFF_MACROS = 230           # 538 bytes: the macro page, header and bodies
OFF_TITLE = 770
OFF_JOYSTICK_EXTRA = 790   # 2 x 12: the 9-point bank, circularity and edge
OFF_MACRO_CYCLE = 820      # v3.1+: one repeat interval per macro slot
OFF_MOTION_CURVE = 830     # v3.1+: 6, the gyro's response curve
TITLE_BYTES = 20

# -- macros -------------------------------------------------------------------
#
# A macro is a sequence of button events the *pad* plays: the key table entry
# for its trigger key holds TARGET_MACRO, and the firmware runs the steps with
# nothing on the host involved. `m_fdg_macro_unit_struct_t` (btn, count, type,
# step[64]) and `m_fdg_macro_state_struct_t` (active, a unit pointer, cur_step,
# cur_time, keystate) are the firmware's own structs, carried into the SDK --
# the second is a running state machine no host would keep.
#
# The page at 230 is `m_fdg_macro_page_struct_t`: a count byte, one offset per
# slot, then the bodies.
#
#     [0]           how many macros, 1..5; anything else means none
#     [1..6]        each macro's offset into the bodies, in 4-byte words
#     [6..538]      the bodies, each one
#                       [0] trigger key id   [1..3] step count, little endian
#                       [3] type             then 4 bytes per step:
#                       cumulative time (16-bit, in 10 ms ticks), key id, event
#
# Times are stored cumulative and read back as gaps, which is what Flydigi's
# own bean holds, so a step's `delay` here is the pause before it.
#
# **Where this lives depends on the protocol version.** From v3.2 the macros
# move out of the blob into their own store behind commands 172/173/174, with
# ten macros at 1 ms resolution. An Apex 5 reports v3.1 and keeps them here --
# confirmed by the hardware dump, which holds five cycle bytes at 820. Anything
# newer is refused rather than written to a region its firmware does not read.
MACRO_REGION = 538
MACRO_SLOTS = 5
MACRO_WORD = 4
MACRO_HEADER = 1 + MACRO_SLOTS
# 133 words for the bodies, and each macro spends one of them on its own header
# -- which is exactly Flydigi's pair of limits, five macros and 128 steps in
# total, rather than a second constraint on top of them.
MACRO_WORDS = (MACRO_REGION - MACRO_HEADER) // MACRO_WORD
MACRO_STEP_BUDGET = MACRO_WORDS - MACRO_SLOTS
MACRO_TICK_MS = 10         # the stored unit below protocol 3.2
MACRO_MAX_VERSION = 2      # (proto & 0xF) at or above this keeps macros elsewhere

# MacroEnableType: what pressing the trigger key does.
MACRO_NONE, MACRO_ONCE, MACRO_WHILE_HELD, MACRO_TOGGLE = 0, 1, 2, 3
MACRO_TYPES = {
    MACRO_ONCE: "once",              # one pass per press
    MACRO_WHILE_HELD: "while held",  # repeats until the key is let go
    MACRO_TOGGLE: "toggle",          # a press starts it, another stops it
}

# MacroActionEvent. Hold is 5 and not 4 -- the enum skips a value, and guessing
# it from position would write an event the firmware does not know.
MACRO_RELEASE, MACRO_PRESS = 0, 1
MACRO_LEFT_STICK, MACRO_RIGHT_STICK, MACRO_HOLD = 2, 3, 5
MACRO_EVENTS = {
    MACRO_RELEASE: "release",
    MACRO_PRESS: "press",
    MACRO_HOLD: "hold",
    MACRO_LEFT_STICK: "left stick",
    MACRO_RIGHT_STICK: "right stick",
}

# The repeat interval at 820, one byte per slot, stored as milliseconds / 10.
# 0xFF is what an untouched slot holds and is carried through as None rather
# than reported as 2550 ms.
MACRO_INTERVAL_UNSET = 0xFF
MACRO_INTERVAL_MAX = 2540

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

# Remappable buttons on an Apex 5, in the order a UI should present them. The
# key table has 32 slots but most are unpopulated on this model.
#
# `GenerateControllerApex5` (FlydigiControllerFactory.cs:525) is what decides
# it. That is the device capability list, and it enumerates the keys the pad
# *has*: no C and no Z -- those are Vader parts, declared only by the
# Vader3/4/5 factories -- and it does carry M5 and M6.
#
# The renderer's k5 file
# (asar/.vite/renderer/main_window/assets/device_config_k5-*.js) is *not* a
# second capability list, whatever it looks like. It is a UI layout table --
# id, name, position, size, rotation and a `clickable` flag -- that draws the
# interactive controller picture, one absolutely-positioned div per entry.
# `clickable` decides whether that rectangle reacts to a click at all: it is
# false for Fn (24), Turbo (25) and Home (27), so Space Station's image offers
# no way to select those three, and true for JsLeft (240) and JsRight (241),
# where a click opens the joystick tab rather than a remap. It says what their
# interface does, and nothing about what the firmware accepts -- see Home.
#
# Fn is what the pad is silkscreened with for key id 24, which the SDK calls
# Menu; it switches profiles. Turbo arms the per-key turbo modes rather than
# being a mappable input of its own. Neither is listed below, and neither has
# been tried on hardware: both are probably rebindable in the same way Home
# turned out to be, but the case for offering it does not carry across. Home
# takes a press every time a Steam overlay is opened; Fn and Turbo take a few
# dozen in half a year, so the worn-out-button argument below is theirs alone.
#
# Home *is* listed, because an inert rectangle in their app is not the firmware
# refusing anything -- measured on a wired Apex 5, both directions:
#
#   m1 -> home   pressing M1 fired the Guide button
#   home -> a    pressing Home sent A, with no Guide event reaching the pad's
#                evdev node at all
#
# So the firmware honours Home as a remap source and as a target. It is worth
# offering for a pad whose Home button has failed, which is the one case Space
# Station's UI leaves no way out of.
#
# JsLeft and JsRight are the stick bodies, not the Thl/Thr clicks at ids 14 and
# 15, which are ordinary keys here. What their tab configures -- `JoystickMapType
# {Joystick=0, Keyboard=1, Mouse=2, DPad=3}` -- never reaches the pad: the parser
# hardcodes `MapType = Joystick` when reading a blob, no command factory in the
# SDK carries a keyboard key id, and Space Station converts a stick to keyboard
# or mouse on the host (KeyboardMouseInjectRunner). All the pad ever stores is
# 127 in a stick's centre byte, meaning "not acting as a stick", which is why
# that value is a sentinel above rather than a dead zone.
APEX5_KEYS = [
    "a", "b", "x", "y",
    "up", "down", "left", "right",
    "lb", "rb", "lt", "rt",
    "thl", "thr",
    "select", "start", "home",
    "m1", "m2", "m3", "m4",
    "m5", "m6",
]

# A Vader's keys are an Apex 5's plus the two it has and this pad does not: C
# and Z, which the Vader3/4/5 factories declare and `GenerateControllerApex5`
# does not. They go after Rb because that is where they sit in the SDK's own
# declaration order, which is what a UI should follow.
#
# **The order is presentation only.** A key's place in the blob comes from
# `KEY_IDS[name]` -- C is 16 and Z is 17, in the key table all along, since the
# table has 32 slots and most models leave most of them unpopulated. So adding
# two names to a list moves no offsets and cannot disturb an Apex 5.
VADER5_KEYS = (APEX5_KEYS[:APEX5_KEYS.index("rb") + 1] + ["c", "z"]
               + APEX5_KEYS[APEX5_KEYS.index("rb") + 1:])

# Which physical keys a model has, by DeviceCode. Only `k5` is a pad this
# project drives; `f5` is here for the same reason `identity.CAPABILITIES`
# carries it, and because a key list keyed by model is the only thing that
# makes "reset every key to default" mean what it says on a pad that is not
# this one.
MODEL_KEYS = {"k5": APEX5_KEYS, "f5": VADER5_KEYS}

# What a key may be remapped *to*. Deliberately smaller than the key list: the
# extra buttons -- the M1-M4 paddles on the back and the M5/M6 shoulder pair,
# and a Vader's C and Z -- have no XInput equivalent, so a host cannot receive
# them. They are sources: you map a paddle onto a real button, which is what the
# pad ships doing. Offering them as targets would let someone map A to something
# nothing can see, which reads as "A stopped working".
EXTRA_KEYS = ["m1", "m2", "m3", "m4", "m5", "m6", "c", "z"]
XINPUT_TARGETS = [key for key in APEX5_KEYS if key not in EXTRA_KEYS]


def keys_for(code):
    """The physical keys of a model, by DeviceCode.

    Falls back to the Apex 5's list for an unknown code, which is the pad this
    was measured on and the only one anything here has ever written to. Not a
    guess about other hardware: a caller that reaches this with something else
    has already passed `identity.require`, which does not let an unknown model
    through.
    """
    return MODEL_KEYS.get(code, APEX5_KEYS)


def targets_for(code):
    """What a key on this model may be remapped to. See EXTRA_KEYS."""
    return [key for key in keys_for(code) if key not in EXTRA_KEYS]

# -- the gyro mapped to a stick, at 137 ---------------------------------------
#
# MotionMapType: what the gyro drives. The pad does the mapping itself, which
# is the whole point of it -- gyro aim in any game, with nothing running on the
# host, where Linux otherwise has only Steam Input.
#
# `mouse` is a real value of theirs and is written like any other. It is not a
# pad feature: Space Station moves the host's pointer from the raw motion stream
# in its own KeyboardMouseInjectRunner, and the pad's copy is a note to that
# process, so a Linux host has nothing to act on it. That makes it something not
# to *offer* -- which the app does not -- rather than something to refuse here.
MOTION_OFF, MOTION_LEFT_STICK, MOTION_RIGHT_STICK, MOTION_MOUSE = 0, 1, 2, 3
MOTION_TARGETS = {
    MOTION_OFF: "off",
    MOTION_LEFT_STICK: "left stick",    # Space Station labels it "racing games"
    MOTION_RIGHT_STICK: "right stick",  # ... and this one "shooting games"
    MOTION_MOUSE: "mouse",
}
MOTION_TARGET_IDS = {name: value for value, name in MOTION_TARGETS.items()}

# MotionEnableType: what the enable key does. There is no "always on" -- the
# gyro is bound to a key either way, and a mapping with no key set is a mapping
# nothing can turn on.
MOTION_CLICK, MOTION_PRESS = 0, 1
MOTION_ENABLE_TYPES = {
    MOTION_CLICK: "click",   # a press turns it on, another turns it off
    MOTION_PRESS: "hold",    # on for as long as the key is held
}
MOTION_ENABLE_TYPE_IDS = {name: value for value, name in MOTION_ENABLE_TYPES.items()}

# MotionUseMode. Not a control of its own in Space Station: their save path
# derives it from the target -- left stick means Racer, right stick means FPS --
# so the two travel together and `set_motion` derives it the same way unless a
# caller says otherwise. What the firmware does differently between them is
# unmeasured.
MOTION_FPS, MOTION_RACER = 0, 1
MOTION_USE_MODES = {MOTION_FPS: "fps", MOTION_RACER: "racer"}
MOTION_USE_MODE_IDS = {name: value for value, name in MOTION_USE_MODES.items()}

# ControllerKey.None -- what an unset enable key holds. Numerically the same as
# TARGET_IDENTITY, and unrelated: this is the absence of a key, that one is a
# key left doing its own job.
MOTION_KEY_NONE = 255

# Both sliders in Space Station's gyro panel run 0..100, and the two fields are
# bytes, so the ceiling is the interface's rather than the wire's.
MOTION_SENSITIVITY_MAX = 100
MOTION_DEAD_ZONE_MAX = 100

# The response curve at 830 is the joystick core block minus its type byte, on
# the same 0..127 scale, and the factory holds the identity line there.
MOTION_CURVE_ENTRY = 6
MOTION_CURVE_MAX = 127


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
    for body in blobs.replies(ctrl, build(CMD_STATUS, b""), wait,
                              blobs.answers(CMD_STATUS)):
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
    for body in blobs.replies(ctrl, build(CMD_APPLY, bytes([cfg_id])), wait,
                              blobs.answers(CMD_APPLY)):
        if body[2] == CMD_APPLY:
            return True
    return False


def next_data_version(current=None):
    """A fresh change tag for a slot, different from the one it carries now.

    The tag is how anything holding a cached copy of a profile decides whether
    that copy is still good: `read_status` reports one per slot without reading
    a config at all. Space Station rerolls a random one on *every* save for
    exactly that reason -- `SaveConfig` loops on `Random.Next(65535)` until it
    differs from the current value -- and then trusts the tag thereafter.

    Which is why leaving it alone, as this module used to do, was wrong rather
    than merely conservative. A rename written with the old tag lands on the
    pad and every other application goes on showing the name it had cached,
    because the one flag that says "this changed" says it did not. Measured
    here: renaming a slot through the CLI left all four tags at their previous
    values.

    0xFFFF is skipped -- that is what an untouched slot reads, so handing it
    back would claim a written slot had never been written.
    """
    while True:
        value = random.randrange(0, 0xFFFF)
        if value != current:
            return value


def save_config(ctrl, version=0, wait=2.0):
    """Commit the working config to flash. Slow -- the pad takes seconds.

    `version` is the id `read_status` reports for the slot, and the same value
    the blob carries at OFF_DATA_VERSION. Flydigi's SDK gives this command a
    10 second timeout where every other command gets 500 ms, which is what a
    flash write looks like.

    Pass `next_data_version(config.data_version)`, not the config's own tag:
    the point of the field is to change when the profile does. Passing 0 -- the
    default -- claims the tag is zero, which is a lie of a different shape and
    is kept only because nothing has confirmed what the pad does with it.
    """
    payload = struct.pack("<H", version & 0xFFFF)
    for body in blobs.replies(ctrl, build(CMD_SAVE, payload), wait,
                              blobs.answers(CMD_SAVE)):
        if body[2] == CMD_SAVE:
            return True
    return False


def switch_cfg_id(cfg_id):
    """The Switch-bank id for one of the four XInput slots."""
    if not 0 <= cfg_id < SLOTS:
        raise ValueError(f"no profile slot {cfg_id}; there are {SLOTS}")
    return cfg_id + SWITCH_BANK


def reset_config(ctrl, cfg_id, wait=10.0):
    """Restore one slot to the pad's factory profile. Slow -- a flash write.

    Command 175, `ResetMappingConfigByCfgIdCommandFactory`, which Flydigi give a
    10 second timeout where almost everything else gets 500 ms. This is the
    stock app's "Restore default", and it resets the *whole* slot -- the key
    table, the sticks, the trigger blocks, the macro page and **the profile's
    name**, since the name is a field of the blob at OFF_TITLE like any other.
    Observed through Space Station on the pad here: a slot renamed out of its
    factory name came back carrying that name again.

    Destructive and not undoable. Anything offering it has to say that the name
    goes too, because "restore defaults" does not describe losing it.
    """
    for body in blobs.replies(ctrl, build(CMD_RESET, bytes([cfg_id])), wait,
                              blobs.answers(CMD_RESET)):
        if body[2] == CMD_RESET:
            return True
    return False


def _build_save_switch(cfg_id, version):
    """171's frame, which Flydigi's own builder writes inconsistently.

    The length byte says 4, exactly as command 166's does -- and 166 carries two
    payload bytes with its checksum at 7. 171 carries *three*: the version, then
    the target slot at offset 7, where 166 puts the checksum. The checksum then
    lands at 8, over `Crc(3, 3 + length)` -- a range that stops at 6 and so
    excludes the slot id it is supposed to protect.

    Reproduced literally rather than corrected. The pad answers the bytes it is
    sent and not the arithmetic behind them, and a frame this module built
    "properly" -- length 5, checksum covering the slot -- would be a different
    packet from the one Space Station is known to get an ACK for.
    """
    buf = device.build(CMD_SAVE_SWITCH)
    buf[4] = 4
    buf[5] = version & 0xFF
    buf[6] = (version >> 8) & 0xFF
    buf[7] = cfg_id
    buf[8] = device.checksum(buf, 3, 7)
    return buf


def save_switch_config(ctrl, cfg_id, version, wait=10.0):
    """Commit the running config into a Switch-bank slot. Slow -- a flash write.

    Command 171, `SaveCurrentSwitchMappingConfigCommandFactory`. It is command
    166 with a target: 166 commits whichever profile the pad is running into the
    slot it came from, while 171 commits it into the slot you name. Flydigi only
    ever aim it at the Switch bank -- `ApplySwitchConfigAsync` refuses anything
    outside 4..7 -- and `cfg_id` here is checked the same way, because what the
    firmware does with 171 aimed at an XInput slot is unmeasured and the failure
    mode would be a profile silently overwritten.

    `version` is the slot's change tag, as for `save_config`; roll a fresh one
    with `next_data_version`.
    """
    if not SWITCH_BANK <= cfg_id < SWITCH_BANK + SLOTS:
        raise ValueError(
            f"cfg_id {cfg_id} is not a Switch slot; use switch_cfg_id() to get "
            f"one of {SWITCH_BANK}..{SWITCH_BANK + SLOTS - 1}")
    for body in blobs.replies(ctrl, _build_save_switch(cfg_id, version), wait,
                              blobs.answers(CMD_SAVE_SWITCH)):
        if body[2] == CMD_SAVE_SWITCH:
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

    @data_version.setter
    def data_version(self, value):
        # Kept in step with what command 166 was told, so a caller's own copy
        # agrees with what `read_status` will report for the slot afterwards.
        struct.pack_into("<H", self.blob, OFF_DATA_VERSION, int(value) & 0xFFFF)

    def normalise_for_switch(self):
        """Strip what a Switch cannot run, and say what was stripped.

        Copied from what Flydigi's own `SaveSwitchConfig` does to a config
        before it writes one: every key bound to a keyboard key goes back to
        sending itself, and both sticks are forced to act as sticks. Neither
        binding means anything to a Switch -- the keyboard half is host-side
        injection this project does not do at all (`TARGET_KEYBOARD` is a bare
        sentinel with no key code anywhere in the blob), and a stick set to
        something that is not a stick carries `CENTER_NOT_A_STICK` where its
        dead zone belongs.

        So this matters for profiles that came from Space Station rather than
        for ones made here, which is exactly why it is not optional: the slot
        being copied is whatever the pad is running.

        Returns the list of things changed, so a caller can tell the user what
        their Switch profile will not have. Edits in place.
        """
        stripped = []
        for name in KEY_NAMES.values():
            try:
                offset, key_id = self._entry(name)
            except KeyError:
                continue
            if self.blob[offset] == TARGET_KEYBOARD:
                self.set_mapping(name, None)
                stripped.append(f"{name} was bound to a keyboard key")
        for side in ("left", "right"):
            if not self.stick(side)["is_stick"]:
                self.set_stick(side, center=0)
                stripped.append(f"the {side} stick was not acting as a stick")
        return stripped

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
        # Pointing a key at something other than its macro orphans the body,
        # and the firmware goes on playing it: the macro page and the key table
        # are read independently, so the key sends its new binding *and* runs
        # the old macro underneath it. Measured -- M1 remapped to A with a body
        # of three X taps gives `press a`, `x x x`, `release a`. Flydigi's own
        # repository drops the macro at exactly this moment, and this is the
        # only place that can see the change happen.
        if (target != "macro" and self.macros_in_blob
                and self.blob[offset] == TARGET_MACRO):
            name = KEY_NAMES.get(key_id)
            self.set_macros([m for m in self.macros() if m["key"] != name])
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
        """Only the keys that differ from the default -- what a UI should mark.

        **Pass the pad's own keys.** A blob has no idea which model it came off,
        and the default here is the Apex 5's list, so a caller that leaves it
        out on any other pad silently reports nothing about the buttons that
        model has and this one does not -- a Vader's C and Z. Use
        `keys_for(code)`, where `code` is what `identity.require` already
        returned to whoever opened the pad.
        """
        out = {}
        for key in keys or APEX5_KEYS:
            target, mode, frequency = self.mapping(key)
            if target != key or frequency:
                out[key] = (target, mode, frequency)
        return out

    # -- macros ------------------------------------------------------------
    #
    # See the block comment beside OFF_MACROS for the page layout. A macro is
    # a dict:
    #
    #     {"key": "m1", "type": MACRO_ONCE, "interval": 30,
    #      "steps": [{"delay": 0, "key": "a", "event": MACRO_PRESS}, ...]}
    #
    # `delay` is the pause in milliseconds before that step, quantised to 10 ms
    # on the wire. `interval` is Flydigi's own per-slot field, the one the
    # factory leaves at 30 ms; None means the slot has never been written.

    @property
    def macro_page(self):
        """The stored macro bytes, for asking whether they have changed.

        **Why a caller needs this.** A key-table write takes effect the moment
        the packet lands, but a macro does not: the firmware parses the page
        into its own structs when a profile is *loaded*, so a macro written and
        not applied is stored and never played. Measured on hardware -- the
        same macros produced nothing until command 162 went out, and played to
        the millisecond afterwards. So anything writing a profile compares this
        and applies when it differs, rather than applying every time and making
        the pad re-seat its trigger motors over a remap that did not need it.
        """
        page = bytes(self.blob[OFF_MACROS : OFF_MACROS + MACRO_REGION])
        cycles = bytes(self.blob[OFF_MACRO_CYCLE : OFF_MACRO_CYCLE + MACRO_SLOTS])
        return page + cycles

    @property
    def macros_in_blob(self):
        """Whether this profile is one that carries its macros at 230.

        False from protocol 3.2, where they move to their own command family.
        Reading is allowed either way -- an older blob still parses -- but
        writing is not, because on such a pad this region is not what the
        firmware plays.
        """
        return (self.proto_version & 0xF) < MACRO_MAX_VERSION

    def _macro_intervals(self):
        """The five repeat intervals, in ms, or None where never written."""
        base = OFF_MACRO_CYCLE
        if len(self.blob) < base + MACRO_SLOTS:
            return [None] * MACRO_SLOTS
        return [None if raw == MACRO_INTERVAL_UNSET else raw * MACRO_TICK_MS
                for raw in self.blob[base : base + MACRO_SLOTS]]

    def macros(self):
        """Every macro stored in this profile, in slot order.

        Permissive on purpose: a step whose key id is not one a host can
        receive is reported as the raw id rather than refused, so a profile
        written by something else reads back as what it is. `set_macros` is
        the strict half.
        """
        data = self.blob[OFF_MACROS : OFF_MACROS + MACRO_REGION]
        count = data[0] if data else 0
        # An untouched region is 0xFF throughout, so the count byte alone
        # separates "five macros" from "never written" -- as Flydigi's reader
        # does, which returns nothing outside 1..5.
        if not 1 <= count <= MACRO_SLOTS:
            return []
        offsets = list(data[1 : 1 + count])
        intervals = self._macro_intervals()
        out = []
        for slot in range(count):
            start = MACRO_HEADER + offsets[slot] * MACRO_WORD
            end = (MACRO_HEADER + offsets[slot + 1] * MACRO_WORD
                   if slot + 1 < count else len(data))
            if not 0 <= start <= end <= len(data) or end - start < MACRO_WORD:
                continue
            body = data[start:end]
            steps_stored = body[1] | (body[2] << 8)
            # Trust the space, not the count: a truncated body would otherwise
            # read past its own end into the next macro's first step.
            steps = min(steps_stored, (len(body) - MACRO_WORD) // MACRO_WORD)
            actions, previous = [], 0
            for index in range(steps):
                at = MACRO_WORD + index * MACRO_WORD
                when = (body[at] | (body[at + 1] << 8)) * MACRO_TICK_MS
                actions.append({
                    "delay": when - previous,
                    "key": KEY_NAMES.get(body[at + 2], body[at + 2]),
                    "event": body[at + 3],
                })
                previous = when
            out.append({
                "key": KEY_NAMES.get(body[0], body[0]),
                "type": body[3],
                "interval": intervals[slot],
                "steps": actions,
            })
        return out

    def macro(self, key):
        """The macro bound to one key, or None."""
        name = KEY_NAMES.get(key, key) if not isinstance(key, str) else key
        return next((m for m in self.macros() if m["key"] == name), None)

    def set_macros(self, macros):
        """Replace the whole macro page.

        Everything is written from this list, so a caller edits by reading
        `macros()`, changing it, and passing it back -- the same read-modify-
        write the rest of this module uses for blocks with more than one field.

        The key table is left alone. Binding a key to its macro is
        `set_mapping(key, "macro")`, and `set_macro` does both.
        """
        if not self.macros_in_blob:
            raise ProtocolError(
                f"protocol {self.proto_version >> 8}.{self.proto_version & 0xF} "
                "keeps macros in their own store, not in the profile")
        macros = list(macros)
        if len(macros) > MACRO_SLOTS:
            raise ValueError(f"the pad holds {MACRO_SLOTS} macros, got {len(macros)}")
        total = sum(len(m.get("steps", ())) for m in macros)
        if total > MACRO_STEP_BUDGET:
            raise ValueError(
                f"{total} steps across {len(macros)} macro(s); the page holds "
                f"{MACRO_STEP_BUDGET} in total")

        header = bytearray(MACRO_HEADER)
        header[0] = len(macros)
        body = bytearray()
        word = 0
        for slot, macro in enumerate(macros):
            header[1 + slot] = word
            steps = list(macro.get("steps", ()))
            body += bytes([self._macro_key(macro.get("key")),
                           len(steps) & 0xFF, (len(steps) >> 8) & 0xFF,
                           int(macro.get("type", MACRO_ONCE)) & 0xFF])
            ticks = 0
            for step in steps:
                # Each gap is quantised on its own and the ticks accumulated,
                # which is what Flydigi's writer does. Summing first and
                # dividing once would drift away from what their app produces
                # for the same recording.
                ticks = min(0xFFFF, ticks + max(0, int(step.get("delay", 0)))
                            // MACRO_TICK_MS)
                body += bytes([ticks & 0xFF, ticks >> 8,
                               self._macro_step_key(step.get("key")),
                               int(step.get("event", MACRO_PRESS)) & 0xFF])
            word += 1 + len(steps)

        # 0xFF fill behind the bodies, as their writer emits -- so a page we
        # write matches one Space Station wrote byte for byte.
        region = bytearray(b"\xff" * MACRO_REGION)
        region[: len(header) + len(body)] = header + body
        self.blob[OFF_MACROS : OFF_MACROS + MACRO_REGION] = region

        # The intervals belong to the slots rather than to the macros in them,
        # and Flydigi's writer re-emits all five from the read -- so a slot
        # nobody set keeps its byte instead of being blanked. Passing None
        # therefore means "leave this slot alone", which is what a factory 30 ms
        # survives on.
        if len(self.blob) >= OFF_MACRO_CYCLE + MACRO_SLOTS:
            for slot, macro in enumerate(macros):
                interval = macro.get("interval")
                if interval is not None:
                    self.blob[OFF_MACRO_CYCLE + slot] = max(
                        0, min(MACRO_INTERVAL_MAX, int(interval))) // MACRO_TICK_MS

    def set_macro(self, key, steps, macro_type=MACRO_ONCE, interval=None):
        """Bind a macro to one key, replacing any macro already on it.

        Writes the key table as well: a body with no `TARGET_MACRO` beside it
        is a macro the pad will never run, and a key set to `TARGET_MACRO` with
        no body is a key that does nothing. The two are one edit.
        """
        name = self._macro_key_name(key)
        macros = [m for m in self.macros() if m["key"] != name]
        if len(macros) >= MACRO_SLOTS:
            raise ValueError(
                f"all {MACRO_SLOTS} macro slots are taken; clear one first")
        macros.append({"key": name, "type": macro_type, "interval": interval,
                       "steps": list(steps)})
        self.set_macros(macros)
        self.set_mapping(name, "macro")

    def clear_macro(self, key):
        """Drop the macro on a key and give the key back to itself."""
        name = self._macro_key_name(key)
        macros = [m for m in self.macros() if m["key"] != name]
        self.set_macros(macros)
        # The body is already gone, so `set_mapping` finds nothing to clean up
        # and this is a plain key-table write.
        if self.mapping(name)[0] == "macro":
            self.set_mapping(name, None)

    @staticmethod
    def _macro_key_name(key):
        name = key if isinstance(key, str) else KEY_NAMES.get(key)
        if name not in KEY_IDS:
            raise KeyError(f"no key {key!r}")
        return name

    @staticmethod
    def _macro_key(key):
        """A trigger key id. Any key on the shell may run a macro."""
        if isinstance(key, int):
            return key & 0xFF
        return KEY_IDS[MappingConfig._macro_key_name(key)]

    @staticmethod
    def _macro_step_key(key):
        """A key id for one step, refusing the ones nothing can receive.

        M1-M4 and C/Z are remap *sources*: they have no XInput equivalent, so a
        step that presses one is a step the host never sees. Same reasoning as
        XINPUT_TARGETS, and the same failure if it is skipped -- a macro that
        plays perfectly and does nothing.
        """
        name = key if isinstance(key, str) else KEY_NAMES.get(key)
        if name not in XINPUT_TARGETS:
            raise ValueError(
                f"a macro step cannot send {key!r}: only {', '.join(XINPUT_TARGETS)} "
                "reach a host, the rest are remap sources with no XInput id")
        return KEY_IDS[name]

    # -- the gyro mapped to a stick ---------------------------------------
    #
    # 8 bytes, `m_fdg_macro_motion_mapping_struct_t`:
    #
    #   [0] target, MotionMapType        [4] sensitivity, x axis
    #   [1] enable key, ControllerKey    [5] sensitivity, y axis
    #   [2] enable type                  [6] use mode, MotionUseMode
    #   [3] dead-zone offset             [7] second enable key
    #
    # The pad plays this itself, so a gyro-to-stick mapping works in any game
    # with nothing running on the host. Its own UI warns that turning it on
    # lowers the polling rate.
    #
    # The response curve belongs to this block and does not live in it -- six
    # more bytes at 830, in the v3.1 tail. See `motion_curve`.

    def motion(self):
        """Where the gyro is mapped, and what turns it on.

        `sensitivity` is the pair collapsed the way Flydigi's reader collapses
        it, to the larger of the two axes, because their writer only ever emits
        one number into both. `sensitivity_xy` is what the bytes really hold,
        and on a factory pad the two differ -- 25 and 20 -- so the block ships
        in a state their own software cannot produce.

        Enable keys read as key names, or None for ControllerKey.None. Both are
        reported: which of them the firmware honours is unmeasured, and see
        `set_motion` for why the second one is a trap.
        """
        base = OFF_MOTION
        x, y = self.blob[base + 4], self.blob[base + 5]
        return {
            "target": self.blob[base],
            "enable_type": self.blob[base + 2],
            "keys": (self._motion_key_name(self.blob[base + 1]),
                     self._motion_key_name(self.blob[base + 7])),
            "sensitivity": max(x, y),
            "sensitivity_xy": (x, y),
            "dead_zone": self.blob[base + 3],
            "use_mode": self.blob[base + 6],
        }

    def set_motion(self, target=None, enable_type=None, keys=None,
                   sensitivity=None, dead_zone=None, use_mode=None):
        """Map the gyro onto a stick.

        **This mirrors `ParseMotionConfigToArray` and takes no view of its
        own.** Every rule below is Flydigi's, including the two that are traps,
        because a byte layout is the wrong place to hold an opinion -- the same
        reason the force-trigger block here only moves bytes. What to *offer* is
        the caller's business, and `gui/models/profile.py` is where this
        project's answers to that live.

        `target` and `enable_type` take either the id or the name from
        MOTION_TARGETS / MOTION_ENABLE_TYPES. `use_mode` is derived from the
        target, as their save path derives it -- left stick means Racer, right
        stick means FPS -- and turning the mapping off leaves it alone, as
        theirs does. `sensitivity` goes into both axis bytes from one number.

        Two rules are theirs and are worth knowing before calling this:

        **The second enable key is only written under Hold.** Their writer
        assigns `EnableKey[1]` inside `s == MotionEnableType_Press` and re-emits
        whatever it read otherwise. It matters because the factory blob holds 0
        in byte 7 -- D-pad Up, not "no key" -- and byte 7 is honoured on its own,
        measured with `tools/gyro-map-probe --window 3`. So turning the mapping
        on under Click leaves Up as a live second way to switch the gyro on,
        and this reproduces that rather than quietly fixing it. Pass an explicit
        pair under Hold to set it; the app shows the byte instead of hiding it.

        **Mouse is writable here.** The pad stores the byte and does nothing
        with it -- Space Station moves the host pointer from the raw motion
        stream, and the pad's copy is a note to that process. Refusing it in
        this module would mean a profile brought over from Windows could not be
        edited at all; the app simply does not offer it.
        """
        base = OFF_MOTION
        if target is not None:
            target = self._motion_value("target", target, MOTION_TARGET_IDS,
                                        MOTION_TARGETS)
            self.blob[base] = target
            if use_mode is None and target in (MOTION_LEFT_STICK,
                                               MOTION_RIGHT_STICK):
                use_mode = (MOTION_RACER if target == MOTION_LEFT_STICK
                            else MOTION_FPS)
        if enable_type is not None:
            self.blob[base + 2] = self._motion_value(
                "enable type", enable_type, MOTION_ENABLE_TYPE_IDS,
                MOTION_ENABLE_TYPES)
        if use_mode is not None:
            self.blob[base + 6] = self._motion_value(
                "use mode", use_mode, MOTION_USE_MODE_IDS, MOTION_USE_MODES)
        if self.blob[base] == MOTION_MOUSE:
            # Their Mouse branch, whole: no enable keys in the blob and no dead
            # zone, because both belong to the host process that does the work.
            self.blob[base + 1] = MOTION_KEY_NONE
            self.blob[base + 3] = 0
            self.blob[base + 7] = MOTION_KEY_NONE
        elif keys is not None:
            if isinstance(keys, (str, int)):
                keys = (keys, None)
            first, second = keys
            self.blob[base + 1] = self._motion_key(first)
            if self.blob[base + 2] == MOTION_PRESS:
                self.blob[base + 7] = self._motion_key(second)
        if sensitivity is not None:
            value = max(0, min(MOTION_SENSITIVITY_MAX, int(sensitivity)))
            self.blob[base + 4] = self.blob[base + 5] = value
        if dead_zone is not None and self.blob[base] != MOTION_MOUSE:
            self.blob[base + 3] = max(0, min(MOTION_DEAD_ZONE_MAX,
                                             int(dead_zone)))

    def motion_curve(self):
        """The gyro's response curve, or None on a protocol older than 3.1.

        Six bytes -- `zero, point1, point2, end` -- which is the joystick core
        block with its type byte removed, on the same 0..127 scale, and the
        factory holds the same identity line: `0 63 63 127 127 127`.

        **The pad does not read this block.** Written flat to zero output, with
        the mapping otherwise byte-identical to a run that drove the stick to
        0.97 of full travel, the stick still reached 1.10 -- measured with
        `tools/gyro-map-probe --window 5`. So it is inert, and nothing should
        offer a control for it.

        Space Station cannot edit it either, by three independent faults in one
        panel: the div holding the slider carries a hardcoded `none` class, the
        parent passes the value down as `Smoothness` while the child reads
        `smoothness`, and the save path never assigns the field at all. Their
        slider is also a 0..255 number over a field that is a four-node curve.
        Kept because the block is real and the layout is confirmed against
        their v3.1 writer, which re-emits all six bytes on every write.
        """
        base = OFF_MOTION_CURVE
        if len(self.blob) < base + MOTION_CURVE_ENTRY:
            return None
        return {
            "zero": self.blob[base],
            "point1": (self.blob[base + 1], self.blob[base + 2]),
            "point2": (self.blob[base + 3], self.blob[base + 4]),
            "end": self.blob[base + 5],
        }

    def set_motion_curve(self, zero=None, point1=None, point2=None, end=None):
        """Shape the gyro's response.

        `end` is settable here, unlike the joystick core block's, because
        Flydigi's v3.1 writer emits all six of these bytes from the bean on
        every write rather than leaving one of them to a reader that corrupts
        it.
        """
        base = OFF_MOTION_CURVE
        if len(self.blob) < base + MOTION_CURVE_ENTRY:
            raise ProtocolError(
                "this profile has no motion curve block -- protocol 3.1 only")
        if zero is not None:
            self.blob[base] = self._motion_curve_byte(zero)
        for offset, point in ((base + 1, point1), (base + 3, point2)):
            if point is not None:
                x, y = point
                self.blob[offset] = self._motion_curve_byte(x)
                self.blob[offset + 1] = self._motion_curve_byte(y)
        if end is not None:
            self.blob[base + 5] = self._motion_curve_byte(end)

    @staticmethod
    def _motion_curve_byte(value):
        return max(0, min(MOTION_CURVE_MAX, int(value)))

    @staticmethod
    def _motion_value(what, value, ids, names):
        """One of the block's small enums, by id or by name."""
        if isinstance(value, str):
            if ids is None or value not in ids:
                raise ValueError(f"no {what} called {value!r}")
            return ids[value]
        value = int(value)
        if value not in names:
            raise ValueError(f"no {what} {value}")
        return value

    @staticmethod
    def _motion_key(key):
        """An enable key as the block stores it. None means no key."""
        if key is None:
            return MOTION_KEY_NONE
        key_id = KEY_IDS[key] if isinstance(key, str) else int(key)
        if not 0 <= key_id <= 255:
            raise ValueError(f"no key id {key!r}")
        return key_id

    @staticmethod
    def _motion_key_name(byte):
        return None if byte == MOTION_KEY_NONE else KEY_NAMES.get(byte, byte)

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
