# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Stateful GPA6 emulator. These are explicit firmware assumptions, not a pad.

The wire decoder is independent of the production encoder. Area 1 is assumed
volatile, RAM 6 persistent, and profile selection resets live motor modes.
Frames require setup, and the trigger column is intentionally unsupported.
No claim is made about actuator response, receiver timing or selector routing.
"""
from collections import deque
import contextlib
import struct
import threading

from ..device import DeviceBusy
from ..gpa6 import ReportLayout


class FakeApex6:
    dialect = "gpa6"
    fd = None

    def __init__(self, device_type=150, *, features=0xf7, uid=None,
                 layout=None, versions=(22, 0x109, 1), path="mock:apex6"):
        self.path = path
        self.device_type, self.features = device_type, features
        self.uid = uid or "000102030405060708090a0b0c0d0e0f"
        self.report_layout = layout or ReportLayout(0, 32)
        self.versions = versions
        self.present = True
        self.slot = 0
        self.maps = []
        for _ in range(4):
            mapping = bytearray(64)
            mapping[0] = 2
            mapping[23:27] = bytes((1, 75, 1, 60))
            self.maps.append(mapping)
        self.runtime = {0x10: (1, b"\x4b"), 0x11: (1, b"\x3c")}
        self.history = deque(maxlen=256)
        self.frames = deque(maxlen=256)
        self.frame_count = 0
        self.faults = {}  # command -> deque of None/error/drop/malformed/disconnect
        self._lock = threading.RLock()
        self._bus = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        pass

    @contextlib.contextmanager
    def claim(self, timeout=5):
        if not self._lock.acquire(timeout=timeout):
            raise DeviceBusy("emulated Apex 6 is in use")
        try:
            yield self
        finally:
            self._lock.release()

    def change_profile(self, slot):
        self.slot = slot
        mapping = self.maps[slot]
        self.runtime = {target: (mapping[offset], bytes(mapping[offset + 1:offset + 2])
                                if mapping[offset] else b"")
                        for target, offset in ((16, 23), (17, 25))}

    def _reply(self, command, payload, v21=False):
        report = bytearray(32)
        report[:3] = bytes((0x5a, 0xa5, command))
        start = 3 if v21 else 5
        if not v21:
            report[3:5] = b"\x01\x00"
        report[start:start + len(payload)] = payload
        report[31] = sum(report[2:31]) & 255
        if self.report_layout.input_ids != (0,):
            report = bytes([self.report_layout.input_ids[0]]) + report
        return bytes(report)

    def send(self, report, wait=0.3, until=None):
        with self.claim():
            if self._bus:
                from . import entry_for
                entry = entry_for(self.path)
                if entry is None or not entry.get("present", True):
                    raise OSError("emulated Apex 6 disconnected")
            if not self.present:
                raise OSError("emulated Apex 6 disconnected")
            layout = self.report_layout
            if len(report) != layout.output_size + 1 or report[0] != layout.output_id:
                raise ValueError("legacy or incorrect HID report rejected by Apex 6")
            body = bytes(report[1:33])
            if body[:2] != b"\x5a\xa5" or any(report[33:]):
                raise ValueError("invalid private frame/padding")
            if sum(body[2:31]) & 255 != body[31] or not 2 <= body[3] <= 29:
                raise ValueError("invalid private frame checksum/length")
            command, payload = body[2], body[4:body[3] + 2]
            if any(body[body[3] + 2:31]):
                raise ValueError("invalid private frame padding")
            self.history.append((command, payload))
            faults = self.faults.get(command)
            fault = faults.popleft() if faults else None
            if fault == "disconnect":
                self.present = False
                raise OSError("emulated disconnect during write")
            if fault == "error":
                reply = self._reply(command, b"\x01")
            elif command == 1:
                info = bytearray(25)
                info[0:2] = bytes((self.device_type, 1))
                info[6] = 5
                info[10:24] = struct.pack(">7H", 0x7010, 0, 0, 0x1010, 0, 0, 0)
                info[24] = self.features
                reply = self._reply(command, info)
            elif command == 4:
                reply = self._reply(command, bytes.fromhex(self.uid))
            elif command == 7:
                reply = self._reply(command, struct.pack("<3H", *self.versions))
            elif command == 0xa1:
                reply = self._reply(command, bytes([self.slot]) + bytes(10))
            elif command == 0xa3:
                if len(payload) != 4 or payload[:2] != b"\x00\x06" or payload[3] != 16:
                    raise ValueError("only RAM 6 chunk reads are emulated")
                index = payload[2]
                if index > 3:
                    raise ValueError("invalid motor map chunk")
                reply = self._reply(command, bytes((0, 0, 6, self.slot, 64, 0, index, 16))
                                    + self.maps[self.slot][index * 16:(index + 1) * 16], v21=True)
            elif command == 0x53:
                valid = (len(payload) >= 3 and payload[0] == 1
                         and payload[1] in (16, 17, 18))
                if valid:
                    target, mode, params = payload[1], payload[2], payload[3:]
                    valid = ((mode == 0 and not params)
                             or (mode == 1 and len(params) == 1)
                             or (mode == 2 and params == b"\x40\x00" and self.features & 128))
                if valid:
                    for side in ((16, 17) if target == 18 else (target,)):
                        self.runtime[side] = mode, params
                reply = self._reply(command, bytes([0 if valid else 1]))
            elif command == 0x57:
                if len(payload) != 25 or payload[0] & 0xe4 != 0x80:
                    raise ValueError("invalid control byte or unsupported trigger output")
                for bit, side in ((8, 16), (16, 17)):
                    if payload[0] & bit and self.runtime[side][0] != 2:
                        # Neutral cleanup is accepted after partial setup too.
                        column = 2 if side == 16 else 3
                        if any(x != 128 for x in payload[column::3]):
                            raise ValueError("waveform received before grip setup")
                if any(x != 128 for x in payload[1::3]):
                    raise ValueError("trigger samples must be neutral")
                self.frames.append(body)
                self.frame_count += 1
                return []
            else:
                raise ValueError(f"legacy/unsupported command 0x{command:02x} refused")
            if fault == "drop":
                return []  # mutation happened, ACK lost
            if fault == "malformed":
                reply = reply[:-1] + bytes([reply[-1] ^ 0xff])
            replies = [reply]
            if until:
                until(replies)
            return replies


def build_pad(path, index, entry):
    pad = FakeApex6(device_type=int(entry.get("device_type", 149)),
                   features=int(entry.get("features", 0xf7)),
                   uid=entry.get("uid", f"{index + 1:032x}"), path=path)
    pad._bus = True
    return pad
