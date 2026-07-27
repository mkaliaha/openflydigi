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


def replies(ctrl, buf, wait):
    """Send and return reply bodies with the report-id byte stripped."""
    return [r[1:] for r in ctrl.send(buf, wait=wait) if len(r) > 7]


def acked(ctrl, cmd_id, payload, wait):
    return any(body[2] == cmd_id for body in replies(ctrl, build(cmd_id, payload), wait))


def read_blob(ctrl, cmd_id, cfg_id, what, pkg_size=PKG_SIZE, wait=1.5, retries=3):
    """Read one stored config as a flat byte string.

    The pad streams the packets back to back, each carrying (total, index,
    cfgId, data), so collect until the last index arrives rather than issuing a
    request per packet.
    """
    last_error = "no reply"
    for _ in range(retries):
        chunks = {}
        total = None
        for body in replies(ctrl, build(cmd_id, bytes([cfg_id, pkg_size])), wait):
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
    for start, packets in runs:
        header = bytes([cfg_id, start, len(packets), pkg_size])
        if not acked(ctrl, start_cmd, header, wait):
            raise ProtocolError(f"pad rejected the write header at packet {start}")
        for offset, packet in enumerate(packets):
            if not acked(ctrl, pack_cmd, bytes([offset]) + packet, wait):
                raise ProtocolError(f"pad rejected packet {start + offset}")
            sent += 1
    return sent
