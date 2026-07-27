# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application window.

Scope is the controller itself. The charging dock is a separate SDK we have not
decompiled, and nothing here talks to it.
"""
import sys

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStatusBar,
    QTabWidget, QVBoxLayout, QWidget)

from .profiles import ProfilePage
from .triggers import TriggerPage
from .worker import DeviceThread

PROFILE_COUNT = 4
INFO_INTERVAL_MS = 30_000


class MainWindow(QMainWindow):
    # Requests go to the worker as signals, never as direct calls: calling a
    # slot on an object living in another thread just runs it on this one,
    # which would put blocking HID traffic back on the UI thread.
    request_info = Signal()
    request_profiles = Signal(int)
    request_vibration = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flydigi Apex 5")
        self.resize(760, 620)

        self.device = DeviceThread()
        worker = self.device.worker

        central = QWidget()
        layout = QVBoxLayout(central)

        header = QHBoxLayout()
        self.device_label = QLabel("Looking for a controller…")
        header.addWidget(self.device_label, 1)
        self.battery_label = QLabel("")
        header.addWidget(self.battery_label)
        self.reload_button = QPushButton("Reload from pad")
        self.reload_button.clicked.connect(self._reload)
        header.addWidget(self.reload_button)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.profile_page = ProfilePage()
        self.trigger_page = TriggerPage()
        self.tabs.addTab(self.profile_page, "Profiles && buttons")
        self.tabs.addTab(self.trigger_page, "Adaptive triggers")
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self.profile_page.write_requested.connect(worker.write_profile)
        self.profile_page.apply_requested.connect(worker.apply_profile)
        self.trigger_page.apply_vibration.connect(self.request_vibration)
        self.request_info.connect(worker.refresh_info)
        self.request_profiles.connect(worker.load_profiles)
        self.request_vibration.connect(worker.apply_vibration)
        worker.vibration_applied.connect(self._vibration_applied)

        worker.info_changed.connect(self._info_changed)
        worker.profiles_changed.connect(self.profile_page.set_profiles)
        worker.profile_written.connect(self._written)
        worker.status.connect(self.statusBar().showMessage)
        worker.failed.connect(self._failed)

        # Kick off the first read once the window is up, so it appears
        # immediately rather than after a second of blocking HID traffic.
        QTimer.singleShot(0, self._reload)
        self._info_timer = QTimer(self)
        self._info_timer.timeout.connect(self.request_info)
        self._info_timer.start(INFO_INTERVAL_MS)

    def _reload(self):
        self.request_info.emit()
        self.request_profiles.emit(PROFILE_COUNT)

    def _info_changed(self, info):
        self.device_label.setText(f"Apex 5 connected ({info['connect_type']})")
        if info["charging"]:
            self.battery_label.setText("Charging")
        else:
            self.battery_label.setText(f"Battery {info['battery_level']}/8")

    def _written(self, cfg_id, packets, saved):
        self.profile_page.confirm_written(cfg_id, packets, saved)
        where = "saved to flash" if saved else "in memory only"
        self.statusBar().showMessage(
            f"Profile {cfg_id + 1}: wrote {packets} packet(s), {where}", 8000)

    def _failed(self, message):
        self.statusBar().showMessage(message, 10_000)
        self.device_label.setText("Controller not responding")

    def _vibration_applied(self, name, sides):
        self.statusBar().showMessage(
            f"{name}: applied to {sides or 'nothing'}", 8000)

    def closeEvent(self, event):
        self.trigger_page.save_prefs()
        self.device.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Flydigi Apex 5")
    app.setApplicationDisplayName("Flydigi Apex 5")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
