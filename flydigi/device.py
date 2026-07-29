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

Several processes drive this pad at once -- the desktop app polls it, a
per-game driver rewrites trigger effects as often as every 50 ms, and the
config editor streams whole profiles -- so an exchange is not just a write.
See `Controller.claim`.
"""
import contextlib
import fcntl
import glob
import os
import select
import threading
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

# How long to wait for another process to finish its exchange. Generous on
# purpose: the longest thing anyone here holds the pad for is a config write,
# which streams up to 42 packets and waits for an ACK on each. A lock is
# released by the kernel when its holder's file closes, crash included, so
# waiting cannot be waiting on a corpse.
CLAIM_TIMEOUT = 5.0
CLAIM_POLL = 0.002


class DeviceNotFound(Exception):
    pass


class DeviceBusy(Exception):
    """Another process held the pad for longer than we were willing to wait."""


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

    The node stays open to anyone -- Steam Input holds it too -- and nothing
    here tries to prevent that. What `claim` prevents is our own processes
    talking over each other; see below.
    """

    def __init__(self, path=None):
        self.path = path or find_device()
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        # flock is per *open file description*, so two Controllers in one
        # process already exclude each other -- but two threads sharing one
        # Controller would not. This covers that case; between them the pair
        # is exclusive whichever way the caller is arranged.
        self._threads = threading.RLock()
        self._depth = 0

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextlib.contextmanager
    def claim(self, timeout=CLAIM_TIMEOUT):
        """Hold the pad for one exchange, excluding our own other processes.

        `send` takes this for a single packet, which is enough for the effect
        commands. Wrap a *sequence* in it by hand -- a config write is a
        header, up to 42 packets and a save, and half of one interleaved with
        anything else is the failure worth preventing.

        The lock is `flock(2)`, which is advisory: it binds only processes that
        ask for it. That is the right kind of lock here rather than a
        limitation to apologise for. Everything in this project goes through
        this class, so our own processes are covered; Steam and SDL hold the
        same node, will not take the lock, and **must not be excluded** -- the
        vendor interface keeps working with Steam Input on, which is what lets
        trigger effects run in games Steam has taken the pad for. A lock that
        shut them out would break a working configuration to fix nothing.

        What it does not cover: Steam's own writes can still land between ours.
        Harmless for effects, which the next frame overwrites, and a risk only
        for a config write -- a rare, deliberate action, not something a game
        triggers.

        Re-entrant, so a claimed sequence can call `send` freely.
        """
        with self._threads:
            if self._depth == 0:
                self._flock(timeout)
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
                if self._depth == 0:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)

    def _flock(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise DeviceBusy(
                        f"another process has held {self.path} for more than "
                        f"{timeout:g}s") from None
                time.sleep(CLAIM_POLL)

    def _drain(self):
        """Throw away replies that arrived before we asked anything.

        Replies are broadcast to every reader of the node, so a poll by the
        desktop app lands in a driver's buffer and vice versa. Read under the
        claim, so anything already waiting provably belongs to an exchange that
        is over -- and `ack_ok` matches on the command byte alone, which would
        otherwise be happy to accept it.
        """
        while True:
            ready, _, _ = select.select([self.fd], [], [], 0)
            if not ready:
                return
            try:
                if not os.read(self.fd, 64):
                    return
            except BlockingIOError:
                return

    def send(self, buf, wait=0.3, until=None):
        """Write a packet and collect replies for `wait` seconds.

        `until` is a predicate on the replies so far; when it returns true the
        collection stops early. Without it this always waits the full `wait`,
        which is right when the answer may arrive in several packets and no
        caller can say how many -- a config read streams 42 of them.

        It is wrong for a long stream of one-for-one exchanges. A screen frame
        is over a thousand packets, each acked by exactly one reply, and waiting
        out the timeout on every one of them turns a two-second upload into nine
        minutes. Pass `until` there and the wait becomes a ceiling rather than a
        cost.
        """
        with self.claim():
            self._drain()
            os.write(self.fd, bytes(buf))
            replies = []
            deadline = time.time() + wait
            while time.time() < deadline:
                ready, _, _ = select.select(
                    [self.fd], [], [], max(0.0, deadline - time.time()))
                if not ready:
                    continue
                data = os.read(self.fd, 64)
                if data:
                    replies.append(data)
                    if until is not None and until(replies):
                        break
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
