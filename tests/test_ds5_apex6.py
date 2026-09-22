#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Apex 6 USB feedback integration without hardware, root or vendor files."""
from collections import deque
from pathlib import Path
import math
import runpy
import struct
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flydigi import ds5, ds5_apex6 as bridge, evdev, gpa6, identity, waveform
from flydigi.ds5_usbip import DualSense, EP_HID_OUT, EP_ISO_OUT
from flydigi.mock.apex6 import FakeApex6


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def pcm(left=12000, right=-6000, frames=384):
    return struct.pack("<hhhh", 30000, -30000, left, right) * frames


def rumble(left, right, flags=3):
    report = bytearray(48)
    report[0:5] = bytes((ds5.DS_OUTPUT_REPORT_USB, flags, 0, right, left))
    return bytes(report)


def samples(pad, side):
    return pad.frames[-1][6 + side:29:3]


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.pad = FakeApex6()
        self.original = self.pad.runtime.copy()
        self.opener = Mock(return_value=self.pad)
        self.feedback = bridge.Feedback(self.pad.uid, clock=self.clock, open_pad=self.opener)
        self.feedback.set_connected(True)
        self.feedback.step()
        self.device = DualSense(on_output=self.feedback.on_output,
                                on_haptics=self.feedback.on_haptics)

    def tearDown(self):
        self.pad.present = True
        self.feedback.close()

    def audio(self, data, packets=None):
        self.device.isochronous(EP_ISO_OUT, data, packets or [(0, len(data), 0, 0)])

    def test_usb_audio_preserves_all_fresh_buffers_and_stereo(self):
        data = pcm()
        for offset in range(0, len(data), 48 * 8):
            self.audio(data[offset:offset + 48 * 8])
        expected = waveform.StereoResampler(channels=4).feed(data)
        self.feedback.step()
        self.assertEqual(samples(self.pad, 0), bytes(gpa6.quantize(l) for l, _ in expected))
        self.assertEqual(samples(self.pad, 1), bytes(gpa6.quantize(r) for _, r in expected))
        self.assertEqual(self.pad.frames[-1][5:29:3], bytes([128] * 8))
        self.assertEqual(self.pad.frames[-1][4], 0x98)
        self.assertEqual(self.feedback.dropped_bytes, 0)

    def test_packet_offsets_and_failed_packets(self):
        data = pcm(frames=192) + b"padding!" + pcm(frames=192)
        self.audio(data, [(0, 1536, 0, 0), (1544, 1536, 0, -1)])
        expected = waveform.StereoResampler(channels=4).feed(data[:1536] + bytes(1536))
        self.feedback.step()
        self.assertEqual(samples(self.pad, 0), bytes(gpa6.quantize(l) for l, _ in expected))

    def test_hid_rumble_without_audio_and_explicit_stop(self):
        self.device.transfer_out(EP_HID_OUT, rumble(255, 0))
        self.feedback.step()
        self.assertTrue(any(x != 128 for x in samples(self.pad, 0)))
        self.assertEqual(samples(self.pad, 1), bytes([128] * 8))
        self.clock.now += .008
        self.device.transfer_out(EP_HID_OUT, rumble(0, 0))
        self.feedback.step()
        self.assertEqual(samples(self.pad, 0), bytes([128] * 8))

    def test_audio_and_hid_rumble_are_independent_and_additive(self):
        self.device.transfer_out(EP_HID_OUT, rumble(0, 255))
        data = pcm(left=0, right=20000)
        self.audio(data)
        expected = waveform.StereoResampler(channels=4).feed(data)
        self.feedback.step()
        self.assertEqual(samples(self.pad, 0), bytes([128] * 8))
        self.assertEqual(samples(self.pad, 1), bytes(
            gpa6.quantize(r + .35 * math.sin(2 * math.pi * 160 * i / 1000))
            for i, (_, r) in enumerate(expected)))
        self.clock.now += .008
        self.feedback.step()  # audio underrun does not cancel HID rumble
        self.assertTrue(any(x != 128 for x in samples(self.pad, 1)))

    def test_audio_disable_keeps_hid(self):
        self.feedback.audio = False
        self.audio(pcm())
        self.device.transfer_out(EP_HID_OUT, rumble(100, 100))
        self.feedback.step()
        self.assertFalse(self.feedback.blocks)
        self.assertTrue(any(x != 128 for x in samples(self.pad, 0)))

    def test_triggers_and_invalid_reports_never_send_legacy_commands(self):
        self.device.transfer_out(EP_HID_OUT, rumble(0, 0, flags=12))
        self.device.transfer_out(EP_HID_OUT, b"\x02\xff")
        self.feedback.step()
        self.assertEqual(self.feedback.ignored_triggers, 2)
        self.assertTrue(all(cmd in (1, 4, 7, 0xa1, 0xa3, 0x53, 0x57)
                            for cmd, _ in self.pad.history))

    def test_oversized_and_stale_pcm_are_bounded(self):
        self.audio(pcm(frames=48000))
        self.assertEqual(self.feedback.queued_bytes, bridge.MAX_BYTES)
        self.assertEqual(self.feedback.dropped_bytes, (48000 - 1920) * 8)
        self.clock.now = .2
        self.feedback.step()
        self.assertEqual(self.feedback.queued_bytes, 0)
        self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))
        self.assertEqual(self.pad.frame_count, 1)

    def test_overflow_resets_filter_and_discards_older_session_samples(self):
        self.audio(pcm(frames=1920))
        self.feedback.step()
        self.assertTrue(self.feedback.session.queue)
        for _ in range(41):
            self.audio(pcm(left=0, right=0, frames=48))
        self.clock.now += .008
        self.feedback.step()
        self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))
        self.assertLessEqual(len(self.feedback.session.queue), 40)

    def test_invalid_audio_does_not_cancel_rumble(self):
        self.feedback.on_output(rumble(255, 0))
        for packets in ([(0, 100000, 0, 0)], [(-8, 8, 0, 0)],
                        [(0, 7, 0, 0)], [(0, 16, 0, 0), (8, 8, 0, 0)]):
            self.audio(pcm(), packets)
        self.assertEqual(self.feedback.invalid_urbs, 4)
        self.feedback.step()
        self.assertTrue(any(x != 128 for x in samples(self.pad, 0)))

    def test_callbacks_do_not_write_or_wait_for_hid_owner(self):
        before = list(self.pad.history)
        thread = threading.Thread(target=lambda: (self.audio(pcm()),
                                                    self.feedback.on_output(rumble(255, 0))))
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(list(self.pad.history), before)

    def test_disconnect_clears_audio_and_rumble_and_restores(self):
        self.audio(pcm())
        self.feedback.on_output(rumble(255, 255))
        self.feedback.set_connected(False)
        self.feedback.step()
        self.assertEqual(self.pad.runtime, self.original)
        self.assertFalse(self.feedback.accepting)
        self.audio(pcm())
        self.feedback.on_output(rumble(255, 255))
        self.feedback.set_connected(True)
        self.feedback.step()
        self.feedback.step()
        self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))
        self.assertEqual(self.opener.call_count, 2)
        self.opener.assert_called_with("uid:" + self.pad.uid)

    def test_profile_change_restores_new_profile_and_restarts(self):
        self.pad.maps[1][23:27] = bytes((0, 0, 1, 22))
        self.pad.change_profile(1)
        self.feedback.step()
        self.assertIsNone(self.feedback.session)
        self.assertEqual(self.pad.runtime, {16: (0, b""), 17: (1, b"\x16")})
        self.clock.now = 1
        self.feedback.step()
        self.assertTrue(self.feedback.session.active)

    def test_reply_timeout_reconnects_without_replaying_samples(self):
        self.audio(pcm())
        self.pad.faults[4] = deque(["drop", "drop"])
        self.feedback.step()
        self.assertIsNone(self.feedback.session)
        self.assertFalse(self.feedback.blocks)
        self.clock.now = 1
        self.feedback.step()
        self.feedback.step()
        self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))

    def test_disconnect_during_setup_and_cleanup_can_reconnect(self):
        self.feedback.set_connected(False)
        self.feedback.step()
        # Setup applies realtime mode, loses its ACK, then loses the cleanup
        # UUID reply too. This remains a recoverable communication timeout.
        self.pad.faults[0x53] = deque(["drop"])
        self.pad.faults[4] = deque([None, "drop"])
        self.feedback.set_connected(True)
        self.feedback.step()
        self.assertIsNone(self.feedback.session)
        self.assertIsNone(self.pad._grip_session)
        self.clock.now = 1
        self.feedback.step()
        self.assertTrue(self.feedback.accepting)

    def test_identify_timeout_during_reconnect_is_retried(self):
        self.feedback.set_connected(False)
        self.feedback.step()
        self.pad.history.clear()
        self.pad.faults[1] = deque(["drop"])
        self.feedback.set_connected(True)
        self.feedback.step()
        self.assertIsNone(self.feedback.session)
        self.assertIsNone(self.pad._grip_session)
        self.assertFalse(self.feedback.accepting)
        self.assertFalse(any(c in (0x53, 0x57) for c, _ in self.pad.history))
        self.clock.now = .5
        self.feedback.step()
        self.assertEqual(self.opener.call_count, 2)
        self.clock.now = 1
        self.feedback.step()
        self.assertTrue(self.feedback.accepting)
        self.assertEqual(self.feedback.session.uid, self.pad.uid)

    def test_malformed_identify_reply_is_fatal(self):
        self.feedback.set_connected(False)
        self.feedback.step()
        self.pad.history.clear()
        self.pad.faults[1] = deque(["malformed"])
        self.feedback.set_connected(True)
        with self.assertRaises(identity.WrongDevice):
            self.feedback.step()
        self.assertIsNone(self.pad._grip_session)
        self.assertFalse(any(c in (0x53, 0x57) for c, _ in self.pad.history))

    def test_lost_cleanup_ack_on_disconnect_can_reconnect(self):
        self.audio(pcm())
        self.feedback.on_output(rumble(255, 255))
        self.pad.faults[0x53] = deque(["drop"])
        self.feedback.set_connected(False)
        self.feedback.step()
        self.assertIsNone(self.feedback.session)
        self.assertIsNone(self.pad._grip_session)
        self.assertEqual(self.pad.runtime, self.original)
        self.feedback.set_connected(True)
        self.clock.now = 1
        self.feedback.step()
        self.assertTrue(self.feedback.accepting)
        self.feedback.step()
        self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))

    def test_lost_cleanup_ack_on_profile_change_can_reconnect(self):
        self.audio(pcm())
        self.feedback.on_output(rumble(255, 255))
        self.pad.maps[1][23:27] = bytes((0, 0, 1, 22))
        self.pad.change_profile(1)
        self.pad.faults[0x53] = deque(["drop"])
        self.feedback.step()
        self.assertIsNone(self.feedback.session)
        self.assertIsNone(self.pad._grip_session)
        self.assertEqual(self.pad.runtime, {16: (0, b""), 17: (1, b"\x16")})
        self.clock.now = 1
        self.feedback.step()
        self.assertTrue(self.feedback.accepting)
        self.assertEqual(self.feedback.session.slot, 1)
        self.feedback.step()
        self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))

    def test_replacement_uuid_is_refused_before_writes(self):
        self.feedback.set_connected(False)
        self.feedback.step()
        replacement = FakeApex6(uid="ff" * 16)
        self.opener.return_value = replacement
        self.feedback.set_connected(True)
        with self.assertRaises(identity.WrongDevice):
            self.feedback.step()
        self.assertFalse(any(c in (0x53, 0x57) for c, _ in replacement.history))

    def test_malformed_ack_is_fatal_and_restores(self):
        self.pad.faults[4] = deque(["malformed"])
        with self.assertRaises(gpa6.ProtocolError):
            self.feedback.step()
        self.assertEqual(self.pad.runtime, self.original)


class WiringTests(unittest.TestCase):
    def test_worker_owns_claim_and_cleanup(self):
        pad = FakeApex6()
        original = pad.runtime.copy()
        feedback = bridge.Feedback(pad.uid, open_pad=lambda _: pad)
        feedback.set_connected(True)
        try:
            feedback.start()
            deadline = time.monotonic() + 2
            while not feedback.accepting and time.monotonic() < deadline:
                time.sleep(.002)
            self.assertTrue(feedback.accepting)
            feedback.on_output(rumble(255, 0))
            while not pad.frames and time.monotonic() < deadline:
                time.sleep(.002)
            self.assertTrue(pad.frames)
        finally:
            feedback.close()
        self.assertIsNone(feedback.failed)
        self.assertIsNone(pad._grip_session)
        self.assertEqual(pad.runtime, original)

    def test_input_pins_uuid_and_usb_root_and_checks_axes(self):
        pad = FakeApex6()
        link = bridge.InputLink(pad.path)
        entry = {"path": pad.path, "uid": pad.uid}
        reader = Mock(abs={axis: None for axis in (
            evdev.ABS_X, evdev.ABS_Y, evdev.ABS_RX, evdev.ABS_RY,
            evdev.ABS_Z, evdev.ABS_RZ, evdev.ABS_HAT0X, evdev.ABS_HAT0Y)})
        with patch.object(bridge.registry, "find", return_value=entry), \
                patch.object(bridge.registry, "open_pad", return_value=pad), \
                patch.object(evdev, "usb_device_of_hidraw", return_value="/sys/selected"), \
                patch.object(evdev, "find_device", return_value=("/dev/input/event42", "Apex 6")) as find, \
                patch.object(evdev, "Reader", return_value=reader):
            self.assertIsNone(link._resolve())
            self.assertEqual(link.pad, "uid:" + pad.uid)
            self.assertIs(link._open_reader()[0], reader)
            find.assert_called_once_with(vendor=0x37d7, axes=True, usb_root="/sys/selected")
            reader.abs = {}
            self.assertIsNone(link._open_reader()[0])
            reader.close.assert_called_once()
            pad.uid = "ff" * 16
            self.assertIn("UUID changed", link._resolve())
        self.assertFalse(link.want_ctrl)
        self.assertFalse(link.want_motion)

    def test_input_requires_usb_topology_and_linear_grips(self):
        pad = FakeApex6()
        with patch.object(bridge.registry, "find", return_value={"path": pad.path, "uid": pad.uid}), \
                patch.object(bridge.registry, "open_pad", return_value=pad), \
                patch.object(evdev, "usb_device_of_hidraw", return_value=None):
            self.assertIn("no USB", bridge.InputLink(pad.path)._resolve())
            pad.features &= 0x7f
            self.assertIn("linear grips", bridge.InputLink(pad.path)._resolve())

    def test_input_reconnect_reresolves_uuid_after_reader_failure(self):
        required = (evdev.ABS_X, evdev.ABS_Y, evdev.ABS_RX, evdev.ABS_RY,
                    evdev.ABS_Z, evdev.ABS_RZ, evdev.ABS_HAT0X, evdev.ABS_HAT0Y)
        for failure in ("missing", "open", "axes"):
            with self.subTest(failure=failure):
                clock, ctrl = Clock(), FakeApex6()
                link = bridge.InputLink(ctrl.path, clock=clock)
                first = Mock(path="/dev/input/event1", abs=dict.fromkeys(required))
                incomplete = Mock(path="/dev/input/event2", abs={})
                recovered = Mock(path="/dev/input/event3", abs=dict.fromkeys(required))
                current = {"path": "/dev/hidraw1", "root": "/sys/usb/1-1"}
                scanned_roots = []

                def find_input(**kwargs):
                    scanned_roots.append(kwargs["usb_root"])
                    if kwargs["usb_root"] != current["root"]:
                        return None, None
                    if current["path"] == "/dev/hidraw2" and failure == "missing":
                        return None, None
                    number = current["path"][-1]
                    return f"/dev/input/event{number}", "Apex 6"

                def open_reader(path):
                    if path == incomplete.path and failure == "open":
                        raise OSError("input node disappeared")
                    return {r.path: r for r in (first, incomplete, recovered)}[path]

                with patch.object(bridge.registry, "find", side_effect=lambda *_a, **_k: {
                        "path": current["path"], "uid": ctrl.uid}) as find, \
                        patch.object(bridge.registry, "open_pad", return_value=ctrl), \
                        patch.object(evdev, "usb_device_of_hidraw", side_effect=lambda _: current["root"]), \
                        patch.object(evdev, "find_device", side_effect=find_input), \
                        patch.object(evdev, "Reader", side_effect=open_reader):
                    self.assertEqual(link.open(), (True, None))
                    self.assertIs(link.reader, first)
                    link.broken = True
                    self.assertTrue(link.poll())
                    self.assertFalse(link.connected)
                    first.close.assert_called_once()

                    current["path"] = "/dev/hidraw2"
                    clock.now = 1
                    self.assertFalse(link.poll())
                    self.assertIsNone(link.path)
                    self.assertIsNone(link.usb_root)
                    self.assertEqual(link.pad, "uid:" + ctrl.uid)
                    if failure == "axes":
                        incomplete.close.assert_called_once()

                    current.update(path="/dev/hidraw3", root="/sys/usb/2-1")
                    clock.now = 2
                    self.assertTrue(link.poll())
                    self.assertIs(link.reader, recovered)
                    self.assertEqual(link.path, current["path"])
                    self.assertEqual(link.usb_root, current["root"])
                    self.assertEqual(find.call_count, 3)
                    for call in find.call_args_list[1:]:
                        self.assertEqual(call.args[0], "uid:" + ctrl.uid)
                    self.assertEqual(scanned_roots,
                                     ["/sys/usb/1-1", "/sys/usb/1-1", "/sys/usb/2-1"])
                link.close()

    def test_cli_opt_in_validation_before_root_or_attach(self):
        main = runpy.run_path(str(ROOT / "tools/flydigi-ds5-usbip"))["main"]
        for argv in (["--experimental-apex6"],
                     ["--experimental-apex6", "--device", "test", "--dump"],
                     ["--experimental-apex6", "--device", "test", "--gain", "nan"]):
            with patch("sys.argv", ["flydigi-ds5-usbip"] + argv), \
                    patch("sys.stderr"), patch.object(bridge.usbip.Server, "attach") as attach:
                with self.assertRaises(SystemExit) as exc:
                    main()
                self.assertEqual(exc.exception.code, 2)
                attach.assert_not_called()

    def test_runtime_startup_failure_detaches_and_drops_privilege_before_open(self):
        calls = []
        args = SimpleNamespace(device="uid:test", motors=True, gain=1,
                               stay_root=False, verify=False)
        server = Mock(failed=None)
        server.attach.side_effect = lambda **_: calls.append("attach") or 0
        server.start.side_effect = lambda: calls.append("serve")
        pad = Mock()
        pad.open.side_effect = lambda: calls.append("open") or (False, "missing input")
        with patch.object(bridge.usbip, "Server", return_value=server), \
                patch.object(bridge.usbip, "module_loaded", return_value=True), \
                patch.object(bridge.dsmode, "drop_privileges", side_effect=lambda: calls.append("drop") or 1000), \
                patch.object(bridge, "InputLink", return_value=pad):
            with self.assertRaisesRegex(RuntimeError, "missing input"):
                bridge.run(args, lambda _: None)
        self.assertEqual(calls, ["attach", "drop", "serve", "open"])
        server.detach.assert_called_once()
        pad.close.assert_called_once_with(restore=False)
        server.start.assert_called_once()

    def test_runtime_keeps_virtual_pad_and_releases_input_across_disconnect(self):
        clock, ctrl = Clock(), FakeApex6()
        original = ctrl.runtime.copy()
        feedback = bridge.Feedback(ctrl.uid, clock=clock, open_pad=lambda _: ctrl)
        # Run the real session state machine from select using a fake clock.
        feedback.start = feedback.step
        reader = Mock(path="/dev/input/event42", axes={})
        reader.axis.side_effect = lambda code, lo, hi, default: default
        reader.pressed.side_effect = lambda code: code == evdev.BTN_SOUTH
        reader.fileno.return_value = 42
        pad = Mock(uid=ctrl.uid, connected=True, reader=reader, name="Apex 6", drops=0)
        pad.open.return_value = (True, None)
        pad.fds.side_effect = lambda: [42] if pad.reader else []
        poll_count = 0

        def poll():
            nonlocal poll_count
            poll_count += 1
            if poll_count == 2:
                pad.connected, pad.reader, pad.drops = False, None, 1
                return True
            if poll_count == 4:
                pad.connected, pad.reader = True, reader
                return True
            return False

        pad.poll.side_effect = poll
        server = Mock(failed=None)
        reports = []
        server.complete_one.side_effect = lambda ep, report: reports.append(report) or True

        def select_step(*_):
            if poll_count == 5:
                raise KeyboardInterrupt
            clock.now += .008
            feedback.on_output(rumble(255, 0))
            feedback.on_haptics(pcm(), [(0, 3072, 0, 0)])
            feedback.step()
            self.assertEqual(server.detach.call_count, 0)
            return [], [], []

        args = SimpleNamespace(device="uid:" + ctrl.uid, motors=True, gain=1,
                               stay_root=True, verify=False)
        with patch.object(bridge, "Feedback", return_value=feedback), \
                patch.object(bridge, "InputLink", return_value=pad), \
                patch.object(bridge.usbip, "Server", return_value=server), \
                patch.object(bridge.usbip, "module_loaded", return_value=True), \
                patch.object(bridge.select, "select", side_effect=select_step), \
                patch.object(bridge.time, "monotonic", clock):
            bridge.run(args, lambda _: None)
        self.assertEqual([bool(r[8] & ds5.CROSS) for r in reports], [True, False, False, True])
        self.assertTrue(all(r[1:5] == bytes([128] * 4) for r in reports))
        self.assertEqual(ctrl.runtime, original)
        self.assertIsNone(ctrl._grip_session)
        server.attach.assert_called_once_with(serve=False)
        server.detach.assert_called_once()


if __name__ == "__main__":
    unittest.main()
