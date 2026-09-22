# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Experimental Apex 6 protocol, independent of the Apex 5 profile dialect.

Wire facts come from the pinned web client and xHaptics Engine 5.0.1.4 in
docs/findings-apex6.md. Hardware timing and actuator behavior remain untested.
Only identity, motor-map reads and volatile grip output are implemented.
"""
from dataclasses import dataclass
import math
import struct

PRODUCT_IDS = (0x2502, 0x2401)
SKUS = {149: "Apex 6", 150: "Apex 6 Pro", 152: "Apex 6 Pro Phantom Blade Zero"}
FEATURES = ("imu", "led", "switch", "screen", "trigger_haptic",
            "trigger_vibrate", "audio", "haptic_grip")


class ProtocolError(ValueError):
    pass


class ReplyTimeout(ProtocolError):
    """No reply arrived; a wireless pad may be absent behind an open receiver."""


@dataclass(frozen=True)
class ReportLayout:
    output_id: int
    output_size: int
    input_ids: tuple = (0,)

    def wrap(self, frame):
        if len(frame) != 32 or not 32 <= self.output_size <= 64:
            raise ProtocolError("GPA6 needs a 32–64 byte output report")
        # hidraw requires the zero placeholder even for unnumbered outputs.
        return bytes([self.output_id]) + frame.ljust(self.output_size, b"\0")

    def unwrap(self, report):
        if self.input_ids == (0,):
            return bytes(report)  # unnumbered Linux reads have no placeholder
        if not report or report[0] not in self.input_ids:
            return b""
        return bytes(report[1:])


def report_layout(descriptor):
    """Read HID global items and vendor collections; never assume report ID 3.

    Reject ambiguous/undersized outputs instead of guessing a Linux framing.
    Report sizes include all fields of a report, including constant padding.
    """
    state = {"page": 0, "id": 0, "size": 0, "count": 0}
    stack, collections = [], []
    sizes, vendor = {}, set()
    pos = 0
    while pos < len(descriptor):
        prefix = descriptor[pos]
        pos += 1
        if prefix == 0xfe:
            raise ProtocolError("unsupported HID long item")
        size = (0, 1, 2, 4)[prefix & 3]
        if pos + size > len(descriptor):
            raise ProtocolError("truncated HID descriptor")
        value = int.from_bytes(descriptor[pos:pos + size], "little")
        pos += size
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            field = {0: "page", 7: "size", 8: "id", 9: "count"}.get(tag)
            if field:
                state[field] = value
            elif tag == 10:
                stack.append(state.copy())
            elif tag == 11:
                if not stack:
                    raise ProtocolError("unbalanced HID globals")
                state = stack.pop()
        elif kind == 0:
            if tag == 10:
                collections.append(state["page"] == 0xffa0 or any(collections))
            elif tag == 12:
                if not collections:
                    raise ProtocolError("unbalanced HID collection")
                collections.pop()
            elif tag in (8, 9):
                key = (tag, state["id"])
                sizes[key] = sizes.get(key, 0) + state["size"] * state["count"]
                if any(collections):
                    vendor.add(key)
    outputs = [(rid, sizes[(tag, rid)]) for tag, rid in vendor if tag == 9]
    inputs = tuple(sorted(rid for tag, rid in vendor if tag == 8))
    if collections or stack or len(outputs) != 1 or not inputs:
        raise ProtocolError("ambiguous or incomplete vendor HID reports")
    rid, bits = outputs[0]
    if bits % 8 or not 256 <= bits <= 512 or not 0 <= rid <= 255:
        raise ProtocolError("unsupported GPA6 output report size/id")
    if any(not 0 <= report_id <= 255 or sizes[(8, report_id)] % 8
           or not 256 <= sizes[(8, report_id)] <= 512 for report_id in inputs):
        raise ProtocolError("unsupported GPA6 input report size/id")
    if 0 in inputs and inputs != (0,):
        raise ProtocolError("mixed numbered/unnumbered HID input reports")
    return ReportLayout(rid, bits // 8, inputs)


def frame(command, payload=b""):
    if len(payload) > 27:
        raise ValueError("GPA6 payload exceeds 27 bytes")
    buf = bytearray(32)
    buf[:4] = bytes((0x5a, 0xa5, command, len(payload) + 2))
    buf[4:4 + len(payload)] = payload
    buf[31] = sum(buf[2:31]) & 255
    return bytes(buf)


def _payload(body, command, size, v21=False):
    if len(body) < 4 or body[:3] != bytes((0x5a, 0xa5, command)):
        return None
    start = 3 if v21 else 5
    error = not v21 and body[3] == 0
    count = 1 if error else size
    end = start + count
    # Both compact web replies and padded engine replies occur in the source.
    # Unlike the web parser, a corrupt checksum is never accepted as an ACK.
    for check in dict.fromkeys((end, 31, len(body) - 1)):
        if check < end or check >= len(body):
            continue
        if any(body[end:check]) or any(body[check + 1:]):
            continue
        if (sum(body[2:check]) & 255) != body[check]:
            continue
        if error:
            raise ProtocolError(f"GPA6 command 0x{command:02x}: error {body[start]}")
        if not v21 and body[3:5] != b"\x01\x00":
            raise ProtocolError("unexpected GPA6 packet count/index")
        return body[start:end]
    raise ProtocolError(f"malformed GPA6 reply to 0x{command:02x}")


class Protocol:
    def __init__(self, ctrl):
        if getattr(ctrl, "dialect", None) != "gpa6":
            raise ProtocolError("not a GPA6 command interface")
        self.ctrl = ctrl
        self.layout = ctrl.report_layout

    def write(self, command, payload=b""):
        self.ctrl.send(self.layout.wrap(frame(command, payload)), wait=0)

    def ask(self, command, size, payload=b"", v21=False):
        def parse(reply):
            return _payload(self.layout.unwrap(reply), command, size, v21)
        replies = self.ctrl.send(self.layout.wrap(frame(command, payload)),
                                 wait=0.6, until=lambda seen: parse(seen[-1]) is not None)
        for reply in replies:
            found = parse(reply)
            if found is not None:
                return found
        raise ReplyTimeout(f"no GPA6 reply to 0x{command:02x}")

    def info(self):
        p = self.ask(1, 25)
        return {"device_type": p[0], "connection_mode": p[1],
                "connect_type": {1: "wired", 2: "dongle"}.get(p[1], "unknown"),
                "mac": ":".join(f"{b:02X}" for b in reversed(p[2:6])) if any(p[2:6]) else None,
                "battery_level": p[6] & 15, "charging": bool(p[6] >> 4),
                "firmware": tuple(struct.unpack(">7H", p[10:24])),
                "features_raw": p[24],
                **{key: bool(p[24] & (1 << bit)) for bit, key in enumerate(FEATURES)}}

    def uid(self):
        return self.ask(4, 16).hex()

    def versions(self):
        return struct.unpack("<3H", self.ask(7, 6))

    def slot(self):
        slot = self.ask(0xa1, 11)[0]
        if slot > 3:
            raise ProtocolError(f"invalid active slot {slot}")
        return slot

    def motor_map(self):
        slot = self.slot()
        data = bytearray()
        for index in range(4):
            p = self.ask(0xa3, 24, bytes((0, 6, index, 16)), v21=True)
            if p[:8] != bytes((0, 0, 6, slot, 64, 0, index, 16)):
                raise ProtocolError("motor map ACK status, slot, length or chunk mismatch")
            data.extend(p[8:24])
        if self.slot() != slot:
            raise ProtocolError("profile changed during motor map read")
        grip_restore(data)  # reject layouts we cannot restore before any write
        return slot, bytes(data)

    def mode(self, target, mode, params=b""):
        status = self.ask(0x53, 1, bytes((1, target, mode)) + params)[0]
        if status != 0:
            raise ProtocolError(f"motor configuration refused: status {status}")


def grip_restore(mapping):
    """RAM 6 layout 2: grips at 23/25, mode 0 or mode 1 + one gain byte.

    Native decoder RVAs 22cd00 / 22c780; restoration uses area 1 and targets
    0x10/0x11. We only change grips, so trigger entries are left alone.
    """
    if len(mapping) != 64 or mapping[0] != 2:
        raise ProtocolError("unsupported motor mapping layout")
    result = []
    for target, offset in ((0x10, 23), (0x11, 25)):
        mode = mapping[offset]
        if mode not in (0, 1):
            raise ProtocolError("unsupported persistent grip mode")
        result.append((target, mode, bytes(mapping[offset + 1:offset + 2]) if mode else b""))
    return result


def quantize(sample):
    if not math.isfinite(sample):
        raise ValueError("waveform samples must be finite")
    def f32(x):
        return struct.unpack("<f", struct.pack("<f", x))[0]
    x = f32(max(-1.0, min(1.0, sample)))
    return max(0, min(255, math.floor(f32(f32(x + 1.0) * 127.5) + 0.5)))


def waveform(trigger=(), left=(), right=(), *, enabled=(False, True, True), selector=0):
    """Eight 1 kHz sample triples, exactly matching the native streaming builder.

    Selector values are preserved for fixture comparisons, but the grip session
    never enables the trigger column: its physical routing is not validated.
    """
    if selector not in range(4) or len(enabled) != 3:
        raise ValueError("invalid waveform selector/channel enables")
    payload = bytearray([0x80 | selector | sum(b for b, on in zip((4, 8, 16), enabled) if on)])
    columns = [list(c)[-8:] for c in (trigger, left, right)]
    for i in range(8):
        for column, on in zip(columns, enabled):
            payload.append(quantize(column[i]) if on and i < len(column) else 128)
    return frame(0x57, payload)
