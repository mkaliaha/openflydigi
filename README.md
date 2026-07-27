# Flydigi Apex 5 on Linux

Native Linux support for the Flydigi Apex 5's **ForceAdapt adaptive triggers** —
the one major feature that otherwise requires Flydigi's Windows-only Space
Station app.

Everything here is pure Python with **no dependencies**.

## Status

Adaptive triggers work. Force effects and trigger haptics are both driven from
Linux over `/dev/hidraw`, verified on hardware. See [PROTOCOL.md](PROTOCOL.md)
for the wire protocol and [PROGRESS.md](PROGRESS.md) for project state.

| Approach | Games | State |
|---|---|---|
| Vibration bind (config only) | 33 | Working, automated |
| Forza telemetry | 4 | Built, self-tested |
| DSX protocol listener | third-party mods | Built, self-tested |
| Game-memory monitor | 31 | Built, offsets unvalidated |
| Virtual DualSense | 15 | Foundation working |

## Quick start

```bash
# what the controller exposes
python3 tools/hid_probe.py

# talk to it directly
python3 tools/flydigi_cmd.py info
python3 tools/flydigi_cmd.py race right --stroke 40 --resistance 30
python3 tools/flydigi_cmd.py normal both

# auto-apply per-game config when a game starts
python3 tools/flydigid

# per-game via Steam launch options
tools/flydigi-run "DEATH STRANDING 2" -- %command%

# Forza: enable Data Out (127.0.0.1:5300) in game, then
tools/flydigi-forza

# any DualSenseX-compatible mod
tools/flydigi-dsx

# game-memory driven effects (Dark Souls, Cyberpunk, Elden Ring, ...)
tools/fetch-configs --monitor-configs
tools/flydigi-monitor --probe configs/monitor/<game>.json   # check offsets first
tools/flydigi-monitor configs/monitor/<game>.json

# present the pad to games as a DualSense (triggers, gyro, battery)
tools/flydigi-ds5
#   set per-game: SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 %command%
#   and disable Steam Input for that game
```

## Requirements

- Linux with `hidraw` (`/dev/hidraw*` readable — usually already the case)
- Python 3.9+
- For the memory monitor: `kernel.yama.ptrace_scope = 0`
- For the virtual DualSense: `/dev/uhid` writable

## Reproducing the analysis

The repository deliberately contains **no Flydigi content** — no decompiled
sources, extracted assemblies, mod binaries, or config files. To reproduce:

1. Fetch the game list and per-game configs (public, unauthenticated API):
   ```bash
   python3 tools/fetch-configs
   ```
2. For protocol work, decompile Space Station yourself. The steps are recorded
   in [PROGRESS.md](PROGRESS.md) — install the Windows app under Wine, unpack
   the .NET single-file bundle with `sfextract`, decompile with `ilspycmd`.

## Licensing

Per-file, following [REUSE](https://reuse.software/) — every file carries an
SPDX header, full texts are in `LICENSES/`, and `reuse lint` verifies it.

| | |
|---|---|
| `flydigi/`, `tools/`, `tests/` | MIT |
| `README.md`, `PROGRESS.md`, `docs/` | MIT |
| `PROTOCOL.md` | CC0-1.0 |
| `udev/`, `pipewire/` | CC0-1.0 |
| `gui/` | GPL-3.0-or-later |

The protocol implementation is MIT because reuse is welcome without conditions —
take it into any project, under any license. Only the desktop frontend is
copyleft, since it links Qt and copyleft costs nothing there. The GUI importing
`flydigi/` does not make `flydigi/` GPL; those files stay independently
reusable. See [LICENSE](LICENSE) for the full reasoning.

The DualSense report descriptor and feature-report blobs in
`flydigi/ps5_data.py` are generated from
[inputtino](https://github.com/games-on-whales/inputtino), MIT licensed — see
[NOTICE](NOTICE).

## Building a release

The backend has no dependencies, so a source release needs nothing special. The
GUI adds PySide6, which is LGPLv3 — that only creates obligations if you ship
Qt yourself.

- **Source / PyPI / AUR / COPR** — you distribute no Qt at all (pip or the
  distro provides it), so there is nothing to discharge.
- **AppImage** — bundles Qt, so it is conveyed as a whole under GPL-3.0. Include
  `LICENSES/` in the image, name the bundled PySide6 and Qt versions in the
  release notes with a link to their sources, and use PyInstaller `--onedir`
  rather than `--onefile` so the libraries stay swappable. Corresponding source
  is satisfied twice over: it is a Python app, so the source is inside the
  bundle, and the git tag sits next to the download.
- **Flatpak** — cleanest licensing (Qt comes from `org.kde.Platform`) but the
  sandbox cannot see host PIDs, so `flydigi-monitor` cannot work from inside
  one. Only viable once the GUI talks to a host daemon over D-Bus.

Protocol details were obtained by reverse engineering Flydigi's software for
interoperability. Flydigi's game configs and mod binaries are their content and
are fetched at runtime rather than redistributed here.
