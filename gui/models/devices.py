# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every Flydigi device attached, and which one the window is showing.

The app drove "the pad" for its whole life: one worker, one handle, whichever
node sorted first. That is right for one pad and wrong for two, and it is wrong
in a way nothing on screen could show -- the header said "Apex 5 connected" and
meant "an Apex 5 connected".

This is the list behind the picker. It holds an entry per device from
`flydigi/registry.py`, remembers which pad and which dock are selected, and
turns a selection into the one thing the rest of the app needs: a **selector**,
the stable name a device can be reopened by after it has slept and come back
under a different node number.

**Two selections, one picker.** A pad and a dock are not alternatives -- the
Lighting page belongs to a pad and the Dock page to a dock -- so choosing a dock
must not make the pad pages go blank, and choosing a pad must not forget which
of two docks was being worked on. So `currentIndex` is what the picker moves and
what decides which pages the window offers, while `pad` and `dock` each remember
the last device of their kind. Selecting one of a kind is what changes that
kind's selector, and nothing else.

**The pad selection is written to the preferences file**, which is how the
daemon learns it: `flydigi/prefs.py:primary_pad` is read by `tools/flydigid`
for every route that holds one pad. The tier-1 vibration bind ignores it and
goes to every pad that takes one, because that route has nothing host-side in
the loop and so has no reason to choose.

Nothing here opens a device. Enumerating costs an exchange per device and
blocks; `gui/worker.py` does it on its thread and hands the result here.
"""
from PySide6.QtCore import (Property, QAbstractListModel, QModelIndex, Qt,
                            Signal, Slot)
from PySide6.QtQml import QmlElement

from flydigi import prefs, registry

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# One per kind, since the sidebar's icon and the picker's have to agree and
# there is no third place to put them.
ICONS = {registry.KIND_PAD: "input-gaming",
         registry.KIND_DOCK: "battery-full-charging"}


def detail(entry):
    """The second line of a picker row: what it is, and what is wrong with it.

    Never the nickname, which is the first line. This is what distinguishes two
    devices that a person has not named -- so it leads with the model and the
    node, which are the only things two identical pads do not share.
    """
    parts = [entry.get("model") or entry.get("product") or "unrecognised"]
    if entry.get("mock"):
        parts.append("mock")
    parts.append(entry["path"])
    if entry.get("error"):
        parts.append(entry["error"])
    elif not entry.get("supported"):
        parts.append("not driven by this app")
    return " · ".join(parts)


@QmlElement
class DevicesModel(QAbstractListModel):
    """The attached devices, as a list a picker and a page can both bind to."""

    LabelRole = Qt.UserRole + 1
    DetailRole = Qt.UserRole + 2
    KindRole = Qt.UserRole + 3
    PathRole = Qt.UserRole + 4
    SelectorRole = Qt.UserRole + 5
    IconRole = Qt.UserRole + 6
    MockRole = Qt.UserRole + 7
    SupportedRole = Qt.UserRole + 8
    BatteryRole = Qt.UserRole + 9
    ChargingRole = Qt.UserRole + 10
    FirmwareRole = Qt.UserRole + 11
    NicknameRole = Qt.UserRole + 12
    ModelRole = Qt.UserRole + 13
    UidRole = Qt.UserRole + 14
    ErrorRole = Qt.UserRole + 15

    countChanged = Signal()
    currentChanged = Signal()
    padChanged = Signal()
    dockChanged = Signal()
    # Asking the worker to look at the bus, since enumerating blocks.
    refreshRequested = Signal()
    # A device was selected: the worker drops whatever handle it held.
    padSelected = Signal(str)
    dockSelected = Signal(str)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._entries = []
        self._current = -1
        self._pad = ""
        self._dock = ""
        # The preferences file, shared with the daemon and with the Games page.
        # Injectable so a test can point it somewhere that is not the user's.
        self._prefs = settings if settings is not None else prefs.Prefs()
        self._pad = self._prefs.primary_pad() or ""

    # -- the list ----------------------------------------------------------

    def roleNames(self):
        return {
            self.LabelRole: b"label", self.DetailRole: b"detail",
            self.KindRole: b"kind", self.PathRole: b"path",
            self.SelectorRole: b"selector", self.IconRole: b"iconName",
            self.MockRole: b"mock", self.SupportedRole: b"supported",
            self.BatteryRole: b"battery", self.ChargingRole: b"charging",
            self.FirmwareRole: b"firmware", self.NicknameRole: b"nickname",
            self.ModelRole: b"model", self.UidRole: b"uid",
            self.ErrorRole: b"error",
        }

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index, role=Qt.DisplayRole):
        if not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role in (self.LabelRole, Qt.DisplayRole):
            return registry.label(entry)
        if role == self.DetailRole:
            return detail(entry)
        if role == self.KindRole:
            return entry["kind"]
        if role == self.PathRole:
            return entry["path"]
        if role == self.SelectorRole:
            return registry.key(entry)
        if role == self.IconRole:
            return ICONS.get(entry["kind"], "network-card")
        if role == self.MockRole:
            return bool(entry.get("mock"))
        if role == self.SupportedRole:
            return bool(entry.get("supported"))
        if role == self.BatteryRole:
            return -1 if entry.get("battery") is None else int(entry["battery"])
        if role == self.ChargingRole:
            return bool(entry.get("charging"))
        if role == self.FirmwareRole:
            return entry.get("firmware") or ""
        if role == self.NicknameRole:
            return entry.get("nickname") or ""
        if role == self.ModelRole:
            return entry.get("model") or entry.get("product") or ""
        if role == self.UidRole:
            return entry.get("uid") or ""
        if role == self.ErrorRole:
            return entry.get("error") or ""
        return None

    @Property(int, notify=countChanged)
    def count(self):
        return len(self._entries)

    # -- what the worker hands back ----------------------------------------

    @Slot(list)
    def devicesReceived(self, entries):
        """Replace the list, keeping the selection pointed at the same device.

        By selector, not by row. A pad that sleeps and returns lands on a
        different node and may sort somewhere else entirely, and a picker that
        held its row number would silently be showing a different device.

        A device that is gone leaves the selection alone rather than clearing
        it: `pad` and `dock` are what the app reopens by, and forgetting the
        pad because it dozed off during a config edit would lose the edit.
        """
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()
        self.countChanged.emit()
        self._reselect()

    def _reselect(self):
        wanted = self._pad if self.currentKind != registry.KIND_DOCK else self._dock
        row = self._row_for(wanted)
        if row < 0:
            # Whatever this kind's selection was, it is not here. Fall back to
            # the first device of the same kind, and then to the first device
            # at all -- an empty picker over a full bus is worse than a picker
            # showing something the user did not last choose.
            row = self._row_for_kind(self.currentKind)
        if row < 0:
            row = 0 if self._entries else -1
        self._set_current(row, remember=False)

    def _row_for(self, selector):
        if not selector:
            return -1
        for row, entry in enumerate(self._entries):
            if registry.matches(entry, selector):
                return row
        return -1

    def _row_for_kind(self, kind):
        for row, entry in enumerate(self._entries):
            if entry["kind"] == kind:
                return row
        return -1

    # -- the selection -----------------------------------------------------

    @Property(int, notify=currentChanged)
    def currentIndex(self):
        return self._current

    @currentIndex.setter
    def currentIndex(self, row):
        self._set_current(int(row), remember=True)

    def _set_current(self, row, remember):
        row = row if 0 <= row < len(self._entries) else -1
        changed = row != self._current
        self._current = row
        if row >= 0 and remember:
            entry = self._entries[row]
            self._remember(entry)
        if changed:
            self.currentChanged.emit()

    def _remember(self, entry):
        """Make this device its kind's selection, and tell the worker.

        The signal goes out even when the selector has not moved, because the
        thing on the far end of it is a handle that may have been dropped --
        re-selecting the pad you are already on is how a person asks for it to
        be picked up again.
        """
        selector = registry.key(entry)
        if entry["kind"] == registry.KIND_DOCK:
            self._dock = selector
            self.dockChanged.emit()
            self.dockSelected.emit(selector)
            return
        if selector != self._pad:
            self._pad = selector
            # The daemon reads this file for every route that holds one pad.
            # Written on the change rather than at shutdown: a window that is
            # killed should not take the choice with it.
            self._prefs.set_primary_pad(selector)
            self._prefs.save()
        self.padChanged.emit()
        self.padSelected.emit(selector)

    @Property(str, notify=padChanged)
    def pad(self):
        """The selector for the pad every pad page is showing."""
        return self._pad

    @Property(str, notify=dockChanged)
    def dock(self):
        """The selector for the dock the dock pages are showing."""
        return self._dock

    @Property(str, notify=currentChanged)
    def currentKind(self):
        if 0 <= self._current < len(self._entries):
            return self._entries[self._current]["kind"]
        # An empty bus shows the pad pages: they are what the app is for, and
        # they already say "looking for a controller" when there is none. A
        # window that offered nothing but a device list until something was
        # plugged in would be a worse answer to the same state.
        return registry.KIND_PAD

    @Property(bool, notify=currentChanged)
    def currentIsDock(self):
        return self.currentKind == registry.KIND_DOCK

    @Property(str, notify=currentChanged)
    def currentLabel(self):
        if 0 <= self._current < len(self._entries):
            return registry.label(self._entries[self._current])
        return ""

    @Property(int, notify=countChanged)
    def dockCount(self):
        """How many docks are attached, which is what puts the pages on offer."""
        return sum(1 for e in self._entries
                   if e["kind"] == registry.KIND_DOCK)

    @Property(int, notify=countChanged)
    def padCount(self):
        return sum(1 for e in self._entries if e["kind"] == registry.KIND_PAD)

    @Property(bool, notify=countChanged)
    def hasMock(self):
        """Whether anything on this bus is a fake, so a view can say so."""
        return any(e.get("mock") for e in self._entries)

    # -- what QML calls ----------------------------------------------------

    @Slot()
    def refresh(self):
        self.refreshRequested.emit()

    @Slot(int)
    def select(self, row):
        self._set_current(row, remember=True)

    @Slot(int, result="QVariant")
    def entry(self, row):
        """One row as a plain dict, for a details panel."""
        if 0 <= row < len(self._entries):
            return dict(self._entries[row])
        return {}
