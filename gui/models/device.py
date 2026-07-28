# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the pad is, as opposed to what is stored on it.

Battery, connection and which profile is running -- the things a header shows.
Kept apart from the profile models because none of it is editable: this is
reported state, and the only writer is the worker thread's info signal.
"""
from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

# Registers this class into the Apex5 QML module. The decorator reads these two
# names out of the module's globals, so every file with a QmlElement needs
# them. `tools/generate-qmltypes` turns the result into the type information
# qmllint checks QML against.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# The pad reports charge in eight steps, not a percentage. Showing "4/8" is
# honest about that; inventing "50%" would not be.
BATTERY_STEPS = 8


@QmlElement
class DeviceModel(QObject):
    """Connection, battery, and the transient status/error line."""

    connectedChanged = Signal()
    batteryChanged = Signal()
    chargingChanged = Signal()
    connectionTypeChanged = Signal()
    activeProfileChanged = Signal()
    statusChanged = Signal()
    errorChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._battery = 0
        self._charging = False
        self._connection_type = ""
        self._active_profile = -1
        self._status = ""
        self._error = ""

    @Property(bool, notify=connectedChanged)
    def connected(self):
        return self._connected

    @connected.setter
    def connected(self, value):
        value = bool(value)
        if self._connected != value:
            self._connected = value
            self.connectedChanged.emit()

    @Property(int, constant=True)
    def batterySteps(self):
        return BATTERY_STEPS

    @Property(int, notify=batteryChanged)
    def battery(self):
        return self._battery

    @battery.setter
    def battery(self, value):
        value = max(0, min(BATTERY_STEPS, int(value)))
        if self._battery != value:
            self._battery = value
            self.batteryChanged.emit()

    @Property(bool, notify=chargingChanged)
    def charging(self):
        return self._charging

    @charging.setter
    def charging(self, value):
        value = bool(value)
        if self._charging != value:
            self._charging = value
            self.chargingChanged.emit()

    @Property(str, notify=connectionTypeChanged)
    def connectionType(self):
        return self._connection_type

    @connectionType.setter
    def connectionType(self, value):
        value = str(value or "")
        if self._connection_type != value:
            self._connection_type = value
            self.connectionTypeChanged.emit()

    @Property(int, notify=activeProfileChanged)
    def activeProfile(self):
        """Slot the pad is running, or -1 before the first status read."""
        return self._active_profile

    @activeProfile.setter
    def activeProfile(self, value):
        value = int(value)
        if self._active_profile != value:
            self._active_profile = value
            self.activeProfileChanged.emit()

    @Property(str, notify=statusChanged)
    def status(self):
        """Last progress message. Replaces the widget app's status bar."""
        return self._status

    @status.setter
    def status(self, value):
        value = str(value or "")
        if self._status != value:
            self._status = value
            self.statusChanged.emit()

    @Property(str, notify=errorChanged)
    def error(self):
        """Last failure, or "" when clear. Becomes a Kirigami.InlineMessage."""
        return self._error

    @error.setter
    def error(self, value):
        value = str(value or "")
        if self._error != value:
            self._error = value
            self.errorChanged.emit()

    @Property(str, notify=connectedChanged)
    def summary(self):
        """One line for a header: what is attached, or that nothing is."""
        if not self._connected:
            return "Looking for a controller…"
        if self._connection_type:
            return f"Apex 5 connected ({self._connection_type})"
        return "Apex 5 connected"

    # -- slots the worker's signals land on --------------------------------

    @Slot(dict)
    def infoReceived(self, info):
        """Fold one `motion.read_info` reply into the reported state."""
        self.connected = True
        self.error = ""
        self.battery = info.get("battery_level", 0)
        self.charging = bool(info.get("charging"))
        self.connectionType = info.get("connect_type", "")
        # `summary` reads off connection type as well as connectedness, so it
        # needs a nudge whenever either moves.
        self.connectedChanged.emit()

    @Slot(str)
    def failed(self, message):
        self.connected = False
        self.error = message
        self.connectedChanged.emit()
