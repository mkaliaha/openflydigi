# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Stereo PCM -> experimental Apex 6 linear grip output.

This is a sample stream, not the rotary-motor RMS mailbox in haptics.py.
Session is driven from one owning thread; feed/tick accept a fake clock for
deterministic scheduling tests. No audio, HID or timing dependency is required.
"""
from collections import deque
import math
import struct
import time

from . import gpa6, identity

RATE = 1000
SAMPLES = 8
PERIOD = SAMPLES / RATE


class StereoResampler:
    """48 kHz s16le PCM, stereo or DS5 four-channel, to 1 kHz stereo.

    A 769-tap Blackman-windowed sinc (350 Hz cutoff) filters before decimation.
    Its eight millisecond group delay is fixed; byte/frame and filter history
    survive arbitrary input chunk boundaries. Channels never mix.
    """
    def __init__(self, channels=2, gain=1.0):
        if channels not in (2, 4) or not math.isfinite(gain) or not 0 <= gain <= 4:
            raise ValueError("expected 2/4 channels and finite gain from 0 to 4")
        self.channels, self.gain = channels, gain
        taps = []
        for i in range(769):
            x = i - 384
            sinc = 2 * 350 / 48000 if x == 0 else math.sin(2 * math.pi * 350 * x / 48000) / (math.pi * x)
            window = 0.42 - 0.5 * math.cos(2 * math.pi * i / 768) + 0.08 * math.cos(4 * math.pi * i / 768)
            taps.append(sinc * window)
        total = sum(taps)
        self.taps = [t / total for t in taps]
        self.reset()

    def reset(self):
        self.left = deque([0.0] * 769, maxlen=769)
        self.right = deque([0.0] * 769, maxlen=769)
        self.pending = b""
        self.phase = 0

    def feed(self, pcm):
        buf = self.pending + pcm
        width = self.channels * 2
        usable = len(buf) // width * width
        self.pending = buf[usable:]
        result = []
        for samples in struct.iter_unpack("<" + "h" * self.channels, buf[:usable]):
            self.left.append(samples[-2] / 32768.0)
            self.right.append(samples[-1] / 32768.0)
            self.phase += 1
            if self.phase == 48:
                self.phase = 0
                result.append((sum(x * t for x, t in zip(self.left, self.taps)) * self.gain,
                               sum(x * t for x, t in zip(self.right, self.taps)) * self.gain))
        return result


class ProfileChanged(gpa6.ProtocolError):
    pass


class GripSession:
    """Exclusive, UID-bound volatile grip session with rollback on every exit.

    Format versions are reported, not guessed into old profile codecs. Motor
    RAM layout 2 is the compatibility gate. Unknown layouts cannot be restored
    and are refused. A profile change ends the session, restoring that profile's
    current persistent grip map rather than overwriting it with a stale one.
    """
    def __init__(self, ctrl, *, experimental=False, expected_uid=None,
                 channels=2, gain=1.0, clock=time.monotonic, max_latency=0.040):
        if not experimental:
            raise identity.WrongDevice("Apex 6 grip output requires explicit experimental opt-in")
        if not PERIOD <= max_latency <= 0.100 or not math.isfinite(max_latency):
            raise ValueError("max latency must be between 8 and 100 ms")
        self.ctrl, self.protocol = ctrl, gpa6.Protocol(ctrl)
        self.expected_uid, self.clock = expected_uid, clock
        self.resampler = StereoResampler(channels, gain)
        self.max_latency = max_latency
        self.capacity = int(max_latency * RATE)
        self.queue = deque()
        self.active = self.dirty = False
        self._claim = None
        self.sent = self.dropped = self.underruns = 0
        self.last_feed = None

    def start(self):
        if self._claim is not None:
            raise RuntimeError("grip session already started")
        claim = self.ctrl.claim(timeout=0)
        claim.__enter__()
        if getattr(self.ctrl, "_grip_session", None) is not None:
            claim.__exit__(None, None, None)
            from .device import DeviceBusy
            raise DeviceBusy("this controller already has a grip session")
        self.ctrl._grip_session = self
        self._claim = claim
        try:
            identity.require(self.ctrl, "k6")
            self.info = self.protocol.info()
            if not self.info["haptic_grip"]:
                raise identity.WrongDevice("this Apex 6 reports rotary grips, not hapticGrip")
            self.uid = self.protocol.uid()
            if self.expected_uid is not None and self.uid != self.expected_uid:
                raise identity.WrongDevice("selected Apex 6 UUID changed; refusing replacement pad")
            self.versions = self.protocol.versions()
            self.slot, self.saved = self.protocol.motor_map()
            # Mark dirty before the write: a timed-out ACK can still mean the
            # pad applied the command. Roll back that case too.
            self.dirty = True
            # Native RVA 21e27d: area=1, target=both grips, realtime=2,
            # params 40 00. No trigger selector assumptions are needed here.
            self.protocol.mode(0x12, 2, b"\x40\x00")
            self.active = True
            self.queue.clear()
            self.resampler.reset()
            self.last_feed = None
            self.next_packet = self.clock()
            self.next_check = self.next_packet
            return self
        except BaseException as exc:
            try:
                self.stop()
            except Exception as cleanup:
                raise gpa6.ProtocolError(f"{exc}; cleanup failed: {cleanup}") from exc
            raise

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def feed(self, pcm, now=None):
        if not self.active:
            raise RuntimeError("grip session is not active")
        now = self.clock() if now is None else now
        if self.last_feed is not None and now - self.last_feed > self.max_latency:
            self.dropped += len(self.queue)
            self.queue.clear()
            self.resampler.reset()
        self.last_feed = now
        # Bound both DSP work and queued latency when a caller delivers a large
        # stale backlog. Keep the newest whole PCM frames and reset the filter.
        limit = self.capacity * 48 * self.resampler.channels * 2
        if len(pcm) + len(self.resampler.pending) > limit:
            pcm = self.resampler.pending + pcm
            width = self.resampler.channels * 2
            skip = (len(pcm) - limit) // width * width
            self.dropped += skip // (48 * width) + len(self.queue)
            self.queue.clear()
            self.resampler.reset()
            pcm = pcm[skip:]
        for left, right in self.resampler.feed(pcm):
            self.queue.append((now, left, right))
        while len(self.queue) > self.capacity:
            self.queue.popleft()
            self.dropped += 1

    def tick(self, now=None):
        if not self.active:
            raise RuntimeError("grip session is not active")
        now = self.clock() if now is None else now
        if now + 1e-9 < self.next_packet:
            return False
        try:
            if now >= self.next_check:
                if self.protocol.uid() != self.uid:
                    raise identity.WrongDevice("selected Apex 6 UUID changed")
                if self.protocol.slot() != self.slot:
                    raise ProfileChanged("profile changed; restart the grip session")
                # Queries can block. Age samples and schedule from their
                # completion, even when the caller supplied the tick time.
                now = max(now, self.clock())
                self.next_check = now + 0.25
            # Do not burst old packets after a scheduler stall.
            missed = int(max(0, now - self.next_packet) / PERIOD)
            for _ in range(min(len(self.queue), missed * SAMPLES)):
                self.queue.popleft()
                self.dropped += 1
            while self.queue and now - self.queue[0][0] > self.max_latency:
                self.queue.popleft()
                self.dropped += 1
            left, right = [], []
            for _ in range(min(SAMPLES, len(self.queue))):
                _, l, r = self.queue.popleft()
                left.append(l)
                right.append(r)
            self.underruns += SAMPLES - len(left)
            self.ctrl.send(self.protocol.layout.wrap(gpa6.waveform(left=left, right=right)), wait=0)
            self.sent += 1
            self.next_packet += (missed + 1) * PERIOD
            return True
        except BaseException as exc:
            try:
                self.stop()
            except Exception as cleanup:
                if isinstance(exc, gpa6.ReplyTimeout) and isinstance(cleanup, (OSError, gpa6.ReplyTimeout)):
                    raise gpa6.ReplyTimeout(f"{exc}; cleanup failed: {cleanup}") from exc
                if isinstance(exc, OSError) and isinstance(cleanup, (OSError, gpa6.ReplyTimeout)):
                    raise exc from cleanup
                raise gpa6.ProtocolError(f"{exc}; cleanup failed: {cleanup}") from exc
            raise

    def stop(self):
        self.active = False
        self.queue.clear()
        self.resampler.reset()
        if self._claim is None:
            return
        errors = []
        try:
            if self.dirty:
                # Never restore the saved state to a replacement at the same
                # path. A disconnected handle cannot safely receive cleanup.
                if self.protocol.uid() != self.uid:
                    raise identity.WrongDevice("UUID changed; cleanup refused on replacement pad")
                for frame in (gpa6.waveform(), gpa6.waveform(enabled=(False, False, False))):
                    try:
                        self.ctrl.send(self.protocol.layout.wrap(frame), wait=0)
                    except Exception as exc:
                        errors.append(str(exc))
                try:
                    self.protocol.mode(0x12, 0)
                except Exception as exc:
                    errors.append(str(exc))
                # Slot changes may happen through the pad itself even though
                # our tools are excluded by the session claim.
                mapping = self.saved
                if self.protocol.slot() != self.slot:
                    _, mapping = self.protocol.motor_map()
                for target, mode, params in gpa6.grip_restore(mapping):
                    try:
                        self.protocol.mode(target, mode, params)
                    except Exception as exc:
                        errors.append(str(exc))
        finally:
            self.dirty = False
            claim, self._claim = self._claim, None
            self.ctrl._grip_session = None
            claim.__exit__(None, None, None)
        if errors:
            raise gpa6.ProtocolError("grip cleanup incomplete: " + "; ".join(errors))
