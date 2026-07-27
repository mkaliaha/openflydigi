# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Button remapping profiles stored in the controller's own memory.

Space Station calls these "configs"; the pad holds several and switches between
them with the Menu button. They persist in hardware, which is why remapping set
up on Windows keeps working on Linux -- the pad does the remapping itself, and
nothing on the host is involved.

A config is one flat byte blob, transferred in fixed-size packets. Only the
NewXInput variants of the commands are implemented here, which is what the
Apex 5 speaks:

    read   163  [cfgId, pkgSize]              -> multi-packet reply
    apply  162  [cfgId]                       switch the pad to this config
    write  164  [cfgId, startIdx, nPkts, pkgSize] then 165 [pktNum, data...]
    save   166  [versionLo, versionHi]        commit to flash

Unlike the trigger-effect commands, these are checksummed and the pad rejects
a packet whose checksum is wrong -- see `build`.

**Blob layout** (protocol version 3.x). Offsets are into the assembled blob,
not into any packet:

    0..2     protocol version, little endian
    2        package count: 79 for v3.0, 84 for v3.1+
    3..13    legacy LED config
    13..109  key table -- 32 entries of 3 bytes, indexed by key id
    109..123 joystick curves
    123..137 trigger travel curves
    137..145 motion
    145..154 grip vibration
    154..183 trigger motors
    183..185 wheel
    185..225 force trigger (2 x 20)
    225..227 data version, little endian
    230..768 macros
    770..790 title, UTF-16LE
    790..840 v3.1+: joystick extra, macro cycle, motion curve

Only the key table and the title are interpreted here. Everything else is
carried through byte-for-byte, so writing a config back cannot disturb settings
this code does not understand -- which matters, because a config also holds the
trigger and macro state.
"""
import struct

from . import device

CMD_STATUS = 161
CMD_APPLY = 162
CMD_READ = 163
CMD_WRITE_START = 164
CMD_WRITE_PACK = 165
CMD_SAVE = 166

# NewXInput moves 20 bytes per packet; older protocols use 10.
PKG_SIZE = 20

OFF_PROTO_VERSION = 0
OFF_PACKAGE_COUNT = 2
OFF_KEY_TABLE = 13
OFF_DATA_VERSION = 225
OFF_TITLE = 770
TITLE_BYTES = 20

KEY_SLOTS = 32
KEY_ENTRY = 3

# Sentinels stored in the key table's target byte.
TARGET_IDENTITY = 255      # key does what it says on the shell
TARGET_MACRO = 32          # key runs a macro
TARGET_KEYBOARD = 254      # key sends keyboard/mouse, or is multi-function

# Turbo modes (the key table's second byte).
TURBO_OFF = 0
TURBO_WHILE_HELD = 1
TURBO_TOGGLE = 2

# ControllerKey ids. The key table is indexed by these, and a table entry's
# target byte is one of these too.
KEY_NAMES = {
    0: "up", 1: "right", 2: "down", 3: "left",
    4: "a", 5: "b", 6: "select", 7: "x", 8: "y", 9: "start",
    10: "lb", 11: "rb", 12: "lt", 13: "rt", 14: "thl", 15: "thr",
    16: "c", 17: "z",
    18: "m1", 19: "m2", 20: "m3", 21: "m4", 22: "m5", 23: "m6",
    24: "menu", 25: "turbo", 27: "home", 28: "back",
}
KEY_IDS = {name: key_id for key_id, name in KEY_NAMES.items()}

# Physical buttons on an Apex 5, in the order a UI should present them. The key
# table has 32 slots but most are unpopulated on this model.
APEX5_KEYS = [
    "a", "b", "x", "y",
    "up", "down", "left", "right",
    "lb", "rb", "lt", "rt",
    "thl", "thr",
    "select", "start", "home",
    "c", "z",
    "m1", "m2", "m3", "m4",
]


class ProtocolError(Exception):
    pass


def build(cmd_id, payload):
    """Build a checksummed vendor packet.

    The length byte counts the command and length bytes themselves, so it is
    payload length + 2, and the checksum lands at 3 + length. Flydigi's
    `CreateSimpleCommand` + `Crc(3, 3 + len)` in one step.
    """
    buf = device.build(cmd_id)
    length = len(payload) + 2
    buf[4] = length
    buf[5 : 5 + len(payload)] = payload
    buf[3 + length] = device.checksum(buf, 3, 3 + length)
    return buf


def _replies(ctrl, buf, wait):
    return [r[1:] for r in ctrl.send(buf, wait=wait) if len(r) > 7]


def read_status(ctrl, wait=1.0, slots=4):
    """Which profile is active, and a version id for each.

    Cheap, and unlike `read_config` it has no side effect -- worth preferring
    wherever it will do. The version ids are each config's `data_version`
    field, so a caller can tell whether a cached copy is still current without
    reading the config at all. 0xFFFF means the slot has never been written.
    """
    for body in _replies(ctrl, build(CMD_STATUS, b""), wait):
        if body[2] != CMD_STATUS:
            continue
        raw = body[5]
        # Slots are reported across two banks of four; the second bank reports
        # 4..7 for the same profiles.
        active = raw - 4 if 3 < raw <= 7 else (raw if raw <= 7 else 0)
        versions = [(body[7 + 2 * i] << 8) | body[6 + 2 * i] for i in range(slots)]
        return {"active": active, "versions": versions}
    return None


def read_config(ctrl, cfg_id, wait=1.5, retries=3):
    """Read one stored config off the pad.

    The reply is a run of packets carrying (total, index, cfgId, 20 bytes). The
    pad sends them back to back, so collect until the last index arrives rather
    than issuing one request per packet.

    **This switches the pad to the config being read** -- the firmware pages it
    in as the live one, audibly re-seating the trigger motors. Confirmed on
    hardware: after reading config 2, `read_status` reports 2 as active. A
    caller that does not intend to change what the user is playing with must
    read the status first and re-apply the original afterwards; see
    `read_config_preserving`.
    """
    for _ in range(retries):
        chunks = {}
        total = None
        for body in _replies(ctrl, build(CMD_READ, bytes([cfg_id, PKG_SIZE])), wait):
            if body[2] != CMD_READ:
                continue
            total, index = body[3], body[4]
            chunks[index] = bytes(body[6 : 6 + PKG_SIZE])
        if total and len(chunks) == total:
            blob = bytearray(total * PKG_SIZE)
            for index, chunk in chunks.items():
                blob[index * PKG_SIZE : (index + 1) * PKG_SIZE] = chunk
            return MappingConfig(blob, cfg_id)
        if total:
            missing = sorted(set(range(total)) - set(chunks))
            last_error = f"got {len(chunks)}/{total} packets, missing {missing}"
        else:
            last_error = "no reply -- the pad may be asleep, press a button"
    raise ProtocolError(f"reading config {cfg_id} failed: {last_error}")


def read_config_preserving(ctrl, cfg_id, wait=1.5):
    """Read a config and leave the pad on whatever it was using before.

    Reading switches the pad, which is not what someone browsing their profiles
    asked for. Returns (config, restored_to) so the caller can say what
    happened; restored_to is None when no restore was needed or possible.
    """
    status = read_status(ctrl)
    config = read_config(ctrl, cfg_id, wait=wait)
    previous = status["active"] if status else None
    if previous is None or previous == cfg_id:
        return config, None
    apply_config(ctrl, previous)
    return config, previous


def apply_config(ctrl, cfg_id, wait=0.5):
    """Switch the pad to a stored config."""
    for body in _replies(ctrl, build(CMD_APPLY, bytes([cfg_id])), wait):
        if body[2] == CMD_APPLY:
            return True
    return False


def save_config(ctrl, version=0, wait=2.0):
    """Commit the working config to flash. Slow -- the pad takes seconds."""
    payload = struct.pack("<H", version & 0xFFFF)
    for body in _replies(ctrl, build(CMD_SAVE, payload), wait):
        if body[2] == CMD_SAVE:
            return True
    return False


def write_config(ctrl, cfg_id, config, old=None, wait=0.5):
    """Write a config to the pad, sending only the packets that changed.

    Flydigi diffs against the previously read config and transfers contiguous
    runs of changed packets. That is worth copying: a full config is 42 packets,
    and remapping one button touches one of them.

    Returns the number of packets sent. Call `save_config` afterwards to make
    it survive a power cycle.
    """
    new_packets = config.packets()
    old_packets = old.packets() if old is not None else None
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
        header = bytes([cfg_id, start, len(packets), PKG_SIZE])
        if not _acked(ctrl, CMD_WRITE_START, header, wait):
            raise ProtocolError(f"pad rejected write header at packet {start}")
        for offset, packet in enumerate(packets):
            if not _acked(ctrl, CMD_WRITE_PACK, bytes([offset]) + packet, wait):
                raise ProtocolError(f"pad rejected packet {start + offset}")
            sent += 1
    return sent


def _acked(ctrl, cmd_id, payload, wait):
    return any(body[2] == cmd_id for body in _replies(ctrl, build(cmd_id, payload), wait))


class MappingConfig:
    """One stored profile. Wraps the raw blob and edits it in place."""

    def __init__(self, blob, cfg_id=None):
        self.blob = bytearray(blob)
        self.cfg_id = cfg_id

    def copy(self):
        return MappingConfig(bytearray(self.blob), self.cfg_id)

    def packets(self, size=PKG_SIZE):
        return [bytes(self.blob[i : i + size]) for i in range(0, len(self.blob), size)]

    @property
    def proto_version(self):
        return struct.unpack_from("<H", self.blob, OFF_PROTO_VERSION)[0]

    @property
    def package_count(self):
        return self.blob[OFF_PACKAGE_COUNT]

    @property
    def data_version(self):
        return struct.unpack_from("<H", self.blob, OFF_DATA_VERSION)[0]

    @property
    def title(self):
        raw = bytes(self.blob[OFF_TITLE : OFF_TITLE + TITLE_BYTES])
        return raw.decode("utf-16-le", "replace").rstrip("￿\x00")

    @title.setter
    def title(self, value):
        raw = value.encode("utf-16-le")[:TITLE_BYTES]
        self.blob[OFF_TITLE : OFF_TITLE + TITLE_BYTES] = raw.ljust(TITLE_BYTES, b"\x00")

    def _entry(self, key):
        key_id = KEY_IDS[key] if isinstance(key, str) else key
        if not 0 <= key_id < KEY_SLOTS:
            raise KeyError(f"no key slot {key!r}")
        return OFF_KEY_TABLE + key_id * KEY_ENTRY, key_id

    def mapping(self, key):
        """What this physical key currently does.

        Returns (target, turbo_mode, turbo_frequency). `target` is a key name,
        or "macro" / "keyboard" for the two sentinels. A key that is not
        remapped reports itself, which is how the pad stores identity.
        """
        offset, key_id = self._entry(key)
        target, mode, frequency = self.blob[offset : offset + KEY_ENTRY]
        if target == TARGET_MACRO:
            return "macro", TURBO_OFF, 0
        if frequency > 0:
            return KEY_NAMES.get(target, target), mode, frequency
        if target == TARGET_KEYBOARD:
            return "keyboard", TURBO_OFF, 0
        # Anything above the key range means "unchanged", stored as 255.
        if target > TARGET_MACRO:
            target = key_id
        return KEY_NAMES.get(target, target), TURBO_OFF, 0

    def set_mapping(self, key, target, turbo_mode=TURBO_OFF, frequency=0):
        """Remap a physical key. `target` may be a key name, id, or None.

        None (or the key's own name) restores the default, which the pad stores
        as 255 rather than as the key's own id.
        """
        offset, key_id = self._entry(key)
        if target is None:
            target_id = TARGET_IDENTITY
        elif isinstance(target, str):
            if target == "macro":
                target_id = TARGET_MACRO
            elif target == "keyboard":
                target_id = TARGET_KEYBOARD
            else:
                target_id = KEY_IDS[target]
        else:
            target_id = target
        if target_id == key_id:
            target_id = TARGET_IDENTITY
        if frequency > 0:
            # Turbo needs a real target; identity has no id to repeat.
            if target_id in (TARGET_IDENTITY, TARGET_KEYBOARD):
                target_id = key_id
            self.blob[offset : offset + KEY_ENTRY] = bytes(
                [target_id, turbo_mode, min(255, frequency)])
        else:
            self.blob[offset : offset + KEY_ENTRY] = bytes([target_id, 0, 0])

    def mappings(self, keys=None):
        """Every populated key, as {name: (target, mode, frequency)}."""
        return {key: self.mapping(key) for key in (keys or APEX5_KEYS)}

    def remapped(self, keys=None):
        """Only the keys that differ from the default -- what a UI should mark."""
        out = {}
        for key in keys or APEX5_KEYS:
            target, mode, frequency = self.mapping(key)
            if target != key or frequency:
                out[key] = (target, mode, frequency)
        return out

    def __repr__(self):
        version = f"{self.proto_version >> 8}.{self.proto_version & 0xF}"
        return (f"<MappingConfig cfg={self.cfg_id} v{version} "
                f"{len(self.blob)}B title={self.title!r}>")
