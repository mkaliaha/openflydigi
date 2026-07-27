"""Mapping between the Apex 5 (evdev) and a virtual DualSense.

Kept in the library rather than the CLI so it can be unit tested and reused by
a GUI later.
"""
from . import ds5, evdev

FLYDIGI_VID, FLYDIGI_PID = 0x37D7, 0x2501

# xpad exposes no BTN_TL2/TR2, so the digital L2/R2 come from the analog axes.
TRIGGER_DIGITAL_THRESHOLD = 0.12

# Positional mapping: Xbox layout -> DualSense layout.
FACE_BUTTONS = [
    (evdev.BTN_SOUTH, ds5.CROSS, 0),
    (evdev.BTN_EAST, ds5.CIRCLE, 0),
    (evdev.BTN_WEST, ds5.SQUARE, 0),
    (evdev.BTN_NORTH, ds5.TRIANGLE, 0),
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

# DualSense trigger effect type -> Flydigi effect.
#
# PROVISIONAL. The Flydigi modes are confirmed on hardware (0 normal,
# 1 race/constant resistance, 2 vibration-style), but which DS5 effect type
# should feel like which Flydigi mode needs tuning in a real game. Table-driven
# so it can be adjusted without touching relay logic.
EFFECT_MAP = {
    0x00: ("normal", None),
    0x05: ("normal", None),
    0x01: ("race", (0, 1)),       # rigid / feedback: start, force
    0x21: ("race", (0, 1)),
    0x26: ("race", (0, 1)),
    0x02: ("vibration", (0, 2)),  # weapon: start, end, force
    0x22: ("vibration", (0, 2)),
    0x25: ("vibration", (0, 2)),
    0x06: ("vibration", (0, 2)),
    0x23: ("vibration", (0, 2)),
    0x27: ("vibration", (0, 2)),
}


def translate(effect):
    """Map a DualSense trigger effect onto a Flydigi (side, mode, params)."""
    side = SIDE_ID[effect.side]
    kind, picks = EFFECT_MAP.get(effect.type, ("normal", None))
    params = list(effect.params)

    def pick(index, default):
        return params[index] if index < len(params) else default

    if kind == "normal":
        return side, 0, [0, 0, 0, 0]
    if kind == "race":
        return side, 1, [pick(picks[0], 0), max(1, pick(picks[1], 128)), 1, 0]
    return side, 2, [pick(picks[0], 0), 1, max(1, pick(picks[1], 128)), 40]


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
