# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Read-only Apex 6 configuration snapshots and bounded format inspection.

Sources: pinned GPA6 web core/codec/contract in docs/findings-apex6.md.
Raw RAM bytes are retained even when a decoder does not know their format.
There is deliberately no write, save, slot-switch or restore operation here.
"""
import binascii
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
import tempfile

from . import gpa6, identity

RAM_BLOCKS = {1: "global", 4: "mapping", 5: "lighting", 6: "motors"}


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


def inspect_block(ram_id, data, versions):
    if ram_id == 1:
        return global_config(data, versions[0])
    if ram_id == 4:
        return mapping_header(data)
    if ram_id == 5:
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
