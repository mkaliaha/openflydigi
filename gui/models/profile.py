# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Profile state: the four slots, the open config, and the edits to it.

Buttons, vibration and triggers all live in the same profile blob, so they are
three views onto one `MappingConfig` rather than three pieces of independent
state -- that is why they share a dirty flag and a write button.

Dirty is derived, not tracked: the edited blob compared against what the pad
last gave us. The widget code needed a `_loading` guard because setting a
widget fired its own change signal straight back into the config; nothing here
writes back on refresh, so the guard is gone.
"""
from PySide6.QtCore import (Property, QAbstractListModel, QModelIndex, QObject,
                            Qt, QUrl, Signal, Slot)
from PySide6.QtQml import QmlElement

from flydigi import effects, mapping

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1


def local_path(where):
    """Accept either a plain path or the file:// URL a QML FileDialog hands back."""
    text = str(where)
    if text.startswith("file:"):
        return QUrl(text).toLocalFile()
    return text

# "(default)" is not a target the pad stores -- it means "this key does what the
# shell says", which the pad encodes as 255 rather than as the key's own id.
DEFAULT_TARGET = "(default)"
TARGETS = [DEFAULT_TARGET] + mapping.XINPUT_TARGETS

TURBO_MODES = [("While held", mapping.TURBO_WHILE_HELD),
               ("Toggle", mapping.TURBO_TOGGLE)]

# Effect ids as stored in the profile, using the same vocabulary as the live
# SetForceTrigger command. The whole of Flydigi's AdapterTriggerType, in their
# order, so the combo box index is the stored mode byte -- see flydigi/effects.py
# for what each one's parameters mean and where they land in the blob.
TRIGGER_MODES = [(effect.label, effect.mode) for effect in effects.EFFECTS]

TURBO_MAX_HZ = 40

# The pad stores a profile title as 20 bytes of UTF-16 and truncates anything
# longer without saying so. Enforced here rather than in a text field, so the
# limit holds whatever sets the title.
TITLE_MAX_CHARS = mapping.TITLE_BYTES // 2

# Physical grouping, so the list reads like the shell in your hands rather than
# like the key table's storage order.
KEY_CLUSTERS = [
    ("Face buttons", ["a", "b", "x", "y"]),
    ("D-pad", ["up", "down", "left", "right"]),
    ("Shoulders and triggers", ["lb", "rb", "lt", "rt", "m5", "m6"]),
    ("Sticks", ["thl", "thr"]),
    ("System", ["select", "start", "home"]),
    ("Paddles", ["m1", "m2", "m3", "m4"]),
]

CLUSTER_OF = {key: title for title, keys in KEY_CLUSTERS for key in keys}

# The pad's own short names are what the protocol uses; these are what the
# shell is silkscreened with, or what a person would call the button.
#
# M5 and M6 are the shoulder pair, not paddles. Space Station labels them LM
# and RM, which is what its k5 hitbox map puts at the top edge of the shell
# either side of the triggers; "M5"/"M6" appear only in the SDK's key ids.
KEY_LABELS = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "lb": "Left bumper", "rb": "Right bumper",
    "lt": "Left trigger", "rt": "Right trigger",
    "thl": "Left stick click", "thr": "Right stick click",
    "select": "Select", "start": "Start", "home": "Home",
    "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4",
    "m5": "LM", "m6": "RM",
}


def key_label(key):
    return KEY_LABELS.get(key, key.upper())


class Decodes:
    """Decode the profile once per edit, rather than once per property read.

    Every getter in this file reads a field out of the profile blob, and the
    blob is bytes -- so reading a field means decoding it. That is the right
    shape when a read is a field access. It is the wrong one here: a read from
    QML is an interpreter call wrapping real work, these models are wide, and
    each of them notifies with a single `changed` covering the lot. `MotionModel`
    has twelve properties and every one of them called `config.motion()`, so one
    `changed` -- emitted on every knob move -- cost thirteen decodes of the same
    eight bytes. `KeyMapModel.data` decoded a row before it looked at which role
    was being asked for, and a view sweeping 23 rows across 9 roles decoded the
    key table 207 times for 23 rows' worth of information.

    So each decoder memoises against `ProfileModel.generation`, which moves
    whenever the bytes might have. Reads come through `_decoded`; writers take
    the config from `ProfileModel.edited()`, and *that* is what moves the
    generation. The split is the whole safety argument: a mutator cannot forget
    to invalidate, because the call that hands it something to mutate is the
    call that invalidates.

    There is one way round `edited`, and `refresh` is it: something outside
    these models -- a test, a restore, a caller holding `config` -- can write
    into the blob directly, and `refresh` is what it calls to say so. So every
    `refresh` here drops the cache before it emits. That is not a patch over a
    hole; it is what `refresh` has always meant.

    Cached values are handed out by reference and must be treated as read-only.
    Two mutators here want to edit what they just read, and both take a copy
    first -- see `setEffectParam` and `_editable_macros`.

    Held by each model rather than inherited: PySide6 is particular about what
    may be mixed into a QObject, and a plain attribute costs nothing to be sure
    about.
    """

    def __init__(self, profile):
        self._profile = profile
        self._cache = {}

    def __call__(self, name, decode):
        generation = self._profile.generation
        cached = self._cache.get(name)
        if cached is not None and cached[0] == generation:
            return cached[1]
        value = decode()
        self._cache[name] = (generation, value)
        return value

    def clear(self):
        self._cache.clear()


@QmlElement
class VibrationSideModel(QObject):
    """One grip motor's window.

    A single `changed` signal covers all four fields because the backend keeps
    min <= max by swapping them, so setting one can move another.
    """

    changed = Signal()

    def __init__(self, profile, side):
        super().__init__(profile)
        self._profile = profile
        self._side = side
        self._decoded = Decodes(profile)

    def _window(self):
        config = self._profile.config
        return self._decoded(
            "vibration",
            lambda: config.vibration(self._side) if config is not None else None)

    def _field(self, index):
        window = self._window()
        return window[index] if window is not None else 0

    def _set(self, **kwargs):
        config = self._profile.edited()
        if config is None:
            return
        config.set_vibration(self._side, **kwargs)
        self.changed.emit()
        self._profile.markChanged()

    @Property(bool, notify=changed)
    def enabled(self):
        return bool(self._field(0))

    @enabled.setter
    def enabled(self, value):
        self._set(enabled=bool(value))

    @Property(int, notify=changed)
    def minimum(self):
        return self._field(1)

    @minimum.setter
    def minimum(self, value):
        self._set(minimum=int(value))

    @Property(int, notify=changed)
    def maximum(self):
        return self._field(2)

    @maximum.setter
    def maximum(self, value):
        self._set(maximum=int(value))

    @Property(int, notify=changed)
    def scale(self):
        return self._field(3)

    @scale.setter
    def scale(self, value):
        self._set(scale=int(value))

    def refresh(self):
        self._decoded.clear()
        self.changed.emit()


@QmlElement
class VibrationModel(QObject):
    """Master switch plus the two grip motors."""

    enabledChanged = Signal()

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._sides = {side: VibrationSideModel(profile, side)
                       for side in mapping.SIDES}

    @Property(VibrationSideModel, constant=True)
    def left(self):
        return self._sides["left"]

    @Property(VibrationSideModel, constant=True)
    def right(self):
        return self._sides["right"]

    def side(self, name):
        return self._sides[name]

    @Property(bool, notify=enabledChanged)
    def enabled(self):
        config = self._profile.config
        return bool(config.vibration_enabled) if config is not None else False

    @enabled.setter
    def enabled(self, value):
        config = self._profile.edited()
        if config is None:
            return
        config.vibration_enabled = bool(value)
        self.enabledChanged.emit()
        self._profile.markChanged()

    def refresh(self):
        self.enabledChanged.emit()
        for side in self._sides.values():
            side.refresh()


@QmlElement
class EffectParamsModel(QAbstractListModel):
    """One row per knob the chosen trigger effect has.

    **A model rather than a list, because a list broke the knobs.** This was a
    `QVariantList` property built fresh on every read and notified by the same
    `changed` signal that moving a knob emits -- so moving a knob replaced the
    Repeater's model, and replacing a Repeater's model destroys its delegates.
    The delegate being destroyed was the one under the pointer, and it took the
    mouse grab with it. Driven with synthetic pointer events: a slider outside
    the Repeater reported `moved` forty times across a drag, the same slider
    inside it reported once. The knobs could not be dragged at all, only
    clicked, and `tests/qml/tst_triggers.qml` missed it by calling `moved(60)`
    on the control rather than dragging anything.

    A model that answers an edit with `dataChanged` leaves the delegates alone,
    which is the whole difference. The row *set* still changes when the effect
    does -- Racing has two knobs, Sniper five, General none -- and that is a
    reset, correctly, because those really are different rows.
    """

    KeyRole = Qt.UserRole + 1
    LabelRole = Qt.UserRole + 2
    DescriptionRole = Qt.UserRole + 3
    MinimumRole = Qt.UserRole + 4
    MaximumRole = Qt.UserRole + 5
    KindRole = Qt.UserRole + 6
    ValueRole = Qt.UserRole + 7

    def __init__(self, trigger):
        super().__init__(trigger)
        self._trigger = trigger
        self._rows = []

    def roleNames(self):
        # Not "from"/"to", which is what the list carried, because a role name
        # becomes a property on the delegate and `from` is a keyword in enough
        # contexts to be worth not finding out about.
        return {
            self.KeyRole: b"key",
            self.LabelRole: b"label",
            self.DescriptionRole: b"description",
            self.MinimumRole: b"minimum",
            self.MaximumRole: b"maximum",
            self.KindRole: b"kind",
            self.ValueRole: b"value",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._rows):
            return None
        param, value = self._rows[row]
        if role == self.KeyRole:
            return param.key
        if role in (self.LabelRole, Qt.DisplayRole):
            return param.label
        if role == self.DescriptionRole:
            return param.description
        if role == self.MinimumRole:
            return param.minimum
        if role == self.MaximumRole:
            return param.maximum
        if role == self.KindRole:
            return param.kind
        if role == self.ValueRole:
            return value
        return None

    def refresh(self):
        """Take the current effect's knobs, saying only what actually moved."""
        values = self._trigger._values()
        rows = [(param, values.get(param.key, param.default))
                for param in effects.effect(self._trigger._mode()).params]
        if rows == self._rows:
            return
        same_knobs = [param.key for param, _v in rows] == [
            param.key for param, _v in self._rows]
        if same_knobs and rows:
            self._rows = rows
            self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, 0),
                                  [self.ValueRole])
            return
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


@QmlElement
class TriggerSideModel(QObject):
    """One trigger's stored adaptive effect and travel window."""

    changed = Signal()

    def __init__(self, profile, side):
        super().__init__(profile)
        self._profile = profile
        self._side = side
        self._decoded = Decodes(profile)
        self._params = EffectParamsModel(self)

    def _mode(self):
        config = self._profile.config
        return self._decoded(
            "mode",
            lambda: config.trigger_effect(self._side)[0] if config is not None else 0)

    def _values(self, mode=None):
        """One effect's knobs by name, as the vocabulary reads them out of the
        blob. `mode` defaults to the stored one; passing another asks what that
        effect would make of the same bytes, which is how switching effects
        keeps the numbers an earlier visit to it left behind.

        Only the stored reading is cached. The others are asked for once, by
        `effect`'s setter at the moment the picker moves, and caching a reading
        under an effect that is not the stored one would need the mode in the
        key for no benefit.
        """
        if mode is not None:
            return self._read_values(mode)
        return self._decoded("values", self._read_values)

    def _read_values(self, mode=None):
        config = self._profile.config
        if config is None:
            return {}
        stored_mode, params = config.trigger_effect(self._side)
        return effects.values(stored_mode if mode is None else mode, params,
                              config.trigger_bind(self._side))

    def _store(self, mode, values):
        config = self._profile.edited()
        if config is None:
            return
        params, bind = effects.stored(mode, values)
        config.set_trigger_effect(self._side, mode, params, bind)
        # Before `changed`, so a handler that reads the row model sees the knob
        # it just moved rather than the one before it.
        self._params.refresh()
        self.changed.emit()
        self._profile.markChanged()

    @Property(int, notify=changed)
    def effect(self):
        """Index into TRIGGER_MODES, not the stored id."""
        mode = self._mode()
        return next((i for i, (_l, m) in enumerate(TRIGGER_MODES) if m == mode), 0)

    @effect.setter
    def effect(self, value):
        index = max(0, min(len(TRIGGER_MODES) - 1, int(value)))
        mode = TRIGGER_MODES[index][1]
        # Written with what the new effect makes of the bytes already there:
        # its own numbers if it was chosen before, its defaults where the last
        # effect left something it cannot use. Writing the mode alone would
        # leave a frequency of 0 in a field the pad refuses at 0.
        self._store(mode, self._values(mode))

    @Property(EffectParamsModel, constant=True)
    def effectParams(self):
        """The chosen effect's knobs, in order, each ready to draw as a row.

        A model rather than named properties because the knobs are not the same
        from one effect to the next -- Racing has two, Sniper five, General none
        -- and a fixed pair of "start"/"strength" properties could only describe
        one of them honestly. `constant=True` because the model object never
        changes; what changes is its contents, which it reports itself.
        """
        return self._params

    @Slot(str, result=int)
    def paramValue(self, key):
        """One knob's stored value, by name. -1 when this effect has no such knob.

        The row model is how the page *draws* the knobs; this is how anything
        that already knows a key reads one, without asking for the whole list to
        find it. Nothing decodes here that `effectParams` has not decoded
        already -- both go through the same memoised reading.
        """
        param = next((p for p in effects.effect(self._mode()).params
                      if p.key == key), None)
        if param is None:
            return -1
        return self._values().get(key, param.default)

    @Slot(str, int)
    def setEffectParam(self, key, value):
        """Move one knob, keeping the rest -- read-modify-write of the block."""
        mode = self._mode()
        values = self._values()
        if key not in values:
            return
        # A copy: `_values` is memoised and hands out the cached dict itself.
        values = dict(values)
        values[key] = int(value)
        self._store(mode, values)

    # -- the stroke window -------------------------------------------------
    #
    # Space Station's "Stroke Setting": where the trigger starts registering and
    # where it reads full. It is the travel curve block at offset 123, and this
    # pad plays it -- measured with `tools/trigger-stroke-probe`, which gave one
    # trigger a 0..16 window and left the other alone: 17 distinct evdev values
    # against the control's 240, in the same sweep. The reported range stays
    # 0..255 either way, so what the window moves is the physical travel, not
    # what the game reads, exactly as Flydigi's own tooltip says.
    #
    # The neighbouring candidate is dead. `Param[0..1]` of the force-trigger
    # block at 195/196 carries the same pair on paper -- it is what Space
    # Station writes for a pad *with* adaptive triggers -- and the same probe
    # found it inert here, 238 against 239. Nor does their UI ever set it:
    # `triggerStrokeUsable` is `!supportAdaptTrigger`, so on a k5 the slider is
    # hidden, and `ForceTriggerConfigNormal` sends `[side, 0]` with no
    # parameters at all. Do not move this pair to 195/215.

    def _stroke(self, key):
        config = self._profile.config
        if config is None:
            return 0 if key == "zero" else 255
        return self._decoded(
            "curve", lambda: config.trigger_curve(self._side))[key]

    def _set_stroke(self, **kwargs):
        config = self._profile.edited()
        if config is None:
            return
        config.set_trigger_curve(self._side, **kwargs)
        self.changed.emit()
        self._profile.markChanged()

    @Property(int, notify=changed)
    def strokeStart(self):
        return self._stroke("zero")

    @strokeStart.setter
    def strokeStart(self, value):
        self._set_stroke(zero=int(value))

    @Property(int, notify=changed)
    def strokeEnd(self):
        return self._stroke("end")

    @strokeEnd.setter
    def strokeEnd(self, value):
        self._set_stroke(end=int(value))

    def refresh(self):
        self._decoded.clear()
        self._params.refresh()
        self.changed.emit()


@QmlElement
class TriggerModel(QObject):
    """The two triggers' per-profile settings."""

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._sides = {side: TriggerSideModel(profile, side) for side in mapping.SIDES}

    # No trigger-motor properties. The blob has a trigger-vibration block and
    # this pad has no such motors -- see MappingConfig.trigger_motor and the
    # note on the Triggers page. The switch that used to be here was writing a
    # byte nothing reads.

    @Property("QStringList", constant=True)
    def effectNames(self):
        return [label for label, _mode in TRIGGER_MODES]

    @Property(TriggerSideModel, constant=True)
    def left(self):
        return self._sides["left"]

    @Property(TriggerSideModel, constant=True)
    def right(self):
        return self._sides["right"]

    def side(self, name):
        return self._sides[name]

    def refresh(self):
        for side in self._sides.values():
            side.refresh()


# The curve presets, in the order a picker shows them. Space Station's own
# labels: the enum calls the middle two Quick and Slow, the UI does not.
CURVE_PRESETS = [("Default", mapping.CURVE_DEFAULT),
                 ("Instant", mapping.CURVE_QUICK),
                 ("Delay", mapping.CURVE_SLOW),
                 ("Custom", mapping.CURVE_CUSTOM)]

# Both are percentages, and both refuse their negative half -- see
# mapping.BIPOLAR_MAX for why only one half has an encoding we trust.
STICK_MAX = mapping.BIPOLAR_MAX

# Where the gyro can be pointed. Named the way Space Station names them --
# the stick first, the genre it suits in parentheses ("Left joystick (racing
# games)", "Right joystick (shooting games)") -- since the thing that actually
# moves belongs in the label and the genre is the hint. Mouse is not offered --
# the pad cannot do it, see mapping.MOTION_TARGETS.
MOTION_TARGETS = [("Off", mapping.MOTION_OFF),
                  ("Left stick (racing)", mapping.MOTION_LEFT_STICK),
                  ("Right stick (aiming)", mapping.MOTION_RIGHT_STICK)]

MOTION_ENABLE_TYPES = [("Press to toggle", mapping.MOTION_CLICK),
                       ("While held", mapping.MOTION_PRESS)]

# What may turn the gyro on. Every button the shell has, unlike a remap target:
# an enable key is read by the pad itself and never sent anywhere, so the
# paddles -- which XInput cannot carry -- are the best keys for it.
MOTION_NO_KEY = "(none)"
MOTION_KEYS = [MOTION_NO_KEY] + mapping.APEX5_KEYS


@QmlElement
class StickSideModel(QObject):
    """One stick's response curve.

    A single `changed` signal for the lot, because every field feeds the same
    compiler: moving the dead zone recomputes the bank, and choosing a preset
    moves the dead zone. Nothing here changes on its own.
    """

    changed = Signal()

    def __init__(self, profile, side):
        super().__init__(profile)
        self._profile = profile
        self._side = side
        self._decoded = Decodes(profile)

    def _read_stick(self):
        config = self._profile.config
        if config is None:
            return {"type": 0, "center": 0, "edge": 0, "circular": False,
                    "bank": list(mapping.stick_bank()), "is_stick": True}
        return config.stick(self._side)

    def _stick(self):
        return self._decoded("stick", self._read_stick)

    def _set(self, **kwargs):
        config = self._profile.edited()
        if config is None:
            return
        config.set_stick(self._side, **kwargs)
        self.changed.emit()
        self._profile.markChanged()

    @Property(int, notify=changed)
    def curveType(self):
        """Index into CURVE_PRESETS, not the stored id."""
        stored = self._stick()["type"]
        return next((i for i, (_l, v) in enumerate(CURVE_PRESETS) if v == stored),
                    len(CURVE_PRESETS) - 1)

    @curveType.setter
    def curveType(self, value):
        index = max(0, min(len(CURVE_PRESETS) - 1, int(value)))
        self._set(curve_type=CURVE_PRESETS[index][1])

    @Property(int, notify=changed)
    def center(self):
        stick = self._stick()
        # 127 is the firmware's "this stick is mapped to something that is not a
        # stick" sentinel. Reporting it as a dead zone of 127 would be a lie, and
        # on a 0..100 slider an impossible one.
        return stick["center"] if stick["is_stick"] else 0

    @center.setter
    def center(self, value):
        self._set(center=max(0, min(STICK_MAX, int(value))))

    @Property(int, notify=changed)
    def edge(self):
        edge = self._stick()["edge"]
        return edge if edge <= STICK_MAX else 0

    @edge.setter
    def edge(self, value):
        self._set(edge=max(0, min(STICK_MAX, int(value))))

    @Property(bool, notify=changed)
    def circular(self):
        return bool(self._stick()["circular"])

    @circular.setter
    def circular(self, value):
        self._set(circular=bool(value))

    @Property("QVariantList", notify=changed)
    def bank(self):
        """The nine points the pad actually plays, for drawing the curve.

        Exposed because this is the only honest thing to plot: the polyline is
        what the user edits, but the bank is what the stick will do.

        Cached with the decode rather than rebuilt per read: this crosses into
        QML as a `QVariantList`, which is a conversion of nine values, and the
        page reads it on every notification whether or not the curve moved.
        """
        return self._decoded(
            "bank", lambda: [int(v) for v in self._stick()["bank"]])

    @Property(bool, notify=changed)
    def isStick(self):
        """False when this stick is bound to keyboard, mouse or d-pad.

        The curve fields mean nothing then, and the remapping that caused it is
        not something this app can edit yet -- so the page says so rather than
        offering controls that would write into a block the pad is ignoring.
        """
        return bool(self._stick()["is_stick"])

    def refresh(self):
        self._decoded.clear()
        self.changed.emit()


@QmlElement
class StickModel(QObject):
    """Both sticks."""

    def __init__(self, profile):
        super().__init__(profile)
        self._sides = {side: StickSideModel(profile, side)
                       for side in mapping.SIDES}

    @Property(StickSideModel, constant=True)
    def left(self):
        return self._sides["left"]

    @Property(StickSideModel, constant=True)
    def right(self):
        return self._sides["right"]

    @Property("QStringList", constant=True)
    def presetNames(self):
        return [label for label, _value in CURVE_PRESETS]

    @Property(int, constant=True)
    def maximum(self):
        return STICK_MAX

    def side(self, name):
        return self._sides[name]

    def refresh(self):
        for side in self._sides.values():
            side.refresh()


@QmlElement
class MotionModel(QObject):
    """The gyro mapped onto a stick, stored in the open profile.

    One `changed` signal for the lot, as the sticks have: picking a target also
    moves the use mode, so no field here is independent of the others.
    """

    changed = Signal()

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._decoded = Decodes(profile)

    def _read_motion(self):
        config = self._profile.config
        if config is None:
            # Space Station's own starting numbers, so a page with no profile
            # open shows the same sliders it will show a moment later.
            return {"target": mapping.MOTION_OFF,
                    "enable_type": mapping.MOTION_CLICK,
                    "keys": (None, None), "sensitivity": 50, "dead_zone": 15,
                    "use_mode": mapping.MOTION_FPS}
        return config.motion()

    def _motion(self):
        # Thirteen properties on this model read through here, and one `changed`
        # invalidates all of them at once. Without the memo that was thirteen
        # decodes of the same eight bytes per knob move.
        return self._decoded("motion", self._read_motion)

    def _set(self, **kwargs):
        config = self._profile.edited()
        if config is None:
            return
        config.set_motion(**kwargs)
        self.changed.emit()
        self._profile.markChanged()

    @Property("QStringList", constant=True)
    def targetNames(self):
        return [label for label, _value in MOTION_TARGETS]

    @Property("QStringList", constant=True)
    def enableTypeNames(self):
        return [label for label, _value in MOTION_ENABLE_TYPES]

    @Property("QStringList", constant=True)
    def keyNames(self):
        return [MOTION_NO_KEY] + [key_label(key) for key in mapping.APEX5_KEYS]

    @Property(int, constant=True)
    def maximum(self):
        return mapping.MOTION_SENSITIVITY_MAX

    @Property(int, notify=changed)
    def target(self):
        """Index into MOTION_TARGETS, not the stored id."""
        stored = self._motion()["target"]
        return next((i for i, (_l, v) in enumerate(MOTION_TARGETS) if v == stored),
                    0)

    @target.setter
    def target(self, value):
        index = max(0, min(len(MOTION_TARGETS) - 1, int(value)))
        self._set(target=MOTION_TARGETS[index][1])

    @Property(bool, notify=changed)
    def enabled(self):
        """Whether the gyro drives anything -- what the page's controls hang on."""
        return self._motion()["target"] != mapping.MOTION_OFF

    @Property(bool, notify=changed)
    def isMouse(self):
        """Set up on Windows to move the host's pointer, which this cannot do.

        Not a state this app can produce, and one a profile brought over from
        Space Station can be in. The page says so rather than showing Off,
        which is what the target combo has to fall back to.
        """
        return self._motion()["target"] == mapping.MOTION_MOUSE

    @Property(int, notify=changed)
    def enableType(self):
        stored = self._motion()["enable_type"]
        return next((i for i, (_l, v) in enumerate(MOTION_ENABLE_TYPES)
                     if v == stored), 0)

    @enableType.setter
    def enableType(self, value):
        index = max(0, min(len(MOTION_ENABLE_TYPES) - 1, int(value)))
        self._set(enable_type=MOTION_ENABLE_TYPES[index][1])

    def _key_index(self, which):
        key = self._motion()["keys"][which]
        return MOTION_KEYS.index(key) if key in MOTION_KEYS else 0

    def _set_key(self, which, value):
        index = max(0, min(len(MOTION_KEYS) - 1, int(value)))
        chosen = None if index == 0 else MOTION_KEYS[index]
        keys = list(self._motion()["keys"])
        keys[which] = chosen
        self._set(keys=tuple(keys))

    @Property(int, notify=changed)
    def key(self):
        return self._key_index(0)

    @key.setter
    def key(self, value):
        self._set_key(0, value)

    @Property(int, notify=changed)
    def secondKey(self):
        return self._key_index(1)

    @secondKey.setter
    def secondKey(self, value):
        self._set_key(1, value)

    @Property(bool, notify=changed)
    def hasKey(self):
        """A mapping with no enable key is one nothing can switch on."""
        return any(key is not None for key in self._motion()["keys"])

    @Property(bool, notify=changed)
    def holdMode(self):
        """Whether the second enable key can be edited at all.

        The blob's format only carries a change to it under Hold -- Flydigi's
        writer assigns byte 7 inside that branch and re-emits what it read
        otherwise -- so offering the control under Click would be offering one
        that silently does nothing. Space Station reveals its second-key row on
        the same condition.
        """
        return self._motion()["enable_type"] == mapping.MOTION_PRESS

    @Property(str, notify=changed)
    def strandedKey(self):
        """A second enable key that is live and cannot be reached from here.

        Empty unless the profile is on Click *and* byte 7 holds a key. That is
        the factory's own state -- it ships with D-pad Up there. Byte 7 is
        known to work on its own, measured with `tools/gyro-map-probe`, but
        that window was a *Hold* one; whether the firmware reads the second key
        under Click is not settled either way. So the page names the key rather
        than showing a control that might not write.
        """
        if self.holdMode:
            return ""
        second = self._motion()["keys"][1]
        return "" if second is None else key_label(second)

    @Property(int, notify=changed)
    def sensitivity(self):
        return self._motion()["sensitivity"]

    @sensitivity.setter
    def sensitivity(self, value):
        self._set(sensitivity=int(value))

    @Property(int, notify=changed)
    def deadZone(self):
        return self._motion()["dead_zone"]

    @deadZone.setter
    def deadZone(self, value):
        self._set(dead_zone=int(value))

    @Property(str, notify=changed)
    def useMode(self):
        """Shown and not offered: it follows the target, as it does in theirs."""
        return {mapping.MOTION_FPS: "FPS",
                mapping.MOTION_RACER: "Racing"}.get(self._motion()["use_mode"], "")

    def refresh(self):
        self._decoded.clear()
        self.changed.emit()


@QmlElement
class KeyMapModel(QAbstractListModel):
    """One row per physical key: what it sends, and its turbo."""

    KeyRole = Qt.UserRole + 1
    LabelRole = Qt.UserRole + 2
    TargetRole = Qt.UserRole + 3
    TargetIndexRole = Qt.UserRole + 4
    TurboRole = Qt.UserRole + 5
    TurboModeRole = Qt.UserRole + 6
    RemappedRole = Qt.UserRole + 7
    EditableRole = Qt.UserRole + 8
    ClusterRole = Qt.UserRole + 9

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._decoded = Decodes(profile)

    def roleNames(self):
        return {
            self.KeyRole: b"key",
            self.LabelRole: b"label",
            self.TargetRole: b"target",
            self.TargetIndexRole: b"targetIndex",
            self.TurboRole: b"turbo",
            self.TurboModeRole: b"turboMode",
            self.RemappedRole: b"isRemapped",
            self.EditableRole: b"isEditable",
            self.ClusterRole: b"cluster",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(mapping.APEX5_KEYS)

    @Property(int, constant=True)
    def count(self):
        return len(mapping.APEX5_KEYS)

    @Property("QStringList", constant=True)
    def targets(self):
        return list(TARGETS)

    @Property("QStringList", constant=True)
    def turboModes(self):
        return [label for label, _mode in TURBO_MODES]

    @Property(int, constant=True)
    def turboMax(self):
        return TURBO_MAX_HZ

    def _table(self):
        """Every row's (key, target, mode, frequency), decoded once."""
        config = self._profile.config

        def decode():
            if config is None:
                return [(key, key, mapping.TURBO_OFF, 0)
                        for key in mapping.APEX5_KEYS]
            return [(key,) + tuple(config.mapping(key))
                    for key in mapping.APEX5_KEYS]

        return self._decoded("table", decode)

    def _row(self, row):
        """(key, target, mode, frequency) for a row, or None if out of range."""
        if not 0 <= row < len(mapping.APEX5_KEYS):
            return None
        return self._table()[row]

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if not 0 <= row < len(mapping.APEX5_KEYS):
            return None
        # The three roles that need only the key name are answered before the
        # table is touched. A view sweeping 23 rows across 9 roles asked for the
        # key table 207 times to fill in 23 rows' worth of labels; a third of
        # those questions never needed the profile at all.
        key = mapping.APEX5_KEYS[row]
        if role == self.KeyRole:
            return key
        if role in (self.LabelRole, Qt.DisplayRole):
            return key_label(key)
        if role == self.ClusterRole:
            return CLUSTER_OF.get(key, "Other")
        _key, target, mode, frequency = self._table()[row]
        if role == self.TargetRole:
            return target
        if role == self.TargetIndexRole:
            # Macro and keyboard bindings are real but not editable here, so
            # they show as default rather than being silently rewritten.
            if target == key and not frequency:
                return 0
            if target in mapping.XINPUT_TARGETS:
                return mapping.XINPUT_TARGETS.index(target) + 1
            return 0
        if role == self.TurboRole:
            return frequency
        if role == self.TurboModeRole:
            return 1 if mode == mapping.TURBO_TOGGLE else 0
        if role == self.RemappedRole:
            return target != key or bool(frequency)
        if role == self.EditableRole:
            return target == key or target in mapping.XINPUT_TARGETS
        return None

    def setData(self, index, value, role=Qt.EditRole):
        role_setters = {
            self.TargetIndexRole: self.setTarget,
            self.TurboRole: self.setTurbo,
            self.TurboModeRole: self.setTurboMode,
        }
        setter = role_setters.get(role)
        if setter is None:
            return False
        setter(index.row(), int(value))
        return True

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

    def _write(self, row, target_index=None, turbo=None, turbo_mode=None):
        entry = self._row(row)
        current_target = self.data(self.index(row, 0), self.TargetIndexRole)
        current_mode = self.data(self.index(row, 0), self.TurboModeRole)
        # After the reads above, because asking for a writable config is what
        # drops their cached decode -- see `ProfileModel.edited`.
        config = self._profile.edited()
        if entry is None or config is None:
            return
        key = entry[0]
        current_turbo = entry[3]

        target_index = current_target if target_index is None else target_index
        turbo = current_turbo if turbo is None else turbo
        turbo_mode = current_mode if turbo_mode is None else turbo_mode

        target = None if target_index == 0 else TARGETS[target_index]
        config.set_mapping(key, target, TURBO_MODES[turbo_mode][1], turbo)
        top_left = self.index(row, 0)
        self.dataChanged.emit(top_left, top_left)
        self._profile.markChanged()

    @Slot(int, int)
    def setTarget(self, row, target_index):
        self._write(row, target_index=max(0, min(len(TARGETS) - 1, int(target_index))))

    @Slot(int, int)
    def setTurbo(self, row, frequency):
        self._write(row, turbo=max(0, min(TURBO_MAX_HZ, int(frequency))))

    @Slot(int, int)
    def setTurboMode(self, row, mode_index):
        self._write(row, turbo_mode=max(0, min(len(TURBO_MODES) - 1, int(mode_index))))

    @Slot(int, result=int)
    def turboAt(self, row):
        """The turbo rate on a row, without going through a delegate."""
        entry = self._row(row)
        return entry[3] if entry is not None else 0

    @Slot(int, result=str)
    def targetAt(self, row):
        """What a row currently sends, without going through a delegate."""
        entry = self._row(row)
        return entry[1] if entry is not None else ""

    @Slot(str, result=int)
    def rowForKey(self, key):
        try:
            return mapping.APEX5_KEYS.index(key)
        except ValueError:
            return -1

    def refresh(self):
        self._decoded.clear()
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self.rowCount() - 1, 0))


# What pressing a macro's key does, in the order the picker shows them.
MACRO_TYPES = [("Once per press", mapping.MACRO_ONCE),
               ("While held", mapping.MACRO_WHILE_HELD),
               ("Toggle", mapping.MACRO_TOGGLE)]

MACRO_EVENT_LABELS = {mapping.MACRO_PRESS: "press",
                      mapping.MACRO_RELEASE: "release",
                      mapping.MACRO_HOLD: "hold"}

# Long enough for a combo nobody would type out by hand, short enough that a
# forgotten recording ends by itself. Stop ends it sooner.
RECORD_SECONDS = 30.0


@QmlElement
class MacroModel(QAbstractListModel):
    """The macros stored in the open profile, one row each.

    The pad plays these itself, so a row is a piece of the profile blob and
    not something this app runs: what is here is an editor, plus the recorder
    that fills a row in.
    """

    KeyRole = Qt.UserRole + 1
    LabelRole = Qt.UserRole + 2
    TypeRole = Qt.UserRole + 3
    IntervalRole = Qt.UserRole + 4
    StepCountRole = Qt.UserRole + 5
    DurationRole = Qt.UserRole + 6
    StepsRole = Qt.UserRole + 7

    countChanged = Signal()
    recordingChanged = Signal()
    refused = Signal(str)
    recordRequested = Signal(float)      # matches the worker's slot

    def __init__(self, profile):
        super().__init__(profile)
        self._profile = profile
        self._recording = False
        self._record_key = ""
        self._decoded = Decodes(profile)
        # What the view is currently showing, so `refresh` can tell a profile
        # whose macros differ from one whose macros are the same.
        self._shown = []

    def roleNames(self):
        return {
            self.KeyRole: b"key",
            self.LabelRole: b"label",
            self.TypeRole: b"typeIndex",
            self.IntervalRole: b"interval",
            self.StepCountRole: b"stepCount",
            self.DurationRole: b"duration",
            self.StepsRole: b"steps",
        }

    def _macros(self):
        """The stored macros, decoded once per edit. Read-only -- see below.

        `rowCount` is on this path, and Qt asks a list model for its row count
        far more often than a page changes; `data` was on it once per role per
        row. Decoding the macro page is the most expensive read in this file --
        538 bytes, parsed into a list of dicts of lists.
        """
        config = self._profile.config
        return self._decoded(
            "macros", lambda: config.macros() if config is not None else [])

    def _editable_macros(self):
        """A copy, for the three slots that edit what they have just read.

        Shallow per macro is enough: they set a field on one macro or drop one
        from the list, and nothing here reaches into a macro's steps.
        """
        return [dict(macro) for macro in self._macros()]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._macros())

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._macros())

    @Property(int, constant=True)
    def slots(self):
        return mapping.MACRO_SLOTS

    @Property(int, constant=True)
    def stepBudget(self):
        return mapping.MACRO_STEP_BUDGET

    @Property(int, notify=countChanged)
    def stepsUsed(self):
        return sum(len(macro["steps"]) for macro in self._macros())

    @Property("QStringList", constant=True)
    def typeNames(self):
        return [label for label, _value in MACRO_TYPES]

    @Property("QStringList", constant=True)
    def triggerKeys(self):
        """Every key that can run a macro, as the shell labels them.

        All of them, unlike the remap targets: a macro's trigger key is read by
        the pad, so a paddle with no XInput id runs one perfectly well. The
        keys a macro may *press* are the smaller set, and the backend refuses
        the rest.
        """
        return [key_label(key) for key in mapping.APEX5_KEYS]

    @Property(int, constant=True)
    def intervalMax(self):
        return mapping.MACRO_INTERVAL_MAX

    @Property(bool, notify=recordingChanged)
    def recording(self):
        return self._recording

    @Property(str, notify=recordingChanged)
    def recordingKey(self):
        return key_label(self._record_key) if self._record_key else ""

    @Property(bool, notify=countChanged)
    def canAdd(self):
        return (self._profile.config is not None
                and len(self._macros()) < mapping.MACRO_SLOTS)

    def data(self, index, role=Qt.DisplayRole):
        macros = self._macros()
        row = index.row()
        if not 0 <= row < len(macros):
            return None
        macro = macros[row]
        if role == self.KeyRole:
            return macro["key"]
        if role in (self.LabelRole, Qt.DisplayRole):
            return key_label(macro["key"])
        if role == self.TypeRole:
            return next((i for i, (_l, v) in enumerate(MACRO_TYPES)
                         if v == macro["type"]), 0)
        if role == self.IntervalRole:
            return macro["interval"] or 0
        if role == self.StepCountRole:
            return len(macro["steps"])
        if role == self.DurationRole:
            return sum(step["delay"] for step in macro["steps"])
        if role == self.StepsRole:
            return self.stepsAt(row)
        return None

    @Slot(int, result="QVariantList")
    def stepsAt(self, row):
        """One macro's steps, ready to list."""
        macros = self._macros()
        if not 0 <= row < len(macros):
            return []
        return [{"delay": step["delay"],
                 "key": key_label(step["key"]) if isinstance(step["key"], str)
                        else str(step["key"]),
                 "event": MACRO_EVENT_LABELS.get(step["event"], str(step["event"]))}
                for step in macros[row]["steps"]]

    def _write(self, macros):
        config = self._profile.edited()
        if config is None:
            return False
        try:
            config.set_macros(macros)
        except (ValueError, mapping.ProtocolError) as exc:
            self.refused.emit(str(exc))
            return False
        self.refresh()
        self._profile.markChanged()
        return True

    @Slot(int, int)
    def setType(self, row, type_index):
        macros = self._editable_macros()
        if not 0 <= row < len(macros):
            return
        index = max(0, min(len(MACRO_TYPES) - 1, int(type_index)))
        macros[row]["type"] = MACRO_TYPES[index][1]
        self._write(macros)

    @Slot(int, int)
    def setInterval(self, row, milliseconds):
        macros = self._editable_macros()
        if not 0 <= row < len(macros):
            return
        macros[row]["interval"] = max(0, min(mapping.MACRO_INTERVAL_MAX,
                                             int(milliseconds)))
        self._write(macros)

    @Slot(int)
    def remove(self, row):
        """Drop a macro and give its key back to itself."""
        macros = self._macros()
        if not 0 <= row < len(macros):
            return
        key = macros[row]["key"]
        config = self._profile.edited()
        try:
            config.clear_macro(key)
        except (ValueError, KeyError, mapping.ProtocolError) as exc:
            self.refused.emit(str(exc))
            return
        self.refresh()
        self._profile.keys.refresh()
        self._profile.markChanged()

    # -- recording ---------------------------------------------------------

    @Slot(int)
    def record(self, key_index):
        """Start recording, for the key at `key_index` in `triggerKeys`."""
        if self._recording or self._profile.config is None:
            return
        if not 0 <= key_index < len(mapping.APEX5_KEYS):
            return
        self._record_key = mapping.APEX5_KEYS[key_index]
        self._recording = True
        self.recordingChanged.emit()
        self.recordRequested.emit(RECORD_SECONDS)

    @Slot(list)
    def recorded(self, steps):
        """Steps back from the worker. An empty list means nothing was played."""
        key, self._record_key = self._record_key, ""
        self._recording = False
        self.recordingChanged.emit()
        config = self._profile.edited()
        if config is None or not key:
            return
        if not steps:
            self.refused.emit(
                "Nothing was recorded. If another program has taken the pad "
                "over, its evdev node stops reporting — turn third-party "
                "control off on the Controller page and try again.")
            return
        try:
            config.set_macro(key, steps)
        except (ValueError, mapping.ProtocolError) as exc:
            self.refused.emit(str(exc))
            return
        self.refresh()
        self._profile.keys.refresh()
        self._profile.markChanged()

    def refresh(self):
        """Say what actually moved, rather than rebuilding the page every time.

        `ProfileModel._open` calls this for every profile read, and a reset
        destroys and rebuilds every delegate attached -- the same defect
        `DevicesModel` had against the sidebar picker, where it cost the whole
        window its frame rate twice a minute. Most reads do not change the
        macros at all: opening the profile the pad is already running, or
        re-reading after a write that touched the key table, both leave this
        region byte-identical.

        So: nothing when nothing moved, `dataChanged` when the same rows hold
        different values -- which updates delegates in place -- and a reset only
        when the number of rows really has changed, which is the one case a view
        cannot absorb any other way.
        """
        self._decoded.clear()
        macros = self._macros()
        if macros == self._shown:
            return
        if len(macros) == len(self._shown):
            self._shown = list(macros)
            self.dataChanged.emit(self.index(0, 0), self.index(len(macros) - 1, 0))
            return
        self.beginResetModel()
        self._shown = list(macros)
        self.endResetModel()
        self.countChanged.emit()


@QmlElement
class ProfileListModel(QAbstractListModel):
    """The pad's profile slots, and the cache of what was read from each."""

    SlotRole = Qt.UserRole + 1
    TitleRole = Qt.UserRole + 2
    LoadedRole = Qt.UserRole + 3
    ActiveRole = Qt.UserRole + 4
    CurrentRole = Qt.UserRole + 5
    DirtyRole = Qt.UserRole + 6

    countChanged = Signal()
    activeChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots = []          # [{title, blob}]
        self._pending = set()     # reads asked for, not yet arrived
        self._active = -1
        self._current = -1
        self._dirty_slot = -1

    def roleNames(self):
        return {
            self.SlotRole: b"slot",
            self.TitleRole: b"title",
            self.LoadedRole: b"loaded",
            self.ActiveRole: b"isActive",
            self.CurrentRole: b"isCurrent",
            self.DirtyRole: b"dirty",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._slots)

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._slots)

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._slots):
            return None
        entry = self._slots[row]
        if role == self.SlotRole:
            return row
        if role in (self.TitleRole, Qt.DisplayRole):
            return entry["title"] or f"Profile {row + 1}"
        if role == self.LoadedRole:
            return entry["blob"] is not None
        if role == self.ActiveRole:
            return row == self._active
        if role == self.CurrentRole:
            return row == self._current
        if role == self.DirtyRole:
            return row == self._dirty_slot
        return None

    def setCount(self, count):
        self.beginResetModel()
        self._slots = [{"title": "", "blob": None} for _ in range(count)]
        self._pending.clear()
        self.endResetModel()
        self.countChanged.emit()

    def _touch(self, row):
        if 0 <= row < len(self._slots):
            index = self.index(row, 0)
            self.dataChanged.emit(index, index)

    def stored(self, cfg_id):
        if 0 <= cfg_id < len(self._slots):
            return self._slots[cfg_id]["blob"]
        return None

    def setStored(self, cfg_id, blob, title=None):
        if not 0 <= cfg_id < len(self._slots):
            return
        self._slots[cfg_id]["blob"] = bytes(blob)
        if title is not None:
            self._slots[cfg_id]["title"] = title
        self._pending.discard(cfg_id)
        self._touch(cfg_id)

    def setTitle(self, cfg_id, title):
        if 0 <= cfg_id < len(self._slots):
            self._slots[cfg_id]["title"] = title
            self._touch(cfg_id)

    def forget(self):
        """Drop cached profiles so the open one is re-read from the pad."""
        self.beginResetModel()
        for entry in self._slots:
            entry["blob"] = None
        self._pending.clear()
        self.endResetModel()

    def isPending(self, cfg_id):
        return cfg_id in self._pending

    def markPending(self, cfg_id):
        self._pending.add(cfg_id)

    @Property(int, notify=activeChanged)
    def active(self):
        return self._active

    def setActive(self, cfg_id):
        previous, self._active = self._active, int(cfg_id)
        self._touch(previous)
        self._touch(self._active)
        self.activeChanged.emit()

    @Property(int, notify=activeChanged)
    def current(self):
        return self._current

    def setCurrent(self, cfg_id):
        previous, self._current = self._current, int(cfg_id)
        self._touch(previous)
        self._touch(self._current)

    def setDirtySlot(self, cfg_id):
        previous, self._dirty_slot = self._dirty_slot, int(cfg_id)
        if previous != self._dirty_slot:
            self._touch(previous)
            self._touch(self._dirty_slot)


@QmlElement
class ProfileModel(QObject):
    """The open profile, and the edits held against it.

    Edits live only in the open config, so selecting another slot drops them --
    the same behaviour the widget app had.
    """

    cfgIdChanged = Signal()
    titleChanged = Signal()
    dirtyChanged = Signal()
    loadedChanged = Signal()

    # These mirror the worker's slot signatures exactly, so wiring is a
    # straight connect with no adapter in between.
    writeRequested = Signal(int, bytes, bytes, bool)   # cfg_id, blob, previous, save
    loadRequested = Signal(int)
    restoreFailed = Signal(str)
    saveRefused = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._edited = None
        self._cfg_id = -1
        self._saved = True
        self._generation = 0
        self._dirty = False
        self._slots = ProfileListModel(self)
        self._keys = KeyMapModel(self)
        self._macros = MacroModel(self)
        self._vibration = VibrationModel(self)
        self._triggers = TriggerModel(self)
        self._sticks = StickModel(self)
        self._motion = MotionModel(self)

    # `config` is a plain attribute, not a Q_PROPERTY: MappingConfig is not a
    # QObject and has no business crossing into QML. The sub-models reach it
    # from Python only.
    @property
    def config(self):
        """The open config, to read from. See `edited` for writing to it."""
        return self._edited

    @property
    def generation(self):
        """How many times the open profile's bytes may have moved.

        What `Decodes` memoises against. It is deliberately an over-estimate --
        it counts writes handed out, not writes that changed anything -- because
        a cache that is dropped too often is slow and one that is kept too long
        is wrong.
        """
        return self._generation

    def edited(self):
        """The open config, to write to. Returns None when none is open.

        Handing the config out for writing is what invalidates every decode
        cached against it, so a mutator that goes through here cannot forget to
        invalidate. Readers use `config`; anything about to call a `set_*` uses
        this. The difference is load-bearing rather than stylistic, and a
        mutator that reads through `config` instead will leave stale values on
        screen until the next unrelated edit.
        """
        self._generation += 1
        return self._edited

    def _replace(self, config):
        """Swap the open profile out. Nothing else may assign `_edited`.

        The generation moves *before* any notification goes out, because the
        emissions below reach QML synchronously: a binding that re-read during
        `_open` would otherwise be served the previous profile's cached decode
        as though it were current.
        """
        self._edited = config
        self._generation += 1

    @Property(ProfileListModel, constant=True)
    def slots(self):
        return self._slots

    @Property(KeyMapModel, constant=True)
    def keys(self):
        return self._keys

    @Property(MacroModel, constant=True)
    def macros(self):
        return self._macros

    @Property(VibrationModel, constant=True)
    def vibration(self):
        return self._vibration

    @Property(TriggerModel, constant=True)
    def triggers(self):
        return self._triggers

    @Property(StickModel, constant=True)
    def sticks(self):
        return self._sticks

    @Property(MotionModel, constant=True)
    def motion(self):
        return self._motion

    @Property(int, notify=cfgIdChanged)
    def cfgId(self):
        return self._cfg_id

    @Property(bool, notify=loadedChanged)
    def loaded(self):
        return self._edited is not None

    @Property(bool, notify=dirtyChanged)
    def dirty(self):
        """Whether the open profile differs from what the pad last gave us.

        **A field, and it used to be an 840-byte compare per read.** Derived
        state is still the right design -- nothing has to remember to set a flag
        -- but deriving it inside the getter meant every reader paid for it, and
        the readers are not few: `hint` consults it, `ProfileFooter` reads it
        three times, and the footer is on seven pages that are all alive at
        once. `_recompute_dirty` does the comparison once, where the bytes move.
        """
        return self._dirty

    def _recompute_dirty(self):
        self._dirty = (self._edited is not None and self._cfg_id >= 0
                       and bytes(self._edited.blob)
                       != self._slots.stored(self._cfg_id))
        return self._dirty

    @Property(bool, notify=cfgIdChanged)
    def canSaveToFlash(self):
        """Whether committing to flash would reach the profile being edited.

        Command 166 carries a version and nothing else -- the SDK's
        SaveCurrentMappingConfigCommandFactory sets only array[5..6], while the
        slot-addressed variant is a different command (171, array[7] = cfgId).
        So 166 commits whichever config the pad is *running*. Opening a profile
        switches the pad to it, so the two usually agree -- but the pad has its
        own profile button, and the running slot can change underneath the app
        between status reads. Saving then would write the wrong slot to flash
        while reporting success.
        """
        return self._cfg_id >= 0 and self._cfg_id == self._slots.active

    @Property(bool, notify=dirtyChanged)
    def saveNeeded(self):
        """Applied to the pad's memory, but not committed to flash.

        Distinct from `dirty`, and the reason saving is not simply "dirty" as
        well: once a change has been applied there is nothing left to apply,
        but it is still only in working memory and dies when the pad sleeps.
        Binding the save button to `dirty` made it impossible to keep a change
        you had just applied.
        """
        return self._edited is not None and not self._saved

    @Property(str, notify=titleChanged)
    def title(self):
        return self._edited.title if self._edited else ""

    @title.setter
    def title(self, value):
        value = str(value)[:TITLE_MAX_CHARS]
        if self._edited is None or self._edited.title == value:
            return
        self.edited().title = value
        self.titleChanged.emit()
        self.markChanged()

    @Property(int, constant=True)
    def titleMaxChars(self):
        return TITLE_MAX_CHARS

    @Property(str, notify=dirtyChanged)
    def hint(self):
        if self._edited is None:
            return "Reading this profile from the pad…"
        if self.dirty:
            if not self.canSaveToFlash:
                return ("Unsaved changes. Apply takes effect now. To save it, "
                        "switch the pad to this profile first — the pad "
                        "commits whichever profile it is running.")
            return ("Unsaved changes. Apply takes effect now; "
                    "saving also keeps it across a power cycle.")
        if self.saveNeeded:
            if not self.canSaveToFlash:
                return ("Applied, but only to the pad's memory. Switch the pad "
                        "to this profile to save it — the pad commits whichever "
                        "profile it is running.")
            return ("Applied, but only to the pad's memory — it will be lost "
                    "when the pad sleeps. Save to keep it.")
        return "Matches what is on the pad."

    # -- selection and loading ---------------------------------------------

    @Slot(int)
    def setSlotCount(self, count):
        self._slots.setCount(count)

    @Slot(int)
    def select(self, cfg_id):
        cfg_id = int(cfg_id)
        if cfg_id < 0:
            return
        # Re-selecting the open profile would rebuild it from the cached blob
        # and throw away every unsaved edit -- silently, with no device traffic
        # to hint at it. A radio delegate emits clicked() even for the row that
        # is already checked, and the keyboard reaches it too, so this is one
        # stray click away rather than a corner case.
        #
        # The "is it cached" test is load-bearing: `forget()` clears the cache
        # and then re-selects the same id on purpose, and a bare id comparison
        # would turn "Reload from pad" into a no-op.
        if (cfg_id == self._cfg_id and self._edited is not None
                and self._slots.stored(cfg_id) is not None):
            return
        self._cfg_id = cfg_id
        self._slots.setCurrent(cfg_id)
        self.cfgIdChanged.emit()
        blob = self._slots.stored(cfg_id)
        if blob is not None:
            self._open(blob)
            return
        # Not read yet. Reading is expensive and switches the pad, so never ask
        # twice for the same profile.
        self._replace(None)
        self._recompute_dirty()
        self.loadedChanged.emit()
        # The title has just become "" -- without this the name field goes on
        # showing the profile the user has navigated away from.
        self.titleChanged.emit()
        self.dirtyChanged.emit()
        if not self._slots.isPending(cfg_id):
            self._slots.markPending(cfg_id)
            self.loadRequested.emit(cfg_id)

    def _open(self, blob):
        self._replace(mapping.MappingConfig(bytearray(blob), self._cfg_id))
        # Freshly read from the pad, so there is nothing waiting to be
        # committed to flash.
        self._saved = True
        self.loadedChanged.emit()
        self.titleChanged.emit()
        self._keys.refresh()
        self._macros.refresh()
        self._vibration.refresh()
        self._triggers.refresh()
        self._sticks.refresh()
        self._motion.refresh()
        self.markChanged()

    @Slot()
    def forget(self):
        """Drop the cache so the open profile is re-read from the pad.

        Nothing is selected when neither a profile nor the pad's active slot is
        known yet -- on a cold start that arrives moments later from the status
        read, and `setActive` opens it. Falling back to slot 0 here would read
        the wrong profile and switch the pad away from the one in use.
        """
        self._slots.forget()
        target = self._cfg_id if self._cfg_id >= 0 else self._slots.active
        if target >= 0:
            self.select(target)

    @Slot(int, bytes, str)
    def profileLoaded(self, cfg_id, blob, title):
        self._slots.setStored(cfg_id, blob, title)
        if cfg_id == self._cfg_id:
            self._open(blob)

    @Slot(int)
    def setActive(self, cfg_id):
        self._slots.setActive(cfg_id)
        self.cfgIdChanged.emit()
        # The first time we learn what the pad is running, open that one. It is
        # already loaded on the pad, so reading it switches nothing -- whereas
        # opening slot 0 by default would switch the pad away from the profile
        # in use and back again, for nothing.
        if self._cfg_id < 0 and cfg_id >= 0:
            self.select(cfg_id)

    # -- editing -----------------------------------------------------------

    def markChanged(self):
        """Recompute the derived dirty state. Called by every sub-model.

        Still emits unconditionally rather than only on a change: `saveNeeded`
        and `hint` are notified by the same signal and both move without `dirty`
        moving -- `confirmWritten` is exactly that case.
        """
        self._slots.setDirtySlot(self._cfg_id if self._recompute_dirty() else -1)
        self.dirtyChanged.emit()

    @Slot()
    def resetAll(self):
        config = self.edited()
        if config is None:
            return
        for key in mapping.APEX5_KEYS:
            config.set_mapping(key, None)
        self._keys.refresh()
        self.markChanged()

    @Slot(bool)
    def write(self, save):
        if self._edited is None or self._cfg_id < 0:
            return
        # Refuse rather than commit the wrong slot -- see canSaveToFlash. The
        # guard is here and not only in the view because `write` is a public
        # slot; a view is not the last line of defence for something that
        # writes flash.
        if save and not self.canSaveToFlash:
            self.saveRefused.emit(
                "The pad commits whichever profile it is running, so this "
                "would save the wrong one. Switch the pad to this profile "
                "first, then save.")
            return
        self.writeRequested.emit(self._cfg_id, bytes(self._edited.blob),
                                 self._slots.stored(self._cfg_id) or b"",
                                 bool(save))

    @Slot(int, bool)
    def confirmWritten(self, cfg_id, saved=False):
        """The pad accepted the write, so it is now the reference copy.

        `saved` says whether it also reached flash; if it did not, the change
        is only in the pad's working memory and dies with the next sleep.
        """
        if self._edited is not None and cfg_id == self._cfg_id:
            self._slots.setStored(cfg_id, bytes(self._edited.blob),
                                  self._edited.title)
            self._saved = bool(saved)
            self.markChanged()

    # -- backup and restore ------------------------------------------------

    @Slot(str)
    def backup(self, where):
        if self._edited is None:
            return
        blob = self._slots.stored(self._cfg_id) or bytes(self._edited.blob)
        with open(local_path(where), "wb") as fh:
            fh.write(blob)

    @Slot(str)
    def restore(self, where):
        if self._edited is None:
            return
        try:
            with open(local_path(where), "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            self.restoreFailed.emit(str(exc))
            return
        expected = len(self._edited.blob)
        if len(blob) != expected:
            self.restoreFailed.emit(
                f"That file is {len(blob)} bytes; this pad's profiles "
                f"are {expected}. Refusing to write it.")
            return
        self._replace(mapping.MappingConfig(bytearray(blob), self._cfg_id))
        self.titleChanged.emit()
        self._keys.refresh()
        self._macros.refresh()
        self._vibration.refresh()
        self._triggers.refresh()
        self._sticks.refresh()
        self._motion.refresh()
        self.markChanged()
