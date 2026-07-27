"""Minimal evdev reader -- no python-evdev dependency.

The Apex 5's interface 0 binds to the kernel's xpad driver, which decodes the
pad into a normal evdev gamepad. That is where we read real input from, rather
than parsing the vendor report stream ourselves.

evdev is event-driven: we block until the pad reports, so there is no polling
loop to add latency or jitter.
"""
import ctypes
import fcntl
import glob
import os
import struct

# struct input_event { struct timeval time; __u16 type, code; __s32 value; }
_EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(_EVENT_FMT)

EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03

ABS_X, ABS_Y, ABS_Z = 0x00, 0x01, 0x02
ABS_RX, ABS_RY, ABS_RZ = 0x03, 0x04, 0x05
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11

BTN_SOUTH, BTN_EAST = 0x130, 0x131
BTN_NORTH, BTN_WEST = 0x133, 0x134
BTN_TL, BTN_TR = 0x136, 0x137
BTN_SELECT, BTN_START, BTN_MODE = 0x13A, 0x13B, 0x13C
BTN_THUMBL, BTN_THUMBR = 0x13D, 0x13E

_IOC_READ = 2


def _eviocgabs(code):
    # EVIOCGABS(abs) = _IOR('E', 0x40 + abs, struct input_absinfo) -- 6 * __s32
    size = 24
    return (_IOC_READ << 30) | (size << 16) | (ord("E") << 8) | (0x40 + code)


class AbsInfo:
    __slots__ = ("value", "minimum", "maximum", "fuzz", "flat", "resolution")

    def __init__(self, raw):
        (self.value, self.minimum, self.maximum,
         self.fuzz, self.flat, self.resolution) = struct.unpack("6i", raw)

    def normalize(self, value, lo=0.0, hi=1.0):
        """Scale a raw axis value into [lo, hi]."""
        span = self.maximum - self.minimum
        if span <= 0:
            return lo
        frac = (value - self.minimum) / span
        return lo + frac * (hi - lo)


def find_device(name=None, vendor=None, product=None):
    """Return the /dev/input/eventN path of a matching device.

    Numbering shifts as devices come and go, so always resolve by name or
    vendor/product rather than hardcoding a path.
    """
    for path in sorted(glob.glob("/dev/input/event*"),
                       key=lambda p: int(p.rsplit("event", 1)[1])):
        node = os.path.basename(path)
        base = f"/sys/class/input/{node}/device"
        try:
            with open(f"{base}/name") as fh:
                dev_name = fh.read().strip()
        except OSError:
            continue
        if name and name.lower() not in dev_name.lower():
            continue
        if vendor or product:
            try:
                with open(f"{base}/id/vendor") as fh:
                    vid = int(fh.read().strip(), 16)
                with open(f"{base}/id/product") as fh:
                    pid = int(fh.read().strip(), 16)
            except OSError:
                continue
            if vendor and vid != vendor:
                continue
            if product and pid != product:
                continue
        return path, dev_name
    return None, None


class Reader:
    """Reads a gamepad's evdev node and tracks current axis/button state."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        self.abs = {}
        self.axes = {}
        self.keys = {}
        for code in (ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ,
                     ABS_HAT0X, ABS_HAT0Y):
            try:
                buf = bytearray(24)
                fcntl.ioctl(self.fd, _eviocgabs(code), buf, True)
                info = AbsInfo(bytes(buf))
            except OSError:
                continue
            self.abs[code] = info
            self.axes[code] = info.value

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def fileno(self):
        return self.fd

    def read(self):
        """Consume pending events. Returns True if a SYN completed a frame."""
        synced = False
        try:
            data = os.read(self.fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return False
        for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _s, _us, etype, code, value = struct.unpack_from(
                _EVENT_FMT, data, offset)
            if etype == EV_ABS:
                self.axes[code] = value
            elif etype == EV_KEY:
                self.keys[code] = value
            elif etype == EV_SYN:
                synced = True
        return synced

    def axis(self, code, lo=0.0, hi=1.0, default=0.0):
        info = self.abs.get(code)
        if info is None:
            return default
        return info.normalize(self.axes.get(code, info.value), lo, hi)

    def pressed(self, code):
        return bool(self.keys.get(code))
