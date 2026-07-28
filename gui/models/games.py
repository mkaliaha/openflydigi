# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The supported-game list, and the filter over it.

Wording matters here and the model owns it, so the widget and the QML page
cannot drift apart: the "vibration" route is not a per-game integration but a
preset for one bind, and saying otherwise would oversell it.
"""
from PySide6.QtCore import (Property, QAbstractListModel, QModelIndex,
                            QSortFilterProxyModel, Qt, Signal, Slot)
from PySide6.QtQml import QmlElement

from flydigi import games, prefs

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

TIER_LABELS = {
    "vibration": "Preset — tunes the pad's rumble-to-trigger bind",
    "telemetry": "Game telemetry (needs flydigi-forza)",
    "monitor": "Game memory (needs flydigi-monitor)",
    "ps5": "DualSense mode (needs flydigi-ds5)",
    "bespoke": "Third-party mod (needs flydigi-dsx)",
    "unknown": "No trigger support",
}

ALL_ROUTES = "All routes"

# Only the pad-side route can be pushed onto the hardware from here; the others
# need a helper running next to the game, which this app does not start.
APPLIABLE_ROUTE = "vibration"

VIBRATION_DETAIL = (
    "The pad drives the triggers from this game's own rumble — nothing runs "
    "alongside it. This entry is a preset for that bind: travel, strength and "
    "filtering, tuned for this game. Loading it replaces the current bind.")

# Short enough to sit in a combo box. TIER_LABELS says what each route needs,
# which is the wrong length for a control the width of a game's name.
ROUTE_NAMES = {
    "vibration": "Pad preset",
    "telemetry": "Telemetry",
    "monitor": "Game memory",
    "ps5": "DualSense",
    "bespoke": "Third-party mod",
    "unknown": "None",
}


def route_name(route):
    return ROUTE_NAMES.get(route, route)


def game_name(game):
    return game.get("enGameName") or game.get("gameName") or "(unnamed)"


def route_detail(game, route):
    """The explanatory line for one game, given its route."""
    if route == APPLIABLE_ROUTE:
        return VIBRATION_DETAIL
    label = TIER_LABELS.get(route, route)
    if route == "unknown":
        return label + "."
    processes = ", ".join(game.get("processGameNames") or []) or "not listed"
    return f"{label} — start it alongside the game. Process: {processes}"


@QmlElement
class GameListModel(QAbstractListModel):
    """One row per game, with its route already resolved."""

    NameRole = Qt.UserRole + 1
    RouteRole = Qt.UserRole + 2
    RouteLabelRole = Qt.UserRole + 3
    CanApplyRole = Qt.UserRole + 4
    DetailRole = Qt.UserRole + 5
    GameRole = Qt.UserRole + 6
    AutoRole = Qt.UserRole + 7
    RouteChoicesRole = Qt.UserRole + 8
    ChosenRouteIndexRole = Qt.UserRole + 9

    countChanged = Signal()

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._games = []
        # The same file the daemon reads, and it re-reads on change, so a
        # toggle here reaches a running daemon within its next poll.
        #
        # Injectable only so a test can be given a temporary file: the default
        # is the real one, and a test that forgot would rewrite the
        # preferences of whoever ran it.
        self._prefs = settings if settings is not None else prefs.Prefs()

    def roleNames(self):
        return {
            self.NameRole: b"name",
            self.RouteRole: b"route",
            self.RouteLabelRole: b"routeLabel",
            self.CanApplyRole: b"canApply",
            self.DetailRole: b"detail",
            self.AutoRole: b"auto",
            self.RouteChoicesRole: b"routeChoices",
            self.ChosenRouteIndexRole: b"chosenRouteIndex",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._games)

    def data(self, index, role=Qt.DisplayRole):
        if not 0 <= index.row() < len(self._games):
            return None
        game = self._games[index.row()]
        # The chosen route, not the tier: nine games support more than one, and
        # a row that still said "Game memory" after being switched to DualSense
        # would be describing something that is no longer going to happen.
        route = self._prefs.route(game)
        if role in (self.NameRole, Qt.DisplayRole):
            return game_name(game)
        if role == self.RouteRole:
            return route
        if role == self.RouteLabelRole:
            return TIER_LABELS.get(route, route)
        if role == self.CanApplyRole:
            return route == APPLIABLE_ROUTE
        if role == self.DetailRole:
            return route_detail(game, route)
        if role == self.GameRole:
            return game
        if role == self.AutoRole:
            return self._prefs.auto(game)
        if role == self.RouteChoicesRole:
            return [route_name(r) for r in prefs.routes(game)]
        if role == self.ChosenRouteIndexRole:
            return prefs.routes(game).index(route)
        return None

    # -- per-game auto mode ------------------------------------------------

    def prefs(self):
        return self._prefs

    def setAuto(self, row, value):
        game = self.game(row)
        if game is None:
            return
        self._prefs.set_auto(game, value)
        self._changed(row)

    def setRouteIndex(self, row, choice):
        """Pick a route by its position in this row's `routeChoices`.

        Indexed rather than named so the view never has to hold a route
        identifier, and so the combo box it came from cannot pick one the game
        does not offer.
        """
        game = self.game(row)
        if game is None:
            return
        available = prefs.routes(game)
        if not 0 <= choice < len(available):
            return
        self._prefs.set_route(game, available[choice])
        self._changed(row)

    def _changed(self, row):
        """Save and tell the view. Every role: the route drives most of them."""
        self._prefs.save()
        index = self.index(row, 0)
        self.dataChanged.emit(index, index)

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._games)

    def setGames(self, entries):
        self.beginResetModel()
        self._games = list(entries or [])
        self.endResetModel()
        self.countChanged.emit()

    def game(self, row):
        """The raw gamelist entry, for handing to the worker."""
        if 0 <= row < len(self._games):
            return self._games[row]
        return None

    @Slot()
    def load(self):
        """Read the cached gamelist. Returns False if there is not one yet."""
        try:
            self.setGames(games.load())
        except (OSError, ValueError, KeyError):
            self.setGames([])
            return False
        return True


@QmlElement
class GameFilterModel(QSortFilterProxyModel):
    """Search text and route filter, kept out of the view."""

    searchChanged = Signal()
    routeChanged = Signal()
    countChanged = Signal()

    def __init__(self, source=None, parent=None):
        super().__init__(parent)
        self._search = ""
        self._route = ALL_ROUTES
        if source is not None:
            self.setSourceModel(source)
            source.countChanged.connect(self.countChanged)
        self.rowsInserted.connect(self.countChanged)
        self.rowsRemoved.connect(self.countChanged)
        self.modelReset.connect(self.countChanged)

    @Property(str, notify=searchChanged)
    def search(self):
        return self._search

    @search.setter
    def search(self, value):
        value = str(value or "").strip().lower()
        if self._search != value:
            self._search = value
            self.invalidateFilter()
            self.searchChanged.emit()
            self.countChanged.emit()

    @Property("QStringList", constant=True)
    def routeNames(self):
        return [ALL_ROUTES] + sorted(TIER_LABELS)

    @Property(str, notify=routeChanged)
    def route(self):
        return self._route

    @route.setter
    def route(self, value):
        value = str(value or ALL_ROUTES)
        if self._route != value:
            self._route = value
            self.invalidateFilter()
            self.routeChanged.emit()
            self.countChanged.emit()

    @Property(int, notify=countChanged)
    def count(self):
        return self.rowCount()

    @Property(int, notify=countChanged)
    def total(self):
        """How many games there are before filtering.

        `count` alone cannot tell "we have never downloaded the list" from "your
        search matched nothing", and a view that treats both as empty offers to
        re-download the list because someone mistyped a game's name.
        """
        source = self.sourceModel()
        return source.rowCount() if source is not None else 0

    def filterAcceptsRow(self, row, parent):
        source = self.sourceModel()
        if source is None:
            return False
        game = source.game(row)
        if game is None:
            return False
        # Match against every name the entry carries, not just the displayed
        # one: the list is bilingual and people search in either language.
        if self._search and self._search not in games.names(game).lower():
            return False
        # Filter on the chosen route, so a game switched to another route moves
        # to that filter rather than staying under the one it no longer takes.
        if self._route != ALL_ROUTES and source.prefs().route(game) != self._route:
            return False
        return True

    def _sourceRow(self, row):
        return self.mapToSource(self.index(row, 0)).row()

    def game(self, row):
        """Map a proxy row back to the underlying gamelist entry."""
        source = self.sourceModel()
        if source is None:
            return None
        return source.game(self._sourceRow(row))

    # Row lookups for a selection, so a view never has to name a role number.

    @Slot(int, result=str)
    def nameAt(self, row):
        game = self.game(row)
        return game_name(game) if game is not None else ""

    @Slot(int, result=str)
    def detailAt(self, row):
        game = self.game(row)
        if game is None:
            return ""
        return route_detail(game, self.sourceModel().prefs().route(game))

    @Slot(int, result=bool)
    def canApplyAt(self, row):
        game = self.game(row)
        if game is None:
            return False
        return self.sourceModel().prefs().route(game) == APPLIABLE_ROUTE

    # -- per-game auto mode, addressed by proxy row ------------------------

    @Slot(int, bool)
    def setAutoAt(self, row, value):
        source = self.sourceModel()
        if source is not None:
            source.setAuto(self._sourceRow(row), value)

    @Slot(int, int)
    def setRouteIndexAt(self, row, choice):
        source = self.sourceModel()
        if source is not None:
            source.setRouteIndex(self._sourceRow(row), choice)
