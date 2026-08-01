# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Flydigi's `default_mapping_<DeviceType>.dat`, turned into a wire blob.

Space Station restores one profile by writing a factory config into the slot,
from a protobuf file it ships per model -- `Configs/Controller/<code>/default/
default_mapping_<DeviceType>.dat`, a `ControllerMappingConfigBeans` holding four
`ControllerMappingConfigBean`s under repeated field 1. This turns one of those
into the 840 bytes the pad is sent, which is how a model nobody here owns gets a
factory profile at all.

**It is checkable, and that is the whole design.** `flydigi/factory_config.py`
holds the Apex 5's factory blob *read off the pad*, so running this over
`default_mapping_128.dat` and comparing byte for byte tests every field mapping
at once -- a field written to the wrong offset, scaled wrongly, or skipped shows
up as a mismatch and cannot be quietly wrong. `tools/gen-factory-config --check`
is that test, and it is the gate a Vader 5 blob has to pass through before it is
emitted. Nothing here is trusted because it was transcribed carefully.

**Two things this deliberately does not do.** It reads protobuf without
`protobuf`, because the backend has no dependencies and a generator that needed
one would not run where the project does; and it reads the *wire* format by
field number rather than a compiled schema, because the field numbers are
already documented in `docs/findings-profile-blob.md` and a `.proto` transcribed
by hand would be a second thing to get wrong.

The emitter is `MappingConfigParserV30.ParseConfigToArray` plus
`MappingConfigParserV31.ParseConfigToArray`, in that order, over a buffer filled
with 0xFF -- their `ParseConfigToArray(config, perPkgCount)`.
`MappingConfigParserV32` is empty, so a v3.2 profile is built by exactly the
same two passes as a v3.1 one, minus the two regions gated on the version.
"""
import struct

# -- the protobuf wire format, as much of it as these files use ---------------
#
# Three wire types appear: varint (0), length-delimited (2) and nothing else.
# A repeated scalar is *packed*, so it arrives as wire type 2 whose payload is a
# run of varints -- `Points`, `EnableKey`, `OldLedConfig` and `Lunpan` are all
# that shape, and reading one as a nested message is the mistake to avoid.

WIRE_VARINT, WIRE_64, WIRE_BYTES, WIRE_32 = 0, 1, 2, 5


def _varint(buf, i):
    out = shift = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[i]
        i += 1
        out |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return out, i
        shift += 7


def parse(buf):
    """A message as {field number: [values]}. Values are ints or bytes.

    Repeated fields keep every occurrence, in order. Nothing is decoded beyond
    the wire format: what a field *means* is the emitter's business, which is
    what keeps this half schema-free.
    """
    out = {}
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == WIRE_VARINT:
            value, i = _varint(buf, i)
        elif wire == WIRE_BYTES:
            length, i = _varint(buf, i)
            if i + length > len(buf):
                raise ValueError(f"field {number} runs past the end")
            value, i = buf[i : i + length], i + length
        elif wire == WIRE_32:
            value, i = int.from_bytes(buf[i : i + 4], "little"), i + 4
        elif wire == WIRE_64:
            value, i = int.from_bytes(buf[i : i + 8], "little"), i + 8
        else:
            raise ValueError(f"field {number} has wire type {wire}")
        out.setdefault(number, []).append(value)
    return out


def one(msg, number, default=0):
    """A scalar field, or `default` when it is absent.

    Absent and zero are the same thing on the wire for proto3 without explicit
    presence, so a default of 0 is not a guess -- it is what the reference's own
    generated property returns.
    """
    values = msg.get(number)
    return default if not values else values[-1]


def has(msg, number):
    """Whether a field was written at all. For the `HasX` tests the parser does."""
    return bool(msg.get(number))


def sub(msg, number):
    """A nested message, parsed. An absent one is an empty message.

    Empty rather than None because the reference reads through absent
    sub-messages freely -- C# gives them a default instance -- so every `sub`
    chain here has to keep working the same way.
    """
    values = msg.get(number)
    return parse(values[-1]) if values else {}


def subs(msg, number):
    """Every occurrence of a repeated message field, parsed."""
    return [parse(raw) for raw in msg.get(number, ())]


def packed(msg, number):
    """A packed repeated scalar as a list of ints.

    Handles the unpacked encoding too -- one tag per value -- because proto3
    only *defaults* to packed and a file written by an older library would be
    accepted by the reference either way.
    """
    out = []
    for value in msg.get(number, ()):
        if isinstance(value, int):
            out.append(value)
            continue
        i = 0
        while i < len(value):
            item, i = _varint(value, i)
            out.append(item)
    return out


def text(msg, number):
    values = msg.get(number)
    if not values or not isinstance(values[-1], (bytes, bytearray)):
        return ""
    return bytes(values[-1]).decode("utf-8", "replace")


# -- the beans, by field number -----------------------------------------------
#
# From the `GeneratedClrTypeInfo` table in `Flydigi.SharedResources`, where the
# name list is in field-number order. Only what the blob needs is named:
# `LedConfigBean` is field 11 of a config bean and is not in the blob at all --
# it goes out over commands 168/169 -- which is why the biggest sub-message in
# the file is also the one this ignores.

CFG_ID, PROTO_VERSION, PACKAGE_COUNT, DATA_VERSION, TITLE = 1, 2, 3, 4, 5
JOYSTICK, KEYS, VIBRATION, TRIGGER, MOTION = 6, 7, 8, 9, 10
LED, MACROS, OLD_LED, LUNPAN = 11, 12, 13, 14

# JoystickMapType / KeyMapType / MotionMapType, from the enums of those names.
JOYSTICK_MAP_JOYSTICK = 0
KEY_MAP_KEY, KEY_MAP_CONTINUOUS, KEY_MAP_MACRO, KEY_MAP_MULTI, KEY_MAP_KEYBOARD = (
    0, 1, 2, 3, 4)
MOTION_MAP_MOUSE = 3

BLOB_LEN = 840
FILL = 0xFF


def beans(data):
    """Every config bean in a `.dat` file, in slot order. Four, in practice."""
    return [parse(raw) for raw in parse(bytes(data)).get(1, ())]


def blob_from_bean(bean):
    """One config bean as the 840 bytes the pad is written.

    `ParseConfigToArray` over an 0xFF-filled buffer: V30 for everything, then
    V31 for the tail at 790, and V32 for nothing at all because their V32
    parser has empty bodies.
    """
    proto = one(bean, PROTO_VERSION)
    minor = proto & 0xF
    data = bytearray([FILL] * BLOB_LEN)

    # -- V30 ---------------------------------------------------------------
    for index, value in enumerate(packed(bean, OLD_LED)):
        data[3 + index] = value & 0xFF
    for index, value in enumerate(packed(bean, LUNPAN)[:2]):
        data[183 + index] = value & 0xFF
    struct.pack_into("<H", data, 0, proto & 0xFFFF)
    data[2] = one(bean, PACKAGE_COUNT) & 0xFF
    struct.pack_into("<H", data, 225, one(bean, DATA_VERSION) & 0xFFFF)
    # UTF-16LE and truncated to twenty bytes, not to ten characters -- their
    # `Encoding.Unicode.GetBytes` then `Take(20)`, which would cut a surrogate
    # pair in half and is copied rather than corrected.
    title = text(bean, TITLE).encode("utf-16-le")[:20]
    data[770 : 770 + len(title)] = title

    keys = _keys_to_array(sub(bean, KEYS))
    data[13 : 13 + len(keys)] = keys
    data[145:154] = _vibration_to_array(sub(bean, VIBRATION))
    data[109:123] = _joystick_to_array(sub(bean, JOYSTICK), proto < 769)
    travel, force, motors = _trigger_to_array(sub(bean, TRIGGER))
    data[123:137] = travel
    data[137:145] = _motion_to_array(sub(bean, MOTION))
    data[154:183] = motors
    data[185:225] = force
    if minor < 2:
        data[230:768] = _macros_to_array(sub(bean, MACROS), proto)

    # -- V31, for anything from 3.1 on -------------------------------------
    if minor >= 1:
        data[790:814] = _joystick_extra(sub(bean, JOYSTICK))
        # The one region v3.2 drops. Their own gate, and the reason a v3.2 blob
        # is not simply a v3.1 blob with a different version word.
        if proto < 770:
            data[820:825] = _macro_extra(sub(bean, MACROS))
        data[830:836] = _motion_extra(sub(bean, MOTION))
    return data


def _keys_to_array(bean):
    """The key table: three bytes per entry, `ParseKeyConfigToArray`."""
    entries = subs(bean, 1)
    out = bytearray([FILL] * (len(entries) * 3))
    for index, key in enumerate(entries):
        at = index * 3
        map_type = one(key, 2)
        if map_type == KEY_MAP_CONTINUOUS:
            continuous = sub(key, 4)
            out[at] = one(continuous, 1) & 0xFF
            out[at + 1] = one(continuous, 2) & 0xFF
            out[at + 2] = one(continuous, 3) & 0xFF
            continue
        map_key = sub(key, 3)
        if map_type == KEY_MAP_MACRO:
            out[at] = 32
        elif map_type == KEY_MAP_MULTI or has(map_key, 2):
            # 254 for both, with no key code anywhere in the blob: keyboard and
            # multi-function bindings are injected host-side, which is why this
            # project does not offer them at all.
            out[at] = 254
        elif one(map_key, 1) == index:
            # A key mapped to itself is stored as 255, not as its own id.
            out[at] = FILL
        else:
            out[at] = one(map_key, 1) & 0xFF
        out[at + 1] = 0
        out[at + 2] = 0
    return out


def _vibration_to_array(bean):
    """The grip motors at 145. Enable is **inverted**: 0 is on, 255 is off."""
    out = bytearray([FILL] * 9)
    out[0] = 0 if one(bean, 1) else FILL
    for side in range(2):
        item = sub(bean, 2 + side)
        at = side * 4
        out[at + 1] = 0 if one(item, 1) else FILL
        out[at + 2] = one(item, 2) & 0xFF
        out[at + 3] = one(item, 3) & 0xFF
        out[at + 4] = one(item, 4) & 0xFF
    return out


def _joystick_to_array(bean, old_protocol=False):
    """The stick curves at 109. Two entries of seven.

    The interior breakpoints are **recomputed against the centre** rather than
    written across, which is `CalculatePoint`: a point stored 0..127 is taken to
    percent, offset by the dead zone, and taken back. So the bytes in the blob
    are not the bytes in the bean, and a translator that copied them would be
    wrong in a way only a comparison against real hardware would catch.
    """
    out = bytearray([FILL] * 14)
    for side in range(2):
        param = sub(bean, 1 + side)
        at = side * 7
        stick = sub(param, 2)
        sensitivity = sub(stick, 4)
        out[at] = one(sensitivity, 1) & 0xFF
        centre = one(stick, 2) & 0xFF
        x1, y1 = _curve_point(sub(sensitivity, 2), centre)
        x2, y2 = _curve_point(sub(sensitivity, 3), centre)
        end = one(param, 5)
        if old_protocol:
            centre = (centre * 127 // 100) & 0xFF
            out[at + 1] = centre if one(param, 1) == JOYSTICK_MAP_JOYSTICK else 127
            x1 = x1 * (end - one(stick, 2)) / 100.0
            x2 = x2 * (end - one(stick, 2)) / 100.0
        else:
            # 127 in the centre byte is the sentinel for "not acting as a
            # stick", which is why it is not a dead zone value.
            out[at + 1] = centre if one(param, 1) == JOYSTICK_MAP_JOYSTICK else 127
        out[at + 2] = int(x1) & 0xFF
        out[at + 3] = int(y1) & 0xFF
        out[at + 4] = int(x2) & 0xFF
        out[at + 5] = int(y2) & 0xFF
        out[at + 6] = end & 0xFF
    return out


def _curve_point(point, centre):
    """`CalculatePoint`: 0..127 -> percent -> offset by the centre -> 0..127.

    Truncated on the way out by the cast to byte, where Space Station's own
    Electron renderer rounds -- the same divergence recorded for the nine-point
    bank in docs/findings-profile-blob.md.
    """
    x = one(point, 1) * 100.0 / 127.0
    return (centre + (100.0 - centre) * x / 100.0) * 127.0 / 100.0, one(point, 2)


def _trigger_to_array(bean):
    """(travel at 123, force triggers at 185, trigger motors at 154)."""
    travel = bytearray([FILL] * 14)
    force = bytearray([FILL] * 40)
    motors = bytearray([FILL] * 29)
    for side in range(2):
        trigger = sub(bean, 1 + side)
        at = side * 7
        travel[at] = one(trigger, 5) & 0xFF          # Type
        travel[at + 1] = one(trigger, 1) & 0xFF      # Zero
        for index, field in enumerate((6, 7)):       # Point1, Point2
            point = sub(trigger, field)
            travel[at + 2 + index * 2] = one(point, 1) & 0xFF
            travel[at + 3 + index * 2] = one(point, 2) & 0xFF
        travel[at + 6] = one(trigger, 2) & 0xFF      # End

        adapter = sub(trigger, 4)
        bind = sub(adapter, 2)
        at = side * 20
        effect = one(adapter, 1)
        force[at] = effect & 0xFF
        # bindType 2 for the Vibration effect and 0 for every other, which is
        # the `bindType` recorded in PROGRESS.md as always 2 on the wire.
        force[at + 1] = 2 if effect == 5 else 0
        force[at + 2] = one(bind, 2) & 0xFF          # Filter
        force[at + 3] = one(bind, 3) & 0xFF          # Scale
        for index, value in enumerate(packed(bind, 4)[:5]):
            force[at + 4 + index] = value & 0xFF
        force[at + 9] = one(adapter, 3) & 0xFF       # MixedBorder
        for index, value in enumerate(packed(adapter, 4)[:10]):
            force[at + 10 + index] = value & 0xFF

        vibration = sub(trigger, 3)
        if side == 0:
            # One enable byte for both triggers, written only on the left pass,
            # and inverted the other way round from the grip block above: 0 is
            # on there, and 0 is on here too, but the *test* is negated.
            motors[0] = 0 if one(vibration, 1) else 1
        for index, field in enumerate((2, 3)):       # Linear, Micro
            typed = sub(vibration, field)
            base = side * 14 + 1 + index * 7
            for offset in range(7):
                motors[base + offset] = one(typed, 1 + offset) & 0xFF
    return travel, force, motors


def _motion_to_array(bean):
    """The gyro block at 137. Eight bytes, `ParseMotionConfigToArray`."""
    out = bytearray([FILL] * 8)
    mapping_type = one(bean, 2)
    joystick = sub(bean, 3)
    out[0] = mapping_type & 0xFF
    out[2] = one(joystick, 1) & 0xFF                 # EnableType
    # One sensitivity in the bean, written to both axes -- so the pad's two
    # differing factory values cannot be reproduced by a round trip through
    # Flydigi's own software, only preserved by not touching them.
    out[4] = out[5] = one(joystick, 3) & 0xFF
    out[6] = one(bean, 1) & 0xFF                     # UseMode
    keys = packed(joystick, 2)
    if mapping_type == MOTION_MAP_MOUSE:
        out[1] = FILL
        out[3] = 0
        out[7] = FILL
    else:
        out[1] = (keys[0] if keys else 0) & 0xFF
        out[3] = one(joystick, 4) & 0xFF             # DeadZone
        out[7] = (keys[1] if len(keys) > 1 else 0) & 0xFF
    return out


def _macros_to_array(bean, proto):
    """The v3.1 macro page at 230. Absent from v3.2 -- see `mapping.MacroStore`."""
    out = bytearray([FILL] * 538)
    slots = 10 if proto >= 770 else 5
    tick = 1 if proto >= 770 else 10
    macros = subs(bean, 3)
    page = bytearray(1 + slots)
    page[0] = len(macros) & 0xFF
    running = 0
    for index, macro in enumerate(macros[:-1]):
        running += len(subs(macro, 4)) + 1
        page[index + 2] = running & 0xFF
    for macro in macros:
        actions = subs(macro, 4)
        page += bytes([one(macro, 1) & 0xFF, one(macro, 2) & 0xFF,
                       (one(macro, 2) >> 8) & 0xFF, one(macro, 3) & 0xFF])
        when = 0
        for action in actions:
            when += one(action, 2) // tick
            page += bytes([when & 0xFF, (when >> 8) & 0xFF,
                           one(action, 1) & 0xFF, one(action, 3) & 0xFF])
    out[: len(page)] = page
    return out


def _macro_extra(bean):
    """The five repeat intervals at 820, stored as milliseconds over ten."""
    out = bytearray([FILL] * 5)
    for index, value in enumerate(packed(bean, 2)[:5]):
        out[index] = (value // 10) & 0xFF
    return out


def _joystick_extra(bean):
    """The nine-point bank, circularity and edge at 790. Two entries of twelve.

    This is the block the pad actually plays -- the polyline at 109 is the
    host-side source form and the firmware reads nothing in it, measured with
    `tools/joystick-curve-probe`.
    """
    out = bytearray([FILL] * 24)
    for side in range(2):
        param = sub(bean, 1 + side)
        stick = sub(param, 2)
        sensitivity = sub(stick, 4)
        at = side * 12
        out[at] = one(sensitivity, 1) & 0xFF
        for index, value in enumerate(packed(sensitivity, 4)[:9]):
            out[at + 1 + index] = value & 0xFF
        out[at + 10] = one(stick, 1) & 0xFF          # CircularityType
        out[at + 11] = one(stick, 3) & 0xFF          # Edge
    return out


def _motion_extra(bean):
    """The gyro's response curve at 830. Six bytes, and inert on this pad."""
    smoothness = sub(sub(bean, 3), 5)
    point1, point2 = sub(smoothness, 2), sub(smoothness, 3)
    return bytearray([one(smoothness, 1) & 0xFF,
                      one(point1, 1) & 0xFF, one(point1, 2) & 0xFF,
                      one(point2, 1) & 0xFF, one(point2, 2) & 0xFF,
                      one(smoothness, 4) & 0xFF])
