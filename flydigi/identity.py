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
# **26, measured to the byte.** The payload starts at buf[5] in a 32-byte
# packet, which leaves 27 bytes -- but the pad stores `buf[4] - 1` of them,
# one more than the name, whether or not anything was written there. So a
# 27-byte name asks it to read past the end of the packet, and 27 and 28 both
# came back truncated to 26 with the tail lost. Refused rather than truncated
# here: a name silently shortened on the device is worse than a rejected one.
#
# Bytes, not characters. UTF-8 round-trips -- Cyrillic and CJK names were
# written and read back intact -- so a name of eight characters may be 24 bytes.
NICKNAME_MAX = 26


def answers(cmd_id):
    """`Controller.send`'s `until`, for a one-frame reply to `cmd_id`.

    **Worth passing, not optional.** Without an `until` these reads sit out the
    whole timeout, and the vendor node is not quiet while they do: it delivers
    input reports at about 970 Hz, so a 0.6 s wait appends some six hundred
    packets nobody wants and tests each one in Python. Three of those per pad,
    on a bus poll every ten seconds, is most of a second in a hot loop on the
    worker thread -- which the GUI thread then queues behind for the GIL every
    time a QML binding reads a model property. It shows up as the whole window
    dropping frames on a timer, with nothing on screen to blame for it.
    """
    def check(replies):
        # Their `IsAck` is the command byte alone, at data[2] before the report
        # id is put back. Anything else on the node is somebody else's reply.
        reply = replies[-1]
        return (len(reply) > 3 and reply[0] == motion.INPUT_REPORT_ID
                and reply[3] == cmd_id)
    return check


def _ask(ctrl, cmd_id, wait):
    """Send a bare command and return the reply that answers it, or None."""
    buf = build(cmd_id)
    buf[4] = 2
    buf[5] = checksum(buf, 3, 3 + buf[4])
    for reply in ctrl.send(buf, wait=wait, until=answers(cmd_id)):
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

    **The payload is at raw 6, like every other single-frame reply**, and this
    took writing a name to find out. It was read from raw 5 first, on the
    strength of `ReadNickNameControllerCommandNewXInput` slicing
    `data.Slice(4, data.Length - 6)` where the charger SDK slices from its own
    data[6] -- and an unnamed pad seemed to agree, answering 0x00 at raw 5,
    which is Flydigi's own test for an erased name. It was the index byte, zero
    for every single-frame reply. Writing "Desk" and reading it back settled it:

        04 5a a5 02 01 00 | 44 a5 73 6b 00 00 ...
                            ^^ raw 6

    So the two SDKs do not disagree after all, and the reference's own
    indexing for this one command is one byte off from every other.

    **A pad that has never been named does not answer with zeroes.** The one
    here shipped with `01 01 09 09 09 64 04 5e` in the field, and Flydigi's
    emptiness test -- first byte neither 0x00 nor 0xFF -- calls that a name, so
    Space Station shows it as one. Their test is kept, and a second one added:
    a field that is not printable text is not a name either. Showing
    `\\x01\\x01\\t\\t\\td\\x04^` in a device picker would be worse than showing
    nothing, and this is what a factory-fresh pad actually contains.
    """
    reply = _ask(ctrl, CMD_READ_NICKNAME, wait)
    if reply is None or len(reply) < 8:
        return None
    raw = bytes(reply[6:]).split(b"\x00", 1)[0].split(b"\xff", 1)[0]
    if not raw or raw[0] in (0x00, 0xFF):
        return None
    try:
        name = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    # `str.isprintable` is false for the control bytes a never-named pad holds,
    # and true for every name a person would type, spaces included.
    if not name or not name.isprintable():
        return None
    return name


def nickname_packet(name, reference=False):
    """The command-24 packet for `name`. Raises ValueError if it will not fit.

    **No checksum, and that is measured rather than lazy.** Three things about
    this command came out of writing names to the pad here and reading them
    back, and every one of them contradicts what the SDK suggests:

      * **It is not checksum-validated.** A packet with the checksum slot left
        at zero is acknowledged and stored, where a mapping packet with a bad
        checksum draws no reply at all.
      * **The pad stores `buf[4] - 1` bytes from buf[5]**, which is one more
        than the name. So a checksum written where the framing says -- at
        `3 + buf[4]`, immediately after the name -- is stored *as part of the
        name*: "Desk" with a checksum came back as `44 65 73 6b a5`, and
        without one as `44 65 73 6b`.
      * **Flydigi's builder is broken anyway**, for every name but a one-letter
        one::

            array[4] = (byte)(2 + bytes.Length);
            Array.Copy(bytes, 0, array, 5, bytes.Length);
            array[6] = array.Crc(3, 3 + array[4]);     // <- fixed index

        Index 6 is the right slot only when the name is a single byte. For
        anything longer it overwrites the name's second character: "Desk" sent
        their way is stored by the pad as `44 a5 73 6b`. Space Station's rename
        has never worked past one character, and the pad was never the reason.

    So the packet this builds carries the name and nothing after it.
    `reference=True` still produces their bytes exactly, which is how the above
    was established and how it can be re-checked.
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
    if reference:
        buf[6] = checksum(buf, 3, 3 + buf[4])
    return buf


def write_nickname(ctrl, name, wait=0.6, reference=False):
    """Name a pad, so a two-pad desk can be talked about. Returns the name.

    Verified by reading it back rather than by the ack, for the reason every
    write in `flydigi/settings.py` is: an ack carries the command id and nothing
    about what it changed. Here it matters more than usual -- the pad
    acknowledges this command whatever is in it, including Flydigi's own
    corrupted form, so the ack is worth nothing at all as evidence.
    """
    buf = nickname_packet(name, reference=reference)
    replies = ctrl.send(buf, wait=wait)
    if not any(len(r) > 3 and r[3] == CMD_WRITE_NICKNAME for r in replies):
        raise NicknameRefused(
            f"the pad did not acknowledge the name {name!r} -- which it does "
            "even for a malformed packet, so silence here means it is not "
            "listening at all.")
    return read_nickname(ctrl, wait=wait)
