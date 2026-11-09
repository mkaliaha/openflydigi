# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native feedback controls over the shared, staged profile package."""
from PySide6.QtCore import QObject, Property, Signal
from PySide6.QtQml import QmlElement

from flydigi import gpa6, gpa6_haptics

QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1
SCENES = list(gpa6_haptics.SCENES)
SCENE_NAMES = ["Normal (no scene)"] + [name.replace("-", " ").capitalize() for name in SCENES]


@QmlElement
class Apex6FeedbackModel(QObject):
    changed = Signal()

    def __init__(self, owner, target):
        super().__init__(owner)
        self._owner, self._target = owner, target

    def _read(self):
        return self._owner.state(self._target)

    @Property(bool, constant=True)
    def isGrip(self):
        return self._target == "grips"

    @Property(str, notify=changed)
    def issue(self):
        return self._read().get("issue", "")

    @Property(bool, notify=changed)
    def editable(self):
        state = self._read()
        return bool(state) and not state.get("issue")

    @Property(bool, notify=changed)
    def enabled(self):
        return self._read().get("vibration", {}).get("enabled", False)

    @enabled.setter
    def enabled(self, value):
        if self.editable:
            self._owner.edit(lambda c: c.set_vibration(self._target, enabled=value))

    @Property(int, notify=changed)
    def level(self):
        return self._read().get("vibration", {}).get("level", 1)

    @level.setter
    def level(self, value):
        if self.editable:
            self._owner.edit(lambda c: c.set_vibration(self._target, level=value))

    @Property(bool, notify=changed)
    def strengthZero(self):
        curve = self._read().get("vibration", {}).get("curve")
        return bool(curve) and not any(y for _, y in (
            curve["start"], curve["end"], *curve["points"]))

    @Property("QStringList", constant=True)
    def sceneNames(self):
        return SCENE_NAMES

    @Property(int, notify=changed)
    def scene(self):
        current = self._read().get("scene", {})
        name = current.get("scene")
        return SCENES.index(name) + 1 if name in SCENES else (
            0 if current.get("mode") in ("normal", "vibration") else -1)

    @scene.setter
    def scene(self, value):
        if not self.isGrip and self.editable and value in range(len(SCENE_NAMES)):
            self._owner.edit(lambda c: c.set_trigger_scene(
                self._target, "normal" if value == 0 else SCENES[value - 1]))

    @Property(str, notify=changed)
    def modeText(self):
        current = self._read().get("scene", {})
        mode = current.get("mode")
        if mode == "scene":
            return SCENE_NAMES[self.scene]
        return {"normal": "Normal", "vibration": "Trigger-bound feedback",
                "custom": "Custom scene", "unknown": "Unknown motor mode"}.get(mode, "")

    @Property(bool, notify=changed)
    def hasFrequency(self):
        name = self._read().get("scene", {}).get("scene")
        return name in SCENES and bool(gpa6_haptics.SCENES[name][3])

    @Property(bool, notify=changed)
    def adjustable(self):
        current = self._read().get("scene", {})
        return (self.editable and current.get("scene") in SCENES
                and current.get("travel") is not None
                and (not self.hasFrequency or current.get("frequency") is not None))

    @Property(int, notify=changed)
    def travel(self):
        return self._read().get("scene", {}).get("travel") or 1

    @travel.setter
    def travel(self, value):
        if self.adjustable:
            self._owner.edit(lambda c: c.set_trigger_scene(self._target, travel=value))

    @Property(int, notify=changed)
    def frequency(self):
        return self._read().get("scene", {}).get("frequency") or 1

    @frequency.setter
    def frequency(self, value):
        if self.adjustable and self.hasFrequency:
            self._owner.edit(lambda c: c.set_trigger_scene(self._target, frequency=value))


@QmlElement
class Apex6HapticsModel(QObject):
    changed = Signal()

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._error = ""
        self._cache_key = None
        self._cache = {}
        self._sides = {target: Apex6FeedbackModel(self, target)
                       for target in ("grips", "left", "right")}

    def _config(self):
        package = self._profile.nativeConfig
        if package is None:
            return None
        features = package.source["features_raw"]
        return gpa6_haptics.HapticConfig(
            package.mapping.blob, package.motors.blob, cfg_id=package.cfg_id,
            haptic_grip=bool(features & (1 << gpa6.FEATURES.index("haptic_grip"))),
            trigger_haptic=bool(features & (1 << gpa6.FEATURES.index("trigger_haptic"))))

    def state(self, target):
        key = self._profile.generation
        if key != self._cache_key:
            config = self._config()
            self._cache = {}
            if config is not None:
                for name in self._sides:
                    state = self._cache[name] = {}
                    try:
                        state["vibration"] = config.vibration(name)
                        if name != "grips":
                            state["scene"] = config.trigger_scene(name)
                            entry = config.motors.trigger(name)
                            if not config.trigger_haptic:
                                raise gpa6.ProtocolError("This controller does not report trigger haptics.")
                            if (entry["mode"] not in (0, 1, 2) or entry["record"] !=
                                    bytes([entry["mode"]]) + entry["params"].ljust(10, b"\0")):
                                raise gpa6.ProtocolError("The stored motor settings cannot be edited or restored exactly. They are kept unchanged.")
                    except (ValueError, gpa6.ProtocolError) as exc:
                        state["issue"] = str(exc)
            self._cache_key = key
        return self._cache.get(target, {})

    def edit(self, operation):
        config = self._config()
        if config is None:
            return
        try:
            operation(config)
        except (ValueError, gpa6.ProtocolError) as exc:
            self._error = str(exc)
            self.changed.emit()
            # Rejected edits must restore the controls' model bindings too.
            for side in self._sides.values():
                side.changed.emit()
            return
        self._profile.setNativeHaptics(config)

    def refresh(self):
        self._cache_key = None
        self._error = ""
        self.changed.emit()
        for side in self._sides.values():
            side.changed.emit()

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(Apex6FeedbackModel, constant=True)
    def grips(self):
        return self._sides["grips"]

    @Property(Apex6FeedbackModel, constant=True)
    def left(self):
        return self._sides["left"]

    @Property(Apex6FeedbackModel, constant=True)
    def right(self):
        return self._sides["right"]
