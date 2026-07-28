# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Flydigi controller transport over Linux hidraw.

Packet framing (verified on Apex 5, wired and dongle):
    [0] report id 0x03
    [1] 0x5A
    [2] 0xA5
    [3] command id
    [4] payload length
    [5..] payload          -- 32 bytes total

Replies arrive on report 0x04. After stripping the report-id byte,
data[2] is the echoed command id and data[5] is the success flag.
"""
import glob
import os
import select
import time

VID = 0x37D7
PACKET_LEN = 32
MAGIC1 = 0x5A
MAGIC2 = 0xA5
REPORT_ID_OUT = 0x03

# Vendor collection: usage page 0xffa0. Identical on wired and dongle.
VENDOR_DESC_PREFIX = b"\x06\xa0\xff"

CMD_GET_INFO = 0x01
CMD_RUMBLE = 0x12
CMD_SET_FORCE_TRIGGER = 81
CMD_SET_FORCE_TRIGGER_GRIP = 82
CMD_K6_TRIGGER_MODE = 83
CMD_K6_TRIGGER_WAVEFORM = 85
CMD_K6_TRIGGER_REALTIME = 87

SIDE_LEFT = 1
SIDE_RIGHT = 2
SIDE_BOTH = 3


class DeviceNotFound(Exception):
    pass


def checksum(buf, start, end):
    """8-bit sum over [start, end). Matches Flydigi ByteExtension.Crc."""
    return sum(buf[start:end]) & 0xFF


def find_device():
    """Return the hidraw path of the Flydigi vendor command interface.

    Works in both wired and dongle mode -- the node number changes but the
    report descriptor prefix does not.

    Confirmed on hardware: a sleeping Apex 5 does not merely stop answering,
    it leaves the USB bus. `usb 3-4: USB disconnect` with no matching connect,
    and nothing under /dev/hidraw* carrying the vendor id -- indistinguishable
    from an unplugged cable at this level, which is why the message names both.
    """
    for path in sorted(glob.glob("/dev/hidraw*")):
        node = os.path.basename(path)
        try:
            with open(f"/sys/class/hidraw/{node}/device/uevent") as fh:
                if f"{VID:04X}" not in fh.read().upper():
                    continue
            with open(f"/sys/class/hidraw/{node}/device/report_descriptor", "rb") as fh:
                if fh.read(3) == VENDOR_DESC_PREFIX:
                    return path
        except OSError:
            continue
    raise DeviceNotFound(
        "no Flydigi controller found -- press a button to wake the pad, since "
        "it leaves the USB bus entirely when it sleeps, or check the cable")


def build(cmd_id, payload=b""):
    buf = bytearray(PACKET_LEN)
    buf[0] = REPORT_ID_OUT
    buf[1] = MAGIC1
    buf[2] = MAGIC2
    buf[3] = cmd_id
    buf[4] = len(payload)
    buf[5 : 5 + len(payload)] = payload
    return buf


class Controller:
    """Open handle to the vendor interface.

    Multiple processes may hold the node at once (Steam Input does), so this
    deliberately does not take exclusive access.
    """

    def __init__(self, path=None):
        self.path = path or find_device()
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, buf, wait=0.3):
        """Write a packet and collect replies for `wait` seconds."""
        os.write(self.fd, bytes(buf))
        replies = []
        deadline = time.time() + wait
        while time.time() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, deadline - time.time()))
            if not ready:
                continue
            data = os.read(self.fd, 64)
            if data:
                replies.append(data)
        return replies

    def command(self, cmd_id, payload=b"", wait=0.3):
        return self.send(build(cmd_id, payload), wait=wait)

    @staticmethod
    def ack_ok(reply, cmd_id):
        """Interpret a reply the way Flydigi's ParseAckData does."""
        if not reply or len(reply) < 7:
            return False
        body = reply[1:]  # strip report-id byte
        return body[2] == cmd_id and body[5] == 1
