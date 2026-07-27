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

# Effects we generate frames for. The pad has no animation generator of its own
# -- see flydigi/lighting.py -- so each of these writes the frame data, and the
# mode byte stored alongside is only a record of which one produced it.
# "Breath" and "Flow" are Space Station's own names for its two controller
# modes; static and rainbow are the obvious remaining cases.
EFFECTS = ["Static", "Breath", "Flow", "Rainbow"]

# The stored mode byte uses Space Station's numbering, not ours, so on load we
# cannot say which of our effects produced what is on the pad. Rather than
# claim one, the picker starts here: leave the frames alone until an effect is
# actually chosen.
KEEP_CURRENT = "(keep what is on the pad)"

# A slower cycle is a larger stored number, which reads backwards on a control
# labelled "speed". The slider is inverted so that right is faster, and the
# stored value is shown next to it rather than hidden.
CYCLE_MIN, CYCLE_MAX = 1, 30


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
        self.mode.addItems([KEEP_CURRENT] + EFFECTS)
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
        self.speed.setRange(CYCLE_MIN, CYCLE_MAX)
        self.speed_readout = QLabel("")
        self.speed_readout.setMinimumWidth(96)
        self.speed.valueChanged.connect(self._show_cycle)
        self.speed.valueChanged.connect(lambda _v: self._edit())
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("slower"))
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(QLabel("faster"))
        speed_row.addWidget(self.speed_readout)
        form.addRow("Animation speed", speed_row)

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
        form.addRow("Colour", colour_row)

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
        self.brightness.setValue(self._edited.brightness)
        self.speed.setValue(self._invert(self._edited.cycle_time))
        self._show_cycle(self.speed.value())
        self.click_feedback.setChecked(self._edited.click_feedback)
        self.mode.setCurrentIndex(0)          # KEEP_CURRENT
        # Seed the picker from a lit LED, since frame 0 of a breath is black.
        self._colour = self._first_lit() or (0, 128, 255)
        self._loading = False
        self._update_preview()
        self.info.setText(
            f"{self._edited.frames} frames of {self._edited.leds_per_frame} LEDs, "
            f"protocol v{self._edited.version:#06x}. The pad has no effect "
            "generator — choosing an effect rewrites the frames it plays.")
        self._mark_changes()

    def _first_lit(self):
        for frame in range(self._edited.frames):
            for led in self._edited.frame(frame):
                if any(led):
                    return led
        return None

    @staticmethod
    def _invert(cycle):
        """Map stored cycle time to slider position, so right is faster."""
        return max(CYCLE_MIN, min(CYCLE_MAX, CYCLE_MAX + CYCLE_MIN - int(cycle)))

    def _show_cycle(self, value):
        self.speed_readout.setText(f"cycle {self._invert(value)}")

    def _pick_colour(self):
        from PySide6.QtGui import QColor
        current = QColor(*self._colour)
        chosen = QColorDialog.getColor(current, self, "Lighting colour")
        if not chosen.isValid():
            return
        self._colour = (chosen.red(), chosen.green(), chosen.blue())
        self._update_preview()
        if self._edited is not None:
            self._apply_effect()
            self._mark_changes()

    def _update_preview(self):
        red, green, blue = self._colour
        self.colour_preview.setStyleSheet(
            f"background: rgb({red},{green},{blue}); border: 1px solid palette(mid);")

    def _edit(self):
        if self._loading or self._edited is None:
            return
        self._edited.brightness = self.brightness.value()
        self._edited.cycle_time = self._invert(self.speed.value())
        self._edited.click_feedback = self.click_feedback.isChecked()
        self._apply_effect()
        self._mark_changes()

    def _apply_effect(self):
        """Regenerate the frames, which is the only thing that changes the look."""
        effect = self.mode.currentText()
        if effect == KEEP_CURRENT:
            self.colour_button.setEnabled(False)
            return
        colours = [self._colour]
        if effect == "Static":
            self._edited.set_solid(self._colour)
        elif effect == "Breath":
            self._edited.set_breath(colours)
        elif effect == "Flow":
            self._edited.set_flow(colours + [(0, 0, 0)])
        elif effect == "Rainbow":
            self._edited.set_rainbow()
        # Keep the byte in step with what we generated, so a round trip through
        # Space Station sees something coherent.
        self._edited.mode = EFFECTS.index(effect)
        self.colour_button.setEnabled(effect != "Rainbow")

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
