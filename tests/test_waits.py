#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Every read stops when its answer arrives, rather than waiting out a timeout.

`Controller.send` collects replies for `wait` seconds and takes an `until`
predicate to stop early. A read without one is not slow in a way any other test
can see: the reply is identical, the assertions all pass, and the only
difference is that the call sat there for its whole timeout. That is how five
reads on the app's load path came to cost 5.1 s between them -- `status 1005,
transport 604, settings 504, lighting 1504, profile 1504` on hardware, every one
of those a timeout expiring rather than a pad being slow.

The waiting is not idle either. The vendor node streams input reports at about
970 Hz, so a 0.6 s wait appends some six hundred packets nobody asked for and
tests each one in a Python loop for the one that mattered -- on the worker
thread, holding the GIL, while the window tries to draw.

`FakePad` cannot show any of this, because it answers and then goes quiet, which
makes every read look free. `Chatty` is the same pad with the node's noise put
back: it hands the collected packets to `until` one at a time exactly as the
real transport does, and counts how many packets of noise the caller read before
it stopped. The assertion throughout is that the count is zero.

`test_the_harness_notices_a_read_with_no_predicate` is the control. Without it
this file would pass just as happily against a predicate that never fires.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import blobs, charger, device, effects, identity
from flydigi import lighting, mapping, motion, settings
from tests.fake_pad import FakePad

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


# One input report, reused by identity so the harness can tell the noise it
# injected from anything the pad actually said. Report id 4 with 0xEF at [3] is
# what `motion.parse_report` matches, which is to say it is what the node
# actually streams -- and it passes the `len(r) > 7` filter every caller here
# applies, so a read without a predicate collects it and then discards it.
NOISE = bytes([motion.INPUT_REPORT_ID, device.MAGIC1, device.MAGIC2,
               motion.INPUT_REPORT_MARKER] + [0] * 60)


class Chatty(FakePad):
    """The fake pad, plus the chatter a real vendor node never stops making."""

    # Enough to be unmistakable, and far short of the six hundred a 0.6 s wait
    # really collects; this file is about whether the count is zero.
    NOISE_PACKETS = 50

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.noise_read = 0
        self.sends = 0

    def send(self, buf, wait=0.3, until=None):
        answer = list(super().send(buf, wait=wait))
        self.sends += 1
        collected = []
        for packet in answer + [NOISE] * self.NOISE_PACKETS:
            collected.append(packet)
            if until is not None and until(collected):
                break
        self.noise_read += sum(1 for p in collected if p is NOISE)
        return collected

    def command(self, cmd_id, payload=b"", wait=0.3, until=None):
        return self.send(device.build(cmd_id, payload), wait=wait, until=until)


def quiet(name, call, *args, **kwargs):
    """Run one exchange and assert it read none of the node's chatter."""
    pad = Chatty()
    result = call(pad, *args, **kwargs)
    check(f"{name} stops at the answer",
          pad.noise_read == 0, f"read {pad.noise_read} packets of noise")
    return pad, result


# -- the control -----------------------------------------------------------


def test_the_harness_notices_a_read_with_no_predicate():
    """Proof that the assertions above can fail.

    Every other case here asserts an absence, and an absence is exactly what a
    broken harness reports too. So drive one exchange the old way -- no `until`
    at all -- and require that this does see the noise.
    """
    pad = Chatty()
    pad.send(blobs.build(mapping.CMD_STATUS, b""), wait=0.3)
    check("a send with no predicate reads the whole stream",
          pad.noise_read == Chatty.NOISE_PACKETS, pad.noise_read)


# -- the app's load path ---------------------------------------------------
#
# `App._read_the_rest` asks for these five in a row on every Reload and on every
# reconnect, and this is the sequence that was measured at 5.1 s.


def test_the_profile_status_read_stops_at_the_answer():
    _, status = quiet("mapping.read_status", mapping.read_status)
    check("and still reads the status", status is not None and "active" in status)


def test_the_transport_read_stops_at_the_answer():
    _, state = quiet("motion.read_transport", motion.read_transport)
    check("and still reads the transport", state is not None)


def test_the_settings_block_read_stops_at_the_answer():
    _, state = quiet("settings.read_status", settings.read_status)
    check("and still reads the block", "sleep_minutes" in state)


def test_the_lighting_read_stops_at_the_last_packet():
    _, config = quiet("lighting.read_config", lighting.read_config)
    check("and still reads the whole config", len(config.blob) > 0)


def test_the_profile_read_stops_at_the_last_packet():
    """The one that shows a blob read is not a special case.

    A packetised read cannot stop on a command byte -- forty-two packets carry
    the same one -- so its predicate counts indices against the `total` the pad
    states in each packet. That it stops at all is the thing worth asserting.
    """
    _, config = quiet("mapping.read_config", mapping.read_config, 0)
    check("and still reads a whole config", len(config.blob) > 0)


# -- everything else the app sends -----------------------------------------


def test_the_info_and_version_reads_stop_at_the_answer():
    _, info = quiet("motion.read_info", motion.read_info)
    check("and still reads the battery", info is not None and "battery_level" in info)
    _, versions = quiet("motion.read_versions", motion.read_versions)
    check("and still reads the firmware", versions is not None and "main" in versions)


def test_the_identity_reads_stop_at_the_answer():
    """The three the bus poll runs, every ten seconds, for every pad attached."""
    _, uid = quiet("identity.read_uid", identity.read_uid)
    check("and still reads a uid", bool(uid))

    # Written first, because an unnamed pad answers None -- a real answer, and
    # one that would let a broken read pass as "nothing to see".
    pad = Chatty()
    identity.write_nickname(pad, "bench")
    before = pad.noise_read
    name = identity.read_nickname(pad)
    check("identity.read_nickname stops at the answer", pad.noise_read == before,
          pad.noise_read - before)
    check("and still reads the name back", name == "bench", name)


def test_the_transport_write_stops_at_its_ack():
    pad, ok = quiet("motion.set_raw_data", motion.set_raw_data,
                    controller_data=1, raw=1)
    check("and the pad acknowledged", ok)


def test_a_settings_write_stops_at_its_echo():
    """Command 19's predicate is the echoed value, not the command byte.

    Its caller accepts a reply only when both match, so a predicate matching the
    byte alone could stop on a reply the caller then rejects -- turning a slow
    success into a reported failure. Matching what the caller accepts leaves the
    answer alone and shortens only the waiting.
    """
    sub_id = settings.SUB_IDS["always_on"]
    _, ok = quiet("settings.set_feature", settings.set_feature, sub_id, True)
    check("and the write was acknowledged", ok)
    _, ok = quiet("settings.set_sleep_minutes", settings.set_sleep_minutes, 5)
    check("and so was the standalone command", ok)


def test_applying_and_saving_a_profile_stop_at_their_acks():
    _, ok = quiet("mapping.apply_config", mapping.apply_config, 1)
    check("and the switch was acknowledged", ok)
    _, ok = quiet("mapping.save_config", mapping.save_config)
    check("and the save was acknowledged", ok)


def test_a_profile_write_stops_at_every_packet_ack():
    """The one where the cost multiplies.

    `write_blob` acks each packet one for one, so a write without a predicate
    pays the whole timeout per packet rather than once for the exchange.
    """
    pad = Chatty()
    config = mapping.read_config(pad, 0)
    before = pad.noise_read
    config.blob[mapping.OFF_KEY_TABLE] ^= 0xFF
    sent = mapping.write_config(pad, 0, config)
    check("a config write reads no noise at all", pad.noise_read == before,
          pad.noise_read - before)
    check("and still sends the changed packets", sent > 0, sent)


def test_engaging_the_stored_effects_stops_at_each_ack():
    """Two commands after every profile read and every profile write.

    `wait=0` skips the deliberate half-second `engage_stored` sleeps before it
    speaks; the delay is the reference's and not what this is measuring.
    """
    pad = Chatty()
    config = mapping.read_config(pad, 0)
    before = pad.noise_read
    results = effects.engage_stored(pad, config, wait=0)
    check("engaging the stored effects reads no noise",
          pad.noise_read == before, pad.noise_read - before)
    check("and still speaks for both triggers", len(results) == 2, results)


# -- the dock --------------------------------------------------------------


class ChattyDock(charger.Dock):
    """The dock's fake, with the same treatment.

    A dock is quieter than a pad -- it volunteers a status frame about once a
    second rather than streaming input reports -- but `read_status` waits for
    that unsolicited frame and not for the heartbeat's own reply, so it is the
    one dock read that cannot use `Dock.command`'s predicate.
    """

    def __init__(self):
        from flydigi.mock.dock import FakeDock
        self._fake = FakeDock()
        self.noise_read = 0

    def send(self, buf, wait=0.3, until=None):
        answer = list(self._fake.send(buf, wait=wait))
        collected = []
        for packet in answer + [NOISE] * Chatty.NOISE_PACKETS:
            collected.append(packet)
            if until is not None and until(collected):
                break
        self.noise_read += sum(1 for p in collected if p is NOISE)
        return collected

    def command(self, cmd_id, payload=b"", wait=0.5, size=charger.PACKET_LEN,
                until=None):
        return self.send(charger.build(cmd_id, payload, size), wait=wait,
                         until=until)


def test_the_dock_status_read_stops_at_the_status_frame():
    dock = ChattyDock()
    status = charger.read_status(dock)
    check("charger.read_status stops at the answer", dock.noise_read == 0,
          dock.noise_read)
    check("and still reads the dock", status is not None and "docked" in status)


def main():
    for test in (test_the_harness_notices_a_read_with_no_predicate,
                 test_the_profile_status_read_stops_at_the_answer,
                 test_the_transport_read_stops_at_the_answer,
                 test_the_settings_block_read_stops_at_the_answer,
                 test_the_lighting_read_stops_at_the_last_packet,
                 test_the_profile_read_stops_at_the_last_packet,
                 test_the_info_and_version_reads_stop_at_the_answer,
                 test_the_identity_reads_stop_at_the_answer,
                 test_the_transport_write_stops_at_its_ack,
                 test_a_settings_write_stops_at_its_echo,
                 test_applying_and_saving_a_profile_stop_at_their_acks,
                 test_a_profile_write_stops_at_every_packet_ack,
                 test_engaging_the_stored_effects_stops_at_each_ack,
                 test_the_dock_status_read_stops_at_the_status_frame):
        test()
    total = len(PASSED) + len(FAILED)
    print(f"\n{len(PASSED)}/{total} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
