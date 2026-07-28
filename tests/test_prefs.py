#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for per-game auto-mode preferences and the setup checklist.

Runs against the real gamelist, since the route defaults are only meaningful
against real entries, but writes preferences to a temporary file.

    python3 tests/test_prefs.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import games, prefs, setup  # noqa: E402


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if not ok else ""))
    return bool(ok)


def _pyside_here():
    """Whether this interpreter could start the UI. Not imported -- asked of a
    subprocess, because the backend's test run must stay Qt-free."""
    import subprocess
    try:
        subprocess.run([sys.executable, "-c", "import PySide6"],
                       capture_output=True, timeout=60, check=True)
    except Exception:
        return False
    return True


def main():
    results = []
    all_games = games.load()
    multi = [g for g in all_games if prefs.has_choice(g)]

    # Single-route samples deliberately: the first vibration-tier game in the
    # list also offers PS5, which quietly made a fallback assertion vacuous.
    def only(route):
        return next(g for g in all_games if prefs.routes(g) == [route])

    vibration = only("vibration")
    monitor = only("monitor")

    # Multi-route detection. Six of these are the MapMode pairs Space Station
    # offers; the other three were missed by modelling only that pair.
    results.append(check("nine multi-route games", len(multi) == 9, f"got {len(multi)}"))
    results.append(check("every route offered is a real capability",
                         all(set(prefs.routes(g)) <= {games.tier(g), "vibration", "ps5"}
                             for g in multi)))
    results.append(check("the tier is always the first route offered",
                         all(prefs.routes(g)[0] == games.tier(g) for g in all_games)))
    names = {g.get("enGameName") for g in multi}
    results.append(check("the vibration+ps5 pair is included",
                         any("Lost Legacy" in n for n in names)
                         and any("Apex Legends" in n for n in names), str(sorted(names))))

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "games.json")

        # Route defaults: the pad-side route acts, the intrusive ones do not.
        p = prefs.Prefs(path)
        results.append(check("vibration is automatic by default", p.auto(vibration)))
        results.append(check("monitor is not automatic by default", not p.auto(monitor)))
        results.append(check("no default is an explicit choice",
                             not p.is_explicit(vibration)))

        # An override survives a round trip and outranks the default.
        p.set_auto(vibration, False)
        p.set_auto(monitor, True)
        p.save()
        p = prefs.Prefs(path)
        results.append(check("override off survives a reload", not p.auto(vibration)))
        results.append(check("override on survives a reload", p.auto(monitor)))
        results.append(check("override reads as explicit", p.is_explicit(vibration)))

        # Reset returns to the route default rather than to false.
        p.clear(vibration)
        results.append(check("reset restores the route default", p.auto(vibration)))
        results.append(check("reset clears explicitness", not p.is_explicit(vibration)))

        # Routes: unchanged for ordinary games, chosen for multi-route ones.
        results.append(check("route of a vibration game is its tier",
                             p.route(vibration) == "vibration", p.route(vibration)))
        game = games.find("Cyberpunk", all_games)
        results.append(check("a multi-route game defaults to Flydigi's own effects",
                             p.route(game) == "monitor", p.route(game)))
        p.set_route(game, "ps5")
        results.append(check("choosing another route changes the route",
                             p.route(game) == "ps5", p.route(game)))
        results.append(check("route and auto are independent",
                             p.auto(game) is False and p.route(game) == "ps5"))

        bad = False
        try:
            p.set_route(game, "vibration")
        except ValueError:
            bad = True
        results.append(check("a route the game does not offer is refused", bad))

        # The auto default follows the chosen route, not the tier: a preset the
        # pad applies to itself may act unasked, taking the pad over may not.
        apex = games.find("Apex Legends", all_games)
        results.append(check("vibration route acts by default", p.auto(apex)))
        p.set_route(apex, "ps5")
        results.append(check("switching to PS5 withdraws the default",
                             not p.auto(apex), p.route(apex)))
        p.set_auto(apex, True)
        results.append(check("an explicit yes still wins", p.auto(apex)))

        # A stored route that the gamelist no longer offers is ignored, since
        # the list is refetched from Flydigi's API.
        p.data["games"][prefs.key(vibration)] = {"route": "ps5"}
        results.append(check("a route that vanished falls back to the tier",
                             p.route(vibration) == "vibration", p.route(vibration)))

        # Unknown fields written by a newer version are not dropped on save.
        p.save()
        with open(path) as fh:
            data = json.load(fh)
        data["games"][prefs.key(game)]["fromTheFuture"] = 42
        with open(path, "w") as fh:
            json.dump(data, fh)
        p = prefs.Prefs(path)
        p.set_auto(game, True)
        p.save()
        with open(path) as fh:
            kept = json.load(fh)["games"][prefs.key(game)]
        results.append(check("unknown fields survive a save",
                             kept.get("fromTheFuture") == 42, str(kept)))

        # The file shipped as an empty object before it had a schema.
        with open(path, "w") as fh:
            fh.write("{}")
        p = prefs.Prefs(path)
        results.append(check("an empty file loads as first-run",
                             p.auto(vibration) and not p.is_explicit(vibration)))

        # Corrupt content must not take the daemon down with it.
        with open(path, "w") as fh:
            fh.write("not json at all")
        p = prefs.Prefs(path)
        results.append(check("corrupt file falls back to defaults",
                             p.auto(vibration) and not p.auto(monitor)))

        # mtime drives the daemon's reload, so it has to move on save.
        missing = prefs.Prefs(os.path.join(tmp, "absent.json")).mtime()
        p.save()
        results.append(check("mtime is 0 before the file exists", missing == 0.0))
        results.append(check("mtime is set after a save", p.mtime() > 0))

    # Setup: the rule comparison ignores comments, which is why the checklist
    # stopped reporting the live rules as stale over an added SPDX header.
    # REUSE-IgnoreStart -- the sample below is data, not this file's licence
    a = "# comment\nKERNEL==\"hidraw*\", TAG+=\"uaccess\"\n"
    b = "# SPDX-License-Identifier: CC0-1.0\n\nKERNEL==\"hidraw*\", TAG+=\"uaccess\"\n"
    # REUSE-IgnoreEnd
    results.append(check("rule comparison ignores comments",
                         setup.effective_rules(a) == setup.effective_rules(b)))
    results.append(check("rule comparison sees a real change",
                         setup.effective_rules(a) != setup.effective_rules(
                             a.replace("hidraw", "hidraw9"))))

    # The unit has to name the host interpreter and the daemon, since it runs
    # on the host while this may be imported from inside the container.
    unit = setup.unit_text()
    interpreter = setup.host_python()
    results.append(check("unit runs the host interpreter", interpreter in unit,
                         interpreter))
    results.append(check("the interpreter it names actually exists",
                         os.path.exists(setup.host_path(interpreter)),
                         interpreter))
    results.append(check("unit points at flydigid", "tools/flydigid" in unit))
    results.append(check("unit starts at login when enabled",
                         "WantedBy=default.target" in unit))

    # The launcher is written into the shared home but run by the host's menu,
    # so from inside a container it has to re-enter that container by name.
    command = setup.desktop_exec()
    box = setup.container_name()
    results.append(check("the launcher changes directory first",
                         f"cd {setup.ROOT}" in command, command))
    results.append(check("the launcher starts the app module",
                         command.endswith('-m gui"'), command))
    if box:
        results.append(check("inside a container it re-enters by name",
                             box in command and (
                                 "distrobox enter" in command
                                 or "toolbox run" in command), command))
    else:
        results.append(check("natively it just runs a shell",
                             command.startswith("bash -lc"), command))

    # A launcher pointing at an interpreter that cannot load the UI is worse
    # than no launcher: it fails with nothing on screen to say why.
    results.append(check("the launcher is only offered when it would work",
                         setup.desktop_target_runs_the_app() is bool(
                             box or _pyside_here()), str(box)))

    entry = setup.desktop_text()
    results.append(check("the entry is named to match setDesktopFileName",
                         setup.DESKTOP_NAME == "flydigi-apex5.desktop"))
    results.append(check("the entry declares itself an application",
                         "Type=Application" in entry))
    results.append(check("the entry does not open a terminal",
                         "Terminal=false" in entry))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
