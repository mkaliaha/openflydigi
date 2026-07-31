#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""What the daemon does when there is more than one pad.

The split the whole feature turns on: **tier 1 goes to every pad that takes it,
every other tier goes to one.** The vibration bind is command 82 and nothing
else -- a pad-side setting driven by the pad's own rumble, with no host process
in the loop once written -- so two pads in a local co-op game both get adaptive
triggers and neither has to be chosen. A driver rewriting trigger effects at
20 Hz, or a relay presenting one DualSense, has to pick.

`tools/flydigid` is a script rather than a module, so it is loaded by path. The
bus is the mock one: this is the case that cannot be run on a desk with one pad.

    python3 tests/test_daemon.py
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import games, mock, prefs, registry     # noqa: E402

PASSED = []
FAILED = []

SPEC = {
    "hide_real": True,
    "devices": [
        {"kind": "pad", "code": "k5", "nickname": "Desk"},
        {"kind": "pad", "code": "k5", "nickname": "Couch"},
        # On the same desk and not driven by this project. It must be
        # enumerated, named, and left alone.
        {"kind": "pad", "code": "f5", "nickname": "Vader"},
        {"kind": "dock", "type": 0},
    ],
}


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def load_daemon():
    loader = importlib.machinery.SourceFileLoader(
        "flydigid", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tools", "flydigid"))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("flydigid", loader))
    loader.exec_module(module)
    # Its log goes to stdout, which would bury the test's own output.
    module.log = lambda _message: None
    return module


def vibration_game():
    for game in games.load():
        if game.get("isVibration"):
            return game
    raise SystemExit("no vibration game in the gamelist -- run tools/fetch-configs")


def main():
    handle, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(handle, "w") as fh:
        json.dump(SPEC, fh)
    os.environ[mock.ENV] = path
    mock.reset()
    try:
        run()
    finally:
        os.environ.pop(mock.ENV, None)
        mock.reset()
        os.unlink(path)
    print(f"{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    return 1 if FAILED else 0


def run():
    daemon = load_daemon()
    game = vibration_game()

    check("the pad is present when any pad is", daemon.pad_present())

    # -- tier 1 fans out ---------------------------------------------------
    check("the bind is applied", daemon.apply_for(game, "vibration"))
    pads = {registry.label(e): mock.instance(e["path"])
            for e in registry.list_pads(deep=True)}
    check("the first pad got it", pads["Desk"].live_binds != {},
          str(pads["Desk"].live_binds))
    check("and so did the second", pads["Couch"].live_binds != {},
          str(pads["Couch"].live_binds))
    check("both triggers on each",
          sorted(pads["Desk"].live_binds) == [1, 2], str(pads["Desk"].live_binds))
    check("the Vader was left alone", pads["Vader"].live_binds == {},
          str(pads["Vader"].live_binds))

    # -- and clearing has to reach every pad it wrote to --------------------
    #
    # What is asserted is the fan-out, not what the pad makes of it. `clear_all`
    # sends command **81** mode Normal, and a vibration bind is command **82**:
    # whether 81 undoes an 82 bind on hardware is unmeasured, and predates this.
    # A test that asserted it here would be inventing the answer, so it asserts
    # what is actually in question -- that the second pad is not forgotten.
    for pad in pads.values():
        pad.live_effects.clear()
    daemon.clear_triggers()
    check("the first pad was told to clear",
          [mode for mode, _params in pads["Desk"].live_effects.values()] == [0, 0],
          str(pads["Desk"].live_effects))
    check("and so was the second",
          [mode for mode, _params in pads["Couch"].live_effects.values()] == [0, 0],
          str(pads["Couch"].live_effects))
    check("and the Vader was left alone here too",
          pads["Vader"].live_effects == {}, str(pads["Vader"].live_effects))

    # -- a pad that is away is not a failure -------------------------------
    spec = mock.spec()
    spec["devices"][1]["present"] = False
    check("applying still succeeds with one pad asleep",
          daemon.apply_for(game, "vibration"))
    spec["devices"][1]["present"] = True

    spec["devices"][0]["present"] = False
    spec["devices"][1]["present"] = False
    check("and fails when no pad it drives is there",
          not daemon.apply_for(game, "vibration"))
    check("even though a Vader is attached", daemon.pad_present())
    spec["devices"][0]["present"] = True
    spec["devices"][1]["present"] = True

    # -- every other tier acts on one pad ----------------------------------
    argv = daemon.driver_argv(game, "telemetry", "uid:abc123")
    check("a driver is told which pad", "--device" in argv, str(argv))
    check("and which one", argv[argv.index("--device") + 1] == "uid:abc123",
          str(argv))
    check("a driver with no choice made is not told one",
          "--device" not in daemon.driver_argv(game, "telemetry", None),
          str(daemon.driver_argv(game, "telemetry", None)))
    check("the vibration route starts no driver",
          daemon.driver_argv(game, "vibration", "uid:abc123") is None)

    # -- and the choice comes from the file the app writes ------------------
    settings = prefs.Prefs(os.path.join(tempfile.mkdtemp(), "games.json"))
    check("nothing is chosen to begin with", settings.primary_pad() is None)
    settings.set_primary_pad("uid:deadbeef")
    settings.save()
    check("a choice survives a reload",
          prefs.Prefs(settings.path).primary_pad() == "uid:deadbeef")
    settings.set_primary_pad(None)
    settings.save()
    check("and clearing it goes back to whichever comes first",
          prefs.Prefs(settings.path).primary_pad() is None)


if __name__ == "__main__":
    sys.exit(main())
