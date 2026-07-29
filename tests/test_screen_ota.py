#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the serial screen upload. No controller, no serial port.

This is the path that actually reaches an Apex 5's panel -- the HID family in
`test_screen.py` is protocol-conformant and inert on this pad. What can be
checked without hardware is that the packets match `OtaNewUpdater` field for
field, that erase precedes every write, and that what the chip ends up holding
is the frames we handed over.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import screen, screen_ota
from tests.fake_pad import FakeScreenChip

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def frames(count, first=0):
    return [screen.encode_frame(bytes([(first + n) % 256, n % 256, 7])
                                * (screen.WIDTH * screen.HEIGHT))
            for n in range(count)]


# -- framing ---------------------------------------------------------------


def test_the_length_field_is_a_constant_not_a_length():
    """Three of the five disagree with the bytes that follow them.

    Pinned because it looks like a bug and is not ours to fix: deriving the
    field would be right for two opcodes and wrong for three, and the pad is the
    one that decides.
    """
    check("PicGetBaseAddr says 4 for 4 payload bytes",
          screen_ota.LENGTH_FIELD[screen_ota.OP_PIC_GET_BASE] == 4)
    check("PicGetVersion says 6 and sends none",
          screen_ota.LENGTH_FIELD[screen_ota.OP_PIC_GET_VERSION] == 6)
    check("EraseSector says 6 and sends 4",
          screen_ota.LENGTH_FIELD[screen_ota.OP_ERASE] == 6)
    check("WriteData says 64, the whole packet",
          screen_ota.LENGTH_FIELD[screen_ota.OP_WRITE] == 64)
    check("PicResetDevice says 8 for 8",
          screen_ota.LENGTH_FIELD[screen_ota.OP_PIC_RESET] == 8)

    packet = screen_ota.build(screen_ota.OP_ERASE, struct.pack("<I", 0x1234))
    check("opcode leads", packet[0] == screen_ota.OP_ERASE)
    check("length is little endian", packet[1:3] == b"\x06\x00", packet[1:3].hex())
    check("payload follows the three-byte header", packet[3:] == struct.pack("<I", 0x1234))


def test_a_short_reply_is_still_a_reply():
    """The end of the session is under five bytes, so it has no length field.

    A parser that insisted on a header would discard exactly the packet that
    says the upload finished.
    """
    result, opcode, body = screen_ota.parse(bytes([0, screen_ota.OP_PIC_RESET]))
    check("short reply keeps its opcode", opcode == screen_ota.OP_PIC_RESET)
    check("short reply has no payload", body == b"")
    check("nothing at all parses to nothing", screen_ota.parse(None)[1] is None)
    full = bytes([0, screen_ota.OP_PIC_GET_BASE, 8, 0]) + struct.pack("<I", 0xDEAD)
    _r, opcode, body = screen_ota.parse(full)
    check("a full reply yields its payload",
          opcode == screen_ota.OP_PIC_GET_BASE and body == struct.pack("<I", 0xDEAD))


def test_the_picture_metadata_matches_space_stations():
    check("one frame is a PNG", screen_ota.picture_type(1) == screen_ota.PIC_TYPE_PNG)
    check("more than one is a GIF", screen_ota.picture_type(2) == screen_ota.PIC_TYPE_GIF)
    # frameRate here is interval/10, where the HID start packet uses /100. The
    # two paths disagree and this is the one that reaches the screen.
    check("100 ms is 10", screen_ota.frame_rate(100) == 10)
    check("40 ms is 4", screen_ota.frame_rate(40) == 4)
    check("never zero", screen_ota.frame_rate(1) == 1)
    check("never past a byte", screen_ota.frame_rate(100000) == 255)


def test_the_checksum_is_stable_and_not_a_standard_crc32():
    """Their arithmetic, not zlib's -- indexed on bits 8..15, in signed 32-bit.

    No reference value exists outside the firmware, so this pins the
    implementation against drift rather than against truth. What it can prove is
    that it is not accidentally the standard CRC-32, which is the mistake a
    reader would make when tidying it.
    """
    import zlib
    sample = bytes(range(256)) * 4
    ours = screen_ota.checksum(sample)
    check("deterministic", ours == screen_ota.checksum(sample))
    check("fits in 32 bits", 0 <= ours <= 0xFFFFFFFF, hex(ours))
    check("not zlib's crc32", ours != zlib.crc32(sample), hex(ours))
    check("empty input is zero", screen_ota.checksum(b"") == 0)
    check("one byte differing changes it",
          screen_ota.checksum(b"\x00\x01") != screen_ota.checksum(b"\x00\x02"))


# -- the upload ------------------------------------------------------------


def test_a_single_frame_lands_byte_identical():
    chip = FakeScreenChip()
    payload = frames(1)
    base = screen_ota.upload(chip, payload, interval_ms=100)
    check("used the base the chip gave us", base == chip.base, hex(base))
    check("the frame arrived unchanged", chip.contents() == payload[0])
    check("nothing was refused for unerased flash", chip.refused == 0, chip.refused)
    check("metadata says one PNG frame at 10",
          chip.config == (screen_ota.PIC_TYPE_PNG, 1, 10, 0), str(chip.config))
    length, crc = chip.reset
    check("the reset carries the byte count", length == screen.FRAME_LEN, length)
    check("the reset carries our checksum", crc == screen_ota.checksum(payload[0]))


def test_an_animation_lands_in_order():
    chip = FakeScreenChip()
    payload = frames(4)
    screen_ota.upload(chip, payload, interval_ms=40, restore_default=True)
    check("all four frames, concatenated", chip.contents() == screen.join_frames(payload))
    check("metadata says four GIF frames at 4, restoring defaults",
          chip.config == (screen_ota.PIC_TYPE_GIF, 4, 4, 1), str(chip.config))
    check("the reset counts every byte",
          chip.reset[0] == 4 * screen.FRAME_LEN, chip.reset[0])


def test_every_block_is_erased_before_it_is_written():
    """The ordering the fake enforces, and the reason it does.

    Flash cannot be written back to 1, so a write into an unerased block does
    not simply overwrite -- it ANDs. A fake that accepted it would let an
    upload pass here and produce a corrupt picture on the pad.
    """
    chip = FakeScreenChip()
    payload = frames(2)
    screen_ota.upload(chip, payload)
    size = 2 * screen.FRAME_LEN
    expected_blocks = (size + screen_ota.ERASE_BLOCK - 1) // screen_ota.ERASE_BLOCK
    check("one erase per 4096 bytes", len(chip.erased) == expected_blocks,
          f"{len(chip.erased)} != {expected_blocks}")
    check("erases start at the base", min(chip.erased) == chip.base)
    check("erases cover the last byte",
          max(chip.erased) + screen_ota.ERASE_BLOCK >= chip.base + size)
    check("no write was refused", chip.refused == 0)


def test_the_tail_is_a_short_packet_not_a_padded_one():
    """A frame is not a multiple of 55, so the last write is short.

    Flydigi leave the inner length at 55 and simply send fewer bytes; copied,
    because the alternative -- padding -- would write bytes past the picture.
    """
    chip = FakeScreenChip()
    payload = frames(1)
    screen_ota.upload(chip, payload)
    expected_writes = (screen.FRAME_LEN + screen_ota.WRITE_CHUNK - 1) // screen_ota.WRITE_CHUNK
    check("one write per 55 bytes", chip.writes == expected_writes,
          f"{chip.writes} != {expected_writes}")
    check("and not one byte more than the frame",
          len(chip.contents()) == screen.FRAME_LEN, len(chip.contents()))


def test_progress_counts_erases_and_writes():
    chip = FakeScreenChip()
    seen = []
    screen_ota.upload(chip, frames(1), progress=lambda done, total: seen.append((done, total)))
    check("progress was reported", bool(seen))
    check("it ends at the total", seen[-1][0] == seen[-1][1], str(seen[-1]))
    check("it never goes backwards",
          all(b[0] > a[0] for a, b in zip(seen, seen[1:])))


def test_silence_stops_the_upload():
    """A chip that stops answering must not read as a finished upload."""
    chip = FakeScreenChip()

    class Deaf(FakeScreenChip):
        def read_reply(self, timeout=None):
            return None

    try:
        screen_ota.upload(Deaf(), frames(1))
        check("silence raises", False)
    except screen_ota.OtaError as exc:
        check("silence raises", "no reply" in str(exc), str(exc))

    try:
        screen_ota.upload(chip, [])
        check("an empty upload is refused", False)
    except screen_ota.OtaError:
        check("an empty upload is refused", True)


def test_a_reply_for_the_wrong_opcode_is_not_accepted():
    """Replies are matched to their command, because a stale one would fit.

    The pad's HID node broadcasts replies to every reader; there is no evidence
    the bootloader does, but a mismatched opcode is cheap to check and the
    failure it prevents -- treating somebody else's answer as ours -- has
    already happened once on the HID side of this project.
    """
    class Confused(FakeScreenChip):
        def _get_version(self, _payload):
            return self._reply(screen_ota.OP_ERASE, b"\x00\x00\x00\x00")

    try:
        screen_ota.upload(Confused(), frames(1))
        check("a mismatched reply raises", False)
    except screen_ota.OtaError as exc:
        check("a mismatched reply raises", "expected a reply" in str(exc), str(exc))


def test_entering_upgrade_mode_names_the_screen_and_nothing_else():
    """Command 31 takes a chip module, and this one only ever sends the screen.

    Asserted rather than trusted: the difference between a picture upload and a
    brick is which byte goes in that field.
    """
    from flydigi import blobs, device
    sent = []

    class Recorder:
        def send(self, buf, wait=0.3, until=None):
            sent.append(bytes(buf))
            return []

    screen_ota.enter_upgrade_mode(Recorder())
    check("one packet", len(sent) == 1)
    buf = sent[0]
    check("command 31", buf[3] == 31, buf[3])
    check("payload is the screen chip", buf[5] == screen_ota.CHIP_SCREEN, buf[5])
    check("length counts cmd+len+payload", buf[4] == 3, buf[4])
    check("checksum is where the pad looks",
          buf[3 + buf[4]] == device.checksum(buf, 3, 3 + buf[4]))
    check("built like every other command", buf == bytes(blobs.build(31, bytes([4]))))


def main():
    for test in (test_the_length_field_is_a_constant_not_a_length,
                 test_a_short_reply_is_still_a_reply,
                 test_the_picture_metadata_matches_space_stations,
                 test_the_checksum_is_stable_and_not_a_standard_crc32,
                 test_a_single_frame_lands_byte_identical,
                 test_an_animation_lands_in_order,
                 test_every_block_is_erased_before_it_is_written,
                 test_the_tail_is_a_short_packet_not_a_padded_one,
                 test_progress_counts_erases_and_writes,
                 test_silence_stops_the_upload,
                 test_a_reply_for_the_wrong_opcode_is_not_accepted,
                 test_entering_upgrade_mode_names_the_screen_and_nothing_else):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
