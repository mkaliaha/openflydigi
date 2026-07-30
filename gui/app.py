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

from .models import (DeviceModel, DsModeModel, GameFilterModel, GameListModel,
                     LightingModel, ProfileModel, ScreenModel, SetupModel)
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
    requestTransport = Signal()
    requestScreen = Signal()
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
        self._screen = ScreenModel(self)
        self._setup = SetupModel(self)
        self._dsmode = DsModeModel(self)
        # A failed setup action is the same kind of news as a failed device
        # one, so it goes to the same inline message rather than a second
        # channel the user has to learn to look at.
        self._setup.failed.connect(self._setup_failed)
        self._dsmode.failed.connect(self._setup_failed)
        # And its ordinary news goes where every other passing message goes,
        # rather than through the error banner: stopping DualSense mode is
        # something the user asked for, not something that went wrong.
        self._dsmode.note.connect(self._status)

        self._profile.setSlotCount(PROFILE_COUNT)
        self._games.load()

        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self.requestInfo)
        self._polling = False

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
        self.requestTransport.connect(worker.refresh_transport)
        self._device.thirdPartyRequested.connect(worker.set_third_party)
        self.requestVibration.connect(worker.apply_vibration)
        self._profile.loadRequested.connect(worker.load_profile)
        self._profile.writeRequested.connect(worker.write_profile)
        self._lighting.writeRequested.connect(worker.write_lighting)
        self.requestScreen.connect(worker.refresh_screen)
        self._screen.uploadRequested.connect(self._screen_upload_starting)
        self._screen.uploadRequested.connect(worker.upload_screen)
        self._screen.settingRequested.connect(worker.set_screen_setting)

        # -- replies in -----------------------------------------------------
        worker.info_changed.connect(self._device.infoReceived)
        worker.failed.connect(self._device.failed)
        worker.status.connect(self._status)
        worker.active_changed.connect(self._profile.setActive)
        worker.profile_loaded.connect(self._profile.profileLoaded)
        worker.profile_written.connect(self._written)
        worker.transport_changed.connect(self._device.transportReceived)
        worker.versions_changed.connect(self._device.versionsReceived)
        worker.lighting_loaded.connect(self._lighting.configLoaded)
        worker.lighting_written.connect(self._lighting_written)
        worker.vibration_applied.connect(self._vibration_applied)
        worker.screen_status.connect(self._screen.statusReceived)
        worker.screen_progress.connect(self._screen.progressReceived)
        worker.screen_finished.connect(self._screen_finished)

        self._polling = poll
        if poll:
            self._info_timer.start(INFO_INTERVAL_MS)
            self.requestScreen.emit()

    @Slot()
    def shutdown(self):
        # Same reason as the fetch thread below: a running QThread that loses
        # its last reference is a qFatal, and installing rules can be sitting
        # on an authentication prompt when someone closes the window.
        self._setup.wait(5000)
        # The relay is deliberately not stopped: it is a device the system has
        # now, and closing this window is no reason to take a pad away from a
        # game. Only the model's own short-lived thread is waited for.
        self._dsmode.wait(5000)
        if self._fetch is not None:
            # Bounded because flydigi.games.fetch_gamelist has its own timeout;
            # dropping the last reference to a running QThread is a qFatal.
            self._fetch.wait(5000)
            self._fetch = None
        if self.thread is not None:
            # Keep the reference when the thread did not finish. Dropping it
            # destroys a running QThread, which Qt turns into qFatal and a core
            # dump -- an ugly way to end an otherwise ordinary quit.
            if self.thread.stop():
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

    @Property(ScreenModel, constant=True)
    def screen(self):
        return self._screen

    @Property(SetupModel, constant=True)
    def setup(self):
        return self._setup

    @Property(DsModeModel, constant=True)
    def dsmode(self):
        return self._dsmode

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
        self.requestTransport.emit()
        self.requestScreen.emit()
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

    def _screen_upload_starting(self, _frames, _interval, _restore):
        self._info_timer.stop()

    def _status(self, message):
        self._device.status = message

    def _setup_failed(self, message):
        self._device.error = message

    def _written(self, cfg_id, packets, saved):
        self._profile.confirmWritten(cfg_id, saved)
        where = "saved to flash" if saved else "in memory only"
        self._device.status = (
            f"Profile {cfg_id + 1}: wrote {packets} packet(s), {where}")

    def _lighting_written(self, packets, saved):
        self._lighting.confirmWritten(saved)
        where = "saved to flash" if saved else "in memory only"
        self._device.status = f"Lighting: wrote {packets} packet(s), {where}"

    def _screen_finished(self, ok):
        """Let the info poll resume, and re-read the screen state.

        The poll is stopped for the upload rather than left to fail: the pad is
        busy bridging to its screen chip for minutes, and a 30-second `Get info`
        landing in the middle would report a healthy pad as broken.
        """
        self._screen.uploadFinished(ok)
        if self._polling:
            self._info_timer.start(INFO_INTERVAL_MS)
        if ok:
            self.requestScreen.emit()

    def _vibration_applied(self, name, sides):
        self._device.status = f"{name}: applied to {sides or 'nothing'}"
