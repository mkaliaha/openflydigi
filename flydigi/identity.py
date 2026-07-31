# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Which Flydigi device is on the other end, and what it is allowed to be sent.

**`find_device` cannot tell two Flydigi pads apart.** It narrows to the
controller family -- vendor id, the product id's top nibble, and the vendor
collection's report-descriptor prefix -- and any pad that publishes a vendor
node matches all three, so a second one plugged in beside an Apex 5, or instead
of it, opens exactly the same way. Nothing above that layer notices, which would
mean an Apex 5 profile written into another pad's flash with no error anywhere.

**Which pads those are is a shorter list than it looks.** `IsOldProtocol()` is
`VendorId != 0x37D7`, so only the `5a a5` generation carries this vendor id at
all -- Apex 5, Apex 6, Vader 5. Everything older is an XInput device, `045e:028e`
with no HID vendor collection, cabled or on its dongle alike; Windows reaches
those through the HID front end `xusb22` synthesises, and nothing on Linux can
see them. Measured on a Vader 4 Pro.

So the device that would open exactly like an Apex 5 and take an Apex 5 profile
into its flash is a **Vader 5 or an Apex 6**, not a Vader 4. That is why this
gate is not decoration, and why `SUPPORTED` names a code rather than trusting
the transport.
-> docs/findings-other-devices.md

The nibble separates *kinds* of device, never models: it is what keeps the CD2
charging dock -- same vendor, same descriptor prefix -- out of a pad handle, and
it does nothing at all about two pads. That is this module's job and it always
was. See `flydigi/device.py` for the split and `flydigi/charger.py:require` for
the dock's own version of the gate below.

The only thing that distinguishes them is a **command-1 read**: `DeviceType` is a
number per SKU, and Flydigi's own `FlydigiControllerFactory` dispatches on it.
That is one exchange, and it is the price of not writing an 840-byte blob into
the wrong controller.

`require` is the gate. Call it once per connection before anything writes:

    with device.Controller() as ctrl:
        models.require(ctrl, "k5")          # raises WrongDevice otherwise
        mapping.write_config(ctrl, ...)

Reads are deliberately not gated. Asking an unknown pad what it is, or dumping a
blob to look at it, is how this project learns anything -- and it cannot damage a
device. Writes are.

**Refusing the wrong pad and choosing the right one are different jobs**, and
this module now does both. `require` is the first: it takes whatever
`find_device` handed over and decides whether it may be written to. The second
is `read_uid`, `read_nickname` and `write_nickname` at the bottom of this file
-- names for a particular pad, so a caller can ask for that one rather than for
whichever node sorted first. What turns those into a selection is
`flydigi/registry.py`; what turns a selection into a device the whole stack
agrees on is its `open_pad`.

The table is `docs/findings-other-devices.md`, which is the same dispatch read
out of the SDK. Codes are Flydigi's, and they do not follow the product names:
`k2` is the Apex **4**, and there is no `k3` or `k4`.
"""
from . import motion
from .device import build, checksum


class WrongDevice(Exception):
    """A device answered, and it is not one this caller is willing to write to."""


class NicknameRefused(Exception):
    """The pad did not acknowledge a nickname write."""


# DeviceType -> DeviceCode, from `FlydigiControllerUtil.GetDeviceCodeById`. One
# entry per SKU rather than per model, which is why a single code owns several
# numbers -- 128 and 129 are the Apex 5 base model and the Eva edition, and
# SDL's own Flydigi driver recognises exactly that pair.
#
# `GetDeviceCodeById` maps neither `K5LZ = 136` nor `F5_DBZ = 144`, and never
# returns `fp1` or `fp2` for anything; those reach the dispatch only through
# `RecognizeDeviceCodeFromProductName`, which derives a code from the product
# string. The entries below marked "by name" are the enum's own names for those
# SKUs rather than something the dispatch produces, and they are here because
# this table exists so a refusal can say *what* it found.
DEVICE_TYPES = {
    24: "k1", 26: "k1", 29: "k1",
    84: "k2", 86: "k2", 87: "k2", 92: "k2", 93: "k2",
    102: "k2", 103: "k2", 104: "k2",
    128: "k5", 129: "k5", 133: "k5", 134: "k5", 135: "k5", 136: "k5",
    149: "k6", 150: "k6",
    # `f3` is the plain Vader 3 alone; the three Pro SKUs are their own code.
    28: "f3",
    80: "f3p", 81: "f3p", 88: "f3p",
    85: "f4", 91: "f4",
    130: "f5", 144: "f5", 145: "f5",
    25: "fp1", 30: "fp1", 31: "fp1",                        # by name
    82: "fp2", 83: "fp2", 89: "fp2", 90: "fp2", 94: "fp2",  # by name
    95: "fp3",
    97: "fp3",                                              # by name
    132: "fp4", 146: "fp4", 147: "fp4", 148: "fp4",
}

# As Flydigi's own locales name them, so a refusal reads the way the box does.
# They have no strings for `fp1` or `fp2`; those two names follow the pattern
# and nothing else.
PRODUCT_NAMES = {
    "k1": "Apex 3",
    "k2": "Apex 4",          # not the Apex 2
    "k5": "Apex 5",
    "k6": "Apex 6",
    "f3": "Vader 3",
    "f3p": "Vader 3 Pro",
    "f4": "Vader 4",
    "f5": "Vader 5 Pro",
    "fp1": "Direwolf",
    "fp2": "Direwolf 2",
    "fp3": "Direwolf 3",
    "fp4": "Direwolf 4",
}

# What this project actually drives. Everything else is recognised so the error
# can name it, which is the difference between "wrong device" and "no idea".
SUPPORTED = ("k5",)


def code_for(device_type):
    """Flydigi's `DeviceCode` for a `DeviceType`, or None if it is not in the table.

    None is not an error on its own -- Flydigi ship SKUs faster than this table
    is updated, and a number missing from it is an unknown model rather than a
    broken read.
    """
    return DEVICE_TYPES.get(int(device_type))


def name_for(device_type):
    """A product name for an error message. Never raises."""
    code = code_for(device_type)
    if code is None:
        return f"an unrecognised Flydigi device (DeviceType {device_type})"
    return f"{PRODUCT_NAMES.get(code, code)} ({code}, DeviceType {device_type})"


def identify(ctrl, wait=0.6):
    """Ask the connected device what it is. One command-1 exchange.

    Returns `{"device_type", "code", "name"}`. Raises `WrongDevice` when the pad
    does not answer at all, because a caller about to write needs the difference
    between "it is not an Apex 5" and "it did not say" to be loud either way --
    a sleeping pad and an unknown model must not both read as "carry on".
    """
    info = motion.read_info(ctrl, wait=wait)
    if not info or "device_type" not in info:
        raise WrongDevice(
            "no reply to the identify read -- press a button to wake the pad, "
            "which leaves the USB bus entirely when it sleeps")
    device_type = info["device_type"]
    return {
        "device_type": device_type,
        "code": code_for(device_type),
        "name": name_for(device_type),
    }


def require(ctrl, *codes, wait=0.6):
    """Refuse to continue unless the device is one of `codes`. Returns identity.

    Defaults to what this project drives, so `require(ctrl)` is the ordinary
    call and naming a code is for a tool that genuinely wants another model.

    Costs one exchange, and belongs once per connection rather than once per
    write -- the device on the far end of an open handle does not change.
    """
    wanted = codes or SUPPORTED
    identity = identify(ctrl, wait=wait)
    if identity["code"] not in wanted:
        expected = " or ".join(PRODUCT_NAMES.get(c, c) for c in wanted)
        raise WrongDevice(
            f"this is {identity['name']}, and that write is for {expected}. "
            "Flydigi pads share a vendor id and a report descriptor, so the "
            "device was opened normally -- only the identify read tells them "
            "apart.")
    return identity


# --------------------------------------------------------------------------
# Which pad, rather than which model
# --------------------------------------------------------------------------
#
# `require` above answers "may this be written to". These answer "which one is
# this", which is a different question and the one a desk with two pads on it
# asks. Three names, in increasing order of what they cost and decreasing order
# of how stable they are:
#
#   * **uid** -- thirteen bytes, command 4, one exchange of its own.
#     **Measured**: this pad answered `14 20 6e 7a 1c 00 00 00 00 dc ba 3e 00`,
#     at the offset the SDK predicts. The one to key anything on.
#   * **mac** -- four bytes, free inside the command-1 reply, and **all zero on
#     this pad**. It was meant to be the cheap identifier and it is not one; see
#     `flydigi/motion.py:parse_mac` for the capture. Still read, still offered
#     as a selector, because a pad that does fill it in costs nothing to
#     support and because saying "it is empty here" is worth more than dropping
#     the field.
#   * **nickname** -- whatever the user typed, command 2 to read and 24 to
#     write. Not an identifier the pad guarantees, and the only one a person can
#     read out loud, which is what makes it worth having.
#
# **Reading is settled; writing is not.** Commands 1, 2 and 4 have all been sent
# to the pad on this desk and answered where the SDK said they would. Command 24
# has never been sent by anything here -- see `nickname_packet` for why sending
# Flydigi's own bytes is unlikely to work, and `tools/flydigi-devices name` for
# the one run that settles it.

CMD_READ_UID = 4
CMD_READ_NICKNAME = 2
CMD_WRITE_NICKNAME = 24

UID_LEN = 13
# Payload starts at raw 5 and the checksum needs the slot after the name, so a
# 32-byte packet leaves this many bytes for one. Refused rather than truncated:
# half a name written to flash is worse than a rejected one.
NICKNAME_MAX = 26


def _ask(ctrl, cmd_id, wait):
    """Send a bare command and return the reply that answers it, or None."""
    buf = build(cmd_id)
    buf[4] = 2
    buf[5] = checksum(buf, 3, 3 + buf[4])
    for reply in ctrl.send(buf, wait=wait):
        # Their `IsAck` is the command byte alone, at data[2] before the report
        # id is put back. Anything else on the node is somebody else's reply.
        if len(reply) > 3 and reply[0] == motion.INPUT_REPORT_ID \
                and reply[3] == cmd_id:
            return reply
    return None


def read_uid(ctrl, wait=0.6):
    """The pad's 13-byte uid as lower-case hex, or None if it does not answer.

    `ReadUidControllerCommandNewXInput`: command 4, payload from data[5] in
    their indexing and so from raw 6 in ours -- which is where every other
    single-frame reply puts its payload.
    """
    reply = _ask(ctrl, CMD_READ_UID, wait)
    if reply is None or len(reply) < 6 + UID_LEN:
        return None
    return "".join(f"{b:02x}" for b in reply[6 : 6 + UID_LEN])


def read_nickname(ctrl, wait=0.6):
    """The name stored on the pad, or None when it has never been given one.

    **The two SDKs disagree about where a nickname starts, and the pad's reply
    settles it.** `ReadNickNameControllerCommandNewXInput` slices
    `data.Slice(4, data.Length - 6)` -- raw 5 here, the index byte's slot, one
    earlier than every other payload -- while the charger's slices from its own
    data[6]. An unnamed pad on this desk answered

        04 5a a5 02 01 00 | 01 01 09 09 09 64 04 5e 00 00 ...
                        ^^ raw 5

    and only the earlier slice reads that as unset: raw 5 is 0x00, which is
    their own test for an erased name, while raw 6 is 0x01 and would have been
    decoded as the first byte of a name that is not there. So the controller
    SDK's offset is right for a controller, and the dock's is right for a dock.

    Their emptiness test is kept exactly: a first byte of 0x00 or 0xFF is an
    erased name rather than a name. The cut at the first NUL is ours -- their
    span is sized by a HID layer that hands them the report's own length, and a
    64-byte read here cannot know it.
    """
    reply = _ask(ctrl, CMD_READ_NICKNAME, wait)
    if reply is None or len(reply) < 8:
        return None
    raw = bytes(reply[5 : len(reply) - 2])
    if not raw or raw[0] in (0x00, 0xFF):
        return None
    name = raw.split(b"\x00", 1)[0].split(b"\xff", 1)[0]
    return name.decode("utf-8", "replace").strip() or None


def nickname_packet(name, reference=False):
    """The command-24 packet for `name`. Raises ValueError if it will not fit.

    **Flydigi's own version of this is broken for every name but a one-letter
    one**, and that is worth stating rather than quietly fixing:

        array[4] = (byte)(2 + bytes.Length);
        Array.Copy(bytes, 0, array, 5, bytes.Length);
        array[6] = array.Crc(3, 3 + array[4]);     // <- fixed index

    The checksum belongs at `3 + array[4]`, which is 6 only when the name is a
    single byte. For anything longer their write puts the checksum *inside the
    name*, overwriting its second byte, and leaves the real checksum slot at
    zero. A mapping packet with a bad checksum draws no reply from this pad at
    all, so either command 24 is not checksummed, or Space Station's rename has
    never worked past one character.

    `reference=True` produces their bytes exactly, for settling that on
    hardware. The default produces the packet the framing says is right.
    """
    raw = name.encode("utf-8")
    if not raw:
        raise ValueError("a nickname cannot be empty")
    if len(raw) > NICKNAME_MAX:
        raise ValueError(
            f"{name!r} is {len(raw)} bytes and the packet holds {NICKNAME_MAX}")
    buf = build(CMD_WRITE_NICKNAME)
    buf[4] = 2 + len(raw)
    buf[5 : 5 + len(raw)] = raw
    buf[6 if reference else 3 + buf[4]] = checksum(buf, 3, 3 + buf[4])
    return buf


def write_nickname(ctrl, name, wait=0.6, reference=False):
    """Name a pad, so a two-pad desk can be talked about. Returns the name.

    Verified by reading it back rather than by the ack, for the reason every
    write in `flydigi/settings.py` is: an ack carries the command id and nothing
    about what it changed.
    """
    buf = nickname_packet(name, reference=reference)
    replies = ctrl.send(buf, wait=wait)
    if not any(len(r) > 3 and r[3] == CMD_WRITE_NICKNAME for r in replies):
        raise NicknameRefused(
            f"the pad did not acknowledge the name {name!r}. Command 24 has "
            "never been sent to hardware from here, and Flydigi's own version "
            "of it puts the checksum in the wrong slot for a name longer than "
            "one byte -- see nickname_packet.")
    return read_nickname(ctrl, wait=wait)
