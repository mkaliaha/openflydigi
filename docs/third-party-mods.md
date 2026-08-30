# Third-party game mods

Reference for game-side mods that emit DSX-compatible adaptive-trigger
commands. Inventory counts were verified against the local game-list snapshot
on 2026-08-23.

This project implements the local listener protocol but does not bundle,
install, maintain, or support the external mods themselves. See
[game routes](findings-games.md) and [PROTOCOL.md](../PROTOCOL.md) §4.

## Inventory

The game list contains 94 entries. Forty-six carry a mod download URL:

| Implementation | Games |
|---|---:|
| XGameMonitor config | 31 |
| Forza telemetry helper | 4 |
| Third-party/bespoke mod | 11 |

The 11 bespoke entries are:

| Framework | Games |
|---|---|
| REFramework/Lua | Monste Hunter Rise, Devil May Cry 5, Resident Evil 2, Resident Evil 3, Resident Evil 7 Biohazard |
| ScriptHookV | Grand Theft Auto V |
| F4SE | Fallout 4 |
| Bannerlord native module | Mount & Blade II Bannerlord |
| Standalone F1 telemetry process | F1® 23, F1® 24, F1® 25 |

Names are reproduced from Flydigi's list, including `Monste Hunter Rise`.
Fallout 4 is the only bespoke entry that also offers a vibration-preset route.

## Security and compatibility warning

Most in-game mods inject code into the game process. The REFramework and GTA V
packages use a `dinput8.dll` proxy; Fallout 4 loads through F4SE. Such loaders
can break after game updates, require Proton-specific setup, and conflict with
anti-cheat software. The observed GTA V package expects BattlEye to be disabled.

Do not install an archive without reviewing its origin and contents. Do not
disable anti-cheat or use injected mods in protected online play. The project
does not verify the current safety or compatibility of externally hosted
archives.

The Bannerlord module and standalone F1 processes do not use the same DLL-proxy
injection path, but they remain third-party executables.

## DSX messages

Eight mods run inside their game and send DSX-style JSON datagrams to
`127.0.0.1:7878`. The three F1 helpers read their own game's telemetry and emit
the same protocol from a sidecar process.

A trigger update contains an instruction type and parameters for controller
index, side, opcode 19, effect mode, and up to five effect parameters. Existing
mods disagree about JSON scalar types: some quote every value, while others mix
numbers and strings. `flydigi.dsx` accepts both and also accepts integral float
values.

Effect modes map to the project's common trigger modes:

| DSX mode | Project mode |
|---:|---|
| 0 | `MODE_NORMAL` |
| 1 | `MODE_RACE` |
| 2 | `MODE_SNIPER` |
| 3 | `MODE_RECOIL` |

The listener sends command 81 per changed side and normalizes the parameter
block to five bytes. Identical consecutive effects are suppressed.

The Lua clients send deltas rather than complete state. An unchanged side may
be omitted or represented by instruction type 0, and no datagram is sent when
neither side changes. Socket silence while a held effect remains active is
therefore normal.

Only trigger-update instructions are implemented. Lightbar and other DSX
instruction types are ignored without decoding their parameters.

## Listener reference

Start the listener before or with the game:

```bash
tools/flydigi-dsx [--device SELECTOR]
```

The selected device must advertise adaptive triggers and must be accessible to
the current user. A selector may be a node path, UID or UID prefix, MAC address,
or nickname.

| Option | Behavior |
|---|---|
| `--device SELECTOR` | Select the adaptive-trigger pad to drive. |
| `--port PORT` | Listen on a different loopback UDP port. Default: 7878. |
| `--quiet` | Suppress per-effect lines. |
| `--dump` | Decode datagrams without opening or writing a controller. |
| `--forward PORT` | Forward raw datagrams to another loopback UDP port. |

The listener binds only `127.0.0.1`; the bind address is not configurable.
Proton games on the same host can reach it, while a VM or another host cannot
without an external relay.

If no packet arrives within 45 seconds, the listener prints one reminder to
check that the game-side mod is installed and targets the selected port. Ctrl+C
clears both trigger effects before exit.

### Known limitation: dump and forwarding

`--dump` handles and logs each packet before the normal listener handler. It
therefore bypasses `--forward`: `--dump --forward PORT` reports the forwarding
destination but sends no datagrams. Use forwarding without `--dump`, or capture
the destination listener separately.

`tools/flydigid` starts `flydigi-dsx --quiet` for a bespoke route and passes its
selected primary pad through `--device`. Bespoke Auto mode defaults to off and
must be enabled explicitly.

## Testing

The parser and listener can be tested without a controller or game:

```bash
python3 tests/test_dsx.py
```

The suite covers mixed JSON types, ignored instructions, repeat suppression,
and command-81 payload construction.

## Download utility

Flydigi's public game-list response provides the archive URLs. Fetch a fresh
snapshot instead of relying on a URL copied into documentation:

```bash
tools/fetch-configs --all-mods
```

The command downloads every mod-backed archive into the gitignored `mods/`
directory. Filenames use:

```text
<three-digit-id>_<normalized-English-name>.zip
```

Spaces and slashes become underscores and the full base name is truncated to
48 characters before `.zip` is appended. An existing nonempty file is skipped;
an empty file is retried.

Use `tools/fetch-configs --monitor-configs` to extract XGameMonitor JSON files
to `configs/monitor/` instead of retaining the external executable archives.
