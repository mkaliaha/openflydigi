# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""The Apex 5's screen: image format, upload, and the two screen settings.

**The image format is settled and verified offline.** Every frame Flydigi ships
in `Configs/Controller/<code>/default/default_screen_image_<deviceType>.bin` is
25604 bytes, and each one is an LVGL v8 image:

    0..4    header, a little-endian uint32 of bit fields:
            cf (5) | always_zero (3) | reserved (2) | width (11) | height (11)
            An Apex 5 frame is cf=4 (LV_IMG_CF_TRUE_COLOR), 160 x 80, which is
            the constant `04 80 02 0a`.
    4..     160 x 80 pixels, RGB565, **high byte first**, row-major

Two things pin that down rather than assuming it. `always_zero` really is zero
and `reserved` really is zero, which a wrong bit layout would not produce; and
the width falls out as 160 while an autocorrelation over the pixel bytes finds
its lowest inter-row difference at a stride of exactly 320 = 160 x 2. Decoding
the shipped frames the other byte order gives colour noise, and this way gives a
picture -- `tools/flydigi-screen preview` will show you.

Byte order is worth a sentence because LVGL calls it a build option rather than
a format: this is `LV_COLOR_16_SWAP = 1`, the byte-swapped 16-bit true colour.

**The transport is settled, and the answer is that this family does not drive
the screen on an Apex 5. Use `flydigi/screen_ota.py`.** The SDK has one
picture-upload family -- 208 start, 209 data, 210 end, 211 finish -- and
`ControllerSdk.UploadPicImpl` will send it to any pad whose `IsSupportScreen` is
set, which includes the Apex 5. But Space Station never asks it to:
`upload_pic2screen` in the Electron layer branches on the device code, and for
`k5` it sends `SwitchUsb` -- which is `SwitchToFirmwareUpgradeMode`, command 31
-- and then runs `FirmwareConsole.exe` with `--upgrade_type 2` over the frames
instead. Only the other pads take the HID path.

Tested on hardware: all four commands parse, ACK and echo every field back, two
complete uploads went out (9623 packets, no errors) and **the display never
changed**. So 208..211 is live firmware that drives nothing here. This module is
kept for other screen pads; `probe()` is a capability check for those rather
than a first contact for this one. Nothing in this module sends
command 31, and nothing should: it is a one-way door into a bootloader whose
protocol we do not have. Flydigi's own failure text for a broken screen upload
is "toggle the power switch on the back of the controller to restart it", which
is also the recovery here if an upload stalls half way.

The framing is a further unknown, because the picture commands are the *old*
envelope -- they predate the `5A A5` one every other command on this pad uses --
and the SDK has no NewXInput branch for them at all. All three of its branches
are the same packet with a different magic prefix, so `DIALECTS` carries all
three and `probe` tries each.
"""
from . import blobs, device

CMD_UPLOAD_START = 208
CMD_UPLOAD_DATA = 209
CMD_UPLOAD_END = 210
CMD_UPLOAD_FINISH = 211
CMD_TEST_SCREEN = 242
CMD_SETTING = 19
CMD_HARDWARE_STATUS = 3

SUB_STATUS_BAR = 8
SUB_OFF_SCREEN = 9

WIDTH = 160
HEIGHT = 80
HEADER_LEN = 4
PIXEL_BYTES = 2
FRAME_LEN = HEADER_LEN + WIDTH * HEIGHT * PIXEL_BYTES     # 25604

CF_TRUE_COLOR = 4          # LV_IMG_CF_TRUE_COLOR

# A data packet carries the offset in two bytes ahead of its chunk, and the
# whole thing still has to fit the pad's 32-byte report: 1 report id, 5 envelope
# bytes at most, 2 offset bytes, 1 checksum. Flydigi size theirs the same way,
# from the packet down, rather than picking a round number.
CHUNK = device.PACKET_LEN - 8

# The three envelopes in the SDK, which differ only in what precedes the command
# byte. `new` is the one every working command on this pad uses; `a5` is the
# DInput branch, `bare` the XInput one. Everything after the prefix is identical
# in all three: command, length, payload, checksum.
DIALECTS = {
    "new": (device.MAGIC1, device.MAGIC2),
    "a5": (device.MAGIC2,),
    "bare": (),
}
DEFAULT_DIALECT = "new"


class ScreenError(Exception):
    pass


# -- image format ---------------------------------------------------------


def frame_header(width=WIDTH, height=HEIGHT, cf=CF_TRUE_COLOR):
    """The 4-byte LVGL v8 image header."""
    value = (cf & 0x1F) | ((width & 0x7FF) << 10) | ((height & 0x7FF) << 21)
    return value.to_bytes(4, "little")


def parse_frame_header(data):
    """(cf, width, height) from the first four bytes of a frame.

    Raises if the bits LVGL requires to be zero are not, which is the cheapest
    way to notice that a file is not this format at all rather than decoding
    25600 bytes of something else into a picture of noise.
    """
    if len(data) < HEADER_LEN:
        raise ScreenError("frame is shorter than its header")
    value = int.from_bytes(data[:HEADER_LEN], "little")
    if (value >> 5) & 0x1F:
        raise ScreenError(
            f"not an LVGL image header: {data[:HEADER_LEN].hex()} has bits set "
            "where the format requires zeros")
    return value & 0x1F, (value >> 10) & 0x7FF, (value >> 21) & 0x7FF


def pack_pixel(red, green, blue):
    """One RGB888 pixel as RGB565, high byte first."""
    value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
    return value >> 8, value & 0xFF


def unpack_pixel(high, low):
    """RGB565 back to RGB888, replicating the top bits into the low ones.

    Straight shifting would cap white at (248, 252, 248), so a round trip
    through here has to reproduce the input rather than darken it slightly.
    """
    value = (high << 8) | low
    red, green, blue = (value >> 11) & 0x1F, (value >> 5) & 0x3F, value & 0x1F
    return (red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)


def encode_frame(rgb, width=WIDTH, height=HEIGHT):
    """Pack RGB888 bytes into one uploadable frame, header included."""
    expected = width * height * 3
    if len(rgb) != expected:
        raise ScreenError(
            f"expected {expected} bytes of RGB for {width}x{height}, got {len(rgb)}")
    frame = bytearray(frame_header(width, height))
    out = bytearray(width * height * PIXEL_BYTES)
    for index in range(width * height):
        high, low = pack_pixel(rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2])
        out[index * 2] = high
        out[index * 2 + 1] = low
    frame += out
    return bytes(frame)


def decode_frame(frame):
    """(width, height, RGB888 bytes) from one frame."""
    _cf, width, height = parse_frame_header(frame)
    pixels = width * height
    body = frame[HEADER_LEN:HEADER_LEN + pixels * PIXEL_BYTES]
    if len(body) < pixels * PIXEL_BYTES:
        raise ScreenError(
            f"header says {width}x{height} but only {len(body)} pixel bytes follow")
    rgb = bytearray(pixels * 3)
    for index in range(pixels):
        red, green, blue = unpack_pixel(body[index * 2], body[index * 2 + 1])
        rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2] = red, green, blue
    return width, height, bytes(rgb)


def split_frames(data, frame_len=FRAME_LEN):
    """Cut a .bin into frames. Flydigi's own container is nothing more."""
    if not data or len(data) % frame_len:
        raise ScreenError(
            f"{len(data)} bytes is not a whole number of {frame_len}-byte frames")
    return [bytes(data[i:i + frame_len]) for i in range(0, len(data), frame_len)]


def join_frames(frames):
    return b"".join(frames)


def fit(rgb, width, height, target=(WIDTH, HEIGHT), mode="fill", background=(0, 0, 0)):
    """Resample RGB888 bytes to the screen, in pure Python.

    `fill` covers the screen and crops what does not fit, `fit` letterboxes,
    `stretch` ignores the aspect ratio. Box averaging on the way down and
    nearest on the way up -- the screen is 160x80, so anything cleverer is below
    what it can show, and the alternative was making the backend depend on a
    imaging library it otherwise does not need.
    """
    out_w, out_h = target
    if len(rgb) != width * height * 3:
        raise ScreenError(
            f"expected {width * height * 3} bytes of RGB, got {len(rgb)}")

    src_x, src_y, src_w, src_h = 0, 0, width, height
    dst_x, dst_y, dst_w, dst_h = 0, 0, out_w, out_h
    if mode == "fill":
        scale = max(out_w / width, out_h / height)
        src_w, src_h = min(width, round(out_w / scale)), min(height, round(out_h / scale))
        src_x, src_y = (width - src_w) // 2, (height - src_h) // 2
    elif mode == "fit":
        scale = min(out_w / width, out_h / height)
        dst_w, dst_h = max(1, round(width * scale)), max(1, round(height * scale))
        dst_x, dst_y = (out_w - dst_w) // 2, (out_h - dst_h) // 2
    elif mode != "stretch":
        raise ScreenError(f"unknown fit mode {mode!r}")

    out = bytearray(out_w * out_h * 3)
    for channel in range(3):
        out[channel::3] = bytes([background[channel]]) * (out_w * out_h)

    for y in range(dst_h):
        # The source rows this destination row averages over. Half-open, and at
        # least one row wide so an upscale samples rather than divides by zero.
        y0 = src_y + y * src_h // dst_h
        y1 = max(y0 + 1, src_y + (y + 1) * src_h // dst_h)
        for x in range(dst_w):
            x0 = src_x + x * src_w // dst_w
            x1 = max(x0 + 1, src_x + (x + 1) * src_w // dst_w)
            red = green = blue = count = 0
            for sy in range(y0, y1):
                row = (sy * width + x0) * 3
                for _sx in range(x0, x1):
                    red += rgb[row]
                    green += rgb[row + 1]
                    blue += rgb[row + 2]
                    row += 3
                    count += 1
            at = ((dst_y + y) * out_w + dst_x + x) * 3
            out[at] = red // count
            out[at + 1] = green // count
            out[at + 2] = blue // count
    return bytes(out)


def to_frame(rgb, width, height, **kwargs):
    """Resample an image and encode it in one step."""
    return encode_frame(fit(rgb, width, height, **kwargs))


# -- packets --------------------------------------------------------------


def build(cmd_id, payload=b"", dialect=DEFAULT_DIALECT):
    """A picture-family packet in one of the three envelopes.

    The length byte counts the command and length bytes as well as the payload,
    and the checksum is an 8-bit sum from the command byte up to it -- the same
    rule as every other command here, just with a shorter prefix in the two
    legacy dialects.
    """
    try:
        prefix = DIALECTS[dialect]
    except KeyError:
        raise ScreenError(
            f"unknown dialect {dialect!r}, try one of {sorted(DIALECTS)}") from None
    buf = bytearray(device.PACKET_LEN)
    buf[0] = device.REPORT_ID_OUT
    at = 1
    for magic in prefix:
        buf[at] = magic
        at += 1
    length = len(payload) + 2
    if at + length >= device.PACKET_LEN:
        raise ScreenError(f"payload of {len(payload)} does not fit a packet")
    buf[at] = cmd_id
    buf[at + 1] = length
    buf[at + 2:at + 2 + len(payload)] = payload
    buf[at + length] = device.checksum(buf, at, at + length)
    return buf


def start_packet(frame_index, frame_count, period=1, size=FRAME_LEN,
                 pic_id=1, dialect=DEFAULT_DIALECT):
    """Announce one frame.

    Field order follows the SDK's XInput branch rather than its DInput one. The
    two disagree: DInput drops `period` -- it writes a literal zero where the
    frame interval belongs -- and adds one to the picture type and index on top
    of the caller's own numbering. Only one of those can be what the firmware
    wants, and the branch that carries the frame interval is the one that can
    describe an animation at all.
    """
    return build(CMD_UPLOAD_START, bytes([
        pic_id,
        1 if frame_count > 1 else 0,      # picType: animation or single image
        frame_count,
        frame_index,
        period,
        (size >> 8) & 0xFF,
        size & 0xFF,
    ]), dialect)


def data_packet(offset, chunk, dialect=DEFAULT_DIALECT):
    return build(CMD_UPLOAD_DATA,
                 bytes([(offset >> 8) & 0xFF, offset & 0xFF]) + bytes(chunk), dialect)


def end_packet(frame_index, size=FRAME_LEN, pic_id=1, dialect=DEFAULT_DIALECT):
    return build(CMD_UPLOAD_END, bytes(
        [pic_id, frame_index, (size >> 8) & 0xFF, size & 0xFF, 0]), dialect)


def finish_packet(frame_count, size=FRAME_LEN, pic_id=1, dialect=DEFAULT_DIALECT):
    return build(CMD_UPLOAD_FINISH, bytes(
        [pic_id, frame_count, (size >> 8) & 0xFF, size & 0xFF, 0]), dialect)


def chunks(frame, size=CHUNK):
    """(offset, chunk) pairs covering a frame, the tail padded rather than short.

    Flydigi pad theirs too, and it matters beyond tidiness: every packet is the
    same length, so a pad counting bytes rather than reading the offset field
    still lands in the right place.
    """
    out = []
    for offset in range(0, len(frame), size):
        chunk = frame[offset:offset + size]
        out.append((offset, chunk + bytes(size - len(chunk))))
    return out


# -- talking to the pad ---------------------------------------------------


def _replies(ctrl, buf, wait, until=None):
    return [r[1:] for r in ctrl.send(buf, wait=wait, until=until) if len(r) > 7]


# What the pad answers each picture command with. Hardware, not the SDK: 210 and
# 211 come back under their own id, but 208 and 209 answer as 0x18 and 0x19 --
# and they are answers, not coincidences, because every field we vary in the
# payload comes back echoed. The SDK has no NewXInput branch for this family at
# all, so there was nothing to predict this from and nothing to check it against
# except the pad.
ACK_ID = {
    CMD_UPLOAD_START: 0x18,
    CMD_UPLOAD_DATA: 0x19,
    CMD_UPLOAD_END: CMD_UPLOAD_END,
    CMD_UPLOAD_FINISH: CMD_UPLOAD_FINISH,
}


def _acked(ctrl, buf, cmd_id, wait):
    """Send one packet and stop as soon as its own answer arrives.

    The early exit is what makes an upload finish in seconds rather than
    minutes: over a thousand packets a frame, each acked exactly once.
    """
    wanted = ACK_ID.get(cmd_id, cmd_id)

    def arrived(replies):
        return any(len(r) > 7 and r[3] == wanted for r in replies)

    return any(body[2] == wanted for body in _replies(ctrl, buf, wait, arrived))


def probe(ctrl, dialects=None, wait=0.5):
    """Ask each envelope whether the pad knows command 208.

    Returns [(dialect, [reply bodies])]; `acked` says whether one answered.

    **This is not free, and an earlier version of this docstring said it was.**
    It sent a finish (211) after each start to close the session politely --
    and 211 is the command that commits picture metadata, so announcing a frame
    that never arrives and then committing it is how you tell a pad it has
    pictures it does not have. On a wired Apex 5 that cost a stored custom
    image: the screen fell back to its status view after the next reboot. Only
    the start goes out now, and even that leaves an announced-but-unsent frame
    behind, which a power cycle or a real upload clears.

    On an Apex 5 you do not need this at all any more -- all four commands are
    known live, see the module docstring. It is here for other pads and other
    firmware.
    """
    results = []
    with ctrl.claim():
        for dialect in (dialects or list(DIALECTS)):
            results.append(
                (dialect, _replies(ctrl, start_packet(1, 1, dialect=dialect), wait)))
    return results


def acked(bodies, cmd_id=CMD_UPLOAD_START):
    return any(body[2] == ACK_ID.get(cmd_id, cmd_id) for body in bodies)


def upload(ctrl, frames, period=1, dialect=DEFAULT_DIALECT, chunk=CHUNK,
           wait=0.5, progress=None):
    """Send frames to the screen. Returns the number of packets written.

    Read the module docstring first: on an Apex 5 this path is **proven inert**
    -- every packet is acknowledged and the display never changes -- because
    Space Station uploads to a k5 through the firmware console instead. Kept for
    other screen pads. For an Apex 5 use `flydigi/screen_ota.py`.

    Held under one claim from the first packet to the last. The pad is tracking
    a byte count across the whole stream, so a config write from the desktop app
    landing in the middle would be read as picture data.
    """
    frames = list(frames)
    if not frames:
        raise ScreenError("nothing to upload")
    # The frame count and index are one byte each in the start packet, so 255 is
    # a hard ceiling rather than a chosen limit. The longest animation Flydigi
    # ship is 144 frames, so it is not a tight one.
    if len(frames) > 255:
        raise ScreenError(
            f"{len(frames)} frames -- the frame count is one byte, so 255 is the most")
    for index, frame in enumerate(frames):
        if len(frame) != FRAME_LEN:
            raise ScreenError(
                f"frame {index} is {len(frame)} bytes, expected {FRAME_LEN}")

    plan = [(index, chunks(frame, chunk)) for index, frame in enumerate(frames)]
    total = sum(len(parts) + 2 for _index, parts in plan) + 1
    sent = 0

    def step(buf, cmd_id, what):
        nonlocal sent
        if not _acked(ctrl, buf, cmd_id, wait):
            raise ScreenError(f"pad did not answer {what}")
        sent += 1
        if progress:
            progress(sent, total)

    with ctrl.claim():
        for index, parts in plan:
            # Frame numbering is 1-based on the wire: Flydigi pass i+1 for the
            # start and the end, and the frame count for the finish.
            step(start_packet(index + 1, len(frames), period, dialect=dialect),
                 CMD_UPLOAD_START, f"the start of frame {index + 1}")
            for offset, part in parts:
                step(data_packet(offset, part, dialect),
                     CMD_UPLOAD_DATA, f"data at offset {offset} of frame {index + 1}")
            step(end_packet(index + 1, dialect=dialect),
                 CMD_UPLOAD_END, f"the end of frame {index + 1}")
        step(finish_packet(len(frames), dialect=dialect),
             CMD_UPLOAD_FINISH, "the upload finish")
    return sent


def period_from_interval(interval_ms):
    """The start packet's frame period, from an interval in milliseconds.

    Flydigi divide by 100 and keep the integer, which is coarse enough that
    anything under 100 ms rounds to nothing -- so the floor is 1 rather than 0,
    since a period of zero is not a frame rate. Their firmware-console path
    scales the same number by 10 instead, and the two cannot both be the unit
    this command wants; only hardware settles which.

    The field is one byte, so the ceiling is 255 -- about 25 seconds a frame.
    """
    return max(1, min(255, int(interval_ms) // 100))


# -- the screen's own settings --------------------------------------------


def _setting(ctrl, sub_id, value, wait=0.5):
    """Write one command-19 sub-setting. True if the pad acknowledged it.

    **The reply does not carry the sub-id, whatever the SDK says.** Flydigi's
    `IsAck` for this family matches `data[2] == 19 && data[5] == subId`, and on
    an Apex 5 `data[5]` is the *value* echoed back -- so their own check could
    never match, and ours copied it and reported a working command as failed.
    Measured:

        19 sub9 value=1  ->  5a a5 13 01 00 01 21
        19 sub8 value=1  ->  5a a5 13 01 00 01 20
        19 sub8 value=0  ->  5a a5 13 01 00 00 1f

    Nothing distinguishes one sub-setting's reply from another's, so an ACK here
    means "a setting was written", not "this setting was written". When it
    matters, read command 3 back -- `read_screen_status` does.
    """
    wanted = 1 if value else 0
    payload = bytes([sub_id, wanted])
    return any(body[2] == CMD_SETTING and body[5] == wanted
               for body in _replies(ctrl, blobs.build(CMD_SETTING, payload), wait))


def set_always_on(ctrl, enabled, wait=0.5):
    """Keep the stored picture on screen while the pad idles. 19, sub-id 9.

    **The SDK calls this bit `OffScreen`, and it is not a screen-off switch.**
    Measured on a wired Apex 5, watching the panel:

        19/9 = 1   the stored picture plays continuously -- an always-on display
        19/9 = 0   the panel is dark, and the logo button wakes the status
                   display for about two seconds before it goes dark again

    So the flag reads inverted against its name, and the name is the giveaway
    once you see the behaviour: 息屏显示, "off-screen display", is the standard
    Chinese term for always-on display -- the display you get while the device
    is *off*, not a switch that turns it off.

    This function takes what you mean rather than what the wire says: `enabled`
    True keeps the picture up, False lets the panel go dark. `set_off_screen`
    used to take the wire value under the SDK's name and was exactly backwards.

    Worth knowing this exists: **`enabled=False` is a real screen blank**, and it
    is the control the pad has and Space Station does not surface -- putting a
    black image up as wallpaper is the workaround people reach for instead.
    """
    return _setting(ctrl, SUB_OFF_SCREEN, enabled, wait)


def set_status_bar_always_on(ctrl, always_on, wait=0.5):
    """Keep the status bar up rather than letting it time out. 19, sub-id 8."""
    return _setting(ctrl, SUB_STATUS_BAR, always_on, wait)


def test_screen_packet(on, colour=(255, 255, 255), faithful=False):
    """The command 242 packet, in either of the two readings of Flydigi's.

    Their builder disagrees with itself. It writes four payload bytes -- on,
    red, green, blue -- sets the length byte to **5**, then puts the checksum at
    offset 9 and sums it over the range a length of 5 implies. A length of 5
    means three payload bytes and a checksum at offset 8; a length of 6 means
    four and a checksum at 9. The placement says 6 and the length byte says 5,
    so one of the two is a typo.

    We default to 6, because it is the reading that keeps the blue byte and
    agrees with where they actually put the checksum -- and because a length of
    5 would drop blue from a colour command. `faithful=True` sends their exact
    bytes for a second attempt if the pad refuses ours.
    """
    red, green, blue = (max(0, min(255, int(c))) for c in colour)
    payload = bytes([1 if on else 0, red, green, blue])
    if not faithful:
        return blobs.build(CMD_TEST_SCREEN, payload)
    buf = device.build(CMD_TEST_SCREEN, payload)
    buf[4] = 5
    buf[9] = device.checksum(buf, 3, 8)
    return buf


def test_screen(ctrl, on, colour=(255, 255, 255), faithful=False, wait=0.5):
    """Flood the screen with one colour. Command 242, no upload involved.

    The cheapest proof that the screen answers the host at all, and the thing to
    try before an upload rather than after a failed one.

    **Verified on a wired Apex 5, and it is stickier than its name suggests.**
    Two things the SDK does not tell you. It floods the **RGB LEDs as well as
    the screen** -- it is a whole-device indicator test, not a screen test. And
    `on=False` **does not clear it**: the command ACKs and the pad stays
    flooded. The only exit found is the pad's own power switch. Treat entering
    this mode as a deliberate act with a physical undo, not as a preview.
    """
    return any(body[2] == CMD_TEST_SCREEN
               for body in _replies(ctrl, test_screen_packet(on, colour, faithful), wait))


def parse_screen_status(body):
    """The screen's bits out of a command 3 reply.

    Command 3 answers for the whole settings block -- sleep time, report rate,
    the stick options -- and the rest of it belongs with the device-settings
    work rather than here. These four bits are the screen's own state, and a
    screen tool that could only write them would be guessing at what it changed.
    """
    if len(body) < 9 or body[2] != CMD_HARDWARE_STATUS:
        raise ScreenError("not a command 3 reply")
    return {
        "status_bar_usable": bool(body[5] & 0x80),
        "status_bar_always_on": bool(body[6] & 0x80),
        # The SDK's `OffScreenUsable` / `OffScreen` bits, reported under what
        # they were measured to do rather than what they are called. See
        # `set_always_on`: the bit set means the picture stays up.
        "always_on_usable": bool(body[7] & 0x01),
        "always_on": bool(body[8] & 0x01),
    }


def read_screen_status(ctrl, wait=0.5):
    for body in _replies(ctrl, blobs.build(CMD_HARDWARE_STATUS), wait):
        if body[2] == CMD_HARDWARE_STATUS:
            return parse_screen_status(body)
    raise ScreenError("no reply to command 3 -- the pad may be asleep")
