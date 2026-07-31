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

from flydigi import motion

# Registers this class into the Apex5 QML module. The decorator reads these two
# names out of the module's globals, so every file with a QmlElement needs
# them. `tools/generate-qmltypes` turns the result into the type information
# qmllint checks QML against.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# The pad reports charge in steps, not a percentage. Showing "4/5" is honest
# about that; inventing "80%" would not be.
#
# **Five, not eight**, and this said eight for its whole life. The scale comes
# from the pad's own reply -- a 4-bit nibble, which is where eight was guessed
# from -- but Space Station only ever draws `Power0.svg` through `Power6.svg`,
# picked as `level <= 6 ? level : 0`, and the SDK turns the charging bit into
# the literal 6. So the display domain is 0..6 with 6 meaning charging, which
# leaves 0..5 for charge and makes **5 full**. `flydigi/motion.py` had it right
# all along; this constant did not, and reported a full pad as five-eighths.
BATTERY_STEPS = motion.MAX_LEVEL


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
    thirdPartyChanged = Signal()

    # Asking the worker to flip it, since the write is blocking HID traffic.
    thirdPartyRequested = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._battery = 0
        self._charging = False
        self._connection_type = ""
        self._active_profile = -1
        self._status = ""
        self._error = ""
        self._third_party = False
        self._control_by = ""
        self._third_party_available = False
        self._firmware = ""
        # The last failure reported, so the same one is not reported twice. See
        # `failed`.
        self._last_failure = None

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

    # -- third-party takeover ----------------------------------------------
    #
    # A device setting rather than a profile one, and a handover rather than a
    # preference: with it on the pad lets another driver acquire it, and Steam's
    # native Flydigi support is on the far side of that. Confirmed on hardware
    # -- turning it on made `controlBy` fill in with "SDL" by itself, and Steam
    # went from "generic XInput controller" to "Apex 5".

    @Property(bool, notify=thirdPartyChanged)
    def thirdParty(self):
        return self._third_party

    @thirdParty.setter
    def thirdParty(self, value):
        # Optimistic locally, then corrected by the read that follows the write:
        # the new holder reconfigures the transport itself, so what the pad ends
        # up reporting is not simply what we asked for.
        value = bool(value)
        if self._third_party != value:
            self._third_party = value
            self.thirdPartyChanged.emit()
        self.thirdPartyRequested.emit(value)

    @Property(str, notify=thirdPartyChanged)
    def controlBy(self):
        """Who currently holds the pad, or "" for nobody.

        Worth showing rather than hiding: it is the difference between "this
        setting is on" and "this setting is on and Steam has taken you up on it".
        """
        return self._control_by

    @Property(bool, notify=thirdPartyChanged)
    def thirdPartyAvailable(self):
        """Whether this pad's firmware offers the feature at all.

        Space Station hides it below 7.0.3.0 on a k5. We check numerically; see
        motion.version_at_least for why not the way they do.
        """
        return self._third_party_available

    @Property(str, notify=thirdPartyChanged)
    def firmware(self):
        return self._firmware

    @Slot(dict)
    def transportReceived(self, state):
        self._third_party = bool(state.get("third_party"))
        self._control_by = str(state.get("control_by") or "")
        self.thirdPartyChanged.emit()

    @Slot(dict)
    def versionsReceived(self, versions):
        self._firmware = str(versions.get("main") or "")
        self._third_party_available = motion.version_at_least(
            self._firmware, motion.THIRD_PARTY_MIN_FIRMWARE["k5"])
        self.thirdPartyChanged.emit()

    # -- slots the worker's signals land on --------------------------------

    @Slot(dict)
    def infoReceived(self, info):
        """Fold one `motion.read_info` reply into the reported state."""
        self.connected = True
        self.error = ""
        self._last_failure = None
        self.battery = info.get("battery_level", 0)
        self.charging = bool(info.get("charging"))
        self.connectionType = info.get("connect_type", "")
        # `summary` reads off connection type as well as connectedness, so it
        # needs a nudge whenever either moves.
        self.connectedChanged.emit()

    @Slot(str)
    def failed(self, message):
        """Report a failure, but say the same one only once.

        The hunt for a missing pad runs every two seconds, and every round of it
        fails with the identical sentence. Posting each one put a banner the user
        had just dismissed straight back on screen, over and over, for a state
        the header already reports in words -- "No controller / press a button to
        wake it". So a repeat while already disconnected is not news.

        A *different* failure still is: "permission denied" arriving where "no
        controller found" was is the one that has to reach the user, since it is
        the difference between a sleeping pad and udev rules that were never
        installed. So is any failure after a spell of the pad answering, which is
        what `was` covers.
        """
        was = self._connected
        self.connected = False
        if was or message != self._last_failure:
            self.error = message
        self._last_failure = message
        self.connectedChanged.emit()
