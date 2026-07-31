#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the CD2 charging dock.

    python3 tests/test_charger.py

Two things here are covered by a fake and by nothing else, because the hardware
cannot show them:

  * **Two docks.** Only one has ever been on this bus, so every multi-device
    path -- picking by uid, refusing an ambiguous prefix, listing one that will
    not answer -- is exercised against `FakeDock` and would otherwise be
    written blind and shipped untried.
  * **A dock that is not a CD2.** The gen-1 dock is a different device that
    must not be driven as one, and `require` is what stands between them. A
    fake reporting an unknown charger type is the only way to run that branch.

The rest is checked against what the real dock answered, quoted where it
matters. `flydigi/charger.py` records which findings came off hardware.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import charger, device  # noqa: E402
from fake_dock import FakeDock       # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------

def test_a_command_is_the_packet_flydigi_builds():
    """Byte for byte against a heartbeat the real dock answered."""
    packet = charger.build(charger.CMD_HEARTBEAT)
    check("heartbeat is 32 bytes", len(packet) == 32, len(packet))
    check("heartbeat bytes", bytes(packet[:6]) == bytes.fromhex("005aa5010203"),
          bytes(packet[:6]).hex(" "))
    check("report id is 0 and not the pad's 3", packet[0] == 0)

    # A switch write: one payload byte, so length 3 and the checksum one along.
    flag = charger.build(charger.CMD_LED_SYNC, b"\x01")
    check("switch write bytes", bytes(flag[:7]) == bytes.fromhex("005aa512030116"),
          bytes(flag[:7]).hex(" "))
    check("checksum sits at 3 + length", flag[3 + flag[4]] == flag[6])


def test_a_pack_command_is_64_bytes_with_its_checksum_at_the_end():
    pack = bytes(range(50))
    head = bytes((0, 10, 0, 3, len(pack)))
    packet = charger.build(charger.CMD_WRITE_LED_PACK, head + pack,
                           size=charger.PACK_PACKET_LEN)
    check("pack buffer is 64", len(packet) == 64, len(packet))
    check("pack length byte", packet[4] == len(pack) + 7, packet[4])
    check("pack data starts at 10", bytes(packet[10:60]) == pack)
    check("pack checksum at packLen + 10", packet[60] == sum(packet[3:60]) & 0xFF)


def test_a_bad_checksum_gets_no_reply_at_all():
    """The dock's own behaviour: silence, not an error reply."""
    dock = FakeDock()
    packet = bytearray(charger.build(charger.CMD_HEARTBEAT))
    packet[5] ^= 0xFF
    check("corrupt packet is ignored", dock.send(packet) == [])
    check("and is counted as corrupt", dock.bad_checksums == 1)
    check("a good one still answers", dock.send(charger.build(charger.CMD_HEARTBEAT)))


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def test_the_reply_a_real_dock_gave_decodes_field_by_field():
    """The captured heartbeat, byte for byte. Every field below came off it."""
    reply = bytes.fromhex(
        "5aa50114010000cdab52efe7bc065801003901010100 0d".replace(" ", ""))
    check("reply checksum is at 2 + length",
          reply[2 + reply[3]] == sum(reply[2:2 + reply[3]]) & 0xFF)

    dock = FakeDock()
    info = charger.read_info(dock)
    check("device type", info["device_type"] == 0, info)
    check("firmware", info["firmware"] == "0.0.3.9", info["firmware"])
    check("chip type", info["chip_type"] == 1, info["chip_type"])
    check("sleep when charging", info["sleep_when_charging"] is True)
    check("lighting sync", info["led_sync"] is True)
    check("close with system", info["close_with_system"] is True)
    check("power display", info["show_animation_when_charging"] is False)


def test_an_all_zero_firmware_reads_as_not_reported():
    """Flydigi treat an all-zero version as "not reported", not as zero.

    The version is the dock's own now rather than a module constant a test
    reaches in and swaps, because two mock docks on one bus may run different
    firmware -- and a test that patched a global was testing the patch as much
    as the parser.
    """
    check("all-zero firmware is None",
          charger.read_info(FakeDock(firmware=(0x00, 0x00)))["firmware"] is None)
    check("and a real one still parses",
          charger.read_info(FakeDock(firmware="0.0.3.9"))["firmware"] == "0.0.3.9")


def test_uid_and_nickname():
    dock = FakeDock()
    check("uid is 13 bytes of hex",
          charger.read_uid(dock) == "1960f0f1f2cdab52efe7bc0658",
          charger.read_uid(dock))
    check("an unset nickname reads as None",
          charger.read_nickname(dock) is None)
    # Only a prefix: Flydigi's slice is `data[3] - 3` long, which runs one byte
    # past the name onto the checksum. Reproduced faithfully rather than
    # quietly corrected, and no dock here has a name set to measure against.
    named = charger.read_nickname(FakeDock(nickname="desk"))
    check("a set one comes back", named.startswith("desk"), repr(named))


def test_the_led_read_uses_a_different_field_order_from_the_write():
    """Flydigi transpose them, and getting it backwards is silent."""
    dock = FakeDock()
    config = charger.read_led_config(dock)
    check("mode", config.mode == charger.MODE_PULSE, config.mode)
    check("brightness", config.brightness == 50, config.brightness)
    check("period", config.period == 2, config.period)
    check("colours", config.colours == [(0, 116, 255)], config.colours)

    written = charger.serialise(config)
    check("a write puts frame count first", written[0] == 0, written[0])
    check("then period, then brightness, then mode",
          (written[1], written[2], written[3]) == (2, 50, charger.MODE_PULSE),
          written[:6].hex(" "))


def test_the_status_report_is_told_apart_from_an_ack():
    captured = bytes.fromhex("5aa5ef0801003900010032")
    parsed = charger.parse_status(captured)
    check("status report parses", parsed == {"docked": False, "battery": 1},
          parsed)
    check("an ack is not a status report",
          charger.parse_status(bytes.fromhex("5aa50102 03".replace(" ", ""))) is None)


def test_a_dock_that_says_nothing_raises():
    class Mute(FakeDock):
        def send(self, buf, wait=0.3, until=None):
            return []
    try:
        charger.read_info(Mute())
        check("a mute dock raises", False)
    except charger.ProtocolError:
        check("a mute dock raises", True)


# --------------------------------------------------------------------------
# The generators
# --------------------------------------------------------------------------

def test_every_generator_fills_162_leds():
    for mode, name in charger.MODE_NAMES.items():
        if mode in (charger.MODE_DEFAULT, charger.MODE_CUSTOM):
            continue
        config = charger.LedConfig(mode=mode)
        charger.generate(config)
        check(f"{name} produces frames", config.frames, name)
        widths = {len(frame) for frame in config.frames}
        check(f"{name} frames are 162 LEDs", widths == {charger.LED_COUNT},
              widths)
        bad = [c for frame in config.frames for c in frame
               if len(c) != 3 or not all(0 <= v <= 255 for v in c)]
        check(f"{name} channels are bytes", not bad, bad[:2])


def test_frame_counts_match_the_reference():
    counts = {charger.MODE_CLOSE: 1, charger.MODE_SOLID: 1,
              charger.MODE_GRADIENT: 50, charger.MODE_RAINBOW: 50,
              charger.MODE_WAVE_GRADIENT: 50, charger.MODE_DIAGONAL_FLOW: 50,
              charger.MODE_PULSE: 50}
    for mode, want in counts.items():
        config = charger.LedConfig(mode=mode)
        charger.generate(config)
        check(f"{charger.MODE_NAMES[mode]} has {want} frames",
              len(config.frames) == want, len(config.frames))


def test_breath_length_follows_its_colour_and_black_does_not_crash():
    """The fade scales what is left by 1 - step/50, so a darker colour is
    shorter. Black never enters the loop, and Flydigi's `shift()` on the
    emptied array is a no-op where popping index 0 would raise."""
    for colour, want in (((0, 116, 255), 40), ((255, 255, 255), 40),
                         ((3, 3, 3), 6), ((1, 0, 0), 2), ((0, 0, 0), 1)):
        config = charger.LedConfig(mode=charger.MODE_BREATH, colours=[colour])
        charger.generate(config)
        check(f"breath {colour} is {want} frames",
              len(config.frames) == want, len(config.frames))
    empty = charger.LedConfig(mode=charger.MODE_BREATH, colours=[])
    charger.generate(empty)
    check("breath with no colour makes nothing", empty.frames == [])


def test_breath_starts_at_the_colour_reaches_black_and_comes_back():
    config = charger.LedConfig(mode=charger.MODE_BREATH, colours=[(255, 0, 0)])
    charger.generate(config)
    frames = config.frames
    check("starts at the colour", frames[0][0] == (255, 0, 0), frames[0][0])
    darkest = min(range(len(frames)), key=lambda i: sum(frames[i][0]))
    check("reaches black", frames[darkest][0] == (0, 0, 0), frames[darkest][0])
    check("mirror does not repeat the endpoints",
          frames[-1] != frames[0] and frames[darkest] not in
          (frames[darkest + 1:] and [frames[darkest + 1]] or []),
          (frames[0][0], frames[-1][0]))


def test_close_is_black_and_solid_is_one_colour():
    close = charger.LedConfig(mode=charger.MODE_CLOSE)
    charger.generate(close)
    check("close is one black frame",
          set(close.frames[0]) == {(0, 0, 0)}, set(close.frames[0]))
    solid = charger.LedConfig(mode=charger.MODE_SOLID, colours=[(1, 2, 3)])
    charger.generate(solid)
    check("solid is one flat frame",
          set(solid.frames[0]) == {(1, 2, 3)}, set(solid.frames[0]))
    bare = charger.LedConfig(mode=charger.MODE_SOLID, colours=[])
    charger.generate(bare)
    check("solid with no colour falls back to Flydigi's #212225",
          set(bare.frames[0]) == {charger.FALLBACK_COLOUR})


def test_direction_changes_rainbow_and_wave_and_nothing_else():
    def first_frame(mode, direction):
        config = charger.LedConfig(mode=mode, direction=direction)
        charger.generate(config)
        return config.frames[0]

    for mode in (charger.MODE_RAINBOW, charger.MODE_WAVE_GRADIENT):
        left = first_frame(mode, charger.DIR_LEFT)
        right = first_frame(mode, charger.DIR_RIGHT)
        check(f"{charger.MODE_NAMES[mode]} reads direction", left != right)
    for mode in (charger.MODE_PULSE, charger.MODE_BREATH,
                 charger.MODE_GRADIENT, charger.MODE_DIAGONAL_FLOW):
        left = first_frame(mode, charger.DIR_LEFT)
        right = first_frame(mode, charger.DIR_RIGHT)
        check(f"{charger.MODE_NAMES[mode]} ignores direction", left == right)


def test_the_geometric_effects_are_centred_on_the_lattice():
    """Pulse is a radial wave, so the LED nearest the centre and the one
    furthest from it cannot be in step for every frame."""
    config = charger.LedConfig(mode=charger.MODE_PULSE, colours=[(255, 0, 0)])
    charger.generate(config)
    centre = [frame[114][0] for frame in config.frames]
    corner = [frame[0][0] for frame in config.frames]
    check("centre and edge are out of phase", centre != corner)
    check("both actually vary", len(set(centre)) > 5 and len(set(corner)) > 5)


def test_the_mode_that_needs_a_file_is_refused_rather_than_faked():
    config = charger.LedConfig(mode=charger.MODE_DEFAULT)
    try:
        charger.generate(config)
        check("default is refused", False)
    except charger.ProtocolError as exc:
        check("default is refused", "Space Station" in str(exc), exc)


def test_custom_keeps_the_frames_it_was_given():
    frames = [[(9, 8, 7)] * charger.LED_COUNT]
    config = charger.LedConfig(mode=charger.MODE_CUSTOM, frames=frames)
    charger.generate(config)
    check("custom is left alone", config.frames == frames)


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def test_a_write_arrives_whole_and_parses_back():
    dock = FakeDock()
    config = charger.LedConfig(mode=charger.MODE_BREATH, brightness=80,
                              period=4, colours=[(255, 0, 0)])
    charger.generate(config)
    packs = charger.write_led_config(dock, config)

    blob = charger.serialise(config)
    check("every pack arrived", packs == len(dock.packs), (packs, len(dock.packs)))
    check("the bytes reassemble exactly", dock.led_blob == blob)
    check("the dock read back the header the write meant",
          (dock.led.mode, dock.led.brightness, dock.led.period)
          == (charger.MODE_BREATH, 80, 4),
          (dock.led.mode, dock.led.brightness, dock.led.period))
    check("the frame count on the wire matches the frames sent",
          dock.frame_count == len(config.frames),
          (dock.frame_count, len(config.frames)))
    check("the frames survive the round trip",
          dock.frames == config.frames)


def test_the_advertised_pack_count_is_the_one_actually_sent():
    """Flydigi advertise `len // 50 + 1` while sending `ceil(len / 50)`, so a
    blob that divides by 50 exactly promises the dock one pack more than it
    gets. Four custom frames is 1950 bytes and hits it."""
    frames = [[(1, 2, 3)] * charger.LED_COUNT for _ in range(4)]
    config = charger.LedConfig(mode=charger.MODE_CUSTOM, frames=frames)
    blob = charger.serialise(config)
    check("the awkward size is reachable", len(blob) == 1950, len(blob))
    check("Flydigi would advertise 40", len(blob) // 50 + 1 == 40)

    dock = FakeDock()
    packs = charger.write_led_config(dock, config)
    check("we advertise 39", packs == 39, packs)
    check("and send 39", len(dock.packs) == 39, len(dock.packs))
    check("so the dock sees the count it was promised",
          dock.advertised_packs == len(dock.packs))
    check("and the blob still reassembles", dock.led_blob == blob)


def test_a_short_final_pack_is_sent_short_rather_than_padded():
    dock = FakeDock()
    config = charger.LedConfig(mode=charger.MODE_CLOSE)
    charger.generate(config)
    charger.write_led_config(dock, config)
    blob = charger.serialise(config)
    tail = len(blob) % charger.PACK_BYTES
    check("this blob has a short tail", tail not in (0,), tail)
    check("and the last pack carries exactly that many bytes",
          len(dock.packs[-1][1]) == tail, len(dock.packs[-1][1]))


def test_a_write_is_held_for_the_whole_stream():
    dock = FakeDock()
    config = charger.LedConfig(mode=charger.MODE_CLOSE)
    charger.generate(config)
    charger.write_led_config(dock, config)
    check("the stream is claimed once, not per packet", dock.claims == 1,
          dock.claims)


def test_a_dock_that_stops_acking_mid_stream_says_so():
    class Flaky(FakeDock):
        def send(self, buf, wait=0.3, until=None):
            if len(self.packs) >= 3 and bytes(buf)[3] == charger.CMD_WRITE_LED_PACK:
                return []
            return super().send(buf, wait=wait, until=until)

    dock = Flaky()
    config = charger.LedConfig(mode=charger.MODE_PULSE)
    charger.generate(config)
    try:
        charger.write_led_config(dock, config)
        check("a stalled write raises", False)
    except charger.ProtocolError as exc:
        check("a stalled write raises", "pack 3" in str(exc), exc)
        check("and warns the frame memory is now partial",
              "partial" in str(exc), exc)


def test_each_switch_writes_and_reads_back():
    dock = FakeDock()
    for setter, key, cmd in (
            (charger.set_sleep_when_charging, "sleep_when_charging",
             charger.CMD_SLEEP_WHEN_CHARGING),
            (charger.set_led_sync, "led_sync", charger.CMD_LED_SYNC),
            (charger.set_close_with_system, "close_with_system",
             charger.CMD_CLOSE_WITH_SYSTEM),
            (charger.set_show_animation_when_charging,
             "show_animation_when_charging", charger.CMD_SHOW_ANIMATION)):
        for value in (False, True, False):
            setter(dock, value)
            check(f"{key} -> {value}",
                  charger.read_info(dock)[key] is value)
            check(f"{key} reached command {cmd}", dock.settings[cmd] is value)


def test_the_two_mutually_exclusive_switches_are_not_enforced_here():
    """Space Station's UI forces one off when the other goes on. Nothing in
    the SDK does, so this does not either -- a caller with a UI can."""
    dock = FakeDock()
    charger.set_sleep_when_charging(dock, True)
    charger.set_show_animation_when_charging(dock, True)
    info = charger.read_info(dock)
    check("both can be on at the wire level",
          info["sleep_when_charging"] and info["show_animation_when_charging"])


# --------------------------------------------------------------------------
# Which dock, and whether it is one at all
# --------------------------------------------------------------------------

def test_a_dock_that_is_not_a_cd2_is_refused():
    check("a CD2 passes", charger.require(FakeDock(device_type=0))["device_type"] == 0)
    for edition in (1, 2, 3, 4):
        check(f"CD2 edition {edition} passes",
              charger.require(FakeDock(device_type=edition)) is not None)
    try:
        charger.require(FakeDock(device_type=7))
        check("an unknown charger type is refused", False)
    except charger.WrongDock as exc:
        check("an unknown charger type is refused", "not a CD2" in str(exc))
        check("and the message names what it found", "7" in str(exc), exc)


def test_the_product_names_are_flydigis_own():
    check("type 0 is the plain dock",
          charger.name_for(0) == "Controller Charging Dock 2 Pro")
    check("type 1 is the EVA edition", "EVA" in charger.name_for(1))
    check("an unknown type has no name", charger.name_for(9) is None)


def test_two_docks_are_told_apart_by_uid(monkeypatched):
    """Never measured -- only one dock has ever been on this bus."""
    first = FakeDock(uid=bytes.fromhex("11" * 13), path="/dev/hidraw3")
    second = FakeDock(uid=bytes.fromhex("22" * 13), path="/dev/hidraw9")
    monkeypatched(first, second)

    listed = charger.list_docks()
    check("both are listed", len(listed) == 2, len(listed))
    check("in node order",
          [d["path"] for d in listed] == ["/dev/hidraw3", "/dev/hidraw9"])
    check("each with its own uid",
          [d["uid"] for d in listed] == ["11" * 13, "22" * 13])

    check("a uid prefix picks one",
          charger.open_dock(uid="2222").path == "/dev/hidraw9")
    check("a full uid picks one",
          charger.open_dock(uid="11" * 13).path == "/dev/hidraw3")
    check("bare takes the first", charger.open_dock().path == "/dev/hidraw3")
    check("a node picks one", charger.open_dock("/dev/hidraw9").path
          == "/dev/hidraw9")


def test_an_ambiguous_or_absent_uid_is_refused(monkeypatched):
    first = FakeDock(uid=bytes.fromhex("aa" * 13), path="/dev/hidraw3")
    second = FakeDock(uid=bytes.fromhex("aa" * 12 + "bb"), path="/dev/hidraw9")
    monkeypatched(first, second)
    try:
        charger.open_dock(uid="aa")
        check("an ambiguous prefix is refused", False)
    except device.DeviceNotFound as exc:
        check("an ambiguous prefix is refused", "more than one" in str(exc))
    try:
        charger.open_dock(uid="ff")
        check("an absent uid is refused", False)
    except device.DeviceNotFound as exc:
        check("an absent uid is refused", "no charging dock" in str(exc))


def test_a_dock_that_will_not_answer_is_listed_rather_than_hidden(monkeypatched):
    class Mute(FakeDock):
        def send(self, buf, wait=0.3, until=None):
            return []
    working = FakeDock(path="/dev/hidraw3")
    mute = Mute(path="/dev/hidraw9")
    monkeypatched(working, mute)
    listed = charger.list_docks()
    check("both appear", len(listed) == 2, len(listed))
    check("the mute one is marked rather than dropped",
          listed[1]["info"] is None and listed[1]["path"] == "/dev/hidraw9")


# --------------------------------------------------------------------------

def with_fake_docks(test):
    """Run `test` with `find_nodes` and `Dock` pointed at fakes.

    The nodes are never opened, so the paths are labels. This is the only way
    the two-dock paths get run at all: there is one dock here.
    """
    def run():
        installed = {}

        def monkeypatched(*docks):
            installed.update({dock.path: dock for dock in docks})

        real_find, real_dock = device.find_nodes, charger.Dock
        device.find_nodes = lambda family=device.FAMILY_PAD: (
            iter(sorted(installed)) if family == device.FAMILY_DOCK else iter(()))
        charger.Dock = lambda path=None: installed[path or sorted(installed)[0]]
        try:
            test(monkeypatched)
        finally:
            device.find_nodes, charger.Dock = real_find, real_dock
    run.__name__ = test.__name__
    return run


def main():
    for test in (test_a_command_is_the_packet_flydigi_builds,
                 test_a_pack_command_is_64_bytes_with_its_checksum_at_the_end,
                 test_a_bad_checksum_gets_no_reply_at_all,
                 test_the_reply_a_real_dock_gave_decodes_field_by_field,
                 test_an_all_zero_firmware_reads_as_not_reported,
                 test_uid_and_nickname,
                 test_the_led_read_uses_a_different_field_order_from_the_write,
                 test_the_status_report_is_told_apart_from_an_ack,
                 test_a_dock_that_says_nothing_raises,
                 test_every_generator_fills_162_leds,
                 test_frame_counts_match_the_reference,
                 test_breath_length_follows_its_colour_and_black_does_not_crash,
                 test_breath_starts_at_the_colour_reaches_black_and_comes_back,
                 test_close_is_black_and_solid_is_one_colour,
                 test_direction_changes_rainbow_and_wave_and_nothing_else,
                 test_the_geometric_effects_are_centred_on_the_lattice,
                 test_the_mode_that_needs_a_file_is_refused_rather_than_faked,
                 test_custom_keeps_the_frames_it_was_given,
                 test_a_write_arrives_whole_and_parses_back,
                 test_the_advertised_pack_count_is_the_one_actually_sent,
                 test_a_short_final_pack_is_sent_short_rather_than_padded,
                 test_a_write_is_held_for_the_whole_stream,
                 test_a_dock_that_stops_acking_mid_stream_says_so,
                 test_each_switch_writes_and_reads_back,
                 test_the_two_mutually_exclusive_switches_are_not_enforced_here,
                 test_a_dock_that_is_not_a_cd2_is_refused,
                 test_the_product_names_are_flydigis_own,
                 with_fake_docks(test_two_docks_are_told_apart_by_uid),
                 with_fake_docks(test_an_ambiguous_or_absent_uid_is_refused),
                 with_fake_docks(
                     test_a_dock_that_will_not_answer_is_listed_rather_than_hidden)):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
