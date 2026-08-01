# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""DualSense mode as state a view can bind to.

One switch for the whole system, not a per-game route: the virtual pad presents
a DualSense and any DS5-aware game gets it, including games Flydigi has never
heard of. `flydigi.dsmode` owns everything factual here -- the command line, the
module, what counts as running -- so this page and the CLI cannot disagree.

**The relay is not this window's child in any meaningful sense.** It is a device
the system now has, so closing the settings window does not take it away from a
running game, and the app can be started again to find DS mode already on. That
is why the state is read back from the process table on a timer rather than
remembered, and why nothing here holds a thread that lives as long as the relay:
its output goes to a file, which needs no reader alive to keep it from filling.

Starting is a `fork`+`exec` and returns at once -- the polkit dialog blocks the
*relay*, not us -- so only stopping needs a thread, because it waits for the
relay to put the pad's motors and triggers back.
"""
from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement

from flydigi import dsmode

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# How often the process table and the relay's log are re-read while the page is
# open. The relay can go on its own -- the pad sleeps and leaves the bus, the
# authentication is refused -- and a switch still showing "on" afterwards is
# worse than one that takes two seconds to notice.
POLL_MS = 2000


class StopThread(QThread):
    """SIGTERM, then wait. On a thread because the wait is up to five seconds.

    Short-lived by construction, which is what makes it safe to `wait()` on at
    shutdown -- unlike anything that lives as long as the relay does.
    """

    done = Signal(bool)

    def run(self):
        self.done.emit(dsmode.stop())


@QmlElement
class DsModeModel(QObject):
    """What the DualSense page binds to."""

    changed = Signal()
    busyChanged = Signal()
    statsChanged = Signal()
    pollingChanged = Signal()
    failed = Signal(str)
    # Ordinary news, which is most of it. Stopping is something the user asked
    # for, and even an unasked-for clean exit is not a fault -- reporting either
    # through `failed` put a red banner and the relay's whole closing summary on
    # screen for pressing the switch off.
    note = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = dsmode.state()
        self._proc = None
        self._stopper = None
        self._motors = True
        self._stats = {}
        self._detail = ""
        self._starting = False
        self._asked_to_stop = False
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._repoll)

    # -- state -------------------------------------------------------------

    @Property(bool, notify=changed)
    def available(self):
        """Whether this kernel has vhci-hcd at all.

        Apart from `moduleLoaded` on purpose: not loaded is not a problem, since
        the relay loads it as root at the moment DS mode is switched on. No
        module in the kernel is a problem, and a different sentence.
        """
        return bool(self._state.get("available"))

    @Property(bool, notify=changed)
    def moduleLoaded(self):
        return bool(self._state.get("loaded"))

    @Property(bool, notify=changed)
    def running(self):
        return bool(self._state.get("running"))

    @Property(bool, notify=busyChanged)
    def busy(self):
        """Starting or stopping. Covers the polkit dialog, which is not quick."""
        return self._starting or self._stopper is not None

    @Property(bool, notify=changed)
    def motors(self):
        """Whether haptic audio should drive the Apex 5's motors."""
        return self._motors

    @motors.setter
    def motors(self, value):
        value = bool(value)
        if self._motors != value:
            self._motors = value
            self.changed.emit()

    @Property(str, notify=statsChanged)
    def detail(self):
        """The relay's own last word, shown rather than paraphrased."""
        return self._detail

    @Property(int, notify=statsChanged)
    def outputReports(self):
        """Output reports from the game -- proof it bound to the virtual pad."""
        return self._stats.get("out", 0)

    @Property(int, notify=statsChanged)
    def hapticUrbs(self):
        """Isochronous URBs -- proof a game is writing haptic audio to it."""
        return self._stats.get("iso_urbs", 0)

    @Property(int, notify=statsChanged)
    def inputReports(self):
        """Reports served to the host -- proof the virtual pad is alive."""
        return self._stats.get("reports", 0)

    @Property(bool, notify=statsChanged)
    def padConnected(self):
        """Whether the Apex 5 itself is on the bus. Not whether DS mode is on.

        The two used to be the same thing and are not any more: the pad leaves
        the bus whenever it sleeps, and the relay now stays up without it. So
        the page can say "asleep" where it would once have said nothing,
        because the whole thing had died.

        True when the key is missing, which is how a relay from before this
        existed reads -- there is no way to tell from its output that the pad
        went away, and claiming it did would be worse than assuming it did not.
        """
        return bool(self._stats.get("pad", 1))

    @Property(int, notify=statsChanged)
    def padDrops(self):
        """How many times the pad has left the bus this session."""
        return self._stats.get("drops", 0)

    @Property(str, constant=True)
    def ignoreDevices(self):
        """The launch option, from the backend rather than restated here.

        Part of the feature, not a footnote: with DS mode on a game sees both
        the Apex 5 and the virtual DualSense, and nothing can hide a physical
        pad from a game that enumerates it.
        """
        return dsmode.IGNORE_DEVICES

    @Property(str, constant=True)
    def logPath(self):
        return dsmode.LOG_PATH

    # -- actions -----------------------------------------------------------

    @Slot()
    def refresh(self):
        """Re-read the system's state, and keep re-reading it.

        Unchanged for the QML that calls this: still "now, and every couple of
        seconds after". What is new is that `polling` can turn the second half
        of that back off again.
        """
        if self.polling:
            self._repoll()
        else:
            # Turning the poll on takes its own first reading, so asking for
            # one here as well would walk /proc twice for one refresh.
            self.polling = True

    @Slot(bool)
    def setRunning(self, value):
        if value:
            self._start()
        else:
            self._stop()

    def _start(self):
        if self.running or self._starting:
            return
        if not self.available:
            # The switch is disabled in this state, so getting here means
            # something else asked. Say why rather than spending an
            # authentication on a modprobe that cannot succeed.
            self.failed.emit("DualSense mode needs the vhci-hcd kernel module, "
                             "and this kernel does not have it.")
            return
        self._stats = {}
        self._detail = "Waiting for permission to attach a USB device…"
        self._starting = True
        self.statsChanged.emit()
        try:
            self._proc = dsmode.start(motors=self._motors, haptics=False)
        except OSError as exc:
            self._starting = False
            self._proc = None
            self._detail = ""
            self.statsChanged.emit()
            self.failed.emit(f"Could not start DualSense mode: {exc}")
            return
        self.busyChanged.emit()
        self.refresh()

    def _stop(self):
        if self._stopper is not None:
            return
        self._asked_to_stop = True
        self._detail = "Stopping…"
        self.statsChanged.emit()
        self._stopper = StopThread(self)
        self._stopper.done.connect(self._stopped)
        self._stopper.start()
        self.busyChanged.emit()

    def _stopped(self, ok):
        self._stopper = None
        self._repoll()
        self.busyChanged.emit()
        if not ok:
            # It is still running, so whatever it does next is not something
            # this asked for any more.
            self._asked_to_stop = False
            self.failed.emit("Could not stop DualSense mode — a relay is still "
                             "running. Stop it from a terminal: "
                             "pkill -f flydigi-ds5-usbip")

    # -- polling -----------------------------------------------------------

    @Property(bool, notify=pollingChanged)
    def polling(self):
        """Whether the process table is being re-read on a timer.

        Writable, so that something with a lifetime can own the poll. Reading
        the system costs milliseconds on the GUI thread -- see
        `flydigi.dsmode.state` -- and the only reader that ever needs the
        answer is a page somebody is looking at. Until this existed the poll
        was armed from a page's `Component.onCompleted` and stopped only at
        shutdown, and since Kirigami keeps a replaced page alive, one visit to
        the DualSense page left /proc being scanned every two seconds for the
        rest of the session, from whatever page the window was showing.

        Nothing starts it by itself: a model that has just been built is not
        polling, and stays that way until asked.

        The timer is the state rather than a flag kept beside it, because
        `wait()` stops the timer at shutdown and a flag would go on claiming a
        poll that no longer runs.
        """
        return self._poll.isActive()

    @polling.setter
    def polling(self, value):
        if bool(value) == self.polling:
            return
        if value:
            # Whoever turned this on is about to show the answer, and the last
            # one may be two seconds old -- or, on the first page visit of a
            # session, as old as the app.
            self._repoll()
            self._poll.start()
        else:
            self._poll.stop()
        self.pollingChanged.emit()

    def _repoll(self):
        was = dict(self._state)
        self._state = dsmode.state()
        running = self._state.get("running")

        if self._proc is not None and self._proc.poll() is not None:
            # It exited. Whether that is worth saying depends on what is running
            # now: with `host-spawn` in front, the handle is the wrapper's and
            # it can finish while the relay it started keeps going.
            code = self._proc.returncode
            self._proc = None
            self._starting = False
            if not running:
                self._report_exit(code)
        elif running:
            self._starting = False

        if running:
            self._stats = dsmode.latest_status()
            lines = dsmode.tail(count=1)
            if lines and not dsmode.parse_status(lines[0]):
                self._detail = lines[0]
            self.statsChanged.emit()
        elif was.get("running") and not running:
            self._stats = {}
            self._detail = ""
            self.statsChanged.emit()

        if self._state != was:
            self.changed.emit()
            self.busyChanged.emit()

    def _report_exit(self, code):
        """Say what happened, and only shout when something went wrong.

        The relay exits 0 on SIGTERM, which is what pressing the switch off
        sends -- so treating every exit as a failure meant turning DualSense
        mode off produced a red banner quoting the relay's closing summary.
        Three cases, and only the last is an error:

          * asked to stop, or exited cleanly -- ordinary news, if news at all;
          * 126/127 -- pkexec's own codes for a dismissed dialog or a refused
            password, which is a decision rather than a fault, but is worth
            saying because nothing else on screen would explain the switch
            going back;
          * anything else -- the relay explained itself before exiting, and its
            own words beat any summary: "no free hs port", "Apex 5 gamepad not
            found", "not readable as uid 1000 -- install the udev rules".
        """
        asked = self._asked_to_stop
        self._asked_to_stop = False
        self._detail = ""
        self.statsChanged.emit()
        self.busyChanged.emit()

        if asked or code == 0:
            # Nothing at all when it was asked for: the switch moving back is
            # the feedback, and a notification for an action just taken is
            # noise. An unasked-for clean exit does need a word -- Ctrl-C in a
            # terminal, or someone else's pkill -- or the switch would appear to
            # move on its own.
            if not asked:
                self.note.emit("DualSense mode stopped")
            return
        if code in (126, 127):
            self.failed.emit("DualSense mode was not started — the "
                             "authentication was cancelled or refused")
            return
        said = [line for line in dsmode.tail(count=4)
                if not dsmode.parse_status(line)]
        self.failed.emit(f"DualSense mode stopped (status {code})"
                         + (":\n" + "\n".join(said) if said else
                            f". See {dsmode.LOG_PATH}"))

    # -- shutdown ----------------------------------------------------------

    def wait(self, msecs=5000):
        """For shutdown: dropping a running QThread is a qFatal.

        Only the stop thread can be running here, and it finishes on its own.
        The relay is deliberately left alone -- see the module docstring.
        """
        try:
            # Through the property, so anything bound to `polling` is told. The
            # timer is the state, but for QML the notification *is* the state:
            # without this a binding goes on reporting a poll that has stopped.
            self.polling = False
        except RuntimeError:
            # Shutdown can arrive after the QML engine has already taken the
            # object graph down, leaving a Python wrapper around a deleted
            # QTimer. Nothing to stop in that case, and refusing to notice it
            # would turn a clean quit into a traceback.
            pass
        if self._stopper is not None:
            self._stopper.wait(msecs)
            self._stopper = None
