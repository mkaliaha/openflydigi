# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

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

Deathloop was first measured writing ch3 only, from the PipeWire side. Measured
again at the device itself, both actuator channels carry signal and track each
other closely, with ch2 often slightly stronger, while ch0 and ch1 stay at
exactly zero. The direct observation is the better one. They are summed anyway,
because the split below is by frequency rather than by side.

**Conversion approach.** The DualSense's actuators are full-range voice coils;
the Apex 5's motors are not interchangeable -- the left is a large low-frequency
mass, the right a small high-frequency one. Mapping left-to-left would discard
the character of the waveform, so instead the haptic signal is split by
frequency: low-band energy drives the left motor, high-band the right. That
reproduces the feel of the effect rather than only its loudness.
"""
import math
import struct
from array import array

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


# A USB DualSense sends s16le; parec was asked for float32. Both feed the same
# DSP, so the conversion lives here rather than in each caller.
#
# DECIMATE exists because ERM motors respond over roughly 50-250 Hz, so 48 kHz
# carries about a hundred times more resolution than the motors can physically
# express. Averaging blocks of samples rather than dropping them matters: plain
# decimation folds everything above the new Nyquist back down into the band
# being measured, which inflates the high band with energy that was never there.
DECIMATE = 8


def unpack_s16(buf, stride=CHANNELS, decimate=1, channels=HAPTIC_CHANNELS):
    """Interleaved s16le bytes -> interleaved floats, block-averaged.

    Only `channels` are filled; the rest stay zero, since Splitter sums only
    those and nothing else reads them.
    """
    samples = array("h")
    usable = len(buf) - len(buf) % (2 * stride)
    samples.frombytes(buf[:usable])
    frames = len(samples) // stride
    if frames == 0:
        return []
    if decimate <= 1:
        return [v / 32768.0 for v in samples]

    out_frames = frames // decimate
    if out_frames == 0:
        return []
    out = [0.0] * (out_frames * stride)
    scale = 1.0 / (decimate * 32768.0)
    for ch in channels:
        column = samples[ch::stride]
        for i in range(out_frames):
            out[i * stride + ch] = sum(column[i * decimate:(i + 1) * decimate]) * scale
    return out


def channel_energy(buf, stride=CHANNELS):
    """Per-channel (sum of squares, peak, frame count) for interleaved s16le.

    array + a generator sum rather than a per-sample int.from_bytes loop: same
    arithmetic, about four times the speed. The peak uses max()/min() on the
    array slice, which runs at C speed, and is clamped to 32767 -- abs(-32768)
    is 32768, which reads as "louder than a sample can be".
    """
    samples = array("h")
    usable = len(buf) - len(buf) % (2 * stride)
    samples.frombytes(buf[:usable])
    frames = len(samples) // stride
    if frames == 0:
        return [], [], 0
    sumsq, peaks = [], []
    for ch in range(stride):
        column = samples[ch::stride]
        sumsq.append(sum(x * x for x in column))
        peaks.append(min(32767, max(max(column), -min(column))))
    return sumsq, peaks, frames


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
