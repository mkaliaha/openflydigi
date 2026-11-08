# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Lossless Apex 6 profile packages, guarded restoration and a local library.

Like mapping.pack_config/unpack_config, one bytes value carries every profile
domain. This is an OpenFlydigi format, not the vendor's decoded JSON export or
the diagnostic snapshot format. Globals are device settings, not profile data.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
import zlib

from . import gpa6, gpa6_config, gpa6_haptics, gpa6_profile, identity

SCHEMA = "openflydigi.apex6.profile.v1"
DOMAINS = {"mapping": 5, "lighting": 4, "motors": 6}
MAX_PACKAGE_SIZE = 64 * 1024
# IMU, lighting, trigger haptics/vibration and grip technology affect profiles.
PROFILE_FEATURES = 0xb3


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


class ProfileConfig:
    """Reusable profile value with mapping, lighting and motor bytes intact.

    cfg_id identifies the live capture/result, as in mapping.MappingConfig. Source
    metadata always describes the capture and is never used to select a slot.
    """

    def __init__(self, mapping_blob, lighting_blob, motor_blob, source, *, name="", cfg_id=None):
        self.mapping = gpa6_profile.MappingConfig(mapping_blob, cfg_id)
        gpa6_config.lighting_config(lighting_blob)
        self.lighting = bytes(lighting_blob)
        self.motors = gpa6_haptics.MotorConfig(motor_blob)
        self.source = copy.deepcopy(source)
        self.name = name
        self.cfg_id = cfg_id

    def blocks(self):
        return {5: bytes(self.mapping.blob), 4: bytes(self.lighting), 6: bytes(self.motors.blob)}

    def copy(self):
        return ProfileConfig(self.mapping.blob, self.lighting, self.motors.blob,
                             self.source, name=self.name, cfg_id=self.cfg_id)

    def describe(self):
        blocks = self.blocks()
        return {"name": self.name, "source": copy.deepcopy(self.source),
                "blocks": {name: {"length": len(blocks[ram]),
                                   "sha256": hashlib.sha256(blocks[ram]).hexdigest()}
                           for name, ram in DOMAINS.items()}}


def _validate_source(source):
    if not isinstance(source, dict):
        raise ValueError("missing profile source metadata")
    if (not _integer(source.get("device_type"), 0, 255) or source["device_type"] not in gpa6.SKUS
            or not _integer(source.get("features_raw"), 0, 255)
            or not isinstance(source.get("uid"), str)
            or re.fullmatch(r"[0-9a-f]{32}", source["uid"]) is None
            or not _integer(source.get("slot"), 0, 3)
            or not isinstance(source.get("captured_at"), str)):
        raise ValueError("invalid profile source metadata")
    for key, size in (("format_versions", 3), ("firmware", 7)):
        values = source.get(key)
        if not isinstance(values, list) or len(values) != size or not all(_integer(v, 0, 65535) for v in values):
            raise ValueError(f"invalid profile {key}")
    if source["format_versions"][1] not in (18, 19):
        raise ValueError("unsupported mapping protocol version")


def _document(config):
    _validate_source(config.source)
    if (not isinstance(config.name, str) or not config.name.strip() or len(config.name) > 120
            or any(ord(c) < 32 for c in config.name)):
        raise ValueError("profile name must be 1..120 characters without control characters")
    blocks = config.blocks()
    for ram, data in blocks.items():
        if not 1 <= len(data) <= gpa6.MAX_RAM_SIZE:
            raise ValueError("invalid profile block size")
    gpa6_profile.MappingConfig(blocks[5])
    gpa6_config.lighting_config(blocks[4])
    gpa6_haptics.MotorConfig(blocks[6])
    version = gpa6_config.mapping_header(blocks[5])["version"]
    if version != f"1.{config.source['format_versions'][1] - 10}":
        raise ValueError("mapping header and source protocol version disagree")
    return {"schema": SCHEMA, "name": config.name, "source": copy.deepcopy(config.source),
            "blocks": {name: {"length": len(blocks[ram]),
                              "sha256": hashlib.sha256(blocks[ram]).hexdigest(),
                              "data_hex": blocks[ram].hex()} for name, ram in DOMAINS.items()}}


def pack_config(config):
    """Pack a complete profile into one portable, versioned bytes value."""
    content = (json.dumps(_document(config), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(content) > MAX_PACKAGE_SIZE:
        raise ValueError("profile package is too large")
    return content


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate profile JSON key: {key}")
        result[key] = value
    return result


def unpack_config(data, cfg_id=None):
    """Validate the whole package before it can reach any write operation."""
    if len(data) > MAX_PACKAGE_SIZE:
        raise ValueError("profile package is too large")
    try:
        doc = json.loads(data, object_pairs_hook=_unique_object)
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            raise ValueError("not an OpenFlydigi Apex 6 profile package (snapshots and legacy backups are not restorable here)")
        if set(doc) != {"schema", "name", "source", "blocks"} or set(doc["blocks"]) != set(DOMAINS):
            raise ValueError("unexpected or missing profile package fields")
        _validate_source(doc["source"])
        blocks = {}
        for name, ram in DOMAINS.items():
            block = doc["blocks"][name]
            if not isinstance(block, dict) or set(block) != {"length", "sha256", "data_hex"}:
                raise ValueError("invalid profile block fields")
            if (not _integer(block["length"], 1, gpa6.MAX_RAM_SIZE)
                    or not isinstance(block["data_hex"], str)
                    or len(block["data_hex"]) != block["length"] * 2):
                raise ValueError("profile block length mismatch")
            raw = bytes.fromhex(block["data_hex"])
            if len(raw) != block["length"] or hashlib.sha256(raw).hexdigest() != block["sha256"]:
                raise ValueError("profile block checksum mismatch")
            blocks[ram] = raw
        result = ProfileConfig(blocks[5], blocks[4], blocks[6], doc["source"], name=doc["name"], cfg_id=cfg_id)
        _document(result)
        return result
    except (KeyError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("malformed Apex 6 profile package") from exc


def load_config(path):
    with open(path, "rb") as stream:
        return unpack_config(stream.read(MAX_PACKAGE_SIZE + 1))


def export_config(path, config):
    """Reuse atomic, no-overwrite snapshot publication for portable packages."""
    document = json.loads(pack_config(config))
    gpa6_config.write_snapshot(path, document)


def _from_snapshot(snapshot, name=None):
    source = {key: snapshot["info"][key] for key in ("device_type", "features_raw", "firmware")}
    source.update(uid=snapshot["uid"], slot=snapshot["active_slot"],
                  captured_at=snapshot["captured_at"], format_versions=snapshot["format_versions"])
    blobs = {name: bytes.fromhex(snapshot["blocks"][name]["data_hex"]) for name in DOMAINS}
    config = ProfileConfig(blobs["mapping"], blobs["lighting"], blobs["motors"], source,
                           name=name or f"Profile {snapshot['active_slot'] + 1}", cfg_id=snapshot["active_slot"])
    return unpack_config(pack_config(config), config.cfg_id)


def _read_retry(operation, *args, **kwargs):
    """Retry only unanswered read-only operations; never replay a mutation."""
    for attempt in range(3):
        try:
            return operation(*args, **kwargs)
        except gpa6.ReplyTimeout:
            if attempt == 2:
                raise
            time.sleep(.05)


def read_config(ctrl, *, name=None, expected_uid=None):
    return _from_snapshot(_read_retry(gpa6_config.snapshot, ctrl, expected_uid=expected_uid), name)


def _plan(before, wanted, trigger_haptic):
    """Preflight every transition; quiesce triggers before replacing mappings."""
    old, new = before.blocks(), wanted.blocks()
    if old[6][:1] != new[6][:1] or old[6][23:] != new[6][23:]:
        raise gpa6.ProtocolError("backup differs in motor bytes without a supported persistent write path")
    motors = gpa6_haptics.MotorConfig(old[6])
    destination = gpa6_haptics.MotorConfig(new[6])
    steps = []

    def motor(side, mode, params=b""):
        previous = bytes(motors.blob)
        motors.set_trigger(side, mode, params)
        after = bytes(motors.blob)
        if previous != after:
            if not trigger_haptic:
                raise gpa6.ProtocolError("controller does not report trigger haptics")
            steps.append((6, previous, after, side))

    # Even a mapping-only restore must avoid running an old scene/binding
    # against a new mapping between the independent firmware transactions.
    for side in gpa6_haptics.SIDES:
        if old[5] != new[5] or motors.trigger(side)["record"] != destination.trigger(side)["record"]:
            motor(side, 0)
    for ram in (5, 4):
        if old[ram] != new[ram]:
            steps.append((ram, old[ram], new[ram], None))
    for side in gpa6_haptics.SIDES:
        entry = destination.trigger(side)
        if motors.trigger(side)["record"] != entry["record"]:
            # Reject a destination whose unused bytes the command would clear.
            motor(side, entry["mode"], entry["params"])
    if bytes(motors.blob) != new[6]:
        raise gpa6.ProtocolError("backup has a non-restorable trigger record")
    return steps


def write_config(ctrl, config, *, save=False, expected_uid=None, expected_slot=None,
                 expected_config=None, before_write=None):
    """Restore to the active slot with full preflight and exact-state rollback.

    before_write receives a fresh recovery ProfileConfig before any mutation;
    the CLI persists it in the library. The callback must succeed before writes.
    An explicit save failure reports uncertainty without rolling RAM back.
    The device does not offer an atomic transaction across these domains.
    """
    wanted = unpack_config(pack_config(config))
    if expected_slot is not None and not _integer(expected_slot, 0, 3):
        raise ValueError("expected slot must be an integer in 0..3")
    with ctrl.claim(timeout=0):
        protocol, uid = _read_retry(gpa6_config._editor, ctrl, expected_uid)
        snapshot = _read_retry(gpa6_config.snapshot, ctrl, expected_uid=uid)
        before = _from_snapshot(snapshot)
        slot = before.cfg_id
        if expected_slot is not None and slot != expected_slot:
            raise gpa6.ProtocolError("active slot differs from requested restore slot")
        for key in ("device_type", "format_versions"):
            if wanted.source[key] != before.source[key]:
                raise gpa6.ProtocolError(f"backup {key} is incompatible with the selected controller")
        if (wanted.source["features_raw"] ^ before.source["features_raw"]) & PROFILE_FEATURES:
            raise gpa6.ProtocolError("backup capabilities are incompatible with the selected controller")
        if expected_config is not None and before.blocks() != expected_config.blocks():
            raise gpa6.ProtocolError("profile changed before restore")
        steps = _plan(before, wanted, snapshot["info"]["trigger_haptic"])
        expected = {block["ram_id"]: bytes.fromhex(block["data_hex"]) for block in snapshot["blocks"].values()}
        journal = []

        def check_identity():
            if _read_retry(protocol.uid) != uid:
                raise identity.WrongDevice("UUID changed during profile restore")
            if _read_retry(protocol.slot) != slot or list(_read_retry(protocol.versions)) != before.source["format_versions"]:
                raise gpa6.ProtocolError("slot/format changed during profile restore")

        def check_state(state):
            check_identity()
            if any(_read_retry(protocol.read_ram, ram) != blob for ram, blob in state.items()):
                raise gpa6.ProtocolError("concurrent/partial configuration change; restore or rollback refused")
            check_identity()

        def write(ram, data, side, previous):
            if ram in (4, 5):
                protocol.write_ram(ram, data, expected=(slot, len(previous), zlib.crc32(previous)))
            else:
                entry = gpa6_haptics.MotorConfig(data).trigger(side)
                protocol.configure_trigger(gpa6_haptics.SIDES.index(side), entry["mode"], entry["params"])

        if steps and before_write is not None:
            before_write(before.copy())
        try:
            for ram, old, new, side in steps:
                check_state(expected)
                prior = expected.copy()
                expected[ram] = new
                journal.append((ram, old, new, side, prior, expected.copy()))
                write(ram, new, side, old)
                check_state(expected)
            check_state(expected)
        except BaseException as exc:
            try:
                for ram, old, new, side, prior, after in reversed(journal):
                    check_identity()
                    current = {key: _read_retry(protocol.read_ram, key) for key in after}
                    check_identity()
                    if current == prior:
                        continue  # attempted command did not change the profile
                    if current != after:
                        raise gpa6.ProtocolError("concurrent/partial configuration change; automatic rollback refused")
                    write(ram, old, side, new)
                    check_state(prior)
            except BaseException as cleanup:
                raise gpa6.ProtocolError(f"{exc}; rollback incomplete: {cleanup}") from exc
            raise
        if save:
            check_state(expected)
            protocol.save_slot(slot)
        wanted.cfg_id = wanted.mapping.cfg_id = slot
        return wanted


class Library:
    """Named immutable entries, each a self-contained portable profile file."""

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else Path(
            os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "openflydigi/apex6/profiles"

    def _path(self, entry_id):
        if not isinstance(entry_id, str) or re.fullmatch(r"[0-9a-f]{32}", entry_id) is None:
            raise ValueError("library ID must be 32 lowercase hexadecimal characters")
        return self.directory / (entry_id + ".json")

    def get(self, entry_id):
        return load_config(self._path(entry_id))

    def add(self, config, *, name=None):
        config = config.copy()
        if name is not None:
            config.name = name
        pack_config(config)  # validate before creating a directory
        self.directory.mkdir(parents=True, exist_ok=True)
        entry_id = uuid.uuid4().hex
        export_config(self._path(entry_id), config)
        return entry_id

    def list(self):
        result = []
        for path in sorted(self.directory.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{32}", path.stem) is None:
                continue
            try:
                result.append({"id": path.stem, **self.get(path.stem).describe()})
            except (OSError, ValueError) as exc:
                result.append({"id": path.stem, "error": str(exc)})
        return result

    def import_file(self, path, *, name=None):
        return self.add(load_config(path), name=name)

    def export(self, entry_id, path):
        export_config(path, self.get(entry_id))

    def remove(self, entry_id):
        self._path(entry_id).unlink()
