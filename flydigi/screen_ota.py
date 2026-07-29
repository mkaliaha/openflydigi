# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Writing pictures to the Apex 5's screen, the way Space Station does it.

**This is the path that works on a k5**, and the HID picture family in
`screen.py` is not -- see PROTOCOL.md §8b. That one ACKs every packet, echoes
every field, and never changes the display, because on this pad the panel is
driven by a separate Freq chip that the main chip does not forward to. Space
Station knows: `upload_pic2screen` branches `deviceCode == "k5"` to here and
sends every other screen pad down the HID route.

The shape of it, traced through all four of their layers:

    1. convert the frames to the 25604-byte LVGL images of `screen.py`
    2. HID command 31, chipModule = CHIP_SCREEN -- the pad re-enumerates
    3. wait for a USB CDC serial device, VID FFAA PID 5555
    4. talk the UART OTA below at 921600 8N1
    5. the pad syncs for ~15 s and reboots itself

**On command 31, and why this module exists despite the standing rule against
it.** The rule is about *program* images: four bootloader vendors, no recovery,
a dozen independently flashable chips. A picture upload is a different operation
that happens to share the transport, and three things separate them. The picture
base address is **read back from the chip** rather than chosen here, and every
erase and write is `base + offset`, so nothing this module sends can address the
program region -- reaching it needs `ScreenUpgradeType.PROGRAM`, which is not
implemented here and should not be. `PicResetDevice` is a defined way out. And
the factory image ships with Space Station as
`Configs/Controller/k5/default/default_screen_image_<deviceType>.bin`, so a bad
upload has a documented repair rather than only a power cycle.

That is the argument for this one operation. It does not extend to flashing
anything else, and `enter_upgrade_mode` deliberately takes no chip argument.
"""
import glob
import os
import select
import struct
import time

from . import blobs, screen

CMD_SWITCH_USB = 31
CHIP_SCREEN = 4

# Opcodes. Note the enum in `OtaNewUpdater` numbers PicGetVersion 10 and
# PicGetBaseAddr 11, but the state machine fetches the base address *first* --
# read the transitions, not the declaration order.
OP_ERASE = 3
OP_WRITE = 5
OP_PIC_GET_VERSION = 10
OP_PIC_GET_BASE = 11
OP_PIC_RESET = 12

# `ScreenUpgradePicType`. Space Station sends PNG for a single frame and GIF for
# an animation, which is about how many frames there are rather than what the
# user's file was.
PIC_TYPE_GIF = 1
PIC_TYPE_PNG = 2

USB_VID = 0xFFAA
USB_PID = 0x5555
BAUD_ATTR = "B921600"

ERASE_BLOCK = 4096
WRITE_CHUNK = 55

# Their timer ticks every 300 ms and gives up after 60 of them.
REPLY_TIMEOUT = 18.0
PORT_TIMEOUT = 30.0
SWITCH_SETTLE = 5.0            # Space Station's own hard-coded wait

# The length field is a per-opcode constant and **not** the length of what
# follows. Three of the five disagree with their own payloads: PicGetVersion
# says 6 and sends none, EraseSector says 6 and sends 4, WriteData says 64 --
# the whole packet -- and sends 61. Copied rather than computed, because a
# derived length would be right twice and wrong three times.
LENGTH_FIELD = {
    OP_PIC_GET_BASE: 4,
    OP_PIC_GET_VERSION: 6,
    OP_ERASE: 6,
    OP_WRITE: 64,
    OP_PIC_RESET: 8,
}


class OtaError(Exception):
    pass


# -- the checksum ---------------------------------------------------------
#
# A CRC-32 variant of Flydigi's own, and not any standard one. It uses the
# ordinary reflected table but indexes it with bits 8..15 of the running value
# rather than the top byte, and it runs in C# `int` -- 32-bit, signed, with
# division that truncates toward zero where Python's floors. All three of those
# change the answer, so this is written to emulate the arithmetic rather than to
# look like a CRC.

_POLY_TABLE = None


def _table():
    global _POLY_TABLE
    if _POLY_TABLE is None:
        table = []
        for index in range(256):
            value = index
            for _ in range(8):
                value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
            table.append(value)
        _POLY_TABLE = table
    return _POLY_TABLE


def _to_signed(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def checksum(data):
    """Flydigi's `AppOtasCrcCal`, faithful to its C# integer semantics."""
    table = _table()
    crc = 0
    for byte in data:
        # `crc / 256` in C#: truncates toward zero, so it is not `crc >> 8` once
        # the value has gone negative.
        quotient = int(crc / 256)
        crc = _to_signed(crc << 8)
        crc = _to_signed(crc ^ _to_signed(table[(quotient ^ byte) & 0xFF]))
    return crc & 0xFFFFFFFF


# -- getting the pad into upgrade mode ------------------------------------


def enter_upgrade_mode(ctrl, wait=0.5):
    """HID command 31 for the **screen chip only**. The pad then re-enumerates.

    Takes no chip argument on purpose. Command 31 is a one-way door for chips we
    have no flashing protocol for; this module has one for exactly the screen,
    so this function can reach exactly the screen.

    Returns True if the pad acknowledged. It usually will not have time to --
    it is leaving the bus -- so a False here is not a failure, and the real
    check is whether the serial port turns up.
    """
    payload = bytes([CHIP_SCREEN])
    try:
        replies = [r[1:] for r in ctrl.send(blobs.build(CMD_SWITCH_USB, payload), wait=wait)
                   if len(r) > 7]
    except OSError:
        return False           # the node can vanish mid-write, which is the point
    return any(body[2] == CMD_SWITCH_USB for body in replies)


def find_port(vid=USB_VID, pid=USB_PID):
    """The tty for the pad's bootloader, or None.

    Space Station asks WMI for a `Win32_PnPEntity` whose caption carries the
    VID/PID. The Linux equivalent is sysfs: every tty backed by USB has the
    interface as its `device`, and the ids live on the parent.
    """
    for path in sorted(glob.glob("/sys/class/tty/ttyACM*") + glob.glob("/sys/class/tty/ttyUSB*")):
        base = os.path.join(path, "device", "..")
        try:
            with open(os.path.join(base, "idVendor")) as fh:
                have_vid = int(fh.read().strip(), 16)
            with open(os.path.join(base, "idProduct")) as fh:
                have_pid = int(fh.read().strip(), 16)
        except OSError:
            continue
        if (have_vid, have_pid) == (vid, pid):
            return "/dev/" + os.path.basename(path)
    return None


def wait_for_port(timeout=PORT_TIMEOUT, poll=0.5, vid=USB_VID, pid=USB_PID):
    deadline = time.monotonic() + timeout
    while True:
        port = find_port(vid, pid)
        if port:
            return port
        if time.monotonic() >= deadline:
            raise OtaError(
                f"no {vid:04x}:{pid:04x} serial device appeared within {timeout:g}s. "
                "The pad may not have switched -- power-cycle it at its own switch "
                "and check it comes back as a gamepad.")
        time.sleep(poll)


# -- the link -------------------------------------------------------------


class OtaLink:
    """A raw 921600 8N1 tty, opened the way their SerialPort is configured.

    Deliberately termios rather than pyserial: the backend has no dependencies
    and this is not enough serial port to be worth acquiring one.
    """

    TIOCMBIS = 0x5416
    TIOCM_DTR = 0x002
    TIOCM_RTS = 0x004

    def __init__(self, path):
        import fcntl
        import termios
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        speed = getattr(termios, BAUD_ATTR)
        attrs[0] = 0                                   # iflag: no translation
        attrs[1] = 0                                   # oflag: no post-processing
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0                                   # lflag: raw, no echo
        attrs[4] = attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        # Their SerialPort sets DtrEnable and RtsEnable; some bootloaders watch
        # these lines, so assert them rather than relying on the driver default.
        fcntl.ioctl(self.fd, self.TIOCMBIS,
                    struct.pack("I", self.TIOCM_DTR | self.TIOCM_RTS))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def write(self, data):
        os.write(self.fd, data)

    def read_reply(self, timeout=REPLY_TIMEOUT):
        """One reply, or None on timeout.

        Replies are short and arrive whole, but a tty can hand them over in
        pieces, so this keeps reading until it has a header's worth or the
        stream goes quiet -- the end-of-session reply is under five bytes and
        would be discarded by a rule that insisted on a full header.
        """
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [],
                                        max(0.0, deadline - time.monotonic()))
            if not ready:
                continue
            chunk = os.read(self.fd, 64)
            if not chunk:
                continue
            buf += chunk
            # Give a fragmented reply a moment to finish, then take what we have.
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                continue
            return buf
        return None


def build(opcode, payload=b""):
    return bytes([opcode]) + struct.pack("<H", LENGTH_FIELD[opcode]) + payload


def parse(reply):
    """(result, opcode, payload). A short reply carries only result and opcode."""
    if reply is None or len(reply) < 2:
        return None, None, b""
    if len(reply) < 5:
        return reply[0], reply[1], b""
    return reply[0], reply[1], reply[4:]


# -- the upload -----------------------------------------------------------


def picture_type(frame_count):
    return PIC_TYPE_PNG if frame_count == 1 else PIC_TYPE_GIF


def frame_rate(interval_ms):
    """Their `Math.round(frameInterval / 10)` -- hundredths of a second.

    Not the `/100` the HID start packet uses. The two paths disagree, and this
    is the one that reaches the screen.
    """
    return max(1, min(255, int(round(interval_ms / 10.0))))


def upload(link, frames, interval_ms=100, restore_default=False, progress=None):
    """Write a picture set over an open link. Returns the base address used.

    The sequence is `OtaNewUpdater`'s, flattened from its 300 ms timer into
    straight line code -- every step here waits for the reply that step's state
    transition waited for.
    """
    data = screen.join_frames(frames)
    if not data:
        raise OtaError("nothing to upload")

    def exchange(opcode, payload=b"", expect=None):
        link.write(build(opcode, payload))
        result, got, body = parse(link.read_reply())
        if got is None:
            raise OtaError(f"no reply to opcode {opcode}")
        if expect is not None and got != expect:
            raise OtaError(f"opcode {opcode}: expected a reply for {expect}, got {got}")
        return result, body

    _result, body = exchange(OP_PIC_GET_BASE, bytes([
        picture_type(len(frames)),
        len(frames) & 0xFF,
        frame_rate(interval_ms),
        1 if restore_default else 0,
    ]), expect=OP_PIC_GET_BASE)
    if len(body) < 4:
        raise OtaError("the pad did not return a picture base address")
    base = struct.unpack("<I", body[:4])[0]

    exchange(OP_PIC_GET_VERSION, expect=OP_PIC_GET_VERSION)

    erases = (len(data) + ERASE_BLOCK - 1) // ERASE_BLOCK
    writes = (len(data) + WRITE_CHUNK - 1) // WRITE_CHUNK
    total = erases + writes
    done = 0

    for index in range(erases):
        exchange(OP_ERASE, struct.pack("<I", base + index * ERASE_BLOCK),
                 expect=OP_ERASE)
        done += 1
        if progress:
            progress(done, total)

    for index in range(writes):
        offset = index * WRITE_CHUNK
        chunk = data[offset:offset + WRITE_CHUNK]
        # The inner length stays 55 even when the tail is shorter, and the
        # packet is simply short. Flydigi's writer does the same.
        exchange(OP_WRITE,
                 struct.pack("<I", base + offset) + struct.pack("<H", WRITE_CHUNK) + chunk,
                 expect=OP_WRITE)
        done += 1
        if progress:
            progress(done, total)

    link.write(build(OP_PIC_RESET,
                     struct.pack("<I", len(data)) + struct.pack("<I", checksum(data))))
    _result, got, _body = parse(link.read_reply())
    if got != OP_PIC_RESET:
        raise OtaError(f"the pad did not confirm the reset (got {got})")
    return base


def upload_picture(ctrl, frames, interval_ms=100, restore_default=False,
                   progress=None, settle=SWITCH_SETTLE, port=None):
    """The whole thing: switch the pad, find the port, write, let it reboot.

    `ctrl` is a live `device.Controller`; it is unusable afterwards, since the
    pad leaves the HID bus. Pass `port` to skip the switch when the pad is
    already in upgrade mode -- which is what to do after a failed attempt,
    rather than sending 31 to a pad that is already across.
    """
    if port is None:
        enter_upgrade_mode(ctrl)
        time.sleep(settle)
        port = wait_for_port()
    with OtaLink(port) as link:
        return upload(link, frames, interval_ms, restore_default, progress)
