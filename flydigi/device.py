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
REPORT_ID_IN = 0x04

# **The Vader 5 Pro receiver speaks this same protocol with unnumbered
# reports.** Measured on a Vader 5 Pro, 2.4G receiver `37d7:2401`, firmware
# 7.1.4.4 / receiver 0.4.6.7: its vendor interface (usage page 0xffa0,
# interface 1) declares one 32-byte input and one 32-byte output report and no
# report ids. The frame inside is byte for byte the one above -- 5A A5, command,
# payload length + 2, payload, additive checksum at 3 + length -- and a command-1
# reply carries the Apex 5's fields at the Apex 5's offsets:
#
#   5a a5 01 01 00 82 02 00 00 00 00 05 45 01 00 71 44 04 67 ...
#            |  |  ^^ DeviceType 130 = f5, then dongle, mac, battery 5/5
#
# What differs is only the report id: 0x00 placeholder out, nothing in. So the
# Controller writes such a node with 0x00 where 0x03 was, and prepends 0x04 to
# what it reads, and every legacy parser keeps its indexing. The same 32-byte
# write (placeholder + 31 bytes) is what Vader5-Control sends to this pad.
#
# PID 2401 is also the id Space Station's new-architecture probe accepts, which
# is why it sits in `gpa6.PRODUCT_IDS`. Sending a GPA6 frame to a Vader 5 got
# an answer only intermittently -- its checksum sits at byte 31 rather than
# after the payload -- and the 0x01 reply then fails GPA6's strict checksum:
# the pad's trailing byte is 0x27 where a sum over bytes 2..30 is 0x92. That was
# the "malformed GPA6 reply to 0x01". The product string decides which dialect
# a 2401 node gets; `identity.require` still checks the DeviceType afterwards.
VADER5_RECEIVER_PID = 0x2401

# **The receiver drops a frame identical to the one before it**, if it arrives
# within about a second. Measured: command 1 sent five times 0.5 s apart answers
# 1st and 4th only; 1.0 s apart, every time; 1, 16, 1, 16, 1 back to back, every
# time. Changing a byte after the checksum does not help, so it is the frame
# rather than the raw report that is compared. A repeated read -- a poll, a retry
# -- would otherwise time out every other call. So a repeat inside the window is
# preceded by one read-only separator (command 16, or 1 when the repeat is
# itself 16), whose reply is consumed before the real exchange.
REPEAT_WINDOW = 1.5
SEPARATOR_CMDS = (0x10, 0x01)

# Vendor collection: usage page 0xffa0. Identical on wired and dongle.
VENDOR_DESC_PREFIX = b"\x06\xa0\xff"

# **The vendor id and the descriptor prefix do not identify a pad.** The CD2
# charging dock is 37d7 too, and its report descriptor also begins 06 a0 ff, so
# for as long as this matched on those two alone it would hand a caller the
# dock. Measured here: with the pad asleep -- which takes it off the USB bus
# entirely, leaving no pad node at all -- `find_device()` returned the dock's
# /dev/hidraw7, and every ungated tool would have written pad packets at it.
#
# The sort order is a second, narrower way in rather than the main one. hidraw
# hands out the lowest free minor, so a pad that sleeps and returns reclaims
# the minors it freed and lands *below* a dock plugged in after it -- observed
# twice this session. What it takes to lose the race is the dock being attached
# first, or the string sort putting "hidraw10" before "hidraw7".
#
# What tells them apart is the product id's top nibble, which is what Flydigi
# key on as well: `ControllerHidManager` takes `pid >> 12 == 2`,
# `ChargerHidManager` takes `pid >> 12 == 6` and `CoolerHidManager` takes
# `pid & 0xff00 == 0x1000`. The three sets are disjoint, which is why Space
# Station never had this problem. Their controller test carries a third clause,
# `pid >> 8 != 8`, that cannot fire once the nibble is 2; it is not reproduced.
#
# The nibble, not the whole id, on purpose. `0x2501` is this pad and it is the
# only Flydigi product id that appears anywhere in the decompiled source, so
# hard-coding it would narrow this to one SKU on no evidence that the others
# differ. Matching the nibble keeps the old behaviour -- open any pad, and let
# `identity.require` refuse the wrong one on its device type -- while no longer
# opening something that is not a pad at all.
FAMILY_PAD = 2
FAMILY_DOCK = 6
# `CoolerHidManager` tests `pid & 0xff00 == 0x1000`, which is this nibble with
# an 8-bit model space under it. Named so a message can say what was looked
# for; nothing here drives one.
FAMILY_COOLER = 1

FAMILY_NAMES = {FAMILY_PAD: "controller", FAMILY_DOCK: "charging dock",
                FAMILY_COOLER: "cooler"}

CMD_GET_INFO = 0x01
CMD_RUMBLE = 0x12
CMD_SET_FORCE_TRIGGER = 81
CMD_SET_FORCE_TRIGGER_GRIP = 82
# Historical SDK command numbers. Current Apex 6 framing and payloads live in
# gpa6.py; these constants do not make the legacy packet builder compatible.
CMD_K6_TRIGGER_MODE = 83
CMD_K6_TRIGGER_WAVEFORM = 85
CMD_K6_TRIGGER_REALTIME = 87

SIDE_LEFT = 1
SIDE_RIGHT = 2
# **The pad ignores this one.** It is in Flydigi's `ForceTriggerSide` enum, and a
# trigger command carrying it ACKs and does nothing -- measured at full
# resistance with rumble pulses marking the phases, twice, against per-side
# commands that worked every time in between. Their own SDK never produces it
# either: `SetForceTriggerConfigImpl` takes a left config and a right config and
# sends two commands. So send one command per trigger; everything in this
# repository does, which is why the trap went unnoticed for so long.
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


def hid_ids(node):
    """(vendor, product) for a hidraw node, or None if it declares neither.

    From `HID_ID=<bus>:<vendor>:<product>` in the node's uevent, all three
    zero-padded to eight hex digits.
    """
    try:
        with open(f"/sys/class/hidraw/{node}/device/uevent") as fh:
            for line in fh:
                if line.startswith("HID_ID="):
                    parts = line.strip().split("=", 1)[1].split(":")
                    if len(parts) == 3:
                        return int(parts[1], 16), int(parts[2], 16)
    except (OSError, ValueError):
        return None
    return None


def hid_name(node):
    """`HID_NAME` for a hidraw node, or "". The product string, as the kernel
    saw it -- "Flydigi APEX5 Wireless", "flydigi Flydigi CD2"."""
    try:
        with open(f"/sys/class/hidraw/{node}/device/uevent") as fh:
            for line in fh:
                if line.startswith("HID_NAME="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return ""


def is_mock(path):
    """Whether a path belongs to the mock bus rather than to the kernel.

    A separate question from "is the mock bus on": a real pad and a fake one
    are enumerated together, so what matters about a given device is which of
    the two it is. Everything a person can see it through says so --
    `registry.describe`, `flydigi-devices list`, the app's device list.
    """
    from . import mock
    return mock.is_mock(path)


def is_vader5_receiver(node, ids=None):
    """Whether a hidraw node is the Vader 5 receiver's unnumbered interface."""
    ids = ids or hid_ids(node)
    return (ids is not None and ids[0] == VID and ids[1] == VADER5_RECEIVER_PID
            and "vader" in hid_name(node).lower())


def find_nodes(family=FAMILY_PAD):
    """Every Flydigi command interface of one device family, in node order.

    The family nibble picks the kind of device. The descriptor prefix then
    picks the vendor collection, and is applied **to pads only** -- which is
    where the reference applies it and where it is needed: a pad publishes a
    keyboard/mouse node under the same ids, and only its second interface
    carries `06 a0 ff`. `ChargerHidManager.FindSpecialHidDevice` tests the
    vendor id and the nibble and nothing else, so requiring a usage page of a
    dock would be this project inventing a condition Flydigi do not have, and
    would silently hide any dock that ordered its collections differently.

    **Mock devices come out of here too, and only when asked for.** This is the
    one place the whole stack agrees on what is attached -- the tools, the
    daemon and the app all reach the bus through it -- so it is the one place a
    device that is not there has to be added, or they would disagree about what
    exists. `FLYDIGI_MOCK_BUS` unset means not a line of this runs.
    """
    from . import mock
    mocking = mock.enabled()
    if not (mocking and mock.hide_real()):
        yield from _real_nodes(family)
    if mocking:
        yield from mock.nodes(family)


def _real_nodes(family):
    for path in sorted(glob.glob("/dev/hidraw*")):
        node = os.path.basename(path)
        ids = hid_ids(node)
        if ids is None or ids[0] != VID or (ids[1] >> 12) != family:
            continue
        if family != FAMILY_PAD:
            yield path
            continue
        try:
            with open(f"/sys/class/hidraw/{node}/device/report_descriptor", "rb") as fh:
                descriptor = fh.read()
                from . import gpa6
                if ids[1] in gpa6.PRODUCT_IDS:
                    try:
                        gpa6.report_layout(descriptor)
                    except gpa6.ProtocolError:
                        continue
                    yield path
                elif descriptor[:3] == VENDOR_DESC_PREFIX:
                    yield path
        except OSError:
            continue


def find_device(family=FAMILY_PAD):
    """Return the hidraw path of the Flydigi vendor command interface.

    Works in both wired and dongle mode -- the node number changes but the
    report descriptor prefix does not.

    Confirmed on hardware: a sleeping Apex 5 does not merely stop answering,
    it leaves the USB bus. `usb 3-4: USB disconnect` with no matching connect,
    and nothing under /dev/hidraw* carrying the vendor id -- indistinguishable
    from an unplugged cable at this level, which is why the message names both.

    With two devices of the same family attached this still returns whichever
    node sorts first, and `identity.require` is still what refuses the wrong
    one. What it no longer does is return a *different kind* of device.
    """
    for path in find_nodes(family):
        return path
    if family == FAMILY_PAD:
        raise DeviceNotFound(
            "no Flydigi controller found -- press a button to wake the pad, "
            "since it leaves the USB bus entirely when it sleeps, or check "
            "the cable")
    if family == FAMILY_DOCK:
        raise DeviceNotFound(
            "no Flydigi charging dock found -- check that it is plugged into "
            "the host and not just into power")
    raise DeviceNotFound(
        f"no Flydigi {FAMILY_NAMES.get(family, f'device of family {family}')} "
        f"found")


def build(cmd_id, payload=b""):
    buf = bytearray(PACKET_LEN)
    buf[0] = REPORT_ID_OUT
    buf[1] = MAGIC1
    buf[2] = MAGIC2
    buf[3] = cmd_id
    buf[4] = len(payload)
    buf[5 : 5 + len(payload)] = payload
    return buf


def acked(cmd_id):
    """`Controller.send`'s `until` for a command its own ACK ends.

    A free function rather than a method so the fakes in `tests/` and
    `flydigi/mock/` do not have to grow a copy of it: it interprets bytes and
    needs no handle. `Controller.ack_ok` is what a caller then tests each reply
    with, so stopping here returns the very reply it was going to accept --
    the answer is unchanged and only the waiting is shorter.
    """
    def check(replies):
        return Controller.ack_ok(replies[-1], cmd_id)
    return check


class Controller:
    """Open handle to the vendor interface.

    The node stays open to anyone -- Steam Input holds it too -- and nothing
    here tries to prevent that. What `claim` prevents is our own processes
    talking over each other; see below.
    """

    # What a bare construction goes looking for. A subclass that drives another
    # kind of device says so here rather than resolving a path of its own, so
    # that the resolving happens once, in `__new__`, where the mock bus is also
    # consulted. `flydigi/charger.py:Dock` is the one subclass.
    FAMILY = FAMILY_PAD
    dialect = "legacy"
    # True for the Vader 5 receiver: legacy frames over unnumbered reports.
    unnumbered = False
    _last_frame = None
    _last_sent = 0.0

    def __new__(cls, path=None):
        """Resolve which device this is, and hand back a fake for a fake one.

        The resolving moved up here from `__init__` because the answer decides
        whether there is an `__init__` to run at all: a mock path has no file
        behind it, so what comes back is the in-process device from
        `flydigi/mock/`, and Python skips `__init__` for a `__new__` that
        returned something that is not an instance of the class. That fake
        answers `send`, `command`, `claim` and `close` -- the whole of what
        anything here asks of a handle.

        Doing it in `__init__` instead would mean opening the file first and
        deciding afterwards, which cannot work: there is no file. Doing it in
        every caller would mean every caller knowing about mock devices, which
        is the coupling this avoids -- `Controller()` is written in a dozen
        places and none of them should have to care.
        """
        path = path or find_device(cls.FAMILY)
        from . import mock
        fake = mock.instance(path)
        if fake is not None:
            return fake
        handle = super().__new__(cls)
        handle.path = path
        return handle

    def __init__(self, path=None):
        # `path` is deliberately not read here: `__new__` had to resolve it to
        # decide whether this is a real device, and asking the bus a second
        # time could get a second answer -- the node numbers move.
        from . import gpa6
        node = os.path.basename(self.path)
        ids = hid_ids(node)
        if ids and ids[0] == VID and ids[1] in gpa6.PRODUCT_IDS:
            with open(f"/sys/class/hidraw/{node}/device/report_descriptor", "rb") as fh:
                layout = gpa6.report_layout(fh.read())
            if is_vader5_receiver(node, ids):
                if (layout.output_id, layout.output_size, layout.input_ids) != (0, PACKET_LEN, (0,)):
                    raise OSError(f"{self.path}: unexpected Vader 5 report layout {layout}")
                self.unnumbered = True
            else:
                self.report_layout = layout
                self.dialect = "gpa6"
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
        if not self._threads.acquire(timeout=timeout):
            raise DeviceBusy(f"another thread is using {self.path}")
        try:
            if self._depth == 0:
                self._flock(timeout)
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
                if self._depth == 0:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            self._threads.release()

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
                if not os.read(self.fd, 65 if self.dialect == "gpa6" else 64):
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
        if self.dialect == "gpa6":
            from .gpa6 import ProtocolError
            if (len(buf) != self.report_layout.output_size + 1
                    or buf[0] != self.report_layout.output_id
                    or buf[1:3] != b"\x5a\xa5"):
                raise ProtocolError("legacy packets are refused on GPA6 devices")
        out = self._outgoing(buf)
        with self.claim():
            self._drain()
            if self.unnumbered:
                self._separate(out)
            if os.write(self.fd, out) != len(out):
                raise OSError("short HID report write")
            if self.unnumbered:
                self._last_frame, self._last_sent = out, time.monotonic()
            replies = []
            deadline = time.time() + wait
            while time.time() < deadline:
                ready, _, _ = select.select(
                    [self.fd], [], [], max(0.0, deadline - time.time()))
                if not ready:
                    continue
                data = self._incoming(
                    os.read(self.fd, 65 if self.dialect == "gpa6" else 64))
                if data:
                    replies.append(data)
                    if until is not None and until(replies):
                        break
            return replies

    def _outgoing(self, buf):
        """The bytes to write: the legacy frame, re-addressed for this node."""
        buf = bytes(buf)
        if not self.unnumbered:
            return buf
        if len(buf) != PACKET_LEN or buf[0] != REPORT_ID_OUT or buf[1:3] != b"\x5a\xa5":
            raise ValueError("only legacy 32-byte vendor frames go to a Vader 5 receiver")
        return b"\x00" + buf[1:]

    def _separate(self, out):
        """Make a repeat inside REPEAT_WINDOW differ from the frame before it."""
        if (out != self._last_frame
                or time.monotonic() - self._last_sent >= REPEAT_WINDOW):
            return
        cmd = SEPARATOR_CMDS[out[3] == SEPARATOR_CMDS[0]]
        sep = bytearray(PACKET_LEN)
        sep[1:6] = bytes((MAGIC1, MAGIC2, cmd, 2, (cmd + 2) & 0xFF))
        if os.write(self.fd, bytes(sep)) != len(sep):
            raise OSError("short HID report write")
        self._last_frame, self._last_sent = bytes(sep), time.monotonic()
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [self.fd], [], [], max(0.0, deadline - time.monotonic()))
            if ready:
                data = os.read(self.fd, 64)
                if len(data) > 2 and data[:3] == bytes((MAGIC1, MAGIC2, cmd)):
                    return

    def _incoming(self, data):
        """A reply as legacy parsers index it: report id 0x04 at [0]."""
        if self.unnumbered and len(data) == PACKET_LEN and data[:2] == b"\x5a\xa5":
            return bytes([REPORT_ID_IN]) + bytes(data)
        return data

    def command(self, cmd_id, payload=b"", wait=0.3, until=None):
        return self.send(build(cmd_id, payload), wait=wait, until=until)

    @staticmethod
    def ack_ok(reply, cmd_id):
        """Interpret a reply the way Flydigi's ParseAckData does."""
        if not reply or len(reply) < 7:
            return False
        body = reply[1:]  # strip report-id byte
        return body[2] == cmd_id and body[5] == 1
