# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""View-agnostic state for the desktop app.

Nothing in here imports QtWidgets or QtQuick -- that is the check that the
extraction is real, and `tests/test_models.py` asserts it. The widgets and the
QML pages are both views onto these objects.
"""
from .device import BATTERY_STEPS, DeviceModel
from .games import (ALL_ROUTES, APPLIABLE_ROUTE, TIER_LABELS, GameFilterModel,
                    GameListModel, game_name, route_detail)
from .lighting import (CYCLE_MAX, CYCLE_MIN, EFFECT_NAMES, EFFECTS,
                       KEEP_CURRENT, ColourListModel, LightingModel, from_hex,
                       invert_cycle, to_hex)
from .profile import (DEFAULT_TARGET, KEY_CLUSTERS, KEY_LABELS, TARGETS,
                      TITLE_MAX_CHARS, TRIGGER_MODES, TURBO_MAX_HZ,
                      TURBO_MODES, KeyMapModel,
                      ProfileListModel, ProfileModel, TriggerModel,
                      TriggerSideModel, VibrationModel, VibrationSideModel,
                      key_label)

__all__ = [
    "ALL_ROUTES", "APPLIABLE_ROUTE", "BATTERY_STEPS", "CYCLE_MAX", "CYCLE_MIN",
    "DEFAULT_TARGET", "EFFECTS", "EFFECT_NAMES", "KEEP_CURRENT", "KEY_CLUSTERS",
    "KEY_LABELS", "TARGETS", "TIER_LABELS", "TITLE_MAX_CHARS",
    "TRIGGER_MODES", "TURBO_MAX_HZ",
    "TURBO_MODES",
    "ColourListModel", "DeviceModel", "GameFilterModel", "GameListModel",
    "KeyMapModel", "LightingModel", "ProfileListModel", "ProfileModel",
    "TriggerModel", "TriggerSideModel", "VibrationModel", "VibrationSideModel",
    "from_hex", "game_name", "invert_cycle", "key_label", "route_detail",
    "to_hex",
]
