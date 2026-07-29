#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the screen: image format, packets, upload. No controller.

The format half of this is settled -- every frame Flydigi ships decodes and
re-encodes byte-identical, which `tools/flydigi-screen check` will show you over
their own files. The transport half is not: see `flydigi/screen.py`. These cases
prove we build the packets the SDK builds, which is a different claim from the
pad answering them.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import device, screen
from tests.fake_pad import FakePad

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def gradient(width=screen.WIDTH, height=screen.HEIGHT):
    """An image that varies in both axes, so a transposed decode is visible."""
    rgb = bytearray(width * height * 3)
    for y in range(height):
        for x in range(width):
            at = (y * width + x) * 3
            rgb[at] = x * 255 // max(1, width - 1)
            rgb[at + 1] = y * 255 // max(1, height - 1)
            rgb[at + 2] = (x + y) % 256
    return bytes(rgb)


# -- the image format ------------------------------------------------------


def test_the_header_is_the_four_bytes_every_shipped_frame_starts_with():
    check("header is 04 80 02 0a", screen.frame_header().hex() == "0480020a",
          screen.frame_header().hex())
    cf, width, height = screen.parse_frame_header(screen.frame_header())
    check("header parses back", (cf, width, height) == (screen.CF_TRUE_COLOR, 160, 80),
          f"got {(cf, width, height)}")
    # The three bits LVGL requires to be zero are what tells this format from a
    # file that merely happens to be the right length.
    try:
        screen.parse_frame_header(b"\x24\x80\x02\x0a")
        check("a non-zero always_zero field is refused", False)
    except screen.ScreenError:
        check("a non-zero always_zero field is refused", True)


def test_a_frame_is_the_size_flydigis_container_cuts_at():
    check("frame length", screen.FRAME_LEN == 25604, screen.FRAME_LEN)
    frames = [bytes([i]) * screen.FRAME_LEN for i in range(3)]
    check("split reverses join",
          screen.split_frames(screen.join_frames(frames)) == frames)
    try:
        screen.split_frames(b"\x00" * (screen.FRAME_LEN + 1))
        check("a partial frame is refused", False)
    except screen.ScreenError:
        check("a partial frame is refused", True)


def test_pixels_keep_their_extremes_through_565():
    check("black survives", screen.unpack_pixel(*screen.pack_pixel(0, 0, 0)) == (0, 0, 0))
    # Shifting alone would cap white at (248, 252, 248); the low bits have to be
    # filled from the high ones or every round trip darkens the image.
    check("white survives",
          screen.unpack_pixel(*screen.pack_pixel(255, 255, 255)) == (255, 255, 255),
          str(screen.unpack_pixel(*screen.pack_pixel(255, 255, 255))))
    for colour in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        check(f"primary {colour} survives",
              screen.unpack_pixel(*screen.pack_pixel(*colour)) == colour)


def test_the_high_byte_of_a_pixel_comes_first():
    """Byte order is the one thing a self-consistent codec cannot catch itself.

    Encode and decode agree with each other whichever way round they are, so
    this pins the wire order against a colour whose two bytes differ: pure red
    is 0xF800, and the first byte on the wire is 0xF8.
    """
    high, low = screen.pack_pixel(255, 0, 0)
    check("red packs high byte first", (high, low) == (0xF8, 0x00), f"{high:#04x} {low:#04x}")
    high, low = screen.pack_pixel(0, 0, 255)
    check("blue packs high byte first", (high, low) == (0x00, 0x1F), f"{high:#04x} {low:#04x}")


def test_encoding_is_stable_and_reversible():
    rgb = gradient()
    frame = screen.encode_frame(rgb)
    check("encoded frame is a whole frame", len(frame) == screen.FRAME_LEN, len(frame))
    check("encoded frame carries the header",
          frame[:4] == screen.frame_header())
    width, height, back = screen.decode_frame(frame)
    check("decode returns the screen's size", (width, height) == (160, 80))
    # Not equal to the input -- 888 does not fit in 565 -- but re-encoding what
    # came back has to reproduce the frame exactly, or an edit-and-write cycle
    # would drift a little further every time.
    check("re-encoding a decoded frame is byte-identical",
          screen.encode_frame(back, width, height) == frame)


def test_encoding_refuses_the_wrong_amount_of_pixels():
    try:
        screen.encode_frame(b"\x00" * 30)
        check("a short image is refused", False)
    except screen.ScreenError:
        check("a short image is refused", True)


def test_fit_puts_something_the_right_size_on_the_screen():
    source = gradient(64, 64)
    for mode in ("fill", "fit", "stretch"):
        out = screen.fit(source, 64, 64, mode=mode)
        check(f"{mode} produces a screenful",
              len(out) == screen.WIDTH * screen.HEIGHT * 3, len(out))
    # Letterboxing a square into a 2:1 screen leaves the sides at the background
    # colour; filling it does not, because it crops instead.
    letterboxed = screen.fit(source, 64, 64, mode="fit", background=(255, 0, 255))
    check("fit letterboxes with the background colour",
          tuple(letterboxed[0:3]) == (255, 0, 255), str(tuple(letterboxed[0:3])))
    filled = screen.fit(source, 64, 64, mode="fill", background=(255, 0, 255))
    check("fill leaves no background showing",
          tuple(filled[0:3]) != (255, 0, 255))
    # A flat image has to survive resampling flat, whichever way it is scaled.
    flat = bytes([17, 34, 51]) * (200 * 200)
    out = screen.fit(flat, 200, 200, mode="stretch")
    check("a flat image resamples flat", set(out) == {17, 34, 51})


# -- packets ---------------------------------------------------------------


def test_packets_are_framed_like_every_other_command():
    buf = screen.start_packet(1, 1)
    check("report id", buf[0] == device.REPORT_ID_OUT)
    check("magic", (buf[1], buf[2]) == (device.MAGIC1, device.MAGIC2))
    check("command", buf[3] == screen.CMD_UPLOAD_START)
    check("length counts cmd+len+payload", buf[4] == 9, buf[4])
    expected = device.checksum(buf, 3, 3 + buf[4])
    check("checksum lands at 3+len", buf[3 + buf[4]] == expected)
    check("packet is one report", len(buf) == device.PACKET_LEN)


def test_the_three_dialects_are_one_packet_with_different_prefixes():
    """The SDK's XInput, DInput and NewXInput envelopes differ by two bytes.

    Worth asserting because it is the reason `probe` can try all three from one
    builder rather than three, and because getting it wrong would send a
    payload byte where the pad expects a command id.
    """
    payload = bytes([1, 0, 1, 1, 1, 100, 4])
    packets = {name: screen.build(screen.CMD_UPLOAD_START, payload, name)
               for name in screen.DIALECTS}
    check("bare has no magic", packets["bare"][1] == screen.CMD_UPLOAD_START)
    check("a5 has one magic byte",
          packets["a5"][1] == device.MAGIC2 and packets["a5"][2] == screen.CMD_UPLOAD_START)
    check("new has both",
          packets["new"][3] == screen.CMD_UPLOAD_START)
    bodies = set()
    for name, buf in packets.items():
        offset = 1 + len(screen.DIALECTS[name])
        length = buf[offset + 1]
        check(f"{name}: checksum covers the command onwards",
              buf[offset + length] == device.checksum(buf, offset, offset + length))
        bodies.add(bytes(buf[offset:offset + length]))
    check("command, length and payload are identical in all three", len(bodies) == 1,
          str(sorted(b.hex() for b in bodies)))
    try:
        screen.build(screen.CMD_UPLOAD_START, payload, "nonsense")
        check("an unknown dialect is refused", False)
    except screen.ScreenError:
        check("an unknown dialect is refused", True)


def test_the_start_packet_says_what_flydigis_own_builder_says():
    """Fields from the SDK's XInput branch: id, type, count, index, period, size.

    The size is the whole frame including its four header bytes -- 25604, which
    Flydigi pass as the literal pair (100, 4) -- and not the pixel count.
    """
    buf = screen.start_packet(2, 5, period=3)
    payload = buf[5:5 + 7]
    check("picture id", payload[0] == 1)
    check("type is 1 for an animation", payload[1] == 1)
    check("frame count", payload[2] == 5)
    check("frame index is 1-based", payload[3] == 2)
    check("period", payload[4] == 3)
    check("size is the frame with its header",
          (payload[5] << 8) | payload[6] == screen.FRAME_LEN,
          f"{payload[5]},{payload[6]}")
    check("type is 0 for a single image", screen.start_packet(1, 1)[6] == 0)


def test_chunks_cover_the_frame_and_pad_the_tail():
    frame = bytes(range(256)) * (screen.FRAME_LEN // 256) + b"\x00" * (screen.FRAME_LEN % 256)
    parts = screen.chunks(frame)
    check("offsets step by the chunk size",
          [offset for offset, _ in parts[:3]] == [0, screen.CHUNK, 2 * screen.CHUNK])
    check("every chunk is full length",
          {len(chunk) for _, chunk in parts} == {screen.CHUNK})
    check("the chunks reassemble the frame",
          b"".join(chunk for _, chunk in parts)[:screen.FRAME_LEN] == frame)
    check("a data packet is one report",
          len(screen.data_packet(*parts[0])) == device.PACKET_LEN)
    # The offset is two bytes, so the last one has to still fit in them.
    check("the last offset fits in two bytes", parts[-1][0] <= 0xFFFF, parts[-1][0])


# -- upload, against the fake ----------------------------------------------


def test_an_uploaded_frame_arrives_byte_identical():
    pad = FakePad()
    frame = screen.encode_frame(gradient())
    sent = screen.upload(pad, [frame])
    check("the pad collected one frame", len(pad.screen_frames) == 1,
          str(len(pad.screen_frames)))
    check("the frame arrived unchanged", pad.screen_frames[0] == frame)
    check("the upload was finished", pad.screen_uploads == 1)
    expected = len(screen.chunks(frame)) + 3     # start, data..., end, finish
    check("every packet was acked", sent == expected, f"{sent} != {expected}")


def test_an_animation_keeps_its_frames_in_order():
    pad = FakePad()
    frames = [screen.encode_frame(bytes([n, n, n]) * (screen.WIDTH * screen.HEIGHT))
              for n in (10, 20, 30)]
    screen.upload(pad, frames, period=4)
    check("all three arrived", pad.screen_frames == frames)
    check("the period reached the pad", pad.screen_period == 4, str(pad.screen_period))
    check("one finish for the whole animation", pad.screen_uploads == 1)


def test_a_second_upload_replaces_the_first():
    pad = FakePad()
    first = screen.encode_frame(bytes([1, 2, 3]) * (screen.WIDTH * screen.HEIGHT))
    second = screen.encode_frame(bytes([4, 5, 6]) * (screen.WIDTH * screen.HEIGHT))
    screen.upload(pad, [first])
    screen.upload(pad, [second])
    check("the screen holds only the newer image", pad.screen_frames == [second])


def test_upload_refuses_a_frame_that_is_not_a_frame():
    pad = FakePad()
    try:
        screen.upload(pad, [b"\x00" * 100])
        check("a short frame is refused", False)
    except screen.ScreenError:
        check("a short frame is refused", True)
    check("nothing was sent", pad.screen_frames == [] and pad.packets_received == 0)
    try:
        screen.upload(pad, [])
        check("an empty upload is refused", False)
    except screen.ScreenError:
        check("an empty upload is refused", True)


def test_an_unanswered_packet_stops_the_upload():
    """A pad that does not know this command family must not read as success.

    The fake speaks only the 5A A5 envelope, so a legacy dialect gets silence
    from it -- which is exactly what an Apex 5 will do if the picture commands
    are not in its firmware. The point of the case is that silence raises.
    """
    pad = FakePad()
    frame = screen.encode_frame(gradient())
    try:
        screen.upload(pad, [frame], dialect="bare")
        check("silence is not mistaken for success", False)
    except screen.ScreenError as exc:
        check("silence is not mistaken for success", "did not answer" in str(exc), str(exc))
    check("nothing was collected", pad.screen_frames == [])


def test_probe_reports_each_dialect_separately():
    pad = FakePad()
    results = dict(screen.probe(pad))
    check("every dialect was tried", set(results) == set(screen.DIALECTS))
    check("the envelope this pad speaks answered", screen.acked(results["new"]))
    check("the ones it does not speak stayed silent",
          not screen.acked(results["a5"]) and not screen.acked(results["bare"]))
    check("probing uploaded nothing", pad.screen_frames == [])


def test_the_frame_period_stays_inside_its_byte():
    check("100 ms is one period", screen.period_from_interval(100) == 1)
    check("250 ms is two", screen.period_from_interval(250) == 2)
    # Flydigi's integer division sends 0 for anything faster than 100 ms, which
    # is not a frame rate; and the field is a byte, so a very slow animation
    # would wrap rather than crawl.
    check("40 ms floors at one, not zero", screen.period_from_interval(40) == 1)
    check("a minute a frame caps at 255", screen.period_from_interval(60000) == 255)


def test_more_frames_than_the_count_byte_holds_is_refused():
    """The frame count and index are one byte each, so 255 is the ceiling.

    Worth its own case because the failure without it is a ValueError out of
    `bytes()`, which says nothing about frames.
    """
    pad = FakePad()
    frame = screen.encode_frame(bytes(3) * (screen.WIDTH * screen.HEIGHT))
    try:
        screen.upload(pad, [frame] * 256)
        check("256 frames is refused", False)
    except screen.ScreenError as exc:
        check("256 frames is refused", "255" in str(exc), str(exc))
    check("and nothing was sent", pad.packets_received == 0)


# -- settings --------------------------------------------------------------


def test_the_display_can_be_kept_up_or_let_go_dark():
    """The SDK calls this bit OffScreen and setting it keeps the display on.

    Named here for the measured behaviour, so a caller asking for `always_on`
    gets a lit screen rather than a dark one -- which the SDK's own name would
    have led them to write backwards.
    """
    pad = FakePad()
    check("always-on acked", screen.set_always_on(pad, True))
    check("the pad kept the picture up", pad.always_on)
    status = screen.read_screen_status(pad)
    check("the status reads it back", status["always_on"])
    check("reported as supported", status["always_on_usable"])
    screen.set_always_on(pad, False)
    check("and dark again", not screen.read_screen_status(pad)["always_on"])


def test_the_status_bar_toggle_is_a_different_sub_command():
    pad = FakePad()
    check("status bar acked", screen.set_status_bar_always_on(pad, True))
    check("the pad kept it on", pad.status_bar_always_on)
    status = screen.read_screen_status(pad)
    check("the status reads it back", status["status_bar_always_on"])
    check("switching the status bar left the display alone", not status["always_on"])


def test_screen_status_reads_the_reply_a_real_pad_gave():
    """The bytes an Apex 5 answered command 3 with, recorded in docs/device-settings.md.

    Not the fake's, so the bit positions are checked against hardware rather
    than against the other half of this repository.
    """
    body = bytes([90, 165, 3, 1, 0, 251, 123, 1, 0, 15, 0, 2, 17])
    status = screen.parse_screen_status(body)
    check("status bar is supported", status["status_bar_usable"])
    check("status bar was off", not status["status_bar_always_on"])
    check("always-on is supported", status["always_on_usable"])
    check("the display was dark when idle", not status["always_on"])


def test_test_screen_carries_the_whole_colour():
    """Command 242 floods the screen with a colour, with no upload involved.

    Flydigi's builder disagrees with itself: four payload bytes, a length byte
    saying five, and the checksum placed where a six would put it. This pins
    which reading we send and shows what the other one costs.
    """
    pad = FakePad()
    check("242 was acked", screen.test_screen(pad, True, (255, 128, 0)))
    check("the pad saw the whole colour", pad.screen_test == (True, (255, 128, 0)),
          str(pad.screen_test))

    ours = screen.test_screen_packet(True, (255, 128, 0))
    theirs = screen.test_screen_packet(True, (255, 128, 0), faithful=True)
    check("we say six where they say five", (ours[4], theirs[4]) == (6, 5),
          f"{ours[4]},{theirs[4]}")
    check("both put the checksum at offset nine",
          ours[9] == device.checksum(ours, 3, 9) and theirs[9] == device.checksum(theirs, 3, 8))
    check("only the length byte and its checksum differ",
          ours[:4] == theirs[:4] and ours[5:9] == theirs[5:9])
    # Their length byte does not agree with where they put the checksum, so a
    # pad that validates one rejects the packet. Ours is the reading that keeps
    # the blue byte; theirs is here for a second attempt on hardware.
    pad.screen_test = None
    check("a pad that checks the checksum refuses their bytes",
          not screen.test_screen(pad, True, (255, 128, 0), faithful=True))
    check("and recorded nothing from it", pad.screen_test is None)

    screen.test_screen(pad, False)
    check("switching the test pattern off works", pad.screen_test[0] is False)


def main():
    for test in (test_the_header_is_the_four_bytes_every_shipped_frame_starts_with,
                 test_a_frame_is_the_size_flydigis_container_cuts_at,
                 test_pixels_keep_their_extremes_through_565,
                 test_the_high_byte_of_a_pixel_comes_first,
                 test_encoding_is_stable_and_reversible,
                 test_encoding_refuses_the_wrong_amount_of_pixels,
                 test_fit_puts_something_the_right_size_on_the_screen,
                 test_packets_are_framed_like_every_other_command,
                 test_the_three_dialects_are_one_packet_with_different_prefixes,
                 test_the_start_packet_says_what_flydigis_own_builder_says,
                 test_chunks_cover_the_frame_and_pad_the_tail,
                 test_an_uploaded_frame_arrives_byte_identical,
                 test_an_animation_keeps_its_frames_in_order,
                 test_a_second_upload_replaces_the_first,
                 test_upload_refuses_a_frame_that_is_not_a_frame,
                 test_an_unanswered_packet_stops_the_upload,
                 test_probe_reports_each_dialect_separately,
                 test_the_frame_period_stays_inside_its_byte,
                 test_more_frames_than_the_count_byte_holds_is_refused,
                 test_the_display_can_be_kept_up_or_let_go_dark,
                 test_the_status_bar_toggle_is_a_different_sub_command,
                 test_screen_status_reads_the_reply_a_real_pad_gave,
                 test_test_screen_carries_the_whole_colour):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
