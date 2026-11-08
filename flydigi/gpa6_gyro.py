# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Native XMUL gyro record, separate from the legacy motion wire format."""
from . import gpa6, mapping
from .gpa6_actions import BUTTONS

KEYS = {**BUTTONS, "lt": 22, "rt": 23}
_ALIASES = {"select": "back", "thl": "ls", "thr": "rs"}
DEFAULT = bytes([0, 0, 255, 255, 0, 0, 0, 0, 0, 0, 0,
                 0, 0, 0, 0, 0, 255, 0, 255, 0, 255, 1, 2, 32])


def key_id(value):
    """Names use the shared facade; numeric IDs here are native GPA6 IDs."""
    if value is None:
        return 255
    if isinstance(value, str):
        value = KEYS.get(_ALIASES.get(value, value))
    if type(value) is not int or value not in (*range(24), 255):
        raise ValueError("gyro key must be a controller button, lt, rt or None")
    return value


def target_id(value):
    if isinstance(value, str):
        value = mapping.MOTION_TARGET_IDS.get(value)
    if type(value) is not int or value not in mapping.MOTION_TARGETS:
        raise ValueError("gyro target must be off, left stick, right stick or mouse")
    return value


def activation_id(value):
    if isinstance(value, str):
        value = mapping.MOTION_ENABLE_TYPE_IDS.get(value)
    if type(value) is not int or value not in mapping.MOTION_ENABLE_TYPES:
        raise ValueError("gyro enable type must be hold or click")
    # The legacy facade's hold/click numbers are reversed on the native wire.
    return 0 if value == mapping.MOTION_PRESS else 1


def offsets(raw):
    """Recognize the current curve-first and older prefix-first 24-byte layouts.

    The vendor decoder tries these in this order. Keep the original ordering
    when editing. An unrecognized record is never rebuilt from guessed fields.
    """
    if len(raw) != 24:
        raise gpa6.ProtocolError("unsupported gyro extension length")
    for curve, flags in ((0, 11), (13, 0)):
        if (raw[curve + 4] <= 2 and raw[flags + 1] <= 3
                and raw[flags + 10] <= 3 and raw[flags + 11] <= 3
                and all(raw[p + 1] == 255 or (raw[p] <= 1 and raw[p + 1] <= 23)
                        for p in range(flags + 2, flags + 10, 2))):
            return curve, flags
    raise gpa6.ProtocolError("unsupported gyro record fields/layout")


def decode(raw):
    curve, flags = offsets(raw)
    names = {v: k for k, v in KEYS.items()}
    slots = [{"enable_type": mapping.MOTION_PRESS if raw[p] == 0 else mapping.MOTION_CLICK,
              "key": names[raw[p + 1]]} if raw[p + 1] != 255 else None
             for p in range(flags + 2, flags + 10, 2)]
    modes = {slot["enable_type"] for slot in slots if slot is not None}
    return {"target": raw[flags + 1],
            "enable_type": next(iter(modes)) if len(modes) == 1 else None,
            "keys": tuple(slot["key"] if slot else None for slot in slots[:2]),
            "activation_slots": slots,
            "invert_x": bool(raw[flags] & 1), "invert_y": bool(raw[flags] & 2),
            "axis_x": ("none", "imu_x", "imu_y", "imu_z")[raw[flags + 10]],
            "axis_y": ("none", "imu_x", "imu_y", "imu_z")[raw[flags + 11]],
            "mouse_window_radius": raw[flags + 12],
            "layout": "curve_first" if curve == 0 else "prefix_first"}
