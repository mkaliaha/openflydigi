# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native profile-wide gyro settings over the shared mapping transaction."""
from copy import deepcopy

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import gpa6, gpa6_gyro, mapping
from .apex6_target import BUTTON_LABELS

QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1
KEYS = [None, *gpa6_gyro.KEYS]
KEY_LABELS = {**BUTTON_LABELS, "lt": "Left trigger", "rt": "Right trigger", None: "Not set"}
ENABLE_TYPES = (mapping.MOTION_PRESS, mapping.MOTION_CLICK)


@QmlElement
class Apex6GyroModel(QObject):
    changed = Signal()

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._error = ""
        self._cache_key = None
        self._cache = {}

    def _config(self):
        package = self._profile.nativeConfig
        return package.mapping if package is not None else None

    def _read(self):
        key = self._profile.generation
        if key != self._cache_key:
            state = dict(motion={}, curve=None, issue="", curve_issue="")
            config = self._config()
            if config is not None:
                try:
                    state["motion"] = config.motion()
                    if not self._profile.nativeConfig.source["features_raw"] & (1 << gpa6.FEATURES.index("imu")):
                        raise gpa6.ProtocolError("This controller does not report a gyro.")
                except gpa6.ProtocolError as exc:
                    state["issue"] = str(exc)
                if not state["issue"]:
                    try:
                        curve = config.motion_curve()
                        nodes = [curve["start"], *curve["points"], curve["end"]]
                        if any(a[0] > b[0] for a, b in zip(nodes, nodes[1:])):
                            raise gpa6.ProtocolError("Stored curve inputs are out of order")
                        state["curve"] = curve
                    except gpa6.ProtocolError as exc:
                        state["curve_issue"] = str(exc)
            self._cache, self._cache_key = state, key
        return self._cache

    def _edit(self, operation):
        if not self.editable:
            return
        try:
            candidate = self._config().copy()
            operation(candidate)
        except (ValueError, gpa6.ProtocolError) as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._profile.setNativeMapping(candidate)

    def refresh(self):
        self._cache_key = None
        self._error = ""
        self.changed.emit()

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def issue(self):
        return self._read()["issue"]

    @Property(bool, notify=changed)
    def editable(self):
        return bool(self._read()["motion"]) and not self.issue

    @Property("QStringList", constant=True)
    def targetNames(self):
        return ["Off", "Left stick", "Right stick", "Mouse"]

    @Property(int, notify=changed)
    def target(self):
        return self._read()["motion"].get("target", 0)

    @target.setter
    def target(self, value):
        if value in range(4):
            self._edit(lambda c: c.set_motion(target=value))

    @Property("QStringList", constant=True)
    def enableTypeNames(self):
        return ["While held", "Press to toggle"]

    @Property(int, notify=changed)
    def enableType(self):
        value = self._read()["motion"].get("enable_type")
        return ENABLE_TYPES.index(value) if value in ENABLE_TYPES else -1

    @enableType.setter
    def enableType(self, value):
        if value in range(2):
            self._edit(lambda c: c.set_motion(enable_type=ENABLE_TYPES[value]))

    @Property("QStringList", constant=True)
    def keyNames(self):
        return [KEY_LABELS[key] for key in KEYS]

    def _key(self, which):
        return KEYS.index(self._read()["motion"].get("keys", (None, None))[which])

    def _set_key(self, which, value):
        if self.editable and value in range(len(KEYS)):
            keys = list(self._read()["motion"]["keys"])
            keys[which] = KEYS[value]
            self._edit(lambda c: c.set_motion(keys=keys))

    @Property(int, notify=changed)
    def key(self):
        return self._key(0)

    @key.setter
    def key(self, value):
        self._set_key(0, value)

    @Property(int, notify=changed)
    def secondKey(self):
        return self._key(1)

    @secondKey.setter
    def secondKey(self, value):
        self._set_key(1, value)

    @Property(bool, notify=changed)
    def hasKey(self):
        return any(self._read()["motion"].get("activation_slots", []))

    @Property(str, notify=changed)
    def additionalKeys(self):
        slots = self._read()["motion"].get("activation_slots", [])
        return ", ".join(f"{KEY_LABELS[slot['key']]} ({'hold' if slot['enable_type'] == mapping.MOTION_PRESS else 'toggle'})"
                         for slot in slots[2:] if slot)

    @Property(str, notify=changed)
    def axes(self):
        motion = self._read()["motion"]
        return " / ".join(motion.get(name, "") for name in ("axis_x", "axis_y"))

    @Property(bool, notify=changed)
    def invertX(self):
        return self._read()["motion"].get("invert_x", False)

    @invertX.setter
    def invertX(self, value):
        self._edit(lambda c: c.set_motion(invert_x=value))

    @Property(bool, notify=changed)
    def invertY(self):
        return self._read()["motion"].get("invert_y", False)

    @invertY.setter
    def invertY(self, value):
        self._edit(lambda c: c.set_motion(invert_y=value))

    @Property(int, notify=changed)
    def mouseRadius(self):
        return self._read()["motion"].get("mouse_window_radius", 0)

    @mouseRadius.setter
    def mouseRadius(self, value):
        self._edit(lambda c: c.set_motion(mouse_window_radius=value))

    @Property(str, notify=changed)
    def curveIssue(self):
        return self._read()["curve_issue"]

    @Property(bool, notify=changed)
    def curveEditable(self):
        return self._read()["curve"] is not None

    @Property("QVariantList", notify=changed)
    def nodes(self):
        curve = self._read()["curve"]
        if curve is None:
            return []
        points = [curve["start"], *curve["points"], curve["end"]]
        return [dict(label="Start" if i == 0 else "End" if i == len(points) - 1 else f"Point {i}",
                     x=x, y=y, minimum=points[i - 1][0] if i else 0,
                     maximum=points[i + 1][0] if i + 1 < len(points) else 255)
                for i, (x, y) in enumerate(points)]

    @Slot(int, int, int)
    def setCoordinate(self, index, axis, value):
        if not self.curveEditable or axis not in (0, 1) or index not in range(len(self.nodes)):
            return
        curve = deepcopy(self._read()["curve"])
        points = [list(curve["start"]), *map(list, curve["points"]), list(curve["end"])]
        points[index][axis] = value
        self._edit(lambda c: c.set_motion_curve(start=points[0], end=points[-1], points=points[1:-1]))

    @Property(int, notify=changed)
    def midpointCount(self):
        return max(0, len(self.nodes) - 2)

    @midpointCount.setter
    def midpointCount(self, value):
        if not self.curveEditable or value not in range(3) or value == self.midpointCount:
            return
        curve = self._read()["curve"]
        points = list(curve["points"][:value])
        start, end = points[-1] if points else curve["start"], curve["end"]
        missing = value - len(points)
        points.extend(tuple(round(a + (b - a) * i / (missing + 1)) for a, b in zip(start, end))
                      for i in range(1, missing + 1))
        self._edit(lambda c: c.set_motion_curve(points=points))
