# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Apex 5's factory profile, read off the pad. Generated -- see below.

Regenerate with `tools/gen-factory-config`, which needs a pad whose four slots
have all been reset. Do not hand-edit.

**Why this is bytes rather than code.** A factory profile is not "every field at
its default": this project's own reconstruction in `flydigi/mock/pad.py` differs
from the real thing in 93 of 840 bytes -- the header at 2..12 including the
`OldLedConfig` mirror nothing here decodes, the grip-vibration block, and the
whole trigger region from 153 to 224. Writing that reconstruction to a pad and
calling it "factory" would be a guess with a confident label on it.

**Why one blob covers four slots.** The four factory profiles are byte-identical
apart from a single byte at 774, the digit in their titles -- measured, and
`gen-factory-config` refuses to regenerate if that stops being true.

**What this is for.** A per-profile restore. Command 175 resets all four slots
and ignores the slot it is given, so restoring one means writing the factory
bytes into it and saving -- which is exactly what Space Station does, from a
`default_mapping_<DeviceType>` file it ships. This is that file, obtained from
the hardware instead.
"""

# One byte per slot, at offset 774: the digit in the factory title.
TITLE_DIGITS = [49, 50, 51, 52]

FACTORY_BLOB = bytes.fromhex(
    "01034d200428a00000ff000000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff00"
    "00ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff00"
    "00ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff0000ff00"
    "0000003f3f7f7f7f00003f3f7f7f7f00000000ffffff00000000ffffff000c0004191400"
    "0000003cff320050ff3200011e5005013200ff287805003200011e5005013200ff287805"
    "003200000000000a0a6401ff4600000000000000000000000000000a0a6401ff46000000"
    "000000000000000000ffffffffff000000000000ffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    "ffffffffffffffffffffffffffff4d916e7f310000000000000000000000ffffffff0032"
    "3e4b5764707d8996000000323e4b5764707d89960000ffffffffffff0303030303ffffff"
    "ffff003f3f7f7f7fffffffff"
)


def for_slot(cfg_id):
    """The factory profile for one slot, as a fresh bytearray."""
    if not 0 <= cfg_id < len(TITLE_DIGITS):
        raise ValueError(f"no profile slot {cfg_id}")
    blob = bytearray(FACTORY_BLOB)
    blob[774] = TITLE_DIGITS[cfg_id]
    return blob

