#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Self-test for the app's view-agnostic models.

These need no display at all -- not even Qt's offscreen platform -- because
nothing here is a widget or a QML item. That is the whole point of the
extraction: the facts being asserted ("editing marks dirty", "one remap changes
one packet's worth of blob") are properties of the state, not of a view.

    .venv/bin/python tests/test_models.py

Skipped (exit 0) when PySide6 is not installed, so the backend's test run stays
dependency-free.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtCore import QCoreApplication, QModelIndex
except ImportError:
    print("PySide6 not installed -- skipping model tests")
    sys.exit(0)

from flydigi import lighting as led
from flydigi import dsmode as ds_backend
from flydigi import effects, mapping, prefs
from flydigi import setup as system_setup
from gui import models
from tests.fake_pad import FakePad, blank_blob

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def role(model, row, role_name):
    """Read one role by its QML name, the way a delegate would."""
    wanted = next(key for key, value in model.roleNames().items()
                  if value == role_name)
    return model.data(model.index(row, 0), wanted)


# -- profile ---------------------------------------------------------------

def make_profile(load=True, active=0):
    """A profile model with slot 0 open and the pad running `active`.

    The pad's active slot is set because the real app always learns it -- the
    startup status read feeds `setActive` -- and saving depends on it: the save
    command commits whichever profile is running, so a model that does not know
    refuses to save at all.
    """
    profile = models.ProfileModel()
    profile.setSlotCount(4)
    requested = []
    profile.loadRequested.connect(requested.append)
    profile.setActive(active)
    profile.select(0)
    if load:
        profile.profileLoaded(0, bytes(blank_blob("Profile 1")), "Profile 1")
    return profile, requested


def test_selecting_an_unread_profile_requests_it_once():
    profile, requested = make_profile(load=False)
    check("selecting an unread profile requests a read", requested == [0],
          str(requested))
    profile.select(0)
    check("asking again while pending does not re-request", requested == [0],
          str(requested))

    profile.profileLoaded(0, bytes(blank_blob()), "Profile 1")
    profile.select(1)
    profile.select(0)
    check("revisiting a loaded profile does not re-read it", requested == [0, 1],
          str(requested))


def test_a_loaded_profile_is_clean():
    profile, _ = make_profile()
    check("a freshly loaded profile is open", profile.loaded)
    check("a freshly loaded profile is not dirty", not profile.dirty)
    check("its hint says so", profile.hint == "Matches what is on the pad.",
          profile.hint)


def test_editing_a_key_marks_dirty_and_lands_in_the_blob():
    profile, _ = make_profile()
    keys = profile.keys
    row = keys.rowForKey("m1")
    check("rowForKey finds a key", row >= 0, str(row))

    keys.setTarget(row, models.TARGETS.index("a"))
    check("editing marks the profile dirty", profile.dirty)
    check("the edit reaches the config",
          profile.config.remapped() == {"m1": ("a", 0, 0)},
          str(profile.config.remapped()))
    check("the row reports itself remapped", role(keys, row, b"isRemapped"))
    check("the row reports its new target", role(keys, row, b"target") == "a",
          str(role(keys, row, b"target")))


RECORDED = [{"delay": 0, "key": "a", "event": mapping.MACRO_PRESS},
            {"delay": 80, "key": "a", "event": mapping.MACRO_RELEASE}]


def key_index(name):
    return mapping.APEX5_KEYS.index(name)


def test_a_recording_becomes_a_macro_row():
    profile, _ = make_profile()
    macros = profile.macros
    check("a fresh profile has no macros", macros.count == 0, str(macros.count))
    check("and has room for one", macros.canAdd)

    requested = []
    macros.recordRequested.connect(requested.append)
    macros.record(key_index("m1"))
    check("recording asks the worker", len(requested) == 1, str(requested))
    check("and says so while it waits", macros.recording)
    check("naming the key it is recording for", macros.recordingKey == "M1",
          macros.recordingKey)

    macros.recorded(RECORDED)
    check("recording ends when the steps arrive", not macros.recording)
    check("the macro is a row", macros.count == 1, str(macros.count))
    check("the row is labelled by its key", role(macros, 0, b"label") == "M1",
          str(role(macros, 0, b"label")))
    check("the row counts its steps", role(macros, 0, b"stepCount") == 2,
          str(role(macros, 0, b"stepCount")))
    check("the row reports how long it takes",
          role(macros, 0, b"duration") == 80, str(role(macros, 0, b"duration")))
    check("editing marks the profile dirty", profile.dirty)
    check("the key now runs it", profile.config.mapping("m1")[0] == "macro")
    # The Buttons page has to notice: the key's row changed under it.
    check("the key list was told to refresh",
          role(profile.keys, profile.keys.rowForKey("m1"), b"target") == "macro")


def test_a_recording_that_caught_nothing_says_why():
    """The likeliest reason is third-party control, and it is not guessable."""
    profile, _ = make_profile()
    refusals = []
    profile.macros.refused.connect(refusals.append)
    profile.macros.record(key_index("m1"))
    profile.macros.recorded([])
    check("an empty recording is refused", len(refusals) == 1, str(refusals))
    check("and the reason names what to do about it",
          "third-party" in refusals[0], refusals[0])
    check("nothing was stored", profile.macros.count == 0)


def test_a_macros_type_and_interval_write_through():
    profile, _ = make_profile()
    macros = profile.macros
    macros.record(key_index("m1"))
    macros.recorded(RECORDED)

    macros.setType(0, models.MACRO_TYPES.index(("While held",
                                                mapping.MACRO_WHILE_HELD)))
    check("the type reaches the blob",
          profile.config.macros()[0]["type"] == mapping.MACRO_WHILE_HELD,
          str(profile.config.macros()[0]["type"]))
    macros.setInterval(0, 300)
    check("the interval reaches the blob",
          profile.config.macros()[0]["interval"] == 300,
          str(profile.config.macros()[0]["interval"]))
    check("and the row reports it", role(macros, 0, b"interval") == 300,
          str(role(macros, 0, b"interval")))


def test_deleting_a_macro_gives_the_key_back():
    profile, _ = make_profile()
    macros = profile.macros
    macros.record(key_index("m1"))
    macros.recorded(RECORDED)
    macros.remove(0)
    check("the row is gone", macros.count == 0, str(macros.count))
    check("the key is its own again", profile.config.mapping("m1")[0] == "m1",
          str(profile.config.mapping("m1")))


def test_the_page_stops_offering_more_than_the_pad_holds():
    profile, _ = make_profile()
    macros = profile.macros
    for name in ("m1", "m2", "m3", "m4", "c"):
        macros.record(key_index(name))
        macros.recorded(RECORDED)
    check("all five slots are used", macros.count == mapping.MACRO_SLOTS,
          str(macros.count))
    check("and a sixth is not offered", not macros.canAdd)
    check("the budget is reported", macros.stepsUsed == 10, str(macros.stepsUsed))


def test_turbo_survives_a_target_change():
    """Setting one field must not silently clear the others."""
    profile, _ = make_profile()
    keys = profile.keys
    row = keys.rowForKey("m2")
    keys.setTarget(row, models.TARGETS.index("x"))
    keys.setTurbo(row, 12)
    check("turbo is stored", role(keys, row, b"turbo") == 12,
          str(role(keys, row, b"turbo")))
    check("the target survived the turbo edit",
          role(keys, row, b"target") == "x", str(role(keys, row, b"target")))

    keys.setTurboMode(row, 1)
    check("turbo mode is stored", role(keys, row, b"turboMode") == 1)
    check("turbo frequency survived the mode edit",
          role(keys, row, b"turbo") == 12, str(role(keys, row, b"turbo")))


def test_write_carries_the_blob_and_the_previous_copy():
    profile, _ = make_profile()
    before = bytes(profile.config.blob)
    profile.keys.setTarget(profile.keys.rowForKey("m1"), models.TARGETS.index("b"))

    seen = []
    profile.writeRequested.connect(lambda *args: seen.append(args))
    profile.write(False)
    check("write emits one request", len(seen) == 1, str(len(seen)))
    cfg_id, blob, previous, save = seen[0]
    check("write names the open slot", cfg_id == 0, str(cfg_id))
    check("write sends the edited blob", blob == bytes(profile.config.blob))
    check("write sends the pad's copy as the diff base", previous == before)
    check("write does not save unless asked", save is False, str(save))

    profile.write(True)
    check("saving asks for a save", seen[1][3] is True, str(seen[1][3]))


def test_applying_without_saving_still_offers_a_save():
    """Binding the save button to `dirty` made keeping a change impossible.

    Applying leaves nothing to apply, so `dirty` goes false -- but the change
    is only in the pad's working memory and dies with the next sleep.
    """
    profile, _ = make_profile()
    check("a freshly read profile needs no save", not profile.saveNeeded)

    profile.keys.setTarget(profile.keys.rowForKey("m1"), models.TARGETS.index("a"))
    profile.confirmWritten(0, False)          # applied, not committed
    check("applying clears dirty", not profile.dirty)
    check("but the change still needs saving", profile.saveNeeded)
    check("and the hint says the pad will forget it",
          "lost when the pad sleeps" in profile.hint, profile.hint)

    profile.confirmWritten(0, True)           # now committed
    check("saving clears the pending save", not profile.saveNeeded)
    check("and the hint says it matches the pad",
          profile.hint == "Matches what is on the pad.", profile.hint)


def test_saving_a_profile_the_pad_is_not_running_is_refused():
    """The save command carries no slot id -- it commits whatever is running.

    Command 166 sets only a version (SaveCurrentMappingConfigCommandFactory);
    the slot-addressed variant is a different command. Browsing restores the
    pad deliberately, so the edited profile is routinely not the running one,
    and saving then would commit the wrong slot while reporting success.
    """
    profile, _ = make_profile()
    profile.setActive(1)                       # pad runs slot 1, we edit slot 0
    profile.keys.setTarget(profile.keys.rowForKey("m1"), models.TARGETS.index("a"))

    check("saving the wrong slot is not offered", not profile.canSaveToFlash)
    refusals, writes = [], []
    profile.saveRefused.connect(refusals.append)
    profile.writeRequested.connect(lambda *a: writes.append(a))

    profile.write(True)
    check("the save is refused", len(refusals) == 1, str(refusals))
    check("and nothing is sent to the pad", writes == [], str(writes))
    check("the refusal says what to do",
          "Switch the pad to this profile" in refusals[0], refusals[0])
    check("the hint explains why", "whichever profile it is running" in profile.hint,
          profile.hint)

    # Applying without saving is still fine: those packets carry the slot id.
    profile.write(False)
    check("applying is still allowed", len(writes) == 1, str(writes))

    profile.setActive(0)                       # pad now runs the edited slot
    check("saving is offered once the pad is on it", profile.canSaveToFlash)
    profile.write(True)
    check("and now it is sent", len(writes) == 2 and writes[1][3] is True,
          str(writes))


def test_reselecting_the_open_profile_keeps_unsaved_edits():
    """A radio delegate fires clicked() on the row that is already checked."""
    profile, _ = make_profile()
    profile.title = "Racing"
    profile.keys.setTarget(profile.keys.rowForKey("m1"), models.TARGETS.index("a"))
    check("the edits are there", profile.dirty)

    profile.select(0)                          # the row it is already on
    check("re-selecting keeps the edits", profile.dirty,
          "the edits were silently discarded")
    check("and keeps the new name", profile.title == "Racing", profile.title)
    check("and the remap", profile.config.remapped() == {"m1": ("a", 0, 0)},
          str(profile.config.remapped()))


def test_reloading_still_re_reads_the_open_profile():
    """The guard above must not make "Reload from pad" a no-op."""
    profile, requested = make_profile()
    before = list(requested)
    profile.forget()
    check("reload asks the pad again", requested == before + [0], str(requested))
    check("and the profile is closed until it arrives", not profile.loaded)


def test_selecting_an_unread_profile_clears_the_title():
    """Otherwise the name field goes on showing the profile you left."""
    profile, _ = make_profile()
    profile.title = "Racing"
    seen = []
    profile.titleChanged.connect(lambda: seen.append(profile.title))

    profile.select(1)                          # not read yet
    check("switching to an unread profile announces the title change",
          seen and seen[-1] == "", str(seen))
    check("and the title is empty rather than the previous one",
          profile.title == "", profile.title)


def test_lighting_applying_without_saving_still_offers_a_save():
    model = make_lighting()
    check("a freshly read lighting config needs no save", not model.saveNeeded)
    model.effect = models.EFFECT_NAMES.index("Static")
    model.confirmWritten(False)
    check("applying clears dirty", not model.dirty)
    check("but it still needs saving", model.saveNeeded)
    model.confirmWritten(True)
    check("saving clears it", not model.saveNeeded)


def test_confirming_a_write_clears_dirty():
    profile, _ = make_profile()
    profile.keys.setTarget(profile.keys.rowForKey("m3"), models.TARGETS.index("y"))
    check("edited profile is dirty", profile.dirty)
    profile.confirmWritten(0, True)
    check("confirming clears the dirty state", not profile.dirty)
    check("the pad's copy is now the edited one",
          profile.slots.stored(0) == bytes(profile.config.blob))


def test_reset_all_clears_every_remap():
    seeded = mapping.MappingConfig(blank_blob())
    seeded.set_mapping("m3", "x")
    seeded.set_mapping("m4", "y")

    profile = models.ProfileModel()
    profile.setSlotCount(4)
    profile.select(0)
    profile.profileLoaded(0, bytes(seeded.blob), "Profile 1")
    check("existing remaps are visible",
          profile.config.remapped() == {"m3": ("x", 0, 0), "m4": ("y", 0, 0)},
          str(profile.config.remapped()))

    profile.resetAll()
    check("reset clears them all", profile.config.remapped() == {},
          str(profile.config.remapped()))
    check("reset marks the profile dirty", profile.dirty)


def test_rename_reaches_the_config():
    profile, _ = make_profile()
    profile.title = "Racing"
    check("rename marks dirty", profile.dirty)
    check("rename reaches the config", profile.config.title == "Racing",
          profile.config.title)


def test_a_title_is_capped_at_what_the_pad_stores():
    """The pad keeps 20 bytes of UTF-16 and truncates without saying so."""
    profile, _ = make_profile()
    profile.title = "a name far too long for the pad"
    check("the title is capped", len(profile.title) == models.TITLE_MAX_CHARS,
          f"{len(profile.title)}: {profile.title!r}")
    check("the cap is ten characters", models.TITLE_MAX_CHARS == 10,
          str(models.TITLE_MAX_CHARS))
    check("what the model reports is what the config holds",
          profile.title == profile.config.title,
          f"{profile.title!r} vs {profile.config.title!r}")


def test_vibration_writes_through_to_the_blob():
    profile, _ = make_profile()
    vib = profile.vibration
    vib.enabled = True
    check("master switch reaches the config", profile.config.vibration_enabled)

    left = vib.side("left")
    left.enabled = True
    left.minimum = 40
    left.maximum = 200
    left.scale = 128
    enabled, minimum, maximum, scale = profile.config.vibration("left")
    check("per-side vibration reaches the config",
          (enabled, minimum, maximum, scale) == (True, 40, 200, 128),
          str((enabled, minimum, maximum, scale)))
    check("the model reads back what it wrote",
          (left.minimum, left.maximum, left.scale) == (40, 200, 128),
          str((left.minimum, left.maximum, left.scale)))
    check("editing vibration marks dirty", profile.dirty)


def test_vibration_keeps_min_below_max():
    """The backend swaps an inverted window; the model must report the swap."""
    profile, _ = make_profile()
    left = profile.vibration.side("left")
    left.minimum = 200
    left.maximum = 50
    check("an inverted window is corrected", left.minimum <= left.maximum,
          f"min {left.minimum} max {left.maximum}")


def test_trigger_fields_are_independent():
    profile, _ = make_profile()
    right = profile.triggers.side("right")
    right.effect = 1                     # racing
    right.setEffectParam("start", 60)
    right.setEffectParam("resistance", 200)
    right.strokeStart = 15
    right.strokeEnd = 200

    mode, params = profile.config.trigger_effect("right")
    check("trigger effect reaches the config",
          mode == models.TRIGGER_MODES[1][1], str(mode))
    check("both knobs are kept", (params[0], params[1]) == (60, 200),
          str(params[:2]))
    curve = profile.config.trigger_curve("right")
    check("the stroke window reaches the curve block at 123",
          (curve["zero"], curve["end"]) == (15, 200), str(curve))
    # The window is the curve block, the effect's knobs are the force-trigger
    # block, and the probe that settled which of the two the pad plays would be
    # worthless if the app wrote the window into both. 195/196 is inert here.
    check("the window stays out of the effect's parameter slots",
          (params[0], params[1]) == (60, 200), str(params[:2]))
    check("the model reads back its own effect index", right.effect == 1)
    check("editing a trigger marks dirty", profile.dirty)


def test_each_effect_offers_its_own_controls():
    """The knobs are not the same from one effect to the next, so the page
    asks the model what to draw rather than drawing a fixed pair."""
    profile, _ = make_profile()
    right = profile.triggers.side("right")

    check("all six effects are offered", len(profile.triggers.effectNames) == 6,
          str(profile.triggers.effectNames))
    check("General offers no controls at all",
          right.effect == 0 and right.effectParams == [], str(right.effectParams))

    for index, (label, mode) in enumerate(models.TRIGGER_MODES):
        right.effect = index
        keys = [row["key"] for row in right.effectParams]
        check(f"{label}: the controls are the effect's own",
              keys == [p.key for p in effects.effect(mode).params], str(keys))
        check(f"{label}: every control is inside its own range",
              all(row["from"] <= row["value"] <= row["to"]
                  for row in right.effectParams), str(right.effectParams))
        check(f"{label}: a switch is drawn as one",
              all(row["kind"] in ("number", "switch") for row in right.effectParams))


def test_an_effect_remembers_its_numbers_across_a_switch():
    """All six share ten byte slots, so switching effect and back must not
    silently retune the one you left."""
    profile, _ = make_profile()
    right = profile.triggers.side("right")
    right.effect = 1                                    # racing
    right.setEffectParam("start", 77)

    right.effect = 4                                    # trigger lock
    check("the lock has its own position",
          right.effectParams[0]["key"] == "start", str(right.effectParams))
    right.effect = 1
    values = {row["key"]: row["value"] for row in right.effectParams}
    check("racing kept the start it was given", values["start"] == 77,
          str(values))


def test_an_unknown_knob_is_refused_rather_than_stored():
    profile, _ = make_profile()
    right = profile.triggers.side("right")
    right.effect = 4                                    # trigger lock
    before = bytes(profile.config.blob)
    right.setEffectParam("frequency", 200)              # lock has no frequency
    check("a knob this effect does not have changes nothing",
          bytes(profile.config.blob) == before)


def test_no_trigger_motor_controls_are_offered():
    """The Apex 5 has no trigger vibration motors, so the model offers nothing
    for them.

    The blob carries the block regardless -- it is one struct shared across
    the range -- and this app had a switch over it for months.
    `GenerateControllerApex5` sets seven capability flags and
    `IsSupportTriggerVibration` is not among them, while Vader 3, 4 and 5 all
    set it, and Flydigi reads the block only when the flag is on. Trigger
    haptics here come out of the force triggers instead.
    """
    profile, _ = make_profile()
    for name in ("motorEnabled", "motorStrength", "motorThreshold"):
        check(f"no {name} on the trigger model",
              not hasattr(profile.triggers, name))
    for name in ("amplitudeMin", "amplitudeMax"):
        check(f"no {name} on a trigger",
              not hasattr(profile.triggers.side("left"), name))

    # The accessor stays -- the block is real and a Vader would use it -- so
    # what is asserted is that nothing in the app writes it.
    before = bytes(profile.config.blob)
    profile.triggers.side("right").effect = 1
    profile.triggers.side("right").strokeStart = 12
    block = slice(mapping.OFF_TRIGGER_MOTOR, mapping.OFF_TRIGGER_MOTOR + 29)
    check("editing a trigger leaves the motor block alone",
          bytes(profile.config.blob)[block] == before[block],
          str(list(profile.config.blob[block])))


def test_a_stick_edit_recompiles_the_bank():
    """The one thing this page cannot get away with not doing.

    The pad has no curve evaluator -- confirmed on hardware, flattening the bank
    silences the stick while flattening the polyline at 109 changes nothing. So
    a model that stored what the user edited and stopped there would produce a
    slider that moves, a profile that goes dirty, a write that succeeds, and no
    change whatsoever in the hand.
    """
    profile, _ = make_profile()
    left = profile.sticks.left
    factory = list(left.bank)

    left.center = 30
    check("the source form is stored", profile.config.stick("left")["center"] == 30)
    check("the bank moved with it", list(left.bank) != factory, str(left.bank))
    check("and it is what the compiler produces",
          list(left.bank) == mapping.stick_bank(center=30), str(left.bank))
    check("editing a stick is a change", profile.dirty)
    check("the other stick is untouched", list(profile.sticks.right.bank) == factory)


def test_a_stick_edit_moves_the_curve_to_custom():
    profile, _ = make_profile()
    names = profile.sticks.presetNames
    check("Custom is the last preset", names[-1] == "Custom", str(names))
    check("a fresh profile is on Default", profile.sticks.left.curveType == 0)

    profile.sticks.left.edge = 15
    check("editing a node makes it Custom",
          profile.sticks.left.curveType == len(names) - 1,
          str(profile.sticks.left.curveType))

    # And picking a preset again restores its whole shape, ends included.
    profile.sticks.left.curveType = 0
    check("the preset is stored as itself", profile.sticks.left.curveType == 0)
    check("it cleared the outer dead zone", profile.sticks.left.edge == 0)


def test_a_stick_bound_to_a_key_is_not_offered_a_curve():
    """127 in the centre byte is a sentinel, not a dead zone of 127."""
    profile, _ = make_profile()
    check("a real stick says so", profile.sticks.left.isStick)

    base = mapping.OFF_JOYSTICK_CURVE
    profile.config.blob[base + 1] = mapping.CENTER_NOT_A_STICK
    profile.sticks.refresh()
    check("the sentinel is recognised", not profile.sticks.left.isStick)
    check("and no impossible dead zone is reported",
          profile.sticks.left.center == 0, str(profile.sticks.left.center))
    check("the right stick is unaffected", profile.sticks.right.isStick)


def test_circularity_is_not_part_of_the_curve():
    """It is the one field in these blocks the firmware applies itself.

    Which is also why picking a curve preset must not clear it: the preset is a
    shape for the response curve, and circularity is not part of that shape.
    """
    profile, _ = make_profile()
    check("rectangular by default", not profile.sticks.left.circular)

    profile.sticks.left.circular = True
    check("it round-trips", profile.sticks.left.circular)

    profile.sticks.left.curveType = 0
    check("picking a preset leaves it alone", profile.sticks.left.circular)
    check("and the preset still took", profile.sticks.left.curveType == 0)


def test_restore_refuses_a_wrong_sized_file(tmp="/tmp"):
    profile, _ = make_profile()
    path = os.path.join(tmp, "apex5-bad-profile.bin")
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 17)
    refusals = []
    profile.restoreFailed.connect(refusals.append)
    profile.restore(path)
    check("a wrong-sized restore is refused", len(refusals) == 1, str(refusals))
    check("the refusal says what is wrong", "17 bytes" in refusals[0],
          refusals[0])
    check("the open profile is untouched", not profile.dirty)
    os.unlink(path)


def test_backup_then_restore_round_trips(tmp="/tmp"):
    profile, _ = make_profile()
    profile.keys.setTarget(profile.keys.rowForKey("m1"), models.TARGETS.index("a"))
    profile.confirmWritten(0)            # make the edit the reference copy
    path = os.path.join(tmp, "apex5-profile.bin")
    profile.backup(path)

    profile.resetAll()
    check("reset removed the remap", profile.config.remapped() == {})
    profile.restore(path)
    check("restore brings the remap back",
          profile.config.remapped() == {"m1": ("a", 0, 0)},
          str(profile.config.remapped()))
    os.unlink(path)


def test_slot_list_tracks_active_and_current():
    profile, _ = make_profile()
    profile.setActive(1)
    check("four slots are listed", profile.slots.count == 4,
          str(profile.slots.count))
    check("the active slot is marked", role(profile.slots, 1, b"isActive"))
    check("other slots are not", not role(profile.slots, 0, b"isActive"))
    check("the open slot is marked current", role(profile.slots, 0, b"isCurrent"))
    check("an unread slot reports itself unloaded",
          not role(profile.slots, 2, b"loaded"))
    check("the read slot reports itself loaded", role(profile.slots, 0, b"loaded"))
    profile.setActive(0)
    check("the open profile is the one the pad runs", profile.canSaveToFlash)


def test_dirty_is_reported_on_the_slot_row():
    profile, _ = make_profile()
    check("nothing is dirty at rest", not role(profile.slots, 0, b"dirty"))
    profile.keys.setTarget(profile.keys.rowForKey("m1"), models.TARGETS.index("a"))
    check("the edited slot reports dirty", role(profile.slots, 0, b"dirty"))
    profile.confirmWritten(0, True)
    check("writing clears it", not role(profile.slots, 0, b"dirty"))


# -- lighting --------------------------------------------------------------

def make_lighting():
    model = models.LightingModel()
    model.configLoaded(bytes(FakePad().led_blob))
    return model


def test_lighting_loads_clean():
    model = make_lighting()
    check("lighting loads", model.loaded)
    check("a freshly read lighting config is not dirty", not model.dirty)
    check("the effect picker starts on 'keep what is on the pad'",
          model.effect == 0 and models.EFFECT_NAMES[0] == models.KEEP_CURRENT)
    check("it reports the frame geometry", "frames of" in model.info, model.info)


def test_choosing_an_effect_rewrites_the_frames():
    model = make_lighting()
    before = bytes(model._edited.blob)
    model.effect = models.EFFECT_NAMES.index("Static")
    check("choosing an effect marks dirty", model.dirty)
    check("choosing an effect changes the blob",
          bytes(model._edited.blob) != before)


def test_brightness_and_speed_write_through():
    model = make_lighting()
    model.brightness = 55
    check("brightness reaches the config", model._edited.brightness == 55,
          str(model._edited.brightness))
    check("brightness is clamped to the max",
          (setattr(model, "brightness", 999) or model.brightness) == led.BRIGHTNESS_MAX,
          str(model.brightness))

    model.speed = models.CYCLE_MAX          # fastest
    check("a fast speed stores a small cycle time",
          model.cycleTime == models.CYCLE_MIN, str(model.cycleTime))
    check("speed round-trips through the inversion",
          model.speed == models.CYCLE_MAX, str(model.speed))
    check("editing lighting marks dirty", model.dirty)


def test_the_vibration_light_effect_is_byte_nine():
    """The switch spent its life on byte 2, which does nothing on this pad.

    Asserted against the blob offset rather than the accessor, because the whole
    defect was an accessor pointing one field away from the one that works.
    """
    from flydigi import lighting as led_backend

    model = make_lighting()
    check("the fake pad ships with it on, as the real one does", model.gripSync)
    check("and a freshly read config is clean", not model.dirty)

    model.gripSync = False
    check("turning it off reaches byte 9",
          model._edited.blob[led_backend.OFF_GRIP_SYNC] == 0)
    check("and leaves byte 2 alone",
          model._edited.blob[led_backend.OFF_CLICK_FEEDBACK] == 0)
    check("the accessor agrees", not model._edited.grip_sync)
    check("toggling it marks dirty", model.dirty)

    model.gripSync = True
    check("and back on lands on byte 9 again",
          model._edited.blob[led_backend.OFF_GRIP_SYNC] == 1)
    check("byte 2 never moved",
          model._edited.blob[led_backend.OFF_CLICK_FEEDBACK] == 0)


def test_colour_list_respects_what_the_effect_allows():
    model = make_lighting()
    colours = model.colours
    model.effect = models.EFFECT_NAMES.index("Static")
    check("a single-colour effect allows one colour", colours.allowed == 1,
          str(colours.allowed))
    check("no more can be added", not colours.canAdd)

    model.effect = models.EFFECT_NAMES.index("Breathing")
    check("a multi-colour effect allows five", colours.allowed == led.MAX_COLOURS,
          str(colours.allowed))
    colours.add()
    check("a colour can be added", colours.count == 2, str(colours.count))
    colours.remove()
    check("a colour can be removed", colours.count == 1, str(colours.count))
    check("the last one cannot be removed",
          (colours.remove() or colours.count) == 1, str(colours.count))

    colours.add()
    model.effect = models.EFFECT_NAMES.index("Static")
    check("switching to a narrower effect trims the list", colours.count == 1,
          str(colours.count))


def test_rainbow_and_off_use_no_colours():
    model = make_lighting()
    for name in ("Rainbow", "Off"):
        model.effect = models.EFFECT_NAMES.index(name)
        check(f"{name} uses no colours", model.colours.allowed == 0,
              str(model.colours.allowed))


def test_colours_cross_the_boundary_as_hex():
    model = make_lighting()
    model.effect = models.EFFECT_NAMES.index("Breathing")
    model.colours.setColour(0, "#ff8000")
    check("a colour is stored as a tuple",
          model.colours.colours()[0] == (255, 128, 0),
          str(model.colours.colours()[0]))
    check("and read back as hex", role(model.colours, 0, b"colour") == "#ff8000",
          str(role(model.colours, 0, b"colour")))
    check("hex round-trips", models.to_hex(models.from_hex("#0074ff")) == "#0074ff")


def test_lighting_write_and_confirm():
    model = make_lighting()
    model.effect = models.EFFECT_NAMES.index("Static")
    seen = []
    model.writeRequested.connect(lambda *args: seen.append(args))
    model.write(True)
    check("lighting write emits a request", len(seen) == 1)
    check("it asks to save", seen[0][2] is True)
    model.confirmWritten(True)
    check("confirming clears lighting dirty", not model.dirty)


# -- games -----------------------------------------------------------------

GAMES = [
    {"id": 1, "enGameName": "Forza Horizon 6", "modDownLoadUrl": "x",
     "modName": "ForzaDualSense.exe", "processGameNames": ["forza.exe"]},
    # DualSense-aware and nothing else: 15 of the 94 entries are like this.
    # There is no route to take for it -- DualSense mode is a switch for the
    # whole system -- but the flag is still shown.
    {"id": 2, "enGameName": "Deathloop", "isPS5": True},
    {"id": 3, "enGameName": "Silksong", "isVibration": True},
    # A mod *and* a preset, like Fallout 4 -- the one remaining case that makes
    # the route a choice rather than a label. Eight more used to qualify by
    # pairing isPS5 with something else; that is not a route any more.
    # DualSense-aware as well, like the six MapMode games: the flag is not the
    # row's headline, so it is the one case where a badge says something new.
    {"id": 4, "enGameName": "Two Ways", "isVibration": True, "isPS5": True,
     "modDownLoadUrl": "x", "modName": "Fallout 4",
     "processGameNames": ["fallout4.exe"]},
]


def make_games():
    """Always against a throwaway preferences file.

    The model's default is the real ~/.config/flydigi/games.json, so a test
    that toggled anything would rewrite the preferences of whoever ran it.
    """
    settings = prefs.Prefs(os.path.join(tempfile.mkdtemp(), "games.json"))
    source = models.GameListModel(settings=settings)
    source.setGames(GAMES)
    return source, models.GameFilterModel(source)


def test_game_list_and_filters():
    source, view = make_games()
    check("every game is listed", view.count == 4, str(view.count))

    view.search = "death"
    check("search filters", view.count == 1, str(view.count))
    check("search finds the right game", role(view, 0, b"name") == "Deathloop",
          str(role(view, 0, b"name")))

    view.search = ""
    view.route = "vibration"
    # One: the two-route game starts on its tier, which is the mod.
    check("route filter works", view.count == 1, str(view.count))
    check("route filter picks the pad-side game",
          role(view, 0, b"name") == "Silksong", str(role(view, 0, b"name")))
    check("the filtered row maps back to its entry",
          view.game(0)["enGameName"] == "Silksong")

    view.route = models.ALL_ROUTES
    check("clearing the filter restores the list", view.count == 4,
          str(view.count))


def test_only_the_pad_side_route_can_be_applied():
    source, view = make_games()
    routes = {role(view, row, b"name"): role(view, row, b"canApply")
              for row in range(view.count)}
    check("the vibration game can be applied", routes["Silksong"] is True,
          str(routes))
    check("a helper-route game cannot", routes["Deathloop"] is False, str(routes))


def test_route_wording_does_not_oversell_the_preset():
    source, view = make_games()
    view.route = "vibration"
    detail = role(view, 0, b"detail")
    check("the vibration route is described as a preset", "preset" in detail,
          detail)
    check("and says nothing runs alongside", "nothing runs" in detail, detail)

    view.route = "telemetry"
    check("a helper route names what to start",
          "start it alongside" in role(view, 0, b"detail"),
          str(role(view, 0, b"detail")))


def test_a_dualsense_game_is_marked_rather_than_offered_a_route():
    source, view = make_games()
    marked = {role(view, row, b"name"): role(view, row, b"ds5Mark")
              for row in range(view.count)}
    # A badge has to say something the row does not already say. "Two Ways"
    # pairs the flag with a mod, so its label is about the mod and the badge is
    # the only mention; "Deathloop" has nothing else, so its own label spells
    # DualSense mode out and a badge beside it would be decoration.
    check("a game whose label is about something else is badged",
          marked["Two Ways"] is True, str(marked))
    check("a game whose label already says it is not",
          marked["Deathloop"] is False, str(marked))
    check("and a game without the flag never is", marked["Silksong"] is False,
          str(marked))

    row = row_for(view, "Deathloop")
    check("it offers no route to choose",
          len(role(view, row, b"routeChoices")) == 1,
          str(role(view, row, b"routeChoices")))
    check("and none of them is the old ps5 route",
          role(view, row, b"route") != "ps5", str(role(view, row, b"route")))
    detail = role(view, row, b"detail")
    # "No trigger support" would be the wrong sentence for a game that works
    # perfectly well once DualSense mode is on.
    check("the row explains DualSense mode instead of denying support",
          "DualSense mode on" in detail, detail)
    check("and says there is nothing per-game to do",
          "per game" in detail, detail)
    check("its one-line label does not deny support either",
          "No trigger support" not in role(view, row, b"routeLabel"),
          str(role(view, row, b"routeLabel")))

    # Auto mode acts per route, and this game has none for it to take -- so
    # there is nothing to offer, and offering it anyway would be a switch that
    # does nothing whichever way it is set.
    check("it is not offered auto mode", role(view, row, b"canAuto") is False)
    check("and games with a route still are",
          role(view, row_for(view, "Silksong"), b"canAuto") is True)
    view.setAutoAt(row, True)
    check("asking anyway is refused rather than stored",
          role(view, row, b"auto") is False, str(role(view, row, b"auto")))


# -- per-game auto mode ----------------------------------------------------

def row_for(view, name):
    for row in range(view.count):
        if role(view, row, b"name") == name:
            return row
    return -1


def test_auto_defaults_follow_the_route():
    source, view = make_games()
    auto = {role(view, row, b"name"): role(view, row, b"auto")
            for row in range(view.count)}
    check("the pad-side route acts by default", auto["Silksong"] is True,
          str(auto))
    check("a game with no route to take does not",
          auto["Deathloop"] is False, str(auto))
    check("nor does one that runs a helper", auto["Forza Horizon 6"] is False,
          str(auto))


def test_toggling_auto_is_saved_and_announced():
    source, view = make_games()
    row = row_for(view, "Silksong")
    seen = []
    source.dataChanged.connect(lambda *args: seen.append(args))

    view.setAutoAt(row, False)
    check("the toggle reaches the model", role(view, row, b"auto") is False)
    check("the view is told the row changed", len(seen) == 1, str(len(seen)))

    # The file is what the daemon reads, so it is the thing that must change.
    reloaded = prefs.Prefs(source.prefs().path)
    check("the choice is on disk", reloaded.auto(GAMES[2]) is False)
    check("and reads as deliberate", reloaded.is_explicit(GAMES[2]))


def test_only_multi_route_games_offer_a_choice():
    source, view = make_games()
    choices = {role(view, row, b"name"): role(view, row, b"routeChoices")
               for row in range(view.count)}
    check("a single-route game offers one", len(choices["Silksong"]) == 1,
          str(choices["Silksong"]))
    check("a two-route game offers two", len(choices["Two Ways"]) == 2,
          str(choices["Two Ways"]))
    check("the choices are readable names",
          "Pad preset" in choices["Two Ways"], str(choices["Two Ways"]))


def test_choosing_a_route_changes_what_the_row_says():
    source, view = make_games()
    row = row_for(view, "Two Ways")
    check("it starts on its tier", role(view, row, b"route") == "bespoke",
          str(role(view, row, b"route")))
    check("so it cannot be applied from here",
          role(view, row, b"canApply") is False)
    check("and the chosen index points at it",
          role(view, row, b"chosenRouteIndex") == 0)
    check("the mod route does not act by default",
          role(view, row, b"auto") is False)

    view.setRouteIndexAt(row, 1)
    check("choosing the other route takes effect",
          role(view, row, b"route") == "vibration",
          str(role(view, row, b"route")))
    check("the row starts offering to load a preset",
          role(view, row, b"canApply") is True)
    check("its description follows the choice",
          "preset" in role(view, row, b"detail"),
          str(role(view, row, b"detail")))
    # Picking a route from the dropdown says how this game is handled, not that
    # it should be handled unasked. It used to grant auto mode as a side effect,
    # because the default followed the chosen route alone.
    check("but choosing a route does not switch auto on",
          role(view, row, b"auto") is False, str(role(view, row, b"auto")))

    # A route the game does not have must not be reachable from a view.
    view.setRouteIndexAt(row, 7)
    check("an out-of-range choice is ignored",
          role(view, row, b"route") == "vibration",
          str(role(view, row, b"route")))


def test_the_route_filter_follows_the_chosen_route():
    source, view = make_games()
    view.route = "vibration"
    check("the two-route game is not there under its tier",
          row_for(view, "Two Ways") < 0, str(view.count))

    view.route = models.ALL_ROUTES
    view.setRouteIndexAt(row_for(view, "Two Ways"), 1)
    view.route = "vibration"
    check("after choosing, it filters as the route it now takes",
          row_for(view, "Two Ways") >= 0, str(view.count))


def test_the_route_filter_no_longer_offers_dualsense():
    _source, view = make_games()
    # It was one of the routes here, and a stale filter value would silently
    # match nothing rather than saying it is gone.
    check("ps5 is not in the filter list", "ps5" not in view.routeNames,
          str(view.routeNames))
    check("nor is it a route name", "ps5" not in models.ROUTE_NAMES,
          str(sorted(models.ROUTE_NAMES)))


# -- DualSense mode --------------------------------------------------------
#
# Nothing here starts a relay: what is worth asserting is that the model reads
# the system rather than remembering what it did, since the relay outlives the
# app on purpose, and that a failed start says which failure it was.

class FakeProc:
    """A Popen that has already exited with `code`."""

    def __init__(self, code):
        self.returncode = code

    def poll(self):
        return self.returncode


def make_dsmode(state, start=None):
    """A model over a stand-in system.

    The model reaches the backend by attribute on the module rather than by a
    name bound at import, so replacing them here is what the model will
    actually call. `state` is kept by reference: mutate it to make the system
    change under the model, which is the whole thing being tested.
    """
    ds_backend.state = lambda: dict(state)
    ds_backend.latest_status = lambda **_: {"out": 7, "iso_urbs": 99,
                                            "reports": 5}
    ds_backend.tail = lambda **kwargs: []
    if start is not None:
        ds_backend.start = start
    return models.DsModeModel()


def test_dsmode_reads_the_system_rather_than_remembering():
    live = {"available": True, "loaded": True, "running": False, "pids": [],
            "relay": "x"}
    model = make_dsmode(live)
    try:
        check("a stopped relay reads as off", model.running is False)

        # Started by something else -- a terminal, or this app before it was
        # restarted. The switch has to notice.
        live["running"] = True
        live["pids"] = [4242]
        model.refresh()
        check("a relay started elsewhere reads as on", model.running is True)
        check("and its counters are picked up", model.outputReports == 7,
              str(model.outputReports))
        check("including the haptic ones", model.hapticUrbs == 99,
              str(model.hapticUrbs))

        live["running"] = False
        live["pids"] = []
        model.refresh()
        check("and it notices when the relay goes", model.running is False)
        check("dropping the counters with it", model.outputReports == 0,
              str(model.outputReports))
    finally:
        model.wait(100)


def test_dsmode_refuses_to_start_what_cannot_run():
    model = make_dsmode({"available": False, "loaded": False, "running": False,
                         "pids": [], "relay": "x"})
    said = []
    model.failed.connect(said.append)
    try:
        model.setRunning(True)
        check("a kernel without vhci-hcd is refused, not attempted",
              len(said) == 1, str(said))
        check("and the reason names the module",
              said and "vhci-hcd" in said[0], str(said))
        check("nothing is reported as running", model.running is False)
    finally:
        model.wait(100)


def test_dsmode_does_not_call_a_clean_stop_a_failure():
    """Pressing the switch off is not news, and it is certainly not an error.

    The relay exits 0 on SIGTERM, which is exactly what the switch sends, so
    treating any exit as a failure put a red banner and the relay's closing
    summary on screen every time DualSense mode was turned off.
    """
    live = {"available": True, "loaded": True, "running": True, "pids": [900],
            "relay": "x"}
    model = make_dsmode(live, start=lambda **_: FakeProc(0))
    errors, notes = [], []
    model.failed.connect(errors.append)
    model.note.connect(notes.append)
    try:
        model.setRunning(False)
        # The stop runs on its own thread; the relay going is what the poll
        # sees, so simulate the system it is asking about.
        live["running"] = False
        live["pids"] = []
        model._proc = FakeProc(0)
        model.refresh()
        check("a stop that was asked for is not an error", errors == [],
              str(errors))
        check("and does not need announcing either", notes == [], str(notes))
        check("the switch is off afterwards", model.running is False)
    finally:
        model.wait(2000)


def test_dsmode_says_so_when_the_relay_goes_by_itself():
    live = {"available": True, "loaded": True, "running": True, "pids": [900],
            "relay": "x"}
    model = make_dsmode(live)
    errors, notes = [], []
    model.failed.connect(errors.append)
    model.note.connect(notes.append)
    try:
        # Nobody asked -- Ctrl-C in a terminal, or someone else's pkill. Still
        # a clean exit, so still not an error, but the switch moving on its own
        # needs a word or it looks like a glitch.
        live["running"] = False
        live["pids"] = []
        model._proc = FakeProc(0)
        model.refresh()
        check("an unasked-for clean exit is not an error", errors == [],
              str(errors))
        check("but it is announced", len(notes) == 1, str(notes))
    finally:
        model.wait(2000)


def test_dsmode_reports_a_cancelled_authentication():
    live = {"available": True, "loaded": True, "running": False, "pids": [],
            "relay": "x"}
    # pkexec exits 126 when the dialog is dismissed or the password refused.
    model = make_dsmode(live, start=lambda **_: FakeProc(126))
    said = []
    model.failed.connect(said.append)
    try:
        model.setRunning(True)
        model.refresh()
        check("a dismissed dialog is reported", len(said) == 1, str(said))
        check("in words rather than an exit code",
              said and "cancelled" in said[0], str(said))
        check("and the switch goes back", model.running is False)
        check("and stops claiming to be busy", model.busy is False)
    finally:
        model.wait(100)


# -- setup -----------------------------------------------------------------

def make_setup(*checks):
    model = models.SetupModel()
    model._checks.setChecks(checks)
    model._loaded = True
    return model


def ok(check_id):
    return system_setup.Check(check_id, check_id, system_setup.OK, "", None)


def failing(check_id):
    return system_setup.Check(check_id, check_id, system_setup.FAIL, "", "fix")


def test_setup_reports_ready_only_when_nothing_fails():
    model = make_setup(ok("hidraw"), ok("uhid"), ok("input"), ok("rules"),
                       ok("unit"))
    check("all green reads as ready", model.ready)

    model = make_setup(ok("hidraw"), failing("unit"))
    check("a failure is not ready", not model.ready)

    # A skipped check is a state, not a fault: an unplugged pad and a rules
    # file nobody needs both report as skipped.
    model = make_setup(system_setup.Check("hidraw", "", system_setup.SKIP,
                                          "", None))
    check("a skipped check does not block ready", model.ready)


def test_setup_asks_for_root_only_when_something_needs_it():
    model = make_setup(ok("hidraw"), ok("uhid"), ok("input"), ok("rules"))
    check("nothing broken means no password prompt", not model.rulesNeeded)

    model = make_setup(failing("hidraw"))
    check("an unreachable device asks for the rules", model.rulesNeeded)


def test_setup_keeps_running_and_starting_at_login_apart():
    model = make_setup(ok("unit"), ok("running"))
    check("running is read from its own check", model.running)
    check("start-at-login is not implied by running", not model.startAtLogin)

    model = make_setup(ok("unit"), ok("enabled"))
    check("start-at-login is read from its own check", model.startAtLogin)
    check("and does not imply it is running now", not model.running)


# -- device ----------------------------------------------------------------

def test_device_folds_in_an_info_reply():
    device = models.DeviceModel()
    check("nothing is connected at rest", not device.connected)
    check("and the summary says so", "Looking for" in device.summary,
          device.summary)

    device.infoReceived({"battery_level": 5, "charging": False,
                         "connect_type": "dongle"})
    check("an info reply marks it connected", device.connected)
    check("battery is reported in steps", device.battery == 5, str(device.battery))
    check("battery steps are five -- 5 is a full pad", device.batterySteps == 5)
    check("connection type is reported", device.connectionType == "dongle")
    check("the summary names the connection", "dongle" in device.summary,
          device.summary)


def test_the_third_party_gate_follows_firmware():
    """Space Station hides this below 7.0.3.0 on a k5, so we do too."""
    device = models.DeviceModel()
    check("hidden before anything is known", not device.thirdPartyAvailable)

    device.versionsReceived({"main": "7.0.2.9"})
    check("hidden below the minimum", not device.thirdPartyAvailable)

    device.versionsReceived({"main": "7.0.4.5"})
    check("offered at or above it", device.thirdPartyAvailable)
    check("and the version is reported", device.firmware == "7.0.4.5")

    # The case Flydigi's own gate gets wrong. Their CompareVersion is an ordinal
    # string compare, so "7.0.10.0" sorts below "7.0.3.0" and the feature would
    # be hidden on firmware newer than the one that introduced it.
    device.versionsReceived({"main": "7.0.10.0"})
    check("a double-digit component still counts as newer",
          device.thirdPartyAvailable)
    check("which is where we differ from their string compare",
          "7.0.10.0" < "7.0.3.0")


def test_the_holder_is_reported_separately_from_the_switch():
    """"Allowed" and "actually taken" are different, and the switch shows one."""
    device = models.DeviceModel()
    device.transportReceived({"third_party": False, "control_by": ""})
    check("nobody holds it to begin with", device.controlBy == "")
    check("and it is not allowed", not device.thirdParty)

    device.transportReceived({"third_party": True, "control_by": "SDL"})
    check("the flag is reported", device.thirdParty)
    check("and so is who took it up", device.controlBy == "SDL",
          device.controlBy)


def test_flipping_the_switch_asks_the_worker():
    device = models.DeviceModel()
    asked = []
    device.thirdPartyRequested.connect(asked.append)

    device.thirdParty = True
    check("the request reaches the worker", asked == [True], str(asked))
    check("and the switch moves at once", device.thirdParty)

    # The pad has the last word: the acquirer reconfigures things, so what comes
    # back is not necessarily what was asked for.
    device.transportReceived({"third_party": False, "control_by": ""})
    check("a contrary read wins", not device.thirdParty)


def test_device_reports_a_failure():
    device = models.DeviceModel()
    device.infoReceived({"battery_level": 3, "charging": True,
                         "connect_type": "wired"})
    check("charging is reported", device.charging)
    device.failed("no reply -- press a button to wake the pad")
    check("a failure clears connectedness", not device.connected)
    check("a failure is reported", "wake the pad" in device.error, device.error)


def test_battery_is_clamped():
    device = models.DeviceModel()
    device.infoReceived({"battery_level": 99, "connect_type": "wired"})
    check("an impossible battery level is clamped",
          device.battery == device.batterySteps, str(device.battery))


# -- the extraction itself -------------------------------------------------

def test_models_pull_in_no_view_code():
    """The check that the extraction is real, not just relocated."""
    leaked = sorted(name for name in sys.modules
                    if name.startswith("PySide6.QtWidgets")
                    or name.startswith("PySide6.QtQuick"))
    check("models import no view toolkit", not leaked, str(leaked))



# -- screen ---------------------------------------------------------------


def screen_model_with(frames=1):
    """A ScreenModel holding a picture, loaded from a real file.

    Through the file reader rather than a back door, because reading files is
    most of what the model does and a test that skipped it would leave the only
    interesting part uncovered.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage

    from gui.models import ScreenModel

    model = ScreenModel()
    if frames > 1:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "qml", "four-frames.gif")
    else:
        path = os.path.join(tempfile.mkdtemp(), "still.png")
        image = QImage(90, 70, QImage.Format_RGB888)
        image.fill(0x804020)
        # Not a flat colour: a solid image survives crop, letterbox and squash
        # identically, so it cannot tell the three fit modes apart. Taller than
        # the 2:1 panel, with something off-centre to crop away.
        from PySide6.QtGui import QColor, QPainter
        painter = QPainter(image)
        painter.fillRect(0, 0, 90, 18, QColor("white"))
        painter.fillRect(10, 40, 30, 25, QColor("blue"))
        painter.end()
        image.save(path, "PNG")
    loaded = model.open(QUrl.fromLocalFile(path))
    return model, loaded


def test_a_picture_is_encoded_as_it_is_loaded():
    """The frames exist before the button is pressed, not after.

    That is what lets the page state a frame count and a time estimate up
    front, and an upload is far too long to start before knowing either.
    """
    from flydigi import screen

    model, loaded = screen_model_with(1)
    check("the still loaded", loaded)
    check("one frame", model.frameCount == 1, model.frameCount)
    check("not an animation", not model.animated)
    check("an estimate is offered", bool(model.estimate), model.estimate)
    check("a preview was written", model.previewSource.startswith("file:"),
          model.previewSource)

    model, loaded = screen_model_with(4)
    check("the animation loaded", loaded)
    check("every frame was taken", model.frameCount == 4, model.frameCount)
    check("it knows it moves", model.animated)
    # The GIF states 120 ms; a still has none and falls back to 100.
    check("the interval came from the file", model.interval == 120, model.interval)


def test_the_frames_handed_over_are_ones_the_pad_would_accept():
    """A malformed frame would only surface minutes into an upload."""
    from flydigi import screen

    model, _ = screen_model_with(4)
    sent = {}
    model.uploadRequested.connect(
        lambda frames, interval, restore: sent.update(
            frames=frames, interval=interval, restore=restore))
    model.upload()

    check("every frame went", len(sent.get("frames", [])) == 4)
    check("each is a whole frame",
          all(len(f) == screen.FRAME_LEN for f in sent["frames"]))
    check("each carries the header",
          all(f[:4] == screen.frame_header() for f in sent["frames"]))
    check("the interval went with them", sent["interval"] == 120)
    check("and it is not a factory restore", sent["restore"] is False)


def test_an_upload_in_flight_locks_everything_that_would_disturb_it():
    """Covered here rather than in QML on purpose.

    In the QML suite `upload()` reaches the real worker, which switches the pad
    into upgrade mode and then waits half a minute for a serial device -- which
    stalls the worker thread and fails every case after it. No worker is
    attached here, so the signal goes nowhere and the state machine is all that
    is under test.
    """
    model, _ = screen_model_with(1)
    check("ready before", model.canUpload)
    model.upload()
    check("busy after", model.busy)
    check("and cannot be started again", not model.canUpload)
    check("the progress line says something", bool(model.progressText))

    model.progressReceived(50, 200)
    check("progress is a fraction", abs(model.progress - 0.25) < 1e-6, model.progress)
    check("and is spelled out too", "50 of 200" in model.progressText,
          model.progressText)

    model.uploadFinished(True)
    check("released afterwards", not model.busy)
    check("and ready to send again", model.canUpload)


def test_changing_the_fit_changes_the_pixels():
    """Fill crops, fit letterboxes, stretch squashes -- three different images.

    The source is taller than the 2:1 panel, so all three have to give something
    different up. A preview that showed the same bytes for each would be lying
    about what the pad is going to hold.
    """
    from flydigi import screen

    model, _ = screen_model_with(1)
    encoded = {}
    model.uploadRequested.connect(
        lambda frames, _i, _r: encoded.__setitem__(model.fitMode, frames[0]))
    for index in range(3):
        model.fitMode = index
        check(f"fit mode {index} took", model.fitMode == index)
        model.upload()
        model.uploadFinished(True)

    check("three fits, three different images", len(set(encoded.values())) == 3,
          str(sorted(len(v) for v in encoded.values())))
    # Letterboxing a tall picture into a wide panel leaves the corners black.
    _w, _h, rgb = screen.decode_frame(encoded[1])
    check("fit leaves a black corner", rgb[:3] == b"\x00\x00\x00", rgb[:3].hex())
    _w, _h, filled = screen.decode_frame(encoded[0])
    check("fill does not", filled[:3] != b"\x00\x00\x00", filled[:3].hex())


def test_every_frame_gets_a_preview_so_the_page_can_play_it():
    """A still frame of an animation says almost nothing about it.

    And the upload is far too long to be how you find out what you chose, so
    the frames are written once at load and the page cycles them.
    """
    from PySide6.QtCore import QUrl

    model, _ = screen_model_with(4)
    check("one preview per frame", len(model.previewFrames) == 4,
          str(len(model.previewFrames)))
    check("all distinct files", len(set(model.previewFrames)) == 4)
    check("previewSource is the first of them",
          model.previewSource == model.previewFrames[0])
    check("they are file URLs that exist",
          all(os.path.exists(QUrl(u).toLocalFile()) for u in model.previewFrames))

    # A second picture must not show the first one's pixels: Qt caches by URL,
    # so the names carry a serial rather than the paths being reused.
    old = list(model.previewFrames)
    model.fitMode = 2
    check("a re-encode renames them", set(model.previewFrames).isdisjoint(old),
          str(model.previewFrames[:1]))
    check("and cleans the old ones up",
          not any(os.path.exists(QUrl(u).toLocalFile()) for u in old))

    model.clear()
    check("clearing drops them all", model.previewFrames == [])


def test_the_screen_state_is_read_rather_than_assumed():
    """The bits arrive from command 3, under the names they were measured with.

    `always_on` is the SDK's `OffScreen`, and it is not a screen-off switch --
    see flydigi/screen.py. Named for the behaviour so a true value means a lit
    screen.
    """
    from gui.models import ScreenModel

    model = ScreenModel()
    check("nothing is claimed before a read", not model.loaded)
    model.statusReceived({"always_on": True, "status_bar_always_on": False,
                          "always_on_usable": True})
    check("loaded", model.loaded)
    check("the display is up", model.alwaysOn)
    check("the status bar is not pinned", not model.statusBarAlwaysOn)
    check("and it is supported", model.supported)


def test_the_two_switches_are_different_sub_commands():
    from flydigi import screen

    from gui.models import ScreenModel

    model = ScreenModel()
    asked = []
    model.settingRequested.connect(lambda sub, value: asked.append((sub, value)))
    model.setAlwaysOn(True)
    model.setStatusBarAlwaysOn(False)
    check("the display is sub-id 9",
          asked[0] == (screen.SUB_OFF_SCREEN, True), str(asked))
    check("the status bar is sub-id 8",
          asked[1] == (screen.SUB_STATUS_BAR, False), str(asked))


def test_the_settings_block_fills_the_page():
    """The thirteen bytes an Apex 5 answered, all the way to the properties.

    Parsed rather than hand-written as a dict, so this covers the decode and
    the model together -- a bit position moving would show up here as a switch
    in the wrong place.
    """
    from flydigi import settings as backend

    from gui.models import SettingsModel

    model = SettingsModel()
    check("nothing is claimed before a read", not model.loaded)
    check("and nothing is offered either", not model.quickSwitchUsable)

    model.stateReceived(backend.parse_status(
        bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])))
    check("loaded", model.loaded)
    check("quick switch was on", model.quickSwitch)
    check("and it is offered", model.quickSwitchUsable)
    check("sleep is 15 minutes", model.sleepMinutes == 15, str(model.sleepMinutes))
    check("which reads as words", model.sleepText == "15 min", model.sleepText)
    check("the report rate says what the pad answered",
          model.reportRateText == "default (0)", model.reportRateText)


def test_the_pickers_index_by_resolution_and_write_by_wire_value():
    """The trap in `JoystickPrecision`, seen from the UI side.

    The enum is in declaration order -- 8, 10, 12, 9, 11, 14, 16 -- so a picker
    sorted the way a person expects disagrees with the wire from 9-bit on. What
    goes out has to be the wire value.
    """
    from flydigi import settings as backend

    from gui.models import SettingsModel

    model = SettingsModel()
    model.stateReceived(backend.parse_status(
        bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])))
    check("the picker is sorted by resolution",
          model.precisionNames == ["8-bit", "9-bit", "10-bit", "11-bit",
                                   "12-bit", "14-bit", "16-bit"],
          str(model.precisionNames))
    check("wire 2 shows as 10-bit", model.precisionNames[model.precision] == "10-bit")

    asked = []
    model.writeRequested.connect(lambda name, value: asked.append((name, value)))
    model.precision = 4                       # the row for 12-bit
    check("12-bit goes out as wire value 3", asked[-1] == ("precision", 3), str(asked))

    check("seven sensitivities, most first",
          model.sensitivityNames[0] == "Highest"
          and model.sensitivityNames[-1] == "Lowest"
          and len(model.sensitivityNames) == 7,
          str(model.sensitivityNames))
    model.sensitivity = 0
    check("the most sensitive is wire 14", asked[-1] == ("sensitivity", 14), str(asked))


def test_a_switch_asks_the_worker_and_moves_at_once():
    """Optimistic, then corrected. The pad takes about a second to answer, and a
    switch that snapped back for that long would read as a failed click."""
    from flydigi import settings as backend

    from gui.models import SettingsModel

    model = SettingsModel()
    model.stateReceived(backend.parse_status(
        bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])))
    asked = []
    model.writeRequested.connect(lambda name, value: asked.append((name, value)))

    model.quickSwitch = False
    check("the worker was asked", asked == [("quick_switch", 0)], str(asked))
    check("and the switch moved", not model.quickSwitch)

    # And the read-back is what it settles on, even when the pad disagrees.
    model.stateReceived(backend.parse_status(
        bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])))
    check("the pad has the last word", model.quickSwitch)

    model.sleepMinutes = 999
    check("sleep is clamped to what the pad takes",
          asked[-1] == ("sleep_minutes", backend.SLEEP_MAX_MINUTES), str(asked))


def test_auto_calibration_is_unavailable_without_debounce():
    """Flydigi's own string for the debounce toggle says so; not our rule."""
    from flydigi import settings as backend

    from gui.models import SettingsModel

    model = SettingsModel()
    model.stateReceived(backend.parse_status(
        bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])))
    check("reachable while debounce is on", model.autoCalibrationUsable)
    model.stickDebounce = False
    check("and not once it is off", not model.autoCalibrationUsable)


def test_an_unsupported_feature_is_reported_as_such_not_as_off():
    """The pad acknowledges settings it does not have, so the sentence after a
    write has to come from the read-back rather than from the request."""
    from flydigi import settings as backend
    from gui.models.settings import describe_setting

    state = backend.parse_status(
        bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17]))
    check("an unsupported one says so",
          describe_setting("motion_debounce", state)
          == "Motion debounce: not supported on this pad",
          describe_setting("motion_debounce", state))
    check("a supported one says on or off",
          describe_setting("quick_switch", state) == "Quick-switch config: on",
          describe_setting("quick_switch", state))
    check("a number says the number",
          describe_setting("sleep_minutes", state) == "Sleep: 15 min",
          describe_setting("sleep_minutes", state))
    check("and precision says the depth, not the index",
          describe_setting("precision", state) == "Stick precision: 10-bit",
          describe_setting("precision", state))


def main():
    QCoreApplication.instance() or QCoreApplication([])
    for test in (test_selecting_an_unread_profile_requests_it_once,
                 test_a_loaded_profile_is_clean,
                 test_editing_a_key_marks_dirty_and_lands_in_the_blob,
                 test_a_recording_becomes_a_macro_row,
                 test_a_recording_that_caught_nothing_says_why,
                 test_a_macros_type_and_interval_write_through,
                 test_deleting_a_macro_gives_the_key_back,
                 test_the_page_stops_offering_more_than_the_pad_holds,
                 test_turbo_survives_a_target_change,
                 test_write_carries_the_blob_and_the_previous_copy,
                 test_confirming_a_write_clears_dirty,
                 test_applying_without_saving_still_offers_a_save,
                 test_saving_a_profile_the_pad_is_not_running_is_refused,
                 test_reselecting_the_open_profile_keeps_unsaved_edits,
                 test_reloading_still_re_reads_the_open_profile,
                 test_selecting_an_unread_profile_clears_the_title,
                 test_lighting_applying_without_saving_still_offers_a_save,
                 test_reset_all_clears_every_remap,
                 test_rename_reaches_the_config,
                 test_a_title_is_capped_at_what_the_pad_stores,
                 test_vibration_writes_through_to_the_blob,
                 test_vibration_keeps_min_below_max,
                 test_trigger_fields_are_independent,
                 test_each_effect_offers_its_own_controls,
                 test_an_effect_remembers_its_numbers_across_a_switch,
                 test_an_unknown_knob_is_refused_rather_than_stored,
                 test_no_trigger_motor_controls_are_offered,
                 test_a_stick_edit_recompiles_the_bank,
                 test_a_stick_edit_moves_the_curve_to_custom,
                 test_a_stick_bound_to_a_key_is_not_offered_a_curve,
                 test_circularity_is_not_part_of_the_curve,
                 test_restore_refuses_a_wrong_sized_file,
                 test_backup_then_restore_round_trips,
                 test_slot_list_tracks_active_and_current,
                 test_dirty_is_reported_on_the_slot_row,
                 test_lighting_loads_clean,
                 test_choosing_an_effect_rewrites_the_frames,
                 test_brightness_and_speed_write_through,
                 test_the_vibration_light_effect_is_byte_nine,
                 test_colour_list_respects_what_the_effect_allows,
                 test_rainbow_and_off_use_no_colours,
                 test_colours_cross_the_boundary_as_hex,
                 test_lighting_write_and_confirm,
                 test_game_list_and_filters,
                 test_only_the_pad_side_route_can_be_applied,
                 test_route_wording_does_not_oversell_the_preset,
                 test_a_dualsense_game_is_marked_rather_than_offered_a_route,
                 test_auto_defaults_follow_the_route,
                 test_toggling_auto_is_saved_and_announced,
                 test_only_multi_route_games_offer_a_choice,
                 test_choosing_a_route_changes_what_the_row_says,
                 test_the_route_filter_follows_the_chosen_route,
                 test_the_route_filter_no_longer_offers_dualsense,
                 test_dsmode_reads_the_system_rather_than_remembering,
                 test_dsmode_refuses_to_start_what_cannot_run,
                 test_dsmode_does_not_call_a_clean_stop_a_failure,
                 test_dsmode_says_so_when_the_relay_goes_by_itself,
                 test_dsmode_reports_a_cancelled_authentication,
                 test_setup_reports_ready_only_when_nothing_fails,
                 test_setup_asks_for_root_only_when_something_needs_it,
                 test_setup_keeps_running_and_starting_at_login_apart,
                 test_device_folds_in_an_info_reply,
                 test_the_third_party_gate_follows_firmware,
                 test_the_holder_is_reported_separately_from_the_switch,
                 test_flipping_the_switch_asks_the_worker,
                 test_device_reports_a_failure,
                 test_battery_is_clamped,
                 test_a_picture_is_encoded_as_it_is_loaded,
                 test_the_frames_handed_over_are_ones_the_pad_would_accept,
                 test_an_upload_in_flight_locks_everything_that_would_disturb_it,
                 test_changing_the_fit_changes_the_pixels,
                 test_every_frame_gets_a_preview_so_the_page_can_play_it,
                 test_the_screen_state_is_read_rather_than_assumed,
                 test_the_two_switches_are_different_sub_commands,
                 test_the_settings_block_fills_the_page,
                 test_the_pickers_index_by_resolution_and_write_by_wire_value,
                 test_a_switch_asks_the_worker_and_moves_at_once,
                 test_auto_calibration_is_unavailable_without_debounce,
                 test_an_unsupported_feature_is_reported_as_such_not_as_off,
                 test_models_pull_in_no_view_code):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
