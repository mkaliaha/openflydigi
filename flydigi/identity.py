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

The table is `docs/findings-other-devices.md`, which is the same dispatch read
out of the SDK. Codes are Flydigi's, and they do not follow the product names:
`k2` is the Apex **4**, and there is no `k3` or `k4`.
"""
from . import motion


class WrongDevice(Exception):
    """A device answered, and it is not one this caller is willing to write to."""


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
