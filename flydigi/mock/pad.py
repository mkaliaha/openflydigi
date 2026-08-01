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

**This used to live in `tests/`**, and moved here when the mock bus was built:
the desktop app and the tools have to be able to run against several devices,
and only one pad exists to run against. `tests/fake_pad.py` is still the name
the tests import it by. Nothing constructs one unless `FLYDIGI_MOCK_BUS` is set
-- see `flydigi/mock/__init__.py`.

**It answers the identity commands too**, which is what makes two of these
tellable apart: command 1 carries a device type, an address and the firmware,
command 4 a uid, and commands 2 and 24 read and write a nickname. All four are
now measured on the pad here, and two of them are modelled the opposite way
round from how the SDK reads: the nickname payload is at raw 6 rather than raw
5, and an unnamed pad answers with `FACTORY_NICKNAME` rather than with zeroes.
Both were transcribed from the reference first, both were wrong, and both were
caught by writing a name and reading it back.
"""
import contextlib

from .. import device, identity, lighting, mapping, motion, screen, settings

PROTO_V31 = 0x0301
PACKAGE_COUNT = 84
BLOB_LEN = PACKAGE_COUNT * 10          # 840 bytes, matching a real v3.1 config


# Commands that carry no checksum. The trigger-effect family is the whole of
# it: `device.build` writes no checksum byte, and the pad acts on them anyway,
# while a mapping or lighting packet with a bad one gets no reply at all.
UNCHECKSUMMED = frozenset((device.CMD_SET_FORCE_TRIGGER,
                           device.CMD_SET_FORCE_TRIGGER_GRIP,
                           device.CMD_RUMBLE))

# Checksummed in the sense that a checksum *fits*, and not checked. Measured:
# command 24 with the checksum slot left at zero is acknowledged and stored,
# where a mapping packet with a bad one draws no reply at all. It is its own set
# because the payload slicing is the checksummed kind -- see `send`.
UNVERIFIED = frozenset((identity.CMD_WRITE_NICKNAME,))


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
    # The motion block and its response curve, likewise off the hardware. The
    # 0xFF fill would have parsed as a gyro mapped to nothing with both enable
    # keys unset, which is tidier than the truth: the pad ships with Lt in the
    # first enable-key byte and 0 -- D-pad Up -- in the second, and with the two
    # sensitivity axes at different values. See docs/findings-profile-blob.md.
    blob[mapping.OFF_MOTION : mapping.OFF_MOTION + 8] = bytes(
        [0, 12, 0, 4, 25, 20, 0, 0])
    blob[mapping.OFF_MOTION_CURVE : mapping.OFF_MOTION_CURVE
         + mapping.MOTION_CURVE_ENTRY] = bytes([0, 63, 63, 127, 127, 127])
    # The macro page and its repeat intervals, as a real pad holds them: a
    # six-byte header of zeroes and 0xFF behind it, and 3 -- 30 ms -- in all
    # five interval slots. Read off the hardware, like the curves above; the
    # 0xFF fill this used to have parses as "no macros" too, but by a different
    # route, so a reader tested against it was not tested against the pad.
    blob[mapping.OFF_MACROS : mapping.OFF_MACROS + mapping.MACRO_HEADER] = (
        bytes(mapping.MACRO_HEADER))
    blob[mapping.OFF_MACRO_CYCLE : mapping.OFF_MACRO_CYCLE + mapping.MACRO_SLOTS] = (
        bytes([3] * mapping.MACRO_SLOTS))
    config = mapping.MappingConfig(blob)
    config.title = title
    return bytearray(config.blob)


# All zero, because that is what the pad on this desk answers with -- see
# `flydigi/motion.py:parse_mac`. A mock pad that invented an address would make
# the mac look like a usable identifier in every test, which is the one thing
# hardware has already said it is not. A spec entry can still set one, so the
# code path that matches on a mac is reachable.
DEFAULT_MAC = "00:00:00:00"
# Thirteen bytes, in the shape the real one came back in: this is that uid with
# its first byte replaced per device, so a mock pad reads like a pad.
DEFAULT_UID = "00206e7a1c00000000dcba3e00"
DEFAULT_FIRMWARE = "7.0.4.5"       # what the pad on this desk reports
# What command 2 answers on a pad nobody has ever named -- read off this one,
# and not the zeroes anybody would have guessed. Flydigi's emptiness test is
# "first byte is neither 0x00 nor 0xFF", so their own UI calls this a name.
FACTORY_NICKNAME = bytes((0x01, 0x01, 0x09, 0x09, 0x09, 0x64, 0x04, 0x5E))


def _version_bytes(text):
    """"7.0.4.5" -> (0x70, 0x45). Two BCD bytes, four nibbles, Flydigi's order.

    A version that is not four numbers becomes all-zero, which is how the pad
    reports a component it does not have -- so an odd string produces "absent"
    rather than nonsense.
    """
    parts = str(text or "").split(".")
    if len(parts) != 4 or not all(p.isdigit() and int(p) < 16 for p in parts):
        return (0, 0)
    a, b, c, d = (int(p) for p in parts)
    return ((a << 4) | b, (c << 4) | d)


class FakePad:
    """Quacks like flydigi.device.Controller."""

    def __init__(self, slots=4, path="/dev/hidraw-fake", device_type=128,
                 mac=DEFAULT_MAC, uid=None, nickname=None, battery=4,
                 charging=False, wired=True, firmware=DEFAULT_FIRMWARE):
        self.path = path
        # -- what this pad *is*, as opposed to what is stored on it ----------
        self.device_type = device_type
        self.mac = mac
        # 13 bytes, the length command 4 answers with.
        self.uid = uid or DEFAULT_UID
        # Bytes, not text: what the pad stores is a field, and a name written
        # by Flydigi's own builder is not decodable. `nickname` stays as the
        # constructor's convenience.
        self.nickname = nickname
        self.nickname_bytes = (nickname.encode("utf-8") if nickname else b"")
        self.battery = battery
        self.charging = charging
        self.wired = wired
        self.firmware = firmware
        self.nickname_writes = []          # every name this pad was sent
        # The other six components, as an Apex 5 reports them: no dongle while
        # wired, and no ADC chip ever -- that one is a Vader 4 part.
        self.versions = {"main": firmware, "dongle": None, "switch": "1.0.0.2",
                         "trigger": "1.0.0.6", "screen": "1.0.2.3",
                         "adc": None, "nearlink": "1.0.0.1"}
        # Command 17's five flags, and who holds the pad. `control_by` fills
        # itself in on real hardware when a third party acquires; here it is
        # whatever a test or a spec put there.
        self.transport = {"controller_data": True, "raw_data": False,
                          "keyboard": True, "mouse": True, "third_party": False}
        self.control_by = ""
        self.blobs = {i: blank_blob(f"Profile {i + 1}") for i in range(slots)}
        # Lighting is a separate config with the same transfer shape.
        self.led_blob = bytearray(b"\xff" * 380)
        self.led_blob[lighting.OFF_VERSION] = 0x00
        self.led_blob[lighting.OFF_VERSION + 1] = 0x03
        self.led_blob[lighting.OFF_BRIGHTNESS] = 20
        self.led_blob[lighting.OFF_LED_COUNT] = 12
        self.led_blob[lighting.OFF_MODE] = 7
        self.led_blob[lighting.OFF_CLICK_FEEDBACK] = 0
        # On, as the pad on the desk reports it -- the vibration light effect
        # ships enabled, which is part of why it went unnoticed for so long.
        self.led_blob[lighting.OFF_GRIP_SYNC] = 1
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

        # -- live trigger effects -------------------------------------------
        # Kept per side, because a stored effect does nothing until one of
        # these arrives: writing the block and applying the config engages
        # nothing on real hardware. A fake that only modelled the blob would
        # let that bug pass, since the bytes would look right the whole time.
        # Never keyed by side 3 -- a command addressed to `Both` is inert.
        self.live_effects = {}         # side -> (mode, [params])
        self.live_binds = {}           # side -> (bind_type, filter, scale, [params])

        # -- screen ---------------------------------------------------------
        self.screen_frames = []        # every frame this pad has been sent
        self.screen_period = None
        self.screen_uploads = 0        # how many finished uploads landed
        self.screen_test = None        # (on, colour) from the last command 242
        self._frame = None             # (index, count, bytearray) mid-transfer

        # -- device settings ------------------------------------------------
        #
        # What a real Apex 5 answered command 3 with, feature by feature:
        # supported 251 (everything but motion debounce), enabled 123 (the
        # status bar off, the rest on), sleep 15 min, report rate 0, precision
        # 2 = 10-bit, sensitivity 17 = Middle.
        self.settings = {
            "quick_switch": True,
            "xbox_home": True,
            "motion_debounce": False,
            "mapping_switch": True,
            "stick_debounce": True,
            "auto_calibration": True,
            "stick_rebound": True,
            "status_bar_always_on": False,
            "always_on": False,
            "audio": False,
        }
        self.sleep_minutes = 15
        self.report_rate = 0
        self.precision = 2
        self.sensitivity = 17
        self.restarts = 0

    # The screen's two settings are two entries of the block above, and the
    # screen tests reach for them by name. Properties rather than a second copy:
    # a fake with two stores would let a decoder read the wrong bit and still
    # pass.
    @property
    def always_on(self):
        return self.settings["always_on"]

    @property
    def status_bar_always_on(self):
        return self.settings["status_bar_always_on"]

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

    # The rest of the handle contract. A test constructs one of these directly
    # and never closes it; the mock bus hands the same object to code written
    # against `device.Controller`, which does close it -- and keeps it
    # afterwards, so closing must not throw the state away. A real pad does not
    # forget its profiles when a program exits either.
    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

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
        # The config family is checksummed and the trigger-effect family is
        # not: `device.build` leaves the slot at zero for 81/82 and the pad
        # takes them anyway. Checking every command here counted those as
        # corrupt and answered nothing, which reads exactly like a pad that
        # refused the effect.
        if cmd not in UNCHECKSUMMED and cmd not in UNVERIFIED:
            if buf[3 + length] != device.checksum(buf, 3, 3 + length):
                self.bad_checksums += 1
                return []              # the real pad simply does not answer
        # Two length conventions in one protocol. `blobs.build` counts the
        # command and length bytes themselves, so the payload ends at
        # 3 + length; `device.build`, which the trigger family uses, stores the
        # payload length plainly. Slicing everything the first way truncated
        # every trigger command by two bytes -- enough that a three-byte
        # "clear the effect" arrived as one byte and was dropped as malformed.
        payload = (buf[5 : 5 + length] if cmd in UNCHECKSUMMED
                   else buf[5 : 3 + length])
        # A third convention, for one command. The pad keeps `buf[4] - 1` bytes
        # from buf[5] -- one more than the name -- so anything written into the
        # slot after the name is stored as part of it. That is what makes a
        # dutifully-appended checksum come back as a trailing character, and
        # modelling it is the whole reason this fake can be trusted about
        # nicknames at all.
        if cmd in UNVERIFIED:
            payload = buf[5 : 4 + length]
        handler = {
            device.CMD_GET_INFO: self._info,
            identity.CMD_READ_NICKNAME: self._read_nickname,
            identity.CMD_READ_UID: self._read_uid,
            identity.CMD_WRITE_NICKNAME: self._write_nickname,
            motion.CMD_READ_TRANSPORT: self._read_transport,
            motion.CMD_ENABLE_RAW: self._set_transport,
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
            settings.CMD_SETTING: self._setting,
            settings.CMD_STATUS: self._hardware_status,
            settings.CMD_REPORT_RATE: self._report_rate,
            settings.CMD_PRECISION: self._precision,
            settings.CMD_SENSITIVITY: self._sensitivity,
            settings.CMD_SLEEP: self._sleep,
            settings.CMD_RESTART: self._restart,
            device.CMD_SET_FORCE_TRIGGER: self._force_trigger,
            device.CMD_SET_FORCE_TRIGGER_GRIP: self._force_trigger_grip,
        }.get(cmd)
        return handler(payload) if handler else []

    def command(self, cmd_id, payload=b"", wait=0.3, until=None):
        """As `Controller.command` -- build the envelope and send it."""
        return self.send(device.build(cmd_id, payload), wait=wait, until=until)

    # Borrowed rather than reimplemented: a fake that decided for itself what
    # counts as an ACK would let a caller pass here and fail on the pad.
    ack_ok = staticmethod(device.Controller.ack_ok)

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

    # -- what this pad is, as opposed to what is stored on it --------------
    #
    # The four commands that let two pads be told apart. Every offset here is
    # the parser's in `flydigi/motion.py` and `flydigi/identity.py`, which are
    # the SDK's plus one for the report-id byte this transport keeps.

    def _single(self, cmd, body=b"", start=6):
        """A single-frame reply carrying `body`.

        `start` is 6 for every payload but the nickname's, which the controller
        SDK reads one byte earlier. That inconsistency is the reference's and is
        reproduced rather than tidied -- see `_read_nickname`.
        """
        out = bytearray(32)
        out[0] = motion.INPUT_REPORT_ID
        out[1], out[2] = device.MAGIC1, device.MAGIC2
        out[3] = cmd
        out[4] = 1                       # one frame...
        out[5] = 0                       # ...and this is it
        out[start : start + len(body)] = body
        return [bytes(out)]

    def _info(self, _payload):
        """Command 1: type, address, battery, connection, seven versions."""
        body = bytearray(26)                                   # raw 6..31
        body[0] = self.device_type
        body[1] = 1 if self.wired else 0
        # Stored least-significant first and reversed on the way out, which is
        # what `Array.Reverse` in their heartbeat parser does.
        mac = [int(part, 16) & 0xFF for part in self.mac.split(":")]
        body[2:6] = bytes(reversed(mac[:4]))
        body[6] = (0x10 if self.charging else 0) | (self.battery & 0x0F)
        body[7] = 0x01                                         # chip type
        body[8] = 0x01                                         # motion chip
        for index, name in enumerate(motion.VERSION_NAMES):
            at = motion.VERSION_OFFSET - 6 + 2 * index
            body[at], body[at + 1] = _version_bytes(self.versions.get(name))
        return self._single(device.CMD_GET_INFO, body)

    def _read_uid(self, _payload):
        return self._single(identity.CMD_READ_UID, bytes.fromhex(self.uid))

    def _read_nickname(self, _payload):
        """Command 2. The name is at raw 6, like every other payload.

        **Measured**, by writing one and reading it back -- it was modelled at
        raw 5 first, on the strength of the SDK's own slice, and the pad
        disagreed. See `identity.read_nickname`.

        An unnamed pad does not answer with zeroes either: the one here shipped
        with `FACTORY_NICKNAME` in the field, which Flydigi's emptiness test
        reads as a name. That is what this fake ships with too, so a reader
        that only handles clean zeroes fails here rather than on hardware.
        """
        return self._single(identity.CMD_READ_NICKNAME,
                            self.nickname_bytes or FACTORY_NICKNAME)

    def _write_nickname(self, payload):
        """Command 24. Stores the bytes given, checksum and all.

        Three measured behaviours, all of them the opposite of what the SDK
        suggests, and each one caught a bug in this project's own reader or
        writer before it was modelled here:

          * The checksum is not checked, so `send` lets this through whatever
            is in that slot -- including nothing.
          * `buf[4] - 1` bytes are kept, one more than the name, so a checksum
            appended after the name is stored *inside* it.
          * Flydigi's own builder puts that checksum at index 6, over the
            name's second character, so their form of "Desk" is stored as
            `44 a5 73 6b`.

        Stored as raw bytes rather than as text: "Desk" plus a checksum is not
        valid UTF-8, and a fake that decoded leniently here would hide exactly
        the corruption this exists to reproduce.
        """
        self.nickname_bytes = bytes(payload).split(b"\x00", 1)[0]
        self.nickname_writes.append(self.nickname_bytes)
        return [self._ack(identity.CMD_WRITE_NICKNAME)]

    def _read_transport(self, _payload):
        body = bytearray(25)                                   # raw 6..30
        for index, name in enumerate(motion.TRANSPORT_FLAGS):
            body[index] = int(self.transport[name])
        holder = self.control_by.encode("ascii", "replace")[:20]
        body[5 : 5 + len(holder)] = holder
        return self._single(motion.CMD_READ_TRANSPORT, body)

    def _set_transport(self, payload):
        """Command 17. 0xFF is "leave alone", which is how one flag moves."""
        for index, name in enumerate(motion.TRANSPORT_FLAGS):
            if index < len(payload) and payload[index] != motion.UNCHANGED:
                self.transport[name] = payload[index] == 1
        # On hardware the holder fills itself in: turning third-party control
        # on and letting SDL acquire puts "SDL" here. Nothing acquires a fake,
        # so this is the flag on with nobody having taken it up -- a state the
        # app has to render as well, and the one it will show against a mock.
        if not self.transport["third_party"]:
            self.control_by = ""
        return [self._ack(motion.CMD_ENABLE_RAW)]

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

    def _force_trigger(self, payload):
        """SetForceTrigger (81): [applyFlag, side, mode, params...].

        The real pad echoes the mode and the parameters back rather than a bare
        success flag, which is how we know it parses the payload and does not
        merely acknowledge the command id. Modelled, because a caller reading
        the echo would otherwise be testing nothing.
        """
        if len(payload) < 3:
            return []
        _apply_flag, side, mode = payload[0], payload[1], payload[2]
        if side == device.SIDE_BOTH:
            # ACKs on hardware and does nothing at all. Recording it would let a
            # writer that addresses `Both` look like it worked.
            return [self._ack(device.CMD_SET_FORCE_TRIGGER)]
        self.live_effects[side] = (mode, list(payload[3:]))
        # `[success=1][mode][params...]`, side dropped -- the shape a real Apex 5
        # sends back. The success flag keeps its usual slot, so this is the echo
        # form that puts data after it rather than the sub-id form that replaces
        # it; getting that backwards makes every effect look refused.
        return [self._ack(device.CMD_SET_FORCE_TRIGGER,
                          bytes([1, mode]) + bytes(payload[3:]))]

    def _force_trigger_grip(self, payload):
        """SyncWithGrip (82): [side, bindType, filter, scale, params...]."""
        if len(payload) < 4:
            return []
        side = payload[0]
        if side == device.SIDE_BOTH:
            return [self._ack(device.CMD_SET_FORCE_TRIGGER_GRIP)]
        self.live_binds[side] = (payload[1], payload[2], payload[3],
                                 list(payload[4:8]))
        return [self._ack(device.CMD_SET_FORCE_TRIGGER_GRIP)]

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

    # -- device settings ---------------------------------------------------

    # What this pad answers as *supported*, exactly as the hardware did: motion
    # debounce and audio come back unsupported on a k5. Kept as the names rather
    # than as the two bytes, so the bit packing below is the only place bits are
    # built and a wrong shift shows up as a wrong feature.
    UNSUPPORTED = ("motion_debounce", "audio")

    def _setting(self, payload):
        """Command 19. Acks whatever it understands; changes only what it can.

        An unsupported sub-setting is acknowledged and does nothing, which is
        this pad's rule everywhere -- "a command answering is not a command
        working". A fake that refused them instead would let a caller believe an
        ACK proves a feature exists.
        """
        sub_id, value = payload[0], payload[1]
        name = next((n for n, s in settings.SUB_IDS.items() if s == sub_id), None)
        if name is None:
            return []
        if name not in self.UNSUPPORTED:
            self.settings[name] = value == 1
        # The pad echoes the *value*, not the sub-id -- so nothing in a reply
        # says which setting it belonged to. Modelled, because the SDK's own
        # IsAck matches on the sub-id and would never fire against real
        # hardware; a fake that echoed the sub-id would hide that.
        return [self._ack(settings.CMD_SETTING, echo=bytes([value & 1]))]

    def _standalone(self, cmd, attribute, payload):
        setattr(self, attribute, payload[0])
        return [self._ack(cmd)]

    def _report_rate(self, payload):
        return self._standalone(settings.CMD_REPORT_RATE, "report_rate", payload)

    def _precision(self, payload):
        return self._standalone(settings.CMD_PRECISION, "precision", payload)

    def _sensitivity(self, payload):
        return self._standalone(settings.CMD_SENSITIVITY, "sensitivity", payload)

    def _sleep(self, payload):
        return self._standalone(settings.CMD_SLEEP, "sleep_minutes", payload)

    def _restart(self, _payload):
        self.restarts += 1
        return [self._ack(settings.CMD_RESTART)]

    def _hardware_status(self, _payload):
        """Command 3: the whole settings block, built from this pad's state."""
        supported = [0, 0]
        enabled = [0, 0]
        for name, sub_id in settings.SUB_IDS.items():
            half = 0 if sub_id <= 8 else 1
            mask = 1 << ((sub_id - 1) if half == 0 else (sub_id - 9))
            if name not in self.UNSUPPORTED:
                supported[half] |= mask
            if self.settings[name]:
                enabled[half] |= mask
        body = bytearray(32)
        body[0] = 0x04
        body[1], body[2] = device.MAGIC1, device.MAGIC2
        body[3] = settings.CMD_STATUS
        body[4] = 1
        body[6], body[7] = supported[0], enabled[0]
        body[8], body[9] = supported[1], enabled[1]
        body[10] = self.sleep_minutes
        body[11] = self.report_rate
        body[12] = self.precision
        body[13] = self.sensitivity
        return [bytes(body)]


def device_type_for(model, default=128):
    """A `DeviceType` from a spec's `"code": "k5"` or `"code": 130`.

    A code names a *family* of numbers -- 128, 129, 133, 134, 135 and 136 are
    all `k5` -- so the lowest is taken, which is the base SKU rather than a
    special edition. Naming a number outright is how a spec asks for one of
    those, or for a type this table has never heard of, which is a device worth
    being able to conjure: the guard's job is to refuse it by name.
    """
    if model is None:
        return default
    text = str(model).strip().lower()
    if text.isdigit():
        return int(text)
    numbers = sorted(n for n, code in identity.DEVICE_TYPES.items()
                     if code == text)
    if not numbers:
        known = ", ".join(sorted(set(identity.DEVICE_TYPES.values())))
        raise ValueError(f"{model!r} is not a Flydigi device code -- {known}")
    return numbers[0]


def build_pad(path, index, entry):
    """One mock pad, from one entry of the bus spec.

    Identity is derived from the device's place in the spec when the spec does
    not give one, so it is the same on every run -- a `uid:` selector stored in
    a config file has to keep resolving, or the mock bus could not be used to
    test the thing it exists to test.
    """
    # The uid carries the index, since it is the identifier that works; the mac
    # stays as the hardware reports it -- empty -- unless a spec asks for one.
    uid = entry.get("uid") or f"{index & 0xFF:02x}{DEFAULT_UID[2:]}"
    pad = FakePad(
        path=path,
        device_type=device_type_for(entry.get("code")),
        mac=entry.get("mac") or DEFAULT_MAC,
        uid=uid,
        nickname=entry.get("nickname"),
        battery=int(entry.get("battery", 4)),
        charging=bool(entry.get("charging", False)),
        wired=str(entry.get("connect", "wired")).lower() != "dongle",
        firmware=entry.get("firmware", DEFAULT_FIRMWARE))
    if entry.get("third_party"):
        pad.transport["third_party"] = True
        pad.control_by = str(entry.get("control_by", "SDL"))
    return pad


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
