# Apex 5 on Linux — project state

Goal: replace Flydigi's Windows-only Space Station app on Linux. Started with adaptive triggers;
now covers five delivery mechanisms plus a virtual DualSense.

## Read this first

**Status: adaptive triggers are done and validated in real games, and the desktop app now covers
profiles, button remapping, vibration, per-profile trigger config and RGB lighting.** What remains
is the screen/GIF upload, a real battery reading, the charging dock, the third-party-app mapping
toggle, macros, the device settings, and a daemon that picks the right tier per game — see "Next".

| Tier | Games | Validated in |
|---|---|---|
| 1. Vibration bind (cmd 82) | 33 | Death Stranding 2 |
| 2. Forza telemetry | 4 | Forza Horizon 6 |
| 2b. DSX listener (UDP 7878) | third-party mod ecosystem | hardware |
| 3. XGameMonitor (memory) | 31 | Dark Souls: Remastered |
| 4. Virtual DualSense | **any DS5-aware game** | Deathloop |
| 5. Third-party mods | 11 | works via the DSX listener; not supported |

The backend is pure Python with zero dependencies — that is a feature worth defending, since it
means `flydigi-ds5` runs on any machine with Python 3.9 and no Qt. `PROTOCOL.md` has the wire
protocol and what is hardware-verified. Nothing Flydigi-owned is committed; `tools/fetch-configs`
restores it.

Licensing is per-file via REUSE: MIT backend, CC0 protocol docs and system config, GPL-3.0-or-later
for `gui/` only. `LICENSE` explains why, `gui/README.md` states the rule that keeps it true
(`gui/` may import `flydigi/`, never the reverse). Verify with `reuse lint`.

## The desktop app

**PySide6, in `gui/`**, calling the backend in-process — no D-Bus. Run it with:

```bash
python3 -m venv .venv && .venv/bin/pip install -r gui/requirements.txt
.venv/bin/python -m gui
```

PySide6 specifically, **not PyQt6** — PyQt is GPL-only and would force the whole tree copyleft,
which is where the "Qt means GPL" belief comes from. Installed as `PySide6-Essentials`, which also
leaves out the add-ons that are GPL-3.0-only (Charts, Data Visualization, Virtual Keyboard). Draw
graphs with `QPainter` or a QML `Canvas`.

| Tab | What works |
|---|---|
| Profiles → Buttons | remap, turbo + hold/toggle, rename, switch active, back up / restore to file |
| Profiles → Vibration | master switch, per-grip enable, min/max window, strength |
| Profiles → Triggers | stored effect (off / constant resistance), dead zone, trigger motor |
| Adaptive triggers | all 94 games, searchable, filtered by route; vibration presets load onto the pad from here |
| Lighting | effect, up to 5 colours, brightness, cycle time, react-to-rumble |

**Everything device-facing runs on a worker thread** (`gui/worker.py`) and requests cross as
signals. Calling a worker slot directly runs it on the caller's thread, which silently puts blocking
HID traffic back on the UI thread — that bug was written twice already.

**Apply vs save.** "Apply" writes the changed packets (164/165) and takes effect immediately;
"Apply & save" additionally sends 166, which Flydigi's SDK gives a 10 s timeout where everything
else gets 500 ms.

Confirmed on hardware: **an applied-but-unsaved change is lost when the pad sleeps** — not merely
on a power cycle. Applied lighting reverted after the pad idled out. So "apply" is working memory
in the literal sense, and anything meant to last needs the save.

Still unverified: that 166 itself works. It has only ever run against the fake pad, and nothing has
yet confirmed a saved change surviving a sleep.

### Tests, and how to run them without hardware

`tests/fake_pad.py` answers reads, diffed writes, apply and save, and refuses a bad checksum by
staying silent exactly as the pad does. `tests/test_gui.py` drives the real widgets offscreen.

```bash
for t in tests/test_*.py; do python3 "$t"; done   # 105 backend tests, no Qt needed
.venv/bin/python tests/test_gui.py                # 24 GUI tests
```

`test_gui.py` exits 0 with a skip message when PySide6 is absent, so the backend run stays
dependency-free.

## Next

Agreed feature list, roughly in the order it came up. Each is a fresh-context-sized piece of work.

**1. Screen image / GIF upload.** `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`,
`TestScreen`, `OffScreen`, `ReadScreenSetting`. Note Space Station only offers this **over a wired
connection** — worth assuming the dongle cannot carry it, and testing wired first rather than
debugging a dongle failure that is by design. The image encoding may live in the Electron layer
rather than the SDK, so check `asar/` as well as `decompiled/`.

**2. A real battery reading.** Currently `motion.parse_info` takes `data[12] & 0x0F` and reports
x/8, which is what the pad's own nibble gives. Somewhere better is likely:
`HeartBeatCommandFactory.cs` carries more device state (it is where `CurrentConfigId` comes from at
`data[3]` / `data[27]`), and a voltage or percentage field may be in the same reply. Start there
before assuming 8 steps is all the hardware exposes.

**3. Charging dock, and syncing it with the pad.** `Flydigi.ChargerSdk.dll` and
`Flydigi.CoolerSdk.dll` are in `bundle/` and **not yet decompiled** — that is step one
(`~/.dotnet/tools/ilspycmd -o decompiled/Flydigi.ChargerSdk bundle/Flydigi.ChargerSdk.dll` in the
`wine-arch` distrobox). The Electron locales already show what the feature looks like:
`cd2_charger_led_type_{breath,custom,default,diagonal_flow,gradient,pulse,rainbow,wave_gradient}`,
and `cd2_led_sync` — "Keep the lighting mode of the controller and dock in sync". So the dock has
its own effect set plus a sync toggle, which is the integration the user wants.

**4. "Allow third-party apps to take over mappings"** — a pad-side setting, not Steam's. Space
Station's own words:

> When the switch is turned on and a third-party application (such as Steam, reWASD, etc.) is
> opened, the controller mapping will be taken over, and all Space Station settings will be invalid
> at this time.

The likely command is `EnableMappingSwitchCommandFactory`, NewXInput **19**, payload
`[4]=4, [5]=4, [6]=enable`, crc at `[7]` — the `[5]=4` looks like a sub-function selector, so 19 is
probably a generic "enable feature N". **Unconfirmed**: the flag it sets is called `MappingSwitch`,
which might instead mean the Menu-button profile switching. Read the state back with command 3
(below), toggle, and read again to find out which bit moves — that settles it in one test.

Also relevant to the "extra buttons and gyro" part: `DeviceMaskCommandFactory` (**16**) takes
`maskController`, `maskMedia`, `maskGyro`, which is how the pad decides what to expose to the host.

### Command 3: the whole settings block in one read

Found while chasing the above, and it covers most of item 6 by itself. `ReadHardwareFunctionStatus`,
NewXInput command **3**, payload length 2 (no arguments). The reply carries capability and enabled
bits separately, so the pad tells you both what it supports and what is on:

```
data[5]  supported   bit0 quick-switch config   bit1 Xbox home button  bit2 motion debounce
                     bit3 mapping switch        bit4 stick debounce    bit5 stick auto-calibration
                     bit6 stick rebound         bit7 status bar always on
data[6]  enabled     same bit order
data[7]  supported   bit0 off-screen   bit1 audio
data[8]  enabled     same
data[9]  sleep time        data[10] report rate
data[11] stick precision   data[12] stick sensitivity
```

So sleep time is readable as well as writable — worth reading before `UpdateSleepTime` writes it.

**4b. An editor for the vibration bind.** Tier 1 is one bind — game rumble drives the trigger
motors — and each "supported game" is a **preset** of numbers for it: `vibType`, `vibFilter`,
`pwmScal`, and `vibParams` (stroke, pressure, strength, frequency per side). That is a sensible
design; the labels just have to say so, or it reads as a per-game integration like the other four
routes. Wording was fixed; the numbers still cannot be edited from the GUI, only through
`tools/flydigi_cmd.py bind`.

A lead worth checking first: the profile blob's force-trigger section (185..225) holds a `bind`
sub-struct of **type, filter, scale + 5 params**, and the live bind command (82) takes bindType,
filter, scale, stroke, pressure, strength, frequency. Same fields, with one param byte spare.
**Unverified** — the counts do not match exactly — but if it is the persistent form of the same
setting, an editor could write the bind into the profile so it survives a sleep instead of needing
re-applying every session. Test by applying a game bind, reading the profile, and diffing 185..225.

**5. Auto-launch per game — the daemon.** This is what the games tab is missing, and what its
"Preference" column should really be: a per-game **Auto** toggle meaning *when this game starts, do
the right thing for it without me*. Concretely, on detecting the game:

  * vibration → load its preset onto the pad
  * telemetry / monitor / ps5 / bespoke → start `flydigi-forza`, `flydigi-monitor`, `flydigi-ds5`
    or `flydigi-dsx`, and stop it again when the game exits

`flydigid` already does detect-and-apply for the vibration route, so the work is generalising it to
launch and supervise the other four, plus a UI toggle and somewhere to persist it
(`gui/triggers.py` already writes `~/.config/flydigi/games.json`).

**How Space Station does it**, from `AdapterTriggerRunner.CheckGameRunning` — worth knowing before
inventing something cleverer, because it is deliberately dull:

  * a loop with `Task.Delay(1000)`: plain **1 Hz polling**, no WMI event watcher, no ETW
  * `GameHelper.IsProcessRunning` wraps `Process.GetProcessesByName` behind a **5 second cache**,
    so the poll is cheap even with the whole game list to check
  * tries `ProcessGameName` first, then each entry in `ProcessGameNames`, and latches whichever
    matched
  * separately checks whether the mod process is already running, so it does not start it twice
  * `ModStartType` says where the mod executable lives: 0 = game directory + mod path,
    1 = Space Station's own directory + mod path

So 1 Hz is enough and `flydigid`'s approach is already the right one. Two things they do not have
to deal with that we do: Proton wrappers carrying the game's path in their cmdline (see
`monitor.find_process`, which requires the PE to actually be mapped), and no equivalent of their
"launch the game from our UI" path — which is what `flydigi-run` replaces.

Two things that will bite:

  * **Polling cannot detect every game.** Many entries have empty `processGameNames` (Silksong,
    Space Marine 2), so those need the `flydigi-run` launch wrapper via Steam launch options
    instead. A per-game Auto toggle should say which mechanism a title will use, or it will
    silently do nothing for the ones polling cannot see.
  * **Per-game mode preference** — six titles support both Flydigi's mod and PS5 mode (Cyberpunk
    2077, Death Stranding DC, Jedi Survivor, Spider-Man Remastered, Miles Morales, Uncharted 4).
    Auto has to know which to start; the storage exists, the UI does not.

**Small and worth doing first:**
  * **verify command 166 on hardware** — apply, save, let the pad sleep, read back. It is the last
    unknown in the write path and takes minutes.
  * `UpdateSleepTimeCommandFactory` — raising the sleep timeout would stop the pad dropping out
    mid-session, which has interrupted nearly every test.
  * macros (`ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable`); the profile blob at
    230..768 is already carried through untouched.

## Space Station exclusives — what is done and what is not

All command factories are decompiled under `decompiled/Flydigi.ControllerSdk/`.

| Feature | Commands | State |
|---|---|---|
| Mapping profiles | status **161**, apply **162**, read **163**, write **164**/**165**, save **166** | **done** — `flydigi/mapping.py`, `tools/flydigi-mapping`, GUI |
| Vibration + triggers | inside the profile blob | **done** — same module |
| RGB / lighting | read **167**, write **168**/**169** | **done** — `flydigi/lighting.py`, GUI |
| Macros | `ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable` | not started; blob at 230..768 is carried through untouched |
| Screen image (pad + dock) | `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`, `TestScreen`, `OffScreen` | not started; **wired only in Space Station**; encoding may live in the Electron layer |
| Device settings | read them all with **3**; writes are the 22 factories in `command.setting/` | not started — but command 3 already returns supported/enabled bits plus sleep time, report rate and stick precision/sensitivity in one reply, see "Next" |
| Dock / cooler | `Flydigi.ChargerSdk.dll`, `Flydigi.CoolerSdk.dll` in `bundle/` | not decompiled — now in scope, including `cd2_led_sync` (dock/pad lighting sync) |

### Config blobs, both verified on hardware

Mapping profile, 840 bytes (42 packets of 20), protocol v3.1:

```
0..2 version   2 package count   3..13 legacy LED   13..109 key table (32 x 3)
109..123 joystick curves      123..137 trigger travel curves
137..145 motion               145..154 grip vibration (master + 2 x 4)
154..183 trigger motors       183..185 wheel
185..225 force trigger (2 x 20)   225..227 data version   230..768 macros
770..790 title UTF-16LE       790..840 joystick extra, macro cycle, motion curve
```

Lighting, 380 bytes (19 packets of 20):

```
0..2 version   2 click feedback   3 loop start   4 loop end   5 cycle time
6 brightness   7 LED count (12)   8 mode   9..20 reserved
20.. frames of `LED count` RGB triples -- 10 x 12 on an Apex 5
```

Config structures for mapping/macro/RGB are already decompiled as `m_fdg_*_struct_t` types.

## Hard-won facts worth not rediscovering

  * **Report id is `0x03`** on the vendor interface, not the `6` the decompiled
    `TakeEndpointByDevice()` suggests. Find the node by report-descriptor prefix `06 a0 ff`; it moves
    between wired and dongle.
  * **Wine maps game PEs at their image base** (`0x140000000`), same as Windows, so Flydigi's memory
    offsets work unmodified.
  * **Never match a game process by cmdline alone** — Steam/Proton wrappers (`reaper`, `bwrap`,
    `pv-adverb`, `steam.exe`) all carry the game's path. Require the PE to be mapped.
  * **Effects persist in controller state** until changed; there is no timeout.
  * **The pad discards unsaved config when it sleeps.** Not just on a power cycle — idling out is
    enough, observed with lighting. Applying is working memory; command 166 is what makes it last.
  * **`effects.rumble()` must use `wait=0`** when driven continuously, or the 100 ms ACK wait puts
    the motors far behind.
  * **Steam Input must be off** for Tier 4 — it masks the pad and breaks DualSense semantics.
  * The Apex 5 sleeps on idle and its hidraw/evdev node numbers change on reconnect. Resolve by
    name/descriptor, never by path.
  * **Reading a mapping config switches the pad to it.** The firmware pages it in as the live one,
    audibly re-seating the trigger motors — that noise is the tell. Confirmed: after reading config
    2, `read_status` reports 2 as active. Use `read_config_preserving`, and prefer command **161**,
    which reports the active slot and a version id per slot with no side effect at all.
  * **The config commands are checksummed and the trigger-effect commands are not.** A mapping or
    lighting packet with a bad checksum gets no reply — the pad stays silent rather than erroring.
  * **Lighting effects are frame data, not a mode byte.** The pad has no animation generator; it
    plays the stored frames. Space Station computes them from (mode, colours) and uploads them, so
    writing a different mode number alone changes nothing visible.
  * **Frame geometry is not 16 x 10** despite what `LedConfigParser` walks — that is the older
    490-byte layout. An Apex 5 returns 380 bytes = 10 frames x 12 LEDs. Derive it from the blob.
  * **M1–M4 and C/Z are remap sources, not targets.** They have no XInput equivalent, so mapping a
    face button onto one makes it send nothing. `APEX5_KEYS` is the source list, `XINPUT_TARGETS`
    is what a remap may point at.
  * **Never combine a `pkill -f` with the relaunch in one shell command** — the pattern matches the
    shell running it and kills the session (exit 144). Two separate commands, and the `'[p]attern'`
    bracket trick.

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
| `flydigi/` | Library — `device.py` (transport), `blobs.py` (packetised config transfer), `effects.py` (live trigger commands), `mapping.py` (profiles, remapping, vibration, stored triggers), `lighting.py` (RGB), `games.py`, `forza.py` |
| `gui/` | PySide6 desktop app (GPL-3.0-or-later) — `main.py`, `worker.py` (all device I/O), `profiles.py`, `triggers.py`, `lighting.py` |
| `tools/flydigi-mapping` | CLI for profiles — list/show/set/clear/rename/apply/backup/restore |
| `tools/flydigi-forza` | Forza driver — UDP 5300 → rules → triggers (`--dump` for telemetry only) |
| `tools/flydigi-dsx` | DSX protocol listener on UDP 7878 — drives triggers from any DSX-compatible mod |
| `tools/flydigi-monitor` | Memory-reading driver using Flydigi's XGameMonitor configs (`--probe` to debug offsets) |
| `flydigi/uhid.py` | Pure-Python `/dev/uhid` binding (no dependencies) — creates kernel-side HID devices |
| `flydigi/ps5_data.py` | Generated DualSense descriptor + feature blobs (from MIT inputtino) |
| `tools/gen_ps5_data.py` | Regenerates the above from inputtino's `ps5.hpp` |
| `work/ref/inputtino/` | MIT reference clone — DS5 output report layout, canned feature reports |
| `tests/` | `test_forza.py` (7), `test_dsx.py` (9), `test_monitor.py`, `test_relay.py` (37), `test_mapping.py` (105), `test_gui.py` (24) — all pass without hardware |
| `tests/fake_pad.py` | Stand-in controller: multi-packet reads, diffed writes, apply, save, checksum rejection |
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
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | **Done — validated in Dark Souls: Remastered.** Weapon-specific filters fire from live memory reads; resistance differs correctly per weapon |
| 4. PS5 emulation | 15 listed, **any DS5-aware game in practice** | Game natively speaks DualSense; needs uhid virtual DS5 | **Validated in Deathloop** — adaptive triggers work in-game. Input relay, DS5 binding and effect translation all confirmed. Rumble and gyro outstanding, see notes |
| 5. Third-party mods | 11 | Game-side mods (REFramework, ScriptHookV, F4SE, Bannerlord module, F1 telemetry) | **No work needed** — they send DSX JSON to 127.0.0.1:7878, which `tools/flydigi-dsx` already accepts. Deliberately not shipped or supported; see [docs/third-party-mods.md](docs/third-party-mods.md) |

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

## Tier 4 is not limited to Flydigi's game list

The virtual DualSense is game-agnostic. Nothing in `tools/flydigi-ds5` is per-game, and
`relay.translate_ds5` maps DualSense effect **types**, not titles. So it works with any PC game that
natively supports DualSense adaptive triggers — Metro Exodus Enhanced, Ghostwire Tokyo, FF7 Remake,
Returnal, Ratchet & Clank, Stellar Blade, the Spider-Man ports, God of War Ragnarok, and whatever
ships next.

That is a better proposition than the mod-based tiers: Flydigi must author a mod per title, while
this covers every DualSense-aware game for free, including ones released after any given Space
Station update. The 15 in the game list are only the ones *Flydigi* flagged as PS5-mode.

**What works, and what to tell users:**

| Feature | Status |
|---|---|
| Adaptive triggers | Works — proven in Deathloop |
| Rumble via HID motor fields | Works — the path most games use |
| Gyro / motion aiming | Works |
| Battery reporting | Works, including the desktop battery widget |
| Touchpad click | Works, mapped to SELECT |
| HD / audio haptics | **Does not work** — structurally blocked, see below |
| Touchpad gestures, finger position | Does not work — the Apex has no touchpad |

Requirements per game: Steam Input **disabled** (it masks the pad and breaks DualSense semantics)
and `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501` so the game binds the virtual pad.

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

  **Superseded — see "Haptic audio" below.** Original finding retained for context:

  **Built, tested, negative result.** Neither Flydigi nor DSX implements audio haptics — verified
  by decompilation: Space Station bundles no audio libraries at all, and its `EnableAudio` command
  is a device feature toggle, not PC audio capture.

  We built the missing piece anyway (`tools/flydigi-haptics`): a fake 4-channel DualSense sink
  (`pipewire/99-dualsense-haptics.conf`) plus a bridge that measures haptic-channel energy and
  converts it to motor rumble. **The bridge works** — verified with `tools/haptics-simulate`, which
  plays synthetic gunshots and engine rumble and produces correctly decaying motor values.

  **But games do not use it.** With the sink present and named "Wireless Controller", Deathloop
  opened exactly one audio stream and routed it to the speakers; our sink measured absolute silence
  (peak 0.00000). A virtual pad has no OS-level link between its HID device and an audio endpoint,
  and an unassociated sink is not picked up.

  Cannot distinguish "looked for a controller endpoint and rejected ours" from "never looks on PC".
  The outcome is the same either way. Tooling is kept because it is proven working — if a game is
  ever found that does write to such a sink, only the sink config needs reinstalling.

  Two notes for anyone re-running this: `pw-record` prepends a file header and will silently
  misalign a raw reader (use `parec --raw`), and `paplay --raw` declares no channel map so PipeWire
  remixes the channels — do not assume fixed haptic channel indices.

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

## Haptic audio

Deathloop *does* drive DualSense haptics on PC, and it works under Proton — verified with a real
DualSense connected over USB (haptic audio needs wired USB; over Bluetooth the endpoint does not
exist). The game opens a **dedicated second stream** to the controller's audio device alongside its
normal game audio, so this is real haptic output rather than misrouted sound.

**DualSense audio channel map**, established by playing tones into each channel and having a human
report what happened — identified by pulse count rather than play order, after an off-by-one made
the first attempt wrong:

    ch0  headphone jack        ch2  left haptic actuator
    ch1  speaker               ch3  right haptic actuator

Deathloop writes **ch3 only** (active in 87% of 373 sampled windows; ch1 never touched). Treat
haptics as mono rather than assuming stereo.

**Conversion** (`flydigi/haptics.py`, `tools/flydigi-haptics`): the DualSense's actuators are
full-range voice coils, but the Apex 5's motors are not interchangeable — left is a large
low-frequency mass, right a small high-frequency one. Mapping left-to-left would throw away the
character of the waveform, so the signal is split by frequency instead: low band drives the left
motor, high band the right. Confirmed working against live game haptics.

Three things dominated latency, all of which made it feel sluggish and "keep going" after effects
ended:
  * `effects.rumble()` waited 100 ms for an ACK on every update. Pass `wait=0.0` when driving
    continuously — the ACK carries nothing useful.
  * `parec` buffers generously by default; ask for `--latency-msec`.
  * When falling behind, **drop stale audio** rather than working through the backlog.

Useful settings: `--gain 1.5 --crossover 250`.

**What this does not do:** it requires a real DualSense present as the haptic source. Making the
Apex work standalone needs the game to write haptics to a device we control — see the USB gadget
note below.

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

## Prior art (researched)

  * **`DualSense-haptic-helper`** (MIT) — real hardware; independently found haptics on channels
    2 and 3 of a 4.0 stream, matching our tone probing. Warns that **Steam Input masks the
    DualSense as an Xbox pad and breaks 4-channel audio**, so it must be disabled.
  * **`Haptic-Feedback-Linux`** and **`xzn/proton-ds5-haptic`** — Wine/Proton patches enabling DS5
    haptics, plus a udev rule setting `SOUND_DESCRIPTION="Wireless Controller"`.
  * **GE-Proton 11-2** and **proton-cachyos** now ship wired PS5 haptics natively for real
    controllers. A WirePlumber rule may be needed to stop PipeWire collapsing the DS5 node to mono.

**The mechanism**, from the patch discussions: games locate the haptic device by name, and the Wine
patches "fetch the audio-side ContainerId from setupapi so HID and MMDevice agree by construction".
That is precisely the association our null sink lacked — a uhid device and an unrelated PipeWire
sink can never share a ContainerId.

**Nobody has emulated a virtual DualSense with a working audio device.** Every project either uses
real hardware or emulates HID only (inputtino, DSX). The audio half of virtual emulation is
unexplored, consistent with the blockers below.

## Virtual USB composite device (not built)

Our PipeWire null sink was ignored by the game even when named "Wireless Controller", while a real
DualSense was used immediately. That points at device identity/association rather than name
matching: a game finds the haptic endpoint via the OS-level link between the HID device and the
audio device, which a null sink does not have.

The architecturally correct fix is one virtual USB composite device exposing both interfaces, so
the kernel creates the hidraw node and the ALSA card from the same device:

    dummy_hcd   provides a virtual UDC (this laptop is USB host-only, so there is no real one)
    configfs    gadget with hid.usb0 + uac2.usb0, VID:PID 054c:0ce6

Both modules are present on the kernel. Target spec, from the real device:
`s16le 4ch 48000Hz`, `alsa.components = USB054c:0ce6`, `device.bus = usb`, haptics on ch3.

**Tested and ruled out: PipeWire property spoofing.** Wine synthesises the Windows device instance
id from the underlying Linux device — USB devices become `USB\VID_xxxx&PID_xxxx\...`, everything
else `ROOT\MEDIA\N`, and that string is what ties an audio endpoint to a HID device. A null sink
was given every property the real device carries (`device.bus=usb`, `device.vendor.id=0x054c`,
`device.product.id=0x0ce6`, `sysfs.path`, `alsa.components`), then the node name and description
were made byte-identical to the real device's. Wine still assigned `ROOT\MEDIA\N` and the game
never opened the sink. Per Wine development discussion, winepulse resolves identity through the
**sysfs path** and looks it up in setupapi — a virtual node has no kernel device to find.

**Why uhid cannot close this.** uhid creates HID devices only; it has no audio concept and no way to
attach one. A real DualSense is a composite USB device whose HID and audio interfaces are siblings
under one USB device. Only real (or emulated) USB device topology produces that.

This is not Linux-specific: a virtual audio device on Windows needs an audio driver, and DSX ships
a virtual gamepad bus driver rather than one — consistent with DSX's virtual pad also failing to
produce haptics in Death Stranding DC.

**Untested idea worth revisiting.** Plug in a real DualSense purely as a haptic transducer, but
unbind its HID interface so the game cannot see it as a gamepad:

    echo -n "0003:054C:0CE6.00XX" | sudo tee /sys/bus/hid/drivers/playstation/unbind

The audio card stays (snd-usb-audio is untouched), so there is a genuine USB DualSense audio
endpoint with a proper instance id, while input comes from our virtual pad. If the game then writes
haptics to it, matching is **by name** and a cleverer virtual device might work; if not, matching is
**by association** and only real USB topology will ever do. Either way it answers the question we
could not settle, because the earlier fake-sink test failed for a different reason (no USB instance
id at all). Note `SDL_GAMECONTROLLER_IGNORE_DEVICES` cannot be used to hide the real pad -- our
virtual one shares its VID/PID.

Of limited practical value on its own (it needs a DualSense physically attached), but diagnostically
decisive.

**Blocked on this kernel.** Fedora ships neither `usb_f_uac2` nor `raw_gadget`, so there is no way
to present a USB audio interface without building and signing a kernel module — an ongoing chore on
a Secure Boot, auto-updating, ostree system. `dummy_hcd`, `vhci-hcd`, `usb_f_hid` and `usb_f_fs`
are all present and Fedora-signed, so the HID half is easy; only audio is missing.

**Open question: does a gaming distro ship these?** Not answered — searching returned only generic
distro comparisons. Worth checking directly rather than guessing, since some ship custom kernels for
handheld hardware that needs gadget mode (the Steam Deck has a real dual-role USB port, so SteamOS
plausibly enables UAC2 gadget). To check on any candidate:

    zcat /proc/config.gz | grep -E 'F_UAC2|RAW_GADGET'      # on a live/booted system
    # or inspect the distro's kernel spec/config in its repo

Candidates: SteamOS, Bazzite, CachyOS, Nobara. If one ships `usb_f_uac2`, the whole gadget route
becomes a rebase instead of a build-and-sign treadmill.

Remaining routes, none cheap: build `usb_f_uac2` and sign it; implement UAC2 over FunctionFS
including isochronous endpoints (no reference implementation exists); or rebase to an image that
ships the module.

**Deliberately not pursued:** deriving rumble from the game's own audio output. It fires on music
and dialogue and does not resemble real haptics.

**Status: parked.** The conversion works and is proven against real game haptics; it needs a real
DualSense present as the source. Reviving this means solving the audio-device emulation above.

## RGB: not working via the test command

`TestLedCommandFactory` (command **245**, `[4]=5, [5]=R, [6]=G, [7]=B, [8]=sum(3,3+5)`) ACKs
cleanly and echoes the exact RGB values back, but **the controller's lighting does not change** --
tested with 3-second holds per colour, re-sent at 4 Hz, so an overriding mode would have shown as a
flicker.

Most likely explanation: 245 lives in `command.test/` alongside TestScreen/TestJoystick/TestRF and
is exposed as `IpcCommandEnum_TestRgb`. These are factory-test commands and may require the device
to be in a diagnostic state first.

**The real path is the persistent config**, which is how Space Station does it:

  * `ReadLedConfigCommand` = **167**, `[4]=4, [5]=cfgId, [6]=pkgSize, [7]=sum`. Confirmed working --
    the pad replied `04 5a a5 a7 0c 00 00 00 03 00 00 09 04 14 0c 07 01 ff ff ff ...`
  * `WriteRgbConfigCommand` = **169**, written in packs: `[4]=len+3, [5]=packNum, [6..]=pack data`
  * Structure `m_fdg_mapping_rgb_sturct_t`: `version[2], type, loop_start, loop_end, loop_time,
    light_scale, rgb_num, rgb_type, reserve[11], id[16]` where each id is 10 x `{r,g,b}`.
    `type` / `rgb_type` select the lighting mode -- that is what needs setting to a static mode
    before a colour will stick.

So bridging the DualSense lightbar to the pad means decoding that config, setting a static mode and
writing it back -- a real job, not the one-command bridge originally assumed. The lightbar bytes
themselves are already parsed (`data[45..47]` of the DS5 output report).

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

### Dark Souls: Remastered — VALIDATED

Confirmed working. Notes from doing it:

  * **Wine maps the PE at its image base** (`0x140000000` for a 64-bit game), the same value
    `Module32Next` reports on Windows, so Flydigi's offsets work unmodified. This was the
    assumption flagged as riskiest and it turned out fine.
  * **Process selection cannot match on command line alone.** Under Steam and Proton a chain of
    wrappers (`reaper`, `bwrap`, `pv-adverb`, `steam.exe`) all carry the game's path in their
    cmdline. `find_process` now requires the candidate to have actually mapped the PE, which also
    yields the module base.
  * Dark Souls: Remastered keys off `move` (an animation id encoding weapon + attack). Black Knight
    Halberd swings produced `1123300`/`1123310`, matching the config's 黑骑士钺 entries, and the
    right trigger resisted heavily while the shield side stayed light — exactly as configured.

### Original notes

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
