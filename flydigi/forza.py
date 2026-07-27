"""Forza "Data Out" telemetry -> Apex 5 trigger effects.

Native reimplementation of Flydigi's ForzaDualSense.exe. Instead of the original
chain (game -> UDP 5300 -> mod -> DSX UDP 7878 -> Space Station -> HID) this goes
game -> UDP 5300 -> here -> HID, dropping two hops.

The rule config format is Flydigi's own (configs/forza.json), so their tuning is
reused verbatim.

Enable in game: HUD and Gameplay -> Data Out -> ON, IP 127.0.0.1, port 5300.
"""
import json
import socket
import struct
import time


FORZA_DATA_OUT_PORT = 5300

# Packet length -> offset applied to post-sled fields. Anything else is ignored.
BUFFER_OFFSETS = {311: 0, 324: 12}

_U8, _U16, _U32, _F32, _I8 = "u8", "u16", "u32", "f32", "i8"

# DataPacket field name -> (kind, offset, shifted_by_buffer_offset).
# Names match Flydigi's DataPacket fields because config keys reference those.
FIELDS = {
    "IsRaceOn": (_F32, 0, False),
    "TimestampMS": (_U32, 4, False),
    "EngineMaxRpm": (_F32, 8, False),
    "EngineIdleRpm": (_F32, 12, False),
    "CurrentEngineRpm": (_F32, 16, False),
    "AccelerationX": (_F32, 20, False),
    "AccelerationY": (_F32, 24, False),
    "AccelerationZ": (_F32, 28, False),
    "VelocityX": (_F32, 32, False),
    "VelocityY": (_F32, 36, False),
    "VelocityZ": (_F32, 40, False),
    "AngularVelocityX": (_F32, 44, False),
    "AngularVelocityY": (_F32, 48, False),
    "AngularVelocityZ": (_F32, 52, False),
    "Yaw": (_F32, 56, False),
    "Pitch": (_F32, 60, False),
    "Roll": (_F32, 64, False),
    "NormalizedSuspensionTravelFrontLeft": (_F32, 68, False),
    "NormalizedSuspensionTravelFrontRight": (_F32, 72, False),
    "NormalizedSuspensionTravelRearLeft": (_F32, 76, False),
    "NormalizedSuspensionTravelRearRight": (_F32, 80, False),
    "TireSlipRatioFrontLeft": (_F32, 84, False),
    "TireSlipRatioFrontRight": (_F32, 88, False),
    "TireSlipRatioRearLeft": (_F32, 92, False),
    "TireSlipRatioRearRight": (_F32, 96, False),
    "WheelRotationSpeedFrontLeft": (_F32, 100, False),
    "WheelRotationSpeedFrontRight": (_F32, 104, False),
    "WheelRotationSpeedRearLeft": (_F32, 108, False),
    "WheelRotationSpeedRearRight": (_F32, 112, False),
    "WheelOnRumbleStripFrontLeft": (_F32, 116, False),
    "WheelOnRumbleStripFrontRight": (_F32, 120, False),
    "WheelOnRumbleStripRearLeft": (_F32, 124, False),
    "WheelOnRumbleStripRearRight": (_F32, 128, False),
    "WheelInPuddleDepthFrontLeft": (_F32, 132, False),
    "WheelInPuddleDepthFrontRight": (_F32, 136, False),
    "WheelInPuddleDepthRearLeft": (_F32, 140, False),
    "WheelInPuddleDepthRearRight": (_F32, 144, False),
    "SurfaceRumbleFrontLeft": (_F32, 148, False),
    "SurfaceRumbleFrontRight": (_F32, 152, False),
    "SurfaceRumbleRearLeft": (_F32, 156, False),
    "SurfaceRumbleRearRight": (_F32, 160, False),
    "TireSlipAngleFrontLeft": (_F32, 164, False),
    "TireSlipAngleFrontRight": (_F32, 168, False),
    "TireSlipAngleRearLeft": (_F32, 172, False),
    "TireSlipAngleRearRight": (_F32, 176, False),
    "TireCombinedSlipFrontLeft": (_F32, 180, False),
    "TireCombinedSlipFrontRight": (_F32, 184, False),
    "TireCombinedSlipRearLeft": (_F32, 188, False),
    "TireCombinedSlipRearRight": (_F32, 192, False),
    "SuspensionTravelMetersFrontLeft": (_F32, 196, False),
    "SuspensionTravelMetersFrontRight": (_F32, 200, False),
    "SuspensionTravelMetersRearLeft": (_F32, 204, False),
    "SuspensionTravelMetersRearRight": (_F32, 208, False),
    # Flydigi reads these five as single bytes. Kept identical so behaviour
    # matches theirs; none are referenced by any shipped rule config.
    "CarOrdinal": (_U8, 212, False),
    "CarClass": (_U8, 216, False),
    "CarPerformanceIndex": (_U8, 220, False),
    "DrivetrainType": (_U8, 224, False),
    "NumCylinders": (_U8, 228, False),
    "PositionX": (_F32, 232, True),
    "PositionY": (_F32, 236, True),
    "PositionZ": (_F32, 240, True),
    "Speed": (_F32, 244, True),
    "Power": (_F32, 248, True),
    "Torque": (_F32, 252, True),
    "TireTempFl": (_F32, 256, True),
    "TireTempFr": (_F32, 260, True),
    "TireTempRl": (_F32, 264, True),
    "TireTempRr": (_F32, 268, True),
    "Boost": (_F32, 272, True),
    "Fuel": (_F32, 276, True),
    "Distance": (_F32, 280, True),
    "BestLapTime": (_F32, 284, True),
    "LastLapTime": (_F32, 288, True),
    "CurrentLapTime": (_F32, 292, True),
    "CurrentRaceTime": (_F32, 296, True),
    "Lap": (_U16, 300, True),
    "RacePosition": (_U8, 302, True),
    "Accelerator": (_U8, 303, True),
    "Brake": (_U8, 304, True),
    "Clutch": (_U8, 305, True),
    "Handbrake": (_U8, 306, True),
    "Gear": (_U8, 307, True),
    "Steer": (_I8, 308, True),
    "NormalDrivingLine": (_U8, 309, True),
    "NormalAiBrakeDifference": (_U8, 310, True),
}

_READERS = {
    _U8: (1, "<B"),
    _I8: (1, "<b"),
    _U16: (2, "<H"),
    _U32: (4, "<I"),
    _F32: (4, "<f"),
}

SIDE_BY_NAME = {"left": 1, "right": 2}


def parse(buf):
    """Decode a Forza Data Out datagram. Returns None for unknown formats."""
    shift = BUFFER_OFFSETS.get(len(buf))
    if shift is None:
        return None
    out = {}
    for name, (kind, offset, shifted) in FIELDS.items():
        size, fmt = _READERS[kind]
        pos = offset + (shift if shifted else 0)
        if pos + size > len(buf):
            continue
        (value,) = struct.unpack_from(fmt, buf, pos)
        out[name] = value
    out["IsRaceOn"] = out.get("IsRaceOn", 0.0) > 0.0
    return out


def _compare(value, op, expected):
    """Mirror ConfigHelper.CheckValue, including its float equality tolerance."""
    if value is None:
        return False
    if isinstance(value, bool):
        want = str(expected).strip().lower()
        if want in ("true", "false"):
            want = want == "true"
            if op == "=":
                return value == want
            if op == "!=":
                return value != want
        return False
    try:
        number = float(expected)
    except (TypeError, ValueError):
        return False
    if isinstance(value, float):
        # Flydigi uses a tolerance of 1.0 for float equality. Replicated so the
        # shipped rule configs behave the same as they do on Windows.
        if op == "=":
            return abs(value - number) < 1.0
        if op == "!=":
            return abs(value - number) > 1.0
    if op == "=":
        return value == number
    if op == "!=":
        return value != number
    if op == "<":
        return value < number
    if op == "<=":
        return value <= number
    if op == ">":
        return value > number
    if op == ">=":
        return value >= number
    return False


def _match(action, packet, last):
    """Evaluate an action's filters.

    Folds left to right with no operator precedence, exactly as ConfigHelper does.
    """
    result = False
    condition = (action.get("condition") or "and").lower()
    for i, filt in enumerate(action.get("filters") or []):
        key = filt.get("key")
        value = packet.get(key)
        if filt.get("changed"):
            # Flydigi seeds lastPacket with a zero-filled DataPacket rather than
            # null, so on the first datagram "changed" compares against 0 and is
            # false for idle fields. Treating it as "everything changed" would
            # fire every changed-filter at once and, with first-match-wins, latch
            # the wrong effect.
            previous = 0 if last is None else last.get(key, 0)
            ok = value is not None and previous != value
        else:
            ok = _compare(value, filt.get("op"), filt.get("value"))
        if i == 0:
            result = ok
        elif condition == "and":
            result = result and ok
        elif condition == "or":
            result = result or ok
        else:
            result = False
    return result


from .effects import common_effect_payload as effect_payload  # noqa: E402


class Engine:
    """Evaluates the rule config and emits trigger effects.

    Effects persist in controller state, so an effect is only sent when it
    differs from what that side is already set to.
    """

    def __init__(self, config, controller, verbose=False):
        self.actions = config.get("actions") or []
        self.ctrl = controller
        self.verbose = verbose
        self.last_packet = None
        self.applied = {}       # side -> (mode, params tuple)
        self.pending = []       # [(due_timestamp, side, mode, params, name)]

    def _emit(self, side, mode, params, label):
        key = (mode, tuple(params))
        if self.applied.get(side) == key:
            return False
        self.ctrl.send(effect_payload(side, mode, params), wait=0.0)
        self.applied[side] = key
        if self.verbose:
            name = "left" if side == 1 else "right"
            print(f"  [{name}] mode={mode} params={list(params)}  ({label})", flush=True)
        return True

    def feed(self, packet):
        """Process one telemetry packet."""
        self.flush_pending()
        for action in self.actions:
            if not _match(action, packet, self.last_packet):
                continue
            trigger = action.get("trigger") or {}
            side = SIDE_BY_NAME.get((trigger.get("side") or "").lower())
            if side:
                self._emit(side, trigger.get("mode", 0),
                           trigger.get("params") or [], action.get("name", "?"))
            duration = action.get("duration") or 0
            after = action.get("afterTrigger")
            if duration and after and not self.pending:
                after_side = SIDE_BY_NAME.get((after.get("side") or "").lower())
                if after_side:
                    self.pending.append((
                        time.time() + float(duration), after_side,
                        after.get("mode", 0), after.get("params") or [],
                        (action.get("name", "?") + " (after)"),
                    ))
            break  # first match wins, as in TryParseData
        self.last_packet = packet

    def flush_pending(self):
        now = time.time()
        due = [p for p in self.pending if p[0] <= now]
        self.pending = [p for p in self.pending if p[0] > now]
        for _, side, mode, params, label in due:
            self._emit(side, mode, params, label)


def load_config(path):
    with open(path) as fh:
        return json.load(fh)


def listen(port=FORZA_DATA_OUT_PORT, bind="127.0.0.1"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, port))
    sock.settimeout(0.5)
    return sock
