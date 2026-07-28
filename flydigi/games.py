# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Game list handling.

gamelist.json comes from the public, unauthenticated endpoint:
    GET https://api.flydigi.com/pc/adapter_trigger/list
Refresh with fetch_gamelist().
"""
import json
import os
import urllib.request

API_URL = "https://api.flydigi.com/pc/adapter_trigger/list"
DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gamelist.json"
)


def fetch_gamelist(path=DEFAULT_PATH, timeout=30):
    with urllib.request.urlopen(API_URL, timeout=timeout) as resp:
        payload = json.load(resp)
    with open(path, "w") as fh:
        json.dump(payload, fh)
    return payload["data"]


def load(path=DEFAULT_PATH):
    with open(path) as fh:
        return json.load(fh)["data"]


def names(game):
    return (game.get("enGameName") or "") + " " + (game.get("gameName") or "")


def find(name, games=None):
    """Find one game by substring match on either name field."""
    games = games if games is not None else load()
    needle = name.lower()
    matches = [g for g in games if needle in names(g).lower()]
    if not matches:
        raise KeyError(f"no game matching {name!r}")
    # prefer an exact English-name hit when the substring is ambiguous
    for g in matches:
        if (g.get("enGameName") or "").lower() == needle:
            return g
    return matches[0]


def tier(game):
    """Which implementation path a game needs."""
    if game.get("modDownLoadUrl"):
        mod = game.get("modName") or "(unnamed)"
        if mod == "XGameMonitor.exe":
            return "monitor"
        if mod == "ForzaDualSense.exe":
            return "telemetry"
        return "bespoke"
    if game.get("isVibration"):
        return "vibration"
    if game.get("isPS5"):
        return "ps5"
    return "unknown"


def _claim(index, name, game):
    """Record a process name for a game, without displacing an earlier claim."""
    name = (name or "").strip().lower()
    if not name:
        return
    index.setdefault(name, game)
    if name.endswith(".exe"):
        index.setdefault(name[:-4], game)


def process_index(games=None):
    """Map lowercased process name -> game, for detection.

    All 94 entries carry a name: 72 have only the singular `processGameName`
    and an empty `processGameNames` list, which is why both are indexed here.

    **The singular name is authoritative and is claimed first.** Nine entries
    put names in the plural list that are not their own singular, and four
    process names are claimed by two entries each -- so a single pass with
    `setdefault` resolved them by position in the file, which is to say by
    accident. Two passes make the entry that calls a process *its own* win:

      * `u4` and `tll` are both listed by both Uncharted entries. They are one
        Steam app (1659420) shipping two executables, and Flydigi splits them
        into two entries taking different routes -- A Thief's End reads memory,
        Lost Legacy uses a vibration preset. First-wins gave `tll` to A Thief's
        End, so starting Lost Legacy ran the wrong game's memory config.
      * OVERWATCH's plural list contains `HorizonForbiddenWest` and `RiftApart`,
        which belong to two other entries. Those two resolved correctly only
        because they happened to come first.

    So the plural list is not merely "one executable per graphics API" as this
    note previously claimed: it also carries sibling titles (both Uncharted
    entries, six Call of Duty executables under one entry) and, for OVERWATCH,
    names that look like editing debris.
    """
    games = games if games is not None else load()
    index = {}
    for g in games:
        _claim(index, g.get("processGameName"), g)
    for g in games:
        for p in (g.get("processGameNames") or []):
            _claim(index, p, g)
    return index
