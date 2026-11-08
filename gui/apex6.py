# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
# SPDX-License-Identifier: GPL-3.0-or-later

"""Apex 6 desktop adapter. No legacy commands or Qt dependencies live here."""
from flydigi import gpa6, gpa6_backup, gpa6_config


SETTING_FIELDS = {
    "sleep_minutes": "sleep_time",
    "quick_switch": "quick_switch",
    "auto_calibration": "stick_center_auto_adjust",
    "mapping_switch": "local_mapping",
    "precision": "stick_resolution",
    "report_rate": "usb_report_rate_hz",
    "xbox_home": "xinput_home",
    "third_party": "allow_third_party",
}
BOOL_SETTINGS = {"quick_switch", "auto_calibration", "mapping_switch",
                 "xbox_home", "third_party"}
LIGHT_EFFECTS = ["Off", "Solid", "Breathing", "Gradient", "Streaming", "Default"]


def settings_state(raw):
    state = {key: raw[field] for key, field in SETTING_FIELDS.items()}
    state.update({key + "_usable": True for key in BOOL_SETTINGS})
    state["apex6"] = True
    return state


class LightingConfig:
    """Native effect parameters behind the shared lighting model.

    Keep the original bytes until an edit, including an absent optional flag.
    Mode changes remember the colour/speed controls without inventing frames.
    """
    def __init__(self, blob):
        self.blob = bytes(blob)
        config = gpa6_config.lighting_config(self.blob)
        self.mode = config["base"]["mode"]
        values = config["base"]["config"] or {}
        self.brightness = values.get("brightness", 50)
        self.speed = values.get("speed", 50)
        self.grip_sync = config["vibration"]
        colours = values.get("colors", [values.get("color", dict(r=255, g=255, b=255))])
        self.colours = [tuple(c[key] for key in ("r", "g", "b")) for c in colours]

    def encode(self):
        colours = [dict(zip(("r", "g", "b"), c)) for c in self.colours]
        values = (None if self.mode == 0 else
                  dict(color=colours[0], brightness=self.brightness) if self.mode == 1 else
                  dict(colors=colours, brightness=self.brightness, speed=self.speed)
                  if self.mode in (2, 3) else
                  dict(brightness=self.brightness, speed=self.speed))
        self.blob = gpa6_config.encode_lighting({
            "base": {"mode": self.mode, "config": values},
            "vibration": bool(self.grip_sync)})


class Backend:
    """Worker-side adapter bound to a full UUID for the life of the handle."""
    def __init__(self, worker, ctrl):
        self.worker, self.ctrl = worker, ctrl
        self.protocol = gpa6.Protocol(ctrl)
        self.uid = self.protocol.uid()

    def _check(self, uid=None, slot=None):
        if (uid is not None and uid != self.uid) or self.protocol.uid() != self.uid:
            raise gpa6.ProtocolError("Controller changed; reload before writing")
        if slot is not None and self.protocol.slot() != slot:
            raise gpa6.ProtocolError("Active profile changed; reload before writing")

    def refresh_info(self):
        info = self.protocol.info()
        info["battery_steps"] = 10
        self.worker.info_changed.emit(info)
        self.refresh_status()

    def refresh_status(self):
        self.worker.active_changed.emit(self.protocol.slot())

    def refresh_settings(self):
        with self.ctrl.claim(timeout=0):
            self._check()
            raw = gpa6_config.global_config(self.protocol.read_ram(1), self.protocol.versions()[0])
        self._settings(raw)

    def _settings(self, raw):
        self.worker.settings_changed.emit(dict(settings_state(raw), uid=self.uid))
        self.worker.transport_changed.emit({"third_party": raw["allow_third_party"],
                                            "control_by": ""})

    def write_setting(self, name, value, uid):
        self._check(uid)
        field = SETTING_FIELDS[name]
        raw = gpa6_config.set_setting(
            self.ctrl, field, bool(value) if name in BOOL_SETTINGS else value,
            expected_uid=self.uid)
        self._settings(raw)
        self.worker.setting_written.emit(name, True)
        self.worker.status.emit("Controller setting verified")

    def refresh_transport(self):
        info = self.protocol.info()
        version = info["firmware"][0]
        self.worker.versions_changed.emit({
            "main": ".".join(str((version >> shift) & 15) for shift in (12, 8, 4, 0)),
            "third_party_available": False})
        # Settings refresh supplies takeover permission. There is no native
        # equivalent of the legacy holder string, so do not guess one.

    def load_profile(self, slot):
        with self.ctrl.claim(timeout=0):
            self._check()
            if self.protocol.slot() != slot:
                self.protocol.select_slot(slot)
            config = gpa6_backup.read_config(self.ctrl, expected_uid=self.uid)
            if config.cfg_id != slot:
                raise gpa6.ProtocolError("Active profile changed during read")
        self.worker.profile_loaded.emit(slot, gpa6_backup.pack_config(config), config.name)
        self.worker.active_changed.emit(slot)
        self.worker.apex6_lighting_loaded.emit(config.lighting, self.uid, slot)
        self.worker.status.emit(f"Profile {slot + 1} read")

    def write_profile(self, slot, blob, previous, save):
        # This foundation supports slot selection and saving; profile editors
        # and package restoration are enabled separately as their UI is adapted.
        if not save or blob != previous:
            raise gpa6.ProtocolError("Apex 6 profile editing is not available in this view")
        config = gpa6_backup.unpack_config(previous, slot)
        self._check(config.source["uid"], slot)
        gpa6_backup.write_config(self.ctrl, config, save=True, expected_uid=self.uid,
                                expected_slot=slot, expected_config=config)
        self.worker.profile_written.emit(slot, 0, True)

    def load_lighting(self):
        with self.ctrl.claim(timeout=0):
            self._check()
            fingerprint = self.protocol.ram_info(4)
            data = self.protocol.read_ram(4, expected=fingerprint)
            gpa6_config.lighting_config(data)
            self._check(slot=fingerprint[0])
        self.worker.apex6_lighting_loaded.emit(data, self.uid, fingerprint[0])

    def write_lighting(self, blob, previous, save, uid, slot):
        with self.ctrl.claim(timeout=0):
            self._check(uid, slot)
            def edit(current):
                self._check(uid, slot)
                if current != previous:
                    raise gpa6.ProtocolError("Lighting changed on the pad; reload before writing")
                return blob
            result = gpa6_config.edit_ram(self.ctrl, 4, edit, save=save, expected_uid=uid)
        self.worker.apex6_lighting_written.emit(result, save, uid, slot)
        self.worker.status.emit("Lighting verified" + (" and profile saved" if save else
                                "; the controller may retain applied changes without saving"))
