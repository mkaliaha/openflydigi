# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Small Linux FF_RUMBLE output adapter, using the native input.h ABI."""
import ctypes as C
import fcntl
import os
import struct


class _Periodic(C.Structure):
    _fields_ = [("waveform", C.c_uint16), ("period", C.c_uint16),
                ("magnitude", C.c_int16), ("offset", C.c_int16),
                ("phase", C.c_uint16), ("envelope", C.c_uint16 * 4),
                ("custom_len", C.c_uint32), ("custom_data", C.POINTER(C.c_int16))]


class _Rumble(C.Structure):
    _fields_ = [("strong", C.c_uint16), ("weak", C.c_uint16)]


class _Union(C.Union):
    # Periodic is the largest/alignment-determining union member on Linux.
    _fields_ = [("periodic", _Periodic), ("rumble", _Rumble)]


class _Effect(C.Structure):
    _fields_ = [("type", C.c_uint16), ("id", C.c_int16), ("direction", C.c_uint16),
                ("trigger", C.c_uint16 * 2), ("replay", C.c_uint16 * 2), ("u", _Union)]


class RumbleOutput:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.effect_id = -1
        self._uploaded = None

    def play(self, strong, weak, duration_ms=150):
        """Start/update an effect; duration 0 holds until stop or fd closure."""
        if (any(type(v) is not int or not 0 <= v <= 65535 for v in (strong, weak))
                or type(duration_ms) is not int or not 0 <= duration_ms <= 65535):
            raise ValueError("invalid rumble magnitude or duration")
        wanted = strong, weak, duration_ms
        if self._uploaded != wanted:
            effect = _Effect()
            effect.type, effect.id = 0x50, self.effect_id
            effect.replay[0] = duration_ms
            effect.u.rumble.strong, effect.u.rumble.weak = strong, weak
            data = bytearray(bytes(effect))
            fcntl.ioctl(self.fd, (1 << 30) | (C.sizeof(_Effect) << 16) | (ord('E') << 8) | 0x80, data, True)
            self.effect_id = _Effect.from_buffer_copy(data).id
            self._uploaded = wanted
        # Playback restarts the finite effect's stop timer. Re-upload only
        # when parameters change, avoiding redundant kernel effect updates.
        os.write(self.fd, struct.pack('llHHi', 0, 0, 0x15, self.effect_id, 1))

    def stop(self):
        if self.fd is not None and self.effect_id >= 0:
            os.write(self.fd, struct.pack('llHHi', 0, 0, 0x15, self.effect_id, 0))

    def close(self):
        try:
            self.stop()
            if self.effect_id >= 0 and self.fd is not None:
                fcntl.ioctl(self.fd, (1 << 30) | (C.sizeof(C.c_int) << 16) | (ord('E') << 8) | 0x81,
                            self.effect_id)
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
