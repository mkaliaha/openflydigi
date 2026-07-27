# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A stand-in for a connected Apex 5, so tests need no hardware.

Implements just enough of the vendor protocol to exercise the mapping code:
the multi-packet config read, the diffing write, apply and save. It stores a
config blob per slot and answers exactly as the pad does, including the
checksummed framing -- which means a packet the real pad would reject is
rejected here too.
"""
from flydigi import device, mapping

PROTO_V31 = 0x0301
PACKAGE_COUNT = 84
BLOB_LEN = PACKAGE_COUNT * 10          # 840 bytes, matching a real v3.1 config


def blank_blob(title="Profile"):
    """A config with every key at its default, laid out like the real thing."""
    blob = bytearray(b"\xff" * BLOB_LEN)
    blob[mapping.OFF_PROTO_VERSION] = PROTO_V31 & 0xFF
    blob[mapping.OFF_PROTO_VERSION + 1] = PROTO_V31 >> 8
    blob[mapping.OFF_PACKAGE_COUNT] = PACKAGE_COUNT
    for slot in range(mapping.KEY_SLOTS):
        offset = mapping.OFF_KEY_TABLE + slot * mapping.KEY_ENTRY
        blob[offset : offset + mapping.KEY_ENTRY] = bytes([mapping.TARGET_IDENTITY, 0, 0])
    config = mapping.MappingConfig(blob)
    config.title = title
    return bytearray(config.blob)


class FakePad:
    """Quacks like flydigi.device.Controller."""

    def __init__(self, slots=4):
        self.blobs = {i: blank_blob(f"Profile {i + 1}") for i in range(slots)}
        self.active = 0
        self.saved = {}
        self.packets_received = 0
        self.bad_checksums = 0
        self._pending_write = None     # (cfg_id, start_index, count)

    # -- transport ---------------------------------------------------------

    def send(self, buf, wait=0.3):
        buf = bytes(buf)
        cmd = buf[3]
        length = buf[4]
        if buf[3 + length] != device.checksum(buf, 3, 3 + length):
            self.bad_checksums += 1
            return []                  # the real pad simply does not answer
        payload = buf[5 : 3 + length]
        handler = {
            mapping.CMD_READ: self._read,
            mapping.CMD_APPLY: self._apply,
            mapping.CMD_SAVE: self._save,
            mapping.CMD_WRITE_START: self._write_start,
            mapping.CMD_WRITE_PACK: self._write_pack,
        }.get(cmd)
        return handler(payload) if handler else []

    @staticmethod
    def _ack(cmd, extra=b""):
        # Replies carry a report-id byte that the transport strips.
        body = bytearray(32)
        body[0] = 0x04
        body[1], body[2] = device.MAGIC1, device.MAGIC2
        body[3] = cmd
        body[4] = 1
        body[6] = 1
        body[3 : 3 + len(extra)] = extra if extra else body[3 : 3 + len(extra)]
        return bytes(body)

    # -- commands ----------------------------------------------------------

    def _read(self, payload):
        cfg_id, pkg_size = payload[0], payload[1]
        blob = self.blobs.get(cfg_id)
        if blob is None:
            return []
        total = len(blob) // pkg_size
        replies = []
        for index in range(total):
            body = bytearray(32)
            body[0] = 0x04
            body[1], body[2] = device.MAGIC1, device.MAGIC2
            body[3] = mapping.CMD_READ      # data[2] once the report id is stripped
            body[4] = total
            body[5] = index
            body[6] = cfg_id
            chunk = blob[index * pkg_size : (index + 1) * pkg_size]
            body[7 : 7 + len(chunk)] = chunk
            replies.append(bytes(body))
        return replies

    def _apply(self, payload):
        self.active = payload[0]
        return [self._ack(mapping.CMD_APPLY)]

    def _save(self, _payload):
        self.saved = {k: bytes(v) for k, v in self.blobs.items()}
        return [self._ack(mapping.CMD_SAVE)]

    def _write_start(self, payload):
        cfg_id, start, count, _size = payload[0], payload[1], payload[2], payload[3]
        self._pending_write = (cfg_id, start, count)
        return [self._ack(mapping.CMD_WRITE_START)]

    def _write_pack(self, payload):
        if self._pending_write is None:
            return []
        cfg_id, start, _count = self._pending_write
        offset, chunk = payload[0], payload[1:]
        index = start + offset
        blob = self.blobs[cfg_id]
        blob[index * mapping.PKG_SIZE : index * mapping.PKG_SIZE + len(chunk)] = chunk
        self.packets_received += 1
        return [self._ack(mapping.CMD_WRITE_PACK)]
