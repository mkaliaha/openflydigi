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


def process_index(games=None):
    """Map lowercased process name -> game, for detection.

    All 94 entries carry a name: 72 have only the singular `processGameName`
    and an empty `processGameNames` list, which is why both are indexed here.
    An earlier version of this note claimed some games were undetectable -- that
    was reading the empty plural field as "no name", and it is not true.

    The plural list is for one game shipping several executables -- typically
    one per graphics API -- rather than per-store variants. Apex Legends is the
    only entry that really uses it (`r5apex` and `r5apex_dx12`), and the other
    21 just repeat the singular name. Multi-store titles mostly have no plural list
    at all, their executables being named the same everywhere.
    """
    games = games if games is not None else load()
    index = {}
    for g in games:
        procs = list(g.get("processGameNames") or [])
        if g.get("processGameName"):
            procs.append(g["processGameName"])
        for p in procs:
            p = (p or "").strip().lower()
            if not p:
                continue
            index.setdefault(p, g)
            if p.endswith(".exe"):
                index.setdefault(p[:-4], g)
    return index
