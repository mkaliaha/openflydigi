# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Factory profiles, one blob per model. Generated -- see below.

Regenerate with `tools/gen-factory-config`. Do not hand-edit.

**Why this is bytes rather than code.** A factory profile is not "every field at
its default": this project's own reconstruction in `flydigi/mock/pad.py` differs
from the real thing in 93 of 840 bytes -- the header at 2..12 including the
`OldLedConfig` mirror nothing here decodes, the grip-vibration block, and the
whole trigger region from 153 to 224. Writing that reconstruction to a pad and
calling it "factory" would be a guess with a confident label on it.

**Why one blob covers four slots.** The four factory profiles are byte-identical
apart from a single byte at 774, the digit in their titles -- measured on
the pad, true of Flydigi's own files as well, and `gen-factory-config` refuses to
regenerate if it stops being true.

**What this is for.** A per-profile restore. Command 175 resets all four slots
and ignores the slot it is given, so restoring one means writing the factory
bytes into it and saving -- which is exactly what Space Station does, from a
`default_mapping_<DeviceType>` file it ships.

**The two blobs do not have the same standing, and the difference is worth
knowing before trusting one.** The Apex 5's came off the hardware here. The
Vader 5's is that shipped file put through `tools/mapping_bean.py`, so it is
what Space Station *would write* to restore a slot -- which is not provably what
a factory Vader 5 holds in flash. Held to the strongest check available: the
same translator run over the Apex 5's file reproduces the blob below in 828 of
840 bytes, and each of the twelve that differ has a mechanical explanation in
`gen-factory-config`'s `KNOWN_DIVERGENCES` -- a gyro axis their format cannot
represent, one byte for hardware the Apex 5 does not have, and ten bytes of
title padding. A thirteenth would fail the check.
"""

# One byte per slot, at offset 774: the digit in the factory title.
TITLE_DIGITS = [49, 50, 51, 52]
TITLE_DIGIT_OFFSET = 774

# By DeviceCode, as `identity.DEVICE_TYPES` names them. A model absent here has
# no per-slot restore, which `identity.CAPABILITIES` states as
# `factory_profile` -- writing an Apex 5's key table into a Vader would install
# a profile with nothing on C and Z and call it factory.
FACTORY_BLOBS = {
    # Vader 5 Pro -- translated from Flydigi's own default_mapping_130.dat.
    "f5": bytes.fromhex(
        "02034d200428a00000ff000000ff0000ff0000ff0000ff0000ff0000ff0000ff0000"
        "ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff"
        "0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff00"
        "00ff0000ff00000005423f7f7f7f0005423f7f7f7f00000000ffffff00000000ffff"
        "ff000c00041919000000003cff320050ff320001337205013200ff28780500320001"
        "337205013200ff287805003200000000000a0a6401ff460000000000000000000000"
        "0000000a0a6401ff46000000000000000000000000ffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffff4d916e7f3100ffffffffffff"
        "ffffffffffffffff002d3a4754616e7b89960000002d3a4754616e7b89960000ffff"
        "ffffffffffffffffffffffffffff003f3f7f7f7fffffffff"
    ),
    # Apex 5 -- read off the pad.
    "k5": bytes.fromhex(
        "01034d200428a00000ff000000ff0000ff0000ff0000ff0000ff0000ff0000ff0000"
        "ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff"
        "0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff00"
        "00ff0000ff000000003f3f7f7f7f00003f3f7f7f7f00000000ffffff00000000ffff"
        "ff000c00041914000000003cff320050ff3200011e5005013200ff28780500320001"
        "1e5005013200ff287805003200000000000a0a6401ff460000000000000000000000"
        "0000000a0a6401ff46000000000000000000000000ffffffffff000000000000ffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffff4d916e7f3100000000000000"
        "00000000ffffffff00323e4b5764707d8996000000323e4b5764707d89960000ffff"
        "ffffffff0303030303ffffffffff003f3f7f7f7fffffffff"
    ),
}


def have(code):
    """Whether this project holds `code`'s factory profile."""
    return code in FACTORY_BLOBS


def for_slot(cfg_id, code="k5"):
    """The factory profile for one slot of one model, as a fresh bytearray."""
    if not 0 <= cfg_id < len(TITLE_DIGITS):
        raise ValueError(f"no profile slot {cfg_id}")
    if code not in FACTORY_BLOBS:
        raise ValueError(
            f"no factory profile for {code} -- tools/gen-factory-config adds one")
    blob = bytearray(FACTORY_BLOBS[code])
    blob[TITLE_DIGIT_OFFSET] = TITLE_DIGITS[cfg_id]
    return blob

