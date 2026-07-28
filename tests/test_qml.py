#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run the QML test suite.

The test cases are QML, in tests/qml/, and run inside the engine via
QtQuickTest -- Qt's own framework for this. That is deliberate: a delegate only
exists inside a running view, and reaching for one from Python means poking at
object trees through `findChild`, which does not see delegate-created items at
all. Written as QML, a test just addresses the item.

Model logic is not tested here. It lives in `tests/test_models.py`, needs no
engine and no display, and is the cheaper place to assert it.

    python3 tests/test_qml.py            # everything
    python3 tests/test_qml.py -functions # list the cases

Runs offscreen, so it needs no display and no controller. Kirigami must be
importable -- see gui/README.md for the runtime this expects.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Software rasterising and a basic render loop: an offscreen run has no GPU
# context to share, and the threaded loop would wait for one that never comes.
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QSG_RENDER_LOOP", "basic")

try:
    from PySide6.QtQuickTest import QUICK_TEST_MAIN_WITH_SETUP
except ImportError:
    print("PySide6 not installed -- skipping QML tests")
    sys.exit(0)

from PySide6.QtQuickControls2 import QQuickStyle

from tests.qml_harness import Setup

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    # Match the application: Controls resolve against the KDE desktop style,
    # and a page that only lays out correctly under Basic would pass here and
    # fail in front of a user.
    QQuickStyle.setStyle("org.kde.desktop")
    return QUICK_TEST_MAIN_WITH_SETUP(
        "apex5", Setup, sys.argv, os.path.join(HERE, "qml"))


if __name__ == "__main__":
    sys.exit(main())
