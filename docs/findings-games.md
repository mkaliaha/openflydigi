# Games: detection, routes, and per-game validation

How the daemon decides a game is running and which route to take, what the game list
really offers, and the notes from each title that validated a tier.

Index: [PROGRESS.md](../PROGRESS.md).

## Auto-launch per game — the daemon

This is what the games tab was missing, and what its
"Preference" column should really be: a per-game **Auto** toggle meaning *when this game starts, do
the right thing for it without me*. Concretely, on detecting the game:

  * vibration → load its preset onto the pad
  * telemetry / monitor / ps5 / bespoke → start `flydigi-forza`, `flydigi-monitor`, `flydigi-ds5`
    or `flydigi-dsx`, and stop it again when the game exits

**Done.** The toggle exists (`prefs.py`, the Games tab's Auto switch, `tools/flydigi-auto`), the
daemon reads it and re-reads it about a second after it changes, and it now starts and stops the
right helper for the route: `flydigi-monitor`, `flydigi-forza` or `flydigi-dsx`. Validated live in
Dark Souls: Remastered — the daemon started the driver, which attached to the game and resolved its
module base at `0x140000000`. The PS5 route is deliberately excluded: the virtual DualSense has to
exist before the game enumerates pads, so reacting to a launch is already too late, and
`tools/flydigi-run` stays the way in.

**"Is the game running" and "which process is the game" are different questions, and only one
route asks the second.** Starting Dark Souls: Remastered produced **eight** processes matching its
name — `reaper`, `steam-runtime-launcher`, two `bwrap`s, `pv-adverb`, a `python3`, `steam.exe`, and
the game — since every Proton wrapper carries the game's path in its command line. For *which game
is running* that is not a problem at all: every one of them agrees on the answer. None of the
drivers is handed a pid either; `flydigi-monitor` looks the game up itself, and the two listeners
do not care.

So the daemon does not track a pid. A game is running while **any** of its processes is, which is
also the honest end signal: the chain comes and goes around the game — `reaper` outlives it, inner
wrappers exit before it — so watching one pid picks an arbitrary moment to call it over.

Where the distinction does earn its keep is *when* to start the memory driver. `flydigi-monitor`
gives up if the PE is not mapped yet, and an exited driver is deliberately not restarted, so
starting it while only the launcher chain is up would make auto mode abandon a game that was merely
still loading. The daemon therefore waits for a process that has really mapped the executable —
`monitor.has_executable_mapped`, the check `find_process` always made, now shared rather than
reimplemented. `comm` counts as evidence too, since native Linux games have no PE to look for.

Two things fell out of building it.

**The daemon belongs on the host, and the app cannot spawn it there — but does not need to.**
It has to see the host's process table, which a Flatpak build never will, so it is not something
this app can contain. **And the memory route cannot run in a container at all**: from inside the
distrobox, `/proc/<pid>/maps` of a host process is `Permission denied` — not only for a game inside
pressure-vessel's sandbox but for an ordinary host process too, `flydigid` itself included, even
though `stat` reports the same owner. Reading it needs PTRACE_MODE_READ across a user-namespace
boundary, with SELinux enforcing on top. So tier 3 is host-only by construction. A shared PID
namespace and `ptrace_scope=0` are not enough to infer otherwise — only an attempted cross-process
read answers this.

Measured per route, from inside the distrobox:

| Route | Needs | From the container |
|---|---|---|
| vibration | write the vendor hidraw node | works |
| telemetry | bind UDP 127.0.0.1:5300 | works — the network namespace is the host's |
| bespoke | bind UDP 127.0.0.1:7878 | works |
| ps5 | `/dev/uhid` | works, and **the host sees the device** — a HID node created inside the container appeared on the host as `hidraw7`, since there is no device namespacing. The full relay was not run from in there, as it would take the pad over |
| monitor | read another process's memory | **denied** |

The `ps5` row was checked properly rather than by inference: a device with Sony's IDs and the real
descriptor, created inside the container, is bound by `hid-playstation` on the host and produces all
four input nodes (`Wireless Controller`, plus Motion Sensors, Touchpad and Headset Jack). Test it
that way or not at all: a vendor-usage device with a made-up VID is rightly ignored by the gamepad
stack, so it shows a hidraw node appearing and nothing about whether anything believed it.

So exactly one route is blocked, and it is the one that decides where the daemon lives. It turned out not to matter: distrobox shares `/run/user`, so `systemctl
--user` from inside the container drives the *host's* user manager and the unit runs in the host's
mount namespace. Verified by starting a transient unit from the container and comparing
`/proc/<pid>/ns/mnt`. So the app writes the unit into the shared home and calls systemctl, with no
container-specific path. The udev rules are the one exception — there is no system bus in the
container, so pkexec cannot reach polkit from in there, and `setup.escalation()` goes through
host-spawn.

## Polling cost, and how Space Station does the same job

**A 1 Hz sweep of `/proc` is not free.** Reading `comm` and `cmdline` for every process every
second cost 15.4s of CPU over 10 minutes on an idle desktop of ~590 processes — 2.4% of a core,
almost all of it re-reading processes already ruled out. That was tolerable when the daemon was
something you started for a session and is not once it starts at login. Examining each process once
and remembering the result brings it to 0.28%, measured over two minutes. Space Station's 5-second
cache is the same instinct, arrived at from the other direction.

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

**Detection covers every game, and this closes an open issue.** PROGRESS.md used to list "many
entries have empty `processGameNames`" as a gap needing a fallback — resolving the executable from
the Steam manifest, the way Flydigi's bundled `GameFinder.StoreHandlers.Steam` does. Counting says
otherwise, so no fallback is needed. All 94 entries carry a process name — 72 have only the singular
`processGameName` with an empty `processGameNames` list, which is why `games.process_index()` reads
both. Most multi-store titles have no plural list at all, their executable being named the same
everywhere. Polling can reach the whole list, so `flydigi-run` is a convenience (instant, no 1 Hz lag,
survives a renamed process) rather than a requirement for coverage.

**The plural list is not just graphics-API variants.** Nine entries add names
beyond their singular, and they are three different things: API variants (Apex Legends, Forza
Motorsport), *sibling titles* under one entry (Call of Duty carries six `*-cod` executables; both
Uncharted entries list both `u4` and `tll`), and — for OVERWATCH — two other games' executables,
`HorizonForbiddenWest` and `RiftApart`, which look like editing debris.

That makes four process names claimed by two entries each, so **the singular name has to win**.
`process_index()` now claims singulars in a first pass and fills plural-only names in a second.
Before that, first-wins-by-file-order gave `tll` to *A Thief's End*: starting Lost Legacy ran the
wrong game's memory config, and its own entry — a different route entirely — was unreachable.
Verified after the fix by running a process named `tll` with the daemon up; it applied Lost
Legacy's vibration preset and cleared it on exit. `tests/test_games.py` guards all four clashes.

So 1 Hz is enough and `flydigid`'s approach is already the right one. Two things they do not have
to deal with that we do: Proton wrappers carrying the game's path in their cmdline (see
`monitor.find_process`, which requires the PE to actually be mapped), and no equivalent of their
"launch the game from our UI" path — which is what `flydigi-run` replaces.

One thing that will bite:

  * **Per-game mode preference** — six titles support both Flydigi's mod and PS5 mode (Cyberpunk
    2077, Death Stranding DC, Jedi Survivor, Spider-Man Remastered, Miles Morales, Uncharted 4),
    and counting capability flags rather than trusting that pair turns up nine. Auto has to know
    which to start. **Since done**: the storage is `prefs.routes()` and the Games page has the
    route picker — see *Dual-mode games* below.

**Still worth doing, from the same list:**
  * ~~verify command 166 on hardware~~ — **done, it works**; see
    [findings-desktop-app.md](findings-desktop-app.md).
  * `UpdateSleepTimeCommandFactory` (**23**) — the pad ships at **15 minutes**, read straight off
    command 3. Raising it would stop the pad dropping out mid-session, which has interrupted nearly
    every test; and since sleeping means leaving the USB bus entirely, the drop-out is not a nuisance
    to work around but a disconnect to recover from.
  * macros (`ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable`); the profile blob at
    230..768 is already carried through untouched.

## Dual-mode games

Six titles are both `XGameMonitor` and `isPS5`, so Space Station lets the user choose between
Flydigi's memory-reading mod and DualSense emulation (`AutoTriggerMapMode { Flydigi, PS5 }`,
stored per game as `MapMode`):

    Cyberpunk 2077          Spider-Man Remastered
    Death Stranding DC      Spider-Man: Miles Morales
    Jedi Survivor           Uncharted 4

Note the tradeoff differs per mode: PS5 mode gives the game full DualSense semantics including
battery reporting, while Flydigi mode uses their hand-tuned per-game effects.

**But `MapMode` is not the whole story — nine games have a choice, not six.** Counting capability
flags across the gamelist rather than assuming Space Station's pair was exhaustive turns up three
more, because `games.tier()` returns only the winner of its priority chain and hides the rest:

| Combination | Count | Games |
|---|---|---|
| `XGameMonitor` + `isPS5` | 6 | the `MapMode` six above |
| vibration + `isPS5` | 2 | Apex Legends, Uncharted: Lost Legacy |
| mod + vibration | 1 | Fallout 4 |

So the preference is a **route chosen from a list**, not a binary mode: `prefs.routes()` returns
everything a game supports with its tier first, and a stored choice the gamelist no longer offers
is ignored rather than honoured, since the list is refetched from Flydigi's API.

Also worth knowing: **battery already reaches the desktop**. `hid-playstation` turns the virtual
pad's reported battery into a power-supply device, so it appears in KDE's battery widget as
"Wireless Controller" — verified via `upower`.

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

## Deathloop — validates tier 4 (virtual DualSense)

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
- Effects logged but not felt → the `relay.translate_ds5` mapping is wrong, not the transport; the transport is
  the same cmd 81 that Forza already proved.
- Touchpad-click is on the touchpad *sub-device*, which needs `udev/72-flydigi-apex5.rules`
  installed or the node stays root-owned. **The rules only started working when they were renamed
  from 99- to 72-**: `TAG+="uaccess"` merely sets a tag, and systemd's own `73-seat-late.rules` is
  what acts on it, so a file sorting after 73 tagged devices nobody looked at again. Verified both
  ways by standing up a virtual DualSense and reading the ACLs on its four input nodes — before,
  only the gamepad node had one, and it came from systemd's `70-uaccess.rules` for joysticks; after,
  all four do, touchpad included.

## Dark Souls: Remastered — validates tier 3 (memory reading)

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

### Bringing up a new memory config

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

## Owned games (for prioritisation)

- **Tier 1**: Death Stranding 2, Silksong, Uncharted: Lost Legacy, Space Marine 2
  *(200 GB — skipped, disk limited to 512 GB)*
- **Tier 2**: Forza Horizon 4, 5, 6 (all three)
- **Tier 3**: everything except Starfield, AC Odyssey/Origins/Valhalla, Hitman, Sniper Elite 5,
  Atomic Heart, 7 Days to Die, Mafia, Hunter: Call of the Wild
- **Tier 4**: Deathloop, GTA5 Enhanced. *(Marvel Rivals does not run on Linux — anti-cheat)*
- **Tier 5**: DMC5


## Tier summary, in full

The compact version is in [PROGRESS.md](../PROGRESS.md); this is the long-form state of each.

| Tier | Games | Mechanism | State |
|---|---|---|---|
| 1. Vibration bind | 33 | cmd `82` SyncWithGrip, config from API, driven by game rumble | **Done & automated** — verified in Death Stranding 2, triggers buzz with in-game rumble, daemon auto-detects and applies |
| 2. ForzaDualSense | 4 | Forza "Data Out" UDP telemetry → JSON rule engine → cmd `81` | **Done — validated in Forza Horizon 6.** All 7 distinct rules fired in-game and the effects are felt on the pad |
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | **Done — validated in Dark Souls: Remastered.** Weapon-specific filters fire from live memory reads; resistance differs correctly per weapon |
| 4. PS5 emulation | 15 listed, **any DS5-aware game in practice** | Game natively speaks DualSense; needs uhid virtual DS5 | **Validated in Deathloop** — adaptive triggers work in-game. Input relay, DS5 binding, effect translation, rumble, gyro and battery all confirmed. Haptic-audio titles need the PipeWire bridge, and M1-M4 have no DualSense destination — see notes |
| 5. Third-party mods | 11 | Game-side mods (REFramework, ScriptHookV, F4SE, Bannerlord module, F1 telemetry) | **No work needed** — they send DSX JSON to 127.0.0.1:7878, which `tools/flydigi-dsx` already accepts. Deliberately not shipped or supported; see [third-party-mods.md](third-party-mods.md) |
