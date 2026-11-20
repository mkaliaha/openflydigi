#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the transport: the claim, and what it is for.

Runs against real file descriptors rather than a stand-in, because the thing
under test *is* a kernel behaviour -- a fake would only prove that the fake
agrees with me. Two fixtures, because no single one shows both halves:

  * `FakeNode`, a socket pair, for what a send does. What the Controller
    writes comes out of the far end and what the far end writes arrives as a
    reply, which is the shape of a hidraw node.
  * `Lockable`, two Controllers over one path, for the claim. They must be
    *separately opened*: `flock` attaches to the open file description, so a
    dup'd handle is the same lock holder and would quietly pass every test
    here while excluding nothing in the field.

    python3 tests/test_device.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import device  # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


class FakeNode:
    """A socket pair dressed as a hidraw node.

    `controller` is what a Controller is handed; `pad` is the other end, which
    receives what is written and can push replies back.
    """

    def __init__(self):
        self.pad, self.node = socket.socketpair()
        self.node.setblocking(False)
        self.pad.setblocking(False)

    def controller(self):
        return _wrap(os.dup(self.node.fileno()), "socketpair")

    def reply(self, data, after=0.0):
        """Push a reply, optionally on a timer -- a reply that lands *during*
        the read window is the only kind that proves anything about draining."""
        if not after:
            self.pad.send(bytes(data))
            return
        timer = threading.Timer(after, lambda: self.pad.send(bytes(data)))
        timer.daemon = True
        timer.start()
        return timer

    def written(self):
        try:
            return self.pad.recv(4096)
        except BlockingIOError:
            return b""

    def close(self):
        self.pad.close()
        self.node.close()


def _wrap(fd, path):
    """A Controller around an already-open fd, skipping the hardware search."""
    ctrl = object.__new__(device.Controller)
    ctrl.path = path
    ctrl.fd = fd
    ctrl._threads = threading.RLock()
    ctrl._depth = 0
    return ctrl


class Lockable:
    """One path, two independently opened Controllers -- the app and a driver."""

    def __init__(self):
        handle, self.path = tempfile.mkstemp(prefix="apex5-claim-")
        os.close(handle)

    def controller(self):
        return _wrap(os.open(self.path, os.O_RDWR), self.path)

    def close(self):
        os.unlink(self.path)


def ack(cmd_id):
    """A reply shaped like the pad's: report id, magic, command, success."""
    return bytes([0x04, device.MAGIC1, device.MAGIC2, cmd_id, 1, 0, 1, 0x80])


def test_a_claim_excludes_another_process():
    """Two Controllers over one node is the app plus a driver, in miniature."""
    node = Lockable()
    first, second = node.controller(), node.controller()
    try:
        with first.claim():
            start = time.monotonic()
            refused = False
            try:
                with second.claim(timeout=0.05):
                    pass
            except device.DeviceBusy:
                refused = True
            waited = time.monotonic() - start
            check("a second claim is refused while the first is held", refused)
            check("and it waited rather than failing instantly", waited >= 0.05,
                  f"{waited:.3f}s")

        taken = False
        with second.claim(timeout=0.5):
            taken = True
        check("releasing hands it over", taken)
    finally:
        first.close()
        second.close()
        node.close()


def test_a_claim_is_re_entrant():
    """`send` claims for itself, so a claimed sequence must be able to send.

    The failure this guards against is silent: an inner claim releasing on the
    way out would leave the rest of a config write unprotected while the code
    reads as if it were covered.
    """
    node = Lockable()
    first, second = node.controller(), node.controller()
    try:
        with first.claim():
            with first.claim():
                pass
            still_held = False
            try:
                with second.claim(timeout=0.02):
                    pass
            except device.DeviceBusy:
                still_held = True
            check("an inner claim does not release the outer one", still_held)
        check("the outer release does let go", first._depth == 0)
    finally:
        first.close()
        second.close()
        node.close()


def test_a_stale_reply_is_not_mistaken_for_an_answer():
    """The bug this exists for, reproduced.

    A Get info ACK belonging to the desktop app's poll was read by a process
    that had sent a rumble command -- replies go to every reader of the node.
    `ack_ok` matches on the command byte alone, so a stale reply for the
    command we are about to send would be accepted as its answer.
    """
    node = FakeNode()
    ctrl = node.controller()
    try:
        node.reply(ack(device.CMD_GET_INFO))     # someone else's, already waiting
        replies = ctrl.command(device.CMD_GET_INFO, wait=0.05)
        check("the reply that arrived before the question is gone",
              replies == [], str(replies))

        # ... and one that really is ours, landing inside the read window.
        node.reply(ack(device.CMD_GET_INFO), after=0.02)
        replies = ctrl.command(device.CMD_GET_INFO, wait=0.2)
        check("a reply that arrives after the question is kept",
              len(replies) == 1, str(replies))
        check("and it reads as an ACK",
              ctrl.ack_ok(replies[0], device.CMD_GET_INFO))
    finally:
        ctrl.close()
        node.close()


def test_sending_writes_the_packet_and_frees_the_node():
    node = FakeNode()
    ctrl = node.controller()
    try:
        ctrl.command(device.CMD_GET_INFO, wait=0.0)
        written = node.written()
        check("the packet reached the node", len(written) == device.PACKET_LEN,
              str(len(written)))
        check("framed as the pad expects",
              written[0] == device.REPORT_ID_OUT and written[3] == device.CMD_GET_INFO,
              written[:5].hex(" "))

        # Asserted on the depth rather than by racing a second handle: both of
        # this fixture's handles are dups of one description, so a second claim
        # would be granted whether or not the first was ever released, and the
        # test would pass while proving nothing. Exclusion between separate
        # openers is covered above.
        check("the claim is released when the send returns", ctrl._depth == 0)
    finally:
        ctrl.close()
        node.close()


def test_a_waiting_claim_is_granted_when_the_holder_finishes():
    """Waiting has to actually work, not just time out politely."""
    node = Lockable()
    first, second = node.controller(), node.controller()
    granted = []
    try:
        with first.claim():
            def wait_for_it():
                try:
                    with second.claim(timeout=2.0):
                        granted.append(time.monotonic())
                except device.DeviceBusy:
                    granted.append(None)

            waiter = threading.Thread(target=wait_for_it)
            waiter.start()
            time.sleep(0.05)
            check("it is still waiting while we hold it", granted == [],
                  str(granted))
        waiter.join(timeout=3.0)
        check("and gets it once we let go", granted and granted[0] is not None,
              str(granted))
    finally:
        first.close()
        second.close()
        node.close()


def test_threads_sharing_one_controller_are_serialised():
    """flock is per open file description, so one shared handle needs more.

    Two threads on one Controller would both "succeed" at flock -- same
    description -- and interleave. The in-process lock is what stops that, and
    dropping it would leave a test suite that passes and an app that races.
    """
    node = Lockable()
    ctrl = node.controller()
    overlaps = []
    inside = []
    try:
        def hold():
            with ctrl.claim():
                inside.append(1)
                if len(inside) > 1:
                    overlaps.append(1)
                time.sleep(0.05)
                inside.pop()

        threads = [threading.Thread(target=hold) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)
        check("no two threads were inside the claim at once", overlaps == [],
              str(overlaps))
    finally:
        ctrl.close()
        node.close()


# --- Vader 5 receiver: legacy frames over unnumbered reports ---------------

def _vader_frame(cmd_id):
    buf = device.build(cmd_id)
    buf[4] = 2
    buf[5] = device.checksum(buf, 3, 5)
    return buf


def _vader_reply(cmd_id):
    """An unnumbered 32-byte reply, the shape the Vader 5 receiver sends."""
    body = bytearray(32)
    body[:6] = bytes([device.MAGIC1, device.MAGIC2, cmd_id, 1, 0, 0x82])
    body[31] = 0x27  # its command-1 trailer is not the additive checksum
    return bytes(body)


def _vader(node):
    ctrl = node.controller()
    ctrl.unnumbered = True
    return ctrl


def test_vader_frames_are_readdressed_both_ways():
    node = FakeNode()
    ctrl = _vader(node)
    try:
        node.reply(_vader_reply(1), after=0.05)
        replies = ctrl.send(_vader_frame(1), wait=0.3,
                            until=lambda seen: seen[-1][3] == 1)
        out = node.written()
        check("the 0x03 report id becomes the 0x00 placeholder",
              len(out) == 32 and out[0] == 0 and out[1:] == bytes(_vader_frame(1))[1:],
              out.hex())
        check("an unnumbered reply gains 0x04 so legacy parsers keep their offsets",
              replies and replies[0] == bytes([device.REPORT_ID_IN]) + _vader_reply(1),
              repr(replies))
        refused = False
        try:
            ctrl.send(bytes(33), wait=0)
        except ValueError:
            refused = True
        check("anything but a legacy 32-byte frame is refused", refused)
    finally:
        ctrl.close()
        node.close()


def test_a_quick_repeat_is_separated():
    node = FakeNode()
    ctrl = _vader(node)
    try:
        ctrl.send(_vader_frame(1), wait=0)
        node.written()
        node.reply(_vader_reply(0x10), after=0.03)
        ctrl.send(_vader_frame(1), wait=0)
        out = node.written()
        check("a repeat inside the window is preceded by a command-16 read",
              len(out) == 64 and out[3] == 0x10 and out[5] == 0x12
              and out[32:] == b"\x00" + bytes(_vader_frame(1))[1:], out.hex())
        node.reply(_vader_reply(1), after=0.03)
        ctrl.send(_vader_frame(0x10), wait=0)
        ctrl.send(_vader_frame(0x10), wait=0)
        out = node.written()
        check("a repeated command 16 is separated by command 1",
              len(out) == 96 and out[35] == 0x01 and out[67] == 0x10, out.hex())
        ctrl._last_sent -= device.REPEAT_WINDOW
        ctrl.send(_vader_frame(0x10), wait=0)
        check("outside the window nothing extra is sent", len(node.written()) == 32)
    finally:
        ctrl.close()
        node.close()


def test_numbered_pads_are_untouched():
    node = FakeNode()
    ctrl = node.controller()
    try:
        ctrl.send(_vader_frame(1), wait=0)
        ctrl.send(_vader_frame(1), wait=0)
        out = node.written()
        check("an Apex 5 still gets report 0x03 and no separator",
              len(out) == 64 and out[0] == 3 and out[32] == 3, out.hex())
    finally:
        ctrl.close()
        node.close()


def test_vader5_receiver_detection():
    saved = device.hid_name, device.hid_ids
    try:
        device.hid_ids = lambda node: (device.VID, 0x2401)
        device.hid_name = lambda node: "Flydigi Vader 5 Pro"
        check("a 2401 node named Vader is the Vader 5 receiver",
              device.is_vader5_receiver("hidraw0"))
        device.hid_name = lambda node: "Flydigi APEX6"
        check("another 2401 node stays on the GPA6 path",
              not device.is_vader5_receiver("hidraw0"))
        device.hid_ids = lambda node: (device.VID, 0x2502)
        device.hid_name = lambda node: "Flydigi Vader 5 Pro"
        check("only PID 2401 qualifies", not device.is_vader5_receiver("hidraw0"))
    finally:
        device.hid_name, device.hid_ids = saved


def main():
    for test in (test_a_claim_excludes_another_process,
                 test_vader_frames_are_readdressed_both_ways,
                 test_a_quick_repeat_is_separated,
                 test_numbered_pads_are_untouched,
                 test_vader5_receiver_detection,
                 test_a_claim_is_re_entrant,
                 test_a_stale_reply_is_not_mistaken_for_an_answer,
                 test_sending_writes_the_packet_and_frees_the_node,
                 test_a_waiting_claim_is_granted_when_the_holder_finishes,
                 test_threads_sharing_one_controller_are_serialised):
        test()
    for name in FAILED:
        print(f"  FAILED: {name}")
    print(f"\n{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
