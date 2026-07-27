# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""XGameMonitor-equivalent engine: read game memory, drive trigger effects.

Flydigi's XGameMonitor.exe is a generic engine -- all 31 games it supports are
just different JSON configs. The per-game logic is a set of pointer chains into
the game's memory plus conditions on the values read.

On Windows that needs OpenProcess + ReadProcessMemory (their XHelper.dll). On
Linux the equivalent is pread on /proc/<pid>/mem, with the module base taken
from /proc/<pid>/maps -- no injection and no Wine-side helper, so the game runs
untouched under Proton.

Chain semantics, decompiled from XHelper.dll!readXData:

    addr  = module_base + offsets[0]
    value = read_u64(addr)                        # always an 8-byte read
    if value == 0: return 0                       # hard fail, first hop only
    for off in offsets[1:]:
        base  = value if value else module_base   # zero falls back to base
        value = read_u64(base + off)              # read failure -> 0
    return value & 0xFFFFFFFF                     # truncated to uint32

The declared `type` in vDefines is not used for the read size; every hop reads
8 bytes and the result is truncated.

Requires kernel.yama.ptrace_scope = 0 (or CAP_SYS_PTRACE) to read another
process's memory as the same user.
"""
import json
import os
import re
import struct
import time

from .effects import common_effect_payload

SIDE_LEFT = 1
SIDE_RIGHT = 2
UINT32_MASK = 0xFFFFFFFF


class ProcessNotFound(Exception):
    pass


class ModuleNotFound(Exception):
    pass


def load_config(path):
    """Load an XGameMonitor config.

    These are not strict JSON -- Newtonsoft tolerates trailing commas and BOMs
    and several shipped configs rely on that.
    """
    with open(path, encoding="utf-8-sig") as fh:
        raw = fh.read()
    raw = re.sub(r",(\s*[}\]])", r"\1", raw)
    return json.loads(raw)


def _exe_names(pid):
    names = set()
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            parts = fh.read().split(b"\0")
    except OSError:
        return names
    for raw in parts:
        if not raw:
            continue
        token = raw.decode("utf-8", "replace").replace("\\", "/")
        base = os.path.basename(token).strip().lower()
        if base:
            names.add(base)
    return names


def _maps_exe_base(pid, exe_name):
    """Lowest mapping of `exe_name` in this process, or None.

    This doubles as the module base: Wine maps the PE at its image base, so the
    lowest mapping of the executable is what Module32Next would report.
    """
    best = None
    try:
        with open(f"/proc/{pid}/maps") as fh:
            for line in fh:
                parts = line.split(None, 5)
                if len(parts) < 6:
                    continue
                if os.path.basename(parts[5].strip()).lower() != exe_name:
                    continue
                start = int(parts[0].split("-")[0], 16)
                if best is None or start < best:
                    best = start
    except OSError:
        return None
    return best


def find_process(process_name):
    """Find a running game by its configured process name.

    Matching on the command line alone is not enough: under Steam and Proton a
    whole chain of wrappers (reaper, pressure-vessel, pv-adverb, steam.exe)
    carries the game's path in its cmdline. Those match by name but have no PE
    mapped, so require the candidate to have actually mapped the executable.
    """
    exe_name = process_name.lower()
    if not exe_name.endswith(".exe"):
        exe_name += ".exe"
    wanted = {process_name.lower(), exe_name}

    fallback = None
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        if not (_exe_names(entry) & wanted):
            continue
        if _maps_exe_base(entry, exe_name) is not None:
            return int(entry)
        if fallback is None:
            fallback = int(entry)
    if fallback is not None:
        raise ProcessNotFound(
            f"found processes matching {process_name!r} but none had "
            f"{exe_name} mapped -- they are probably Steam/Proton wrappers")
    raise ProcessNotFound(f"no process matching {process_name!r}")


def find_module_base(pid, module_name):
    """Lowest mapped address of the module's image in the target process.

    With module_name unset (true for every shipped config) this resolves the
    main executable, matching xHelperInit's Module32Next behaviour.
    """
    wanted = module_name.lower() if module_name else None
    if wanted and not wanted.endswith(".exe"):
        wanted += ".exe"
    best = None
    best_path = None
    # Wine maps the PE at its image base (typically 0x140000000 for a 64-bit
    # game), so the lowest mapping of the executable is the module base.
    with open(f"/proc/{pid}/maps") as fh:
        for line in fh:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            path = parts[5].strip()
            base = os.path.basename(path).lower()
            if wanted:
                if base != wanted:
                    continue
            elif not base.endswith(".exe"):
                continue
            start = int(parts[0].split("-")[0], 16)
            if best is None or start < best:
                best, best_path = start, path
    if best is None:
        raise ModuleNotFound(f"no module mapping found in pid {pid}")
    return best, best_path


class MemoryReader:
    """Reads another process's memory via /proc/<pid>/mem."""

    def __init__(self, pid):
        self.pid = pid
        self.fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read_u64(self, address):
        """Return an 8-byte little-endian value, or None if unreadable."""
        if address <= 0 or address > 0x7FFFFFFFFFFF:
            return None
        try:
            data = os.pread(self.fd, 8, address)
        except OSError:
            return None
        if len(data) != 8:
            return None
        return struct.unpack("<Q", data)[0]


def read_chain(reader, module_base, offsets):
    """Walk a pointer chain. See module docstring for the exact semantics."""
    if not offsets:
        return 0
    value = reader.read_u64(module_base + offsets[0])
    if not value:
        return 0
    for offset in offsets[1:]:
        base = value if value else module_base
        value = reader.read_u64(base + offset) or 0
    return value & UINT32_MASK


def _condition_ok(item, values):
    """Evaluate one condition item against the current define values."""
    current = values.get(item.get("use_define"))
    if current is None:
        return False
    mod_num = item.get("modNum") or 0
    if mod_num > 0:
        current = current // mod_num * mod_num
    op = item.get("op")
    if op == "in":
        return str(current) in [str(v) for v in (item.get("values") or [])]
    try:
        expected = int(item.get("value"))
    except (TypeError, ValueError):
        return False
    if op == ">=":
        return current >= expected
    if op == ">":
        return current > expected
    if op == "<=":
        return current <= expected
    if op == "<":
        return current < expected
    if op == "=":
        return current == expected
    if op == "!=":
        return current != expected
    return False


def _filter_matches(vfilter, values):
    condition = vfilter.get("vCondition") or {}
    items = condition.get("items") or []
    match_type = condition.get("match_type")
    result = False
    for i, item in enumerate(items):
        ok = _condition_ok(item, values)
        if i == 0:
            result = ok
        elif match_type == "and":
            result = result and ok
        elif match_type == "or":
            result = result or ok
    return result


class Engine:
    """Polls game memory and applies trigger effects.

    Mirrors TriggerHelper: only re-evaluate when a define's value changes, walk
    filters in priority order, first match wins, fall back to trigger_default
    when nothing matches.
    """

    def __init__(self, config, controller, verbose=False):
        self.config = config
        self.ctrl = controller
        self.verbose = verbose
        self.period = (config.get("period") or 1000) / 1000.0
        self.defines = config.get("vDefines") or []
        # Higher priority first, preserving order within a priority.
        self.filters = sorted(config.get("vFilters") or [],
                              key=lambda f: -(f.get("priority") or 0))
        self.default = config.get("trigger_default")
        self.values = {}
        self.applied = {}
        self.pending = None      # (due_time, triggers) from trigger_after
        self.game_version = ""

    def poll(self, reader, module_base):
        """Read all defines; returns the names whose value changed."""
        changed = []
        for define in self.defines:
            version = define.get("game_version")
            if version not in (None, "", "0") and version != self.game_version:
                continue
            name = define.get("name")
            value = read_chain(reader, module_base, define.get("offset") or [])
            if self.values.get(name) != value:
                self.values[name] = value
                changed.append(name)
                if self.verbose:
                    print(f"  {name} = {value}", flush=True)
        return changed

    def evaluate(self, changed):
        """Apply the first matching filter, else the default."""
        # A new reading cancels any scheduled trigger_after, as _cts.Cancel does.
        self.pending = None
        for vfilter in self.filters:
            items = (vfilter.get("vCondition") or {}).get("items") or []
            if not any(item.get("use_define") in changed for item in items):
                continue
            if not _filter_matches(vfilter, self.values):
                continue
            if self.verbose:
                print(f"  -> {vfilter.get('name')}", flush=True)
            self.apply(vfilter.get("trigger"))
            after = vfilter.get("trigger_after")
            if after:
                self.pending = (time.time() + (after.get("duration") or 0) / 1000.0,
                                after.get("trigger"))
            nested = vfilter.get("watch_change_define") or []
            if nested:
                self.evaluate(nested)
            return True
        if self.default:
            self.apply(self.default)
        return False

    def apply(self, triggers):
        if not triggers:
            return
        if triggers.get("use_default") and self.default:
            self.apply(self.default)
            return
        for key, side in (("left", SIDE_LEFT), ("right", SIDE_RIGHT)):
            spec = triggers.get(key)
            if not spec:
                continue
            params = list(spec.get("param") or [])[:5]
            mode = spec.get("mode", 0)
            state = (mode, tuple(params))
            if self.applied.get(side) == state:
                continue
            self.ctrl.send(common_effect_payload(side, mode, params), wait=0.0)
            self.applied[side] = state

    def flush_pending(self):
        if self.pending and time.time() >= self.pending[0]:
            _due, triggers = self.pending
            self.pending = None
            if self.verbose:
                print("  -> trigger_after", flush=True)
            self.apply(triggers)
