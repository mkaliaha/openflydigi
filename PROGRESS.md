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
| 2. ForzaDualSense | 4 | Forza "Data Out" UDP telemetry → JSON rule engine → cmd `81` | **Done — validated in Forza Horizon 6.** All 7 distinct rules fired in-game and the effects are felt on the pad |
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | **Built & self-tested.** Chain walker verified against a real process via `/proc/<pid>/mem`. Needs a game to validate offsets |
| 4. PS5 emulation | 15 | Game natively speaks DualSense; needs uhid virtual DS5 | **Validated in Deathloop** — adaptive triggers work in-game. Input relay, DS5 binding and effect translation all confirmed. Rumble and gyro outstanding, see notes |
| 5. Bespoke | 11 | One mod per game (F1 23/24/25, GTA5, Fallout 4, MH Rise, DMC5, RE2/3/7, Bannerlord) | Not started |

## Owned games (for prioritisation)

- **Tier 1**: Death Stranding 2 *(downloading)*, Silksong, Uncharted: Lost Legacy, Space Marine 2
  *(200 GB — skipped, disk limited to 512 GB)*
- **Tier 2**: Forza Horizon 4, 5, 6 (all three)
- **Tier 3**: everything except Starfield, AC Odyssey/Origins/Valhalla, Hitman, Sniper Elite 5,
  Atomic Heart, 7 Days to Die, Mafia, Hunter: Call of the Wild
- **Tier 4**: Deathloop, GTA5 Enhanced. *(Marvel Rivals does not run on Linux — anti-cheat)*
- **Tier 5**: DMC5

## Forza notes

- **FH6 uses the 324-byte Data Out format**, same as FH5 — no `--accept` override needed.
- In-game: HUD and Gameplay → Data Out → ON, IP `127.0.0.1`, port `5300`.
- All four Forza mods (FH4, FH5, FH6, Motorsport) ship byte-identical rule configs (`af0961d95b34`),
  so one `configs/forza.json` covers every one of them.
- Validation run: 162 effect writes, all 7 behaviours exercised — traction loss/regain, gear shift,
  low- and high-speed braking, manual and automatic reverse.
- **FH6 itself is unstable under Proton**, unrelated to this project: it hits an NVIDIA-only sparse
  model-buffer bug (vkd3d-proton#3053, Xid 109 / `NVRM: can't update VA space`). Root cause is still
  unidentified upstream. Disabling DLSS/Reflex avoids the early splash crash; low geometry quality
  reduces sparse buffer pressure. FH5 is the calmer target and exercises identical code.

## Deathloop / Tier 4 findings

Adaptive triggers **work in game**. Confirmed the transcribed mapping behaves exactly like
Flydigi's: the game sent `type=0x25 p[0]=12` → `mode 3 [70,0,12]` and `type=0x21 p[1]=3` →
`mode 1 [140,1]`, both matching their table's branches. Zero unmapped patterns.

Two gaps remain, neither in the transport:

- **No rumble — investigated and closed as a known limitation of virtual DualSense emulation.**

  The DualSense has no conventional rumble motors; its voice coils do both jobs. Games can drive
  them two ways: `motor_left`/`motor_right` in the HID output report (the compatibility path, which
  we already support and which most PC ports use), or arbitrary waveforms written to the
  controller's USB *audio* device (the rich PS5 haptics). Deathloop uses the audio path.

  Confirmed by testing: with the real Apex 5 as a plain Xbox pad the game vibrates readily, even on
  a menu button press — so it does emit motor rumble, just not to a DualSense. Our own output path
  is proven: a direct cmd `0x12` rumbles the pad and ACKs.

  **Not implemented, feasibility untested.** DSX on Windows does not implement audio haptics
  either — it simply omits them, which is why Death Stranding DC behaves the same way there. That
  is not evidence the approach is impossible, only that nobody has built it.

  The open doubt is device association: a game locates the haptic audio endpoint through an
  OS-level link between the HID device and the audio device, and a virtual pad has no such link.
  Whether a game would instead accept an unassociated, suitably-named audio sink is **unknown and
  untested**. A PipeWire null sink was created and removed without ever being tested against a
  running game, so nothing here is settled empirically.

  If revisited: create a 4-channel sink (2 haptic + 2 speaker) described as "Wireless Controller",
  launch a haptic-audio title with the relay running, and check whether the game opens any stream
  against it. That single observation decides whether the rest is worth building.

  **Practical consequence, per game:** for titles using haptic audio, choose adaptive triggers
  (DS5 mode) or rumble (plain Xbox mode). Titles using the HID motor path get both, and that is
  the majority.
- **Gyro/accel: implemented.** The vendor input stream (command 17, "raw data transport in")
  carries the IMU at ~300 Hz, and enabling it does **not** disturb the xpad node, so sticks and
  buttons still come from evdev. Offsets follow `OperatorDataParser` for `NewXInput`, shifted by
  one because we keep the report-id byte. Accelerometer is scaled by 2.441: the pad reports
  ~4096/g while the DualSense calibration we advertise implies 10000/g — verified by the pad
  reading exactly 1.00 g flat. Gyro scale is left at 1.0 and is the one value worth tuning by feel
  (`--gyro-scale`). M1-M4 buttons are in the same stream and still to do.
- **Battery: implemented.** Command 1 returns device type, connection type and battery; polled
  every 30 s and mapped to the DualSense's 0-10 scale (Flydigi reports 0-5, with a high nibble
  flagging charging). Also fixed `BATTERY_FULL`, which was 0x01 (= charging) rather than 0x02, so
  the pad had been reporting "charging" permanently.

## Dual-mode games

Six titles are both `XGameMonitor` and `isPS5`, so Space Station lets the user choose between
Flydigi's memory-reading mod and DualSense emulation (`AutoTriggerMapMode { Flydigi, PS5 }`,
stored per game as `MapMode`):

    Cyberpunk 2077          Spider-Man Remastered
    Death Stranding DC      Spider-Man: Miles Morales
    Jedi Survivor           Uncharted 4

We expose no equivalent choice — `tools/flydigi-ds5` and `tools/flydigi-monitor` are run manually.
A per-game mode preference belongs in the daemon (and in the GUI later). Note the tradeoff differs
per mode: PS5 mode gives the game full DualSense semantics including battery reporting, while
Flydigi mode uses their hand-tuned per-game effects.

Also worth knowing: **battery already reaches the desktop**. `hid-playstation` turns the virtual
pad's reported battery into a power-supply device, so it appears in KDE's battery widget as
"Wireless Controller" — verified via `upower`.

## M1-M4 buttons: no DualSense destination

Reading M1-M4 from the vendor stream is easy, but there is nowhere in the DualSense protocol to
deliver them, so the scope is smaller than it first looks.

Emulating a **DualSense Edge is the wrong answer**:
  * it has two back buttons, not four;
  * even on a real Edge they have no HID inputs of their own -- they must be remapped onto existing
    buttons in the controller;
  * its different hardware ID *loses* native DualSense support in some games. DSX has a "DualSense
    Emulation" mode and Special K an "Identify DualSense Edge as DualSense" option precisely to undo
    this, and `ds5-edge-relay` exists to convert an Edge into a plain DualSense.

What reading them is still worth:
  * **M1 -> touchpad click**, which frees SELECT to be Create (its correct mapping). Today we
    sacrifice Create because there is no other source for touchpad-click.
  * **daemon-side actions** that never reach the game: profile switching, toggling the relay,
    cycling trigger presets.

For anything else the pad's own onboard remapping is the better mechanism -- it works with no
software running and persists in controller memory.

## Open issues

- **Game detection**: many entries have empty `processGameNames` (incl. Silksong, Space Marine 2).
  Death Stranding 2 (`['DS2', 'DEATH STRANDING 2: ON THE BEACH']`) and Uncharted (`['u4','tll']`)
  do have them. Need a fallback — likely resolving the exe from the Steam manifest, which is what
  Flydigi's bundled `GameFinder.StoreHandlers.Steam` does.
- **Steam not yet installed** (`flatpak install -y flathub com.valvesoftware.Steam`).
- **Steam Input contention**: Steam/SDL also claim the hidraw node and send their own
  acquire/heartbeat (`0x1C`). May need to disable Steam Input for the pad or tolerate it.
- Which command family the K6 path needs (`83`/`85`/`87`) — untested; `81`/`82` were sufficient so far.

## Next-session runbook

Start by reading this file and `PROTOCOL.md`. Everything gitignored is reproducible:
`tools/fetch-configs --monitor-configs --all-mods` restores `gamelist.json`, `configs/` and `mods/`.
The decompile toolchain lives in the `wine-arch` distrobox (see Environment above); the decompiled
sources under `decompiled/` are only needed for new protocol work, not to run anything.

### Deathloop — validates Tier 4 (virtual DualSense, 15 games)

Deathloop is `isPS5` with no mod: the game speaks DualSense natively, so there is nothing to
install. The whole job is the relay.

1. Connect the pad (it sleeps on idle — wake it first) and confirm all three interfaces:
   `python3 tools/hid_probe.py` should show the vendor node (`usage pages 0xffa0`), and
   `/dev/input/by-id/` should list `...-event-joystick`.
2. Steam launch options for Deathloop:
   `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 %command%`
   Without this the game may bind the real Apex 5 instead of the virtual DualSense.
3. Run `tools/flydigi-ds5` before launching the game. It logs each decoded DS5 effect and what
   it translated to.
4. In game, check the button prompts show PlayStation glyphs — that confirms it bound the virtual pad.
5. The effect mapping in `flydigi/relay.py::translate_ds5` is transcribed from Flydigi's
   `PS5DataManager.ProcessDataWithResult`, so it should be right rather than approximate. If
   something feels wrong, diff against that decompiled method before adjusting by feel.
   Unmapped effect patterns are logged as "unmapped, trigger unchanged" — those are new byte
   patterns Flydigi never handled, and are worth recording.

What to watch for:
- Double input (both pads registering) → the SDL ignore variable is not taking effect.
- Effects logged but not felt → EFFECT_MAP mapping is wrong, not the transport; the transport is
  the same cmd 81 that Forza already proved.
- Touchpad-click is on the touchpad *sub-device*, which needs `udev/99-flydigi-apex5.rules`
  installed or the node stays root-owned.

### Dark Souls: Remastered — validates Tier 3 (memory monitor, 31 games)

Chosen for the shortest pointer chain (3 hops vs 6-12 elsewhere) and the smallest download.

1. `tools/fetch-configs --monitor-configs` → `configs/monitor/DarkSoulsRemastered.default.json`
2. Start the game, get in-world, then:
   `tools/flydigi-monitor --probe configs/monitor/DarkSoulsRemastered.default.json`
   `--probe` reads memory and prints values without touching the controller.
3. Success looks like: the `move` define changing as you swing a weapon or roll.
4. If it reads 0 or a constant, the prime suspect is **module-base resolution under Proton**.
   `find_module_base()` takes the lowest mapped address of a `.exe` in `/proc/<pid>/maps`; Wine may
   map the PE differently from how `Module32Next` reports it on Windows. Inspect the maps directly
   and compare against the config's first offset (`0x1A31768` for DS:R).
5. Once values move sensibly, drop `--probe` to drive the triggers.

Pointer chains are build-specific: a game patch will break a config until Flydigi ships new offsets.

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
