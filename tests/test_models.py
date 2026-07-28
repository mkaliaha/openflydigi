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
    right.deadZone = 15

    mode, params = profile.config.trigger_effect("right")
    check("trigger effect reaches the config",
          mode == models.TRIGGER_MODES[1][1], str(mode))
    check("both knobs are kept", (params[0], params[1]) == (60, 200),
          str(params[:2]))
    check("dead zone reaches the curve",
          profile.config.trigger_curve("right")["zero"] == 15)
    # The motor enable is a single shared byte, so it lives on TriggerModel
    # rather than on a side -- see the dedicated test below.
    profile.triggers.motorEnabled = True
    check("motor reaches the config", profile.config.trigger_motor("right")[0])
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


def test_the_trigger_motors_share_one_enable():
    """One byte on the pad, so one switch -- not one per trigger.

    MappingConfig.trigger_motor computes a side-indexed base for min/max/scale
    but reads the enable from the un-indexed byte at OFF_TRIGGER_MOTOR. A
    per-side property over that would let a caller ask for left-on/right-off,
    read back agreement, and send both.
    """
    profile, _ = make_profile()
    check("the motors start off", not profile.triggers.motorEnabled)
    check("no per-side motor property exists",
          not hasattr(profile.triggers.side("left"), "motor"))

    profile.triggers.motorEnabled = True
    check("both sides report it on",
          profile.config.trigger_motor("left")[0]
          and profile.config.trigger_motor("right")[0])
    # The pad's switches are inverted -- 0 is on -- so this is ENABLED, not a
    # truthiness test.
    check("and it is the one shared byte",
          profile.config.blob[mapping.OFF_TRIGGER_MOTOR] == mapping.ENABLED,
          str(profile.config.blob[mapping.OFF_TRIGGER_MOTOR]))
    check("turning it on is a change", profile.dirty)


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


def test_click_feedback_toggles():
    model = make_lighting()
    model.clickFeedback = True
    check("react-to-rumble reaches the config", model._edited.click_feedback)
    check("toggling it marks dirty", model.dirty)


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
    {"id": 2, "enGameName": "Deathloop", "isPS5": True},
    {"id": 3, "enGameName": "Silksong", "isVibration": True},
    # Both flags, like Apex Legends and Uncharted: Lost Legacy -- the case that
    # makes the route a choice rather than a label.
    {"id": 4, "enGameName": "Two Ways", "isVibration": True, "isPS5": True},
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
    # Two: Silksong, and the two-route game while it is still on its tier.
    check("route filter works", view.count == 2, str(view.count))
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

    view.route = "ps5"
    check("a helper route names what to start",
          "start it alongside" in role(view, 0, b"detail"),
          str(role(view, 0, b"detail")))


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
    check("a route that takes the pad over does not",
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
    check("it starts on its tier", role(view, row, b"route") == "vibration",
          str(role(view, row, b"route")))
    check("so it can be applied from here", role(view, row, b"canApply") is True)
    check("and the chosen index points at it",
          role(view, row, b"chosenRouteIndex") == 0)

    view.setRouteIndexAt(row, 1)
    check("choosing the other route takes effect",
          role(view, row, b"route") == "ps5", str(role(view, row, b"route")))
    check("the row stops offering to load a preset",
          role(view, row, b"canApply") is False)
    check("its description follows the choice",
          "start it alongside" in role(view, row, b"detail"),
          str(role(view, row, b"detail")))
    check("and auto withdraws with it", role(view, row, b"auto") is False)

    # A route the game does not have must not be reachable from a view.
    view.setRouteIndexAt(row, 7)
    check("an out-of-range choice is ignored",
          role(view, row, b"route") == "ps5", str(role(view, row, b"route")))


def test_the_route_filter_follows_the_chosen_route():
    source, view = make_games()
    view.route = "ps5"
    check("the two-route game is not there under its tier",
          row_for(view, "Two Ways") < 0, str(view.count))

    view.route = models.ALL_ROUTES
    view.setRouteIndexAt(row_for(view, "Two Ways"), 1)
    view.route = "ps5"
    check("after choosing, it filters as the route it now takes",
          row_for(view, "Two Ways") >= 0, str(view.count))


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
    check("battery steps are eight", device.batterySteps == 8)
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


def main():
    QCoreApplication.instance() or QCoreApplication([])
    for test in (test_selecting_an_unread_profile_requests_it_once,
                 test_a_loaded_profile_is_clean,
                 test_editing_a_key_marks_dirty_and_lands_in_the_blob,
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
                 test_the_trigger_motors_share_one_enable,
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
                 test_click_feedback_toggles,
                 test_colour_list_respects_what_the_effect_allows,
                 test_rainbow_and_off_use_no_colours,
                 test_colours_cross_the_boundary_as_hex,
                 test_lighting_write_and_confirm,
                 test_game_list_and_filters,
                 test_only_the_pad_side_route_can_be_applied,
                 test_route_wording_does_not_oversell_the_preset,
                 test_auto_defaults_follow_the_route,
                 test_toggling_auto_is_saved_and_announced,
                 test_only_multi_route_games_offer_a_choice,
                 test_choosing_a_route_changes_what_the_row_says,
                 test_the_route_filter_follows_the_chosen_route,
                 test_setup_reports_ready_only_when_nothing_fails,
                 test_setup_asks_for_root_only_when_something_needs_it,
                 test_setup_keeps_running_and_starting_at_login_apart,
                 test_device_folds_in_an_info_reply,
                 test_the_third_party_gate_follows_firmware,
                 test_the_holder_is_reported_separately_from_the_switch,
                 test_flipping_the_switch_asks_the_worker,
                 test_device_reports_a_failure,
                 test_battery_is_clamped,
                 test_models_pull_in_no_view_code):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
