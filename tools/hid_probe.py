#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Passive HID probe for Flydigi Apex 5.

Reads hidraw device info and report descriptors. Writes nothing to the device.
"""
import fcntl
import glob
import struct
import sys

_IOC_READ = 2


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


HIDIOCGRDESCSIZE = _ioc(_IOC_READ, "H", 0x01, 4)
HIDIOCGRDESC = _ioc(_IOC_READ, "H", 0x02, 4 + 4096)
HIDIOCGRAWINFO = _ioc(_IOC_READ, "H", 0x03, 8)
HIDIOCGRAWNAME = _ioc(_IOC_READ, "H", 0x04, 256)

FLYDIGI_VID = 0x37D7

ITEM_MAIN_INPUT = 0x80
ITEM_MAIN_OUTPUT = 0x90
ITEM_MAIN_FEATURE = 0xB0
ITEM_MAIN_COLLECTION = 0xA0
ITEM_MAIN_END_COLLECTION = 0xC0
ITEM_GLOBAL_USAGE_PAGE = 0x04
ITEM_GLOBAL_REPORT_SIZE = 0x74
ITEM_GLOBAL_REPORT_ID = 0x84
ITEM_GLOBAL_REPORT_COUNT = 0x94
ITEM_LOCAL_USAGE = 0x08


def parse_descriptor(desc):
    """Return {report_id: {'input': bits, 'output': bits, 'feature': bits}} and usage pages."""
    reports = {}
    usage_pages = []
    report_id = 0
    report_size = 0
    report_count = 0
    i = 0
    n = len(desc)
    while i < n:
        prefix = desc[i]
        size = prefix & 0x03
        if size == 3:
            size = 4
        tag = prefix & 0xFC
        data = desc[i + 1 : i + 1 + size]
        value = int.from_bytes(data, "little") if data else 0
        i += 1 + size

        if tag == ITEM_GLOBAL_REPORT_ID:
            report_id = value
        elif tag == ITEM_GLOBAL_REPORT_SIZE:
            report_size = value
        elif tag == ITEM_GLOBAL_REPORT_COUNT:
            report_count = value
        elif tag == ITEM_GLOBAL_USAGE_PAGE:
            usage_pages.append(value)
        elif tag in (ITEM_MAIN_INPUT, ITEM_MAIN_OUTPUT, ITEM_MAIN_FEATURE):
            kind = {
                ITEM_MAIN_INPUT: "input",
                ITEM_MAIN_OUTPUT: "output",
                ITEM_MAIN_FEATURE: "feature",
            }[tag]
            entry = reports.setdefault(
                report_id, {"input": 0, "output": 0, "feature": 0}
            )
            entry[kind] += report_size * report_count
    return reports, usage_pages


def probe(path):
    try:
        fd = open(path, "rb")
    except OSError as exc:
        print(f"{path}: cannot open: {exc}")
        return

    with fd:
        info = bytearray(8)
        fcntl.ioctl(fd, HIDIOCGRAWINFO, info, True)
        bustype, vendor, product = struct.unpack("<IhH", bytes(info))
        vendor &= 0xFFFF

        name = bytearray(256)
        fcntl.ioctl(fd, HIDIOCGRAWNAME, name, True)
        name_str = bytes(name).split(b"\x00")[0].decode("utf-8", "replace")

        size_buf = bytearray(4)
        fcntl.ioctl(fd, HIDIOCGRDESCSIZE, size_buf, True)
        desc_size = struct.unpack("<I", bytes(size_buf))[0]

        desc_buf = bytearray(4 + 4096)
        desc_buf[0:4] = struct.pack("<I", desc_size)
        fcntl.ioctl(fd, HIDIOCGRDESC, desc_buf, True)
        desc = bytes(desc_buf[4 : 4 + desc_size])

    flag = "  <<< FLYDIGI" if vendor == FLYDIGI_VID else ""
    print(f"=== {path} ==={flag}")
    print(f"  name      : {name_str}")
    print(f"  vid:pid   : {vendor:04x}:{product:04x}  bus={bustype}")
    print(f"  desc size : {desc_size} bytes")

    reports, usage_pages = parse_descriptor(desc)
    print(f"  usage pages: {[hex(u) for u in dict.fromkeys(usage_pages)]}")
    if not reports:
        print("  reports   : (none parsed)")
    for rid in sorted(reports):
        r = reports[rid]
        parts = []
        for kind in ("input", "output", "feature"):
            if r[kind]:
                parts.append(f"{kind}={r[kind] // 8}B")
        label = f"id 0x{rid:02x}" if rid else "id none"
        print(f"  report {label:>10}: {', '.join(parts) if parts else '(empty)'}")
    print(f"  raw descriptor: {desc.hex()}")
    print()


def main():
    paths = sys.argv[1:] or sorted(glob.glob("/dev/hidraw*"))
    for path in paths:
        probe(path)


if __name__ == "__main__":
    main()
