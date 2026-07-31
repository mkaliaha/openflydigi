#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the device-settings block: command 3, 19, 20..23 and 29.

The load-bearing case is `test_the_reply_a_real_pad_gave_decodes_feature_by_feature`,
which decodes the thirteen bytes a wired Apex 5 actually answered and checks
every field against the table in docs/device-settings.md. The rest goes through
the fake pad, which models two things the hardware taught us: a command-19 reply
echoes the value and never the sub-id, and an unsupported setting acknowledges
without changing anything.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import blobs, device, settings
from tests.fake_pad import FakePad

PASSED = []
FAILED = []

# The reply an Apex 5 gave, recorded in docs/device-settings.md. Everything here
# that claims to know a bit position is checked against this rather than against
# the fake, which was written from the same understanding and so cannot confirm
# it.
HARDWARE_REPLY = bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


# -- reading ---------------------------------------------------------------


def test_the_reply_a_real_pad_gave_decodes_feature_by_feature():
    state = settings.parse_status(HARDWARE_REPLY)
    # supported 251 = everything but motion debounce; enabled 123 = the same
    # set with the status bar off.
    for name in ("quick_switch", "xbox_home", "mapping_switch", "stick_debounce",
                 "auto_calibration", "stick_rebound", "status_bar_always_on"):
        check(f"{name} is supported", state[f"{name}_usable"], str(state))
    check("motion debounce is not supported", not state["motion_debounce_usable"])
    check("audio is not supported", not state["audio_usable"])
    check("the always-on display is supported", state["always_on_usable"])

    check("quick switch was on", state["quick_switch"])
    check("the status bar was off", not state["status_bar_always_on"])
    check("the panel was dark when idle", not state["always_on"])

    check("sleep time is 15 minutes", state["sleep_minutes"] == 15,
          str(state["sleep_minutes"]))
    check("report rate reads 0", state["report_rate"] == 0)
    check("precision reads 2", state["precision"] == 2)
    check("sensitivity reads 17", state["sensitivity"] == 17)


def test_precision_is_in_declaration_order_not_by_bit_depth():
    """The trap in `JoystickPrecision`: 9- and 11-bit were added after 8/10/12.

    So the pad's 2 is 10-bit. A mapping that assumed the number climbs with
    resolution would call it 12-bit, which is the wrong answer by two bits and
    reads as plausible.
    """
    check("2 is 10-bit", settings.precision_name(2) == "10-bit",
          settings.precision_name(2))
    check("3 is 12-bit", settings.precision_name(3) == "12-bit")
    check("4 is 9-bit, not 16", settings.precision_name(4) == "9-bit",
          settings.precision_name(4))
    check("7 is 16-bit", settings.precision_name(7) == "16-bit")
    check("0 is not a resolution", "unknown" in settings.precision_name(0),
          settings.precision_name(0))
    check("a value off the end does not raise", "unknown" in settings.precision_name(99))


def test_sensitivity_and_report_rate_read_as_words():
    check("17 is Middle", settings.sensitivity_name(17) == "Middle")
    check("14 is Highest", settings.sensitivity_name(14) == "Highest")
    check("20 is Lowest", settings.sensitivity_name(20) == "Lowest")
    check("1 is 1000 Hz", settings.report_rate_hz(1) == 1000)
    check("8 is 125 Hz", settings.report_rate_hz(8) == 125)
    # 0 is what this pad reports and is not in Flydigi's map. None, rather than
    # a guessed 1000: the endpoints argue for "default" but nothing measures it.
    check("0 is not a documented rate", settings.report_rate_hz(0) is None)

    described = settings.describe(settings.parse_status(HARDWARE_REPLY))
    check("the block describes itself",
          described == {"sleep": "15 min", "report_rate": "default (0)",
                        "precision": "10-bit", "sensitivity": "Middle"},
          str(described))
    never = dict(settings.parse_status(HARDWARE_REPLY), sleep_minutes=0)
    check("sleep 0 is never", settings.describe(never)["sleep"] == "never")


def test_a_short_or_foreign_reply_is_refused():
    for body, why in ((HARDWARE_REPLY[:9], "a truncated reply"),
                      (bytes([90, 165, 19, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0]),
                       "some other command's reply")):
        try:
            settings.parse_status(body)
            check(f"{why} is refused", False)
        except settings.SettingsError:
            check(f"{why} is refused", True)


def test_reading_from_a_pad_that_says_nothing_raises():
    class Silent:
        def send(self, buf, wait=0.3, until=None):
            return []

    try:
        settings.read_status(Silent())
        check("silence is an error, not an empty block", False)
    except settings.SettingsError as exc:
        check("silence is an error, not an empty block", "asleep" in str(exc), str(exc))


# -- the sub-command map ---------------------------------------------------


def test_sub_ids_and_bit_positions_are_one_list_read_twice():
    """Sub-id N is bit N-1. Asserted, because the two are used far apart.

    The decoder derives the bit from the sub-id, so this is what stops a
    renumbering from silently moving every feature one place along.
    """
    check("quick switch is sub 1", settings.SUB_IDS["quick_switch"] == 1)
    check("the status bar is sub 8", settings.SUB_IDS["status_bar_always_on"] == 8)
    check("the always-on display is sub 9", settings.SUB_IDS["always_on"] == 9)
    check("audio is sub 10", settings.SUB_IDS["audio"] == 10)
    check("ten features, no more", len(settings.FEATURES) == 10)

    # Every feature's own bit, one at a time: a reply with just that bit set
    # must decode to that feature and nothing else. The bit is worked out here
    # from the rule rather than from the module's own helper, so this checks the
    # decoder against the wire layout instead of against itself.
    for name, sub_id in settings.SUB_IDS.items():
        body = bytearray(13)
        body[0], body[1], body[2] = 90, 165, settings.CMD_STATUS
        if sub_id <= 8:
            body[6] = 1 << (sub_id - 1)          # data[6]: the first enabled byte
        else:
            body[8] = 1 << (sub_id - 9)          # data[8]: the second
        state = settings.parse_status(bytes(body))
        on = [key for key in settings.FEATURES if state[key]]
        check(f"bit for {name} decodes alone", on == [name], str(on))


def test_a_setting_write_is_the_packet_flydigi_builds():
    """Command 19 is `[4]=4, [5]=subId, [6]=value, [7]=crc`."""
    buf = blobs.build(settings.CMD_SETTING,
                      bytes([settings.SUB_QUICK_SWITCH, 1]))
    check("command byte", buf[3] == 19, str(buf[3]))
    check("length counts itself and the command", buf[4] == 4, str(buf[4]))
    check("sub-id then value", (buf[5], buf[6]) == (1, 1))
    check("checksum at 7", buf[7] == device.checksum(buf, 3, 7), buf[:9].hex())

    sleep = blobs.build(settings.CMD_SLEEP, bytes([30]))
    check("a standalone command is one byte shorter", sleep[4] == 3, str(sleep[4]))
    check("value then checksum", (sleep[5], sleep[6]) == (30, device.checksum(sleep, 3, 6)))

    restart = blobs.build(settings.CMD_RESTART)
    check("restart takes no argument", restart[4] == 2, str(restart[4]))
    check("restart's checksum lands at 5",
          restart[5] == device.checksum(restart, 3, 5))


# -- writing ---------------------------------------------------------------


def test_each_feature_writes_and_reads_back():
    pad = FakePad()
    check("quick switch starts on", settings.read_status(pad)["quick_switch"])
    state = settings.apply(pad, "quick_switch", False)
    check("turning it off takes", not state["quick_switch"], str(state))
    check("and nothing else moved", state["stick_debounce"] and state["mapping_switch"])
    state = settings.apply(pad, "quick_switch", True)
    check("and back on", state["quick_switch"])


def test_an_unsupported_setting_acks_and_does_nothing():
    """The pad's rule everywhere: an ACK means the firmware parsed the packet.

    Motion debounce is unsupported on this pad, and the write is still
    acknowledged -- so a caller trusting the ACK would report a feature this
    hardware does not have as switched on.
    """
    pad = FakePad()
    check("the pad acknowledges it",
          settings.set_feature(pad, settings.SUB_MOTION_DEBOUNCE, True))
    state = settings.read_status(pad)
    check("and it is still off", not state["motion_debounce"])
    check("and still unsupported", not state["motion_debounce_usable"])


def test_the_numeric_settings_go_through_their_own_commands():
    pad = FakePad()
    state = settings.apply(pad, "sleep_minutes", 30)
    check("sleep time is written", state["sleep_minutes"] == 30, str(state["sleep_minutes"]))
    check("the pad holds it", pad.sleep_minutes == 30)

    state = settings.apply(pad, "precision", 3)
    check("precision is written", state["precision"] == 3)
    check("and reads as 12-bit", settings.precision_name(state["precision"]) == "12-bit")

    state = settings.apply(pad, "sensitivity", 14)
    check("sensitivity is written", state["sensitivity"] == 14)
    check("and reads as Highest", settings.sensitivity_name(state["sensitivity"]) == "Highest")

    state = settings.apply(pad, "report_rate", 1)
    check("report rate is written", state["report_rate"] == 1)
    check("and reads as 1000 Hz", settings.report_rate_hz(state["report_rate"]) == 1000)


def test_sleep_time_is_clamped_to_what_the_picker_offers():
    pad = FakePad()
    settings.set_sleep_minutes(pad, 999)
    check("an hour is the ceiling", pad.sleep_minutes == settings.SLEEP_MAX_MINUTES,
          str(pad.sleep_minutes))
    settings.set_sleep_minutes(pad, -5)
    check("and never is the floor", pad.sleep_minutes == 0, str(pad.sleep_minutes))


def test_applying_an_unknown_name_raises_rather_than_writing_nothing():
    """A typo has to be loud. Otherwise it writes nothing, reads the block back
    unchanged, and hands the caller a success."""
    pad = FakePad()
    try:
        settings.apply(pad, "quick_swtich", True)
        check("an unknown setting is refused", False)
    except settings.SettingsError as exc:
        check("an unknown setting is refused", "no such setting" in str(exc), str(exc))


def test_restart_is_acknowledged():
    pad = FakePad()
    check("the pad takes command 29", settings.restart(pad))
    check("and counted it", pad.restarts == 1)


def test_the_screen_bits_are_the_same_block_narrowed():
    """The screen page and this one read one reply, not two.

    `flydigi/screen.py` narrows rather than decoding again, so a bit position
    cannot drift between them -- this is what asserts the two agree.
    """
    from flydigi import screen

    pad = FakePad()
    settings.apply(pad, "always_on", True)
    settings.apply(pad, "status_bar_always_on", True)
    whole = settings.read_status(pad)
    narrow = screen.read_screen_status(pad)
    check("the display bit agrees", narrow["always_on"] == whole["always_on"] is True)
    check("the status bar bit agrees",
          narrow["status_bar_always_on"] == whole["status_bar_always_on"] is True)
    check("and so does what is supported",
          narrow["status_bar_usable"] == whole["status_bar_always_on_usable"] is True)


def main():
    for test in (test_the_reply_a_real_pad_gave_decodes_feature_by_feature,
                 test_precision_is_in_declaration_order_not_by_bit_depth,
                 test_sensitivity_and_report_rate_read_as_words,
                 test_a_short_or_foreign_reply_is_refused,
                 test_reading_from_a_pad_that_says_nothing_raises,
                 test_sub_ids_and_bit_positions_are_one_list_read_twice,
                 test_a_setting_write_is_the_packet_flydigi_builds,
                 test_each_feature_writes_and_reads_back,
                 test_an_unsupported_setting_acks_and_does_nothing,
                 test_the_numeric_settings_go_through_their_own_commands,
                 test_sleep_time_is_clamped_to_what_the_picker_offers,
                 test_applying_an_unknown_name_raises_rather_than_writing_nothing,
                 test_restart_is_acknowledged,
                 test_the_screen_bits_are_the_same_block_narrowed):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
