# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Device access, kept off the UI thread.

Every exchange with the pad is blocking and slow by UI standards: a config read
waits over a second for 42 packets, and a flash save takes seconds. Doing any
of it on the GUI thread would freeze the window, so all of it happens here and
results come back as signals.

The controller handle is opened lazily and reopened on demand. The pad sleeps
and its hidraw node number changes between wired and dongle, so a handle that
worked a minute ago may not now -- treat every failure as "reconnect and retry
once" rather than as fatal.
"""
from PySide6.QtCore import QObject, QThread, Signal, Slot

from flydigi import blobs, device, effects, lighting, mapping, motion


class DeviceWorker(QObject):
    """Runs on its own thread. Slots are invoked queued, signals arrive on the UI thread."""

    info_changed = Signal(dict)          # battery, connection
    profile_loaded = Signal(int, bytes, str)   # cfg_id, blob, title
    profile_written = Signal(int, int, bool)   # cfg_id, packets, saved to flash
    vibration_applied = Signal(str, str)       # game name, sides applied
    transport_changed = Signal(dict)     # third-party flag + who holds the pad
    versions_changed = Signal(dict)      # the seven firmware components
    lighting_loaded = Signal(bytes)
    lighting_written = Signal(int, bool)
    active_changed = Signal(int)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._ctrl = None
        self._stopping = False

    def request_stop(self):
        """Stop retrying. Called from the owning thread as the app quits.

        Only ever set, never cleared, and read as a plain bool -- so no lock is
        needed for it to do its job, which is to keep shutdown from waiting out
        a second full attempt.
        """
        self._stopping = True

    def _controller(self):
        if self._ctrl is None:
            self._ctrl = device.Controller()
        return self._ctrl

    def _drop(self):
        if self._ctrl is not None:
            try:
                self._ctrl.close()
            except OSError:
                pass
            self._ctrl = None

    def _attempt(self, work, what):
        """Run `work(ctrl)`, reconnecting once if the handle has gone stale."""
        for attempt in (1, 2):
            try:
                return work(self._controller())
            except (OSError, device.DeviceNotFound, blobs.ProtocolError) as exc:
                self._drop()
                if attempt == 2 or self._stopping:
                    self.failed.emit(f"{what}: {exc}")
                    return None
            except Exception as exc:
                # Anything not in the tuple above is a bug rather than a sulking
                # pad, and used to escape the slot in silence: no reply, no
                # `failed`, nothing on screen, and the UI waiting forever. A
                # missing method on the fake pad hid a whole untested code path
                # this way. Report it and stop -- retrying a bug just repeats it.
                self._drop()
                self.failed.emit(f"{what}: {exc!r}")
                return None
        return None

    @Slot()
    def refresh_info(self):
        def work(ctrl):
            info = motion.read_info(ctrl)
            if info is None:
                raise blobs.ProtocolError("no reply -- press a button to wake the pad")
            return info

        info = self._attempt(work, "reading device info")
        if info:
            self.info_changed.emit(info)

    @Slot(int)
    def load_profile(self, cfg_id):
        """Read a single profile, leaving the pad on it.

        Reading switches the pad, and that is the intended effect: opening a
        profile is how you switch to it, the way Space Station does it. The
        pad then runs what is on screen, which is also what makes saving
        correct -- the save command commits whichever profile is running.

        Reading is not free: the pad audibly re-seats its trigger motors on
        every config read, so profiles are fetched one at a time, when first
        opened, never all four to fill a list.
        """
        self.status.emit(f"Reading profile {cfg_id + 1}…")
        config = self._attempt(lambda ctrl: mapping.read_config(ctrl, cfg_id),
                               f"reading profile {cfg_id + 1}")
        if config is None:
            return
        self.profile_loaded.emit(cfg_id, bytes(config.blob), config.title)
        self.active_changed.emit(cfg_id)
        self.status.emit(f"Profile {cfg_id + 1} read")

    @Slot()
    def refresh_transport(self):
        """Who holds the pad, and whether it is allowed to be held.

        Read rather than assumed: the holder reconfigures the transport flags
        itself once it acquires, so what the pad reports afterwards is not what
        anyone asked for.
        """
        state = self._attempt(motion.read_transport, "reading the transport state")
        if state:
            self.transport_changed.emit(state)
        versions = self._attempt(motion.read_versions, "reading the firmware version")
        if versions:
            self.versions_changed.emit(versions)

    @Slot(bool)
    def set_third_party(self, enabled):
        """Allow or refuse another driver taking the pad over.

        Only this flag is sent; the other four go as 0xFF, "leave alone". They
        still move, because whoever acquires next sets them to suit itself.
        """
        def work(ctrl):
            motion.set_raw_data(ctrl, third_party=1 if enabled else 0)
            return motion.read_transport(ctrl)

        state = self._attempt(work, "changing third-party control")
        if state:
            self.transport_changed.emit(state)
            holder = state.get("control_by") or "nothing"
            self.status.emit(
                f"Third-party control {'on' if state['third_party'] else 'off'}"
                f" — held by {holder}")

    @Slot()
    def refresh_status(self):
        """Which profile the pad is actually on. Cheap and side-effect free."""
        status = self._attempt(mapping.read_status, "reading controller status")
        if status:
            self.active_changed.emit(status["active"])

    @Slot(int, bytes, bytes, bool)
    def write_profile(self, cfg_id, blob, previous, save):
        """Write a profile, sending only packets that differ from `previous`."""
        self.status.emit(f"Writing profile {cfg_id + 1}…")
        new = mapping.MappingConfig(blob, cfg_id)
        old = mapping.MappingConfig(previous, cfg_id) if previous else None

        def work(ctrl):
            sent = mapping.write_config(ctrl, cfg_id, new, old=old)
            # Pass the config's own id so committing does not overwrite the
            # slot's version tag with zero.
            saved = mapping.save_config(ctrl, new.data_version) if save else False
            return sent, saved

        result = self._attempt(work, f"writing profile {cfg_id + 1}")
        if result is None:
            return
        sent, saved = result
        self.profile_written.emit(cfg_id, sent, saved)

    @Slot(dict)
    def apply_vibration(self, game):
        """Write a game's rumble-to-trigger binding into the pad, where it stays."""
        results = self._attempt(lambda ctrl: effects.apply_game(ctrl, game),
                                "applying the game's binding")
        if results is None:
            return
        sides = ", ".join(side for side, ok in results if ok)
        name = game.get("enGameName") or game.get("gameName") or "game"
        self.vibration_applied.emit(name, sides)

    @Slot(int)
    def apply_profile(self, cfg_id):
        ok = self._attempt(lambda ctrl: mapping.apply_config(ctrl, cfg_id),
                           f"switching to profile {cfg_id + 1}")
        if ok:
            self.active_changed.emit(cfg_id)
            self.status.emit(f"Pad switched to profile {cfg_id + 1}")
        elif ok is not None:
            self.failed.emit("No reply to the switch -- the pad may be asleep")

    @Slot()
    def load_lighting(self):
        config = self._attempt(lighting.read_config, "reading the lighting config")
        if config is not None:
            self.lighting_loaded.emit(bytes(config.blob))

    @Slot(bytes, bytes, bool)
    def write_lighting(self, blob, previous, save):
        self.status.emit("Writing lighting…")
        new = lighting.LedConfig(blob)
        old = lighting.LedConfig(previous) if previous else None

        def work(ctrl):
            sent = lighting.write_config(ctrl, new, old=old)
            # Lighting shares the mapping save command; the pad commits the
            # working set, not one config at a time. The lighting blob has no
            # version tag of its own, so this leaves the field at zero -- which
            # is one reason saving lighting is still unverified on hardware.
            saved = mapping.save_config(ctrl) if save else False
            return sent, saved

        result = self._attempt(work, "writing lighting")
        if result is not None:
            self.lighting_written.emit(*result)

    @Slot()
    def shutdown(self):
        self._drop()


class DeviceThread:
    """Owns the worker and its thread, so callers do not have to."""

    # Long enough for any single exchange to finish. A save waits 2 s, a config
    # read up to three attempts of 1.5 s, and a from-scratch 42-packet write
    # rather longer -- `Controller.send` never breaks its deadline loop early,
    # so each exchange burns its full wait.
    STOP_TIMEOUT_MS = 10_000

    def __init__(self):
        self.thread = QThread()
        self.worker = DeviceWorker()
        self.worker.moveToThread(self.thread)
        self.thread.start()

    def stop(self):
        """Shut the worker down. True if the thread finished in time.

        Order matters. `worker.shutdown()` is a plain Python call -- @Slot only
        adds a metaobject entry -- so calling it first ran `os.close(fd)` on the
        *caller's* thread while the worker could be blocked in `select()` on
        that same descriptor. `Controller.send` re-reads `self.fd` every
        iteration, so it would then either select on None (TypeError, which
        `_attempt` does not catch, escaping the slot silently) or read from a
        descriptor number the kernel had already handed to someone else.

        So: ask the thread to finish, wait for it, and only close once nothing
        can be using it. If the wait times out the handle is deliberately left
        open -- the process is going away and the kernel will reclaim it, which
        is cheaper than corrupting an unrelated fd.
        """
        self.worker.request_stop()
        self.thread.quit()
        finished = self.thread.wait(self.STOP_TIMEOUT_MS)
        if finished:
            self.worker.shutdown()
        return finished
