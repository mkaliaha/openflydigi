# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Which Flydigi device is on the other end, and what it is allowed to be sent.

**`find_device` cannot tell two Flydigi pads apart.** It matches on the vendor id
and the vendor collection's report-descriptor prefix, and every model in the
range shares both -- so a Vader 4 Pro plugged in beside an Apex 5, or instead of
it, opens exactly the same way. Nothing above that layer noticed, which meant an
Apex 5 profile could be written into a Vader's flash with no error anywhere.

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


# DeviceType -> DeviceCode, straight from `FlydigiControllerFactory`. One entry
# per SKU rather than per model, which is why a single code owns several numbers
# -- 128 and 129 are the Apex 5 base model and the Eva edition, and SDL's own
# Flydigi driver recognises exactly that pair.
DEVICE_TYPES = {
    24: "k1", 26: "k1", 29: "k1",
    84: "k2", 86: "k2", 87: "k2", 92: "k2", 93: "k2",
    102: "k2", 103: "k2", 104: "k2",
    128: "k5", 129: "k5", 133: "k5", 134: "k5", 135: "k5", 136: "k5",
    149: "k6", 150: "k6",
    28: "f3", 80: "f3", 81: "f3", 88: "f3",
    85: "f4", 91: "f4",
    130: "f5", 144: "f5", 145: "f5",
    25: "fp1", 30: "fp1", 31: "fp1", 82: "fp1", 83: "fp1", 95: "fp1",
    132: "fp1", 146: "fp1", 147: "fp1", 148: "fp1",
}

PRODUCT_NAMES = {
    "k1": "Apex 3",
    "k2": "Apex 4",          # not the Apex 2
    "k5": "Apex 5",
    "k6": "Apex 6",
    "f3": "Vader 3",
    "f4": "Vader 4",
    "f5": "Vader 5",
    "fp1": "Direwolf",
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
