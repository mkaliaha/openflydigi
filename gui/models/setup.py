# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""System setup as state a view can bind to.

Every fact and every action comes from `flydigi.setup`, so this page and
`tools/apex5-setup` cannot disagree about whether a machine is set up.

Nothing here runs on the UI thread. The systemd calls are subprocesses, and
installing the udev rules blocks on a polkit dialog for as long as it takes
someone to find their password -- a frozen window for the length of an
authentication prompt is the worst possible moment to look broken. Each action
runs on a short-lived thread, the way `FetchThread` handles the game-list
download.

Starting at login and running now are separate switches because systemd keeps
them separate, and conflating them would mean "start it now" silently signed
you up for every login after that.
"""
import subprocess

from PySide6.QtCore import (Property, QAbstractListModel, QModelIndex, QObject,
                            QThread, Qt, Signal, Slot)
from PySide6.QtQml import QmlElement

from flydigi import setup

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# What each action does, off the UI thread. Kept as data so the worker has no
# opinions of its own and the set of things a view may ask for is one list.
ACTIONS = {
    "refresh": lambda: None,
    "installUnit": setup.install_unit,
    "removeUnit": setup.remove_unit,
    "start": lambda: setup.set_running(True),
    "stop": lambda: setup.set_running(False),
    "enable": lambda: setup.set_enabled(True),
    "disable": lambda: setup.set_enabled(False),
    "installDesktop": setup.install_desktop,
    "removeDesktop": setup.remove_desktop,
}


def install_rules():
    """Run the privileged half, escalating on the host.

    A non-zero return is usually someone pressing Cancel, which is a choice
    rather than a fault, so it is reported as plain text and not as an error.
    """
    argv = setup.escalation("install-rules")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"could not run {' '.join(argv)}: {exc}. Install the rules by hand:"
            f" sudo cp {setup.RULES_SOURCE} {setup.RULES_TARGET}")
    if proc.returncode != 0:
        raise RuntimeError("the rules were not installed — the authentication "
                           "was cancelled or refused")


ACTIONS["installRules"] = install_rules


class SetupWorker(QThread):
    """One action, then a fresh reading of the checklist."""

    done = Signal(str, object, str)   # action, checks, error

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self._action = action

    def run(self):
        error = ""
        try:
            ACTIONS[self._action]()
        except Exception as exc:                  # subprocess, permissions, OS
            error = str(exc)
        try:
            checks = setup.checks()
        except Exception:                         # a broken check must not
            checks = []                           # hide the action's result
        self.done.emit(self._action, checks, error)


@QmlElement
class SetupChecksModel(QAbstractListModel):
    """One row per requirement, in the order a person would fix them."""

    IdRole = Qt.UserRole + 1
    LabelRole = Qt.UserRole + 2
    StateRole = Qt.UserRole + 3
    DetailRole = Qt.UserRole + 4

    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checks = []
        # Built beside the list, never separately -- see `setChecks`.
        self._state_by_id = {}

    def roleNames(self):
        return {
            self.IdRole: b"checkId",
            self.LabelRole: b"label",
            # Not "state": every QML Item already has one, and a delegate
            # property of that name shadows it.
            self.StateRole: b"checkState",
            self.DetailRole: b"detail",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._checks)

    def data(self, index, role=Qt.DisplayRole):
        if not 0 <= index.row() < len(self._checks):
            return None
        check = self._checks[index.row()]
        if role in (self.LabelRole, Qt.DisplayRole):
            return check.label
        if role == self.IdRole:
            return check.id
        if role == self.StateRole:
            return check.state
        if role == self.DetailRole:
            return check.detail
        return None

    def setChecks(self, checks):
        """The one place the rows move, so the one place the index is built.

        `_state_by_id` is assigned here and nowhere else, and only ever from
        the list assigned on the line above it, so there is no ordering in
        which the two can disagree. `_checks` is not handed out and the rows
        are `setup.Check` namedtuples, so nothing outside can edit a state
        behind the index's back.

        `setdefault` rather than a comprehension so that a repeated id keeps
        the first row's state, which is what a scan for it found. `checks()`
        never emits one -- every id is a single if/elif chain -- but this takes
        whatever list it is handed, and a silent change of which duplicate wins
        is not worth saving a line.
        """
        self.beginResetModel()
        self._checks = list(checks or [])
        self._state_by_id = {}
        for check in self._checks:
            self._state_by_id.setdefault(check.id, check.state)
        self.endResetModel()
        self.countChanged.emit()

    def state(self, check_id):
        """One requirement's state, or UNKNOWN if it was not reported.

        A lookup rather than a scan of the rows. Seven of `SetupModel`'s
        properties are answered from here and all seven hang off one `changed`,
        so a reading of the checklist asks this up to fourteen times -- `ready`
        alone asks up to five, `rulesNeeded` up to four -- and each of those
        was a walk of the ten rows looking for an id.
        """
        return self._state_by_id.get(check_id, setup.UNKNOWN)

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._checks)


@QmlElement
class SetupModel(QObject):
    """What the Setup page binds to."""

    changed = Signal()
    busyChanged = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checks = SetupChecksModel(self)
        self._thread = None
        # The one that has reported but may not have exited -- see `_run`.
        self._previous = None
        self._loaded = False
        self._desktop_command = ""

    # -- state -------------------------------------------------------------

    @Property(SetupChecksModel, constant=True)
    def checks(self):
        return self._checks

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._thread is not None

    @Property(bool, notify=changed)
    def loaded(self):
        """False until the first reading arrives, so the page can say so."""
        return self._loaded

    @Property(bool, notify=changed)
    def ready(self):
        return self._loaded and not any(
            self._checks.state(c) == setup.FAIL
            for c in ("hidraw", "uhid", "input", "rules", "unit"))

    @Property(bool, notify=changed)
    def unitInstalled(self):
        return self._checks.state("unit") == setup.OK

    @Property(bool, notify=changed)
    def running(self):
        return self._checks.state("running") == setup.OK

    @Property(bool, notify=changed)
    def startAtLogin(self):
        return self._checks.state("enabled") == setup.OK

    @Property(bool, notify=changed)
    def desktopInstalled(self):
        return self._checks.state("desktop") == setup.OK

    @Property(str, notify=changed)
    def desktopCommand(self):
        """What the menu entry would run, so it is visible before installing.

        The app is normally started from a terminal here, and a launcher whose
        command nobody can see is the kind of thing that quietly points at the
        wrong checkout after a move.

        **Worked out once, because working it out touches the filesystem.**
        `setup.desktop_exec` opens /run/.containerenv to name the box and then
        asks `shutil.which` for distrobox's helper, which walks every directory
        on PATH. That is a syscall-per-read getter on the UI thread, in the one
        file whose whole shape is an argument for keeping blocking work off it,
        and it re-ran on every `changed` -- which every action on this page
        emits.

        Holding the answer for the life of the process is honest because none
        of its inputs can move while the process runs: which container we are
        inside, and where this checkout is. Installing does not read it either
        -- `setup.desktop_text` calls `desktop_exec` itself, on the worker
        thread -- so the file that gets written is never taken from here.
        """
        if not self._desktop_command:
            self._desktop_command = setup.desktop_exec()
        return self._desktop_command

    @Property(bool, notify=changed)
    def rulesNeeded(self):
        """Whether anything actually calls for the privileged step.

        The rules were once not a requirement in themselves -- on this system
        the hidraw nodes are already world-accessible, and asking for root when
        nothing is broken is how a checklist teaches people to click through it.
        That held while every device they cover could be tested at rest. It
        stopped holding with the screen: its bootloader is a tty that exists
        only while an upload has the pad switched over, so a missing rule cannot
        be seen from here and shows up instead as an upload that dies with the
        pad off the HID bus. `setup.checks` now fails an absent rules file for
        that reason, and this follows it.
        """
        return any(self._checks.state(c) == setup.FAIL
                   for c in ("hidraw", "uhid", "input", "rules"))

    @Property(bool, notify=changed)
    def rulesInstalled(self):
        """So the page can say "installed" rather than guessing "not needed"."""
        return self._checks.state("rules") == setup.OK

    # -- actions -----------------------------------------------------------

    @Slot()
    def refresh(self):
        self._run("refresh")

    @Slot()
    def installUnit(self):
        self._run("installUnit")

    @Slot()
    def removeUnit(self):
        self._run("removeUnit")

    @Slot(bool)
    def setRunning(self, value):
        self._run("start" if value else "stop")

    @Slot(bool)
    def setStartAtLogin(self, value):
        self._run("enable" if value else "disable")

    @Slot()
    def installRules(self):
        self._run("installRules")

    @Slot()
    def installDesktop(self):
        self._run("installDesktop")

    @Slot()
    def removeDesktop(self):
        self._run("removeDesktop")

    def _run(self, action):
        if self._thread is not None:
            return
        # Join the previous one before letting go of it. It has emitted `done`
        # -- that is why it is here rather than in `_thread` -- but `done` is
        # the last statement of `run()`, so it need not have returned yet, and
        # this is the only place the object is dropped. Nothing waits here in
        # practice for the same reason: there is one statement left to run.
        if self._previous is not None:
            self._previous.wait()
            self._previous = None
        # Unparented, and held by these two attributes alone. As a child of the
        # model each worker outlived its work: `_finished` let go of the Python
        # reference but the parent kept the object, so one dead QThread stayed
        # on the model per reading -- and the page takes a reading every time it
        # is opened and again on every "Check again".
        self._thread = SetupWorker(action)
        self._thread.done.connect(self._finished)
        self._thread.start()
        self.busyChanged.emit()

    def _finished(self, action, checks, error):
        # `busy` goes false here, but the thread is not gone yet -- see `_run`
        # and `wait`. Nulling `_thread` without keeping the handle is what left
        # `wait` with nothing to wait on.
        self._thread, self._previous = None, self._thread
        self._loaded = True
        self._checks.setChecks(checks)
        self.busyChanged.emit()
        self.changed.emit()
        if error:
            self.failed.emit(error)

    def wait(self, msecs=5000):
        """For shutdown: dropping a running QThread is a qFatal.

        Both handles, because `_finished` runs the moment `done` reaches this
        thread's event loop and the worker has one statement to go after
        emitting it. A window closed inside that gap used to find `_thread`
        already None, wait on nothing, and then destroy a QThread that had not
        returned from `run()`.

        **A handle is dropped only when its thread has really finished.** The
        wait is bounded, and a bounded wait can time out with the thread still
        running -- `installRules` is behind a polkit prompt whose subprocess
        timeout is five minutes, against five seconds here. Clearing the handle
        anyway would drop the last reference to a running QThread, since these
        are unparented, which is precisely the qFatal this exists to prevent.
        A worker still going is kept instead, and the process takes it with it.
        `gui/models/screen.py`'s encode worker is held on the same terms.
        """
        for name in ("_thread", "_previous"):
            thread = getattr(self, name)
            if thread is not None and thread.wait(msecs):
                setattr(self, name, None)
