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

from . import blobs
from .blobs import PKG_SIZE, ProtocolError, build   # re-exported for callers

CMD_STATUS = 161
CMD_APPLY = 162
CMD_READ = 163
CMD_WRITE_START = 164
CMD_WRITE_PACK = 165
CMD_SAVE = 166

OFF_PROTO_VERSION = 0
OFF_PACKAGE_COUNT = 2
OFF_KEY_TABLE = 13
OFF_TRIGGER_CURVE = 123    # 2 x 7: travel curve per trigger
OFF_GRIP_VIBRATION = 145   # 1 + 2 x 4: the grip motors
OFF_TRIGGER_MOTOR = 154    # 1 + 2 x 14: the trigger motors
OFF_FORCE_TRIGGER = 185    # 2 x 20: the adaptive-trigger effect
OFF_DATA_VERSION = 225
OFF_TITLE = 770
TITLE_BYTES = 20

# Enable flags in this config are stored inverted: 0 means on, 0xFF means off.
ENABLED, DISABLED = 0, 0xFF

SIDE_LEFT, SIDE_RIGHT = 0, 1
SIDES = ("left", "right")

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

# What a key may be remapped *to*. Deliberately smaller than APEX5_KEYS: the
# extra buttons (M1-M4, and the C/Z pair by the bumpers) have no XInput
# equivalent, so a host cannot receive them. They are sources -- you map a
# paddle onto a real button, which is what the pad ships doing -- and offering
# them as targets would let someone map A to something nothing can see, which
# reads as "A stopped working".
EXTRA_KEYS = ["c", "z", "m1", "m2", "m3", "m4"]
XINPUT_TARGETS = [key for key in APEX5_KEYS if key not in EXTRA_KEYS]


def read_status(ctrl, wait=1.0, slots=4):
    """Which profile is active, and a version id for each.

    Cheap, and unlike `read_config` it has no side effect -- worth preferring
    wherever it will do. The version ids are each config's `data_version`
    field, so a caller can tell whether a cached copy is still current without
    reading the config at all. 0xFFFF means the slot has never been written.
    """
    for body in blobs.replies(ctrl, build(CMD_STATUS, b""), wait):
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
    blob = blobs.read_blob(ctrl, CMD_READ, cfg_id, f"config {cfg_id}",
                           wait=wait, retries=retries)
    return MappingConfig(blob, cfg_id)


def read_config_preserving(ctrl, cfg_id, wait=1.5):
    """Read a config and leave the pad on whatever it was using before.

    Reading switches the pad, which is not what someone browsing their profiles
    asked for. Returns (config, restored_to) so the caller can say what
    happened; restored_to is None when no restore was needed or possible.

    The desktop app deliberately does not use this. Command 166 commits
    whichever profile the pad is running, so an app that browses without
    switching cannot save what it is showing; it opens profiles the way Space
    Station does instead, leaving the pad on the one being edited. This stays
    for callers that really do want to look without disturbing anything.
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
    for body in blobs.replies(ctrl, build(CMD_APPLY, bytes([cfg_id])), wait):
        if body[2] == CMD_APPLY:
            return True
    return False


def save_config(ctrl, version=0, wait=2.0):
    """Commit the working config to flash. Slow -- the pad takes seconds.

    `version` is the id `read_status` reports for the slot, and the same value
    the blob carries at OFF_DATA_VERSION. Flydigi's SDK gives this command a
    10 second timeout where every other command gets 500 ms, which is what a
    flash write looks like.

    The observed values (23224, 65078, 65535 for an untouched slot) look like
    random tags for change detection rather than a counter, so callers should
    pass the config's own `data_version` to leave it alone. Passing 0 -- the
    default -- overwrites the slot's id with zero, which is almost certainly
    not what you want; it is kept as the default only because nothing has yet
    confirmed what the pad does with the value.
    """
    payload = struct.pack("<H", version & 0xFFFF)
    for body in blobs.replies(ctrl, build(CMD_SAVE, payload), wait):
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
    return blobs.write_blob(ctrl, CMD_WRITE_START, CMD_WRITE_PACK, cfg_id,
                            config.blob, old.blob if old is not None else None,
                            wait=wait)


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

    # -- grip vibration ---------------------------------------------------
    #
    # 9 bytes: a master switch, then per side (switch, min, max, scale). The
    # switches are inverted -- 0 is on. min/max bound how hard the motor is
    # allowed to run, so they are the intensity control; the pad clamps the
    # game's rumble into that window.

    def vibration(self, side):
        """(enabled, min, max, scale) for one grip motor."""
        base = OFF_GRIP_VIBRATION + 1 + self._side(side) * 4
        return (self.blob[base] == ENABLED, self.blob[base + 1],
                self.blob[base + 2], self.blob[base + 3])

    def set_vibration(self, side, enabled=None, minimum=None, maximum=None,
                      scale=None):
        base = OFF_GRIP_VIBRATION + 1 + self._side(side) * 4
        if enabled is not None:
            self.blob[base] = ENABLED if enabled else DISABLED
        if minimum is not None:
            self.blob[base + 1] = max(0, min(255, minimum))
        if maximum is not None:
            self.blob[base + 2] = max(0, min(255, maximum))
        if scale is not None:
            self.blob[base + 3] = max(0, min(255, scale))
        # The pad reads these as a window, so keep min <= max rather than
        # letting a slider produce an inverted range.
        if self.blob[base + 1] > self.blob[base + 2]:
            self.blob[base + 1], self.blob[base + 2] = (
                self.blob[base + 2], self.blob[base + 1])

    @property
    def vibration_enabled(self):
        return self.blob[OFF_GRIP_VIBRATION] == ENABLED

    @vibration_enabled.setter
    def vibration_enabled(self, value):
        self.blob[OFF_GRIP_VIBRATION] = ENABLED if value else DISABLED

    # -- adaptive triggers, stored per profile ----------------------------
    #
    # 20 bytes per side: effect type, an 8-byte rumble binding, a mixed border
    # and 10 effect parameters. This is the same effect vocabulary the live
    # SetForceTrigger command uses -- the difference is that this copy lives in
    # the pad, so it applies with no host process and no game integration.

    def trigger_effect(self, side):
        """(mode, params) for one trigger's stored effect."""
        base = OFF_FORCE_TRIGGER + self._side(side) * 20
        return self.blob[base], list(self.blob[base + 10 : base + 20])

    def set_trigger_effect(self, side, mode, params=()):
        base = OFF_FORCE_TRIGGER + self._side(side) * 20
        self.blob[base] = mode & 0xFF
        values = list(params)[:10] + [0] * max(0, 10 - len(params))
        self.blob[base + 10 : base + 20] = bytes(
            max(0, min(255, int(v))) for v in values)

    def trigger_curve(self, side):
        """(type, zero, point1, point2, end) -- where the trigger's travel maps."""
        base = OFF_TRIGGER_CURVE + self._side(side) * 7
        return {
            "type": self.blob[base],
            "zero": self.blob[base + 1],
            "point1": (self.blob[base + 2], self.blob[base + 3]),
            "point2": (self.blob[base + 4], self.blob[base + 5]),
            "end": self.blob[base + 6],
        }

    def set_trigger_curve(self, side, zero=None, end=None):
        base = OFF_TRIGGER_CURVE + self._side(side) * 7
        if zero is not None:
            self.blob[base + 1] = max(0, min(255, zero))
        if end is not None:
            self.blob[base + 6] = max(0, min(255, end))

    def trigger_motor(self, side):
        """(enabled, min, max, scale) for one trigger's own vibration motor."""
        base = OFF_TRIGGER_MOTOR + 1 + self._side(side) * 14
        return (self.blob[OFF_TRIGGER_MOTOR] == ENABLED, self.blob[base + 1],
                self.blob[base + 2], self.blob[base + 5])

    def set_trigger_motor(self, side, enabled=None, minimum=None, maximum=None,
                          scale=None):
        base = OFF_TRIGGER_MOTOR + 1 + self._side(side) * 14
        if enabled is not None:
            self.blob[OFF_TRIGGER_MOTOR] = ENABLED if enabled else DISABLED
        if minimum is not None:
            self.blob[base + 1] = max(0, min(255, minimum))
        if maximum is not None:
            self.blob[base + 2] = max(0, min(255, maximum))
        if scale is not None:
            self.blob[base + 5] = max(0, min(255, scale))

    @staticmethod
    def _side(side):
        if isinstance(side, str):
            return SIDES.index(side)
        return SIDE_RIGHT if side else SIDE_LEFT

    def __repr__(self):
        version = f"{self.proto_version >> 8}.{self.proto_version & 0xF}"
        return (f"<MappingConfig cfg={self.cfg_id} v{version} "
                f"{len(self.blob)}B title={self.title!r}>")
