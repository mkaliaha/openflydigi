# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Device settings: the pad's own state, as opposed to a profile's.

Everything here survives switching profiles and belongs to the controller
rather than to one of its four slots. That is the whole organising idea of the
page, and it is why there is no apply/save footer: these commands take effect as
they land and there is nothing to commit.

**What is written is not what is shown.** Every write is followed by a read of
the whole block, and the model takes its state from that read. The pad forces
this rather than caution doing it: a command-19 reply echoes the value and never
the sub-id, so an acknowledgement says "a setting was written" and nothing about
which. The switch moves optimistically so the UI does not lag a second behind a
click, and the read-back is what it settles on.

Two of the ten features are not here. **Always-on display** and **status bar**
are the same command and the same reply, but they are the screen's, and they are
on the Screen page where someone looking for them will be. Two more are absent
because the pad says so: motion debounce and audio come back unsupported on a
k5, and `usable` is what hides them rather than a hard-coded list.
"""
from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import settings

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# The picker's order is by resolution; the wire's is the order Flydigi declared
# the enum in, which puts 9- and 11-bit after 12. Sorting here rather than
# renumbering there keeps the one thing that must not move -- the wire value --
# exactly as the pad reports it.
PRECISION_WIRE = tuple(value for value, _bits in sorted(
    ((value, bits) for value, bits in enumerate(settings.PRECISION_BITS) if bits),
    key=lambda pair: pair[1]))
PRECISION_NAMES = [settings.precision_name(value) for value in PRECISION_WIRE]

# Seven wire values, ascending, which runs from most to least sensitive. Space
# Station collapses them into three choices; this does not, because the pad can
# report any of the seven and a three-way picker could not show four of them.
SENSITIVITY_WIRE = settings.SENSITIVITY_VALUES
SENSITIVITY_NAMES = [settings.sensitivity_name(value) for value in SENSITIVITY_WIRE]

# The switches this page owns, in the order they appear on it.
SWITCHES = ("quick_switch", "stick_debounce", "auto_calibration",
            "stick_rebound", "mapping_switch")

# What each setting is called in a sentence. Here rather than in the QML because
# the worker builds the "it is now …" line, and a label that only existed in a
# page would leave that line naming a wire field.
SETTING_LABELS = {
    "quick_switch": "Quick-switch config",
    "xbox_home": "The Xbox home button",
    "motion_debounce": "Motion debounce",
    "mapping_switch": "The mapping switch",
    "stick_debounce": "Stick debounce",
    "auto_calibration": "Stick auto-calibration",
    "stick_rebound": "The rebound filter",
    "status_bar_always_on": "The status bar",
    "always_on": "The always-on display",
    "audio": "Audio",
    "sleep_minutes": "Sleep",
    "precision": "Stick precision",
    "sensitivity": "Centre sensitivity",
    "report_rate": "Report rate",
}


def describe_setting(name, state):
    """One sentence about a setting, taken from the block the pad read back.

    From the read-back and not from what was requested, which is the whole point
    of reading it back: the pad acknowledges settings it does not have.
    """
    label = SETTING_LABELS.get(name, name)
    if name == "sleep_minutes":
        return f"{label}: {settings.describe(state)['sleep']}"
    if name == "precision":
        return f"{label}: {settings.precision_name(state['precision'])}"
    if name == "sensitivity":
        return f"{label}: {settings.sensitivity_name(state['sensitivity'])}"
    if name == "report_rate":
        return f"{label}: {settings.describe(state)['report_rate']}"
    if not state.get(f"{name}_usable"):
        # The honest answer when the pad acknowledged a feature it does not
        # have. Silence here would read as success.
        return f"{label}: not supported on this pad"
    return f"{label}: {'on' if state.get(name) else 'off'}"


@QmlElement
class SettingsModel(QObject):
    """The command-3 block, as something QML can bind to."""

    changed = Signal()
    # name, value -- the worker writes it and reads the block back.
    writeRequested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = {}
        self._loaded = False

    # -- what the worker hands back ----------------------------------------

    @Slot(dict)
    def stateReceived(self, state):
        self._state = dict(state)
        self._loaded = True
        self.changed.emit()

    @Property(bool, notify=changed)
    def loaded(self):
        """False until the first read. The page shows nothing rather than zeroes."""
        return self._loaded

    # -- the switches -------------------------------------------------------

    def _flag(self, name):
        return bool(self._state.get(name))

    def _usable(self, name):
        # Unread means unavailable, not unsupported: a page built before the
        # first reply would otherwise announce that the pad supports nothing.
        return self._loaded and bool(self._state.get(f"{name}_usable"))

    def _set_flag(self, name, value):
        value = bool(value)
        if self._flag(name) != value:
            self._state[name] = value
            self.changed.emit()
        self.writeRequested.emit(name, 1 if value else 0)

    @Property(bool, notify=changed)
    def quickSwitch(self):
        return self._flag("quick_switch")

    @quickSwitch.setter
    def quickSwitch(self, value):
        self._set_flag("quick_switch", value)

    @Property(bool, notify=changed)
    def quickSwitchUsable(self):
        return self._usable("quick_switch")

    @Property(bool, notify=changed)
    def stickDebounce(self):
        return self._flag("stick_debounce")

    @stickDebounce.setter
    def stickDebounce(self, value):
        self._set_flag("stick_debounce", value)

    @Property(bool, notify=changed)
    def stickDebounceUsable(self):
        return self._usable("stick_debounce")

    @Property(bool, notify=changed)
    def autoCalibration(self):
        return self._flag("auto_calibration")

    @autoCalibration.setter
    def autoCalibration(self, value):
        self._set_flag("auto_calibration", value)

    @Property(bool, notify=changed)
    def autoCalibrationUsable(self):
        """Supported, and only reachable while stick debounce is on.

        Flydigi's own string for the debounce toggle says turning it off
        disables auto-calibration, so the dependency is theirs and not an
        invention here -- the page greys the row rather than leaving a switch
        that silently does nothing.
        """
        return self._usable("auto_calibration") and self._flag("stick_debounce")

    @Property(bool, notify=changed)
    def stickRebound(self):
        return self._flag("stick_rebound")

    @stickRebound.setter
    def stickRebound(self, value):
        self._set_flag("stick_rebound", value)

    @Property(bool, notify=changed)
    def stickReboundUsable(self):
        return self._usable("stick_rebound")

    @Property(bool, notify=changed)
    def mappingSwitch(self):
        return self._flag("mapping_switch")

    @mappingSwitch.setter
    def mappingSwitch(self, value):
        self._set_flag("mapping_switch", value)

    @Property(bool, notify=changed)
    def mappingSwitchUsable(self):
        return self._usable("mapping_switch")

    # -- sleep --------------------------------------------------------------

    @Property(int, notify=changed)
    def sleepMinutes(self):
        return int(self._state.get("sleep_minutes", 0))

    @sleepMinutes.setter
    def sleepMinutes(self, value):
        value = max(0, min(settings.SLEEP_MAX_MINUTES, int(value)))
        if self.sleepMinutes != value:
            self._state["sleep_minutes"] = value
            self.changed.emit()
        self.writeRequested.emit("sleep_minutes", value)

    @Property(int, constant=True)
    def sleepMax(self):
        return settings.SLEEP_MAX_MINUTES

    @Property(str, notify=changed)
    def sleepText(self):
        return settings.describe(self._state)["sleep"] if self._loaded else ""

    # -- the two stick numbers ---------------------------------------------

    @Property(list, constant=True)
    def precisionNames(self):
        return PRECISION_NAMES

    @Property(int, notify=changed)
    def precision(self):
        """Index into `precisionNames`, or -1 for a value outside the enum."""
        wire = int(self._state.get("precision", -1))
        return PRECISION_WIRE.index(wire) if wire in PRECISION_WIRE else -1

    @precision.setter
    def precision(self, index):
        if not 0 <= int(index) < len(PRECISION_WIRE) or self.precision == int(index):
            return
        wire = PRECISION_WIRE[int(index)]
        self._state["precision"] = wire
        self.changed.emit()
        self.writeRequested.emit("precision", wire)

    @Property(list, constant=True)
    def sensitivityNames(self):
        return SENSITIVITY_NAMES

    @Property(int, notify=changed)
    def sensitivity(self):
        wire = int(self._state.get("sensitivity", -1))
        return SENSITIVITY_WIRE.index(wire) if wire in SENSITIVITY_WIRE else -1

    @sensitivity.setter
    def sensitivity(self, index):
        if not 0 <= int(index) < len(SENSITIVITY_WIRE) or self.sensitivity == int(index):
            return
        wire = SENSITIVITY_WIRE[int(index)]
        self._state["sensitivity"] = wire
        self.changed.emit()
        self.writeRequested.emit("sensitivity", wire)

    # -- report rate, which is shown and not offered -----------------------

    @Property(str, notify=changed)
    def reportRateText(self):
        """What the pad reports, in words. Read-only, and deliberately.

        This pad answers 0, which is not in Flydigi's `{1000: 1, 500: 2, ...}`
        map, so what a write would do to it has never been observed -- and both
        its input endpoints already declare the 1 ms interval that is the
        ceiling for a full-speed device. There is nothing to gain and a working
        rate to lose. `tools/flydigi-settings report-rate --i-know` exists for
        the bench; a control here would be an invitation.
        """
        return settings.describe(self._state)["report_rate"] if self._loaded else ""
