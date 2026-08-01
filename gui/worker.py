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

import time

from flydigi import (blobs, charger, device, effects, identity, lighting,
                     macros, mapping, motion, registry, screen, screen_ota,
                     settings)

# The one piece of user-facing text this file needs. It lives with the model
# because a label that only existed in a QML page would leave the status line
# naming a wire field.
from .models.settings import SETTING_LABELS, describe_setting


class DeviceWorker(QObject):
    """Runs on its own thread. Slots are invoked queued, signals arrive on the UI thread."""

    info_changed = Signal(dict)          # battery, connection
    profile_loaded = Signal(int, bytes, str)   # cfg_id, blob, title
    profile_written = Signal(int, int, bool)   # cfg_id, packets, saved to flash
    vibration_applied = Signal(str, str)       # game name, sides applied
    transport_changed = Signal(dict)     # third-party flag + who holds the pad
    third_party_written = Signal(bool)   # did the takeover flag land
    versions_changed = Signal(dict)      # the seven firmware components
    settings_changed = Signal(dict)      # the whole command-3 block
    setting_written = Signal(str, bool)  # setting name, did it land
    screen_status = Signal(dict)
    screen_progress = Signal(int, int)
    screen_finished = Signal(bool)
    lighting_loaded = Signal(bytes)
    lighting_written = Signal(int, bool)
    macro_recorded = Signal(list)        # steps; empty means nothing was played
    active_changed = Signal(int)
    devices_changed = Signal(list)       # every device attached, probed
    pad_selected = Signal(str)           # a different pad is now the open one
    dock_state = Signal(dict)            # one dock's whole state
    dock_switch = Signal(str, str, bool)  # selector, switch name, did it land
    dock_progress = Signal(float)        # 0..1 through a lighting upload
    dock_finished = Signal(bool)
    status = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._ctrl = None
        # Which pad to open, as a selector -- see `flydigi/registry.py`. None
        # is "whichever the bus offers first", which is what one pad means and
        # what this did before there could be two.
        self._selector = None
        self._stopping = False
        self._stop_recording = False

    def request_stop_recording(self):
        """End a recording early. Called straight from the UI thread.

        Deliberately not a slot. This thread is inside the recorder's own poll
        loop for the whole recording, so a queued slot call would not be
        delivered until the recording it is meant to stop had already ended.
        A plain bool, set from one thread and read from the other, is what
        `request_stop` does for the same reason.
        """
        self._stop_recording = True

    def request_stop(self):
        """Stop retrying. Called from the owning thread as the app quits.

        Only ever set, never cleared, and read as a plain bool -- so no lock is
        needed for it to do its job, which is to keep shutdown from waiting out
        a second full attempt.
        """
        self._stopping = True

    def _controller(self):
        """Open the pad, and refuse it if it is not the one this app drives.

        The check is here rather than at each write because this is the only
        place a handle is opened, and the device on the far end of an open
        handle does not change. One command-1 exchange per connection.

        It is not paranoia: `find_device` narrows to the controller family and
        its vendor collection, and every pad of the `5a a5` generation matches
        both -- a Vader 5 or an Apex 6 would open exactly like an Apex 5 and
        take an 840-byte Apex 5 profile into its flash without a word. (Nothing
        older can: those carry no Flydigi vendor id and no HID vendor
        collection at all.) The family nibble tells kinds of device apart,
        never models, so this check is still the only thing standing between
        two pads.
        """
        if self._ctrl is None:
            ctrl = registry.open_pad(self._selector)
            try:
                identity.require(ctrl)
            except identity.WrongDevice:
                ctrl.close()
                raise
            self._ctrl = ctrl
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
            except identity.WrongDevice as exc:
                # Not a stale handle and not worth a second attempt: the pad
                # answered, and it is the wrong pad. Retrying would ask the same
                # device the same question and hide the answer behind a delay.
                self._drop()
                self.failed.emit(str(exc))
                return None
            except (OSError, device.DeviceNotFound, device.DeviceBusy,
                    blobs.ProtocolError) as exc:
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

    # -- which devices are here, and which one we are driving ---------------

    @Slot()
    def refresh_devices(self):
        """Probe the whole bus and hand the result to the picker.

        On this thread because it is one exchange per device -- three, since it
        asks for uids and nicknames as well -- and the picker needs those: a
        selector may be a nickname, and two identical pads are told apart by
        nothing else.

        Never routed through `_attempt`. There is no handle to go stale, and a
        retry would double the traffic on a bus that is being polled anyway. A
        device that will not answer comes back as an entry saying so, which is
        what the list should show.
        """
        try:
            entries = registry.list_devices(deep=True)
        except OSError as exc:
            self.failed.emit(f"looking for devices: {exc}")
            return
        self.devices_changed.emit(entries)

    @Slot(str)
    def select_pad(self, selector):
        """Drive a different pad from now on, then say so.

        The handle is dropped rather than reused: it is open on the old pad,
        and every read after this belongs to the new one.

        **The re-read is driven by the reply, not by the picker.** Whoever
        chose the pad cannot ask for the reads itself: its request would be
        queued to this thread *before* this slot ran, so every read in it would
        go to the pad that was just switched away from -- the header would fill
        in with the old pad's battery and the new one would not be read until
        the next poll. Emitting from here means the requests are queued behind a
        switch that has already happened.
        """
        selector = str(selector or "") or None
        if selector == self._selector:
            return
        self._selector = selector
        self._drop()
        self.pad_selected.emit(selector or "")

    # -- the charging dock --------------------------------------------------
    #
    # A device of its own, opened per request rather than held. The pad is held
    # because the app polls it every thirty seconds and edits stream whole
    # profiles into it; a dock is read when its page is open and written when a
    # switch moves, and holding one would be holding a node another process
    # might want for no benefit.

    def _with_dock(self, selector, work, what):
        try:
            with registry.open_dock(selector) as dock:
                charger.require(dock)
                return work(dock)
        except (OSError, device.DeviceNotFound, device.DeviceBusy,
                charger.ProtocolError, charger.WrongDock) as exc:
            self.failed.emit(f"{what}: {exc}")
            return None
        except Exception as exc:              # a bug here, not a sulking dock
            self.failed.emit(f"{what}: {exc!r}")
            return None

    @Slot(str)
    def load_dock(self, selector):
        """Everything one dock will say: heartbeat, uid, name, lighting, status."""
        def work(dock):
            state = {
                "selector": selector,
                "info": charger.read_info(dock),
                "uid": charger.read_uid(dock),
                "nickname": charger.read_nickname(dock),
            }
            config = charger.read_led_config(dock)
            state["lighting"] = {
                "mode": config.mode, "brightness": config.brightness,
                "period": config.period, "direction": config.direction,
                "colours": [list(c) for c in config.colours],
            }
            # Unsolicited and about once a second, so this is a wait rather
            # than a question. None when none arrived, which the page says.
            state["status"] = charger.read_status(dock)
            return state

        state = self._with_dock(selector, work, "reading the charging dock")
        if state is not None:
            self.dock_state.emit(state)

    @Slot(str, str, bool)
    def set_dock_switch(self, selector, name, value):
        """One of the four switches, then the heartbeat back -- and only that.

        **Not read back, unlike the pad's**, and the difference is in the
        protocol rather than in how much is trusted. Command 19 covers every
        one of the pad's settings and its ack echoes the value without the
        sub-id, so nothing in a reply says *which* setting moved -- and an
        unsupported one acks and changes nothing, measured. A re-read of
        command 3 is the only way to know, and it happens to be exactly that
        page's own state, so it costs one exchange and disturbs nothing.

        Each of the dock's four switches is its own command, and
        `charger._set_flag` already raises when the dock does not answer that
        command. So pass and fail are known at the point of writing, and the
        page can be optimistic and be put back if it was wrong. What is not
        known is whether the dock can acknowledge a command and ignore it; the
        pad does that for a setting it lacks, and nothing here has been seen to.
        If one ever is, `charger.read_info` is one exchange and carries all four.

        Nothing else, and that is the change. It used to call `load_dock`, so
        confirming one bit also fetched the uid, the nickname, a forty-two
        packet read of the LED config and a wait for an unsolicited status
        frame -- none of which a switch can change. It cost about a second a
        toggle, and the lighting it dragged back landed on the page and put an
        effect somebody had chosen but not applied back to whatever the dock was
        still playing.
        """
        setter = {
            "sleep_when_charging": charger.set_sleep_when_charging,
            "led_sync": charger.set_led_sync,
            "close_with_system": charger.set_close_with_system,
            "show_animation_when_charging": charger.set_show_animation_when_charging,
        }.get(name)
        if setter is None:
            self.failed.emit(f"no such dock setting: {name}")
            return
        if self._with_dock(selector, lambda dock: setter(dock, value),
                           f"changing {name.replace('_', ' ')}") is None:
            # Said rather than left implied: the page moved the switch the
            # moment it was clicked, and a failure it is not told about leaves
            # it showing a state the dock never took.
            self.dock_switch.emit(selector, name, False)
            return
        self.status.emit(f"Dock: {name.replace('_', ' ')} "
                         f"{'on' if value else 'off'}")
        self.dock_switch.emit(selector, name, True)

    @Slot(str, dict)
    def write_dock_lighting(self, selector, wanted):
        """Generate an effect's frames and upload the lot. Seconds, not milliseconds.

        Deliberately not through `_attempt`: this is 487 packets and a few
        seconds, and a silent second attempt would double that and leave the
        dock's frame memory holding half of one animation and half of another.
        A failure says so and stops, which is what `write_led_config`'s own
        message is written for.
        """
        config = charger.LedConfig(
            mode=int(wanted.get("mode", charger.MODE_PULSE)),
            brightness=int(wanted.get("brightness", 50)),
            period=int(wanted.get("period", 1)),
            direction=int(wanted.get("direction", charger.DIR_NONE)),
            colours=[tuple(c) for c in wanted.get("colours") or ()])
        # A picture arrives already sampled, as one flat `bytes` -- the model
        # holds the decoded source images and `charger.generate` leaves a custom
        # config's frames exactly as it finds them.
        blob = wanted.get("frames")
        self.status.emit("Computing the dock's frames…")

        def work(dock):
            # Unpacked in here rather than above, so that a blob of the wrong
            # length is a failure `_with_dock` reports and finishes -- outside,
            # it would raise past `dock_finished` and leave the page busy for
            # the rest of the session.
            if blob:
                config.frames = charger.unpack_frames(bytes(blob))
                config.use_colour_count = 0
            charger.generate(config)
            self.status.emit(f"Uploading {len(config.frames)} frame(s) to the dock…")
            return charger.write_led_config(
                dock, config, progress=self.dock_progress.emit)

        packs = self._with_dock(selector, work, "writing the dock's lighting")
        self.dock_finished.emit(packs is not None)
        if packs is not None:
            self.status.emit(f"Dock lighting: {len(config.frames)} frame(s) in "
                             f"{packs} packet(s)")

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

        def work(ctrl):
            config = mapping.read_config(ctrl, cfg_id)
            # The read is what switches the pad, so this is Space Station's
            # "after every applied-config read" case literally: the profile now
            # running has trigger effects stored in it, and they do not start on
            # their own. Opening a profile therefore engages its effects, which
            # is what makes the Triggers page describe something real.
            effects.engage_stored(ctrl, config)
            return config

        config = self._attempt(work, f"reading profile {cfg_id + 1}")
        if config is None:
            return
        self.profile_loaded.emit(cfg_id, bytes(config.blob), config.title)
        self.active_changed.emit(cfg_id)
        self.status.emit(f"Profile {cfg_id + 1} read")

    @Slot(int)
    def reset_profile(self, cfg_id):
        """Command 175: put one slot back to factory and re-read it.

        The read afterwards is not a nicety. The slot is a different profile
        now, so everything the app is holding for it is stale -- and the read is
        also what puts the pad on the restored profile, which is the state the
        rest of this class assumes.
        """
        self.status.emit(f"Restoring profile {cfg_id + 1} to factory…")

        def work(ctrl):
            with ctrl.claim():
                if not mapping.reset_config(ctrl, cfg_id):
                    return None
                config = mapping.read_config(ctrl, cfg_id)
                effects.engage_stored(ctrl, config)
                return config

        config = self._attempt(work, f"restoring profile {cfg_id + 1}")
        if config is None:
            return
        self.profile_loaded.emit(cfg_id, bytes(config.blob), config.title)
        self.active_changed.emit(cfg_id)
        self.status.emit(f"Profile {cfg_id + 1} restored to factory "
                         f"— it is called {config.title!r} again")

    @Slot(int)
    def copy_to_switch(self, cfg_id):
        """Command 171: copy one profile into the matching Switch slot.

        171 carries a version and a slot id and no blob at all: the pad copies
        its own working memory into the slot named. So the profile has to be
        read first -- reading is what makes it the running one -- and anything
        a Switch cannot run has to be stripped *on the pad* rather than in a
        copy here, because a copy here is not what gets committed.

        That edit is then undone. It is working memory only -- no 166 anywhere
        in this method, so the source profile's saved copy is never at risk --
        but leaving someone's running profile silently normalised is a side
        effect they did not ask for. Nothing made in this app can trigger it:
        keyboard binding is host-side and unimplemented here, so `stripped` is
        empty unless the profile came from Space Station.
        """
        target = mapping.switch_cfg_id(cfg_id)
        self.status.emit(f"Copying profile {cfg_id + 1} to Switch slot {target}…")

        def work(ctrl):
            with ctrl.claim():
                config = mapping.read_config(ctrl, cfg_id)
                original = mapping.MappingConfig(bytes(config.blob), cfg_id)
                stripped = config.normalise_for_switch()
                if stripped:
                    mapping.write_config(ctrl, cfg_id, config, old=original)
                version = mapping.next_data_version(config.data_version)
                ok = mapping.save_switch_config(ctrl, target, version)
                if stripped:
                    # Put the running profile back. The stripped copy only had
                    # to exist so that 171 had something to commit -- the pad
                    # copies its own working memory and there is no blob in the
                    # packet, so the edit could not be avoided. Writing the
                    # original back is cheaper than the switch-away-and-back
                    # that would page it in from flash, and it costs no audible
                    # re-seat of the trigger motors.
                    mapping.write_config(ctrl, cfg_id, original, old=config)
                    config = original
                effects.engage_stored(ctrl, config)
                return ok, stripped, config

        result = self._attempt(work, f"copying profile {cfg_id + 1} to Switch")
        if result is None:
            return
        ok, stripped, config = result
        self.profile_loaded.emit(cfg_id, bytes(config.blob), config.title)
        self.active_changed.emit(cfg_id)
        if not ok:
            self.status.emit("The pad did not acknowledge the Switch copy")
            return
        note = f"; dropped: {', '.join(stripped)}" if stripped else ""
        self.status.emit(f"Profile {cfg_id + 1} copied to Switch slot "
                         f"{target}{note}")

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
        # Either way: the switch moved when it was clicked, and a failure it is
        # not told about leaves it claiming a handover that never happened.
        self.third_party_written.emit(bool(state))
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
            # Write and commit as one exchange: the save command commits
            # whatever is in the pad's working memory, so anything of ours that
            # got between the two would be committed along with this profile.
            with ctrl.claim():
                sent = mapping.write_config(ctrl, cfg_id, new, old=old)
                # A changed macro needs the profile paged in again before the
                # firmware will play it -- a write alone leaves it stored and
                # silent, which is measured, not assumed. Only when the macro
                # bytes moved: applying makes the pad re-seat its trigger
                # motors audibly, and a remap has no need of it.
                if sent and (old is None or new.macro_page != old.macro_page):
                    mapping.apply_config(ctrl, cfg_id)
                # Roll a fresh slot tag rather than re-committing the old one.
                # The tag is the only thing that tells another application its
                # cached copy of this profile is stale, so saving under the
                # previous value writes the change and announces that nothing
                # changed -- which is why a rename made here never showed up
                # anywhere else. Space Station rerolls on every save too.
                version = mapping.next_data_version(new.data_version)
                saved = mapping.save_config(ctrl, version) if save else False
                if saved:
                    new.data_version = version
                # A stored trigger effect is inert until a live command starts
                # it -- writing the block and applying the config engages
                # nothing, which is measured, not assumed. Space Station
                # replays it as command 81 per side after every applied-config
                # read, so we do the same after a write. Unconditional, like
                # theirs: live effect state survives a config apply, so "the
                # trigger bytes did not change" does not mean the pad is
                # running them.
                effects.engage_stored(ctrl, new)
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
            with ctrl.claim():
                sent = lighting.write_config(ctrl, new, old=old)
                # Lighting shares the mapping save command; the pad commits the
                # working set, not one config at a time. The lighting blob has
                # no version tag of its own, so the id this sends belongs to the
                # mapping profile that is going to be committed alongside it --
                # asked for here rather than assumed, because command 166 writes
                # whatever it is given into that slot's tag and `read_status`
                # reports the tag back as how anything knows its cached copy is
                # still current. Sending 0, which is what this did, saved the
                # lighting and silently told every later reader that the profile
                # had changed.
                #
                # Command 161 is the right way to ask: it reports the active
                # slot and a version per slot with no side effect at all, unlike
                # a config read, which pages the profile in. One exchange, on an
                # operation that already takes the pad seconds.
                saved = False
                if save:
                    status = mapping.read_status(ctrl)
                    version = (status["versions"][status["active"]]
                               if status else 0)
                    saved = mapping.save_config(ctrl, version)
            return sent, saved

        result = self._attempt(work, "writing lighting")
        if result is not None:
            self.lighting_written.emit(*result)

    @Slot(float)
    def record_macro(self, seconds):
        """Watch the pad and turn what is played into macro steps.

        Not routed through `_attempt`: this touches the evdev node rather than
        the hidraw one, so there is no stale handle to reconnect, and a silent
        second attempt would start a recording nobody asked for. It runs on
        this thread because it blocks -- for as long as someone keeps playing.
        """
        self._stop_recording = False
        self.status.emit("Recording — play the sequence on the pad")
        try:
            steps = macros.record(seconds,
                                  should_stop=lambda: self._stop_recording
                                  or self._stopping)
        except OSError as exc:
            self.failed.emit(f"recording a macro: {exc}")
            self.macro_recorded.emit([])
            return
        self.macro_recorded.emit(steps)
        self.status.emit(f"Recorded {len(steps)} step(s)" if steps
                         else "Nothing was recorded")

    # -- device settings ----------------------------------------------------

    def _settings_read(self, block):
        """One command-3 reply, delivered to both pages that live off it.

        The screen's two toggles are two bits of this same block, so a read here
        keeps the Screen page current too rather than each page polling the pad
        for the same thirteen bytes.
        """
        self.settings_changed.emit(block)
        self.screen_status.emit(screen.screen_bits(block))

    @Slot()
    def refresh_settings(self):
        block = self._attempt(settings.read_status, "reading the device settings")
        if block:
            self._settings_read(block)

    @Slot(str, int)
    def write_setting(self, name, value):
        """Write one setting, then report the block as the pad reads it back.

        Never what was asked for: a command-19 ack echoes the value and never
        the sub-id, so nothing in a reply says which setting moved. Worse, a
        setting the pad does not support is acknowledged and changed anyway --
        measured, and asserted in `tests/test_settings.py`. Command 3 says what
        is actually true, which is why `settings.apply` ends in a read of it.

        **That is why this reads back and the dock's switches do not.** Each of
        those is its own command and the write raises unless the dock answers
        it, so there is nothing a read would add; here there is no such thing as
        the reply to one setting. The read costs one exchange and returns
        exactly this page's own state, so unlike the dock's it has nothing
        unrelated to overwrite -- see `set_dock_switch` for what that cost.
        """
        block = self._attempt(lambda ctrl: settings.apply(ctrl, name, value),
                              f"changing {SETTING_LABELS.get(name, name).lower()}")
        if block:
            self._settings_read(block)
            self.status.emit(describe_setting(name, block))
        # Either way, because the page moved the control the moment it was
        # touched. On success the block above has already corrected it and this
        # only releases it; on failure it is the only thing that will.
        self.setting_written.emit(name, bool(block))

    # -- screen ------------------------------------------------------------

    @Slot()
    def refresh_screen(self):
        """The screen's state, read as part of the whole settings block.

        Deliberately not `screen.read_screen_status`: that is the same command 3
        with everything but four bits thrown away, and this page and the device
        settings page would then take turns asking for it.
        """
        block = self._attempt(settings.read_status, "reading the screen state")
        if block:
            self._settings_read(block)

    @Slot(int, bool)
    def set_screen_setting(self, sub_id, value):
        """One command-19 sub-setting, then read the block back.

        Read back rather than trusted: the reply to command 19 echoes the value
        and never the sub-id, so an ack says "a setting was written" and not
        which one. Command 3 says what actually happened.
        """
        def work(ctrl):
            settings.set_feature(ctrl, sub_id, value)
            return settings.read_status(ctrl)

        block = self._attempt(work, "changing a screen setting")
        if block:
            self._settings_read(block)
            if sub_id == screen.SUB_OFF_SCREEN:
                self.status.emit("Screen keeps your picture up" if block["always_on"]
                                 else "Screen dark when idle")
            else:
                self.status.emit("Status bar always on"
                                 if block["status_bar_always_on"]
                                 else "Status bar hides itself")

    @Slot(list, int, bool)
    def upload_screen(self, frames, interval, restore):
        """Put a picture on the screen, over Space Station's own serial route.

        Not routed through `_attempt`, and deliberately: that retries once, and
        retrying something that takes six minutes and has already switched the
        pad into upgrade mode is a decision for a person. A failure here leaves
        the pad reachable on its tty, so the honest thing is to say so and stop.
        """
        self.status.emit("Switching the screen into upgrade mode…")
        try:
            port = screen_ota.find_port()
            if port is None:
                # Asked again here, and not left to the model. Command 31 is
                # the irreversible half of this: measured on the dongle, the
                # pad takes it, switches its screen chip over and then reaches
                # the PC not at all, because the dongle does not relay the
                # bootloader's serial device. It stays in upgrade mode until it
                # is power-cycled. One exchange against six minutes of upload,
                # and it also covers a cable pulled between the button and here.
                info = motion.read_info(self._controller())
                if (info or {}).get("connect_type") != "wired":
                    raise screen_ota.OtaError(
                        "the pad is not on a cable. On the dongle it takes "
                        "command 31, switches to upgrade mode and then nothing "
                        "reaches the PC, which strands it until it is "
                        "power-cycled at its own switch.")
                screen_ota.enter_upgrade_mode(self._controller())
                # The pad keeps its HID nodes but the handle is no longer worth
                # holding across a reboot it is about to do.
                self._drop()
                time.sleep(screen_ota.SWITCH_SETTLE)
                port = screen_ota.wait_for_port()
            self.status.emit(f"Writing {len(frames)} frame(s) over {port}…")
            with screen_ota.OtaLink(port) as link:
                screen_ota.upload(link, frames, interval_ms=interval,
                                  restore_default=restore,
                                  progress=self.screen_progress.emit)
        except (screen_ota.OtaError, OSError, device.DeviceNotFound) as exc:
            self._drop()
            self.failed.emit(f"screen upload: {exc}")
            self.screen_finished.emit(False)
            return
        self._drop()
        self.screen_finished.emit(True)
        self.status.emit("Screen written — the pad reboots itself in about 15 seconds")

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
