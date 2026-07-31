# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""RGB lighting state.

The pad has no animation generator -- see flydigi/lighting.py -- so choosing an
effect rewrites the frames it plays. That is why every colour or effect change
here regenerates the blob rather than setting a mode byte and stopping.
"""
from PySide6.QtCore import (Property, QAbstractListModel, QModelIndex, QObject,
                            Qt, Signal, Slot)
from PySide6.QtQml import QmlElement

from flydigi import lighting

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# Space Station's own effect ids and names, with how many colours each consumes.
EFFECTS = [
    ("Static", lighting.EFFECT_STATIC_SINGLE, 1),
    ("Static, multiple colours", lighting.EFFECT_STATIC_MULTI, lighting.MAX_COLOURS),
    ("Breathing", lighting.EFFECT_BREATHING, lighting.MAX_COLOURS),
    ("Flow", lighting.EFFECT_STREAMING, lighting.MAX_COLOURS),
    ("Rotation", lighting.EFFECT_ROTATION, lighting.MAX_COLOURS),
    ("Wave", lighting.EFFECT_WAVE, lighting.MAX_COLOURS),
    ("Flash", lighting.EFFECT_FLASH, lighting.MAX_COLOURS),
    ("Rainbow", lighting.EFFECT_RAINBOW, 0),
    ("Off", lighting.EFFECT_OFF, 0),
]

# The stored mode byte uses Space Station's numbering, not ours, so on load we
# cannot say which of our effects produced what is on the pad. Rather than
# claim one, the picker starts here: leave the frames alone until an effect is
# actually chosen.
KEEP_CURRENT = "(keep what is on the pad)"

EFFECT_NAMES = [KEEP_CURRENT] + [name for name, _id, _n in EFFECTS]

# A slower cycle is a larger stored number, which reads backwards on a control
# labelled "speed", so views show the inverse. The mapping lives here rather
# than in a view so both UIs invert it the same way.
CYCLE_MIN, CYCLE_MAX = 1, 30


def invert_cycle(value):
    """Map between stored cycle time and a speed control, in either direction."""
    return max(CYCLE_MIN, min(CYCLE_MAX, CYCLE_MAX + CYCLE_MIN - int(value)))


def to_hex(colour):
    return "#{:02x}{:02x}{:02x}".format(*colour)


def from_hex(text):
    text = str(text).lstrip("#")
    # QML hands back "#AARRGGBB" whenever the colour carries an alpha channel.
    # The pad has no alpha, so drop it rather than reading the wrong bytes.
    if len(text) == 8:
        text = text[2:]
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


@QmlElement
class ColourListModel(QAbstractListModel):
    """The up-to-five colours an effect cycles through.

    Colours stay as RGB tuples in Python and are converted to "#rrggbb" at the
    boundary, which QML reads as a colour directly and QtWidgets can parse.
    """

    ColourRole = Qt.UserRole + 1

    countChanged = Signal()
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._colours = [lighting.DEFAULT_COLOUR]
        self._allowed = 1

    def roleNames(self):
        return {self.ColourRole: b"colour"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._colours)

    def data(self, index, role=Qt.DisplayRole):
        if not 0 <= index.row() < len(self._colours):
            return None
        if role in (self.ColourRole, Qt.DisplayRole):
            return to_hex(self._colours[index.row()])
        return None

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._colours)

    @Property(int, notify=countChanged)
    def allowed(self):
        """How many colours the chosen effect uses. Rainbow and Off use none."""
        return self._allowed

    @Property(bool, notify=countChanged)
    def canAdd(self):
        return len(self._colours) < self._allowed

    @Property(bool, notify=countChanged)
    def canRemove(self):
        return len(self._colours) > 1 and self._allowed > 1

    def colours(self):
        return list(self._colours)

    def setColours(self, colours):
        self.beginResetModel()
        self._colours = [tuple(c) for c in colours] or [lighting.DEFAULT_COLOUR]
        self.endResetModel()
        self.countChanged.emit()

    def setAllowed(self, allowed):
        """Trim if the newly chosen effect takes fewer colours than are shown."""
        self._allowed = int(allowed)
        trimmed = False
        if self._allowed and len(self._colours) > self._allowed:
            self.beginResetModel()
            del self._colours[self._allowed:]
            self.endResetModel()
            trimmed = True
        self.countChanged.emit()
        return trimmed

    @Slot(int, str)
    def setColour(self, row, text):
        if not 0 <= row < len(self._colours):
            return
        colour = from_hex(text)
        if self._colours[row] == colour:
            return
        self._colours[row] = colour
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [self.ColourRole])
        self.changed.emit()

    @Slot()
    def add(self):
        if not self.canAdd:
            return
        row = len(self._colours)
        self.beginInsertRows(QModelIndex(), row, row)
        self._colours.append(lighting.suggest_colour(self._colours))
        self.endInsertRows()
        self.countChanged.emit()
        self.changed.emit()

    @Slot()
    def remove(self):
        if not self.canRemove:
            return
        row = len(self._colours) - 1
        self.beginRemoveRows(QModelIndex(), row, row)
        self._colours.pop()
        self.endRemoveRows()
        self.countChanged.emit()
        self.changed.emit()


@QmlElement
class LightingModel(QObject):
    """One lighting config, edited in memory until written."""

    loadedChanged = Signal()
    dirtyChanged = Signal()
    effectChanged = Signal()
    brightnessChanged = Signal()
    cycleTimeChanged = Signal()
    gripSyncChanged = Signal()
    infoChanged = Signal()

    # (blob, previous, save) -- what the worker's write_lighting slot takes.
    writeRequested = Signal(bytes, bytes, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stored = None       # bytes as last read from the pad
        self._edited = None
        self._effect_index = 0    # 0 is KEEP_CURRENT
        self._saved = True
        self._colours = ColourListModel(self)
        self._colours.changed.connect(self._regenerate)
        self._info = ""

    @Property(ColourListModel, constant=True)
    def colours(self):
        return self._colours

    @Property(bool, notify=loadedChanged)
    def loaded(self):
        return self._edited is not None

    @Property(bool, notify=dirtyChanged)
    def dirty(self):
        return (self._edited is not None
                and bytes(self._edited.blob) != self._stored)

    @Property(bool, notify=dirtyChanged)
    def saveNeeded(self):
        """Applied to the pad's memory, but not committed to flash.

        See ProfileModel.saveNeeded: binding the save button to `dirty` alone
        means applying a change immediately makes it impossible to keep.
        """
        return self._edited is not None and not self._saved

    @Property(str, notify=infoChanged)
    def info(self):
        return self._info

    @Property("QStringList", constant=True)
    def effectNames(self):
        return list(EFFECT_NAMES)

    @Property(int, notify=effectChanged)
    def effect(self):
        """Index into `effectNames`; 0 leaves the pad's frames alone."""
        return self._effect_index

    @effect.setter
    def effect(self, value):
        value = int(value)
        if self._effect_index == value:
            return
        self._effect_index = value
        self.effectChanged.emit()
        self._regenerate()

    @Property(int, notify=brightnessChanged)
    def brightness(self):
        return self._edited.brightness if self._edited else 0

    @brightness.setter
    def brightness(self, value):
        if self._edited is None:
            return
        value = max(0, min(lighting.BRIGHTNESS_MAX, int(value)))
        if self._edited.brightness == value:
            return
        self._edited.brightness = value
        self.brightnessChanged.emit()
        self._mark()

    @Property(int, constant=True)
    def brightnessMax(self):
        return lighting.BRIGHTNESS_MAX

    @Property(int, notify=cycleTimeChanged)
    def cycleTime(self):
        """As stored: bigger is slower."""
        return self._edited.cycle_time if self._edited else CYCLE_MIN

    @cycleTime.setter
    def cycleTime(self, value):
        if self._edited is None:
            return
        value = max(CYCLE_MIN, min(CYCLE_MAX, int(value)))
        if self._edited.cycle_time == value:
            return
        self._edited.cycle_time = value
        self.cycleTimeChanged.emit()
        self._mark()

    @Property(int, notify=cycleTimeChanged)
    def speed(self):
        """The same number the other way up, so right is faster."""
        return invert_cycle(self.cycleTime)

    @speed.setter
    def speed(self, value):
        self.cycleTime = invert_cycle(value)

    @Property(int, constant=True)
    def speedMin(self):
        return CYCLE_MIN

    @Property(int, constant=True)
    def speedMax(self):
        return CYCLE_MAX

    @Property(bool, notify=gripSyncChanged)
    def gripSync(self):
        """The lighting's reaction to vibration -- LED-blob byte 9.

        This was bound to byte 2 for its whole life, under the name "React to
        rumble". Byte 2 is inert on this pad; byte 9 is the one that measurably
        dims the ring while a motor runs. See flydigi/lighting.py.
        """
        return bool(self._edited.grip_sync) if self._edited else False

    @gripSync.setter
    def gripSync(self, value):
        if self._edited is None:
            return
        value = bool(value)
        if bool(self._edited.grip_sync) == value:
            return
        self._edited.grip_sync = value
        self.gripSyncChanged.emit()
        self._mark()

    # -- loading and writing -----------------------------------------------

    @Slot(bytes)
    def configLoaded(self, blob):
        self._stored = bytes(blob)
        self._edited = lighting.LedConfig(bytearray(blob))
        # Freshly read, so nothing is waiting to reach flash.
        self._saved = True
        self._effect_index = 0
        # Seed from a lit LED, since frame 0 of a breath is black.
        self._colours.setColours([self._first_lit() or lighting.DEFAULT_COLOUR])
        self._colours.setAllowed(self._max_colours())
        self._info = (
            f"{self._edited.frames} frames of {self._edited.leds_per_frame} LEDs, "
            f"protocol v{self._edited.version:#06x}. The pad has no effect "
            "generator — choosing an effect rewrites the frames it plays.")
        for signal in (self.loadedChanged, self.effectChanged,
                       self.brightnessChanged, self.cycleTimeChanged,
                       self.gripSyncChanged, self.infoChanged):
            signal.emit()
        self._mark()

    def _first_lit(self):
        for frame in range(self._edited.frames):
            for led in self._edited.frame(frame):
                if any(led):
                    return led
        return None

    def _max_colours(self):
        index = self._effect_index - 1
        return EFFECTS[index][2] if 0 <= index < len(EFFECTS) else 1

    def _regenerate(self):
        """Rewrite the frames -- the only thing that changes the look."""
        if self._edited is None:
            return
        self._colours.setAllowed(self._max_colours())
        index = self._effect_index - 1
        if 0 <= index < len(EFFECTS):
            _name, effect, _allowed = EFFECTS[index]
            self._edited.apply_effect(effect, self._colours.colours())
        self._mark()

    def _mark(self):
        self.dirtyChanged.emit()

    @Slot(bool)
    def write(self, save):
        if self._edited is None:
            return
        self.writeRequested.emit(
            bytes(self._edited.blob), self._stored or b"", bool(save))

    @Slot(bool)
    def confirmWritten(self, saved=False):
        """The pad accepted it, so it is now the reference copy.

        `saved` says whether it also reached flash.
        """
        if self._edited is not None:
            self._stored = bytes(self._edited.blob)
            self._saved = bool(saved)
            self._mark()
