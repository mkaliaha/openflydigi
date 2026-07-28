# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Per-game auto-mode preferences, shared by the daemon and the app.

Auto mode is a per-game decision: "when this game starts, do the right thing
for it without me". What that means depends on the game's route -- load a
vibration preset onto the pad, or run a driver for as long as the game lives.

Nine games support more than one route, so "the right thing" is itself a
per-game choice; see `routes()`.

Keyed by the gamelist's `id`, which is present and unique across all 94 entries
-- names are not, since the same title appears differently per store, and the
process name is a list for the games that ship one executable per graphics API.

**Defaults are per route, not global.** Anything the pad does to itself defaults
to on: a vibration preset is a handful of numbers written to the controller,
which is what the daemon already did before any of this existed, and turning
that off by default would be a regression dressed as a feature. Routes that
spawn a process, read another process's memory, or take the controller over
default to off, because starting those unasked is not a preference, it is a
surprise. Either way one toggle per game overrides it.

The file is ~/.config/flydigi/games.json. It is rewritten atomically because
the daemon re-reads it while the app is editing it.
"""
import json
import os

from . import games

DEFAULT_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "flydigi", "games.json")

VERSION = 1

# Routes that may act on their own. See the module docstring.
AUTO_BY_DEFAULT = ("vibration",)

def routes(game):
    """Every route a game supports, the one `games.tier` picks first.

    A game's capability flags are not exclusive and `tier()` returns only the
    winner of its priority chain, which hides the alternatives. Nine of the 94
    entries carry more than one:

        6  XGameMonitor mod + isPS5   -- the choice Space Station calls MapMode
        2  vibration + isPS5          -- Apex Legends, Uncharted: Lost Legacy
        1  Fallout 4 mod + vibration

    Only the first six were modelled at first, on the assumption that MapMode
    was the whole story. Counting the flag combinations across the gamelist
    said otherwise, so this returns a list and the preference picks from it.

    `tier()` stays at the head, so a game nobody has an opinion about behaves
    exactly as it did before any of this existed.
    """
    first = games.tier(game)
    out = [first]
    for route, present in (("vibration", game.get("isVibration")),
                           ("ps5", game.get("isPS5"))):
        if present and route not in out:
            out.append(route)
    return out


def has_choice(game):
    """True when a game supports more than one route, so the pick is real."""
    return len(routes(game)) > 1


def default_auto_for(route):
    return route in AUTO_BY_DEFAULT


def key(game):
    return str(game.get("id"))


class Prefs:
    """The preferences file, loaded once and saved on change.

    Unknown keys are preserved on save so a newer app writing a field this
    version does not know about does not lose it on the next toggle here.
    """

    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path) as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("version", VERSION)
        # The file shipped as an empty object before it had a schema, so an
        # absent "games" is the normal first-run state rather than corruption.
        if not isinstance(raw.get("games"), dict):
            raw["games"] = {}
        return raw

    def _entry(self, game):
        return self.data["games"].get(key(game)) or {}

    def _update(self, game, field, value):
        entry = dict(self._entry(game))
        entry[field] = value
        self.data["games"][key(game)] = entry

    # -- auto -------------------------------------------------------------

    def auto(self, game):
        """Whether this game acts on its own, falling back to its route default.

        The default follows the *chosen* route rather than the game's tier, so
        switching Apex Legends from its vibration preset to DualSense emulation
        also stops it acting unasked -- taking the controller over is not
        something to inherit from a preset's default.
        """
        entry = self._entry(game)
        if "auto" in entry:
            return bool(entry["auto"])
        return default_auto_for(self.route(game))

    def set_auto(self, game, value):
        self._update(game, "auto", bool(value))

    def is_explicit(self, game):
        """True when the user has actually chosen, rather than inheriting."""
        return "auto" in self._entry(game)

    def clear(self, game):
        """Drop the override and go back to the route default."""
        self.data["games"].pop(key(game), None)

    # -- route ------------------------------------------------------------

    def route(self, game):
        """The route to actually take -- what the daemon dispatches on.

        A stored choice that is no longer offered is ignored rather than
        honoured: the gamelist is refetched from Flydigi's API, so a route can
        disappear from under a preference saved months earlier.
        """
        available = routes(game)
        chosen = self._entry(game).get("route")
        return chosen if chosen in available else available[0]

    def set_route(self, game, route):
        available = routes(game)
        if route not in available:
            raise ValueError(
                f"{game.get('enGameName')} has no {route!r} route; it offers "
                + ", ".join(available))
        self._update(game, "route", route)

    def set_mode(self, game, value):
        if value not in (MODE_FLYDIGI, MODE_PS5):
            raise ValueError(f"unknown mode {value!r}")
        self._update(game, "mode", value)

    # -- persistence ------------------------------------------------------

    def save(self):
        """Atomic, because the daemon re-reads this file while it is written."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def mtime(self):
        """For the daemon's reload check. 0 when the file is not there yet."""
        try:
            return os.stat(self.path).st_mtime
        except OSError:
            return 0.0
