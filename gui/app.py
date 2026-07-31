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

**Scope is every Flydigi device attached, not "the pad".** `devices` is the list
behind the picker; `device` and the pages under it are whichever pad is chosen,
and `dock` is whichever charging dock is. Two selections, because a pad and a
dock are not alternatives -- see `gui/models/devices.py`.
"""
from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement, QmlSingleton

from flydigi import games

from .models import (DeviceModel, DevicesModel, DockModel, DsModeModel,
                     GameFilterModel, GameListModel, LightingModel,
                     ProfileModel, ScreenModel, SettingsModel, SetupModel)
from .worker import DeviceThread

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

PROFILE_COUNT = 4
# Two intervals for the same poll, because it does two different jobs. Against a
# connected pad it is watching battery and charge, which move slowly and cost an
# exchange to ask about. Against a missing one it is *looking* for the pad -- and
# a pad that has gone to sleep has left the USB bus entirely, so "missing" is the
# ordinary state of a pad nobody is holding rather than a fault. Waiting thirty
# seconds to notice one that was just woken reads as never noticing it.
INFO_INTERVAL_MS = 30_000
SEARCH_INTERVAL_MS = 2_000
# How often the whole bus is re-enumerated, as opposed to the selected pad being
# asked how it is. Slower than either, and for a different reason: this is three
# exchanges with *every* attached device, so it is a hotplug check rather than a
# poll. The pad's own info poll is what notices the selected pad coming and
# going, and it runs every two seconds while one is missing.
DEVICES_INTERVAL_MS = 10_000


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
    requestSettings = Signal()
    requestVibration = Signal(dict)
    fetchingChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.thread = None
        self._fetch = None
        self._devices = DevicesModel(self)
        self._dock = DockModel(self)
        self._device = DeviceModel(self)
        self._profile = ProfileModel(self)
        self._lighting = LightingModel(self)
        self._games = GameListModel(self)
        self._games_view = GameFilterModel(self._games, self)
        self._screen = ScreenModel(self)
        self._settings = SettingsModel(self)
        self._setup = SetupModel(self)
        self._dsmode = DsModeModel(self)
        # A failed setup action is the same kind of news as a failed device
        # one, so it goes to the same inline message rather than a second
        # channel the user has to learn to look at.
        self._setup.failed.connect(self._setup_failed)
        self._dsmode.failed.connect(self._setup_failed)
        # A macro the pad cannot hold, or a recording that caught nothing, is
        # the same kind of news -- it goes to the same banner.
        self._profile.macros.refused.connect(self._setup_failed)
        # And its ordinary news goes where every other passing message goes,
        # rather than through the error banner: stopping DualSense mode is
        # something the user asked for, not something that went wrong.
        self._dsmode.note.connect(self._status)

        self._profile.setSlotCount(PROFILE_COUNT)
        self._games.load()

        # The picker feeding the worker, and the worker feeding it back. The
        # dock model asks for its own reads, because which dock it is showing
        # is its own state and nothing else needs to know.
        #
        # The pad's re-read is deliberately *not* hung off the picker's signal:
        # see `worker.select_pad`. It is hung off the worker's reply, in
        # `start()`, so the reads are queued behind a switch that has happened.
        self._devices.dockSelected.connect(self._dock.setSelector)

        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self.requestInfo)
        self._devices_timer = QTimer(self)
        self._devices_timer.setInterval(DEVICES_INTERVAL_MS)
        self._devices_timer.timeout.connect(self._devices.refreshRequested)
        self._polling = False
        # Our own copy, because `connectedChanged` is not an edge: both
        # `infoReceived` and `failed` emit it unconditionally so that `summary`
        # re-evaluates, which makes it "the pad reported in" rather than "the
        # pad's connectedness moved".
        self._was_connected = False
        self._device.connectedChanged.connect(self._connection_changed)

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
        self._profile.macros.recordRequested.connect(worker.record_macro)
        self._lighting.writeRequested.connect(worker.write_lighting)
        self.requestScreen.connect(worker.refresh_screen)
        self.requestSettings.connect(worker.refresh_settings)
        self._settings.writeRequested.connect(worker.write_setting)
        self._screen.uploadRequested.connect(self._screen_upload_starting)
        self._screen.uploadRequested.connect(worker.upload_screen)
        self._screen.settingRequested.connect(worker.set_screen_setting)
        self._devices.refreshRequested.connect(worker.refresh_devices)
        self._devices.padSelected.connect(worker.select_pad)
        worker.pad_selected.connect(self._pad_selected)
        self._dock.refreshRequested.connect(worker.load_dock)
        self._dock.switchRequested.connect(worker.set_dock_switch)
        self._dock.lightingRequested.connect(worker.write_dock_lighting)

        # -- replies in -----------------------------------------------------
        worker.info_changed.connect(self._device.infoReceived)
        worker.failed.connect(self._device.failed)
        worker.status.connect(self._status)
        worker.active_changed.connect(self._profile.setActive)
        worker.profile_loaded.connect(self._profile.profileLoaded)
        worker.profile_written.connect(self._written)
        worker.macro_recorded.connect(self._profile.macros.recorded)
        worker.transport_changed.connect(self._device.transportReceived)
        worker.versions_changed.connect(self._device.versionsReceived)
        worker.lighting_loaded.connect(self._lighting.configLoaded)
        worker.lighting_written.connect(self._lighting_written)
        worker.vibration_applied.connect(self._vibration_applied)
        worker.settings_changed.connect(self._settings.stateReceived)
        worker.screen_status.connect(self._screen.statusReceived)
        worker.screen_progress.connect(self._screen.progressReceived)
        worker.screen_finished.connect(self._screen_finished)
        worker.devices_changed.connect(self._devices.devicesReceived)
        worker.dock_state.connect(self._dock.stateReceived)
        worker.dock_progress.connect(self._dock.progressReceived)
        worker.dock_finished.connect(self._dock.writeFinished)

        if poll:
            self.beginPolling()

    @Slot()
    def beginPolling(self):
        """Start watching the pad, which is also how the first read happens.

        Separate from `start` for the same reason `start` is separate from the
        constructor: a test builds the graph, puts a fake pad behind the worker,
        and only then lets anything be asked of a real one.

        There is no startup read apart from this. Asking for the info and
        letting the answer drive the rest means a pad that was there all along
        and a pad plugged in later come down one path -- the path that gets
        exercised on every launch, rather than one that only runs when something
        has gone missing and so is the first thing to rot.
        """
        self._polling = True
        self._resume_polling()
        # The bus first: the pad the worker opens is the one the picker last
        # chose, and until the list has arrived nothing knows whether that pad
        # is here. Asking for the info in the same breath is deliberate --
        # neither answer waits on the other, and the header should not sit
        # blank for a whole enumeration.
        self._devices_timer.start()
        self._devices.refreshRequested.emit()
        self.requestInfo.emit()

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

    # -- staying in touch with the pad -------------------------------------

    def _resume_polling(self):
        """(Re)start the info poll at whichever interval the state calls for.

        Never while a screen upload is running: the pad spends minutes bridging
        to its screen chip, and a poll landing in the middle of that would at
        best contend with the upload for the node and at worst bury its own
        progress under a failure every two seconds. The upload's end calls this
        again.

        Note the vendor node does *not* go away: command 31 adds a CDC
        interface beside the gamepad rather than replacing the pad with a
        bootloader, and the `37d7:2501` hidraw nodes stay enumerated
        throughout (PROTOCOL.md). What is unmeasured is whether the firmware
        still answers on them mid-bridge, which is reason enough not to ask.
        """
        if self._screen.busy:
            return
        self._info_timer.start(
            INFO_INTERVAL_MS if self._device.connected else SEARCH_INTERVAL_MS)

    def _connection_changed(self):
        """Hunt for the pad while it is gone, and read it whole when it returns.

        The poll only ever asked for device info, so a pad that arrived after
        startup got a header saying "Apex 5" over pages that had never been
        filled -- no active profile, no lighting, no device settings, nothing
        until someone pressed Reload. Coming back is precisely the moment when
        everything is worth re-reading; unsaved edits are what it must not cost.
        """
        connected = self._device.connected
        if connected == self._was_connected:
            return
        self._was_connected = connected
        if not self._polling:
            return
        self._resume_polling()
        if connected:
            self._read_the_rest(keep_edits=True)

    # -- what QML binds to -------------------------------------------------

    @Property(DevicesModel, constant=True)
    def devices(self):
        """Everything attached, and which pad and dock are selected."""
        return self._devices

    @Property(DockModel, constant=True)
    def dock(self):
        """The charging dock the dock pages are showing, if one is selected."""
        return self._dock

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

    @Property(SettingsModel, constant=True)
    def settings(self):
        return self._settings

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
        self._read_the_rest(keep_edits=False)

    def _read_the_rest(self, keep_edits):
        """Everything the window shows apart from the device info.

        Apart, because the two callers arrive differently. The Reload button
        asks for the info along with the rest; a reconnect is *triggered* by an
        info reply, and asking again there would be putting a question to the
        pad it has just answered.

        `keep_edits` is the other difference. Pressing Reload is someone asking
        for the pad's version of the truth, so it wins over anything unsaved. A
        pad falling asleep on a half-finished remap and being woken again is not
        anyone asking for anything, and answering it by dropping the remap would
        be the app losing work nobody told it to lose -- and the pad does sleep,
        in minutes, while an editing session touches it not at all.
        """
        self.requestStatus.emit()
        self.requestTransport.emit()
        # One read for both: the screen's two toggles are two bits of the
        # device-settings block, so asking for the block fills the Screen page
        # as well and the pad is not asked the same question twice. Never held
        # back for edits: every device setting is written the moment it is
        # toggled, so there is no unsaved version of it to lose.
        self.requestSettings.emit()
        if not (keep_edits and self._lighting.dirty):
            self.requestLighting.emit()
        if not (keep_edits and self._profile.dirty):
            self._profile.forget()

    @Slot()
    def stopMacroRecording(self):
        """End a recording early.

        A direct call and not a signal, on purpose: the worker thread is inside
        the recorder's poll loop, so anything queued would only arrive once the
        recording had finished by itself. Setting a bool is what the shutdown
        path does for the same reason.
        """
        if self.thread is not None:
            self.thread.worker.request_stop_recording()

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

    def _pad_selected(self, _selector):
        """A different pad is on screen: read it as though it had just arrived.

        Edits are deliberately not kept. `keep_edits` exists for a pad that
        dozed off mid-remap and came back, where dropping the work would be the
        app losing something nobody asked it to lose. Choosing another pad is
        someone asking, and carrying a half-finished remap across from one pad
        to another would be the app inventing an intention.
        """
        self._device.connected = False
        self.requestInfo.emit()
        self._read_the_rest(keep_edits=False)

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
            self._resume_polling()
        if ok:
            self.requestScreen.emit()

    def _vibration_applied(self, name, sides):
        self._device.status = f"{name}: applied to {sides or 'nothing'}"
