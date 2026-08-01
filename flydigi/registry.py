# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Every Flydigi device attached, and which one the caller meant.

`flydigi/device.py` finds *a* device: it filters `/dev/hidraw*` down to a family
and hands back the first match in sorted-node order. That is the whole of the
selection this project had, and it has two consequences a desk with more than
one device on it runs into immediately -- `hidraw10` sorts before `hidraw2`, and
a pad that sleeps and comes back reclaims a different minor. So "the pad" is not
a stable thing to name, and a second one of anything is unreachable.

This module is the layer above that. It enumerates a family, asks each device
what it is, and turns a **selector** -- a node path, a uid, a mac, or a nickname
-- into one open handle, refusing an ambiguous one rather than guessing. The
dock had a private version of this (`charger.list_docks`, `charger.open_dock`);
those now call in here, so pads and docks are chosen the same way and a tool's
`--device` means the same thing whatever it is pointed at.

**What identifies a device.** Three names, cheapest first, and a caller may use
any of them:

    kind   how                              cost              stable
    ----   ------------------------------   ---------------   ---------------
    path   /dev/hidrawN                     free              no -- moves
    mac    command 1, pads only             the probe read    yes
    uid    command 4, pads and docks        one exchange      yes
    name   command 2, pads and docks        one exchange      until renamed

`key()` picks the best available one and writes it in a `uid:`/`mac:`/`path:`
form, which is what belongs in a config file -- `flydigi/prefs.py` stores the
daemon's primary pad that way, and the app's picker writes it.

**One probe is one exchange.** A pad's command-1 reply carries its device type,
address, battery, connection and all seven firmware versions at once, and a
dock's heartbeat carries its type, firmware and four switches, so an inventory
of the bus costs one read per device. `deep=True` adds the uid and the nickname,
two more each, and is what a list a person reads should ask for.

**A device that will not answer is listed, not dropped.** Same rule as
`charger.list_docks` had: "there is something here and it will not talk" is the
state a caller most needs to see, and it is the ordinary state of a sleeping pad
-- which, on this hardware, has left the USB bus entirely and so is not listed
at all. A node that is present and mute is a different thing, and worth saying.
"""
import os

from . import device, identity, motion
from .device import DeviceBusy, DeviceNotFound       # re-exported for callers

KIND_PAD = "pad"
KIND_DOCK = "dock"

# What each kind is on the wire, and what opens one. The dock's opener is
# imported late: `flydigi/charger.py` calls back in here for its own
# `list_docks`, and a module-level import either way round would be a cycle.
FAMILIES = {KIND_PAD: device.FAMILY_PAD, KIND_DOCK: device.FAMILY_DOCK}
KINDS = tuple(FAMILIES)

# What to call each kind in a sentence. `device.FAMILY_NAMES` has these too, and
# says "controller" for a pad; a message about a missing one has always said
# "charging dock" rather than "dock", so both stay as they were written.
NOUNS = {KIND_PAD: "controller", KIND_DOCK: "charging dock"}

# How a stored selector says which sort of name it is. A bare selector is
# matched against all of them; these exist so a config file can be unambiguous,
# and so `path:` can be written down while still reading as the guess it is.
PREFIXES = ("uid", "mac", "name", "path")


class Ambiguous(DeviceNotFound):
    """A selector matched more than one device. Never resolved by guessing."""


def _opener(kind):
    if kind == KIND_DOCK:
        from . import charger
        return charger.Dock
    return device.Controller


# --------------------------------------------------------------------------
# Looking
# --------------------------------------------------------------------------

def nodes(kind=KIND_PAD):
    """Every command interface of one kind, in node order. No exchanges."""
    return list(device.find_nodes(FAMILIES[kind]))


def blank(path, kind):
    """The entry for a device nothing has been asked yet.

    Every key the probes below can fill is present and None, so a caller never
    has to test for a missing one -- a UI binding against `entry["battery"]`
    should not need to know whether the probe got that far.
    """
    return {
        "path": path,
        "kind": kind,
        "family": FAMILIES[kind],
        "product": device.hid_name(os.path.basename(path)),
        "mock": device.is_mock(path),
        "device_type": None,
        "code": None,
        "model": None,
        "uid": None,
        "mac": None,
        "nickname": None,
        "firmware": None,
        "battery": None,
        "charging": None,
        "connect_type": None,
        "supported": False,
        "info": None,
        "error": None,
    }


def probe_pad(ctrl, entry, deep=False):
    """Fill in a pad's entry. One exchange, or three with `deep`."""
    # `until`, or every probe costs the full 0.6 s even when the pad answers at
    # once -- and the bus is probed on a timer. See `Controller.send`.
    replies = ctrl.send(_info_request(), wait=0.6,
                        until=lambda seen: motion.parse_info(seen[-1]) is not None)
    for reply in replies:
        info = motion.parse_info(reply)
        if not info:
            continue
        entry["info"] = info
        entry["device_type"] = info["device_type"]
        entry["code"] = identity.code_for(info["device_type"])
        entry["model"] = identity.PRODUCT_NAMES.get(entry["code"])
        entry["mac"] = info["mac"]
        entry["battery"] = info["battery_level"]
        entry["charging"] = info["charging"]
        entry["connect_type"] = info["connect_type"]
        entry["supported"] = entry["code"] in identity.SUPPORTED
        # The same reply carries the seven firmware versions, so reading them
        # is free once it has arrived. `main` is the one a list wants.
        versions = motion.parse_versions(reply)
        if versions:
            entry["firmware"] = versions.get("main")
        break
    if entry["info"] is None:
        entry["error"] = "no reply to the identify read"
        return entry
    if deep:
        entry["uid"] = identity.read_uid(ctrl)
        entry["nickname"] = identity.read_nickname(ctrl)
    return entry


def probe_dock(dock, entry, deep=False):
    """Fill in a dock's entry. The heartbeat, plus uid and name when deep."""
    from . import charger
    try:
        info = charger.read_info(dock)
    except charger.ProtocolError as exc:
        entry["error"] = str(exc)
        return entry
    entry["info"] = info
    entry["device_type"] = info["device_type"]
    entry["model"] = charger.name_for(info["device_type"])
    entry["firmware"] = info["firmware"]
    entry["supported"] = entry["model"] is not None
    if deep:
        try:
            entry["uid"] = charger.read_uid(dock)
            entry["nickname"] = charger.read_nickname(dock)
        except charger.ProtocolError:
            pass
    return entry


def _info_request():
    buf = device.build(motion.CMD_GET_INFO)
    buf[4] = 2
    buf[5] = device.checksum(buf, 3, 3 + buf[4])
    return buf


def probe(path, kind, deep=False):
    """Open one node, ask it what it is, and close it again.

    Never raises for a device's own sake: a node that cannot be opened, is held
    by someone past the claim timeout, or answers nothing at all comes back as
    an entry carrying the reason. Only a caller asking about a path that is not
    there gets an exception, and that comes from the open.
    """
    entry = blank(path, kind)
    try:
        with _opener(kind)(path) as handle:
            if kind == KIND_DOCK:
                return probe_dock(handle, entry, deep=deep)
            return probe_pad(handle, entry, deep=deep)
    except (OSError, DeviceBusy, DeviceNotFound) as exc:
        entry["error"] = str(exc)
        return entry


def list_devices(kinds=KINDS, deep=False):
    """Everything attached, of every kind asked for, in node order per kind.

    Costs one exchange per device, three with `deep`. Pads come before docks
    because that is the order `KINDS` is written in and the order a person
    thinks in, not because the bus says anything about it.
    """
    if isinstance(kinds, str):
        kinds = (kinds,)
    found = []
    for kind in kinds:
        for path in nodes(kind):
            found.append(probe(path, kind, deep=deep))
    return found


def list_pads(deep=False):
    return list_devices(KIND_PAD, deep=deep)


def drivable_pads(deep=False):
    """Every attached pad this project is willing to write to.

    The fan-out list, for the one thing that acts on all of them at once: the
    daemon's tier-1 vibration bind is a pad-side setting with nothing host-side
    in the loop, so every pad can drive its own triggers from its own rumble
    and there is no reason to pick one. Everything else -- a driver rewriting
    trigger effects at 20 Hz, a relay presenting one DualSense -- holds a
    single pad and chooses it.

    `supported` is `identity.SUPPORTED`, so a Vader 5 on the same desk is
    enumerated, named, and left alone rather than written to.
    """
    return [e for e in list_pads(deep=deep) if e["supported"]]


def list_docks(deep=True):
    return list_devices(KIND_DOCK, deep=deep)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

def key(entry):
    """The most stable name this device has, in `kind:value` form.

    What goes in a config file. A `path:` key is deliberately still produced
    for a device that answered nothing -- it is a worse name and it is the only
    one there is, and a caller that stores it gets the behaviour it would have
    had anyway rather than nothing at all.
    """
    if entry.get("uid"):
        return f"uid:{entry['uid']}"
    if entry.get("mac"):
        return f"mac:{entry['mac']}"
    return f"path:{entry['path']}"


def label(entry):
    """What to call this device on screen or in a line of output.

    The nickname is first because it is the only name the user chose, and the
    whole point of naming a pad is that it wins over "Apex 5" when there are
    two of them.
    """
    return (entry.get("nickname") or entry.get("model")
            or entry.get("product") or entry["path"])


def describe(entry):
    """One line: what it is, what it is called, and what is wrong with it."""
    parts = [entry["path"], label(entry)]
    if entry.get("mock"):
        parts.append("MOCK")
    if entry.get("firmware"):
        parts.append(f"fw {entry['firmware']}")
    if entry.get("battery") is not None:
        parts.append(f"battery {entry['battery']}/{motion.MAX_LEVEL}"
                     if not entry.get("charging") else "charging")
    if entry.get("error"):
        parts.append(f"-- {entry['error']}")
    return "  ".join(parts)


def _normalise_mac(text):
    return text.replace(":", "").replace("-", "").lower()


def matches(entry, selector):
    """Whether `selector` names this device.

    A selector may be typed (`uid:`, `mac:`, `name:`, `path:`) or bare. Bare is
    tried against every name, because a person copying a uid out of a listing
    should not also have to say which column it came from -- and the four
    spaces do not collide: a node path starts with a slash, a mac has colons or
    is eight hex digits, a uid is twenty-six, and a nickname is neither.

    A uid or mac may be given as a **prefix**, since nobody types twenty-six hex
    digits. A nickname must match in full, case-insensitively: prefix-matching
    names would make "Desk" ambiguous the moment a "Desk 2" appeared, which is
    exactly when a second pad shows up.
    """
    if not selector:
        return True
    selector = selector.strip()
    kind, _, value = selector.partition(":")
    if kind in PREFIXES and value:
        selector, typed = value.strip(), kind
    else:
        typed = None

    if typed in (None, "path") and entry["path"] == selector:
        return True
    if typed == "path":
        return False

    uid = (entry.get("uid") or "").lower()
    if typed in (None, "uid") and uid and uid.startswith(selector.lower()):
        return True
    if typed == "uid":
        return False

    mac = _normalise_mac(entry.get("mac") or "")
    wanted = _normalise_mac(selector)
    if typed in (None, "mac") and mac and wanted and mac.startswith(wanted):
        return True
    if typed == "mac":
        return False

    nickname = (entry.get("nickname") or "").lower()
    return bool(nickname) and nickname == selector.lower()


def find(selector=None, kinds=KINDS, deep=True):
    """The one device `selector` names. Raises rather than picking for you.

    `deep` by default: a selector may be a nickname or a uid, and neither is
    known without asking, so a lookup that did not ask would silently only ever
    match paths and macs.

    **A bare lookup takes the first device rather than refusing.** It is the
    same rule `open_device` follows and the same one `charger.open_dock` always
    had: naming nothing is what every caller that has only ever seen one device
    does, and answering it with "that is ambiguous" would turn plugging in a
    second device into an error in the tools that have not been pointed at one.
    """
    kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    found = [e for e in list_devices(kinds, deep=deep) if matches(e, selector)]
    if not found:
        raise DeviceNotFound(_nothing_matched(selector, kinds))
    if not selector:
        return found[0]
    if len(found) > 1:
        names = ", ".join(f"{e['path']} ({label(e)})" for e in found)
        raise Ambiguous(
            f"{selector!r} matches more than one device: {names}. Name one of "
            f"them exactly, or use its uid -- `flydigi-devices list` prints "
            f"both.")
    return found[0]


def _nothing_matched(selector, kinds):
    what = " or ".join(NOUNS.get(k, k) for k in kinds)
    if selector:
        return (f"no {what} matches {selector!r} -- "
                f"`flydigi-devices list` prints what is attached")
    if KIND_PAD in kinds:
        return ("no Flydigi controller found -- press a button to wake the "
                "pad, since it leaves the USB bus entirely when it sleeps, or "
                "check the cable")
    return (f"no Flydigi {what} found -- check that it is plugged into the "
            f"host and not just into power")


def kind_of(path):
    """Which kind of device a node is, without opening it. None if unknown.

    From the product id's top nibble, the same partition `find_nodes` makes, so
    a path can be opened with the right handle class without an inventory of
    the bus first.
    """
    from . import mock
    found = mock.locate(path)
    if found:
        return found[0]
    ids = device.hid_ids(os.path.basename(path))
    if ids is None or ids[0] != device.VID:
        return None
    family = ids[1] >> 12
    return next((k for k, f in FAMILIES.items() if f == family), None)


def _node_selector(selector):
    """The path a selector names outright, or None if it names something else.

    A node is an exact answer already. Opening it directly rather than looking
    it up keeps `--device /dev/hidraw5` costing nothing, where resolving it
    would mean one exchange with every other device on the bus to confirm what
    the caller had already said.
    """
    from . import mock
    text = selector.strip()
    if text.startswith("path:"):
        text = text[len("path:"):].strip()
    if text.startswith("/") or mock.is_mock(text):
        return text
    return None


# --------------------------------------------------------------------------
# Opening
# --------------------------------------------------------------------------

def open_device(selector=None, kinds=KINDS):
    """Open the device `selector` names. Bare, the first one of its kind.

    Bare is deliberately still first-in-node-order rather than an error: one
    device is the ordinary case, every existing caller passes nothing, and
    making them all choose would be this layer charging for a problem they do
    not have.
    """
    kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    if not selector:
        for kind in kinds:
            for path in nodes(kind):
                return _opener(kind)(path)
        raise DeviceNotFound(_nothing_matched(None, kinds))
    node = _node_selector(selector)
    if node is not None:
        kind = kind_of(node)
        if kind in kinds:
            return _opener(kind)(node)
        if kind is None and len(kinds) == 1:
            # A path that sysfs says nothing about, with only one kind in
            # play -- take the caller at their word and let the open fail with
            # the kernel's own error rather than "no device matches".
            return _opener(kinds[0])(node)
    entry = find(selector, kinds)
    return _opener(entry["kind"])(entry["path"])


# The one sentence every tool's `--device` is described by. Written once so the
# flag cannot come to mean four slightly different things across nine scripts,
# which is what happened to `--uid` and `--device` before this layer existed.
DEVICE_HELP = ("which device: a node path, a uid or a prefix of one, a mac, or "
               "a nickname. `flydigi-devices list` prints all four")


def add_device_argument(parser, help=None):
    """Give an argparse parser the standard `--device`. Returns the parser."""
    parser.add_argument("--device", metavar="SELECTOR", default=None,
                        help=help or DEVICE_HELP)
    return parser


def open_pad(selector=None):
    """One pad, by any of its names. Still ungated -- `identity.require` gates.

    Selection and permission stay apart on purpose. This answers "which device",
    and it can be pointed at a Vader 5 quite deliberately; `identity.require`
    answers "may this be written to" and is the caller's own call to make, once
    per connection.
    """
    return open_device(selector, KIND_PAD)


def open_dock(selector=None):
    return open_device(selector, KIND_DOCK)
