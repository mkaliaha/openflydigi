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
    from PySide6.QtCore import (QCoreApplication, QEventLoop, QModelIndex,
                                QTimer)
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


def test_revisiting_a_profile_re_reads_it_because_the_read_is_the_switch():
    """A cache in front of the picker is a pad running the wrong profile.

    Reading a config is what makes the pad play it, so serving a profile from
    memory shows a page the pad is not running. Space Station caches here and
    that is the bug: switch to a profile it has already read and the pad stays
    where it was. Anyone tempted to optimise the re-read away should move the
    switch onto a command of its own first.
    """
    profile, requested = make_profile()
    profile.select(1)
    profile.profileLoaded(1, bytes(blank_blob("Profile 2")), "Profile 2")
    profile.select(0)
    check("revisiting a profile already in the cache still reads it",
          requested == [0, 1, 0], str(requested))


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
    for name in ("m1", "m2", "m3", "m4", "m5"):
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


def test_the_key_table_follows_the_pad_it_is_editing():
    """An Apex 5 has no C and no Z; every Vader declares both.

    Everything that walks "every key" -- the remap table, the gyro's enable-key
    picker, the macro binder, reset-all -- has to walk *that pad's* keys. The
    one that bites is reset-all: iterating a hardcoded list would leave two
    buttons remapped on a Vader and report success, which is the shape of bug
    nobody reports because the app says it worked.

    The properties behind those views used to be `constant`, which QML caches
    for the life of the window, so this also asserts they now move.
    """
    profile = models.ProfileModel()
    profile.setSlotCount(4)
    profile.select(0)
    profile.profileLoaded(0, bytes(blank_blob()), "Profile 1")

    check("it starts on the pad this project drives", profile.modelCode == "k5")
    apex_rows = profile.keys.count
    check("and shows that pad's keys", apex_rows == len(mapping.APEX5_KEYS),
          str(apex_rows))
    check("with no C or Z among them",
          not {"c", "z"} & set(profile.padKeys), str(profile.padKeys))

    moved = []
    profile.keysChanged.connect(lambda: moved.append(True))
    profile.modelCode = "f5"

    check("switching model is announced", moved == [True], str(moved))
    check("the remap table gains the two extra buttons",
          profile.keys.count == apex_rows + 2, str(profile.keys.count))
    check("the gyro's enable-key picker gains them too",
          len(profile.motion.keyNames) == apex_rows + 3,   # +1 for "(none)"
          str(len(profile.motion.keyNames)))
    check("and so does the macro binder",
          len(profile.macros.triggerKeys) == apex_rows + 2,
          str(len(profile.macros.triggerKeys)))

    # The bug this is really for.
    seeded = mapping.MappingConfig(blank_blob())
    seeded.set_mapping("c", "a")
    seeded.set_mapping("m3", "x")
    profile.profileLoaded(0, bytes(seeded.blob), "Profile 1")
    check("a Vader-only key can be remapped at all",
          profile.config.remapped(profile.padKeys).get("c") == ("a", 0, 0),
          str(profile.config.remapped(profile.padKeys)))
    profile.resetAll()
    check("and reset-all reaches it rather than reporting success without it",
          profile.config.remapped(profile.padKeys) == {},
          str(profile.config.remapped(profile.padKeys)))
    # The default really is Apex-5-only, which is why the call above passes the
    # pad's keys and why `remapped` says so. Asserted rather than assumed: it
    # is the difference between a UI that marks C and one that never mentions it.
    seeded_again = mapping.MappingConfig(blank_blob())
    seeded_again.set_mapping("c", "a")
    check("and the unqualified call cannot see it",
          seeded_again.remapped() == {}, str(seeded_again.remapped()))

    # Back again, because a two-pad desk switches both ways.
    profile.modelCode = "k5"
    check("choosing the Apex 5 again drops the extra keys",
          profile.keys.count == apex_rows, str(profile.keys.count))

    same = []
    profile.keysChanged.connect(lambda: same.append(True))
    profile.modelCode = "k5"
    check("and re-selecting the same model rebuilds nothing", not same)


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


def effect_rows(model):
    """The knob rows a Repeater would draw, read the way a delegate reads them.

    `effectParams` is a list model rather than a list -- see `EffectParamsModel`
    for what a list did to the knobs -- so anything that wants to look at every
    row has to go through the roles.
    """
    roles = {"key": model.KeyRole, "label": model.LabelRole,
             "description": model.DescriptionRole,
             "minimum": model.MinimumRole, "maximum": model.MaximumRole,
             "kind": model.KindRole, "value": model.ValueRole}
    return [{name: model.data(model.index(row, 0), role)
             for name, role in roles.items()}
            for row in range(model.rowCount())]


def test_each_effect_offers_its_own_controls():
    """The knobs are not the same from one effect to the next, so the page
    asks the model what to draw rather than drawing a fixed pair."""
    profile, _ = make_profile()
    right = profile.triggers.side("right")

    check("all six effects are offered", len(profile.triggers.effectNames) == 6,
          str(profile.triggers.effectNames))
    check("General offers no controls at all",
          right.effect == 0 and effect_rows(right.effectParams) == [],
          str(effect_rows(right.effectParams)))

    for index, (label, mode) in enumerate(models.TRIGGER_MODES):
        right.effect = index
        rows = effect_rows(right.effectParams)
        keys = [row["key"] for row in rows]
        check(f"{label}: the controls are the effect's own",
              keys == [p.key for p in effects.effect(mode).params], str(keys))
        check(f"{label}: every control is inside its own range",
              all(row["minimum"] <= row["value"] <= row["maximum"]
                  for row in rows), str(rows))
        check(f"{label}: a switch is drawn as one",
              all(row["kind"] in ("number", "switch") for row in rows))


def test_an_effect_remembers_its_numbers_across_a_switch():
    """All six share ten byte slots, so switching effect and back must not
    silently retune the one you left."""
    profile, _ = make_profile()
    right = profile.triggers.side("right")
    right.effect = 1                                    # racing
    right.setEffectParam("start", 77)

    right.effect = 4                                    # trigger lock
    rows = effect_rows(right.effectParams)
    check("the lock has its own position", rows[0]["key"] == "start", str(rows))
    right.effect = 1
    values = {row["key"]: row["value"] for row in effect_rows(right.effectParams)}
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


def test_the_gyro_starts_off_and_shows_the_keys_the_factory_left():
    profile, _ = make_profile()
    motion = profile.motion
    check("a factory profile has the gyro off", motion.target == 0)
    check("so the page's controls are not offered", not motion.enabled)
    check("and it is not a mouse mapping", not motion.isMouse)

    # The two bytes a fresh pad ships with. Reporting them as "(none)" would
    # hide a binding that is live the moment a target is picked.
    names = list(motion.keyNames)
    check("the first enable key is the factory's Lt",
          names[motion.key] == "Left trigger", names[motion.key])
    check("and the second is the factory's Up",
          names[motion.secondKey] == "Up", names[motion.secondKey])
    check("so something would turn it on", motion.hasKey)
    check("(none) leads the key list", names[0] == models.MOTION_NO_KEY)


def test_picking_a_stick_picks_the_motion_mode_with_it():
    """Nothing on the page sets the mode, and every profile needs one."""
    profile, _ = make_profile()
    motion = profile.motion

    motion.target = 1
    check("the left stick is Racing", motion.useMode == "Racing", motion.useMode)
    check("and the block agrees",
          profile.config.motion()["use_mode"] == mapping.MOTION_RACER)

    motion.target = 2
    check("the right stick is FPS", motion.useMode == "FPS", motion.useMode)
    check("mapping the gyro is a change", profile.dirty)


def test_the_second_enable_key_is_offered_only_where_it_would_write():
    """Under Click the format carries no change to byte 7, so nothing pretends.

    The factory ships D-pad Up in that byte and the pad acts on it, so the page
    has to account for it either way -- as an editable control under Hold, and
    as a named fact under Click.
    """
    profile, _ = make_profile()
    motion = profile.motion
    motion.target = 2
    motion.enableType = 0                       # press to toggle
    check("no second-key control in toggle mode", not motion.holdMode)
    check("but the live leftover is named",
          motion.strandedKey == "Up", motion.strandedKey)

    names = list(motion.keyNames)
    motion.key = names.index("M1")
    check("the chosen key is stored", profile.config.motion()["keys"][0] == "m1")
    check("and byte 7 is untouched, as Flydigi leaves it",
          profile.config.motion()["keys"][1] == "up",
          str(profile.config.motion()["keys"]))

    motion.enableType = 1                       # while held
    check("the control appears", motion.holdMode)
    check("and nothing is stranded once it can be edited",
          motion.strandedKey == "", motion.strandedKey)
    motion.secondKey = 0
    check("clearing the second key clears it",
          profile.config.motion()["keys"] == ("m1", None),
          str(profile.config.motion()["keys"]))
    check("and the page says something still turns it on", motion.hasKey)

    motion.key = 0
    check("with neither key set, nothing does", not motion.hasKey)


def test_the_gyro_sliders_are_the_ones_space_station_shows():
    profile, _ = make_profile()
    motion = profile.motion
    check("both run to 100", motion.maximum == mapping.MOTION_SENSITIVITY_MAX)

    motion.sensitivity = 70
    motion.deadZone = 5
    check("sensitivity round-trips", motion.sensitivity == 70)
    check("and reaches both axis bytes",
          profile.config.motion()["sensitivity_xy"] == (70, 70),
          str(profile.config.motion()["sensitivity_xy"]))
    check("the dead-zone offset round-trips", motion.deadZone == 5)


def test_a_mouse_mapping_is_named_rather_than_shown_as_off():
    """A profile brought over from Windows can hold one; this cannot honour it."""
    profile, _ = make_profile()
    profile.config.blob[mapping.OFF_MOTION] = mapping.MOTION_MOUSE
    profile.motion.refresh()
    check("the page can say so", profile.motion.isMouse)
    check("the combo falls back to Off, having nothing else to show",
          profile.motion.target == 0)

    # And picking a stick replaces it, rather than being refused underneath.
    profile.motion.target = 2
    check("choosing a stick takes", not profile.motion.isMouse)
    check("and it really is the right stick",
          profile.config.motion()["target"] == mapping.MOTION_RIGHT_STICK)


def test_the_gyro_editor_leaves_the_response_curve_alone():
    """The block is inert, so there is nothing here to offer a control for.

    Measured with `tools/gyro-map-probe --window 5`: flattened to zero output,
    with the mapping otherwise identical to a run that reached 0.97 of full
    travel, the stick still reached 1.10. Space Station cannot edit it either --
    a hardcoded `none` class on the div, a prop passed as `Smoothness` and read
    as `smoothness`, and a save path that never assigns the field -- but that is
    no longer the reason the app leaves it alone.
    """
    profile, _ = make_profile()
    for name in ("curve", "smoothness", "responseCurve"):
        check(f"no {name} on the gyro model", not hasattr(profile.motion, name))

    before = bytes(profile.config.blob)
    profile.motion.target = 2
    profile.motion.sensitivity = 80
    block = slice(mapping.OFF_MOTION_CURVE,
                  mapping.OFF_MOTION_CURVE + mapping.MOTION_CURVE_ENTRY)
    check("editing the gyro leaves the curve block alone",
          bytes(profile.config.blob)[block] == before[block],
          str(list(profile.config.blob[block])))


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


STOPS = []


def make_dsmode(state, start=None):
    """A model over a stand-in system.

    The model reaches the backend by attribute on the module rather than by a
    name bound at import, so replacing them here is what the model will
    actually call. `state` is kept by reference: mutate it to make the system
    change under the model, which is the whole thing being tested.

    **`stop` is stood in for whether a test asks for a stop or not.** The real
    one takes no pids and means every relay it can find in the process table --
    it is written that way because the relay outlives the app, so the switch
    cannot go by what this process happens to have started. Left unreplaced,
    one `setRunning(False)` down here reaches out of the test and takes down the
    virtual DualSense somebody is playing a game through. It did, and the only
    trace was a graceful `[ds5] stopping` in the relay's log.
    """
    ds_backend.state = lambda: dict(state)
    ds_backend.latest_status = lambda **_: {"out": 7, "iso_urbs": 99,
                                            "reports": 5}
    ds_backend.tail = lambda **kwargs: []
    ds_backend.stop = lambda *a, **k: (STOPS.append((a, k)), True)[1]
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
    del STOPS[:]
    try:
        model.setRunning(False)
        # The stop runs on its own thread; the relay going is what the poll
        # sees, so simulate the system it is asking about.
        live["running"] = False
        live["pids"] = []
        model._proc = FakeProc(0)
        model.refresh()
        model.wait(2000)
        check("the switch really asks the backend to stop", len(STOPS) == 1,
              str(STOPS))
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


def spin(msecs):
    """Let timers actually fire for a while.

    `processEvents` will not do: it returns as soon as the queue is empty,
    which is before a timer a few milliseconds out has come due.
    """
    loop = QEventLoop()
    QTimer.singleShot(msecs, loop.quit)
    loop.exec()


def test_dsmode_polls_only_while_it_is_asked_to():
    """The /proc scan has a lifetime now, and it does not begin on its own.

    It used to be armed from a page's `Component.onCompleted` and stopped only
    at shutdown, so one visit to the DualSense page cost a walk of the process
    table every two seconds until the app quit -- on every other page as well,
    since Kirigami keeps a replaced page alive.
    """
    live = {"available": True, "loaded": True, "running": False, "pids": [],
            "relay": "x"}
    reads = []
    model = make_dsmode(live)
    # Installed over the fixture's own stand-in, and *before* anything the count
    # is asserted about. Installing it after building the model would make the
    # first check below true whatever the constructor did.
    ds_backend.state = lambda: (reads.append(None) or dict(live))
    built = len(reads)
    edges = []
    model.pollingChanged.connect(lambda: edges.append(model.polling))
    try:
        check("a new model is not scanning /proc", model.polling is False)
        # It does take one reading as it is built, which is deliberate: the
        # window shows DS mode's state before any page has asked for it. What it
        # must not do is keep taking them.
        spin(60)
        check("and takes no further reading until it is asked",
              len(reads) == built, str(len(reads) - built))

        # Two seconds is the app's interval, not a test's patience.
        model._poll.setInterval(5)
        model.polling = True
        check("turning the poll on takes a reading at once",
              len(reads) == built + 1, str(len(reads) - built))
        spin(60)
        check("and goes on taking them", len(reads) > 1, str(len(reads)))

        model.polling = False
        settled = len(reads)
        spin(60)
        check("turning it off stops them", len(reads) == settled,
              f"{settled} then {len(reads)}")
        check("and both edges are announced", edges == [True, False],
              str(edges))

        # The QML calls refresh() and nothing else, so it has to keep meaning
        # what it meant: read now, and keep reading.
        model.refresh()
        check("refresh still arms the poll", model.polling is True)
        check("reading exactly once as it does", len(reads) == settled + 1,
              str(len(reads) - settled))
        model.polling = False
    finally:
        model.wait(100)


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


def test_an_info_reply_notifies_once_per_thing_that_moved():
    """One reply is one commit, not one invalidation wave per field.

    Assigning through the setters emitted a signal apiece, so a single reply
    invalidated every binding on this model five separate times -- and the
    change guards that make that cheap on a steady poll all pass on a cold
    start and on Reload, which is exactly when the most is on screen. The
    fields are written first and the notifications go out afterwards.
    """
    device = models.DeviceModel()
    counts = {}
    for name in ("connectedChanged", "batteryChanged", "chargingChanged",
                 "connectionTypeChanged", "errorChanged"):
        counts[name] = 0

        def bump(_n=name):
            counts[_n] += 1

        getattr(device, name).connect(bump)

    device.infoReceived({"battery_level": 5, "charging": False,
                         "connect_type": "wired"})
    check("everything that moved is announced exactly once",
          all(counts[n] == 1 for n in ("connectedChanged", "batteryChanged",
                                       "connectionTypeChanged")), str(counts))
    check("and what did not move is not announced at all",
          counts["chargingChanged"] == 0, str(counts))

    before = dict(counts)
    device.infoReceived({"battery_level": 5, "charging": False,
                         "connect_type": "wired"})
    moved = {n: counts[n] - before[n] for n in counts}
    # `summary` reads off connection type as well as connectedness, so that one
    # is nudged whatever happens; nothing else may be.
    check("an identical reply changes nothing but the summary nudge",
          all(v == 0 for n, v in moved.items() if n != "connectedChanged"),
          str(moved))


def test_a_profile_read_does_not_rebuild_macros_that_did_not_change():
    """A reset destroys and rebuilds every delegate attached to the model.

    `ProfileModel._open` refreshes the macro list on every profile read, and
    most reads leave that region byte-identical -- opening the profile the pad
    is already running, or re-reading after a write that touched only the key
    table. `DevicesModel` had the same defect against the sidebar picker.
    """
    profile, _ = make_profile()
    macros = profile.macros
    resets = []
    macros.modelAboutToBeReset.connect(lambda: resets.append(1))

    macros.refresh()
    macros.refresh()
    check("re-reading an unchanged profile rebuilds nothing", resets == [],
          str(len(resets)))


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

# --------------------------------------------------------------------------
# The device list and the dock
# --------------------------------------------------------------------------
#
# No bus here: these are handed the entries `flydigi/registry.py` would have
# produced, because what is under test is the selection -- which device the
# window shows, what survives a re-enumeration, and what gets written where.
# `tests/test_registry.py` is where the entries themselves come from hardware
# or from the mock bus.

def pad_entry(index, nickname=None, path=None, supported=True):
    return {"path": path or f"/dev/hidraw{index}", "kind": "pad", "family": 2,
            "product": "Flydigi APEX5 Wireless", "mock": False,
            "device_type": 128 if supported else 130,
            "code": "k5" if supported else "f5",
            "model": "Apex 5" if supported else "Vader 5 Pro",
            # `supported` here means "the app may write to it", which both pads
            # now are; the parameter still names the *other model* because that
            # is what every caller uses it for.
            "uid": f"{index:02x}" * 13, "mac": None, "nickname": nickname,
            "firmware": "7.0.4.5", "battery": 4, "charging": False,
            "connect_type": "wired", "supported": supported, "info": {},
            "error": None}


def dock_entry(index, nickname=None):
    return {"path": f"/dev/hidraw{index}", "kind": "dock", "family": 6,
            "product": "flydigi Flydigi CD2", "mock": False, "device_type": 0,
            "code": None, "model": "Controller Charging Dock 2 Pro",
            "uid": f"{index:02x}" * 13, "mac": None, "nickname": nickname,
            "firmware": "0.0.3.9", "battery": None, "charging": None,
            "connect_type": None, "supported": True, "info": {}, "error": None}


def devices_model():
    """A DevicesModel writing to a preferences file of its own.

    Injected rather than defaulted: this model writes the chosen pad into the
    file the daemon reads, and a test that used the real one would rewrite the
    developer's own auto-mode preferences.
    """
    settings = prefs.Prefs(os.path.join(tempfile.mkdtemp(), "games.json"))
    return models.DevicesModel(settings=settings), settings


def test_the_device_list_shows_what_each_device_is():
    model, _settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"), pad_entry(4),
                           dock_entry(6, "Shelf")])
    check("every device is listed", model.count == 3, str(model.count))
    check("pads and docks are counted apart",
          (model.padCount, model.dockCount) == (2, 1),
          f"{model.padCount}/{model.dockCount}")
    labels = [model.data(model.index(row, 0), models.DevicesModel.LabelRole)
              for row in range(model.count)]
    check("a nickname wins over the model name",
          labels == ["Desk", "Apex 5", "Shelf"], str(labels))
    detail = model.data(model.index(1, 0), models.DevicesModel.DetailRole)
    check("an unnamed pad is told apart by its node", "/dev/hidraw4" in detail,
          detail)
    check("nothing is marked as a mock", not model.hasMock)


def test_choosing_a_pad_tells_the_daemon_and_choosing_a_dock_does_not():
    """The pad choice is shared state; the dock choice is this window's."""
    model, settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"), pad_entry(4, "Couch"),
                           dock_entry(6, "Shelf")])
    asked = []
    model.padSelected.connect(asked.append)
    docks = []
    model.dockSelected.connect(docks.append)

    model.select(1)
    check("the pad selector moves", model.pad == "uid:" + "04" * 13, model.pad)
    check("the worker is told once", len(asked) == 1, str(asked))
    check("and the daemon's file has it",
          prefs.Prefs(settings.path).primary_pad() == model.pad,
          str(prefs.Prefs(settings.path).primary_pad()))

    model.select(2)
    check("choosing a dock does not move the pad",
          model.pad == "uid:" + "04" * 13, model.pad)
    check("and does not write to the file again",
          prefs.Prefs(settings.path).primary_pad() == "uid:" + "04" * 13)
    check("the dock selector moves", model.dock == "uid:" + "06" * 13,
          model.dock)
    check("and the dock page is told", docks == [model.dock], str(docks))
    check("the window follows the dock", model.currentIsDock)
    check("and names it", model.currentLabel == "Shelf", model.currentLabel)


def test_a_pad_this_project_does_not_drive_never_becomes_the_daemon_s():
    """Looking at a Vader 5 is allowed; aiming the drivers at it is not.

    `primary_pad` is not "what the window is showing" -- it is the selector the
    daemon hands to `flydigi-monitor`, `flydigi-forza` and `flydigi-dsx` as
    `--device`, and each of those holds that pad for a whole session rewriting
    its trigger effects. Every Flydigi pad of this generation opens identically,
    so before this the picker offering an unsupported pad meant one click aimed
    commands 81 and 82 at it. The drivers refuse it themselves too; this is the
    half that stops the preferences file ever asking.
    """
    model, settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"),
                           pad_entry(4, "Vader", supported=False)])
    asked = []
    model.padSelected.connect(asked.append)

    model.select(0)
    check("the supported pad is written through",
          prefs.Prefs(settings.path).primary_pad() == "uid:" + "02" * 13,
          str(prefs.Prefs(settings.path).primary_pad()))

    model.select(1)
    check("the window does follow the unsupported pad",
          model.pad == "uid:" + "04" * 13, model.pad)
    check("and the worker is still told, so its pages can say what it is",
          len(asked) == 2, str(asked))
    check("but the daemon's file is left on the pad it can drive",
          prefs.Prefs(settings.path).primary_pad() == "uid:" + "02" * 13,
          str(prefs.Prefs(settings.path).primary_pad()))


def test_the_sidebar_is_told_what_the_selected_pad_can_do():
    """Two pads are driven now and they are not the same hardware.

    `kinds` keeps a dock's pages away from a pad; this is the second filter and
    a different question -- a Vader is a pad, and gets Buttons, Macros, Sticks,
    Gyro, Vibration and Lighting like any pad, but it has no screen and no force
    triggers. Offering a Screen page for a panel that is not there would be the
    window offering to configure something that does not exist.
    """
    model, _settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"),
                           pad_entry(4, "Vader", supported=False)])

    model.select(0)
    check("an Apex 5 has force triggers",
          model.capabilities.get("adaptive_triggers") is True,
          str(model.capabilities))
    check("and a screen", model.capabilities.get("screen") is True,
          str(model.capabilities))
    check("and no trigger motors",
          model.capabilities.get("trigger_motors") is False,
          str(model.capabilities))

    model.select(1)
    check("a Vader has neither the triggers",
          model.capabilities.get("adaptive_triggers") is False,
          str(model.capabilities))
    check("nor the screen", model.capabilities.get("screen") is False,
          str(model.capabilities))
    check("but it does have the trigger motors",
          model.capabilities.get("trigger_motors") is True,
          str(model.capabilities))

    # An empty bus reads as "can nothing", which is what hides those pages
    # while the window is still looking for a controller.
    model.devicesReceived([])
    check("nothing attached can nothing", model.capabilities == {},
          str(model.capabilities))


def test_a_dock_nobody_picked_is_still_read():
    """A dock on the bus has to be pointed at without being chosen first.

    `_remember` runs when a person picks a device, and it is the only thing
    that fills the dock selector in — a pad's comes out of the preferences file
    and a dock has no equivalent there. So until this existed, the Dock page sat
    on "Reading the dock…" until you opened the picker and chose the dock that
    was already selected. Reachable without trying: the pad leaves the USB bus
    whenever it sleeps, and a bus with only a dock on it opens on the dock's own
    pages.
    """
    model, _settings = devices_model()
    docks = []
    model.dockSelected.connect(docks.append)

    model.devicesReceived([dock_entry(6, "Shelf")])
    check("the dock is adopted without being picked",
          docks == ["uid:" + "06" * 13], str(docks))
    check("and the selector is set", model.dock == "uid:" + "06" * 13, model.dock)

    # The enumeration poll runs every few seconds, and re-reading the dock on
    # each one would be far worse than not reading it at all.
    model.devicesReceived([dock_entry(6, "Shelf")])
    model.devicesReceived([dock_entry(6, "Shelf")])
    check("and it is not asked for again on the next poll", len(docks) == 1,
          str(docks))

    # A pad alongside changes which pages are shown and not which dock is meant.
    model, _settings = devices_model()
    docks = []
    model.dockSelected.connect(docks.append)
    model.devicesReceived([pad_entry(2, "Desk"), dock_entry(6, "Shelf")])
    check("a dock behind a pad is adopted too",
          docks == ["uid:" + "06" * 13], str(docks))
    check("while the window still opens on the pad", not model.currentIsDock)

    # And an explicit choice still wins: adoption takes the first dock, so a
    # second one must not be taken back off the user on the next poll.
    model, _settings = devices_model()
    model.devicesReceived([dock_entry(6, "Shelf"), dock_entry(8, "Desk")])
    model.select(1)
    check("picking the second dock moves the selector",
          model.dock == "uid:" + "08" * 13, model.dock)
    model.devicesReceived([dock_entry(6, "Shelf"), dock_entry(8, "Desk")])
    check("and a poll does not drag it back to the first",
          model.dock == "uid:" + "08" * 13, model.dock)


def test_the_selection_survives_a_pad_moving_to_another_node():
    """The reason a selection is a uid and not a row.

    A pad that sleeps and comes back lands on a different node and may sort
    somewhere else entirely; a picker holding a row number would silently be
    showing a different device.
    """
    model, _settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"), pad_entry(4, "Couch")])
    model.select(1)
    chosen = model.pad

    # Same two pads, other way round, on new nodes.
    moved = pad_entry(4, "Couch")
    moved["path"] = "/dev/hidraw11"
    model.devicesReceived([moved, pad_entry(2, "Desk")])
    check("the same pad is still selected", model.pad == chosen, model.pad)
    check("at its new row", model.currentIndex == 0, str(model.currentIndex))
    check("and it is still the one named", model.currentLabel == "Couch",
          model.currentLabel)


def test_a_pad_that_goes_away_is_not_forgotten():
    """Losing a half-finished remap because the pad dozed off is the bug here."""
    model, _settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"), pad_entry(4, "Couch")])
    model.select(1)
    chosen = model.pad

    model.devicesReceived([pad_entry(2, "Desk")])
    check("the selection is kept", model.pad == chosen, model.pad)
    check("while the list shows what is there", model.count == 1)
    check("and something is shown rather than nothing",
          model.currentIndex == 0, str(model.currentIndex))

    model.devicesReceived([pad_entry(2, "Desk"), pad_entry(4, "Couch")])
    check("and it is selected again when it returns",
          model.currentIndex == 1 and model.currentLabel == "Couch",
          f"{model.currentIndex} {model.currentLabel}")


def test_an_empty_bus_still_shows_the_pad_pages():
    model, _settings = devices_model()
    model.devicesReceived([])
    check("nothing is selected", model.currentIndex == -1)
    check("and the window stays on the pad's pages",
          model.currentKind == "pad" and not model.currentIsDock)


def test_a_dock_switch_moves_at_once_and_asks_the_worker():
    model = models.DockModel()
    asked = []
    model.switchRequested.connect(lambda sel, name, value:
                                  asked.append((sel, name, value)))
    reads = []
    model.refreshRequested.connect(reads.append)

    model.setSelector("uid:aa")
    check("pointing it at a dock reads it", reads == ["uid:aa"], str(reads))
    check("and it has nothing to show yet", not model.present)

    model.stateReceived({
        "selector": "uid:aa",
        "info": {"firmware": "0.0.3.9", "device_type": 0,
                 "sleep_when_charging": True, "led_sync": False,
                 "close_with_system": True,
                 "show_animation_when_charging": False},
        "uid": "aa" * 13, "nickname": "Shelf",
        "lighting": {"mode": 5, "brightness": 40, "period": 3, "direction": 0,
                     "colours": [[0, 116, 255]]},
        "status": {"docked": True, "battery": 3}})
    check("the dock is present", model.present)
    check("its name is shown", model.nickname == "Shelf")
    check("its switches are read, not assumed",
          (model.sleepWhenCharging, model.ledSync, model.closeWithSystem,
           model.showAnimationWhenCharging) == (True, False, True, False),
          str([model.sleepWhenCharging, model.ledSync, model.closeWithSystem,
               model.showAnimationWhenCharging]))
    check("and what is sitting in it", "docked" in model.dockedState,
          model.dockedState)

    model.ledSync = True
    check("the switch moves at once", model.ledSync)
    check("and the worker is asked, for this dock",
          asked == [("uid:aa", "led_sync", True)], str(asked))


def test_a_reply_for_a_dock_nobody_is_looking_at_is_dropped():
    """Two docks and a picker: a read started before the switch must not land."""
    model = models.DockModel()
    model.setSelector("uid:aa")
    model.stateReceived({"selector": "uid:aa", "info": {"firmware": "0.0.3.9"},
                         "nickname": "Shelf", "lighting": {}, "status": None})
    model.setSelector("uid:bb")
    model.stateReceived({"selector": "uid:aa", "info": {"firmware": "9.9.9.9"},
                         "nickname": "Shelf", "lighting": {}, "status": None})
    check("the stale reply is ignored", model.firmware != "9.9.9.9",
          model.firmware)
    check("and the page is still waiting for the dock it is on",
          not model.present)


def test_a_dock_effect_takes_its_own_defaults():
    """Space Station gives each mode a period, colours and a direction.

    Jumping between them without taking those leaves a rainbow running at a
    breath's frame interval.
    """
    model = models.DockModel()
    model.setSelector("uid:aa")
    model.modeIndex = models.MODE_NAMES.index("Rainbow")
    check("rainbow reads a direction", model.usesDirection)
    check("and no colours", model.coloursUsed == 0, str(model.coloursUsed))
    check("its interval range is its own",
          (model.periodMin, model.periodMax) == (1, 5),
          f"{model.periodMin}..{model.periodMax}")

    model.modeIndex = models.MODE_NAMES.index("Breath")
    check("breath reads one colour", model.coloursUsed == 1,
          str(model.coloursUsed))
    check("and no direction", not model.usesDirection)

    wanted = []
    model.lightingRequested.connect(lambda sel, cfg: wanted.append((sel, cfg)))
    model.apply()
    check("applying is busy until the upload finishes", model.busy)
    check("and asks for this dock", wanted[0][0] == "uid:aa", str(wanted))
    check("with the mode chosen", wanted[0][1]["mode"] == 5, str(wanted))
    model.writeFinished(True)
    check("and stops being busy when it is done", not model.busy)


# -- the dock's picture ---------------------------------------------------


def dock_model_with(picture="still"):
    """A DockModel holding a picture, loaded from a real file.

    Through the file reader rather than a back door, as the screen fixture is
    and for the same reason: decoding is most of what this half of the model
    does, and it is where an EXIF rotation or a transparent background would go
    wrong.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QPainter

    model = models.DockModel()
    model.setSelector("uid:aa")
    model.modeIndex = models.MODE_NAMES.index("Picture")
    if picture == "gif":
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "qml", "four-frames.gif")
    else:
        path = os.path.join(tempfile.mkdtemp(), "still.png")
        # Deliberately not a flat colour and deliberately wider than the crop
        # window's 334x304: a solid image samples identically however it is
        # framed, so it could not tell filling from fitting from a pan.
        image = QImage(600, 400, QImage.Format_RGB888)
        painter = QPainter(image)
        for x in range(600):
            painter.fillRect(x, 0, 1, 400,
                             QColor(int(255 * x / 599), 40, 255 - int(255 * x / 599)))
        painter.fillRect(20, 20, 80, 80, QColor("white"))
        painter.end()
        image.save(path, "PNG")
    loaded = model.openImage(QUrl.fromLocalFile(path))
    return model, loaded


def test_a_picture_is_framed_on_the_window_the_leds_are_read_from():
    """Filling, fitting and the zoom, in the stage's own coordinates."""
    model, loaded = dock_model_with("still")
    check("the picture loaded", loaded)
    check("the page knows it", model.hasImage)
    check("one frame", model.frameCount == 1, model.frameCount)
    check("so it is not an animation", not model.animated)

    # 600x400 filled onto 334x304 covers by height: 304/400 * 600 = 456 wide.
    width, height = model.renderedSize
    check("filling covers the window", (round(width), round(height)) == (456, 304),
          (width, height))
    check("and is centred in it", round(model.imageX) == 92 and model.imageY == 8,
          (model.imageX, model.imageY))
    check("which leaves it draggable", model.canPan)

    model.imageFitMode = models.dock.FIT_INSIDE
    width, height = model.renderedSize
    check("fitting inside letterboxes instead",
          (round(width), round(height)) == (334, 223), (width, height))

    model.imageFitMode = models.dock.FIT_STRETCH
    check("stretching takes the window's own shape",
          [round(v) for v in model.renderedSize] == [334, 304], model.renderedSize)

    model.imageFitMode = models.dock.FIT_FILL
    model.zoom = model.zoomMax
    check("the zoom tops out at 1.95x", model.zoomLabel == "1.95×", model.zoomLabel)
    check("and it scales the fitted size",
          round(model.renderedSize[0]) == round(456 * 1.95), model.renderedSize)
    check("a zoom re-centres, as Space Station's does",
          round(model.imageX) == round(153 + (334 - 456 * 1.95) / 2),
          model.imageX)


def test_the_picture_cannot_be_dragged_off_the_window():
    """A pan stops when an edge arrives, because past it is black LEDs."""
    model, _loaded = dock_model_with("still")
    width, _height = model.renderedSize

    model.panBy(10000, 0)
    check("dragging right stops at the picture's left edge",
          model.imageX == 153, model.imageX)
    model.panBy(-10000, 0)
    check("and dragging left stops at its right edge",
          round(model.imageX) == round(153 + 334 - width), model.imageX)
    check("the picture is not tall enough to pan vertically",
          model.imageY == 8, model.imageY)

    # Fitting inside makes it smaller than the window on both axes, and there
    # is no valid range left to clamp into.
    model.imageFitMode = models.dock.FIT_INSIDE
    check("a picture smaller than the window stops being draggable",
          not model.canPan)
    before = model.imageX
    model.panBy(50, 50)
    check("and stays centred when dragged anyway", model.imageX == before,
          (before, model.imageX))


def test_moving_the_picture_changes_the_leds():
    """The preview is sampled from the framing, not from the file."""
    model, _loaded = dock_model_with("still")
    check("162 colours, one per LED", len(model.frameColours) == 162,
          len(model.frameColours))
    check("and every one is a colour",
          all(c.startswith("#") and len(c) == 7 for c in model.frameColours))

    framed = list(model.frameColours)
    model.panBy(-60, 0)
    check("panning re-samples", model.frameColours != framed)

    panned = list(model.frameColours)
    model.zoom = 12
    check("and so does zooming", model.frameColours != panned)

    zoomed = list(model.frameColours)
    model.imageFitMode = models.dock.FIT_STRETCH
    check("and so does the fit", model.frameColours != zoomed)

    model.clearImage()
    check("clearing puts the panel back to unlit", model.frameColours == [])
    check("and forgets the file", not model.hasImage and model.imageName == "")


def test_an_animation_is_trimmed_the_way_space_stations_bar_trims():
    model, loaded = dock_model_with("gif")
    check("the animation loaded", loaded)
    check("every source frame was read", model.sourceFrameCount == 4,
          model.sourceFrameCount)
    check("and all of them start selected",
          (model.trimMin, model.trimMax) == (0, 3),
          (model.trimMin, model.trimMax))
    check("so four would be sent", model.frameCount == 4, model.frameCount)
    check("which makes it an animation", model.animated)
    check("a filmstrip was written",
          model.filmstripSource.startswith("file:"), model.filmstripSource)
    check("the frame time came off the GIF", model.intervalMs > 0, model.intervalMs)

    model.setTrim(1, 2)
    check("both ends are inclusive", model.frameCount == 2, model.frameCount)
    model.setTrim(3, 1)
    check("a range given backwards is put the right way round",
          (model.trimMin, model.trimMax) == (1, 3), (model.trimMin, model.trimMax))
    model.setTrim(2, 2)
    check("one frame is allowed", model.frameCount == 1, model.frameCount)
    check("and one frame is not an animation", not model.animated)
    model.setTrim(-5, 99)
    check("and a range outside the file is clamped to it",
          (model.trimMin, model.trimMax) == (0, 3),
          (model.trimMin, model.trimMax))


def test_the_preview_walks_the_trimmed_frames_and_wraps():
    """The index is into the selection, not into the file.

    A preview counting through the whole GIF while the trim bar says otherwise
    would be showing frames that are not going to be sent.
    """
    model, _loaded = dock_model_with("gif")
    model.setTrim(1, 2)
    model.previewFrame = 0
    check("it starts on the first selected frame", model.previewFrame == 0)
    model.previewFrame = 1
    check("and steps", model.previewFrame == 1)
    model.previewFrame = 2
    check("and wraps at the end of the selection", model.previewFrame == 0,
          model.previewFrame)

    model.setTrim(0, 3)
    model.previewFrame = 3
    check("a wider selection reaches further", model.previewFrame == 3)
    model.setTrim(0, 1)
    check("and narrowing it does not leave the index past the end",
          model.previewFrame < model.frameCount,
          (model.previewFrame, model.frameCount))


def test_a_picture_is_applied_as_frames_rather_than_as_a_mode():
    """What crosses to the worker, and why it crosses as bytes.

    Two hundred frames as nested lists is ninety-seven thousand Python integers
    through a queued signal; as `bytes` it is one QByteArray.
    """
    from flydigi import charger

    model, _loaded = dock_model_with("gif")
    model.setTrim(0, 2)
    model.intervalMs = 120

    wanted = []
    model.lightingRequested.connect(lambda sel, cfg: wanted.append((sel, cfg)))
    model.apply()
    check("it asked for this dock", wanted and wanted[0][0] == "uid:aa", str(wanted))
    sent = wanted[0][1]
    check("the mode is custom", sent["mode"] == charger.MODE_CUSTOM, sent["mode"])
    check("the frames are one flat bytes", isinstance(sent["frames"], bytes),
          type(sent["frames"]).__name__)
    check("three frames of 162 colours", len(sent["frames"]) == 3 * 162 * 3,
          len(sent["frames"]))
    check("and they unpack into what the dock takes",
          len(charger.unpack_frames(sent["frames"])) == 3)
    check("full brightness, as Space Station sends", sent["brightness"] == 100,
          sent["brightness"])
    check("the period is the frame time in the dock's own 20 ms units",
          sent["period"] == 6, sent["period"])
    check("and no palette goes with it", sent["colours"] == [], sent["colours"])
    check("nor a direction", sent["direction"] == charger.DIR_NONE,
          sent["direction"])

    # Space Station rounds with `Math.round`, which sends halves up; Python's
    # own `round` sends them to even. 50 ms is half a unit, a period of 3 their
    # way and 2 with Python's rounding -- a third too fast on the dock. And 50
    # needs no typing: a two-frame GIF at 40 and 60 ms averages to it.
    model.writeFinished(True)
    wanted.clear()
    model.intervalMs = 50
    model.apply()
    check("a half-unit frame time rounds up, as theirs does",
          wanted[0][1]["period"] == 3, wanted[0][1]["period"])
    model.writeFinished(True)

    # A still gets Space Station's own period of 1 rather than an interval.
    still, _ = dock_model_with("still")
    wanted.clear()
    still.lightingRequested.connect(lambda sel, cfg: wanted.append((sel, cfg)))
    still.apply()
    check("a still goes up with period 1", wanted[0][1]["period"] == 1,
          wanted[0][1]["period"])
    check("as a single frame", len(wanted[0][1]["frames"]) == 162 * 3,
          len(wanted[0][1]["frames"]))


def test_a_picture_that_is_not_there_is_not_uploaded():
    """The mode alone is not enough, and the frame count must not be zero.

    A custom config with no frames is the state that left the dock cycling its
    own frame memory -- fragments, then noise, then flat white.
    """
    model = models.DockModel()
    model.setSelector("uid:aa")
    model.modeIndex = models.MODE_NAMES.index("Picture")
    check("with no picture there is nothing to apply", not model.canApplyImage)

    wanted = []
    model.lightingRequested.connect(lambda sel, cfg: wanted.append((sel, cfg)))
    model.apply()
    check("so applying asks for nothing", not wanted, str(wanted))
    check("and the page does not go busy", not model.busy)

    check("a picture is not an effect with a colour", model.coloursUsed == 0)
    check("nor one with a direction", not model.usesDirection)
    check("and the page knows to hide the rest", model.isPicture)


def test_a_huge_picture_is_not_kept_at_a_size_nothing_can_show():
    """162 dots do not need 1080p, and 255 frames of it is a gigabyte and a half.

    The bound is what the window can actually sample at full zoom, so the
    limit has to stay comfortably above 334x304 rather than merely below the
    source.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QPainter

    limit = models.dock.decode_limit(1920, 1080)
    check("a 1080p frame is cut down", limit[0] < 1920, limit)
    check("but stays wider than the window it fills",
          limit[0] > 334 and limit[1] > 304, limit)
    check("keeping its shape", abs(limit[0] / limit[1] - 1920 / 1080) < 0.01, limit)
    check("a picture already smaller than that is left alone",
          models.dock.decode_limit(300, 200) == (300, 200))
    check("and nothing divides by zero", models.dock.decode_limit(0, 0) == (0, 0))

    path = os.path.join(tempfile.mkdtemp(), "big.png")
    image = QImage(1920, 1080, QImage.Format_RGB888)
    painter = QPainter(image)
    for band in range(12):
        painter.fillRect(band * 160, 0, 160, 1080,
                         QColor.fromHsv(band * 29, 220, 240))
    painter.end()
    image.save(path, "PNG")

    model = models.DockModel()
    model.setSelector("uid:aa")
    model.modeIndex = models.MODE_NAMES.index("Picture")
    check("the big picture loaded", model.openImage(QUrl.fromLocalFile(path)))
    check("and was decoded at the smaller size",
          model._images[0].width() == limit[0], model._images[0].width())
    check("and still samples 162 LEDs", len(model.frameColours) == 162)


def test_the_playback_cursor_does_not_churn_the_framing():
    """Three signals, because they move on entirely different occasions.

    The preview cursor ticks ten times a second while an animation plays, and
    the framing moves on every pointer event of a drag. On one signal, every
    property of both halves re-evaluates for either -- and the trim slider,
    which has to be assigned to rather than bound since dragging a handle
    destroys any binding on it, would be written to underneath the user's own
    drag on the stage.
    """
    model, _loaded = dock_model_with("gif")
    picture, framing, preview = [], [], []
    model.imageChanged.connect(lambda: picture.append(1))
    model.framingChanged.connect(lambda: framing.append(1))
    model.previewChanged.connect(lambda: preview.append(1))

    model.previewFrame = 1
    check("stepping the preview says so", len(preview) == 1, len(preview))
    check("and says nothing about the framing or the file",
          not framing and not picture, f"{len(framing)}/{len(picture)}")

    model.panBy(-20, 0)
    check("panning moves the framing", len(framing) == 1, len(framing))
    check("and leaves the file, the trim and the cost alone",
          not picture, len(picture))

    model.setTrim(1, 2)
    check("trimming is the file's half", len(picture) == 1, len(picture))
    check("and does not claim the picture was moved", len(framing) == 1,
          len(framing))


def test_a_file_qt_cannot_read_says_so_rather_than_half_loading():
    model = models.DockModel()
    path = os.path.join(tempfile.mkdtemp(), "not-a-picture.png")
    with open(path, "wb") as handle:
        handle.write(b"nothing of the sort")
    from PySide6.QtCore import QUrl
    check("loading fails", not model.openImage(QUrl.fromLocalFile(path)))
    check("it says which file", "not-a-picture.png" in model.imageMessage,
          model.imageMessage)
    check("and nothing is left half loaded", not model.hasImage)
    check("with no frames to send", model.frameCount == 0)


def test_models_pull_in_no_view_code():
    """The check that the extraction is real, not just relocated."""
    leaked = sorted(name for name in sys.modules
                    if name.startswith("PySide6.QtWidgets")
                    or name.startswith("PySide6.QtQuick"))
    check("models import no view toolkit", not leaked, str(leaked))



# -- screen ---------------------------------------------------------------


def screen_settled(model, msecs=5000):
    """Wait for the encode worker to hand its frames back.

    Framing a picture happens on a short-lived thread now, so `open`, a fit
    change and `framingSettled` all return before there are any frames to see.
    Every screen case below that looks at frames, previews or `canUpload` goes
    through here first -- and so does every one that does not, because a case
    that returns while a worker is still running drops the last reference to a
    live QThread, which Qt turns into a qFatal.
    """
    for _ in range(0, msecs, 10):
        if not model.encoding:
            break
        spin(10)
    spin(10)          # let `finished` land too, so nothing is still running
    check("the encode finished", not model.encoding)


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
    # An upload is wired-only, so every test that sends one has to say the pad
    # is on a cable. `test_an_upload_needs_a_cable` is the one that does not.
    model.infoReceived({"connect_type": "wired"})
    if loaded:
        screen_settled(model)
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
        # The fit moves at once; the pixels it implies arrive off the thread
        # that encodes them, and it is the pixels this case is about.
        screen_settled(model)
        model.upload()
        model.uploadFinished(True)

    check("three fits, three different images", len(set(encoded.values())) == 3,
          str(sorted(len(v) for v in encoded.values())))
    # Letterboxing a tall picture into a wide panel leaves the corners black.
    _w, _h, rgb = screen.decode_frame(encoded[1])
    check("fit leaves a black corner", rgb[:3] == b"\x00\x00\x00", rgb[:3].hex())
    _w, _h, filled = screen.decode_frame(encoded[0])
    check("fill does not", filled[:3] != b"\x00\x00\x00", filled[:3].hex())


def test_an_upload_needs_a_cable():
    """On the dongle, starting one strands the pad in upgrade mode.

    **Measured on the hardware here.** The pad takes command 31 over the dongle
    and switches its screen chip into upgrade mode; nothing then appears on the
    PC, because the dongle does not relay the bootloader's USB CDC device and
    has no notion that there is one. So the upload times out waiting for a tty
    that cannot arrive, and the pad sits in upgrade mode until it is
    power-cycled at its own switch. Nothing about that is recoverable from the
    application, which is why the refusal has to come before the command.
    """
    model, _ = screen_model_with(1)
    check("a wired pad may upload", model.canUpload)
    check("and is not nagged about it", model.uploadBlocked == "")

    model.infoReceived({"connect_type": "dongle"})
    check("the same picture on the dongle may not", not model.canUpload)
    check("and the page is told why", "cable" in model.uploadBlocked,
          model.uploadBlocked)

    started = []
    model.uploadRequested.connect(lambda *a: started.append(a))
    model.upload()
    check("calling upload() anyway sends nothing", started == [])
    check("and it does not leave the page stuck busy", not model.busy)

    # An unanswered info poll is not evidence of a cable either.
    model.infoReceived({})
    check("an unknown connection counts as not wired", not model.canUpload)

    model.infoReceived({"connect_type": "wired"})
    model.upload()
    check("plugging it in lets it through", len(started) == 1)


def test_the_screen_picture_can_be_dragged_under_the_panel():
    """The Dock page's stage, against the panel's 160x80 window.

    Space Station has no framing here at all -- their screen page takes the
    middle of the picture and that is the whole of it -- so every number below
    is this project's own, and the arithmetic is `imaging.CropFrame`'s.
    """
    model, _ = screen_model_with(1)

    # 90x70 filled onto 160x80 covers by width: 160/90 * 70 = 124.4 tall. The
    # stage is 320x160, so the window's corner is at (80, 40).
    check("filling covers the window",
          (round(model.imageDrawWidth), round(model.imageDrawHeight)) == (160, 124),
          (model.imageDrawWidth, model.imageDrawHeight))
    check("and is centred in it",
          model.imageX == 80 and round(model.imageY, 1) == 17.8,
          (model.imageX, model.imageY))
    check("a picture taller than the window is draggable", model.canPan)

    model.panBy(0, 10000)
    check("dragging down stops at the picture's top edge",
          model.imageY == 40, model.imageY)
    model.panBy(0, -10000)
    check("and dragging up stops at its bottom edge",
          round(model.imageY, 1) == -4.4, model.imageY)
    check("it is exactly as wide as the window, so it does not slide sideways",
          model.imageX == 80, model.imageX)

    model.fitMode = 1
    check("fitting inside letterboxes instead",
          (round(model.imageDrawWidth), round(model.imageDrawHeight)) == (103, 80),
          (model.imageDrawWidth, model.imageDrawHeight))
    check("and leaves nothing to drag", not model.canPan)

    model.fitMode = 0
    model.zoom = model.zoomMax
    check("the zoom tops out at 1.95x", model.zoomLabel == "1.95×", model.zoomLabel)
    check("and it scales the fitted size",
          round(model.imageDrawWidth) == round(160 * 1.95), model.imageDrawWidth)
    check("a zoom re-centres, as the dock's does",
          round(model.imageX) == round(80 + (160 - 160 * 1.95) / 2), model.imageX)
    # Nothing above waits on an encode -- the stage is arithmetic and answers
    # at once, which is the whole reason a drag can be smooth. The wait is so
    # the two fit changes above do not outlive the case.
    screen_settled(model)


def test_a_screen_drag_re_encodes_when_it_ends_and_not_before():
    """The one thing this page cannot copy from the Dock page.

    Sampling 162 LEDs is half a millisecond, so the dock recomputes on every
    mouse move. Here a reframe is an encode plus a preview file per frame, so a
    255-frame animation would be seconds of work per pointer event. The stage
    follows the pointer for free either way -- it is only moving an item that is
    already on the scene graph -- and the encoded preview waits for the release.
    """
    model, _ = screen_model_with(1)
    sent = []
    model.uploadRequested.connect(lambda frames, _i, _r: sent.append(frames[0]))

    def encoded_now():
        model.upload()
        model.uploadFinished(True)
        return sent[-1]

    before = encoded_now()
    model.panBy(0, -40)
    check("the stage moved", round(model.imageY, 1) == -4.4, model.imageY)
    check("but nothing was re-encoded", encoded_now() == before)

    model.framingSettled()
    screen_settled(model)
    check("the release is what pays for it", encoded_now() != before)


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
    screen_settled(model)
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


# -- state kept in fields rather than derived per read ----------------------

def test_lighting_dirty_comes_back_down_as_well_as_up():
    """`dirty` is a field now, so the way it fails is by latching.

    Every other lighting test asserts an edit turns it on, which a flag that
    is only ever set would pass. This walks a byte out and back instead: the
    blob ends identical to what the pad gave us, so anything but clean means
    the field stopped tracking the bytes.
    """
    model = make_lighting()
    check("a freshly read config is clean", not model.dirty)

    model.gripSync = False
    check("an edit is dirty", model.dirty)
    model.gripSync = True
    check("putting the byte back reads clean again", not model.dirty)

    # Through a different mutator, because each one reaches `_mark` on its own.
    model.brightness = 0 if model.brightness else 1
    check("another field dirties it too", model.dirty)
    model.confirmWritten(True)
    check("confirming makes the edit the reference copy", not model.dirty)
    check("and there is nothing left to save", not model.saveNeeded)

    # A second read of the same pad: `configLoaded` replaces both blobs, and
    # a flag left over from the profile before it would show as a page that
    # thinks it has unsaved changes it cannot name.
    model.effect = models.EFFECT_NAMES.index("Static")
    check("choosing an effect is dirty", model.dirty)
    model.configLoaded(bytes(FakePad().led_blob))
    check("re-reading from the pad is clean again", not model.dirty)


def test_a_second_setup_reading_replaces_the_checklist():
    """The check states are indexed by id, and an index is a thing to go stale.

    `setChecks` is the only writer and rebuilds it outright, which is what a
    requirement that has just been fixed -- or just broken -- depends on.
    Every other setup test builds a fresh model, so none of them would notice
    an index that merged rather than replaced.
    """
    model = make_setup(ok("hidraw"), ok("uhid"), ok("input"), ok("rules"),
                       ok("unit"))
    check("all green reads as ready", model.ready)

    model._checks.setChecks([failing("unit")])
    check("the later reading is the one that counts", not model.ready)
    check("a check the reading no longer mentions is unknown, not what it was",
          model._checks.state("hidraw") == system_setup.UNKNOWN,
          model._checks.state("hidraw"))

    model._checks.setChecks([ok("unit"), ok("hidraw"), ok("uhid"),
                             ok("input"), ok("rules")])
    check("and a fixed requirement goes green", model.ready)


def test_the_launcher_command_is_worked_out_once():
    """It opens /run/.containerenv and walks PATH -- not a getter's job.

    Asserted against `flydigi.setup` rather than against itself, because the
    way a held answer goes wrong is by being something other than what would
    actually be installed.
    """
    calls = []
    real = system_setup.desktop_exec
    system_setup.desktop_exec = lambda: (calls.append(None) or real())
    try:
        model = models.SetupModel()
        first = model.desktopCommand
        # A reading arriving re-emits `changed`, which re-runs the binding.
        model._finished("refresh", [ok("unit")], "")
        second = model.desktopCommand
    finally:
        system_setup.desktop_exec = real

    check("it is the command the entry would be given", first == real(), first)
    check("a reading does not change it", second == first, second)
    check("and the filesystem was asked once", len(calls) == 1, str(len(calls)))


def test_a_reported_setup_worker_is_still_there_to_be_waited_on():
    """`busy` falls when `done` arrives, which is before the thread has gone.

    `done` is the last statement of `run()`, so the handle has to outlive the
    slot that hears it: shutdown calls `wait`, and a window closed inside that
    gap used to find nothing to wait on. Releasing it is `_run`'s job, which
    is also what stops a page taken twice from leaving two threads behind.
    """
    model = models.SetupModel()
    real = system_setup.checks
    system_setup.checks = lambda: [ok("unit")]
    try:
        model.refresh()
        check("it is busy while the thread runs", model.busy)
        for _ in range(200):
            if not model.busy:
                break
            spin(10)
        check("and not busy once the reading lands", not model.busy)
        check("the reading arrived", model.unitInstalled)
        check("the thread is still held for shutdown to wait on",
              model._previous is not None)

        model.refresh()
        check("starting the next one releases the last", model._previous is None)
        for _ in range(200):
            if not model.busy:
                break
            spin(10)
        model.wait(2000)
        check("waiting leaves nothing behind",
              model._thread is None and model._previous is None)
    finally:
        system_setup.checks = real
        model.wait(2000)


class CountingConfig(mapping.MappingConfig):
    """A profile that says how often it was asked to decode itself."""

    def __init__(self, blob, cfg_id=None):
        super().__init__(blob, cfg_id)
        self.decodes = {}

    def _count(self, what):
        self.decodes[what] = self.decodes.get(what, 0) + 1

    def motion(self):
        self._count("motion")
        return super().motion()

    def mapping(self, key):
        self._count("mapping")
        return super().mapping(key)

    def macros(self):
        self._count("macros")
        return super().macros()


MOTION_PROPERTIES = ("target", "enabled", "isMouse", "enableType", "key",
                     "secondKey", "hasKey", "holdMode", "strandedKey",
                     "sensitivity", "deadZone", "useMode")


def counting_profile():
    """A loaded profile whose config counts its own decodes."""
    profile, _ = make_profile()
    counting = CountingConfig(bytes(profile.config.blob), profile.cfgId)
    profile._replace(counting)
    return profile, counting


def test_a_page_of_properties_decodes_the_profile_once():
    """The state layer, asserted where it is invisible everywhere else.

    A getter that decodes the blob for itself returns exactly the same answer as
    one that reads a cached field, so no other test in this file can tell them
    apart -- which is how `MotionModel` came to decode the same eight bytes
    thirteen times for one `changed`, and `KeyMapModel` to decode the key table
    207 times to fill 23 rows. Both were found by reading, not by failing.

    So this counts. `CountingConfig` is the real `MappingConfig` with a tally on
    the three decoders the models lean on hardest.
    """
    profile, counting = counting_profile()
    motion = profile.motion

    before = counting.decodes.get("motion", 0)
    for _ in range(3):
        for name in MOTION_PROPERTIES:
            getattr(motion, name)
    check("twelve properties read three times decode the motion block once",
          counting.decodes.get("motion", 0) == before + 1,
          counting.decodes.get("motion", 0) - before)

    motion.sensitivity = 40
    before = counting.decodes.get("motion", 0)
    for name in MOTION_PROPERTIES:
        getattr(motion, name)
    check("and an edit costs exactly one more",
          counting.decodes.get("motion", 0) == before + 1,
          counting.decodes.get("motion", 0) - before)
    check("which is still the value that was written", motion.sensitivity == 40)


def test_a_sweep_of_the_key_table_decodes_it_once_per_row():
    """23 rows across 9 roles is 23 decodes, not 207.

    Three of the nine roles -- the key, its label and its cluster -- are answers
    about the shell and not about the profile, so they are given before the
    table is touched at all.
    """
    profile, counting = counting_profile()
    keys = profile.keys
    roles = list(keys.roleNames())

    before = counting.decodes.get("mapping", 0)
    for row in range(keys.count):
        for role in roles:
            keys.data(keys.index(row, 0), role)
    check("one sweep decodes one row's worth per row",
          counting.decodes.get("mapping", 0) == before + keys.count,
          counting.decodes.get("mapping", 0) - before)

    before = counting.decodes.get("mapping", 0)
    for row in range(keys.count):
        for role in roles:
            keys.data(keys.index(row, 0), role)
    check("and a second sweep, with nothing edited, decodes nothing",
          counting.decodes.get("mapping", 0) == before,
          counting.decodes.get("mapping", 0) - before)

    keys.setTurbo(0, 20)
    before = counting.decodes.get("mapping", 0)
    for row in range(keys.count):
        keys.data(keys.index(row, 0), keys.TargetRole)
    check("a remap invalidates it, once", 
          counting.decodes.get("mapping", 0) == before + keys.count,
          counting.decodes.get("mapping", 0) - before)
    check("and the row reads back what was written", keys.turboAt(0) == 20)


def test_the_macro_page_is_not_decoded_for_every_row_count():
    """`rowCount` is asked far more often than the macros change.

    Decoding the macro page is the most expensive read in the model: 538 bytes
    parsed into a list of dicts of lists, and it sat behind `rowCount`, `count`,
    `canAdd`, `stepsUsed` and every role of every row.
    """
    profile, counting = counting_profile()
    macros = profile.macros

    before = counting.decodes.get("macros", 0)
    for _ in range(20):
        macros.rowCount()
        macros.count
        macros.canAdd
        macros.stepsUsed
    check("eighty reads decode the macro page once",
          counting.decodes.get("macros", 0) == before + 1,
          counting.decodes.get("macros", 0) - before)


class CountingPrefs(prefs.Prefs):
    """A preferences file that says which games it has been asked about.

    `route` is what a game's row hangs off: it reads the game's stored entry
    and rebuilds the list of routes the game offers, and every other field of
    the row is worked out from what it returns. Counting it is how the two
    tests below tell "decoded once" from "decoded once per read" without timing
    anything, in the same spirit as `CountingConfig` above.
    """

    def __init__(self, path):
        super().__init__(path)
        self.asked = []

    def route(self, game):
        self.asked.append(prefs.key(game))
        return super().route(game)


def counting_games():
    """The same list as `make_games`, over a preferences file that counts."""
    settings = CountingPrefs(os.path.join(tempfile.mkdtemp(), "games.json"))
    source = models.GameListModel(settings=settings)
    source.setGames(GAMES)
    return source, models.GameFilterModel(source), settings


def test_a_game_row_is_worked_out_once_rather_than_once_per_role():
    """Ninety-four rows across nine roles is what the Games page asks for.

    `data` used to resolve the chosen route before it looked at which role it
    had been asked for, so every one of those questions paid for it -- and two
    of the roles then built a fresh list or a paragraph of prose on top. The
    row is worked out where the data moves instead, and read back as fields.
    """
    source, view, settings = counting_games()
    check("every game is worked out as the list is set",
          set(settings.asked) == {prefs.key(game) for game in GAMES},
          str(settings.asked))

    del settings.asked[:]
    roles = list(source.roleNames())
    for row in range(source.rowCount()):
        for role_id in roles:
            source.data(source.index(row, 0), role_id)
    check("and reading every role of every row works out nothing",
          settings.asked == [], str(settings.asked))

    # The one case where it has to happen again -- and one row's worth of it,
    # because a preference was written for one game.
    row = row_for(view, "Silksong")
    view.setAutoAt(row, False)
    check("a row whose preference was just written is worked out again",
          set(settings.asked) == {prefs.key(GAMES[2])}, str(settings.asked))
    check("and reads back what was written",
          role(view, row, b"auto") is False, str(role(view, row, b"auto")))


def test_a_search_matches_without_working_out_a_single_route():
    """`filterAcceptsRow` runs for all 94 rows on every keystroke.

    It used to lowercase a fresh haystack for each of them and resolve each
    row's route on the way past, so typing a game's name paid for the whole
    list once per letter. Both readings are settled when the row is decoded.
    """
    source, view, settings = counting_games()
    del settings.asked[:]

    view.search = "death"
    check("the search still finds the game", view.count == 1, str(view.count))
    check("the filter asks the preferences nothing", settings.asked == [],
          str(settings.asked))

    view.search = ""
    view.route = "vibration"
    check("nor does the route filter", settings.asked == [],
          str(settings.asked))
    check("and it still filters", view.count == 1, str(view.count))


def test_a_battery_tick_moves_one_row_rather_than_rebuilding_the_list():
    """A reset destroys every delegate attached, and the picker is on every page.

    The equal-probe guard covers an idle bus, which is most polls. It does not
    cover the one field that moves without anybody touching anything: the
    battery level. Treating that as a new list put the whole window through the
    same rebuild the guard exists to prevent -- a few times an hour rather than
    twice a minute, and just as invisible from the page it happens on.
    """
    model, _settings = devices_model()
    model.devicesReceived([pad_entry(2, "Desk"), dock_entry(6, "Shelf")])
    model.select(0)
    resets, changed = [], []
    model.modelAboutToBeReset.connect(lambda: resets.append(1))
    model.dataChanged.connect(
        lambda top, _bottom, roles: changed.append((top.row(), list(roles))))

    model.devicesReceived([pad_entry(2, "Desk"), dock_entry(6, "Shelf")])
    check("an unchanged bus still says nothing at all",
          (resets, changed) == ([], []), f"{resets} {changed}")

    drained = pad_entry(2, "Desk")
    drained["battery"] = 1
    model.devicesReceived([drained, dock_entry(6, "Shelf")])
    check("a battery tick rebuilds nothing", resets == [], str(len(resets)))
    check("it names the row that moved",
          [row for row, _roles in changed] == [0], str(changed))
    check("and the one role that moved with it",
          changed[0][1] == [models.DevicesModel.BatteryRole], str(changed))
    check("the new level is what the row reads",
          role(model, 0, b"battery") == 1, str(role(model, 0, b"battery")))
    check("and the selection is where it was", model.currentIndex == 0,
          str(model.currentIndex))

    # A device really leaving is what a reset is for: the rows a view holds are
    # no longer the rows the model has.
    model.devicesReceived([dock_entry(6, "Shelf")])
    check("losing a device is still a reset", len(resets) == 1,
          str(len(resets)))


def test_a_renamed_device_reports_the_name_the_picker_shows():
    """`label` is a fallback chain, not a field of the entry.

    So an in-place update has to know which roles a field feeds, or the picker
    goes on showing "Apex 5" for a pad that has just been given a name. A
    nickname feeds two roles and is read by neither of the ones it looks like.
    """
    model, _settings = devices_model()
    model.devicesReceived([pad_entry(2)])
    check("an unnamed pad shows its model", role(model, 0, b"label") == "Apex 5",
          str(role(model, 0, b"label")))

    changed = []
    model.dataChanged.connect(
        lambda _top, _bottom, roles: changed.append(set(roles)))
    model.devicesReceived([pad_entry(2, "Desk")])
    check("the name it was given is what the row says now",
          role(model, 0, b"label") == "Desk", str(role(model, 0, b"label")))
    check("and both roles a nickname feeds are named",
          changed == [{models.DevicesModel.NicknameRole,
                       models.DevicesModel.LabelRole}], str(changed))


def screen_cache_files():
    """The screen previews sitting in the cache directory, by path."""
    from PySide6.QtCore import QStandardPaths

    folder = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
    try:
        return {os.path.join(folder, name) for name in os.listdir(folder)
                if name.startswith("screen-preview-")}
    except OSError:
        return set()


def screen_frames_now(model):
    """What the model would send this instant, taken through `upload`.

    Only meaningful on a settled framing: sending is deliberately refused while
    an encode is outstanding, which is its own case below.
    """
    sent = []

    def took(frames, _interval, _restore):
        sent.append(tuple(frames))

    model.uploadRequested.connect(took)
    try:
        model.upload()
        model.uploadFinished(True)
    finally:
        model.uploadRequested.disconnect(took)
    return sent[-1] if sent else ()


def test_a_framing_is_encoded_somewhere_other_than_the_gui_thread():
    """The measured cost was 1.3 s a gesture for a 200-frame animation.

    That was a pure-Python per-pixel encode of every held frame plus a decode
    and a PNG write each, run from the plain slot `CropStage.qml` calls when a
    drag settles -- so the window stopped answering the mouse for over a second
    every time one ended.

    Two claims, and they are different ones. That the slot returns before the
    work is done is this model's contract. That the work really happens on
    another thread is `EncodeWorker`'s, and it is checked by watching where
    `CropFrame.render` is called from -- that is the expensive half, and the
    half that touches Qt.
    """
    from PySide6.QtCore import QStandardPaths, QThread
    from PySide6.QtGui import QImage

    from flydigi import screen as backend
    from gui.models.imaging import CropFrame
    from gui.models.screen import (STAGE_HEIGHT, STAGE_WIDTH, EncodeWorker,
                                   unlink_all)

    model, _ = screen_model_with(1)
    # Read behind the property on purpose: `upload` refuses while an encode is
    # outstanding, which is the very state this is looking at.
    held = list(model._frames)
    model.panBy(0, -10000)
    model.framingSettled()
    check("the gesture's slot returns with the work outstanding", model.encoding)
    check("and what is held is still the framing before the gesture",
          list(model._frames) == held)
    screen_settled(model)
    check("the frames arrive once the worker is done",
          list(model._frames) != held)

    class WatchingFrame(CropFrame):
        """Says which threads it was rendered from."""

        threads = set()

        def render(self, image):
            WatchingFrame.threads.add(QThread.currentThread())
            return super().render(image)

    picture = QImage(90, 70, QImage.Format_RGB888)
    picture.fill(0x204080)
    frame = WatchingFrame(backend.WIDTH, backend.HEIGHT,
                          STAGE_WIDTH, STAGE_HEIGHT)
    frame.set_natural(90, 70)
    worker = EncodeWorker(
        1, [picture], frame,
        QStandardPaths.writableLocation(QStandardPaths.CacheLocation))
    landed = []
    worker.done.connect(lambda _s, frames, paths: landed.append((frames, paths)))
    worker.start()
    for _ in range(500):
        if landed:
            break
        spin(10)
    worker.wait(2000)
    check("the worker encoded the frame", len(landed) == 1 and landed[0][0])
    check("and did it away from this thread",
          bool(WatchingFrame.threads)
          and QThread.currentThread() not in WatchingFrame.threads,
          str(WatchingFrame.threads))
    unlink_all(landed[0][1] if landed else [])


def test_the_last_gesture_is_the_one_that_lands():
    """A drag arriving mid-encode makes the running encode worthless.

    It is a picture of where the picture used to be, so it is dropped -- said
    here because dropping a result is a decision and not an accident. What must
    never happen is the dropped one landing *after* the one that replaced it:
    the pad would then be handed a framing nobody chose, with no further
    gesture coming to correct it.

    Driven without letting the event loop run between the two gestures, which
    is exactly the overlap -- the first worker is still going when the second
    framing is asked for.
    """
    from PySide6.QtCore import QUrl

    from gui.models.screen import unlink_all

    model, _ = screen_model_with(1)

    model.panBy(0, 10000)
    model.framingSettled()
    screen_settled(model)
    at_top = screen_frames_now(model)

    model.panBy(0, -10000)
    model.framingSettled()
    screen_settled(model)
    at_bottom = screen_frames_now(model)
    check("the two ends of the drag are different pictures", at_top != at_bottom)

    # Every model in this process names its previews from its own counter, so
    # earlier cases leave files behind that can share a name with this one's.
    # Swept first, so what is in the directory afterwards is this model's doing
    # and nothing else's.
    held = {QUrl(url).toLocalFile() for url in model.previewFrames}
    unlink_all(screen_cache_files() - held)
    before_files = screen_cache_files()

    model.panBy(0, 10000)
    model.framingSettled()
    model.panBy(0, -10000)
    model.framingSettled()
    screen_settled(model)

    check("the framing that landed is the one the gesture ended on",
          screen_frames_now(model) == at_bottom)
    check("and there is a preview for it", len(model.previewFrames) == 1,
          str(len(model.previewFrames)))

    # The overtaken worker writes PNGs before it notices, and nothing else knows
    # their names -- so an orphan here is a cache directory that grows by a file
    # per frame per drag.
    now = {QUrl(url).toLocalFile() for url in model.previewFrames}
    added = screen_cache_files() - before_files
    check("no preview outlived the gesture that asked for it", added == now,
          str(sorted(added - now)))
    check("and the framing before it took its own files with it",
          not any(os.path.exists(path) for path in held))


def test_sending_waits_for_the_framing_it_would_send():
    """The frames only change when a worker's answer lands.

    So between a gesture and its answer the model is holding the framing from
    *before* the gesture, and an upload started in that window would spend
    minutes putting the wrong crop on a pad that cannot be interrupted once it
    is across. The button is off for the duration instead.
    """
    model, _ = screen_model_with(1)
    check("ready with a settled framing", model.canUpload)

    model.panBy(0, -5000)
    model.framingSettled()
    check("not while the framing is being encoded", not model.canUpload)
    started = []
    model.uploadRequested.connect(lambda *a: started.append(a))
    model.upload()
    check("and calling upload() anyway sends nothing", started == [])
    check("nor does it leave the page stuck busy", not model.busy)

    screen_settled(model)
    check("ready again once the frames are the ones on screen", model.canUpload)


def test_the_screen_page_reads_fields_rather_than_recomputing_them():
    """One `changed` covers thirty-odd bindings, and a drag emits one per move.

    So a getter that computes is computed once per binding per pointer event.
    `previewFrames` built a fresh list of file URLs on every read -- 200 of them
    for a 200-frame animation -- and the four stage-geometry properties each
    recomputed the rendered size, which `canPan` then recomputed twice more.

    Counted rather than timed: `rendered_size` is where that arithmetic is, and
    reading the stage must not reach it at all.
    """
    model, _ = screen_model_with(1)

    calls = []
    real = model._frame.rendered_size
    model._frame.rendered_size = lambda: (calls.append(None) or real())
    try:
        for _ in range(20):
            _ = (model.imageX, model.imageY, model.imageDrawWidth,
                 model.imageDrawHeight, model.canPan, model.zoomLabel,
                 model.imageSource, model.sourceWidth, model.sourceHeight,
                 model.sourceName, model.estimate, model.previewFrames)
        check("reading the stage costs no arithmetic at all", not calls,
              str(len(calls)))

        was = model.imageY
        model.panBy(0, -10)
        # Three: `clamp` needs it to pull the picture back, then the refresh
        # takes it for the drawn size and `can_pan` asks again. Per pointer
        # event rather than per binding, which is the whole of the change.
        check("moving the picture is what pays for it", len(calls) == 3,
              str(len(calls)))
        check("and the stage followed", model.imageY != was)
    finally:
        del model._frame.rendered_size

    check("the preview list is handed out, not rebuilt",
          model.previewFrames is model.previewFrames)
    urls = model.previewFrames
    check("and it is the list of previews", len(urls) == 1
          and urls[0].startswith("file:"), str(urls))
    check("the estimate is a field too", model.estimate == "about 25 seconds",
          model.estimate)


def test_quitting_mid_encode_leaves_no_thread_running():
    """Dropping a running QThread is a qFatal, which is a core dump on quit.

    `gui/app.py`'s shutdown waits for the models that own threads and does not
    know about this one yet, so the model hears `aboutToQuit` itself. `wait` is
    the same call either way, and this is that call.
    """
    model, _ = screen_model_with(1)
    model.panBy(0, -10000)
    model.framingSettled()
    check("a worker is running", model._worker is not None)

    model.wait(5000)
    check("waiting leaves nothing behind", model._worker is None)
    check("and nothing claims to be encoding", not model.encoding)

    # A result that got out before the worker noticed is dropped rather than
    # applied to a model that has been told to stop.
    spin(50)
    check("the abandoned answer did not come back", not model.encoding)


def test_the_dock_swatches_are_rows_rather_than_a_rebuilt_list():
    """Editing one colour must not hand the Repeater a whole new model.

    The swatches used to be a list-valued property notified by the model-wide
    `lightingChanged`, so moving the brightness slider replaced the Repeater's
    model and destroyed every swatch delegate -- including the one under the
    pointer that had just opened a colour dialog. `EffectParamsModel` is the
    same fix for the same defect on the Triggers page, where it was worse: the
    knobs could not be dragged at all.
    """
    from PySide6.QtCore import QAbstractListModel

    model = models.DockModel()
    model.setSelector("uid:aa")
    model.modeIndex = models.MODE_NAMES.index("Solid")
    swatches = model.colours

    check("the swatches are a model, not a list",
          isinstance(swatches, QAbstractListModel))
    check("with a row per colour the effect reads",
          swatches.count == model.coloursUsed,
          f"{swatches.count} vs {model.coloursUsed}")

    resets = []
    moved = []
    swatches.modelAboutToBeReset.connect(lambda: resets.append(True))
    swatches.dataChanged.connect(
        lambda first, last, roles: moved.append((first.row(), last.row())))

    before = swatches.data(swatches.index(0, 0), swatches.ColourRole)
    model.setColour(0, "#123456")
    after = swatches.data(swatches.index(0, 0), swatches.ColourRole)
    check("editing a colour changes the row", after != before, f"{before} -> {after}")
    check("and says so with dataChanged", moved, str(moved))
    check("without rebuilding the list", not resets, str(resets))

    # A different effect really does have different rows, and that is the one
    # case a view cannot absorb any other way.
    resets.clear()
    model.modeIndex = models.MODE_NAMES.index("Diagonal flow")
    check("picking an effect that reads two colours gives it two rows",
          swatches.count == model.coloursUsed == 2,
          f"{swatches.count} vs {model.coloursUsed}")
    check("and that one is a reset, because they really are different rows",
          resets, str(resets))


def test_dragging_the_dock_picture_costs_the_stage_and_not_the_wedge():
    """A pointer move moves the picture; the LEDs wait for the drag to end.

    Following the pointer is four numbers. Re-reading what the *window* sees is
    a 334x304 canvas repainted and 162 colours sampled off it -- 0.868 ms a
    frame, measured -- and `_reframed` used to do both on every event. So a drag
    across the stage paid for the LED wedge once per pointer move, for a wedge
    whose final state is the only one anybody sees.

    `framingSettled` is the hook `CropStage.qml` calls when the drag is released
    or the zoom slider let go. It existed and had an empty body.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage

    path = os.path.join(tempfile.mkdtemp(), "wide.png")
    image = QImage(600, 200, QImage.Format_RGB888)
    image.fill(0x3366CC)
    image.save(path, "PNG")

    model = models.DockModel()
    model.setSelector("uid:aa")
    model.modeIndex = models.MODE_NAMES.index("Picture")
    check("the picture loaded", model.openImage(QUrl.fromLocalFile(path)))
    model.fit = models.dock.FIT_FILL
    model.zoom = 200

    framings = []
    previews = []
    model.framingChanged.connect(lambda: framings.append(True))
    model.previewChanged.connect(lambda: previews.append(True))

    for _ in range(12):
        model.panBy(-4, 0)
    check("every pointer move moves the stage", len(framings) == 12,
          str(len(framings)))
    check("and none of them resamples the wedge", not previews,
          str(len(previews)))

    model.framingSettled()
    check("letting go does", len(previews) == 1, str(len(previews)))

    # Reading the swatches is what actually takes the sample -- `framingSettled`
    # only says they are stale. Until something has read them the framing really
    # is unsampled, and saying so again is correct.
    model.frameColours
    previews.clear()
    model.framingSettled()
    check("and settling again on a framing already sampled is quiet",
          not previews, str(len(previews)))


def test_a_deferred_sample_is_never_the_framing_from_before_the_drag():
    """What makes deferring the resample safe rather than merely cheaper.

    The sampled frames are not recomputed while a drag is running, so between
    the first pointer move and the release there is a cache full of colours from
    a framing that is no longer true. Nothing may be handed those: `_sample`
    keys them on the framing they were taken at, and `_reframed` moves that on,
    so a reader arriving mid-drag pays for a fresh sample instead of being told
    a comfortable lie. Anything that reads the wedge without waiting for the
    release -- the preview timer, a test, a page rebuilt under the pointer --
    is that reader.
    """
    model, _loaded = dock_model_with("still")
    before = list(model.frameColours)

    # No `framingSettled` anywhere in here: the drag is still in progress as
    # far as this model knows.
    model.panBy(-80, 0)
    check("a read mid-drag is the framing it is at now, not the one it left",
          model.frameColours != before, "the colours did not move")

    mid = list(model.frameColours)
    model.zoom = 14
    check("and a zoom mid-gesture is no different",
          model.frameColours != mid, "the colours did not move")

    # The same guarantee from the other side: a frame sampled under this
    # framing is kept, and asking twice does not sample twice.
    sampled = []
    drawn = model._render
    model._render = lambda index: (sampled.append(index), drawn(index))[1]
    model.frameColours
    model.frameColours
    check("a framing that has not moved is sampled once and remembered",
          not sampled, str(sampled))


def dock_state(effect="Wave gradient", brightness=40, sleep=True):
    """One whole dock read, the shape `gui/worker.py` sends.

    `effect` is named rather than numbered because the wire mode and the
    dropdown index are different numbers, and mixing them up is the obvious way
    to write a test that asserts nothing.
    """
    mode = models.dock.MODES[models.MODE_NAMES.index(effect)][1]
    return {
        "selector": "uid:aa",
        "info": {"firmware": "0.0.3.9", "device_type": 0,
                 "sleep_when_charging": sleep, "led_sync": False,
                 "close_with_system": True,
                 "show_animation_when_charging": False},
        "uid": "aa" * 13, "nickname": "Shelf",
        "lighting": {"mode": mode, "brightness": brightness, "period": 3,
                     "direction": 0, "colours": [[0, 116, 255]]},
        "status": {"docked": True, "battery": 3}}


def test_a_switch_write_does_not_throw_away_an_unapplied_effect():
    """Flicking a switch must not undo the effect somebody just picked.

    Every switch write ends in a read of the whole dock -- the reply to a write
    says nothing about what it changed -- and that read landed on the lighting
    block as well, which arrives in the same packet and has nothing to do with
    the switch. So choosing an effect and then turning "Sleep while docked" on
    put the dropdown back to whatever the dock was still playing, with nothing
    on screen to say why.
    """
    model = models.DockModel()
    model.setSelector("uid:aa")
    model.stateReceived(dock_state("Wave gradient"))
    check("the dock's own effect is shown",
          model.modeIndex == models.MODE_NAMES.index("Wave gradient"),
          str(model.modeIndex))
    check("and nothing is unapplied yet", not model.lightingDirty)

    chosen = models.MODE_NAMES.index("Breath")
    model.modeIndex = chosen
    check("picking an effect is an unapplied change", model.lightingDirty)

    # The switch. Nothing reads the dock back for it any more -- see
    # `DeviceWorker.set_dock_switch` -- so the worst that can arrive is the
    # confirmation, which carries no lighting at all.
    model.setSwitch("sleep_when_charging", False)
    model.switchFinished("uid:aa", "sleep_when_charging", True)

    check("the switch took", not model.sleepWhenCharging)
    check("and the effect that was picked is still picked",
          model.modeIndex == chosen, str(model.modeIndex))
    check("still marked as not on the dock", model.lightingDirty)

    # And once it has been applied, the dock's version is the same version, so
    # the next read stops being something to defend against.
    model.stateReceived(dock_state("Breath"))
    check("an applied effect leaves nothing unapplied", not model.lightingDirty,
          str(model.modeIndex))
    check("and it is still the one that was picked", model.modeIndex == chosen,
          str(model.modeIndex))


def test_a_switch_the_dock_refuses_goes_back():
    """A page that moves the moment it is clicked has to be able to move back.

    Nothing reads the switches back: each one is its own command and the write
    raises unless the dock answers that command, so pass and fail are known
    where the write is made. What that buys has to be paid for here -- a
    failure the page is not told about leaves a switch showing a state the dock
    never took.
    """
    model = models.DockModel()
    model.setSelector("uid:aa")
    model.stateReceived(dock_state(sleep=True))
    check("the dock's own answer is shown", model.sleepWhenCharging)

    model.setSwitch("sleep_when_charging", False)
    check("the page moves at once", not model.sleepWhenCharging)
    model.switchFinished("uid:aa", "sleep_when_charging", False)
    check("and back, when the dock refused it", model.sleepWhenCharging)

    # A confirmation leaves it where the click put it.
    model.setSwitch("led_sync", True)
    model.switchFinished("uid:aa", "led_sync", True)
    check("a switch that landed stays where it was put", model.ledSync)
    # And a late failure for a switch already settled changes nothing.
    model.switchFinished("uid:aa", "led_sync", False)
    check("a second answer for it is not a second chance", model.ledSync)

    # A reply for a dock nobody is looking at is not this dock's business.
    model.setSwitch("close_with_system", False)
    model.switchFinished("uid:zz", "close_with_system", False)
    check("another dock's answer is ignored", not model.closeWithSystem)


def test_a_read_still_lands_when_nothing_was_edited():
    """The guard above must not turn into "the dock can never say anything".

    A dock has its own button, so its lighting can change while the app is
    watching. With nothing edited here, what it reports is the truth.
    """
    model = models.DockModel()
    model.setSelector("uid:aa")
    model.stateReceived(dock_state("Wave gradient", brightness=40))
    model.stateReceived(dock_state("Breath", brightness=90))
    check("the dock's new effect is shown",
          model.modeIndex == models.MODE_NAMES.index("Breath"),
          str(model.modeIndex))
    check("and its new brightness", model.brightness == 90, str(model.brightness))

    # A different dock is a different device: what was on screen belonged to
    # the one being left, however unapplied it was.
    model.modeIndex = models.MODE_NAMES.index("Rainbow")
    check("that is an unapplied change", model.lightingDirty)
    model.setSelector("uid:bb")
    model.stateReceived(dict(dock_state("Wave gradient"), selector="uid:bb"))
    check("choosing another dock shows that dock's lighting",
          model.modeIndex == models.MODE_NAMES.index("Wave gradient"),
          str(model.modeIndex))
    check("with nothing carried over", not model.lightingDirty)


def test_a_setting_the_pad_refuses_goes_back():
    """The pad's controls move as they are touched, so they have to move back.

    Unlike the dock's switches, these cannot skip the read: command 19 covers
    every one of them and its ack echoes the value without the sub-id, so a
    reply says nothing about which setting moved -- and a setting the pad does
    not support is acknowledged and changed anyway. Command 3 is the only
    honest answer, and it is one exchange returning exactly this page's state.

    What the read cannot do is arrive when the write failed. Until this existed
    a refused write left the toggle showing a state the pad never took, and only
    the *next* successful write put it right.
    """
    model = models.SettingsModel()
    model.stateReceived({"quick_switch": True, "quick_switch_usable": True,
                         "sleep_minutes": 15, "precision": 0, "sensitivity": 0})
    check("the pad's own answer is shown", model.quickSwitch)

    asked = []
    model.writeRequested.connect(lambda name, value: asked.append((name, value)))

    model.quickSwitch = False
    check("the page moves at once", not model.quickSwitch)
    check("and the write went out", asked == [("quick_switch", 0)], str(asked))
    model.writeFinished("quick_switch", False)
    check("and back, when the pad refused it", model.quickSwitch)

    # A successful write is corrected by the read that follows it, so the
    # confirmation only has to release the value.
    model.quickSwitch = False
    model.stateReceived({"quick_switch": False, "quick_switch_usable": True,
                         "sleep_minutes": 15, "precision": 0, "sensitivity": 0})
    model.writeFinished("quick_switch", True)
    check("a setting that landed stays landed", not model.quickSwitch)

    # The numbers too, not only the bits.
    model.sleepMinutes = 45
    check("the number moves at once", model.sleepMinutes == 45,
          str(model.sleepMinutes))
    model.writeFinished("sleep_minutes", False)
    check("and back when it is refused", model.sleepMinutes == 15,
          str(model.sleepMinutes))

    # And a read is the pad's whole truth, so it settles anything outstanding.
    model.sleepMinutes = 30
    model.stateReceived({"quick_switch": False, "quick_switch_usable": True,
                         "sleep_minutes": 30, "precision": 0, "sensitivity": 0})
    model.writeFinished("sleep_minutes", False)
    check("a failure after the pad has answered does not undo the answer",
          model.sleepMinutes == 30, str(model.sleepMinutes))


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
                 test_the_key_table_follows_the_pad_it_is_editing,
                 test_rename_reaches_the_config,
                 test_a_title_is_capped_at_what_the_pad_stores,
                 test_vibration_writes_through_to_the_blob,
                 test_vibration_keeps_min_below_max,
                 test_trigger_fields_are_independent,
                 test_each_effect_offers_its_own_controls,
                 test_a_page_of_properties_decodes_the_profile_once,
                 test_a_sweep_of_the_key_table_decodes_it_once_per_row,
                 test_the_macro_page_is_not_decoded_for_every_row_count,
                 test_an_effect_remembers_its_numbers_across_a_switch,
                 test_an_unknown_knob_is_refused_rather_than_stored,
                 test_no_trigger_motor_controls_are_offered,
                 test_a_stick_edit_recompiles_the_bank,
                 test_a_stick_edit_moves_the_curve_to_custom,
                 test_a_stick_bound_to_a_key_is_not_offered_a_curve,
                 test_circularity_is_not_part_of_the_curve,
                 test_the_gyro_starts_off_and_shows_the_keys_the_factory_left,
                 test_picking_a_stick_picks_the_motion_mode_with_it,
                 test_the_second_enable_key_is_offered_only_where_it_would_write,
                 test_the_gyro_sliders_are_the_ones_space_station_shows,
                 test_a_mouse_mapping_is_named_rather_than_shown_as_off,
                 test_the_gyro_editor_leaves_the_response_curve_alone,
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
                 test_dsmode_polls_only_while_it_is_asked_to,
                 test_dsmode_reports_a_cancelled_authentication,
                 test_setup_reports_ready_only_when_nothing_fails,
                 test_setup_asks_for_root_only_when_something_needs_it,
                 test_setup_keeps_running_and_starting_at_login_apart,
                 test_device_folds_in_an_info_reply,
                 test_an_info_reply_notifies_once_per_thing_that_moved,
                 test_a_profile_read_does_not_rebuild_macros_that_did_not_change,
                 test_the_third_party_gate_follows_firmware,
                 test_the_holder_is_reported_separately_from_the_switch,
                 test_flipping_the_switch_asks_the_worker,
                 test_device_reports_a_failure,
                 test_battery_is_clamped,
                 test_a_picture_is_encoded_as_it_is_loaded,
                 test_the_frames_handed_over_are_ones_the_pad_would_accept,
                 test_an_upload_in_flight_locks_everything_that_would_disturb_it,
                 test_changing_the_fit_changes_the_pixels,
                 test_an_upload_needs_a_cable,
                 test_the_screen_picture_can_be_dragged_under_the_panel,
                 test_a_screen_drag_re_encodes_when_it_ends_and_not_before,
                 test_every_frame_gets_a_preview_so_the_page_can_play_it,
                 test_the_screen_state_is_read_rather_than_assumed,
                 test_the_two_switches_are_different_sub_commands,
                 test_the_settings_block_fills_the_page,
                 test_the_pickers_index_by_resolution_and_write_by_wire_value,
                 test_a_switch_asks_the_worker_and_moves_at_once,
                 test_auto_calibration_is_unavailable_without_debounce,
                 test_an_unsupported_feature_is_reported_as_such_not_as_off,
                 test_the_device_list_shows_what_each_device_is,
                 test_choosing_a_pad_tells_the_daemon_and_choosing_a_dock_does_not,
                 test_a_pad_this_project_does_not_drive_never_becomes_the_daemon_s,
                 test_the_sidebar_is_told_what_the_selected_pad_can_do,
                 test_a_dock_nobody_picked_is_still_read,
                 test_the_selection_survives_a_pad_moving_to_another_node,
                 test_a_pad_that_goes_away_is_not_forgotten,
                 test_an_empty_bus_still_shows_the_pad_pages,
                 test_a_dock_switch_moves_at_once_and_asks_the_worker,
                 test_a_reply_for_a_dock_nobody_is_looking_at_is_dropped,
                 test_a_dock_effect_takes_its_own_defaults,
                 test_a_picture_is_framed_on_the_window_the_leds_are_read_from,
                 test_the_picture_cannot_be_dragged_off_the_window,
                 test_moving_the_picture_changes_the_leds,
                 test_an_animation_is_trimmed_the_way_space_stations_bar_trims,
                 test_the_preview_walks_the_trimmed_frames_and_wraps,
                 test_a_picture_is_applied_as_frames_rather_than_as_a_mode,
                 test_a_picture_that_is_not_there_is_not_uploaded,
                 test_a_huge_picture_is_not_kept_at_a_size_nothing_can_show,
                 test_the_playback_cursor_does_not_churn_the_framing,
                 test_dragging_the_dock_picture_costs_the_stage_and_not_the_wedge,
                 test_a_deferred_sample_is_never_the_framing_from_before_the_drag,
                 test_the_dock_swatches_are_rows_rather_than_a_rebuilt_list,
                 test_a_switch_write_does_not_throw_away_an_unapplied_effect,
                 test_a_switch_the_dock_refuses_goes_back,
                 test_a_setting_the_pad_refuses_goes_back,
                 test_a_read_still_lands_when_nothing_was_edited,
                 test_a_file_qt_cannot_read_says_so_rather_than_half_loading,
                 test_lighting_dirty_comes_back_down_as_well_as_up,
                 test_a_second_setup_reading_replaces_the_checklist,
                 test_the_launcher_command_is_worked_out_once,
                 test_a_reported_setup_worker_is_still_there_to_be_waited_on,
                 test_a_game_row_is_worked_out_once_rather_than_once_per_role,
                 test_a_search_matches_without_working_out_a_single_route,
                 test_a_battery_tick_moves_one_row_rather_than_rebuilding_the_list,
                 test_a_renamed_device_reports_the_name_the_picker_shows,
                 test_models_pull_in_no_view_code,
                 test_a_framing_is_encoded_somewhere_other_than_the_gui_thread,
                 test_the_last_gesture_is_the_one_that_lands,
                 test_sending_waits_for_the_framing_it_would_send,
                 test_the_screen_page_reads_fields_rather_than_recomputing_them,
                 test_quitting_mid_encode_leaves_no_thread_running):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
