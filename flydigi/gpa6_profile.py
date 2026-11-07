# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Lossless edits to XMUL v1.8/v1.9 mappings, timings and response curves.

Edits touch only the selected activator entry, timing field or response
extension. Other sections, metadata and reserved bytes keep their exact bytes.
"""
import struct

from . import gpa6, mapping
from .gpa6_config import mapping_header, unwrap_blob, wrap_blob

BUTTONS = {name: i for i, name in enumerate((
    "a", "b", "x", "y", "lb", "rb", "back", "start", "ls", "rs",
    "up", "down", "left", "right", "c", "z", "m1", "m2", "m3", "m4", "m5", "m6"))}


def _layout(blob):
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
    extensions = []
    if position == len(data):
        return data, layers, extensions
    # The tail uses length-prefixed TLVs. Preserve unknown types, but require
    # structural validity rather than copying truncated configuration data.
    while position < len(data):
        start = position
        kind, size = take(2)
        take(size)
        extensions.append((kind, start, position))
        if kind == 255:
            if size or position != len(data):
                raise gpa6.ProtocolError("invalid XMUL end marker/trailing bytes")
            return data, layers, extensions
    raise gpa6.ProtocolError("missing XMUL end marker")


def _layers(blob):
    data, layers, _ = _layout(blob)
    return data, layers


def describe(blob):
    data, layers = _layers(blob)
    result = mapping_header(data)
    names = {value: key for key, value in BUTTONS.items()}
    result["decoded_scope"] = "button activators, timing and stick/trigger response; other sections preserved"
    result["layers"] = []
    for layer_id, _, _, entries in layers:
        mappings = {}
        for source, _, _, activators in entries:
            if len(activators) == 5 and activators[0:2] == b"\0\0" and activators[3:] == b"\0\0":
                mappings[names[source]] = names.get(activators[2], f"button:{activators[2]}")
            else:
                mappings[names[source]] = {"activators_hex": activators.hex()}
        result["layers"].append({"id": layer_id, "overrides": mappings})
    config = MappingConfig(blob)
    result["response"] = {}
    for control in ("stick", "trigger"):
        result["response"][control] = {}
        for side in mapping.SIDES:
            try:
                value = config.stick(side) if control == "stick" else config.trigger_curve(side)
            except gpa6.ProtocolError as exc:
                value = {"decode_error": str(exc)}
            result["response"][control][side] = value
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


def _integer(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer in 0..{maximum}")
    return value


def _point(value, maximum=255):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("a curve point requires two coordinates")
    return tuple(_integer(v, maximum, "curve coordinate") for v in value)


def _kind(control, side):
    if control not in ("stick", "trigger") or side not in mapping.SIDES:
        raise ValueError("response control must be stick/trigger and side left/right")
    return (1 if control == "stick" else 3) + mapping.SIDES.index(side)


def _decode_curve(raw):
    if raw[4] > 2:
        raise gpa6.ProtocolError("unsupported response midpoint count")
    return {"start": tuple(raw[:2]), "end": tuple(raw[2:4]),
            "points": [tuple(raw[i:i + 2]) for i in range(5, 5 + raw[4] * 2, 2)]}


def _patch_curve(raw, *, start=None, end=None, points=None):
    result = bytearray(raw)
    if start is not None:
        result[:2] = _point(start)
    if end is not None:
        result[2:4] = _point(end)
    if points is not None:
        if not isinstance(points, (tuple, list)):
            raise ValueError("curve midpoints must be a sequence of coordinate pairs")
        points = list(points)
        if len(points) > 2:
            raise ValueError("a response curve supports at most two midpoints")
        result[4] = len(points)
        for index, point in enumerate(points):
            result[5 + index * 2:7 + index * 2] = _point(point)
        # Unused slots and reserved bytes belong to the original profile.
    curve = _decode_curve(result)
    nodes = [curve["start"], *curve["points"], curve["end"]]
    if any(a[0] > b[0] for a, b in zip(nodes, nodes[1:])):
        raise ValueError("response points must be ordered within the input window")
    return result


def _preview_bank(curve):
    """Nine interpolated display samples in the legacy UI's 50..150 scale.

    XMUL stores the points themselves, never this bank. This is a preview of
    their polyline, not a measurement of the firmware's response.
    """
    nodes = [curve["start"], *curve["points"], curve["end"]]
    if any(a[0] > b[0] for a, b in zip(nodes, nodes[1:])):
        return []
    values = []
    for index in range(mapping.BANK_POINTS):
        x = 255 * index / (mapping.BANK_POINTS - 1)
        y = nodes[0][1]
        for (x0, y0), (x1, y1) in zip(nodes, nodes[1:]):
            if x < x0:
                break
            if x >= x1:
                y = y1
            else:
                y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
                break
        values.append(50 + int(y * 100 / 255))
    return values


class MappingConfig:
    """An editable XMUL profile, with the legacy profile's UI-facing methods.

    ``blob``, ``cfg_id`` and ``copy()`` follow mapping.MappingConfig. The stick
    facade uses its percentages, preset IDs and 0..127 relative control points;
    trigger travel uses 0..255. ``response_curve``/``set_response_curve`` expose
    exact absolute 0..255 coordinates for richer Apex 6 editors. Setters are
    atomic and touch only the selected TLV. This is not a legacy wire config:
    submit it through gpa6_config.edit_ram, never mapping.write_config.
    """

    def __init__(self, blob, cfg_id=None):
        _layout(blob)
        self.blob = bytearray(blob)
        self.cfg_id = cfg_id

    def copy(self):
        return MappingConfig(self.blob, self.cfg_id)

    def _response(self, control, side):
        kind = _kind(control, side)
        data, _, extensions = _layout(self.blob)
        matches = [(start, stop) for key, start, stop in extensions if key == kind]
        if len(matches) > 1:
            raise gpa6.ProtocolError("ambiguous duplicate response extension")
        if matches:
            start, stop = matches[0]
            raw = data[start + 2:stop]
            if len(raw) != (12 if control == "stick" else 11):
                raise gpa6.ProtocolError("unsupported response extension length")
        else:
            # Same absent-extension defaults as the vendor editor. An ordinary
            # read or an empty setter does not materialize these defaults.
            raw = bytes([0, 0, 255, 255, 0, 0, 0, 0, 0, 0, 0])
            if control == "stick":
                raw += b"\x04"
        _decode_curve(raw)
        return raw, bool(matches)

    def _store_response(self, control, side, raw):
        old, present = self._response(control, side)
        if raw == old:
            return
        kind = _kind(control, side)
        data, _, extensions = _layout(self.blob)
        if present:
            _, start, stop = next(item for item in extensions if item[0] == kind)
        else:
            start = stop = next((offset for key, offset, _ in extensions
                                if key > kind and key in (*range(1, 10), 32, 255)), len(data))
        replacement = bytes([kind, len(raw)]) + raw
        if not extensions:
            replacement += b"\xff\0"
        self.blob[:] = wrap_blob(data[:start] + replacement + data[stop:])

    def response_curve(self, control, side):
        raw, _ = self._response(control, side)
        return _decode_curve(raw)

    def set_response_curve(self, control, side, *, start=None, end=None, points=None):
        """Edit exact endpoints and zero, one or two absolute 0..255 midpoints.

        None preserves a field; points=[] explicitly clears active midpoints.
        Equal input positions represent a step. Output need not be monotonic.
        """
        raw, _ = self._response(control, side)
        if start is not None or end is not None or points is not None:
            self._store_response(control, side, _patch_curve(raw, start=start, end=end, points=points))

    def stick(self, side):
        raw, present = self._response("stick", side)
        curve = _decode_curve(raw)
        start, end, points = curve["start"], curve["end"], curve["points"]
        span = end[0] - start[0]
        relative = [(round((x - start[0]) * 127 / span) if span > 0 else 0,
                     round(y * 127 / 255)) for x, y in points]
        kind = mapping.CURVE_CUSTOM
        if start == (0, 0) and end == (255, 255):
            if all(x == y for x, y in points):
                kind = mapping.CURVE_DEFAULT
            else:
                for preset, nodes in mapping.STICK_PRESETS.items():
                    if nodes is not None and list(nodes) == relative:
                        kind = preset
                        break
        return {"type": kind, "center": round(start[0] * 100 / 255),
                "edge": round((255 - end[0]) * 100 / 255), "is_stick": True,
                "point1": relative[0] if relative else (63, 63),
                "point2": relative[1] if len(relative) > 1 else (127, 127),
                "end": 127, "circular": bool(raw[11] & 4),
                "invert_x": bool(raw[11] & 1), "invert_y": bool(raw[11] & 2),
                "bank": _preview_bank(curve), "bank_is_preview": True,
                "curve": curve, "present": present}

    def set_stick(self, side, curve_type=None, center=None, edge=None,
                  point1=None, point2=None, circular=None, *, invert_x=None, invert_y=None):
        raw, _ = self._response("stick", side)
        result = bytearray(raw)
        for mask, value in ((1, invert_x), (2, invert_y), (4, circular)):
            if value is not None:
                if type(value) is not bool:
                    raise ValueError("stick flags must be boolean")
                result[11] = (result[11] | mask) if value else (result[11] & ~mask)
        curve = _decode_curve(raw)
        start, end, points = curve["start"], curve["end"], curve["points"]
        if curve_type is not None:
            _integer(curve_type, mapping.CURVE_CUSTOM, "curve preset")
            if curve_type != mapping.CURVE_CUSTOM:
                start, end = (0, 0), (255, 255)
                points = ([] if curve_type == mapping.CURVE_DEFAULT else
                          [tuple(round(v * 255 / 127) for v in p)
                           for p in mapping.STICK_PRESETS[curve_type]])
        old_start, old_end = start, end
        if center is not None:
            _integer(center, 100, "center")
        if edge is not None:
            end = (255 - round(_integer(edge, 100, "edge") * 255 / 100), end[1])
        if center is not None and edge is not None and center + edge > 100:
            raise ValueError("center and edge cannot use more than 100 percent of travel")
        if center is not None:
            start = (min(round(center * 255 / 100), end[0]), start[1])
        if edge is not None:
            end = (max(end[0], start[0]), end[1])
        span = end[0] - start[0]
        old_span = old_end[0] - old_start[0]
        if (start, end) != (old_start, old_end):
            points = [(start[0] + round((x - old_start[0]) * span / old_span)
                       if old_span > 0 else start[0], y) for x, y in points]
        if point1 is not None or point2 is not None:
            while len(points) < 2:
                x, y = mapping.STICK_PRESETS[mapping.CURVE_DEFAULT][len(points)]
                points.append((start[0] + round(x * span / 127), round(y * 255 / 127)))
            for index, value in enumerate((point1, point2)):
                if value is not None:
                    x, y = _point(value, 127)
                    points[index] = (start[0] + round(x * span / 127), round(y * 255 / 127))
        if (start, end, points) != (curve["start"], curve["end"], curve["points"]):
            result = _patch_curve(result, start=start, end=end, points=points)
        self._store_response("stick", side, result)

    def trigger_curve(self, side):
        raw, present = self._response("trigger", side)
        curve = _decode_curve(raw)
        points = curve["points"]
        linear = curve["start"] == (0, 0) and curve["end"] == (255, 255) and all(x == y for x, y in points)
        return {"type": mapping.CURVE_DEFAULT if linear else mapping.CURVE_CUSTOM,
                "zero": curve["start"][0], "end": curve["end"][0],
                "point1": points[0] if points else curve["start"],
                "point2": points[1] if len(points) > 1 else curve["end"],
                "curve": curve, "present": present}

    def set_trigger_curve(self, side, zero=None, end=None, mirror_points=True):
        """Move the 0..255 travel window, using the legacy setter convention.

        The default produces a linear window. mirror_points=False preserves
        the native output endpoints/midpoints (and refuses stranded points).
        Use set_response_curve for an explicit nonlinear response.
        """
        raw, _ = self._response("trigger", side)
        if type(mirror_points) is not bool:
            raise ValueError("mirror_points must be boolean")
        if zero is None and end is None:
            return
        curve = _decode_curve(raw)
        low = curve["start"][0] if zero is None else _integer(zero, 255, "zero")
        high = curve["end"][0] if end is None else _integer(end, 255, "end")
        low, high = sorted((low, high))
        if (low, high) == (curve["start"][0], curve["end"][0]):
            return
        self._store_response("trigger", side, _patch_curve(raw,
            start=(low, 0 if mirror_points else curve["start"][1]),
            end=(high, 255 if mirror_points else curve["end"][1]),
            points=[] if mirror_points else None))
