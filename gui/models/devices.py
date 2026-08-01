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
    """The attached devices, as a list a picker and a page can both bind to.

    **A row is decoded when the bus reports, not when a delegate reads.** The
    entries the worker hands over are raw probe results; a label is a fallback
    chain through four fields, a detail line is a list joined together, and a
    selector picks the best of three names. `data` did all three per read, and
    the picker in the sidebar header reads them on every page. `_set_entries` is
    the one place `_entries` is assigned and it rebuilds the decoded rows in the
    same breath, so nothing can put an entry in without the row following it.
    """

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

    # Which roles each field of a probe entry feeds, so a poll that found one
    # number different can say which number rather than replacing the row. The
    # derived roles are the reason this is not one field to one role: a label
    # falls back through four fields, a detail line reads six.
    #
    # A field that reaches no role at all is listed with an empty tuple, so
    # that a field this table has never heard of is distinguishable from one it
    # has decided about -- see `_moved_roles`, which treats the unknown as
    # every role. `family` and `device_type` are identity the label never
    # shows, `code` feeds `model` upstream in `flydigi/registry.py`, `info` is
    # the raw reply the entry was built from, and `connect_type` is read from
    # the pad's own model rather than from here.
    FIELD_ROLES = {
        "path": (PathRole, LabelRole, DetailRole, SelectorRole),
        "kind": (KindRole, IconRole),
        "product": (LabelRole, DetailRole, ModelRole),
        "model": (LabelRole, DetailRole, ModelRole),
        "nickname": (NicknameRole, LabelRole),
        "uid": (UidRole, SelectorRole),
        "mac": (SelectorRole,),
        "mock": (MockRole, DetailRole),
        "supported": (SupportedRole, DetailRole),
        "error": (ErrorRole, DetailRole),
        "battery": (BatteryRole,),
        "charging": (ChargingRole,),
        "firmware": (FirmwareRole,),
        "family": (),
        "device_type": (),
        "code": (),
        "connect_type": (),
        "info": (),
    }

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
        # One decoded row per entry, keyed by role, plus the three tallies the
        # page and the sidebar bind to. Both are filled in by `_set_entries`.
        self._rows = []
        self._pad_count = 0
        self._dock_count = 0
        self._has_mock = False
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

    def _decode(self, entry):
        """One probe result, as the fields a delegate and a page will ask for."""
        return {
            self.LabelRole: registry.label(entry),
            self.DetailRole: detail(entry),
            self.KindRole: entry["kind"],
            self.PathRole: entry["path"],
            self.SelectorRole: registry.key(entry),
            self.IconRole: ICONS.get(entry["kind"], "network-card"),
            self.MockRole: bool(entry.get("mock")),
            self.SupportedRole: bool(entry.get("supported")),
            self.BatteryRole: (-1 if entry.get("battery") is None
                               else int(entry["battery"])),
            self.ChargingRole: bool(entry.get("charging")),
            self.FirmwareRole: entry.get("firmware") or "",
            self.NicknameRole: entry.get("nickname") or "",
            self.ModelRole: entry.get("model") or entry.get("product") or "",
            self.UidRole: entry.get("uid") or "",
            self.ErrorRole: entry.get("error") or "",
        }

    def _set_entries(self, entries):
        """Take a new list of probe results, decoding as they land.

        The only assignment to `_entries` there is. Everything derived from the
        list -- the rows, and the three tallies below -- is rebuilt here, so a
        caller cannot leave one of them describing the previous bus.
        """
        self._entries = entries
        self._rows = [self._decode(entry) for entry in entries]
        self._pad_count = sum(1 for e in entries
                              if e["kind"] == registry.KIND_PAD)
        self._dock_count = sum(1 for e in entries
                               if e["kind"] == registry.KIND_DOCK)
        self._has_mock = any(e.get("mock") for e in entries)

    def data(self, index, role=Qt.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._rows):
            return None
        return self._rows[row].get(self.LabelRole if role == Qt.DisplayRole
                                   else role)

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

        **An unchanged bus is not news, and saying it was cost the whole window
        its frame rate.** This arrives every ten seconds whether or not anything
        moved, and a reset destroys and rebuilds every delegate attached to the
        model -- including the picker in the sidebar header, which is on screen
        on *every* page. The result was a hitch across the entire application
        twice a minute, with nothing on the page responsible for it and no way
        to tell from the symptom which page was at fault.

        A probe of an idle bus is deep-equal to the last one: the fields are
        identity, firmware, nickname, and a battery level that is a 0..5 integer
        moving a few times an hour. So the common case compares equal and
        returns.

        **And when it does not compare equal, that is usually still the same
        devices.** The battery level is the field that moves on its own, and it
        moves without anything being plugged in or unplugged -- so treating an
        unequal probe as a new list put the whole window through the rebuild
        this guard exists to prevent, just a few times an hour instead of twice
        a minute. A row set that has not changed does not need a reset: the same
        devices in the same order are updated in place with `dataChanged`, which
        leaves every delegate where it is, and a reset is kept for a bus that
        really has gained or lost something.
        """
        entries = list(entries)
        if entries == self._entries:
            return
        if self._same_devices(entries):
            self._update_in_place(entries)
            return
        self.beginResetModel()
        self._set_entries(entries)
        self.endResetModel()
        self.countChanged.emit()
        self._reselect()

    def _same_devices(self, entries):
        """Whether this is the same devices in the same order as now.

        By selector and kind, which is what `registry.key` exists to be: the
        stable name a device keeps across a sleep. A pad that answers carries a
        uid -- `gui/worker.py` probes deep -- so its key is `uid:…` and moving
        to another hidraw node does not change it. That is the point: the
        picker keeps its row and its selection through a sleep.

        A device that does *not* answer has no uid and falls back to its node,
        so an unresponsive pad that moves does read as a different device and
        takes the reset path. That is also right -- there is nothing else to
        identify it by.
        """
        if len(entries) != len(self._entries):
            return False
        return all(registry.key(now) == row[self.SelectorRole]
                   and now["kind"] == row[self.KindRole]
                   for now, row in zip(entries, self._rows))

    def _update_in_place(self, entries):
        """Same devices, different readings. Say which readings.

        `count` cannot have moved -- the lists are the same length -- but
        `dockCount`, `padCount` and `hasMock` are notified by `countChanged`
        and by nothing else, so they are compared across the swap rather than
        assumed unchanged. A device changing kind under a stable selector is
        not something the bus does; a binding that quietly went stale would be
        very hard to see, and the comparison is three integers.
        """
        previous = self._entries
        tallies = (self._pad_count, self._dock_count, self._has_mock)
        self._set_entries(entries)
        for row, (was, now) in enumerate(zip(previous, entries)):
            roles = self._moved_roles(was, now)
            if not roles:
                # Something moved in a field no role reads -- the raw info
                # blob, most often. Nothing on screen is describing it.
                continue
            index = self.index(row, 0)
            self.dataChanged.emit(index, index, sorted(roles))
        if tallies != (self._pad_count, self._dock_count, self._has_mock):
            self.countChanged.emit()
        # The selection is by selector and every selector is where it was, so
        # this settles on the same row. It runs anyway because a selector may
        # be a nickname -- see `registry.matches` -- and a renamed device is
        # exactly the case where "the same devices" and "the same selection"
        # come apart.
        self._reselect()

    def _moved_roles(self, was, now):
        """Which roles differ between two readings of one device."""
        roles = set()
        for field in set(was) | set(now):
            if was.get(field) == now.get(field):
                continue
            fed = self.FIELD_ROLES.get(field)
            if fed is None:
                # A field this model has never been told about. Reporting every
                # role costs one row's worth of reads; reporting none would
                # leave a delegate showing something the bus has stopped
                # saying.
                return set(self.roleNames())
            roles.update(fed)
        return roles

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
        self._adopt_dock()

    def _adopt_dock(self):
        """Point the dock pages at a dock nobody has explicitly chosen.

        `_remember` is the only thing that fills `_dock` in, and it runs on a
        person picking a device in the picker -- so until somebody did, the Dock
        page sat waiting on a read that was never asked for. A pad does not have
        this problem because `_pad` comes out of the preferences file, and the
        dock has no equivalent there: the daemon has no use for one, so there is
        nothing to persist.

        The visible failure was narrow and reachable: with a dock attached and
        no pad -- which is every time the pad is asleep, since it leaves the USB
        bus entirely -- the window opens on the dock's own pages and the Dock
        page reads "Reading the dock…" until you open the picker and choose the
        dock that is already selected.

        Adopting rather than re-emitting, because this runs on every enumeration
        poll: once `_dock` names something on the bus this returns immediately,
        so a dock is read once and not every few seconds.
        """
        if self._dock and self._row_for(self._dock) >= 0:
            return
        row = self._row_for_kind(registry.KIND_DOCK)
        if row < 0:
            return
        selector = self._rows[row][self.SelectorRole]
        if not selector or selector == self._dock:
            return
        self._dock = selector
        self.dockChanged.emit()
        self.dockSelected.emit(selector)

    def _row_for(self, selector):
        if not selector:
            return -1
        for row, entry in enumerate(self._entries):
            if registry.matches(entry, selector):
                return row
        return -1

    def _row_for_kind(self, kind):
        for row, decoded in enumerate(self._rows):
            if decoded[self.KindRole] == kind:
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
            self._remember(row)
        if changed:
            self.currentChanged.emit()

    def _remember(self, row):
        """Make this device its kind's selection, and tell the worker.

        The signal goes out even when the selector has not moved, because the
        thing on the far end of it is a handle that may have been dropped --
        re-selecting the pad you are already on is how a person asks for it to
        be picked up again.
        """
        decoded = self._rows[row]
        selector = decoded[self.SelectorRole]
        if decoded[self.KindRole] == registry.KIND_DOCK:
            self._dock = selector
            self.dockChanged.emit()
            self.dockSelected.emit(selector)
            return
        if selector != self._pad:
            self._pad = selector
            # The daemon reads this file for every route that holds one pad.
            # Written on the change rather than at shutdown: a window that is
            # killed should not take the choice with it.
            #
            # **Only a pad this project drives.** Looking at an unsupported pad
            # is allowed -- the row is listed, and the page says what it is --
            # but this field is not "what the window is showing", it is "which
            # pad the routes drive", and the three drivers the daemon starts
            # rewrite trigger effects for the length of a session. Writing a
            # Vader 5 here would aim commands 81 and 82 at it from one click in
            # a picker. The drivers refuse it themselves as well; this stops the
            # preferences file ever asking them to.
            if decoded[self.SupportedRole]:
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
        if 0 <= self._current < len(self._rows):
            return self._rows[self._current][self.KindRole]
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
        if 0 <= self._current < len(self._rows):
            return self._rows[self._current][self.LabelRole]
        return ""

    @Property(int, notify=countChanged)
    def dockCount(self):
        """How many docks are attached, which is what puts the pages on offer."""
        return self._dock_count

    @Property(int, notify=countChanged)
    def padCount(self):
        return self._pad_count

    @Property(bool, notify=countChanged)
    def hasMock(self):
        """Whether anything on this bus is a fake, so a view can say so."""
        return self._has_mock

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
