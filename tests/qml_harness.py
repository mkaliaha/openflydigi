# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test fixtures for the QML suite: a fake pad, and a way to assert about it.

QtQuickTest runs the test cases inside the QML engine, which is the only place
a delegate actually exists -- so the assertions live in QML too. What QML has
no way to see on its own is the pad: whether a write really reached it, what
ended up in the blob. `PadProbe` is that window, and nothing more.

The pad itself is `tests.fake_pad.FakePad`, shared with the backend tests, plus
the two commands only the desktop app asks for.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlTypeId

from flydigi import device as flydigi_device
from flydigi import mapping, motion
from tests.fake_pad import blank_blob

# Load-bearing: importing this runs the QmlElement decorators, which is what
# puts the Apex5 module into the QML type system. Without it the test files
# fail to compile with "module Apex5 is not installed".
from gui import i18n
from gui.app import App  # noqa: F401
from tests.fake_pad import FakePad

QML_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "gui", "qml")

SYSTEM_QML_PATHS = ("/usr/lib64/qt6/qml", "/usr/lib/qt6/qml", "/usr/lib/qml")


def as_dict(entry):
    """A real Python dict, whatever QML handed us."""
    to_variant = getattr(entry, "toVariant", None)
    if to_variant is not None:
        entry = to_variant()
    return {str(k): v for k, v in dict(entry).items()}


class TestPad(FakePad):
    """A fake pad that also answers device info and the live trigger bind.

    `FakePad` covers the mapping and lighting protocol and is shared with the
    backend tests, so the two extra commands the desktop app asks for live here
    rather than being pushed down into it.
    """

    def __init__(self, battery=5, charging=False, wired=True,
                 firmware=(0x70, 0x45), acquirer="SDL", device_type=128):
        super().__init__()
        # 128 is an Apex 5 -- the base model, and one of the two ids SDL's own
        # Flydigi driver recognises. It used to be 0x59, which is not a
        # DeviceType any Flydigi product has, so the identify guard could not
        # have been exercised against it. A test wanting a different pad passes
        # one: 85 or 91 is a Vader 4.
        self.device_type = device_type
        self.switches = []
        self.reads = []
        self.binds = []
        self.battery = battery
        self.charging = charging
        self.wired = wired
        # Two BCD bytes: 0x70 0x45 is 7.0.4.5, which is what the pad on the desk
        # runs and comfortably above the 7.0.3.0 the third-party toggle needs.
        self.firmware = firmware
        # Who takes the pad once it is allowed to be taken. Steam calls itself
        # SDL; a test wanting "allowed but nobody wants it" sets this to "".
        self.acquirer = acquirer
        self.holder = ""
        self.transport = {"controller_data": True, "raw_data": False,
                          "keyboard": False, "mouse": False, "third_party": False}

    def send(self, buf, wait=0.3, until=None):
        buf = bytes(buf)
        # Only the history is kept here; answering command 82 is `FakePad`'s
        # job now that it knows the trigger-effect family carries no checksum.
        # This used to build its own ack to dodge that check, which meant two
        # implementations of one reply and only one of them exercised.
        if buf[3] == flydigi_device.CMD_SET_FORCE_TRIGGER_GRIP:
            self.binds.append(list(buf[5:13]))
        if buf[3] == motion.CMD_GET_INFO:
            body = bytearray(32)
            body[0] = motion.INPUT_REPORT_ID
            body[3] = motion.CMD_GET_INFO
            body[6] = self.device_type
            body[7] = 1 if self.wired else 2
            body[12] = 0x10 if self.charging else (self.battery & 0x0F)
            # Seven BCD firmware versions follow the chip types. Only the main
            # one is populated: the rest read as all-zero, which is how a real
            # pad reports a component it does not have.
            body[16], body[17] = self.firmware
            return [bytes(body)]
        if buf[3] == motion.CMD_ENABLE_RAW:
            # 0xFF means "leave alone", so only the flags actually named move.
            for index, name in enumerate(("controller_data", "raw_data",
                                          "keyboard", "mouse", "third_party")):
                if buf[5 + index] != motion.UNCHANGED:
                    self.transport[name] = buf[5 + index] == 1
            # A real pad does not sit at the value you asked for: whoever is
            # waiting to acquire does so the moment it is allowed, and then sets
            # the transport to suit its own driver. The fake does the same, or
            # nothing here would ever exercise that.
            if self.transport["third_party"]:
                self.transport.update(controller_data=False, raw_data=True)
                self.holder = self.acquirer
            else:
                self.holder = ""
            body = bytearray(32)
            body[0] = motion.INPUT_REPORT_ID
            body[3] = motion.CMD_ENABLE_RAW
            body[6] = 1
            return [bytes(body)]
        if buf[3] == motion.CMD_READ_TRANSPORT:
            body = bytearray(32)
            body[0] = motion.INPUT_REPORT_ID
            body[3] = motion.CMD_READ_TRANSPORT
            for index, name in enumerate(("controller_data", "raw_data",
                                          "keyboard", "mouse", "third_party")):
                body[6 + index] = 1 if self.transport[name] else 0
            tag = self.holder.encode("ascii")[:20]
            body[11 : 11 + len(tag)] = tag
            return [bytes(body)]
        if buf[3] == mapping.CMD_READ:
            self.reads.append(buf[5])
        if buf[3] == mapping.CMD_APPLY:
            self.switches.append(buf[5])
        return super().send(buf, wait)

    def close(self):
        pass


class PadProbe(QObject):
    """What the QML tests are allowed to know about the pad."""

    changed = Signal()

    def __init__(self, pad, parent=None):
        super().__init__(parent)
        self._pad = pad

    @Property(int, notify=changed)
    def packetsReceived(self):
        return self._pad.packets_received

    @Property(int, notify=changed)
    def savedCount(self):
        return len(self._pad.saved)

    @Property(int, notify=changed)
    def badChecksums(self):
        return self._pad.bad_checksums

    @Property("QVariantList", notify=changed)
    def reads(self):
        return list(self._pad.reads)

    @Property("QVariantList", notify=changed)
    def switches(self):
        return list(self._pad.switches)

    @Property(int, notify=changed)
    def active(self):
        return self._pad.active

    @Property(bool, notify=changed)
    def failReads(self):
        """Make config reads go unanswered, as a sleeping pad does.

        The state the app is in at a cold start with the pad asleep, which is
        where the Buttons page used to show nothing at all.
        """
        return self._pad.fail_reads

    @failReads.setter
    def failReads(self, value):
        self._pad.fail_reads = bool(value)
        self.changed.emit()

    @Property("QVariantList", notify=changed)
    def binds(self):
        """Each rumble-to-trigger binding the pad was sent, as raw payloads."""
        return [list(b) for b in self._pad.binds]

    @Property("QVariantMap", notify=changed)
    def liveEffects(self):
        """The effect actually running on each trigger, by side id.

        Read off the pad and not out of the profile, because that is the whole
        distinction: storing an effect in the blob leaves the triggers loose,
        and only a live command 81 starts it. A test asserting against the
        model would pass with nothing engaged at all.
        """
        return {str(side): [mode] + list(params)
                for side, (mode, params) in self._pad.live_effects.items()}

    # -- device settings ---------------------------------------------------
    #
    # Read off the pad rather than off the model: the model shows a setting as
    # on the moment it is clicked, so asserting against it would pass whether or
    # not the write ever landed.

    @Property("QVariantMap", notify=changed)
    def settings(self):
        return dict(self._pad.settings)

    @Property(int, notify=changed)
    def sleepMinutes(self):
        return self._pad.sleep_minutes

    @Property(int, notify=changed)
    def precision(self):
        return self._pad.precision

    @Property(int, notify=changed)
    def sensitivity(self):
        return self._pad.sensitivity

    @Slot()
    def reset(self):
        """Put the pad back to factory state, blobs included.

        Counters alone are not enough: a case that writes a remap leaves it on
        the pad, and the next case then edits a key to the value it already has
        and sees nothing change. The lighting config needs the same treatment
        -- a case that applies an effect leaves those frames on the pad, so the
        next case choosing the same effect is genuinely not a change.
        """
        fresh = FakePad()
        self._pad.blobs = {i: blank_blob(f"Profile {i + 1}") for i in range(4)}
        self._pad.led_blob = fresh.led_blob
        # And the device settings, for the same reason: a case that turns
        # quick-switch off leaves it off, and the next case toggling it finds
        # nothing to change.
        self._pad.settings = dict(fresh.settings)
        self._pad.sleep_minutes = fresh.sleep_minutes
        self._pad.precision = fresh.precision
        self._pad.sensitivity = fresh.sensitivity
        self._pad.active = 0
        self._pad.fail_reads = False
        self.resetCounters()

    @Slot()
    def resetCounters(self):
        """Forget what the pad has been asked so far.

        One pad is shared by every case in a file, and each case brings the
        window up again -- which re-reads. Without this, "startup reads exactly
        one profile" counts every earlier case's read too.
        """
        self._pad.packets_received = 0
        self._pad.saved = {}
        self._pad.reads.clear()
        self._pad.switches.clear()
        self._pad.binds.clear()
        self._pad.bad_checksums = 0
        # Live effect state survives a config apply on real hardware, so it has
        # to be cleared deliberately here too -- otherwise a case asserting
        # "applying engaged the effect" would pass on what an earlier case left
        # running.
        self._pad.live_effects.clear()
        self._pad.live_binds.clear()
        self.changed.emit()

    @Slot(int, str, result=str)
    def targetOf(self, cfg_id, key):
        """What the pad now believes `key` sends, straight out of its blob."""
        config = mapping.MappingConfig(self._pad.blobs[cfg_id])
        return config.mapping(key)[0]

    @Slot(int, str, result=int)
    def turboOf(self, cfg_id, key):
        config = mapping.MappingConfig(self._pad.blobs[cfg_id])
        return config.mapping(key)[2]

    @Slot(int, result=str)
    def titleOf(self, cfg_id):
        return mapping.MappingConfig(self._pad.blobs[cfg_id]).title

    @Slot(int, str, result="QVariantList")
    def bankOf(self, cfg_id, side):
        """The nine points the pad would actually play, out of its own blob.

        The only assertion that proves a stick edit did anything: the polyline
        the UI edits is not what the firmware reads.
        """
        config = mapping.MappingConfig(self._pad.blobs[cfg_id])
        return [int(v) for v in config.joystick_shape(side)["bank"]]

    @Slot(int, str, result=int)
    def centerOf(self, cfg_id, side):
        config = mapping.MappingConfig(self._pad.blobs[cfg_id])
        return config.joystick_curve(side)["center"]

    @Slot(int, str, result=bool)
    def circularOf(self, cfg_id, side):
        config = mapping.MappingConfig(self._pad.blobs[cfg_id])
        return bool(config.joystick_shape(side)["circular"])

    @Slot(int, result="QVariantMap")
    def motionOf(self, cfg_id):
        """The gyro block as the pad now holds it.

        Handed over whole rather than one field at a time: the target, the mode
        and the two enable keys are written together, and a case that checked
        only the one it moved would miss the others going with it.
        """
        motion = mapping.MappingConfig(self._pad.blobs[cfg_id]).motion()
        return {"target": motion["target"], "useMode": motion["use_mode"],
                "enableType": motion["enable_type"],
                "key": motion["keys"][0] or "", "key2": motion["keys"][1] or "",
                "sensitivity": motion["sensitivity"],
                "deadZone": motion["dead_zone"]}

    @Slot(int, result=int)
    def remapCount(self, cfg_id):
        return len(mapping.MappingConfig(self._pad.blobs[cfg_id]).remapped())

    @Slot(int, str, str)
    def seedRemap(self, cfg_id, key, target):
        """Put a remap on the pad before the app reads it."""
        config = mapping.MappingConfig(self._pad.blobs[cfg_id])
        config.set_mapping(key, target)
        self._pad.blobs[cfg_id] = bytearray(config.blob)


class Fixture(QObject):
    """Test-only control over what the app was given to work with."""

    changed = Signal()

    def __init__(self, app, engine, parent=None):
        super().__init__(parent)
        self._app = app
        self._engine = engine
        self._shell = None
        self._profile_reads = 0
        self._lighting_reads = 0
        self._screen_reads = 0
        self._settings_reads = 0
        # Connected after each model's own handler, so by the time a counter
        # moves the model already holds the new config.
        app.thread.worker.profile_loaded.connect(self._count_profile_read)
        app.thread.worker.lighting_loaded.connect(self._count_lighting_read)
        app.thread.worker.screen_status.connect(self._count_screen_read)
        app.thread.worker.settings_changed.connect(self._count_settings_read)

    def _count_profile_read(self, *_args):
        self._profile_reads += 1
        self.changed.emit()

    def _count_lighting_read(self, *_args):
        self._lighting_reads += 1
        self.changed.emit()

    def _count_screen_read(self, *_args):
        self._screen_reads += 1
        self.changed.emit()

    def _count_settings_read(self, *_args):
        self._settings_reads += 1
        self.changed.emit()

    @Property(int, notify=changed)
    def settingsReads(self):
        """How many command-3 blocks have landed. See `profileReads`.

        Every write ends in one of these, so this is also how a case waits for
        a write to have been answered rather than merely sent.
        """
        return self._settings_reads

    @Property(int, notify=changed)
    def lightingReads(self):
        """How many lighting reads have landed. See `profileReads`."""
        return self._lighting_reads

    @Slot(int, result=str)
    def testImage(self, frames):
        """A file URL for a still picture, or for the committed animation.

        The screen model reads real files through `QImageReader`, so a test that
        wants a loaded picture needs one on disk. A still is generated here; an
        animation cannot be, because **Qt reads animated GIFs and cannot write
        them**. `QImageWriter.supportedImageFormats()` has no gif in it at all,
        and multi-page tiff and webp both write happily and then read back as a
        single frame. So `qml/four-frames.gif` is committed instead -- 213 bytes,
        ours rather than Flydigi's, and the only way to exercise the animation
        branch at all.

        `frames` is honoured for a still and ignored above one; the committed
        file has four, which is what a caller asking for an animation gets.
        """
        import os
        import tempfile

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QImage

        if frames > 1:
            return QUrl.fromLocalFile(
                os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "qml", "four-frames.gif")).toString()
        folder = tempfile.mkdtemp(prefix="apex5-screen-")
        path = os.path.join(folder, "picture.png")
        image = QImage(120, 60, QImage.Format_RGB888)
        image.fill(0x203040)
        image.save(path, "PNG")
        return QUrl.fromLocalFile(path).toString()

    @Property(int, notify=changed)
    def screenStatusReads(self):
        return self._screen_reads

    @Property(int, notify=changed)
    def profileReads(self):
        """How many profile reads have landed on the model.

        A test cannot wait on `App.profile.loaded`: it is still true from the
        previous case, so the wait returns at once and the read in flight
        overwrites the edit a moment later.
        """
        return self._profile_reads

    @Slot()
    def resetCounts(self):
        self._profile_reads = 0
        self._lighting_reads = 0
        self._screen_reads = 0
        self._settings_reads = 0
        self.changed.emit()

    @Slot(result=QObject)
    def shell(self):
        """The real application window, built once, the way main.py builds it.

        Creating Main.qml from QML instead -- Qt.createComponent inside a test
        -- makes every page it pushes warn that it was not placed in the
        graphics scene. Going through QQmlComponent here matches what the
        application actually does, and is quiet.
        """
        if self._shell is None:
            component = QQmlComponent(
                self._engine, QUrl.fromLocalFile(os.path.join(QML_DIR, "Main.qml")))
            self._shell = component.create()
            if self._shell is None:
                raise RuntimeError(
                    "Main.qml failed to load: "
                    + "; ".join(e.toString() for e in component.errors()))
        return self._shell

    @Slot("QVariantList")
    def seedGames(self, entries):
        """Replace the gamelist, so a test does not depend on a downloaded one.

        Each entry is copied into a real Python dict first. What arrives from a
        QML array is not one, and `App.requestVibration` is a `Signal(dict)`
        delivered across a thread boundary -- an entry Qt cannot marshal makes
        the queued call vanish silently: the signal emits, the worker slot never
        runs, and nothing is reported. The application itself is unaffected,
        because it loads the gamelist with json.load.
        """
        self._app.games.sourceModel().setGames([as_dict(e) for e in entries])

    @Slot()
    def clearGames(self):
        self._app.games.sourceModel().setGames([])


class Setup(QObject):
    """QtQuickTest instantiates this and calls the hooks it recognises."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._apps = []

    @Slot()
    def cleanupTestCase(self):
        """Stop every worker thread before the interpreter goes down.

        Qt calls qFatal when a QThread is destroyed while still running, which
        would end the run in a core dump instead of a test report.
        """
        for app in self._apps:
            app.shutdown()
        self._apps = []

    # The signature has to be QQmlEngine exactly: QtQuickTest looks the slot up
    # by signature, and a QObject parameter silently never matches.
    @Slot(QQmlEngine)
    def qmlEngineAvailable(self, engine):
        for path in SYSTEM_QML_PATHS:
            if os.path.isdir(path):
                engine.addImportPath(path)
        # The generated Apex5 module lives beside the sources; the decorators
        # register the types at import time, so this is only for QML's resolver.
        engine.addImportPath(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        # QtQuickTest builds its own engine, so the shim the application
        # installs in gui/main.py has to be installed here too, or every
        # Kirigami component that calls i18n* throws.
        i18n.install(engine)

        pad = TestPad()
        engine.rootContext().setContextProperty("Pad", PadProbe(pad, self))
        engine.rootContext().setContextProperty("QmlDir", QML_DIR)

        app = engine.singletonInstance(qmlTypeId("Apex5", 1, 0, "App"))
        # Start without the polling timer, then put the fake pad behind the
        # worker before anything asks the real one for something.
        app.start(False)
        app.thread.worker._drop()
        app.thread.worker._controller = lambda: pad
        self._apps.append(app)
        engine.rootContext().setContextProperty("Fixture", Fixture(app, engine, self))
