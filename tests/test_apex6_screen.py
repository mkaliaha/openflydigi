#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Screen protocol, selection and interrupted-upload checks without hardware."""
from collections import deque
from contextlib import ExitStack
import hashlib
import io
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flydigi import gpa6, gpa6_screen, identity, screen, screen_ota
from flydigi.mock.apex6 import FakeApex6
from flydigi.mock.pad import FakePad


def frames(count):
    return [b"\x04\x80\x02\x0a" + bytes((i + frame * 17) % 256 for i in range(25600))
            for frame in range(count)]


class ScreenChip:
    """Independent bootloader model: exact lengths, erase-before-write, echoes."""
    def __init__(self, base=0x120000):
        self.base = base
        self.packets, self.replies = [], deque()
        self.erased, self.memory, self.reset = set(), bytearray(), None
        self.fault = lambda packet, reply: [reply]

    def __enter__(self): return self
    def __exit__(self, *exc): pass

    def write(self, packet):
        packet = bytes(packet)
        self.packets.append(packet)
        op, length = struct.unpack("<BH", packet[:3])
        body = packet[3:]
        if op == 11:
            assert length == 4 and len(body) == 4
            self.config = tuple(body)
            self.memory = bytearray(body[1] * 25604)
            reply = b"\0\x0b\x04\0" + struct.pack("<I", self.base)
        elif op == 10:
            assert length == 6 and not body
            reply = b"\0\x0a\x04\0\x03\x02\x00\x01"
        elif op == 3:
            assert length == 6 and len(body) == 4
            address, = struct.unpack("<I", body)
            assert address >= self.base and (address - self.base) % 4096 == 0
            self.erased.add(address)
            offset = address - self.base
            self.memory[offset: min(offset + 4096, len(self.memory))] = b"\xff" * min(4096, len(self.memory) - offset)
            reply = b"\0\x03\x04\0" + body
        elif op == 5:
            address, size = struct.unpack("<IH", body[:6])
            assert length == len(packet) == size + 9 and 0 < size <= 55
            assert len(body[6:]) == size
            offset = address - self.base
            assert 0 <= offset < offset + size <= len(self.memory)
            for at in range(address, address + size):
                assert self.base + ((at - self.base) // 4096) * 4096 in self.erased
            self.memory[offset:offset + size] = body[6:]
            reply = b"\0\x05\x06\0" + body[:6]
        elif op == 12:
            assert length == 8 and len(body) == 8
            self.reset = struct.unpack("<II", body)
            reply = b"\0\x0c\0\0"
        else:
            raise AssertionError(f"unexpected opcode {op}")
        self.replies.extend(self.fault(packet, reply))

    def read_reply(self, timeout=None):
        return self.replies.popleft() if self.replies else None


class WiredPad(FakeApex6):
    def __init__(self, **kwargs):
        super().__init__(features=0x9f, **kwargs)
        self.connection_mode = 0

    def send(self, report, **kwargs):
        if report[3] == 31:
            body = report[1:]
            assert len(body) == 32 and body[:5] == b"\x5a\xa5\x1f\x03\x04"
            assert body[31] == sum(body[2:31]) & 255
            self.history.append((31, b"\x04"))
            return []
        replies = super().send(report, **kwargs)
        if report[3] == 1:
            reply = bytearray(replies[0])
            reply[6] = self.connection_mode
            reply[31] = sum(reply[2:31]) & 255
            return [bytes(reply)]
        return replies


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.chip, self.frames = ScreenChip(), frames(1)
        self.addCleanup(patch.stopall)
        patch("flydigi.screen_ota.time.sleep").start()

    def upload(self, **kwargs):
        return screen_ota.upload(self.chip, self.frames, strict=True, **kwargs)

    def test_independent_vendor_packet_sequences(self):
        fixture = json.loads((ROOT / "tests/fixtures/apex6-screen.json").read_text())
        for case in fixture["cases"]:
            with self.subTest(count=case["count"]):
                chip = ScreenChip()
                data = frames(case["count"])
                screen_ota.upload(chip, data, case["interval_ms"], case["restore_default"], strict=True)
                digest = hashlib.sha256()
                for packet in chip.packets:
                    digest.update(struct.pack("<i", len(packet)))
                    digest.update(packet)
                self.assertEqual(digest.hexdigest(), case["packets_sha256"])
                self.assertEqual(len(chip.packets), case["packet_count"])
                self.assertEqual(chip.memory, b"".join(data))
                self.assertEqual(chip.packets[-2].hex(), case["last_write"])
                self.assertEqual(chip.packets[-1].hex(), case["reset"])

    def test_short_tail_and_round_half_up(self):
        self.upload(interval_ms=25)
        self.assertEqual(self.chip.config, (2, 1, 3, 0))
        packet = self.chip.packets[-2]
        self.assertEqual((len(packet), struct.unpack("<H", packet[1:3])[0],
                          struct.unpack("<H", packet[7:9])[0]), (38, 38, 29))

    def test_preflight_rejects_bad_resources_without_serial_writes(self):
        for data, interval in [([],100), (self.frames*151,100), ([b"bad"],100),
                ([b"\x05"+self.frames[0][1:]],100), (self.frames,0),
                (self.frames,float("nan")),(self.frames,2501), (self.frames,True)]:
            with self.subTest(interval=interval, count=len(data)):
                with self.assertRaises(screen_ota.OtaError):
                    screen_ota.upload(self.chip, data, interval, strict=True)
        self.assertEqual(self.chip.packets, [])

    def test_error_result_stops_before_next_command(self):
        for target in (11,10,3,5,12):
            self.chip = ScreenChip()
            self.chip.fault = lambda p,r: [bytes([7,p[0]])] if p[0] == target else [r]
            with self.assertRaisesRegex(screen_ota.OtaError, "device result 7"):
                self.upload()
            self.assertEqual(self.chip.packets[-1][0], target)

    def test_lost_write_ack_retries_identical_packet(self):
        lost = []
        def fault(packet, reply):
            if packet[0] == 5 and not lost:
                lost.append(packet)
                return []
            return [reply]
        self.chip.fault = fault
        self.upload()
        self.assertEqual(self.chip.packets.count(lost[0]), 2)
        self.assertEqual(self.chip.memory, self.frames[0])

    def test_silence_is_bounded_and_never_sends_reset(self):
        self.chip.fault = lambda p,r: [] if p[0] == 5 else [r]
        with self.assertRaisesRegex(screen_ota.OtaError, "4 attempts"):
            self.upload()
        self.assertEqual(sum(p[0] == 5 for p in self.chip.packets), 4)
        self.assertIsNone(self.chip.reset)

    def test_wrong_address_or_length_ack_cannot_advance(self):
        for target, index in [(3,4),(5,4),(5,8)]:
            self.chip = ScreenChip()
            def fault(packet, reply):
                if packet[0] == target:
                    reply = bytearray(reply); reply[index] ^= 1
                return [bytes(reply)]
            self.chip.fault = fault
            with self.assertRaisesRegex(screen_ota.OtaError,"4 attempts"):
                self.upload()
            self.assertIsNone(self.chip.reset)
            self.assertEqual(self.chip.packets[-1][0],target)

    def test_split_and_coalesced_stale_replies(self):
        def fault(packet, reply):
            stale = b"\0\x0a\x08\0\0\0\0\0" if packet[0] != 10 else b"\0\x0c"
            return [stale + reply[:1], reply[1:3], reply[3:]] if len(reply)>2 else [stale+reply]
        self.chip.fault = fault
        self.upload()
        self.assertEqual(self.chip.memory,self.frames[0])

    def test_reset_timeout_is_uncertain_and_not_retried(self):
        self.chip.fault = lambda p,r: [] if p[0] == 12 else [r]
        with self.assertRaisesRegex(screen_ota.OtaError,"completion is uncertain"):
            self.upload()
        self.assertEqual(sum(p[0] == 12 for p in self.chip.packets),1)

    def test_invalid_base_never_erases(self):
        for base in (0x120001,0xfffff000):
            self.chip = ScreenChip(base)
            with self.assertRaises(screen_ota.OtaError): self.upload()
            self.assertEqual([p[0] for p in self.chip.packets],[11])

    def test_interrupted_write_and_explicit_complete_restart(self):
        original = self.chip.write
        def fail(packet):
            original(packet)
            if packet[0] == 5: raise OSError("disconnected after write")
        with patch.object(self.chip,"write",side_effect=fail):
            with self.assertRaises(OSError): self.upload()
        self.assertIsNone(self.chip.reset)
        self.upload()
        self.assertEqual(self.chip.memory,self.frames[0])
        self.assertIsNotNone(self.chip.reset)

    def test_legacy_frame_limit_precedes_upgrade_entry(self):
        with patch.object(screen_ota,"enter_upgrade_mode") as enter:
            with self.assertRaises(screen_ota.OtaError):
                screen_ota.upload_picture(object(),self.frames*256)
            enter.assert_not_called()


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.pad, self.chip = WiredPad(), ScreenChip()
        self.data = frames(1)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("flydigi.screen_ota.time.sleep"))
        self.root = self.stack.enter_context(patch("flydigi.gpa6_screen.evdev.usb_device_of_hidraw",return_value="/sys/usb/3-3"))
        self.ports = self.stack.enter_context(patch("flydigi.screen_ota.find_ports",return_value=[]))
        self.wait = self.stack.enter_context(patch("flydigi.gpa6_screen._wait_for_port",return_value="/dev/ttyACM8"))
        self.link = self.stack.enter_context(patch("flydigi.screen_ota.OtaLink",return_value=self.chip))

    def upload(self, **kwargs):
        return gpa6_screen.upload_picture(self.pad,self.data,expected_uid=self.pad.uid,**kwargs)

    def assert_no_switch(self):
        self.assertNotIn(31,[cmd for cmd,_ in self.pad.history])
        self.link.assert_not_called()

    def test_wired_uuid_bound_upload_and_native_entry(self):
        recorded=[]; original=self.pad.send
        def send(packet,**kwargs):
            if packet[3]==31: recorded.append(bytes(packet))
            return original(packet,**kwargs)
        with patch.object(self.pad,"send",side_effect=send): self.upload()
        native=bytes.fromhex("5aa51f0304000000000000000000000000000000000000000000000000000026")
        self.assertEqual(recorded,[b"\0"+native])
        self.assertEqual(self.chip.memory,self.data[0])
        self.wait.assert_called_once_with("/sys/usb/3-3")
        self.link.assert_called_once_with("/dev/ttyACM8",usb_root="/sys/usb/3-3")
        self.assertTrue(all(cmd in (1,4,31) for cmd,_ in self.pad.history))

    def test_receiver_or_unknown_connection_is_refused(self):
        for mode in (1,2):
            self.pad.connection_mode=mode
            with self.assertRaisesRegex(screen_ota.OtaError,"wired"): self.upload()
        self.assert_no_switch()

    def test_missing_screen_capability_and_legacy_pad_are_refused(self):
        self.pad.features &= ~8
        with self.assertRaises(identity.WrongDevice): self.upload()
        self.assert_no_switch()
        with self.assertRaises(gpa6.ProtocolError): gpa6_screen.upload_picture(FakePad(),self.data)

    def test_changed_uuid_is_refused(self):
        with self.assertRaises(identity.WrongDevice):
            gpa6_screen.upload_picture(self.pad,self.data,expected_uid="wrong")
        self.assert_no_switch()
        with patch.object(gpa6.Protocol,"uid",side_effect=[self.pad.uid,"changed"]):
            with self.assertRaises(identity.WrongDevice): self.upload()
        self.assert_no_switch()

    def test_unknown_usb_root_and_existing_bootloader_are_refused(self):
        self.root.return_value=None
        with self.assertRaises(screen_ota.OtaError): self.upload()
        self.root.return_value="/sys/usb/3-3"; self.ports.return_value=["/dev/ttyACM8"]
        with self.assertRaises(screen_ota.OtaError): self.upload()
        self.assert_no_switch()

    def test_bad_content_rejected_before_identity_or_switch(self):
        self.data=[b"bad"]
        with self.assertRaises(screen_ota.OtaError): self.upload()
        self.assertEqual(list(self.pad.history),[])
        self.assert_no_switch()

    def test_bootloader_timeout_never_opens_unrelated_port(self):
        self.wait.side_effect=screen_ota.OtaError("timeout")
        with self.assertRaises(screen_ota.OtaError): self.upload()
        self.assertEqual(sum(cmd==31 for cmd,_ in self.pad.history),1)
        self.link.assert_not_called()

    def test_recovery_requires_explicit_matching_bootloader(self):
        with self.assertRaises(screen_ota.OtaError):
            gpa6_screen.upload_picture(None,self.data,port="/dev/ttyACM8")
        self.ports.return_value=["/dev/ttyACM8"]
        with patch("flydigi.gpa6_screen.evdev.usb_device",return_value="/sys/usb/3-3"):
            gpa6_screen.upload_picture(None,self.data,port="/dev/ttyACM8")
        self.assertEqual(self.chip.memory,self.data[0])
        self.assertEqual(list(self.pad.history),[])
        with self.assertRaises(ValueError): self.upload(port="/dev/ttyACM8")


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader=importlib.machinery.SourceFileLoader("screen_cli",str(ROOT/"tools/flydigi-screen"))
        cls.cli=importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
        loader.exec_module(cls.cli)

    def run_cli(self,*args):
        with patch.object(sys,"argv",["flydigi-screen",*args]), \
                patch("sys.stdout",new_callable=io.StringIO), patch("sys.stderr",new_callable=io.StringIO):
            return self.cli.main()

    def test_send_routes_to_apex6_and_never_legacy_settings(self):
        pad=WiredPad()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"image.bin"; path.write_bytes(frames(1)[0])
            with patch.object(self.cli.registry,"open_pad",return_value=pad), \
                    patch.object(self.cli.gpa6_screen,"upload_picture",return_value=0x120000) as upload:
                self.assertEqual(self.run_cli("send",str(path),"--experimental-apex6","--device","/dev/hidraw9"),0)
                self.assertEqual(upload.call_args.kwargs["expected_uid"],pad.uid)
                self.assertEqual(upload.call_args.args[1],tuple(frames(1)))

    def test_missing_optin_or_invalid_route_never_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"image.bin"; path.write_bytes(frames(1)[0])
            with patch.object(self.cli.registry,"open_pad",return_value=WiredPad()), \
                    patch.object(self.cli.screen_ota,"find_port",return_value=None), \
                    patch.object(self.cli.gpa6_screen,"upload_picture") as upload:
                with self.assertRaises(SystemExit): self.run_cli("send",str(path),"--device","/dev/hidraw9")
                for options in [[],["--device","/dev/hidraw9","--via","hid"],
                                ["--device","/dev/hidraw9","--i-know"],
                                ["--device","/dev/hidraw9","--port","/dev/ttyACM8"]]:
                    self.assertEqual(self.run_cli("send",str(path),"--experimental-apex6",*options),1)
                upload.assert_not_called()

    def test_zero_interval_is_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"image.bin"; path.write_bytes(frames(1)[0])
            with patch.object(self.cli.registry,"open_pad") as opened:
                self.assertEqual(self.run_cli("send",str(path),"--experimental-apex6",
                    "--device","/dev/hidraw9","--interval","0"),1)
                opened.assert_not_called()


class SerialLinkTests(unittest.TestCase):
    def test_chunk_read_returns_without_a_quiet_period(self):
        link = object.__new__(screen_ota.OtaLink); link.fd = 123
        with patch("flydigi.screen_ota.select.select", return_value=([123], [], [])) as select, \
                patch("flydigi.screen_ota.os.read", return_value=b"\0\x0c"):
            self.assertEqual(link.read_chunk(), b"\0\x0c")
            self.assertEqual(select.call_count, 1)

    def test_chunk_read_disconnect_and_timeout(self):
        link = object.__new__(screen_ota.OtaLink); link.fd = 123
        with patch("flydigi.screen_ota.select.select", return_value=([], [], [])):
            self.assertIsNone(link.read_chunk())
        with patch("flydigi.screen_ota.select.select", return_value=([123], [], [])), \
                patch("flydigi.screen_ota.os.read", return_value=b""):
            with self.assertRaisesRegex(screen_ota.OtaError, "disconnected"):
                link.read_chunk()

    def test_partial_write_and_would_block_are_completed(self):
        link = object.__new__(screen_ota.OtaLink); link.fd = 123
        with patch("flydigi.screen_ota.select.select",return_value=([],[123],[])), \
                patch("flydigi.screen_ota.os.write",side_effect=[2,BlockingIOError(),3]) as write:
            link.write(b"abcde")
        self.assertEqual([bytes(c.args[1]) for c in write.call_args_list],[b"abcde",b"cde",b"cde"])

    def test_open_failure_closes_descriptor_without_writing(self):
        with patch("flydigi.screen_ota.os.open",return_value=123), \
                patch("fcntl.flock",side_effect=BlockingIOError("busy")), \
                patch("flydigi.screen_ota.os.close") as close:
            with self.assertRaises(BlockingIOError): screen_ota.OtaLink("/dev/ttyACM8")
            close.assert_called_once_with(123)

    def test_changed_usb_root_is_rejected_after_open(self):
        with patch("flydigi.screen_ota.os.open",return_value=123), \
                patch("fcntl.flock"), patch("flydigi.screen_ota.os.fstat") as stat, \
                patch("flydigi.evdev.usb_device",return_value="/sys/usb/other"), \
                patch("flydigi.screen_ota.os.close") as close:
            stat.return_value.st_rdev=os.makedev(166,8)
            with self.assertRaises(screen_ota.OtaError):
                screen_ota.OtaLink("/dev/ttyACM8",usb_root="/sys/usb/3-3")
            close.assert_called_once_with(123)

    def test_port_discovery_walks_usb_ancestors_and_filters_selected_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots=[]; tty_paths=[]
            for index in range(2):
                root=Path(tmp)/f"3-{index+3}"; root.mkdir()
                (root/"idVendor").write_text("ffaa"); (root/"idProduct").write_text("5555")
                interface=root/f"3-{index+3}:1.0"; interface.mkdir()
                tty=Path(tmp)/f"ttyACM{index}"; tty.mkdir(); (tty/"device").symlink_to(interface)
                roots.append(str(root)); tty_paths.append(str(tty))
            with patch("flydigi.screen_ota.glob.glob",side_effect=[tty_paths,[]]):
                self.assertEqual(screen_ota.find_ports(usb_root=roots[1]),["/dev/ttyACM1"])

    def test_multiple_bootloaders_on_selected_port_are_ambiguous(self):
        with patch("flydigi.screen_ota.find_ports",return_value=["/dev/ttyACM0","/dev/ttyACM1"]):
            with self.assertRaisesRegex(screen_ota.OtaError,"multiple"):
                gpa6_screen._wait_for_port("/sys/usb/3-3")


if __name__=="__main__": unittest.main()
