#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Smoke test for the application window, loaded exactly as `gui/main.py` does.

The window is brought up through `QQmlApplicationEngine`, not created from
inside a QML test: a top-level Window instantiated by the test engine is never
placed in a graphics scene, and every page it pushes says so. Loading it the
way the application does is both quieter and a truer test.

Only window-level facts are asserted here -- what is in the page stack, what
the models hold after startup. Anything that needs clicking lives in
tests/qml/, inside a window QtQuickTest shows and activates.

    python3 tests/test_shell.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

# **Before anything imports gui.** Two environment variables, both about not
# letting this suite touch the machine it runs on.
#
# The app now enumerates the bus at startup and writes the chosen pad into the
# preferences file, so without these a test run would probe whatever is plugged
# into the developer's desk -- and could rewrite their auto-mode preferences.
# Neither is a thing a test may do, and the second is the kind of damage nobody
# notices for a week.
#
# The mock bus is the answer to both halves of "what is attached": `hide_real`
# means the window sees these three devices and nothing else, on any machine.
_CONFIG_HOME = tempfile.mkdtemp(prefix="apex5-test-config-")
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME
_BUS = os.path.join(_CONFIG_HOME, "mock-bus.json")
with open(_BUS, "w") as _fh:
    json.dump({"hide_real": True, "devices": [
        {"kind": "pad", "code": "k5", "nickname": "Desk"},
        {"kind": "pad", "code": "k5", "nickname": "Couch", "battery": 2},
        {"kind": "dock", "type": 0, "nickname": "Shelf"},
    ]}, _fh)
os.environ["FLYDIGI_MOCK_BUS"] = _BUS

try:
    from PySide6.QtCore import QObject, QThread, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle
except ImportError:
    print("PySide6 not installed -- skipping shell tests")
    sys.exit(0)

from gui import app as gui_app
from gui import main as gui_main
from tests.qml_harness import TestPad

PASSED = []
FAILED = []
WARNINGS = []

# Every App started here owns a worker thread. Qt calls qFatal if a QThread is
# still running when it is destroyed, so a test that raises part way through
# would take the interpreter down at exit with a core dump rather than a
# failure message. Nothing may be left running.
STARTED = []

# Every section the window knows, in the order `Main.qml` lists them. Not what
# the sidebar shows at any given moment: a section belongs to a kind of device,
# and the drawer offers the ones belonging to whichever device is selected.
ALL_SECTIONS = ["Devices", "Controller", "Device", "Buttons", "Macros",
                "Sticks", "Gyro", "Vibration", "Triggers", "Lighting",
                "Screen", "Games", "DualSense", "Dock", "Setup"]

# What the sidebar offers with a pad selected, which is how the window starts.
SECTIONS = [name for name in ALL_SECTIONS if name != "Dock"]

# And with a dock selected. Three, because a pad's pages have nothing to say
# about a dock -- offering Buttons and Macros there would be offering to edit
# something that is not on screen.
DOCK_SECTIONS = ["Devices", "Dock", "Setup"]

# The real poll intervals, kept so the tests that shorten them can put them
# back -- they are module globals, and leaving one at 50 ms would have every
# later test polling a fake pad twenty times a second.
INFO_MS = gui_app.INFO_INTERVAL_MS
SEARCH_MS = gui_app.SEARCH_INTERVAL_MS


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def pump(qt_app, rounds=60):
    for _ in range(rounds):
        qt_app.processEvents()
        QThread.msleep(20)
        qt_app.processEvents()


def load_shell(qt_app, pad, controller=None):
    """Bring the window up against `pad`, the way main.py brings it up.

    `controller` stands in for the bus rather than for the pad: pass one that
    raises `DeviceNotFound` on demand and the pad can be taken away and put
    back, which is the only way to reach the reconnect path.
    """
    engine = gui_main.build_engine()
    engine.warnings.connect(
        lambda errors: WARNINGS.extend(e.toString() for e in errors))

    app_object = gui_main.app_singleton(engine)
    STARTED.append(app_object)
    app_object.start(False)
    worker = app_object.thread.worker
    worker._drop()
    # Both: the callable so a reconnect after `_drop` finds the fake again
    # rather than opening real hardware, and `_ctrl` so the shutdown path really
    # does call close() on it -- which is the thing worth testing about shutdown.
    worker._controller = controller or (lambda: pad)
    worker._ctrl = pad
    # What the window's `App.start()` does, now that the fake is in place.
    # Nothing else reads the pad at startup: the poll asks how it is doing and
    # the answer pulls the rest in, so a test that skipped this would be testing
    # an app that never looked at its pad.
    app_object.beginPolling()

    engine.load(QUrl.fromLocalFile(os.path.join(gui_main.QML_DIR, "Main.qml")))
    roots = engine.rootObjects()
    return app_object, engine, (roots[0] if roots else None)


def test_the_window_loads_and_pushes_its_first_page(qt_app):
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    check("the window loads", window is not None)
    if window is None:
        app_object.shutdown()
        return
    pump(qt_app)

    # replace() on an empty stack silently drops the page: the object is
    # created, it is just never shown. The count is what catches that.
    check("the first page is really in the stack",
          window.property("openPageCount") == 1,
          str(window.property("openPageCount")))
    check("the first page is the Controller page",
          window.property("openPageTitle") == "Controller",
          str(window.property("openPageTitle")))
    app_object.shutdown()


def test_startup_reads_only_the_open_profile(qt_app):
    pad = TestPad()
    pad.active = 1
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app)

    # Every config read makes the pad audibly re-seat its trigger motors, so
    # filling the slot list must not read all four.
    check("startup reads exactly one profile", len(pad.reads) == 1,
          str(pad.reads))
    # And it reads the one already running, which costs no switch at all --
    # opening slot 0 by default would switch the pad away and back for nothing.
    check("startup reads the profile the pad is running", pad.reads == [1],
          str(pad.reads))
    check("startup switches the pad not at all", pad.switches == [],
          str(pad.switches))
    check("the pad is still on the profile it started on", pad.active == 1,
          str(pad.active))
    check("the open profile is the running one",
          app_object.profile.cfgId == 1, str(app_object.profile.cfgId))
    check("all four slots are listed", app_object.profile.slots.count == 4)
    app_object.shutdown()


def test_the_models_reflect_what_the_pad_reported(qt_app):
    # Five, not six. Six is the charging sentinel, and a pad cannot report it
    # with the charging bit clear -- the fixture used to, which only looked
    # sensible while the scale was wrongly believed to run to eight.
    pad = TestPad(battery=5, wired=False)
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app)

    device = app_object.device
    check("the pad is reported connected", device.connected)
    check("battery comes from the pad", device.battery == 5, str(device.battery))
    check("and five is a full pad", device.battery == device.batterySteps)
    check("battery is reported in five steps, not eight", device.batterySteps == 5)
    check("the connection type comes from the pad",
          device.connectionType == "dongle", device.connectionType)
    check("the summary names the connection", "dongle" in device.summary,
          device.summary)
    check("lighting was read too", app_object.lighting.loaded)
    app_object.shutdown()


def test_a_charging_pad_says_so_rather_than_a_level(qt_app):
    pad = TestPad(charging=True)
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app)
    check("charging is reported", app_object.device.charging)
    app_object.shutdown()


def test_every_section_opens(qt_app):
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=20)

    # Spelled out rather than read back off the window: the drawer's contents
    # are part of what this is checking, so taking the expected list from the
    # thing under test would assert nothing. Every section, including the ones
    # the sidebar is not offering right now -- opening one has to work whether
    # or not its device is the selected one, since that is what the picker does.
    for index, name in enumerate(ALL_SECTIONS):
        window.openSection(index)
        pump(qt_app, rounds=5)
        check(f"the {name} section opens",
              window.property("openPageTitle") == name,
              str(window.property("openPageTitle")))
        check(f"the {name} section does not stack up",
              window.property("openPageCount") == 1,
              str(window.property("openPageCount")))
    app_object.shutdown()


def test_the_drawer_offers_every_section(qt_app):
    """Every section must be reachable from the sidebar, under its own name.

    `openSection(i)` working proves nothing about the drawer: the actions are
    written out one by one, and when the Screen page was added without one,
    every label after it shifted by a section and Setup fell off the end
    entirely. The window still worked; you simply could not get to Setup.
    """
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=20)

    # A QML array arrives as a QJSValue, which is not iterable from here.
    offered = window.property("drawerSections").toVariant() or []
    check("the drawer offers every section, in order", offered == SECTIONS,
          str(offered))

    for index, name in enumerate(SECTIONS[:len(offered)]):
        # And that each entry opens the page it is named after, rather than a
        # neighbour: last time the labels and the indices shifted together, so
        # a list of names alone would have looked right.
        window.pressDrawerAction(index)
        pump(qt_app, rounds=5)
        check(f"the sidebar's {name} entry opens {name}",
              window.property("openPageTitle") == name,
              str(window.property("openPageTitle")))
    app_object.shutdown()


def test_the_sidebar_follows_the_selected_device(qt_app):
    """Choosing a dock must not leave the pad's pages on offer.

    The window drives one pad and one dock at a time, and the pages of the two
    have nothing to do with each other -- a sidebar offering Buttons and Macros
    while a dock is selected is offering to edit something that is not on
    screen. So the sections belong to a kind of device, and the drawer shows
    the kind that is selected.

    The bus here is the mock one (see the top of this file), which is the only
    way this runs at all: it needs two pads and a dock.
    """
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=40)

    devices = app_object.devices
    check("the mock bus is what the window sees", devices.count == 3,
          str(devices.count))
    check("two pads and a dock",
          (devices.padCount, devices.dockCount) == (2, 1),
          f"{devices.padCount}/{devices.dockCount}")
    check("and it says they are not real", devices.hasMock)
    check("a pad is selected to begin with", not devices.currentIsDock)
    offered = window.property("drawerSections").toVariant() or []
    check("so the pad's sections are offered", offered == SECTIONS, str(offered))

    # The dock is last, after the two pads.
    devices.select(2)
    pump(qt_app, rounds=10)
    check("the window follows it", devices.currentIsDock)
    offered = window.property("drawerSections").toVariant() or []
    check("the sidebar is the dock's", offered == DOCK_SECTIONS, str(offered))
    check("and the Dock page is what is open",
          window.property("openPageTitle") == "Dock",
          str(window.property("openPageTitle")))
    check("the dock model was pointed at it",
          app_object.dock.selector == devices.dock,
          f"{app_object.dock.selector} vs {devices.dock}")

    # And back. Choosing a pad returns the pad's pages and the page it was on.
    devices.select(1)
    pump(qt_app, rounds=10)
    check("choosing a pad brings its sections back",
          (window.property("drawerSections").toVariant() or []) == SECTIONS)
    check("and the window is on a pad page again",
          window.property("openPageTitle") == "Controller",
          str(window.property("openPageTitle")))
    check("the dock is still remembered", devices.dock != "", devices.dock)
    # The picker reaching the worker at all. The re-read that follows is hung
    # off the worker's own reply rather than off the picker's signal -- see
    # `worker.select_pad` -- because a request queued from here would arrive
    # ahead of the switch and read the pad that was just switched away from.
    check("the worker was told which pad to open",
          app_object.thread.worker._selector == devices.pad,
          f"{app_object.thread.worker._selector} vs {devices.pad}")
    app_object.shutdown()


def test_choosing_another_pad_does_not_move_the_page(qt_app):
    """Only a change of *kind* navigates.

    Switching between two pads while editing lighting should leave you on
    Lighting; it is the same page, about a different device.
    """
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=40)

    window.openSection(ALL_SECTIONS.index("Lighting"))
    pump(qt_app, rounds=5)
    app_object.devices.select(1)
    pump(qt_app, rounds=10)
    check("the page stays put", window.property("openPageTitle") == "Lighting",
          str(window.property("openPageTitle")))
    app_object.shutdown()


def test_the_i18n_functions_are_installed(qt_app):
    """Kirigami calls these from QML and throws without them."""
    from PySide6.QtQml import QQmlEngine

    from gui import i18n as gui_i18n

    engine = QQmlEngine()
    shim = gui_i18n.install(engine)

    check("every i18n name is installed",
          all(not shim.property(name).isUndefined() for name in gui_i18n.NAMES),
          str([n for n in gui_i18n.NAMES if shim.property(n).isUndefined()]))

    # The one kirigami-addons calls from FormTextFieldDelegate: a domain, a
    # disambiguation context, then the message and its arguments.
    result = shim.property("i18ndc").call(
        ["kirigami-addons6", "@label", "%1/%2", 3, 10]).toString()
    check("i18ndc substitutes its arguments", result == "3/10", result)

    check("i18nc skips only the context",
          shim.property("i18nc").call(["@label", "%1 left", 7]).toString()
          == "7 left")
    check("i18n takes the message first",
          shim.property("i18n").call(["Battery %1"]).toString() == "Battery %1"
          or shim.property("i18n").call(["Battery %1", 5]).toString() == "Battery 5")
    check("a plural form picks the singular for one",
          shim.property("i18np").call(["%1 game", "%1 games", 1]).toString()
          == "1 game")
    check("a plural form picks the plural otherwise",
          shim.property("i18np").call(["%1 game", "%1 games", 4]).toString()
          == "4 games")


def test_a_game_list_update_that_fails_is_reported(qt_app):
    """The network is never touched here -- the reply handler is."""
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=10)

    # Seed a known list first. Comparing against whatever happened to be on
    # disk makes the "left alone" check vacuous on a machine with no cached
    # gamelist -- as the tautology this replaces was.
    app_object._fetched([{"enGameName": "Silksong", "isVibration": True},
                         {"enGameName": "Deathloop", "isPS5": True}], "")
    before = app_object.games.count
    check("the seeded list is there to be disturbed", before == 2, str(before))

    app_object._fetched(None, "Name or service not known")
    check("a failed update is reported",
          "Could not update the game list" in app_object.device.error,
          app_object.device.error)
    check("a failed update leaves the list alone",
          app_object.games.count == before,
          f"{before} -> {app_object.games.count}")
    check("the app is not left thinking it is still fetching",
          not app_object.fetchingGames)
    app_object.shutdown()


def test_a_game_list_update_that_succeeds_replaces_the_list(qt_app):
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=10)

    app_object._fetched([{"enGameName": "Silksong", "isVibration": True},
                         {"enGameName": "Deathloop", "isPS5": True}], "")
    check("the fetched list is shown", app_object.games.count == 2,
          str(app_object.games.count))
    check("the update is reported", "Game list updated" in app_object.device.status,
          app_object.device.status)
    check("no error is left behind", app_object.device.error == "",
          app_object.device.error)
    app_object.shutdown()


def test_an_unexpected_worker_error_is_reported(qt_app):
    """A bug in a worker slot used to vanish without a trace.

    `_attempt` caught only OSError, DeviceNotFound and ProtocolError. Anything
    else escaped the slot: no reply signal, no `failed`, nothing on screen, and
    the UI waiting forever. A method missing from the fake pad hid an entire
    untested code path that way.
    """
    class BrokenPad(TestPad):
        def send(self, buf, wait=0.3, until=None):
            raise AttributeError("no such thing")

    pad = BrokenPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app)

    check("an unexpected failure reaches the user",
          app_object.device.error != "", repr(app_object.device.error))
    check("and it says what actually went wrong",
          "no such thing" in app_object.device.error, app_object.device.error)
    app_object.shutdown()


def test_shutdown_stops_the_thread_before_closing_the_device(qt_app):
    """Closing the descriptor early is a race against the worker's own reads."""
    holder = {}

    class WatchfulPad(TestPad):
        def close(self):
            thread = holder.get("thread")
            holder["finished_when_closed"] = (thread is not None
                                              and thread.isFinished())

    pad = WatchfulPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app)
    holder["thread"] = app_object.thread.thread

    finished = app_object.thread.stop()
    check("the thread finished within the timeout", finished)
    check("the thread is really done", app_object.thread.thread.isFinished())
    check("the handle was closed", "finished_when_closed" in holder,
          "close() was never called")
    # os.close on the calling thread while the worker is blocked in select() on
    # that same descriptor is undefined, and the fd number can be reused.
    check("and only once the thread had stopped",
          holder.get("finished_when_closed") is True,
          str(holder.get("finished_when_closed")))

    app_object.thread = None
    app_object.shutdown()


def test_loading_the_window_is_warning_free(qt_app):
    """The engine must not report anything while bringing the app up."""
    check("no QML warnings while loading the window", not WARNINGS,
          "; ".join(WARNINGS[:5]))


def test_a_changed_macro_is_applied_and_a_remap_is_not(qt_app):
    """Measured on hardware: a macro is stored by the write and played by the
    apply. The same macros produced nothing until command 162 went out and
    played to the millisecond afterwards, so the write path sends one when the
    macro bytes move -- and not otherwise, since applying makes the pad
    audibly re-seat its trigger motors over a remap that never needed it."""
    from flydigi import mapping

    pad = TestPad()
    app_object, _engine, _window = load_shell(qt_app, pad)
    pump(qt_app)
    profile = app_object.profile
    if profile.config is None:
        check("a profile is open to edit", False)
        app_object.shutdown()
        return

    pad.switches.clear()
    profile.config.set_mapping("m2", "a")
    profile.markChanged()
    profile.write(False)
    pump(qt_app)
    check("a remap does not re-apply the profile", pad.switches == [],
          str(pad.switches))

    pad.switches.clear()
    profile.config.set_macro("m1", [
        {"delay": 0, "key": "a", "event": mapping.MACRO_PRESS},
        {"delay": 80, "key": "a", "event": mapping.MACRO_RELEASE}])
    profile.markChanged()
    profile.write(False)
    pump(qt_app)
    check("a macro edit re-applies the profile it was written to",
          pad.switches == [profile.cfgId], str(pad.switches))
    app_object.shutdown()


def test_a_vader_is_refused_before_anything_is_written(qt_app):
    """The guard, exercised through the worker rather than the library.

    Every Flydigi pad opens the same way -- one vendor id, one report descriptor
    -- so this is the only thing that stops the app streaming an Apex 5 profile
    into a Vader 4 Pro. The other cases here replace `_controller` wholesale and
    so never reach it; this one lets the worker open a device for real, with
    `device.Controller` standing in for the bus.
    """
    from gui import worker as gui_worker

    pad = TestPad(device_type=85)                    # a Vader 4
    app_object, engine, window = load_shell(qt_app, TestPad())
    worker = app_object.thread.worker
    # Undo the harness's shortcut so the guard is actually reached.
    del worker._controller
    worker._ctrl = None
    real_controller = gui_worker.device.Controller
    gui_worker.device.Controller = lambda *a, **k: pad

    failures = []
    # Direct, not the default queued connection: the worker lives on another
    # thread, so a queued `failed` would sit in its event queue and never reach
    # this assertion. `_attempt` is being called on this thread anyway.
    from PySide6.QtCore import Qt
    worker.failed.connect(failures.append, Qt.DirectConnection)
    try:
        result = worker._attempt(lambda ctrl: "written", "writing something")
        check("the write never ran", result is None, str(result))
        check("and it was reported", failures, str(failures))
        check("naming the pad that answered",
              failures and "Vader 4" in failures[0], str(failures))
        check("and what was expected",
              failures and "Apex 5" in failures[0], str(failures))
        # A refusal is not a stale handle: retrying would ask the same device
        # the same question, so it must be reported on the first attempt.
        check("without a second attempt", len(failures) == 1, str(failures))
    finally:
        gui_worker.device.Controller = real_controller
    app_object.shutdown()


def hotplug(qt_app, pad, absent, search_ms=50, info_ms=None):
    """Bring the window up over a pad that can be taken away and put back.

    `absent` is a one-key dict the caller flips; the worker asks it on every
    attempt, so the pad leaves and returns exactly as it does on a bus -- from
    the app's side, without anything telling it so.

    Both poll intervals are the module's own globals, so a caller shortening
    either must put it back with `restore_intervals`. `info_ms` is left alone by
    default: a test that wants to see the pad go away has to shorten it, and one
    that only wants to see it arrive can then assert the real thirty seconds are
    back in force.

    The intervals are set before `load_shell`, because it is what starts the
    poll and the interval it picks is read at that moment.

    The engine comes back with the app because it owns it: the App is the
    engine's QML singleton, so a caller that keeps only the app gets the C++
    object deleted underneath it the moment the engine is collected.
    """
    from flydigi import device as flydigi_device

    gui_app.SEARCH_INTERVAL_MS = search_ms
    if info_ms is not None:
        gui_app.INFO_INTERVAL_MS = info_ms

    def controller():
        if absent["still"]:
            raise flydigi_device.DeviceNotFound("no Flydigi controller found")
        return pad

    return load_shell(qt_app, pad, controller)


def restore_intervals():
    gui_app.INFO_INTERVAL_MS = INFO_MS
    gui_app.SEARCH_INTERVAL_MS = SEARCH_MS


def test_a_pad_that_arrives_late_is_found_and_read(qt_app):
    """The pad was not there when the window opened, and then it is.

    Two failures in one, and the second is the one that survived being noticed:
    a pad that answered late used to fill in the header and nothing else, so the
    sidebar said "Apex 5" over pages that had never been read. Nothing came back
    until someone pressed Reload.
    """
    pad = TestPad()
    pad.active = 2
    absent = {"still": True}
    app_object, engine, window = hotplug(qt_app, pad, absent)
    try:
        pump(qt_app)
        check("a pad that is not there is not claimed to be",
              not app_object.device.connected)
        check("and nothing is read off it", pad.reads == [], str(pad.reads))
        # Looked for on the short interval, which is the difference between
        # noticing a pad and noticing it half a minute later.
        check("a missing pad is looked for often",
              app_object._info_timer.interval() == 50,
              str(app_object._info_timer.interval()))

        absent["still"] = False
        pump(qt_app)
        check("the pad is found without anyone asking",
              app_object.device.connected)
        # The one the pad is running, and only that one: a reconnect is no
        # reason to make the pad re-seat its trigger motors four times.
        check("and the profile it is running is read", pad.reads == [2],
              str(pad.reads))
        check("the lighting comes back too", app_object.lighting.loaded)
        check("and so does the firmware version",
              app_object.device.firmware == "7.0.4.5",
              app_object.device.firmware)
        # And once it is there the hunt stops: a found pad is only being watched
        # for battery, and every ask costs it an exchange.
        check("a pad that is there is left alone",
              app_object._info_timer.interval() == INFO_MS,
              str(app_object._info_timer.interval()))
    finally:
        restore_intervals()
    app_object.shutdown()


def test_a_pad_that_was_there_all_along_is_read_once(qt_app):
    """Launching is a reconnect, and must cost what one costs.

    Startup and hotplug come down the same path on purpose -- the poll asks how
    the pad is doing, and the answer pulls the rest in -- so the thing to hold
    is that going through it once asks the pad each question once. It caught the
    real cost of joining them up: `reload` used to be kicked off by the window
    as well, and its info request arrived on the heels of the one that had just
    been answered.

    The hunt interval is left long on purpose. At 50 ms it fires two or three
    times before the first reply lands and slows it down again, and then the
    counts this is about are whatever the scheduler felt like.
    """
    from flydigi import mapping

    pad = TestPad()
    pad.active = 3
    absent = {"still": False}
    app_object, engine, window = hotplug(qt_app, pad, absent, search_ms=5000)
    try:
        pump(qt_app)
        check("the pad is there and says so", app_object.device.connected)
        check("its profile is read exactly once", pad.reads == [3],
              str(pad.reads))
        check("and it is asked what it is running once, not twice",
              pad.asked.count(mapping.CMD_STATUS) == 1,
              str(pad.asked.count(mapping.CMD_STATUS)))
    finally:
        restore_intervals()
    app_object.shutdown()


def test_a_dismissed_no_controller_message_stays_dismissed(qt_app):
    """The hunt runs every two seconds and fails the same way every time.

    Reporting each round put the banner back seconds after it was closed, for
    something the header already says in words. A failure that differs, or one
    that follows a spell of the pad answering, is still news.
    """
    pad = TestPad()
    absent = {"still": True}
    # Both shortened: the pad has to be seen going away again at the end, and
    # that is the slow interval's job.
    app_object, engine, window = hotplug(qt_app, pad, absent, info_ms=50)
    try:
        pump(qt_app, rounds=20)
        check("the first failure is reported", app_object.device.error != "",
              app_object.device.error)
        app_object.device.error = ""                  # what Dismiss does
        pump(qt_app, rounds=40)                       # many more poll rounds
        check("and dismissing it makes it stay gone",
              app_object.device.error == "", app_object.device.error)
        check("while the header still says what is wrong",
              not app_object.device.connected)

        # The pad answering and going again is a change, so it is news again.
        absent["still"] = False
        pump(qt_app, rounds=20)
        check("the pad is found", app_object.device.connected)
        absent["still"] = True
        pump(qt_app, rounds=20)
        check("and losing it afterwards is reported",
              app_object.device.error != "", app_object.device.error)
    finally:
        restore_intervals()
    app_object.shutdown()


def test_a_pad_that_comes_back_keeps_unsaved_edits(qt_app):
    """Sleeping and waking is not the same request as pressing Reload.

    The pad sleeps in minutes and an editing session touches it not at all, so
    this is the ordinary way an edit meets a reconnect -- and re-reading over
    the top of it would be the app throwing away work nobody asked it to.
    """
    pad = TestPad()
    pad.active = 1
    absent = {"still": False}
    # Both intervals shortened: the pad has to be seen leaving as well as
    # arriving, and leaving is only ever noticed by the slow one.
    app_object, engine, window = hotplug(qt_app, pad, absent, info_ms=50)
    try:
        pump(qt_app)
        app_object.profile.title = "Unsaved"
        app_object.lighting.brightness = 3
        check("the edits are there to lose", app_object.profile.dirty
              and app_object.lighting.dirty)

        absent["still"] = True
        pump(qt_app)
        check("the pad going away is noticed", not app_object.device.connected)
        absent["still"] = False
        pump(qt_app)

        check("it comes back", app_object.device.connected)
        check("the profile edit survives it",
              app_object.profile.title == "Unsaved",
              app_object.profile.title)
        check("the lighting edit too", app_object.lighting.brightness == 3,
              str(app_object.lighting.brightness))
        # Not re-read, which is the same fact from the pad's side: a second read
        # here is exactly what would have overwritten the edit.
        check("and the profile is not read over the top of it",
              pad.reads == [1], str(pad.reads))
    finally:
        restore_intervals()
    app_object.shutdown()


def test_leaving_a_section_destroys_its_page(qt_app):
    """A page the window has navigated away from must actually go.

    **It did not, for the whole life of the window.** `pageFor` memoised every
    page it built and nothing ever called `pop`, `clear` or `destroy`, and
    Kirigami does not destroy a replaced page either: `ColumnView::replaceItem`
    gates its `deleteLater` on `shouldDeleteOnRemove`, which is false as soon as
    an item has a visual parent, and the page is created with `pageStack` as its
    parent. So every section ever opened stayed alive, and a live page's
    bindings re-evaluate whether or not anyone can see them -- seven profile
    pages meant seven footers recomputing on every `dirtyChanged` for six
    footers nobody was looking at.

    Nothing else here can see that. The window looks and behaves identically
    either way; the only difference is how much work a notification causes. So
    this asserts the page object itself is gone, by name, from the object tree.
    """
    pad = TestPad()
    app_object, engine, window = load_shell(qt_app, pad)
    pump(qt_app, rounds=20)

    check("the window opens on Controller",
          window.property("openPageTitle") == "Controller",
          str(window.property("openPageTitle")))
    check("and that page exists",
          window.findChild(QObject, "controllerPage") is not None)

    window.pressDrawerAction(SECTIONS.index("Triggers"))
    # Long enough for the deferred `destroy` to run: it is deferred on purpose,
    # because the outgoing page is still being animated away when the window
    # asks for it to go.
    pump(qt_app, rounds=80)

    check("the section that was opened is there",
          window.findChild(QObject, "triggersPage") is not None)
    check("and the one that was left is not",
          window.findChild(QObject, "controllerPage") is None)

    # Going back rebuilds it, which is the other half of the bargain.
    window.pressDrawerAction(SECTIONS.index("Controller"))
    pump(qt_app, rounds=40)
    check("going back builds it again",
          window.findChild(QObject, "controllerPage") is not None)
    check("and shows it", window.property("openPageTitle") == "Controller",
          str(window.property("openPageTitle")))
    app_object.shutdown()


def main():
    QQuickStyle.setStyle("org.kde.desktop")
    qt_app = QGuiApplication.instance() or QGuiApplication([])
    try:
        for test in (test_the_window_loads_and_pushes_its_first_page,
                     test_startup_reads_only_the_open_profile,
                     test_the_models_reflect_what_the_pad_reported,
                     test_a_charging_pad_says_so_rather_than_a_level,
                     test_every_section_opens,
                     test_the_drawer_offers_every_section,
                 test_leaving_a_section_destroys_its_page,
                     test_the_sidebar_follows_the_selected_device,
                     test_choosing_another_pad_does_not_move_the_page,
                     test_the_i18n_functions_are_installed,
                     test_a_game_list_update_that_fails_is_reported,
                     test_a_game_list_update_that_succeeds_replaces_the_list,
                     test_a_changed_macro_is_applied_and_a_remap_is_not,
                     test_an_unexpected_worker_error_is_reported,
                     test_shutdown_stops_the_thread_before_closing_the_device,
                     test_a_vader_is_refused_before_anything_is_written,
                     test_a_pad_that_arrives_late_is_found_and_read,
                     test_a_pad_that_was_there_all_along_is_read_once,
                     test_a_dismissed_no_controller_message_stays_dismissed,
                     test_a_pad_that_comes_back_keeps_unsaved_edits,
                     test_loading_the_window_is_warning_free):
            try:
                test(qt_app)
            except Exception as exc:                  # a broken test, not a failure
                FAILED.append(test.__name__)
                print(f"  ERROR {test.__name__}: {exc!r}")
    finally:
        # Even if a test raised: a live worker thread at interpreter shutdown
        # is a qFatal, which buries the real error under a core dump.
        for app_object in STARTED:
            app_object.shutdown()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
