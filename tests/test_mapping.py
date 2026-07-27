#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for mapping profiles. No controller required -- see fake_pad.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import device, mapping
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


def main():
    for test in (test_packet_framing, test_identity_and_remap, test_turbo,
                 test_title, test_read_write_round_trip,
                 test_write_without_baseline_sends_everything,
                 test_unchanged_write_sends_nothing, test_apply_and_save,
                 test_bad_checksum_is_rejected):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
