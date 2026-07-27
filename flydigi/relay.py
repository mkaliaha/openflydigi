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
