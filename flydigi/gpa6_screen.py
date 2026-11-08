# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Apex 6 picture resources over the shared screen serial bootloader.

Only custom images/animations are uploaded. LCD flags and program firmware are
outside this interface. The current image cannot be read back or rolled back.
"""
import os
import time

from . import evdev, gpa6, identity, screen_ota

MAX_FRAMES = 150  # Current vendor screen editor, below the one-byte wire limit.


def validate_frames(frames, interval_ms=100):
    frames = screen_ota.validate_frames(frames, interval_ms, max_frames=MAX_FRAMES)
    if not 1 <= interval_ms <= 2500:
        raise screen_ota.OtaError("Apex 6 frame interval must be 1..2500 ms")
    return frames


def _wait_for_port(root):
    deadline = time.monotonic() + screen_ota.PORT_TIMEOUT
    while True:
        ports = screen_ota.find_ports(usb_root=root)
        if len(ports) > 1:
            raise screen_ota.OtaError("multiple screen bootloader ports appeared for the selected controller")
        if ports:
            return ports[0]
        if time.monotonic() >= deadline:
            raise screen_ota.OtaError(
                "no screen bootloader appeared on the selected USB port; "
                "power-cycle the controller and reconnect by cable")
        time.sleep(0.25)


def upload_picture(ctrl, frames, interval_ms=100, restore_default=False,
                   progress=None, *, expected_uid=None, port=None):
    """Validate, switch one wired Apex 6, then upload picture resources.

The caller owns/closes ctrl and must discard it after the controller restarts.
For recovery, pass ctrl=None and an explicit bootloader port. A bootloader has
no readable controller UUID: recovery authenticates the USB kind/path only.
No automatic fallback to a different controller or arbitrary tty is allowed.
"""
    frames = validate_frames(frames, interval_ms)
    if port is not None:
        if ctrl is not None or expected_uid is not None:
            raise ValueError("recovery --port cannot also select a controller UUID")
        port = os.path.realpath(port)
        if port not in screen_ota.find_ports():
            raise screen_ota.OtaError("recovery port is not an FFAA:5555 screen bootloader")
        root = evdev.usb_device(f"/sys/class/tty/{os.path.basename(port)}/device")
        if root is None:
            raise screen_ota.OtaError("cannot identify the recovery port's USB device")
        with screen_ota.OtaLink(port, usb_root=root) as link:
            return screen_ota.upload(link, frames, interval_ms, restore_default, progress, strict=True)

    protocol = gpa6.Protocol(ctrl)
    with ctrl.claim(timeout=0):
        identity.require(ctrl, "k6")
        uid = protocol.uid()
        if expected_uid is not None and uid != expected_uid:
            raise identity.WrongDevice("selected Apex 6 UUID changed before screen upload")
        root = evdev.usb_device_of_hidraw(ctrl.path)
        if root is None:
            raise screen_ota.OtaError("cannot bind the screen upload to the selected controller's USB port")
        if screen_ota.find_ports(usb_root=root):
            raise screen_ota.OtaError("screen is already in upgrade mode; recover with an explicit --port")
        if protocol.uid() != uid:
            raise identity.WrongDevice("Apex 6 UUID changed before entering screen upgrade mode")
        info = protocol.info()
        if info["device_type"] not in gpa6.SKUS or not info["screen"]:
            raise identity.WrongDevice("selected Apex 6 does not advertise screen support")
        if info["connection_mode"] != 0:
            raise screen_ota.OtaError("Apex 6 screen uploads require a wired USB connection")
        # The native engine sends this once without awaiting an ACK. The new
        # serial interface, on this physical port, is the decisive observation.
        protocol.write(screen_ota.CMD_SWITCH_USB, bytes([screen_ota.CHIP_SCREEN]))
        time.sleep(screen_ota.SWITCH_SETTLE)
        port = _wait_for_port(root)
        with screen_ota.OtaLink(port, usb_root=root) as link:
            return screen_ota.upload(link, frames, interval_ms, restore_default, progress, strict=True)
