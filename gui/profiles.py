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
    QAbstractItemView, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from flydigi import mapping

COL_BUTTON, COL_TARGET, COL_TURBO, COL_MODE = range(4)

TURBO_MODES = [("While held", mapping.TURBO_WHILE_HELD),
               ("Toggle", mapping.TURBO_TOGGLE)]


class ProfilePage(QWidget):
    """Edit one profile at a time; write only what changed."""

    write_requested = Signal(int, bytes, bytes, bool)
    apply_requested = Signal(int)
    reload_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self._profiles = {}       # cfg_id -> bytes as last read from the pad
        self._edited = None       # MappingConfig being edited
        self._cfg_id = None
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

        box = QGroupBox("Buttons")
        box_layout = QVBoxLayout(box)
        self.table = QTableWidget(len(mapping.APEX5_KEYS), 4)
        self.table.setHorizontalHeaderLabels(["Button", "Sends", "Turbo (Hz)", "Turbo mode"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._build_rows()
        box_layout.addWidget(self.table)
        layout.addWidget(box, 1)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("Reset all to default")
        self.reset_button.clicked.connect(self._reset_all)
        actions.addWidget(self.reset_button)
        actions.addStretch(1)
        for label, slot in (("Back up…", self._backup), ("Restore…", self._restore)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            actions.addWidget(button)
        self.write_button = QPushButton("Write to pad")
        self.write_button.clicked.connect(lambda: self._write(save=False))
        actions.addWidget(self.write_button)
        self.save_button = QPushButton("Write && save to flash")
        self.save_button.clicked.connect(lambda: self._write(save=True))
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self.hint = QLabel("Writing takes effect at once; saving also survives a power cycle.")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.set_enabled(False)

    def _build_rows(self):
        targets = ["(default)"] + mapping.APEX5_KEYS
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

    def _widgets(self, key):
        row = mapping.APEX5_KEYS.index(key)
        return (self.table.cellWidget(row, COL_TARGET),
                self.table.cellWidget(row, COL_TURBO),
                self.table.cellWidget(row, COL_MODE))

    def set_enabled(self, enabled):
        for widget in (self.table, self.title_edit, self.selector, self.activate,
                       self.write_button, self.save_button, self.reset_button):
            widget.setEnabled(enabled)

    def set_profiles(self, profiles):
        """Replace everything with what was just read off the pad."""
        self._loading = True
        self._profiles = {cfg_id: blob for cfg_id, blob, _ in profiles}
        current = self.selector.currentIndex()
        self.selector.clear()
        for cfg_id, _blob, title in profiles:
            self.selector.addItem(f"{cfg_id + 1}. {title or '(unnamed)'}", cfg_id)
        self._loading = False
        self.set_enabled(True)
        self.selector.setCurrentIndex(min(max(current, 0), self.selector.count() - 1))
        self._select(self.selector.currentIndex())

    def _select(self, index):
        if self._loading or index < 0:
            return
        self._cfg_id = self.selector.itemData(index)
        blob = self._profiles.get(self._cfg_id)
        if blob is None:
            return
        self._edited = mapping.MappingConfig(bytearray(blob), self._cfg_id)
        self._refresh_widgets()

    def _refresh_widgets(self):
        self._loading = True
        self.title_edit.setText(self._edited.title)
        for key in mapping.APEX5_KEYS:
            target, mode, frequency = self._edited.mapping(key)
            combo, spin, mode_combo = self._widgets(key)
            if target == key and not frequency:
                combo.setCurrentIndex(0)
            elif target in mapping.APEX5_KEYS:
                combo.setCurrentIndex(mapping.APEX5_KEYS.index(target) + 1)
            else:
                # macro / keyboard bindings we do not model yet
                combo.setCurrentIndex(0)
                combo.setToolTip(f"currently: {target} (not editable here)")
            spin.setValue(frequency)
            mode_combo.setCurrentIndex(1 if mode == mapping.TURBO_TOGGLE else 0)
            mode_combo.setEnabled(frequency > 0)
        self._loading = False
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
            self.hint.setText("Unwritten changes. "
                              "Write applies now; save also survives a power cycle.")
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
