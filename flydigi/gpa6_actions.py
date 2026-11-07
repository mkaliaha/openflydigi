# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""GPA6 four-byte action targets shared by activators, macros and zones.

Names follow the native target model; gamepad IDs also accept our button names.
This is a wire codec, not a claim that every target works in every USB mode.
"""

BUTTONS = {name: i for i, name in enumerate((
    "a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs",
    "up", "down", "left", "right", "c", "z", "m1", "m2", "m3", "m4", "m5", "m6"))}
ACTIVATORS = ("simple", "timing_single", "timing_long", "timing_double",
              "pulse_press", "pulse_release", "toggle")

# Wire kind, then (field, minimum, maximum). All unused target bytes are reserved.
TARGETS = {
    "btn_gamepad": (0, (("id", 0, 255),)),
    "btn_keyboard": (1, (("key_id", 0, 255), ("modifier", 0, 255))),
    "btn_mouse": (2, (("mouse_id", 0, 4),)),
    "btn_consumer": (3, (("consumer_id", 0, 255),)),
    "turbo_gamepad": (10, (("button_id", 0, 255), ("turbo_interval_10ms", 1, 65535))),
    "layer_hold": (20, (("layer_id", 0, 3),)),
    "layer_toggle": (21, (("layer_id", 0, 3),)),
    "macro": (30, (("macro_id", 0, 255),)),
    "simulate_mouse_move": (40, (("delta_x", -128, 127), ("delta_y", -128, 127))),
    "simulate_mouse_wheel": (41, (("delta_wheel", -128, 127),)),
    "simulate_joystick_left": (42, (("axis_x", -128, 127), ("axis_y", -128, 127))),
    "simulate_joystick_right": (43, (("axis_x", -128, 127), ("axis_y", -128, 127))),
    "simulate_linear_left": (44, (("linear_value", 0, 255),)),
    "simulate_linear_right": (45, (("linear_value", 0, 255),)),
    "simulate_moto": (46, (("motor_value_left", 0, 255), ("motor_value_right", 0, 255))),
}


def encode_target(target, *, previous=None):
    """Encode a typed target or gamepad-name shorthand; retain reserved bytes.

    ``none`` is an explicit disabled gamepad target, unlike removing an override.
    Previous bytes only contribute reserved fields when their target kind matches.
    """
    if isinstance(target, str):
        target = {"type": "btn_gamepad", "id": target}
    if not isinstance(target, dict) or not isinstance(target.get("type"), str) or target["type"] not in TARGETS:
        raise ValueError("unsupported action target type")
    kind, fields = TARGETS[target["type"]]
    if set(target) - {"type", *(field[0] for field in fields)}:
        raise ValueError("unknown action target field")
    raw = bytearray(previous) if previous is not None and len(previous) == 4 and previous[0] == kind else bytearray(4)
    raw[0] = kind
    for index, (name, minimum, maximum) in enumerate(fields, 1):
        value = target.get(name, 0 if name == "modifier" else None)
        if name in ("id", "button_id"):
            if isinstance(value, str):
                value = 255 if value == "none" else BUTTONS.get(value)
            if type(value) is not int or value not in (*BUTTONS.values(), 255):
                raise ValueError("gamepad action requires a button name/ID or none")
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
        raw[index] = value & 255
        if name == "turbo_interval_10ms":
            raw[3] = value >> 8
    return bytes(raw)


def decode_target(raw):
    """Describe known targets; retain unknown targets as read-only raw bytes."""
    if len(raw) != 4:
        raise ValueError("an action target must contain four bytes")
    found = next(((name, fields) for name, (kind, fields) in TARGETS.items() if kind == raw[0]), None)
    if found is None:
        return {"type": "unknown", "hex": bytes(raw).hex()}
    name, fields = found
    result = {"type": name}
    names = {value: key for key, value in BUTTONS.items()}
    for index, (field, minimum, _) in enumerate(fields, 1):
        value = raw[index]
        if field == "turbo_interval_10ms":
            value |= raw[3] << 8
        elif minimum < 0 and value >= 128:
            value -= 256
        elif field in ("id", "button_id"):
            value = "none" if value == 255 else names.get(value, value)
        result[field] = value
    return result
