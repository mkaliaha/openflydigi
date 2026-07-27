# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-game adaptive-trigger support -- Space Station calls these plugins.

Games reach the triggers by five different routes and the difference matters to
the user, because it decides what they have to do:

    vibration   the pad does it -- write the binding once, nothing else runs
    telemetry   a listener consumes the game's own network telemetry
    monitor     effects driven by reading the game's memory
    ps5         the game drives a DualSense; the virtual pad translates
    bespoke     a third-party mod speaks DSX; we only listen

Only the vibration route can be applied from here and then forgotten, because
it lives in the pad. The rest need a process running alongside the game, so
this page tells you which and leaves starting it to you -- there is no daemon
picking the route automatically yet.

Choices are stored per game in the user's config directory, not on the pad.
"""
import json
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget)

from flydigi import games

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "flydigi")
PREFS_PATH = os.path.join(CONFIG_DIR, "games.json")

TIER_LABELS = {
    "vibration": "Pad-side (stored on the pad)",
    "telemetry": "Game telemetry (needs flydigi-forza)",
    "monitor": "Game memory (needs flydigi-monitor)",
    "ps5": "DualSense mode (needs flydigi-ds5)",
    "bespoke": "Third-party mod (needs flydigi-dsx)",
    "unknown": "No trigger support",
}

# The six titles that support both Flydigi's own mod and PS5 mode.
DUAL_MODE_HINT = "Both routes work; pick one"

COL_NAME, COL_ROUTE, COL_MODE = range(3)


class FetchThread(QThread):
    """Downloading the game list blocks; keep it off the UI thread."""

    done = Signal(object, str)

    def run(self):
        try:
            self.done.emit(games.fetch_gamelist(), "")
        except Exception as exc:              # network, JSON, permissions
            self.done.emit(None, str(exc))


class TriggerPage(QWidget):
    """Browse supported games and record which route to use for each."""

    apply_vibration = Signal(dict)

    def __init__(self):
        super().__init__()
        self._games = []
        self._prefs = self._load_prefs()
        self._fetch = None

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search games…")
        self.search.textChanged.connect(self._populate)
        top.addWidget(self.search, 1)
        self.only_supported = QComboBox()
        self.only_supported.addItems(["All routes"] + sorted(TIER_LABELS))
        self.only_supported.currentIndexChanged.connect(self._populate)
        top.addWidget(self.only_supported)
        self.refresh_button = QPushButton("Update list")
        self.refresh_button.clicked.connect(self._fetch_list)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Game", "Route", "Preference"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        bottom.addWidget(self.detail, 1)
        self.apply_button = QPushButton("Apply to pad now")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_selected)
        bottom.addWidget(self.apply_button)
        layout.addLayout(bottom)

        self._load_list()

    # -- game list ---------------------------------------------------------

    def _load_list(self):
        try:
            self._games = games.load()
        except (OSError, ValueError, KeyError):
            self._games = []
            self.detail.setText(
                "No game list yet. \"Update list\" fetches it from Flydigi's "
                "public API — that is the only time this app contacts them.")
        self._populate()

    def _fetch_list(self):
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Updating…")
        self._fetch = FetchThread()
        self._fetch.done.connect(self._fetched)
        self._fetch.start()

    def _fetched(self, data, error):
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("Update list")
        if error:
            QMessageBox.warning(self, "Could not update", error)
            return
        self._games = data or []
        self._populate()

    def _visible_games(self):
        needle = self.search.text().strip().lower()
        wanted = self.only_supported.currentText()
        for game in self._games:
            if needle and needle not in games.names(game).lower():
                continue
            route = games.tier(game)
            if wanted != "All routes" and route != wanted:
                continue
            yield game, route

    def _populate(self):
        self.table.setRowCount(0)
        for game, route in self._visible_games():
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = game.get("enGameName") or game.get("gameName") or "(unnamed)"
            item = QTableWidgetItem(name)
            item.setData(Qt.UserRole, game)
            self.table.setItem(row, COL_NAME, item)
            self.table.setItem(row, COL_ROUTE, QTableWidgetItem(TIER_LABELS.get(route, route)))

            preference = self._prefs.get(name, {}).get("route", "")
            cell = QTableWidgetItem(preference or ("—" if route != "ps5" else DUAL_MODE_HINT))
            self.table.setItem(row, COL_MODE, cell)

    # -- selection ---------------------------------------------------------

    def _selected_game(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), COL_NAME).data(Qt.UserRole)

    def _selection_changed(self):
        game = self._selected_game()
        if not game:
            self.apply_button.setEnabled(False)
            self.detail.setText("")
            return
        route = games.tier(game)
        self.apply_button.setEnabled(route == "vibration")
        processes = ", ".join(game.get("processGameNames") or []) or "not listed"
        note = TIER_LABELS.get(route, route)
        if route == "vibration":
            note += " — applying writes the binding into the pad, and it stays."
        elif route == "unknown":
            note += "."
        else:
            note += f" — start it alongside the game. Process: {processes}"
        self.detail.setText(note)

    def _apply_selected(self):
        game = self._selected_game()
        if game:
            self.apply_vibration.emit(game)

    # -- preferences -------------------------------------------------------

    @staticmethod
    def _load_prefs():
        try:
            with open(PREFS_PATH) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def save_prefs(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(PREFS_PATH, "w") as fh:
            json.dump(self._prefs, fh, indent=2)
