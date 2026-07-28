#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for game detection against the real gamelist.

Guards the process index, where two entries can claim the same executable and
a single pass resolved the clash by position in the file.

    python3 tests/test_games.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import games  # noqa: E402


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if not ok else ""))
    return bool(ok)


def main():
    results = []
    all_games = games.load()
    # Built from this same list, so entries can be compared by identity.
    index = games.process_index(all_games)

    results.append(check("every game is detectable",
                         all(g.get("processGameName") or g.get("processGameNames")
                             for g in all_games)))

    # The Uncharted collection is one Steam app (1659420) with two executables
    # on two different routes, and both entries list both names. The singular
    # name has to decide, or Lost Legacy runs A Thief's End's memory config.
    u4 = index.get("u4")
    tll = index.get("tll")
    results.append(check("u4 resolves to A Thief's End",
                         u4 and "Thief" in u4.get("enGameName", ""),
                         u4 and u4.get("enGameName")))
    results.append(check("tll resolves to Lost Legacy",
                         tll and "Lost Legacy" in tll.get("enGameName", ""),
                         tll and tll.get("enGameName")))
    results.append(check("the two Uncharted entries take different routes",
                         u4 and tll and games.tier(u4) != games.tier(tll),
                         f"{games.tier(u4)} vs {games.tier(tll)}" if u4 and tll else ""))

    # OVERWATCH's plural list contains two other games' executables.
    for name, expected in (("horizonforbiddenwest", "Horizon"),
                           ("riftapart", "Ratchet"),
                           ("overwatch", "OVERWATCH")):
        got = index.get(name)
        results.append(check(f"{name} resolves to {expected}",
                             got and expected in got.get("enGameName", ""),
                             got and got.get("enGameName")))

    # Every entry must own the name it calls its own, whatever else claims it.
    stolen = []
    for g in all_games:
        singular = (g.get("processGameName") or "").strip().lower()
        if singular and index.get(singular) is not g:
            stolen.append((g.get("enGameName"), singular,
                           index[singular].get("enGameName")))
    results.append(check("no entry loses its own process name to another",
                         not stolen, str(stolen)))

    # Apex Legends' second executable is a graphics-API variant, not a sibling.
    results.append(check("both Apex Legends executables resolve to it",
                         index.get("r5apex") is index.get("r5apex_dx12") is not None))

    # .exe suffixes are indexed both ways, since Proton cmdlines carry them.
    exe = [g for g in all_games
           if (g.get("processGameName") or "").lower().endswith(".exe")]
    results.append(check("a .exe name is indexed with and without the suffix",
                         all(index.get(g["processGameName"].lower())
                             is index.get(g["processGameName"].lower()[:-4])
                             for g in exe) if exe else True,
                         f"{len(exe)} entries end in .exe"))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
