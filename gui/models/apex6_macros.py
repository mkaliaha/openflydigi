# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native macro definitions, with a draft for editing whole valid timelines."""
from copy import deepcopy

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import gpa6, gpa6_actions as actions, gpa6_config, gpa6_macros as macros
from .apex6_target import TargetFields, BUTTON_LABELS, TARGET_LABELS, FIELD_LABELS

QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

MODE_NAMES = ["Once on press", "Loop while held · stop immediately",
              "Loop while held · finish the sequence", "Hold outputs while pressed"]


@QmlElement
class Apex6MacrosModel(QObject):
    changed = Signal()

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._targets = TargetFields(self)
        self._layer = 0
        self._id = -1
        self._step = 0
        self._draft = self._baseline = None
        self._error = self._validation = ""
        self._cache_key = None
        self._cache = {}

    def _config(self):
        package = self._profile.nativeConfig
        return package.mapping if package is not None else None

    def _read(self):
        key = (self._profile.generation, self._layer)
        if key != self._cache_key:
            config = self._config()
            result = dict(layers=[], macros=[], issue="", names=False)
            if config is not None:
                try:
                    result["layers"] = config.layer_ids()
                    result["names"] = gpa6_config.mapping_header(config.blob)["version"] == "1.9"
                    if self._layer in result["layers"]:
                        result["macros"] = config.macros(layer=self._layer)
                except (ValueError, gpa6.ProtocolError) as exc:
                    result["issue"] = str(exc)
            self._cache, self._cache_key = result, key
        return self._cache

    def _selected(self):
        return next((m for m in self._read()["macros"] if m["macro_id"] == self._id), None)

    def _sync(self):
        entries = self._read()["macros"]
        if self._selected() is None:
            self._id = entries[0]["macro_id"] if entries else -1
        self._baseline = deepcopy(self._selected())
        self._draft = deepcopy(self._baseline)
        self._step = max(0, min(self._step, self.stepCount - 1))

    def refresh(self):
        self._cache_key = None
        if not self.draftDirty:
            self._sync()
        self._notify()

    def clear(self):
        self._layer, self._id, self._step = 0, -1, 0
        self._draft = self._baseline = None
        self._error = ""
        self.refresh()

    def _candidate(self):
        config = self._config()
        if config is None or self._draft is None:
            raise ValueError("Select a macro first.")
        if self._read()["issue"]:
            raise ValueError(self._read()["issue"])
        if self._selected() != self._baseline:
            raise ValueError("This definition changed. Discard the draft and reopen it before editing.")
        changes = {k: v for k, v in self._draft.items()
                   if k != "macro_id" and v != self._baseline[k]}
        if "type" in changes:
            changes["macro_type"] = changes.pop("type")
        if "name" in changes:
            changes["name"] = changes["name"] or ""
        candidate = config.copy()
        candidate.set_macro(self._id, layer=self._layer, **changes)
        return candidate

    def _notify(self):
        self._validation = ""
        if self.draftDirty:
            try:
                self._candidate()
            except (ValueError, KeyError, gpa6.ProtocolError) as exc:
                self._validation = str(exc)
        self.changed.emit()

    def _changed_draft(self):
        self._error = ""
        self._notify()
        self._profile.markChanged()

    def _may_leave(self):
        if self.draftDirty:
            self._error = "Update this macro or discard its draft before selecting another definition or layer."
            self.changed.emit()
            return False
        return True

    def _edit(self, operation):
        config = self._config()
        if config is None or not self._may_leave():
            return
        try:
            candidate = config.copy()
            operation(candidate)
        except (ValueError, KeyError, gpa6.ProtocolError) as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._error = ""
        self._profile.setNativeMapping(candidate)

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def readIssue(self):
        return self._read()["issue"]

    @Property(str, notify=changed)
    def validation(self):
        return self._validation

    @Property(bool, notify=changed)
    def draftDirty(self):
        return self._draft != self._baseline

    @Property(bool, notify=changed)
    def canCommit(self):
        return self.draftDirty and not self._validation

    @Slot()
    def commit(self):
        if not self.draftDirty:
            return
        try:
            candidate = self._candidate()
        except (ValueError, KeyError, gpa6.ProtocolError) as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._draft = self._baseline = None
        self._error = ""
        self._profile.setNativeMapping(candidate)

    @Slot()
    def discard(self):
        self._sync()
        self._changed_draft()

    @Property("QStringList", notify=changed)
    def layerNames(self):
        return [("Base" if i == 0 else f"Shift {i}") +
                ("" if i in self._read()["layers"] else " (not created)") for i in range(4)]

    @Property(int, notify=changed)
    def layer(self):
        return self._layer

    @layer.setter
    def layer(self, value):
        if value in range(4) and value != self._layer and self._may_leave():
            self._layer, self._id, self._step, self._error = value, -1, 0, ""
            self.refresh()

    @Property(bool, notify=changed)
    def layerExists(self):
        return self._layer in self._read()["layers"]

    @Slot()
    def createLayer(self):
        self._edit(lambda c: c.ensure_layer(self._layer))

    @Property("QStringList", notify=changed)
    def macroNames(self):
        return [f"{m['macro_id']} · {m['name'] or 'Unnamed macro'}" for m in self._read()["macros"]]

    @Property(int, notify=changed)
    def current(self):
        return next((i for i, m in enumerate(self._read()["macros"]) if m["macro_id"] == self._id), -1)

    @current.setter
    def current(self, value):
        entries = self._read()["macros"]
        if value in range(len(entries)) and value != self.current and self._may_leave():
            self._id, self._step, self._error = entries[value]["macro_id"], 0, ""
            self._sync()
            self._notify()

    @Property(int, notify=changed)
    def macroId(self):
        return self._id

    @Property(int, notify=changed)
    def nextId(self):
        used = {m["macro_id"] for m in self._read()["macros"]}
        return next((i for i in range(256) if i not in used), -1)

    @Property(bool, notify=changed)
    def canAdd(self):
        return (self.layerExists and not self.readIssue and not self.draftDirty
                and len(self._read()["macros"]) < macros.LIMITS.slots)

    @Slot(int)
    def create(self, macro_id):
        if not self.canAdd:
            return
        def update(config):
            if config.macro(macro_id, layer=self._layer) is not None:
                raise ValueError("That macro ID is already used in this layer.")
            config.set_macro(macro_id, steps=[dict(delay=0, event=1, key="a"),
                                              dict(delay=50, event=0, key="a")], layer=self._layer)
            self._id, self._step = macro_id, 0
        self._edit(update)

    @Slot(int)
    def duplicate(self, macro_id):
        if not self.canAdd or self._draft is None or not self.editable:
            return
        source = deepcopy(self._draft)
        def update(config):
            if config.macro(macro_id, layer=self._layer) is not None:
                raise ValueError("That macro ID is already used in this layer.")
            config.set_macro(macro_id, source["steps"], source["type"], source["interval"],
                             source["name"], layer=self._layer)
            self._id, self._step = macro_id, 0
        self._edit(update)

    @Slot()
    def remove(self):
        if self._selected() is not None:
            self._edit(lambda c: c.clear_macro(self._id, layer=self._layer))

    @Property(bool, notify=changed)
    def namesSupported(self):
        return self._read()["names"]

    @Property(str, notify=changed)
    def name(self):
        return (self._draft["name"] or "") if self._draft else ""

    @name.setter
    def name(self, value):
        if self._draft is not None and self.namesSupported and value != self.name:
            self._draft["name"] = value or None
            self._changed_draft()

    @Property("QStringList", constant=True)
    def modeNames(self):
        return MODE_NAMES

    @Property(int, notify=changed)
    def mode(self):
        value = self._draft["type"] if self._draft else None
        return macros.MODES.index(value) if value in macros.MODES else -1

    @mode.setter
    def mode(self, value):
        if self.editable and value in range(len(macros.MODES)):
            self._draft["type"] = macros.MODES[value]
            self._changed_draft()

    @Property(int, notify=changed)
    def interval(self):
        return self._draft["interval"] if self._draft else 0

    @interval.setter
    def interval(self, value):
        if self._draft is not None:
            self._draft["interval"] = value
            self._changed_draft()

    @Property(bool, notify=changed)
    def editable(self):
        # Check support, not timeline balance: an intermediate draft can have
        # unmatched presses while its partner step is being edited.
        if self._baseline is None or self._baseline["type"] not in macros.MODES:
            return False
        try:
            for step in self._baseline["steps"]:
                target = actions.decode_target(actions.encode_target(macros.step_target(step)))
                if target["type"] not in macros.TARGET_TYPES:
                    return False
                macros.integer(step["delay"], 65535, "delay")
            return len(self._baseline["steps"]) <= macros.LIMITS.steps
        except ValueError:
            return False

    @Property(str, notify=changed)
    def storedMode(self):
        return str(self._draft["type"]) if self._draft else ""

    @Property("QStringList", notify=changed)
    def stepNames(self):
        result = []
        for i, step in enumerate(self._draft["steps"] if self._draft else []):
            event = {1: "Press", 0: "Release"}.get(step["event"], str(step["event"]))
            if "key" in step:
                target = BUTTON_LABELS.get(macros.KEY_ALIASES.get(step["key"], step["key"]), step["key"])
            else:
                value = step["target"]
                kind = value["type"]
                label = (TARGET_LABELS[list(actions.TARGETS).index(kind)]
                         if kind in actions.TARGETS else "Unknown output")
                fields = ", ".join(f"{FIELD_LABELS.get(k, k)}: {BUTTON_LABELS.get(v, v)}"
                                   for k, v in value.items() if k != "type")
                target = f"{label} ({fields})"
            result.append(f"{i + 1}. +{step['delay']} ms · {event} · {target}")
        return result

    @Property(int, notify=changed)
    def stepCount(self):
        return len(self._draft["steps"]) if self._draft else 0

    @Property(int, notify=changed)
    def duration(self):
        return sum(s["delay"] for s in self._draft["steps"]) if self._draft else 0

    @Property(int, notify=changed)
    def step(self):
        return self._step

    @step.setter
    def step(self, value):
        if value in range(self.stepCount):
            self._step = value
            self.changed.emit()

    def _entry(self):
        if self._draft is None or self._step not in range(self.stepCount):
            return None
        step = self._draft["steps"][self._step]
        target = step.get("target")
        if target is None:
            target = actions.decode_target(actions.encode_target(
                macros.KEY_ALIASES.get(step["key"], step["key"])))
        return dict(step, target=target)

    def _change_entry(self, **changes):
        if not self.editable or self._entry() is None:
            return
        step = self._draft["steps"][self._step]
        if "target" in changes:
            step.pop("key", None)
        step.update(changes)
        self._changed_draft()

    @Property(int, notify=changed)
    def delay(self):
        entry = self._entry()
        return entry["delay"] if entry else 0

    @delay.setter
    def delay(self, value):
        self._change_entry(delay=value)

    @Property(int, notify=changed)
    def event(self):
        entry = self._entry()
        return {1: 0, 0: 1}.get(entry["event"], -1) if entry else -1

    @event.setter
    def event(self, value):
        if value in (0, 1):
            self._change_entry(event=1 - value)

    @Property("QStringList", constant=True)
    def targetNames(self):
        return [TARGET_LABELS[list(actions.TARGETS).index(t)] for t in macros.TARGET_TYPES]

    @Property(int, notify=changed)
    def target(self):
        entry = self._entry()
        kind = entry["target"]["type"] if entry else None
        return macros.TARGET_TYPES.index(kind) if kind in macros.TARGET_TYPES else -1

    @target.setter
    def target(self, value):
        if value in range(len(macros.TARGET_TYPES)):
            self._targets.target = list(actions.TARGETS).index(macros.TARGET_TYPES[value])

    @Property("QVariantList", notify=changed)
    def fields(self):
        return self._targets.fields

    @Slot(int, int)
    def setField(self, index, value):
        self._targets.setField(index, value)

    @Slot()
    def addStep(self):
        if self.editable and self.stepCount < macros.LIMITS.steps:
            self._draft["steps"].append(dict(delay=0, event=1, key="a"))
            self._step = self.stepCount - 1
            self._changed_draft()

    @Slot()
    def removeStep(self):
        if self.editable and self._step in range(self.stepCount):
            del self._draft["steps"][self._step]
            self._step = max(0, min(self._step, self.stepCount - 1))
            self._changed_draft()

    @Slot(int)
    def moveStep(self, delta):
        other = self._step + delta
        if self.editable and delta in (-1, 1) and other in range(self.stepCount):
            steps = self._draft["steps"]
            steps[self._step], steps[other] = steps[other], steps[self._step]
            self._step = other
            self._changed_draft()
