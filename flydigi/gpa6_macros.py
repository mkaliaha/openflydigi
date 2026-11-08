# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""XMUL macro records and names, independent of the legacy macro wire format.

Steps share the recorder's delay-before-step and MACRO_PRESS/MACRO_RELEASE
conventions. Native targets cover keyboard, mouse, triggers and stick movement.
Playback modes are native strings: they are not the legacy numeric macro types.
"""
import struct

from . import gpa6, mapping
from .gpa6_actions import BUTTONS, decode_target, encode_target

MODES = ("press_once", "press_loop_immediate", "press_loop_wait", "press_hold")
# Web Space Station limits: steps are per definition, slots are per layer.
LIMITS = mapping.MacroLimits(slots=10, steps=32, tick_ms=1, interval_max=10000)
TARGET_TYPES = ("btn_gamepad", "btn_keyboard", "btn_mouse", "btn_consumer",
                "simulate_linear_left", "simulate_linear_right",
                "simulate_joystick_left", "simulate_joystick_right")
KEY_ALIASES = {"select": "back", "thl": "ls", "thr": "rs"}
RECORD_KEYS = {value: key for key, value in KEY_ALIASES.items()}


def integer(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 0..{maximum}")
    return value


def step_target(step):
    """Accept recorder keys or an explicit native target, never both."""
    if (not isinstance(step, dict) or set(step) - {"delay", "event", "key", "target"}
            or ("key" in step) == ("target" in step)):
        raise ValueError("a macro step requires delay, event and either key or target")
    event = step.get("event")
    if type(event) is not int or event not in (mapping.MACRO_PRESS, mapping.MACRO_RELEASE):
        raise ValueError("macro event must be MACRO_PRESS (1) or MACRO_RELEASE (0)")
    if "target" in step:
        return step["target"]
    key = step["key"]
    if not isinstance(key, str):
        raise ValueError("macro key must be a button name")
    if key in ("lt", "rt"):
        return {"type": "simulate_linear_left" if key == "lt" else "simulate_linear_right",
                "linear_value": 255 if event == mapping.MACRO_PRESS else 0}
    return KEY_ALIASES.get(key, key)


def decode_record(raw, name=None):
    macro_id, mode, interval, count, _ = struct.unpack_from("<BBHBB", raw)
    steps, last = [], 0
    for offset in range(6, 6 + count * 7, 7):
        action = raw[offset]
        when = struct.unpack_from("<H", raw, offset + 5)[0]
        # Unknown events remain inspectable, without aliasing legacy HOLD.
        event = (mapping.MACRO_PRESS if action == 0 else mapping.MACRO_RELEASE
                 if action == 1 else f"unknown:{action}")
        target = decode_target(raw[offset + 1:offset + 5])
        step = {"delay": when - last, "event": event}
        if target["type"] == "btn_gamepad" and target["id"] in BUTTONS:
            step["key"] = RECORD_KEYS.get(target["id"], target["id"])
        else:
            step["target"] = target
        steps.append(step)
        last = when
    return {"macro_id": macro_id, "type": MODES[mode] if mode < len(MODES) else mode,
            "interval": interval, "name": name, "steps": steps}


def encode_record(macro_id, steps, mode, interval, previous=None):
    integer(macro_id, 255, "macro ID")
    if mode not in MODES:
        raise ValueError("unsupported native macro playback mode")
    integer(interval, 65535, "macro interval")
    if not isinstance(steps, (list, tuple)) or len(steps) > LIMITS.steps:
        raise ValueError(f"a macro supports at most {LIMITS.steps} steps")
    record = bytearray(struct.pack("<BBHBB", macro_id, MODES.index(mode), interval,
                                   len(steps), previous[5] if previous else 0))
    when, down = 0, set()
    for index, step in enumerate(steps):
        target = step_target(step)
        when += integer(step.get("delay"), 65535, "macro step delay")
        integer(when, 65535, "macro total duration")
        offset = 6 + index * 7
        old = previous[offset + 1:offset + 5] if previous else None
        raw = encode_target(target, previous=old)
        decoded = decode_target(raw)
        kind = decoded["type"]
        if kind not in TARGET_TYPES:
            raise ValueError("target is not supported by the macro editor")
        stick = kind in ("simulate_joystick_left", "simulate_joystick_right")
        linear = kind in ("simulate_linear_left", "simulate_linear_right")
        pressed = (decoded["axis_x"] != 0 or decoded["axis_y"] != 0) if stick else step["event"] == mapping.MACRO_PRESS
        identity = kind if stick or linear else tuple(decoded.items())
        if mode == "press_hold" and (stick or not pressed):
            raise ValueError("press_hold requires press steps and does not support stick movement")
        if pressed and identity in down and not stick:
            raise ValueError("macro contains a repeated press without release")
        if not pressed and identity not in down:
            raise ValueError("macro contains a release before press")
        if pressed:
            down.add(identity)
        else:
            down.remove(identity)
        record.extend(bytes([0 if step["event"] == mapping.MACRO_PRESS else 1]) + raw + struct.pack("<H", when))
    if mode != "press_hold" and down:
        raise ValueError("macro is missing a release")
    return bytes(record)


def names(data, extensions):
    """Return the metadata TLV and raw name records, rejecting ambiguity."""
    found = [item for item in extensions if item[0] == 32]
    if len(found) > 1:
        raise gpa6.ProtocolError("duplicate XMUL name metadata")
    if not found:
        return None, b"", []
    _, start, end = found[0]
    raw = data[start + 2:end]
    if not raw or len(raw) < raw[0] + 2:
        raise gpa6.ProtocolError("truncated XMUL name metadata")
    profile_name = raw[1:1 + raw[0]]
    count, position = raw[1 + raw[0]], 2 + raw[0]
    records, seen = [], set()
    for _ in range(count):
        if position + 3 > len(raw) or position + 3 + raw[position + 2] > len(raw):
            raise gpa6.ProtocolError("truncated XMUL macro name")
        layer, macro_id, size = raw[position:position + 3]
        if (layer, macro_id) in seen:
            raise gpa6.ProtocolError("duplicate XMUL macro name")
        seen.add((layer, macro_id))
        records.append((layer, macro_id, raw[position + 3:position + 3 + size]))
        position += 3 + size
    if position != len(raw):
        raise gpa6.ProtocolError("unsupported trailing XMUL name metadata")
    return found[0], profile_name, records


def patch_name(data, extensions, layer, macro_id, name):
    """Edit one name without rebuilding unrelated names or the profile title."""
    if not isinstance(name, str):
        raise ValueError("macro name must be a string; empty clears it")
    if struct.unpack_from("<I", data, 4)[0] != 9:
        if name:
            raise ValueError("macro names require XMUL v1.9")
        return data
    entry, title, records = names(data, extensions)
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("macro name must be valid UTF-8") from exc
    if len(encoded) > 255:
        raise ValueError("macro name exceeds 255 UTF-8 bytes")
    result, replaced = [], False
    for old_layer, old_id, raw in records:
        if (old_layer, old_id) == (layer, macro_id):
            replaced = True
            if not encoded:
                continue
            raw = encoded
        result.append((old_layer, old_id, raw))
    if encoded and not replaced:
        result.append((layer, macro_id, encoded))
    if result == records:
        return data
    payload = bytes([len(title)]) + title + bytes([len(result)])
    payload += b"".join(bytes([a, b, len(raw)]) + raw for a, b, raw in result)
    if len(payload) > 255:
        raise ValueError("combined profile and macro names exceed the 255-byte metadata limit")
    replacement = bytes([32, len(payload)]) + payload
    if entry:
        _, start, end = entry
        return data[:start] + replacement + data[end:]
    # No tail is legal; create its terminator when adding metadata for the first time.
    if extensions:
        start = extensions[-1][1]
        return data[:start] + replacement + data[start:]
    return data + replacement + b"\xff\0"
