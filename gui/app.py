# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Owns the models and the device thread, and wires them together.

This is what used to be `MainWindow.__init__` minus the widgets. Keeping it
apart from `main.py` means the whole application graph can be built and driven
in a test without a QML engine, a window or a display.

QML constructs this, as the `App` singleton of the Apex5 module -- which is why
opening the device is a separate `start()` rather than something `__init__`
does. A test needs a window in between: build the graph, put a fake pad behind
the worker, and only then let it talk to anything.

Scope is the controller itself. The charging dock is a separate SDK we have not
decompiled, and nothing here talks to it.
"""
from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

from flydigi import games

from .models import (DeviceModel, GameFilterModel, GameListModel,
                     LightingModel, ProfileModel)
from .worker import DeviceThread

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

PROFILE_COUNT = 4
INFO_INTERVAL_MS = 30_000


class FetchThread(QThread):
    """Downloading the game list blocks; keep it off the UI thread.

    Its own thread rather than the device worker's: that one is busy with
    blocking HID traffic, and a slow download would sit behind a config read.
    """

    done = Signal(object, str)

    def run(self):
        try:
            self.done.emit(games.fetch_gamelist(), "")
        except Exception as exc:              # network, JSON, permissions
            self.done.emit(None, str(exc))


@QmlElement
@QmlSingleton
class App(QObject):
    """The application graph: models, worker thread, and the wiring between."""

    # Requests go to the worker as signals, never as direct calls: calling a
    # slot on an object living in another thread just runs it on this one,
    # which would put blocking HID traffic back on the UI thread.
    requestInfo = Signal()
    requestStatus = Signal()
    requestLighting = Signal()
    requestVibration = Signal(dict)
    fetchingChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self._fetch = None
        self._device = DeviceModel(self)
        self._profile = ProfileModel(self)
        self._lighting = LightingModel(self)
        self._games = GameListModel(self)
        self._games_view = GameFilterModel(self._games, self)

        self._profile.setSlotCount(PROFILE_COUNT)
        self._games.load()

        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self.requestInfo)

    # -- lifecycle ---------------------------------------------------------

    @Slot()
    @Slot(bool)
    def start(self, poll=True):
        """Open the device and wire the worker up. Idempotent.

        Called from QML once the window is up. A test calls it first, with
        `poll=False`, so it can swap in a fake pad before anything is asked of
        the real one -- the second call from QML then does nothing.
        """
        if self.thread is not None:
            return
        self.thread = DeviceThread()
        worker = self.thread.worker

        # -- requests out ---------------------------------------------------
        self.requestInfo.connect(worker.refresh_info)
        self.requestStatus.connect(worker.refresh_status)
        self.requestLighting.connect(worker.load_lighting)
        self.requestVibration.connect(worker.apply_vibration)
        self._profile.loadRequested.connect(worker.load_profile)
        self._profile.writeRequested.connect(worker.write_profile)
        self._lighting.writeRequested.connect(worker.write_lighting)

        # -- replies in -----------------------------------------------------
        worker.info_changed.connect(self._device.infoReceived)
        worker.failed.connect(self._device.failed)
        worker.status.connect(self._status)
        worker.active_changed.connect(self._profile.setActive)
        worker.profile_loaded.connect(self._profile.profileLoaded)
        worker.profile_written.connect(self._written)
        worker.lighting_loaded.connect(self._lighting.configLoaded)
        worker.lighting_written.connect(self._lighting_written)
        worker.vibration_applied.connect(self._vibration_applied)

        if poll:
            self._info_timer.start(INFO_INTERVAL_MS)

    @Slot()
    def shutdown(self):
        if self.thread is not None:
            self.thread.stop()
            self.thread = None

    # -- what QML binds to -------------------------------------------------

    @Property(DeviceModel, constant=True)
    def device(self):
        return self._device

    @Property(ProfileModel, constant=True)
    def profile(self):
        return self._profile

    @Property(LightingModel, constant=True)
    def lighting(self):
        return self._lighting

    @Property(GameFilterModel, constant=True)
    def games(self):
        return self._games_view

    # -- actions -----------------------------------------------------------

    @Slot()
    def reload(self):
        """Re-read from the pad: info now, and the open profile on demand.

        Other profiles stay unread until opened, because each read makes the
        pad audibly re-seat its trigger motors.
        """
        self.requestInfo.emit()
        self.requestStatus.emit()
        self.requestLighting.emit()
        self._profile.forget()

    @Slot(int)
    def applyGamePreset(self, row):
        """Load one game's rumble-to-trigger preset onto the pad."""
        game = self._games_view.game(row)
        if game is not None:
            self.requestVibration.emit(game)

    @Slot(result=bool)
    def refreshGameList(self):
        """Re-read the cached gamelist from disk."""
        return self._games.load()

    @Property(bool, notify=fetchingChanged)
    def fetchingGames(self):
        return self._fetch is not None

    @Slot()
    def fetchGameList(self):
        """Download the game list from Flydigi's public API.

        The only time this application contacts them, and only when asked.
        """
        if self._fetch is not None:
            return
        self._fetch = FetchThread()
        self._fetch.done.connect(self._fetched)
        self._fetch.start()
        self.fetchingChanged.emit()

    def _fetched(self, entries, error):
        self._fetch = None
        self.fetchingChanged.emit()
        if error:
            self._device.error = f"Could not update the game list: {error}"
            return
        self._games.setGames(entries or [])
        self._device.status = f"Game list updated: {self._games.count} games"

    # -- worker replies that need a sentence rather than a model ------------

    def _status(self, message):
        self._device.status = message

    def _written(self, cfg_id, packets, saved):
        self._profile.confirmWritten(cfg_id, saved)
        where = "saved to flash" if saved else "in memory only"
        self._device.status = (
            f"Profile {cfg_id + 1}: wrote {packets} packet(s), {where}")

    def _lighting_written(self, packets, saved):
        self._lighting.confirmWritten(saved)
        where = "saved to flash" if saved else "in memory only"
        self._device.status = f"Lighting: wrote {packets} packet(s), {where}"

    def _vibration_applied(self, name, sides):
        self._device.status = f"{name}: applied to {sides or 'nothing'}"
