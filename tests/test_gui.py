#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Self-test for the desktop app, driving the real widgets against a fake pad.

Runs on Qt's offscreen platform, so it needs no display and no controller. It
exercises the window as a user would -- select a profile, change a combo box,
press a button -- rather than calling internals, because the bugs worth
catching here are in the wiring.

Skipped (exit 0) when PySide6 is not installed, so the backend's test run stays
dependency-free:

    .venv/bin/python tests/test_gui.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
except ImportError:
    print("PySide6 not installed -- skipping GUI tests")
    sys.exit(0)

from flydigi import device, mapping
from tests.fake_pad import FakePad

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


class TestPad(FakePad):
    """A fake pad that also answers status, and records profile switches."""

    def __init__(self):
        super().__init__()
        self.switches = []
        self.reads = []

    def send(self, buf, wait=0.3):
        buf = bytes(buf)
        if buf[3] == mapping.CMD_STATUS:
            body = bytearray(32)
            body[0], body[1], body[2] = 0x04, device.MAGIC1, device.MAGIC2
            body[3] = mapping.CMD_STATUS
            body[4] = 1
            body[6] = self.active
            for slot in range(4):
                body[7 + 2 * slot] = slot + 1     # a distinct version per slot
            return [bytes(body)]
        if buf[3] == mapping.CMD_READ:
            self.reads.append(buf[5])
        if buf[3] == mapping.CMD_APPLY:
            self.switches.append(buf[5])
        return super().send(buf, wait)

    def close(self):
        pass


def build_window(pad):
    """A MainWindow whose worker talks to `pad` instead of real hardware."""
    from gui.main import MainWindow

    window = MainWindow()
    worker = window.device.worker
    worker._drop()
    worker._controller = lambda: pad
    return window


def pump(app, _window, rounds=60):
    """Let the worker thread run and its queued signals land on this one."""
    for _ in range(rounds):
        app.processEvents()
        QThread.msleep(20)
        app.processEvents()


def test_startup_reads_only_the_open_profile(app):
    pad = TestPad()
    pad.active = 1
    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)

    check("startup reads exactly one profile", len(pad.reads) == 1,
          f"read {pad.reads}")
    check("startup reads the selected profile", pad.reads == [0], str(pad.reads))
    check("all four slots are listed",
          window.profile_page.selector.count() == 4)
    check("the active profile is marked",
          "in use" in window.profile_page.selector.itemText(1),
          window.profile_page.selector.itemText(1))
    window.device.stop()
    return pad, window


def test_reading_restores_the_active_profile(app):
    """Reading a config switches the pad, so the app must switch it back."""
    pad = TestPad()
    pad.active = 1
    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)

    check("read of a non-active profile is restored", pad.switches == [1],
          f"switches {pad.switches}")
    check("pad ends on the profile it started on", pad.active == 1,
          f"active {pad.active}")
    window.device.stop()


def test_selecting_a_profile_loads_it_once(app):
    pad = TestPad()
    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)
    before = len(pad.reads)

    window.profile_page.selector.setCurrentIndex(2)
    pump(app, window)
    check("selecting an unread profile reads it", len(pad.reads) == before + 1,
          f"reads {pad.reads}")

    window.profile_page.selector.setCurrentIndex(0)
    pump(app, window, rounds=10)
    window.profile_page.selector.setCurrentIndex(2)
    pump(app, window, rounds=10)
    check("revisiting a profile does not re-read it",
          len(pad.reads) == before + 1, f"reads {pad.reads}")
    window.device.stop()


def test_editing_enables_writing_and_writes_one_packet(app):
    pad = TestPad()
    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)
    page = window.profile_page

    check("nothing to write before editing", not page.write_button.isEnabled())

    row = mapping.APEX5_KEYS.index("m1")
    combo = page.table.cellWidget(row, 1)
    combo.setCurrentIndex(combo.findText("a"))
    check("editing marks the profile dirty", page.write_button.isEnabled())

    pad.packets_received = 0
    page.write_button.click()
    pump(app, window)
    check("one remap writes one packet", pad.packets_received == 1,
          f"got {pad.packets_received}")
    check("writing clears the dirty state", not page.write_button.isEnabled())

    written = mapping.MappingConfig(pad.blobs[0])
    check("the pad holds the remap", written.remapped() == {"m1": ("a", 0, 0)},
          str(written.remapped()))
    window.device.stop()


def test_save_button_commits_to_flash(app):
    pad = TestPad()
    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)
    page = window.profile_page

    row = mapping.APEX5_KEYS.index("m2")
    page.table.cellWidget(row, 2).setValue(10)      # turbo frequency
    check("turbo edit marks dirty", page.save_button.isEnabled())
    page.save_button.click()
    pump(app, window)
    check("saving reaches flash", len(pad.saved) == 4, str(list(pad.saved)))
    window.device.stop()


def test_rename_round_trips_through_the_pad(app):
    pad = TestPad()
    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)
    page = window.profile_page

    page.title_edit.setText("Racing")
    page.title_edit.textEdited.emit("Racing")
    check("rename marks dirty", page.write_button.isEnabled())
    page.write_button.click()
    pump(app, window)
    check("the pad holds the new name",
          mapping.MappingConfig(pad.blobs[0]).title == "Racing",
          mapping.MappingConfig(pad.blobs[0]).title)
    window.device.stop()


def test_reset_all_clears_every_remap(app):
    pad = TestPad()
    seeded = mapping.MappingConfig(pad.blobs[0])
    seeded.set_mapping("m3", "x")
    seeded.set_mapping("m4", "y")
    pad.blobs[0] = bytearray(seeded.blob)

    window = build_window(pad)
    # No explicit reload: the window schedules its own first read on startup,
    # and calling it again here would double-count.
    pump(app, window)
    page = window.profile_page
    check("existing remaps are shown",
          page._edited.remapped() == {"m3": ("x", 0, 0), "m4": ("y", 0, 0)},
          str(page._edited.remapped()))

    page.reset_button.click()
    check("reset clears them all", page._edited.remapped() == {},
          str(page._edited.remapped()))
    window.device.stop()


def test_trigger_page_lists_and_filters(app):
    pad = TestPad()
    window = build_window(pad)
    page = window.trigger_page
    page._games = [
        {"enGameName": "Forza Horizon 6", "modDownLoadUrl": "x",
         "modName": "ForzaDualSense.exe"},
        {"enGameName": "Deathloop", "isPS5": True},
        {"enGameName": "Silksong", "isVibration": True},
    ]
    page._populate()
    check("every game is listed", page.table.rowCount() == 3)

    page.search.setText("death")
    check("search filters", page.table.rowCount() == 1, f"{page.table.rowCount()}")
    check("search finds the right game",
          page.table.item(0, 0).text() == "Deathloop")

    page.search.setText("")
    page.only_supported.setCurrentText("vibration")
    check("route filter works", page.table.rowCount() == 1)
    check("route filter picks the pad-side game",
          page.table.item(0, 0).text() == "Silksong")
    window.device.stop()


def main():
    app = QApplication.instance() or QApplication([])
    for test in (test_startup_reads_only_the_open_profile,
                 test_reading_restores_the_active_profile,
                 test_selecting_a_profile_loads_it_once,
                 test_editing_enables_writing_and_writes_one_packet,
                 test_save_button_commits_to_flash,
                 test_rename_round_trips_through_the_pad,
                 test_reset_all_clears_every_remap,
                 test_trigger_page_lists_and_filters):
        test(app)
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
