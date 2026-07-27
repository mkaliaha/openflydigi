"""Convert DualSense haptic audio into Apex 5 rumble.

Some games drive the DualSense's voice coils by writing waveforms to the
controller's USB audio device rather than using the HID motor fields. Deathloop
is one; it opens a dedicated stream to the controller alongside its normal game
audio.

Channel layout of a real DualSense's 4-channel audio device, established by
playing tones into each channel and having a human report what happened:

    ch0  headphone jack
    ch1  speaker
    ch2  left haptic actuator
    ch3  right haptic actuator

Deathloop drives ch3 only, so treat the haptic pair as one signal rather than
assuming stereo content.

**Conversion approach.** The DualSense's actuators are full-range voice coils;
the Apex 5's motors are not interchangeable -- the left is a large low-frequency
mass, the right a small high-frequency one. Mapping left-to-left would discard
the character of the waveform, so instead the haptic signal is split by
frequency: low-band energy drives the left motor, high-band the right. That
reproduces the feel of the effect rather than only its loudness.
"""
import math
import struct

# Channel indices in a real DualSense audio device.
CH_JACK, CH_SPEAKER, CH_HAPTIC_LEFT, CH_HAPTIC_RIGHT = 0, 1, 2, 3
HAPTIC_CHANNELS = (CH_HAPTIC_LEFT, CH_HAPTIC_RIGHT)

RATE = 48000
CHANNELS = 4
FRAME_BYTES = 4 * CHANNELS      # float32 per channel

# Crossover between "big slow motor" and "small fast motor" content. ERM motors
# respond over roughly 50-250 Hz; splitting near the middle gives each motor
# something it can actually reproduce.
CROSSOVER_HZ = 150.0

SILENCE = 5e-5

# The DualSense's voice coils have a perceptual floor that ERM motors do not:
# quiet haptic content that is imperceptible on a real DualSense still spins a
# motor. Gate it out rather than faithfully reproducing signal the reference
# hardware effectively ignores.
GATE = 0.015

# Shaping exponent. 0.5 (square root) lifts quiet content hard, which is what
# made the pad feel more sensitive than the DualSense; higher values keep quiet
# passages quiet while still compressing the top end.
CURVE = 0.7


class Splitter:
    """Splits a haptic signal into low and high bands and measures each.

    A one-pole low-pass carried across chunks: `low` follows slow movement,
    and whatever is left over is the fast content. State has to persist between
    calls or every chunk boundary produces a click in the measurement.
    """

    def __init__(self, rate=RATE, crossover=CROSSOVER_HZ):
        self.alpha = 1.0 - math.exp(-2.0 * math.pi * crossover / rate)
        self.low_state = 0.0

    def process(self, samples, channels=HAPTIC_CHANNELS, stride=CHANNELS):
        """Return (low_rms, high_rms) for the given interleaved samples."""
        frames = len(samples) // stride
        if frames == 0:
            return 0.0, 0.0
        low_acc = high_acc = 0.0
        alpha = self.alpha
        low = self.low_state
        for f in range(frames):
            base = f * stride
            # Sum the haptic channels: the actuators are physically separate but
            # we are driving two motors chosen by frequency, not by side.
            x = 0.0
            for ch in channels:
                x += samples[base + ch]
            low += alpha * (x - low)
            high = x - low
            low_acc += low * low
            high_acc += high * high
        self.low_state = low
        return math.sqrt(low_acc / frames), math.sqrt(high_acc / frames)


def to_motor(level, gain, gate=GATE, curve=CURVE):
    """Map a band level to a motor value (0-255).

    Compressive shaping because audio energy spans a far wider range than the
    motors can express, and a gate below which nothing is emitted at all.
    """
    if level < max(gate, SILENCE):
        return 0
    # Rescale above the gate so the first perceptible motion starts there
    # rather than jumping straight to a mid value.
    scaled = (level - gate) / max(1e-9, 1.0 - gate)
    return int(min(1.0, (scaled * gain) ** curve) * 255)


def unpack(buf, stride=CHANNELS):
    frames = len(buf) // (4 * stride)
    if frames == 0:
        return ()
    return struct.unpack_from(f"<{frames * stride}f", buf, 0)


def levels_to_motors(low, high, gain, gate=GATE, curve=CURVE):
    """Low band -> left (large, low-frequency), high band -> right (small)."""
    return to_motor(low, gain, gate, curve), to_motor(high, gain, gate, curve)
