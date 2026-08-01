# Games: detection, routes, and per-game validation

How the daemon decides a game is running and which route to take, what the game list offers, and
the notes from each title that validated a tier.

Index: [PROGRESS.md](../PROGRESS.md).

## Auto-launch per game — the daemon

A per-game **Auto** toggle does the right thing for a game when it starts. The toggle lives in
`flydigi/prefs.py`, the Games tab's Auto switch and `tools/flydigi-auto`; the daemon reads it and
re-reads it about a second after it changes. Per route, on detecting the game:

  * vibration → write the game's preset onto the pad; nothing is started
  * monitor → `flydigi-monitor <config> --quiet`
  * telemetry → `flydigi-forza --quiet`
  * bespoke → `flydigi-dsx --quiet`

and the driver is stopped again when the game exits. Validated live in Dark Souls: Remastered.
DualSense mode is not one of these; see *Dual-mode games* below. Nor is `tools/flydigi-run`, which
applies a vibration preset and launches every other tier with no trigger config.

`tools/flydigid` takes `--interval SECONDS` (default 1.0) and `--reassert SECONDS` (default 0,
disabled). The `--reassert` timer only fires while the pad is present. It logs to stdout tagged
`[flydigid]` (`tools/flydigid:31-32`), and the generated unit sets no logging directives, so under
systemd that is the user journal: `journalctl --user -u flydigid -f`.

**Auto is off by default on every route but vibration** (`AUTO_BY_DEFAULT = ("vibration",)`).
Telemetry, monitor and bespoke default to off, because they spawn a process or read another
process's memory. A game's tier and its currently chosen route must *both* permit auto, and the
more cautious of the two wins in either direction, so picking a route from the dropdown can never
grant auto mode as a side effect. An explicit toggle overrides both. `tests/test_prefs.py` guards
these rules, `tests/test_games.py` the process index.

**Where the preference lives.** `$XDG_CONFIG_HOME/flydigi/games.json`, defaulting to
`~/.config/flydigi/games.json`: `{"version": 1, "games": {...}}`, keyed by the gamelist `id`. An
entry carries at most two keys, `"auto"` (bool) and `"route"` (string), each absent until chosen
(`flydigi/prefs.py:149`, `:154`, `:174`, `:183`). Unknown keys are preserved on save, and the file
is rewritten atomically (tmp + fsync + rename) because the daemon re-reads it while the app edits
it. A change makes the daemon rebuild `Prefs`, clear its logged-once sets and reset the scanner, so
turning Auto on for a game that is *already* running takes effect on the next poll rather than on
the next launch. `tools/flydigi-auto` offers `list [--on|--off]`, `on <game>`, `off <game>`,
`reset <game>` (`Prefs.clear()` pops the whole entry, so the stored route choice goes with the auto
override) and `route <game> <route>`. The game argument is a substring match on either name field,
preferring an exact English-name hit, and `route` refuses a game that offers only one.

**The monitor route needs a download that is not in git.** For the 31 monitor games the daemon takes
the singular `processGameName`, strips `.exe`, and uses the first file in `configs/monitor/` whose
name starts with `<name>.` — fetched with `tools/fetch-configs --monitor-configs`. Against the
current download all 31 resolve. Without it the daemon starts nothing and says so exactly once in
its log. The vibration, telemetry and bespoke routes need no per-game download.

**A game's name matches its whole Proton wrapper chain.** Starting Dark Souls: Remastered produced
**eight** processes matching its name — `reaper`, `steam-runtime-launcher`, two `bwrap`s,
`pv-adverb`, a `python3`, `steam.exe`, and the game — since every Proton wrapper carries the game's
path in its command line. All eight answer *which game is running* identically, and no driver is
handed a pid: `flydigi-monitor` looks the game up itself and the two listeners do not care. So the
daemon tracks no pid — a game is running while **any** of its processes is, which is also the end
signal, `reaper` outliving the game and the inner wrappers exiting before it.

Matches are ranked by confidence: **2** = the process has the executable mapped, **1** = its
`comm` matches the name, **0** = the name only appears in the cmdline. `comm` is truncated to 15
characters — Dark Souls: Remastered appears as `DarkSoulsRemast` — so only a prefix comparison is
possible. Anything below confidence 2 is re-examined every poll, since a process can be in `/proc`
before Wine has mapped the PE.

### Confidence 0 is a name in an argument list, and the vibration route acts on it

**Unfixed, and written down rather than fixed because the fix overrides a deliberate decision.**
`candidate()` returns the best-ranked game at *any* confidence; only the **pid** is gated at `>= 2`.
The vibration route needs no pid, so it acts on a confidence-0 match — and `_match`'s own docstring
calls that survivable, on the reasoning that a preset written once does not care which process
asked for it.

What makes it reachable is the other end: `candidate_names` takes the basename of **every** cmdline
argument, lowercased and `.exe`-stripped, and Flydigi's list contains names as short as two
characters. Demonstrated with an ordinary sleeping process and nothing else:

```
python3 -c "import time; time.sleep(25)" --config /etc/myapp/ds   -> DEATH STRANDING DIRECTORS CUT
python3 -c "import time; time.sleep(25)" /opt/tools/wrc           -> EA WRC
```

Both matched at confidence 0 and both were returned by `candidate()`.

**Only some of those cost anything, and the difference is the tier.** `ds`, `u4`, `gow` and `cod`
are `monitor`, `bespoke` and `unknown`, and `prefs.AUTO_BY_DEFAULT` is `("vibration",)` — so the
daemon logs "auto mode off, leaving it alone" and stops. The twelve that are vibration-tier are
auto-on with no user configuration at all, and for those `apply_for` writes **command 82 to every
drivable pad**, over whatever trigger effect was set, plus `clear_all` when the decoy exits:

`bf6` · `ds2` · `nfs14` · `re9` · `tll` · `wrc` · `wrc7` · `wrc8` · `wrc9` · `wrc10` · `wwm` · `yysls`

`wrc` and `ds2` are the plausible ones. A directory or a script called `wrc` is not exotic.

**Why it is still here.** Requiring `>= 1` to act is the obvious fix and it is not free: at launch,
before Wine has mapped the PE, only the wrapper chain is up and every match is confidence 0, so the
bind would wait for the game's own process instead of firing immediately. A game whose `comm` never
matches its name *and* whose PE never maps would stop being bound at all — close to impossible, but
it is the risk being taken, and it trades a false positive nobody has hit for a false negative on a
route that currently always fires. The narrower alternative is to keep confidence 0 but ignore
index keys below some length when they matched only via an argument's basename.

Nothing was false-matching on the development machine when this was written, so it is latent rather
than active.

*When* to start a driver depends on the route. `flydigi-monitor` gives up if the PE is not mapped
yet and an exited driver is not restarted, so starting it while only the launcher chain is up would
abandon a game that was still loading. Only the `monitor` route waits for a process that has really
mapped the executable (`NEEDS_GAME_PROCESS`, `monitor.has_executable_mapped`); telemetry and bespoke
start as soon as any process matches, and vibration starts nothing. `comm` counts as evidence too,
since native Linux games have no PE to look for.

The daemon clears the triggers (`effects.clear_all`) when the game exits, when a driver exits on its
own, and on Ctrl-C. Drivers get SIGINT and are killed only after 5 s, because they put the triggers
back on SIGINT.

**A driver that exits by itself is restarted, up to `DRIVER_RESTARTS` times per game.** This
document previously said it was never restarted and the log line said so too, and neither was true:
nothing recorded the death, so the next poll found the same game running with auto on and started
the driver again — every second, for as long as the game ran. The budget is the honest version of
what both meant to say. It has to be a budget rather than a refusal because the commonest reason a
driver dies is the pad going to sleep mid-game, which recovers by itself, while the case the refusal
was written for — a config whose offsets a game patch broke — fails identically every time.
`DRIVER_SETTLED_SECONDS` separates them without needing to know why it exited: a driver that ran a
minute before stopping was working, so its budget resets, and a config that cannot work spends the
whole budget in three polls. Quitting the game returns the budget, so a later launch tries again.

**The daemon runs on the host.** It has to see the host's process table, which a Flatpak build never
will. **And the memory route cannot run in a container at all**: from inside the distrobox,
`/proc/<pid>/maps` of a host process is `Permission denied` — not only for a game inside
pressure-vessel's sandbox but for an ordinary host process too, `flydigid` itself included, even
though `stat` reports the same owner. Reading it needs PTRACE_MODE_READ across a user-namespace
boundary, with SELinux enforcing on top; a shared PID namespace and `ptrace_scope=0` do not lift it.
Tier 3 is host-only by construction.

Measured per route, from inside the distrobox:

| Route | Needs | From the container |
|---|---|---|
| vibration | write the vendor hidraw node | works |
| telemetry | bind UDP 127.0.0.1:5300 | works — the network namespace is the host's |
| bespoke | bind UDP 127.0.0.1:7878 | works |
| DualSense (not a route) | `/dev/uhid`, or `vhci-hcd` for tier 4b | works, and **the host sees the device** — a HID node created inside the container appeared on the host as `hidraw7`, since there is no device namespacing. Device creation only: the relay itself would take the pad over and was not run from in there. The distrobox also shares the host's pid namespace, which is what lets the app's switch find and stop a relay it did not start |
| monitor | read another process's memory | **denied** |

A device with Sony's IDs and the real descriptor, created inside the container, is bound by
`hid-playstation` on the host and produces all four input nodes (`Wireless Controller`, plus Motion
Sensors, Touchpad and Headset Jack).

Distrobox shares `/run/user`, so `systemctl --user` from inside the container drives the *host's*
user manager and the unit runs in the host's mount namespace — verified by starting a transient unit
from the container and comparing `/proc/<pid>/ns/mnt`. The app therefore writes the
`flydigid.service` user unit into the shared home and calls systemctl, with no container-specific
path; what `tools/apex5-setup` installs is in [PROGRESS.md](../PROGRESS.md). The udev rules are the
one exception — there is no system bus in the container, so pkexec cannot reach polkit from in
there, and `setup.escalation()` wraps it in `host-spawn` or `distrobox-host-exec`.

## Polling cost, and how Space Station does the same job

**A 1 Hz sweep of `/proc` is not free.** Reading `comm` and `cmdline` for every process every
second cost 15.4s of CPU over 10 minutes on an idle desktop of ~590 processes — 2.5% of a core,
almost all of it re-reading processes already ruled out. Examining each process once and remembering
the result brings it to 0.28%, measured over two minutes.

**How Space Station does it**, from `AdapterTriggerRunner.CheckGameRunning`:

  * a loop with `Task.Delay(1000)`: plain **1 Hz polling**, no WMI event watcher, no ETW
  * `GameHelper.IsProcessRunning` wraps `Process.GetProcessesByName` behind a **5 second cache**,
    so the poll is cheap even with the whole game list to check
  * tries `ProcessGameName` first, then each entry in `ProcessGameNames`, and latches whichever
    matched
  * separately checks whether the mod process is already running, so it does not start it twice
  * `ModStartType` says where the mod executable lives: 0 = game directory + mod path,
    1 = Space Station's own directory + mod path

**Detection covers every game, so no Steam-manifest fallback is needed** — the one Flydigi's bundled
`GameFinder.StoreHandlers.Steam` provides. All 94 entries carry a process name; 72 have only the
singular `processGameName` with an empty `processGameNames` list, which is why
`games.process_index()` reads both, and most multi-store titles have no plural list at all, their
executable being named the same everywhere. Polling reaches the whole list, so `flydigi-run` —
`flydigi-run "<game name>" -- %command%` in Steam's launch options — is a convenience (instant, no
1 Hz lag, survives a renamed process) rather than a requirement for coverage.

**The plural list is not just graphics-API variants.** Nine entries add names beyond their singular,
and they are four different things: API variants (Apex Legends, Forza Motorsport), *sibling titles*
under one entry (Call of Duty carries six executables — `cod22-cod`, `sp22-cod`, `cod`, `cod23-cod`,
`sp23-cod`, `sp24-cod`; both Uncharted entries list both `u4` and `tll`), alternate names for one
title (DEATH STRANDING DC, DEATH STRANDING 2, Where Winds Meet), and — for OVERWATCH — two other
games' executables, `HorizonForbiddenWest` and `RiftApart`, which look like editing debris.

That makes four process names claimed by two entries each, so **the singular name wins**:
`process_index()` claims singulars in a first pass and fills plural-only names in a second, rather
than letting file order decide. The two Uncharted entries are one Steam app (1659420) shipping two
executables under different routes — A Thief's End reads memory, Lost Legacy uses a vibration
preset — so file order would give `tll` to A Thief's End, running the wrong game's memory config and
leaving Lost Legacy unreachable. As indexed, a process named `tll` with the daemon up applies Lost
Legacy's vibration preset and clears it on exit. `tests/test_games.py` guards all four clashes.

**The daemon re-applies a vibration bind when the pad comes back.** The bind is live state — command
82, held in controller state until something changes it — so it does not survive the pad leaving the
USB bus, which is what a sleeping Apex 5 does. Every other route has a driver holding the pad and so
fails loudly; the vibration route has nothing running. `flydigid` checks presence each poll while a
vibration game is active (a `find_device()` that opens nothing), logs the pad leaving, and re-applies
the preset once it is back, retrying each poll until a write takes. Space Station does the same for
every route, rebuilding a live 82 from the stored bind after every applied-config read, reconnect
included (`OnAppliedConfigRead`, `fromDeviceChanged: true`). Distinct from `--reassert`, a timer
against Steam Input overwriting the pad's state, off by default.

## Dual-mode games

Six titles are both `XGameMonitor` and `isPS5`, so Space Station lets the user choose between
Flydigi's memory-reading mod and DualSense emulation (`AutoTriggerMapMode { Flydigi, PS5 }`,
stored per game as `MapMode`):

    Cyberpunk 2077          Spider-Man Remastered
    Death Stranding DC      Spider-Man: Miles Morales
    Jedi Survivor           Uncharted 4

The tradeoff differs per mode: PS5 mode gives the game full DualSense semantics including battery
reporting, while Flydigi mode uses their hand-tuned per-game effects.

**Exactly one game has a route to pick.** Counting capability flags turns up three more pairings
than `MapMode` has, because `games.tier()` returns only the winner of its priority chain and hides
the rest:

| Combination | Count | Games | Still a choice? |
|---|---|---|---|
| `XGameMonitor` + `isPS5` | 6 | the `MapMode` six above | **No** — see below |
| vibration + `isPS5` | 2 | Apex Legends, Uncharted: Lost Legacy | **No** |
| mod + vibration | 1 | Fallout 4 | **Yes — the only one** |

DualSense mode is deliberately not a route: the virtual pad has to exist *before* a game enumerates
pads, so acting on detection is already too late, and unlike every other route it needs no per-game
data at all. It is one switch for the whole system — the app's **DualSense** page
(`flydigi/dsmode.py`), or `sudo tools/flydigi-ds5-usbip --haptics --motors`; see
[findings-haptics.md](findings-haptics.md). So the eight `isPS5` pairings are not alternatives to
anything: `prefs.routes()` never returns a `ps5` route and `games.tier()` never yields one, and
`games.ds5_aware()` exposes the flag for display only. Against the real 94-entry gamelist,
`prefs.has_choice()` is true for **Fallout 4 alone** — a mod *and* a vibration preset — which is the
one row the Games page dropdown and `tools/flydigi-auto route` apply to.

The preference is a **route chosen from a list** rather than a binary mode: `prefs.routes()` returns
everything a game supports with its tier first, and a stored choice the gamelist no longer offers
is ignored rather than honoured. The list is `gamelist.json` at the repo root — gitignored,
refreshed by `games.fetch_gamelist()` from `GET https://api.flydigi.com/pc/adapter_trigger/list`
(public, unauthenticated) — so a route can disappear from under a preference saved months earlier.

**Battery reaches the desktop.** `hid-playstation` turns the virtual pad's reported battery into a
power-supply device, so it appears in KDE's battery widget as "Wireless Controller" — verified via
`upower`.

## Tier 4 is not limited to Flydigi's game list

The virtual DualSense is game-agnostic. Nothing in `tools/flydigi-ds5` is per-game, and
`relay.translate_ds5` maps DualSense effect **types**, not titles. So it works with any PC game that
natively supports DualSense adaptive triggers — Metro Exodus Enhanced, Ghostwire Tokyo, FF7 Remake,
Returnal, Ratchet & Clank, Stellar Blade, the Spider-Man ports, God of War Ragnarok, and whatever
ships next. The mod-based tiers need a mod authored per title.

**23** of the 94 entries carry `isPS5` — the flag `games.ds5_aware()` tests — and those are only the
ones *Flydigi* flagged. Of them, 15 carry the flag and nothing else, which is why they land in the
`unknown` tier and get no daemon route at all; the Games page hides the Auto switch entirely for
those 15, rather than showing a toggle that does nothing. The other 8 pair it with a mod or a preset.
The flag says nothing about whether the game runs here at all: Marvel Rivals is one of the 15, and
its anti-cheat has no Linux support.

What the virtual pad delivers — adaptive triggers, rumble, gyro, battery, touchpad click mapped to
SELECT, and HD/audio haptics on tier 4b only — and the per-game requirements (Steam Input off, the
SDL ignore variable, the game started *after* DS mode is on) are in
[findings-haptics.md](findings-haptics.md). Touchpad gestures and finger position have no source at
all: the Apex has no touchpad.

## Forza — validates tier 2 (Data Out telemetry)

- **FH6 uses the 324-byte Data Out format**, same as FH5 — no `--accept` override needed.
- Only two lengths are recognised at all: **311** (post-sled offset 0) and **324** (offset 12).
  Anything else is ignored unless `--accept LEN:OFFSET` is given.
- In-game: HUD and Gameplay → Data Out → ON, IP `127.0.0.1`, port `5300`.
- All four Forza mods (FH4, FH5, FH6, Motorsport) ship byte-identical rule configs — md5 prefix
  `af0961d95b34` — so one `configs/forza.json`, 15 rules under 8 names, covers every one of them.
- Validation run: 162 effect writes, exercising 7 of those 8 rule groups — traction loss/regain,
  gear shift, low- and high-speed braking, manual and automatic reverse. The eighth, 手动档倒车刹车
  (braking while reversing in manual), is not among them.
- The Data Out field table — name → (type, byte offset, whether the buffer offset applies) — is
  `flydigi/forza.py:31-119`, and is what a new length has to be checked against before `--accept`.
  `tools/flydigi-forza` also takes `--port` and `--config`, but no bind address: `forza.listen()`
  defaults to `127.0.0.1` and nothing overrides it (`flydigi/forza.py:285`), so the game has to be
  running on this machine.
- Rule evaluation follows Flydigi's `ConfigHelper`: filters fold left to right with no operator
  precedence, float equality uses a tolerance of 1.0, the first matching action wins, a `changed`
  filter compares against 0 on the first datagram, and `duration` + `afterTrigger` schedule one
  follow-up effect.
- **FH6 itself is unstable under Proton**, unrelated to this project: it hits an NVIDIA-only sparse
  model-buffer bug (vkd3d-proton#3053, Xid 109 / `NVRM: can't update VA space`), still unidentified
  upstream. Disabling DLSS/Reflex avoids the early splash crash; low geometry quality reduces sparse
  buffer pressure. FH5 is the calmer target and exercises identical code.

## Deathloop — validates tier 4 (virtual DualSense)

Deathloop is `isPS5` with no mod: the game speaks DualSense natively, so the whole job is the relay.
The measured results of the run — the effect translations observed, zero unmapped patterns, and the
rumble/haptics outcome — are in [findings-haptics.md](findings-haptics.md).

1. Connect the pad (it sleeps on idle — wake it first) and confirm all three nodes — the
   keyboard/mouse composite hidraw, the vendor hidraw, and the joystick event node:
   `python3 tools/hid_probe.py` should show the vendor node (`usage pages 0xffa0`), and
   `/dev/input/by-id/` should list `...-event-joystick`.
2. Steam launch options for Deathloop:
   `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 SDL_JOYSTICK_IGNORE_DEVICES=0x37d7/0x2501 %command%`
   Without this the game may bind the real Apex 5 instead of the virtual DualSense. Both
   spellings because SDL3 renamed the hint — this run predates the second name and which SDL
   Deathloop used was not recorded, so it is not known which of the two did the work here.
3. Run `tools/flydigi-ds5` before launching the game. It logs each decoded DS5 effect and what
   it translated to.
4. In game, check the button prompts show PlayStation glyphs — that confirms it bound the virtual pad.
5. The effect mapping in `flydigi/relay.py::translate_ds5` is transcribed from Flydigi's
   `PS5DataManager.ProcessDataWithResult`. Unmapped effect patterns are logged as "unmapped,
   trigger unchanged" — byte patterns Flydigi never handled.

What to watch for:
- Double input (both pads registering) → the SDL ignore variable is not taking effect.
- Effects logged but not felt → the `relay.translate_ds5` mapping is wrong, not the transport; the
  transport is the same cmd 81 that Forza already proved.
- Touchpad-click is on the touchpad *sub-device*, which needs `udev/72-flydigi-apex5.rules`
  installed or the node stays root-owned. **The file must sort before systemd's
  `73-seat-late.rules`**, which is what acts on `TAG+="uaccess"`: measured on a virtual DualSense,
  a 99- file left an ACL on the gamepad node alone (and that one from systemd's own
  `70-uaccess.rules` for joysticks), where `72-` covers all four input nodes.

## Dark Souls: Remastered — validates tier 3 (memory reading)

Confirmed working, on the shortest pointer chain of the shipped configs (3 hops against 6-12
elsewhere).

  * **Wine maps the PE at its image base** (`0x140000000` for a 64-bit game), the same value
    `Module32Next` reports on Windows, so Flydigi's offsets work unmodified.
  * `find_process` requires a candidate to have actually mapped the PE — the Steam and Proton
    wrapper chain all carries the game's path in its cmdline — which also yields the module base.
  * Dark Souls: Remastered keys off `move` (an animation id encoding weapon + attack). Its chain is
    `[0x1A31768, 0x18, 0x8B0]`; every hop is an 8-byte read and the result is truncated to uint32,
    with a zero first hop aborting the walk. Black Knight Halberd swings produced
    `1123300`/`1123310`, matching the config's 黑骑士钺 entries, and the right trigger resisted
    heavily while the shield side stayed light — exactly as configured.

### Bringing up a new memory config

Reading another process's memory needs `kernel.yama.ptrace_scope = 0`, or CAP_SYS_PTRACE;
`flydigi-monitor` reads `/proc/sys/kernel/yama/ptrace_scope` at start and warns when it is non-zero.

1. `tools/fetch-configs --monitor-configs` → `configs/monitor/DarkSoulsRemastered.default.json`
2. Start the game, get in-world, then:
   `tools/flydigi-monitor --probe configs/monitor/DarkSoulsRemastered.default.json`
   `--probe` reads memory and prints values without touching the controller. `--process NAME`
   overrides the config's `process_name`; `--quiet` suppresses the per-effect log.
3. Success looks like: the `move` define changing as you swing a weapon or roll.
4. If it reads 0 or a constant, the prime suspect is **module-base resolution under Proton**.
   `find_module_base()` takes the lowest mapped address of a `.exe` in `/proc/<pid>/maps`; Wine may
   map the PE differently from how `Module32Next` reports it on Windows. Inspect the maps directly
   and compare against the config's first offset (`0x1A31768` for DS:R).
5. Once values move sensibly, drop `--probe` to drive the triggers.

The engine polls at the config's `period`, in milliseconds — 50 for DS:R. Only the defines whose
value changed re-trigger evaluation, and an effect is re-sent only when its (mode, params) differ
from what was last written. The configs themselves are parsed leniently — BOM stripped, trailing
commas removed — because Flydigi's shipped files are not strict JSON.

Everything the engine takes from a config is read in `flydigi/monitor.py:272-360`, where two
fields do not mean what they say. A `vDefines` entry's declared `type` does not set the read size:
every hop is an 8-byte read truncated to uint32 whatever the type says (`flydigi/monitor.py:26-27`).
And a define whose `game_version` is set to anything but `""` or `"0"` is skipped without a word, the
engine's own version being always `""` (`:293`, `:299-300`); every `game_version` in the downloaded
configs is empty, so the skip bites only a hand-written or newer file.

Pointer chains are build-specific: a game patch will break a config until Flydigi ships new offsets.

## Tier summary, in full

The compact version is in [PROGRESS.md](../PROGRESS.md); this is the long-form state of each.

| Tier | Games | Mechanism | State |
|---|---|---|---|
| 1. Vibration bind | 33 | cmd `82` SyncWithGrip, config from API, driven by game rumble | Automated; verified in Death Stranding 2 — the triggers buzz with in-game rumble |
| 2. ForzaDualSense | 4 | Forza "Data Out" UDP telemetry → JSON rule engine → cmd `81` | Validated in Forza Horizon 6, 7 of the config's 8 rule groups fired |
| 2b. DSX listener | **any DSX-compatible mod** | Game-side mods speak DSX JSON to 127.0.0.1:7878 | Built, self-tested — `tools/flydigi-dsx`; see [third-party-mods.md](third-party-mods.md) |
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | Validated in Dark Souls: Remastered, resistance differing per weapon from live memory reads |
| 4. PS5 emulation (uhid) | 23 flagged `isPS5`, **any DS5-aware game in practice** | Game natively speaks DualSense; uhid virtual DS5 | Validated in Deathloop — input relay, DS5 binding, effect translation, rumble, gyro and battery. **No haptic audio**, structurally impossible on uhid. Fallback for a machine with no `vhci-hcd`; M1-M6 reach a game only by re-using an existing DualSense control, and only via the pad's own onboard remapping |
| 4b. PS5 emulation over USB | same, **any DS5-aware game** | usbip + `vhci-hcd` composite DS5: `hid-playstation` binds the HID interface, `snd-usb-audio` the audio ones | Validated in Deathloop, and supersedes tier 4 wherever both work — same input and triggers, *plus* PS5 haptic audio reaching the pad's motors. What the app's DualSense switch turns on |
| 5. Third-party mods | 11 | Game-side mods (REFramework, ScriptHookV, F4SE, Bannerlord module, F1 telemetry) | Works via 2b; deliberately not shipped or supported — see [third-party-mods.md](third-party-mods.md) |

**The tiers stop being interchangeable once a second pad is on the desk**, and they split by where
the effect data comes from rather than by tier number.

  * **Tier 1 is the only one that scales for free.** Command `82` is a *pad-side* setting — the pad
    drives its own triggers from its own rumble, with nothing host-side in the per-frame loop — so
    every bound pad reacts correctly and independently. Applying it to all of them is a loop.
  * **Tiers 2 and 3 are single-source by nature.** Forza's Data Out describes one car and
    XGameMonitor reads one player's state out of memory. There is no second player *in the data*,
    so mirroring the same effects to every pad is the only coherent multi-pad behaviour.
  * **Tier 2b carries addressing nobody uses.** DSX's `parameters[0]` is a controller index.
    `flydigi/dsx.py` ignores it, following `OnTriggerCommandReceived`, which ignores it too.
    Whether any mod populates it is unmeasured.
  * **Tiers 4/4b are per-device for input, rumble and triggers, and single for haptic audio** —
    measured, and ruled out on cost rather than on capability. See
    [findings-haptics.md](findings-haptics.md) and [PROGRESS.md](../PROGRESS.md#ruled-out).

Space Station has the same ceiling and less of a choice about it: every trigger path in
`ControllerRepository` — five call sites — is
`DeviceSlots.FirstOrDefault(c => c?.IsSupportForceTrigger ?? false)`, so it drives the first
capable pad and no other, out of the four its `CommunicationManager` can hold.
