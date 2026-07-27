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
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | **Done — validated in Dark Souls: Remastered.** Weapon-specific filters fire from live memory reads; resistance differs correctly per weapon |
| 4. PS5 emulation | 15 listed, **any DS5-aware game in practice** | Game natively speaks DualSense; needs uhid virtual DS5 | **Validated in Deathloop** — adaptive triggers work in-game. Input relay, DS5 binding and effect translation all confirmed. Rumble and gyro outstanding, see notes |
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
