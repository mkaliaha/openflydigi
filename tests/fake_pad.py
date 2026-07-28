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
import contextlib

from flydigi import device, lighting, mapping

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
    # The curve blocks carry what a real Apex 5 ships with, not 0xFF -- read off
    # the hardware, see PROGRESS.md. It matters: each is the identity line on
    # its own scale (sticks run to 127, triggers to 255), so an accessor tested
    # against these is tested against a shape the pad would really hand it,
    # rather than against a fill byte that happens to parse.
    for side in range(2):
        core = mapping.OFF_JOYSTICK_CURVE + side * mapping.CURVE_ENTRY
        blob[core : core + 7] = bytes([0, 0, 63, 63, 127, 127, 127])
        travel = mapping.OFF_TRIGGER_CURVE + side * mapping.CURVE_ENTRY
        blob[travel : travel + 7] = bytes([0, 0, 0, 0, 255, 255, 255])
        extra = mapping.OFF_JOYSTICK_EXTRA + side * mapping.JOYSTICK_EXTRA_ENTRY
        blob[extra : extra + 12] = bytes(
            [0, 50, 62, 75, 87, 100, 112, 125, 137, 150, 0, 0])
    config = mapping.MappingConfig(blob)
    config.title = title
    return bytearray(config.blob)


class FakePad:
    """Quacks like flydigi.device.Controller."""

    def __init__(self, slots=4):
        self.blobs = {i: blank_blob(f"Profile {i + 1}") for i in range(slots)}
        # Lighting is a separate config with the same transfer shape.
        self.led_blob = bytearray(b"\xff" * 380)
        self.led_blob[lighting.OFF_VERSION] = 0x00
        self.led_blob[lighting.OFF_VERSION + 1] = 0x03
        self.led_blob[lighting.OFF_BRIGHTNESS] = 20
        self.led_blob[lighting.OFF_LED_COUNT] = 12
        self.led_blob[lighting.OFF_MODE] = 7
        self.led_blob[lighting.OFF_CLICK_FEEDBACK] = 0
        self.led_blob[lighting.OFF_LOOP_START] = 0
        self.led_blob[lighting.OFF_LOOP_END] = 9
        self.led_blob[lighting.OFF_LOOP_TIME] = 4
        for i in range(lighting.OFF_FRAMES, len(self.led_blob)):
            self.led_blob[i] = 0
        self.active = 0
        self.saved = {}
        self.packets_received = 0
        self.bad_checksums = 0
        self.claims = 0
        self.reads_answered = 0
        # Set to make a read switch the pad and then go silent, which is what a
        # dropped packet looks like from the host: the config is live, the
        # caller gets an exception, and nothing says the two happened together.
        self.fail_reads = False
        self._pending_write = None     # (cfg_id, start_index, count)

    # -- transport ---------------------------------------------------------

    @contextlib.contextmanager
    def claim(self, timeout=None):
        """Part of the Controller contract, and a no-op here.

        There is nobody to exclude: one test process, no file behind it. It
        exists because `blobs.read_blob` and `blobs.write_blob` claim the pad
        around their packet streams, and a fake missing a method the real code
        calls has hidden a whole untested path here before.
        """
        self.claims += 1
        yield self

    def send(self, buf, wait=0.3):
        buf = bytes(buf)
        cmd = buf[3]
        length = buf[4]
        if buf[3 + length] != device.checksum(buf, 3, 3 + length):
            self.bad_checksums += 1
            return []                  # the real pad simply does not answer
        payload = buf[5 : 3 + length]
        handler = {
            lighting.CMD_READ: self._read_led,
            lighting.CMD_WRITE_START: self._write_start_led,
            lighting.CMD_WRITE_PACK: self._write_pack_led,
            mapping.CMD_READ: self._read,
            mapping.CMD_STATUS: self._status,
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
        # Reading pages the config in as the live one -- confirmed on hardware,
        # audibly, by the trigger motors re-seating. Modelled here because code
        # that tries to read without disturbing the pad is only exercised by a
        # fake that actually gets disturbed.
        self.active = cfg_id
        self.reads_answered += 1
        if self.fail_reads:
            return []                  # switched anyway, then went quiet
        return self._stream(mapping.CMD_READ, blob, cfg_id, pkg_size)

    def _status(self, _payload):
        body = bytearray(32)
        body[0] = 0x04
        body[1], body[2] = device.MAGIC1, device.MAGIC2
        body[3] = mapping.CMD_STATUS
        body[4] = 1
        body[6] = self.active
        for slot in self.blobs:
            body[7 + 2 * slot] = slot + 1
        return [bytes(body)]

    def _stream(self, cmd, blob, cfg_id, pkg_size):
        total = len(blob) // pkg_size
        replies = []
        for index in range(total):
            body = bytearray(32)
            body[0] = 0x04
            body[1], body[2] = device.MAGIC1, device.MAGIC2
            body[3] = cmd                   # data[2] once the report id is stripped
            body[4] = total
            body[5] = index
            body[6] = cfg_id
            chunk = blob[index * pkg_size : (index + 1) * pkg_size]
            body[7 : 7 + len(chunk)] = chunk
            replies.append(bytes(body))
        return replies

    def _read_led(self, payload):
        return self._stream(lighting.CMD_READ, self.led_blob, payload[0], payload[1])

    def _write_start_led(self, payload):
        self._pending_led = (payload[1], payload[2])
        return [self._ack(lighting.CMD_WRITE_START)]

    def _write_pack_led(self, payload):
        start, _count = getattr(self, "_pending_led", (0, 0))
        index = start + payload[0]
        chunk = payload[1:]
        self.led_blob[index * mapping.PKG_SIZE : index * mapping.PKG_SIZE + len(chunk)] = chunk
        self.packets_received += 1
        return [self._ack(lighting.CMD_WRITE_PACK)]

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
