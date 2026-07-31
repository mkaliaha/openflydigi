# Third-party game mods

Eleven games get adaptive-trigger support through **game-side mods** rather than anything in this
project — built on third-party frameworks, distributed by Flydigi.

Index: [PROGRESS.md](../PROGRESS.md). Tier table: [findings-games.md](findings-games.md).
DSX wire format: [PROTOCOL.md](../PROTOCOL.md) §4.

**This project does not ship, install or support them.** `tools/flydigi-dsx` accepts what they
emit, so a mod installed by hand drives the controller with no further work.

## Which eleven

Exactly the games `games.tier()` calls `bespoke`: an entry with a `modDownLoadUrl` whose `modName`
is neither `XGameMonitor.exe` nor `ForzaDualSense.exe` (`flydigi/games.py:62-68`). 46 of the 94
gamelist entries carry a mod download; the other 35 are the 31 monitor and 4 telemetry games, which
this project reimplements natively (`tools/flydigi-monitor`, `tools/flydigi-forza`).

## What a mod sends

Eight of the eleven run inside the game and send DSX-protocol JSON over UDP to `127.0.0.1:7878`.
The three F1 mods are standalone processes beside the game: they read its own telemetry output and
emit the same DSX JSON.

The five RE-engine mods carry the whole Lua client in the archive, under `udp_client/`:

```lua
-- instruction.lua
Instruction.TriggerType = {Left=1, Right=2}
Instruction.ModeType = {Normal=0, Resistant=1, Vib=2, Gap=3}
data.type = "1"
data.parameters = {"0", tostring(self.trigger), "19", tostring(self.mode),
                   tostring(self.param1), ... tostring(self.param4)}
-- packet.lua: one datagram carries both sides
data = { instructions = {self.left:Left():packet(), self.right:Right():packet()} }
-- client.lua
address = "127.0.0.1"; Client.get_port = function() return 7878 end
```

The two families disagree about JSON types: the RE mods quote everything, while Monster Hunter Rise
and DMC 5 send `data.type = 1` and `parameters = {0, self.trigger, 19, tostring(self.mode), ...}` —
bare numbers for the first three, strings for the rest. `flydigi/dsx.py` takes either form (§4), and
its `_as_int` also accepts floats.

`ModeType` is Flydigi's `AdapterTriggerType` renamed: 0, 1, 2, 3 are `MODE_NORMAL`, `MODE_RACE`,
`MODE_SNIPER` and `MODE_RECOIL` (`flydigi/effects.py:34-37`). The listener emits one command `81`
SetForceTrigger per effect ([PROTOCOL.md](../PROTOCOL.md) §3a), truncated to five params
(`flydigi/effects.py:359-361`).

The Lua mods send deltas, not state: `Packet:delta` replaces an unchanged side with a nil
instruction, which goes on the wire as type `0` with the `parameters` key absent entirely, and
`dsx.parse` drops it on the type check. When neither side changed the delta is empty and no datagram
is sent, so silence on the socket is normal while a trigger effect is held.

Only `TriggerUpdate` is acted on. The other six instruction types (§4) are skipped on the type alone
and their parameters never decoded, so a mod's lightbar effects never reach the pad, which drives
its RGB from `flydigi/lighting.py` instead.

`python3 tests/test_dsx.py` self-tests parsing, repeat suppression and the emitted command-`81`
layout, with no controller and no game.

## Running the listener

```bash
tools/flydigi-dsx        # then launch the game with its mod installed
```

- It binds `127.0.0.1` only. `--port` moves the port; nothing moves the address, so a mod running in
  a VM or on another host cannot reach it. A Proton game can — PROTOCOL.md §4.
- Identical consecutive effects per side are suppressed rather than rewritten at packet rate.
- Ctrl+C resets both triggers (`effects.clear_all`).
- If nothing arrives in the first 45 seconds it says once that these packets come from a mod inside
  the game.
- `--dump` logs each datagram's size and the trigger effects decoded from it, or
  `no trigger instructions`, and makes no HID writes, so it needs no controller. It decodes in place
  of the handler, so `--dump --forward` relays nothing while still logging that it will
  (`tools/flydigi-dsx:66-67,88-91`). `--quiet` drops the per-effect lines.
- `--forward PORT` relays the raw datagrams onward, to `127.0.0.1` only (`flydigi/dsx.py:156-159`).
- `tools/flydigid` starts `tools/flydigi-dsx --quiet` beside the game and stops it on exit. The
  bespoke route is not in `AUTO_BY_DEFAULT` (`flydigi/prefs.py:51`), so it does nothing until the
  game's Auto toggle is on; the app labels it "Third-party mod (needs flydigi-dsx)".

## Mods, by framework

| Game | Framework | Archive contents | Download |
|---|---|---|---|
| Monste Hunter Rise | REFramework Lua (RE Engine) | `dinput8.dll`, `reframework/autorun/flydigi_apex3/` Lua, LuaSocket `core.dll`, a per-weapon `weapons/*.default.json` | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/202404/MonsterHunterRise_MOD_2024041901.zip) |
| Devil May Cry 5 | REFramework Lua (RE Engine) | as above, with a per-character `players/*.default.json` | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/8/devil5_2022093001.zip) |
| Resident Evil 7 Biohazard | REFramework Lua (RE Engine) | the same under `reframework/autorun/FlydigiAdapterTrigger/`, with `weapon/trigger_config.default.json` and `reframework-d2d.dll` | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/11/RE7.zip) |
| Resident Evil 2 | REFramework Lua (RE Engine) | as RE 7 | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/12/ref2_2022102101.zip) |
| Resident Evil 3 | REFramework Lua (RE Engine) | as RE 7 | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/13/ref3_2022102101.zip) |
| Grand Theft Auto V | ScriptHookV (GTA V modding framework) | `ScriptHookV.dll`, `ScriptHookVDotNet`, `Scripts/AdapterTrigger.dll`, `Scripts/trigger.ini` (per-weapon `Mode=` and `param1..4`), `dinput8.dll`, `xinput1_4.dll`, `NativeTrainer.asi`, `args.txt` | [zip](http://api-web.cdn.flydigi.com/pcspacegame/2026/03/23/f0d3544345f5068ed5167cce0d9ea1d1.zip) |
| Fallout 4 | F4SE (Fallout 4 Script Extender) | `f4se_1_10_163.dll`, `Data/F4SE/Plugins/FlydigiAdapterTrigger.dll`, MCM, 25 MB Address Library `version-1-10-163-0.bin` | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/6/f4se2022093001.zip) |
| Mount & Blade II Bannerlord | Bannerlord native module (C#) | `Modules/FlydigiAdapterTrigger/` with `SubModule.xml` and `ModuleData/TriggerConfig/trigger_config.default.json` | [zip](https://tencent-android.cdn.flydigi.com/PC/MOD/20230726/MB2_1.1.5.21456_20230726.zip) |
| F1® 23 | Standalone telemetry reader (.NET) | one `AdapterTrigger_F1Game23.exe` | [zip](http://api-web.cdn.flydigi.com/pcspacegame/2025/10/11/3e91345f6c823893096d93de00853c36.zip) |
| F1® 24 | Standalone telemetry reader (.NET) | one `AdapterTrigger_F1Game24.exe` | [zip](http://api-web.cdn.flydigi.com/pcspacegame/2025/10/11/886b5d48b8c1f96eb80bccf28e22899d.zip) |
| F1® 25 | Standalone telemetry reader (.NET) | one `AdapterTrigger_F1Game25.exe` | [zip](http://api-web.cdn.flydigi.com/pcspacegame/2025/10/11/6e893fa326dda0b6b7fbec48f77fe75a.zip) |

Names are the gamelist's `enGameName` verbatim, and `games.find()` substring-matches on it
(`flydigi/games.py:38-49`), so the typo must be reproduced: "Monste Hunter Rise" is Flydigi's own
spelling, and the F1 titles carry the ® sign.

Six of the archives install a `dinput8.dll` proxy and Fallout 4 loads under F4SE — those can be
fragile under Proton and may interact badly with anti-cheat: the GTA V archive ships an `args.txt`
of `-nobattleye -noBE`, i.e. it expects the game launched with BattlEye disabled. Bannerlord's
native module and the three F1 executables hook nothing.

Fallout 4 is the only one of the eleven with a second route. It carries `isVibration` as well as a
mod, so its triggers can be driven by the pad-side vibration preset with no F4SE install at all.

The F1 executables are .NET builds on the `F1Game.UDP` library ("Library to parse UDP telemetry
packets from F1 23 game"), the same shape as Flydigi's Forza mod. `flydigi/forza.py` replaces the
Forza one natively; there is no equivalent module for F1.

## Mod list source

Flydigi's public, unauthenticated game list:

    GET https://api.flydigi.com/pc/adapter_trigger/list

`tools/fetch-configs --all-mods` downloads all 46 mod archives (~44 MB) into the gitignored `mods/`,
named `<id>_<enGameName>.zip`, skipping any already present. `--monitor-configs` extracts the JSON
config out of every XGameMonitor mod into `configs/monitor/`. URLs change when Flydigi updates a
mod, so re-fetch rather than relying on this file.
