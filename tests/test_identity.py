#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the device guard: is the thing on the other end an Apex 5?

The case that matters is `test_a_vader_is_refused`. Every Flydigi pad shares a
vendor id and a vendor report descriptor, so `find_device` opens all of them
identically -- the identify read is the only thing standing between a Vader 4
Pro and an 840-byte Apex 5 profile in its flash.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import identity, motion

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


class Pad:
    """Answers command 1 with a chosen DeviceType, and nothing else."""

    def __init__(self, device_type=128, silent=False):
        self.device_type = device_type
        self.silent = silent

    def send(self, buf, wait=0.3, until=None):
        if self.silent or buf[3] != motion.CMD_GET_INFO:
            return []
        body = bytearray(32)
        body[0] = motion.INPUT_REPORT_ID
        body[3] = motion.CMD_GET_INFO
        body[6] = self.device_type
        body[7] = 1
        body[12] = 5
        return [bytes(body)]


def test_the_table_matches_the_sdk_dispatch():
    """Spot-checks against docs/findings-other-devices.md, which is the SDK's own
    dispatch. The names do not follow the products -- k2 is the Apex *4*."""
    check("128 is an Apex 5", identity.code_for(128) == "k5")
    check("129 is an Apex 5 too", identity.code_for(129) == "k5")
    check("85 is a Vader 4", identity.code_for(85) == "f4")
    check("91 is a Vader 4", identity.code_for(91) == "f4")
    check("84 is an Apex 4, not an Apex 2", identity.code_for(84) == "k2")
    check("k2's product name says Apex 4", identity.PRODUCT_NAMES["k2"] == "Apex 4")
    check("an unlisted type is None, not a guess", identity.code_for(200) is None)
    check("127 was never a real DeviceType", identity.code_for(127) is None)
    # 0x59 asserted None here for as long as the table was missing the Fp2
    # SKUs. The enum has carried it all along, as Fp2Wired.
    check("0x59 is Fp2Wired, and now says so", identity.code_for(0x59) == "fp2")

    # The Vader 3 Pro SKUs are their own code, which this table had as plain f3.
    check("28 is a plain Vader 3", identity.code_for(28) == "f3")
    check("80 is a Vader 3 Pro", identity.code_for(80) == "f3p")
    check("88 is a Vader 3 Pro too", identity.code_for(88) == "f3p")

    # Direwolf: `GetDeviceCodeById` returns fp3 and fp4 and never fp1 or fp2,
    # and the whole family used to collapse onto fp1 here.
    check("95 is a Direwolf 3", identity.code_for(95) == "fp3")
    check("132 is a Direwolf 4", identity.code_for(132) == "fp4")
    check("148 is a Direwolf 4", identity.code_for(148) == "fp4")
    check("25 is still a Direwolf 1", identity.code_for(25) == "fp1")
    check("82 is a Direwolf 2", identity.code_for(82) == "fp2")
    check("a Direwolf 4 is named as one",
          identity.name_for(132).startswith("Direwolf 4"), identity.name_for(132))

    # Every code the table produces can be named, or a refusal says "None".
    unnamed = sorted({c for c in identity.DEVICE_TYPES.values()
                      if c not in identity.PRODUCT_NAMES})
    check("every code has a product name", not unnamed, unnamed)


def test_an_apex5_is_accepted():
    got = identity.require(Pad(128))
    check("the guard passes an Apex 5", got["code"] == "k5", str(got))
    check("and says which one", "Apex 5" in got["name"], got["name"])


def test_a_vader_is_refused():
    """The whole point of the module."""
    for device_type in (85, 91):
        try:
            identity.require(Pad(device_type))
            check(f"a Vader ({device_type}) is refused", False)
        except identity.WrongDevice as exc:
            check(f"a Vader ({device_type}) is refused", True)
            check("and the message names it", "Vader 4" in str(exc), str(exc))
            check("and says what was expected", "Apex 5" in str(exc), str(exc))


def test_an_unknown_model_is_refused_rather_than_assumed():
    """A DeviceType Flydigi shipped after this table was written is not an
    invitation to write to it."""
    try:
        identity.require(Pad(200))
        check("an unknown model is refused", False)
    except identity.WrongDevice as exc:
        check("an unknown model is refused", True)
        check("and is named as unrecognised", "unrecognised" in str(exc), str(exc))


def test_a_silent_pad_is_an_error_not_a_pass():
    """A sleeping pad must not read as 'carry on'. It leaves the USB bus when it
    sleeps, so no answer is the normal shape of that failure."""
    try:
        identity.require(Pad(silent=True))
        check("silence does not pass the guard", False)
    except identity.WrongDevice as exc:
        check("silence does not pass the guard", True)
        check("and says to wake the pad", "wake" in str(exc), str(exc))


def test_a_caller_can_ask_for_another_model():
    """The Vader work will want this, and it must not mean editing the guard."""
    got = identity.require(Pad(85), "f4")
    check("a tool can opt into a Vader", got["code"] == "f4", str(got))
    try:
        identity.require(Pad(128), "f4")
        check("and an Apex 5 is then the wrong device", False)
    except identity.WrongDevice:
        check("and an Apex 5 is then the wrong device", True)


def main():
    for test in (test_the_table_matches_the_sdk_dispatch,
                 test_an_apex5_is_accepted,
                 test_a_vader_is_refused,
                 test_an_unknown_model_is_refused_rather_than_assumed,
                 test_a_silent_pad_is_an_error_not_a_pass,
                 test_a_caller_can_ask_for_another_model):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
