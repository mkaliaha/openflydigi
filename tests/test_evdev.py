#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Input queue overruns must not retain missed trigger/button releases."""
import errno
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flydigi import evdev


def event(kind, code, value=0):
    return struct.pack(evdev._EVENT_FMT, 0, 0, kind, code, value)


class ReaderTests(unittest.TestCase):
    def reader(self):
        reader = evdev.Reader.__new__(evdev.Reader)
        reader.fd = 12
        reader.abs = {code: evdev.AbsInfo(struct.pack('6i', 255, 0, 255, 0, 0, 0))
                      for code in (evdev.ABS_Z, evdev.ABS_RZ)}
        reader.axes = {evdev.ABS_Z: 255, evdev.ABS_RZ: 255}
        reader.keys = {evdev.BTN_SOUTH: 1}
        reader._dropped = False
        return reader

    def snapshot(self, fd, request, buf, mutate):
        self.assertEqual(fd, 12)
        if len(buf) == 24:
            buf[:] = struct.pack('6i', 0, 0, 255, 0, 0, 0)
        else:
            self.assertEqual(request & 255, 0x18)
            buf[evdev.BTN_EAST // 8] |= 1 << (evdev.BTN_EAST % 8)

    def test_ordinary_frames_do_not_query_or_discard_input(self):
        reader = self.reader()
        data = event(evdev.EV_ABS, evdev.ABS_Z, 0) + event(evdev.EV_SYN, evdev.SYN_REPORT)
        with patch.object(evdev.os, 'read', return_value=data), \
             patch.object(evdev.fcntl, 'ioctl') as ioctl:
            self.assertTrue(reader.read())
            self.assertEqual(reader.axis(evdev.ABS_Z), 0)
            self.assertEqual(reader.axis(evdev.ABS_RZ), 1)
            ioctl.assert_not_called()

    def test_overrun_recovers_both_releases_and_buttons_from_kernel(self):
        reader = self.reader()
        stale = event(evdev.EV_ABS, evdev.ABS_Z, 255)
        data = (event(evdev.EV_SYN, evdev.SYN_DROPPED) + stale +
                event(evdev.EV_SYN, evdev.SYN_REPORT) + stale)
        with patch.object(evdev.os, 'read', side_effect=[data, stale]) as read, \
             patch.object(evdev.select, 'select', side_effect=[([reader], [], []), ([], [], [])]), \
             patch.object(evdev.fcntl, 'ioctl', side_effect=self.snapshot):
            self.assertTrue(reader.read())
            self.assertEqual(read.call_count, 2)
        self.assertEqual([reader.axis(c) for c in (evdev.ABS_Z, evdev.ABS_RZ)], [0, 0])
        self.assertFalse(reader.pressed(evdev.BTN_SOUTH))
        self.assertTrue(reader.pressed(evdev.BTN_EAST))
        self.assertFalse(reader._dropped)

    def test_overrun_ignores_events_until_report_in_later_read(self):
        reader = self.reader()
        data = [event(evdev.EV_SYN, evdev.SYN_DROPPED),
                event(evdev.EV_ABS, evdev.ABS_Z, 50),
                event(evdev.EV_SYN, evdev.SYN_REPORT)]
        with patch.object(evdev.os, 'read', side_effect=data), \
             patch.object(evdev.select, 'select', return_value=([], [], [])), \
             patch.object(evdev.fcntl, 'ioctl', side_effect=self.snapshot) as ioctl:
            self.assertFalse(reader.read())
            self.assertFalse(reader.read())
            self.assertEqual(reader.axes[evdev.ABS_Z], 255)
            ioctl.assert_not_called()
            self.assertTrue(reader.read())
        self.assertEqual(reader.axes[evdev.ABS_Z], 0)

    def test_resync_error_is_reported_instead_of_using_stale_axes(self):
        reader = self.reader()
        data = event(evdev.EV_SYN, evdev.SYN_DROPPED) + event(evdev.EV_SYN, evdev.SYN_REPORT)
        with patch.object(evdev.os, 'read', return_value=data), \
             patch.object(evdev.select, 'select', return_value=([], [], [])), \
             patch.object(evdev.fcntl, 'ioctl', side_effect=OSError(errno.ENODEV, 'gone')):
            with self.assertRaises(OSError):
                reader.read()

    def test_eof_is_reported_instead_of_retaining_held_triggers(self):
        reader = self.reader()
        with patch.object(evdev.os, 'read', return_value=b''):
            with self.assertRaises(OSError) as caught:
                reader.read()
        self.assertEqual(caught.exception.errno, errno.ENODEV)


if __name__ == '__main__':
    unittest.main()
