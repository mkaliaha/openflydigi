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
    2      click feedback -- set when the mode is Feedback; see below
    3      loop start frame
    4      loop end frame
    5      cycle time -- Space Station calls this "Cycle time", so a
           larger number is a slower animation, not a faster one
    6      brightness
    7      LED count (12 on an Apex 5)
    8      mode
    9      grip sync -- the lights react to vibration. **This is the rumble
           switch**, and byte 2 is not; both are measured, see below
    10..20 reserved
    20..   animation frames of `LED count` LEDs, 3 bytes each, RGB
           (10 frames x 12 LEDs on an Apex 5 -- see below)

**The frames are the effect.** The pad has no built-in animation generator: it
plays the stored frames, cycling `loop_start`..`loop_end` every `cycle_time`.
Space Station's UI offers "Breath" and "Flow" over a list of colours and
computes the frames from them before uploading, so the `mode` byte only records
which of its modes produced the data. Writing a different mode number changes
nothing visible -- to change the lighting you have to write frames, which is
what `set_breath`, `set_flow`, `set_rainbow` and `set_solid` do.

**The rumble switch is byte 9, and this module had it at byte 2 for its whole
life.** Byte 9 is `GripSync`, which Flydigi call "Vibration light effect", and it
is the one that works: at a fixed brightness, with the same left-grip rumble and
nothing else changed, the ring dimmed a segment with it set and did nothing with
it clear. Byte 2 is `ClickFeedback`, which Space Station sets *because* the mode
is Feedback rather than as a control of its own -- and setting it alone over the
pad's stock frames produced no reaction to any button, paddle, stick or trigger,
with third-party control on and again off. It is kept because it is a real field
a Feedback-mode writer would need, not because it does anything by itself.
-> docs/device-settings.md
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
OFF_LOOP_TIME = 5      # "cycle time": bigger is slower
OFF_BRIGHTNESS = 6
OFF_LED_COUNT = 7
OFF_MODE = 8
OFF_GRIP_SYNC = 9
OFF_FRAMES = 20

BRIGHTNESS_MAX = 100

# Frame geometry is not fixed. The decompiled parser walks 16 groups of 10,
# but that is the older 490-byte layout; an Apex 5 returns 380 bytes, and
# 380 - 20 = 360 = 10 frames x 12 LEDs, which is exactly its LED count and its
# loop range of 0..9. So derive both from the blob rather than assuming, or
# writing colours runs off the end and silently grows the config.
DEFAULT_LEDS_PER_FRAME = 10

# Effect ids as Space Station numbers them, recovered from its own enum. An
# Apex 5 ships storing 7 (wave).
#
# These are what goes in the mode byte. The *look*, though, comes from the
# frames -- changing the byte alone was not observed to alter anything -- so
# the generators below produce frame data and set the matching id, which keeps
# a config coherent if Space Station ever reads it back.
EFFECT_OFF = 0
EFFECT_STREAMING = 1        # "Flow"
EFFECT_ROTATION = 2
EFFECT_BREATHING = 3
EFFECT_STATIC_SINGLE = 4
EFFECT_STATIC_MULTI = 5
EFFECT_RAINBOW = 6
EFFECT_WAVE = 7
EFFECT_FLASH = 8
EFFECT_SMART = 9
EFFECT_CUSTOM = 99

# Space Station allows five, and offers this blue when adding one.
MAX_COLOURS = 5
DEFAULT_COLOUR = (0, 116, 255)


def _blend(first, second, amount):
    return tuple(int(a + (b - a) * amount) for a, b in zip(first, second))


def _sample(colours, position):
    """Colour at `position` (0..1) around a cyclic list, blended between stops."""
    count = len(colours)
    if count == 1:
        return tuple(colours[0])
    exact = position * count
    index = int(exact) % count
    return _blend(colours[index], colours[(index + 1) % count], exact - int(exact))


def _hue(position):
    """A saturated, full-brightness colour at `position` (0..1) round the wheel."""
    sector = position * 6.0
    offset = int(sector) % 6
    rising = int(255 * (sector - int(sector)))
    falling = 255 - rising
    return [(255, rising, 0), (falling, 255, 0), (0, 255, rising),
            (0, falling, 255), (rising, 0, 255), (255, 0, falling)][offset]


def suggest_colour(existing=()):
    """A colour to offer when adding one, distinct from what is already there.

    Space Station always offers the same blue, which is fine in a UI that shows
    a colour wheel next to it but reads as "adding did nothing" here: a second
    identical colour produces an identical animation.
    """
    existing = list(existing)
    if not existing:
        return DEFAULT_COLOUR
    return _hue((len(existing) / MAX_COLOURS + 0.33) % 1.0)


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
    def grip_sync(self):
        """Whether the lighting reacts to vibration. Flydigi: "Vibration light
        effect", "there will be a special light effect when the grip vibrates".

        The real switch, and measured: with the same rumble at the same
        brightness, the ring dims a segment with this set and does nothing with
        it clear. Which segment did not reproduce between runs, so the side
        mapping is open -- see docs/device-settings.md.

        It lives at byte 9, inside what this module used to call reserved, which
        is why `click_feedback` wore the label for so long.
        """
        return self.blob[OFF_GRIP_SYNC] == 1

    @grip_sync.setter
    def grip_sync(self, value):
        self.blob[OFF_GRIP_SYNC] = 1 if value else 0

    @property
    def click_feedback(self):
        """Byte 2. **Not the rumble switch** -- that is `grip_sync`.

        Space Station sets this exactly when the chosen mode is `Feedback`
        (`ClickFeedback = config.Mode == LedType.Feedback`), so it is a record of
        the generator rather than a control. Set on its own over the pad's stock
        frames it did nothing for any button, paddle, stick or trigger, with
        third-party control on and again off.

        Kept because a writer that reproduced Space Station's Feedback mode
        would need it -- that mode is byte 2 set, the one colour in frame 0,
        every other frame black and `loop_end = 1`, and what the pad then does
        is still unknown.
        """
        return self.blob[OFF_CLICK_FEEDBACK] == 1

    @click_feedback.setter
    def click_feedback(self, value):
        self.blob[OFF_CLICK_FEEDBACK] = 1 if value else 0

    @property
    def cycle_time(self):
        """How long one animation step lasts. Bigger is slower."""
        return self.blob[OFF_LOOP_TIME]

    @cycle_time.setter
    def cycle_time(self, value):
        self.blob[OFF_LOOP_TIME] = max(1, min(255, int(value)))

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

    # -- effects -----------------------------------------------------------
    #
    # The pad does not synthesise animations. It plays back the frames stored
    # here, cycling loop_start..loop_end every `cycle_time`. Space Station's UI
    # offers "Breath" and "Flow" over a list of colours and computes the frames
    # from them; the mode byte only records which of its modes produced them,
    # so writing a different mode alone changes nothing visible. These build
    # the frame data, which is what actually changes the lighting.

    def _all_frames(self):
        self.loop = (0, max(0, self.frames - 1))

    def apply_effect(self, effect, colours=()):
        """Generate frames for one of Space Station's effect ids.

        The frame data is ours -- only the ids and the names come from their
        software -- so these match in spirit rather than pixel for pixel.
        """
        colours = list(colours) or [DEFAULT_COLOUR]
        builder = {
            EFFECT_OFF: lambda: self.set_off(),
            EFFECT_STREAMING: lambda: self.set_flow(colours),
            EFFECT_ROTATION: lambda: self.set_rotation(colours),
            EFFECT_BREATHING: lambda: self.set_breath(colours),
            EFFECT_STATIC_SINGLE: lambda: self.set_solid(colours[0]),
            EFFECT_STATIC_MULTI: lambda: self.set_static_multi(colours),
            EFFECT_RAINBOW: lambda: self.set_rainbow(),
            EFFECT_WAVE: lambda: self.set_wave(colours),
            EFFECT_FLASH: lambda: self.set_flash(colours),
        }.get(effect)
        if builder is None:
            raise ValueError(f"no frame generator for effect {effect}")
        builder()
        self.mode = effect

    def set_breath(self, colours):
        """Fade the whole pad in and out through each colour in turn."""
        colours = list(colours) or [(255, 255, 255)]
        for frame in range(self.frames):
            # Triangular ramp so the loop joins back to itself smoothly.
            position = frame / max(1, self.frames)
            level = 1.0 - abs(2.0 * position - 1.0)
            colour = colours[int(position * len(colours)) % len(colours)]
            faded = tuple(int(channel * level) for channel in colour)
            for led in range(self.leds_per_frame):
                self.set_led(frame, led, faded)
        self._all_frames()

    def set_flow(self, colours):
        """Run the colours along the pad, shifting by one frame each step."""
        colours = list(colours) or [(255, 0, 0)]
        leds = self.leds_per_frame
        for frame in range(self.frames):
            for led in range(leds):
                # Position along the colour list, advanced by the frame so the
                # pattern travels rather than merely repeating.
                position = (led / leds + frame / max(1, self.frames)) % 1.0
                self.set_led(frame, led, _sample(colours, position))
        self._all_frames()

    def set_rotation(self, colours):
        """A lit arc travelling around the pad, the rest dark."""
        colours = list(colours) or [DEFAULT_COLOUR]
        leds = self.leds_per_frame
        span = max(1, leds // 3)          # how much of the ring is lit at once
        for frame in range(self.frames):
            head = frame * leds / max(1, self.frames)
            colour = colours[frame % len(colours)]
            for led in range(leds):
                # Distance behind the head, wrapped, so the arc fades out.
                behind = (head - led) % leds
                level = max(0.0, 1.0 - behind / span)
                self.set_led(frame, led,
                             tuple(int(channel * level) for channel in colour))
        self._all_frames()

    def set_wave(self, colours):
        """A brightness wave running along the pad."""
        colours = list(colours) or [DEFAULT_COLOUR]
        leds = self.leds_per_frame
        for frame in range(self.frames):
            for led in range(leds):
                position = (led / leds + frame / max(1, self.frames)) % 1.0
                # Triangular rather than sinusoidal, to keep this dependency
                # free and because the difference is not visible on 12 LEDs.
                level = 1.0 - abs(2.0 * position - 1.0)
                colour = _sample(colours, led / leds)
                self.set_led(frame, led,
                             tuple(int(channel * level) for channel in colour))
        self._all_frames()

    def set_flash(self, colours):
        """Everything on, everything off."""
        colours = list(colours) or [DEFAULT_COLOUR]
        for frame in range(self.frames):
            lit = frame % 2 == 0
            colour = colours[(frame // 2) % len(colours)] if lit else (0, 0, 0)
            for led in range(self.leds_per_frame):
                self.set_led(frame, led, colour)
        self._all_frames()

    def set_static_multi(self, colours):
        """The colours spread across the pad at once, not animated."""
        colours = list(colours) or [DEFAULT_COLOUR]
        leds = self.leds_per_frame
        for frame in range(self.frames):
            for led in range(leds):
                self.set_led(frame, led, _sample(colours, led / leds))
        self._all_frames()

    def set_off(self):
        self.set_solid((0, 0, 0))

    def set_rainbow(self):
        """A full hue sweep, the degenerate case of flow over the wheel."""
        self.set_flow([_hue(index / 6.0) for index in range(6)])

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
                f"loop={self.loop} cycle={self.cycle_time}>")
