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
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

try:
    from PySide6.QtCore import QThread, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle
except ImportError:
    print("PySide6 not installed -- skipping shell tests")
    sys.exit(0)

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

# The sections the global drawer offers, in order.
SECTIONS = ["Controller", "Buttons", "Sticks", "Vibration", "Triggers",
            "Lighting", "Screen", "Games", "DualSense", "Setup"]


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def pump(qt_app, rounds=60):
    for _ in range(rounds):
        qt_app.processEvents()
        QThread.msleep(20)
        qt_app.processEvents()


def load_shell(qt_app, pad):
    """Bring the window up against `pad`, the way main.py brings it up."""
    engine = gui_main.build_engine()
    engine.warnings.connect(
        lambda errors: WARNINGS.extend(e.toString() for e in errors))

    app_object = gui_main.app_singleton(engine)
    STARTED.append(app_object)
    app_object.start(False)
    worker = app_object.thread.worker
    worker._drop()
    # Both: the lambda so a reconnect after `_drop` finds the fake again rather
    # than opening real hardware, and `_ctrl` so the shutdown path really does
    # call close() on it -- which is the thing worth testing about shutdown.
    worker._controller = lambda: pad
    worker._ctrl = pad

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
    # thing under test would assert nothing.
    for index, name in enumerate(SECTIONS):
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
                     test_the_i18n_functions_are_installed,
                     test_a_game_list_update_that_fails_is_reported,
                     test_a_game_list_update_that_succeeds_replaces_the_list,
                     test_an_unexpected_worker_error_is_reported,
                     test_shutdown_stops_the_thread_before_closing_the_device,
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
