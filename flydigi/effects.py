# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Trigger effect commands for the Apex 5.

Two families exist (see PROTOCOL.md):
  * SetForceTrigger (81/82) -- effect based. Used by everything so far.
  * K6Trigger (83/85/87)    -- waveform/realtime, gated on DeviceCode "k6".
    Not merely untested: as of July 2026 no controller with that code had
    shipped, so there was nothing to test it against. An Apex 6 is coming
    (FCC registration, July 2026), so this is transcription awaiting hardware.

The six effects below are Flydigi's whole `AdapterTriggerType` vocabulary, and
the same six appear twice: as the live command's `mode` byte and as the first
byte of the 20-byte per-side block a profile carries. This module owns the
vocabulary for both -- what each effect's knobs are called, what they are
allowed to be, and which byte each one lands in -- so the profile editor and
the live command cannot drift apart.
"""
import collections

from . import device
from .device import (
    CMD_RUMBLE,
    CMD_SET_FORCE_TRIGGER,
    CMD_SET_FORCE_TRIGGER_GRIP,
    SIDE_LEFT,
    SIDE_RIGHT,
)

# SetForceTrigger effect modes (params[1]), Flydigi's AdapterTriggerType.
MODE_NORMAL = 0
MODE_RACE = 1
MODE_SNIPER = 2
MODE_RECOIL = 3
MODE_LOCK = 4
MODE_VIBRATION = 5

# One knob of one effect. `minimum`/`maximum` are Space Station's own slider
# bounds, not the byte range: a trigger's travel tops out at 192 of 255, and
# several knobs are refused at 0 by the command builder, so a UI that offered
# 0..255 everywhere would hand the pad values it quietly rewrites.
#
# `default` is ours, not Flydigi's -- their defaults live in whatever profile
# is on the pad, so there is nothing to copy. They are deliberately gentle:
# picking an effect for the first time should be felt, not fought.
Param = collections.namedtuple(
    "Param", "key label description minimum maximum default kind")


def _number(key, label, description, minimum, maximum, default):
    return Param(key, label, description, minimum, maximum, default, "number")


def _switch(key, label, description, default=1):
    return Param(key, label, description, 0, 1, default, "switch")


Effect = collections.namedtuple("Effect", "mode key label description params")

# `label` is Space Station's English string for the effect; the descriptions
# are theirs too, shortened. "Match input" is their
# trigger_*_start_output_data: with it on, the trigger reports 0 until it
# reaches the start position and then covers the full range over what is left,
# so the resistance point becomes the new zero rather than a bump mid-travel.
_MATCH = _switch(
    "match_input", "Match input to the start position",
    "Report no input until the effect begins, then use the travel that is left")

EFFECTS = (
    Effect(MODE_NORMAL, "normal", "General",
           "No added resistance -- the trigger's own travel", ()),
    Effect(MODE_RACE, "race", "Racing",
           "Constant resistance past a point, for a throttle", (
               _number("start", "Damping start position",
                       "Travel that must be pressed before damping begins",
                       0, 192, 50),
               _number("resistance", "Damping strength",
                       "How hard the trigger pushes back", 1, 255, 40),
           )),
    # `key` is the SDK's enum name and `label` is what Space Station shows a
    # user, and for modes 2 and 3 **those are crossed**. Their picker labels
    # AdapterTriggerType_Sniper with `trigger_mode_K2_recoil` ("Recoil", zh 机枪
    # "machine gun") and AdapterTriggerType_Recoil with `trigger_mode_K2_sniper`
    # ("Sniper", zh 狙击). The behaviour follows the label, not the enum: mode 2
    # rattles, mode 3 resists and breaks through, both confirmed by feel.
    #
    # So the labels below are deliberately not the enum names. Someone acting on
    # a Flydigi recommendation to "use Sniper" must land on the same effect here
    # that they would there, and the DualSense mapping agrees -- its
    # vibration/automatic-gun effect maps to mode 2, the machine gun.
    Effect(MODE_SNIPER, "sniper", "Recoil",
           "A vibration that begins once the trigger is held past a point", (
               _number("start", "Vibration start position",
                       "Travel that must be pressed before vibration begins",
                       0, 192, 50),
               _number("press", "Start pressure",
                       "Pressure needed at that position to set it off",
                       1, 255, 25),
               _number("strength", "Vibration strength",
                       "How hard the trigger vibrates", 1, 255, 20),
               _number("frequency", "Vibration frequency",
                       "How fast it vibrates", 1, 255, 20),
               _MATCH,
           )),
    Effect(MODE_RECOIL, "recoil", "Sniper",
           "A resisting band that gives way, like a weapon's break point", (
               _number("start", "Breakthrough start position",
                       "Travel that must be pressed before the band begins",
                       0, 192, 50),
               _number("travel", "Breakthrough travel",
                       "How far the band lasts", 1, 255, 30),
               _number("resistance", "Breakthrough resistance",
                       "Pressure needed to push through it", 1, 255, 40),
               _MATCH,
           )),
    Effect(MODE_LOCK, "lock", "Trigger lock",
           "A hard stop -- the trigger will not travel past the point", (
               _number("start", "Lock position",
                       "Travel the trigger is allowed before it stops",
                       20, 200, 60),
           )),
    # Not the same thing as the live mode-5 command, whose effect is unresolved
    # -- see `vibration()`. Stored type 5 is delivered as SyncWithGrip (82),
    # and these four knobs are 82's filter and scale, which is why Flydigi's
    # own "Vibration" panel edits the bind rather than any mode-5 parameter.
    Effect(MODE_VIBRATION, "vibration", "Vibration",
           "The game's own rumble, routed into the trigger", (
               _number("scale", "Intensity coefficient",
                       "How strongly the trigger follows the grip", 0, 200, 50),
               _number("block", "Vibration threshold",
                       "Rumble below this leaves the trigger still", 1, 255, 10),
               _number("stroke", "Travel range",
                       "How far into the pull the vibration lasts", 1, 200, 50),
               _number("frequency", "Vibration frequency",
                       "How fast it vibrates", 1, 255, 20),
           )),
)

BY_MODE = {effect.mode: effect for effect in EFFECTS}


def effect(mode):
    """The Effect for a stored mode byte, or General for anything unknown."""
    return BY_MODE.get(int(mode), BY_MODE[MODE_NORMAL])


def defaults(mode):
    return {p.key: p.default for p in effect(mode).params}


def _clamp(param, value):
    """A stored byte as the UI should see it.

    Out of range means the byte was never written for this effect -- a profile
    carries all ten parameter slots whatever the mode is, so switching to an
    effect for the first time reads whatever the previous one left there. That
    is not a value to show, so it becomes the default rather than being clipped
    into range: a frequency clipped up from 0 to 1 looks deliberate and is not.
    """
    value = int(value)
    if not param.minimum <= value <= param.maximum:
        return param.default
    return value


# Where each effect's knobs live in the profile's own storage. The 20-byte
# per-side block is `[0]=mode, [1]=bind type, [2]=bind filter, [3]=bind scale,
# [4..8]=bind params, [9]=mixed border, [10..19]=effect params`, and only the
# Vibration effect reaches into the bind half -- see `stored()`.
#
# Slot order per effect, from Flydigi's ControllerRepository.SaveTriggerAdapterConfig:
_SLOTS = {
    MODE_RACE: ("start", "resistance", None, None, None),
    MODE_SNIPER: ("start", "press", "strength", "frequency", "match_input"),
    MODE_RECOIL: ("start", "travel", "resistance", None, "match_input"),
    MODE_LOCK: ("start", None, None, None, None),
}

# Constants Flydigi writes into the slots an effect does not use. Lock's
# strength and Vibration's pair are fixed in their software too -- the effect
# has no control for them, so they are written rather than left over.
_FIXED = {
    MODE_LOCK: {1: 255, 2: 1},
    MODE_VIBRATION: {2: 1, 3: 90},
}


def values(mode, params, bind=None):
    """Named knob values for a stored effect, ready for a UI.

    `params` is the 10-byte parameter half of the block and `bind` the
    `(filter, scale, params)` triple from its first half, needed only by the
    Vibration effect.
    """
    mode = effect(mode).mode
    params = list(params) + [0] * max(0, 10 - len(params))
    if mode == MODE_VIBRATION:
        filt, scale, bind_params = bind if bind else (0, 0, [0] * 5)
        bind_params = list(bind_params) + [0] * max(0, 5 - len(bind_params))
        raw = {"scale": scale, "block": filt,
               "stroke": bind_params[0], "frequency": bind_params[3]}
    else:
        slots = _SLOTS.get(mode, ())
        raw = {key: params[slot] for slot, key in enumerate(slots) if key}
    return {p.key: _clamp(p, raw.get(p.key, p.default))
            for p in BY_MODE[mode].params}


def stored(mode, named):
    """`(params, bind)` to write for an effect, or `(None, None)` for General.

    General is left alone deliberately. It has no knobs, so there is nothing to
    write, and zeroing the slots would throw away the numbers someone tuned
    before switching the effect off.
    """
    mode = effect(mode).mode
    if mode == MODE_NORMAL:
        return None, None
    full = defaults(mode)
    full.update({k: v for k, v in named.items() if k in full})
    knobs = {p.key: max(p.minimum, min(p.maximum, int(full[p.key])))
             for p in BY_MODE[mode].params}

    params = [0] * 10
    for slot, value in _FIXED.get(mode, {}).items():
        params[slot] = value
    if mode == MODE_VIBRATION:
        params[0] = knobs["stroke"]
        params[1] = knobs["frequency"]
        # The live command Flydigi builds from this is SyncWithGrip, whose
        # pressure and strength are these two 1s -- the effect's own strength
        # is the scale, not a per-press level.
        bind = (knobs["block"], knobs["scale"],
                [knobs["stroke"], 1, 1, knobs["frequency"], 0])
        return params, bind
    for slot, key in enumerate(_SLOTS.get(mode, ())):
        if key:
            params[slot] = knobs[key]
    return params, None


def _apply(ctrl, cmd_id, payload):
    replies = ctrl.command(cmd_id, bytes(payload))
    return any(ctrl.ack_ok(r, cmd_id) for r in replies)


def normal(ctrl, side):
    """Clear any effect on a trigger. payload = applyFlag + [side, mode]."""
    return _apply(ctrl, CMD_SET_FORCE_TRIGGER, [1, side, MODE_NORMAL])


def clear_all(ctrl):
    return all(normal(ctrl, s) for s in (SIDE_LEFT, SIDE_RIGHT))


def race(ctrl, side, stroke, resistance, match_stroke=True):
    """Constant resistance past a travel point -- the racing throttle effect."""
    resistance = max(1, min(255, resistance))
    payload = [1, side, MODE_RACE, stroke, resistance, 1 if match_stroke else 0]
    return _apply(ctrl, CMD_SET_FORCE_TRIGGER, payload)


def _least_one(value):
    """The command builder's own clamp: 0 is refused, everything else stands.

    Flydigi's builders raise a 0 to 1 rather than reject it, so a caller that
    sends 0 gets the weakest setting instead of nothing at all.
    """
    return max(1, min(255, int(value)))


def sniper(ctrl, side, stroke, pressure, strength, frequency, match_stroke=True):
    """Vibrate once the trigger is held past a point under enough pressure."""
    payload = [1, side, MODE_SNIPER, stroke, _least_one(pressure),
               _least_one(strength), _least_one(frequency),
               1 if match_stroke else 0]
    return _apply(ctrl, CMD_SET_FORCE_TRIGGER, payload)


def recoil(ctrl, side, stroke, recoil_stroke, strength, match_stroke=True):
    """A band of resistance that gives way -- a weapon's break point.

    The zero before `match_stroke` is a slot the builder leaves empty; it is
    not a knob this effect has.
    """
    payload = [1, side, MODE_RECOIL, stroke, recoil_stroke,
               _least_one(strength), 0, 1 if match_stroke else 0]
    return _apply(ctrl, CMD_SET_FORCE_TRIGGER, payload)


def lock(ctrl, side, stroke, strength=255, match_stroke=True):
    """Stop the trigger dead at a travel point.

    `strength` is 255 in every call Flydigi's own software makes -- the effect
    is a stop, not a resistance -- but the packet carries it, so it is here.
    """
    payload = [1, side, MODE_LOCK, stroke, strength, 1 if match_stroke else 0]
    return _apply(ctrl, CMD_SET_FORCE_TRIGGER, payload)


def vibration(ctrl, side, stroke, pressure, strength, frequency,
              match_stroke=True):
    """Mode 5. **Dead code in Flydigi's stack; keep for completeness only.**

    Nothing constructs their mode-5 builder, the config path turns stored type
    5 into command 82, the DualSense relay emits only modes 0-3, and pads with
    real trigger motors use command 18 instead -- so no caller for this exists
    anywhere, theirs or ours. On hardware it buzzed once with the pad's own
    bind and did nothing with that bind suppressed; inconclusive, and not worth
    more bench time given that nothing depends on it. See PROTOCOL.md 3a.

    Want a trigger to vibrate? `sniper` (mode 2) is the one in real use -- the
    DualSense's own vibration effect maps to it. Want the game's rumble in the
    triggers? `bind_grip` (command 82).
    """
    payload = [1, side, MODE_VIBRATION, stroke, _least_one(pressure),
               _least_one(strength), _least_one(frequency),
               1 if match_stroke else 0]
    return _apply(ctrl, CMD_SET_FORCE_TRIGGER, payload)


def bind_grip(ctrl, side, bind_type, filt, scale, params):
    """SyncWithGrip (82): route the game's rumble into the trigger motors.

    payload = [side, bindType, filter, scale, stroke, pressure, strength, frequency]
    This is what the 33 'vibration' games use -- no game integration needed.
    """
    if len(params) < 4:
        raise ValueError("params needs 4 values: stroke, pressure, strength, frequency")
    payload = [side, bind_type, filt, scale] + list(params[:4])
    buf = device.build(CMD_SET_FORCE_TRIGGER_GRIP)
    buf[4] = 11
    buf[5 : 5 + len(payload)] = bytes(payload)
    replies = ctrl.send(buf)
    return any(ctrl.ack_ok(r, CMD_SET_FORCE_TRIGGER_GRIP) for r in replies)


def common_effect_payload(side, mode, params):
    """Build a SetForceTrigger (81) packet for a config-driven effect.

    Mirrors ForceTriggerControllerCommandNewXInput + ForceTriggerConfigCommon:
        [4]=10, [5]=applyFlag, [6]=side, [7]=mode, [8..]=params
    Used by both the Forza rule engine and the DSX listener, since both carry
    effects as an opaque (side, mode, params) triple.
    """
    values = [side, mode] + [int(p) for p in params]
    values += [0] * (7 - len(values))
    values = values[:7]
    # ForceTriggerConfigCommon quirk: mode==Race && p1==0 && p3==1 -> p3=0
    if values[1] == 1 and values[2] == 0 and values[4] == 1:
        values[4] = 0
    buf = device.build(CMD_SET_FORCE_TRIGGER)
    buf[4] = 10
    buf[5] = 1  # apply, not preview
    for i, value in enumerate(values):
        buf[6 + i] = max(0, min(255, int(value)))
    return buf


def rumble(ctrl, low, high, wait=0.1):
    """Drive the grip motors directly (SDL framing).

    `wait` is how long to collect the ACK for. Pass 0.0 when driving rumble
    continuously: waiting 100 ms per update makes the motors lag well behind
    whatever is driving them, and the ACK carries nothing we need.
    """
    buf = device.build(CMD_RUMBLE)
    buf[4] = 6
    buf[5] = low & 0xFF
    buf[6] = high & 0xFF
    return ctrl.send(buf, wait=wait)


def apply_game(ctrl, game):
    """Apply a game's Tier-1 vibration binding from its gamelist entry.

    Returns a list of (side_name, ok) tuples.
    """
    results = []
    sides = (
        ("left", SIDE_LEFT, "vibParams", "vibFilter", "pwmScal"),
        ("right", SIDE_RIGHT, "vibParamsRight", "vibFilterRight", "pwmScalRight"),
    )
    for name, side_id, pkey, fkey, skey in sides:
        raw = game.get(pkey) or game.get("vibParams") or ""
        if not raw:
            results.append((name, None))
            continue
        params = [int(x) for x in raw.split(",")]
        ok = bind_grip(
            ctrl,
            side_id,
            game.get("vibType") or 0,
            game.get(fkey) or 0,
            game.get(skey) or 0,
            params,
        )
        results.append((name, ok))
    return results
