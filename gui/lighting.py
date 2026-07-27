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

# Space Station's own effect ids and names. The pad has no animation generator
# -- see flydigi/lighting.py -- so picking one of these writes frame data and
# records the matching id.
EFFECTS = [
    ("Static", lighting.EFFECT_STATIC_SINGLE, 1),
    ("Static, multiple colours", lighting.EFFECT_STATIC_MULTI, lighting.MAX_COLOURS),
    ("Breathing", lighting.EFFECT_BREATHING, lighting.MAX_COLOURS),
    ("Flow", lighting.EFFECT_STREAMING, lighting.MAX_COLOURS),
    ("Rotation", lighting.EFFECT_ROTATION, lighting.MAX_COLOURS),
    ("Wave", lighting.EFFECT_WAVE, lighting.MAX_COLOURS),
    ("Flash", lighting.EFFECT_FLASH, lighting.MAX_COLOURS),
    ("Rainbow", lighting.EFFECT_RAINBOW, 0),
    ("Off", lighting.EFFECT_OFF, 0),
]

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
        self._colours = [lighting.DEFAULT_COLOUR]

        layout = QVBoxLayout(self)

        box = QGroupBox("Lighting")
        form = QFormLayout(box)

        self.mode = QComboBox()
        self.mode.addItems([KEEP_CURRENT] + [name for name, _id, _n in EFFECTS])
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

        self.colour_row = QHBoxLayout()
        self.swatches = []
        self.add_colour = QPushButton("+")
        self.add_colour.setFixedWidth(30)
        self.add_colour.setToolTip(f"Add a colour (up to {lighting.MAX_COLOURS})")
        self.add_colour.clicked.connect(self._add_colour)
        self.remove_colour = QPushButton("−")
        self.remove_colour.setFixedWidth(30)
        self.remove_colour.setToolTip("Remove the last colour")
        self.remove_colour.clicked.connect(self._remove_colour)
        self.colour_row.addStretch(1)
        self.colour_row.addWidget(self.add_colour)
        self.colour_row.addWidget(self.remove_colour)
        form.addRow("Colours", self.colour_row)

        layout.addWidget(box)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        layout.addStretch(1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.write_button = QPushButton("Apply")
        self.write_button.clicked.connect(lambda: self._write(save=False))
        actions.addWidget(self.write_button)
        self.save_button = QPushButton("Apply && save")
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
        # Seed from a lit LED, since frame 0 of a breath is black.
        self._colours = [self._first_lit() or lighting.DEFAULT_COLOUR]
        self._loading = False
        self._rebuild_swatches()
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

    def _rebuild_swatches(self):
        """One clickable button per colour, kept in step with the list."""
        for button in self.swatches:
            self.colour_row.removeWidget(button)
            button.deleteLater()
        self.swatches = []
        for index, colour in enumerate(self._colours):
            button = QPushButton()
            button.setFixedSize(44, 24)
            button.setToolTip("Click to change this colour")
            button.setStyleSheet(
                f"background: rgb({colour[0]},{colour[1]},{colour[2]});"
                " border: 1px solid palette(mid);")
            button.clicked.connect(lambda _c=False, i=index: self._pick_colour(i))
            self.colour_row.insertWidget(index, button)
            self.swatches.append(button)
        self._update_colour_controls()

    def _update_colour_controls(self):
        allowed = self._max_colours()
        self.add_colour.setEnabled(len(self._colours) < allowed)
        self.remove_colour.setEnabled(len(self._colours) > 1 and allowed > 1)
        for button in self.swatches:
            button.setVisible(allowed > 0)
        self.add_colour.setVisible(allowed > 1)
        self.remove_colour.setVisible(allowed > 1)

    def _max_colours(self):
        """How many colours the chosen effect uses. Rainbow and Off use none."""
        index = self.mode.currentIndex() - 1
        return EFFECTS[index][2] if 0 <= index < len(EFFECTS) else 1

    def _add_colour(self):
        if len(self._colours) >= self._max_colours():
            return
        self._colours.append(lighting.suggest_colour(self._colours))
        self._rebuild_swatches()
        self._regenerate()

    def _remove_colour(self):
        if len(self._colours) <= 1:
            return
        self._colours.pop()
        self._rebuild_swatches()
        self._regenerate()

    def _pick_colour(self, index):
        from PySide6.QtGui import QColor
        chosen = QColorDialog.getColor(QColor(*self._colours[index]), self,
                                       "Lighting colour")
        if not chosen.isValid():
            return
        self._colours[index] = (chosen.red(), chosen.green(), chosen.blue())
        self._rebuild_swatches()
        self._regenerate()

    def _regenerate(self):
        if self._edited is not None and not self._loading:
            self._apply_effect()
            self._mark_changes()

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
        index = self.mode.currentIndex() - 1
        if index < 0:
            self._update_colour_controls()
            return
        _name, effect, allowed = EFFECTS[index]
        # Trim if the effect takes fewer colours than are on screen.
        if allowed and len(self._colours) > allowed:
            del self._colours[allowed:]
            self._rebuild_swatches()
        self._edited.apply_effect(effect, self._colours)
        self._update_colour_controls()

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
