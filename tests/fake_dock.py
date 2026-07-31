# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A stand-in for a connected CD2 charging dock, so tests need no hardware.

Answers the reads and the writes `flydigi/charger.py` makes, in the dock's own
framing rather than the pad's -- report id 0x00, magic at [1]/[2] on the way
out and at [0]/[1] on the way back, and a reply checksum at `[2 + length]`
where a request's is at `[3 + length]`. Getting that asymmetry wrong is the
mistake this fake exists to catch, so it builds every reply the same way the
dock does rather than echoing what it was sent.

A packet with a bad checksum draws no reply at all, which is what the real dock
does and what makes a framing bug look like silence rather than like an error.

**It reassembles LED writes.** The 97/98 pair is a start packet and a stream of
50-byte packs, and the only way to tell a correct implementation from one that
merely acks is to put the packs back together and parse the result -- so
`led_blob` holds what was actually received and `frame_count` is read out of
it, not out of what the caller intended.

The default values are the ones a real dock reported here: firmware 0.0.3.9,
charger type 0, all three of sleep-when-charging, lighting sync and
close-with-system on, power display off.
"""
import contextlib

from flydigi import charger

UID = bytes.fromhex("1960f0f1f2cdab52efe7bc0658")
FIRMWARE = (0x00, 0x39)          # -> "0.0.3.9"


class FakeDock:
    """As much of `charger.Dock` as the module actually calls."""

    def __init__(self, device_type=0, uid=UID, nickname=None,
                 path="/dev/hidraw-fake"):
        self.path = path
        self.device_type = device_type
        self.uid = uid
        self.nickname = nickname
        self.claims = 0
        self.bad_checksums = 0
        self.unanswered = []
        self.settings = {
            charger.CMD_SLEEP_WHEN_CHARGING: True,
            charger.CMD_LED_SYNC: True,
            charger.CMD_CLOSE_WITH_SYSTEM: True,
            charger.CMD_SHOW_ANIMATION: False,
        }
        # The header a dock ships with, and the frame memory behind it. The
        # blob is kept separately from the header because that is how the
        # hardware behaves: a config write does not clear the frames, which is
        # how a short write shows up as the previous animation in fragments.
        self.led = charger.LedConfig(mode=charger.MODE_PULSE, brightness=50,
                                     period=2, colours=[(0, 116, 255)])
        self.led_blob = b""
        self.packs = []
        self.advertised_packs = None

    # -- transport ---------------------------------------------------------

    @contextlib.contextmanager
    def claim(self, timeout=None):
        self.claims += 1
        yield self

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, buf, wait=0.3, until=None):
        buf = bytes(buf)
        if len(buf) < 6 or buf[1] != 0x5A or buf[2] != 0xA5:
            return []
        cmd, length = buf[3], buf[4]
        if buf[3 + length] != (sum(buf[3:3 + length]) & 0xFF):
            self.bad_checksums += 1
            return []
        payload = buf[5:3 + length]
        handler = {
            charger.CMD_HEARTBEAT: self._heartbeat,
            charger.CMD_READ_NICKNAME: self._read_nickname,
            charger.CMD_READ_UID: self._read_uid,
            charger.CMD_READ_LED: self._read_led,
            charger.CMD_WRITE_LED_START: self._write_start,
            charger.CMD_WRITE_LED_PACK: self._write_pack,
        }.get(cmd)
        if handler is None and cmd in self.settings:
            return self._switch(cmd, payload)
        if handler is None:
            self.unanswered.append(cmd)
            return []
        return handler(payload)

    def command(self, cmd_id, payload=b"", wait=0.5,
                size=charger.PACKET_LEN):
        return self.send(charger.build(cmd_id, payload, size), wait=wait)

    @staticmethod
    def _reply(cmd, body=b""):
        """`5a a5 cmd len body... crc`, with the checksum where the dock puts it."""
        out = bytearray((0x5A, 0xA5, cmd, 2 + len(body)))
        out += bytes(body)
        out.append(sum(out[2:2 + out[3]]) & 0xFF)
        return [bytes(out)]

    # -- reads -------------------------------------------------------------

    def _heartbeat(self, _payload):
        # `body[0]` lands at `data[4]`, so every offset here is the parser's
        # minus four. 18 bytes gives a length byte of 0x14, which is what the
        # real dock sent.
        body = bytearray(18)
        body[2] = self.device_type          # -> data[6]
        body[11] = 0x01                     # -> data[15], chip type
        body[12], body[13] = FIRMWARE       # -> data[16], data[17]
        body[14] = int(self.settings[charger.CMD_SLEEP_WHEN_CHARGING])
        body[15] = int(self.settings[charger.CMD_LED_SYNC])
        body[16] = int(self.settings[charger.CMD_CLOSE_WITH_SYSTEM])
        body[17] = int(self.settings[charger.CMD_SHOW_ANIMATION])
        return self._reply(charger.CMD_HEARTBEAT, body)

    def _read_uid(self, _payload):
        return self._reply(charger.CMD_READ_UID, b"\x00\x00" + self.uid)

    def _read_nickname(self, _payload):
        """The unset reply is the one a real dock gave: `5a a5 02 04 01 00 07`,
        length 4, which is exactly the `data[3] > 4` test failing.

        The *set* reply is a guess, and cannot be anything better. Flydigi
        slice `data[6 : 6 + data[3] - 3]`, and with a name starting at data[6]
        and a length byte of `2 + len(body)` that slice is one byte too long by
        construction -- it runs onto the checksum. No dock here has a nickname
        to measure, so the fake emits the obvious shape and the test asserts
        only that the name comes back, not what trails it.
        """
        if not self.nickname:
            return self._reply(charger.CMD_READ_NICKNAME, b"\x01\x00")
        return self._reply(charger.CMD_READ_NICKNAME,
                           b"\x01\x00" + self.nickname.encode())

    def _read_led(self, _payload):
        cfg = self.led
        body = bytearray((cfg.mode, cfg.brightness, cfg.period, cfg.direction,
                          cfg.colour_count))
        for colour in cfg.colours:
            body += bytes(colour)
        return self._reply(charger.CMD_READ_LED, body)

    # -- writes ------------------------------------------------------------

    def _switch(self, cmd, payload):
        self.settings[cmd] = bool(payload[0])
        return self._reply(cmd, b"\x00")

    def _write_start(self, payload):
        if payload[0] != 10:
            return []           # the literal Flydigi put at array[5]
        self.advertised_packs = (payload[3] << 8) | payload[4]
        self.packs = []
        return self._reply(charger.CMD_WRITE_LED_START, b"\x00")

    def _write_pack(self, payload):
        index = (payload[2] << 8) | payload[3]
        size = payload[4]
        self.packs.append((index, bytes(payload[5:5 + size])))
        if len(self.packs) == self.advertised_packs:
            self._commit()
        return self._reply(charger.CMD_WRITE_LED_PACK, b"\x00")

    def _commit(self):
        blob = b"".join(pack for _, pack in sorted(self.packs))
        self.led_blob = blob
        self.led = charger.LedConfig(
            mode=blob[3], brightness=blob[2], period=blob[1],
            direction=blob[4],
            colours=[tuple(blob[6 + i * 3:9 + i * 3]) for i in range(blob[5])],
            use_colour_count=blob[5])

    # -- what a test wants to know ----------------------------------------

    @property
    def frame_count(self):
        """Frames as the *received bytes* declare, not as the caller meant."""
        return self.led_blob[0] if self.led_blob else 0

    @property
    def frames(self):
        """The uploaded frames, parsed back out of the reassembled blob."""
        if not self.led_blob:
            return []
        start = 6 + self.led_blob[5] * 3
        out = []
        for _ in range(self.frame_count):
            frame = [tuple(self.led_blob[start + i * 3:start + i * 3 + 3])
                     for i in range(charger.LED_COUNT)]
            out.append(frame)
            start += charger.LED_COUNT * 3
        return out
