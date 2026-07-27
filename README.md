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

This project is MIT licensed (see [LICENSE](LICENSE)).

The DualSense report descriptor and feature-report blobs in
`flydigi/ps5_data.py` are generated from
[inputtino](https://github.com/games-on-whales/inputtino), MIT licensed — see
[NOTICE](NOTICE).

Protocol details were obtained by reverse engineering Flydigi's software for
interoperability. Flydigi's game configs and mod binaries are their content and
are fetched at runtime rather than redistributed here.
