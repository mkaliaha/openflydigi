# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""View-agnostic state for the desktop app.

Nothing in here imports QtWidgets or QtQuick -- that is the check that the
extraction is real, and `tests/test_models.py` asserts it. The widgets and the
QML pages are both views onto these objects.
"""
from .device import BATTERY_STEPS, DeviceModel
from .devices import DevicesModel
from .dock import (DIRECTION_NAMES, MODE_NAMES, SWITCH_LABELS, SWITCHES,
                   DockModel)
from .dsmode import DsModeModel
from .games import (ALL_ROUTES, APPLIABLE_ROUTE, ROUTE_NAMES, TIER_LABELS,
                    GameFilterModel, GameListModel, game_name, route_detail,
                    route_name)
from .lighting import (CYCLE_MAX, CYCLE_MIN, EFFECT_NAMES, EFFECTS,
                       KEEP_CURRENT, ColourListModel, LightingModel, from_hex,
                       invert_cycle, to_hex)
from .profile import (CURVE_PRESETS, DEFAULT_TARGET, KEY_CLUSTERS, KEY_LABELS,
                      MACRO_TYPES, MOTION_ENABLE_TYPES, MOTION_KEYS,
                      MOTION_NO_KEY, MOTION_TARGETS, RECORD_SECONDS, STICK_MAX,
                      TARGETS, TITLE_MAX_CHARS, TRIGGER_MODES, TURBO_MAX_HZ,
                      TURBO_MODES, KeyMapModel, MacroModel, MotionModel,
                      ProfileListModel, ProfileModel, StickModel,
                      StickSideModel, TriggerModel,
                      TriggerSideModel, VibrationModel, VibrationSideModel,
                      key_label)
from .screen import FIT_MODES, MAX_FRAMES, ScreenModel
from .settings import (PRECISION_NAMES, PRECISION_WIRE, SENSITIVITY_NAMES,
                       SENSITIVITY_WIRE, SettingsModel)
from .setup import SetupChecksModel, SetupModel

__all__ = [
    "ALL_ROUTES", "APPLIABLE_ROUTE", "BATTERY_STEPS", "CURVE_PRESETS",
    "DIRECTION_NAMES", "MODE_NAMES", "SWITCHES", "SWITCH_LABELS",
    "CYCLE_MAX", "CYCLE_MIN", "FIT_MODES", "MAX_FRAMES", "STICK_MAX",
    "DEFAULT_TARGET", "EFFECTS", "EFFECT_NAMES", "KEEP_CURRENT", "KEY_CLUSTERS",
    "KEY_LABELS", "MACRO_TYPES", "MOTION_ENABLE_TYPES", "MOTION_KEYS",
    "MOTION_NO_KEY", "MOTION_TARGETS", "PRECISION_NAMES", "PRECISION_WIRE",
    "RECORD_SECONDS", "ROUTE_NAMES", "SENSITIVITY_NAMES", "SENSITIVITY_WIRE",
    "TARGETS",
    "TIER_LABELS", "TITLE_MAX_CHARS",
    "TRIGGER_MODES", "TURBO_MAX_HZ",
    "TURBO_MODES",
    "ColourListModel", "DeviceModel", "DevicesModel", "DockModel",
    "DsModeModel", "GameFilterModel",
    "GameListModel", "KeyMapModel", "LightingModel", "MacroModel",
    "MotionModel", "ProfileListModel",
    "ProfileModel",
    "ScreenModel", "SettingsModel", "SetupChecksModel", "SetupModel",
    "StickModel", "StickSideModel", "TriggerModel", "TriggerSideModel", "VibrationModel", "VibrationSideModel",
    "from_hex", "game_name", "invert_cycle", "key_label", "route_detail",
    "route_name", "to_hex",
]
