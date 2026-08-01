#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for mapping profiles. No controller required -- see fake_pad.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import (device, effects, factory_config, identity, lighting,
                     mapping)
from tests.fake_pad import BLOB_LEN, FakePad, blank_blob

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def test_packet_framing():
    """The length byte counts itself and the command, so the checksum lands at 3+len."""
    buf = mapping.build(mapping.CMD_READ, bytes([2, 20]))
    check("framing: report id", buf[0] == device.REPORT_ID_OUT)
    check("framing: command", buf[3] == mapping.CMD_READ)
    check("framing: length counts cmd+len+payload", buf[4] == 4)
    expected = device.checksum(buf, 3, 3 + buf[4])
    check("framing: checksum position and value", buf[7] == expected,
          f"got {buf[7]}, expected {expected}")


def test_identity_and_remap():
    config = mapping.MappingConfig(blank_blob())
    check("default reads as itself", config.mapping("a")[0] == "a")
    check("nothing remapped by default", config.remapped() == {})

    config.set_mapping("m1", "a")
    check("remap takes effect", config.mapping("m1")[0] == "a")
    check("remap is reported", config.remapped() == {"m1": ("a", 0, 0)})

    config.set_mapping("m1", None)
    check("clearing restores default", config.mapping("m1")[0] == "m1")
    check("cleared key is not reported", config.remapped() == {})

    # Mapping a key to itself must be stored as the identity sentinel, not as
    # its own id, or the pad treats it as a real remap.
    config.set_mapping("b", "b")
    offset = mapping.OFF_KEY_TABLE + mapping.KEY_IDS["b"] * mapping.KEY_ENTRY
    check("self-map stored as identity sentinel",
          config.blob[offset] == mapping.TARGET_IDENTITY,
          f"stored {config.blob[offset]}")


def test_turbo():
    config = mapping.MappingConfig(blank_blob())
    config.set_mapping("rb", "rb", mapping.TURBO_TOGGLE, 12)
    target, mode, frequency = config.mapping("rb")
    check("turbo target", target == "rb")
    check("turbo mode round-trips", mode == mapping.TURBO_TOGGLE)
    check("turbo frequency round-trips", frequency == 12)
    check("turbo counts as remapped", "rb" in config.remapped())

    # Turbo needs a concrete target: identity has no id to repeat.
    offset = mapping.OFF_KEY_TABLE + mapping.KEY_IDS["rb"] * mapping.KEY_ENTRY
    check("turbo stores a real key id",
          config.blob[offset] == mapping.KEY_IDS["rb"])

    config.set_mapping("rb", None)
    check("clearing removes turbo", config.mapping("rb")[2] == 0)


def test_title():
    config = mapping.MappingConfig(blank_blob())
    config.title = "Racing"
    check("title round-trips", config.title == "Racing", repr(config.title))
    config.title = "十文字以上のとても長い名前"
    check("over-long title is truncated, not overflowing",
          len(config.blob) == BLOB_LEN)


def test_read_write_round_trip():
    pad = FakePad()
    config = mapping.read_config(pad, 0)
    check("read returns a full blob", len(config.blob) == BLOB_LEN,
          f"got {len(config.blob)}")
    check("read reports its slot", config.cfg_id == 0)

    edited = config.copy()
    edited.set_mapping("m2", "y")
    sent = mapping.write_config(pad, 0, edited, old=config)
    check("one remap sends one packet", sent == 1, f"sent {sent}")

    back = mapping.read_config(pad, 0)
    check("write round-trips", back.remapped() == {"m2": ("y", 0, 0)},
          str(back.remapped()))
    check("nothing else changed", bytes(back.blob) == bytes(edited.blob))


def test_write_without_baseline_sends_everything():
    pad = FakePad()
    config = mapping.read_config(pad, 1)
    pad.packets_received = 0
    sent = mapping.write_config(pad, 1, config, old=None)
    check("no baseline means a full write", sent == BLOB_LEN // mapping.PKG_SIZE,
          f"sent {sent}")


def test_unchanged_write_sends_nothing():
    pad = FakePad()
    config = mapping.read_config(pad, 0)
    sent = mapping.write_config(pad, 0, config, old=config)
    check("identical config sends no packets", sent == 0, f"sent {sent}")


def test_apply_and_save():
    pad = FakePad()
    check("apply is acknowledged", mapping.apply_config(pad, 2) is True)
    check("apply switches the pad", pad.active == 2)
    check("save is acknowledged", mapping.save_config(pad) is True)
    check("save captures every slot", len(pad.saved) == 4)


def test_a_models_keys_are_its_own():
    """Which buttons exist is a property of the pad, not a constant.

    The Apex 5 has no C and no Z; every Vader declares both. Getting this wrong
    is not cosmetic -- anything that iterates "every key" to reset or report
    them would silently skip two on a Vader and call it done.
    """
    check("the Apex 5 keeps exactly the keys it had",
          mapping.keys_for("k5") == mapping.APEX5_KEYS,
          str(mapping.keys_for("k5")))
    check("a Vader adds C and Z and nothing else",
          set(mapping.keys_for("f5")) - set(mapping.APEX5_KEYS) == {"c", "z"},
          str(mapping.keys_for("f5")))
    check("and loses none of them",
          not set(mapping.APEX5_KEYS) - set(mapping.keys_for("f5")))
    check("an unknown model falls back rather than reporting no keys",
          mapping.keys_for("k9") == mapping.APEX5_KEYS)

    # Sources, not targets: C and Z have no XInput equivalent, exactly like the
    # M paddles, so a remap may come *from* them and never point *at* them.
    check("C and Z are not remap targets",
          not {"c", "z"} & set(mapping.targets_for("f5")),
          str(mapping.targets_for("f5")))
    check("the Apex 5's target list is the one it always had",
          mapping.targets_for("k5") == mapping.XINPUT_TARGETS)

    # The trap avoided: the list is presentation order, and an offset comes
    # from KEY_IDS. If these ever diverge, every key on the pad moves.
    config = mapping.MappingConfig(blank_blob())
    for key in mapping.keys_for("f5"):
        offset, key_id = config._entry(key)
        check(f"{key} sits where its id says, not where the list does",
              key_id == mapping.KEY_IDS[key]
              and offset == mapping.OFF_KEY_TABLE + key_id * mapping.KEY_ENTRY,
              f"{key}: {offset}/{key_id}")


def test_a_save_carries_the_version_it_is_given():
    """Command 166 writes its argument into the slot's version tag.

    `read_status` reports that tag, and it is how a caller tells whether a
    cached copy of a config is still current. So the argument is not
    decoration: saving with the default 0 answers "has this changed?" with a
    lie, for every later reader.
    """
    pad = FakePad()
    config = mapping.read_config(pad, 0)
    mapping.save_config(pad, config.data_version)
    check("the config's own id reaches the pad",
          pad.saved_version == config.data_version,
          f"{pad.saved_version} vs {config.data_version}")

    mapping.save_config(pad)
    check("and the default really does overwrite it with zero",
          pad.saved_version == 0, str(pad.saved_version))


def test_nothing_saves_a_profile_without_its_version():
    """The defect this pair exists to catch, across the whole repository.

    `tools/flydigi-mapping --save` passed no version while `gui/worker.py`
    passed one, so the same operation left a correct tag from the app and a
    zeroed one from the command line. Two call sites and one of them wrong is
    not something a unit test of `save_config` can see, so this reads the
    callers.

    The lighting write is the one that looks like an exception and is not. It
    shares this command because the pad commits its working set rather than one
    config at a time, and a lighting blob carries no version of its own -- so it
    asks command 161 for the running profile's tag and commits that, rather than
    zeroing a field that belongs to something it is not editing.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    for folder in ("flydigi", "gui", "tools"):
        for dirpath, _dirs, files in os.walk(os.path.join(root, folder)):
            if "__pycache__" in dirpath:
                continue
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    with open(path) as fh:
                        body = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if "def save_config" in body:
                    continue        # the definition itself
                for line in body.splitlines():
                    if "save_config(ctrl)" in line or "save_config(pad)" in line:
                        offenders.append(f"{os.path.relpath(path, root)}: "
                                         f"{line.strip()}")
    check("every caller says which version it is committing",
          not offenders, "; ".join(offenders))


def test_reading_switches_the_pad():
    """The fact the rest of this file's behaviour hangs off."""
    pad = FakePad()
    mapping.read_config(pad, 3)
    check("a plain read leaves the pad on what it read", pad.active == 3,
          f"active {pad.active}")
    check("status agrees", mapping.read_status(pad)["active"] == 3)


def test_browsing_puts_the_pad_back():
    pad = FakePad()
    mapping.apply_config(pad, 1)
    config, restored = mapping.read_config_preserving(pad, 3)

    check("the browsed config is the one returned", config.cfg_id == 3)
    check("the pad is back where it was", pad.active == 1, f"active {pad.active}")
    check("the restore is reported", restored == 1, str(restored))


def test_browsing_the_running_profile_restores_nothing():
    pad = FakePad()
    mapping.apply_config(pad, 2)
    _config, restored = mapping.read_config_preserving(pad, 2)
    check("no restore when it was already running", restored is None, str(restored))
    check("and the pad has not moved", pad.active == 2)


def test_a_failed_browse_still_puts_the_pad_back():
    """The bug this guards: the pad switches on the first read packet.

    `read_blob` only raises once every retry has failed, by which time the pad
    has been paged over to the browsed config three times. Without the restore
    in a `finally` the caller gets an exception and the pad silently keeps the
    profile it was only meant to peek at -- and a retry then launders it, since
    the next status read truthfully reports that slot as active and the restore
    is skipped as unnecessary.
    """
    pad = FakePad()
    mapping.apply_config(pad, 1)
    pad.fail_reads = True

    raised = False
    try:
        mapping.read_config_preserving(pad, 3, wait=0.0)
    except mapping.ProtocolError:
        raised = True

    check("a failed read is still an error", raised)
    check("the read really did reach the pad", pad.reads_answered > 0,
          f"{pad.reads_answered} reads")
    check("the pad is back where it was", pad.active == 1, f"active {pad.active}")

    # And the laundering it used to enable: a second attempt would find the pad
    # already on the browsed slot and decide there was nothing to restore.
    pad.fail_reads = False
    _config, restored = mapping.read_config_preserving(pad, 3)
    check("the retry still knows where to go back to", restored == 1, str(restored))
    check("and goes there", pad.active == 1, f"active {pad.active}")


def test_bad_checksum_is_rejected():
    """The fake pad refuses a bad checksum exactly as the real one does."""
    pad = FakePad()
    buf = mapping.build(mapping.CMD_APPLY, bytes([1]))
    buf[6] ^= 0xFF                      # corrupt the checksum
    check("corrupt packet gets no reply", pad.send(buf) == [])
    check("corruption was noticed", pad.bad_checksums == 1)
    check("corrupt packet did not switch the pad", pad.active == 0)


def test_vibration_intensity():
    config = mapping.MappingConfig(blank_blob())
    config.set_vibration("left", enabled=True, minimum=40, maximum=200, scale=90)
    check("vibration round-trips", config.vibration("left") == (True, 40, 200, 90),
          str(config.vibration("left")))
    check("sides are independent", config.vibration("right") != (True, 40, 200, 90))

    # A slider dragged past its partner must not produce an inverted window.
    config.set_vibration("left", minimum=250)
    enabled, minimum, maximum, _scale = config.vibration("left")
    check("min and max are kept in order", minimum <= maximum,
          f"min {minimum} max {maximum}")

    config.set_vibration("left", enabled=False)
    check("disabling is stored inverted",
          config.blob[mapping.OFF_GRIP_VIBRATION + 1] == mapping.DISABLED)
    check("disabled reads back as disabled", config.vibration("left")[0] is False)

    config.vibration_enabled = False
    check("master switch round-trips", config.vibration_enabled is False)


def test_trigger_effect_and_curve():
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_effect("right", 1, [80, 200])
    mode, params = config.trigger_effect("right")
    check("trigger mode round-trips", mode == 1)
    check("trigger params round-trip", params[:2] == [80, 200], str(params[:2]))
    check("left trigger untouched", config.trigger_effect("left")[0] != 1)

    config.set_trigger_curve("left", zero=25)
    check("dead zone round-trips", config.trigger_curve("left")["zero"] == 25)

    config.set_trigger_motor("left", enabled=True, minimum=10, maximum=90,
                             scale=70, block=25)
    check("trigger motor round-trips",
          config.trigger_motor("left") == {"enabled": True, "minimum": 10,
                                           "maximum": 90, "scale": 70,
                                           "block": 25},
          str(config.trigger_motor("left")))


def test_the_trigger_motor_fields_land_where_flydigi_puts_them():
    """Four of the seven bytes in the first gear, at their own offsets.

    Getting one of these wrong is invisible in a round trip -- read and write
    would agree with each other and disagree with the pad -- so this asserts
    against the layout `ParseTriggerConfigToArray` writes.
    """
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_motor("right", enabled=True, minimum=20, maximum=200,
                             scale=60, block=30)
    base = mapping.OFF_TRIGGER_MOTOR + 1 + 14        # right side's first gear
    check("the enable is the one shared byte, not a per-side one",
          config.blob[mapping.OFF_TRIGGER_MOTOR] == mapping.ENABLED)
    check("min, max, filter and scale are at +1, +2, +3 and +5",
          [config.blob[base + 1], config.blob[base + 2], config.blob[base + 3],
           config.blob[base + 5]] == [20, 200, 30, 60],
          str(list(config.blob[base : base + 7])))
    check("the second gear is untouched -- Flydigi never writes it either",
          set(config.blob[base + 7 : base + 14]) == {0xFF},
          str(list(config.blob[base + 7 : base + 14])))
    check("and the left trigger's block is untouched",
          config.trigger_motor("left")["scale"] == 0xFF,
          str(config.trigger_motor("left")))


def test_the_motor_strength_is_a_percentage_not_a_byte():
    """`SaveTriggerVibrationConfig` assigns the slider's 1..100 straight in,
    while the amplitude pair beside it is that slider's percent scaled to a
    byte. Clamping strength at 255 would offer two and a half times the field."""
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_motor("left", scale=200, minimum=200)
    check("strength is clamped to its own maximum",
          config.trigger_motor("left")["scale"] == mapping.TRIGGER_MOTOR_SCALE_MAX,
          str(config.trigger_motor("left")["scale"]))
    check("the amplitude beside it is not",
          config.trigger_motor("left")["minimum"] == 200,
          str(config.trigger_motor("left")["minimum"]))


def test_the_amplitude_window_cannot_be_inverted():
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_motor("left", minimum=30, maximum=200)
    config.set_trigger_motor("left", minimum=250)
    motor = config.trigger_motor("left")
    check("an inverted window is put back the right way round",
          motor["minimum"] <= motor["maximum"],
          f"min {motor['minimum']} max {motor['maximum']}")


def test_the_labels_follow_space_station_not_the_enum():
    """Modes 2 and 3 are named the other way round by Flydigi's own UI, and the
    behaviour follows the label -- 2 rattles, 3 breaks through. Locked down
    because it reads like a bug: `effects.sniper` is the effect labelled
    "Recoil", and correcting that to match the enum would send anyone following
    a Flydigi recommendation to the wrong effect."""
    labels = {e.mode: e.label for e in effects.EFFECTS}
    keys = {e.mode: e.key for e in effects.EFFECTS}
    check("mode 2 is keyed sniper and labelled Recoil",
          (keys[2], labels[2]) == ("sniper", "Recoil"), str((keys[2], labels[2])))
    check("mode 3 is keyed recoil and labelled Sniper",
          (keys[3], labels[3]) == ("recoil", "Sniper"), str((keys[3], labels[3])))
    check("the other four agree with themselves",
          [labels[m] for m in (0, 1, 4, 5)]
          == ["General", "Racing", "Trigger lock", "Vibration"],
          str([labels[m] for m in (0, 1, 4, 5)]))


def test_every_effect_round_trips_through_the_profile():
    """All six of Flydigi's effects, not just the two with obvious knobs."""
    for effect in effects.EFFECTS:
        config = mapping.MappingConfig(blank_blob())
        untouched = (config.trigger_effect("left"), config.trigger_bind("left"))
        wanted = effects.defaults(effect.mode)
        params, bind = effects.stored(effect.mode, wanted)
        config.set_trigger_effect("right", effect.mode, params, bind)

        mode, stored_params = config.trigger_effect("right")
        check(f"{effect.key}: mode round-trips", mode == effect.mode, str(mode))
        got = effects.values(mode, stored_params, config.trigger_bind("right"))
        check(f"{effect.key}: every knob round-trips", got == wanted,
              f"{got} != {wanted}")
        check(f"{effect.key}: the other trigger is untouched",
              (config.trigger_effect("left"), config.trigger_bind("left"))
              == untouched, str(config.trigger_bind("left")))


def test_the_effect_block_matches_flydigis_layout():
    """Byte for byte against ControllerRepository.SaveTriggerAdapterConfig.

    The slots an effect does not use are not free space -- Lock's strength and
    Vibration's pair are written as constants by Flydigi's own writer, and a
    profile that leaves the previous effect's numbers there is not the profile
    their software would have written.
    """
    config = mapping.MappingConfig(blank_blob())
    base = mapping.OFF_FORCE_TRIGGER

    config.set_trigger_effect("left", *(
        (effects.MODE_SNIPER,) + effects.stored(effects.MODE_SNIPER, {
            "start": 40, "press": 30, "strength": 20, "frequency": 10,
            "match_input": 1})))
    check("sniper fills five slots in order",
          list(config.blob[base + 10 : base + 15]) == [40, 30, 20, 10, 1],
          str(list(config.blob[base + 10 : base + 15])))

    config.set_trigger_effect("left", *(
        (effects.MODE_RECOIL,) + effects.stored(effects.MODE_RECOIL, {
            "start": 40, "travel": 30, "resistance": 20, "match_input": 0})))
    check("recoil leaves slot 3 empty and matches in slot 4",
          list(config.blob[base + 10 : base + 15]) == [40, 30, 20, 0, 0],
          str(list(config.blob[base + 10 : base + 15])))

    config.set_trigger_effect("left", *(
        (effects.MODE_LOCK,) + effects.stored(effects.MODE_LOCK, {"start": 90})))
    check("lock writes its fixed strength and flag",
          list(config.blob[base + 10 : base + 15]) == [90, 255, 1, 0, 0],
          str(list(config.blob[base + 10 : base + 15])))

    config.set_trigger_effect("left", *(
        (effects.MODE_VIBRATION,) + effects.stored(effects.MODE_VIBRATION, {
            "scale": 60, "block": 12, "stroke": 44, "frequency": 30})))
    check("vibration marks the block as bound", config.blob[base + 1] == 2,
          str(config.blob[base + 1]))
    check("vibration fills the binding half",
          config.trigger_bind("left") == (12, 60, [44, 1, 1, 30, 0]),
          str(config.trigger_bind("left")))
    check("vibration's own slots carry the stroke and frequency",
          list(config.blob[base + 10 : base + 15]) == [44, 30, 1, 90, 0],
          str(list(config.blob[base + 10 : base + 15])))

    config.set_trigger_effect("left", effects.MODE_RACE, [50, 40])
    check("leaving the vibration effect clears the bind marker",
          config.blob[base + 1] == 0, str(config.blob[base + 1]))
    check("but keeps the binding itself, as Flydigi's writer does",
          config.trigger_bind("left") == (12, 60, [44, 1, 1, 30, 0]),
          str(config.trigger_bind("left")))


def test_switching_to_general_keeps_the_numbers():
    """General has no knobs, so it has nothing to write -- zeroing the slots
    would throw away a tuned effect for someone toggling it off and on."""
    config = mapping.MappingConfig(blank_blob())
    params, bind = effects.stored(effects.MODE_RACE,
                                  {"start": 77, "resistance": 88})
    config.set_trigger_effect("right", effects.MODE_RACE, params, bind)

    none_params, none_bind = effects.stored(effects.MODE_NORMAL, {})
    check("General asks for no parameters at all",
          none_params is None and none_bind is None)
    config.set_trigger_effect("right", effects.MODE_NORMAL, none_params, none_bind)
    check("the effect really is off", config.trigger_effect("right")[0] == 0)
    check("and the racing numbers survived",
          config.trigger_effect("right")[1][:2] == [77, 88],
          str(config.trigger_effect("right")[1][:2]))


def test_an_effect_reads_its_own_defaults_out_of_a_foreign_slot():
    """Every effect shares the same ten slots, so a slot the previous effect
    used differently must not read back as a setting of this one.

    Only a value out of range can be caught: Lock's fixed 1 in slot 2 is a
    perfectly legal Sniper strength and survives as one. That is a limit of
    the storage, not of the reading -- the pad keeps no record of which effect
    wrote a slot.
    """
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_effect("right", *(
        (effects.MODE_LOCK,) + effects.stored(effects.MODE_LOCK, {"start": 90})))
    _mode, params = config.trigger_effect("right")

    got = effects.values(effects.MODE_SNIPER, params, config.trigger_bind("right"))
    defaults = effects.defaults(effects.MODE_SNIPER)
    check("a start position in range is kept", got["start"] == 90, str(got))
    check("a frequency that was never written falls back to its default",
          got["frequency"] == defaults["frequency"], str(got))
    check("a travel of 255 is not offered as a start position",
          effects.values(effects.MODE_RACE, [255] * 10)["start"]
          == effects.defaults(effects.MODE_RACE)["start"],
          str(effects.values(effects.MODE_RACE, [255] * 10)))


def test_live_effect_payloads_match_the_command_builders():
    """The wire form of each effect, against SetForceTriggerCommandFactory."""
    class Recorder:
        def __init__(self):
            self.sent = []

        def command(self, cmd_id, payload=b"", wait=0.3, until=None):
            self.sent.append((cmd_id, list(payload)))
            return []

        def send(self, buf, wait=0.3, until=None):
            self.sent.append((buf[3], list(buf[5 : 5 + buf[4]])))
            return []

        @staticmethod
        def ack_ok(_reply, _cmd_id):
            return True

    pad = Recorder()
    effects.sniper(pad, device.SIDE_RIGHT, 40, 30, 20, 10, match_stroke=True)
    check("sniper's wire form", pad.sent[-1] == (81, [1, 2, 2, 40, 30, 20, 10, 1]),
          str(pad.sent[-1]))

    effects.recoil(pad, device.SIDE_RIGHT, 40, 30, 20, match_stroke=False)
    check("recoil carries an empty slot before the match flag",
          pad.sent[-1] == (81, [1, 2, 3, 40, 30, 20, 0, 0]), str(pad.sent[-1]))

    effects.lock(pad, device.SIDE_LEFT, 90)
    check("lock's wire form", pad.sent[-1] == (81, [1, 1, 4, 90, 255, 1]),
          str(pad.sent[-1]))

    # Byte for byte the sniper packet with a different mode, which is what
    # makes the pair worth asserting: on hardware mode 2 vibrates and what
    # mode 5 does is still unresolved (PROTOCOL.md 3a), so the packets being
    # identical bar the mode byte is the fact any future test rests on.
    effects.vibration(pad, device.SIDE_LEFT, 40, 30, 20, 10)
    check("vibration is sniper's packet with mode 5",
          pad.sent[-1] == (81, [1, 1, 5, 40, 30, 20, 10, 1]), str(pad.sent[-1]))
    effects.sniper(pad, device.SIDE_LEFT, 40, 30, 20, 10)
    check("and the two differ in that byte alone",
          pad.sent[-1][1][2] == 2 and pad.sent[-2][1][2] == 5
          and pad.sent[-1][1][3:] == pad.sent[-2][1][3:], str(pad.sent[-2:]))

    # Flydigi's builders raise a zero to one rather than refusing the packet,
    # so a caller that sends 0 gets the weakest setting, not silence.
    effects.sniper(pad, device.SIDE_LEFT, 0, 0, 0, 0)
    check("zero knobs come out as one",
          pad.sent[-1] == (81, [1, 1, 2, 0, 1, 1, 1, 1]), str(pad.sent[-1]))


class _Recorder:
    """A pad that only remembers what was sent to it."""

    def __init__(self):
        self.sent = []

    def command(self, cmd_id, payload=b"", wait=0.3, until=None):
        self.sent.append((cmd_id, list(payload)))
        return []

    def send(self, buf, wait=0.3, until=None):
        self.sent.append((buf[3], list(buf[5 : 5 + buf[4]])))
        return []

    @staticmethod
    def ack_ok(_reply, _cmd_id):
        return True


def test_a_stored_effect_is_replayed_as_the_live_command_flydigi_builds():
    """`engage_stored`, against ControllerRepository.CreateForceAdapterConfig.

    A stored effect does nothing until a live 81 starts it -- measured with Lock
    written into the blob and applied, which left the triggers loose. So the
    replay is the feature, and what it puts on the wire is the whole of it.
    """
    cases = [
        (effects.MODE_NORMAL, [0] * 10, (81, [1, 1, 0])),
        (effects.MODE_RACE, [40, 200, 0, 0, 1], (81, [1, 1, 1, 40, 200, 1])),
        (effects.MODE_SNIPER, [40, 30, 20, 10, 1],
         (81, [1, 1, 2, 40, 30, 20, 10, 1])),
        # Param[3] is not a knob this effect has, and Param[4] is the flag.
        (effects.MODE_RECOIL, [40, 30, 20, 0, 0],
         (81, [1, 1, 3, 40, 30, 20, 0, 0])),
        # Lock's strength is a literal 255 and its match flag a literal 1, not
        # Param[1] and Param[4] -- Flydigi hardcodes both.
        (effects.MODE_LOCK, [90, 255, 1, 0, 0], (81, [1, 1, 4, 90, 255, 1])),
    ]
    for mode, params, expected in cases:
        config = mapping.MappingConfig(blank_blob())
        config.set_trigger_effect("left", mode, params)
        pad = _Recorder()
        effects.engage_stored(pad, config, wait=0)
        name = effects.effect(mode).key
        check(f"{name} replays as its live command",
              pad.sent[0] == expected, str(pad.sent[0]))
        check(f"{name} goes out once per trigger, never side 3",
              len(pad.sent) == 2 and pad.sent[1][1][1] == device.SIDE_RIGHT,
              str(pad.sent))

    # Stored type 5 is the odd one: it becomes SyncWithGrip (82) built from the
    # *bind* half of the block, with the bind type Flydigi always passes.
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_effect("left", effects.MODE_VIBRATION, [50, 20, 1, 90, 0],
                              bind=(10, 60, [50, 1, 1, 20, 0]))
    pad = _Recorder()
    effects.engage_stored(pad, config, wait=0)
    check("stored Vibration replays as command 82, not a mode-5 81",
          pad.sent[0][0] == 82, str(pad.sent[0]))
    check("and it carries the bind half, with bindType 2",
          pad.sent[0][1][:8] == [1, 2, 10, 60, 50, 1, 1, 20], str(pad.sent[0]))


def flydigi_motion_bytes(target, key1, enable_type, dead_zone, sensitivity,
                         use_mode, key2):
    """`MappingConfigParser.ParseMotionConfigToArray`, joystick branch.

    Their writer fills the 8 bytes with 0xFF and then assigns every one of
    them, so a fully specified `set_motion` has to land on the same array.
    """
    data = [0xFF] * 8
    data[0] = target
    data[2] = enable_type
    data[4] = sensitivity
    data[5] = sensitivity
    data[6] = use_mode
    data[1] = key1
    data[3] = dead_zone
    data[7] = key2
    return data


def test_the_factory_motion_block_is_off_but_not_blank():
    config = mapping.MappingConfig(blank_blob())
    motion = config.motion()
    check("the gyro ships off", motion["target"] == mapping.MOTION_OFF, str(motion))
    check("with a dead-zone offset of 4", motion["dead_zone"] == 4, str(motion))
    check("and Lt sitting in the first enable-key byte",
          motion["keys"][0] == "lt", str(motion))
    # The trap set_motion exists to avoid: 0 there is a key, not an absence.
    check("and D-pad Up in the second, which is not the same as no key",
          motion["keys"][1] == "up", str(motion))
    check("the two sensitivity axes ship different",
          motion["sensitivity_xy"] == (25, 20), str(motion))
    check("and collapse the way Flydigi's reader collapses them",
          motion["sensitivity"] == 25, str(motion))


def test_the_motion_block_matches_flydigis_writer():
    config = mapping.MappingConfig(blank_blob())
    config.set_motion(target="right stick", enable_type="hold", keys=("m1", "m2"),
                      sensitivity=70, dead_zone=15)
    written = list(config.blob[mapping.OFF_MOTION : mapping.OFF_MOTION + 8])
    expected = flydigi_motion_bytes(
        mapping.MOTION_RIGHT_STICK, mapping.KEY_IDS["m1"], mapping.MOTION_PRESS,
        15, 70, mapping.MOTION_FPS, mapping.KEY_IDS["m2"])
    check("every byte lands where Flydigi puts it", written == expected,
          f"{written} != {expected}")

    motion = config.motion()
    check("and reads back as it was set",
          (motion["target"], motion["enable_type"], motion["keys"],
           motion["sensitivity"], motion["dead_zone"])
          == (mapping.MOTION_RIGHT_STICK, mapping.MOTION_PRESS, ("m1", "m2"),
              70, 15), str(motion))
    check("one number reaches both axes", motion["sensitivity_xy"] == (70, 70),
          str(motion))

    # Both sliders stop at 100 in Space Station, and the fields are bytes.
    config.set_motion(sensitivity=400, dead_zone=400)
    motion = config.motion()
    check("sensitivity is clamped to the slider's range",
          motion["sensitivity"] == mapping.MOTION_SENSITIVITY_MAX, str(motion))
    check("and so is the dead-zone offset",
          motion["dead_zone"] == mapping.MOTION_DEAD_ZONE_MAX, str(motion))


def test_the_use_mode_follows_the_target():
    """Space Station derives it rather than offering it, and so do we."""
    config = mapping.MappingConfig(blank_blob())
    config.set_motion(target="left stick")
    check("the left stick means Racer",
          config.motion()["use_mode"] == mapping.MOTION_RACER)
    config.set_motion(target="right stick")
    check("the right stick means FPS",
          config.motion()["use_mode"] == mapping.MOTION_FPS)

    # Turning it off assigns neither branch in their writer, so the mode stays.
    config.set_motion(target="off")
    check("turning the gyro off leaves the mode where it was",
          config.motion()["use_mode"] == mapping.MOTION_FPS)

    config.set_motion(target="left stick", use_mode="fps")
    check("an explicit mode wins", config.motion()["use_mode"] == mapping.MOTION_FPS)


def test_the_second_enable_key_is_written_only_under_hold():
    """Flydigi's rule, reproduced rather than corrected.

    Their writer assigns `EnableKey[1]` inside the `Press` branch and re-emits
    whatever it read otherwise. The factory leaves 0 -- D-pad Up -- in that
    byte, and the pad honours it on its own, so under Click a mapping keeps an
    enable key nobody chose. That is a thing for a UI to show, not for a byte
    layout to quietly fix.
    """
    config = mapping.MappingConfig(blank_blob())
    config.set_motion(target="right stick", enable_type="click",
                      keys=("m1", None))
    check("the first key is written under Click",
          config.motion()["keys"][0] == "m1")
    check("and the factory's Up survives, as it does in theirs",
          config.motion()["keys"][1] == "up", str(config.motion()["keys"]))

    config.set_motion(enable_type="hold", keys=("m1", None))
    check("under Hold the second key is written",
          config.motion()["keys"] == ("m1", None), str(config.motion()["keys"]))
    check("stored as ControllerKey.None",
          config.blob[mapping.OFF_MOTION + 7] == mapping.MOTION_KEY_NONE)

    config.set_motion(keys=("lb", "rb"))
    check("a pair sets both", config.motion()["keys"] == ("lb", "rb"))

    # Leaving `keys` out has to leave the bytes alone, or every other setter
    # would quietly unbind the gyro.
    config.set_motion(sensitivity=30)
    check("setting something else does not touch the keys",
          config.motion()["keys"] == ("lb", "rb"))


def test_a_mouse_mapping_is_written_the_way_flydigi_writes_one():
    """Not a pad feature, and still not this module's business to refuse.

    Their Mouse branch blanks both enable keys and the dead zone, because the
    host process that moves the pointer owns all three. Refusing it here would
    leave a profile brought over from Windows uneditable; the app just does not
    offer it.
    """
    config = mapping.MappingConfig(blank_blob())
    config.set_motion(target="mouse", enable_type="hold", keys=("m1", "m2"),
                      sensitivity=40, dead_zone=30)
    written = list(config.blob[mapping.OFF_MOTION : mapping.OFF_MOTION + 8])
    expected = [mapping.MOTION_MOUSE, 0xFF, mapping.MOTION_PRESS, 0,
                40, 40, mapping.MOTION_FPS, 0xFF]
    check("every byte matches their Mouse branch", written == expected,
          f"{written} != {expected}")
    check("and it reads back as mouse",
          mapping.MOTION_TARGETS[config.motion()["target"]] == "mouse")
    check("with no enable keys", config.motion()["keys"] == (None, None))

    for what in (dict(target="wheel"), dict(target=9),
                 dict(enable_type="sometimes"), dict(use_mode=4)):
        raised = False
        try:
            config.set_motion(**what)
        except ValueError:
            raised = True
        check(f"{what} is refused", raised)


def test_the_motion_curve_is_the_joystick_curve_without_its_type():
    config = mapping.MappingConfig(blank_blob())
    curve = config.motion_curve()
    check("the factory curve is the identity line",
          (curve["zero"], curve["point1"], curve["point2"], curve["end"])
          == (0, (63, 63), (127, 127), 127), str(curve))

    config.set_motion_curve(zero=10, point1=(20, 40), point2=(90, 110), end=120)
    curve = config.motion_curve()
    check("it round-trips",
          (curve["zero"], curve["point1"], curve["point2"], curve["end"])
          == (10, (20, 40), (90, 110), 120), str(curve))

    config.set_motion_curve(end=250)
    check("and stays on the stick's 0..127 scale",
          config.motion_curve()["end"] == mapping.MOTION_CURVE_MAX)

    # The block lives in the v3.1 tail, like the joystick extras.
    short = mapping.MappingConfig(bytearray(blank_blob()[:790]))
    check("an older profile has no curve at all", short.motion_curve() is None)
    raised = False
    try:
        short.set_motion_curve(zero=1)
    except mapping.ProtocolError:
        raised = True
    check("and writing one is refused rather than appended", raised)


def test_editing_the_gyro_does_not_disturb_its_neighbours():
    config = mapping.MappingConfig(blank_blob())
    before = bytearray(config.blob)
    config.set_motion(target="left stick", enable_type="click", keys=("m3", None),
                      sensitivity=55, dead_zone=0)
    config.set_motion_curve(point1=(10, 10))
    changed = {i for i, (a, b) in enumerate(zip(before, config.blob)) if a != b}
    allowed = set(range(mapping.OFF_MOTION, mapping.OFF_MOTION + 8)) | set(
        range(mapping.OFF_MOTION_CURVE,
              mapping.OFF_MOTION_CURVE + mapping.MOTION_CURVE_ENTRY))
    check("only the two motion blocks moved", changed <= allowed,
          str(sorted(changed - allowed)))
    check("the trigger travel beside it is intact",
          config.trigger_curve("right")["end"] == 255)
    grip = slice(mapping.OFF_GRIP_VIBRATION, mapping.OFF_GRIP_VIBRATION + 9)
    check("and the grip vibration behind it", config.blob[grip] == before[grip])


def test_the_factory_curves_are_the_identity_line():
    """Both blocks ship as no-curve-at-all, on two different scales."""
    config = mapping.MappingConfig(blank_blob())
    stick = config.joystick_curve("left")
    check("stick curve is default type", stick["type"] == mapping.CURVE_DEFAULT)
    check("stick has no dead zone", stick["center"] == 0)
    check("stick really is a stick", stick["is_stick"])
    check("stick runs to 127", stick["end"] == 127 and stick["point2"] == (127, 127),
          str(stick))

    trigger = config.trigger_curve("right")
    check("trigger runs to 255", trigger["end"] == 255 and trigger["point2"] == (255, 255),
          str(trigger))
    check("trigger points mirror its window",
          trigger["point1"] == (trigger["zero"], trigger["zero"]), str(trigger))


def test_moving_the_trigger_window_moves_its_points():
    """The combination Flydigi's own software produces, and the only one."""
    config = mapping.MappingConfig(blank_blob())
    config.set_trigger_curve("left", zero=40, end=200)
    curve = config.trigger_curve("left")
    check("window round-trips", (curve["zero"], curve["end"]) == (40, 200), str(curve))
    check("point1 followed the start", curve["point1"] == (40, 40), str(curve))
    check("point2 followed the end", curve["point2"] == (200, 200), str(curve))
    check("the other trigger is untouched",
          config.trigger_curve("right")["end"] == 255)

    # Inverted input is a window the pad cannot read, so it is sorted, not stored.
    config.set_trigger_curve("right", zero=200, end=40)
    curve = config.trigger_curve("right")
    check("an inverted window is sorted", (curve["zero"], curve["end"]) == (40, 200),
          str(curve))
    check("and its points follow the sorted window",
          curve["point1"] == (40, 40) and curve["point2"] == (200, 200), str(curve))

    # Equal ends are reachable in Space Station's own UI, so they are allowed.
    config.set_trigger_curve("left", zero=90, end=90)
    check("a zero-width window is permitted",
          config.trigger_curve("left")["zero"] == 90)

    # And a caller shaping the curve deliberately can keep its breakpoints.
    config.set_trigger_curve("left", zero=10, end=250, mirror_points=False)
    check("opting out leaves the points alone",
          config.trigger_curve("left")["point1"] == (90, 90),
          str(config.trigger_curve("left")))


def test_the_joystick_type_is_written_into_both_blocks():
    config = mapping.MappingConfig(blank_blob())
    config.set_joystick_curve("right", curve_type=mapping.CURVE_CUSTOM)
    check("core block took the type",
          config.joystick_curve("right")["type"] == mapping.CURVE_CUSTOM)
    check("extra block agrees",
          config.joystick_shape("right")["type"] == mapping.CURVE_CUSTOM)
    check("the left stick is untouched",
          config.joystick_curve("left")["type"] == mapping.CURVE_DEFAULT)

    raised = False
    try:
        config.set_joystick_curve("left", curve_type=7)
    except ValueError:
        raised = True
    check("an unknown curve type is refused", raised)


def test_the_bank_is_exactly_nine_points():
    """Flydigi's writer has no bound and walks into the next field."""
    config = mapping.MappingConfig(blank_blob())
    shape = config.joystick_shape("left")
    check("factory bank is the straight line",
          shape["bank"] == [50, 62, 75, 87, 100, 112, 125, 137, 150], str(shape["bank"]))
    check("factory shape is rectangular", shape["circular"] is False)
    check("factory edge is zero", shape["edge"] == 0)

    config.set_joystick_shape("left", bank=[50] * 9, circular=True, edge=20)
    shape = config.joystick_shape("left")
    check("bank round-trips", shape["bank"] == [50] * 9, str(shape["bank"]))
    check("circularity round-trips", shape["circular"] is True)
    check("edge round-trips", shape["edge"] == 20)
    check("the right stick is untouched",
          config.joystick_shape("right")["bank"][1] == 62)

    for wrong in ([50] * 8, [50] * 10):
        raised = False
        try:
            config.set_joystick_shape("right", bank=wrong)
        except ValueError:
            raised = True
        check(f"a bank of {len(wrong)} is refused", raised)
    check("and the neighbouring fields survived the attempt",
          config.joystick_shape("right")["edge"] == 0
          and config.joystick_shape("right")["circular"] is False)


def test_the_negative_half_of_center_and_edge_is_refused():
    """Not "no negative dead zones" -- negative means a different control.

    One byte carries two opposite settings and the sign picks which: positive
    `center` is a dead zone, negative is Offset, which pushes the smallest input
    straight to a real output so a game's own dead zone stops swallowing it.
    Both halves are wanted; only the positive one has an encoding we are sure
    of, because Flydigi's reader and writer disagree about the other.
    """
    config = mapping.MappingConfig(blank_blob())
    for setter, kwargs in ((config.set_joystick_curve, {"center": -20}),
                           (config.set_joystick_shape, {"edge": -20})):
        raised = False
        try:
            setter("left", **kwargs)
        except ValueError:
            raised = True
        check(f"negative {list(kwargs)[0]} is refused", raised)

    config.set_joystick_curve("left", center=30)
    check("a positive centre is accepted", config.joystick_curve("left")["center"] == 30)


def test_a_stick_mapped_to_something_else_is_not_a_dead_zone_of_127():
    config = mapping.MappingConfig(blank_blob())
    base = mapping.OFF_JOYSTICK_CURVE
    config.blob[base + 1] = mapping.CENTER_NOT_A_STICK
    curve = config.joystick_curve("left")
    check("the sentinel is reported raw", curve["center"] == 127)
    check("but not as a stick", curve["is_stick"] is False)
    check("a real centre still reads as a stick",
          config.joystick_curve("right")["is_stick"] is True)


FACTORY_BANK = [50, 62, 75, 87, 100, 112, 125, 137, 150]


def test_the_compiler_reproduces_the_pads_own_bank():
    """The whole model, checked against nine bytes read off real hardware.

    If the node maths, the 0..127 scaling, the sample positions or the rounding
    were wrong, this would miss. It is the only ground truth we have for any of
    them, so it is worth more than the rest of this file put together.
    """
    check("the identity curve compiles to the factory bank",
          mapping.stick_bank() == FACTORY_BANK, str(mapping.stick_bank()))

    # Rounding, specifically: their JavaScript would put 63/88/113/138 here.
    rounded = [50, 63, 75, 88, 100, 113, 125, 138, 150]
    check("and not to what Space Station would write",
          mapping.stick_bank() != rounded)


def test_a_dead_zone_reaches_the_bank():
    flat = mapping.stick_bank()
    dead = mapping.stick_bank(center=25)
    check("a dead zone changes the curve", dead != flat, str(dead))
    check("it still ends at full output", dead[-1] == 150, str(dead))
    check("and it starts below the line", dead[0] < flat[0], str(dead))

    # Offset is the other half of the same field: the smallest input already
    # produces real output, so the curve starts above the line rather than below.
    offset = mapping.stick_bank(center=-25)
    check("an offset lifts the start instead", offset[0] > flat[0], str(offset))
    check("the two are opposites", offset[0] > flat[0] > dead[0])

    # Values stay in range whatever is asked for.
    for curve in (mapping.stick_bank(center=100), mapping.stick_bank(center=-100),
                  mapping.stick_bank(edge=100), mapping.stick_bank(edge=-100)):
        check("bank stays a legal byte", all(0 <= v <= 150 for v in curve),
              str(curve))


def test_a_big_dead_zone_does_not_invert_the_curve():
    """The bug this guards produced full output exactly where silence belonged.

    The interior breakpoints sit around x=50 on the 0..100 scale, so a dead zone
    past that puts the start node to their right, the segment between them runs
    backwards, and the lerp comes out inverted: `stick_bank(center=60)` returned
    150 -- full output -- for the first half of the travel. Flydigi avoids it by
    remapping the breakpoints into the span the ends leave, which is what
    `stick_nodes` now does.
    """
    for center in range(0, 101, 5):
        bank = mapping.stick_bank(center=center)
        check(f"a dead zone of {center} rises, never falls",
              all(bank[i] <= bank[i + 1] for i in range(mapping.BANK_POINTS - 1)),
              str(bank))
        check(f"a dead zone of {center} stays a legal byte",
              all(0 <= v <= 150 for v in bank), str(bank))

    for edge in range(0, 101, 5):
        bank = mapping.stick_bank(edge=edge)
        check(f"an outer dead zone of {edge} rises, never falls",
              all(bank[i] <= bank[i + 1] for i in range(mapping.BANK_POINTS - 1)),
              str(bank))

    # A dead zone bigger than the first breakpoint has to silence the bottom of
    # the travel, which is the case that used to do the exact opposite.
    # Stored values are biased by 50, so anything at or below 50 is silence.
    big = mapping.stick_bank(center=60)
    check("a 60% dead zone is silent for the first half",
          all(v <= 50 for v in big[:4]), str(big))
    check("and still reaches full output", big[-1] == 150, str(big))

    # Fully collapsed: no span at all for the curve to rise across.
    whole = mapping.stick_bank(center=100)
    check("a dead zone of everything is silent throughout",
          all(v <= 50 for v in whole[:-1]), str(whole))
    check("and still reaches full at the stop", whole[-1] == 150, str(whole))


def test_the_two_dead_zones_cannot_eat_more_travel_than_exists():
    config = mapping.MappingConfig(blank_blob())
    config.set_stick("left", center=60)
    config.set_stick("left", edge=60)
    stick = config.stick("left")
    check("the second one gives way", stick["center"] + stick["edge"] <= 100,
          f"center {stick['center']} edge {stick['edge']}")
    check("and the one already set is not moved behind the user's back",
          stick["center"] == 60, str(stick["center"]))
    check("the curve is still usable",
          all(stick["bank"][i] <= stick["bank"][i + 1] for i in range(8)),
          str(stick["bank"]))


def test_the_presets_compile_to_different_curves():
    banks = {name: mapping.stick_bank(point1=mapping.STICK_PRESETS[name][0],
                                      point2=mapping.STICK_PRESETS[name][1])
             for name in (mapping.CURVE_DEFAULT, mapping.CURVE_QUICK,
                          mapping.CURVE_SLOW)}
    check("default is the straight line",
          banks[mapping.CURVE_DEFAULT] == FACTORY_BANK)
    # Instant is faster off centre, Delay slower -- so at the halfway point they
    # sit either side of the straight line, which is the whole point of them.
    check("instant is above the line at half travel",
          banks[mapping.CURVE_QUICK][4] > banks[mapping.CURVE_DEFAULT][4],
          str(banks[mapping.CURVE_QUICK]))
    check("delay is below it",
          banks[mapping.CURVE_SLOW][4] < banks[mapping.CURVE_DEFAULT][4],
          str(banks[mapping.CURVE_SLOW]))
    check("all three still reach full output",
          all(bank[-1] == 150 for bank in banks.values()))


def test_editing_a_stick_writes_the_bank_too():
    """A UI that only wrote what it edited would change nothing on the pad."""
    config = mapping.MappingConfig(blank_blob())
    config.set_stick("left", center=30)
    stick = config.stick("left")
    check("the source form is stored", stick["center"] == 30)
    check("the bank was recompiled", stick["bank"] == mapping.stick_bank(center=30),
          str(stick["bank"]))
    check("and it is no longer the factory one", stick["bank"] != FACTORY_BANK)
    check("editing a node makes the curve Custom",
          stick["type"] == mapping.CURVE_CUSTOM, str(stick["type"]))
    check("the right stick is untouched",
          config.stick("right")["bank"] == FACTORY_BANK)


def test_choosing_a_preset_restores_its_whole_shape():
    config = mapping.MappingConfig(blank_blob())
    config.set_stick("left", center=40, circular=True)
    check("the edit took", config.stick("left")["center"] == 40)

    config.set_stick("left", curve_type=mapping.CURVE_DEFAULT)
    stick = config.stick("left")
    check("the preset is stored as itself, not as Custom",
          stick["type"] == mapping.CURVE_DEFAULT, str(stick["type"]))
    check("picking a preset clears the dead zone", stick["center"] == 0)
    check("and restores the factory bank exactly", stick["bank"] == FACTORY_BANK,
          str(stick["bank"]))
    check("circularity is not a curve setting and survives", stick["circular"] is True)


def test_editing_extras_does_not_disturb_buttons():
    """The blob holds everything, so an edit must stay in its own bytes."""
    config = mapping.MappingConfig(blank_blob())
    config.set_mapping("m1", "a")
    before = config.remapped()
    config.set_vibration("left", minimum=10, maximum=250, scale=99)
    config.set_trigger_effect("left", 1, [50, 120])
    check("button mapping survives unrelated edits", config.remapped() == before,
          str(config.remapped()))
    check("title survives too", config.title == "Profile", config.title)


def test_targets_exclude_buttons_xinput_cannot_send():
    for key in ("m1", "m2", "m3", "m4", "m5", "m6"):
        check(f"{key} is not offered as a target",
              key not in mapping.XINPUT_TARGETS)
        check(f"{key} is still a source", key in mapping.APEX5_KEYS)
    for key in ("a", "b", "lb", "start"):
        check(f"{key} is a valid target", key in mapping.XINPUT_TARGETS)


def test_the_key_list_is_this_pad_s_and_not_another_s():
    """APEX5_KEYS once held C and Z and omitted M5/M6, which meant the app drew
    two buttons this pad does not have and could not rebind two that it does.

    Both halves come from Flydigi's own data. `GenerateControllerApex5` lists
    the keys the pad has -- M1..M6, no C, no Z -- while C and Z appear only in
    the Vader3/4/5 factories. Space Station's k5 hitbox map then marks which of
    them may be rebound, and Fn (24), Turbo (25) and Home (27) are false there.
    Neither source is in the repository, so this pins the conclusion instead.
    """
    for key in mapping.APEX5_KEYS:
        check(f"{key} has a ControllerKey id", key in mapping.KEY_IDS)

    for key in ("c", "z"):
        check(f"{key} is a Vader key and not this pad's",
              key not in mapping.APEX5_KEYS)
    for key in ("m5", "m6"):
        check(f"{key} is one of this pad's shoulder buttons",
              key in mapping.APEX5_KEYS)
    for key in ("menu", "turbo"):
        check(f"{key} is not rebindable, so it is not offered",
              key not in mapping.APEX5_KEYS)

    check("the extras are all real keys",
          set(mapping.EXTRA_KEYS) <= set(mapping.APEX5_KEYS))
    check("a key is either a target or an extra, never both",
          not set(mapping.XINPUT_TARGETS) & set(mapping.EXTRA_KEYS))
    check("and the two together account for every key",
          set(mapping.XINPUT_TARGETS) | set(mapping.EXTRA_KEYS)
          == set(mapping.APEX5_KEYS))
    check("no key is listed twice",
          len(mapping.APEX5_KEYS) == len(set(mapping.APEX5_KEYS)))


def test_lighting_round_trip():
    pad = FakePad()
    config = lighting.read_config(pad)
    check("lighting reads back", config.led_count == 12, str(config.led_count))
    check("brightness decoded", config.brightness == 20, str(config.brightness))
    check("mode decoded", config.mode == 7, str(config.mode))

    edited = config.copy()
    edited.brightness = 80
    edited.mode = 1
    edited.set_solid((255, 0, 128))
    pad.packets_received = 0
    sent = lighting.write_config(pad, edited, old=config)
    check("lighting write sends only changed packets", 0 < sent <= 19, f"sent {sent}")

    back = lighting.read_config(pad)
    check("brightness persisted", back.brightness == 80, str(back.brightness))
    check("mode persisted", back.mode == 1)
    last_frame, last_led = back.frames - 1, back.leds_per_frame - 1
    check("colour reached every frame",
          back.led(0, 0) == (255, 0, 128)
          and back.led(last_frame, last_led) == (255, 0, 128),
          f"{back.led(0, 0)} {back.led(last_frame, last_led)}")
    check("writing colours did not resize the config",
          len(back.blob) == len(config.blob),
          f"{len(config.blob)} -> {len(back.blob)}")
    check("geometry matches the LED count",
          (back.frames, back.leds_per_frame) == (10, 12),
          f"{back.frames}x{back.leds_per_frame}")


def test_lighting_brightness_is_clamped():
    config = lighting.LedConfig(FakePad().led_blob)
    config.brightness = 500
    check("brightness clamps to the top", config.brightness == lighting.BRIGHTNESS_MAX,
          str(config.brightness))
    config.brightness = -5
    check("brightness clamps to zero", config.brightness == 0, str(config.brightness))


def test_lighting_effects_write_frames():
    """The pad plays stored frames, so an effect must change the frame data."""
    config = lighting.read_config(FakePad())
    blank = bytes(config.blob)

    config.set_solid((10, 20, 30))
    check("static writes every frame",
          all(config.led(f, l) == (10, 20, 30)
              for f in range(config.frames) for l in range(config.leds_per_frame)))

    config.set_breath([(255, 0, 0)])
    levels = {config.led(f, 0)[0] for f in range(config.frames)}
    check("breath varies over the loop", len(levels) > 1, str(sorted(levels)))
    check("breath is uniform across the pad",
          len({config.led(3, l) for l in range(config.leds_per_frame)}) == 1)

    config.set_flow([(255, 0, 0), (0, 0, 0)])
    check("flow varies along the pad",
          len({config.led(0, l) for l in range(config.leds_per_frame)}) > 1)
    check("flow travels between frames", config.frame(0) != config.frame(3))

    config.set_rainbow()
    check("rainbow uses many colours",
          len({config.led(0, l) for l in range(config.leds_per_frame)}) > 3)
    check("effects never resize the config", len(config.blob) == len(blank))
    check("effects loop over every frame", config.loop == (0, config.frames - 1),
          str(config.loop))


def test_command_175_resets_every_profile_not_the_one_it_is_given():
    """Measured on the pad, and the reason there are two reset paths at all.

    The slots were named A1/B2/C3/D4 and saved, 175 was sent with `cfgId = 2`,
    and all four came back at factory with their tags at 0xFFFF. Flydigi name
    the factory `ResetMappingConfigByCfgId` and gate it on
    `ResetAllMappingUsable`; only the second is true.
    """
    pad = FakePad()
    for cfg in range(4):
        config = mapping.read_config(pad, cfg)
        config.title = f"Mine {cfg}"
        mapping.write_config(pad, cfg, config, old=None)

    check("reset-all is acknowledged", mapping.reset_all_configs(pad) is True)
    for cfg in range(4):
        check(f"slot {cfg} went back to factory",
              mapping.read_config(pad, cfg).title == f"Profile {cfg + 1}",
              mapping.read_config(pad, cfg).title)


def test_restoring_one_profile_writes_the_factory_bytes_instead():
    """A single slot is restored by writing, not by 175.

    Which is what Space Station does -- `ControllerRepository.ResetMappingConfig`
    with a slot id writes a bundled `default_mapping_<DeviceType>` blob into it
    and commits -- and the only thing that *can* be done, since the firmware
    command has no per-slot form.
    """
    pad = FakePad()
    config = mapping.read_config(pad, 1)
    config.title = "Racing"
    config.set_mapping("a", "b")
    mapping.write_config(pad, 1, config, old=None)
    check("the edit landed first", mapping.read_config(pad, 1).title == "Racing")

    restored, saved = mapping.reset_config(pad, 1)
    check("it is saved to flash", saved is True)
    check("the restored profile carries the factory title",
          restored.title == mapping.MappingConfig(
              factory_config.for_slot(1), 1).title, restored.title)
    check("the remap is gone", restored.mapping("a")[0] == "a",
          str(restored.mapping("a")))
    check("and it went under a fresh tag, not the old one",
          restored.data_version != config.data_version,
          f"{restored.data_version} vs {config.data_version}")

    check("the other slots are untouched",
          mapping.read_config(pad, 0).title == "Profile 1",
          mapping.read_config(pad, 0).title)


def test_the_force_trigger_family_is_gated_on_the_hardware():
    """81 and 82 are for force triggers, and a Vader has none.

    The reference gates the whole send rather than the effect:
    `SetForceTriggerConfigImpl` opens with `if (!controller.IsSupportForceTrigger)
    return;`, and the replay `engage_stored` copies sits behind the same test at
    `ControllerBusinessService:1599`. A Vader 5 declares it false, so Space
    Station never sends either command to one.

    **Its trigger motors are a different feature on a different command** --
    `VibrationCommandFactory` writes two more level bytes at `array[7]`/`[8]`
    beside the grips', under the same length byte -- so "the Vader's version of
    SyncWithGrip" is not what 82 is, and gating it loses the pad nothing.
    """
    apex, vader = FakePad(), FakePad(device_type=130)
    config = mapping.read_config(apex, 0)
    config.set_trigger_effect("left", effects.MODE_LOCK, [60])
    config.set_trigger_effect("right", effects.MODE_LOCK, [60])

    check("an Apex 5 is sent the stored effect",
          len(effects.engage_stored(apex, config, "k5", wait=0)) == 2)
    check("and the pad has it live", len(apex.live_effects) == 2,
          str(apex.live_effects))

    check("a Vader is not", effects.engage_stored(vader, config, "f5", wait=0) == [])
    check("and nothing reached it at all", not vader.live_effects and not vader.live_binds,
          f"{vader.live_effects} {vader.live_binds}")

    # An unknown model is not silently trusted either, and a caller that passes
    # nothing still sends -- which is the bench script's escape hatch.
    check("an unknown model is refused", effects.engage_stored(
        FakePad(device_type=149), config, "k6", wait=0) == [])
    check("and no code at all still sends",
          len(effects.engage_stored(FakePad(), config, wait=0)) == 2)


def test_a_restore_writes_this_model_s_profile_and_not_the_other_s():
    """The dangerous half of shipping factory bytes.

    Restoring one slot means writing a config, so it needs *this model's*
    config. An Apex 5's key table written to a Vader would leave C and Z mapped
    to nothing and call the result factory -- so the identify read decides which
    bytes go out, and this is the assertion that they are not the Apex 5's.
    """
    vader = FakePad(device_type=130)
    restored, _saved = mapping.reset_config(vader, 0)
    # What landed on the pad, not what was returned: the returned config carries
    # the fresh change tag the save went out under, which is the one field a
    # factory profile is *meant* to differ in afterwards.
    check("a Vader gets the Vader's factory profile",
          bytes(vader.blobs[0]) == bytes(factory_config.for_slot(0, "f5")))
    check("which is not the Apex 5's",
          bytes(vader.blobs[0]) != bytes(factory_config.for_slot(0, "k5")))
    tag = mapping.OFF_DATA_VERSION
    check("and only the change tag differs from the bytes as committed",
          bytes(restored.blob[:tag]) == bytes(factory_config.for_slot(0, "f5")[:tag])
          and bytes(restored.blob[tag + 2:])
              == bytes(factory_config.for_slot(0, "f5")[tag + 2:]))
    check("and it is a v3.2 profile, so its macros are not in the blob",
          restored.proto_version == mapping.PROTO_V32
          and not restored.macros_in_blob, str(restored.proto_version))
    # The store is the half a v3.1-shaped restore would miss: the profile blob
    # carries no macros on this pad, so a restore that wrote only the blob would
    # leave the old macros playing on a slot the user just put back to factory.
    check("the macro store is emptied too",
          restored.macro_store is not None and restored.macro_store.macros() == [])

    check("and the firmware's own reset still works on it",
          mapping.reset_all_configs(vader) is True)


def test_a_model_with_no_factory_profile_is_refused_the_per_slot_restore():
    """The gate is still there, and it is a data gate rather than a hardware one.

    Both driven models have factory bytes now, so the refusal has to be provoked
    by taking one away -- which is exactly the state a *third* model would be in
    the day it joined SUPPORTED, and the day this matters.
    """
    vader = FakePad(device_type=130)
    without = dict(identity.CAPABILITIES["f5"], factory_profile=False)
    original = identity.CAPABILITIES["f5"]
    identity.CAPABILITIES["f5"] = without
    try:
        mapping.reset_config(vader, 0)
    except identity.WrongDevice as exc:
        check("the refusal names the model", "Vader 5" in str(exc), str(exc))
    else:
        check("a model with no factory profile should be refused", False)
    finally:
        identity.CAPABILITIES["f5"] = original

    check("but the firmware's own reset still works on it",
          mapping.reset_all_configs(vader) is True)


def test_the_factory_blob_is_one_blob_and_a_digit():
    """Four slots, one profile, one differing byte -- and it is the title.

    `tools/gen-factory-config` refuses to regenerate if that stops being true,
    so this is the assertion that would notice a firmware change first.
    """
    for code in sorted(factory_config.FACTORY_BLOBS):
        blobs = [factory_config.for_slot(cfg, code) for cfg in range(4)]
        differing = sorted({i for other in blobs[1:]
                            for i in range(len(blobs[0])) if blobs[0][i] != other[i]})
        check(f"{code}: exactly one byte differs across the four",
              differing == [mapping.OFF_TITLE + 4], str(differing))
        titles = [mapping.MappingConfig(blob, i).title
                  for i, blob in enumerate(blobs)]
        check(f"{code}: and it is the digit in the title",
              len(set(titles)) == 4, str(titles))
        check(f"{code}: 840 bytes, the length every profile command expects",
              len(blobs[0]) == 840, str(len(blobs[0])))

    # Both driven models have one, and a model that does not must say so rather
    # than fall back on somebody else's bytes.
    for code in identity.SUPPORTED:
        check(f"{code} has a factory profile", factory_config.have(code))
    check("and an unknown model does not", not factory_config.have("k6"))
    try:
        factory_config.for_slot(0, "k6")
    except ValueError:
        pass
    else:
        check("asking for one should be refused", False)


def test_a_switch_save_is_aimed_at_the_second_bank():
    pad = FakePad()
    check("the four XInput slots map onto 4..7",
          [mapping.switch_cfg_id(i) for i in range(4)] == [4, 5, 6, 7])

    for bad in (-1, 4):
        try:
            mapping.switch_cfg_id(bad)
        except ValueError:
            pass
        else:
            check(f"switch_cfg_id({bad}) should be refused", False)

    # An XInput slot is refused outright rather than sent: what the firmware
    # does with 171 aimed at 0..3 is unmeasured, and the failure mode would be
    # a profile quietly overwritten.
    try:
        mapping.save_switch_config(pad, 2, 1234)
    except ValueError:
        pass
    else:
        check("171 aimed at an XInput slot should be refused", False)


def test_a_switch_save_commits_the_running_profile_under_a_new_tag():
    """171 is 166 with a destination -- the *source* is whatever is running."""
    pad = FakePad()
    config = mapping.read_config(pad, 2)          # reading switches the pad
    config.title = "Switch"
    mapping.write_config(pad, 2, config, old=None)

    version = mapping.next_data_version(config.data_version)
    check("the switch save is acknowledged",
          mapping.save_switch_config(pad, mapping.switch_cfg_id(2), version)
          is True)

    saved = pad.switch_saved.get(6)
    check("it landed in the matching Switch slot", saved is not None,
          str(sorted(pad.switch_saved)))
    blob, tag = saved
    check("it carries the running profile, not the destination's",
          mapping.MappingConfig(blob, 6).title == "Switch",
          mapping.MappingConfig(blob, 6).title)
    check("and the tag it was told to use", tag == version,
          f"{tag} vs {version}")


def test_the_switch_save_frame_is_flydigis_own_odd_one():
    """Byte-exact, because the arithmetic and the bytes disagree.

    The length byte says 4 -- as 166's does, carrying two payload bytes with
    its checksum at 7 -- while 171 puts the target slot at 7 and its checksum
    at 8, over a range that stops at 6 and never covers the slot. Building it
    "properly" would be a different packet from the one the pad answers.
    """
    buf = mapping._build_save_switch(5, 0x5321)
    check("command id", buf[3] == mapping.CMD_SAVE_SWITCH, str(buf[3]))
    check("the length byte still says 4", buf[4] == 4, str(buf[4]))
    check("version little-endian at 5", (buf[5], buf[6]) == (0x21, 0x53),
          f"{buf[5]:#04x} {buf[6]:#04x}")
    check("the slot sits where 166 keeps its checksum", buf[7] == 5, str(buf[7]))
    check("the checksum is one further along, and excludes the slot",
          buf[8] == sum(buf[3:7]) & 0xFF, f"{buf[8]} vs {sum(buf[3:7]) & 0xFF}")


def test_a_switch_profile_drops_what_a_switch_cannot_run():
    """Flydigi normalise before writing a Switch slot; so do we.

    Nothing made in this project can produce either of these -- keyboard
    binding is host-side and unimplemented here -- but the config being copied
    is whatever the pad is running, which may well have come from theirs.
    """
    pad = FakePad()
    config = mapping.read_config(pad, 0)
    offset, _key_id = config._entry("a")
    config.blob[offset] = mapping.TARGET_KEYBOARD
    # Written straight into the blob: the setter refuses anything above
    # BIPOLAR_MAX, and the sentinel is deliberately outside that range. Only
    # Flydigi's app puts it there, which is the point of the fixture.
    left = mapping.OFF_JOYSTICK_CURVE + mapping.CURVE_ENTRY * 0
    config.blob[left + 1] = mapping.CENTER_NOT_A_STICK
    check("the fixture really is unrunnable on a Switch",
          config.mapping("a")[0] == "keyboard" and not config.stick("left")["is_stick"])

    stripped = mapping.MappingConfig(config.blob, 0)
    notes = stripped.normalise_for_switch()

    check("the keyboard binding went back to sending its own key",
          stripped.mapping("a")[0] == "a", str(stripped.mapping("a")))
    check("the stick acts as a stick again",
          stripped.stick("left")["is_stick"])
    check("and both were reported to the caller", len(notes) == 2, str(notes))
    check("a clean profile needs no stripping",
          mapping.read_config(pad, 0).normalise_for_switch() == [])


def test_effects_by_id_and_colours():
    config = lighting.read_config(FakePad())
    for effect in (lighting.EFFECT_OFF, lighting.EFFECT_STREAMING,
                   lighting.EFFECT_ROTATION, lighting.EFFECT_BREATHING,
                   lighting.EFFECT_STATIC_SINGLE, lighting.EFFECT_STATIC_MULTI,
                   lighting.EFFECT_RAINBOW, lighting.EFFECT_WAVE,
                   lighting.EFFECT_FLASH):
        config.apply_effect(effect, [(255, 0, 0), (0, 0, 255)])
        check(f"effect {effect} records its id", config.mode == effect)
        check(f"effect {effect} stays in bounds", len(config.blob) == 380)

    config.apply_effect(lighting.EFFECT_OFF)
    check("off is dark everywhere",
          all(config.led(f, l) == (0, 0, 0)
              for f in range(config.frames) for l in range(config.leds_per_frame)))

    # Two colours must not produce the same frames as one.
    config.apply_effect(lighting.EFFECT_STATIC_MULTI, [(255, 0, 0)])
    one = bytes(config.blob)
    config.apply_effect(lighting.EFFECT_STATIC_MULTI, [(255, 0, 0), (0, 255, 0)])
    check("a second colour changes the result", bytes(config.blob) != one)

    check("an unknown effect id is refused",
          _raises(lambda: config.apply_effect(1234, [(1, 2, 3)])))


def test_suggested_colours_differ():
    first = lighting.suggest_colour([])
    second = lighting.suggest_colour([first])
    check("adding a colour offers something new", first != second,
          f"{first} {second}")


def _raises(call, kind=ValueError):
    try:
        call()
    except kind:
        return True
    return False


# -- macros -------------------------------------------------------------------

TAP_A = [{"delay": 0, "key": "a", "event": mapping.MACRO_PRESS},
         {"delay": 80, "key": "a", "event": mapping.MACRO_RELEASE}]


def test_a_macro_round_trips():
    config = mapping.MappingConfig(blank_blob())
    check("a blank page holds no macros", config.macros() == [])

    steps = TAP_A + [{"delay": 40, "key": "b", "event": mapping.MACRO_PRESS},
                     {"delay": 80, "key": "b", "event": mapping.MACRO_RELEASE}]
    config.set_macro("m1", steps, macro_type=mapping.MACRO_TOGGLE, interval=50)
    stored = config.macros()
    check("one macro is stored", len(stored) == 1, str(stored))
    check("the trigger key round-trips", stored[0]["key"] == "m1")
    check("the type round-trips", stored[0]["type"] == mapping.MACRO_TOGGLE)
    check("the interval round-trips", stored[0]["interval"] == 50,
          str(stored[0]["interval"]))
    check("the steps round-trip", stored[0]["steps"] == steps, str(stored[0]["steps"]))


def test_a_macro_binds_its_key_and_gives_it_back():
    """A body with no key-table entry is a macro the pad never runs."""
    config = mapping.MappingConfig(blank_blob())
    config.set_macro("m1", TAP_A)
    offset = mapping.OFF_KEY_TABLE + mapping.KEY_IDS["m1"] * mapping.KEY_ENTRY
    check("binding writes the macro sentinel",
          config.blob[offset] == mapping.TARGET_MACRO, str(config.blob[offset]))
    check("the key reports itself as a macro", config.mapping("m1")[0] == "macro")

    config.clear_macro("m1")
    check("clearing drops the body", config.macros() == [])
    check("clearing restores the key",
          config.blob[offset] == mapping.TARGET_IDENTITY, str(config.blob[offset]))


def test_remapping_a_key_away_from_its_macro_drops_the_body():
    """Otherwise the key sends its new binding and plays the old macro too.

    The firmware reads the macro page and the key table independently, so a
    body left behind at 230 keeps running underneath the remap -- measured on
    hardware, and prevented rather than repaired by Space Station as well.
    """
    config = mapping.MappingConfig(blank_blob())
    config.set_macro("m1", TAP_A)
    config.set_mapping("m1", "b")
    check("the orphaned body is gone", config.macros() == [], str(config.macros()))
    check("and the remap went through", config.mapping("m1")[0] == "b")

    # Only the key being remapped: a second macro is none of its business.
    config.set_macro("m2", TAP_A)
    config.set_macro("m3", TAP_A)
    config.set_mapping("m2", None)
    check("the other macro survives",
          [m["key"] for m in config.macros()] == ["m3"], str(config.macros()))


def test_the_macro_page_matches_flydigis_writer():
    """Header, word offsets, cumulative ticks and the 0xFF tail."""
    config = mapping.MappingConfig(blank_blob())
    config.set_macros([
        {"key": "m1", "type": mapping.MACRO_ONCE, "steps": TAP_A},
        {"key": "m2", "type": mapping.MACRO_WHILE_HELD, "steps": TAP_A},
    ])
    page = bytes(config.blob[mapping.OFF_MACROS :
                             mapping.OFF_MACROS + mapping.MACRO_REGION])
    check("the count byte counts macros", page[0] == 2, str(page[0]))
    # Offsets are in 4-byte words, and each body spends one on its own header:
    # the first macro starts at 0, the second three words later (1 + 2 steps).
    check("offsets are word offsets", (page[1], page[2]) == (0, 3),
          f"{page[1]} {page[2]}")
    check("unused offset slots stay zero", page[3:6] == b"\x00\x00\x00")

    body = page[mapping.MACRO_HEADER:]
    check("a body leads with its key and step count",
          (body[0], body[1], body[2]) == (mapping.KEY_IDS["m1"], 2, 0),
          f"{body[0]} {body[1]} {body[2]}")
    check("the type is the body's fourth byte", body[3] == mapping.MACRO_ONCE)
    # Times are stored cumulative in 10 ms ticks: 0 for the press, 8 for a
    # release 80 ms later. Read back as gaps, which is what a caller edits.
    check("the first step is at tick zero", (body[4], body[5]) == (0, 0))
    check("the second step is 80 ms later in ticks", (body[8], body[9]) == (8, 0),
          f"{body[8]} {body[9]}")
    check("a step carries key then event",
          (body[10], body[11]) == (mapping.KEY_IDS["a"], mapping.MACRO_RELEASE))
    check("the tail is 0xFF fill", set(page[mapping.MACRO_HEADER + 24:]) == {0xFF})


def test_macro_delays_are_quantised_not_summed():
    """Each gap is divided on its own, as Flydigi's writer does."""
    config = mapping.MappingConfig(blank_blob())
    steps = [{"delay": 0, "key": "a", "event": mapping.MACRO_PRESS},
             {"delay": 15, "key": "a", "event": mapping.MACRO_RELEASE},
             {"delay": 15, "key": "b", "event": mapping.MACRO_PRESS}]
    config.set_macro("m1", steps)
    delays = [step["delay"] for step in config.macros()[0]["steps"]]
    # 15 ms truncates to one tick each, so the third step lands at 20 ms and not
    # at the 30 ms a single division of the total would have given.
    check("each gap truncates to a tick", delays == [0, 10, 10], str(delays))


def test_the_macro_page_is_bounded():
    config = mapping.MappingConfig(blank_blob())
    too_many = [{"key": key, "type": mapping.MACRO_ONCE, "steps": TAP_A}
                for key in ("m1", "m2", "m3", "m4", "m5", "m6")]
    check("a sixth macro is refused", _raises(lambda: config.set_macros(too_many)))

    step = dict(TAP_A[0])
    over_budget = [{"key": "m1", "type": mapping.MACRO_ONCE,
                    "steps": [step] * (mapping.MACRO_STEP_BUDGET + 1)}]
    check("more steps than the page holds is refused",
          _raises(lambda: config.set_macros(over_budget)))
    check("the page still holds nothing after a refusal", config.macros() == [])

    at_budget = [{"key": "m1", "type": mapping.MACRO_ONCE,
                  "steps": [step] * mapping.MACRO_STEP_BUDGET}]
    config.set_macros(at_budget)
    check("a full page fits exactly",
          len(config.macros()[0]["steps"]) == mapping.MACRO_STEP_BUDGET,
          str(len(config.macros()[0]["steps"])))


def test_a_macro_step_cannot_send_what_xinput_has_no_id_for():
    """Same reasoning as the remap targets -- and the same silent failure."""
    config = mapping.MappingConfig(blank_blob())
    for key in ("m1", "m5"):
        check(f"a step on {key} is refused",
              _raises(lambda: config.set_macro("m2", [
                  {"delay": 0, "key": key, "event": mapping.MACRO_PRESS}])))
    check("a paddle is still allowed to *run* one",
          config.set_macro("m1", TAP_A) is None)


def test_macros_are_read_permissively():
    """A profile written elsewhere reads back as what it is."""
    config = mapping.MappingConfig(blank_blob())
    page = bytearray(b"\xff" * mapping.MACRO_REGION)
    page[0] = 1
    page[1] = 0
    page[mapping.MACRO_HEADER : mapping.MACRO_HEADER + 8] = bytes([
        mapping.KEY_IDS["m1"], 1, 0, mapping.MACRO_ONCE,
        0, 0, mapping.KEY_IDS["m1"], mapping.MACRO_PRESS])
    config.blob[mapping.OFF_MACROS : mapping.OFF_MACROS + mapping.MACRO_REGION] = page
    stored = config.macros()
    check("a step nothing can receive still reads back",
          stored and stored[0]["steps"][0]["key"] == "m1", str(stored))


def test_a_count_outside_the_page_reads_as_nothing():
    config = mapping.MappingConfig(blank_blob())
    for count in (0, mapping.MACRO_SLOTS + 1, 0xFF):
        config.blob[mapping.OFF_MACROS] = count
        check(f"a count of {count} reads as no macros", config.macros() == [])


def test_the_interval_belongs_to_the_slot_not_the_macro():
    """A factory 30 ms survives a macro that does not set one."""
    config = mapping.MappingConfig(blank_blob())
    config.set_macro("m1", TAP_A)
    check("an unset interval keeps the factory byte",
          config.blob[mapping.OFF_MACRO_CYCLE] == 3,
          str(config.blob[mapping.OFF_MACRO_CYCLE]))
    check("and reads back as the milliseconds it is",
          config.macros()[0]["interval"] == 30)

    config.blob[mapping.OFF_MACRO_CYCLE] = mapping.MACRO_INTERVAL_UNSET
    check("an unwritten slot reads as None",
          config.macros()[0]["interval"] is None)


def test_editing_macros_does_not_disturb_its_neighbours():
    config = mapping.MappingConfig(blank_blob())
    before = bytes(config.blob)
    config.set_macros([{"key": "m1", "type": mapping.MACRO_ONCE, "steps": TAP_A}])
    after = bytes(config.blob)
    changed = {index for index, (old, new) in enumerate(zip(before, after))
               if old != new}
    inside = set(range(mapping.OFF_MACROS,
                       mapping.OFF_MACROS + mapping.MACRO_REGION))
    key_entry = mapping.OFF_KEY_TABLE + mapping.KEY_IDS["m1"] * mapping.KEY_ENTRY
    inside |= {key_entry}
    check("only the page and the bound key move", changed <= inside,
          str(sorted(changed - inside)))
    check("the data version is untouched",
          before[mapping.OFF_DATA_VERSION:mapping.OFF_DATA_VERSION + 2]
          == after[mapping.OFF_DATA_VERSION:mapping.OFF_DATA_VERSION + 2])
    check("the title is untouched", config.title == "Profile", config.title)


def test_a_newer_protocol_keeps_its_macros_elsewhere():
    """From v3.2 the page moves behind commands 172/173/174."""
    blob = blank_blob()
    blob[mapping.OFF_PROTO_VERSION] = 2          # v3.2
    config = mapping.MappingConfig(blob)
    check("v3.2 is not a blob-macro profile", not config.macros_in_blob)
    # Refused only while nothing is attached. A v3.2 profile read off a pad
    # comes with its store and writes perfectly well -- see the tests below.
    check("and writing one with no store attached is refused",
          _raises(lambda: config.set_macro("m1", TAP_A), mapping.ProtocolError))
    check("reading reports none rather than raising", config.macros() == [])


def test_the_macro_limits_move_with_the_protocol_version():
    """Five and 128 are v3.1's numbers, and this project used to hardcode them.

    `GetMaxMacroCount`, `GetMaxMacroActionCount` and `GetMinMacroInterval` are
    one-line functions over `ProtoVersion >= 770`. A Macros page that read a
    constant offered a Vader 5 half the slots its firmware has.
    """
    check("v3.1 is five macros, 128 steps, 10 ms",
          mapping.macro_limits(769) == (5, 128, 10, 2540),
          str(mapping.macro_limits(769)))
    check("v3.2 is ten, 256 and 1 ms",
          mapping.macro_limits(770) == (10, 256, 1, 0xFFFF),
          str(mapping.macro_limits(770)))
    check("the v3.1 step budget is the page's own size, arrived at both ways",
          mapping.MACRO_LIMITS_V31.steps == mapping.MACRO_STEP_BUDGET == 128)

    v31 = mapping.MappingConfig(blank_blob())
    check("a config answers for its own version", v31.macro_limits.slots == 5)
    blob = blank_blob()
    blob[mapping.OFF_PROTO_VERSION] = 2
    check("and a v3.2 one for its",
          mapping.MappingConfig(blob).macro_limits.slots == 10)


def test_the_v32_store_round_trips_through_a_pad():
    """Ten macros at 1 ms, named, read and written over 172/173/174."""
    pad = FakePad(device_type=130)             # a Vader 5, so v3.2 profiles
    config = mapping.read_config(pad, 0)
    check("reading a v3.2 profile attaches its store",
          config.macro_store is not None)
    check("an untouched store holds no macros", config.macros() == [])

    edited = config.copy()
    check("copy() copies the store, rather than sharing it",
          edited.macro_store is not None
          and edited.macro_store is not config.macro_store)
    keys = ["m1", "m2", "m3", "m4", "m5", "m6", "c", "z", "thl", "thr"]
    for index, key in enumerate(keys):
        edited.set_macro(key, [{"delay": 0, "key": "a", "event": mapping.MACRO_PRESS},
                               {"delay": 1, "key": "a", "event": mapping.MACRO_RELEASE},
                               {"delay": 7, "key": "b", "event": mapping.MACRO_PRESS}],
                         macro_type=mapping.MACRO_WHILE_HELD, interval=333,
                         name=f"combo {index}")
    check("ten macros fit, where a v3.1 page holds five",
          len(edited.macros()) == 10, str(len(edited.macros())))

    mapping.write_config(pad, 0, edited, old=config)
    back = mapping.read_config(pad, 0)
    stored = back.macros()
    check("all ten come back", len(stored) == 10, str(len(stored)))
    first = stored[0]
    check("the trigger key survives", first["key"] == "m1", str(first["key"]))
    check("so does the name, which v3.1 has no room for",
          first["name"] == "combo 0", repr(first["name"]))
    check("the repeat interval is per macro and in milliseconds",
          first["interval"] == 333, str(first["interval"]))
    # The measurement that matters most: a 1 ms gap survives. Quantised to the
    # v3.1 tick it would come back as 0 and every macro would play wrong.
    check("a 1 ms gap survives, which 10 ms ticks would round away",
          [step["delay"] for step in first["steps"]] == [0, 1, 7],
          str([step["delay"] for step in first["steps"]]))
    check("the key table was written too",
          back.mapping("m1")[0] == "macro", str(back.mapping("m1")))
    check("and the whole store round-tripped byte for byte",
          bytes(back.macro_store.blob) == bytes(edited.macro_store.blob))


def test_the_v32_store_is_bounded_and_orphan_free():
    pad = FakePad(device_type=130)
    config = mapping.read_config(pad, 0)
    for key in ("m1", "m2", "m3", "m4", "m5", "m6", "c", "z", "thl", "thr"):
        config.set_macro(key, TAP_A)
    check("an eleventh is refused",
          _raises(lambda: config.set_macro("start", TAP_A), ValueError))
    step = {"delay": 0, "key": "a", "event": mapping.MACRO_PRESS}
    check("and so is a batch past 256 steps",
          _raises(lambda: config.macro_store.set_macros(
              [{"key": "m1", "steps": [step] * 257}]), ValueError))
    check("a name longer than the field is refused rather than truncated",
          _raises(lambda: config.macro_store.set_macros(
              [{"key": "m1", "steps": [], "name": "x" * 21}]), ValueError))

    # Same orphan cleanup as the v3.1 page, and it has to reach the store: the
    # key table and the macro bodies are read independently by the firmware, so
    # a body left behind goes on playing underneath the new binding.
    config.set_mapping("m1", "b")
    check("remapping a key away from its macro drops the body",
          len(config.macros()) == 9, str(len(config.macros())))
    check("and the key really moved", config.mapping("m1")[0] == "b")


def test_a_profile_and_its_store_travel_as_one_value():
    """`pack_config` -- what the desktop app carries across its worker thread."""
    apex = mapping.read_config(FakePad(), 0)
    packed = mapping.pack_config(apex)
    check("a v3.1 profile packs to the 840 bytes it always was",
          len(packed) == 840)
    check("and unpacks with no store", mapping.unpack_config(packed).macro_store is None)
    check("byte for byte", bytes(mapping.unpack_config(packed).blob) == bytes(apex.blob))

    vader = mapping.read_config(FakePad(device_type=130), 0)
    packed = mapping.pack_config(vader)
    check("a v3.2 profile packs to the profile plus its store",
          len(packed) == 840 + mapping.MACRO_STORE_BYTES, str(len(packed)))
    again = mapping.unpack_config(packed, 0)
    check("and unpacks into both halves",
          bytes(again.blob) == bytes(vader.blob)
          and bytes(again.macro_store.blob) == bytes(vader.macro_store.blob))

    # The rule that made the obvious split wrong: the pad reports 77 in the
    # package-count byte while its profile is 840 bytes, so `blob[2] * 10` would
    # cut seventy bytes off the end of every profile ever backed up.
    check("the package-count byte is not the split point",
          apex.package_count * 10 != len(apex.blob),
          f"{apex.package_count} * 10 vs {len(apex.blob)}")


def test_the_apply_decision_can_see_a_v32_macro_change():
    """`macro_page` is what the app compares to know it owes command 162."""
    pad = FakePad(device_type=130)
    config = mapping.read_config(pad, 0)
    before = config.macro_page
    edited = config.copy()
    edited.set_macro("m1", TAP_A)
    check("a v3.2 macro edit is visible in macro_page",
          edited.macro_page != before)
    check("and macro_page is the store's bytes, not the blob's dead region",
          edited.macro_page == bytes(edited.macro_store.blob))


def test_cycle_time_is_a_duration():
    config = lighting.read_config(FakePad())
    config.cycle_time = 12
    check("cycle time round-trips", config.cycle_time == 12)
    config.cycle_time = 0
    check("cycle time never reaches zero", config.cycle_time >= 1,
          str(config.cycle_time))


def main():
    for test in (test_packet_framing, test_identity_and_remap, test_turbo,
                 test_title, test_read_write_round_trip,
                 test_write_without_baseline_sends_everything,
                 test_unchanged_write_sends_nothing, test_apply_and_save,
                 test_reading_switches_the_pad, test_browsing_puts_the_pad_back,
                 test_browsing_the_running_profile_restores_nothing,
                 test_a_failed_browse_still_puts_the_pad_back,
                 test_bad_checksum_is_rejected, test_vibration_intensity,
                 test_trigger_effect_and_curve,
                 test_the_labels_follow_space_station_not_the_enum,
                 test_every_effect_round_trips_through_the_profile,
                 test_the_effect_block_matches_flydigis_layout,
                 test_switching_to_general_keeps_the_numbers,
                 test_an_effect_reads_its_own_defaults_out_of_a_foreign_slot,
                 test_live_effect_payloads_match_the_command_builders,
                 test_a_stored_effect_is_replayed_as_the_live_command_flydigi_builds,
                 test_the_trigger_motor_fields_land_where_flydigi_puts_them,
                 test_the_motor_strength_is_a_percentage_not_a_byte,
                 test_the_amplitude_window_cannot_be_inverted,
                 test_the_factory_motion_block_is_off_but_not_blank,
                 test_the_motion_block_matches_flydigis_writer,
                 test_the_use_mode_follows_the_target,
                 test_the_second_enable_key_is_written_only_under_hold,
                 test_a_mouse_mapping_is_written_the_way_flydigi_writes_one,
                 test_the_motion_curve_is_the_joystick_curve_without_its_type,
                 test_editing_the_gyro_does_not_disturb_its_neighbours,
                 test_the_factory_curves_are_the_identity_line,
                 test_moving_the_trigger_window_moves_its_points,
                 test_the_joystick_type_is_written_into_both_blocks,
                 test_the_bank_is_exactly_nine_points,
                 test_the_negative_half_of_center_and_edge_is_refused,
                 test_a_stick_mapped_to_something_else_is_not_a_dead_zone_of_127,
                 test_the_compiler_reproduces_the_pads_own_bank,
                 test_a_dead_zone_reaches_the_bank,
                 test_a_big_dead_zone_does_not_invert_the_curve,
                 test_the_two_dead_zones_cannot_eat_more_travel_than_exists,
                 test_the_presets_compile_to_different_curves,
                 test_editing_a_stick_writes_the_bank_too,
                 test_choosing_a_preset_restores_its_whole_shape,
                 test_editing_extras_does_not_disturb_buttons,
                 test_targets_exclude_buttons_xinput_cannot_send,
                 test_a_macro_round_trips,
                 test_a_macro_binds_its_key_and_gives_it_back,
                 test_remapping_a_key_away_from_its_macro_drops_the_body,
                 test_the_macro_page_matches_flydigis_writer,
                 test_macro_delays_are_quantised_not_summed,
                 test_the_macro_page_is_bounded,
                 test_a_macro_step_cannot_send_what_xinput_has_no_id_for,
                 test_macros_are_read_permissively,
                 test_a_count_outside_the_page_reads_as_nothing,
                 test_the_interval_belongs_to_the_slot_not_the_macro,
                 test_editing_macros_does_not_disturb_its_neighbours,
                 test_a_newer_protocol_keeps_its_macros_elsewhere,
                 test_the_macro_limits_move_with_the_protocol_version,
                 test_the_v32_store_round_trips_through_a_pad,
                 test_the_v32_store_is_bounded_and_orphan_free,
                 test_a_profile_and_its_store_travel_as_one_value,
                 test_the_apply_decision_can_see_a_v32_macro_change,
                 test_lighting_round_trip, test_lighting_brightness_is_clamped,
                 test_lighting_effects_write_frames, test_cycle_time_is_a_duration,
                 test_a_models_keys_are_its_own,
                 test_a_save_carries_the_version_it_is_given,
                 test_nothing_saves_a_profile_without_its_version,
                 test_command_175_resets_every_profile_not_the_one_it_is_given,
                 test_restoring_one_profile_writes_the_factory_bytes_instead,
                 test_the_factory_blob_is_one_blob_and_a_digit,
                 test_the_force_trigger_family_is_gated_on_the_hardware,
                 test_a_restore_writes_this_model_s_profile_and_not_the_other_s,
                 test_a_model_with_no_factory_profile_is_refused_the_per_slot_restore,
                 test_a_switch_save_is_aimed_at_the_second_bank,
                 test_a_switch_save_commits_the_running_profile_under_a_new_tag,
                 test_the_switch_save_frame_is_flydigis_own_odd_one,
                 test_a_switch_profile_drops_what_a_switch_cannot_run,
                 test_effects_by_id_and_colours, test_suggested_colours_differ):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
