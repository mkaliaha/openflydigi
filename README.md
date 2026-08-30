# OpenFlydigi

OpenFlydigi provides native Linux support for the Flydigi Apex 5 controller and
CD2 charging dock. It replaces the controller-facing parts of Flydigi's
Windows-only Space Station application: profiles, button remapping, macros,
sticks, gyro-to-stick mapping, vibration, ForceAdapt adaptive triggers,
lighting, device settings, the 160×80 display, per-game trigger routes, and the
dock's settings and lighting.

The backend under `flydigi/` uses only the Python standard library. The desktop
application adds PySide6, Qt, Kirigami, and Kirigami Addons.

## Supported hardware

| Hardware | Support level | Available features | Important limits |
|---|---|---|---|
| Flydigi Apex 5 | Hardware-validated | All controller features, adaptive triggers, display, game routes, and both virtual-DualSense relays | Display uploads require a wired connection. Steam Input must be disabled for virtual-DualSense games. |
| Flydigi Vader 5 Pro | Experimental, partially implemented | Identity, profiles, buttons, sticks, gyro, vibration, lighting, settings, the v3.2 macro-store implementation, and a derived factory profile | No Vader 5 has been tested. It has trigger motors rather than force triggers and has no display. The trigger-motor editor is not built. The v3.2 macro store is unverified on hardware. The DualSense relay, evdev discovery, permissions, and SDL ignore identifier are not validated end to end. |
| Flydigi CD2 charging dock | Hardware-validated with one noted gap | Identity, four settings, eight generated lighting effects, and picture/GIF sampling onto 162 LEDs; nickname read/write is implemented | Nickname writing is not hardware-verified. The dock is a separate USB device and uses its own selector. |
| Other Flydigi controllers and accessories | Not driven | Devices may be discovered and identified when they use the current transport | Normal configuration commands, including nickname writes, refuse unsupported models. Developer bench tools explicitly documented as ungated can send raw commands. |

The Apex 5 is the reference hardware. Treat Vader 5 support as an invitation to
test and report results, not as a hardware-validated compatibility claim.

## Requirements

### Base requirements

- Linux with hidraw support.
- Python 3.9 or newer.
- Read and write access to the controller or dock's vendor hidraw node.
- The udev rules in `udev/72-flydigi-apex5.rules`. They cover:
  - the Apex 5 vendor interface (`37d7:2501`);
  - the CD2 dock (`37d7:6001`);
  - the display bootloader serial device (`ffaa:5555`);
  - `/dev/uhid`;
  - the virtual DualSense input sub-devices.

Install the rules with:

~~~bash
tools/apex5-setup install-rules
~~~

The command escalates through `pkexec` only for the system rules. The rule file
must sort before systemd's `73-seat-late.rules` so that `TAG+="uaccess"` is
acted on. The setup command also removes the obsolete
`99-flydigi-apex5.rules` and reloads udev.

### Feature-specific requirements

| Feature | Additional requirements |
|---|---|
| Desktop application | Distribution PySide6, Kirigami, Kirigami Addons, and the desktop Qt Quick Controls style |
| Display image/GIF conversion and preview | Pillow |
| Memory-monitor game route | `kernel.yama.ptrace_scope = 0` and execution on the host, not inside a container |
| Virtual DualSense over uhid | Writable `/dev/uhid` |
| Virtual DualSense with USB audio/haptics | The `vhci-hcd` kernel module and one authentication to attach the virtual USB device |
| Standalone haptic-audio bridge | `parec` and `pactl` from pulseaudio-utils |

Steam Input must be disabled for a game using either virtual-DualSense route.
It otherwise masks the physical controller as an Xbox controller and breaks
DualSense semantics.

A packaged Flatpak is not currently supported. Host process discovery, the
memory-monitor route, and relay-process management need a host-side service
before that packaging target is viable.

## Setup

Run commands from the repository root.

1. Inspect the current machine:

   ~~~bash
   tools/apex5-setup
   ~~~

2. Install or refresh the udev rules:

   ~~~bash
   tools/apex5-setup install-rules
   ~~~

3. Optionally install the user service and desktop-menu entry:

   ~~~bash
   tools/apex5-setup install-unit
   tools/apex5-setup enable
   tools/apex5-setup start
   tools/apex5-setup install-desktop
   ~~~

The service unit and menu entry contain the absolute path of this checkout.
Reinstall both after moving the repository. This repository is not currently a
pip-installable application and does not provide PyPI packaging.

## Fetch game data

Game lists, rule files, monitor configs, and mod archives belong to Flydigi and
are not committed. A fresh clone must fetch the data before using the daemon,
`flydigi-auto`, `flydigi-run`, the Forza route, or the populated Games page.

~~~bash
# gamelist.json and configs/forza.json
tools/fetch-configs

# also extract the memory-monitor configs
tools/fetch-configs --monitor-configs

# optionally download every upstream mod archive
tools/fetch-configs --all-mods
~~~

The desktop Games page can fetch the current game list with **Update list**.
Counts in the upstream list are not project constants and may change whenever
the data is refreshed.

## Select a device

List the bus, then inspect one device in full:

~~~bash
tools/flydigi-devices list
tools/flydigi-devices show --device uid:1420
tools/flydigi-devices show --device Couch
~~~

`list` prints a stable key, node, display label, firmware, and battery state.
`show` prints the full model, UID, reported address, nickname, and selector.

The standard registry selector accepts a hidraw path, UID or UID prefix, MAC
address, or exact nickname. It is supported by the day-to-day multi-device
commands:

- `flydigi-charger`
- `flydigi-ds5` and `flydigi-ds5-usbip`
- `flydigi-dsx`, `flydigi-forza`, and `flydigi-monitor`
- `flydigi-mapping`, `flydigi-screen`, and `flydigi-settings`
- `flydigid`

Some Apex-specific tools do not yet select by compatible model:

- `flydigi-haptics` and `flydigi-run` open the first new-protocol controller
  node and apply their Apex capability gate only afterwards. If a Vader sorts
  first, it is refused and a later Apex is not tried. They do not support Vader.
- `flydigi_cmd.py --device` accepts a raw hidraw path only.
- `hid_probe.py` accepts optional positional hidraw paths.
- Bench probes vary and some are ungated; use each tool's `--help` before
  attaching multiple pads.

The desktop application has separate remembered selections for a pad and a
dock. The daemon reads the selected primary pad for single-pad routes; the
pad-side vibration-bind route is applied to every compatible pad.

To exercise multi-device paths without hardware:

~~~bash
FLYDIGI_MOCK_BUS='pad=Desk,pad:f5=Couch,dock:1=Shelf' tools/flydigi-devices list
~~~

The same environment variable works with the daemon and desktop application.
No mock devices appear unless it is set.

## Common workflows

### Inspect the controller

~~~bash
python3 tools/hid_probe.py
tools/flydigi-devices show
tools/flydigi-settings show
tools/flydigi-mapping list
~~~

Reading a profile switches the controller to that profile and audibly re-seats
the Apex 5 trigger motors. A device information read is otherwise non-writing.

### Back up and edit an Apex 5 profile

Back up a profile before writing it:

~~~bash
tools/flydigi-mapping backup 0 profile0.bin
tools/flydigi-mapping set 0 m1 a --save
tools/flydigi-mapping macro-record 0 m1 --seconds 8 --save
tools/flydigi-mapping gyro 0 right --key m1 --sensitivity 60 --save
~~~

`--save` commits the change to flash. Without it, the change is working memory
and is lost when the pad sleeps, changes profile, or powers off.

The current CLI backup format is the raw 840-byte profile blob. On a Vader 5,
macros live in a separate v3.2 macro store, so CLI backup/restore does **not**
back up or restore those macros. Use the desktop application's packed profile
backup for the complete v3.2 profile until the CLI adopts the same format.

`tools/flydigi-mapping apply SLOT` is not a usable success-status command for a
Vader 5: it switches the profile, then exits nonzero when it reaches the
Apex-only force-trigger engagement step. Use the desktop profile switch for a
Vader 5 until the CLI separates profile switching from trigger engagement.

Safety-sensitive reset commands are deliberately separate:

~~~bash
# restore one slot by writing this model's factory profile
tools/flydigi-mapping reset 0

# command 175: reset every profile and every profile name
tools/flydigi-mapping reset-all
~~~

`reset-all` is destructive, resets all four XInput profiles, removes their
names, and cannot be undone. Both commands ask for confirmation unless their
explicit bypass option is supplied. Lighting is not part of a profile restore
and is left unchanged.

### Device settings

~~~bash
tools/flydigi-settings sleep 30
tools/flydigi-settings sleep never
tools/flydigi-settings show
~~~

Every settings write except `restart` is followed by a full read-back. The
`report-rate` and `xbox-home` commands require `--i-know` because their behavior
is unmeasured or ambiguous on the reference hardware.

### Per-game routes

After fetching game data:

~~~bash
# daemon and per-game policy
tools/flydigid
tools/flydigi-auto on "Death Stranding"

# Steam launch wrapper for vibration-preset games
/path/to/flydigi-run "DEATH STRANDING 2" -- %command%

# Forza Data Out: configure 127.0.0.1:5300 in the game
tools/flydigi-forza

# a DualSenseX-compatible mod
tools/flydigi-dsx

# game-memory route; probe offsets before enabling writes
tools/flydigi-monitor --probe configs/monitor/<game>.json
tools/flydigi-monitor configs/monitor/<game>.json
~~~

The Forza parser has built-in layouts for 311-byte packets at offset 0 and
324-byte packets at offset 12. Use `--accept LEN:OFFSET` only after verifying a
newer packet layout.

Memory-monitor pointer chains are game-build-specific. Probe after a game
update before allowing the route to write effects.

### Virtual DualSense

The desktop application's DualSense switch starts the USB relay. The equivalent
manual commands are:

~~~bash
# USB/IP + vhci-hcd, including haptic audio
sudo tools/flydigi-ds5-usbip --haptics --motors

# uhid input/effects only, without haptic audio
tools/flydigi-ds5
~~~

Start the relay before the game. For the validated Apex 5 path, add both SDL
ignore-variable spellings to the game's launch options:

~~~bash
SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 SDL_JOYSTICK_IGNORE_DEVICES=0x37d7/0x2501 %command%
~~~

The physical Apex 5 may sleep and leave the USB bus while the virtual DualSense
stays attached. Reconnection, input recovery, trigger-state recovery, and
haptic-audio recovery have been confirmed in a real game.

The launch identifier and complete relay path are not validated for Vader 5.
Do not treat the Apex-specific launch option as a confirmed Vader instruction.

### Display

The Apex 5 display upload must be wired. A wireless upload can switch the
display chip into bootloader mode without exposing the serial device needed to
finish the transfer.

~~~bash
tools/flydigi-screen status
tools/flydigi-screen show photo.jpg
tools/flydigi-screen animate spin.gif
tools/flydigi-screen off
~~~

Encoding and preview require Pillow. Uploads take about 25 seconds per frame,
and the controller reboots after an upload. Keep animations short.

### CD2 charging dock

~~~bash
tools/flydigi-charger show
tools/flydigi-charger light pulse
tools/flydigi-charger light breath --colour ff0055 --brightness 80
tools/flydigi-charger lighting-sync on
tools/flydigi-charger list
~~~

Dock lighting effects are generated as frame data and uploaded; they are not a
single firmware mode byte. Uploads therefore take several seconds.

## Command reference

| Command | Purpose |
|---|---|
| `tools/apex5-setup` | Check and manage udev rules, user service, autostart, and desktop entry |
| `tools/fetch-configs` | Fetch the current upstream game list and optional configs/mods |
| `tools/flydigi-devices` | List, inspect, and nickname attached pads and docks |
| `tools/flydigi-mapping` | Profiles, backup/restore, remapping, macros, gyro mapping, single-slot reset, all-slot reset, and Switch-bank copy |
| `tools/flydigi-settings` | Read and write device settings |
| `tools/flydigi-screen` | Inspect, encode, preview, and upload Apex 5 display content |
| `tools/flydigi-charger` | Read and configure the CD2 dock |
| `tools/flydigid` / `tools/flydigi-auto` | Per-game detection, route policy, and automatic application |
| `tools/flydigi-run` | Apex-specific Steam launch wrapper for vibration presets |
| `tools/flydigi-forza` | Forza Data Out telemetry route |
| `tools/flydigi-dsx` | DSX UDP listener |
| `tools/flydigi-monitor` | Game-memory route |
| `tools/flydigi-ds5` | Virtual DualSense over uhid |
| `tools/flydigi-ds5-usbip` | Virtual USB DualSense with optional haptic audio |
| `tools/flydigi-haptics` | Apex-specific bridge from a real DualSense audio sink to grip motors |
| `tools/flydigi_cmd.py` | Ungated manual vendor-command bench tool; raw node selection only |

Developer generators, simulators, and hardware probes also live under `tools/`.
Their help text is the authoritative command reference; they are not part of
the normal setup path.

## Desktop application

Install PySide6 and Kirigami from the same distribution. Do not combine a PyPI
PySide6 wheel, which bundles its own Qt, with distribution Kirigami libraries.

For Fedora/KDE:

~~~bash
sudo dnf install python3-pyside6 kf6-kirigami kf6-kirigami-addons \
                 kf6-qqc2-desktop-style
python3 -m gui
~~~

The application provides device selection, profiles, remapping, macros, sticks,
gyro, vibration, triggers, lighting, display, game routes, DualSense mode, dock
configuration, and system setup. Pages that require adaptive triggers or a
display are hidden on models without that capability.

Vader-specific pages remain experimental. In particular, the trigger-motor
editor is not implemented and DualSense mode is not hardware-validated.

Development setup, QML architecture, type generation, and static checks are in
[gui/README.md](gui/README.md). The current `pyproject.toml` is PySide tooling
metadata, not a complete installable-package definition. Its file list is also
incomplete and must be synchronized with the current models, pages, components,
and `gui/i18n.py` before relying on `pyside6-project build` or its static checks.

## Tests

Tests are plain Python scripts rather than a single test-runner suite.

Run every backend test, including newly added files, with:

~~~bash
status=0
for t in tests/test_*.py; do
    case "$t" in
        tests/test_models.py|tests/test_qml.py|tests/test_shell.py) continue ;;
    esac
    python3 "$t" || status=1
done
test "$status" -eq 0
~~~

The loop continues after a failure so every backend script runs, then returns a
nonzero status if any script failed. Two backend scripts do not yet satisfy the
intended hardware-free contract:

- `tests/test_device.py` searches for a real controller while constructing its
  lock-test wrapper.
- `tests/test_waits.py` searches for a real dock while constructing its fake
  dock subclass.

They fail on a machine with neither device attached. This is a test-harness
defect, not a runtime hardware requirement. The remaining backend tests use
Python only.

The Qt-dependent tests are:

- `tests/test_models.py`: distribution PySide6;
- `tests/test_shell.py` and `tests/test_qml.py`: PySide6 and Kirigami.

`tests/test_qml.py` discovers the `tests/qml/tst_*.qml` cases. Exact commands,
`tools/generate-qmltypes`, and qmllint setup are documented in
[gui/README.md](gui/README.md).

## Documentation

| Document | Contents |
|---|---|
| [PROGRESS.md](PROGRESS.md) | Current feature status, validation level, limitations, and roadmap |
| [PROTOCOL.md](PROTOCOL.md) | Wire protocol and hardware-verification results |
| [docs/findings-profile-blob.md](docs/findings-profile-blob.md) | Profiles, mappings, sticks, gyro, trigger blocks, and macros |
| [docs/device-settings.md](docs/device-settings.md) | Device settings, battery, vibration bind, lighting config, and command inventory |
| [docs/findings-steam.md](docs/findings-steam.md) | Steam interaction, device takeover, and locking |
| [docs/findings-games.md](docs/findings-games.md) | Game detection, routes, and validation notes |
| [docs/findings-haptics.md](docs/findings-haptics.md) | Virtual DualSense, USB/IP, and haptic audio |
| [docs/findings-screen.md](docs/findings-screen.md) | Display upload paths, safeguards, and settings |
| [docs/findings-other-devices.md](docs/findings-other-devices.md) | Device codes, other Flydigi hardware, and dock findings |
| [docs/findings-desktop-app.md](docs/findings-desktop-app.md) | Apply/save behavior and desktop implementation findings |
| [docs/third-party-mods.md](docs/third-party-mods.md) | Unsupported third-party-mod route |

## Flydigi content not shipped by this repository

No decompiled source, extracted assemblies, game configs, mod binaries, or
Flydigi assets are tracked. The following paths are local and ignored:

- `gamelist.json`, `configs/`, and `mods/`, fetched on demand;
- `decompiled/`, `bundle/`, `asar/`, `bin/`,
  `installer-listing.txt`, and `work/`, produced during local protocol research.

These are not part of the public repository layout and are not required for
normal controller use, except for the fetched game data needed by game routes.

## Disclaimer

This is an independent project. It is **not affiliated with, authorized by, or
endorsed by Flydigi**. “Flydigi,” “Space Station,” and product names are used
only to identify interoperable hardware and software; their trademarks belong
to their respective owners.

The protocol was determined by reverse engineering for interoperability. Laws
vary by jurisdiction; check what applies where you are.

No Flydigi code, assembly, asset, game config, or mod binary is redistributed.
`tools/fetch-configs` contacts Flydigi's public API only when a user runs it and
stores the result locally.

The software is provided as-is, without warranty. Writing configuration or
factory data to a controller carries the ordinary risk of writing device flash.

## Licensing

The repository follows [REUSE](https://reuse.software/). Code carries inline
SPDX headers; prose and generated/non-source files are annotated in
`REUSE.toml`; complete license texts are under `LICENSES/`.

| Paths | License |
|---|---|
| `flydigi/`, `tools/`, and non-Qt tests | MIT |
| `gui/`, Qt-dependent tools/tests, QML tests, and `pyproject.toml` | GPL-3.0-or-later |
| `README.md`, `PROGRESS.md`, and `docs/` | MIT |
| `PROTOCOL.md` | CC0-1.0 |
| `udev/` and `pipewire/` | CC0-1.0 |
| `tests/qml/four-frames.gif` | CC0-1.0 |
| Repository metadata listed in `REUSE.toml` | CC0-1.0 |

The Qt-linked desktop layer may import the dependency-free MIT backend; the
backend does not import the desktop layer. See [LICENSE](LICENSE) for the
licensing rationale and [NOTICE](NOTICE) for third-party attribution.
