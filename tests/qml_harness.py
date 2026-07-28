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
    """A fake pad that also answers status and device info.

    `FakePad` covers the mapping and lighting protocol and is shared with the
    backend tests, so the two extra commands the desktop app asks for live here
    rather than being pushed down into it.
    """

    def __init__(self, battery=5, charging=False, wired=True):
        super().__init__()
        self.switches = []
        self.reads = []
        self.binds = []
        self.battery = battery
        self.charging = charging
        self.wired = wired

    def send(self, buf, wait=0.3):
        buf = bytes(buf)
        # Answered before the checksum test, like CMD_GET_INFO: `bind_grip`
        # builds a command-82 packet without a trailing checksum -- Flydigi's
        # own NewXInput builder does not set one -- so FakePad's unconditional
        # check would reject a perfectly correct packet.
        if buf[3] == flydigi_device.CMD_SET_FORCE_TRIGGER_GRIP:
            self.binds.append(list(buf[5:13]))
            body = bytearray(32)
            body[0] = 0x04
            body[1], body[2] = flydigi_device.MAGIC1, flydigi_device.MAGIC2
            body[3] = flydigi_device.CMD_SET_FORCE_TRIGGER_GRIP
            body[6] = 1                          # ack_ok reads body[5] after the report id
            return [bytes(body)]
        if buf[3] == motion.CMD_GET_INFO:
            body = bytearray(32)
            body[0] = motion.INPUT_REPORT_ID
            body[3] = motion.CMD_GET_INFO
            body[6] = 0x59                       # device type
            body[7] = 1 if self.wired else 2
            body[12] = 0x10 if self.charging else (self.battery & 0x0F)
            return [bytes(body)]
        if buf[3] == mapping.CMD_STATUS:
            body = bytearray(32)
            body[0], body[1], body[2] = (0x04, flydigi_device.MAGIC1,
                                         flydigi_device.MAGIC2)
            body[3] = mapping.CMD_STATUS
            body[4] = 1
            body[6] = self.active
            for slot in range(4):
                body[7 + 2 * slot] = slot + 1
            return [bytes(body)]
        if buf[3] == mapping.CMD_READ:
            self.reads.append(buf[5])
        if buf[3] == mapping.CMD_APPLY:
            self.switches.append(buf[5])
        return super().send(buf, wait)

    # `bind_grip` calls this on the controller to read an ack. It is a static
    # method on the real Controller, and FakePad never grew one -- so applying
    # a game preset raised AttributeError, which is not in DeviceWorker's
    # except tuple. It escaped the slot silently: no binding, no `failed`, no
    # status, nothing on screen.
    ack_ok = staticmethod(flydigi_device.Controller.ack_ok)

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

    @Property("QVariantList", notify=changed)
    def binds(self):
        """Each rumble-to-trigger binding the pad was sent, as raw payloads."""
        return [list(b) for b in self._pad.binds]

    @Slot()
    def reset(self):
        """Put the pad back to factory state, blobs included.

        Counters alone are not enough: a case that writes a remap leaves it on
        the pad, and the next case then edits a key to the value it already has
        and sees nothing change. The lighting config needs the same treatment
        -- a case that applies an effect leaves those frames on the pad, so the
        next case choosing the same effect is genuinely not a change.
        """
        self._pad.blobs = {i: blank_blob(f"Profile {i + 1}") for i in range(4)}
        self._pad.led_blob = FakePad().led_blob
        self._pad.active = 0
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
        # Connected after each model's own handler, so by the time a counter
        # moves the model already holds the new config.
        app.thread.worker.profile_loaded.connect(self._count_profile_read)
        app.thread.worker.lighting_loaded.connect(self._count_lighting_read)

    def _count_profile_read(self, *_args):
        self._profile_reads += 1
        self.changed.emit()

    def _count_lighting_read(self, *_args):
        self._lighting_reads += 1
        self.changed.emit()

    @Property(int, notify=changed)
    def lightingReads(self):
        """How many lighting reads have landed. See `profileReads`."""
        return self._lighting_reads

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
