# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Lossless edits to XMUL v1.8/v1.9 button mappings and timing fields.

Only the selected activator entry is replaced. Macro definitions, zones,
curves, metadata, reserved bytes and other layers keep their exact bytes.
"""
import struct

from . import gpa6
from .gpa6_config import mapping_header, unwrap_blob, wrap_blob

BUTTONS = {name: i for i, name in enumerate((
    "a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs",
    "up", "down", "left", "right", "c", "z", "m1", "m2", "m3", "m4", "m5", "m6"))}


def _layers(blob):
    mapping_header(blob)
    data = unwrap_blob(blob, b"XMUL")
    position = 28

    def take(count):
        nonlocal position
        if position + count > len(data):
            raise gpa6.ProtocolError("truncated XMUL layer")
        value = data[position:position + count]
        position += count
        return value

    layers = []
    seen = set()
    if not 1 <= data[24] <= 4:
        raise gpa6.ProtocolError("invalid XMUL layer count")
    for _ in range(data[24]):
        start = position
        header = take(8)
        layer_id, maps, macros = header[0], *struct.unpack_from("<HH", header, 2)
        if layer_id > 3 or layer_id in seen:
            raise gpa6.ProtocolError("invalid or repeated XMUL layer ID")
        seen.add(layer_id)
        entries = []
        sources = set()
        for _ in range(maps):
            entry_start = position
            entry = take(4)
            source, count = entry[:2]
            if source > 21 or source in sources or count > 5:
                raise gpa6.ProtocolError("unsupported XMUL activator entry")
            sources.add(source)
            activators = take(count * 5)  # type:u8 and four-byte target
            entries.append((source, entry_start, position, activators))
        entries_end = position
        for _ in range(macros):
            header = take(6)
            take(header[4] * 7)  # action, target[4], timestamp:u16
        for _ in range(2):
            present = take(1)[0]
            if present not in (0, 1):
                raise gpa6.ProtocolError("unsupported trigger-zone flag")
            if present:
                count = take(1)[0]
                if count > 4:
                    raise gpa6.ProtocolError("invalid trigger-zone count")
                take(count * 6)
        for _ in range(2):
            present = take(1)[0]
            if present not in (0, 1):
                raise gpa6.ProtocolError("unsupported stick-zone flag")
            if present:
                mode, count = take(2)
                if mode > 2 or count > 8:
                    raise gpa6.ProtocolError("invalid stick-zone header")
                take(8 * 7)
        layers.append((layer_id, start, entries_end, entries))
    # The vendor also accepts a mapping with no optional TLV tail (v1.8's
    # encoder omits it when there are no curves or other extensions).
    if position == len(data):
        return data, layers
    # The tail uses length-prefixed TLVs. Preserve unknown types, but require
    # structural validity rather than copying truncated configuration data.
    while position < len(data):
        kind, size = take(2)
        take(size)
        if kind == 255:
            if size or position != len(data):
                raise gpa6.ProtocolError("invalid XMUL end marker/trailing bytes")
            return data, layers
    raise gpa6.ProtocolError("missing XMUL end marker")


def describe(blob):
    data, layers = _layers(blob)
    result = mapping_header(data)
    names = {value: key for key, value in BUTTONS.items()}
    result["decoded_scope"] = "button activators and timing; other sections preserved"
    result["layers"] = []
    for layer_id, _, _, entries in layers:
        mappings = {}
        for source, _, _, activators in entries:
            if len(activators) == 5 and activators[0:2] == b"\0\0" and activators[3:] == b"\0\0":
                mappings[names[source]] = names.get(activators[2], f"button:{activators[2]}")
            else:
                mappings[names[source]] = {"activators_hex": activators.hex()}
        result["layers"].append({"id": layer_id, "overrides": mappings})
    return result


def remap(blob, source, target, *, layer=0):
    if source not in BUTTONS or target not in (*BUTTONS, "default"):
        raise ValueError("unknown source or target button")
    data, layers = _layers(blob)
    found = next((item for item in layers if item[0] == layer), None)
    if found is None:
        raise ValueError("selected mapping layer does not exist")
    _, header, end, entries = found
    source_id = BUTTONS[source]
    old = next((entry for entry in entries if entry[0] == source_id), None)
    replacement = (b"" if target == "default" else
                   bytes([source_id, 1, 0, 0, 0, 0, BUTTONS[target], 0, 0]))
    start, stop = (old[1], old[2]) if old else (end, end)
    result = bytearray(data[:start] + replacement + data[stop:])
    count = len(entries) - bool(old) + bool(replacement)
    struct.pack_into("<H", result, header + 2, count)
    _layers(result)
    return wrap_blob(result)


def timing(blob, *, long_ms=None, double_ms=None):
    data, _ = _layers(blob)
    result = bytearray(data)
    for offset, value in ((12, long_ms), (14, double_ms)):
        if value is not None:
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError("press timings must be integers in 1..65535 ms")
            struct.pack_into("<H", result, offset, value)
    return wrap_blob(result)
