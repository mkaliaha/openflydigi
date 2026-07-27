# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Profile list and button remapping.

Deliberately a table rather than a picture of a controller: Flydigi's service
agreement claims their interface design and artwork, so this presents the same
data in its own way. It is also more usable for the thing people actually do
here, which is see every binding at once.

Edits are held locally until written. The pad is the only copy of these
profiles and Space Station is not available on Linux to rebuild them, so
"write" and "save to flash" are separate, explicit actions.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QSlider, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from flydigi import mapping

COL_BUTTON, COL_TARGET, COL_TURBO, COL_MODE = range(4)

TURBO_MODES = [("While held", mapping.TURBO_WHILE_HELD),
               ("Toggle", mapping.TURBO_TOGGLE)]

# Effect ids as stored in the profile, using the same vocabulary as the live
# SetForceTrigger command. Only these two are confirmed on hardware; the rest
# of the range is left out rather than guessed at in a UI.
TRIGGER_MODES = [("Off — normal travel", 0),
                 ("Constant resistance", 1)]


class ProfilePage(QWidget):
    """Edit one profile at a time; write only what changed."""

    write_requested = Signal(int, bytes, bytes, bool)
    apply_requested = Signal(int)
    load_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self._profiles = {}       # cfg_id -> bytes as last read from the pad
        self._pending = set()     # reads already asked for, not yet arrived
        self._edited = None       # MappingConfig being edited
        self._cfg_id = None
        self._active = None
        self._loading = False

        layout = QVBoxLayout(self)

        picker = QHBoxLayout()
        picker.addWidget(QLabel("Profile:"))
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self._select)
        picker.addWidget(self.selector, 1)
        picker.addWidget(QLabel("Name:"))
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(10)   # 20 bytes of UTF-16
        self.title_edit.textEdited.connect(self._rename)
        picker.addWidget(self.title_edit, 1)
        self.activate = QPushButton("Switch pad to this")
        self.activate.clicked.connect(
            lambda: self._cfg_id is not None and self.apply_requested.emit(self._cfg_id))
        picker.addWidget(self.activate)
        layout.addLayout(picker)

        # Buttons, vibration and triggers all live in the same profile blob, so
        # they share one dirty state and one write button rather than each
        # pretending to be independently saveable.
        self.sections = QTabWidget()
        self.table = QTableWidget(len(mapping.APEX5_KEYS), 4)
        self.table.setHorizontalHeaderLabels(["Button", "Sends", "Turbo (Hz)", "Turbo mode"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._build_rows()
        self.sections.addTab(self.table, "Buttons")
        self.sections.addTab(self._build_vibration(), "Vibration")
        self.sections.addTab(self._build_triggers(), "Triggers")
        layout.addWidget(self.sections, 1)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("Reset all to default")
        self.reset_button.clicked.connect(self._reset_all)
        actions.addWidget(self.reset_button)
        actions.addStretch(1)
        for label, slot in (("Back up…", self._backup), ("Restore…", self._restore)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            actions.addWidget(button)
        self.write_button = QPushButton("Apply")
        self.write_button.clicked.connect(lambda: self._write(save=False))
        actions.addWidget(self.write_button)
        self.save_button = QPushButton("Apply && save")
        self.save_button.clicked.connect(lambda: self._write(save=True))
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self.hint = QLabel("Apply takes effect at once; save also keeps it across a power cycle.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.set_enabled(False)

    def _build_rows(self):
        targets = ["(default)"] + mapping.XINPUT_TARGETS
        for row, key in enumerate(mapping.APEX5_KEYS):
            item = QTableWidgetItem(key.upper())
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, COL_BUTTON, item)

            combo = QComboBox()
            combo.addItems(targets)
            combo.currentIndexChanged.connect(lambda _i, k=key: self._retarget(k))
            self.table.setCellWidget(row, COL_TARGET, combo)

            spin = QSpinBox()
            spin.setRange(0, 40)
            spin.setSpecialValueText("off")
            spin.valueChanged.connect(lambda _v, k=key: self._retarget(k))
            self.table.setCellWidget(row, COL_TURBO, spin)

            mode = QComboBox()
            mode.addItems([label for label, _ in TURBO_MODES])
            mode.currentIndexChanged.connect(lambda _i, k=key: self._retarget(k))
            self.table.setCellWidget(row, COL_MODE, mode)
        self.table.resizeColumnsToContents()

    def _slider(self, minimum, maximum, on_change):
        row = QHBoxLayout()
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        readout = QLabel("0")
        readout.setMinimumWidth(34)
        slider.valueChanged.connect(lambda v: readout.setText(str(v)))
        slider.valueChanged.connect(lambda _v: on_change())
        row.addWidget(slider, 1)
        row.addWidget(readout)
        return row, slider

    def _build_vibration(self):
        """Grip motor limits. The pad clamps a game's rumble into this window."""
        page = QWidget()
        layout = QFormLayout(page)
        self.vib_master = QCheckBox("Rumble enabled")
        self.vib_master.toggled.connect(self._vibration_edited)
        layout.addRow(self.vib_master)

        self.vib = {}
        for side in ("left", "right"):
            box = QGroupBox(f"{side.capitalize()} grip")
            form = QFormLayout(box)
            controls = {}
            controls["enabled"] = QCheckBox("Enabled")
            controls["enabled"].toggled.connect(self._vibration_edited)
            form.addRow(controls["enabled"])
            for field, label in (("min", "Minimum"), ("max", "Maximum"),
                                 ("scale", "Strength")):
                row, slider = self._slider(0, 255, self._vibration_edited)
                controls[field] = slider
                form.addRow(label, row)
            self.vib[side] = controls
            layout.addRow(box)

        note = QLabel("Minimum and maximum bound how hard the motor may run; "
                      "the pad squeezes whatever a game asks for into that range.")
        note.setWordWrap(True)
        layout.addRow(note)
        return page

    def _build_triggers(self):
        """Adaptive-trigger settings held in the profile, so no game is needed."""
        page = QWidget()
        layout = QFormLayout(page)
        self.trig = {}
        for side in ("left", "right"):
            box = QGroupBox(f"{side.capitalize()} trigger")
            form = QFormLayout(box)
            controls = {}
            controls["mode"] = QComboBox()
            for label, _mode in TRIGGER_MODES:
                controls["mode"].addItem(label)
            controls["mode"].currentIndexChanged.connect(self._trigger_edited)
            form.addRow("Effect", controls["mode"])
            for field, label, top in (("start", "Starts at", 255),
                                      ("strength", "Resistance", 255),
                                      ("deadzone", "Dead zone", 255)):
                row, slider = self._slider(0, top, self._trigger_edited)
                controls[field] = slider
                form.addRow(label, row)
            controls["motor"] = QCheckBox("Trigger vibration motor")
            controls["motor"].toggled.connect(self._trigger_edited)
            form.addRow(controls["motor"])
            self.trig[side] = controls
            layout.addRow(box)

        note = QLabel("Stored in the profile, so it applies with no game "
                      "integration and no program running. A game that drives "
                      "the triggers itself will override it while it runs.")
        note.setWordWrap(True)
        layout.addRow(note)
        return page

    def _widgets(self, key):
        row = mapping.APEX5_KEYS.index(key)
        return (self.table.cellWidget(row, COL_TARGET),
                self.table.cellWidget(row, COL_TURBO),
                self.table.cellWidget(row, COL_MODE))

    def set_enabled(self, enabled):
        for widget in (self.table, self.title_edit, self.selector, self.activate,
                       self.write_button, self.save_button, self.reset_button):
            widget.setEnabled(enabled)

    def set_slots(self, count):
        """List the profile slots. Reads nothing -- `forget` starts that."""
        self._loading = True
        self.selector.clear()
        for cfg_id in range(count):
            self.selector.addItem(f"Profile {cfg_id + 1}", cfg_id)
        self.selector.setCurrentIndex(0)
        self._loading = False

    def set_active(self, cfg_id):
        """Mark which profile the pad is actually using."""
        self._active = cfg_id
        for index in range(self.selector.count()):
            slot = self.selector.itemData(index)
            text = self.selector.itemText(index).removesuffix("  ● in use")
            self.selector.setItemText(
                index, text + ("  ● in use" if slot == cfg_id else ""))
        self.activate.setEnabled(self._cfg_id is not None and self._cfg_id != cfg_id)

    def forget(self):
        """Drop cached profiles so the open one is re-read from the pad."""
        self._profiles.clear()
        self._pending.clear()
        self._select(self.selector.currentIndex())

    def profile_loaded(self, cfg_id, blob, title):
        self._profiles[cfg_id] = bytes(blob)
        self._pending.discard(cfg_id)
        index = self.selector.findData(cfg_id)
        if index >= 0:
            self._loading = True
            self.selector.setItemText(index, f"{cfg_id + 1}. {title or '(unnamed)'}")
            self._loading = False
            if self._active is not None:
                self.set_active(self._active)
        if cfg_id == self._cfg_id:
            self._show_cached()

    def _select(self, index):
        if self._loading or index < 0:
            return
        self._cfg_id = self.selector.itemData(index)
        if self._cfg_id in self._profiles:
            self._show_cached()
        else:
            # Not read yet -- ask for it and stay disabled until it arrives.
            self._edited = None
            self.set_enabled(False)
            self.selector.setEnabled(True)
            self.hint.setText("Reading this profile from the pad…")
            # Reading is expensive and switches the pad, so never ask twice for
            # the same profile: the user can press Reload repeatedly, and the
            # window's own startup path would otherwise race with it.
            if self._cfg_id not in self._pending:
                self._pending.add(self._cfg_id)
                self.load_requested.emit(self._cfg_id)

    def _show_cached(self):
        self._edited = mapping.MappingConfig(
            bytearray(self._profiles[self._cfg_id]), self._cfg_id)
        self.set_enabled(True)
        self._refresh_widgets()

    def _refresh_widgets(self):
        self._loading = True
        self.title_edit.setText(self._edited.title)
        for key in mapping.APEX5_KEYS:
            target, mode, frequency = self._edited.mapping(key)
            combo, spin, mode_combo = self._widgets(key)
            if target == key and not frequency:
                combo.setCurrentIndex(0)
            elif target in mapping.XINPUT_TARGETS:
                combo.setCurrentIndex(mapping.XINPUT_TARGETS.index(target) + 1)
            else:
                # macro / keyboard bindings we do not model yet
                combo.setCurrentIndex(0)
                combo.setToolTip(f"currently: {target} (not editable here)")
            spin.setValue(frequency)
            mode_combo.setCurrentIndex(1 if mode == mapping.TURBO_TOGGLE else 0)
            mode_combo.setEnabled(frequency > 0)
        self._refresh_extras()
        self._loading = False
        self._mark_changes()

    def _refresh_extras(self):
        """Pull vibration and trigger state out of the config into the widgets."""
        config = self._edited
        self.vib_master.setChecked(config.vibration_enabled)
        for side, controls in self.vib.items():
            enabled, minimum, maximum, scale = config.vibration(side)
            controls["enabled"].setChecked(enabled)
            controls["min"].setValue(minimum)
            controls["max"].setValue(maximum)
            controls["scale"].setValue(scale)
        for side, controls in self.trig.items():
            mode, params = config.trigger_effect(side)
            index = next((i for i, (_l, m) in enumerate(TRIGGER_MODES) if m == mode), 0)
            controls["mode"].setCurrentIndex(index)
            controls["start"].setValue(params[0])
            controls["strength"].setValue(params[1])
            controls["deadzone"].setValue(config.trigger_curve(side)["zero"])
            controls["motor"].setChecked(config.trigger_motor(side)[0])

    def _vibration_edited(self):
        if self._loading or self._edited is None:
            return
        self._edited.vibration_enabled = self.vib_master.isChecked()
        for side, controls in self.vib.items():
            self._edited.set_vibration(
                side,
                enabled=controls["enabled"].isChecked(),
                minimum=controls["min"].value(),
                maximum=controls["max"].value(),
                scale=controls["scale"].value())
        self._mark_changes()

    def _trigger_edited(self):
        if self._loading or self._edited is None:
            return
        for side, controls in self.trig.items():
            _label, mode = TRIGGER_MODES[controls["mode"].currentIndex()]
            # Params mirror the live race effect: where resistance begins, then
            # how hard it pushes back.
            self._edited.set_trigger_effect(
                side, mode, [controls["start"].value(), controls["strength"].value()])
            self._edited.set_trigger_curve(side, zero=controls["deadzone"].value())
            self._edited.set_trigger_motor(side, enabled=controls["motor"].isChecked())
        self._mark_changes()

    def _retarget(self, key):
        if self._loading or self._edited is None:
            return
        combo, spin, mode_combo = self._widgets(key)
        target = None if combo.currentIndex() == 0 else combo.currentText()
        frequency = spin.value()
        mode = TURBO_MODES[mode_combo.currentIndex()][1]
        mode_combo.setEnabled(frequency > 0)
        self._edited.set_mapping(key, target, mode, frequency)
        self._mark_changes()

    def _rename(self, text):
        if self._edited is not None and not self._loading:
            self._edited.title = text
            self._mark_changes()

    def _reset_all(self):
        if self._edited is None:
            return
        for key in mapping.APEX5_KEYS:
            self._edited.set_mapping(key, None)
        self._refresh_widgets()

    def _mark_changes(self):
        """Bold anything that differs from the pad's default, and show if unsaved."""
        for row, key in enumerate(mapping.APEX5_KEYS):
            target, _mode, frequency = self._edited.mapping(key)
            item = self.table.item(row, COL_BUTTON)
            font = item.font()
            font.setBold(target != key or bool(frequency))
            item.setFont(font)
        dirty = self._is_dirty()
        self.write_button.setEnabled(dirty)
        self.save_button.setEnabled(dirty)
        if dirty:
            self.hint.setText("Unsaved changes. Apply takes effect now; "
                              "save also keeps it across a power cycle.")
        else:
            self.hint.setText("Matches what is on the pad.")

    def _is_dirty(self):
        if self._edited is None or self._cfg_id is None:
            return False
        return bytes(self._edited.blob) != self._profiles.get(self._cfg_id)

    def _write(self, save):
        if self._edited is None or self._cfg_id is None:
            return
        self.write_requested.emit(self._cfg_id, bytes(self._edited.blob),
                                  self._profiles.get(self._cfg_id, b""), save)

    def confirm_written(self, cfg_id, _packets, _saved):
        """The pad accepted the write, so it is now the reference copy."""
        if self._edited is not None and cfg_id == self._cfg_id:
            self._profiles[cfg_id] = bytes(self._edited.blob)
            index = self.selector.currentIndex()
            self.selector.setItemText(
                index, f"{cfg_id + 1}. {self._edited.title or '(unnamed)'}")
            self._mark_changes()

    def _backup(self):
        if self._edited is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Back up profile", f"profile{self._cfg_id + 1}.bin",
            "Profile dump (*.bin)")
        if path:
            with open(path, "wb") as fh:
                fh.write(self._profiles.get(self._cfg_id, bytes(self._edited.blob)))

    def _restore(self):
        if self._edited is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore profile", "", "Profile dump (*.bin)")
        if not path:
            return
        with open(path, "rb") as fh:
            blob = fh.read()
        expected = len(self._edited.blob)
        if len(blob) != expected:
            QMessageBox.warning(self, "Wrong size",
                                f"That file is {len(blob)} bytes; this pad's profiles "
                                f"are {expected}. Refusing to write it.")
            return
        self._edited = mapping.MappingConfig(blob, self._cfg_id)
        self._refresh_widgets()
