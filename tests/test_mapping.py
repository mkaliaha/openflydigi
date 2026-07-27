#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for mapping profiles. No controller required -- see fake_pad.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import device, lighting, mapping
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

    config.set_trigger_motor("left", enabled=True, minimum=10, maximum=90, scale=70)
    check("trigger motor round-trips",
          config.trigger_motor("left") == (True, 10, 90, 70),
          str(config.trigger_motor("left")))


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
                 test_bad_checksum_is_rejected, test_vibration_intensity,
                 test_trigger_effect_and_curve,
                 test_editing_extras_does_not_disturb_buttons,
                 test_targets_exclude_buttons_xinput_cannot_send,
                 test_lighting_round_trip, test_lighting_brightness_is_clamped,
                 test_lighting_effects_write_frames, test_cycle_time_is_a_duration):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
