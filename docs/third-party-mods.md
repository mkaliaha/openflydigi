# Third-party game mods

Some games get adaptive-trigger support through **game-side mods** rather than anything we
implement. These are third-party modifications — REFramework, ScriptHookV, F4SE, Bannerlord's module
system — distributed by Flydigi.

**This project does not ship, install or support them.** They are listed here because
`tools/flydigi-dsx` already accepts what they emit, so if you choose to install one yourself it will
drive the controller with no further work.

## How they work

Each mod runs inside the game and sends DSX-protocol JSON over UDP to `127.0.0.1:7878`:

```lua
-- from the Resident Evil mods' udp_client/packet.lua
data = { instructions = { left:packet(), right:packet() } }
Client.get_port = function() return 7878 end
```

That is the same protocol and port `tools/flydigi-dsx` listens on, so:

```bash
tools/flydigi-dsx        # then launch the game with its mod installed
```

Caveats worth knowing: these hook into games via `dinput8.dll` proxies and script extenders, which
can be fragile under Proton and may interact badly with anti-cheat. Installing them is your call.

## Mods, by framework


### Bannerlord native module (C#)

- **Mount & Blade II Bannerlord** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/20230726/MB2_1.1.5.21456_20230726.zip)

### F4SE (Fallout 4 Script Extender)

- **Fallout 4** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/6/f4se2022093001.zip)

### REFramework Lua (RE Engine)

- **Monste Hunter Rise** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/202404/MonsterHunterRise_MOD_2024041901.zip)
- **Devil May Cry 5** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/8/devil5_2022093001.zip)
- **Resident Evil 7 Biohazard** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/11/RE7.zip)
- **Resident Evil 2** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/12/ref2_2022102101.zip)
- **Resident Evil 3** — [mod download](https://tencent-android.cdn.flydigi.com/PC/MOD/13/ref3_2022102101.zip)

### ScriptHookV (GTA V modding framework)

- **Grand Theft Auto V** — [mod download](http://api-web.cdn.flydigi.com/pcspacegame/2026/03/23/f0d3544345f5068ed5167cce0d9ea1d1.zip)

### Standalone telemetry reader (like Forza; could be reimplemented natively)

- **F1® 23** — [mod download](http://api-web.cdn.flydigi.com/pcspacegame/2025/10/11/3e91345f6c823893096d93de00853c36.zip)
- **F1® 24** — [mod download](http://api-web.cdn.flydigi.com/pcspacegame/2025/10/11/886b5d48b8c1f96eb80bccf28e22899d.zip)
- **F1® 25** — [mod download](http://api-web.cdn.flydigi.com/pcspacegame/2025/10/11/6e893fa326dda0b6b7fbec48f77fe75a.zip)

## Where these links come from

Flydigi's public, unauthenticated game list:

    GET https://api.flydigi.com/pc/adapter_trigger/list

`tools/fetch-configs --all-mods` downloads them all. URLs change when Flydigi updates a mod, so
re-fetch rather than relying on this file.
