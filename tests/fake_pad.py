# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A stand-in for a connected Apex 5, so tests need no hardware.

Implements just enough of the vendor protocol to exercise the mapping code:
the multi-packet config read, the diffing write, apply and save. It stores a
config blob per slot and answers exactly as the pad does, including the
checksummed framing -- which means a packet the real pad would reject is
rejected here too.

The screen commands are here on the same terms as everything else, and the reply
ids are the pad's own rather than the SDK's: 208 and 209 answer as 0x18 and 0x19
on real hardware, which nothing in the decompiled source predicts. See
`flydigi/screen.py`.
"""
import contextlib

from flydigi import device, lighting, mapping, screen

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
    # the hardware, see docs/findings-profile-blob.md. It matters: each is the identity line on
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

        # -- screen ---------------------------------------------------------
        self.screen_frames = []        # every frame this pad has been sent
        self.screen_period = None
        self.screen_uploads = 0        # how many finished uploads landed
        self.always_on = False
        self.status_bar_always_on = False
        self.screen_test = None        # (on, colour) from the last command 242
        self._frame = None             # (index, count, bytearray) mid-transfer

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

    def send(self, buf, wait=0.3, until=None):
        # `until` lets a caller stop collecting early. Everything here answers in
        # one go, so it changes nothing -- but a fake missing a keyword the real
        # transport takes has hidden a whole untested path here before.
        buf = bytes(buf)
        # This pad speaks the 5A A5 envelope and nothing else. Worth checking
        # rather than assuming, because the picture commands come from an older
        # protocol and `screen.build` can emit either of two shorter envelopes
        # -- a fake that decoded those anyway would report a dialect as working
        # when what it really did was read a payload byte as a command id.
        if len(buf) < 6 or buf[1] != device.MAGIC1 or buf[2] != device.MAGIC2:
            return []
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
            screen.CMD_UPLOAD_START: self._upload_start,
            screen.CMD_UPLOAD_DATA: self._upload_data,
            screen.CMD_UPLOAD_END: self._upload_end,
            screen.CMD_UPLOAD_FINISH: self._upload_finish,
            screen.CMD_TEST_SCREEN: self._test_screen,
            screen.CMD_SETTING: self._setting,
            screen.CMD_HARDWARE_STATUS: self._hardware_status,
        }.get(cmd)
        return handler(payload) if handler else []

    @staticmethod
    def _ack(cmd, echo=b""):
        """A success reply. `echo` fills from data[5] once the report id is off.

        Most commands put the success flag there. The ones that take a
        sub-command id echo that instead and move success along one, which is
        what their own `IsAck` matches on -- so those pass their own bytes.
        """
        body = bytearray(32)
        body[0] = 0x04
        body[1], body[2] = device.MAGIC1, device.MAGIC2
        body[3] = cmd
        body[4] = 1
        if echo:
            body[6 : 6 + len(echo)] = echo
        else:
            body[6] = 1
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

    # -- screen ------------------------------------------------------------

    @staticmethod
    def _screen_ack(cmd):
        """A picture-command reply, answering under the id the pad really uses.

        Hardware: 210 and 211 come back under their own command byte, but 208
        and 209 answer as 0x18 and 0x19. Modelled rather than tidied, because a
        fake that acked 208 with 208 would let a client that matched on the
        wrong id pass here and hang on the pad.
        """
        return FakePad._ack(screen.ACK_ID[cmd])

    def _upload_start(self, payload):
        _pic_id, _pic_type, count, index, period, high, low = payload[:7]
        size = (high << 8) | low
        if not 1 <= index <= count or size != screen.FRAME_LEN:
            return []
        self.screen_period = period
        if index == 1:
            # A new upload replaces what is on the screen rather than adding to
            # it, so the collected frames start again here and not at the finish.
            self.screen_frames = []
        self._frame = (index, count, bytearray(size))
        return [self._screen_ack(screen.CMD_UPLOAD_START)]

    def _upload_data(self, payload):
        if self._frame is None:
            return []
        offset = (payload[0] << 8) | payload[1]
        _index, _count, buf = self._frame
        if offset >= len(buf):
            return []
        chunk = payload[2:]
        # The last chunk of a frame is padded, not short, so it runs past the
        # end. The pad knows the size it announced; trim rather than grow.
        buf[offset : offset + len(chunk)] = chunk[: len(buf) - offset]
        self.packets_received += 1
        return [self._screen_ack(screen.CMD_UPLOAD_DATA)]

    def _upload_end(self, payload):
        if self._frame is None:
            return []
        _pic_id, index, high, low, _pad = payload[:5]
        frame_index, _count, buf = self._frame
        if index != frame_index or (high << 8) | low != len(buf):
            return []
        self.screen_frames.append(bytes(buf))
        self._frame = None
        return [self._screen_ack(screen.CMD_UPLOAD_END)]

    def _upload_finish(self, payload):
        _pic_id, count, high, low, _pad = payload[:5]
        if count != len(self.screen_frames) or (high << 8) | low != screen.FRAME_LEN:
            return []
        self.screen_uploads += 1
        return [self._screen_ack(screen.CMD_UPLOAD_FINISH)]

    def _test_screen(self, payload):
        self.screen_test = (payload[0] == 1, tuple(payload[1:4]))
        return [self._ack(screen.CMD_TEST_SCREEN)]

    def _setting(self, payload):
        sub_id, value = payload[0], payload[1]
        if sub_id == screen.SUB_OFF_SCREEN:
            self.always_on = value == 1
        elif sub_id == screen.SUB_STATUS_BAR:
            self.status_bar_always_on = value == 1
        else:
            return []
        # The pad echoes the *value*, not the sub-id -- so nothing in a reply
        # says which setting it belonged to. Modelled, because the SDK's own
        # IsAck matches on the sub-id and would never fire against real
        # hardware; a fake that echoed the sub-id would hide that.
        return [self._ack(screen.CMD_SETTING, echo=bytes([value & 1]))]

    def _hardware_status(self, _payload):
        """Command 3, with the screen bits filled from this pad's state.

        The rest of the reply carries what a real Apex 5 answered -- supported
        bits 251, sleep time 15, report rate 0, precision 2, sensitivity 17.
        """
        supported = 0x7B | 0x80              # 251: everything but motion debounce
        enabled = 0x7B | (0x80 if self.status_bar_always_on else 0)
        body = bytearray(32)
        body[0] = 0x04
        body[1], body[2] = device.MAGIC1, device.MAGIC2
        body[3] = screen.CMD_HARDWARE_STATUS
        body[4] = 1
        body[6] = supported
        body[7] = enabled
        body[8] = 0x01                       # off-screen supported, audio not
        body[9] = 0x01 if self.always_on else 0x00
        body[10], body[11], body[12], body[13] = 15, 0, 2, 17
        return [bytes(body)]


class FakeScreenChip:
    """Quacks like `screen_ota.OtaLink` -- the UART bootloader, not the pad.

    Separate from FakePad on purpose: this is a different chip on a different
    transport, reachable only after the pad has left the HID bus. Modelling it
    as its own thing is what lets a test cover the upload without a pad that has
    been switched into upgrade mode.

    Erase is enforced. A real flash cell cannot be written back to 1, so a write
    into a block nobody erased is refused here rather than quietly accepted --
    which is the ordering bug worth catching, and the one an obliging fake would
    hide.
    """

    BASE = 0x00120000
    VERSION = 0x01000203

    def __init__(self, base=None):
        from flydigi import screen_ota
        self._ota = screen_ota
        self.base = self.BASE if base is None else base
        self.flash = {}            # address -> byte
        self.erased = set()        # block addresses
        self.config = None         # (pic_type, pic_num, frame_rate, restore)
        self.reset = None          # (length, crc) from PicResetDevice
        self.writes = 0
        self.refused = 0
        self._out = None

    # -- the link contract -------------------------------------------------

    def write(self, data):
        opcode, payload = data[0], data[3:]
        self._out = {
            self._ota.OP_PIC_GET_BASE: self._get_base,
            self._ota.OP_PIC_GET_VERSION: self._get_version,
            self._ota.OP_ERASE: self._erase,
            self._ota.OP_WRITE: self._write,
            self._ota.OP_PIC_RESET: self._reset,
        }.get(opcode, lambda _p: None)(payload)

    def read_reply(self, timeout=None):
        out, self._out = self._out, None
        return out

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    # -- what it stores ----------------------------------------------------

    def contents(self):
        """The picture as written, from the base address up to the last byte."""
        if not self.flash:
            return b""
        end = max(self.flash) + 1
        return bytes(self.flash.get(a, 0xFF) for a in range(self.base, end))

    @staticmethod
    def _reply(opcode, payload=b""):
        import struct
        return bytes([0, opcode]) + struct.pack("<H", len(payload) + 4) + payload

    def _get_base(self, payload):
        import struct
        self.config = tuple(payload[:4])
        return self._reply(self._ota.OP_PIC_GET_BASE, struct.pack("<I", self.base))

    def _get_version(self, _payload):
        import struct
        return self._reply(self._ota.OP_PIC_GET_VERSION, struct.pack("<I", self.VERSION))

    def _erase(self, payload):
        import struct
        address = struct.unpack("<I", payload[:4])[0]
        self.erased.add(address)
        for offset in range(self._ota.ERASE_BLOCK):
            self.flash.pop(address + offset, None)
        return self._reply(self._ota.OP_ERASE, payload[:4])

    def _write(self, payload):
        import struct
        address = struct.unpack("<I", payload[:4])[0]
        chunk = payload[6:]
        block = address - ((address - self.base) % self._ota.ERASE_BLOCK)
        if block not in self.erased:
            self.refused += 1
            return None            # unerased flash: the pad simply does not answer
        for index, byte in enumerate(chunk):
            self.flash[address + index] = byte
        self.writes += 1
        return self._reply(self._ota.OP_WRITE, payload[:6])

    def _reset(self, payload):
        import struct
        self.reset = (struct.unpack("<I", payload[:4])[0],
                      struct.unpack("<I", payload[4:8])[0])
        return bytes([0, self._ota.OP_PIC_RESET])      # the short end-of-session reply
