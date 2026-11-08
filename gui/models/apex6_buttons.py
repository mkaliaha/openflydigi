# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native button/Shift editor; each edit replaces only one mapping entry."""
from copy import deepcopy

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import gpa6, gpa6_actions as actions, gpa6_config, gpa6_profile

QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

BUTTON_LABELS = {
    "a": "A", "b": "B", "x": "X", "y": "Y", "lb": "Left bumper",
    "rb": "Right bumper", "back": "Select", "start": "Start",
    "ls": "Left stick click", "rs": "Right stick click", "up": "Up",
    "down": "Down", "left": "Left", "right": "Right", "c": "C", "z": "Z",
    "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4", "m5": "LM", "m6": "RM",
    "none": "Disabled",
}
# The native button table has no Home or LT/RT sources. C/Z are retained by the
# codec but are not physical Apex buttons. They remain available as targets.
SOURCES = [key for key in actions.BUTTONS if key not in ("c", "z")]
ACTIVATOR_LABELS = ["While held", "Single press", "Long press", "Double press",
                    "On press", "On release", "Toggle"]
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


@QmlElement
class Apex6ButtonsModel(QObject):
    changed = Signal()

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._layer = 0
        self._source = 0
        self._action = 0
        self._error = ""
        self._cache_generation = -1
        self._cache = {}

    def _config(self):
        package = self._profile.nativeConfig
        return package.mapping if package is not None else None

    def _read(self):
        config = self._config()
        if config is None:
            return dict(layers=[], activators=None, macros=[], timing={})
        key = (self._profile.generation, self._layer, self._source)
        if key != self._cache_generation:
            layers = config.layer_ids()
            try:
                macros = config.macros(layer=self._layer) if self._layer in layers else []
            except gpa6.ProtocolError:
                macros = []  # Opaque macro bodies do not block button editing.
            self._cache = dict(
                layers=layers,
                activators=config.activators(SOURCES[self._source], layer=self._layer)
                    if self._layer in layers else None,
                macros=macros,
                timing=gpa6_config.mapping_header(config.blob))
            self._cache_generation = key
        return self._cache

    def _edit(self, operation):
        config = self._config()
        if config is None:
            return
        try:
            candidate = config.copy()
            operation(candidate)
        except (ValueError, KeyError, gpa6.ProtocolError) as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._profile.setNativeMapping(candidate)
        self.refresh()

    def refresh(self):
        self._cache_generation = -1
        self._action = max(0, min(self._action, len(self._read()["activators"] or []) - 1))
        self._error = ""
        self.changed.emit()

    def clear(self):
        self._layer = self._source = self._action = 0
        self.refresh()

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property("QStringList", notify=changed)
    def layerNames(self):
        return [("Base" if i == 0 else f"Shift {i}") +
                ("" if i in self._read()["layers"] else " (not created)") for i in range(4)]

    @Property(int, notify=changed)
    def layer(self):
        return self._layer

    @layer.setter
    def layer(self, value):
        if value in range(4):
            self._layer, self._action, self._error = value, 0, ""
            self.changed.emit()

    @Property(bool, notify=changed)
    def layerExists(self):
        return self._layer in self._read()["layers"]

    @Slot()
    def createLayer(self):
        self._edit(lambda c: c.ensure_layer(self._layer))

    @Property("QStringList", constant=True)
    def sourceNames(self):
        return [BUTTON_LABELS[key] for key in SOURCES]

    @Property(int, notify=changed)
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        if value in range(len(SOURCES)):
            self._source, self._action, self._error = value, 0, ""
            self.changed.emit()

    @Property(str, notify=changed)
    def bindingState(self):
        current = self._read()["activators"]
        if not self.layerExists:
            return "Create this layer before adding mappings."
        if current is None:
            return "No override in this layer."
        if not current:
            return "Empty override: this button has no actions in this layer."
        return f"{len(current)} of 5 actions used."

    def _editable(self, entry):
        if entry["type"] not in actions.ACTIVATORS:
            return False
        try:
            actions.encode_target(entry["target"])
            return True
        except ValueError:
            return False

    @Property(bool, notify=changed)
    def editable(self):
        return self.layerExists and all(self._editable(a) for a in self._read()["activators"] or [])

    @Property("QStringList", notify=changed)
    def actionNames(self):
        result = []
        for i, entry in enumerate(self._read()["activators"] or []):
            kind = entry["type"]
            label = ACTIVATOR_LABELS[actions.ACTIVATORS.index(kind)] if kind in actions.ACTIVATORS else f"Unknown press {kind}"
            target = entry["target"]
            name = target["type"]
            if name == "btn_gamepad":
                name = BUTTON_LABELS.get(target["id"], str(target["id"]))
            elif name in actions.TARGETS:
                name = TARGET_LABELS[list(actions.TARGETS).index(name)]
            else:
                name = "Unknown target " + target.get("hex", "")
            result.append(f"{i + 1}. {label} → {name}")
        return result

    @Property(int, notify=changed)
    def action(self):
        return self._action

    @action.setter
    def action(self, value):
        if value in range(len(self._read()["activators"] or [])):
            self._action = value
            self.changed.emit()

    def _entry(self):
        entries = self._read()["activators"] or []
        return entries[self._action] if self._action in range(len(entries)) else None

    def _set_entries(self, entries):
        self._edit(lambda c: c.set_activators(SOURCES[self._source], entries, layer=self._layer))

    @Slot()
    def addAction(self):
        entries = deepcopy(self._read()["activators"] or [])
        if not self.editable or len(entries) >= 5:
            return
        entries.append({"type": "simple", "target": {"type": "btn_gamepad", "id": "a"}})
        self._action = len(entries) - 1
        self._set_entries(entries)

    @Slot()
    def removeAction(self):
        if self.editable and self._entry() is not None:
            entries = deepcopy(self._read()["activators"])
            del entries[self._action]
            self._set_entries(entries)

    @Slot()
    def resetButton(self):
        if self.layerExists:
            self._set_entries(None)

    def _change_entry(self, **changes):
        if self.editable and self._entry() is not None:
            entries = deepcopy(self._read()["activators"])
            entries[self._action].update(changes)
            self._set_entries(entries)

    @Property("QStringList", constant=True)
    def activatorNames(self):
        return ACTIVATOR_LABELS

    @Property(int, notify=changed)
    def activator(self):
        entry = self._entry()
        kind = entry["type"] if entry else None
        return actions.ACTIVATORS.index(kind) if kind in actions.ACTIVATORS else -1

    @activator.setter
    def activator(self, value):
        if value in range(len(actions.ACTIVATORS)):
            self._change_entry(type=actions.ACTIVATORS[value])

    @Property("QStringList", constant=True)
    def targetNames(self):
        return TARGET_LABELS

    @Property(int, notify=changed)
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

    @Slot(int, result="QVariantMap")
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

    @Property("QVariantList", notify=changed)
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

    @Slot(int, int)
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

    @Property(int, notify=changed)
    def longPress(self):
        return self._read()["timing"].get("long_press_ms", 1)

    @longPress.setter
    def longPress(self, value):
        self._set_timing(long_ms=value)

    @Property(int, notify=changed)
    def doublePress(self):
        return self._read()["timing"].get("double_press_ms", 1)

    @doublePress.setter
    def doublePress(self, value):
        self._set_timing(double_ms=value)

    def _set_timing(self, **values):
        def update(config):
            config.blob[:] = gpa6_profile.timing(config.blob, **values)
        self._edit(update)
