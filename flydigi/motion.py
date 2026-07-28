# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Motion sensor data from the Apex 5's vendor input stream.

The kernel's xpad driver decodes sticks and buttons but not the IMU, so gyro
and accelerometer have to come from the vendor interface. Flydigi's SDK enables
this with command 17 ("raw data transport in") and then reads the sensors out
of the 0xEF input reports.

Verified on hardware: enabling raw data does NOT disturb the xpad node, so the
relay can keep taking sticks and buttons from evdev and use this only for
motion. Reports arrive at roughly 300 Hz.

Offsets follow OperatorDataParser for ControllerType.NewXInput, shifted by one
because we keep the report-ID byte that Flydigi's HID layer strips.
"""
import os
import struct

from .device import build, checksum

CMD_ENABLE_RAW = 17
INPUT_REPORT_ID = 0x04
INPUT_REPORT_MARKER = 0xEF     # SDL calls this FLYDIGI_V2_INPUT_REPORT
UNCHANGED = 0xFF

GYRO_OFFSET = 18               # gyro X/Y/Z at 18, 20, 22
ACCEL_OFFSET = 24              # accel X/Y/Z at 24, 26, 28
MIN_REPORT_LEN = 30


def set_raw_data(ctrl, controller_data=UNCHANGED, raw=UNCHANGED,
                 keyboard=UNCHANGED, mouse=UNCHANGED, third_party=UNCHANGED):
    """Command 17. Each flag is 1 (on), 0 (off) or 0xFF (leave alone)."""
    buf = build(CMD_ENABLE_RAW)
    buf[4] = 7
    buf[5] = controller_data
    buf[6] = raw
    buf[7] = keyboard
    buf[8] = mouse
    buf[9] = third_party
    buf[10] = checksum(buf, 3, 3 + buf[4])
    replies = ctrl.send(buf, wait=0.4)
    return any(len(r) > 3 and r[3] == CMD_ENABLE_RAW for r in replies)


def enable(ctrl):
    """Start the motion stream."""
    return set_raw_data(ctrl, controller_data=1, raw=1)


def disable(ctrl):
    """Stop it again, leaving the other transport flags alone."""
    return set_raw_data(ctrl, raw=0)


def parse(data):
    """Extract (gyro, accel) from a vendor input report, or None.

    Command replies share report id 0x04 with input reports and are told apart
    only by the marker byte, so that must be checked -- otherwise a battery
    reply gets decoded as motion.
    """
    if len(data) < MIN_REPORT_LEN or data[0] != INPUT_REPORT_ID:
        return None
    if data[3] != INPUT_REPORT_MARKER:
        return None
    gyro = list(struct.unpack_from("<3h", data, GYRO_OFFSET))
    accel = list(struct.unpack_from("<3h", data, ACCEL_OFFSET))
    return gyro, accel


# --- device info / battery -------------------------------------------------
#
# Command 1 returns device type, connection type, MAC, battery and firmware.
# Parsed as HeartBeatControllerCommandNewXInput does, with indices shifted by
# one because we keep the report-id byte.

CMD_GET_INFO = 0x01
CHARGING_LEVEL = 6          # Flydigi's sentinel for "charging"
MAX_LEVEL = 5               # levels run 0..5

# Accelerometer scaling.
#
# The DualSense calibration blob we advertise (from inputtino) declares
# acc_plus = 10000 / acc_minus = -10000. hid-playstation turns that into
# sens = 2 * 8192 / 20000 = 0.8192 and reports in units of 8192 per g, so a
# game sees 1g when the raw value is 10000.
#
# The Apex 5 reports about 4096 per g (measured: az rests near 4096 with the
# pad flat). Without this correction a game reads roughly 0.4g and any
# orientation maths built on gravity is wrong.
APEX5_ACCEL_PER_G = 4096
DS5_ACCEL_RAW_PER_G = 10000
ACCEL_SCALE = DS5_ACCEL_RAW_PER_G / APEX5_ACCEL_PER_G   # ~2.441

# Gyro is left at 1.0 by default. The same calibration implies deg/s =
# raw * 0.05, which put brisk hand rotation in a plausible range during
# testing, but there is no reference to check Flydigi's LSB-per-deg/s against,
# so this is the one value worth tuning by feel in a game.
GYRO_SCALE = 1.0


def parse_info(data):
    """Decode a command-1 reply into a dict, or None if it is not one."""
    if len(data) < 14 or data[0] != INPUT_REPORT_ID or data[3] != CMD_GET_INFO:
        return None
    # data[4] < data[5] selects a payload offset in the original; for the
    # single-frame replies we see, the fields start at raw index 6.
    device_type = data[6]
    connect_type = data[7]
    raw_battery = data[12]
    charging = (raw_battery >> 4) == 1
    level = CHARGING_LEVEL if charging else (raw_battery & 0x0F)
    return {
        "device_type": device_type,
        "connect_type": "wired" if connect_type == 1 else "dongle",
        "battery_level": min(level, CHARGING_LEVEL),
        "charging": charging,
    }


# Seven separately-flashed components, two BCD bytes each, packed after the chip
# types in the same command-1 reply. Each nibble is one version field, so
# `0x70 0x45` is 7.0.4.5. All-zero means "not present" rather than version zero
# -- Space Station nulls them -- which is how a wired pad reports no dongle, and
# how an Apex 5 reports no ADC chip (that one is a Vader 4 part).
VERSION_NAMES = ("main", "dongle", "switch", "trigger", "screen", "adc",
                 "nearlink")
VERSION_OFFSET = 16            # raw index; body[15] once the report id is gone


def parse_versions(data):
    """The firmware versions in a command-1 reply, or None if it is not one."""
    if len(data) < VERSION_OFFSET + 2 * len(VERSION_NAMES):
        return None
    if data[0] != INPUT_REPORT_ID or data[3] != CMD_GET_INFO:
        return None
    versions = {}
    for index, name in enumerate(VERSION_NAMES):
        hi, lo = data[VERSION_OFFSET + 2 * index : VERSION_OFFSET + 2 * index + 2]
        parts = (hi >> 4, hi & 0xF, lo >> 4, lo & 0xF)
        versions[name] = None if not any(parts) else ".".join(map(str, parts))
    return versions


def read_versions(ctrl, wait=0.6):
    """Ask the pad which firmware it is running. None if it is asleep."""
    buf = build(CMD_GET_INFO)
    buf[4] = 2
    buf[5] = checksum(buf, 3, 3 + buf[4])
    for reply in ctrl.send(buf, wait=wait):
        versions = parse_versions(reply)
        if versions:
            return versions
    return None


def version_at_least(version, minimum):
    """Compare dotted versions numerically. False for a missing version.

    Deliberately **not** what Flydigi does. `DeviceUtil.CompareVersion` is
    `string.Compare(new, old, Ordinal) >= 0` -- an ordinal string comparison --
    so their own gate rejects firmware 7.0.10.0 against a minimum of 7.0.3.0,
    because "1" sorts below "3". Comparing numerically differs from them only
    where they are wrong.
    """
    if not version:
        return False

    def parts(text):
        out = []
        for piece in str(text).split("."):
            try:
                out.append(int(piece))
            except ValueError:
                return None
        return out

    have, want = parts(version), parts(minimum)
    if have is None or want is None:
        return False
    # Pad the shorter one so 7.0 and 7.0.0.0 compare equal.
    length = max(len(have), len(want))
    have += [0] * (length - len(have))
    want += [0] * (length - len(want))
    return have >= want


CMD_READ_TRANSPORT = 16

# Firmware below this does not offer third-party control on an Apex 5.
# ControllerBusinessService gates it per device code: "k5" wants 7.0.3.0 and
# "f5" wants 7.1.4.1.
THIRD_PARTY_MIN_FIRMWARE = {"k5": "7.0.3.0", "f5": "7.1.4.1"}


def parse_transport(data):
    """Decode a command-16 reply: what the pad is transporting, and to whom."""
    if len(data) < 30 or data[0] != INPUT_REPORT_ID or data[3] != CMD_READ_TRANSPORT:
        return None
    # `control_by` is the same 20-byte ASCII tag the cooperative-lock command
    # carries, so this answers "is something else driving the pad" *and* "what"
    # in one read -- worth more to a UI than the bare flag.
    holder = bytes(data[11:31]).split(b"\x00", 1)[0]
    return {
        "controller_data": data[6] == 1,
        "raw_data": data[7] == 1,
        "keyboard": data[8] == 1,
        "mouse": data[9] == 1,
        "third_party": data[10] == 1,
        "control_by": holder.decode("ascii", "replace"),
    }


def read_transport(ctrl, wait=0.6):
    """Read the transport flags and the third-party takeover state.

    The counterpart to `set_raw_data`, which has always been write-only -- so
    nothing could show the user what the pad currently believes.
    """
    buf = build(CMD_READ_TRANSPORT)
    buf[4] = 2
    buf[5] = checksum(buf, 3, 3 + buf[4])
    for reply in ctrl.send(buf, wait=wait):
        state = parse_transport(reply)
        if state:
            return state
    return None


def request_info(ctrl):
    """Send command 1. The reply arrives on the same stream as motion data."""
    buf = build(CMD_GET_INFO)
    buf[4] = 2
    buf[5] = checksum(buf, 3, 3 + buf[4])
    ctrl.send(buf, wait=0.0)


def read_report(ctrl):
    """Read one pending vendor report.

    Returns ("motion", gyro, accel), ("info", dict) or None.
    """
    try:
        data = os.read(ctrl.fd, 64)
    except (BlockingIOError, OSError):
        return None
    motion_data = parse(data)
    if motion_data:
        return ("motion",) + motion_data
    info = parse_info(data)
    if info:
        return ("info", info)
    return None


def read_info(ctrl, wait=0.6):
    """Ask for device info and wait for the reply. None if the pad is asleep.

    `read_report` is non-blocking and returns None the moment nothing is
    pending, so polling it in a loop spins instead of waiting. This sends the
    request and lets the transport block on select for the reply, which is what
    a caller asking a one-off question actually wants.
    """
    buf = build(CMD_GET_INFO)
    buf[4] = 2
    buf[5] = checksum(buf, 3, 3 + buf[4])
    for reply in ctrl.send(buf, wait=wait):
        info = parse_info(reply)
        if info:
            return info
    return None


def read(ctrl):
    """Backwards-compatible motion-only read."""
    result = read_report(ctrl)
    if result and result[0] == "motion":
        return result[1], result[2]
    return None
