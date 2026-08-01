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
import re
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

    # Multi-route detection. Eight of the nine pairs were `isPS5` beside
    # something else -- the choice Space Station calls MapMode. DualSense mode
    # is a global switch rather than a route now, so what is left is the one
    # game carrying two routes the daemon can actually take.
    results.append(check("one multi-route game", len(multi) == 1, f"got {len(multi)}"))
    results.append(check("every route offered is a real capability",
                         all(set(prefs.routes(g)) <= {games.tier(g), "vibration"}
                             for g in multi)))
    results.append(check("the tier is always the first route offered",
                         all(prefs.routes(g)[0] == games.tier(g) for g in all_games)))
    results.append(check("no game offers a ps5 route",
                         not any("ps5" in prefs.routes(g) for g in all_games)))
    # The flag itself is not thrown away: it is worth telling someone that a
    # game reads a DualSense directly, it is just not a per-game decision.
    aware = [g for g in all_games if games.ds5_aware(g)]
    results.append(check("the DualSense flag is still readable",
                         len(aware) == 23, f"{len(aware)} games"))
    results.append(check("a DualSense-only game has no route to take",
                         all(games.tier(g) == "unknown" for g in aware
                             if not g.get("isVibration") and not g.get("modDownLoadUrl"))))

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
        game = multi[0]
        results.append(check("a multi-route game defaults to its tier",
                             p.route(game) == games.tier(game), p.route(game)))
        p.set_route(game, "vibration")
        results.append(check("choosing another route changes the route",
                             p.route(game) == "vibration", p.route(game)))

        bad = False
        try:
            p.set_route(game, "monitor")
        except ValueError:
            bad = True
        results.append(check("a route the game does not offer is refused", bad))

        # The auto default takes tier and route together, cautiously. A preset
        # the pad applies to itself may act unasked; spawning a process may not;
        # and picking a route from a dropdown may not grant either -- that is a
        # statement about how, not about whether.
        p.clear(game)
        results.append(check("the mod route does not act by default",
                             not p.auto(game), p.route(game)))
        p.set_route(game, "vibration")
        results.append(check("switching route does not turn auto on by itself",
                             not p.auto(game), p.route(game)))
        p.set_auto(game, True)
        results.append(check("an explicit yes still wins", p.auto(game)))
        # ...and the other direction still withdraws it, which is the case the
        # per-route default existed for.
        p.clear(vibration)
        results.append(check("a pad-side game acts by default",
                             p.auto(vibration)))
        p.data["games"][prefs.key(vibration)] = {"route": "vibration"}
        results.append(check("and keeps doing so on its own route",
                             p.auto(vibration)))

        # A stored route that the gamelist no longer offers is ignored, since
        # the list is refetched from Flydigi's API -- and since "ps5" was one of
        # them, every preference file written before this change carries one.
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

    # The entry records which box to re-enter at the moment it is written, so
    # what it names has to be checked, not just that a file is there.
    results.append(check("the box is read out of a distrobox Exec line",
                         setup._named_container(
                             'distrobox enter -n a-box -- bash -lc "x"') == "a-box"))
    results.append(check("and out of a toolbox one",
                         setup._named_container('toolbox run -c a-box bash -lc "x"')
                         == "a-box"))
    results.append(check("a native Exec line names no box",
                         setup._named_container('bash -lc "x"') is None))

    with tempfile.TemporaryDirectory() as tmp:
        real = setup.DESKTOP_PATH
        try:
            def entry_saying(line):
                setup.DESKTOP_PATH = os.path.join(tmp, "e.desktop")
                with open(setup.DESKTOP_PATH, "w") as fh:
                    fh.write(f"[Desktop Entry]\nExec={line}\n")

            entry_saying(f'bash -lc "cd {setup.ROOT} && exec python3 -m gui"')
            results.append(check("an entry for this checkout counts as installed",
                                 setup.desktop_installed()))

            entry_saying('bash -lc "cd /somewhere/else && exec python3 -m gui"')
            results.append(check("an entry for another checkout does not",
                                 not setup.desktop_installed()))

            if setup.container_name():
                entry_saying(f'distrobox enter -n not-this-box -- bash -lc '
                             f'"cd {setup.ROOT} && exec python3 -m gui"')
                results.append(check("an entry naming another box does not",
                                     not setup.desktop_installed()))
        finally:
            setup.DESKTOP_PATH = real

    entry = setup.desktop_text()
    # Against `gui/main.py` itself rather than against a literal: the two names
    # have to agree or the window is not associated with its launcher, and a
    # test that spells the answer out passes happily while they drift apart.
    # Read as text, because this file is one of the ones that must not import Qt.
    with open(os.path.join(setup.ROOT, "gui", "main.py")) as fh:
        declared = re.search(r'setDesktopFileName\("([^"]+)"\)', fh.read())
    results.append(check("the entry is named to match setDesktopFileName",
                         declared is not None
                         and setup.DESKTOP_NAME == declared.group(1) + ".desktop"))
    results.append(check("the entry it replaces is not the one it installs",
                         setup.STALE_DESKTOP != setup.DESKTOP_PATH))
    results.append(check("the entry declares itself an application",
                         "Type=Application" in entry))
    results.append(check("the entry does not open a terminal",
                         "Terminal=false" in entry))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
