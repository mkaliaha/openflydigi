#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for mapping profiles. No controller required -- see fake_pad.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import device, effects, lighting, mapping
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

        def command(self, cmd_id, payload=b"", wait=0.3):
            self.sent.append((cmd_id, list(payload)))
            return []

        def send(self, buf, wait=0.3):
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
    for key in ("m1", "m2", "m3", "m4", "c", "z"):
        check(f"{key} is not offered as a target",
              key not in mapping.XINPUT_TARGETS)
        check(f"{key} is still a source", key in mapping.APEX5_KEYS)
    for key in ("a", "b", "lb", "start"):
        check(f"{key} is a valid target", key in mapping.XINPUT_TARGETS)


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


def _raises(call):
    try:
        call()
    except ValueError:
        return True
    return False


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
                 test_every_effect_round_trips_through_the_profile,
                 test_the_effect_block_matches_flydigis_layout,
                 test_switching_to_general_keeps_the_numbers,
                 test_an_effect_reads_its_own_defaults_out_of_a_foreign_slot,
                 test_live_effect_payloads_match_the_command_builders,
                 test_the_trigger_motor_fields_land_where_flydigi_puts_them,
                 test_the_motor_strength_is_a_percentage_not_a_byte,
                 test_the_amplitude_window_cannot_be_inverted,
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
                 test_lighting_round_trip, test_lighting_brightness_is_clamped,
                 test_lighting_effects_write_frames, test_cycle_time_is_a_duration,
                 test_effects_by_id_and_colours, test_suggested_colours_differ):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
