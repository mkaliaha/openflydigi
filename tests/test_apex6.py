#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Hardware-free acceptance tests for the experimental GPA6 milestone."""
from collections import deque
import io
import json
import math
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flydigi import device, gpa6, identity, mapping, mock, registry, waveform
from flydigi.mock.apex6 import FakeApex6


def pcm(frames, channels=2):
    return b"".join(struct.pack("<" + "h" * channels, *frame) for frame in frames)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class ProtocolTests(unittest.TestCase):
    def test_native_encoder_fixtures(self):
        fixtures = json.loads((ROOT / "tests/fixtures/apex6-encoder.json").read_text())
        self.assertEqual(len(fixtures["packets"]), 48)
        for case in fixtures["quantizers"]:
            self.assertEqual(gpa6.quantize(case["input"]), case["output"])
        for case in fixtures["packets"]:
            self.assertEqual(gpa6.waveform(*case["channels"], enabled=case["enabled"],
                                          selector=case["selector"]).hex(), case["frame"])

    def test_nonfinite_samples(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                gpa6.quantize(value)

    def test_disabled_and_uneven_columns(self):
        result = gpa6.waveform([1] * 8, [-1, 1], [0.5], enabled=(False, True, True))
        self.assertEqual(result[5:11], bytes((128, 0, 191, 128, 255, 128)))
        self.assertEqual(result[11:29], bytes([128] * 18))

    def test_descriptor_framing(self):
        for rid, size in ((0, 32), (3, 32), (7, 63), (1, 64)):
            with self.subTest(rid=rid, size=size):
                descriptor = bytes.fromhex("06 a0 ff 09 01 a1 01")
                if rid:
                    descriptor += bytes((0x85, rid))
                descriptor += bytes((0x75, 8, 0x95, size, 0x91, 2, 0x81, 2, 0xc0))
                layout = gpa6.report_layout(descriptor)
                self.assertEqual(layout, gpa6.ReportLayout(rid, size, (rid,)))
                pad = FakeApex6(layout=layout)
                p = gpa6.Protocol(pad)
                self.assertEqual(p.uid(), pad.uid)
                self.assertEqual(len(layout.wrap(gpa6.waveform())), size + 1)
                with waveform.GripSession(pad, experimental=True):
                    pass

    def test_descriptor_rejects_unsafe_layouts(self):
        for descriptor in (b"", bytes.fromhex("06 a0"), bytes.fromhex("fe 00 00"),
                           bytes.fromhex("06 a0 ff 09 01 a1 01 75 08 95 1f 91 02 81 02 c0"),
                           bytes.fromhex("06 a0 ff 09 01 a1 01 75 08 95 20 85 03 91 02 85 04 91 02 81 02 c0")):
            with self.assertRaises(gpa6.ProtocolError):
                gpa6.report_layout(descriptor)

    def test_hidraw_read_keeps_numbered_64_byte_reports(self):
        ctrl = object.__new__(device.Controller)
        ctrl.fd, ctrl.path, ctrl.dialect = 123, "/dev/fake-apex6", "gpa6"
        ctrl._threads, ctrl._depth = threading.RLock(), 0
        ctrl.report_layout = gpa6.ReportLayout(3, 64, (4,))
        body = bytearray(64)
        body[:5] = bytes.fromhex("5a a5 04 01 00")
        body[5:21] = bytes(range(16))
        body[63] = sum(body[2:63]) & 255
        reply = b"\x04" + body
        with patch.object(ctrl, "_drain"), patch.object(ctrl, "_flock"), \
                patch("flydigi.device.fcntl.flock"), \
                patch("flydigi.device.os.write", return_value=65) as write, \
                patch("flydigi.device.os.read", return_value=reply) as read, \
                patch("flydigi.device.select.select", return_value=([123], [], [])):
            self.assertEqual(gpa6.Protocol(ctrl).uid(), bytes(range(16)).hex())
            read.assert_called_once_with(123, 65)
            self.assertEqual(write.call_args.args[1][:4], bytes.fromhex("03 5a a5 04"))
            with self.assertRaises(gpa6.ProtocolError):
                ctrl.send(device.build(0xa4))
            write.return_value = 12
            with self.assertRaisesRegex(OSError, "short HID"):
                gpa6.Protocol(ctrl).write(0x57)
            self.assertEqual(ctrl._depth, 0)

    def test_real_handle_thread_claim_timeout(self):
        ctrl = object.__new__(device.Controller)
        ctrl.fd, ctrl.path = 123, "/dev/fake-apex6"
        ctrl._threads, ctrl._depth = threading.RLock(), 0
        errors = []
        def contender():
            try:
                with ctrl.claim(timeout=0):
                    pass
            except device.DeviceBusy:
                errors.append(True)
        with patch.object(ctrl, "_flock"), patch("flydigi.device.fcntl.flock"):
            with ctrl.claim():
                thread = threading.Thread(target=contender)
                thread.start()
                thread.join(1)
                self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [True])

    def test_request_and_compact_error_ack(self):
        self.assertEqual(gpa6.frame(0x53, bytes.fromhex("01 12 02 40 00")),
                         bytes.fromhex("5a a5 53 07 01 12 02 40 00") + bytes(22) + b"\xaf")
        self.assertEqual(gpa6._payload(bytes.fromhex("5a a5 53 01 00 00 54"), 0x53, 1), b"\x00")
        with self.assertRaises(gpa6.ProtocolError):
            gpa6._payload(bytes.fromhex("5a a5 53 00 00 02 55"), 0x53, 1)

    def test_descriptor_globals_and_collection_order(self):
        # A non-vendor collection precedes the command interface; global push
        # and pop restore the output size after a differently sized input.
        descriptor = bytes.fromhex(
            "05 01 09 05 a1 01 85 01 75 08 95 04 81 02 c0 "
            "06 a0 ff 09 01 a1 01 85 03 75 08 95 20 "
            "a4 85 04 95 40 81 02 b4 91 02 c0")
        self.assertEqual(gpa6.report_layout(descriptor), gpa6.ReportLayout(3, 32, (4,)))

    def test_skus_and_runtime_features(self):
        for sku in (149, 150, 152):
            for flags in (0, 0x7f, 0x80, 0xff):
                pad = FakeApex6(sku, features=flags)
                self.assertEqual(identity.require(pad, "k6")["code"], "k6")
                self.assertEqual(gpa6.Protocol(pad).info()["haptic_grip"], bool(flags & 128))
                self.assertEqual(len(identity.read_uid(pad)), 32)
                self.assertTrue(identity.name_for(sku).startswith(gpa6.SKUS[sku]))
                with self.assertRaises(identity.WrongDevice):
                    identity.require(pad)
                with self.assertRaises(identity.WrongDevice):
                    identity.require_capability(pad, "adaptive_triggers")

    def test_legacy_writes_refused(self):
        pad = FakeApex6()
        with self.assertRaises(ValueError):
            mapping.apply_config(pad, 0)
        with self.assertRaises(ValueError):
            identity.write_nickname(pad, "unsafe")
        self.assertFalse(pad.history)
        self.assertNotIn("k6", identity.SUPPORTED)

    def test_format_versions_are_not_legacy_profile_versions(self):
        pad = FakeApex6(versions=(65535, 65535, 65535))
        with waveform.GripSession(pad, experimental=True) as session:
            self.assertEqual(session.versions, (65535,) * 3)
        self.assertFalse(any(c in (164, 165, 166, 167, 175) for c, _ in pad.history))

    def test_bad_ack_and_mapping_reads(self):
        for command in (1, 4, 7, 0xa1, 0xa3):
            for fault in ("drop", "malformed"):
                pad = FakeApex6()
                pad.faults[command] = deque([fault])
                with self.assertRaises((gpa6.ProtocolError, identity.WrongDevice)):
                    waveform.GripSession(pad, experimental=True).start()
                self.assertFalse(any(c == 0x53 for c, _ in pad.history))
                self.assertIsNone(pad._grip_session)

    def test_mapping_chunk_slot_and_length_checked(self):
        class BadMap(FakeApex6):
            def _reply(self, command, payload, v21=False):
                if command == 0xa3:
                    payload = bytearray(payload)
                    payload[self.bad_offset] ^= 1
                return super()._reply(command, payload, v21)
        for offset in range(8):
            pad = BadMap()
            pad.bad_offset = offset
            with self.assertRaises(gpa6.ProtocolError):
                waveform.GripSession(pad, experimental=True).start()
            self.assertFalse(any(c == 0x53 for c, _ in pad.history))


class AudioTests(unittest.TestCase):
    def test_chunk_boundaries_and_channel_isolation(self):
        data = pcm([(int(20000 * math.sin(i / 30)), 0) for i in range(4800)])
        reference = waveform.StereoResampler().feed(data)
        resampler = waveform.StereoResampler()
        result = []
        for i in range(0, len(data), 37):
            result.extend(resampler.feed(data[i:i + 37]))
        self.assertEqual(result, reference)
        self.assertEqual(len(result), 100)
        self.assertTrue(all(r == 0 for _, r in result))

    def test_dualsense_uses_only_haptic_channels(self):
        resampler = waveform.StereoResampler(channels=4)
        result = resampler.feed(pcm([(30000, -30000, 0, 16000)] * 4800, 4))
        self.assertTrue(all(l == 0 for l, _ in result))
        self.assertAlmostEqual(result[-1][1], 16000 / 32768, places=5)

    def test_filter_passband_and_alias_rejection(self):
        amplitudes = {}
        for hz in (100, 1000, 10000):
            data = pcm([(round(16000 * math.sin(2 * math.pi * hz * i / 48000)), 0)
                        for i in range(9600)])
            result = waveform.StereoResampler().feed(data)[30:]
            amplitudes[hz] = math.sqrt(sum(l * l for l, _ in result) / len(result))
        self.assertGreater(amplitudes[100], 0.33)
        self.assertLess(amplitudes[1000], 0.001)
        self.assertLess(amplitudes[10000], 0.001)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakeApex6()
        self.clock = Clock()
        self.original = self.pad.runtime.copy()

    def session(self, **kwargs):
        return waveform.GripSession(self.pad, experimental=True, clock=self.clock, **kwargs)

    def test_opt_in_and_haptic_capability(self):
        with self.assertRaises(identity.WrongDevice):
            waveform.GripSession(self.pad)
        self.pad.features = 0x7f
        with self.assertRaises(identity.WrongDevice):
            self.session().start()
        self.assertFalse(any(c == 0x53 for c, _ in self.pad.history))

    def test_unknown_motor_layout_fails_before_writes(self):
        for offset, value in ((0, 3), (23, 3), (25, 255)):
            pad = FakeApex6()
            pad.maps[0][offset] = value
            with self.assertRaises(gpa6.ProtocolError):
                waveform.GripSession(pad, experimental=True).start()
            self.assertFalse(any(c == 0x53 for c, _ in pad.history))

    def test_order_pacing_underrun_and_restore(self):
        with self.session() as session:
            session.feed(pcm([(10000, -5000)] * 768))
            queued = list(session.queue)
            self.assertTrue(session.tick())
            self.assertFalse(session.tick())
            self.clock.now = .008
            self.assertTrue(session.tick())
            frames = list(self.pad.frames)
            for i, (_, left, right) in enumerate(queued):
                frame = frames[i // 8]
                self.assertEqual(frame[5 + i % 8 * 3:8 + i % 8 * 3],
                                 bytes((128, gpa6.quantize(left), gpa6.quantize(right))))
            self.clock.now = .016
            session.tick()
            self.assertEqual(session.underruns, 8)
            self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))
        self.assertEqual(self.pad.runtime, self.original)
        self.assertEqual(self.pad.frames[-1][4], 0x80)
        session.stop()

    def test_latency_and_queue_are_bounded(self):
        with self.session() as session:
            session.feed(pcm([(10000, 0)] * 48000))
            self.assertLessEqual(len(session.queue), 40)
            self.assertGreater(session.dropped, 900)
            self.clock.now = .2
            session.tick()
            self.assertEqual(len(self.pad.frames), 1)
            self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))
            self.assertFalse(session.tick())

    def test_blocking_identity_checks_age_samples_and_advance_pacing(self):
        for explicit_now in (False, True):
            for delay in (.006, .03):
                with self.subTest(explicit_now=explicit_now, delay=delay):
                    self.clock.now = 0
                    with self.session() as session:
                        session.feed(pcm([(10000, -10000)] * 1920))
                        def delayed(query):
                            def read():
                                self.clock.now += delay
                                return query()
                            return read
                        with patch.object(session.protocol, "uid", delayed(session.protocol.uid)), \
                                patch.object(session.protocol, "slot", delayed(session.protocol.slot)):
                            self.assertTrue(session.tick(0 if explicit_now else None))
                        self.assertGreater(session.next_packet, self.clock.now)
                        self.assertLessEqual(session.next_packet - self.clock.now, waveform.PERIOD)
                        self.assertFalse(session.tick())
                        if delay == .03:
                            self.assertEqual(session.dropped, 40)
                            self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))
                        else:
                            self.assertEqual(session.dropped, 8)
                            self.assertTrue(any(x != 128 for x in self.pad.frames[-1][5:29]))

    def test_audio_gap_clears_filter_and_partial_pcm(self):
        with self.session() as session:
            session.feed(pcm([(10000, 0)] * 384) + b"\x01")
            self.clock.now = .2
            session.feed(pcm([(0, 0)] * 384))
            session.tick()
            self.assertEqual(self.pad.frames[-1][5:29], bytes([128] * 24))

    def test_stop_after_profile_change_uses_current_map(self):
        session = self.session().start()
        self.pad.maps[2][23:27] = bytes((0, 0, 1, 22))
        self.pad.change_profile(2)
        session.stop()
        self.assertEqual(self.pad.runtime, {16: (0, b""), 17: (1, b"\x16")})

    def test_partial_setup_rolls_back_even_after_lost_ack(self):
        for fault in ("error", "drop", "malformed"):
            with self.subTest(fault=fault):
                self.pad.faults[0x53] = deque([fault])
                with self.assertRaises(gpa6.ProtocolError):
                    self.session().start()
                self.assertEqual(self.pad.runtime, self.original)
                self.assertIsNone(self.pad._grip_session)

    def test_exception_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, "test exception"):
            with self.session():
                raise RuntimeError("test exception")
        self.assertEqual(self.pad.runtime, self.original)

    def test_cleanup_continues_after_failed_disable(self):
        session = self.session().start()
        self.pad.faults[0x53] = deque(["error"])
        with self.assertRaisesRegex(gpa6.ProtocolError, "cleanup incomplete"):
            session.stop()
        self.assertEqual(self.pad.runtime, self.original)
        self.assertIsNone(self.pad._grip_session)

    def test_cleanup_failure_is_reported(self):
        session = self.session().start()
        self.pad.faults[0x53] = deque([None, "error", None])
        with self.assertRaisesRegex(gpa6.ProtocolError, "cleanup incomplete"):
            session.stop()
        self.assertEqual(self.pad.runtime[17], self.original[17])
        self.assertIsNone(self.pad._grip_session)

    def test_profile_change_restores_new_profile(self):
        session = self.session().start()
        self.pad.maps[1][23:27] = bytes((1, 33, 0, 0))
        self.pad.change_profile(1)
        with self.assertRaises(waveform.ProfileChanged):
            session.tick()
        self.assertEqual(self.pad.runtime, {16: (1, b"!"), 17: (0, b"")})

    def test_disconnect_and_reconnect_use_fresh_state(self):
        session = self.session().start()
        uid = session.uid
        self.pad.present = False
        with self.assertRaises(OSError):
            session.tick()
        self.assertFalse(session.active)
        self.assertIsNone(self.pad._grip_session)
        self.pad.present = True
        self.pad.maps[0][24] = 42
        with self.session(expected_uid=uid) as resumed:
            resumed.tick()
            self.assertFalse(resumed.queue)
        self.assertEqual(self.pad.runtime[16], (1, b"*"))

    def test_replacement_uid_receives_no_writes(self):
        with self.assertRaises(identity.WrongDevice):
            self.session(expected_uid="ff" * 16).start()
        self.assertFalse(any(c == 0x53 for c, _ in self.pad.history))
        session = self.session().start()
        self.pad.uid = "ff" * 16
        self.pad.history.clear()
        with self.assertRaises(gpa6.ProtocolError):
            session.tick()
        self.assertEqual([c for c, _ in self.pad.history], [4, 4])
        self.assertIsNone(self.pad._grip_session)

    def test_output_contention(self):
        with self.session():
            with self.assertRaises(device.DeviceBusy):
                self.session().start()
            errors = []
            def other_thread():
                try:
                    self.session().start()
                except device.DeviceBusy:
                    errors.append(True)
            thread = threading.Thread(target=other_thread)
            thread.start()
            thread.join(1)
            self.assertEqual(errors, [True])

    def test_emulator_rejects_waveform_without_setup(self):
        with self.assertRaises(ValueError):
            self.pad.send(self.pad.report_layout.wrap(gpa6.waveform(left=[1] * 8)))


class IntegrationTests(unittest.TestCase):
    def test_cli_reconnects_after_unanswered_queries(self):
        for faults, reconnect in ((["drop"], True), (["drop", "drop"], True),
                                  (["drop"], False), (["malformed"], True)):
            with self.subTest(faults=faults, reconnect=reconnect):
                tool = runpy.run_path(str(ROOT / "tools/flydigi-apex6"))
                pad, clock, sessions = FakeApex6(), Clock(), []
                original = pad.runtime.copy()
                class Session(waveform.GripSession):
                    def __init__(self, ctrl, **kwargs):
                        super().__init__(ctrl, clock=clock, **kwargs)

                    def start(self):
                        super().start()
                        if not sessions:
                            pad.faults[4] = deque(faults)
                        sessions.append(self)
                        return self

                def sleep(seconds):
                    clock.now += seconds

                argv = ["flydigi-apex6", "--device", pad.path, "--experimental"]
                if reconnect:
                    argv.append("--reconnect")
                argv.extend(["tone", "--duration", "0.05"])
                stderr = io.StringIO()
                with patch("sys.argv", argv), patch("sys.stderr", stderr), \
                        patch("flydigi.registry.open_pad", return_value=pad), \
                        patch("flydigi.registry.find", return_value={"path": pad.path, "uid": pad.uid}) as find, \
                        patch("flydigi.waveform.GripSession", Session), \
                        patch("time.monotonic", clock), patch("time.sleep", sleep):
                    if reconnect and faults[0] == "drop":
                        self.assertEqual(tool["main"](), 0)
                        self.assertEqual(len(sessions), 2)
                        self.assertEqual(sessions[-1].uid, pad.uid)
                        self.assertGreater(sessions[-1].sent, 0)
                        find.assert_called_once_with("uid:" + pad.uid, registry.KIND_PAD, deep=True)
                        self.assertIn("waiting for UUID " + pad.uid, stderr.getvalue())
                        if len(faults) == 2:
                            self.assertIn("cleanup failed", stderr.getvalue())
                    else:
                        with self.assertRaises(gpa6.ProtocolError):
                            tool["main"]()
                        find.assert_not_called()
                        self.assertEqual(len(sessions), 1)
                self.assertEqual(pad.runtime, original)
                self.assertIsNone(pad._grip_session)

    def test_selection_rechecks_uuid_after_node_reuse(self):
        tool = runpy.run_path(str(ROOT / "tools/flydigi-apex6"))
        pad = FakeApex6(uid="bb" * 16)
        entry = {"path": pad.path, "uid": "aa" * 16}
        with patch("flydigi.registry.find", return_value=entry), \
                patch("flydigi.registry.open_pad", return_value=pad):
            with self.assertRaisesRegex(identity.WrongDevice, "UUID changed"):
                tool["open_selected"]("uid:" + "aa" * 16)
        self.assertFalse(any(c == 0x53 for c, _ in pad.history))

    def test_mixed_bus_discovery_and_legacy_filters(self):
        with patch.dict(os.environ, FLYDIGI_MOCK_BUS="pad:k5,pad:k6,pad:f5"):
            mock.reset()
            try:
                with patch("flydigi.device._real_nodes", return_value=[]):
                    entries = registry.list_pads(deep=True)
                    self.assertEqual([e["code"] for e in entries], ["k5", "k6", "f5"])
                    self.assertEqual([e["code"] for e in registry.drivable_pads()], ["k5", "f5"])
                    entry = entries[1]
                    self.assertEqual(len(entry["uid"]), 32)
                    with registry.open_pad("uid:" + entry["uid"]) as ctrl:
                        self.assertEqual(ctrl.dialect, "gpa6")
            finally:
                mock.reset()

    def test_cli_info_opt_in_and_tone(self):
        env = dict(os.environ, FLYDIGI_MOCK_BUS="pad:k6")
        cmd = [sys.executable, str(ROOT / "tools/flydigi-apex6"), "--device", "mock:pad0"]
        info = subprocess.run(cmd + ["info"], env=env, capture_output=True, text=True)
        self.assertEqual(info.returncode, 0, info.stderr)
        self.assertEqual(len(json.loads(info.stdout)["uid"]), 32)
        refused = subprocess.run(cmd + ["tone"], env=env, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        tone = subprocess.run(cmd + ["--experimental", "tone", "--duration", "0.03"],
                              env=env, capture_output=True, text=True)
        self.assertEqual(tone.returncode, 0, tone.stderr)
        self.assertIn("[MOCK]", tone.stderr)

    def test_cli_wav_and_default_selection_on_isolated_bus(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "haptics.wav"
            with wave.open(str(source), "wb") as out:
                out.setparams((4, 2, 48000, 0, "NONE", "not compressed"))
                out.writeframes(pcm([(0, 0, 5000, -5000)] * 1440, 4))
            bus = Path(directory) / "bus.json"
            bus.write_text(json.dumps({"hide_real": True, "devices": [
                {"kind": "pad", "code": "k5"}, {"kind": "pad", "code": "k6"}]}))
            result = subprocess.run([sys.executable, str(ROOT / "tools/flydigi-apex6"),
                                     "--experimental", "play", str(source)],
                                    env=dict(os.environ, FLYDIGI_MOCK_BUS=str(bus)),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Apex 6", result.stderr)


if __name__ == "__main__":
    unittest.main()
