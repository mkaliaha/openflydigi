# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared native output fields for button actions, analog zones and macro steps."""
from copy import deepcopy

from flydigi import gpa6_actions as actions

BUTTON_LABELS = {
    "a": "A", "b": "B", "x": "X", "y": "Y", "lb": "Left bumper",
    "rb": "Right bumper", "back": "Select", "start": "Start",
    "ls": "Left stick click", "rs": "Right stick click", "up": "Up",
    "down": "Down", "left": "Left", "right": "Right", "c": "C", "z": "Z",
    "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4", "m5": "LM", "m6": "RM",
    "none": "Disabled",
}
TARGET_LABELS = ["Gamepad button", "Keyboard key", "Mouse button", "Consumer key",
                 "Turbo button", "Hold Shift layer", "Toggle Shift layer", "Macro",
                 "Move mouse", "Mouse wheel", "Left stick", "Right stick",
                 "Left trigger", "Right trigger", "Vibration"]
FIELD_LABELS = {
    "id": "Button", "button_id": "Button", "key_id": "Keyboard usage ID",
    "modifier": "Modifier mask", "mouse_id": "Mouse button ID",
    "consumer_id": "Consumer key ID", "turbo_interval_10ms": "Repeat interval (ms)",
    "layer_id": "Layer", "macro_id": "Macro", "delta_x": "Horizontal movement",
    "delta_y": "Vertical movement", "delta_wheel": "Wheel movement",
    "axis_x": "Horizontal position", "axis_y": "Vertical position",
    "linear_value": "Trigger position", "motor_value_left": "Left vibration",
    "motor_value_right": "Right vibration",
}


class TargetFields:
    """Output editing shared by independent Qt models.

    Keep Qt signals/properties on the owning model: inherited notify signals
    in PySide's generated metaobjects are not safe on the supported Qt builds.
    """
    def __init__(self, editor):
        self._editor = editor

    @property
    def _layer(self):
        return self._editor._layer

    @property
    def editable(self):
        return self._editor.editable

    @property
    def changed(self):
        return self._editor.changed

    @property
    def _error(self):
        return self._editor._error

    @_error.setter
    def _error(self, value):
        self._editor._error = value

    def _entry(self):
        return self._editor._entry()

    def _read(self):
        return self._editor._read()

    def _change_entry(self, **changes):
        self._editor._change_entry(**changes)

    @property
    def targetNames(self):
        return TARGET_LABELS

    @property
    def target(self):
        entry = self._entry()
        name = entry["target"]["type"] if entry else None
        return list(actions.TARGETS).index(name) if name in actions.TARGETS else -1

    @target.setter
    def target(self, value):
        if value not in range(len(actions.TARGETS)) or value == self.target:
            return
        name = list(actions.TARGETS)[value]
        target = dict(type=name)
        for field, low, _ in actions.TARGETS[name][1]:
            target[field] = max(0, low)
        if name in ("layer_hold", "layer_toggle"):
            target["layer_id"] = next((i for i in self._read()["layers"] if i != self._layer), self._layer)
        if name == "macro":
            macros = self._read()["macros"]
            if not macros:
                self._error = "This layer has no macros to bind."
                self.changed.emit()
                return
            target["macro_id"] = macros[0]["macro_id"]
        self._change_entry(target=actions.decode_target(actions.encode_target(target)))

    def field(self, index):
        entry = self._entry()
        fields = actions.TARGETS.get(entry["target"]["type"], (0, ()))[1] if entry else ()
        if index not in range(len(fields)):
            return dict(visible=False, label="", minimum=0, maximum=0, value=0, step=1, choices=[], selected=-1)
        name, low, high = fields[index]
        value = entry["target"][name]
        choices = self._choices(name)
        values = [v for v, _ in choices]
        scale = 10 if name == "turbo_interval_10ms" else 1
        return dict(visible=True, label=FIELD_LABELS[name], minimum=low * scale, maximum=high * scale,
                    value=value * scale if type(value) is int else 0, step=scale,
                    choices=[label for _, label in choices], selected=values.index(value) if value in values else -1)

    @property
    def fields(self):
        return [self.field(0), self.field(1)]

    def _choices(self, field):
        if field in ("id", "button_id"):
            return [(key, BUTTON_LABELS[key]) for key in (*actions.BUTTONS, "none")]
        if field == "layer_id":
            return [(i, "Base" if i == 0 else f"Shift {i}") for i in self._read()["layers"]]
        if field == "macro_id":
            return [(m["macro_id"], m["name"] or f"Macro {m['macro_id']}") for m in self._read()["macros"]]
        return []

    def setField(self, index, value):
        entry = self._entry()
        if not self.editable or entry is None:
            return
        target = deepcopy(entry["target"])
        fields = actions.TARGETS[target["type"]][1]
        if index not in range(len(fields)):
            return
        field = fields[index][0]
        if field == "turbo_interval_10ms":
            if value % 10:
                self._error = "Turbo intervals must be a multiple of 10 ms."
                self.changed.emit()
                return
            value //= 10
        choices = self._choices(field)
        if choices:
            if value not in range(len(choices)):
                return
            value = choices[value][0]
        target[field] = value
        self._change_entry(target=target)
