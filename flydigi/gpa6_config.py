# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Apex 6 snapshots, format inspection and bounded configuration edits.

Sources: pinned GPA6 web core/codec/contract in docs/findings-apex6.md.
Raw RAM bytes are retained even when a decoder does not know their format.
Editors preserve unrelated bytes and verify writes before an explicit save.
An A4 edit can already persist without that explicit save on tested firmware.
Snapshots remain read-only; arbitrary snapshot restoration is not supported.
"""
import binascii
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
import tempfile

from . import gpa6, identity

RAM_BLOCKS = {1: "global", 4: "lighting", 5: "mapping", 6: "motors"}


def wrap_blob(data):
    if len(data) + 4 > gpa6.MAX_RAM_SIZE:
        raise ValueError("configuration exceeds supported RAM size")
    return struct.pack("<HH", len(data) + 4, binascii.crc_hqx(data, 0xffff)) + bytes(data)


def unwrap_blob(data, magic):
    """XMUL/LGHT may be bare or prefixed by total length and CCITT CRC16."""
    if data.startswith(magic):
        return data
    if len(data) < 4 or data[4:8] != magic:
        raise gpa6.ProtocolError(f"missing {magic.decode()} magic")
    length, crc = struct.unpack_from("<HH", data)
    if length != len(data) or binascii.crc_hqx(data[4:], 0xffff) != crc:
        raise gpa6.ProtocolError("configuration envelope length/CRC16 mismatch")
    return data[4:]


def mapping_header(data):
    """Inspect v1.8/v1.9 headers only; mappings/macros/TLVs remain opaque."""
    data = unwrap_blob(data, b"XMUL")
    if len(data) < 28:
        raise gpa6.ProtocolError("truncated XMUL header")
    version = struct.unpack_from("<I", data, 4)[0]
    if version not in (8, 9):
        raise gpa6.ProtocolError(f"unsupported XMUL version {version}")
    if data[8] != 1 or data[20] != 0:
        raise gpa6.ProtocolError("unsupported XMUL slot count/ID")
    long_ms, double_ms = struct.unpack_from("<HH", data, 12)
    return {"version": f"1.{version}", "layer_count": data[24],
            "long_press_ms": long_ms, "double_press_ms": double_ms,
            "decoded_scope": "header only"}


def lighting_config(data):
    """Decode LGHT v2, including both checksums and the six known base modes.

    This is a strict reader: unknown tails/flags are left opaque in snapshots
    instead of being silently discarded by a future configuration writer.
    """
    data = unwrap_blob(data, b"LGHT")
    if len(data) < 14:
        raise gpa6.ProtocolError("truncated LGHT header")
    version, length, checksum = struct.unpack_from("<HIH", data, 4)
    if version != 2:
        raise gpa6.ProtocolError(f"unsupported LGHT version {version}")
    if checksum != sum(data[:10]) & 0xffff or len(data) != length + 14:
        raise gpa6.ProtocolError("LGHT header checksum/length mismatch")
    payload = data[12:-2]
    if struct.unpack_from("<H", data, len(data) - 2)[0] != sum(payload) & 0xffff:
        raise gpa6.ProtocolError("LGHT data checksum mismatch")
    if not payload:
        raise gpa6.ProtocolError("missing LGHT mode")
    mode = payload[0]
    if mode == 0:
        end, config = 1, None
    elif mode == 1 and len(payload) >= 5:
        end = 5
        config = {"color": dict(zip(("r", "g", "b"), payload[1:4])),
                  "brightness": payload[4]}
    elif mode in (2, 3) and len(payload) >= 4:
        count = payload[3]
        end = 4 + 3 * count
        if count > 8 or len(payload) < end:
            raise gpa6.ProtocolError("invalid LGHT color count/length")
        config = {"brightness": payload[1], "speed": payload[2],
                  "colors": [dict(zip(("r", "g", "b"), payload[i:i + 3]))
                             for i in range(4, end, 3)]}
    elif mode in (4, 5) and len(payload) >= 3:
        end, config = 3, {"brightness": payload[1], "speed": payload[2]}
    else:
        raise gpa6.ProtocolError("unknown or truncated LGHT mode")
    if len(payload) not in (end, end + 1) or (len(payload) > end and payload[end] > 1):
        raise gpa6.ProtocolError("unsupported LGHT trailing data/flags")
    return {"version": version, "base": {"mode": mode, "config": config},
            "vibration": bool(payload[end]) if len(payload) > end else False}


def global_config(data, version):
    """Inspect the 80-byte global payload used by the pinned v22 client."""
    if version != 22 or len(data) != 82:
        raise gpa6.ProtocolError("unsupported global format version/length")
    p = data[2:82]  # web core tn strips the two-byte RAM prefix
    return {"display_name": p[7:39].split(b"\0", 1)[0].decode("utf-8", "replace"),
            "sleep_time": p[3], "quick_switch": p[4] == 1,
            "stick_center_auto_adjust": p[39] == 1, "local_mapping": p[40] == 1,
            "usb_report_rate_hz": struct.unpack_from("<H", p, 41)[0],
            "stick_resolution": p[43], "allow_third_party": p[47] == 1,
            "xinput_home": p[52] == 1}


# A0 field ID, offset within the 80-byte global payload, accepted values.
GLOBAL_FIELDS = {
    "sleep_time": (32, 3, tuple(range(256))),
    "quick_switch": (20, 4, (False, True)),
    "stick_center_auto_adjust": (25, 39, (False, True)),
    "local_mapping": (23, 40, (False, True)),
    "usb_report_rate_hz": (33, 41, (125, 250, 500, 1000)),
    "stick_resolution": (34, 43, (8, 9, 10, 11, 12)),
    "allow_third_party": (37, 47, (False, True)),
    "xinput_home": (38, 52, (False, True)),
}


def _editor(ctrl, expected_uid=None):
    identity.require(ctrl, "k6")
    protocol = gpa6.Protocol(ctrl)
    uid = protocol.uid()
    if expected_uid is not None and uid != expected_uid:
        raise identity.WrongDevice("selected Apex 6 UUID changed before editing")
    return protocol, uid


def set_setting(ctrl, name, value, *, expected_uid=None):
    """Apply one global field and verify all other global bytes are preserved."""
    if name not in GLOBAL_FIELDS:
        raise ValueError("unsupported Apex 6 setting")
    field, offset, allowed = GLOBAL_FIELDS[name]
    boolean = type(allowed[0]) is bool
    if type(value) is not (bool if boolean else int) or value not in allowed:
        raise ValueError(f"invalid value for {name}")
    with ctrl.claim(timeout=0):
        protocol, uid = _editor(ctrl, expected_uid)
        version = protocol.versions()[0]
        before = protocol.read_ram(1)
        old = global_config(before, version)[name]
        if old == value:
            return global_config(before, version)
        width = 2 if name == "usb_report_rate_hz" else 1
        wanted = bytearray(before)
        encoded = int(value).to_bytes(width, "little")
        wanted[offset + 2:offset + 2 + width] = encoded
        try:
            protocol.set_global(field, encoded)
            after = protocol.read_ram(1)
            if protocol.uid() != uid:
                raise identity.WrongDevice("UUID changed during settings write")
            if after != wanted:
                raise gpa6.ProtocolError("settings read-back differs from the requested field edit")
            return global_config(after, version)
        except BaseException as exc:
            try:
                if protocol.uid() == uid:
                    current = protocol.read_ram(1)
                    if current == wanted:
                        protocol.set_global(field, int(old).to_bytes(width, "little"))
                        if protocol.read_ram(1) != before:
                            raise gpa6.ProtocolError("global setting rollback did not verify")
            except Exception as cleanup:
                raise gpa6.ProtocolError(f"{exc}; setting rollback failed: {cleanup}") from exc
            raise


def encode_lighting(config):
    """Build LGHT v2 using the same six modes as the vendor client."""
    mode = config["base"]["mode"]
    values = config["base"].get("config")
    if type(mode) is not int or mode not in range(6):
        raise ValueError("lighting mode must be 0..5")
    def byte(value, maximum=100):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"lighting value must be 0..{maximum}")
        return value
    payload = bytearray([mode])
    if mode == 1:
        payload.extend(byte(values["color"][key], 255) for key in ("r", "g", "b"))
        payload.append(byte(values["brightness"]))
    elif mode in (2, 3):
        colors = values["colors"]
        if not 1 <= len(colors) <= 8:
            raise ValueError("lighting requires 1..8 colors")
        payload.extend((byte(values["brightness"]), byte(values["speed"]), len(colors)))
        for color in colors:
            payload.extend(byte(color[key], 255) for key in ("r", "g", "b"))
    elif mode in (4, 5):
        payload.extend((byte(values["brightness"]), byte(values["speed"])))
    vibration = config.get("vibration", False)
    if type(vibration) is not bool:
        raise ValueError("lighting vibration must be boolean")
    payload.append(int(vibration))
    header = b"LGHT" + struct.pack("<HI", 2, len(payload))
    return wrap_blob(header + struct.pack("<H", sum(header) & 0xffff)
                     + payload + struct.pack("<H", sum(payload) & 0xffff))


def edit_ram(ctrl, ram_id, transform, *, save=False, expected_uid=None):
    """Edit one domain with optimistic checks and rollback of our own write.

    Unknown blocks are refused. On an ambiguous failure, rollback only if the
    same device/slot still contains our exact candidate; never overwrite a
    third-party edit. A7 save is an explicit caller choice, but A4 commits can
    already persist on the tested firmware; save=False is not a preview mode.
    """
    if ram_id not in (4, 5):
        raise ValueError("only lighting and mapping can be edited")
    with ctrl.claim(timeout=0):
        protocol, uid = _editor(ctrl, expected_uid)
        versions = protocol.versions()
        if ram_id == 5 and versions[1] not in (18, 19):
            raise gpa6.ProtocolError("unsupported mapping protocol version")
        fingerprint = protocol.ram_info(ram_id)
        before = protocol.read_ram(ram_id, expected=fingerprint)
        if ram_id == 5:
            from .gpa6_profile import describe
            validate = describe
        else:
            validate = lighting_config
        validate(before)
        wanted = bytes(transform(before))
        validate(wanted)
        if wanted == before:
            if save:
                protocol.save_slot(fingerprint[0])
            return wanted
        try:
            protocol.write_ram(ram_id, wanted, expected=fingerprint)
            if protocol.uid() != uid:
                raise identity.WrongDevice("UUID changed during configuration write")
        except BaseException as exc:
            try:
                if protocol.uid() == uid and protocol.slot() == fingerprint[0]:
                    current = protocol.ram_info(ram_id)
                    if protocol.read_ram(ram_id, expected=current) == wanted:
                        protocol.write_ram(ram_id, before, expected=current)
            except Exception as cleanup:
                raise gpa6.ProtocolError(f"{exc}; configuration rollback failed: {cleanup}") from exc
            raise
        if save:
            # If the save acknowledgment is lost, report uncertainty. Rolling
            # RAM back cannot establish which bytes reached flash.
            protocol.save_slot(fingerprint[0])
        return wanted


def inspect_block(ram_id, data, versions):
    if ram_id == 1:
        return global_config(data, versions[0])
    if ram_id == 5:
        return mapping_header(data)
    if ram_id == 4:
        return lighting_config(data)
    return {"layout": 2, "decoded_scope": "grips only",
            "grips": {side: {"mode": mode, "params_hex": params.hex()}
                      for side, (_, mode, params) in
                      zip(("left", "right"), gpa6.grip_restore(data))}}


def snapshot(ctrl, *, expected_uid=None):
    """Capture the active RAM, checking device and configuration consistency.

    Holds our advisory claim throughout; third-party programs and physical
    profile buttons can still act. Check all fingerprints again at the end,
    including A1's slot/global CRC16s and the complete device UUID.
    """
    with ctrl.claim(timeout=0):
        identity.require(ctrl, "k6")
        protocol = gpa6.Protocol(ctrl)
        uid = protocol.uid()
        if expected_uid is not None and uid != expected_uid:
            raise identity.WrongDevice("selected Apex 6 UUID changed before snapshot")
        info = protocol.info()
        info["firmware"] = list(info["firmware"])
        versions = protocol.versions()
        state = protocol.config_state()
        fingerprints = {ram_id: protocol.ram_info(ram_id) for ram_id in RAM_BLOCKS}
        if any(fp[0] != state[0] for fp in fingerprints.values()):
            raise gpa6.ProtocolError("active slot changed before snapshot")
        blocks = {}
        for ram_id, name in RAM_BLOCKS.items():
            data = protocol.read_ram(ram_id, expected=fingerprints[ram_id])
            block = {"ram_id": ram_id, "length": len(data),
                     "crc32": f"{fingerprints[ram_id][2]:08x}", "data_hex": data.hex()}
            try:
                block["decoded"] = inspect_block(ram_id, data, versions)
            except gpa6.ProtocolError as exc:
                block["decode_error"] = str(exc)
            blocks[name] = block
        for ram_id, fingerprint in fingerprints.items():
            if protocol.ram_info(ram_id) != fingerprint:
                raise gpa6.ProtocolError("configuration changed during snapshot")
        if protocol.config_state() != state or protocol.versions() != versions:
            raise gpa6.ProtocolError("configuration state/versions changed during snapshot")
        if protocol.uid() != uid:
            raise identity.WrongDevice("selected Apex 6 UUID changed during snapshot")
        return {"schema": "openflydigi.apex6.snapshot.v1", "experimental": True,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "scope": "active RAM; may contain unsaved changes; no restore support",
                "uid": uid, "info": info, "format_versions": list(versions),
                "active_slot": state[0], "slot_crc16": list(state[1:5]),
                "global_crc16": state[5], "blocks": blocks}


def write_snapshot(path, data):
    """Publish a complete JSON file without overwriting an existing path."""
    path = Path(path)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".apex6-snapshot-", delete=False) as out:
            temporary = out.name
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        # Linking in the same directory is atomic and fails if the destination
        # already exists, including a symlink. Never replace a prior snapshot.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            os.unlink(temporary)
