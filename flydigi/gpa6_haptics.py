# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: MIT

"""Apex 6 native profile feedback, matching web Space Station's controls.

Mapping TLVs hold strengths; area-0 motor commands hold trigger modes. The
editor coordinates them under one claim with fresh state, read-back and guarded
rollback. This is separate from the native engine's area-1 streaming lifecycle.
"""
import math
import contextlib
import select
import threading
import time
import zlib

from . import gpa6, gpa6_config, gpa6_profile, identity

SIDES = ("left", "right")
GRIP_LEVELS = (0, 51, 102, 153, 204, 255)
HAPTIC_GRIP_LEVELS = (0, 128, 166, 204, 230, 255)
BIND_PARAMS = bytes((0, 64, 6))
# Vendor scene IDs, fixed gain, default travel and looping frequency intervals.
SCENES = {
    "machine-gun": (0, 29, 2, (80, 45, 0)),
    "rifle": (1, 32, 2, (102, 58, 0)),
    "submachine-gun": (2, 40, 2, (66, 37, 0)),
    "light-machine-gun": (3, 27, 2, (43, 7, 0)),
    "pistol": (4, 28, 1, ()), "shotgun": (5, 28, 1, ()),
    "sniper-rifle": (6, 32, 3, ()), "sword": (7, 36, 2, ()),
    "blade": (8, 40, 2, ()), "dagger": (9, 40, 1, ()),
    "axe": (10, 40, 2, ()), "club": (11, 36, 2, ()),
}


def scene_params(name, *, travel=None, frequency=None):
    if name not in SCENES:
        raise ValueError("unknown trigger scene")
    scene_id, gain, default_travel, intervals = SCENES[name]
    travel = default_travel if travel is None else _level(travel, 3)
    if frequency is not None:
        _level(frequency, 3)
        if not intervals:
            raise ValueError("this scene does not support frequency adjustment")
    start = (50, 80, 178)[travel - 1]
    end = 255 if intervals else start + 10
    interval = intervals[(3 if frequency is None else frequency) - 1] if intervals else 0
    return bytes((scene_id, start, end, bool(intervals), interval, gain, 64, 64, scene_id, 255))


def _level(value, maximum=5):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"strength/adjustment level must be an integer in 1..{maximum}")
    return value


def _round(value):
    # JavaScript Math.round for the nonnegative curve values used by the UI.
    return math.floor(value + .5)


def _display_level(curve):
    return max(1, min(5, _round(curve["end"][1] * 5 / 255)))


def _normalize_curve(curve):
    """Recover the vendor's base curve before applying a strength level."""
    start, end, points = curve["start"], curve["end"], curve["points"]
    if not any(y for _, y in (start, end, *points)):
        xs = [x for x, _ in points]
        ys = [77, 217] if xs == [64, 192] else [255, 255] if xs == [60, 200] else [y for _, y in points]
        return {"start": start, "end": (end[0], 255), "points": list(zip(xs, ys))}
    scale = max(1 / 255, end[1] / 255)
    def point(p):
        return p[0], max(0, min(255, _round(p[1] / scale)))
    return {"start": point(start), "end": point(end), "points": [point(p) for p in points]}


class MotorConfig:
    """Lossless layout-2 motor data; only persistent trigger settings editable."""

    def __init__(self, blob):
        if len(blob) != 64 or blob[0] != 2:
            raise gpa6.ProtocolError("unsupported motor configuration layout")
        self.blob = bytearray(blob)

    def trigger(self, side):
        if side not in SIDES:
            raise ValueError("trigger side must be left/right")
        offset = 1 + SIDES.index(side) * 11
        mode = self.blob[offset]
        width = (0, 10, 3)[mode] if mode in (0, 1, 2) else 10
        return {"mode": mode, "params": bytes(self.blob[offset + 1:offset + 1 + width]),
                "record": bytes(self.blob[offset:offset + 11])}

    def set_trigger(self, side, mode, params=b""):
        old = self.trigger(side)
        if type(mode) is not int or mode not in (0, 1, 2) or len(params) != (0, 10, 3)[mode]:
            raise ValueError("invalid persistent trigger mode/parameters")
        wanted = bytes([mode]) + bytes(params).ljust(10, b"\0")
        if wanted == old["record"]:
            return
        # Firmware clears unused bytes. Refuse a source that cannot be restored
        # exactly through the known commands instead of discarding opaque data.
        if old["mode"] not in (0, 1, 2) or old["record"] != bytes([old["mode"]]) + old["params"].ljust(10, b"\0"):
            raise gpa6.ProtocolError("selected trigger has an unsupported/non-restorable motor record")
        offset = 1 + SIDES.index(side) * 11
        self.blob[offset:offset + 11] = wanted


class HapticConfig:
    """Editable view for CLI and future GUI, with ordered cross-domain edits."""

    def __init__(self, mapping_blob, motor_blob, *, haptic_grip=False, trigger_haptic=True, cfg_id=None):
        self.mapping = gpa6_profile.MappingConfig(mapping_blob, cfg_id)
        self.motors = MotorConfig(motor_blob)
        self.haptic_grip = bool(haptic_grip)
        self.trigger_haptic = bool(trigger_haptic)
        self.cfg_id = cfg_id
        self._steps = []

    def copy(self):
        return HapticConfig(self.mapping.blob, self.motors.blob, haptic_grip=self.haptic_grip,
                            trigger_haptic=self.trigger_haptic, cfg_id=self.cfg_id)

    def vibration(self, target):
        if target == "grips":
            sides = {side: self.mapping.vibration_curve("grip", side) for side in SIDES}
            source = sides["left"] if sides["left"]["enabled"] else sides["right"]
            curve = source["curve"]
            levels = HAPTIC_GRIP_LEVELS if self.haptic_grip else GRIP_LEVELS
            level = (min(range(1, 6), key=lambda i: abs(levels[i] - curve["end"][1]))
                     if not curve["points"] else _display_level(curve))
            return {"enabled": any(s["enabled"] for s in sides.values()), "level": level,
                    "sides": sides, "haptic_grip": self.haptic_grip}
        if target not in SIDES:
            raise ValueError("vibration target must be grips/left/right")
        curve = self.mapping.vibration_curve("trigger", target)["curve"]
        motor = self.motors.trigger(target)
        return {"enabled": motor["mode"] == 2, "level": _display_level(curve),
                "curve": curve, "motor_mode": motor["mode"]}

    def _mapping_step(self, change):
        before = bytes(self.mapping.blob)
        change()
        after = bytes(self.mapping.blob)
        if before != after:
            self._steps.append((5, before, after, None))

    def _motor_step(self, side, mode, params=b""):
        if not self.trigger_haptic:
            raise gpa6.ProtocolError("controller does not report trigger haptics")
        before = bytes(self.motors.blob)
        self.motors.set_trigger(side, mode, params)
        after = bytes(self.motors.blob)
        if before != after:
            self._steps.append((6, before, after, side))

    def set_vibration(self, target, *, enabled=None, level=None):
        """Vendor controls: paired grips, or one trigger's binding and strength.

        Trigger strength alone preserves its mode; enabling replaces a scene.
        Grip strength selection enables grips, matching the vendor slider.
        """
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if level is not None:
            _level(level)
        self.vibration(target)  # validate before changing anything
        if enabled is None and level is None:
            return
        if target == "grips":
            current = self.vibration(target)
            wanted_level = current["level"] if level is None else level
            wanted_enable = (True if level is not None else current["enabled"]) if enabled is None else enabled
            amplitude = (HAPTIC_GRIP_LEVELS if self.haptic_grip else GRIP_LEVELS)[wanted_level]
            def change():
                for side in SIDES:
                    self.mapping._set_vibration_curve("grip", side, enabled=wanted_enable,
                        start=(0, 0), end=(255, amplitude), points=[])
            self._mapping_step(change)
            return
        if not self.trigger_haptic:
            raise gpa6.ProtocolError("controller does not report trigger haptics")
        if enabled is True:
            if self.motors.trigger(target)["mode"] == 1:
                self._motor_step(target, 0)
            self._motor_step(target, 2, BIND_PARAMS)
        elif enabled is False:
            self._motor_step(target, 0)
        if level is not None:
            curve = _normalize_curve(self.mapping.vibration_curve("trigger", target)["curve"])
            def point(p):
                return p[0], max(0, min(255, _round(p[1] * level / 5)))
            self._mapping_step(lambda: self.mapping._set_vibration_curve("trigger", target,
                start=point(curve["start"]), end=point(curve["end"]),
                points=[point(p) for p in curve["points"]]))

    def describe(self):
        return {"vibration": {target: self.vibration(target) for target in ("grips", *SIDES)},
                "trigger_scenes": {side: self.trigger_scene(side) for side in SIDES}}

    def trigger_scene(self, side):
        entry = self.motors.trigger(side)
        mode, params = entry["mode"], entry["params"]
        result = {"mode": {0: "normal", 1: "custom", 2: "vibration"}.get(mode, "unknown"),
                  "scene": None, "travel": None, "frequency": None, "params_hex": params.hex()}
        if mode != 1:
            return result
        for name, (scene_id, gain, default_travel, intervals) in SCENES.items():
            if params[0] != scene_id or params[3] != bool(intervals) or params[8] != scene_id:
                continue
            result.update(mode="scene", scene=name)
            for travel, start in enumerate((50, 80, 178), 1):
                if params[1:3] == bytes((start, 255 if intervals else start + 10)):
                    result["travel"] = travel
            if intervals and params[4] in intervals:
                result["frequency"] = intervals.index(params[4]) + 1
            return result
        return result

    def set_trigger_scene(self, side, scene=None, *, travel=None, frequency=None):
        """Select a vendor scene or adjust its three-level settings.

        Selecting a scene disables conflicting vibration curves. Selecting
        normal from haptics resets the selected base-layer trigger curves/zones,
        as the web Pro page does. Adjustment preserves unrelated input settings.
        """
        sides = SIDES if side == "both" else (side,)
        if any(s not in SIDES for s in sides):
            raise ValueError("scene side must be left/right/both")
        if scene is None and travel is None and frequency is None:
            return
        if not self.trigger_haptic:
            raise gpa6.ProtocolError("controller does not report trigger haptics")
        if scene == "normal":
            if travel is not None or frequency is not None:
                raise ValueError("normal mode has no scene travel/frequency settings")
            if not any(self.motors.trigger(s)["mode"] == 1 for s in sides):
                return
            for s in sides:
                self._motor_step(s, 0)
            def reset():
                for s in sides:
                    self.mapping.set_response_curve("trigger", s, start=(0, 0), end=(255, 255), points=[])
                    self.mapping.set_trigger_zones(s, [], layer=0)
            self._mapping_step(reset)
            return
        wanted = {}
        for s in sides:
            if scene is None:
                current = self.trigger_scene(s)
                if (current["scene"] is None or current["travel"] is None
                        or (SCENES[current["scene"]][3] and current["frequency"] is None)):
                    raise ValueError("current haptic settings cannot be adjusted; select a scene first")
                wanted[s] = scene_params(current["scene"],
                    travel=current["travel"] if travel is None else travel,
                    frequency=current["frequency"] if frequency is None else frequency)
            else:
                wanted[s] = scene_params(scene, travel=travel, frequency=frequency)
        if scene is not None and any(self.mapping.vibration_curve("trigger", s)["curve"]["end"][1] > 0 for s in sides):
            def disable_vibration():
                for s in sides:
                    self.mapping._set_vibration_curve("trigger", s, start=(0, 0), end=(255, 0), points=[])
            self._mapping_step(disable_vibration)
        for s, params in wanted.items():
            self._motor_step(s, 1, params)


def _read(protocol):
    if protocol.versions()[1] not in (18, 19):
        raise gpa6.ProtocolError("unsupported mapping protocol version")
    fingerprints = {ram: protocol.ram_info(ram) for ram in (5, 6)}
    if fingerprints[5][0] != fingerprints[6][0]:
        raise gpa6.ProtocolError("profile changed while reading haptic settings")
    blobs = {ram: protocol.read_ram(ram, expected=fingerprints[ram]) for ram in (5, 6)}
    if any(protocol.ram_info(ram) != fingerprints[ram] for ram in (5, 6)):
        raise gpa6.ProtocolError("haptic settings changed during read")
    info = protocol.info()
    return HapticConfig(blobs[5], blobs[6], haptic_grip=info["haptic_grip"],
                        trigger_haptic=info["trigger_haptic"], cfg_id=fingerprints[5][0])


def read(ctrl, *, expected_uid=None):
    with ctrl.claim(timeout=0):
        protocol, uid = gpa6_config._editor(ctrl, expected_uid)
        config = _read(protocol)
        if protocol.uid() != uid or protocol.slot() != config.cfg_id:
            raise gpa6.ProtocolError("device/profile changed during haptic read")
        return config


def edit(ctrl, transform, *, save=False, expected_uid=None):
    """Apply a pure HapticConfig edit with verified, reversible ordered writes.

    On failure, restore only a step whose exact candidate is still on the same
    UUID/profile. Concurrent edits and uncertain partial writes are reported,
    never overwritten. Explicit save errors remain outside rollback, like the
    other configuration editors. Applied edits may persist without --save.
    """
    with ctrl.claim(timeout=0):
        protocol, uid = gpa6_config._editor(ctrl, expected_uid)
        config = _read(protocol)
        expected = {5: bytes(config.mapping.blob), 6: bytes(config.motors.blob)}
        transform(config)
        journal = []

        def check_identity():
            if protocol.uid() != uid:
                raise identity.WrongDevice("UUID changed during haptic edit")
            if protocol.slot() != config.cfg_id:
                raise gpa6.ProtocolError("profile changed during haptic edit")

        def write(ram, data, side, previous):
            if ram == 5:
                protocol.write_ram(5, data, expected=(config.cfg_id, len(previous), zlib.crc32(previous)))
            else:
                entry = MotorConfig(data).trigger(side)
                protocol.configure_trigger(SIDES.index(side), entry["mode"], entry["params"])

        try:
            for step in config._steps:
                ram, before, after, side = step
                check_identity()
                if any(protocol.read_ram(key) != value for key, value in expected.items()):
                    raise gpa6.ProtocolError("haptic settings changed before write")
                journal.append(step)  # include a write whose ACK may be lost
                write(ram, after, side, before)
                check_identity()
                expected[ram] = after
                if any(protocol.read_ram(key) != value for key, value in expected.items()):
                    raise gpa6.ProtocolError("haptic configuration read-back mismatch")
        except BaseException as exc:
            try:
                for ram, before, after, side in reversed(journal):
                    check_identity()
                    current = protocol.read_ram(ram)
                    if current == before:
                        continue
                    if current != after:
                        raise gpa6.ProtocolError("concurrent/partial change: automatic rollback refused")
                    write(ram, before, side, after)
                    if protocol.read_ram(ram) != before:
                        raise gpa6.ProtocolError("haptic rollback read-back mismatch")
            except BaseException as cleanup:
                raise gpa6.ProtocolError(f"{exc}; rollback incomplete: {cleanup}") from exc
            raise
        if save:
            check_identity()
            if any(protocol.read_ram(key) != value for key, value in expected.items()):
                raise gpa6.ProtocolError("haptic settings changed before save")
            protocol.save_slot(config.cfg_id)
        return config


class VibrationTest:
    """Vendor-style test: either trigger above 20% requests ordinary rumble.

    Refresh a 150-ms effect every 50 ms, matching the browser test cadence.
    HID status reads disturbed ongoing rumble on the tested firmware, so checks
    run only between holds, serialized with output.
    Input/output runs every 50 ms; an active effect can stop on release without
    waiting for a HID query. Closing the output fd also removes the effect.
    Call tick from the owning thread to check state/errors. Profile/configuration
    changes are detected while idle. No configuration writes or waveform takeover
    are used.
    """

    def __init__(self, ctrl, *, expected_uid=None):
        self.ctrl, self.expected_uid = ctrl, expected_uid
        self._stack = None

    def __enter__(self):
        from . import evdev, evdev_rumble
        stack = contextlib.ExitStack()
        try:
            stack.enter_context(self.ctrl.claim(timeout=0))
            self.protocol, self.uid = gpa6_config._editor(self.ctrl, self.expected_uid)
            self.config = read(self.ctrl, expected_uid=self.uid)
            root = evdev.usb_device_of_hidraw(self.ctrl.path)
            if root is None:
                raise gpa6.ProtocolError("cannot resolve selected controller's USB input device")
            path, _ = evdev.find_device(axes=True, usb_root=root)
            if path is None:
                raise gpa6.ProtocolError("selected controller has no gamepad input node")
            self.reader = evdev.Reader(path)
            stack.callback(self.reader.close)
            if not all(code in self.reader.abs for code in (evdev.ABS_Z, evdev.ABS_RZ)):
                raise gpa6.ProtocolError("selected input node has no trigger axes")
            self.output = evdev_rumble.RumbleOutput(path)
            stack.callback(self.output.close)
            self._next_check = 0
            self._check_ram = 5
            self._active = False
            self._error = None
            self._stop = threading.Event()
            self._output_lock = threading.Lock()
            self._worker = threading.Thread(target=self._run_output, name="apex6-vibration-test", daemon=True)
            self._worker.start()
            stack.callback(self._stop_output)
            self._stack = stack.pop_all()
            return self
        finally:
            stack.close()

    def tick(self):
        if self._stack is None:
            raise RuntimeError("vibration test is not started")
        if self._error is not None:
            raise self._error
        # Hold the same lock used to start output: an idle check must not
        # overlap a trigger press that arrives while the HID reply is pending.
        with self._output_lock:
            now = time.monotonic()
            if not self._active and now >= self._next_check:
                if self.protocol.uid() != self.uid or self.protocol.slot() != self.config.cfg_id:
                    raise gpa6.ProtocolError("device/profile changed during vibration test")
                ram = self._check_ram
                blob = self.config.mapping.blob if ram == 5 else self.config.motors.blob
                if self.protocol.ram_info(ram) != (self.config.cfg_id, len(blob), zlib.crc32(blob)):
                    raise gpa6.ProtocolError("settings changed during vibration test; restart the test")
                # One fingerprint per idle check keeps control traffic bounded.
                self._check_ram = 11 - ram
                self._next_check = now + .5
        if self._error is not None:
            raise self._error
        return self._active

    def _tick_output(self):
        from . import evdev
        while select.select([self.reader], [], [], 0)[0]:
            if not self.reader.read():
                break
        pressed = any(self.reader.axis(code) > .2 for code in (evdev.ABS_Z, evdev.ABS_RZ))
        enabled = any(self.config.vibration(target)["enabled"] for target in ("grips", *SIDES))
        if pressed and enabled:
            # The web's dual-rumble request uses max(grip, trigger) on each
            # side, with either enabled trigger setting both request levels.
            self.output.play(65535, 65535, 150)
        elif not (pressed and enabled) and self._active:
            self.output.stop()
        self._active = pressed and enabled

    def _run_output(self):
        try:
            while not self._stop.is_set():
                start = time.monotonic()
                with self._output_lock:
                    self._tick_output()
                self._stop.wait(max(0, .05 - (time.monotonic() - start)))
        except Exception as exc:
            self._error = exc
        finally:
            try:
                self.output.stop()
            except OSError as exc:
                if self._error is None:
                    self._error = exc
            self._active = False

    def _stop_output(self):
        self._stop.set()
        self._worker.join()

    def __exit__(self, *exc):
        if self._stack is not None:
            stack, self._stack = self._stack, None
            stack.close()
