# Flydigi Apex 5 on Linux

Native Linux support for the Flydigi Apex 5: **ForceAdapt adaptive triggers**, profiles and
button remapping, macros, sticks, vibration, lighting, the pad's device settings and its
160x80 screen — without Flydigi's Windows-only Space Station app.

`flydigi/` is pure Python with **no dependencies**; only the desktop app needs Qt, and every tool
that talks to the pad runs on a machine without it.

Force effects and trigger haptics both run over `/dev/hidraw`, verified on hardware. Six routes
drive them, from a vibration bind the pad plays by itself to a virtual DualSense that carries a
game's PS5 haptic audio to the pad's motors. The **160x80 screen** takes pictures and animations
over the same serial route Space Station uses, plus the always-on display and status bar: ~25 s
a frame, wired connection only.

[PROTOCOL.md](PROTOCOL.md) has the wire protocol, [PROGRESS.md](PROGRESS.md) the project state
and the per-route tier table, [docs/](docs/) the long-form findings behind each route.

## Requirements

- Linux with `hidraw` (`/dev/hidraw*` readable — usually already the case)
- Python 3.9+
- The udev rules in `udev/`, installed with `tools/apex5-setup install-rules`: they tag the
  vendor hidraw node (37d7:2501), the screen's `root:dialout` bootloader tty (ffaa:5555),
  `/dev/uhid`, and the virtual DualSense's input sub-devices. Without the last, touchpad-click
  is silently dead — that event is reported on the touchpad node, not the gamepad node; without
  the tty rule a screen upload switches the pad into its bootloader and then cannot open the
  port. The file must stay numbered below 73, or systemd's `73-seat-late.rules` runs before
  `TAG+="uaccess"` is set and nothing ever acts on it. `install-rules` also deletes a leftover
  `99-flydigi-apex5.rules`, which never applied for the same reason; `check` fails while one is
  present, even with the new file in place.
- For the memory monitor: `kernel.yama.ptrace_scope = 0`, and a host process — the route cannot
  run from inside a container at all (see [Desktop app](#desktop-app))
- For the virtual DualSense with haptics: the `vhci-hcd` module (`=m` on every distribution
  checked), and one authentication to attach the virtual USB device
- For the virtual DualSense without haptics: `/dev/uhid` writable
- For either DualSense route: Steam Input **off** for that game — it masks the pad as an Xbox
  controller and breaks DS5 semantics
- For `tools/flydigi-screen show`, `animate`, `convert` and `preview`: Pillow
  (`pip install pillow`); `check` does not need it, and neither does `flydigi/screen.py`.
- For `tools/flydigi-haptics`: `parec` and `pactl` from pulseaudio-utils. `pw-record` cannot
  substitute — it prepends a file header.
- `pkexec` for `install-rules` and the app's DualSense switch. Inside a Flatpak that runs through
  `flatpak-spawn --host`, and inside a distrobox through `host-spawn` or `distrobox-host-exec`,
  since pkexec cannot reach polkit from in there.

**Every Flydigi pad opens the same way.** `find_device` matches on the vendor id and the vendor
collection's report-descriptor prefix, which every model in the range shares, so a Vader 4 Pro
opens exactly like an Apex 5. Profile and settings writes are therefore gated on a command-1
`DeviceType` read that refuses anything but an Apex 5 (`k5`). `flydigi-mapping`,
`flydigi-settings` and the app call that gate once per connection, so their reads are refused
too; the trigger-effect and screen tools are ungated, and `flydigi/` itself gates nothing —
the caller decides. With two Flydigi pads attached, nothing chooses between them by itself:
`find_device` returns the first match in sorted-by-name `/dev/hidraw*` order (`hidraw10` before
`hidraw2`). `Controller(path=...)` takes a node, but of the day-to-day tools only
`tools/flydigi_cmd.py` takes a `--device` path.

## Quick start

Run from the repository root.

```bash
# what the controller exposes
python3 tools/hid_probe.py

# talk to it directly
python3 tools/flydigi_cmd.py info
python3 tools/flydigi_cmd.py race right --stroke 40 --resistance 30
python3 tools/flydigi_cmd.py normal both

# the profiles stored in the pad: remapping, macros, backup/restore
tools/flydigi-mapping list              # --profiles N, before the subcommand, scans N slots (4)
tools/flydigi-mapping backup 0 profile0.bin      # back up before writing; this is the only copy
#   the file is the raw blob, no header; restore only checks its length against the pad's
tools/flydigi-mapping set 0 m1 a --save          # --save commits to flash
#   --turbo N repeats the target while the key is held, --turbo-toggle latches it instead
tools/flydigi-mapping macro-record 0 m1 --seconds 8 --save

# the pad's own device settings
tools/flydigi-settings show             # the whole block, one read
tools/flydigi-settings sleep 30         # minutes, or `never`
#   report-rate and xbox-home need --i-know

# auto-apply per-game config when a game starts
tools/flydigid                          # --interval 1.0 s; --reassert N re-applies every
#                                         N seconds against Steam Input, 0 (default) is off
#   it logs to stdout; under its unit that is the user journal, journalctl --user -u flydigid
tools/flydigi-auto on "Death Stranding"
#   only the vibration route acts by itself; routes that spawn a process, read another
#   process's memory or take the controller over default to off. State: ~/.config/flydigi/games.json

# per-game via Steam launch options -- vibration-preset games only
/path/to/flydigi-run "DEATH STRANDING 2" -- %command%

# Forza: enable Data Out (127.0.0.1:5300) in game, then
tools/flydigi-forza
tools/flydigi-forza --accept 331:12     # accept a Data Out packet size the parser does not know
#   yet. Built in: 311 (offset 0) and 324 (offset 12), which covers every shipped Forza.
#   The packet's field layout is in flydigi/forza.py.

# any DualSenseX-compatible mod, on UDP 127.0.0.1:7878
tools/flydigi-dsx
tools/flydigi-dsx --forward 8787        # also relay onward to the port Flydigi's own software uses

# game-memory driven effects (Dark Souls, Cyberpunk, Elden Ring, ...)
tools/fetch-configs --monitor-configs
tools/flydigi-monitor --probe configs/monitor/<game>.json   # check offsets first
tools/flydigi-monitor configs/monitor/<game>.json
#   pointer chains are per-build: a game update breaks a config until Flydigi ship new offsets

# present the pad to games as a DualSense (triggers, gyro, battery, haptics)
# -- the desktop app's DualSense switch does the same behind one authentication
sudo tools/flydigi-ds5-usbip --haptics --motors
#   root is given back as soon as the USB attach is done
#   set per-game: SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 %command%
#   start the game *after* this
#   the Apex 5 may sleep and wake as it likes: it leaves the USB bus when it
#   sleeps, but the virtual DualSense stays attached and the game keeps it
tools/flydigi-ds5                       # the same without haptics, no root,
#                                         for a machine with no vhci-hcd

# the 160x80 screen
tools/flydigi-screen status
tools/flydigi-screen show photo.jpg     # ~25s; the pad reboots itself after
tools/flydigi-screen animate spin.gif   # ~25s per frame, so keep it short
tools/flydigi-screen off                # blank the panel -- Space Station cannot

# system setup: udev rules, the daemon's unit, autostart, the menu entry
tools/apex5-setup                       # the checklist
tools/apex5-setup install-rules         # the one subcommand that needs root
#   there is no install step: the unit and the menu entry hard-code this checkout's path, so
#   moving the repository means writing both again
```

| Tool | What it does |
|---|---|
| `tools/apex5-setup` | `check` (default), `install-unit`/`remove-unit` (a `flydigid.service` user unit in `~/.config/systemd/user`), `enable`/`disable`/`start`/`stop`, `install-desktop`/`remove-desktop` (`flydigi-apex5.desktop`), `install-rules` — the same functions the app's Setup page drives in-process |
| `tools/fetch-configs` | Flydigi's game list, the Forza rule config, the monitor configs, the mod archives |
| `tools/flydigi_cmd.py` | one-off vendor commands: device info, trigger effects, rumble |
| `tools/flydigi-auto` | which games the daemon may act on by itself, and which route it takes |
| `tools/flydigid` | daemon: detect a running game, apply its route, reset on exit |
| `tools/flydigi-ds5` | virtual DualSense over uhid |
| `tools/flydigi-ds5-usbip` | virtual DualSense over usbip + vhci, with haptic audio |
| `tools/flydigi-dsx` | DSX listener, UDP 127.0.0.1:7878 |
| `tools/flydigi-forza` | Forza Data Out telemetry through the rule engine |
| `tools/flydigi-haptics` | Apex 5 rumble driven from a DualSense's haptic-audio sink |
| `tools/flydigi-mapping` | profiles: `list`/`show`/`set`/`clear`/`rename`/`apply`, `backup`/`restore`, macro record and bind |
| `tools/flydigi-monitor` | trigger effects from game memory, per Flydigi's XGameMonitor configs |
| `tools/flydigi-run` | Steam launch wrapper for one named game |
| `tools/flydigi-screen` | the 160x80 screen: pictures, animations, always-on, status bar |
| `tools/flydigi-settings` | the pad's device settings block: sleep timer, quick-switch, stick debounce/rebound/auto-calibration, precision, sensitivity, status bar, always-on, restart. Every write but `restart` is followed by a read of the whole block |
| `tools/hid_probe.py` | what the pad exposes on the HID bus |

The other eleven files in `tools/` are not day-to-day: seven hardware probes, the offline
simulators `forza-simulate` and `haptics-simulate`, `gen_ds5_usb.py` (regenerates
`flydigi/ds5_usb.py` from descriptors captured off a real DualSense) and `generate-qmltypes`
(GUI build tooling). They are listed individually in [PROGRESS.md](PROGRESS.md#repo-contents).

## Desktop app

Profiles, remapping, macros, sticks, vibration, triggers, lighting, device settings, the screen
and per-game routes — plus a **DualSense** switch that turns the virtual-DualSense relay on for
the whole system, and a **Setup** page for the daemon's unit, autostart, menu entry and udev
rules. QML on Kirigami:

```bash
# Fedora / KDE
sudo dnf install python3-pyside6 kf6-kirigami kf6-kirigami-addons \
                 kf6-qqc2-desktop-style
python3 -m gui
```

**Not a pip install.** PySide6 from PyPI bundles its own Qt, built with different
private-symbol versioning from the distribution's, and Kirigami will not load against it — the
two are mutually incompatible, in both directions. The app needs the distribution's PySide6,
built against the same Qt as Kirigami. On an immutable system a `distrobox` with those packages
works without layering anything; see [gui/README.md](gui/README.md).

Measured from inside the distrobox, every route works except the memory monitor, which needs a
cross-process read no container grants; the per-route table is in
[docs/findings-games.md](docs/findings-games.md).

## Tests

Plain scripts, no test runner. The backend tests need nothing but Python:

```bash
python3 tests/test_device.py       # and every other tests/test_*.py
```

Three are Qt-dependent instead: `tests/test_models.py` needs the distribution's PySide6, and
`tests/test_shell.py`, `tests/test_qml.py` and the ten `tests/qml/tst_*.qml` cases the last of
them drives need Kirigami as well, so they run wherever the app does. Their invocations, and
`tools/generate-qmltypes`, are in [gui/README.md](gui/README.md).

## Flydigi content this repository does not ship

No decompiled sources, extracted assemblies, mod binaries or game configs are here (see
[Disclaimer](#disclaimer)). Everything gitignored is reproducible: `gamelist.json`, `configs/`
and `mods/` are fetched on demand, and `decompiled/`, `bundle/`, `asar/`, `bin/`,
`installer-listing.txt` and the `work/` scratch tree are produced locally. To reproduce:

1. Fetch the game list, configs and mods from Flydigi's public, unauthenticated API
   (`GET https://api.flydigi.com/pc/adapter_trigger/list`):
   ```bash
   tools/fetch-configs                    # gamelist.json + configs/forza.json
   tools/fetch-configs --monitor-configs  # those, plus configs/monitor/*.json
   tools/fetch-configs --all-mods         # those, plus mods/, ~44 MB
   ```
2. For protocol work, decompile Space Station yourself; the toolchain and the steps are in
   [PROGRESS.md](PROGRESS.md).

## Disclaimer

This is an independent project. It is **not affiliated with, authorised by, or
endorsed by Flydigi**. "Flydigi", "Space Station" and "Apex 5" are used only to
identify the hardware and software this project interoperates with; any
trademarks belong to their respective owners.

The protocol was determined by reverse engineering Flydigi's own software for
the sole purpose of making hardware you already own work on Linux — an
interoperability purpose expressly permitted in some jurisdictions (in the EU,
Directive 2009/24/EC Art. 6, which Art. 8 protects from contractual override)
and treated differently in others. Check what applies where you are.

No Flydigi code, assembly, asset, game config or mod binary is included or
redistributed here. `tools/fetch-configs` contacts Flydigi's public API only
when you run it, and stores what it fetches locally without republishing it.

Provided as-is, with no warranty. Writing configuration to a controller carries
the ordinary risk of writing to any device's flash.

## Licensing

Per-file, following [REUSE](https://reuse.software/): code carries inline SPDX headers, prose
and `gui/` are annotated in `REUSE.toml`, full texts are in `LICENSES/`, and `reuse lint`
verifies it.

| | |
|---|---|
| `flydigi/`, `tools/`, `tests/` | MIT, except the Qt-dependent files — `tools/generate-qmltypes`, `tests/test_models.py`, `tests/test_qml.py`, `tests/test_shell.py`, `tests/qml_harness.py`, `tests/qml/*.qml` — which are GPL-3.0-or-later. `tests/qml/four-frames.gif` is CC0-1.0 |
| `README.md`, `PROGRESS.md`, `docs/` | MIT |
| `PROTOCOL.md` | CC0-1.0 |
| `udev/`, `pipewire/` | CC0-1.0 |
| `gui/` | GPL-3.0-or-later |
| `pyproject.toml` | GPL-3.0-or-later — it describes `gui/` to PySide6's tooling |
| `.gitignore`, `LICENSE`, `NOTICE`, `REUSE.toml` | CC0-1.0 |

Copyleft is confined to what links Qt: `gui/` may import `flydigi/` and never the reverse, which
is what keeps the backend independently reusable and dependency-free. See [LICENSE](LICENSE) for
the reasoning and [gui/README.md](gui/README.md) for the import rule.

The DualSense report layouts in `flydigi/ds5.py` follow
[inputtino](https://github.com/games-on-whales/inputtino), MIT licensed, and the
placeholder Bluetooth addresses in `flydigi/ds5_usb.py` are inputtino's public
ones. The descriptors and feature-report blobs themselves are read from
hardware — see [NOTICE](NOTICE).

## Building a release

The GUI adds PySide6, which is LGPLv3 — that only creates obligations if you
ship Qt yourself.

- **Source / PyPI / AUR / COPR** — you distribute no Qt at all (pip or the
  distro provides it), so there is nothing to discharge.
- **AppImage** — bundles Qt, so it is conveyed as a whole under GPL-3.0. Include
  `LICENSES/` in the image, name the bundled PySide6 and Qt versions in the
  release notes with a link to their sources, and use PyInstaller `--onedir`
  rather than `--onefile` so the libraries stay swappable. Corresponding source
  is satisfied twice over: it is a Python app, so the source is inside the
  bundle, and the git tag sits next to the download.
- **Flatpak** — cleanest licensing (Qt comes from `org.kde.Platform`) but the daemon needs the
  host process table and the memory route needs a host-side cross-process read, which no
  container grants. Only viable once the GUI talks to a host daemon over D-Bus.
