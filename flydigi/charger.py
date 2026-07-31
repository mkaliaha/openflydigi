# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""The CD2 charging dock.

Same `5a a5` envelope as the pad and a different report id, so the transport is
`device.Controller` with one byte changed:

    [0] report id 0x00 -- the dock declares no report ids at all
    [1] 0x5A
    [2] 0xA5
    [3] command id
    [4] length, counting bytes [3] and [4] themselves
    [5..] payload
    [3 + length] checksum, the 8-bit sum over [3, 3 + length)

Replies carry no report-id byte, so they start at the magic:

    [0] 0x5A  [1] 0xA5  [2] command id  [3] length  [4..] payload

**The reply checksum sits one slot earlier than a request's** -- at
`[2 + length]`, over `[2, 2 + length)`. Verified against every read this module
makes: heartbeat, uid, nickname, LED config, and the unsolicited status report.
The command-97 ack is the one exception seen, putting it at `[3 + length]`.
Flydigi never check a reply checksum anywhere -- `ParseAckData` matches on the
command byte alone -- so neither does this module, and a write is confirmed by
reading it back.

**162 LEDs in a wedge**, 16 rows of 14, 15, 16, 15, 14, 13 ... down to 3.

Sizes and pacing, all from the SDK: a command is a 32-byte buffer, a data pack
is 64, packs carry 50 payload bytes each, and every pack waits for its own ack
before the next goes out -- there is no inter-packet delay anywhere in
Flydigi's stack, and no blind streaming either.

Measured on the dock here (firmware 0.0.3.9, DeviceType 0): a short output
report is accepted. 32-, 64- and 65-byte writes of the same heartbeat all drew
identical replies, so Linux hidraw not zero-padding the way hidapi-on-Windows
does costs nothing.

-> docs/findings-other-devices.md
"""
import math
import os

from . import device
from .device import DeviceBusy, DeviceNotFound      # re-exported for callers

PACKET_LEN = 32
PACK_PACKET_LEN = 64
REPORT_ID_OUT = 0x00

CMD_HEARTBEAT = 1
CMD_READ_NICKNAME = 2
CMD_READ_UID = 4
CMD_SLEEP_WHEN_CHARGING = 17
CMD_LED_SYNC = 18
CMD_CLOSE_WITH_SYSTEM = 19
CMD_READ_LED = 20
CMD_WRITE_RGB_START = 22
CMD_WRITE_RGB_PACK = 23
CMD_WRITE_NICKNAME = 24
CMD_SHOW_ANIMATION = 25
CMD_WRITE_LED_START = 97
CMD_WRITE_LED_PACK = 98

# Built but deliberately not wrapped. 175 resets the mapping config -- and
# Space Station's own "restore defaults" does not send it, it re-uploads the
# shipped default file instead, so what the firmware does with a bare 175 is
# unmeasured. 224 is firmware upgrade mode and 254 rewrites the device type;
# both are one-way trips on a device with no recovery documented, and the
# argument in docs/findings-other-devices.md against aiming 31 at the pad's
# program chips applies here unchanged.
CMD_RESET_MAPPING = 175
CMD_UPGRADE_MODE = 224
CMD_WRITE_DEVICE_TYPE = 254

# Unsolicited, about once a second, and the only report the dock sends on its
# own. `ChargerProtocol.ParseData` singles it out and forwards it to a raw-data
# listener rather than treating it as an ack.
REPORT_STATUS = 239

PACK_BYTES = 50

LED_COUNT = 162
ROW_LENGTHS = (14, 15, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3)

BRIGHTNESS_MIN = 1
BRIGHTNESS_MAX = 100

# ChargerLedType. Ten members; Space Station's dropdown offers nine of them,
# omitting Solid.
MODE_CLOSE = 0
MODE_SOLID = 1
MODE_DEFAULT = 2
MODE_CUSTOM = 3
MODE_DIAGONAL_FLOW = 4
MODE_BREATH = 5
MODE_GRADIENT = 6
MODE_WAVE_GRADIENT = 7
MODE_RAINBOW = 8
MODE_PULSE = 9

MODE_NAMES = {
    MODE_CLOSE: "close", MODE_SOLID: "solid", MODE_DEFAULT: "default",
    MODE_CUSTOM: "custom", MODE_DIAGONAL_FLOW: "diagonal-flow",
    MODE_BREATH: "breath", MODE_GRADIENT: "gradient",
    MODE_WAVE_GRADIENT: "wave-gradient", MODE_RAINBOW: "rainbow",
    MODE_PULSE: "pulse",
}

# ChargerLedDirection. Nine members; the picker offers the first four after
# NONE, and only Rainbow and WaveGradient show it at all.
DIR_NONE = 0
DIR_RIGHT = 1
DIR_LEFT = 2
DIR_DOWN = 3
DIR_UP = 4
DIR_RIGHT_DOWN = 5
DIR_RIGHT_UP = 6
DIR_LEFT_DOWN = 7
DIR_LEFT_UP = 8

DIR_NAMES = {DIR_NONE: "none", DIR_RIGHT: "right", DIR_LEFT: "left",
             DIR_DOWN: "down", DIR_UP: "up"}

# Space Station's own defaults, per mode: (period, colours, direction). A mode
# whose colour list is empty is one whose generator ignores colour entirely.
# `period` is Flydigi's "frame interval" -- bigger is slower.
BLUE = (0, 116, 255)
VIOLET = (195, 32, 230)
CYAN = (22, 230, 249)

MODE_DEFAULTS = {
    MODE_CLOSE: (3, (), DIR_NONE),
    MODE_DEFAULT: (3, (), DIR_NONE),
    MODE_CUSTOM: (1, (), DIR_NONE),
    # Solid is in the firmware's enum and out of Space Station's dropdown, so
    # it has no row in their table either. This is what their lookup falls back
    # to for a mode it does not know -- and the fallback carries **no** colour,
    # which is what makes `solid_frames` reach Flydigi's own #212225 rather
    # than a colour this module invented.
    MODE_SOLID: (1, (), DIR_NONE),
    MODE_DIAGONAL_FLOW: (2, (VIOLET, CYAN), DIR_NONE),
    MODE_BREATH: (3, (BLUE,), DIR_NONE),
    MODE_GRADIENT: (5, (), DIR_NONE),
    MODE_WAVE_GRADIENT: (2, (VIOLET, CYAN), DIR_RIGHT),
    MODE_RAINBOW: (2, (), DIR_RIGHT),
    MODE_PULSE: (2, (BLUE,), DIR_NONE),
}

# The period slider's bounds, per mode, as the UI enforces them.
MODE_PERIOD_RANGE = {
    MODE_CLOSE: (1, 1), MODE_DEFAULT: (1, 1), MODE_CUSTOM: (1, 1),
    MODE_SOLID: (1, 1),
    MODE_DIAGONAL_FLOW: (1, 5), MODE_BREATH: (1, 5), MODE_GRADIENT: (1, 10),
    MODE_WAVE_GRADIENT: (1, 5), MODE_RAINBOW: (1, 5), MODE_PULSE: (1, 5),
}

# What Space Station's lookup answers for a mode with no row of its own, so a
# caller building a picker off MODE_NAMES never has to handle a missing key.
PERIOD_RANGE_FALLBACK = (1, 1)

# The shade `X` falls back to when a solid mode is asked for with no colour.
FALLBACK_COLOUR = (0x21, 0x22, 0x25)


class ProtocolError(Exception):
    pass


class WrongDock(Exception):
    """Something answered on a dock node, and it is not a CD2."""


# `ChargerDeviceType` -> the product name Space Station shows for it. Every
# member is a CD2 variant; the special editions differ in artwork, not in
# protocol, and all five are `cd2`.
#
# **This table is the guard, because Flydigi have none.**
# `FlydigiChargerUtil.GetDeviceCodeById` returns the literal string "cd2" for
# any argument whatsoever, so their SDK drives anything in the 0x6xxx family as
# a CD2 without ever asking. Ours asks.
#
# The gen-1 dock -- the Vader 4's -- has never been on this bus, so what it
# enumerates as is unmeasured. Two things make it unlikely to reach this code:
# it must carry vendor 0x37d7 with a product id of 0x6xxx to be found at all,
# and Flydigi's own `IsOldProtocol()` is `VendorId != 0x37D7`, so anything that
# does carry that vendor id speaks the `5a a5` dialect this module writes. A
# device that gets past both and answers with an unknown type is refused here
# rather than driven on the assumption it is a CD2.
DOCK_TYPES = {
    0: "Controller Charging Dock 2 Pro",
    1: "Controller Charging Dock 2 Pro EVA .ver",
    2: "Honkai: Star Rail Castorice Controller Charging Dock",
    3: "Genshin Impact Skirk - Void Star Game Controller Charging Dock",
    4: "Elysia Elite Gaming Controller Charging Dock",
}


def name_for(device_type):
    return DOCK_TYPES.get(device_type)


def require(dock, wait=0.5):
    """Refuse a dock this module does not know how to drive. Returns its info.

    One heartbeat, the same shape as `identity.require` for a pad, and for the
    same reason: writes are gated, reads are not. Call it once per connection
    before anything writes.

    Two layers, and the first is the stronger one. A gen-1 dock almost
    certainly cannot get past it: all sixteen charger commands are built
    `isNewProtocol: true` and not one uses the legacy default, so unlike the
    controller SDK -- where most command factories carry an XInput and a DInput
    twin beside the NewXInput one -- the charger SDK has no legacy dialect at
    all. A dock old enough to predate `5a a5` has nothing to answer with, and
    `read_info` raises before this gets a device type to look at. The type
    check is the second layer, and it separates *editions* rather than
    generations: 0..4 are the standard CD2 and its four collaborations.

    The product string is deliberately **not** a gate. `HID_NAME` does name
    the generation -- this dock reports "flydigi Flydigi CD2" -- but whether
    the four special editions carry the same string is unmeasured, and
    refusing a real CD2 over its artwork would be worse than the case being
    guarded against. It goes in the message instead, so a refusal says what it
    found.
    """
    info = read_info(dock, wait=wait)
    if info["device_type"] not in DOCK_TYPES:
        name = device.hid_name(os.path.basename(dock.path)) or "unnamed"
        raise WrongDock(
            f"the device on {dock.path} ({name}) reports charger type "
            f"{info['device_type']}, which is not a CD2 -- refusing to write "
            f"to it. Flydigi's own SDK would treat it as one; this does not.")
    return info


def find_dock():
    return device.find_device(device.FAMILY_DOCK)


def list_docks():
    """Every dock on the bus: {path, uid, nickname, info} for each.

    Opens each node in turn and asks it what it is, so this costs one exchange
    per dock and needs the node free. A dock that does not answer is reported
    with `info` None rather than omitted, because "there is a device here and
    it will not talk" is the thing a caller most needs to see.

    **Only one dock has ever been measured**, so ordering between two of them
    is `find_nodes`' sorted-node order and nothing better. Selecting by uid is
    the stable way to name one; the node number is not.
    """
    found = []
    for path in device.find_nodes(device.FAMILY_DOCK):
        entry = {"path": path, "uid": None, "nickname": None, "info": None,
                 "product": device.hid_name(os.path.basename(path))}
        try:
            with Dock(path) as dock:
                entry["info"] = read_info(dock)
                entry["uid"] = read_uid(dock)
                entry["nickname"] = read_nickname(dock)
        except (OSError, ProtocolError, DeviceBusy):
            pass
        found.append(entry)
    return found


def open_dock(path=None, uid=None):
    """Open one dock, by node or by uid. Bare, it takes the first one.

    A uid is matched case-insensitively and may be given as a prefix, since the
    full 26 hex digits are not something anyone types.
    """
    if path is not None:
        return Dock(path)
    if uid is None:
        return Dock()
    wanted = uid.lower().replace(":", "")
    matches = [d for d in list_docks()
               if d["uid"] and d["uid"].startswith(wanted)]
    if not matches:
        raise DeviceNotFound(f"no charging dock with a uid starting {uid!r}")
    if len(matches) > 1:
        names = ", ".join(d["uid"] for d in matches)
        raise DeviceNotFound(f"{uid!r} matches more than one dock: {names}")
    return Dock(matches[0]["path"])


def build(cmd_id, payload=b"", size=PACKET_LEN):
    buf = bytearray(size)
    buf[0] = REPORT_ID_OUT
    buf[1] = device.MAGIC1
    buf[2] = device.MAGIC2
    buf[3] = cmd_id
    buf[4] = 2 + len(payload)
    buf[5:5 + len(payload)] = payload
    buf[3 + buf[4]] = sum(buf[3:3 + buf[4]]) & 0xFF
    return buf


class Dock(device.Controller):
    """Open handle to the dock's command interface.

    Everything about holding the node -- the advisory `flock`, the re-entrant
    claim, draining stale replies -- is `device.Controller`'s and is right here
    unchanged. Only the node it opens and the packet it builds differ.
    """

    def __init__(self, path=None):
        super().__init__(path or find_dock())

    def command(self, cmd_id, payload=b"", wait=0.5, size=PACKET_LEN):
        return self.send(build(cmd_id, payload, size), wait=wait,
                         until=lambda seen: any(reply_for(r, cmd_id)
                                                for r in seen))


def reply_for(data, cmd_id):
    """True when `data` is this dock answering `cmd_id`.

    Flydigi's test exactly: the magic, then the command byte. Nothing checks a
    reply's checksum, and neither does this -- see the module docstring.
    """
    return (len(data) > 3 and data[0] == device.MAGIC1
            and data[1] == device.MAGIC2 and data[2] == cmd_id)


def _answer(replies, cmd_id, what):
    for data in replies:
        if reply_for(data, cmd_id):
            return data
    raise ProtocolError(f"no reply to the {what} read")


def _ask(dock, cmd_id, what, payload=b"", wait=0.5):
    return _answer(dock.command(cmd_id, payload, wait=wait), cmd_id, what)


# --------------------------------------------------------------------------
# What the dock says about itself
# --------------------------------------------------------------------------

def read_info(dock, wait=0.5):
    """Heartbeat: the device type, firmware and the four switch states.

    Offsets are `HeartBeatCommand.ParseAckData`'s. Firmware is two bytes of
    packed nibbles, and Flydigi treat an all-zero version as "not reported".
    """
    data = _ask(dock, CMD_HEARTBEAT, "heartbeat", wait=wait)
    version = (f"{data[16] >> 4}.{data[16] & 0xF}."
               f"{data[17] >> 4}.{data[17] & 0xF}")
    return {
        "device_type": data[6],
        "chip_type": data[15] & 0xF,
        "firmware": None if set(version) <= {"0", "."} else version,
        "sleep_when_charging": data[18] == 1,
        "led_sync": data[19] == 1,
        "close_with_system": data[20] == 1,
        "show_animation_when_charging": data[21] == 1,
    }


def read_uid(dock, wait=0.5):
    data = _ask(dock, CMD_READ_UID, "uid", wait=wait)
    return "".join(f"{b:02x}" for b in data[6:19])


def read_nickname(dock, wait=0.5):
    """The stored nickname, or None when the dock has never been given one.

    `data[3] > 4` is Flydigi's own test for "there is a name here", and the
    first byte being 0x00 or 0xff is their test for an erased one.
    """
    data = _ask(dock, CMD_READ_NICKNAME, "nickname", wait=wait)
    if data[3] <= 4:
        return None
    raw = data[6:6 + data[3] - 3]
    if not raw or raw[0] in (0x00, 0xFF):
        return None
    return raw.decode("utf-8", "replace").strip()


def parse_status(data):
    """The unsolicited `239` report: whether a pad is docked, and its battery.

    Offsets are the service layer's (`ChargerRepository`'s raw-data handler).
    Measured with nothing in the dock, `docked` reads false, which fits; the
    battery byte has not been seen with a pad actually seated.
    """
    if not reply_for(data, REPORT_STATUS):
        return None
    return {"docked": bool(data[7]), "battery": data[8]}


def read_status(dock, wait=1.5):
    """Wait for one status report. The dock sends them about once a second."""
    for data in dock.send(build(CMD_HEARTBEAT), wait=wait):
        parsed = parse_status(data)
        if parsed is not None:
            return parsed
    return None


# --------------------------------------------------------------------------
# The four switches
# --------------------------------------------------------------------------

def _set_flag(dock, cmd_id, enable, what, wait=0.5):
    replies = dock.command(cmd_id, bytes((1 if enable else 0,)), wait=wait)
    if not any(reply_for(r, cmd_id) for r in replies):
        raise ProtocolError(f"the dock did not acknowledge {what}")
    return True


def set_sleep_when_charging(dock, enable, wait=0.5):
    """Space Station calls this "Intelligent start"."""
    return _set_flag(dock, CMD_SLEEP_WHEN_CHARGING, enable,
                     "sleep-when-charging", wait=wait)


def set_led_sync(dock, enable, wait=0.5):
    """"Lighting Sync" -- keep the dock's lighting in step with the pad's.

    Nothing host-side moves lighting between the two: this is one enable byte
    and the devices arrange it between themselves.
    """
    return _set_flag(dock, CMD_LED_SYNC, enable, "lighting sync", wait=wait)


def set_close_with_system(dock, enable, wait=0.5):
    """"Close When Shutdown"."""
    return _set_flag(dock, CMD_CLOSE_WITH_SYSTEM, enable,
                     "close-with-system", wait=wait)


def set_show_animation_when_charging(dock, enable, wait=0.5):
    """"Power Display".

    Space Station makes this and sleep-when-charging mutually exclusive in its
    UI, forcing the other off whenever one goes on. That is a UI rule, not a
    firmware one -- nothing in the SDK enforces it -- so it is not enforced
    here; a caller that wants the pairing should do it in its own UI.
    """
    return _set_flag(dock, CMD_SHOW_ANIMATION, enable,
                     "show-animation-when-charging", wait=wait)


# --------------------------------------------------------------------------
# Lighting
# --------------------------------------------------------------------------

class LedConfig:
    """The dock's lighting.

    `frames` is a list of frames, each a list of `LED_COUNT` (r, g, b) tuples.
    A read never returns frames -- command 20 answers with the header only --
    so a config that came off the dock has `frames == []` and cannot be
    re-uploaded as-is. Regenerate from `mode` instead; that is what `generate`
    is for.
    """

    def __init__(self, mode=MODE_PULSE, brightness=50, period=None,
                 direction=None, colours=None, frames=None,
                 use_colour_count=None):
        period_default, colours_default, direction_default = MODE_DEFAULTS.get(
            mode, (1, (), DIR_NONE))
        self.mode = mode
        self.brightness = brightness
        self.period = period_default if period is None else period
        self.direction = direction_default if direction is None else direction
        self.colours = [tuple(c) for c in
                        (colours_default if colours is None else colours)]
        self.frames = [] if frames is None else frames
        # `useColorCount` is a field of its own on the wire, not the palette's
        # length, and Flydigi's own preset table has the two disagreeing in
        # both directions -- two colours with a count of 0, and a count of 1
        # with no colours. That the dock plays those presets regardless is the
        # evidence it does not use the byte to find the frame data. Deriving it
        # is what every live path does; this exists so a captured config can be
        # replayed byte for byte.
        self.use_colour_count = use_colour_count

    @property
    def colour_count(self):
        if self.use_colour_count is None:
            return len(self.colours)
        return self.use_colour_count

    def copy(self):
        return LedConfig(self.mode, self.brightness, self.period,
                         self.direction, list(self.colours),
                         [list(f) for f in self.frames], self.use_colour_count)

    def __repr__(self):
        return (f"LedConfig(mode={MODE_NAMES.get(self.mode, self.mode)}, "
                f"brightness={self.brightness}, period={self.period}, "
                f"direction={DIR_NAMES.get(self.direction, self.direction)}, "
                f"colours={self.colours}, frames={len(self.frames)})")


def read_led_config(dock, wait=0.5):
    """Read the header. Note the field order differs from the write's.

    A read answers mode, brightness, period, direction, count; a write
    serialises frame count, period, brightness, mode, direction, count. The
    transposition is Flydigi's and is easy to get backwards.
    """
    data = _ask(dock, CMD_READ_LED, "LED config", wait=wait)
    count = data[8]
    colours = []
    if 0 < count < 10:
        colours = [(data[9 + i * 3], data[9 + i * 3 + 1], data[9 + i * 3 + 2])
                   for i in range(count)]
    return LedConfig(mode=data[4], brightness=data[5], period=data[6],
                     direction=data[7], colours=colours,
                     use_colour_count=count)


def serialise(config):
    """`ConfigParser.ParseFrameLedConfigToArray`, byte for byte."""
    out = bytearray((len(config.frames) & 0xFF, config.period & 0xFF,
                     config.brightness & 0xFF, config.mode & 0xFF,
                     config.direction & 0xFF, config.colour_count & 0xFF))
    for colour in config.colours:
        out += bytes(int(c) & 0xFF for c in colour)
    for frame in config.frames:
        for colour in frame:
            out += bytes(int(c) & 0xFF for c in colour)
    return bytes(out)


def write_led_config(dock, config, wait=1.0):
    """Upload a whole LED config: one start packet, then the data in packs.

    **The frame count in the header must match the frames actually sent.**
    Measured here: a header claiming zero frames, with none supplied, left the
    dock cycling whatever was already in its frame memory -- fragments of the
    previous animation, then noise, then flat white. The buffer is not cleared
    by a config write, so a short write shows as corruption rather than as
    nothing happening.

    One deliberate divergence from Flydigi. They advertise
    `len(blob) // 50 + 1` packs while sending `ceil(len(blob) / 50)`, which
    agree except when the blob divides by 50 exactly -- and there they promise
    the dock one more pack than they send. This sends the true count. Their
    arithmetic is reachable: a Custom animation of 4 frames is 1950 bytes, 39
    packs against an advertised 40.
    """
    blob = serialise(config)
    packs = [blob[i:i + PACK_BYTES] for i in range(0, len(blob), PACK_BYTES)]
    if not packs:
        packs = [b""]
    total = len(packs)

    with dock.claim():
        start = bytes((10, 0, 0, (total >> 8) & 0xFF, total & 0xFF))
        if not any(reply_for(r, CMD_WRITE_LED_START)
                   for r in dock.command(CMD_WRITE_LED_START, start, wait=wait)):
            raise ProtocolError("the dock did not acknowledge the LED write")

        for index, pack in enumerate(packs):
            head = bytes(((total >> 8) & 0xFF, total & 0xFF,
                          (index >> 8) & 0xFF, index & 0xFF, len(pack)))
            replies = dock.command(CMD_WRITE_LED_PACK, head + pack,
                                   wait=wait, size=PACK_PACKET_LEN)
            if not any(reply_for(r, CMD_WRITE_LED_PACK) for r in replies):
                raise ProtocolError(
                    f"the dock stopped acknowledging at pack {index} of "
                    f"{total} -- its frame memory now holds a partial "
                    f"animation, so write a whole config before trusting it")
    return total


# --------------------------------------------------------------------------
# The effects, computed here because the dock plays frames rather than
# generating them
# --------------------------------------------------------------------------
#
# Ported from Space Station's own generators in `useLedEffectRenderer`, which
# is where the arithmetic below comes from -- including the parts that look
# wrong. Two of them are worth naming, since a "corrected" port would not match
# the reference:
#
#   * `_hsl` uses `q = l + s - l*s` unconditionally, where the usual HSL
#     conversion picks `l * (1 + s)` below mid-lightness. Every call site passes
#     lightness 0.5 and saturation 1, where the two agree.
#   * `_fade` takes the period and ignores it, so a breath's step size does not
#     depend on its frame interval.
#
# The geometric effects (pulse, diagonal flow) do not use the LED positions the
# image sampler uses. They build their own lattice around whichever preview
# circle sits nearest the middle of a 450x420 box -- `point_115`, index 114 --
# with a horizontal pitch of width/20 and a vertical pitch of that times
# sqrt(3)/2. Transcribed rather than reasoned about.

_VIEW_W, _VIEW_H = 450.0, 420.0
_CENTRE_X = 47.56 * _VIEW_W / 100.0
_CENTRE_Y = 50.48 * _VIEW_H / 100.0
_PITCH_X = _VIEW_W / 20.0
_PITCH_Y = _PITCH_X * math.sqrt(3) / 2.0


def _round(value):
    """JavaScript's Math.round: halves go up. Every input here is >= 0."""
    return int(math.floor(value + 0.5))


def _lattice():
    """(index, x, y) for each LED on the synthetic grid the effects use."""
    index = 0
    for row, length in enumerate(ROW_LENGTHS):
        left = _CENTRE_X - ((length - 1) * _PITCH_X) / 2.0
        y = _CENTRE_Y + (row - len(ROW_LENGTHS) / 2.0) * _PITCH_Y
        for column in range(length):
            yield index, left + column * _PITCH_X, y
            index += 1


def _hsl(hue, saturation, lightness):
    def channel(low, high, position):
        if position < 0:
            position += 1
        if position > 1:
            position -= 1
        if position < 1 / 6:
            return low + (high - low) * 6 * position
        if position < 0.5:
            return high
        if position < 2 / 3:
            return low + (high - low) * (2 / 3 - position) * 6
        return low
    high = lightness + saturation - lightness * saturation
    low = 2 * lightness - high
    return (_round(channel(low, high, hue + 1 / 3) * 255),
            _round(channel(low, high, hue) * 255),
            _round(channel(low, high, hue - 1 / 3) * 255))


def _fade(current, target, step):
    value = (target - current) * step / 50 + current
    if value < 0:
        return 0
    if value > 255:
        return 255
    return int(math.floor(value))


def _flat(colour):
    return [tuple(colour)] * LED_COUNT


def _colour(config, index, fallback):
    if index < len(config.colours):
        return tuple(config.colours[index])
    return fallback


def close_frames(_config):
    return [_flat((0, 0, 0))]


def solid_frames(config):
    return [_flat(_colour(config, 0, FALLBACK_COLOUR))]


def breath_frames(config):
    """Fade the colour to black one step at a time, then play it backwards.

    The step count is not fixed: each step scales what is left by
    `1 - step / 50`, so a darker colour reaches black sooner and yields fewer
    frames. Termination is guaranteed by step 50, where the factor turns
    negative and the clamp takes over.
    """
    if not config.colours:
        return []
    current = list(_colour(config, 0, BLUE))
    frames = [_flat(current)]
    step = 1
    while current[0] or current[1] or current[2]:
        current[2] = _fade(current[2], 0, step)
        current[0] = _fade(current[0], 0, step)
        current[1] = _fade(current[1], 0, step)
        frames.append(_flat(current))
        step += 1
    # The mirror, minus both endpoints so neither is played twice. Flydigi
    # reverse, `pop()` and `shift()`; on a one-frame list -- which is what an
    # all-black colour produces, since the fade loop never runs -- their
    # `shift()` on an already-emptied array is a no-op, while popping index 0
    # here would raise. A slice gets both cases right.
    return frames + frames[-2:0:-1]


def gradient_frames(config):
    return [_flat(_hsl(step / 50, 1, 0.5)) for step in range(50)]


def rainbow_frames(config):
    frames = []
    rows = len(ROW_LENGTHS)
    for step in range(50):
        offset = (step / 50) % 1
        frame = [None] * LED_COUNT
        index = 0
        for row, length in enumerate(ROW_LENGTHS):
            for column in range(length):
                if config.direction == DIR_DOWN:
                    along = 1 - row / rows
                elif config.direction == DIR_UP:
                    along = row / rows
                elif config.direction == DIR_RIGHT:
                    along = 1 - column / length
                else:
                    along = column / length
                frame[index] = _hsl((offset + along) % 1, 1, 0.5)
                index += 1
        frames.append(frame)
    return frames


def wave_gradient_frames(config):
    first = _colour(config, 0, (255, 0, 0))
    second = _colour(config, 1, (0, 0, 255))
    rows = len(ROW_LENGTHS)
    frames = []
    for step in range(50):
        phase = step / 50 * math.pi * 2
        frame = [None] * LED_COUNT
        index = 0
        for row, length in enumerate(ROW_LENGTHS):
            for column in range(length):
                if config.direction in (DIR_DOWN, DIR_UP):
                    ripple = row if config.direction == DIR_UP else rows - 1 - row
                    across = row / (rows - 1)
                else:
                    ripple = (column if config.direction == DIR_LEFT
                              else length - 1 - column)
                    across = column / (length - 1)
                if config.direction in (DIR_UP, DIR_LEFT):
                    across = 1 - across
                level = (math.sin(phase + ripple * 0.3) + 1) / 2
                frame[index] = tuple(
                    _round((a + (b - a) * across) * level)
                    for a, b in zip(first, second))
                index += 1
        frames.append(frame)
    return frames


def diagonal_flow_frames(config):
    first = _colour(config, 0, (255, 0, 0))
    second = _colour(config, 1, (0, 0, 255))
    lattice = list(_lattice())
    frames = []
    for step in range(50):
        phase = step / 50 * math.pi * 2
        frame = [None] * LED_COUNT
        for index, x, y in lattice:
            angle = math.atan2(y - _CENTRE_Y, x - _CENTRE_X) + phase
            turn = ((angle + math.pi) / (math.pi * 2)) % 1
            across = turn * 2 if turn < 0.5 else 2 - turn * 2
            frame[index] = tuple(_round(a + (b - a) * across)
                                 for a, b in zip(first, second))
        frames.append(frame)
    return frames


def pulse_frames(config):
    first = _colour(config, 0, (255, 0, 0))
    lattice = list(_lattice())
    furthest = max(math.hypot(x - _CENTRE_X, y - _CENTRE_Y)
                   for _, x, y in lattice) or 1.0
    frames = []
    for step in range(50):
        phase = step / 50 * math.pi * 2
        frame = [None] * LED_COUNT
        for index, x, y in lattice:
            reach = math.hypot(x - _CENTRE_X, y - _CENTRE_Y) / furthest * 10
            level = (math.sin(phase - reach) + 1) / 2
            frame[index] = tuple(_round(c * level) for c in first)
        frames.append(frame)
    return frames


GENERATORS = {
    MODE_CLOSE: close_frames,
    MODE_SOLID: solid_frames,
    MODE_DIAGONAL_FLOW: diagonal_flow_frames,
    MODE_BREATH: breath_frames,
    MODE_GRADIENT: gradient_frames,
    MODE_WAVE_GRADIENT: wave_gradient_frames,
    MODE_RAINBOW: rainbow_frames,
    MODE_PULSE: pulse_frames,
}


def generate(config):
    """Fill `config.frames` for its mode. Returns the config.

    `MODE_DEFAULT` is the one mode that cannot be computed: Space Station does
    not generate it either, it reads the frames out of a `.dat` file its
    installer ships under `Configs/Charger/cd2/default/`. Nothing in this
    repository has that file and `tools/fetch-configs` does not fetch it, so
    the mode is rejected rather than silently producing something else.

    `MODE_CUSTOM` keeps whatever frames the caller supplied.
    """
    if config.mode == MODE_DEFAULT:
        raise ProtocolError(
            "the 'default' mode's frames come from a file Space Station "
            "installs, not from a calculation, and this repository does not "
            "have it")
    if config.mode == MODE_CUSTOM:
        return config
    generator = GENERATORS.get(config.mode)
    if generator is None:
        config.frames = solid_frames(config)
        return config
    config.frames = generator(config)
    return config


def apply(dock, config, wait=1.0):
    """Generate the frames for a config and upload the whole thing."""
    return write_led_config(dock, generate(config), wait=wait)
