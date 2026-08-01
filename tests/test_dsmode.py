#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for DualSense mode: the switch around the relay, and vhci itself.

Needs no controller, no root and no vhci port. What it does need is a process to
find and stop, so it starts one of its own carrying the relay's path in its
command line -- which is exactly what `running_pids` looks for, and the only
part of this that a mock would make vacuous.

    python3 tests/test_dsmode.py
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import dsmode, usbip  # noqa: E402


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if not ok else ""))
    return bool(ok)


def modules_dep(tmp, release, lines):
    """A fake /lib/modules/<release>/modules.dep, to test the reader."""
    path = os.path.join(tmp, "lib", "modules", release)
    os.makedirs(path)
    with open(os.path.join(path, "modules.dep"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def main():
    results = []

    # -- the module ---------------------------------------------------------
    #
    # "loaded" and "available" answer different questions and are worth keeping
    # apart: an unloaded module is loaded by the relay itself when DS mode is
    # switched on, and a kernel without one cannot run DS mode at all.
    results.append(check("loaded is asked of the platform device, not lsmod",
                         usbip.module_loaded()
                         == os.path.isdir(usbip.VHCI_SYSFS)))
    results.append(check("a loaded module is available by definition",
                         not usbip.module_loaded() or usbip.module_available()))

    with tempfile.TemporaryDirectory() as tmp:
        real_sysfs = usbip.VHCI_SYSFS
        try:
            # Point the loaded check at nothing, so availability is decided by
            # the file rather than short-circuited by this machine's own module.
            usbip.VHCI_SYSFS = os.path.join(tmp, "absent")
            root = os.path.join(tmp, "lib", "modules")
            modules_dep(tmp, "has-it", [
                "kernel/drivers/usb/usbip/usbip-core.ko.xz:",
                "kernel/drivers/usb/usbip/vhci-hcd.ko.xz: "
                "kernel/drivers/usb/usbip/usbip-core.ko.xz",
            ])
            # Compression varies by distro and the name must survive it: Fedora
            # ships .ko.xz, Arch .ko.zst, Debian plain .ko.
            modules_dep(tmp, "plain-ko", ["kernel/drivers/usb/usbip/vhci-hcd.ko:"])
            modules_dep(tmp, "without-it", [
                "kernel/drivers/usb/usbip/usbip-core.ko.xz:",
                "kernel/drivers/net/dummy.ko.xz:",
            ])
            results.append(check("a kernel that has it says so",
                                 usbip.module_available("has-it", root)))
            results.append(check("compression suffixes do not hide it",
                                 usbip.module_available("plain-ko", root)))
            results.append(check("a kernel without it says so",
                                 not usbip.module_available("without-it", root)))
            results.append(check("a kernel with no modules.dep at all is not it",
                                 not usbip.module_available("no-such", root)))
            # vhci-hcd would match "vhci-hcd-something" on a sloppy substring
            # test, and worse, usbip-core would match any test for "usbip".
            modules_dep(tmp, "near-miss", ["kernel/drivers/usb/vhci-hcd-x.ko:"])
            results.append(check("a similarly named module is not it",
                                 not usbip.module_available("near-miss", root)))
        finally:
            usbip.VHCI_SYSFS = real_sysfs

    # -- the command line ---------------------------------------------------
    argv = dsmode.relay_argv(haptics=True, motors=True, quiet=True)
    results.append(check("the relay is the first word", argv[0] == dsmode.RELAY))
    results.append(check("haptic audio drives the motors by request",
                         "--motors" in argv and "--haptics" in argv))
    results.append(check("motors can be left off",
                         "--motors" not in dsmode.relay_argv(motors=False)))
    full = dsmode.start_argv()
    results.append(check("the app escalates rather than assuming root",
                         "pkexec" in full, str(full)))
    results.append(check("escalation wraps the relay, not apex5-setup",
                         dsmode.RELAY in full))

    # -- privileges ---------------------------------------------------------
    #
    # The one thing assertable without being root: it must not claim to have
    # dropped anything when there was nothing to drop.
    if os.geteuid() != 0:
        results.append(check("dropping privileges is a no-op for a plain user",
                             dsmode.drop_privileges() is None))

    # -- finding and stopping a running relay -------------------------------
    #
    # A real process, because the point of running_pids is that it survives the
    # app being restarted while DS mode is on: the state comes from the process
    # table, not from a handle the app happens to still hold.
    # Everything below is relative to what is already running. Asserting that
    # nothing is fails on the one machine most likely to run this: DS mode was
    # simply switched on in the app, and the test called a correct answer a
    # failure.
    already = set(dsmode.running_pids())
    if already:
        print(f"  note  DualSense mode is on ({len(already)} relay); "
              f"working around it")

    # The escalation wrapper carries the relay's path as an argument, so a
    # substring test for it reported "running" while the password dialog was
    # still up and nothing had been attached yet.
    results.append(check("pkexec in front of the relay is not the relay",
                         not dsmode._is_relay(
                             b"pkexec\0" + dsmode.RELAY.encode() + b"\0--motors\0")))
    results.append(check("nor is host-spawn in front of that",
                         not dsmode._is_relay(
                             b"host-spawn\0pkexec\0" + dsmode.RELAY.encode() + b"\0")))
    results.append(check("the relay itself is",
                         dsmode._is_relay(
                             b"python3\0" + dsmode.RELAY.encode() + b"\0--motors\0")))
    # A relative invocation records a relative path, so the name is compared
    # rather than the whole thing.
    results.append(check("started by a relative path, it still is",
                         dsmode._is_relay(b"python3\0tools/flydigi-ds5-usbip\0")))
    results.append(check("an unrelated python process is not",
                         not dsmode._is_relay(b"python3\0tools/flydigi-screen\0")))
    proc = subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(60)", dsmode.RELAY])
    try:
        deadline = time.monotonic() + 5
        while proc.pid not in dsmode.running_pids() and time.monotonic() < deadline:
            time.sleep(0.05)
        results.append(check("a running relay is found by its command line",
                             proc.pid in dsmode.running_pids(),
                             str(dsmode.running_pids())))
        results.append(check("and reported as running", dsmode.running()))
        # By pid, never `stop()` with no argument: that would take down a relay
        # the person running the tests is using.
        results.append(check("stopping it stops it", dsmode.stop([proc.pid])))
        results.append(check("and it is gone from the process table",
                             proc.pid not in dsmode.running_pids()))
        results.append(check("without disturbing anything else that was running",
                             already <= set(dsmode.running_pids()) | {
                                 p for p in already if not dsmode._alive(p)},
                             str(sorted(already))))
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)

    # -- status lines -------------------------------------------------------
    #
    # `out` and `iso_urbs` are the two numbers that answer "is this working":
    # output reports mean a game has bound to the virtual pad, isochronous URBs
    # mean it is writing haptic audio to it.
    parsed = dsmode.parse_status(
        "reports=1200 evdev=340 motion=9000 out=17 iso_urbs=4400")
    results.append(check("a status line parses to numbers",
                         parsed.get("out") == 17 and parsed.get("iso_urbs") == 4400,
                         str(parsed)))
    results.append(check("prose lines parse to nothing",
                         dsmode.parse_status("virtual DualSense attached on "
                                             "vhci port 0") == {}))
    results.append(check("unknown keys are left alone",
                         "worst_iter" not in dsmode.parse_status("worst_iter=1.2ms")))

    # The physical pad is now reported apart from the virtual one, because they
    # come and go independently: `pad=0` is a sleeping Apex 5 on a relay that is
    # very much still running and still holding the game's DualSense.
    parsed = dsmode.parse_status(
        "reports=1200 evdev=340 motion=9000 out=17 iso_urbs=4400 pad=0 drops=2")
    results.append(check("a sleeping pad is readable off the status line",
                         parsed.get("pad") == 0 and parsed.get("drops") == 2,
                         str(parsed)))

    # -- the whole reading --------------------------------------------------
    state = dsmode.state()
    results.append(check("state answers every question a page asks",
                         set(state) == {"available", "loaded", "running",
                                        "pids", "relay"}, str(sorted(state))))

    # The desktop app asks for this every two seconds on the thread that draws
    # the window, and `running` used to be answered by a second scan of the
    # process table -- the same question as `pids`, asked again.
    walks = []
    real_running_pids = dsmode.running_pids
    dsmode.running_pids = lambda: (walks.append(None) or [4242])
    try:
        state = dsmode.state()
    finally:
        dsmode.running_pids = real_running_pids
    results.append(check("one walk of /proc answers the whole reading",
                         len(walks) == 1, f"{len(walks)} walks"))
    results.append(check("and both answers come out of that one walk",
                         state["running"] is True and state["pids"] == [4242],
                         str(state)))

    ok = sum(1 for r in results if r)
    print(f"\n{ok}/{len(results)} passed")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
