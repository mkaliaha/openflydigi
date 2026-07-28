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

Two distinct trigger families exist, and the Apex 5's is `SetForceTrigger` — confirmed by
hardware (§7) and by the SDK, which gates `K6Trigger*` on `DeviceCode == "k6"` (the Apex 6).
This pad is `k5`.

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

| Effect | mode | params after `side, mode` |
|---|---|---|
| `Normal` | 0 | — |
| `Race` | 1 | `stroke, resistance(min 1), matchStroke` |
| `Sniper` | 2 | `stroke, pressureLevel(min 1), strength(min 1), frequency(min 1), matchStroke` |
| `Recoil` | 3 | `stroke, recoilStroke, strength(min 1), 0, matchStroke` |

| `Lock` | 4 | `stroke, strength(255 in every call Flydigi makes), matchStroke` |
| `Vibration` | 5 | `stroke, pressureLevel(min 1), strength(min 1), frequency(min 1), matchStroke` |
| `SyncWithGrip` | cmd 82 | `bindType, filter, scale, stroke, pressureLevel, strength, frequency` |

`min 1` is the builder's own clamp: it raises a zero rather than refusing the packet, so a
caller that sends 0 gets the weakest setting. `Recoil`'s fourth slot is genuinely empty — the
builder emits a 0 there and `matchStroke` follows it.

**Modes 2 and 3 are labelled the other way round in Space Station's UI, and the behaviour follows
the label.** Their picker binds `AdapterTriggerType_Sniper` to `trigger_mode_K2_recoil` ("Recoil",
zh 机枪 *machine gun*) and `AdapterTriggerType_Recoil` to `trigger_mode_K2_sniper` ("Sniper", zh
狙击) — verified in the bundled `index-*.js`, and consistent with the parameter panels, which give
mode 2 the `trigger_vibration_*` strings and mode 3 the `trigger_recoil_*` ones. Felt on hardware
the same way: mode 2 rattles, mode 3 resists and breaks through. The DualSense mapping agrees, since
its vibration/automatic-gun effect maps to mode 2.

So an effect has two names, and which one is right depends on who you are talking to:

| Mode | SDK enum | Space Station UI | Behaviour |
|---|---|---|---|
| 2 | `Sniper` | **Recoil** (机枪) | vibration/rattle |
| 3 | `Recoil` | **Sniper** (狙击) | breakthrough resistance |

This project uses the enum name in code and on the wire (`effects.sniper` is mode 2) and Space
Station's label in the UI, so that a recommendation to "use Sniper" picks the same effect in both
applications.

The mode numbers are `AdapterTriggerType`, and the same six are the first byte of the
per-profile block (§3c). `Race` is the racing-throttle resistance effect — the Forza Horizon
case. **Modes 0–4 are confirmed by feel on an Apex 5** (§7); mode 5 is the exception, and the
next few paragraphs are about why.

**Nothing in Flydigi's software ever sends mode 5.** Worth establishing before reading the
hardware results below, because it explains them. Every path that can produce a mode byte:

| Source | Modes it emits |
|---|---|
| Profile/config (`ControllerRepository.CreateForceAdapterConfig`) | 0, 1, 2, 3, 4 — stored type 5 becomes **command 82**, not mode 5 |
| DualSense relay (`PS5DataManager`, and our `relay.translate_ds5`) | 0, 1, 2, 3 only — never 4, never 5 |
| `ForceTriggerConfigVibration`, the mode-5 builder | `SetForceTriggerCommandFactory.cs:197` — defined, **never constructed anywhere** |

Nor is it for another pad. Pads with real trigger motors (`IsSupportTriggerVibration`: Vader 3, 4,
5) do not use `SetForceTrigger` for them at all — trigger vibration there is **command 18**,
`VibrationCommandFactory` with `VibrationType.Trigger`, levels in `[5]`/`[6]`. Every pad that *does*
have force triggers goes through the repository path above. So mode 5 is a vestigial enum slot, and
firmware that does nothing with it has never been asked to do anything else.

Two names worth keeping straight, because "Vibration" is a red herring twice over. The vibration
effect in real use is **mode 2**: the DualSense's own vibration/automatic-gun effect
(`data[11] == 6`) maps to it, and Space Station labels it 机枪, *machine gun*. And the "Vibration"
a user picks in their UI is the **stored** type 5, which is delivered as command 82 — the rumble
bind, which works.

**`Sniper` and `Vibration` take identical parameters. Mode 2 works. Mode 5 measured inconclusively**
— written three different ways in one session, so what follows is the measurements, not a
conclusion. Given that nothing sends it, this is a curiosity rather than a gap.

| # | Bind state | Effect | Rumble | Felt |
|---|---|---|---|---|
| 1 | the pad's own, untouched | mode 5 | yes | **buzzed** |
| 2 | suppressed (`82`, bindType 2, filter 255, scale 0, zero params) | mode 5 | yes | nothing |
| 3 | suppressed, same params | mode 2 | no | vibrates on press |
| 4 | `82` sent with **bindType 0** | Normal, and mode 5 | yes | nothing either side |

Run 3 is the control for run 2: the vibration path is alive with the bind zeroed, so mode 5's
silence there is not the suppression killing everything. Run 4 does **not** test an active bind —
`bindType` is `2` in every `SyncWithGrip` Flydigi constructs and in all 34 vibration games in the
gamelist, so `0` is not a value they ever send and appears to mean no bind at all. It was sent by
mistake here while trying to restore the pad, which is why runs after it read as silence.

So mode 5 did something once with the pad's own bind and nothing with the bind suppressed. Left
open, and not worth more bench time given that no caller exists: the run that would settle it is a
bindType-2 bind at working values with `Normal` on one trigger and mode 5 on the other, under one
rumble.

One thing this did settle, and it cost two wasted runs: **a config apply does not restore live bind
state.** It survives the switch, so an experiment that alters the bind leaves it altered until
something sets it back — "I re-applied the profile" is not a restore.

### 3c. The same effects, stored in a profile

A mapping config carries 20 bytes per trigger at offset 185, which is the same vocabulary
sitting in the pad rather than on the wire — so it applies with no host process:

```
[0]      effect mode (AdapterTriggerType)
[1]      bind type: 2 for the Vibration effect, 0 for every other
[2]      bind filter          [3] bind scale
[4..8]   bind params          [9] mixed border
[10..19] effect params
```

Which effect uses which parameter slot (`ControllerRepository.SaveTriggerAdapterConfig`):

| Effect | `[10]` | `[11]` | `[12]` | `[13]` | `[14]` |
|---|---|---|---|---|---|
| `Normal` | start | end | 0 | 0 | 0 |
| `Race` | start | resistance | 0 | 0 | 0 |
| `Sniper` | start | pressLevel | vibrationLevel | frequency | matchStart |
| `Recoil` | start | end | resistance | 0 | matchStart |
| `Lock` | start | 255 | 1 | 0 | 0 |
| `Vibration` | stroke | frequency | 1 | 90 | 0 |

`Vibration` is the one effect that reaches into the bind half: `filter` = its shielding value,
`scale` = its intensity coefficient, bind params = `[stroke, 1, 1, frequency, 0]`. Slots the
effect does not use are not free space — Lock's 255/1 and Vibration's 1/90 are constants
Flydigi writes, and every effect shares all ten slots, so a slot holds whatever the last effect
put there.

Slider bounds, from Space Station's own UI (not the byte range): travel positions run 0–192,
`Lock`'s position 20–200, `Vibration`'s intensity 0–200 and travel 1–200, everything else
1–255.

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
| `81` | SetForceTrigger — `Sniper` | ACK + **felt**: vibrates on its own past the travel point |
| `81` | SetForceTrigger — `Recoil` | ACK + **felt**: resists, then gives way |
| `81` | SetForceTrigger — `Lock` | ACK + **felt**: trigger stops dead at the position |
| `81` | SetForceTrigger — `Vibration` | ACK; what it produces is unresolved — see §3a |
| `82` (`0x52`) | SyncWithGrip (Tier-1 vibration bind) | ACK + **physically confirmed** |
| `0x12` | Rumble (SDL framing) | ACK, drives motors |

Every mode ACKed and echoed its own parameters back — `[success=1][mode][params…]`, with the
side byte dropped — so the pad is parsing the payload, not just acknowledging the command id.

**Additional findings.**
- **Replies are broadcast to every reader of the hidraw node.** A `Get info` ACK belonging to the
  desktop app's 30-second poll was read by a second process that had sent nothing of the sort.
  Anything that matches replies by command id can therefore pick up someone else's answer; this
  is the arbitration problem, observed rather than reasoned about.
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

Four of the six questions that used to sit here are answered, in §7 of this file and in PROGRESS.md,
and are recorded there rather than repeated: the Apex 5's family is `SetForceTrigger` (`K6Trigger*`
is gated on `DeviceCode == "k6"`, the Apex 6, and this pad is `k5`); the wired report ID is `0x03`
on the vendor node, found by its `06 a0 ff` descriptor prefix; no CRC byte is required for 81/82;
and `AcquireController` is not a precondition for trigger commands. What is genuinely still open:

- Report ID for the Apex 5 over the 2.4G dock and Bluetooth. Only wired has been tested.
- **Whether mode 5 (`Vibration`) does anything** — a curiosity, not a gap: nothing in Flydigi's
  stack sends it (§3a), so no route depends on the answer. Modes 0–4 are settled and felt (§7).
- Whether the pad honours a stored effect on profile switch without the host re-sending it. Effects
  applied live persist until changed, but that is not the same claim.

---

## 6. Implementation path (Linux)

1. `hidraw` writer implementing §2 framing + §3 commands.
2. Bench-verify with `Race` (`SetForceTrigger`) and `K6` realtime — both are directly observable.
3. UDP listener on 7878 speaking §4 DSX JSON → existing DSX mods work under Proton
   (Wine shares the host loopback, so `127.0.0.1:7878` from inside the prefix reaches a Linux daemon).
4. Optional: `/tmp/fcs.sock` protobuf server to drive the stock Electron UI on Linux.
5. Optional: telemetry providers (e.g. Forza "Data Out" UDP) for titles with no mod.
