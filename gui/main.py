# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""QML entry point.

The models reach QML as the `App` singleton of the Apex5 module, not as context
properties. Importing `gui.app` is what registers the module -- the QmlElement
decorators run at import time -- so the import below is load-bearing even
though `App` is only named once here.

Registered types are also what lets qmllint check the QML: see
`tools/generate-qmltypes`.
"""
import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlTypeId
from PySide6.QtQuickControls2 import QQuickStyle

from . import i18n, watchdog
from .app import App  # noqa: F401  -- registers the Apex5 QML module

QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")

QML_MODULE = "Apex5"
QML_MODULE_MAJOR = 1
QML_MODULE_MINOR = 0

# Where a distribution keeps Kirigami. The system PySide6 finds these on its
# own; listing them makes an unusual layout (a Flatpak, a venv pointed at the
# system Qt) work without an environment variable.
SYSTEM_QML_PATHS = ("/usr/lib64/qt6/qml", "/usr/lib/qt6/qml", "/usr/lib/qml")


def build_engine():
    """An engine with the Kirigami import paths set and i18n available."""
    engine = QQmlApplicationEngine()
    for path in SYSTEM_QML_PATHS:
        if os.path.isdir(path) and path not in engine.importPathList():
            engine.addImportPath(path)
    # Kirigami's own components call i18n* and there is no KLocalizedContext to
    # be had from PySide6; without this they throw. See gui/i18n.py.
    i18n.install(engine)
    return engine


def app_singleton(engine):
    """The one `App`, created by the QML engine on first access."""
    return engine.singletonInstance(
        qmlTypeId(QML_MODULE, QML_MODULE_MAJOR, QML_MODULE_MINOR, "App"))


def main():
    # Must precede the first QQuickWindow, or Controls render in the default
    # style and look alien on a KDE desktop.
    QQuickStyle.setStyle("org.kde.desktop")

    qt_app = QGuiApplication(sys.argv)
    qt_app.setApplicationName("openflydigi")
    qt_app.setApplicationDisplayName("OpenFlydigi")
    qt_app.setOrganizationName("openflydigi")
    qt_app.setDesktopFileName("openflydigi")
    qt_app.setWindowIcon(QIcon.fromTheme("input-gaming"))

    # Before the engine, so that loading the window is watched too. Creates
    # nothing and starts nothing unless FLYDIGI_STALL_WATCHDOG is set.
    watchdog.arm()

    engine = build_engine()
    engine.load(QUrl.fromLocalFile(os.path.join(QML_DIR, "Main.qml")))
    if not engine.rootObjects():
        print("Failed to load the QML interface", file=sys.stderr)
        return 1

    qt_app.aboutToQuit.connect(app_singleton(engine).shutdown)
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
