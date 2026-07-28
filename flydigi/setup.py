# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""System integration: the systemd unit, the udev rules, and what is missing.

`tools/apex5-setup` and the desktop app both read their state from `checks()`,
so the CLI and the app cannot disagree about whether a machine is set up.

Two boundaries run through this file.

**Privilege.** Everything except the udev rules is unprivileged: the unit lives
in ~/.config/systemd/user and `systemctl --user` needs no root at all. Only
writing into /etc does, so that is one idempotent action behind one
authentication prompt rather than a series of them.

**The container.** The app runs in the `apex-dev` distrobox; the daemon must
not. It has to see the host's process table to notice a game starting, and a
Flatpak build -- the shipping target -- gets its own PID namespace and never
will. Today that split is free, because distrobox shares /run/user with the
host: `systemctl --user` inside the container drives the *host's* user manager,
and the service it starts runs in the host's mount namespace. Verified by
starting a transient unit from inside the container and comparing its
/proc/<pid>/ns/mnt to the host's -- they match, and differ from the container's.

The udev half has no such luck. There is no system bus inside the container, so
pkexec cannot reach polkit from in there; `escalation()` routes around it with
host-spawn, which does land on the host.

Starting at login and running now are deliberately separate, because systemd
separates them: `enable` is the login switch, `start` is the button.
"""
import collections
import glob
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICE = "flydigid.service"
UNIT_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "systemd", "user")
UNIT_PATH = os.path.join(UNIT_DIR, SERVICE)

# Must match QGuiApplication.setDesktopFileName in gui/main.py, or the window
# is not associated with its launcher and appears under a generic icon.
DESKTOP_NAME = "flydigi-apex5.desktop"
DESKTOP_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    "applications")
DESKTOP_PATH = os.path.join(DESKTOP_DIR, DESKTOP_NAME)

# 72, not 99. TAG+="uaccess" only sets a tag; systemd's 73-seat-late.rules is
# what acts on it, so a file numbered above 73 tags devices nobody will look at
# again. These rules were 99- and silently did nothing.
RULES_NAME = "72-flydigi-apex5.rules"
RULES_SOURCE = os.path.join(ROOT, "udev", RULES_NAME)
RULES_TARGET = os.path.join("/etc/udev/rules.d", RULES_NAME)

# The name it shipped under before. Installing removes it, or the two would
# both be present and only one of them would mean anything.
STALE_RULES = os.path.join("/etc/udev/rules.d", "99-flydigi-apex5.rules")

DAEMON = os.path.join(ROOT, "tools", "flydigid")
SETUP_CLI = os.path.join(ROOT, "tools", "apex5-setup")

# Where a host interpreter might be. Deliberately not sys.executable: the unit
# runs on the host while this module may be imported inside the container or a
# venv, and the backend has no dependencies to lose by using a system one.
# Not a single hardcoded path either -- /usr/bin/python3 is a Fedora fact, not
# a Linux one, and a unit naming an interpreter that is not there fails at
# start with nothing on screen to say why.
HOST_PYTHON_CANDIDATES = ("/usr/bin/python3", "/usr/local/bin/python3",
                          "/bin/python3")

# state values
OK = "ok"
FAIL = "fail"
SKIP = "skip"
UNKNOWN = "unknown"

Check = collections.namedtuple("Check", "id label state detail fix")


# --------------------------------------------------------------------------
# environment

def is_flatpak():
    return bool(os.environ.get("FLATPAK_ID")) or os.path.exists("/.flatpak-info")


def is_container():
    """True inside distrobox/toolbox/podman, which podman marks with this file."""
    return os.path.exists("/run/.containerenv") or os.path.exists("/.dockerenv")


HOST_ROOT = "/run/host"


def host_path(path):
    """Read-side translation for a host path when we are inside a container.

    The container has its own /etc, so reading /etc/udev/rules.d from the
    distrobox reported the rules missing while they were installed on the host
    all along -- and would have sent the app asking for root to fix nothing.
    Both distrobox and Flatpak mount the host root at /run/host.

    Writing needs no equivalent: install_rules() only ever runs on the host,
    under pkexec, where the plain path is the right one.
    """
    if (is_container() or is_flatpak()) and os.path.isdir(os.path.join(HOST_ROOT, "etc")):
        return os.path.join(HOST_ROOT, path.lstrip("/"))
    return path


def host_python():
    """An interpreter that exists on the host, checked rather than assumed.

    From inside a container the candidates are testable through /run/host, so
    this is a real check and not a guess. Falls back to whatever `python3`
    resolves to when running natively.
    """
    for candidate in HOST_PYTHON_CANDIDATES:
        if os.path.exists(host_path(candidate)):
            return candidate
    return shutil.which("python3") or HOST_PYTHON_CANDIDATES[0]


def container_name():
    """The distrobox we are inside, or None.

    podman writes it into /run/.containerenv, so the launcher does not have to
    be told which box the app lives in -- and does not silently keep pointing
    at one that has been renamed or replaced.
    """
    try:
        with open("/run/.containerenv") as fh:
            for line in fh:
                if line.startswith("name="):
                    return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        return None
    return None


def escalation(*args):
    """argv that runs `apex5-setup <args>` as root, from wherever we are.

    Flatpak's --host spawn needs the Development portal permission, which is a
    hole worth avoiding; the app should prefer showing this command to running
    it when `is_flatpak()`.
    """
    cmd = ["pkexec", SETUP_CLI, *args]
    if is_flatpak() and shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", *cmd]
    if is_container():
        # Either will do; neither is guaranteed to be installed, and a plain
        # pkexec from in here fails with a bare "no polkit agent" that says
        # nothing about the real reason. The caller reports the manual
        # commands when the escalation cannot run at all.
        for helper in ("host-spawn", "distrobox-host-exec"):
            if shutil.which(helper):
                return [helper, *cmd]
    return cmd


# --------------------------------------------------------------------------
# systemd

def _systemctl(*args):
    """Run `systemctl --user ...`. Returns (rc, stdout); rc is None if absent.

    `systemctl --user is-system-running` reports "offline" from inside the
    container even though every operation below works there, so unit state is
    read per-unit and that summary is not consulted.
    """
    try:
        proc = subprocess.run(("systemctl", "--user") + args,
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return proc.returncode, proc.stdout.strip()


def unit_text(python=None, daemon=DAEMON):
    """The service unit, generated so the paths match this checkout and host."""
    python = python or host_python()
    return f"""# Generated by flydigi.setup -- edits are overwritten on reinstall.
[Unit]
Description=Flydigi Apex 5 adaptive-trigger daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart={python} {daemon}
# The pad leaves the USB bus when it sleeps and the daemon tolerates its
# absence, so a restart here is for crashes, not for an unplugged controller.
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def unit_installed():
    """True when the unit on disk matches what this checkout would write."""
    try:
        with open(UNIT_PATH) as fh:
            return fh.read() == unit_text()
    except OSError:
        return False


def install_unit():
    """Write the unit and reload. Unprivileged, and reaches the host manager."""
    os.makedirs(UNIT_DIR, exist_ok=True)
    with open(UNIT_PATH, "w") as fh:
        fh.write(unit_text())
    _systemctl("daemon-reload")
    return UNIT_PATH


def remove_unit():
    set_enabled(False)
    set_running(False)
    try:
        os.unlink(UNIT_PATH)
    except OSError:
        pass
    _systemctl("daemon-reload")


def is_enabled():
    """Whether the daemon starts at login."""
    rc, out = _systemctl("is-enabled", SERVICE)
    if rc is None:
        return None
    return out == "enabled"


def is_running():
    rc, out = _systemctl("is-active", SERVICE)
    if rc is None:
        return None
    return out == "active"


def set_enabled(enabled):
    """The login switch. Does not start or stop anything by itself."""
    _systemctl("enable" if enabled else "disable", SERVICE)
    return is_enabled()


def set_running(running):
    """The button. Independent of whether it is enabled at login."""
    _systemctl("start" if running else "stop", SERVICE)
    return is_running()


# --------------------------------------------------------------------------
# the launcher

def enter_command(box):
    """How the host re-enters this container, for the launcher's Exec line.

    distrobox and toolbox spell it differently and do not share a marker file
    worth trusting: this distrobox has /run/.toolboxenv too, because the image
    it is built from is a toolbox one. What distrobox does leave inside is its
    own helper, so that is what is asked about.
    """
    if shutil.which("distrobox-host-exec") or os.path.exists("/usr/bin/distrobox-enter"):
        return f"distrobox enter -n {box} --"
    return f"toolbox run -c {box}"


def desktop_exec():
    """The command the menu entry runs.

    The entry is written into the shared home but launched by the *host's*
    menu, so when the app lives in a container the command has to re-enter it.
    Entering does not reliably land in a particular directory, so the working
    directory is set explicitly rather than assumed.

    `python3` rather than an absolute path: this half runs inside the container
    (or natively), where PATH is the right answer and the host's interpreter is
    not. Quoted as one argument, which the desktop-entry spec allows; no inner
    double quotes, so nothing needs escaping.
    """
    box = container_name()
    inner = f"cd {ROOT} && exec python3 -m gui"
    if box:
        return f'{enter_command(box)} bash -lc "{inner}"'
    return f'bash -lc "{inner}"'


def desktop_text():
    """The menu entry, generated so it matches this checkout and this box."""
    box = container_name()
    comment = ("Configure the Flydigi Apex 5"
               + (f" (runs in the {box} distrobox)" if box else ""))
    return f"""[Desktop Entry]
# Generated by flydigi.setup -- edits are overwritten on reinstall.
Type=Application
Name=Flydigi Apex 5
GenericName=Game Controller Settings
Comment={comment}
Exec={desktop_exec()}
Icon=input-gaming
Terminal=false
Categories=Settings;HardwareSettings;Qt;KDE;
Keywords=gamepad;controller;flydigi;apex;trigger;
StartupNotify=true
"""


def desktop_installed():
    """Whether a menu entry is installed that launches *this* checkout.

    Not a byte comparison against what this side would write. The entry is
    generated differently depending on where it is generated from -- inside the
    container it re-enters the box, on the host it does not -- so comparing
    exactly made the host report a perfectly good entry, written from the
    container, as out of date.
    """
    try:
        with open(DESKTOP_PATH) as fh:
            for line in fh:
                if line.startswith("Exec="):
                    return ROOT in line
    except OSError:
        return False
    return False


def desktop_target_runs_the_app():
    """Whether the command this would write can actually start the app.

    Installing the entry from the host, on a machine where the app only runs in
    the distrobox, would write a launcher pointing at a Python that cannot load
    Kirigami -- an icon that does nothing when clicked, with the reason only
    visible to someone who runs it from a terminal. Inside a container the
    entry re-enters that container by name, so it is right by construction.
    """
    if container_name():
        return True
    try:
        subprocess.run((shutil.which("python3") or host_python(),
                        "-c", "import PySide6"),
                       capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False
    return True


def install_desktop():
    """Write the menu entry. Unprivileged, and lands in the shared home."""
    if not desktop_target_runs_the_app():
        raise RuntimeError(
            "not installing a launcher that would not start: this is the host, "
            "and its Python has no PySide6. Run this from inside the distrobox "
            "the app runs in, or from the app's own Setup page.")
    os.makedirs(DESKTOP_DIR, exist_ok=True)
    with open(DESKTOP_PATH, "w") as fh:
        fh.write(desktop_text())
    os.chmod(DESKTOP_PATH, 0o755)
    # Best effort: KDE notices the file on its own, but a stale mimeinfo cache
    # makes other launchers miss it.
    try:
        subprocess.run(("update-desktop-database", DESKTOP_DIR),
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return DESKTOP_PATH


def remove_desktop():
    try:
        os.unlink(DESKTOP_PATH)
    except OSError:
        pass


# --------------------------------------------------------------------------
# udev

def effective_rules(text):
    """The lines udev acts on -- comments and blank lines removed.

    Compared this way rather than byte-for-byte because the first thing a byte
    comparison did was report the live rules as stale when the only difference
    was an SPDX header added to the checkout afterwards. Sending someone to an
    authentication prompt to install a comment is how a checklist earns being
    ignored.
    """
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def rules_installed():
    """True when the installed rules do what this checkout's copy would."""
    try:
        with open(RULES_SOURCE) as fh:
            want = effective_rules(fh.read())
        with open(host_path(RULES_TARGET)) as fh:
            return effective_rules(fh.read()) == want
    except OSError:
        return False


def install_rules():
    """Copy the rules into /etc and reload udev. Requires root."""
    with open(RULES_SOURCE, "rb") as fh:
        data = fh.read()
    os.makedirs(os.path.dirname(RULES_TARGET), exist_ok=True)
    with open(RULES_TARGET, "wb") as fh:
        fh.write(data)
    os.chmod(RULES_TARGET, 0o644)
    try:
        os.unlink(STALE_RULES)
    except OSError:
        pass
    for argv in (("udevadm", "control", "--reload"), ("udevadm", "trigger")):
        subprocess.run(argv, capture_output=True, timeout=60)
    return RULES_TARGET


# --------------------------------------------------------------------------
# live access -- what the rules are for, asked of the devices themselves

def _writable(path):
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    os.close(fd)
    return True


def _vendor_node():
    """The Apex 5 vendor node, or None when the pad is away.

    Imported lazily so this module stays usable on a machine with no pad and no
    intention of touching one.
    """
    from . import device
    try:
        return device.find_device()
    except device.DeviceNotFound:
        return None


def _dualsense_event_nodes():
    """Event nodes belonging to a DualSense, real or ours.

    The touchpad and motion nodes are the ones systemd leaves at root:input,
    and touchpad-click is reported on the touchpad node -- so an unreadable
    node here is a real, silent breakage rather than a cosmetic one.
    """
    nodes = []
    for path in sorted(glob.glob("/sys/class/input/event*")):
        try:
            with open(os.path.join(path, "device", "name")) as fh:
                name = fh.read().strip()
        except OSError:
            continue
        if name.startswith("Wireless Controller"):
            nodes.append(os.path.join("/dev/input", os.path.basename(path)))
    return nodes


# --------------------------------------------------------------------------
# the checklist

def checks():
    """Every requirement, in the order a person would fix them.

    A check is SKIP when the hardware it concerns is absent -- an unplugged pad
    is not a misconfigured system, and reporting it as a failure would train
    people to ignore the list.
    """
    out = []

    node = _vendor_node()
    if node is None:
        out.append(Check("hidraw", "Controller access", SKIP,
                         "no pad on the bus -- it also leaves when it sleeps", None))
    elif _writable(node):
        out.append(Check("hidraw", "Controller access", OK, node, None))
    else:
        out.append(Check("hidraw", "Controller access", FAIL,
                         f"{node} is not writable", "install-rules"))

    if not os.path.exists("/dev/uhid"):
        out.append(Check("uhid", "Virtual DualSense (/dev/uhid)", FAIL,
                         "missing -- the uhid module is not loaded", None))
    elif _writable("/dev/uhid"):
        out.append(Check("uhid", "Virtual DualSense (/dev/uhid)", OK, "/dev/uhid", None))
    else:
        out.append(Check("uhid", "Virtual DualSense (/dev/uhid)", FAIL,
                         "/dev/uhid is not writable", "install-rules"))

    nodes = _dualsense_event_nodes()
    unreadable = [n for n in nodes if not os.access(n, os.R_OK)]
    if not nodes:
        out.append(Check("input", "DualSense input nodes", SKIP,
                         "no DualSense present -- checked when the relay runs", None))
    elif unreadable:
        out.append(Check("input", "DualSense input nodes", FAIL,
                         "unreadable: " + ", ".join(unreadable), "install-rules"))
    else:
        out.append(Check("input", "DualSense input nodes", OK,
                         f"{len(nodes)} readable", None))

    if os.path.exists(host_path(STALE_RULES)):
        # Reported even when the new file is in place: the old one sorts after
        # systemd's seat rules, so it never did anything, and leaving it around
        # invites the same confusion again.
        out.append(Check("rules", "udev rules", FAIL,
                         "the old 99- rules are still installed and never "
                         "applied — they sort after systemd's seat rules",
                         "install-rules"))
    elif rules_installed():
        out.append(Check("rules", "udev rules", OK, RULES_TARGET, None))
    elif os.path.exists(host_path(RULES_TARGET)):
        out.append(Check("rules", "udev rules", FAIL,
                         "installed copy differs from this checkout", "install-rules"))
    else:
        out.append(Check("rules", "udev rules", SKIP,
                         "not installed -- only needed if a check above fails",
                         "install-rules"))

    if unit_installed():
        out.append(Check("unit", "Daemon installed", OK, UNIT_PATH, None))
    elif os.path.exists(UNIT_PATH):
        out.append(Check("unit", "Daemon installed", FAIL,
                         "unit is out of date", "install-unit"))
    else:
        out.append(Check("unit", "Daemon installed", FAIL, "no unit", "install-unit"))

    if desktop_installed():
        out.append(Check("desktop", "Menu entry", OK, DESKTOP_PATH, None))
    elif os.path.exists(DESKTOP_PATH):
        out.append(Check("desktop", "Menu entry", FAIL,
                         "out of date — it may point at the wrong path or box",
                         "install-desktop"))
    else:
        out.append(Check("desktop", "Menu entry", SKIP,
                         "not installed — start it from a terminal instead",
                         "install-desktop"))

    enabled = is_enabled()
    if enabled is None:
        out.append(Check("enabled", "Start at login", UNKNOWN,
                         "no user service manager here", None))
    else:
        out.append(Check("enabled", "Start at login", OK if enabled else SKIP,
                         "enabled" if enabled else "off", "enable"))

    running = is_running()
    if running is None:
        out.append(Check("running", "Running now", UNKNOWN,
                         "no user service manager here", None))
    else:
        out.append(Check("running", "Running now", OK if running else SKIP,
                         "active" if running else "stopped", "start"))

    return out


def ready():
    """True when nothing is failing. SKIP is a choice, not a fault."""
    return not any(c.state == FAIL for c in checks())
