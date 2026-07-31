#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Enumerating the bus, naming a device, and refusing an ambiguous name.

Every case here needs more than one device, and there is one pad and one dock on
this desk -- so the bus these run against is the mock one, which is the reason it
exists. What is *not* mocked is the code under test: `flydigi/registry.py` opens
these the same way it opens hardware, through `device.Controller`, and the fakes
answer the same protocol.

    python3 tests/test_registry.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import device, identity, mock, registry   # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


def with_bus(spec):
    """Run the body against a mock bus described by `spec`.

    `hide_real` throughout: these assert on counts and on ordering, and a pad
    plugged into the machine running them would change both.
    """
    def wrap(test):
        def run():
            handle, path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(handle, "w") as fh:
                json.dump(spec, fh)
            before = os.environ.get(mock.ENV)
            os.environ[mock.ENV] = path
            mock.reset()
            try:
                test()
            finally:
                if before is None:
                    os.environ.pop(mock.ENV, None)
                else:
                    os.environ[mock.ENV] = before
                mock.reset()
                os.unlink(path)
        run.__name__ = test.__name__
        return run
    return wrap


TWO_PADS_TWO_DOCKS = {
    "hide_real": True,
    "devices": [
        {"kind": "pad", "code": "k5", "nickname": "Desk"},
        {"kind": "pad", "code": "k5", "nickname": "Couch", "battery": 2},
        {"kind": "pad", "code": "f5", "nickname": "Vader"},
        {"kind": "dock", "type": 0, "nickname": "Shelf"},
        {"kind": "dock", "type": 1},
    ],
}


# --------------------------------------------------------------------------
# The bus
# --------------------------------------------------------------------------

def test_nothing_appears_until_it_is_asked_for():
    """The default is no mock devices at all, and that matters more than it reads.

    A fake pad that could turn up in front of someone driving real hardware
    would be worse than having no fakes: every other guarantee in this project
    is about not writing the wrong bytes to the wrong device.
    """
    before = os.environ.pop(mock.ENV, None)
    mock.reset()
    try:
        check("the mock bus is off by default", not mock.enabled())
        check("and offers no nodes",
              list(mock.nodes(device.FAMILY_PAD)) == [])
        check("and claims no path", mock.instance("/dev/hidraw0") is None)
    finally:
        if before is not None:
            os.environ[mock.ENV] = before
        mock.reset()


def test_the_short_spec_and_the_file_spec_agree():
    before = os.environ.get(mock.ENV)
    os.environ[mock.ENV] = "pad,pad:f5=Couch,dock:1=Shelf"
    mock.reset()
    try:
        devices = mock.spec()["devices"]
        check("three devices", len(devices) == 3, str(devices))
        check("the second is a Vader", devices[1]["code"] == "f5", str(devices))
        check("and is named", devices[1]["nickname"] == "Couch", str(devices))
        check("the dock carries its edition", devices[2]["type"] == "1",
              str(devices))
        check("real hardware is not hidden by the short form",
              not mock.hide_real())
    finally:
        if before is None:
            os.environ.pop(mock.ENV, None)
        else:
            os.environ[mock.ENV] = before
        mock.reset()


def test_a_spec_with_a_typo_says_so():
    before = os.environ.get(mock.ENV)
    os.environ[mock.ENV] = "pad,padd"
    mock.reset()
    try:
        mock.spec()
        check("a bad kind is refused", False)
    except ValueError as exc:
        check("a bad kind is refused", "not a mock device" in str(exc), str(exc))
    finally:
        if before is None:
            os.environ.pop(mock.ENV, None)
        else:
            os.environ[mock.ENV] = before
        mock.reset()


@with_bus(TWO_PADS_TWO_DOCKS)
def test_every_device_is_listed_with_what_it_is():
    found = registry.list_devices(deep=True)
    check("five devices", len(found) == 5, str(len(found)))
    check("pads first, then docks",
          [e["kind"] for e in found] == ["pad"] * 3 + ["dock"] * 2,
          str([e["kind"] for e in found]))
    check("each is marked as a mock", all(e["mock"] for e in found))
    labels = [registry.label(e) for e in found]
    check("nicknames win over models",
          labels[:3] == ["Desk", "Couch", "Vader"], str(labels))
    check("a dock with no name falls back to its model",
          labels[4] == "Controller Charging Dock 2 Pro EVA .ver", str(labels))

    desk = found[0]
    check("a pad reports its model", desk["model"] == "Apex 5", str(desk))
    check("and its battery", desk["battery"] == 4, str(desk))
    check("and its firmware", desk["firmware"] == "7.0.4.5", str(desk))
    check("and a uid", len(desk["uid"] or "") == 26, str(desk["uid"]))
    check("a k5 is supported", desk["supported"])
    check("a Vader 5 is not", not found[2]["supported"], str(found[2]))
    check("but is still named, so a refusal can say what it found",
          found[2]["model"] == "Vader 5 Pro", str(found[2]))


@with_bus(TWO_PADS_TWO_DOCKS)
def test_only_the_pads_this_project_drives_are_fanned_out_to():
    """What the daemon writes a tier-1 bind to."""
    drivable = registry.drivable_pads()
    check("two of the three pads", len(drivable) == 2, str(len(drivable)))
    check("and not the Vader",
          all(e["code"] == "k5" for e in drivable),
          str([e["code"] for e in drivable]))


@with_bus(TWO_PADS_TWO_DOCKS)
def test_a_device_is_found_by_any_of_its_names():
    listed = registry.list_devices(deep=True)
    couch = listed[1]

    check("by nickname", registry.find("Couch")["path"] == couch["path"])
    check("by nickname, case-insensitively",
          registry.find("couch")["path"] == couch["path"])
    check("by node", registry.find(couch["path"])["path"] == couch["path"])
    check("by uid", registry.find(couch["uid"])["path"] == couch["path"])
    check("by a uid prefix",
          registry.find(couch["uid"][:6])["path"] == couch["path"])
    check("by a typed selector",
          registry.find(f"uid:{couch['uid']}")["path"] == couch["path"])
    check("the stored key round-trips",
          registry.find(registry.key(couch))["path"] == couch["path"])


@with_bus(TWO_PADS_TWO_DOCKS)
def test_a_bare_lookup_takes_the_first_rather_than_refusing():
    """Every caller that has only ever seen one device passes nothing."""
    check("bare picks the first pad",
          registry.find(None, registry.KIND_PAD)["path"] == "mock:pad0")
    check("bare picks the first dock",
          registry.find(None, registry.KIND_DOCK)["path"] == "mock:dock3")
    with registry.open_pad() as pad:
        check("and opening bare opens it", pad.path == "mock:pad0", pad.path)


@with_bus(TWO_PADS_TWO_DOCKS)
def test_an_ambiguous_name_is_refused_rather_than_guessed():
    try:
        # Every mock uid ends the same way, so a suffix-free prefix of the
        # shared tail matches all of them.
        registry.find("0")
        check("an ambiguous uid prefix is refused", False)
    except registry.Ambiguous as exc:
        check("an ambiguous uid prefix is refused", True)
        check("and the message names the candidates",
              "mock:pad0" in str(exc) and "mock:pad1" in str(exc), str(exc))
    try:
        registry.find("nothing-called-this")
        check("an absent name is refused", False)
    except device.DeviceNotFound as exc:
        check("an absent name is refused", "no controller or charging dock"
              in str(exc), str(exc))


@with_bus(TWO_PADS_TWO_DOCKS)
def test_a_kind_narrows_the_search():
    check("a dock name does not match among pads",
          registry.find(None, registry.KIND_PAD)["kind"] == "pad")
    try:
        registry.find("Shelf", registry.KIND_PAD)
        check("a dock is not found among pads", False)
    except device.DeviceNotFound as exc:
        check("a dock is not found among pads", "no controller" in str(exc),
              str(exc))


@with_bus(TWO_PADS_TWO_DOCKS)
def test_a_selected_pad_is_the_one_that_is_opened():
    """The point of the whole layer: writes land where they were aimed."""
    with registry.open_pad("Couch") as pad:
        check("the named pad is the open one", pad.path == "mock:pad1",
              pad.path)
        check("and it answers as itself",
              identity.read_nickname(pad) == "Couch")
        identity.require(pad)
    with registry.open_pad("Vader") as pad:
        try:
            identity.require(pad)
            check("the guard still refuses a pad this project cannot drive",
                  False)
        except identity.WrongDevice as exc:
            check("the guard still refuses a pad this project cannot drive",
                  "Vader 5 Pro" in str(exc), str(exc))


@with_bus(TWO_PADS_TWO_DOCKS)
def test_naming_a_pad_reproduces_what_the_hardware_does():
    """Command 24, and every claim here came off the pad rather than the SDK.

    Written the way the reference builds it, this fails; written the way the
    framing implied, it *also* failed, because the pad keeps one byte more than
    the name and an appended checksum lands inside it. Three findings, and the
    fake reproduces all three -- so a reader or writer that regresses to either
    wrong shape fails here rather than on hardware.
    """
    with registry.open_pad("Desk") as pad:
        # Factory state: the field is not zeroes, it is the control bytes the
        # pad here shipped with -- which Flydigi's own emptiness test calls a
        # name, and which this reads as unnamed because it is not printable.
        mock.instance(pad.path).nickname_bytes = b""
        check("a never-named pad reads as unnamed, though its field is not zero",
              identity.read_nickname(pad) is None)

        check("an ASCII name round-trips",
              identity.write_nickname(pad, "Desk") == "Desk")
        check("and a long one",
              identity.write_nickname(pad, "Desk pad (left)") == "Desk pad (left)")
        check("and a one-character one, where both packet forms agree",
              identity.write_nickname(pad, "K") == "K")
        check("UTF-8 round-trips, so a name is bytes rather than characters",
              identity.write_nickname(pad, "\u0421\u0442\u043e\u043b")
              == "\u0421\u0442\u043e\u043b")

        # Flydigi's own builder, byte for byte. Their checksum lands on the
        # name's second character: "Desk" is stored as 44 a5 73 6b, which is
        # not valid UTF-8 and so reads as no name at all.
        check("Flydigi's own bytes corrupt the name",
              identity.write_nickname(pad, "Desk", reference=True) is None)
        stored = mock.instance(pad.path).nickname_bytes
        check("and corrupt it in the measured way",
              stored == bytes((0x44, 0xa5, 0x73, 0x6b)), stored.hex(" "))

        # The packet carries no checksum, because the pad does not check one
        # and because anything in that slot is stored as part of the name.
        packet = identity.nickname_packet("Desk")
        check("no checksum is written after the name",
              packet[9] == 0, packet[:12].hex(" "))
        check("where the reference writes one over the name",
              identity.nickname_packet("Desk", reference=True)[6] != 0x65)

    check("a name too long to fit is refused rather than truncated",
          _refuses_long_name())


def _refuses_long_name():
    try:
        identity.nickname_packet("x" * (identity.NICKNAME_MAX + 1))
    except ValueError:
        return True
    return False


@with_bus(TWO_PADS_TWO_DOCKS)
def test_state_survives_the_handle_being_closed():
    """A mock device is a device, not a fresh object per open.

    Without this the app's own write-then-read-back would pass against a mock
    for the wrong reason -- every read would see a factory-fresh pad.
    """
    with registry.open_pad("Desk") as pad:
        identity.write_nickname(pad, "Renamed")
    with registry.open_pad("Renamed") as pad:
        check("the write is still there after a close and reopen",
              identity.read_nickname(pad) == "Renamed")
        identity.write_nickname(pad, "Desk")


@with_bus(TWO_PADS_TWO_DOCKS)
def test_a_device_that_is_not_present_is_not_on_the_bus():
    """Flipping `present` is a pad going to sleep, which is how it leaves USB."""
    spec = mock.spec()
    spec["devices"][1]["present"] = False
    check("one pad fewer", len(registry.list_pads()) == 2,
          str(len(registry.list_pads())))
    try:
        registry.find("Couch")
        check("and it cannot be found", False)
    except device.DeviceNotFound:
        check("and it cannot be found", True)
    spec["devices"][1]["present"] = True
    check("and it comes back", len(registry.list_pads()) == 3)
    check("as the same device, remembering what it was told",
          registry.find("Couch")["nickname"] == "Couch")


@with_bus(TWO_PADS_TWO_DOCKS)
def test_the_key_is_stable_and_the_mac_is_not_used():
    """This pad reports an all-zero address, so a key must not be built on one."""
    for entry in registry.list_devices(deep=True):
        check(f"{registry.label(entry)} is keyed by uid",
              registry.key(entry).startswith("uid:"), registry.key(entry))
        check(f"{registry.label(entry)} reports no address, as hardware does",
              entry["mac"] is None, str(entry["mac"]))


# --------------------------------------------------------------------------

def main():
    for test in (test_nothing_appears_until_it_is_asked_for,
                 test_the_short_spec_and_the_file_spec_agree,
                 test_a_spec_with_a_typo_says_so,
                 test_every_device_is_listed_with_what_it_is,
                 test_only_the_pads_this_project_drives_are_fanned_out_to,
                 test_a_device_is_found_by_any_of_its_names,
                 test_a_bare_lookup_takes_the_first_rather_than_refusing,
                 test_an_ambiguous_name_is_refused_rather_than_guessed,
                 test_a_kind_narrows_the_search,
                 test_a_selected_pad_is_the_one_that_is_opened,
                 test_naming_a_pad_reproduces_what_the_hardware_does,
                 test_state_survives_the_handle_being_closed,
                 test_a_device_that_is_not_present_is_not_on_the_bus,
                 test_the_key_is_stable_and_the_mac_is_not_used):
        test()
    print(f"{len(PASSED)}/{len(PASSED) + len(FAILED)} passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
