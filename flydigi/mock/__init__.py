# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A bus of devices that are not there, for the ones nobody owns.

There is one Apex 5 and one CD2 on this desk. Everything this project does
about *more than one* device -- picking between two pads, refusing an ambiguous
name, fanning a vibration bind out across every pad that supports it, watching
one disappear while another stays -- cannot be run against that, and code that
is never run is code that does not work. `tests/fake_pad.py` covered the
protocol from the start; this covers the bus.

**Switched on by `FLYDIGI_MOCK_BUS`, and off by default.** Nothing here is
reachable unless that variable is set, which is the property that matters: a
mock device must never be able to appear in front of someone driving real
hardware. When it is set, the mock devices are enumerated *after* the real ones
by every part of the stack at once -- `flydigi/device.py:find_nodes`, so the
tools, the daemon and the desktop app all see the same bus -- and every one of
them carries `mock: True` through `flydigi/registry.py` so a UI can say so.

Two forms. A short one for a shell::

    FLYDIGI_MOCK_BUS='pad,pad:f5=Couch,dock,dock:1=Shelf' tools/flydigi-devices list

and a JSON file, when the state wants to be edited while something is running::

    FLYDIGI_MOCK_BUS=~/mock-bus.json distrobox enter apex-dev -- python3 -m gui

    {
      "hide_real": false,
      "devices": [
        {"kind": "pad",  "code": "k5", "nickname": "Desk",  "battery": 5},
        {"kind": "pad",  "code": "k5", "nickname": "Couch", "battery": 2,
         "present": false},
        {"kind": "dock", "type": 1,    "nickname": "Shelf"}
      ]
    }

**The file is re-read whenever the bus is enumerated**, so flipping `present`
and saving is a device being unplugged, and flipping it back is one waking up --
which is how the app's reconnect path, the daemon's `pad_present`, and a picker
whose selected device vanishes get exercised at all. State survives that, on
purpose: the fake keeps its profiles and its lighting across an absence, the way
a pad keeps everything but an unsaved config across a sleep.

**Identity is derived, not random.** A mock pad's uid and mac come from its
index, so they are the same on the next run and a selector stored in a config
file still resolves. Nothing here calls `random`.
"""
import json
import os

ENV = "FLYDIGI_MOCK_BUS"

# Distinctive on purpose. Anything that treats a device path as a filename gets
# something that cannot be opened rather than something that opens the wrong
# file, and anything that prints one is obviously not printing /dev/hidraw3.
PREFIX = "mock:"

KIND_PAD = "pad"
KIND_DOCK = "dock"

# Families, repeated rather than imported: `flydigi/device.py` imports this
# module, so importing it back at module level would be a cycle. They are two
# constants that have never moved.
FAMILY_PAD = 2
FAMILY_DOCK = 6
FAMILIES = {KIND_PAD: FAMILY_PAD, KIND_DOCK: FAMILY_DOCK}

_spec = None            # the parsed spec, or None when nothing is loaded
_source = None          # (env value, file mtime) the spec was parsed from
_devices = {}           # path -> the live fake, kept so state survives absence


def enabled():
    return bool(os.environ.get(ENV, "").strip())


def is_mock(path):
    return isinstance(path, str) and path.startswith(PREFIX)


def reset():
    """Forget the spec and every fake. For a test that changes the bus."""
    global _spec, _source
    _spec, _source = None, None
    _devices.clear()


# --------------------------------------------------------------------------
# The spec
# --------------------------------------------------------------------------

def _parse_short(text):
    """`pad,pad:f5=Couch,dock:1=Shelf` -> a list of entries.

    Deliberately forgiving about whitespace and nothing else: an unknown kind
    raises, because a typo in a bus spec should say so rather than quietly
    produce a shorter bus than was asked for.
    """
    devices = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        head, _, nickname = item.partition("=")
        kind, _, model = head.strip().partition(":")
        kind = kind.strip().lower()
        if kind not in FAMILIES:
            raise ValueError(
                f"{item!r} is not a mock device -- a spec item is "
                f"'pad' or 'dock', optionally ':model' and '=nickname'")
        entry = {"kind": kind}
        if nickname.strip():
            entry["nickname"] = nickname.strip()
        if model.strip():
            entry["code" if kind == KIND_PAD else "type"] = model.strip()
        devices.append(entry)
    return {"devices": devices}


def _load():
    """The spec as it is right now, re-reading the file if it has moved on."""
    global _spec, _source
    value = os.environ.get(ENV, "").strip()
    if not value:
        _spec, _source = None, None
        return None
    path = os.path.expanduser(value)
    stamp = None
    if os.path.isfile(path):
        try:
            stamp = os.stat(path).st_mtime
        except OSError:
            stamp = None
    if _spec is not None and _source == (value, stamp):
        return _spec
    if stamp is not None:
        try:
            with open(path) as fh:
                spec = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ValueError(f"{ENV}={value}: {exc}") from None
        if not isinstance(spec, dict):
            spec = {"devices": spec}
    else:
        spec = _parse_short(value)
    spec.setdefault("devices", [])
    spec.setdefault("hide_real", False)
    _spec, _source = spec, (value, stamp)
    return spec


def spec():
    return _load() if enabled() else None


def hide_real():
    """Whether real hardware is hidden while the mock bus is up.

    Off by default, because the case this exists for is a real pad *plus* a
    fake one. On, it makes a machine with nothing attached behave like a desk
    with several devices on it, which is what a screenshot or a demo wants.
    """
    loaded = spec()
    return bool(loaded and loaded.get("hide_real"))


def _entries(kind):
    loaded = spec()
    if not loaded:
        return []
    out = []
    for index, entry in enumerate(loaded["devices"]):
        if not isinstance(entry, dict) or entry.get("kind", KIND_PAD) != kind:
            continue
        out.append((index, entry))
    return out


def _path(kind, index):
    return f"{PREFIX}{kind}{index}"


def nodes(family):
    """Mock paths of one family, in spec order. Absent devices are left out.

    Same contract as `device.find_nodes`: paths only, no exchanges, and a
    device that is not there simply does not appear -- which for a pad is
    exactly what a sleeping one does, since it leaves the USB bus.
    """
    kind = next((k for k, f in FAMILIES.items() if f == family), None)
    if kind is None:
        return []
    return [_path(kind, index) for index, entry in _entries(kind)
            if entry.get("present", True)]


def locate(path):
    """`(kind, index, entry)` for a mock path, or None.

    The index is the device's place in the spec, which is what its derived uid
    and mac are built from -- so it has to come from the spec rather than from
    the digits in the path, or a device would be renamed by the parsing.
    """
    if not is_mock(path):
        return None
    for kind in FAMILIES:
        for index, entry in _entries(kind):
            if _path(kind, index) == path:
                return kind, index, entry
    return None


def entry_for(path):
    """The spec entry behind a mock path, or None."""
    found = locate(path)
    return found[2] if found else None


# --------------------------------------------------------------------------
# The devices
# --------------------------------------------------------------------------

def instance(path):
    """The fake behind a mock path, built once and kept. None for a real path.

    Kept rather than rebuilt so a mock pad behaves like a pad: write a profile,
    close the handle, open it again, and the profile is still there. It also
    means a device that goes absent and comes back is the same device, which is
    what makes the reconnect paths worth exercising against it.

    The imports are late because `flydigi/device.py` imports this module, and
    the fakes import `flydigi/device.py`.
    """
    if not is_mock(path):
        return None
    if path in _devices:
        return _devices[path]
    found = locate(path)
    if found is None:
        from ..device import DeviceNotFound
        listed = len(spec()["devices"]) if spec() else 0
        raise DeviceNotFound(
            f"{path} is not on the mock bus -- {ENV} lists {listed} device(s)")
    kind, index, entry = found
    if kind == KIND_DOCK:
        from .dock import build_dock
        fake = build_dock(path, index, entry)
    else:
        from .pad import build_pad
        fake = build_pad(path, index, entry)
    _devices[path] = fake
    return fake


def summary():
    """One line naming the mock bus, for a tool that wants to admit to it."""
    loaded = spec()
    if not loaded:
        return ""
    kinds = [e.get("kind", KIND_PAD) for e in loaded["devices"]
             if e.get("present", True)]
    counts = ", ".join(f"{kinds.count(k)} {k}(s)" for k in FAMILIES
                       if kinds.count(k))
    where = "instead of" if loaded.get("hide_real") else "beside"
    return f"{ENV} is set: {counts or 'nothing'} {where} real hardware"
