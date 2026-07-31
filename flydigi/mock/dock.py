# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A stand-in for a connected CD2 charging dock, so tests need no hardware.

**This used to live in `tests/`**, and moved when the mock bus was built: there
is one dock on this desk, and "more than one dock" is a case the app now has to
render. `tests/fake_dock.py` is still the name the tests import it by, and
nothing constructs one unless `FLYDIGI_MOCK_BUS` is set.


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

from .. import charger

UID = bytes.fromhex("1960f0f1f2cdab52efe7bc0658")
FIRMWARE = (0x00, 0x39)          # -> "0.0.3.9"


def parse_firmware(value):
    """Accept `(0x00, 0x39)` or `"0.0.3.9"`, and answer the two bytes.

    Four nibbles in two bytes, the dock's own packing. An unparseable string
    becomes all-zero, which is the dock's way of saying "not reported" rather
    than a version of zero -- so a spec with a typo in it produces a device
    that admits to knowing nothing, not one that lies.
    """
    if isinstance(value, str):
        parts = value.split(".")
        if len(parts) != 4 or not all(p.isdigit() and int(p) < 16
                                      for p in parts):
            return (0x00, 0x00)
        a, b, c, d = (int(p) for p in parts)
        return ((a << 4) | b, (c << 4) | d)
    return tuple(value)


class FakeDock:
    """As much of `charger.Dock` as the module actually calls."""

    def __init__(self, device_type=0, uid=UID, nickname=None,
                 path="/dev/hidraw-fake", firmware=FIRMWARE, docked=False,
                 battery=0):
        self.path = path
        self.device_type = device_type
        self.uid = uid
        self.nickname = nickname
        # Two bytes of packed nibbles, or the dotted version they spell. A
        # constructor argument rather than a module constant because two mock
        # docks may differ, and because a test that wants the all-zero
        # "not reported" case should say so on the dock it is testing.
        self.firmware = parse_firmware(firmware)
        # What the unsolicited 239 report says. A real dock sends one about
        # once a second whether or not anything asked, so this fake answers a
        # heartbeat with two frames -- the heartbeat's own reply and a status
        # report behind it. Without that, `charger.read_status` waits out its
        # whole 1.5-second window and reports "no status report in the last
        # second", which is a real dock's *broken* state showing up as a mock
        # dock's ordinary one.
        self.docked = docked
        self.battery = battery
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
            charger.CMD_WRITE_NICKNAME: self._write_nickname,
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
        body[12], body[13] = self.firmware  # -> data[16], data[17]
        body[14] = int(self.settings[charger.CMD_SLEEP_WHEN_CHARGING])
        body[15] = int(self.settings[charger.CMD_LED_SYNC])
        body[16] = int(self.settings[charger.CMD_CLOSE_WITH_SYSTEM])
        body[17] = int(self.settings[charger.CMD_SHOW_ANIMATION])
        return self._reply(charger.CMD_HEARTBEAT, body) + self._status()

    def _status(self):
        """The 239 report, as `ChargerRepository`'s raw-data handler reads it.

        `body[0]` lands at `data[4]`, so the parser's data[7] and data[8] are
        body[3] and body[4].
        """
        body = bytearray(6)
        body[3] = int(bool(self.docked))
        body[4] = self.battery & 0xFF
        return self._reply(charger.REPORT_STATUS, body)

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

    def _write_nickname(self, payload):
        """Command 24. Stored, then answered by the read the writer makes.

        Reached only with a correctly-placed checksum: `send` above drops a bad
        one in silence, and Flydigi's own builder puts it inside the name for
        anything longer than a byte. Same bug in both SDKs, same consequence
        here -- see `charger.write_nickname`.
        """
        name = bytes(payload).split(b"\x00", 1)[0]
        self.nickname = name.decode("utf-8", "replace").strip() or None
        return self._reply(charger.CMD_WRITE_NICKNAME, b"\x00")

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


def dock_type_for(model, default=0):
    """A `ChargerDeviceType` from a spec's `"type": 1` or `"type": "eva"`.

    The five are editions of one dock rather than models, so a name is matched
    loosely against the product string Space Station shows -- "eva" finds the
    EVA .ver. A number is taken as given, including one no CD2 has: a dock the
    guard refuses is a device worth being able to put on the bus, since
    refusing it is the behaviour under test.
    """
    if model is None:
        return default
    text = str(model).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    for number, name in charger.DOCK_TYPES.items():
        if text.lower() in name.lower():
            return number
    known = ", ".join(f"{n} ({name})" for n, name in charger.DOCK_TYPES.items())
    raise ValueError(f"{model!r} is not a CD2 edition -- {known}")


def build_dock(path, index, entry):
    """One mock dock, from one entry of the bus spec.

    The uid is derived from the device's place in the spec unless one is given,
    for the reason a mock pad's is: a selector written into a config file has to
    resolve to the same dock on the next run.
    """
    uid = entry.get("uid")
    if uid:
        raw = bytes.fromhex(str(uid).replace(":", ""))
    else:
        raw = bytes([index & 0xFF]) + UID[1:]
    return FakeDock(device_type=dock_type_for(entry.get("type")),
                    uid=raw, nickname=entry.get("nickname"), path=path,
                    firmware=entry.get("firmware", FIRMWARE),
                    docked=bool(entry.get("docked", False)),
                    battery=int(entry.get("battery", 0)))
