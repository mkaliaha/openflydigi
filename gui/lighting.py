# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""RGB lighting.

Its own config on the pad, separate from the mapping profiles, so it has its
own write and save rather than sharing the profile page's.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

from flydigi import lighting

# The pad's built-in animations. Only the id is protocol; the names are ours,
# from watching what each does, so they are descriptions rather than Flydigi's
# labels.
MODES = [(0, "Off"), (1, "Static"), (2, "Breathing"), (3, "Rainbow"),
         (4, "Wave"), (5, "Ripple"), (6, "Chase"), (7, "Spectrum cycle")]


class LightingPage(QWidget):
    write_requested = Signal(bytes, bytes, bool)
    load_requested = Signal()

    def __init__(self):
        super().__init__()
        self._stored = None       # bytes as last read from the pad
        self._edited = None
        self._loading = False
        self._colour = (0, 128, 255)

        layout = QVBoxLayout(self)

        box = QGroupBox("Lighting")
        form = QFormLayout(box)

        self.mode = QComboBox()
        for _value, label in MODES:
            self.mode.addItem(label)
        self.mode.currentIndexChanged.connect(self._edit)
        form.addRow("Effect", self.mode)

        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, lighting.BRIGHTNESS_MAX)
        self.brightness_readout = QLabel("0")
        self.brightness.valueChanged.connect(
            lambda v: self.brightness_readout.setText(str(v)))
        self.brightness.valueChanged.connect(lambda _v: self._edit())
        row = QHBoxLayout()
        row.addWidget(self.brightness, 1)
        row.addWidget(self.brightness_readout)
        form.addRow("Brightness", row)

        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(1, 30)
        self.speed.valueChanged.connect(lambda _v: self._edit())
        form.addRow("Animation speed", self.speed)

        self.click_feedback = QCheckBox("React to rumble")
        self.click_feedback.setToolTip(
            "With this on the pad drives the lights from vibration itself, "
            "which can override a colour set here.")
        self.click_feedback.toggled.connect(self._edit)
        form.addRow(self.click_feedback)

        colour_row = QHBoxLayout()
        self.colour_button = QPushButton("Pick colour…")
        self.colour_button.clicked.connect(self._pick_colour)
        self.colour_preview = QLabel()
        self.colour_preview.setMinimumWidth(60)
        self.colour_preview.setAutoFillBackground(True)
        colour_row.addWidget(self.colour_button)
        colour_row.addWidget(self.colour_preview, 1)
        form.addRow("Solid colour", colour_row)

        layout.addWidget(box)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.write_button = QPushButton("Write to pad")
        self.write_button.clicked.connect(lambda: self._write(save=False))
        actions.addWidget(self.write_button)
        self.save_button = QPushButton("Write && save to flash")
        self.save_button.clicked.connect(lambda: self._write(save=True))
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self.setEnabled(False)

    def config_loaded(self, blob):
        self._stored = bytes(blob)
        self._edited = lighting.LedConfig(bytearray(blob))
        self.setEnabled(True)
        self._loading = True
        index = next((i for i, (value, _l) in enumerate(MODES)
                      if value == self._edited.mode), None)
        if index is None:
            # An effect we have no name for -- show the number rather than
            # silently snapping it to something else.
            self.mode.addItem(f"Effect {self._edited.mode}")
            index = self.mode.count() - 1
            MODES.append((self._edited.mode, f"Effect {self._edited.mode}"))
        self.mode.setCurrentIndex(index)
        self.brightness.setValue(self._edited.brightness)
        self.speed.setValue(max(1, self._edited.speed))
        self.click_feedback.setChecked(self._edited.click_feedback)
        self._colour = self._edited.led(0, 0)
        self._loading = False
        self._update_preview()
        self.info.setText(
            f"{self._edited.led_count} LEDs, protocol v{self._edited.version:#06x}. "
            "Picking a colour writes it to every animation frame.")
        self._mark_changes()

    def _pick_colour(self):
        from PySide6.QtGui import QColor
        current = QColor(*self._colour)
        chosen = QColorDialog.getColor(current, self, "Lighting colour")
        if not chosen.isValid():
            return
        self._colour = (chosen.red(), chosen.green(), chosen.blue())
        self._update_preview()
        if self._edited is not None:
            self._edited.set_solid(self._colour)
            self._mark_changes()

    def _update_preview(self):
        red, green, blue = self._colour
        self.colour_preview.setStyleSheet(
            f"background: rgb({red},{green},{blue}); border: 1px solid palette(mid);")

    def _edit(self):
        if self._loading or self._edited is None:
            return
        self._edited.mode = MODES[self.mode.currentIndex()][0]
        self._edited.brightness = self.brightness.value()
        self._edited.speed = self.speed.value()
        self._edited.click_feedback = self.click_feedback.isChecked()
        self._mark_changes()

    def _mark_changes(self):
        dirty = self._edited is not None and bytes(self._edited.blob) != self._stored
        self.write_button.setEnabled(dirty)
        self.save_button.setEnabled(dirty)

    def _write(self, save):
        if self._edited is None:
            return
        self.write_requested.emit(bytes(self._edited.blob), self._stored or b"", save)

    def confirm_written(self):
        if self._edited is not None:
            self._stored = bytes(self._edited.blob)
            self._mark_changes()
