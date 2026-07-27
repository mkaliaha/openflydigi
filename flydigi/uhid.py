# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Minimal /dev/uhid binding.

uhid lets userspace create a kernel-side HID device. Unlike uinput (which only
carries evdev events) a uhid device speaks real HID, so a game can send us
output reports -- which is the whole point here: DualSense adaptive-trigger and
rumble commands arrive as HID output reports.

Protocol reference: linux/uapi/linux/uhid.h. Every event is a u32 type followed
by a union; writes send the full union size.

No third-party dependencies -- this is the only thing standing between us and a
virtual DualSense, and it is small enough not to warrant a native library.
"""
import ctypes
import os
import select

UHID_DEVICE = "/dev/uhid"

# Event types (uapi/linux/uhid.h)
UHID_CREATE2 = 11
UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_INPUT2 = 12
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14

BUS_USB = 0x03
BUS_BLUETOOTH = 0x05

HID_REPORT_TYPE_FEATURE = 0
HID_REPORT_TYPE_OUTPUT = 1
HID_REPORT_TYPE_INPUT = 2

_RD_MAX = 4096
_DATA_MAX = 4096


class _Create2Req(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("name", ctypes.c_char * 128),
        ("phys", ctypes.c_char * 64),
        ("uniq", ctypes.c_char * 64),
        ("rd_size", ctypes.c_uint16),
        ("bus", ctypes.c_uint16),
        ("vendor", ctypes.c_uint32),
        ("product", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("country", ctypes.c_uint32),
        ("rd_data", ctypes.c_uint8 * _RD_MAX),
    ]


class _Input2Req(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_uint8 * _DATA_MAX),
    ]


class _OutputReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("data", ctypes.c_uint8 * _DATA_MAX),
        ("size", ctypes.c_uint16),
        ("rtype", ctypes.c_uint8),
    ]


class _GetReportReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("rnum", ctypes.c_uint8),
        ("rtype", ctypes.c_uint8),
    ]


class _SetReportReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("rnum", ctypes.c_uint8),
        ("rtype", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_uint8 * _DATA_MAX),
    ]


class _GetReportReplyReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("err", ctypes.c_uint16),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_uint8 * _DATA_MAX),
    ]


class _SetReportReplyReq(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("err", ctypes.c_uint16),
    ]


class _Union(ctypes.Union):
    _pack_ = 1
    _fields_ = [
        ("create2", _Create2Req),
        ("input2", _Input2Req),
        ("output", _OutputReq),
        ("get_report", _GetReportReq),
        ("get_report_reply", _GetReportReplyReq),
        ("set_report", _SetReportReq),
        ("set_report_reply", _SetReportReplyReq),
    ]


class _Event(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("u", _Union),
    ]


EVENT_SIZE = ctypes.sizeof(_Event)


class UHIDError(Exception):
    pass


class Device:
    """A virtual HID device.

    Usage:
        dev = Device(name, vendor, product, report_descriptor)
        dev.send_input(bytes(...))          # input report -> kernel -> game
        for rtype, data in dev.poll():      # output reports from the game
            ...
    """

    def __init__(self, name, vendor, product, report_descriptor,
                 phys="", uniq="", bus=BUS_USB, version=0, country=0,
                 feature_reports=None):
        # Answers to UHID_GET_REPORT, keyed by report number. A driver probing
        # the device (hid-playstation does this for DualSense calibration and
        # pairing info) will fail and detach if these come back empty.
        self.feature_reports = dict(feature_reports or {})
        if len(report_descriptor) > _RD_MAX:
            raise ValueError("report descriptor too large")
        try:
            self.fd = os.open(UHID_DEVICE, os.O_RDWR)
        except FileNotFoundError as exc:
            raise UHIDError(
                "/dev/uhid missing -- the uhid module is not loaded"
            ) from exc
        except PermissionError as exc:
            raise UHIDError(
                "/dev/uhid not writable -- needs a udev rule or group membership"
            ) from exc

        event = _Event()
        event.type = UHID_CREATE2
        create = event.u.create2
        create.name = name.encode()[:127]
        create.phys = phys.encode()[:63]
        create.uniq = uniq.encode()[:63]
        create.rd_size = len(report_descriptor)
        create.bus = bus
        create.vendor = vendor
        create.product = product
        create.version = version
        create.country = country
        ctypes.memmove(create.rd_data, report_descriptor, len(report_descriptor))
        self._write(event)

        self.name = name
        self._started = False
        self._open = False

    def _write(self, event):
        written = os.write(self.fd, bytes(memoryview(event).cast("B")))
        if written != EVENT_SIZE:
            raise UHIDError(f"short write to /dev/uhid ({written}/{EVENT_SIZE})")

    def send_input(self, data):
        """Deliver an input report to the kernel (and so to any reader)."""
        if len(data) > _DATA_MAX:
            raise ValueError("input report too large")
        event = _Event()
        event.type = UHID_INPUT2
        event.u.input2.size = len(data)
        ctypes.memmove(event.u.input2.data, bytes(data), len(data))
        self._write(event)

    def poll(self, timeout=0.0):
        """Yield (rtype, data) for each output report the kernel hands us.

        Also tracks START/STOP/OPEN/CLOSE so callers can tell whether anything
        is actually listening, and answers GET/SET_REPORT so readers do not
        block waiting for a reply we never send.
        """
        while True:
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if not ready:
                return
            raw = os.read(self.fd, EVENT_SIZE)
            if not raw:
                return
            event = _Event()
            ctypes.memmove(ctypes.byref(event), raw, min(len(raw), EVENT_SIZE))

            if event.type == UHID_START:
                self._started = True
            elif event.type == UHID_STOP:
                self._started = False
            elif event.type == UHID_OPEN:
                self._open = True
            elif event.type == UHID_CLOSE:
                self._open = False
            elif event.type == UHID_OUTPUT:
                out = event.u.output
                size = min(out.size, _DATA_MAX)
                yield out.rtype, bytes(bytearray(out.data[:size]))
            elif event.type == UHID_GET_REPORT:
                rnum = event.u.get_report.rnum
                payload = self.feature_reports.get(rnum)
                reply = _Event()
                reply.type = UHID_GET_REPORT_REPLY
                reply.u.get_report_reply.id = event.u.get_report.id
                if payload is None:
                    reply.u.get_report_reply.err = 0xFFFF  # -EINVAL
                    reply.u.get_report_reply.size = 0
                else:
                    reply.u.get_report_reply.err = 0
                    reply.u.get_report_reply.size = len(payload)
                    ctypes.memmove(reply.u.get_report_reply.data,
                                   bytes(payload), len(payload))
                self._write(reply)
            elif event.type == UHID_SET_REPORT:
                setr = event.u.set_report
                size = min(setr.size, _DATA_MAX)
                reply = _Event()
                reply.type = UHID_SET_REPORT_REPLY
                reply.u.set_report_reply.id = setr.id
                reply.u.set_report_reply.err = 0
                self._write(reply)
                yield setr.rtype, bytes(bytearray(setr.data[:size]))
            timeout = 0.0  # drain anything else already queued

    @property
    def started(self):
        """True once the kernel has bound a driver to the device."""
        return self._started

    @property
    def opened(self):
        """True while something has the device open for reading."""
        return self._open

    def destroy(self):
        if self.fd is None:
            return
        try:
            event = _Event()
            event.type = UHID_DESTROY
            self._write(event)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.destroy()
