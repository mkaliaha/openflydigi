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

from flydigi import mapping

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
# SetForceTrigger command. Only these two are confirmed on hardware; the rest
# of the range is left out rather than guessed at in a UI.
TRIGGER_MODES = [("Off — normal travel", 0),
                 ("Constant resistance", 1)]

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
    ("Shoulders and triggers", ["lb", "rb", "lt", "rt"]),
    ("Sticks", ["thl", "thr"]),
    ("System", ["select", "start", "home"]),
    ("Paddles and extra buttons", ["c", "z", "m1", "m2", "m3", "m4"]),
]

CLUSTER_OF = {key: title for title, keys in KEY_CLUSTERS for key in keys}

# The pad's own short names are what the protocol uses; these are what the
# shell is silkscreened with, or what a person would call the button.
KEY_LABELS = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "lb": "Left bumper", "rb": "Right bumper",
    "lt": "Left trigger", "rt": "Right trigger",
    "thl": "Left stick click", "thr": "Right stick click",
    "select": "Select", "start": "Start", "home": "Home",
    "c": "C", "z": "Z",
    "m1": "M1", "m2": "M2", "m3": "M3", "m4": "M4",
}


def key_label(key):
    return KEY_LABELS.get(key, key.upper())


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

    def _field(self, index):
        config = self._profile.config
        return config.vibration(self._side)[index] if config is not None else 0

    def _set(self, **kwargs):
        config = self._profile.config
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
        config = self._profile.config
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
class TriggerSideModel(QObject):
    """One trigger's stored adaptive effect, dead zone and motor."""

    changed = Signal()

    def __init__(self, profile, side):
        super().__init__(profile)
        self._profile = profile
        self._side = side

    def _effect(self):
        config = self._profile.config
        return config.trigger_effect(self._side) if config is not None else (0, [0] * 10)

    def _set_effect(self, mode=None, start=None, strength=None):
        config = self._profile.config
        if config is None:
            return
        current_mode, params = config.trigger_effect(self._side)
        # Params mirror the live race effect: where resistance begins, then how
        # hard it pushes back. Read-modify-write so setting one keeps the other.
        config.set_trigger_effect(
            self._side,
            current_mode if mode is None else mode,
            [params[0] if start is None else start,
             params[1] if strength is None else strength])
        self.changed.emit()
        self._profile.markChanged()

    @Property(int, notify=changed)
    def effect(self):
        """Index into TRIGGER_MODES, not the stored id."""
        mode = self._effect()[0]
        return next((i for i, (_l, m) in enumerate(TRIGGER_MODES) if m == mode), 0)

    @effect.setter
    def effect(self, value):
        index = max(0, min(len(TRIGGER_MODES) - 1, int(value)))
        self._set_effect(mode=TRIGGER_MODES[index][1])

    @Property(int, notify=changed)
    def start(self):
        return self._effect()[1][0]

    @start.setter
    def start(self, value):
        self._set_effect(start=int(value))

    @Property(int, notify=changed)
    def strength(self):
        return self._effect()[1][1]

    @strength.setter
    def strength(self, value):
        self._set_effect(strength=int(value))

    @Property(int, notify=changed)
    def deadZone(self):
        config = self._profile.config
        return config.trigger_curve(self._side)["zero"] if config is not None else 0

    @deadZone.setter
    def deadZone(self, value):
        config = self._profile.config
        if config is None:
            return
        config.set_trigger_curve(self._side, zero=int(value))
        self.changed.emit()
        self._profile.markChanged()

    @Property(bool, notify=changed)
    def motor(self):
        config = self._profile.config
        return bool(config.trigger_motor(self._side)[0]) if config is not None else False

    @motor.setter
    def motor(self, value):
        config = self._profile.config
        if config is None:
            return
        config.set_trigger_motor(self._side, enabled=bool(value))
        self.changed.emit()
        self._profile.markChanged()

    def refresh(self):
        self.changed.emit()


@QmlElement
class TriggerModel(QObject):
    """The two triggers' per-profile settings."""

    def __init__(self, profile):
        super().__init__(profile)
        self._sides = {side: TriggerSideModel(profile, side) for side in mapping.SIDES}

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

    def _row(self, row):
        """(key, target, mode, frequency) for a row, or None if out of range."""
        if not 0 <= row < len(mapping.APEX5_KEYS):
            return None
        key = mapping.APEX5_KEYS[row]
        config = self._profile.config
        if config is None:
            return key, key, mapping.TURBO_OFF, 0
        target, mode, frequency = config.mapping(key)
        return key, target, mode, frequency

    def data(self, index, role=Qt.DisplayRole):
        entry = self._row(index.row())
        if entry is None:
            return None
        key, target, mode, frequency = entry
        if role == self.KeyRole:
            return key
        if role in (self.LabelRole, Qt.DisplayRole):
            return key_label(key)
        if role == self.ClusterRole:
            return CLUSTER_OF.get(key, "Other")
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
        config = self._profile.config
        if entry is None or config is None:
            return
        key = entry[0]
        current_target = self.data(self.index(row, 0), self.TargetIndexRole)
        current_turbo = entry[3]
        current_mode = self.data(self.index(row, 0), self.TurboModeRole)

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
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(self.rowCount() - 1, 0))


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
        self._slots = ProfileListModel(self)
        self._keys = KeyMapModel(self)
        self._vibration = VibrationModel(self)
        self._triggers = TriggerModel(self)

    # `config` is a plain attribute, not a Q_PROPERTY: MappingConfig is not a
    # QObject and has no business crossing into QML. The sub-models reach it
    # from Python only.
    @property
    def config(self):
        return self._edited

    @Property(ProfileListModel, constant=True)
    def slots(self):
        return self._slots

    @Property(KeyMapModel, constant=True)
    def keys(self):
        return self._keys

    @Property(VibrationModel, constant=True)
    def vibration(self):
        return self._vibration

    @Property(TriggerModel, constant=True)
    def triggers(self):
        return self._triggers

    @Property(int, notify=cfgIdChanged)
    def cfgId(self):
        return self._cfg_id

    @Property(bool, notify=loadedChanged)
    def loaded(self):
        return self._edited is not None

    @Property(bool, notify=dirtyChanged)
    def dirty(self):
        if self._edited is None or self._cfg_id < 0:
            return False
        return bytes(self._edited.blob) != self._slots.stored(self._cfg_id)

    @Property(bool, notify=cfgIdChanged)
    def canSaveToFlash(self):
        """Whether committing to flash would reach the profile being edited.

        Command 166 carries a version and nothing else -- the SDK's
        SaveCurrentMappingConfigCommandFactory sets only array[5..6], while the
        slot-addressed variant is a different command (171, array[7] = cfgId).
        So 166 commits whichever config the pad is *running*. Browsing restores
        the pad deliberately, so the profile being edited is routinely not the
        one running, and saving then would write the wrong slot to flash while
        reporting success.
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
        self._edited.title = value
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
        self._cfg_id = cfg_id
        self._slots.setCurrent(cfg_id)
        self.cfgIdChanged.emit()
        blob = self._slots.stored(cfg_id)
        if blob is not None:
            self._open(blob)
            return
        # Not read yet. Reading is expensive and switches the pad, so never ask
        # twice for the same profile.
        self._edited = None
        self.loadedChanged.emit()
        # The title has just become "" -- without this the name field goes on
        # showing the profile the user has navigated away from.
        self.titleChanged.emit()
        self.dirtyChanged.emit()
        if not self._slots.isPending(cfg_id):
            self._slots.markPending(cfg_id)
            self.loadRequested.emit(cfg_id)

    def _open(self, blob):
        self._edited = mapping.MappingConfig(bytearray(blob), self._cfg_id)
        # Freshly read from the pad, so there is nothing waiting to be
        # committed to flash.
        self._saved = True
        self.loadedChanged.emit()
        self.titleChanged.emit()
        self._keys.refresh()
        self._vibration.refresh()
        self._triggers.refresh()
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
        """Recompute the derived dirty state. Called by every sub-model."""
        self._slots.setDirtySlot(self._cfg_id if self.dirty else -1)
        self.dirtyChanged.emit()

    @Slot()
    def resetAll(self):
        if self._edited is None:
            return
        for key in mapping.APEX5_KEYS:
            self._edited.set_mapping(key, None)
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
        self._edited = mapping.MappingConfig(bytearray(blob), self._cfg_id)
        self.titleChanged.emit()
        self._keys.refresh()
        self._vibration.refresh()
        self._triggers.refresh()
        self.markChanged()
