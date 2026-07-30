#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Regenerate flydigi/ds5_usb.py from descriptors captured off a real DualSense.

The capture is not committed -- work/ is gitignored -- so this exists to make the
generated module reproducible from hardware rather than trusted blindly. With a
DualSense plugged in:

    mkdir -p work/ds5-usb
    cp /sys/bus/usb/devices/<n>/descriptors            work/ds5-usb/descriptors.bin
    cp /sys/bus/hid/devices/0003:054C:0CE6.*/report_descriptor \\
                                                       work/ds5-usb/report_descriptor.bin
    tools/gen_ds5_usb.py

Find <n> with: grep -l 054c /sys/bus/usb/devices/*/idVendor
"""

import fcntl
import glob
import struct
import sys
import textwrap
from pathlib import Path

SRC = Path("work/ds5-usb")
OUT = Path("flydigi/ds5_usb.py")


# Feature reports must come off a live device -- they carry that unit's
# calibration, its MAC and its firmware build. The report descriptor declares
# the payload length for each, and asking for the wrong one times out rather
# than short-reading, which reads as "the device refused" instead of "you asked
# wrong". 0x0B and 0x0C exist on this firmware and not in inputtino's copy.
FEATURE_LENGTHS = {0x05: 40, 0x09: 19, 0x0B: 41, 0x0C: 41, 0x20: 63}

# From inputtino's PS5_PAIRING_INFO -- public, and therefore safe to commit.
SCRUB_CONTROLLER_BDADDR = bytes.fromhex("74e7d63a5335")
SCRUB_HOST_BDADDR = bytes.fromhex("1e00ee74d0bc")

# Report 0x0B carries a third six-byte field that is nobody's address we know:
# it sits between a `01` and a `00`, ahead of the host address, exactly where
# another entry would. What it is has not been established -- a previously
# paired host, part of a link key -- and that is precisely why it is replaced.
#
# The sweep below cannot find it. That looks for the values read out of report
# 0x09, and this is neither, so it went into a public repository unscrubbed and
# was only noticed by dumping the committed blob and asking what every byte was.
# The lesson is the general one: scrubbing what you recognise is not the same as
# scrubbing what identifies, and the second needs every field accounted for.
#
# inputtino publishes no 0x0B at all, so there is no matching placeholder to
# borrow; their host address goes in instead. Nothing reads this -- 0x0B is a
# newer-firmware report the playstation driver never asks for -- so a plausible
# shape is all it has to be.
SCRUB_UNKNOWN_FIELDS = {0x0B: [(10, 16)]}


def _hidiocgfeature(size):
    """_IOWR('H', 0x07, size)"""
    return 0xC0000000 | (size << 16) | (0x48 << 8) | 0x07


def read_features():
    """Read every feature report off a real, non-virtual DualSense."""
    target = None
    for path in sorted(glob.glob("/dev/hidraw*")):
        node = path.rsplit("/", 1)[1]
        try:
            text = Path(f"/sys/class/hidraw/{node}/device/uevent").read_text()
            real = Path(f"/sys/class/hidraw/{node}/device").resolve()
        except OSError:
            continue
        if "054C" in text and "0CE6" in text and "vhci_hcd" not in str(real):
            target = path
            break
    if target is None:
        return None, {}

    out = {}
    addresses = []
    real_controller = bytearray()
    real_host = bytearray()
    for report_id, length in sorted(FEATURE_LENGTHS.items()):
        buf = bytearray(length + 1)
        buf[0] = report_id
        try:
            with open(target, "rb+", buffering=0) as fh:
                got = fcntl.ioctl(fh, _hidiocgfeature(len(buf)), buf, True)
        except OSError as exc:
            print(f"  report 0x{report_id:02X}: {exc}", file=sys.stderr)
            continue
        # Strip the echoed report id; the serving code prepends it once.
        body = bytearray(buf[1:got])
        addresses.append(bytes(body[0:6]) if report_id == 0x09 else b"")
        if report_id == 0x09:
            # Report 0x09 carries Bluetooth addresses: bytes 0-5 are the
            # controller's own, 9-14 the paired host's. Both identify real
            # hardware, and this file is committed, so they are replaced --
            # unconditionally, with no flag to forget. inputtino's values are
            # already public in their repo, so substituting them discloses
            # nothing new and keeps the blob structurally valid. Nothing
            # validates a DualSense MAC; bytes 6-8 are identical across two
            # unrelated controllers, i.e. constant rather than identifying.
            real_controller[:] = body[0:6]
            real_host[:] = body[9:15]
            body[0:6] = SCRUB_CONTROLLER_BDADDR
            body[9:15] = SCRUB_HOST_BDADDR
        out[report_id] = bytes(body)

    # 0x09 is not the only report carrying addresses -- 0x0B holds both as well,
    # at different offsets. Rather than hardcode where each report hides them,
    # sweep every blob for the real values and replace wherever they appear.
    # This was found only by grepping the committed file for the address after
    # believing it already scrubbed.
    if real_controller:
        for rid, body in list(out.items()):
            b = bytearray(body)
            for real, fake in ((bytes(real_controller), SCRUB_CONTROLLER_BDADDR),
                               (bytes(real_host), SCRUB_HOST_BDADDR)):
                start = b.find(real)
                while start != -1:
                    b[start:start + 6] = fake
                    start = b.find(real, start + 1)
            out[rid] = bytes(b)

    # Then the fields no sweep could find, by position. See
    # SCRUB_UNKNOWN_FIELDS: the sweep replaces values it recognises, and an
    # address this controller was paired with before is not one of them.
    for rid, spans in SCRUB_UNKNOWN_FIELDS.items():
        body = out.get(rid)
        if body is None:
            continue
        b = bytearray(body)
        for start, end in spans:
            if end <= len(b):
                b[start:end] = SCRUB_HOST_BDADDR[:end - start]
        out[rid] = bytes(b)
    return target, out


def hexblock(data, indent=4):
    """Format bytes as an indented, wrapped hex string literal."""
    pad = " " * indent
    lines = textwrap.wrap("".join(f"{b:02x}" for b in data), 64)
    return "\n".join(f'{pad}"{line}"' for line in lines)


def main():
    try:
        raw = (SRC / "descriptors.bin").read_bytes()
        rdesc = (SRC / "report_descriptor.bin").read_bytes()
    except FileNotFoundError as exc:
        sys.exit(f"{exc}\n\nSee the docstring: capture from a real DualSense first.")

    device, config = raw[:18], raw[18:]

    # Sanity-check the capture rather than trusting it. A short read of a sysfs
    # file is silent, and a truncated descriptor would enumerate as something
    # subtly wrong -- the same trap that cost us 151 of 289 bytes on the gadget.
    if len(device) != 18 or device[1] != 0x01:
        sys.exit("device descriptor is not 18 bytes of bDescriptorType 1")
    vid, pid = struct.unpack_from("<HH", device, 8)
    if (vid, pid) != (0x054C, 0x0CE6):
        sys.exit(f"not a DualSense: {vid:04x}:{pid:04x}")
    total = struct.unpack_from("<H", config, 2)[0]
    if total != len(config):
        sys.exit(f"config wTotalLength {total} != {len(config)} bytes captured")
    if rdesc[0:1] != b"\x05":  # Usage Page
        sys.exit("report descriptor does not start with a Usage Page item")

    source, features = read_features()
    if features:
        print(f"read {len(features)} feature reports from {source}")
    elif "FEATURE_REPORTS = {" in (OUT.read_text() if OUT.exists() else ""):
        # Refuse rather than regenerate an empty block. Feature reports can only
        # come from a live controller, so running this without one would quietly
        # delete blobs that took hardware to obtain -- and the result still
        # imports, still enumerates, and only fails later at the point where a
        # driver reads calibration.
        sys.exit("no real DualSense on hidraw, and flydigi/ds5_usb.py already "
                 "holds feature reports.\n"
                 "Refusing to overwrite them with nothing -- plug one in, or "
                 "delete the file first if that is really what you want.")

    feature_block = "\n".join(
        f"    0x{rid:02X}: bytes.fromhex(\n{hexblock(body, 8)}\n    ),"
        for rid, body in sorted(features.items())
    ) or "    # none captured -- rerun with a real DualSense connected"

    OUT.write_text(f'''\
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""USB descriptors read off a real DualSense (bcdDevice 0100).

Generated by tools/gen_ds5_usb.py -- do not edit by hand.

These are the bytes a host compares against, so they are served verbatim rather
than rebuilt from fields. Note DEVICE_DESC advertises bNumConfigurations 1 and
iSerial 0: the real controller carries no serial string.

REPORT_DESC is {len(rdesc)} bytes, read from the hardware. inputtino's copy was 273 --
it stopped short of feature reports 0x0B and 0x0C -- and its calibration and
firmware blobs belonged to a different unit of a different vintage. Both tiers
use this file now; the inputtino data is gone.
"""

# 18 bytes. Device descriptor: 054c:0ce6, bcdUSB 2.00, class 0 (per-interface).
DEVICE_DESC = bytes.fromhex(
{hexblock(device)}
)

# {len(config)} bytes, wTotalLength {total}. The whole configuration in one blob:
#   iface 0    Audio Control     bcdADC 1.00 (UAC1, not UAC2)
#   iface 1/1  Audio Streaming   4ch s16le 48000, EP 0x01 OUT iso ADAPTIVE, 392 B
#   iface 2/1  Audio Streaming   2ch s16le 48000, EP 0x82 IN  iso ASYNC,    196 B
#   iface 3    HID               EP 0x84 IN + EP 0x03 OUT, interrupt, 64 B
# Haptic actuators are the RL/RR pair of wChannelConfig 0x0033, i.e. ch2 and ch3.
CONFIG_DESC = bytes.fromhex(
{hexblock(config)}
)

# {len(rdesc)} bytes, from /sys/bus/hid/devices/0003:054C:0CE6.*/report_descriptor.
REPORT_DESC = bytes.fromhex(
{hexblock(rdesc)}
)

VENDOR_ID = 0x{vid:04X}
PRODUCT_ID = 0x{pid:04X}

# String descriptors, by index, as the real device reports them.
STRINGS = {{
    1: "Sony Interactive Entertainment",
    2: "DualSense Wireless Controller",
}}
LANGIDS = (0x0409,)

# Feature reports, read off the same physical controller, WITHOUT the leading
# report id -- whoever serves these prepends it exactly once. inputtino's copies
# included the id, which is a trap: prefixing those shifts every byte of
# calibration data by one and the pad still enumerates.
#
# Every Bluetooth address here is a placeholder. 0x09 and 0x0B each carry the
# controller's own and its paired host's, swept and replaced; 0x0B also has a
# third six-byte field of unestablished meaning, which is replaced by position
# because no sweep could recognise it. 0x05 is this unit's gyro and accel
# calibration -- particular to the controller, but not an identifier, and what
# makes the emulation read correctly.
FEATURE_REPORTS = {{
{feature_block}
}}
''')

    print(f"wrote {OUT}: device {len(device)}, config {len(config)}, report {len(rdesc)} bytes")


if __name__ == "__main__":
    main()
