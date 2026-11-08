# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Native response and per-layer zones, sharing the profile transaction."""
from copy import deepcopy

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import gpa6, gpa6_actions as actions, gpa6_profile, mapping
from .apex6_target import TargetFields, BUTTON_LABELS, TARGET_LABELS

QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

DIRECTION_LABELS = ["Up", "Down", "Left", "Right", "Up left", "Up right", "Down left", "Down right"]


@QmlElement
class Apex6AnalogModel(QObject):
    changed = Signal()

    def __init__(self, profile, control):
        super().__init__(profile)
        self._profile = profile
        self._targets = TargetFields(self)
        self._error = ""
        self._control = control
        self._side = self._layer = self._zone = 0
        self._cache_key = None
        self._cache = {}

    def _config(self):
        package = self._profile.nativeConfig
        return package.mapping if package is not None else None

    def _read(self):
        config = self._config()
        if config is None:
            return dict(layers=[], macros=[], zones=[], response=None, issue="", angle=None)
        key = (self._profile.generation, self._side, self._layer)
        if key != self._cache_key:
            layers = config.layer_ids()
            result = dict(layers=layers, macros=[], zones=[], response=None, issue="", angle=None)
            side = mapping.SIDES[self._side]
            try:
                response = config.stick(side) if self.isStick else config.trigger_curve(side)
                curve = response["curve"]
                nodes = [curve["start"], *curve["points"], curve["end"]]
                if any(a[0] > b[0] for a, b in zip(nodes, nodes[1:])):
                    raise gpa6.ProtocolError("Stored curve inputs are out of order")
                result["response"] = response
            except gpa6.ProtocolError as exc:
                result["issue"] = str(exc)
            if self._layer in layers:
                if self.isStick:
                    zones = config.stick_zones(side, layer=self._layer)
                    if zones is not None:
                        result.update(zones=zones["zones"], angle=zones["angle_mode"])
                else:
                    result["zones"] = config.trigger_zones(side, layer=self._layer)
                try:
                    result["macros"] = config.macros(layer=self._layer)
                except gpa6.ProtocolError:
                    pass  # Opaque macros do not block response or zone reads.
            self._cache, self._cache_key = result, key
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

    def refresh(self):
        self._cache_key = None
        self._zone = max(0, min(self._zone, len(self._read()["zones"]) - 1))
        self._error = ""
        self.changed.emit()

    def clear(self):
        self._side = self._layer = self._zone = 0
        self.refresh()

    @Property(bool, constant=True)
    def isStick(self):
        return self._control == "stick"

    @Property(int, notify=changed)
    def side(self):
        return self._side

    @side.setter
    def side(self, value):
        if value in (0, 1):
            self._side, self._zone = value, 0
            self.refresh()

    @Property(str, notify=changed)
    def responseIssue(self):
        return self._read()["issue"]

    @Property(bool, notify=changed)
    def responseEditable(self):
        return self._read()["response"] is not None

    @Property("QVariantList", notify=changed)
    def nodes(self):
        response = self._read()["response"]
        if response is None:
            return []
        curve = response["curve"]
        points = [curve["start"], *curve["points"], curve["end"]]
        return [dict(label="Start" if i == 0 else "End" if i == len(points) - 1 else f"Point {i}",
                     x=x, y=y, minimum=points[i - 1][0] if i else 0,
                     maximum=points[i + 1][0] if i + 1 < len(points) else 255)
                for i, (x, y) in enumerate(points)]

    @Slot(int, int, int)
    def setCoordinate(self, index, axis, value):
        if not self.responseEditable or axis not in (0, 1) or index not in range(len(self.nodes)):
            return
        curve = deepcopy(self._read()["response"]["curve"])
        points = [list(curve["start"]), *map(list, curve["points"]), list(curve["end"])]
        points[index][axis] = value
        self._edit(lambda c: c.set_response_curve(self._control, mapping.SIDES[self._side],
                   start=points[0], end=points[-1], points=points[1:-1]))

    @Property(int, notify=changed)
    def midpointCount(self):
        return max(0, len(self.nodes) - 2)

    @midpointCount.setter
    def midpointCount(self, value):
        if not self.responseEditable or value not in range(3) or value == self.midpointCount:
            return
        curve = self._read()["response"]["curve"]
        points = list(curve["points"][:value])
        # Add evenly spaced points on the last segment, retaining existing ones.
        start = points[-1] if points else curve["start"]
        end = curve["end"]
        missing = value - len(points)
        points.extend(tuple(round(a + (b - a) * i / (missing + 1)) for a, b in zip(start, end))
                      for i in range(1, missing + 1))
        self._edit(lambda c: c.set_response_curve(self._control, mapping.SIDES[self._side], points=points))

    @Property("QStringList", constant=True)
    def presetNames(self):
        return ["Default", "Instant", "Delay", "Custom"] if self.isStick else ["Default", "Custom"]

    @Property(int, notify=changed)
    def preset(self):
        response = self._read()["response"]
        kind = response["type"] if response else 0
        return kind if self.isStick else int(kind != mapping.CURVE_DEFAULT)

    @preset.setter
    def preset(self, value):
        if not self.responseEditable or value not in range(len(self.presetNames)):
            return
        if value == len(self.presetNames) - 1:
            # Custom describes edits, not a separate stored mode. Put the
            # picker back on the detected preset without rewriting any bytes.
            self.changed.emit()
            return
        if self.isStick:
            self._edit(lambda c: c.set_stick(mapping.SIDES[self._side], curve_type=value))
        else:
            self._edit(lambda c: c.set_response_curve("trigger", mapping.SIDES[self._side],
                       start=(0, 0), end=(255, 255), points=[]))

    def _flag(self, name):
        return bool((self._read()["response"] or {}).get(name, False))

    def _set_flag(self, name, value):
        if self.isStick and self.responseEditable:
            self._edit(lambda c: c.set_stick(mapping.SIDES[self._side], **{name: value}))

    @Property(bool, notify=changed)
    def circular(self):
        return self._flag("circular")

    @circular.setter
    def circular(self, value):
        self._set_flag("circular", value)

    @Property(bool, notify=changed)
    def invertX(self):
        return self._flag("invert_x")

    @invertX.setter
    def invertX(self, value):
        self._set_flag("invert_x", value)

    @Property(bool, notify=changed)
    def invertY(self):
        return self._flag("invert_y")

    @invertY.setter
    def invertY(self, value):
        self._set_flag("invert_y", value)

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
            self._layer, self._zone = value, 0
            self.refresh()

    @Property(bool, notify=changed)
    def layerExists(self):
        return self._layer in self._read()["layers"]

    @Slot()
    def createLayer(self):
        self._edit(lambda c: c.ensure_layer(self._layer))

    @Property(int, constant=True)
    def maximumZones(self):
        return 8 if self.isStick else 4

    @Property("QStringList", notify=changed)
    def zoneNames(self):
        result = []
        for i, zone in enumerate(self._read()["zones"]):
            target = zone["target"]
            kind = target["type"]
            label = (BUTTON_LABELS.get(target["id"], str(target["id"])) if kind == "btn_gamepad" else
                     TARGET_LABELS[list(actions.TARGETS).index(kind)] if kind in actions.TARGETS else
                     "Unknown target " + target.get("hex", ""))
            direction = zone.get("dir")
            prefix = (DIRECTION_LABELS[gpa6_profile.DIRECTIONS.index(direction)]
                      if direction in gpa6_profile.DIRECTIONS else f"Unknown direction {direction}") if self.isStick else "Travel"
            result.append(f"{i + 1}. {prefix} {zone['start']}–{zone['stop']} → {label}")
        return result

    @Property(int, notify=changed)
    def zone(self):
        return self._zone

    @zone.setter
    def zone(self, value):
        if value in range(len(self._read()["zones"])):
            self._zone = value
            self._error = ""
            self.changed.emit()

    @Property(bool, notify=changed)
    def editable(self):
        if not self.layerExists:
            return False
        for zone in self._read()["zones"]:
            if zone["start"] > zone["stop"] or (self.isStick and zone["dir"] not in gpa6_profile.DIRECTIONS):
                return False
            try:
                actions.encode_target(zone["target"])
            except ValueError:
                return False
        return True

    def _entry(self):
        zones = self._read()["zones"]
        return zones[self._zone] if self._zone in range(len(zones)) else None

    def _set_zones(self, zones):
        self._edit(lambda c: getattr(c, "set_" + self._control + "_zones")(
            mapping.SIDES[self._side], zones, layer=self._layer))

    def _change_entry(self, **changes):
        if self.editable and self._entry() is not None:
            zones = deepcopy(self._read()["zones"])
            zones[self._zone].update(changes)
            self._set_zones(zones)

    @Slot()
    def addZone(self):
        if self.editable and len(self._read()["zones"]) < self.maximumZones:
            zones = deepcopy(self._read()["zones"])
            entry = dict(start=64, stop=255, target={"type": "btn_gamepad", "id": "a"})
            if self.isStick:
                entry["dir"] = "up"
            zones.append(entry)
            self._zone = len(zones) - 1
            self._set_zones(zones)

    @Slot()
    def removeZone(self):
        if self.editable and self._entry() is not None:
            zones = deepcopy(self._read()["zones"])
            del zones[self._zone]
            self._set_zones(zones)

    @Slot()
    def clearZones(self):
        if self.layerExists:
            self._set_zones([])

    @Property(int, notify=changed)
    def start(self):
        return (self._entry() or {}).get("start", 0)

    @start.setter
    def start(self, value):
        self._change_entry(start=value)

    @Property(int, notify=changed)
    def stop(self):
        return (self._entry() or {}).get("stop", 255)

    @stop.setter
    def stop(self, value):
        self._change_entry(stop=value)

    @Property("QStringList", constant=True)
    def directionNames(self):
        return DIRECTION_LABELS

    @Property(int, notify=changed)
    def direction(self):
        value = (self._entry() or {}).get("dir")
        return gpa6_profile.DIRECTIONS.index(value) if value in gpa6_profile.DIRECTIONS else -1

    @direction.setter
    def direction(self, value):
        if self.isStick and value in range(len(gpa6_profile.DIRECTIONS)):
            self._change_entry(dir=gpa6_profile.DIRECTIONS[value])

    @Property("QStringList", constant=True)
    def angleNames(self):
        return ["4 directions", "8 directions", "4 directions with overlap"]

    @Property(int, notify=changed)
    def angle(self):
        value = self._read()["angle"]
        return gpa6_profile.ANGLE_MODES.index(value) if value is not None else -1

    @angle.setter
    def angle(self, value):
        if self.isStick and self.angle >= 0 and value in range(3):
            self._edit(lambda c: c.set_stick_zones(mapping.SIDES[self._side], layer=self._layer,
                       angle_mode=gpa6_profile.ANGLE_MODES[value]))

    @Property("QStringList", constant=True)
    def targetNames(self):
        return self._targets.targetNames

    @Property(int, notify=changed)
    def target(self):
        return self._targets.target

    @target.setter
    def target(self, value):
        self._targets.target = value

    @Property("QVariantList", notify=changed)
    def fields(self):
        return self._targets.fields

    @Slot(int, result="QVariantMap")
    def field(self, index):
        return self._targets.field(index)

    @Slot(int, int)
    def setField(self, index, value):
        self._targets.setField(index, value)

    @Property(str, notify=changed)
    def error(self):
        return self._error
