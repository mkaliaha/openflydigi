# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A DualSense, served to the local kernel over usbip.py.

Descriptors are the real controller's, byte for byte (flydigi/ds5_usb.py), so the
kernel sees all four interfaces: audio control, two audio streaming, and HID.
`hid-playstation` binds the HID interface and `snd-usb-audio` the audio ones --
as siblings of one USB device, which is the entire point.

The haptic stream arrives as isochronous OUT on endpoint 0x01: 4 channels, s16le,
48 kHz, with the actuators on channels 2 and 3. See docs/findings-haptics.md.
"""

import errno
import struct

from . import ds5_usb

# Endpoint NUMBERS, not addresses. usbip_header_basic carries `ep` as the plain
# endpoint number 0..15 and puts the direction in its own field, so the high bit
# of a descriptor's bEndpointAddress never appears on the wire. Comparing
# against 0x84 silently stalls every input report -- and only the IN endpoints
# are affected, because an OUT address already equals its number, which makes
# the bug look like "output works, input is broken" rather than an off-by-mask.
EP_ISO_OUT = 1          # addr 0x01, haptic audio, host -> device
EP_ISO_IN = 2           # addr 0x82, microphone; declared, never fed
EP_HID_OUT = 3          # addr 0x03, output reports (rumble, lightbar, triggers)
EP_HID_IN = 4           # addr 0x84, input reports

# bmRequestType
DIR_DEVICE_TO_HOST = 0x80
TYPE_MASK = 0x60
TYPE_STANDARD = 0x00
TYPE_CLASS = 0x20

# bRequest, standard
GET_STATUS = 0x00
CLEAR_FEATURE = 0x01
SET_FEATURE = 0x03
GET_DESCRIPTOR = 0x06
GET_CONFIGURATION = 0x08
SET_CONFIGURATION = 0x09
GET_INTERFACE = 0x0A
SET_INTERFACE = 0x0B

# bRequest, HID class
HID_GET_REPORT = 0x01
HID_SET_REPORT = 0x09
HID_SET_IDLE = 0x0A

# Descriptor types
DT_DEVICE = 1
DT_CONFIG = 2
DT_STRING = 3
DT_HID_REPORT = 0x22

# Feature reports the playstation driver and SDL read at probe: 0x05 is
# calibration, 0x09 the MAC that SDL builds a joystick GUID from, 0x20 the
# firmware build string.
#
# These come off the real controller (flydigi/ds5_usb.py) and hold NO leading
# report id -- it is prepended once below. inputtino's blobs in ps5_data.py do
# include the id, and prefixing those served `05 05 00 00 ...`, shifting every
# byte of calibration by one. The pad still enumerated and Steam still showed it
# with correct artwork, so nothing looked wrong; it is the kind of fault only a
# byte-level diff against real hardware finds.
#
# They also differ by more than the id. inputtino's firmware string is
# "Jun 19 2023 14:47:34" against this unit's "Jul  4 2025 10:38:40", with 42 of
# 63 bytes different -- a different controller of a different vintage.
FEATURE_REPORTS = ds5_usb.FEATURE_REPORTS

INPUT_REPORT_LEN = 64


def neutral_report():
    """A report with nothing pressed and both sticks centred."""
    report = bytearray(INPUT_REPORT_LEN)
    report[0] = 0x01            # report id
    report[1:5] = b"\x80\x80\x80\x80"   # LX LY RX RY
    report[8] = 0x08            # hat: released
    return bytes(report)


class DualSense:
    """Implements flydigi.usbip.Device for a DualSense."""

    speed = 3  # USB_SPEED_HIGH

    def __init__(self, on_output=None, on_haptics=None):
        self.on_output = on_output
        self.on_haptics = on_haptics

        # Feature reports come from ds5_usb.py, whose 0x09 carries inputtino's
        # public Bluetooth addresses rather than any real controller's. That is
        # deliberate twice over: it keeps hardware identity out of the repo, and
        # it stops us colliding with a real DualSense. hid-playstation keys a
        # controller by that MAC and names sysfs entries after it, so a perfect
        # twin evicts the real pad -- it binds usbhid, gets no hidraw node, and
        # silently stops being a gamepad. Observed exactly that way.
        self.features = dict(ds5_usb.FEATURE_REPORTS)
        self.configuration = 0
        self.altsetting = {}
        self.haptic_urbs = 0
        self.haptic_bytes = 0
        self.stalls = {}
        self.first_iso = None
        # State the device answers a poll from. Real hardware answers when
        # polled, from whatever it currently holds; it does not run a clock of
        # its own. Completing a parked URB from another thread's timer means two
        # independent 4 ms clocks beating against each other -- up to a full
        # interval of added latency, and a missed poll when they drift in phase.
        #
        # This holds the *state object*, not a packed report, because pack()
        # advances the sequence number: packing on a loop that runs faster than
        # the host polls makes seq jump between delivered reports, and SDL's PS5
        # driver reads it for drop detection. Packing here advances it exactly
        # once per report that actually goes out.
        self.input_state = None

    # -- control ------------------------------------------------------------

    def control(self, setup, data):
        bm, request, value, index, length = struct.unpack("<BBHHH", setup)

        if (bm & TYPE_MASK) == TYPE_STANDARD:
            return self._standard(bm, request, value, index, length)
        if (bm & TYPE_MASK) == TYPE_CLASS:
            return self._class(bm, request, value, index, length, data)
        return -errno.EPIPE

    def _standard(self, bm, request, value, index, length):
        if request == GET_DESCRIPTOR:
            return self._descriptor(value >> 8, value & 0xFF, length)
        if request == SET_CONFIGURATION:
            self.configuration = value & 0xFF
            return b""
        if request == GET_CONFIGURATION:
            return bytes([self.configuration])
        if request == SET_INTERFACE:
            # Alt 1 on interfaces 1 and 2 is the host opening an audio stream.
            self.altsetting[index] = value
            return b""
        if request == GET_INTERFACE:
            return bytes([self.altsetting.get(index, 0)])
        if request == GET_STATUS:
            # Self-powered, no remote wakeup.
            return b"\x01\x00" if not (bm & 0x1F) else b"\x00\x00"
        if request in (CLEAR_FEATURE, SET_FEATURE):
            return b""
        return -errno.EPIPE

    def _descriptor(self, kind, index, length):
        if kind == DT_DEVICE:
            return ds5_usb.DEVICE_DESC[:length]
        if kind == DT_CONFIG:
            # The host asks for 9 bytes first to learn wTotalLength, then again
            # for the whole blob. Truncation is normal, not an error.
            return ds5_usb.CONFIG_DESC[:length]
        if kind == DT_HID_REPORT:
            return ds5_usb.REPORT_DESC[:length]
        if kind == DT_STRING:
            return self._string(index, length)
        return -errno.EPIPE

    @staticmethod
    def _string(index, length):
        if index == 0:
            body = b"".join(struct.pack("<H", l) for l in ds5_usb.LANGIDS)
        else:
            text = ds5_usb.STRINGS.get(index)
            if text is None:
                return -errno.EPIPE
            body = text.encode("utf-16-le")
        return bytes([len(body) + 2, DT_STRING]) + body[: max(0, length - 2)]

    def _class(self, bm, request, value, index, length, data):
        # HID lives on interface 3; the audio class requests target 0..2.
        if request == HID_GET_REPORT:
            report_id = value & 0xFF
            blob = self.features.get(report_id)
            if blob is None:
                return -errno.EPIPE
            return (bytes([report_id]) + blob)[:length]
        if request in (HID_SET_REPORT, HID_SET_IDLE):
            if data and self.on_output:
                self.on_output(data)
            return b""
        if bm & DIR_DEVICE_TO_HOST:
            # Audio class GET_CUR and friends. Answering with zeros keeps
            # snd-usb-audio's mixer probe happy; nothing here has real controls.
            return bytes(length)
        return b""

    # -- endpoints ----------------------------------------------------------

    def _stall(self, what):
        """Record a stall rather than just returning one.

        A stalled endpoint is invisible from the outside: the host quietly
        retries, gives up, and the device looks merely inert. Counting them is
        how a wrong endpoint number gets noticed in seconds instead of a
        game launch.
        """
        self.stalls[what] = self.stalls.get(what, 0) + 1
        return -errno.EPIPE

    def transfer_in(self, ep, length):
        if ep == EP_HID_IN:
            # Park; the relay completes these on its own cadence.
            #
            # Answering inline from current state is closer to how real hardware
            # behaves and removes a clock, but it coincided with the game losing
            # the pad, so it is reverted pending a bisect rather than defended.
            return None
        if ep == EP_ISO_IN:
            return None
        return self._stall(f"in ep{ep}")

    def transfer_out(self, ep, data):
        if ep == EP_HID_OUT:
            if self.on_output:
                self.on_output(data)
            return b""
        return self._stall(f"out ep{ep}")

    def isochronous(self, ep, data, packets):
        if ep == EP_ISO_IN:
            # The microphone. We declare it because the config descriptor is the
            # real device's, served verbatim -- but we have nothing to put in
            # it. Stalling is the wrong answer: the host resubmits immediately
            # and it becomes a tight retry loop. Observed at 1.5 million stalls,
            # with the haptic stream stalling out alongside it.
            #
            # Answer with silence instead. An isochronous IN carries its data
            # compacted, one packet's actual_length after another.
            total = sum(p[1] for p in packets)
            silence = [(off, plen, plen, 0) for off, plen, _a, _s in packets]
            return bytes(total), silence
        if ep != EP_ISO_OUT:
            return self._stall(f"iso ep{ep}")

        self.haptic_urbs += 1
        self.haptic_bytes += len(data)
        if self.first_iso is None:
            # The wire layout of isochronous OUT was written from the protocol
            # spec and has never been checked against real traffic. Keep the
            # shape of the first URB so it can be: if the descriptor offsets and
            # lengths do not tile `data` exactly, the unpacking is wrong and
            # every channel reading zero would be a parsing artefact rather than
            # silence.
            self.first_iso = {
                "packets": len(packets),
                "data_len": len(data),
                "descriptors": packets[:4],
                "sum_length": sum(p[1] for p in packets),
                "tiles": all(
                    packets[i][0] + packets[i][1] == packets[i + 1][0]
                    for i in range(len(packets) - 1)
                ),
            }
        if self.on_haptics:
            self.on_haptics(data, packets)

        # Report every packet as fully consumed.
        done = [(offset, plen, plen, 0) for offset, plen, _actual, _st in packets]
        return b"", done
