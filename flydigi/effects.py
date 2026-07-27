"""Trigger effect commands for the Apex 5.

Two families exist (see PROTOCOL.md):
  * SetForceTrigger (81/82) -- effect based. Used by everything so far.
  * K6Trigger (83/85/87)    -- waveform/realtime. Untested on hardware.
"""
from . import device
from .device import (
    CMD_RUMBLE,
    CMD_SET_FORCE_TRIGGER,
    CMD_SET_FORCE_TRIGGER_GRIP,
    SIDE_LEFT,
    SIDE_RIGHT,
)

# SetForceTrigger effect modes (params[1]). Only Normal and Race are confirmed.
MODE_NORMAL = 0
MODE_RACE = 1


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
