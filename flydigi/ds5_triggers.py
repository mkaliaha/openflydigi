# SPDX-FileCopyrightText: 2026 tunk3r
#
# SPDX-License-Identifier: MIT

"""DualSense adaptive-trigger effects rendered as voice-coil waveforms.

The Apex 6 Pro triggers have voice coils, not motorised resistance, so a
DualSense trigger block can only be approximated as vibration. The mapping is
an application choice, written from the public DualSense output-report layout:

  resistance (0x01, 0x11, 0x21)  85 Hz carrier, level = force at the position
  weapon (0x02, 0x12, 0x25)      carrier inside the zone, 95 Hz burst on break
  bow (0x22)                     as weapon, burst scaled by the snap force
  vibration (0x06, 0x26)         the effect's own frequency and amplitude
  galloping (0x23)               two 95 Hz pulses per cycle at the foot phases
  machine (0x24)                 frequency alternating between two amplitudes

Positions are 0..255 trigger travel. The ten-zone effects split that travel
into zones 0..9. Unknown effect types leave the trigger unchanged, as the
Apex 5 translation does.
"""
import math

RATE = 1000
CARRIER_HZ = 85.0
BURST_HZ = 95.0
BURST_S = 0.030
PULSE_S = 0.030
HOLD_S = 0.100
PRESSED = 8          # travel below this counts as released for resistance
RESIST_MAX = 0.5
BURST_MAX = 0.9
VIBRATION_MAX = 0.7
LIMIT = 0.9          # bound on any rendered or summed sample
MAX_HZ = 250.0       # keep well below the 500 Hz Nyquist limit of the stream

OFF = "off"


class Effect:
    """A decoded effect. Positions are 0..255; levels are 0..1."""
    __slots__ = ("kind", "start", "end", "levels", "level", "snap",
                 "hz", "first", "second", "level_b", "period")

    def __init__(self, kind, *, start=0, end=255, levels=None, level=0.0, snap=0.0,
                 hz=0.0, first=0, second=0, level_b=0.0, period=0.0):
        self.kind, self.start, self.end = kind, start, end
        self.levels, self.level, self.snap = levels, level, snap
        self.hz, self.first, self.second = hz, first, second
        self.level_b, self.period = level_b, period

    def __repr__(self):
        fields = ", ".join(f"{k}={getattr(self, k)!r}" for k in self.__slots__
                           if getattr(self, k) not in (None, 0, 0.0))
        return f"Effect({fields})"


def zone(position):
    return min(9, max(0, int(position)) * 10 // 256)


def zone_start(index):
    """First trigger position inside zone `index`."""
    return -(-index * 256 // 10)


def _zones(p):
    return p[0] | p[1] << 8


def _packed(p):
    """Ten 3-bit values, stored as value - 1 for each active zone."""
    return p[2] | p[3] << 8 | p[4] << 16 | p[5] << 24


def _span(mask):
    """(start, end) zones of a two-bit start/stop mask, or None."""
    if not mask & 0x3ff:
        return None
    bits = [i for i in range(10) if mask >> i & 1]
    return bits[0], bits[-1]


def _zone_levels(mask, packed):
    return [((packed >> 3 * i & 7) + 1) / 8 if mask >> i & 1 else 0.0 for i in range(10)]


def decode(type_, params):
    """Decode one 11-byte block into an Effect, OFF, or None (unknown type)."""
    p = list(params[:10]) + [0] * (10 - len(params[:10]))
    if type_ in (0x00, 0x05):
        return OFF
    if type_ == 0x01:
        return Effect("resistance", start=p[0], level=p[1] / 255)
    if type_ == 0x11:
        return Effect("resistance", start=p[0], level=min(p[1], 10) / 10)
    if type_ in (0x02, 0x12):
        level = p[2] / 255 if type_ == 0x02 else min(p[2], 10) / 10
        if p[1] <= p[0]:
            return OFF
        return Effect("weapon", start=p[0], end=p[1], level=level, snap=level)
    if type_ == 0x06:
        return Effect("vibration", start=p[2], level=p[1] / 255, hz=p[0])
    if type_ == 0x21:
        mask = _zones(p)
        return Effect("resistance", levels=_zone_levels(mask, _packed(p))) if mask & 0x3ff else OFF
    if type_ == 0x26:
        mask = _zones(p)
        if not mask & 0x3ff or not p[8]:
            return OFF
        return Effect("vibration", levels=_zone_levels(mask, _packed(p)), hz=p[8])
    if type_ in (0x22, 0x23, 0x24, 0x25):
        span = _span(_zones(p))
        if span is None or span[0] == span[1]:
            return OFF
        start, end = zone_start(span[0]), zone_start(span[1])
        if type_ == 0x25:
            level = ((p[2] & 7) + 1) / 8
            return Effect("weapon", start=start, end=end, level=level, snap=level)
        if type_ == 0x22:
            forces = p[2] | p[3] << 8
            return Effect("weapon", start=start, end=end, level=((forces & 7) + 1) / 8,
                          snap=((forces >> 3 & 7) + 1) / 8)
        if type_ == 0x23:
            if not p[3]:
                return OFF
            return Effect("galloping", start=start, end=end, hz=p[3],
                          first=p[2] >> 3 & 7, second=p[2] & 7, level=1.0)
        if not p[3]:
            return OFF
        return Effect("machine", start=start, end=end, hz=p[3],
                      level=(p[2] & 7) / 7, level_b=(p[2] >> 3 & 7) / 7, period=p[4] / 10)
    return None


def _sine(hz, t):
    return math.sin(2 * math.pi * hz * t)


class Trigger:
    """One trigger's latest effect, its off-hold, and its break-point burst."""

    def __init__(self):
        self.effect = None
        self.off_at = None
        self.position = 0
        self.armed = True
        self.burst_until = -1.0
        self.burst_level = 0.0

    def set(self, decoded, now):
        if decoded is None:
            return
        if decoded is OFF:
            if self.effect is not None and self.off_at is None:
                self.off_at = now + HOLD_S
            return
        if self.effect is None or self.effect.kind != decoded.kind:
            self.armed = True
        self.effect, self.off_at = decoded, None

    def clear(self):
        self.__init__()

    def active(self, now):
        if self.off_at is not None and now >= self.off_at:
            self.effect = self.off_at = None
            self.burst_until = -1.0
        return self.effect is not None

    def move(self, position, now):
        """Record a new position; fire the break-point burst on crossing."""
        position = max(0, min(255, int(position)))
        effect = self.effect
        if effect is not None and effect.kind == "weapon":
            if position < effect.start:
                self.armed = True
            elif self.armed and self.position < effect.end <= position:
                self.armed = False
                self.burst_until = now + BURST_S
                self.burst_level = effect.snap
        self.position = position

    def sample(self, t):
        effect, pos = self.effect, self.position
        value = 0.0
        if t < self.burst_until:
            value += BURST_MAX * self.burst_level * _sine(BURST_HZ, t)
        if effect is None:
            return value
        kind = effect.kind
        if kind == "resistance":
            level = effect.levels[zone(pos)] if effect.levels else (
                effect.level if pos >= effect.start else 0.0)
            if pos >= PRESSED:
                value += RESIST_MAX * level * _sine(CARRIER_HZ, t)
        elif kind == "weapon":
            if PRESSED <= pos and effect.start <= pos < effect.end:
                value += RESIST_MAX * effect.level * _sine(CARRIER_HZ, t)
        elif kind == "vibration":
            level = effect.levels[zone(pos)] if effect.levels else (
                effect.level if pos >= effect.start else 0.0)
            value += VIBRATION_MAX * level * _sine(min(effect.hz, MAX_HZ), t)
        elif effect.start <= pos <= effect.end:
            if kind == "galloping":
                phase = (t * effect.hz) % 1.0
                width = min(1 / 8, PULSE_S * effect.hz)
                if any(0 <= phase - foot / 8 < width for foot in (effect.first, effect.second)):
                    value += VIBRATION_MAX * _sine(BURST_HZ, t)
            elif kind == "machine":
                level = effect.level
                if effect.period > 0 and int(t / effect.period) % 2:
                    level = effect.level_b
                value += VIBRATION_MAX * level * _sine(min(effect.hz, MAX_HZ), t)
        return value


def bound(value):
    return max(-LIMIT, min(LIMIT, value))


class Renderer:
    """Both triggers mixed onto the single shared trigger column.

    render() returns (samples, selector) where selector is 0 left, 1 right or
    2 both, or (None, None) when neither trigger has an effect.
    """

    def __init__(self):
        self.left, self.right = Trigger(), Trigger()

    def side(self, name):
        return self.left if name == "left" else self.right

    def clear(self):
        self.left.clear()
        self.right.clear()

    def render(self, positions, now, count=8):
        for trigger, position in zip((self.left, self.right), positions):
            trigger.move(position, now)
        left, right = self.left.active(now), self.right.active(now)
        bursting = [t.burst_until > now for t in (self.left, self.right)]
        left, right = left or bursting[0], right or bursting[1]
        if not (left or right):
            return None, None
        chosen = [t for t, on in ((self.left, left), (self.right, right)) if on]
        selector = 2 if left and right else (0 if left else 1)
        samples = [bound(sum(t.sample(now + i / RATE) for t in chosen)) for i in range(count)]
        return samples, selector
