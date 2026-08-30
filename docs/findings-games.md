# Game detection and adaptive-trigger routes

Reference for game detection, automatic route selection, controller targeting,
and validated integrations. Counts were verified against the local game-list
snapshot on 2026-08-23.

## Route inventory

The current list contains 94 games:

| Route | Games | Mechanism |
|---|---:|---|
| `vibration` | 33 | Command 82 binds each pad's own rumble to its adaptive triggers. |
| `monitor` | 31 | `flydigi-monitor` reads game memory using a downloaded JSON config. |
| `telemetry` | 4 | `flydigi-forza` turns local Forza Data Out datagrams into effects. |
| `bespoke` | 11 | `flydigi-dsx` accepts DSX-compatible mod commands. |
| `unknown` | 15 | No daemon route. These are DualSense-aware entries with no Flydigi route. |

The list also marks 23 titles as DualSense-aware. DualSense mode is a global
system switch rather than a per-game daemon route; it must be enabled before a
game enumerates controllers. It works with compatible titles whether or not
they appear in Flydigi's list.

`gamelist.json` is a gitignored snapshot fetched from Flydigi's public game-list
endpoint. `tools/fetch-configs` refreshes it before downloading configs or mod
archives, so counts and route availability may change with a later snapshot.

Only Fallout 4 currently exposes more than one daemon route: its bespoke mod
and a vibration preset. A stored route that is no longer offered by a refreshed
game list is ignored.

## Automatic daemon

Run the detector with:

```bash
tools/flydigid [--interval SECONDS] [--reassert SECONDS] [--device SELECTOR]
```

| Option | Default | Meaning |
|---|---:|---|
| `--interval` | `1.0` | Process-scan interval in seconds. |
| `--reassert` | `0` | Periodically reapply a vibration preset while the pad is present. Zero disables it. |
| `--device` | saved primary pad, then first matching pad | Pad used by single-pad monitor, telemetry, and bespoke routes. A selector may be a node path, UID or UID prefix, MAC address, or nickname. |

The explicit selector overrides the primary pad selected in the desktop app.
The saved selector is stored as top-level `primary_pad` in
`$XDG_CONFIG_HOME/flydigi/games.json`, normally
`~/.config/flydigi/games.json`.

The vibration route deliberately ignores `--device`: command 82 is pad-local
and needs no host-side session, so the daemon writes it to every attached pad
that advertises `adaptive_triggers`. Other routes hold and update one pad for
the game session.

On detection the daemon performs the following action:

| Route | Action |
|---|---|
| `vibration` | Apply the preset; start no helper process. |
| `monitor` | Start `flydigi-monitor <config> --quiet [--device ...]`. |
| `telemetry` | Start `flydigi-forza --quiet [--device ...]`. |
| `bespoke` | Start `flydigi-dsx --quiet [--device ...]`. |

Helpers receive SIGINT when the game exits and are killed only after a
five-second grace period so they can reset the triggers. The daemon also clears
triggers after a helper dies and on its own shutdown.

### Automatic-mode policy

Auto mode defaults to on only for the `vibration` route. Monitor, telemetry,
bespoke, and unknown routes default to off because they start a process or read
another process's memory. For a multi-route game, both its original tier and
selected route must permit the default; changing a route never silently enables
automatic execution. An explicit user toggle overrides the default.

Preferences are stored atomically in:

```text
$XDG_CONFIG_HOME/flydigi/games.json
```

The version-1 file contains a `games` object keyed by game-list ID and may also
contain `primary_pad`. Per-game entries contain optional `auto` and `route`
fields. Unknown fields are preserved.

Manage the same settings without the GUI through `tools/flydigi-auto`:

```text
list [--on|--off]
on GAME
off GAME
reset GAME
route GAME ROUTE
```

`reset` removes the whole game entry, including both auto and route overrides.
The daemon notices an atomic preference-file replacement and resets its scan so
enabling Auto for an already running game takes effect on the next poll.

### Monitor configurations

The 31 monitor configs are downloaded rather than committed:

```bash
tools/fetch-configs --monitor-configs
```

The daemon matches the singular executable name against the first file in
`configs/monitor/` beginning with `<process-name>.`. If no config is available,
the route is not started and the daemon logs the problem once.

### Steam launch wrapper

`tools/flydigi-run "<game name>" -- %command%` is an immediate alternative to
process detection for vibration-tier games. It applies that game's command-82
preset, launches the command, and clears the triggers on exit. Other routes are
launched without a trigger configuration; DualSense mode remains a separate
system switch.

The wrapper is intended for an adaptive-trigger Apex-class pad but has no
`--device` selector. It opens the first new-protocol Flydigi controller and
checks `adaptive_triggers` only afterward. If a non-adaptive-trigger model is
found first, it launches the game without the preset even when a compatible
Apex is also attached. It also cannot select between multiple Apex pads. There
is no selected-pad form of this wrapper; disconnect non-target pads when its
choice matters. The daemon is not a single-pad substitute for this route,
because daemon-managed vibration deliberately applies to every compatible pad.

### Helper restart policy

A helper that exits while its game remains active receives up to three restart
attempts after its initial start. Refusal occurs after the fourth death. A
helper that ran for at least 60 seconds is considered settled and receives a
fresh budget after its next failure. A later game launch also starts with a new
budget.

The monitor route waits for a process that has mapped the game executable before
starting. Its helper performs its own process lookup and would otherwise exit
during the launcher phase. Telemetry and bespoke routes may start from any
matching process because they do not require a game PID.

## Process detection

All 94 entries provide at least one process name. The index claims singular
`processGameName` values first and adds plural aliases second, so a plural alias
cannot displace another title's authoritative executable name. This resolves
the overlapping Uncharted and erroneous OVERWATCH aliases deterministically.

A game remains active while any matching process in its Proton wrapper chain
remains alive. Candidates are ranked as:

| Confidence | Evidence |
|---:|---|
| 2 | The process maps the named executable. |
| 1 | Its truncated `comm` value matches the name. |
| 0 | A command-line argument basename matches. |

Low-confidence processes are reconsidered on each poll because a Wine process
may map the executable after it first appears.

### False-positive safety limitation

The daemon may act on a confidence-0 name. It indexes the basename of every
command-line argument, including very short executable aliases. Vibration is
automatic by default, so an unrelated argument named like one of these aliases
can write command 82 to every adaptive-trigger pad:

```text
bf6  ds2  nfs14  re9  tll  wrc  wrc7  wrc8  wrc9  wrc10  wwm  yysls
```

This policy gives vibration effects as soon as a Proton wrapper appears, before
the game executable is mapped, but permits false positives. Treat `wrc` and
similarly short path basenames with particular care. Disable Auto for an
affected game if such a name is used by an unrelated program. A future policy
change could require confidence 1 or ignore short confidence-0 basenames.

## Reconnection and reassertion

A vibration bind is live controller state and is lost when a pad leaves the USB
bus. While a vibration game is active, `flydigid` checks for controller nodes
without opening hidraw, detects a reconnect, and retries the bind until a write
succeeds. This is independent of `--reassert`, which periodically counters
other software overwriting controller state.

Single-pad helpers fail more visibly when their selected pad disappears. Their
normal reconnect and restart behavior is documented with the relevant tool.

## DualSense mode

Enable DualSense mode before starting a compatible game:

```bash
sudo tools/flydigi-ds5-usbip --haptics --motors [--device SELECTOR]
```

Use `tools/flydigi-ds5 [--device SELECTOR]` only as the uhid fallback when
USB/IP is unavailable. Steam Input must be disabled for native DualSense
semantics. To keep SDL games from binding the physical Flydigi pad as a second
controller, set both current spellings:

```text
SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501
SDL_JOYSTICK_IGNORE_DEVICES=0x37d7/0x2501
```

The virtual device provides adaptive triggers, rumble, gyro, battery reporting,
and touchpad click mapped to SELECT. USB/IP also exposes the audio interfaces
needed for haptic audio; uhid cannot. The Apex has no touch surface, so touch
position and gestures have no source. See
[DualSense relay](findings-haptics.md).

## Validated integrations

| Integration | Current result | Required setup or limitation |
|---|---|---|
| Vibration bind | Automated for 33 entries; live-state reconnect handling is implemented. | May false-positive at confidence 0; affects every compatible attached pad. |
| Forza telemetry | 311-byte and 324-byte Data Out formats supported; four game entries use the same rule set. | Enable Data Out to `127.0.0.1:5300`. Other lengths require `--accept LEN:OFFSET`. |
| XGameMonitor | Validated with Dark Souls: Remastered using the downloaded config and Wine module base. | Requires `kernel.yama.ptrace_scope=0` or `CAP_SYS_PTRACE`; pointer chains are build-specific. |
| DSX listener | Accepts DSX-compatible local JSON commands. | Listener and external game mod are required; see [third-party mods](third-party-mods.md). |
| DualSense relay | Input, triggers, rumble, gyro, battery, and USB/IP haptic audio are implemented. | Relay must exist before the game starts; Steam Input must be off. |

### Monitor bring-up

Use probe mode before allowing a new memory config to drive the pad:

```bash
tools/fetch-configs --monitor-configs
tools/flydigi-monitor --probe configs/monitor/<name>.default.json \
    [--process NAME] [--device SELECTOR]
```

Confirm that the defined values change during gameplay, then remove `--probe`.
The parser accepts a UTF-8 BOM and trailing commas. Every pointer-chain hop is
read as eight bytes and the final result is truncated to `uint32`; the config's
declared type does not change that read size. Nonempty, nonzero `game_version`
entries are skipped. Game updates can invalidate offsets.

## Multi-controller behavior

- Vibration binds every adaptive-trigger pad independently.
- Monitor and telemetry data describe one player and drive one selected pad.
- DSX packets contain a controller-index field, but the current listener
  ignores it and drives one selected pad.
- Each DualSense relay represents one physical pad. Haptic audio is also
  single-device per relay session.

The desktop device picker writes the primary-pad selector used when the daemon
next starts a single-pad route. An explicit tool `--device` remains the
authoritative override.
