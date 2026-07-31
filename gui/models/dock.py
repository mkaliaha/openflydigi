# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The charging dock's own state: its four switches and its lighting.

A device of its own, not an accessory of the pad -- and there may be more than
one, so nothing here says "the dock". Which dock this is showing comes from
`gui/models/devices.py`; this holds what that one answered.

**Lighting is frames, not a mode byte.** The dock has no effect generator: it
plays what it is given, so choosing an effect computes fifty frames of 162 LEDs
here and uploads about 24 kB in 487 packets, which takes a few seconds. That is
why writing has a busy state and a progress signal, where the pad's lighting
needs neither. `flydigi/charger.py` owns the arithmetic.

Two of Flydigi's ten modes are missing on purpose. `default` needs a file their
installer ships and this repository does not have, and `custom` needs frames
from an image, which is the other half of the dock work and not built yet.
"""
from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import charger

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# Space Station's dropdown order, minus the two that cannot be computed, plus
# `solid` -- which is in the firmware's enum, absent from their dropdown, works,
# and is an obvious thing to want. Same list as `tools/flydigi-charger`.
MODES = [
    ("Off", charger.MODE_CLOSE),
    ("Solid", charger.MODE_SOLID),
    ("Breath", charger.MODE_BREATH),
    ("Diagonal flow", charger.MODE_DIAGONAL_FLOW),
    ("Gradient", charger.MODE_GRADIENT),
    ("Wave gradient", charger.MODE_WAVE_GRADIENT),
    ("Rainbow", charger.MODE_RAINBOW),
    ("Pulse", charger.MODE_PULSE),
]
MODE_NAMES = [name for name, _id in MODES]

DIRECTIONS = [("Right", charger.DIR_RIGHT), ("Left", charger.DIR_LEFT),
              ("Down", charger.DIR_DOWN), ("Up", charger.DIR_UP)]
DIRECTION_NAMES = [name for name, _id in DIRECTIONS]

# How many colours each mode's generator actually reads, and which read a
# direction. Straight off the generators, so the page can grey out a control
# that would do nothing rather than accept a setting the dock ignores.
USES_COLOUR = {charger.MODE_SOLID: 1, charger.MODE_BREATH: 1,
               charger.MODE_PULSE: 1, charger.MODE_DIAGONAL_FLOW: 2,
               charger.MODE_WAVE_GRADIENT: 2}
USES_DIRECTION = (charger.MODE_RAINBOW, charger.MODE_WAVE_GRADIENT)

# The switches, in the order the page shows them, with Flydigi's own labels and
# what each actually does.
# The switches, in the order the page shows them, with what each actually does
# and Flydigi's own name for it kept alongside -- their labels are what Space
# Station shows and what a search finds, and "Intelligent start" says nothing
# whatever about turning two devices' lighting off.
SWITCHES = [
    ("sleep_when_charging", "Sleep while docked",
     "both the pad and the dock go dark while a pad sits in it "
     "— Flydigi call this “Intelligent start”"),
    ("led_sync", "Lighting sync",
     "keep the dock's lighting in step with the pad's"),
    ("close_with_system", "Close when shut down",
     "go dark when the host powers off"),
    ("show_animation_when_charging", "Power display",
     "play the charge animation while a pad is docked"),
]

# The two that sleep-while-docked overrides. Named here rather than in the page
# so the CLI and the app agree about what conflicts with what.
DIMMED_BY_SLEEP = ("led_sync", "show_animation_when_charging")
SWITCH_LABELS = {name: label for name, label, _note in SWITCHES}


def to_hex(colour):
    return "#{:02x}{:02x}{:02x}".format(*colour)


def from_hex(text):
    text = str(text).lstrip("#")
    # QML hands back "#AARRGGBB" whenever the colour carries an alpha channel.
    if len(text) == 8:
        text = text[2:]
    if len(text) != 6:
        return (0, 0, 0)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


@QmlElement
class DockModel(QObject):
    """What the Dock page binds to, for whichever dock is selected."""

    changed = Signal()
    lightingChanged = Signal()
    busyChanged = Signal()
    # Requests out. The selector rides along on every one of them: the page
    # binds to whichever dock is chosen, and a write must not land on the dock
    # that happened to be selected when the worker last looked.
    refreshRequested = Signal(str)
    switchRequested = Signal(str, str, bool)          # selector, name, value
    lightingRequested = Signal(str, dict)             # selector, config

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selector = ""
        self._present = False
        self._info = {}
        self._uid = ""
        self._nickname = ""
        self._mode = charger.MODE_PULSE
        self._brightness = 50
        self._period = 2
        self._direction = charger.DIR_NONE
        self._colours = [charger.BLUE]
        self._docked = None
        self._dock_battery = -1
        self._busy = False
        self._progress = 0.0
        self._error = ""

    # -- which dock --------------------------------------------------------

    @Slot(str)
    def setSelector(self, selector):
        """Point this model at a dock and read it. A no-op for the same one.

        Re-reading on every selection change rather than caching per dock: two
        docks are two devices with their own state, and showing the last one's
        lighting under the new one's name for a second is the kind of wrong a
        person acts on.
        """
        selector = str(selector or "")
        if selector == self._selector:
            return
        self._selector = selector
        self._present = False
        self.changed.emit()
        if selector:
            self.refreshRequested.emit(selector)

    @Property(str, notify=changed)
    def selector(self):
        return self._selector

    @Property(bool, notify=changed)
    def present(self):
        """Whether the selected dock has answered. False before the first read."""
        return self._present

    @Slot()
    def reload(self):
        if self._selector:
            self.refreshRequested.emit(self._selector)

    # -- what came back ----------------------------------------------------

    @Slot(dict)
    def stateReceived(self, state):
        """One whole read: heartbeat, uid, nickname and the LED header."""
        if state.get("selector") and state["selector"] != self._selector:
            # A reply for a dock that is no longer on screen. Dropped rather
            # than shown: the read was started before the picker moved.
            return
        self._present = True
        self._error = ""
        self._info = dict(state.get("info") or {})
        self._uid = state.get("uid") or ""
        self._nickname = state.get("nickname") or ""
        lighting = state.get("lighting") or {}
        if lighting:
            self._mode = int(lighting.get("mode", self._mode))
            self._brightness = int(lighting.get("brightness", self._brightness))
            self._period = int(lighting.get("period", self._period))
            self._direction = int(lighting.get("direction", self._direction))
            colours = lighting.get("colours")
            if colours:
                self._colours = [tuple(c) for c in colours]
        status = state.get("status")
        if status is None:
            self._docked, self._dock_battery = None, -1
        else:
            self._docked = bool(status.get("docked"))
            self._dock_battery = int(status.get("battery", -1))
        self.changed.emit()
        self.lightingChanged.emit()

    @Slot(str)
    def failed(self, message):
        self._error = str(message or "")
        self.changed.emit()

    @Slot(float)
    def progressReceived(self, fraction):
        self._progress = float(fraction)
        self.busyChanged.emit()

    @Slot(bool)
    def writeFinished(self, ok):
        self._busy = False
        self._progress = 0.0
        self.busyChanged.emit()
        if ok and self._selector:
            # Read the header back rather than trusting what was sent: the
            # dock's reply to a write says nothing about what it changed, which
            # is the same reason every device-settings write re-reads.
            self.refreshRequested.emit(self._selector)

    # -- the switches ------------------------------------------------------

    # One property per switch, rather than one `switchValue(name)` a view calls.
    #
    # **A binding on a method never updates.** QML re-evaluates a binding when a
    # property it read changes, and a slot call declares no dependency at all --
    # so `checked: App.dock.switchValue("led_sync")` evaluated once, before the
    # dock had answered, and sat at false for the rest of the session while the
    # dock said otherwise. Four properties notified by `changed` is more lines
    # and is the only shape that works.

    @Slot(str, result=bool)
    def switchValue(self, name):
        """For a caller that has the wire name in hand -- a test, not a binding."""
        return bool(self._info.get(name))

    @Slot(str, bool)
    def setSwitch(self, name, value):
        if not self._selector:
            return
        # Optimistic, then corrected by the read that follows: the page should
        # not feel like it lags a device that answers in milliseconds.
        self._info[name] = bool(value)
        self.changed.emit()
        self.switchRequested.emit(self._selector, str(name), bool(value))

    @Property(bool, notify=changed)
    def sleepWhenCharging(self):
        return bool(self._info.get("sleep_when_charging"))

    @sleepWhenCharging.setter
    def sleepWhenCharging(self, value):
        self.setSwitch("sleep_when_charging", value)

    @Property(bool, notify=changed)
    def ledSync(self):
        return bool(self._info.get("led_sync"))

    @ledSync.setter
    def ledSync(self, value):
        self.setSwitch("led_sync", value)

    @Property(bool, notify=changed)
    def closeWithSystem(self):
        return bool(self._info.get("close_with_system"))

    @closeWithSystem.setter
    def closeWithSystem(self, value):
        self.setSwitch("close_with_system", value)

    @Property(bool, notify=changed)
    def showAnimationWhenCharging(self):
        return bool(self._info.get("show_animation_when_charging"))

    @showAnimationWhenCharging.setter
    def showAnimationWhenCharging(self, value):
        self.setSwitch("show_animation_when_charging", value)

    # -- what the page shows -----------------------------------------------

    @Property(str, notify=changed)
    def firmware(self):
        return self._info.get("firmware") or ""

    @Property(str, notify=changed)
    def uid(self):
        return self._uid

    @Property(str, notify=changed)
    def nickname(self):
        return self._nickname

    @Property(int, notify=changed)
    def deviceType(self):
        return int(self._info.get("device_type", -1))

    @Property(str, notify=changed)
    def model(self):
        return charger.name_for(self._info.get("device_type")) or ""

    @Property(str, notify=changed)
    def dockedState(self):
        """One sentence about what is sitting in it.

        The charge goes through `charger.describe_battery`, which reads the byte
        the way a controller's own battery is read -- 0..5, with 6 meaning
        charging. A bare number here would be the same mistake this app made
        with the pad's own battery for months, and worse: a seated pad is
        charging, so the value it is most likely to carry is the one that would
        print as "battery 6".
        """
        if self._docked is None:
            return "no status report in the last second"
        if not self._docked:
            return "nothing docked"
        if self._dock_battery < 0:
            return "a controller is docked"
        return ("a controller is docked, "
                + charger.describe_battery(self._dock_battery))

    @Property(str, notify=changed)
    def error(self):
        return self._error

    # -- lighting ----------------------------------------------------------

    @Property(int, notify=lightingChanged)
    def modeIndex(self):
        for index, (_name, mode) in enumerate(MODES):
            if mode == self._mode:
                return index
        return -1

    @modeIndex.setter
    def modeIndex(self, index):
        index = int(index)
        if not 0 <= index < len(MODES):
            return
        mode = MODES[index][1]
        if mode == self._mode:
            return
        self._mode = mode
        # Every mode has its own defaults in Space Station -- a period, a
        # colour list, a direction -- and jumping between them without taking
        # those defaults leaves a rainbow running at a breath's frame interval.
        period, colours, direction = charger.MODE_DEFAULTS.get(
            mode, (1, (), charger.DIR_NONE))
        self._period = period
        self._direction = direction
        if colours:
            self._colours = [tuple(c) for c in colours]
        self.lightingChanged.emit()

    @Property(list, notify=lightingChanged)
    def modeNames(self):
        return MODE_NAMES

    @Property(int, notify=lightingChanged)
    def brightness(self):
        return self._brightness

    @brightness.setter
    def brightness(self, value):
        value = max(charger.BRIGHTNESS_MIN,
                    min(charger.BRIGHTNESS_MAX, int(value)))
        if value != self._brightness:
            self._brightness = value
            self.lightingChanged.emit()

    @Property(int, notify=lightingChanged)
    def period(self):
        """Flydigi's "frame interval": bigger is slower."""
        return self._period

    @period.setter
    def period(self, value):
        low, high = charger.MODE_PERIOD_RANGE.get(
            self._mode, charger.PERIOD_RANGE_FALLBACK)
        value = max(low, min(high, int(value)))
        if value != self._period:
            self._period = value
            self.lightingChanged.emit()

    @Property(int, notify=lightingChanged)
    def periodMin(self):
        return charger.MODE_PERIOD_RANGE.get(
            self._mode, charger.PERIOD_RANGE_FALLBACK)[0]

    @Property(int, notify=lightingChanged)
    def periodMax(self):
        return charger.MODE_PERIOD_RANGE.get(
            self._mode, charger.PERIOD_RANGE_FALLBACK)[1]

    @Property(int, notify=lightingChanged)
    def coloursUsed(self):
        """How many colours this mode's generator reads. Zero means it ignores them."""
        return USES_COLOUR.get(self._mode, 0)

    @Property(bool, notify=lightingChanged)
    def usesDirection(self):
        return self._mode in USES_DIRECTION

    @Property(list, notify=lightingChanged)
    def colours(self):
        return [to_hex(c) for c in self._colours]

    @Property(list, notify=lightingChanged)
    def directionNames(self):
        return DIRECTION_NAMES

    @Property(int, notify=lightingChanged)
    def directionIndex(self):
        for index, (_name, value) in enumerate(DIRECTIONS):
            if value == self._direction:
                return index
        return 0

    @directionIndex.setter
    def directionIndex(self, index):
        index = int(index)
        if 0 <= index < len(DIRECTIONS) and DIRECTIONS[index][1] != self._direction:
            self._direction = DIRECTIONS[index][1]
            self.lightingChanged.emit()

    @Slot(int, str)
    def setColour(self, index, text):
        colour = from_hex(text)
        while len(self._colours) <= index:
            self._colours.append(colour)
        if self._colours[index] != colour:
            self._colours[index] = colour
            self.lightingChanged.emit()

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(float, notify=busyChanged)
    def progress(self):
        return self._progress

    @Slot()
    def apply(self):
        """Compute the frames and send them. Several seconds of packets.

        The frames are generated on the worker thread, not here: fifty frames
        of 162 LEDs is a real amount of arithmetic and the UI thread is the one
        place it must not happen.
        """
        if self._busy or not self._selector:
            return
        self._busy = True
        self._progress = 0.0
        self.busyChanged.emit()
        self.lightingRequested.emit(self._selector, {
            "mode": self._mode,
            "brightness": self._brightness,
            "period": self._period,
            "direction": self._direction,
            "colours": [list(c) for c in self._colours[:max(1, self.coloursUsed)]],
        })
