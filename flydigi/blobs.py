# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Packetised config transfer, shared by every stored config on the pad.

Mapping profiles and lighting configs are moved the same way, with different
command ids:

    read        [cfgId, pkgSize]                    -> N packets
    write start [cfgId, startIdx, nPkts, pkgSize]
    write pack  [pktNum, data...]                   x N

Both are checksummed, and the pad answers a bad checksum by staying silent
rather than by reporting an error.
"""
from . import device

# NewXInput moves 20 bytes per packet; older protocols use 10.
PKG_SIZE = 20


class ProtocolError(Exception):
    pass


def build(cmd_id, payload=b""):
    """Build a checksummed vendor packet.

    The length byte counts the command and length bytes themselves, so it is
    payload length + 2 and the checksum lands at 3 + length. Flydigi's
    `CreateSimpleCommand` + `Crc(3, 3 + len)` in one step.
    """
    buf = device.build(cmd_id)
    length = len(payload) + 2
    buf[4] = length
    buf[5 : 5 + len(payload)] = payload
    buf[3 + length] = device.checksum(buf, 3, 3 + length)
    return buf


def replies(ctrl, buf, wait, until=None):
    """Send and return reply bodies with the report-id byte stripped.

    `until` is passed straight through to `Controller.send`, and every caller
    here should pass one. Without it `send` sits out the whole `wait` however
    early the answer lands -- and the node is not quiet while it waits, it
    streams input reports at about 970 Hz, so the cost is not just the delay but
    several hundred packets appended and tested in Python for one that mattered.
    """
    return [r[1:] for r in ctrl.send(buf, wait=wait, until=until) if len(r) > 7]


def answers(cmd_id):
    """`send`'s `until` for a command the pad answers in one packet.

    The command byte is at `reply[3]` with the report id still on, which is the
    `body[2]` every caller here filters on -- so this stops the collection at
    exactly the packet the caller was going to use, and the result is the same
    reply that waiting out the timeout would have produced. Command replies
    share report id 0x04 with input reports and are told apart by that byte
    alone; 0xEF marks an input report and no command id collides with it.
    """
    def check(replies):
        reply = replies[-1]
        return len(reply) > 7 and reply[3] == cmd_id
    return check


def _whole_blob(cmd_id):
    """`send`'s `until` for a packetised read: stop once the run is complete.

    How many packets to expect is not known in advance -- the pad states it in
    each packet's `total` field -- so this counts indices as they arrive rather
    than waiting for a number fixed up front. Stateful, so a caller that retries
    must build a fresh one per attempt.
    """
    seen = set()
    total = None

    def check(replies):
        nonlocal total
        reply = replies[-1]
        if len(reply) > 7 and reply[3] == cmd_id:
            total, index = reply[4], reply[5]
            seen.add(index)
        return total is not None and len(seen) >= total
    return check


def acked(ctrl, cmd_id, payload, wait):
    """Send one packet and stop as soon as its own answer arrives.

    The early exit is what a write costs or saves. `write_blob` acks every
    packet one for one, so without it a config write pays the full `wait` per
    packet -- a mapping profile is 42 of them.
    """
    return any(body[2] == cmd_id
               for body in replies(ctrl, build(cmd_id, payload), wait,
                                   answers(cmd_id)))


def read_blob(ctrl, cmd_id, cfg_id, what, pkg_size=PKG_SIZE, wait=1.5, retries=3):
    """Read one stored config as a flat byte string.

    The pad streams the packets back to back, each carrying (total, index,
    cfgId, data), so collect until the last index arrives rather than issuing a
    request per packet. That is what `_whole_blob` makes true: `wait` is the
    ceiling on a read that goes wrong, not the price of one that goes right.
    """
    last_error = "no reply"
    # Held across the retries as well as the stream: a retry that races another
    # process re-reads into the same half-full `chunks` problem it is retrying.
    with ctrl.claim():
        for _ in range(retries):
            chunks = {}
            total = None
            # Fresh per attempt: the predicate counts the packets it has seen,
            # and a retry starts that count again.
            for body in replies(ctrl, build(cmd_id, bytes([cfg_id, pkg_size])),
                                wait, _whole_blob(cmd_id)):
                if body[2] != cmd_id:
                    continue
                total, index = body[3], body[4]
                chunks[index] = bytes(body[6 : 6 + pkg_size])
            if total and len(chunks) == total:
                blob = bytearray(total * pkg_size)
                for index, chunk in chunks.items():
                    blob[index * pkg_size : (index + 1) * pkg_size] = chunk
                return blob
            if total:
                missing = sorted(set(range(total)) - set(chunks))
                last_error = f"got {len(chunks)}/{total} packets, missing {missing}"
            else:
                last_error = "no reply -- the pad may be asleep, press a button"
    raise ProtocolError(f"reading {what} failed: {last_error}")


def split(blob, pkg_size=PKG_SIZE):
    return [bytes(blob[i : i + pkg_size]) for i in range(0, len(blob), pkg_size)]


def write_blob(ctrl, start_cmd, pack_cmd, cfg_id, blob, old=None,
               pkg_size=PKG_SIZE, wait=0.5):
    """Write a config, sending only the packets that differ from `old`.

    Flydigi transfers contiguous runs of changed packets rather than the whole
    config, which is worth copying: a mapping profile is 42 packets and
    remapping one button touches one of them.

    Returns the number of packets sent.
    """
    new_packets = split(blob, pkg_size)
    old_packets = split(old, pkg_size) if old is not None else None
    if old_packets is not None and len(old_packets) != len(new_packets):
        old_packets = None

    runs = []
    run_start = None
    for i, packet in enumerate(new_packets):
        changed = old_packets is None or packet != old_packets[i]
        if changed and run_start is None:
            run_start = i
        elif not changed and run_start is not None:
            runs.append((run_start, new_packets[run_start:i]))
            run_start = None
    if run_start is not None:
        runs.append((run_start, new_packets[run_start:]))

    sent = 0
    # A header announces how many packets follow, so this is a sequence the pad
    # is tracking, not a series of independent commands. Anything of ours that
    # got in between would be read as one of the packets promised.
    with ctrl.claim():
        for start, packets in runs:
            header = bytes([cfg_id, start, len(packets), pkg_size])
            if not acked(ctrl, start_cmd, header, wait):
                raise ProtocolError(f"pad rejected the write header at packet {start}")
            for offset, packet in enumerate(packets):
                if not acked(ctrl, pack_cmd, bytes([offset]) + packet, wait):
                    raise ProtocolError(f"pad rejected packet {start + offset}")
                sent += 1
    return sent
