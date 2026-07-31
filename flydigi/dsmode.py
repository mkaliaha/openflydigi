# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""DualSense mode as something a switch can turn on and off.

The relay itself is `tools/flydigi-ds5-usbip`; this is what a caller needs to
know around it -- whether the machine can run it, whether it is running, and how
to start and stop it. `tools/apex5-setup` and the desktop app share it, the way
they share `flydigi.setup`.

**It is a switch, not a per-game route.** Tiers 1-3 need per-game data, which is
why the gamelist exists and why the daemon picks a route per game. This tier
needs none: it presents a DualSense, and any DS5-aware game gets it, including
games Flydigi has never heard of. So there is one control for the whole system,
and `flydigi/prefs.py` no longer offers "ps5" as a per-game choice.

**Privilege.** Attaching to vhci is a privileged sysfs write, and it is the only
privileged thing here. The relay is started through pkexec, does the attach as
root, and then drops back to the invoking user before it opens the pad or serves
a single URB -- so what runs for the length of a play session is an ordinary
user process. Stopping is therefore a plain SIGTERM from the session that
started it, and the port comes back on its own: vhci resets a port to
VDEV_ST_NULL when its socket closes.

That is also why there is no unit and no standing polkit rule. An unattended
attach would mean granting the desktop session permanent permission to emulate
USB devices, which is a local privilege-escalation primitive -- one of those
devices is a keyboard. A user-driven switch costs one authentication and grants
nothing lasting.
"""
import errno
import os
import signal
import subprocess
import time

from . import setup, usbip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY = os.path.join(ROOT, "tools", "flydigi-ds5-usbip")

# Where the relay's own output goes. State rather than cache or config: it is
# reproducible only by running the thing again, and it is what a failure has to
# be read out of.
LOG_PATH = os.path.join(
    os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
    "flydigi", "ds5-relay.log")

# What a game has to be told, because nothing here can hide the physical pad
# from a game that enumerates it. Both pads are present with DS mode on.
IGNORE_DEVICES = "SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501"

# The relay's own status lines, as `key=value` pairs. Parsed rather than shown
# raw so a view can pick out the two numbers that answer "is this working":
# `out` is output reports from the game (it has bound to our pad) and `iso_urbs`
# is haptic audio arriving (it is using the audio path).
#
# `pad` is 1 while the physical Apex 5 is on the bus and 0 while it is asleep or
# unplugged, and `drops` counts the times it has left and come back. Neither
# says anything about the virtual pad, which stays attached throughout -- that
# separation is the whole point of them being two numbers.
STATUS_KEYS = ("reports", "evdev", "motion", "out", "iso_urbs", "loopback",
               "pad", "drops")

STOP_GRACE = 5.0


def drop_privileges():
    """Become the user who invoked pkexec again. Returns the uid, or None.

    Called by the relay once the attach is done. `setuid` from root sets the
    real, effective *and* saved uid, so this is one-way -- which is the point,
    and also what makes the process killable by the session that started it: a
    signal needs the sender's uid to match the target's real or saved uid, and
    a process that merely dropped its effective uid would keep root as the saved
    one and refuse the signal.
    """
    if os.geteuid() != 0:
        return None
    for name in ("PKEXEC_UID", "SUDO_UID"):
        raw = os.environ.get(name)
        if raw and raw.isdigit() and int(raw) != 0:
            uid = int(raw)
            break
    else:
        return None

    import pwd
    entry = pwd.getpwuid(uid)
    # initgroups before setgid before setuid: every one of them needs the
    # privilege the next one gives up, and doing them out of order leaves the
    # process in the invoker's uid with root's supplementary groups.
    os.initgroups(entry.pw_name, entry.pw_gid)
    os.setgid(entry.pw_gid)
    os.setuid(uid)
    os.environ["HOME"] = entry.pw_dir
    os.environ["USER"] = os.environ["LOGNAME"] = entry.pw_name
    return uid


def relay_argv(haptics=True, motors=True, quiet=True, extra=()):
    """The relay's own command line, without the escalation in front of it."""
    argv = [RELAY]
    if haptics:
        argv.append("--haptics")
    if motors:
        argv.append("--motors")
    if quiet:
        argv.append("--quiet")
    return argv + list(extra)


def start_argv(**kwargs):
    """The full command the app runs: pkexec (or host-spawn) plus the relay."""
    return setup.escalation_for(*relay_argv(**kwargs))


RELAY_NAME = os.path.basename(RELAY).encode()


def _is_relay(cmdline):
    """Whether a /proc cmdline belongs to the relay itself.

    Not a substring test for the path, which was the first attempt and is
    wrong in both directions. It matches the `pkexec` in front of the relay --
    so the switch would report "on" while the password dialog was still up and
    nothing had been attached -- and it misses a relay started by a relative
    path, since the recorded name is then relative too.

    What is asked instead: the process is a Python interpreter, and one of its
    arguments is a file called `flydigi-ds5-usbip`. The interpreter is in
    argv[0] because a `#!/usr/bin/env python3` script is exec'd through env,
    which the interpreter then replaces -- so a wrapper carrying the same path
    as an argument never looks like this.
    """
    args = [a for a in cmdline.split(b"\0") if a]
    if len(args) < 2 or not os.path.basename(args[0]).startswith(b"python"):
        return False
    return any(os.path.basename(a) == RELAY_NAME for a in args[1:])


def running_pids():
    """Pids of relays running right now, ours or anyone's.

    Asked of /proc rather than remembered, so the answer survives the app being
    restarted while DS mode is on -- otherwise the switch would come back off
    with a virtual pad still attached, and no way to turn it off from the UI.

    A distrobox shares the host's pid namespace, so this sees the relay whether
    it was started from inside the container or on the host. A Flatpak build
    would not; that is one more thing DS mode will need if the app is ever
    packaged that way.
    """
    out = []
    mine = os.getpid()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == mine:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                cmdline = handle.read()
        except OSError:                       # exited, or not ours to read
            continue
        if _is_relay(cmdline):
            out.append(pid)
    return out


def running():
    return bool(running_pids())


def stop(pids=None, grace=STOP_GRACE):
    """Ask every running relay to stop, and make sure it did.

    SIGTERM rather than SIGKILL: the relay puts the pad's motors and triggers
    back on the way out, and killing it outright would leave whatever the game
    last wrote buzzing.
    """
    pids = list(running_pids() if pids is None else pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            # ESRCH: already gone. EPERM: still in its privileged phase, which
            # lasts milliseconds -- the wait below then reports the failure
            # rather than this raising out of a worker thread.
            if exc.errno not in (errno.ESRCH, errno.EPERM):
                raise
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            return True
        time.sleep(0.1)
    for pid in [p for p in pids if _alive(p)]:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return not any(_alive(p) for p in pids)


def _alive(pid):
    """Whether a pid is a process that has not exited yet.

    Read out of /proc rather than tested with `kill(pid, 0)`, because a zombie
    answers that signal: a relay started by this process exits, stays in the
    table until it is reaped, and `kill(pid, 0)` keeps saying yes. That made
    `stop()` wait out its whole grace period and then SIGKILL something that had
    been dead for five seconds. Permission does not enter into it either, which
    matters while the relay is still in its privileged phase.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            # The command name is in parentheses and may contain spaces, so the
            # state field is found by splitting after the LAST ')'.
            fields = handle.read().rsplit(b")", 1)[1].split()
    except (OSError, IndexError):
        return False
    return bool(fields) and fields[0] not in (b"Z", b"X")


def start(log=LOG_PATH, **kwargs):
    """Start the relay, escalating first. Returns the Popen.

    Output goes to a file rather than a pipe, and that is not a detail. The
    relay runs for a whole play session and prints a status line every ten
    seconds; a pipe nobody drains fills at about 4 kB and blocks the writer,
    which here is the process serving USB URBs on a 4 ms deadline. A file needs
    no reader alive at all, which also means the app can quit without taking the
    pad away from a running game.

    Truncated per run, so nothing reads a previous session's last words as this
    one's. The file is opened by the caller and inherited, so it keeps being
    written after the relay drops back to the invoking user.

    The caller keeps the handle to notice an early exit -- a cancelled
    authentication, no pad, no free vhci port -- but must not use it to answer
    "is DS mode on": the process it holds may be a `host-spawn` wrapper rather
    than the relay itself. `running_pids()` is the state.
    """
    argv = start_argv(**kwargs)
    if log:
        os.makedirs(os.path.dirname(log), exist_ok=True)
        handle = open(log, "w")
        try:
            return subprocess.Popen(argv, stdout=handle,
                                    stderr=subprocess.STDOUT)
        finally:
            handle.close()
    return subprocess.Popen(argv)


def tail(count=40, log=LOG_PATH):
    """The last lines the relay wrote, oldest first. [] when it has not run.

    Small enough to read whole: the file is truncated at every start and the
    relay writes a few lines a minute.
    """
    try:
        with open(log) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()][-count:]


def latest_status(log=LOG_PATH):
    """The most recent counters the relay reported, or {}."""
    for line in reversed(tail(log=log)):
        parsed = parse_status(line)
        if parsed:
            return parsed
    return {}


def parse_status(line):
    """`key=value` pairs out of one of the relay's status lines.

    Returns {} for the lines that carry none, so a caller can feed it every line
    the relay prints without deciding first which kind it is.
    """
    out = {}
    for token in line.replace(",", " ").split():
        key, sep, value = token.partition("=")
        if sep and key in STATUS_KEYS:
            try:
                out[key] = int(value)
            except ValueError:
                pass
    return out


def state():
    """Everything a view needs, in one reading.

    `available` and `loaded` are kept apart because they call for different
    answers: an unloaded module is loaded by the relay itself at the moment DS
    mode is switched on, and a kernel without the module cannot run DS mode at
    all.
    """
    return {
        "available": usbip.module_available(),
        "loaded": usbip.module_loaded(),
        "running": running(),
        "pids": running_pids(),
        "relay": RELAY,
    }
