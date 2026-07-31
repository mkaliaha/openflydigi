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
# "K6" is Flydigi's DeviceCode for the Apex 6, which had not shipped as of
# July 2026 -- these three are transcribed from the SDK and have never been
# sent to hardware. The Apex 5 is "k5" and uses 81/82 above. See
# docs/findings-other-devices.md for the full device-code table.
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
                if fh.read(3) == VENDOR_DESC_PREFIX:
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
