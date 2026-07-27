# Apex 5 on Linux — project state

Goal: adaptive triggers (ForceAdapt) working on Linux without the Windows Space Station app.

## Status: core problem solved

Trigger force effects and trigger haptics are both driven successfully from a native Linux
Python script over `/dev/hidraw4`. Physically confirmed on hardware. See `PROTOCOL.md`.

What remains is **coverage and ergonomics**, not feasibility.

## Environment

- Host: Aurora DX (nvidia-open), Fedora 44 atomic, KDE/Wayland
- `wine-arch` distrobox (Arch + wine-staging 11.14, winetricks, innoextract, dotnet-sdk 10,
  ilspycmd, sfextract, nodejs). Created with `distrobox create --name wine-arch --image archlinux:latest --nvidia`
- Wine prefix: `~/.local/share/wineprefixes/flydigi` — Space Station 4.2.1.4 installs and runs
  (UI connects to its service over the named pipe), but **does not detect the controller**
  under Wine. Not needed; kept for reference only.
- Controller: wired. `hidraw3` = keyboard/mouse composite, `hidraw4` = vendor command interface.
  Nodes are `0666`, no udev rule needed.

## Repo contents

| Path | What |
|---|---|
| `PROTOCOL.md` | Full wire protocol + hardware verification results |
| `flydigi/` | Library — `device.py` (transport), `effects.py` (commands), `games.py` (game list), `forza.py` (telemetry + rule engine) |
| `tools/flydigi-forza` | Forza driver — UDP 5300 → rules → triggers (`--dump` for telemetry only) |
| `tools/flydigi-dsx` | DSX protocol listener on UDP 7878 — drives triggers from any DSX-compatible mod |
| `tools/flydigi-monitor` | Memory-reading driver using Flydigi's XGameMonitor configs (`--probe` to debug offsets) |
| `flydigi/uhid.py` | Pure-Python `/dev/uhid` binding (no dependencies) — creates kernel-side HID devices |
| `flydigi/ps5_data.py` | Generated DualSense descriptor + feature blobs (from MIT inputtino) |
| `tools/gen_ps5_data.py` | Regenerates the above from inputtino's `ps5.hpp` |
| `work/ref/inputtino/` | MIT reference clone — DS5 output report layout, canned feature reports |
| `tests/` | `test_forza.py` (7), `test_dsx.py` (9), `test_monitor.py` — all pass without hardware |
| `tools/forza-simulate` | Synthetic telemetry generator, for testing without the game |
| `tests/test_forza.py` | Self-test for the parser and rule engine (no hardware needed) |
| `configs/forza.json` | Flydigi's own 15-rule Forza config, reused verbatim |
| `tools/flydigid` | Polling daemon — auto-detects a running game and applies its config |
| `tools/flydigi-run` | Steam launch wrapper — `flydigi-run "<name>" -- %command%` |
| `tools/hid_probe.py` | Passive HID descriptor dump (writes nothing) |
| `tools/flydigi_cmd.py` | Manual command tool — `info`, `race`, `normal`, `bind`, `rumble`, `game`, `k6*`, `raw` |
| `gamelist.json` | All 94 games + per-game configs (from the public API) |
| `mods/` | All 46 downloadable mod zips (44 MB) |
| `bundle/` | 250 .NET assemblies extracted from `SpaceStationService.exe` |
| `decompiled/` | C# source for AdapterTriggerService, ControllerSdk, Hid, Basic, SpaceStationService |
| `asar/` | Extracted Electron app (`main.pretty.js` is the beautified main process) |

## Implementation tiers

| Tier | Games | Mechanism | State |
|---|---|---|---|
| 1. Vibration bind | 33 | cmd `82` SyncWithGrip, config from API, driven by game rumble | **Done & automated** — verified in Death Stranding 2, triggers buzz with in-game rumble, daemon auto-detects and applies |
| 2. ForzaDualSense | 4 | Forza "Data Out" UDP telemetry → JSON rule engine → cmd `81` | **Built & self-tested.** Native reimplementation; 7/7 tests pass and a simulated drive drives real effects. Awaiting in-game validation |
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | **Built & self-tested.** Chain walker verified against a real process via `/proc/<pid>/mem`. Needs a game to validate offsets |
| 4. PS5 emulation | 15 | Game natively speaks DualSense; needs uhid virtual DS5 | **Built.** Virtual DS5 via pure-Python uhid, `hid-playstation` binds to it; input relay verified live (521 frames, all axes/buttons correct). DS5-effect→Flydigi-mode table is provisional and needs tuning in a game |
| 5. Bespoke | 11 | One mod per game (F1 23/24/25, GTA5, Fallout 4, MH Rise, DMC5, RE2/3/7, Bannerlord) | Not started |

## Owned games (for prioritisation)

- **Tier 1**: Death Stranding 2 *(downloading)*, Silksong, Uncharted: Lost Legacy, Space Marine 2
  *(200 GB — skipped, disk limited to 512 GB)*
- **Tier 2**: Forza Horizon 4, 5, 6 (all three)
- **Tier 3**: everything except Starfield, AC Odyssey/Origins/Valhalla, Hitman, Sniper Elite 5,
  Atomic Heart, 7 Days to Die, Mafia, Hunter: Call of the Wild
- **Tier 4**: Deathloop, GTA5 Enhanced. *(Marvel Rivals does not run on Linux — anti-cheat)*
- **Tier 5**: DMC5

## Open issues

- **Game detection**: many entries have empty `processGameNames` (incl. Silksong, Space Marine 2).
  Death Stranding 2 (`['DS2', 'DEATH STRANDING 2: ON THE BEACH']`) and Uncharted (`['u4','tll']`)
  do have them. Need a fallback — likely resolving the exe from the Steam manifest, which is what
  Flydigi's bundled `GameFinder.StoreHandlers.Steam` does.
- **Steam not yet installed** (`flatpak install -y flathub com.valvesoftware.Steam`).
- **Steam Input contention**: Steam/SDL also claim the hidraw node and send their own
  acquire/heartbeat (`0x1C`). May need to disable Steam Input for the pad or tolerate it.
- Which command family the K6 path needs (`83`/`85`/`87`) — untested; `81`/`82` were sufficient so far.

## End goal: Qt/KDE app replacing Space Station

Not just triggers — a full replacement covering what Steam Input and input-remapper cannot do.
The library/CLI split exists so a GUI can sit on top without rework.

Target features and the commands already recovered for them (all in `decompiled/`):

| Feature | Commands |
|---|---|
| Screen image (gamepad + charging dock) | `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`, `TestScreen`, `OffScreen`, `ReadScreenSetting`, `EnableScreenStatusBarAlwaysOn` |
| Trigger config, game-independent | `SetForceTriggerCommandFactory` (working), `K6Trigger*` |
| Profile switching | `ApplyMappingConfigByCfgId`, `SaveCurrentMappingConfig`, `ReadCurrentMappingConfigId`, `WriteAllMappingConfig`, `ResetMappingConfigByCfgId` |
| RGB / LED | `WriteRgbConfig`, `WriteAllRgbConfig`, `ReadLedConfig`, `TestLed` |
| Macros | `ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable` |
| Device settings | 22 in `command.setting/`: report rate, stick sensitivity/precision, debounce, rebound, auto-calibration, motion debounce, sleep time, dock smart stop, mode switch, nickname |
| Dock / cooler | `Flydigi.ChargerSdk.dll`, `Flydigi.CoolerSdk.dll` (in `bundle/`, not yet decompiled) |

**On needing Windows USB capture:** probably not required. Every layout taken from the decompiled
source has been correct on hardware; the one discrepancy (report id `6` vs `0x03`) was resolved from
the HID report descriptor instead. Capture is a fallback for specific stuck points — most likely
screen-image encoding, where conversion may happen in the Electron layer before reaching HID, or
any undocumented command ordering.

## Next steps

All five engines are built. What remains needs games, not code:

1. **Forza Horizon 6** — enable Data Out (127.0.0.1:5300), run `tools/flydigi-forza --dump`
   first to confirm telemetry crosses the Proton boundary, then run it for real.
2. **Dark Souls: Remastered** — validates Tier 3 (31 games). Run
   `tools/flydigi-monitor --probe <config>` and check the `move` define changes as you swing.
   If it reads 0, suspect module-base resolution under Proton.
3. **Deathloop** — validates Tier 4. Launch with
   `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501` so it binds the virtual DualSense, then
   tune `relay.EFFECT_MAP` by feel.
4. **Decompile ChargerSdk / CoolerSdk** for the gen2 dock.
5. **Qt/KDE app** — see the end-goal section above.
