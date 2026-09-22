# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Experimental USB DualSense input and feedback for GPA6 linear grips.

The output thread owns its HID handle and session claim, including cleanup.
USB callbacks only enqueue bounded PCM or replace a rumble level. Input uses a
separate, UUID-pinned evdev link; it never closes the output worker's handle.
Trigger translation and vendor motion reports are deliberately not enabled.
"""
from collections import deque
import math
import os
import select
import threading
import time

from . import ds5, dsmode, evdev, gpa6, identity, registry, relay, usbip, waveform
from .device import DeviceBusy, DeviceNotFound
from .ds5_usbip import DualSense, EP_HID_IN

PCM_WIDTH = 8  # four signed 16-bit channels
PCM_RATE = 48000
MAX_LATENCY = 0.040
MAX_BYTES = int(PCM_RATE * MAX_LATENCY) * PCM_WIDTH


class InputLink(relay.PadLink):
    """Use only the gamepad underneath the selected GPA6 USB device."""

    def __init__(self, selector, **kwargs):
        if not selector:
            raise ValueError("experimental Apex 6 relay requires --device")
        super().__init__(want_ctrl=False, want_motion=False, pad=selector, **kwargs)
        self.uid = None

    def _resolve(self):
        try:
            entry = registry.find(self.pad, registry.KIND_PAD, deep=True)
            expected = self.uid or entry.get("uid")
            if not expected:
                raise identity.WrongDevice("selected controller did not report a UUID")
            with registry.open_pad(entry["path"]) as ctrl:
                identity.require(ctrl, "k6")
                protocol = gpa6.Protocol(ctrl)
                if protocol.uid() != expected:
                    raise identity.WrongDevice("selected UUID changed while opening input")
                if not protocol.info()["haptic_grip"]:
                    raise identity.WrongDevice("selected Apex 6 does not report linear grips")
            root = evdev.usb_device_of_hidraw(entry["path"])
            if root is None:
                return "selected Apex 6 has no USB input device"
            self.uid = expected
            self.pad = "uid:" + expected
            self.path, self.usb_root = entry["path"], root
        except (OSError, DeviceNotFound, DeviceBusy, identity.WrongDevice, gpa6.ProtocolError) as exc:
            return str(exc)
        return None

    def _open_reader(self):
        # No product/name fallback outside this USB root. Receiver input PIDs
        # need not equal the vendor interface PID.
        path, name = evdev.find_device(vendor=relay.FLYDIGI_VID, axes=True,
                                       usb_root=self.usb_root)
        if not path:
            return self._reader_unavailable(
                "selected Apex 6 has no evdev gamepad; use XInput mode")
        try:
            reader = evdev.Reader(path)
        except OSError as exc:
            return self._reader_unavailable(f"cannot open selected Apex 6 input: {exc}")
        required = (evdev.ABS_X, evdev.ABS_Y, evdev.ABS_RX, evdev.ABS_RY,
                    evdev.ABS_Z, evdev.ABS_RZ, evdev.ABS_HAT0X, evdev.ABS_HAT0Y)
        if not all(axis in reader.abs for axis in required):
            reader.close()
            return self._reader_unavailable(
                "selected input lacks Xbox-style axes; use XInput mode")
        return reader, name, None

    def _reader_unavailable(self, problem):
        # Vendor and input nodes can appear separately. Until both are open,
        # retry the pinned UUID in case it moves to another USB port.
        self.path = self.usb_root = None
        return None, None, problem

    def _node_present(self):
        if not super()._node_present():
            return False
        node = os.path.basename(self.reader.path)
        return evdev.usb_device(f"/sys/class/input/{node}/device") == self.usb_root


class DualSenseSession(waveform.GripSession):
    """Combine game PCM with an approximate, independent HID rumble source.

    80/160 Hz, maximum amplitude 0.35 per side, are application choices rather
    than a recovered Flydigi/DS5 transfer function. The encoder clips the sum.
    Phase follows elapsed time so scheduler stalls cannot accumulate a burst.
    """

    def __init__(self, ctrl, **kwargs):
        super().__init__(ctrl, channels=4, **kwargs)
        self.rumble = (0, 0)

    def _write_samples(self, left, right, now):
        mixed = []
        for values, level, hz in zip((left, right), self.rumble, (80, 160)):
            mixed.append([
                (values[i] if i < len(values) else 0.0)
                + 0.35 * level / 255 * math.sin(2 * math.pi * hz * (now + i / waveform.RATE))
                for i in range(waveform.SAMPLES)
            ])
        super()._write_samples(*mixed, now)


class Feedback:
    """Bounded cross-thread inbox plus a single-owner session state machine.

    step()/close() run on the output thread (or a test's fake clock). Callbacks
    can run on any thread and never perform HID I/O or DSP. A disconnected
    input link invalidates all pending feedback. Reopening always rechecks UUID.
    """

    def __init__(self, uid, *, audio=True, gain=1.0, clock=time.monotonic,
                 open_pad=registry.open_pad, log=None):
        # Validate before the privileged attach or worker starts.
        if not math.isfinite(gain) or not 0 <= gain <= 4:
            raise ValueError("Apex 6 audio gain must be finite and between 0 and 4")
        self.uid, self.audio, self.gain = uid, audio, gain
        self.clock, self.open_pad = clock, open_pad
        self.log = log or (lambda _msg: None)
        self.lock = threading.Lock()
        self.blocks = deque()
        self.queued_bytes = 0
        self.discontinuity = False
        self.rumble = (0, 0)
        self.connected = self.accepting = False
        self.epoch = 0
        self.session_epoch = -1
        self.ctrl = self.session = None
        self.next_try = 0.0
        self.dropped_bytes = self.invalid_urbs = self.output_reports = 0
        self.ignored_triggers = 0
        self.sent = 0
        self.failed = None
        self.stop_event = threading.Event()
        self.thread = None
        self._problem = None

    def _clear_audio(self):
        self.dropped_bytes += self.queued_bytes
        self.blocks.clear()
        self.queued_bytes = 0
        self.discontinuity = True

    def _clear(self):
        self._clear_audio()
        self.rumble = (0, 0)

    def set_connected(self, connected):
        with self.lock:
            if connected != self.connected:
                self.connected = connected
                self.epoch += 1
                self.accepting = False
                self._clear()

    def on_output(self, data):
        parsed = ds5.parse_output(data)
        with self.lock:
            self.output_reports += 1
            self.ignored_triggers += len(parsed["effects"])
            if self.accepting and parsed["rumble"] is not None:
                self.rumble = parsed["rumble"]

    def on_haptics(self, data, packets):
        if not self.audio:
            return
        now = self.clock()
        with self.lock:
            if not self.accepting:
                return
            # OUT uses requested lengths; actual_length is normally zero.
            # Respect offsets (which may include padding), reject malformed
            # descriptors and substitute silence for failed packets.
            end = 0
            for offset, length, _actual, _status in packets:
                if (offset < end or length < 0 or length % PCM_WIDTH
                        or offset + length > len(data)):
                    self.invalid_urbs += 1
                    self._clear_audio()
                    return
                end = offset + length
            for offset, length, _actual, status in packets:
                if not length:
                    continue
                keep = min(length, MAX_BYTES)
                if keep < length:
                    self.dropped_bytes += length - keep
                    self.discontinuity = True
                block = bytes(keep) if status else bytes(data[offset + length - keep:offset + length])
                self.blocks.append((now, block))
                self.queued_bytes += keep
                while self.queued_bytes > MAX_BYTES:
                    stamp, oldest = self.blocks.popleft()
                    count = min(len(oldest), self.queued_bytes - MAX_BYTES)
                    self.queued_bytes -= count
                    self.dropped_bytes += count
                    self.discontinuity = True
                    if count < len(oldest):
                        self.blocks.appendleft((stamp, oldest[count:]))

    def _disconnect(self):
        with self.lock:
            self.accepting = False
            self._clear()
        session, ctrl = self.session, self.ctrl
        self.session = self.ctrl = None
        try:
            if session is not None:
                session.stop()
        finally:
            if ctrl is not None:
                ctrl.close()

    def step(self):
        with self.lock:
            connected, epoch = self.connected, self.epoch
        try:
            if self.session is not None and (not connected or epoch != self.session_epoch):
                self._disconnect()
            if not connected:
                return
            if self.session is None:
                if self.clock() < self.next_try:
                    return
                self.ctrl = self.open_pad("uid:" + self.uid)
                self.session = DualSenseSession(
                    self.ctrl, experimental=True, expected_uid=self.uid,
                    gain=self.gain, clock=self.clock, max_latency=MAX_LATENCY)
                self.session.start()
                self.session_epoch = epoch
                with self.lock:
                    self._clear()
                    self.accepting = self.connected and self.epoch == epoch
                self._problem = None
                self.log("Apex 6 grip feedback connected")
                # The next step takes only samples received after setup.
                return
            now = self.clock()
            with self.lock:
                blocks = list(self.blocks)
                self.blocks.clear()
                self.queued_bytes = 0
                reset, self.discontinuity = self.discontinuity, False
                rumble = self.rumble
            fresh = []
            for stamp, data in blocks:
                if now - stamp > MAX_LATENCY:
                    self.dropped_bytes += len(data)
                    reset = True
                else:
                    fresh.append((stamp, data))
            if reset:
                self.session.dropped += len(self.session.queue)
                self.session.queue.clear()
                self.session.resampler.reset()
                self.session.last_feed = None
            for stamp, data in fresh:
                self.session.feed(data, now=stamp)
            self.session.rumble = rumble
            if self.session.tick():
                self.sent += 1
        except (OSError, DeviceNotFound, DeviceBusy, gpa6.ReplyTimeout,
                waveform.ProfileChanged) as exc:
            try:
                self._disconnect()
            except (OSError, gpa6.ReplyTimeout) as cleanup:
                self.log(f"Apex 6 disconnected; cleanup unavailable: {cleanup}")
            message = str(exc)
            if message != self._problem:
                self.log(f"Apex 6 feedback unavailable: {message}; waiting for UUID {self.uid}")
                self._problem = message
            self.next_try = self.clock() + 1.0

    def _run(self):
        try:
            while not self.stop_event.is_set():
                self.step()
                self.stop_event.wait(0.002)
        except Exception as exc:
            self.failed = exc
            self.log(f"Apex 6 feedback stopped: {exc}")
        finally:
            try:
                self._disconnect()
            except Exception as exc:
                self.failed = self.failed or exc
                self.log(f"Apex 6 cleanup failed: {exc}")

    def start(self):
        if self.thread is not None:
            raise RuntimeError("feedback worker already started")
        self.thread = threading.Thread(target=self._run, name="apex6-feedback", daemon=True)
        self.thread.start()

    def close(self):
        if self.thread is None:
            self._disconnect()
        else:
            self.stop_event.set()
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("Apex 6 output worker did not finish cleanup")


def run(args, log, diagnostics=None):
    """Run the explicitly selected experimental route without legacy writes."""
    feedback = Feedback(None, audio=args.motors, gain=args.gain, log=log)
    pad = InputLink(args.device, log=log)
    # Diagnostics run on the main thread through their own bounded queue.
    meter_lock = threading.Lock()
    meter_blocks = deque(maxlen=8)

    def on_haptics(data, packets):
        feedback.on_haptics(data, packets)
        if diagnostics:
            with meter_lock:
                meter_blocks.append(data[-MAX_BYTES:])

    device = DualSense(on_output=feedback.on_output, on_haptics=on_haptics)
    server = usbip.Server(device)
    loopback = None
    try:
        if not usbip.module_loaded() and not usbip.load_module():
            raise RuntimeError("vhci-hcd is not loaded and modprobe could not load it")
        port = server.attach(serve=False)
        log(f"virtual DualSense attached on vhci port {port}")
        if not args.stay_root:
            uid = dsmode.drop_privileges()
            if uid is not None:
                log(f"dropped to uid {uid}")
        # Answer enumeration as soon as privilege has been dropped; device
        # discovery and motor setup can block for several command timeouts.
        server.start()
        connected, problem = pad.open()
        if not connected:
            raise RuntimeError(problem)
        feedback.uid = pad.uid
        feedback.set_connected(True)
        feedback.start()
        log(f"experimental Apex 6 input: {pad.name} ({pad.reader.path}); UUID {pad.uid}")
        log(f"grip audio {'enabled' if args.motors else 'disabled (use --motors)'}; "
            "synthesized HID rumble enabled; adaptive triggers and motion unavailable")
        ignore = ",".join((dsmode.IGNORE_VALUE, "0x37d7/0x2502"))
        log("games: set " + " ".join(f"{var}={ignore}" for var in dsmode.IGNORE_VARS))
        if args.verify:
            time.sleep(1.5)
            path, _ = evdev.find_device(vendor=0x054C, product=0x0CE6)
            if path:
                loopback = evdev.Reader(path)
            else:
                log("verify: virtual DualSense evdev node not found")
        state = relay.build_state(pad.reader, ds5.InputState())
        reports = frames = verified = 0
        next_report = 0.0
        next_status = time.monotonic() + 10
        while True:
            if server.failed:
                raise RuntimeError(f"USB/IP server failed: {server.failed}")
            if feedback.failed:
                raise RuntimeError(f"Apex 6 feedback failed: {feedback.failed}")
            if pad.poll():
                feedback.set_connected(pad.connected)
                if pad.connected:
                    relay.build_state(pad.reader, state)
                else:
                    relay.release(state)
            watch = pad.fds() + ([loopback.fileno()] if loopback else [])
            ready, _, _ = select.select(watch, [], [], 0.004)
            if pad.reader and pad.reader.fileno() in ready and pad.read_input():
                relay.build_state(pad.reader, state)
                frames += 1
            if loopback and loopback.fileno() in ready and loopback.read():
                verified += 1
            now = time.monotonic()
            if now >= next_report:
                next_report = now + 0.004
                reports += bool(server.complete_one(EP_HID_IN, state.pack()))
            if diagnostics:
                with meter_lock:
                    blocks = list(meter_blocks)
                    meter_blocks.clear()
                for data in blocks:
                    diagnostics.feed(data)
            if now >= next_status:
                next_status = now + 10
                log(f"reports={reports} evdev={frames} motion=0 out={feedback.output_reports} "
                    f"iso_urbs={device.haptic_urbs} pad={int(pad.connected)} drops={pad.drops} "
                    f"loopback={verified} grip_packets={feedback.sent} "
                    f"pcm_dropped={feedback.dropped_bytes} ignored_triggers={feedback.ignored_triggers}")
                if diagnostics:
                    log(diagnostics.summary())
    except KeyboardInterrupt:
        log("stopping")
    finally:
        try:
            feedback.close()
        finally:
            try:
                pad.close(restore=False)
                if loopback:
                    loopback.close()
            finally:
                server.detach()
    if feedback.failed:
        raise RuntimeError(f"Apex 6 feedback cleanup failed: {feedback.failed}")
