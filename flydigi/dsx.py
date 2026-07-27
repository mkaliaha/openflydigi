# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""DualSenseX (DSX) UDP protocol listener.

Flydigi adopted the DSX protocol wholesale rather than inventing one, which is
why their per-game "mods" are really DSX mods. Implementing the listener means
the whole third-party DSX ecosystem drives the Apex 5, not just Flydigi's 46.

Wire format -- ASCII JSON datagrams on UDP 7878:

    {"instructions":[{"type":1,"parameters":[0, side, 19, mode, p1, p2, p3, p4]}]}

    InstructionType: Invalid=0, TriggerUpdate=1, RGBUpdate=2, PlayerLED=3,
                     TriggerThreshold=4, MicLED=5, PlayerLEDNewRevision=6
    Trigger:         Invalid=0, Left=1, Right=2

Parameter mapping, from ControllerBusinessService.OnTriggerCommandReceived:
    parameters[1]   -> side
    parameters[3..] -> mode followed by effect params
    parameters[0] and [2] are ignored (controller index, and a constant 19)

Games under Proton reach a host listener on 127.0.0.1 unchanged -- Wine shares
the host network stack.
"""
import json
import socket

DSX_PORT = 7878
FORWARD_PORT = 8787

TYPE_INVALID = 0
TYPE_TRIGGER_UPDATE = 1
TYPE_RGB_UPDATE = 2
TYPE_PLAYER_LED = 3
TYPE_TRIGGER_THRESHOLD = 4
TYPE_MIC_LED = 5
TYPE_PLAYER_LED_NEW = 6

_TYPE_NAMES = {
    "invalid": TYPE_INVALID,
    "triggerupdate": TYPE_TRIGGER_UPDATE,
    "rgbupdate": TYPE_RGB_UPDATE,
    "playerled": TYPE_PLAYER_LED,
    "triggerthreshold": TYPE_TRIGGER_THRESHOLD,
    "micled": TYPE_MIC_LED,
    "playerlednewrevision": TYPE_PLAYER_LED_NEW,
}

SIDE_LEFT = 1
SIDE_RIGHT = 2


def _as_type(value):
    """Accept both numeric and named instruction types.

    Flydigi deserialises with Newtonsoft, which takes either form, so mods in
    the wild use both.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        return _TYPE_NAMES.get(text.lower().replace("_", ""))
    return None


def _as_int(value):
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            return int(float(value.strip()))
        return int(value)
    except (TypeError, ValueError):
        return None


def parse(payload):
    """Decode a DSX datagram into a list of (side, mode, params) effects.

    Non-trigger instructions are ignored. Returns [] for anything unusable
    rather than raising -- this is fed by third-party mods.
    """
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("ascii", "replace")
        except Exception:
            return []
    payload = payload.strip().rstrip("\x00").strip()
    if not payload:
        return []
    try:
        message = json.loads(payload)
    except ValueError:
        return []
    if not isinstance(message, dict):
        return []

    effects_out = []
    for instruction in message.get("instructions") or []:
        if not isinstance(instruction, dict):
            continue
        if _as_type(instruction.get("type")) != TYPE_TRIGGER_UPDATE:
            continue
        params = instruction.get("parameters")
        if not isinstance(params, list) or len(params) < 4:
            continue
        side = _as_int(params[1])
        if side not in (SIDE_LEFT, SIDE_RIGHT):
            continue
        rest = [_as_int(p) for p in params[3:]]
        if any(v is None for v in rest):
            continue
        mode, values = rest[0], rest[1:]
        effects_out.append((side, mode, values))
    return effects_out


class Listener:
    """UDP server that turns DSX packets into trigger effects.

    Effects persist in controller state, so identical consecutive effects for a
    side are suppressed instead of rewritten at packet rate.
    """

    def __init__(self, controller, forward_port=None, verbose=False):
        self.ctrl = controller
        self.verbose = verbose
        self.applied = {}
        self.forward_port = forward_port
        self._forward_sock = None
        if forward_port:
            self._forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def handle(self, payload):
        """Apply every trigger effect in one datagram. Returns how many were sent."""
        sent = 0
        for side, mode, params in parse(payload):
            key = (mode, tuple(params))
            if self.applied.get(side) == key:
                continue
            from .effects import common_effect_payload

            self.ctrl.send(common_effect_payload(side, mode, params), wait=0.0)
            self.applied[side] = key
            sent += 1
            if self.verbose:
                name = "left" if side == SIDE_LEFT else "right"
                print(f"  [{name}] mode={mode} params={params}", flush=True)
        if self._forward_sock and payload:
            data = payload if isinstance(payload, (bytes, bytearray)) else payload.encode()
            try:
                self._forward_sock.sendto(data, ("127.0.0.1", self.forward_port))
            except OSError:
                pass
        return sent

    def close(self):
        if self._forward_sock:
            self._forward_sock.close()
            self._forward_sock = None


def listen(port=DSX_PORT, bind="127.0.0.1"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((bind, port))
    sock.settimeout(0.5)
    return sock
