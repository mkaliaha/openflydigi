# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""RGB lighting stored on the pad.

Moved exactly like a mapping profile, with its own command ids:

    read        167  [cfgId, pkgSize]
    write start 168  [cfgId, startIdx, nPkts, pkgSize]
    write pack  169  [pktNum, data...]

**Blob layout**, verified against hardware (380 bytes, 19 packets):

    0..2   version, little endian (0x0300 seen)
    2      click feedback -- light reacts to rumble
    3      loop start frame
    4      loop end frame
    5      loop time (animation speed)
    6      brightness
    7      LED count (12 on an Apex 5)
    8      mode
    9..20  reserved
    20..   animation frames of `LED count` LEDs, 3 bytes each, RGB
           (10 frames x 12 LEDs on an Apex 5 -- see below)

The frames are an animation: `mode` picks the built-in effect, and the pad
cycles frames `loop_start`..`loop_end` at `loop_time`. A static colour is the
degenerate case -- every frame the same.
"""
from . import blobs
from .blobs import ProtocolError, build          # re-exported for callers

CMD_READ = 167
CMD_WRITE_START = 168
CMD_WRITE_PACK = 169

OFF_VERSION = 0
OFF_CLICK_FEEDBACK = 2
OFF_LOOP_START = 3
OFF_LOOP_END = 4
OFF_LOOP_TIME = 5
OFF_BRIGHTNESS = 6
OFF_LED_COUNT = 7
OFF_MODE = 8
OFF_FRAMES = 20

BRIGHTNESS_MAX = 100

# Frame geometry is not fixed. The decompiled parser walks 16 groups of 10,
# but that is the older 490-byte layout; an Apex 5 returns 380 bytes, and
# 380 - 20 = 360 = 10 frames x 12 LEDs, which is exactly its LED count and its
# loop range of 0..9. So derive both from the blob rather than assuming, or
# writing colours runs off the end and silently grows the config.
DEFAULT_LEDS_PER_FRAME = 10


def read_config(ctrl, cfg_id=0, wait=1.5, retries=3):
    """Read the lighting config. Unlike a mapping read this has no side effect."""
    return LedConfig(blobs.read_blob(ctrl, CMD_READ, cfg_id, "lighting config",
                                     wait=wait, retries=retries), cfg_id)


def write_config(ctrl, config, old=None, cfg_id=None, wait=0.5):
    """Write lighting back, sending only changed packets."""
    cfg_id = config.cfg_id if cfg_id is None else cfg_id
    return blobs.write_blob(ctrl, CMD_WRITE_START, CMD_WRITE_PACK, cfg_id or 0,
                            config.blob, old.blob if old is not None else None,
                            wait=wait)


class LedConfig:
    """The pad's lighting, as stored."""

    def __init__(self, blob, cfg_id=0):
        self.blob = bytearray(blob)
        self.cfg_id = cfg_id

    def copy(self):
        return LedConfig(bytearray(self.blob), self.cfg_id)

    @property
    def version(self):
        return (self.blob[OFF_VERSION + 1] << 8) | self.blob[OFF_VERSION]

    @property
    def brightness(self):
        return self.blob[OFF_BRIGHTNESS]

    @brightness.setter
    def brightness(self, value):
        self.blob[OFF_BRIGHTNESS] = max(0, min(BRIGHTNESS_MAX, int(value)))

    @property
    def mode(self):
        return self.blob[OFF_MODE]

    @mode.setter
    def mode(self, value):
        self.blob[OFF_MODE] = max(0, min(255, int(value)))

    @property
    def led_count(self):
        return self.blob[OFF_LED_COUNT]

    @property
    def click_feedback(self):
        """Whether the lighting reacts to rumble.

        Worth knowing about: with this on, the pad drives the LEDs itself in
        response to vibration, which can mask a colour set from the host.
        """
        return self.blob[OFF_CLICK_FEEDBACK] == 1

    @click_feedback.setter
    def click_feedback(self, value):
        self.blob[OFF_CLICK_FEEDBACK] = 1 if value else 0

    @property
    def speed(self):
        return self.blob[OFF_LOOP_TIME]

    @speed.setter
    def speed(self, value):
        self.blob[OFF_LOOP_TIME] = max(0, min(255, int(value)))

    @property
    def loop(self):
        return self.blob[OFF_LOOP_START], self.blob[OFF_LOOP_END]

    @loop.setter
    def loop(self, bounds):
        start, end = bounds
        last = max(0, self.frames - 1)
        self.blob[OFF_LOOP_START] = max(0, min(last, int(start)))
        self.blob[OFF_LOOP_END] = max(0, min(last, int(end)))

    # -- colours -----------------------------------------------------------

    @property
    def leds_per_frame(self):
        return self.blob[OFF_LED_COUNT] or DEFAULT_LEDS_PER_FRAME

    @property
    def frames(self):
        return max(0, (len(self.blob) - OFF_FRAMES) // (3 * self.leds_per_frame))

    def _offset(self, frame, led):
        if not 0 <= frame < self.frames:
            raise IndexError(f"frame {frame} out of range (have {self.frames})")
        if not 0 <= led < self.leds_per_frame:
            raise IndexError(f"led {led} out of range (have {self.leds_per_frame})")
        return OFF_FRAMES + (frame * self.leds_per_frame + led) * 3

    def led(self, frame, led):
        offset = self._offset(frame, led)
        return tuple(self.blob[offset : offset + 3])

    def set_led(self, frame, led, colour):
        offset = self._offset(frame, led)
        self.blob[offset : offset + 3] = bytes(
            max(0, min(255, int(c))) for c in colour[:3])

    def frame(self, index):
        return [self.led(index, led) for led in range(self.leds_per_frame)]

    def set_solid(self, colour):
        """One colour, everywhere, on every frame.

        Written to all frames rather than only the first because the pad keeps
        cycling `loop_start`..`loop_end` whatever we do; making every frame
        identical is what actually reads as a static colour.
        """
        for frame in range(self.frames):
            for led in range(self.leds_per_frame):
                self.set_led(frame, led, colour)

    def __repr__(self):
        return (f"<LedConfig v{self.version:#06x} mode={self.mode} "
                f"brightness={self.brightness} "
                f"{self.frames}x{self.leds_per_frame} leds "
                f"loop={self.loop} speed={self.speed}>")
