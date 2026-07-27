"""DualSense input/output report codec.

Layouts follow inputtino's ps5.hpp (MIT) -- see NOTICE.

Input report (USB, report id 0x01, 64 bytes total):
    [0]  report id 0x01
    [1]  x     left stick X       [5]  z   L2 analog
    [2]  y     left stick Y       [6]  rz  R2 analog
    [3]  rx    right stick X      [7]  seq
    [4]  ry    right stick Y
    [8]  buttons0: bits0-2 hat, 0x10 square, 0x20 cross, 0x40 circle, 0x80 triangle
    [9]  buttons1: 0x01 L1, 0x02 R1, 0x04 L2, 0x08 R2, 0x10 create,
                   0x20 options, 0x40 L3, 0x80 R3
    [10] buttons2: 0x01 PS, 0x02 touchpad, 0x04 mic mute
    [53] battery: low nibble charge (0-10), high nibble status

Output report (USB 0x02 / BT 0x31) carries rumble and the adaptive trigger
effects; see parse_output().
"""
import struct

AXIS_NEUTRAL = 0x80
REPORT_ID_INPUT = 0x01
INPUT_REPORT_LEN = 64

# buttons0
SQUARE, CROSS, CIRCLE, TRIANGLE = 0x10, 0x20, 0x40, 0x80
# buttons1
L1, R1, L2, R2 = 0x01, 0x02, 0x04, 0x08
CREATE, OPTIONS, L3, R3 = 0x10, 0x20, 0x40, 0x80
# buttons2
PS_HOME, TOUCHPAD, MIC_MUTE = 0x01, 0x02, 0x04

HAT_N, HAT_NE, HAT_E, HAT_SE = 0, 1, 2, 3
HAT_S, HAT_SW, HAT_W, HAT_NW = 4, 5, 6, 7
HAT_NEUTRAL = 8

BATTERY_FULL = 0x01

# Output report
DS_OUTPUT_REPORT_USB = 0x02
DS_OUTPUT_REPORT_BT = 0x31
FLAG0_MOTOR = 0x01
FLAG0_RIGHT_TRIGGER = 0x04
FLAG0_LEFT_TRIGGER = 0x08
FLAG2_COMPATIBLE_VIBRATION = 0x04


def hat_from_dpad(x, y):
    """Encode dpad axes (-1/0/+1) into the DualSense hat value."""
    if x == 0 and y < 0:
        return HAT_N
    if x > 0 and y < 0:
        return HAT_NE
    if x > 0 and y == 0:
        return HAT_E
    if x > 0 and y > 0:
        return HAT_SE
    if x == 0 and y > 0:
        return HAT_S
    if x < 0 and y > 0:
        return HAT_SW
    if x < 0 and y == 0:
        return HAT_W
    if x < 0 and y < 0:
        return HAT_NW
    return HAT_NEUTRAL


class InputState:
    """Mutable DualSense input state, packed into a report on demand."""

    def __init__(self):
        self.lx = self.ly = AXIS_NEUTRAL
        self.rx = self.ry = AXIS_NEUTRAL
        self.l2 = self.r2 = 0
        self.hat = HAT_NEUTRAL
        self.buttons0 = 0
        self.buttons1 = 0
        self.buttons2 = 0
        self.seq = 0
        self.battery_charge = 10        # 0-10, i.e. 100%
        self.battery_status = BATTERY_FULL

    def set(self, mask, group, pressed):
        attr = f"buttons{group}"
        value = getattr(self, attr)
        setattr(self, attr, value | mask if pressed else value & ~mask)

    def pack(self):
        report = bytearray(INPUT_REPORT_LEN)
        report[0] = REPORT_ID_INPUT
        report[1] = self.lx & 0xFF
        report[2] = self.ly & 0xFF
        report[3] = self.rx & 0xFF
        report[4] = self.ry & 0xFF
        report[5] = self.l2 & 0xFF
        report[6] = self.r2 & 0xFF
        report[7] = self.seq & 0xFF
        report[8] = (self.hat & 0x0F) | (self.buttons0 & 0xF0)
        report[9] = self.buttons1 & 0xFF
        report[10] = self.buttons2 & 0xFF
        # Touch points inactive: the 'contact' bit (0x80) set means no contact.
        report[33] = 0x80
        report[37] = 0x80
        report[53] = (self.battery_charge & 0x0F) | ((self.battery_status & 0x0F) << 4)
        report[54] = 0x0C
        self.seq = (self.seq + 1) & 0xFF
        return bytes(report)


class TriggerEffect:
    __slots__ = ("side", "type", "params")

    def __init__(self, side, type_, params):
        self.side = side          # "left" | "right"
        self.type = type_
        self.params = params

    def key(self):
        return (self.side, self.type, bytes(self.params))

    def __repr__(self):
        return (f"TriggerEffect({self.side}, type=0x{self.type:02X}, "
                f"params={bytes(self.params).hex(' ')})")


def parse_output(data):
    """Decode a DualSense output report.

    Returns {"rumble": (left, right) | None, "effects": [TriggerEffect, ...]}.
    Unknown or truncated reports yield empty results rather than raising --
    this is driven by whatever the game and kernel driver send.
    """
    result = {"rumble": None, "effects": []}
    if not data:
        return result
    if data[0] == DS_OUTPUT_REPORT_USB:
        base = 1
    elif data[0] == DS_OUTPUT_REPORT_BT:
        base = 2
    else:
        return result
    # common: flag0, flag1, motor_right, motor_left, reserved[4], mute_led,
    #         power_save, right_type, right[10], left_type, left[10], ...
    if len(data) < base + 32:
        return result

    flag0 = data[base]
    motor_right = data[base + 2]
    motor_left = data[base + 3]
    flag2 = data[base + 38] if len(data) > base + 38 else 0

    if flag0 & FLAG0_MOTOR or flag2 & FLAG2_COMPATIBLE_VIBRATION:
        result["rumble"] = (motor_left, motor_right)

    if flag0 & FLAG0_RIGHT_TRIGGER:
        result["effects"].append(TriggerEffect(
            "right", data[base + 10], data[base + 11:base + 21]))
    if flag0 & FLAG0_LEFT_TRIGGER:
        result["effects"].append(TriggerEffect(
            "left", data[base + 21], data[base + 22:base + 32]))
    return result
