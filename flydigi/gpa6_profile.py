# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Lossless XMUL v1.8/v1.9 mappings, macros, zones, gyro, timings and response curves.

Edits touch the selected activator, macro, gyro, timing, response or zone section,
or append an empty layer. Macro deletion also removes its bindings. Other
sections, metadata and reserved bytes keep their exact bytes.
"""
import struct

from . import gpa6, gpa6_gyro, gpa6_macros, mapping
from .gpa6_config import mapping_header, unwrap_blob, wrap_blob
from .gpa6_actions import ACTIVATORS, BUTTONS, decode_target, encode_target

ANGLE_MODES = ("4way", "8way", "4way_overlap")
DIRECTIONS = ("up", "down", "left", "right", "up_left", "up_right", "down_left", "down_right")


def _layout(blob, *, zone_sections=None, macro_sections=None):
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
        records = []
        for _ in range(macros):
            record_start = position
            header = take(6)
            take(header[4] * 7)  # action, target[4], timestamp:u16
            records.append((header[0], record_start, position))
        if macro_sections is not None:
            macro_sections[layer_id] = records
        for side in mapping.SIDES:
            zone_start = position
            present = take(1)[0]
            if present not in (0, 1):
                raise gpa6.ProtocolError("unsupported trigger-zone flag")
            if present:
                count = take(1)[0]
                if count > 4:
                    raise gpa6.ProtocolError("invalid trigger-zone count")
                take(count * 6)
            if zone_sections is not None:
                zone_sections[(layer_id, "trigger", side)] = (zone_start, position)
        for side in mapping.SIDES:
            zone_start = position
            present = take(1)[0]
            if present not in (0, 1):
                raise gpa6.ProtocolError("unsupported stick-zone flag")
            if present:
                mode, count = take(2)
                if mode > 2 or count > 8:
                    raise gpa6.ProtocolError("invalid stick-zone header")
                take(8 * 7)
            if zone_sections is not None:
                zone_sections[(layer_id, "stick", side)] = (zone_start, position)
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
    result["decoded_scope"] = "button activators, macros, zones, gyro, timing and stick/trigger response; other sections preserved"
    result["layers"] = []
    config = MappingConfig(blob)
    for layer_id, _, _, entries in layers:
        mappings = {}
        for source, _, _, activators in entries:
            if len(activators) == 5 and activators[0:2] == b"\0\0" and activators[3:] == b"\0\0":
                mappings[names[source]] = names.get(activators[2], f"button:{activators[2]}")
            else:
                mappings[names[source]] = {"activators_hex": activators.hex(),
                                           "activators": _decode_activators(activators)}
        try:
            macros = config.macros(layer=layer_id)
        except gpa6.ProtocolError as exc:
            macros = {"decode_error": str(exc)}
        result["layers"].append({"id": layer_id, "overrides": mappings,
            "macros": macros,
            "trigger_zones": {side: config.trigger_zones(side, layer=layer_id) for side in mapping.SIDES},
            "stick_zones": {side: config.stick_zones(side, layer=layer_id) for side in mapping.SIDES}})
    result["response"] = {}
    for control in ("stick", "trigger"):
        result["response"][control] = {}
        for side in mapping.SIDES:
            try:
                value = config.stick(side) if control == "stick" else config.trigger_curve(side)
            except gpa6.ProtocolError as exc:
                value = {"decode_error": str(exc)}
            result["response"][control][side] = value
    try:
        result["motion"] = {**config.motion(), "curve": config.motion_curve()}
    except gpa6.ProtocolError as exc:
        result["motion"] = {"decode_error": str(exc)}
    return result


def remap(blob, source, target, *, layer=0):
    if source not in BUTTONS or target not in (*BUTTONS, "default"):
        raise ValueError("unknown source or target button")
    config = MappingConfig(blob)
    config.set_activators(source, None if target == "default" else
                          [{"type": "simple", "target": target}], layer=layer)
    return bytes(config.blob)


def _decode_activators(raw):
    return [{"type": ACTIVATORS[raw[i]] if raw[i] < len(ACTIVATORS) else raw[i],
             "target": decode_target(raw[i + 1:i + 5])} for i in range(0, len(raw), 5)]


def _selected_layer(blob, layer):
    _integer(layer, 3, "layer")
    data, layers = _layers(blob)
    found = next((item for item in layers if item[0] == layer), None)
    if found is None:
        raise ValueError("selected mapping layer does not exist")
    return data, layers, found


def _encode_layer_target(data, layers, selected, target, *, previous=None):
    raw = encode_target(target, previous=previous)
    if raw[0] in (20, 21) and raw[1] not in [item[0] for item in layers]:
        raise ValueError("target mapping layer does not exist; create it first")
    if raw[0] == 30:
        _, header, position, _ = selected
        macro_ids = []
        for _ in range(struct.unpack_from("<H", data, header + 4)[0]):
            macro_ids.append(data[position])
            position += 6 + data[position + 4] * 7
        if macro_ids.count(raw[1]) != 1:
            raise ValueError("target macro must exist uniquely in the selected layer")
    return raw


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
    exact absolute 0..255 coordinates for richer Apex 6 editors. Activators use
    the shared native action model; their setters touch only the chosen entry.
    Zone setters touch only the selected control, side and layer's zone section.
    Macro methods share the existing delay/event conventions with native IDs,
    layers and playback modes. Response setters touch only the selected TLV.
    All setters are atomic.
    This is not a legacy wire config:
    submit it through gpa6_config.edit_ram, never mapping.write_config.
    """

    def __init__(self, blob, cfg_id=None):
        _layout(blob)
        self.blob = bytearray(blob)
        self.cfg_id = cfg_id

    def copy(self):
        return MappingConfig(self.blob, self.cfg_id)

    def layer_ids(self):
        return [layer[0] for layer in _layers(self.blob)[1]]

    def _motion_record(self):
        data, _, extensions = _layout(self.blob)
        matches = [(a, b) for kind, a, b in extensions if kind == 5]
        if len(matches) > 1:
            raise gpa6.ProtocolError("ambiguous duplicate gyro extension")
        raw = data[matches[0][0] + 2:matches[0][1]] if matches else gpa6_gyro.DEFAULT
        gpa6_gyro.offsets(raw)
        return raw, matches

    def _store_motion(self, raw):
        old, matches = self._motion_record()
        if raw == old:
            return
        gpa6_gyro.offsets(raw)
        data, _, extensions = _layout(self.blob)
        if matches:
            start, stop = matches[0]
        else:
            start = stop = next((a for kind, a, _ in extensions
                                 if kind in (6, 7, 8, 9, 32, 255)), len(data))
        replacement = bytes([5, 24]) + raw + (b"" if extensions else b"\xff\0")
        self.blob[:] = wrap_blob(data[:start] + replacement + data[stop:])

    def motion(self):
        """Shared target/enable-type IDs and key names, with native gyro fields.

        Four native activation slots are reported; the web exposes the first
        two. Mixed activation types return enable_type=None. Legacy sensitivity,
        dead_zone and use_mode have no equivalent here and are not fabricated.
        This configuration is profile-wide, not a Shift-layer setting.
        """
        raw, matches = self._motion_record()
        return {**gpa6_gyro.decode(raw), "present": bool(matches)}

    def set_motion(self, target=None, enable_type=None, keys=None, *,
                   invert_x=None, invert_y=None, mouse_window_radius=None):
        """Edit the web gyro controls atomically; None preserves a field.

        Target changes use the web's first-use curve defaults (stick end 145,255;
        mouse end 255,51). Same target/off keeps the curve. The browser's separate
        per-target curve cache is not stored on the pad. Any explicit target
        selection resets axis routing to imu_x/imu_y, as the web does.
        keys is a primary key or a (primary, optional secondary) pair; slots 3/4
        survive. enable_type applies to every active slot, matching the web.
        """
        raw, _ = self._motion_record()
        result = bytearray(raw)
        curve, flags = gpa6_gyro.offsets(raw)
        if target is not None:
            target = gpa6_gyro.target_id(target)
            if target != raw[flags + 1] and target != mapping.MOTION_OFF:
                end = (255, 51) if target == mapping.MOTION_MOUSE else (145, 255)
                result[curve:curve + 11] = _patch_curve(raw[curve:curve + 11], start=(0, 0), end=end, points=[])
            result[flags + 1] = target
            result[flags + 10:flags + 12] = b"\x01\x02"
        if keys is not None:
            if isinstance(keys, (str, int)):
                keys = (keys, None)
            if not isinstance(keys, (tuple, list)) or len(keys) != 2:
                raise ValueError("gyro keys require a primary and optional secondary key")
            first, second = map(gpa6_gyro.key_id, keys)
            if first == 255:
                raise ValueError("a primary gyro activation key is required")
            if result[flags + 3] == 255:
                result[flags + 2] = 0
            result[flags + 3] = first
            if second != 255:
                result[flags + 4] = result[flags + 2]
            result[flags + 5] = second
        if enable_type is not None:
            activation = gpa6_gyro.activation_id(enable_type)
            for p in range(flags + 2, flags + 10, 2):
                if result[p + 1] != 255:
                    result[p] = activation
        for bit, value in ((1, invert_x), (2, invert_y)):
            if value is not None:
                if type(value) is not bool:
                    raise ValueError("gyro inversion flags must be boolean")
                result[flags] = (result[flags] & ~bit) | (bit if value else 0)
        if mouse_window_radius is not None:
            result[flags + 12] = _integer(mouse_window_radius, 255, "mouse window radius")
        self._store_motion(result)

    def motion_curve(self):
        """Native absolute 0..255 curve, using the shared response-curve shape."""
        raw, _ = self._motion_record()
        curve, _ = gpa6_gyro.offsets(raw)
        return _decode_curve(raw[curve:curve + 11])

    def set_motion_curve(self, *, start=None, end=None, points=None):
        """Edit native curve endpoints/midpoints; points=[] clears midpoints."""
        raw, _ = self._motion_record()
        if start is None and end is None and points is None:
            return
        curve, _ = gpa6_gyro.offsets(raw)
        result = bytearray(raw)
        result[curve:curve + 11] = _patch_curve(raw[curve:curve + 11], start=start, end=end, points=points)
        self._store_motion(result)

    @property
    def macro_limits(self):
        """Native limits: slots per layer, steps per macro, millisecond timing."""
        return gpa6_macros.LIMITS

    def _macro_layout(self, layer):
        _integer(layer, 3, "layer")
        sections = {}
        data, layers, extensions = _layout(self.blob, macro_sections=sections)
        selected = next((item for item in layers if item[0] == layer), None)
        if selected is None:
            raise ValueError("selected mapping layer does not exist")
        records = sections[layer]
        ids = [item[0] for item in records]
        if len(ids) != len(set(ids)):
            raise gpa6.ProtocolError("duplicate macro IDs in selected layer")
        return data, selected, records, extensions

    def macros(self, *, layer=0):
        """Definitions in storage order, using delay/event/key-or-target steps.

        Unlike legacy macros, these are identified by macro_id, not a source
        button. Bind definitions with set_activators or a zone target. Native
        playback modes are strings; legacy numeric playback modes do not apply.
        """
        data, _, records, extensions = self._macro_layout(layer)
        _, _, names = gpa6_macros.names(data, extensions)
        labels = {(a, b): raw.decode("utf-8", errors="replace") for a, b, raw in names}
        return [gpa6_macros.decode_record(data[start:end], labels.get((layer, macro_id)))
                for macro_id, start, end in records]

    def macro(self, macro_id, *, layer=0):
        _integer(macro_id, 255, "macro ID")
        return next((item for item in self.macros(layer=layer) if item["macro_id"] == macro_id), None)

    def set_macro(self, macro_id, steps=None, macro_type=None, interval=None, name=None, *, layer=0):
        """Create/update one definition atomically; None preserves each field.

        New definitions default to press_once, zero interval and no steps.
        Empty steps clear the body; an empty name clears its v1.9 label.
        Existing bindings and every unselected macro remain byte-exact.
        """
        _integer(macro_id, 255, "macro ID")
        data, selected, records, _ = self._macro_layout(layer)
        old = next((item for item in records if item[0] == macro_id), None)
        if old is None and len(records) >= self.macro_limits.slots:
            raise ValueError(f"all {self.macro_limits.slots} macro slots in this layer are taken")
        if macro_type is not None and macro_type not in gpa6_macros.MODES:
            raise ValueError("unsupported native macro playback mode")
        if interval is not None:
            _integer(interval, self.macro_limits.interval_max, "macro interval")
        raw = data[old[1]:old[2]] if old else None
        if raw is None or steps is not None or macro_type is not None:
            previous = gpa6_macros.decode_record(raw) if raw else None
            mode = macro_type if macro_type is not None else previous["type"] if previous else "press_once"
            gap = interval if interval is not None else previous["interval"] if previous else 0
            body = steps if steps is not None else previous["steps"] if previous else []
            replacement = gpa6_macros.encode_record(macro_id, body, mode, gap, previous=raw)
        else:
            replacement = bytearray(raw)
            if interval is not None:
                struct.pack_into("<H", replacement, 2, interval)
        start, end = old[1:] if old else ((records[-1][2] if records else selected[2]),) * 2
        result = bytearray(data[:start] + replacement + data[end:])
        if old is None:
            struct.pack_into("<H", result, selected[1] + 4, len(records) + 1)
        if name is not None:
            result = gpa6_macros.patch_name(result, _layout(result)[2], layer, macro_id, name)
        _layout(result)
        if result != data:
            self.blob[:] = wrap_blob(result)

    def clear_macro(self, macro_id, *, layer=0):
        """Delete a definition and its same-layer button/zone references.

        Remove only matching activators/zones; retain unrelated actions and
        reserved bytes. Nested macro references are refused rather than broken.
        """
        _integer(macro_id, 255, "macro ID")
        data, selected, records, _ = self._macro_layout(layer)
        old = next((item for item in records if item[0] == macro_id), None)
        if old is None:
            return
        def references(raw):
            return raw[:2] == bytes([30, macro_id])
        for other_id, start, end in records:
            if other_id != macro_id and any(references(data[p + 1:p + 5]) for p in range(start + 6, end, 7)):
                raise ValueError("macro is referenced by another macro; edit that definition first")
        edits = [(old[1], old[2], b"")]
        removed_entries = 0
        for _, start, end, actions in selected[3]:
            kept = [actions[p:p + 5] for p in range(0, len(actions), 5) if not references(actions[p + 1:p + 5])]
            if len(kept) == len(actions) // 5:
                continue
            if kept:
                replacement = bytes([data[start], len(kept)]) + data[start + 2:start + 4] + b"".join(kept)
            else:
                replacement = b""
                removed_entries += 1
            edits.append((start, end, replacement))
        zones = {}
        _layout(data, zone_sections=zones)
        for (zone_layer, kind, _), (start, end) in zones.items():
            if zone_layer != layer or not data[start]:
                continue
            prefix, size, target_at = (2, 6, 2) if kind == "trigger" else (3, 7, 3)
            count = data[start + prefix - 1]
            active = [data[p:p + size] for p in range(start + prefix, start + prefix + count * size, size)]
            kept = [record for record in active if not references(record[target_at:target_at + 4])]
            if len(kept) == count:
                continue
            if not kept:
                replacement = b"\0"
            else:
                replacement = bytearray(data[start:end])
                replacement[prefix - 1] = len(kept)
                replacement[prefix:prefix + len(kept) * size] = b"".join(kept)
                if kind == "trigger":
                    del replacement[prefix + len(kept) * size:]
            edits.append((start, end, replacement))
        result = bytearray(data)
        for start, end, replacement in sorted(edits, reverse=True):
            result[start:end] = replacement
        struct.pack_into("<H", result, selected[1] + 2, len(selected[3]) - removed_entries)
        struct.pack_into("<H", result, selected[1] + 4, len(records) - 1)
        result = gpa6_macros.patch_name(result, _layout(result)[2], layer, macro_id, "")
        self.blob[:] = wrap_blob(result)

    def set_macros(self, macros, *, layer=0):
        """Replace a layer's definition list atomically using shared macro fields.

        Removing a definition also removes its bindings, as in clear_macro.
        Other layers, mappings, zones and extensions remain intact.
        """
        if not isinstance(macros, (list, tuple)) or len(macros) > self.macro_limits.slots:
            raise ValueError(f"a layer supports at most {self.macro_limits.slots} macros")
        ids = []
        for macro in macros:
            if (not isinstance(macro, dict) or "macro_id" not in macro
                    or set(macro) - {"macro_id", "steps", "type", "interval", "name"}):
                raise ValueError("invalid macro definition fields")
            ids.append(_integer(macro["macro_id"], 255, "macro ID"))
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate macro IDs")
        candidate = self.copy()
        for item in self.macros(layer=layer):
            if item["macro_id"] not in ids:
                candidate.clear_macro(item["macro_id"], layer=layer)
        for item in macros:
            previous = candidate.macro(item["macro_id"], layer=layer)
            # List-based UI edits often resubmit the decoded record. Only
            # changed fields should be rebuilt, especially opaque body/name data.
            changes = {key: value for key, value in item.items() if key != "macro_id"
                       and (previous is None or value != previous[key])}
            candidate.set_macro(item["macro_id"], changes.get("steps"), changes.get("type"),
                                changes.get("interval"), changes.get("name"), layer=layer)
        data, selected, records, _ = candidate._macro_layout(layer)
        by_id = {macro_id: data[start:end] for macro_id, start, end in records}
        start = selected[2]
        end = records[-1][2] if records else start
        result = data[:start] + b"".join(by_id[macro_id] for macro_id in ids) + data[end:]
        if result != unwrap_blob(self.blob, b"XMUL"):
            self.blob[:] = wrap_blob(result)

    def ensure_layer(self, layer):
        """Append an empty layer if absent, preserving all existing sections."""
        _integer(layer, 3, "layer")
        data, layers, extensions = _layout(self.blob)
        if layer in [item[0] for item in layers]:
            return
        position = extensions[0][1] if extensions else len(data)
        # Eight-byte header, then four absent zone sections; no maps or macros.
        result = bytearray(data[:position] + bytes([layer]) + bytes(11) + data[position:])
        result[24] += 1
        _layout(result)
        self.blob[:] = wrap_blob(result)

    def activators(self, source, *, layer=0):
        """Explicit activators, or None when the source has no override.

        An empty list represents a present entry with no actions. Unknown types
        remain inspectable; replacing that source is an explicit operation.
        """
        if source not in BUTTONS:
            raise ValueError("unknown source button")
        _, _, (_, _, _, entries) = _selected_layer(self.blob, layer)
        entry = next((entry for entry in entries if entry[0] == BUTTONS[source]), None)
        return _decode_activators(entry[3]) if entry else None

    def set_activators(self, source, activators, *, layer=0):
        """Atomically replace up to five actions; None removes the override.

        Target dictionaries use gpa6_actions' shared native action model. Layer
        and macro references must already exist. No other mapping is rewritten.
        """
        if source not in BUTTONS:
            raise ValueError("unknown source button")
        if activators is not None and (not isinstance(activators, (list, tuple)) or len(activators) > 5):
            raise ValueError("a button supports at most five activators")
        data, layers, selected = _selected_layer(self.blob, layer)
        _, header, end, entries = selected
        source_id = BUTTONS[source]
        old = next((entry for entry in entries if entry[0] == source_id), None)
        records = bytearray()
        for index, action in enumerate(activators or ()):
            if (not isinstance(action, dict) or set(action) != {"type", "target"}
                    or action["type"] not in ACTIVATORS):
                raise ValueError("an activator requires a supported type and target")
            previous = old[3][index * 5 + 1:index * 5 + 5] if old else None
            target = _encode_layer_target(data, layers, selected, action["target"], previous=previous)
            records.extend(bytes([ACTIVATORS.index(action["type"])]) + target)
        if activators is None:
            replacement = b""
        else:
            reserved = data[old[1] + 2:old[1] + 4] if old else b"\0\0"
            replacement = bytes([source_id, len(activators)]) + reserved + records
        start, stop = (old[1], old[2]) if old else (end, end)
        if data[start:stop] == replacement:
            return
        result = bytearray(data[:start] + replacement + data[stop:])
        struct.pack_into("<H", result, header + 2, len(entries) - bool(old) + (activators is not None))
        _layout(result)
        self.blob[:] = wrap_blob(result)

    def _zone_section(self, control, side, layer):
        if control not in ("trigger", "stick") or side not in mapping.SIDES:
            raise ValueError("zone control must be trigger/stick and side left/right")
        _integer(layer, 3, "layer")
        sections = {}
        data, layers, _ = _layout(self.blob, zone_sections=sections)
        selected = next((item for item in layers if item[0] == layer), None)
        if selected is None:
            raise ValueError("selected mapping layer does not exist")
        start, stop = sections[(layer, control, side)]
        return data, layers, selected, start, stop

    def trigger_zones(self, side, *, layer=0):
        """Explicit travel zones in native 0..255 units; [] when absent."""
        data, _, _, start, stop = self._zone_section("trigger", side, layer)
        raw = data[start:stop]
        return [] if not raw[0] else [
            {"start": raw[i], "stop": raw[i + 1], "target": decode_target(raw[i + 2:i + 6])}
            for i in range(2, len(raw), 6)]

    def stick_zones(self, side, *, layer=0):
        """Angle mode and active radius/direction zones, or None when absent.

        Unused records remain in the blob and are never exposed as active zones.
        Unknown directions and targets can be inspected without rewriting them.
        """
        data, _, _, start, stop = self._zone_section("stick", side, layer)
        raw = data[start:stop]
        if not raw[0]:
            return None
        return {"angle_mode": ANGLE_MODES[raw[1]], "zones": [
            {"start": raw[i], "stop": raw[i + 1],
             "dir": DIRECTIONS[raw[i + 2]] if raw[i + 2] < len(DIRECTIONS) else raw[i + 2],
             "target": decode_target(raw[i + 3:i + 7])}
            for i in range(3, 3 + raw[2] * 7, 7)]}

    def set_trigger_zones(self, side, zones, *, layer=0):
        """Replace up to four travel zones. [] removes the selected section."""
        self._set_zones("trigger", side, zones, layer=layer)

    def set_stick_zones(self, side, zones=None, *, angle_mode=None, layer=0):
        """Replace up to eight zones and/or change their shared angle mode.

        None preserves zones; [] removes the section. An omitted angle mode
        preserves it, or defaults to 4way for a new section. A mode-only edit
        needs an existing section and preserves every zone/target/padding byte.
        """
        self._set_zones("stick", side, zones, angle_mode=angle_mode, layer=layer)

    def _set_zones(self, control, side, zones, *, angle_mode=None, layer=0):
        data, layers, selected, start, stop = self._zone_section(control, side, layer)
        raw = data[start:stop]
        stick = control == "stick"
        if angle_mode is not None and angle_mode not in ANGLE_MODES:
            raise ValueError("unknown stick angle mode")
        if zones is None:
            if not stick:
                raise ValueError("trigger zones must be a sequence; use [] to clear")
            if angle_mode is None:
                return
            if not raw[0]:
                raise ValueError("create stick zones before changing their angle mode")
            replacement = bytearray(raw)
            replacement[1] = ANGLE_MODES.index(angle_mode)
        else:
            maximum = 8 if stick else 4
            if not isinstance(zones, (list, tuple)) or len(zones) > maximum:
                raise ValueError(f"{control} zones must be a sequence of at most {maximum} entries")
            if not zones:
                if angle_mode is not None:
                    raise ValueError("cannot set angle mode while clearing stick zones")
                replacement = b"\0"
            else:
                header_size, stride = (3, 7) if stick else (2, 6)
                if stick:
                    replacement = bytearray(raw if raw[0] else bytes(3 + 8 * 7))
                    mode = ANGLE_MODES.index(angle_mode) if angle_mode is not None else (raw[1] if raw[0] else 0)
                    replacement[:3] = bytes([1, mode, len(zones)])
                else:
                    replacement = bytearray([1, len(zones)]) + bytearray(len(zones) * 6)
                for index, zone in enumerate(zones):
                    fields = {"start", "stop", "target", "dir"} if stick else {"start", "stop", "target"}
                    if not isinstance(zone, dict) or set(zone) != fields:
                        raise ValueError("zone requires start, stop, target" + (" and dir" if stick else ""))
                    low = _integer(zone["start"], 255, "zone start")
                    high = _integer(zone["stop"], 255, "zone stop")
                    if low > high:
                        raise ValueError("zone start must not exceed stop")
                    prefix = bytes([low, high])
                    if stick:
                        if zone["dir"] not in DIRECTIONS:
                            raise ValueError("unknown stick zone direction")
                        prefix += bytes([DIRECTIONS.index(zone["dir"])])
                    offset = header_size + index * stride
                    previous = raw[offset + len(prefix):offset + stride] if raw[0] else None
                    target = _encode_layer_target(data, layers, selected, zone["target"], previous=previous)
                    replacement[offset:offset + stride] = prefix + target
        if data[start:stop] == replacement:
            return
        result = data[:start] + replacement + data[stop:]
        _layout(result)
        self.blob[:] = wrap_blob(result)

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

    def _vibration_record(self, control, side):
        if control not in ("grip", "trigger") or side not in mapping.SIDES:
            raise ValueError("vibration control must be grip/trigger and side left/right")
        kind = (8 if control == "grip" else 6) + mapping.SIDES.index(side)
        data, _, records = _layout(self.blob)
        matches = [(start, stop) for key, start, stop in records if key == kind]
        if len(matches) > 1:
            raise gpa6.ProtocolError("ambiguous duplicate vibration extension")
        raw = (data[matches[0][0] + 2:matches[0][1]] if matches else
               bytes([1 if control == "grip" else 0, 0, 0, 255, 255, 0, 0, 0, 0, 0, 0, 0]))
        if len(raw) != 12:
            raise gpa6.ProtocolError("unsupported vibration extension length")
        _decode_curve(raw[1:])
        return kind, raw, matches

    def vibration_curve(self, control, side):
        """Native vibration data; trigger enable lives separately in motor RAM.

        This is a codec view, not an additional freeform configuration control.
        The public haptics editor offers only the vendor's five strength levels.
        """
        _, raw, matches = self._vibration_record(control, side)
        return {"curve": _decode_curve(raw[1:]), "present": bool(matches),
                **({"enabled": bool(raw[0] & 1)} if control == "grip" else {})}

    def _set_vibration_curve(self, control, side, *, enabled=None, **curve):
        kind, raw, matches = self._vibration_record(control, side)
        result = bytearray(raw[:1]) + _patch_curve(raw[1:], **curve)
        if enabled is not None:
            if control != "grip" or type(enabled) is not bool:
                raise ValueError("only grip curves carry a boolean enable flag")
            result[0] = (result[0] & ~1) | int(enabled)
        if result == raw:
            return
        data, _, records = _layout(self.blob)
        if matches:
            start, stop = matches[0]
        else:
            start = stop = next((start for key, start, _ in records
                                if key > kind and key in (*range(1, 10), 32, 255)), len(data))
        replacement = bytes([kind, 12]) + result + (b"" if records else b"\xff\0")
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
