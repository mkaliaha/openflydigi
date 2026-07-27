# Flydigi Apex 5 — ForceAdapt Trigger Protocol

Reverse-engineered from **Flydigi Space Station 4.2.0.9** (Windows installer, run under Wine).
Source: decompiled .NET assemblies from `SpaceStationService.exe` (single-file bundle).

Status: **verified against hardware** (Apex 5, wired, Linux hidraw). Commands `0x01`, `81`, `82`,
`0x12` confirmed accepted and physically effective. See §7 for what is confirmed vs still open.

---

## 1. Stack architecture

```
Electron UI  ──protobuf over named pipe ("fcs.sock")──►  SpaceStationService.exe (.NET)
                                                              │
game mod ──UDP JSON :7878 (DSX protocol)──► AdapterTriggerService ──► Flydigi.ControllerSdk
                                                              │              │
                                              PS5Driver (DualSense emulation) │
                                                                       Flydigi.Hid ──► hidapi ──► device
```

Key assemblies (in `bundle/`):
- `AdapterTriggerService.dll` — UDP ingress, game/mod lifecycle, DualSense emulation
- `Flydigi.ControllerSdk.dll` — command factories (the actual wire protocol)
- `Flydigi.Hid.dll` — HID transport
- `Flydigi.Basic.dll` — packet framing + CRC
- `GameFinder.Wine.dll` — game discovery incl. **Wine/Proton prefixes**

### Linux-relevant discoveries

1. **The Electron client already has a Unix-socket code path:**
   ```js
   this.pipePath = n || (process.platform === "win32"
       ? path.join("\\\\?\\pipe", "fcs.sock")
       : "/tmp/fcs.sock")
   ```
   A Linux service listening on `/tmp/fcs.sock` and speaking their protobuf would drive the
   real UI natively.
2. They ship `GameFinder.Wine.dll` — Wine/Proton game discovery is already a supported concept.
3. Ingress is the **DualSenseX (DSX) UDP protocol**, an open de-facto standard with an existing
   mod ecosystem. Flydigi did not write 60 game integrations; they adopted DSX's.

---

## 2. Packet framing

`AbstractCommand.CreateSimpleCommand(isNewProtocol: true)` → 32-byte buffer:

| Offset | Value |
|---|---|
| `[0]` | report ID — `XInput`→`165 (0xA5)`, `NewXInput`→`6`, else `5` |
| `[1]` | `0x5A` (90) |
| `[2]` | `0xA5` (165) |
| `[3]` | CommandId |
| `[4]` | payload length |
| `[5…]` | payload |

Legacy (`isNewProtocol: false`) is a 15-byte buffer: `[0]`=report ID, `[1]`=CommandId.

This is the **same V2 envelope SDL implements** in `SDL_hidapi_flydigi.c`
(`5A A5 <cmd> <len>`, report ID `0x03`) — SDL uses a different report ID because it drives a
different device mode.

### CRC

Not a real CRC — an 8-bit sum (`Flydigi.Common.util.ByteExtension.Crc`):

```csharp
byte Crc(byte[] v, int start, int end) {
    byte b = 0;
    for (int i = start; i < end; i++) b += v[i];
    return b;   // implicit mod 256
}
```
Note `end` is **exclusive**.

---

## 3. Command families

Two distinct trigger families exist. Which one an Apex 5 accepts is **unverified**.

### 3a. `SetForceTrigger` — effect-based (used by the DSX/adapter-trigger path)

| Mode | CommandId |
|---|---|
| NewXInput | `81` (`82` if SyncWithGrip) |
| XInput | `48` |
| DInput | `160` |

NewXInput layout:
```
[0]=6  [1]=5A  [2]=A5  [3]=81  [4]=10  [5]=applyFlag  [6…]=params
                                        applyFlag: 1=apply, 0=preview only
SyncWithGrip: [3]=82  [4]=11  [5…]=params   (no applyFlag byte)
```

Effect vocabulary — `params[0]`=side, `params[1]`=mode:

`ForceTriggerSide { Left=1, Right=2, Both=3 }`

| Effect | mode | params |
|---|---|---|
| `Normal` | 0 | `[side, 0]` |
| `Race` | 1 | `[side, 1, stroke, resistance(min 1), matchStroke]` |
| `Sniper` | — | `side, stroke, pressureLevel, strength, frequency, matchStroke` |
| `Recoil` | — | `side, stroke, recoilStroke, strength, matchStroke` |
| `Lock` | — | `side, stroke, strength=255, matchStroke=true` |
| `Vibration` | — | `side, stroke, pressureLevel, strength, frequency, matchStroke` |
| `SyncWithGrip` | — | `side, bindType, filter, scale, stroke, pressureLevel, strength, frequency` |

`Race` is the racing-throttle resistance effect — the Forza Horizon case.

### 3b. `K6Trigger*` — low-level / waveform (newer hardware generation)

| Command | ID | Layout |
|---|---|---|
| **Mode** | `83` | `[4]=4, [5]=triggerMode, [6]=gripMode, [7]=Crc(3, 3+4)` |
| **Waveform** | `85` | `[4]=27, [5]=side, [6..7]=totalLen BE, [8]=segment#, [9..29]=21B chunk, [30]=Crc(3, 3+27)` |
| **Realtime** | `87` | `[4]=28, [5]=channel, 8×3B samples at `6+i*3`, `[30]=Crc(1,30)` |

Realtime sample = `(Trigger, LeftGrip, RightGrip)`, each 0–255. 8 samples per 32-byte packet.

Enums:
```
K6TriggerMode  { Local, BindGrip, Realtime }
K6GripMode     { RotorMapping, Realtime }
K6TriggerSide  { Left, Right }
K6WaveformMode { Standard, Special }
K6WaveformShape{ Sine, Square, Sawtooth, Triangle }
K6StrengthMappingTarget { LeftTrigger, RightTrigger, LeftMotor, RightMotor }
```

Config records:
```csharp
K6LocalModeConfig(Side, StartTravel, EndTravel, LoopEnabled, LoopInterval, StartGain, EndGain)
K6StrengthMappingConfig(Target, SegmentIndex, StartIntensity, EndIntensity,
                        StartFrequency, EndFrequency, StartAmplitude, EndAmplitude,
                        WaveformMode, WaveformShape)
K6RealtimeFrame(EffectiveChannel, IReadOnlyList<K6RealtimeSample>)
K6RealtimeSample(Trigger, LeftGrip, RightGrip)
```

**`K6TriggerMode.Local` matters a lot**: with `K6LocalModeConfig` the controller runs a
travel-position-driven effect autonomously (start/end travel, gain ramp, optional loop). No host
streaming required — good for games with zero integration.

ACK: `IsAck(data)` → `data.Length > 5 && data[2] == CommandId`; success = `data[5] == 1`.
Retries: 3, timeout 500 ms.

---

## 4. Ingress — DSX UDP protocol

`AdapterTriggerRunner` listens on UDP **port 7878** (default), forwards raw datagrams to **8787**.
Config: `~/Documents/Flydigi/adapter_trigger_config.json`
```json
{ "Enable": false, "Port": 7878, "ForwardPort": 8787, "UsingBy": 4 }
```
Service only starts when `Enable == true && UsingBy == 4`.

Payload is ASCII JSON:
```json
{"instructions":[{"type":1,"parameters":[...]}]}
```
```
InstructionType { Invalid=0, TriggerUpdate=1, RGBUpdate=2, PlayerLED=3,
                  TriggerThreshold=4, MicLED=5, PlayerLEDNewRevision=6 }
Trigger { Invalid=0, Left=1, Right=2 }
```

`TriggerUpdate` → `SetForceTrigger` mapping (`ControllerBusinessService.OnTriggerCommandReceived`):
```
params[1]        → side   (2 = Right, else Left)
params[3..]      → mode + effect params
params[0], [2]   → ignored
```
Duplicate consecutive packets are suppressed (`lastPacket` equality check).

---

## 7. Hardware verification results

Tested on a wired Apex 5 (`37d7:2501`) via `/dev/hidraw4`, using `tools/flydigi_cmd.py`.

**Transport — confirmed.**
- Vendor interface is the hidraw node whose descriptor starts `06 a0 ff` (usage page `0xffa0`).
  Wired Apex 5 exposes two nodes; `hidraw3` is the keyboard/mouse composite, **`hidraw4`** is
  the command interface (`report 0x03: output 31B`, `report 0x04: input 31B`, `report 0x08: output 12B`).
- **Report ID is `0x03`**, not the `6` that `TakeEndpointByDevice()` returns for `NewXInput`.
  SDL uses `0x03` too. The `6`/`165`/`5` values belong to other connection modes.
- Packets are written as 32 bytes: `03 5A A5 <cmd> <len> <payload…>`.

**ACK format — confirmed.** Replies arrive on report `0x04`, same magic, command echoed:
```
TX  03 5a a5 01 00 …
RX  04 5a a5 01 01 00 80 01 …
```
Decoding as `ParseAckData` does (strip report-ID byte, then index): `data[2]` = command id,
`data[5]` = success flag. `[6] = 0x80` = 128 = Apex 5 device id (matches SDL's `case 128/129`).

**Commands confirmed working.**

| Cmd | What | Result |
|---|---|---|
| `0x01` | Get info | ACK, returns device id 128 |
| `81` (`0x51`) | SetForceTrigger — `Race` | ACK + **physically felt resistance** |
| `81` | SetForceTrigger — `Normal` | ACK, clears the effect |
| `82` (`0x52`) | SyncWithGrip (Tier-1 vibration bind) | ACK + **physically confirmed** |
| `0x12` | Rumble (SDL framing) | ACK, drives motors |

**Additional findings.**
- **No checksum byte is required for `81`/`82`** — packets sent with the CRC field left zero were
  accepted. The `Crc()` sum only appears in the `K6Trigger*` builders.
- **Effects persist in controller state** with no host software running, until explicitly changed
  (`Normal`). Not a streamed-only feature.
- Tier-1 "vibration" games use `82`/SyncWithGrip, which routes the game's ordinary rumble into the
  trigger motors — no game integration and no trigger press required. This is trigger *haptics*,
  not adaptive resistance.
- The game-list API is **public and unauthenticated**:
  `GET https://api.flydigi.com/pc/adapter_trigger/list` → 94 games with per-game trigger configs
  and mod download URLs. No app, no login, no headers.

## 5. Open questions

- Which family the Apex 5 firmware actually accepts (`SetForceTrigger` 81/82 vs `K6Trigger` 83/85/87).
- Report ID for the Apex 5 in each connection mode (wired / 2.4G dock / BT). Wired exposes
  `hidraw3` (input1) and `hidraw4` (input2) — which one takes commands is untested.
- `SetForceTrigger` NewXInput builder sets no CRC byte; `K6Trigger*` does. Unverified whether
  firmware requires one.
- Mode/param byte values for Sniper / Recoil / Lock / Vibration (only Normal=0 and Race=1 confirmed).
- Whether `AcquireController` / heartbeat is a precondition for trigger commands (SDL sends `0x1C`).

---

## 6. Implementation path (Linux)

1. `hidraw` writer implementing §2 framing + §3 commands.
2. Bench-verify with `Race` (`SetForceTrigger`) and `K6` realtime — both are directly observable.
3. UDP listener on 7878 speaking §4 DSX JSON → existing DSX mods work under Proton
   (Wine shares the host loopback, so `127.0.0.1:7878` from inside the prefix reaches a Linux daemon).
4. Optional: `/tmp/fcs.sock` protobuf server to drive the stock Electron UI on Linux.
5. Optional: telemetry providers (e.g. Forza "Data Out" UDP) for titles with no mod.
