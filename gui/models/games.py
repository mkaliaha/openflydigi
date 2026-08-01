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
    "bespoke": "Third-party mod",
    "unknown": "None",
}

# `isPS5` used to be a route here, and is not one any more: DualSense mode is a
# single switch for the whole system (the DualSense page), because unlike every
# other route it needs no per-game data at all. The flag is still worth showing
# -- it says this game reads a DualSense directly -- but it is not a choice, and
# 23 preferences pretending otherwise was Flydigi's model leaking into a tier
# that does not share its constraints.
DS5_DETAIL = (
    "Flydigi lists this game as reading a DualSense directly. Nothing to set "
    "up per game: turn DualSense mode on and every game that speaks it gets "
    "adaptive triggers, gyro and haptics — including games not in this list.")


def marks_ds5(game, route):
    """Whether the row needs a DualSense badge.

    A badge earns its place by saying something the row does not already say.
    `route_label` spells DualSense mode out for a game whose only capability is
    that, so a badge beside it is decoration; for the eight games that pair the
    flag with a mod or a preset, the label is about the other thing and the
    badge is the only mention.

    There used to be a "pad-side" badge too, on exactly the rows whose label
    read "Preset — tunes the pad's rumble-to-trigger bind" and whose footer
    button was enabled. Three ways of saying one thing.
    """
    return games.ds5_aware(game) and route != "unknown"


def route_name(route):
    return ROUTE_NAMES.get(route, route)


def route_label(game, route):
    """The one-line label for a row.

    "No trigger support" is right for a game with no capability flags at all,
    and wrong for the fifteen whose only flag is DualSense: those work, with no
    per-game setup, the moment DualSense mode is on.
    """
    if route == "unknown" and games.ds5_aware(game):
        return "DualSense mode — nothing to set up per game"
    return TIER_LABELS.get(route, route)


def can_auto(route):
    """Whether auto mode has anything to do for a game.

    The daemon acts per route; with no route there is nothing for it to start
    or write, so a switch would be an offer it cannot keep. This became
    reachable when DualSense mode stopped being a route: fifteen games carry
    that flag and nothing else.
    """
    return route != "unknown"


def game_name(game):
    return game.get("enGameName") or game.get("gameName") or "(unnamed)"


def route_detail(game, route):
    """The explanatory line for one game, given its route."""
    if route == "unknown":
        # For the fifteen games whose only capability is DualSense, "No trigger
        # support" is the wrong sentence: there is support, it just is not
        # per-game. Saying otherwise would send someone looking for a route
        # that was deliberately taken away.
        if games.ds5_aware(game):
            return DS5_DETAIL
        return TIER_LABELS[route] + "."
    if route == APPLIABLE_ROUTE:
        base = VIBRATION_DETAIL
    else:
        label = TIER_LABELS.get(route, route)
        processes = ", ".join(game.get("processGameNames") or []) or "not listed"
        base = f"{label} — start it alongside the game. Process: {processes}"
    if games.ds5_aware(game):
        base += "\n\n" + DS5_DETAIL
    return base


@QmlElement
class GameListModel(QAbstractListModel):
    """One row per game, with its route already resolved.

    **A row is decoded once and then read as fields.** Every role here used to
    start from the chosen route, and working that out is not free: it reads the
    preferences entry for the game and rebuilds the list of routes the game
    offers, which itself walks the entry's capability flags. `data` did that
    before it looked at which role it had been asked for, so the nine roles the
    Games page's delegate binds cost nine resolutions per row -- 94 rows, and
    the page is drawn from scratch whenever the filter moves. Two of those roles
    then built something on top of it on every read: `routeChoices` a fresh
    `QVariantList`, `detail` a paragraph of prose.

    So `_decode` works a game out in one pass and `data` returns what it stored.
    The invalidation is the same shape as `gui/models/profile.py`'s: a decoded
    row is refilled wherever the data under it moves, and there are only three
    such places -- `setGames` replaces the lot, `_changed` refills the one row a
    mutator just wrote, and `refresh` exists for anything that writes through
    `prefs()` without going through either.
    """

    NameRole = Qt.UserRole + 1
    RouteRole = Qt.UserRole + 2
    RouteLabelRole = Qt.UserRole + 3
    CanApplyRole = Qt.UserRole + 4
    DetailRole = Qt.UserRole + 5
    GameRole = Qt.UserRole + 6
    AutoRole = Qt.UserRole + 7
    RouteChoicesRole = Qt.UserRole + 8
    ChosenRouteIndexRole = Qt.UserRole + 9
    Ds5Role = Qt.UserRole + 10
    CanAutoRole = Qt.UserRole + 11

    # The haystack a search is matched against, kept in the decoded row beside
    # the roles under a key no role number can collide with. No delegate reads
    # it -- `GameFilterModel` does, once per row per keystroke -- but it comes
    # out of the same entry at the same moment, and a second parallel list
    # would be a second thing to keep in step.
    SearchKey = "search"

    countChanged = Signal()

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._games = []
        # One decoded row per game, in the same order. See the class docstring
        # for what keeps it from going stale.
        self._rows = []
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
            self.Ds5Role: b"ds5Mark",
            self.CanAutoRole: b"canAuto",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._games)

    def _decode(self, game):
        """One game, worked out once, as the fields a delegate will ask for.

        The chosen route is what the rest of the row hangs off -- and it is the
        chosen route, not the tier: a game that supports more than one can be
        switched, and a row that still said "Game memory" afterwards would be
        describing something that is no longer going to happen.

        `RouteChoicesRole` is a list, and `data` hands out this one rather than
        a copy of it -- the point of the change was to stop rebuilding it per
        read. Crossing into QML converts it to a fresh JS array, so a delegate
        cannot reach back through it; a Python caller could, and must not. Same
        terms as `frameColours` in `gui/models/dock.py`.
        """
        route = self._prefs.route(game)
        choices = prefs.routes(game)
        return {
            self.NameRole: game_name(game),
            self.RouteRole: route,
            self.RouteLabelRole: route_label(game, route),
            self.CanApplyRole: route == APPLIABLE_ROUTE,
            self.DetailRole: route_detail(game, route),
            self.AutoRole: self._prefs.auto(game),
            self.RouteChoicesRole: [route_name(r) for r in choices],
            self.ChosenRouteIndexRole: choices.index(route),
            self.Ds5Role: marks_ds5(game, route),
            self.CanAutoRole: can_auto(route),
            self.SearchKey: games.names(game).lower(),
        }

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._rows):
            return None
        if role == self.GameRole:
            # The gamelist entry itself, for the worker. Deliberately not in
            # `roleNames` -- QML has no business with the raw entry -- so it is
            # answered from the list rather than copied into every row.
            return self._games[row]
        return self._rows[row].get(self.NameRole if role == Qt.DisplayRole
                                   else role)

    def searchText(self, row):
        """What a search matches against: every name the entry carries, lowered.

        Decoded with the row rather than built per test, because the proxy asks
        every row on every keystroke.
        """
        return self._rows[row][self.SearchKey] if 0 <= row < len(self._rows) else ""

    def routeAt(self, row):
        """The route this row takes, for the filter. Same value as `RouteRole`."""
        return self._rows[row][self.RouteRole] if 0 <= row < len(self._rows) else ""

    # -- per-game auto mode ------------------------------------------------

    def prefs(self):
        """The settings, to read from. A writer must call `refresh` after."""
        return self._prefs

    def setAuto(self, row, value):
        game = self.game(row)
        if game is None or not can_auto(self.routeAt(row)):
            # Nothing for the daemon to do for this game, so storing a
            # preference about it would only mislead whoever read the file.
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
        """Save, re-decode the row, and tell the view.

        Every role: the route drives most of them. The re-decode happens before
        the signal goes out, because the signal is delivered synchronously and
        the filter proxy re-tests the row the moment it arrives -- a row decoded
        afterwards would be filtered on the route it had a moment ago.
        """
        self._prefs.save()
        if 0 <= row < len(self._rows):
            self._rows[row] = self._decode(self._games[row])
        index = self.index(row, 0)
        self.dataChanged.emit(index, index)

    def refresh(self):
        """Re-decode every row from the preferences file.

        The way round the rule that a row is refilled where its data moves.
        `prefs()` hands the settings out, and a caller that writes through them
        rather than through `setAuto` or `setRouteIndex` would leave these rows
        saying what the file said before. Nothing in the app does that today --
        this is what it would have to call.
        """
        if not self._rows:
            return
        self._rows = [self._decode(game) for game in self._games]
        self.dataChanged.emit(self.index(0, 0),
                              self.index(len(self._rows) - 1, 0))

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._games)

    def setGames(self, entries):
        self.beginResetModel()
        self._games = list(entries or [])
        self._rows = [self._decode(game) for game in self._games]
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
        """Both tests read decoded fields, so a keystroke re-tests 94 rows and
        decodes none of them.

        This runs once per source row per `invalidateFilter`, which is once per
        keystroke in the search field. It used to lowercase a fresh haystack for
        every row and resolve every row's route on the way past; both are now
        settled when the row is decoded.
        """
        source = self.sourceModel()
        if source is None or not 0 <= row < source.rowCount():
            return False
        # Match against every name the entry carries, not just the displayed
        # one: the list is bilingual and people search in either language.
        if self._search and self._search not in source.searchText(row):
            return False
        # Filter on the chosen route, so a game switched to another route moves
        # to that filter rather than staying under the one it no longer takes.
        if self._route != ALL_ROUTES and source.routeAt(row) != self._route:
            return False
        return True

    def _sourceRow(self, row):
        return self.mapToSource(self.index(row, 0)).row()

    def _field(self, row, role):
        """One decoded field of a proxy row, or None off the end of the list."""
        return self.data(self.index(row, 0), role)

    def game(self, row):
        """Map a proxy row back to the underlying gamelist entry."""
        source = self.sourceModel()
        if source is None:
            return None
        return source.game(self._sourceRow(row))

    # Row lookups for a selection, so a view never has to name a role number.
    # They read the same decoded row a delegate does, rather than working the
    # route out again: the footer's detail line and its button are both bound
    # to the selected row, so each of these is a binding that re-runs whenever
    # the selection moves.

    @Slot(int, result=str)
    def nameAt(self, row):
        return self._field(row, GameListModel.NameRole) or ""

    @Slot(int, result=str)
    def detailAt(self, row):
        return self._field(row, GameListModel.DetailRole) or ""

    @Slot(int, result=bool)
    def canApplyAt(self, row):
        return bool(self._field(row, GameListModel.CanApplyRole))

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
