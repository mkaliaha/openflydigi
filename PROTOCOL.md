# Flydigi Apex 5 — ForceAdapt Trigger Protocol

Reverse-engineered from **Flydigi Space Station 4.2.0.9** (Windows installer, run under Wine).
Source: decompiled .NET assemblies from `SpaceStationService.exe` (single-file bundle).

Accepted and physically effective on a wired Apex 5 over Linux hidraw: `0x01`, `81`, `82`, `0x12`,
the stored-config families `161`–`166` and `167`–`169` (§9), the screen settings `19`/`242` (§8c)
and the serial screen upload behind `31` (§8d). §7 has the detail, §5 what is still open.

| § | Subject |
|---|---|
| 1 | Stack architecture and the assemblies the protocol was read out of |
| 2 | Packet framing — report id, length byte, checksum, retries |
| 3 | Trigger commands: live (3a), the K6 family (3b), the same effects stored in a profile (3c) |
| 4 | Ingress — the DSX UDP protocol, and Forza's Data Out telemetry |
| 5 | Open questions |
| 6 | Implementation path on Linux |
| 7 | Apex 5 behaviour on the wire |
| 8 | The screen — image format (8a), the HID picture family (8b), settings (8c), the serial upload (8d), recovery from command 31 (8e) |
| 9 | Stored configs — the packetised blob transfer |
| 10 | The CD2 charging dock — framing (10a), commands (10b), the LED transfer (10c), why it plays frames (10d) |

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

### Linux-relevant properties of the stack

1. **The Electron client already has a Unix-socket code path:** `pipePath` is
   `\\?\pipe\fcs.sock` on win32 and `/tmp/fcs.sock` everywhere else.
2. They ship `GameFinder.Wine.dll` — Wine/Proton game discovery is already a supported concept.
3. Ingress is the **DualSenseX (DSX) UDP protocol** (§4), an open de-facto standard Flydigi adopted
   rather than invented, so the eleven third-party mods in their 94-game list drive the pad
   unmodified — [docs/third-party-mods.md](docs/third-party-mods.md).

---

## 2. Packet framing

`AbstractCommand.CreateSimpleCommand(isNewProtocol: true)` → 32-byte buffer:

| Offset | Value |
|---|---|
| `[0]` | report ID — `XInput`→`165 (0xA5)`, `NewXInput`→`6`, else `5` |
| `[1]` | `0x5A` (90) |
| `[2]` | `0xA5` (165) |
| `[3]` | CommandId |
| `[4]` | **length — payload length + 2**, because it counts the command and length bytes themselves |
| `[5…]` | payload |
| `[3+len]` | 8-bit sum over `[3, 3+len)` |

`CreateSimpleCommand` does not set `[4]`; each command factory does, so read the length per command.

`flydigi/blobs.py` follows the `+2` rule for every checksummed command. `flydigi/device.py`'s
`build`, which serves 81, 82 and 18, writes the **raw payload length and no checksum** — the pad
accepts both, see §3a and §7. A capture from this project will therefore disagree with the table
above on those three commands without being malformed.

Of the SDK builders that emit this envelope, `SetForceTriggerCommandFactory` (81, 82) and
`VibrationCommandFactory` (18) are the only two that set no checksum byte at all. The rest end with
`Crc(3, 3 + len)` — 19, 20–23, 29, 31, 161–169, 171 and 242 among them. The picture family 208–211
is checksummed but not from `[3]`: it is built in the legacy envelopes, so its sum starts at `[1]`
in the XInput branch and `[2]` in the DInput one (§8b). Command 87 also sums from `[1]` (§3b).

Legacy (`isNewProtocol: false`) is a 15-byte buffer: `[0]`=report ID, `[1]`=CommandId.

This is the **same V2 envelope SDL implements** in `SDL_hidapi_flydigi.c`
(`5A A5 <cmd> <len>`, report ID `0x03`) — the same report ID this project uses. The SDK's
`6`/`165`/`5` belong to other connection modes; see §7.

**The default is 3 retries with a 500 ms timeout** — the constructor defaults on
`AbstractControllerCommand`. The commands that override it are the flash writers — 166, 171 and 175
(reset a slot) at 10 000 ms — and two of the picture family, 209 at 1000 ms and 210 at 30 000 ms.

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
hardware (§7) and by the SDK, which gates `K6Trigger*` on a device type or code this pad does not
report. This pad is `k5`; `k6` is §3b. For the device codes generally — `k2` is the Apex 4, not the
Apex 2 — see [docs/findings-other-devices.md](docs/findings-other-devices.md).

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

**`Both=3` is in the enum and the pad ignores it.** Measured on a wired Apex 5 with rumble pulses
marking each phase: one command with `side=3` at full resistance produced **no resistance at all**,
twice, while two commands with `side=1` and `side=2` between them produced it every time. All three
ACKed — an ACK means the firmware parsed the shape of what you sent and nothing more.

`SetForceTriggerConfigImpl` takes a *left* config and a *right* config and sends **two separate
commands**, so `side=3` is a value Flydigi's own code never produces. **Send one command per
trigger.** `device.SIDE_BOTH` exists because the enum has it; `tests/fake_pad.py` models it as
ACK-and-do-nothing so a caller that addresses `Both` cannot look like it worked.

| Effect | mode | params after `side, mode` |
|---|---|---|
| `Normal` | 0 | — |
| `Race` | 1 | `stroke, resistance(min 1), matchStroke` |
| `Sniper` | 2 | `stroke, pressureLevel(min 1), strength(min 1), frequency(min 1), matchStroke` |
| `Recoil` | 3 | `stroke, recoilStroke, strength(min 1), 0, matchStroke` |
| `Lock` | 4 | `stroke, strength(255 in every call Flydigi makes), matchStroke` |
| `Vibration` | 5 | `stroke, pressureLevel(min 1), strength(min 1), frequency(min 1), matchStroke` |

**Command 82 has no mode byte**, so it is not a row in that table: its payload is
`side, bindType, filter, scale, stroke, pressureLevel, strength, frequency` with `[4]=11` and no
applyFlag (`ForceTriggerConfigSyncWithGrip.CreateParams`, `flydigi/effects.py`).

`min 1` is the builder's own clamp: it raises a zero rather than refusing the packet, so a
caller that sends 0 gets the weakest setting. `Recoil`'s fourth slot is genuinely empty — the
builder emits a 0 there and `matchStroke` follows it.

**One case is rewritten, not clamped, and only on the live path.** `ForceTriggerConfigCommon` forces
`matchStroke` to 0 for `Race` (mode 1) when `stroke` is 0 and `matchStroke` is 1:

```csharp
if (command[1] == 1 && command[2] == 0 && command[4] == 1) { command[4] = 0; }
```

Indices are into the params: `[0]` side, `[1]` mode, `[2]` stroke, `[3]` resistance, `[4]`
matchStroke. It happens silently — no error, no report — so a packet capture will disagree with what
the caller asked for.

Which builder runs decides whether it applies:

| Path | Builder | Rewrite? |
|---|---|---|
| DSX / adapter ingress (`ControllerBusinessService.cs:1671`) | `ForceTriggerConfigCommon` | **yes** |
| DualSense translation (`:1805`, `PS5DataManager.ConvertToTriggerCommand`) | `ForceTriggerConfigCommon` | **yes** |
| stored profile config (`ControllerRepository.cs:832`, `:859`) | `ForceTriggerConfigRace` | no — only `resistance` clamped to ≥1 |

So a live Race at stroke 0 never gets input matching, whatever the mod asked for, while the same
effect stored in a profile keeps it. `flydigi/effects.py`'s `common_effect_payload` mirrors this and
is likewise reached only from the live paths — `dsx.py`, `forza.py`, `monitor.py` and the two
DualSense relays; profile effects are written as blob bytes (§9) and never pass through it.

**The length byte is not enforced for 81.** Flydigi always write `[4]=10`; the pad accepts shorter
values matching the real payload, and the effect is felt either way — the same latitude §7 records
for the missing checksum on 81/82.

**Modes 2 and 3 are labelled the other way round in Space Station's UI, and the behaviour follows
the label.** Their picker binds `AdapterTriggerType_Sniper` to `trigger_mode_K2_recoil` ("Recoil",
zh 机枪 *machine gun*) and `AdapterTriggerType_Recoil` to `trigger_mode_K2_sniper` ("Sniper", zh
狙击) — verified in the bundled `index-*.js`, and consistent with the parameter panels, which give
mode 2 the `trigger_vibration_*` strings and mode 3 the `trigger_recoil_*` ones. Felt on hardware
the same way: mode 2 rattles, mode 3 resists and breaks through. The DualSense mapping agrees: its
vibration/automatic-gun effect (`data[11] == 6`) maps to mode 2.

An effect therefore has two names:

| Mode | SDK enum | Space Station UI | Behaviour |
|---|---|---|---|
| 2 | `Sniper` | **Recoil** (机枪) | vibration/rattle |
| 3 | `Recoil` | **Sniper** (狙击) | breakthrough resistance |

This project uses the enum name in code and on the wire (`effects.sniper` is mode 2) and Space
Station's label in the UI, so that a recommendation to "use Sniper" picks the same effect in both
applications.

The mode numbers are `AdapterTriggerType`, and the same six are the first byte of the
per-profile block (§3c). `Race` is the racing-throttle resistance effect — the Forza Horizon
case. **Modes 0–4 are confirmed by feel on an Apex 5** (§7).

**Nothing in Flydigi's software ever sends mode 5.** Every path that can produce a mode byte:

| Source | Modes it emits |
|---|---|
| Profile/config (`ControllerRepository.CreateForceAdapterConfig`) | 0, 1, 2, 3, 4 — stored type 5 becomes **command 82**, not mode 5 |
| DualSense relay (`PS5DataManager`, and `flydigi/relay.py`) | 0, 1, 2, 3 only — never 4, never 5 |
| `ForceTriggerConfigVibration`, the mode-5 builder | `SetForceTriggerCommandFactory.cs:197` — defined, **never constructed anywhere** |

Nor is it for another pad. Pads with real trigger motors (`IsSupportTriggerVibration`: Vader 3, 4,
5) do not use `SetForceTrigger` for them at all — trigger vibration there is **command 18** (`0x12`,
the same id §7 lists as grip rumble), `VibrationCommandFactory` with `VibrationType.Trigger`. In the
NewXInput branch the levels are at `[7]`/`[8]`; grip levels stay at `[5]`/`[6]`. Every pad that *does*
have force triggers goes through the repository path above. So mode 5 is a vestigial enum slot.

`Sniper` and `Vibration` take byte-identical parameters; the mode byte is the only difference.
Mode 2 works. Mode 5 produced a buzz with the pad's own grip bind in place and nothing with the bind
suppressed by `82` at bindType 2, filter 255, scale 0 and zero params; mode 2 vibrates on press
under the same suppression, so the silence is not the suppression killing the vibration path. Mode 5
ACKs and visibly seats the triggers either way, so the ACK settles nothing (§5).

**On the wire, `bindType` is 2** — every `SyncWithGrip` Flydigi construct passes it
(`flydigi/effects.py:439`), and all 34 gamelist entries flagged `isVibration` carry `vibType: 2`.
`0` appears to mean no bind at all: with it set, neither `Normal` nor mode 5 produces anything under
rumble. The stored block writes 2 for the Vibration effect and 0 for every other (§3c). (34 is the
flag count; the vibration *tier* is 33, because Fallout 4 also ships a mod and `games.tier()`
classifies it as bespoke.)

**The live bind survives a config apply.** Applying a config does not restore it, so anything that
alters the bind leaves it altered until something sets it back.

### 3b. `K6Trigger*` — low-level / waveform, gated to `k6` in the SDK

**Nothing in this section is hardware-verified — the layouts are transcribed from the SDK.** It
gates this family on
`ControllerType == NewXInput && (DeviceType == 149 || DeviceCode == "k6")`
(`ControllerSdk.IsK6TriggerProtocolSupported`), so a pad reporting DeviceType 149 passes even if the
code lookup fails, and a non-NewXInput connection never does. `DeviceCode == "k6"` resolves to
`GenerateControllerApex6` — `DeviceType.K6 = 149`, `K6Pro = 150`, and
`RecognizeDeviceCodeFromProductName` returns `k6` for a product name containing "APEX" and a 6.

The SDK carries the support ahead of the hardware —
[docs/findings-other-devices.md](docs/findings-other-devices.md).

| Command | ID | Layout |
|---|---|---|
| **Mode** | `83` | `[4]=4, [5]=triggerMode, [6]=gripMode, [7]=Crc(3, 3+4)` |
| **Local mode** | `84` | `[4]=9, [5]=side, [6]=startTravel, [7]=endTravel, [8]=loopEnabled, [9]=loopInterval, [10]=startGain, [11]=endGain, [12]=Crc(3, 3+9)` |
| **Waveform** | `85` | `[4]=27, [5]=side, [6..7]=totalLen BE, [8]=segment#, [9..29]=21B chunk, [30]=Crc(3, 3+27)` |
| **Strength mapping** | `86` | `[4]=12, [5]=target, [6]=segmentIndex (0..9), [7]=startIntensity, [8]=endIntensity, [9]=startFrequency, [10]=endFrequency, [11]=startAmplitude, [12]=endAmplitude, [13]=waveformMode, [14]=waveformShape, [15]=Crc(3, 3+12)` |
| **Realtime** | `87` | `[4]=28, [5]=channel, 8×3B samples at 6+i*3, [30]=Crc(1,30)` |

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

In `K6TriggerMode.Local`, with a `K6LocalModeConfig` carried by command 84, the controller runs a
travel-position-driven effect autonomously — start/end travel, gain ramp, optional loop — with no
host streaming.

ACK: `IsAck(data)` → `data.Length > 5 && data[2] == CommandId`; success = `data[5] == 1`.

### 3c. The same effects, stored in a profile

A mapping config carries 20 bytes per trigger at offset 185, which is the same vocabulary sitting in
the pad rather than on the wire:

```
[0]      effect mode (AdapterTriggerType)
[1]      bind type: 2 for the Vibration effect, 0 for every other
[2]      bind filter          [3] bind scale
[4..8]   bind params          [9] mixed border
[10..19] effect params
```

**Storing an effect does not engage it.** Measured by putting `Lock` into the blob, applying, and
finding the triggers loose.
The effect starts when a live command 81 (or 82 for stored type 5) carries the stored bytes back
out: Space Station sends exactly that 500 ms after every applied-config read
(`ControllerBusinessService.cs:1595`), and `effects.engage_stored` is the same. Write the blob first
and send the live commands after; the reverse order silently loses the write.

Which effect uses which parameter slot (`ControllerRepository.SaveTriggerAdapterConfig`):

| Effect | `[10]` | `[11]` | `[12]` | `[13]` | `[14]` |
|---|---|---|---|---|---|
| `Normal` | start | end | 0 | 0 | 0 |
| `Race` | start | resistance | 0 | 0 | 0 |
| `Sniper` | start | pressLevel | vibrationLevel | frequency | matchStart |
| `Recoil` | start | end | resistance | 0 | matchStart |
| `Lock` | start | 255 | 1 | 0 | 0 |
| `Vibration` | stroke | frequency | 1 | 90 | 0 |

**`Normal`'s `start`/`end` are inert on an Apex 5** — the pair at blob offsets 195/196 and 215/216.
The trigger travel window this pad plays is the **curve block at blob offset 123**
(`mapping.OFF_TRIGGER_CURVE`; the force-trigger block is `OFF_FORCE_TRIGGER = 185`). Measured with
`tools/trigger-stroke-probe`: a degenerate 0..16 window in the curve block gave 17 distinct evdev
values against the other trigger's 240 in the same run, while the same window in the parameter pair
gave 238 against 239. The probe takes exactly one of `--curve`, `--param`, `--lock` or `--baseline`
a run, gives the `--side` trigger the window and reads the other as the control; 0..16 is its
default `--start`/`--end`. Both triggers still span 0..255 output, so the window moves physical
travel rather than what the game reads. Space Station's one "Stroke Setting" slider has two destinations
chosen by `supportAdaptTrigger` and is hidden entirely on an adaptive pad
(`triggerStrokeUsable = !supportAdaptTrigger`), so their k5 write path is the dead one.

The window has nothing to act on under `Lock`, which makes the axis digital (§7).

`Vibration` is the one effect that reaches into the bind half: `filter` = its shielding value,
`scale` = its intensity coefficient, bind params = `[stroke, 1, 1, frequency, 0]`. Slots the
effect does not use are not free space — Lock's 255/1 and Vibration's 1/90 are constants
Flydigi writes, and every effect shares all ten slots, so a slot holds whatever the last effect
put there.

Slider bounds, from Space Station's own UI (not the byte range): travel positions run 0–192,
`Lock`'s position 20–200, `Vibration`'s intensity 0–200 and travel 1–200, everything else
1–255.

---

## 4. Ingress — DSX UDP protocol and Forza Data Out

`AdapterTriggerRunner` listens on UDP **port 7878** (default), forwards raw datagrams to **8787**.
Config: `~/Documents/Flydigi/adapter_trigger_config.json`
```json
{ "Enable": false, "Port": 7878, "ForwardPort": 8787, "UsingBy": 4 }
```
Service only starts when `Enable == true && UsingBy == 4`.

Payload is ASCII JSON:
```json
{"instructions":[{"type":1,"parameters":[0, side, 19, mode, p1, p2, p3, p4]}]}
```
```
InstructionType { Invalid=0, TriggerUpdate=1, RGBUpdate=2, PlayerLED=3,
                  TriggerThreshold=4, MicLED=5, PlayerLEDNewRevision=6 }
Trigger { Invalid=0, Left=1, Right=2 }
```

**`type` is accepted as a name as well as an integer** — `"TriggerUpdate"`, case-insensitive with
underscores stripped — because Flydigi deserialise with Newtonsoft, which takes either form, and
mods in the wild use both. Every parameter is coerced from a string for the same reason, and
datagrams may be NUL-padded. Matching on `type == 1` alone drops the mods that send the name.

`TriggerUpdate` → `SetForceTrigger` mapping (`ControllerBusinessService.OnTriggerCommandReceived`):
```
params[1]        → side   (2 = Right, else Left)
params[3..]      → mode + effect params
params[0], [2]   → ignored (controller index, and a constant 19)
```
Duplicate consecutive packets are suppressed (`lastPacket` equality check).

This is implemented by `flydigi/dsx.py` (`DSX_PORT = 7878`, `FORWARD_PORT = 8787`), driven by
`tools/flydigi-dsx` (`--port`, `--forward`, `--dump`, `--quiet`). Only `TriggerUpdate` is acted on;
`RGBUpdate`, `PlayerLED`, `MicLED` and `TriggerThreshold` are skipped on the `type` alone, their
parameters never read (`flydigi/dsx.py:106-109`) —
[docs/third-party-mods.md](docs/third-party-mods.md).

Wine shares the host loopback, so `127.0.0.1:7878` from inside a Proton prefix reaches a Linux
daemon unchanged.

**Forza's "Data Out" stream is the second ingress protocol** — UDP port 5300, and the whole
`DataPacket` layout, field name → type, byte offset and whether the buffer offset applies, is
`FIELDS` in `flydigi/forza.py`. Only the lengths in
`BUFFER_OFFSETS` decode — 311 at offset 0, 324 at offset 12 — and `parse` returns nothing for any
other size until `tools/flydigi-forza --accept LEN:OFFSET` adds one; the offset shifts only the
fields from byte 232 on, the 232-byte sled below them being stable across game versions, which is
why one number is enough and why the tool suggests `LEN-312` for a size it does not know. And
`forza.listen()` binds **127.0.0.1** with no flag to change it, so the game has to be on this
machine — a Proton prefix qualifies, as above.

---

## 5. Open questions

- Report ID over **Bluetooth**. Report ID `0x03` on the `06 a0 ff` node holds over the 2.4G dongle
  as well as wired, which `flydigi/device.py` relies on; Bluetooth is untested.
- **Whether mode 5 (`Vibration`) does anything.** Nothing in Flydigi's stack sends it, so no route
  depends on the answer (§3a).
- **Whether the upgrade-mode flag is volatile** — whether a pad switched with command 31 but never
  written to comes back on its own (§8e). Nothing in the decompiled code writes the flag to flash,
  and it lives in firmware that has not been dumped.

---

## 6. Implementation path (Linux)

The hidraw writer (§2 framing, §3 commands) is `flydigi/device.py` and `flydigi/effects.py`; the
DSX listener is `flydigi/dsx.py` + `tools/flydigi-dsx`; the telemetry provider for titles with no
mod is `flydigi/forza.py` + `tools/flydigi-forza`, reading Forza's "Data Out" UDP stream.

Nothing here implements a `/tmp/fcs.sock` protobuf server, which would drive the stock Electron UI
on Linux (§1). [PROGRESS.md](PROGRESS.md) is the status index.

---

## 7. Apex 5 behaviour on the wire

Tested on a wired Apex 5 (`37d7:2501`) via `/dev/hidraw4`, using `python3 tools/flydigi_cmd.py
<subcommand>`. Subcommands: `info, listen, normal, race, sniper, recoil, lock, vibrate, k6mode,
k6realtime, bind, rumble, game, raw`, with `--device` and `--report-id` as globals and
`raw --sum-range start,end,pos` for placing a checksum by hand.

**Transport.**
- Vendor interface is the hidraw node whose descriptor starts `06 a0 ff` (usage page `0xffa0`).
  Wired Apex 5 exposes two nodes; `hidraw3` is the keyboard/mouse composite, **`hidraw4`** is
  the command interface (`report 0x03: output 31B`, `report 0x04: input 31B`, `report 0x08: output 12B`).
- **Report ID is `0x03`**, not the `6` that `TakeEndpointByDevice()` returns for `NewXInput`.
  SDL uses `0x03` too. The `6`/`165`/`5` values belong to other connection modes.
- Packets are written as 32 bytes: `03 5A A5 <cmd> <len> <payload…>`.

**ACK format.** Replies arrive on report `0x04`, same magic, command echoed:
```
TX  03 5a a5 01 00 …
RX  04 5a a5 01 01 00 80 01 …
```
Decoding as `ParseAckData` does (strip report-ID byte, then index): `data[2]` = command id,
`data[5]` = success flag. `[6] = 0x80` = 128 = Apex 5 device id (matches SDL's `case 128/129`).

**Commands confirmed working on hardware.**

| Cmd | What | Result |
|---|---|---|
| `0x01` | Get info | ACK, returns device id 128 |
| `81` (`0x51`) | SetForceTrigger — `Race` | ACK + **physically felt resistance** |
| `81` | SetForceTrigger — `Normal` | ACK, clears the effect |
| `81` | SetForceTrigger — `Sniper` | ACK + **felt**: vibrates on its own past the travel point |
| `81` | SetForceTrigger — `Recoil` | ACK + **felt**: resists, then gives way |
| `81` | SetForceTrigger — `Lock` | ACK + **felt**: trigger stops dead at the position, and the axis goes digital — see below |
| `81` | SetForceTrigger — `Vibration` | ACK; what it produces is unresolved — see §3a |
| `82` (`0x52`) | SyncWithGrip (Tier-1 vibration bind) | ACK + **physically confirmed** |
| `0x12` (18) | Grip rumble — `[4]=6, [5]=leftLevel, [6]=rightLevel`, `VibrationCommandFactory` NewXInput branch. SDL sends the identical packet; the layout is Flydigi's own | ACK, drives motors |

Every mode ACKed and echoed its own parameters back — `[success=1][mode][params…]`, with the
side byte dropped — so the pad is parsing the payload, not just acknowledging the command id.

**`Lock` makes the trigger axis digital.** With it on the axis reports 0 or 255 and nothing between:
2 distinct values and 35 evdev events over a 15-second run of pulls, against ~240 values and ~1100
events with no effect. That is why the profile's travel window (§3c) does nothing under `Lock` —
there is no analogue reading to rescale — while under `Race` the same window works normally, 2
distinct values against a control's 29 in the same run.

**Additional findings.**
- **Replies are broadcast to every reader of the hidraw node**, so anything that matches replies by
  command id can pick up another process's answer: a `Get info` ACK belonging to the desktop app's
  30-second poll was read by a second process that had sent nothing of the sort. Arbitration is
  `Controller.claim()`, an advisory `flock(2)` on the node —
  [docs/findings-steam.md](docs/findings-steam.md). `send` drains already-queued replies before
  writing and takes an `until` predicate; without one it always burns its full timeout, which turns
  a thousand-packet one-for-one exchange from two seconds into nine minutes.
- **No checksum byte is required for `81`/`82`** — packets sent with the CRC field left zero were
  accepted; Flydigi's own builders for 81, 82 and 18 set none either (§2).
- **Effects persist in controller state** with no host software running, until explicitly changed
  (`Normal`). Not a streamed-only feature.
- Tier-1 "vibration" games use `82`/SyncWithGrip, which routes the game's ordinary rumble into the
  trigger motors — no game integration and no trigger press required. This is trigger *haptics*,
  not adaptive resistance.
- The game-list API is **public and unauthenticated**:
  `GET https://api.flydigi.com/pc/adapter_trigger/list` → 94 games with per-game trigger configs
  and mod download URLs. No app, no login, no headers.

---

## 8. The screen

The Apex 5 has a 160×80 colour screen (`IsSupportScreen`, set for the k1, k2 and k5 families). The
image format is verified offline against Flydigi's own files (§8a) and the transport on hardware
over the serial OTA path (§8d), which is what `flydigi/screen_ota.py` implements. The SDK's HID
picture family (§8b) **answers every packet and puts no picture on the panel** on this pad, while
still altering stored state.

### 8a. Image format — LVGL v8 binary, 25604 bytes a frame

A frame is **25604 bytes** and is an **LVGL v8 binary image**:

```
0..4    header: little-endian uint32 of bit fields
        cf (5) | always_zero (3) | reserved (2) | width (11) | height (11)
        An Apex 5 frame is cf=4 (LV_IMG_CF_TRUE_COLOR), 160x80 -- the constant 04 80 02 0A
4..     160 x 80 pixels, RGB565, high byte first, row-major
```

A `.bin` is frames concatenated with no container of any kind: file size is always an exact
multiple of 25604. The row stride is 320 bytes = 160 × 2, and `always_zero` and `reserved` are zero
in every shipped file. All **14** files Flydigi ships under
`Configs/Controller/{k2,k5}/default/default_screen_image_*.bin` — **821** frames, 550 under `k5`
and 271 under `k2` — decode and re-encode **byte-identical** through `flydigi/screen.py`.

Byte order is an LVGL build option rather than part of the format: this is
`LV_COLOR_16_SWAP = 1`. Space Station's own converter is the LVGL one — its image
picker carries `ICF_TRUE_COLOR_ARGB8332 / 8565 / 8565_RBSWAP / 8888` and `CF_RAW` verbatim.

`default_screen_image_<deviceType>.bin` is per device *type*, not per model: an Apex 5 has six
(128 `K5`, 129 `K5Eva`, 133 `K5Mm`, 134 `K5Srs`, 135 `K5GS`, 136 `K5LZ`).

### 8b. Picture upload — the SDK's HID path, which puts no picture on the panel

**All four commands are live on an Apex 5 and no picture reaches the panel.** Two complete uploads
went out over this family — 9623 packets, no errors, every field echoed back — **and the display
never changed**. Stored state is another matter: 211 commits metadata (below), and the two uploads
left the status-bar flag on, which `19` sub `8` puts back (§8c).

Why Flydigi split the two screen models — the k5 on serial, every other screen pad on HID — is
unexplained: the k2 has the *same* separate `ChipScreen`/`ChipType.Freq`, so "separate screen chip"
is not the discriminator.

**211 is a commit, not punctuation.** 208 followed by 211 commits picture metadata for a frame that
was never sent; on a wired Apex 5 that destroys a stored custom image and the screen falls back to
its status view at the next reboot. `screen.probe()` sends only the start.

Four commands, in the *legacy* envelope rather than the `5A A5` one — they predate it, and the SDK
has **no NewXInput branch for them at all**. Its XInput and DInput branches are the same packet with
a different prefix, so all three envelopes are one builder:

```
new     03 5A A5 <cmd> <len> <payload…> <crc>      every other command on this pad
a5      03    A5 <cmd> <len> <payload…> <crc>      the SDK's DInput branch
bare    03       <cmd> <len> <payload…> <crc>      the SDK's XInput branch
```

`len` counts the command and length bytes as well as the payload; the checksum is the usual 8-bit
sum from the command byte up to it — with one exception in Flydigi's own code. 211's `a5` branch
writes `array[9] = array.Crc(2, 7)` where 208 uses `Crc(2, 11)` and 210 uses `Crc(2, 9)`: two bytes
short for a length of 7.

| Cmd | What | Payload |
|---|---|---|
| `208` | start a frame | `picId, picType, picCount, picIdx, period, sizeHigh, sizeLow` |
| `209` | data | `offsetHigh, offsetLow, chunk…` |
| `210` | end a frame | `picId, picIdx, sizeHigh, sizeLow, 0` |
| `211` | finish the upload | `picId, frameCount, sizeHigh, sizeLow, 0` |

`picId` is always 1, `picType` is 1 for an animation and 0 for a single image, `picIdx` is 1-based,
and `size` is **25604** — the frame *including* its four header bytes, which Flydigi pass as the
literal pair `(100, 4)`. The data offsets run over the same 25604 bytes. `period` is
`frameInterval / 100` in the XInput branch; the DInput branch writes a literal zero there instead
and adds one to `picType` and `picIdx` on top of the caller's numbering.

A 209 data packet carries **24 payload bytes** in the `new` envelope (`device.PACKET_LEN - 8`: one
report id, four envelope bytes — `5A`, `A5`, command, length — two offset bytes, one checksum), and
**the tail chunk is zero-padded to full length rather than sent short** — Flydigi pad theirs too,
so a pad counting bytes rather than reading the offset field still lands in the right place.
Flydigi's own XInput branch sizes it `32 - 6 = 26`.

**Space Station never sends any of this to an Apex 5.** `upload_pic2screen` in the Electron layer
branches on the device code: `k5` gets `SwitchUsb` and the serial route of §8d, every other pad
takes the HID path above. `ControllerSdk.UploadPicImpl` gates only on `IsSupportScreen`, so the SDK
*would* send 208..211 to a k5 if asked — nothing in their UI asks.

They answer in the `new` envelope, and every field varied in a payload comes back echoed — the same
signature the trigger commands show:

```
TX 208 picType=1 count=5 idx=2 period=3   RX 5a a5 18 01 00 | 01 05 02 03
TX 209 offset=0x1234 data=AA AA AA        RX 5a a5 19 01 00 | 34 aa aa aa
TX 210 picId=1 idx=3 size=0x1234          RX 5a a5 d2 07    | 01 03 12 34
TX 211 picId=1 count=9 size=0x1234        RX 5a a5 d3 07    | 01 09 12 34
```

**The reply command byte is not always the command's own.** 210 and 211 answer as `0xD2`/`0xD3`,
but 208 and 209 answer as **`0x18`/`0x19`** — which are real command ids elsewhere (nickname write,
mapping enable), so this is not a "no such command" reply. 208 and 209 also drop the first payload
byte from their echo, the way `SetForceTrigger` drops the side byte, while 210 and 211 echo theirs
whole and carry a length of 7 where the other two carry 1. Match on the id the pad uses, not the one
you sent: `screen.ACK_ID` has the map.

Upload is **wired only** in Space Station — its UI refuses with "please use wired connection"
before it gets as far as the device.

### 8c. Screen settings — ordinary NewXInput commands, no upload involved

| Cmd | What | Layout |
|---|---|---|
| `242` | flood the screen with a colour | `[4]=len, [5]=on, [6]=R, [7]=G, [8]=B, [9]=crc` |
| `19` sub `8` | status bar always on | `[4]=4, [5]=8, [6]=enable, [7]=crc` |
| `19` sub `9` | **always-on display** (the SDK's `OffScreen`) | `[4]=4, [5]=9, [6]=enable, [7]=crc` |
| `3` | reads both back | `data[5] bit7`/`data[6] bit7` status bar supported/on; `data[7] bit0`/`data[8] bit0` **always-on** supported/on |

The sender is `flydigi/settings.py` (`CMD_SETTING = 19`, `SUB_STATUS_BAR = 8`, `SUB_OFF_SCREEN = 9`),
reached from `tools/flydigi-settings` and `tools/flydigi-screen status`; `flydigi/screen.py` binds
the same constants for screen callers. The rest of the command-19 sub-id list and the command-3
status block are in [docs/device-settings.md](docs/device-settings.md).

**The SDK name for `19` sub `9` is inverted — do not implement it from the name.** Flydigi call the
bit `OffScreen` (息屏显示), which reads as a screen-*off* switch. Measured on a wired Apex 5 while
watching the panel, it is the opposite:

```
[6] = 1   the stored picture stays up — an always-on display
[6] = 0   the panel is dark; the logo button wakes the status view for ~2 seconds
```

`[6]=0` is a genuine screen blank, a control Space Station never surfaces; `flydigi/screen.py`
reports the command-3 bits as `always_on_usable`/`always_on`.

**An ACK to command 19 does not say which sub-setting was written** — the reply carries the command
id, not the sub-id, so read command 3 back if you need to know what landed.

**242 is confirmed on a wired Apex 5.** Sent with the length-6 reading it ACKed and the screen went
solid orange immediately. Two things the SDK does not say:

  * **it floods the RGB LEDs as well as the screen.** This is a whole-device indicator test, not a
    screen test; Space Station keeps it in `data.command.test` beside the factory-line commands.
  * **`on=0` does not clear it.** The command ACKs, the pad stays flooded, and the only exit found
    was the pad's own power switch.

**Flydigi's 242 builder disagrees with itself.**
It writes four payload bytes (on, R, G, B), sets the length byte to **5**, then puts the checksum at
offset **9** and sums it over the range a length of 5 implies. A length of 5 means three payload
bytes and a checksum at offset 8; a length of 6 means four and a checksum at 9. The placement says
6, the length byte says 5, and one of them is a typo. `flydigi/screen.py` defaults to 6 — it is the
reading that keeps the blue byte — and can send their exact bytes instead.

The neighbouring LED test command, `TestLedCommandFactory` = **245**
(`[4]=5, [5]=R, [6]=G, [7]=B, [8]=sum(3, 3+5)`), ACKs on an Apex 5, echoes the RGB values back and
changes nothing — [docs/device-settings.md](docs/device-settings.md).

### 8d. The serial path — UART OTA over USB CDC, the route that drives the panel

`flydigi/screen_ota.py`. Verified on a wired Apex 5: a test card and a 14-frame animation, each
written at base `0x002ff000` as returned by `PicGetBaseAddr`, each followed by the pad rebooting
itself and coming back on the HID bus.

| | |
|---|---|
| Exchange rate | **~19 a second, ~52 ms each** — steady, and the same for one frame or fourteen |
| One frame | 7 erases + 466 writes = 473 exchanges, **~25 s** |
| 14 frames | 88 erases + 6518 writes = 6606 exchanges, **5 m 46 s** (predicted 346 s, measured 346 s) |
| The 255-frame ceiling | about **1.8 hours** |

It is slow because the unit is 55 bytes and every one waits for its reply; Space Station runs the
same arithmetic.

**The 255-frame ceiling is not enforced on this path.** `screen_ota.upload` sends
`len(frames) & 0xFF` as the picture count, so a 300-frame set writes all 300 frames of data while
telling the chip there are 44. The check exists only in the dead HID path (`screen.upload` raises
above 255) and in the GUI (`MAX_FRAMES = 255`, which truncates and says so).

**The pad does not leave the HID bus.** Observed mid-upload: with the bootloader tty live, the pad's
own `37d7:2501` hidraw nodes were still enumerated and `find_device` resolved normally. So command
31 for the screen *adds* a CDC interface beside the gamepad rather than replacing the device with a
bootloader — the main firmware keeps running throughout. (Only the nodes were checked, not whether
input still flowed.)

**The tty needs a udev rule.** It lands as `root:dialout`, and a screen upload without one gets as
far as finding the port and then cannot open it — with the pad already switched over. See
`udev/72-flydigi-apex5.rules`; `flydigi/setup.py` fails an absent rules file even when every other
device is already reachable.

#### How Space Station does it

`FirmwareConsole.exe` is a .NET single-file bundle (`sfextract`, then `ilspycmd`), and the screen
work is all managed code in `FirmwareLibrary.dll`. The updater is chosen by chip type in
`FirmwareConsole.decompiled.cs:151-186`, and every branch but one reaches a vendor blob — an
external tool or a P/Invoked DLL:

| `ChipType` | Updater | Implementation |
|---|---|---|
| `Freq` — **the screen** | `OtaNewUpdater` | **managed C#, UART OTA over a serial port** |
| `Telink` | `HidUpdater` | managed, HID |
| `Megahunt` | `MhExeUpdater` | runs `tool/mhtool/hid_boot_command.exe` |
| `NearLink` | `HshExeUpdater` | runs `tool/hsh_tool/BurnTool.exe` |
| `Jieli` | `ExeUpdater`, or `JieLiUpdater` for a `.ufw` | `Process.Start` on the firmware file itself; the `.ufw` branch P/Invokes `jl_firmware_upgrade_x64.dll` |
| `Wch` | `CH375Updater` / `WCH59XUpdater` | P/Invokes `CH375DLL64.dll` or `WCH55xISPDLL.dll`. `WCH55xISPDLL.dll` is 32-bit only, so on a 64-bit host `WCH59XUpdater` re-launches `FirmwareConsole_wch_x86/FirmwareConsole.exe` as a helper |

**So the screen chip is the only *screen* branch with no vendor blob in it** — `Telink` is managed
too, but over HID, and `JieLiUpdater` is a screen updater that P/Invokes. After command 31 the pad
re-enumerates as a **USB CDC serial device, VID `FFAA` PID `5555`**, and the upload is a plain
request/response protocol at **921600 8N1**.

#### The chain, Electron to tty

1. **Electron** converts each frame to the 25604-byte LVGL image of §8a and concatenates them into
   a temp `.bin`. No container, no header of its own.
2. It sends IPC `SwitchUsb {uid, chipModule: CHIP_SCREEN (4), chipType: FREQ}`, which the service
   turns into **HID command 31**: `[4]=3, [5]=chipModule, [6]=crc`. Three bytes.
3. It **waits 5000 ms**, hard-coded, for the device to re-enumerate.
4. It spawns `firmware/FirmwareConsole.exe` with
   `--device_id k5 --chip_module 4 --chip_type <Freq> --url <temp.bin> --vendor_id 37d7
   --product_id 2501 --pic_type <1|2> --pic_num <N> --frame_rate <interval/10> --upgrade_type 2
   --is_restore_default <0|1>`.
5. The console maps `ChipType.Freq` to `OtaNewUpdater` and fills a `ScreenUpgradeConfig`, then calls
   `StartUpdate(bin)`.

`ScreenUpgradePicType {NONE=0, GIF=1, PNG=2}` — **PNG for a single frame, GIF for more than one**.
`frameRate` is `frameInterval / 10`, hundredths of a second, which is *not* the `/100` the HID start
packet uses. `ScreenUpgradeType {PROGRAM=0, SYS_PIC=1, CUSTOM_PIC=2}`; a user image is **CUSTOM_PIC**,
and only `SYS_PIC` and `PROGRAM` ever touch the program region.

#### Finding the device and opening it

`ScanDevices` polls for a serial port whose **VID is `FFAA` and PID `5555`**, up to 10 times at 3
second intervals, then opens it at **921600 8N1, no parity, no handshake, DTR and RTS asserted**.
(`InitSerialPort` in the same class says 115200 and is never called on this path — 921600 is the
one that runs.)

On Linux the port lookup is the only part that does not transfer: Space Station runs a WMI query against
`Win32_PnPEntity`; `screen_ota.find_port` walks `/sys/class/tty/ttyACM*` and
`ttyUSB*` and reads the `idVendor`/`idProduct` files under each node's `device/../`. The pad binds
`cdc_acm`, so it lands on `ttyACM*`; `screen_ota.wait_for_port` polls until it appears.

#### The state machine

Out: `[opcode][length uint16 LE][payload…]`. In: `[result][opcode][length uint16][payload…]`, with
a short (<5 byte) reply carrying opcode 12 as the end-of-session signal. A 300 ms timer drives it,
and 60 ticks without a reply — 18 seconds — aborts.

```
11  PicGetBaseAddr   len=4   picType, picNum, frameRate, isRestoreDefault -> baseAddr uint32 at [4]
10  PicGetVersion    len=6   --                                           -> version
 3  EraseSector      len=6   addr uint32       x ceil(size/4096), addr = base + i*4096
 5  WriteData        len=64  addr uint32, 55, 0, then 55 data bytes  x ceil(size/55)
12  PicResetDevice   len=8   totalLength uint32, crc32 uint32         -> done
```

The first column is the opcode. **The base address is fetched first even though it is the higher
opcode** — `PicGetVersion = 10, PicGetBaseAddr = 11`, and the state machine runs 11 then 10.

**Do not compute the length field.** It means something different per opcode and is inconsistent
with the bytes that follow in three of the five: `PicGetVersion` says 6 and sends no payload,
`EraseSector` says 6 and sends 4, `WriteData` says 64 which is the *whole packet* rather than its
payload. Only `PicGetBaseAddr` and `PicResetDevice` state their own payload length. Copy the
constants.

The CRC is a CRC-32 variant of their own: the standard reflected table, but fed MSB-first and
indexed with **bits 8..15** of the running value rather than its top byte, seeded 0, no final xor,
computed over the data in 256-byte chunks (`Crc32CalByByte`, `AppOtasCrcCal`):

```
q = crc / 256;  crc = (crc << 8) ^ table[(q ^ byte) & 0xFF]
```

`crc` is a C# `int`, so `crc / 256` is signed integer division that truncates toward zero — neither
an arithmetic nor a logical shift once the value has gone negative. Port it as an explicit signed
truncating division, not as `>> 8`; `flydigi/screen_ota.checksum` does exactly that.

After the last reply the screen **syncs for about 15 seconds and reboots itself**; Space Station's
own dialog says so and warns against cutting power during it.

**Three things bound a CUSTOM_PIC upload.** The picture base address is **read back from the
device** (`PicGetBaseAddr`), and every erase and write is `base + offset`, so the program region is
only reachable through `ScreenUpgradeType.PROGRAM`, which a picture upload never sends.
`PicResetDevice` ends the session and resets the chip, on top of Flydigi's own "toggle the power
switch on the back of the controller". And a botched upload is recoverable: `isRestoreDefault = 1`
with the stock `default_screen_image_<deviceType>.bin` puts the factory animation back.

### 8e. Coming back from command 31

Four statements from Space Station's own dialogs:

| When | What their UI says |
|---|---|
| Screen upload **succeeded** | "Screen needs ~15 seconds to sync resources. **It will restart automatically** when done. Please do not turn off the device." |
| Screen upload **failed** | "Slide the power switch on the back to restart the controller and retry connection" |
| Any firmware update failed | "Upgrade failed with {type}. Please attempt the upgrade again" — with a Retry button |
| Controller abnormal after an SI flash | "**Hold the START button (lower right of LOGO) for 8 seconds** to restore controller function" |

A failed flash is retried rather than recovered from, so upgrade mode stays addressable. Two uploads
have gone across and back on hardware (§8d). A failed *picture* upload leaves a pad that is still a
gamepad with a stale picture region, retryable with `--port` and without a second command 31. A
power cycle is *contraindicated* only during the ~15 s resource sync after a successful write.

---

## 9. Stored configs — the packetised blob transfer

Mapping profiles and RGB lighting are the two stored configs, and they move the same way. The
framing is in `flydigi/blobs.py`; the mapping commands and blob accessors are in
`flydigi/mapping.py` with the macro page in `flydigi/macros.py`, driven by `tools/flydigi-mapping`;
lighting is `flydigi/lighting.py`. The layouts are in
[docs/findings-profile-blob.md](docs/findings-profile-blob.md).

| Cmd | Family | What | Payload |
|---|---|---|---|
| `161` | mapping | status — active slot and a version per slot | — |
| `162` | mapping | apply a slot (switches the running profile) | `[cfgId]` |
| `163` | mapping | read a stored profile | `[cfgId, pkgSize]` |
| `164` | mapping | write start | `[cfgId, startIdx, nPkts, pkgSize]` |
| `165` | mapping | write pack | `[pktNum, data…]` × N |
| `166` | mapping | **save to flash** — one of the 10 s timeouts of §2, against the 500 ms default | `[versionLo, versionHi]`, LE16 |
| `171` | mapping | save to flash, slot-addressed | `[versionLo, versionHi, cfgId]` |
| `167` | lighting | read | `[cfgId, pkgSize]` |
| `168` | lighting | write start | `[cfgId, startIdx, nPkts, pkgSize]` |
| `169` | lighting | write pack | `[pktNum, data…]` × N |

**161 is the cheap read.** It has no side effect, and neither does the lighting read 167
(`flydigi/lighting.py:137`); 163 is the one that moves the pad. `data[5]` is the active slot —
reported across two banks of four, so 4..7 mean the same profiles as 0..3 — and `data[6 + 2i]` is
slot *i*'s `data_version` as a little-endian 16-bit. `0xFFFF` means the slot has never been written.
A cached copy can be checked for staleness without reading the config at all.

The same field lives in the blob at **offset 225**, little-endian uint16
(`mapping.OFF_DATA_VERSION`), and it is the value 166 wants, so a caller passes the config's own to
leave it alone. What the pad does with a 0 there is unconfirmed (`flydigi/mapping.py:427-430`).
Observed values — 23224, 65078, 65535 for an untouched slot — look like random change-detection
tags rather than a counter.

**163 switches the pad**, and it switches on the *first* reply packet, before the reader knows
whether the whole config arrived. A read that raises has therefore still moved the pad, and a retry
launders it: the next status read truthfully reports the browsed slot as active, so the restore is
skipped. Which slot to return to has to be decided before the read
(`mapping.read_config_preserving`).

**166 commits whichever config the pad is running**, not a slot you name — it carries a version and
nothing else. The slot-addressed variant is 171. An applied-but-unsaved config does not survive the
pad's next **sleep**, not merely a power cycle: applying is working memory, and 166 is what makes it
last. Observed with lighting; it is the general rule for every stored config.

Space Station's own save is four commands, all four ACKing on hardware; the sequence is in
[docs/device-settings.md](docs/device-settings.md).

**Transfer rules**, both families:

  * 20 bytes per packet on NewXInput (10 on older protocols).
  * A mapping profile is **840 bytes = 42 packets**. An Apex 5's lighting blob is **380 bytes = 19
    packets**: a 20-byte header, then 10 frames × 12 LEDs × 3 bytes.
  * A read reply carries `(total, index, cfgId, data)`, so packets can be reassembled out of order.
  * Writes are sent as **contiguous runs of changed packets** — an unchanged prefix or suffix is not
    resent, which is what `startIdx`/`nPkts` are for.
  * **A bad checksum is answered with silence**, not with an error. Time out; do not wait for a NAK.

**For lighting, the frames *are* the effect.** The pad has no animation generator: it plays the
stored frames, cycling `loop_start`..`loop_end` every `cycle_time`. The `mode` byte only records
which of Space Station's generators produced the data, so changing the lighting means writing
frames, and writing a different mode number changes nothing visible.

One wire-ordering trap goes with that: LED-blob byte 2 latches the ring on the frame it is showing
([docs/device-settings.md](docs/device-settings.md)), and `write_config` sends packet 0 first, so
setting byte 2 in the same write freezes the pad before its own frames arrive.

**For macros, 162 is not optional.** On protocol 3.1 they ride inside the mapping profile — the page
at offset 230, laid out in [docs/findings-profile-blob.md](docs/findings-profile-blob.md) — and
there is no macro command in that protocol version at all. A macro written with 164/165 is
**stored and not played** until the profile is applied with 162: the firmware parses the page into
its own structs at load time, while the key table beside it is read as it stands. Verified on
hardware — four macros sat silent while an ordinary remap beside them worked, played after a 162,
and a fifth written and applied with no 166 at all played as well. So 166 decides whether macros
survive a sleep, not whether they run.

### 9a. The macro store — protocol 3.2 and later

**From v3.2 the macros leave the profile and become a config of their own**, addressed by the same
`cfgId` and moved by three commands with exactly the shapes above. An Apex 5 reports **769 = v3.1**
and keeps its macros at offset 230; a Vader 5 reports **770 = v3.2** and does not have them there at
all. `MappingConfigParser` branches on `data[0] >= 2` for the layout and on `ProtoVersion >= 770` for
the limits.

| Command | Payload | Reply |
|---|---|---|
| 172 `ReadMacroConfig` | `[cfgId, pkgSize]` | N packets `(total, index, cfgId, data)` |
| 173 `WriteMarcoConfig` start | `[cfgId, startIdx, nPkts, pkgSize]` | ack |
| 174 `WriteMarcoConfig` pack | `[pktNum, data…]` | ack per packet |

The store is **81 packets = 1620 bytes**, against a profile's 42 and 840. `MacroConfigParserV10` is
the only parser for it and declares 81 whatever version it is handed.

```
0..2      version, little endian — the store's own, not the profile's
2..4      how many macros, little endian; 1..10, anything else means none
4..24     ten offsets into the bodies, 16-bit, in 4-byte words from 24;
          0xFFFF for a slot with nothing in it
24..      the bodies, each  [0]      trigger key id
                            [1..3]   step count, little endian
                            [3]      type, MacroEnableType
                            [4..6]   repeat interval, little endian, milliseconds
                            [6..12]  0xFF padding
                            [12..32] a name, UTF-8, 0xFF filled
                            [32..]   4 bytes per step: cumulative time (16-bit),
                                     key id, event
```

Three things differ from the v3.1 page, and each is a way to get a v3.2 pad quietly wrong:

  * **A step's time is in milliseconds, not 10 ms ticks.** `GetMinMacroInterval` is the multiplier
    and it is 1 from 770 on. Writing ticks here plays every macro ten times too slow.
  * **The repeat interval belongs to the macro, not to the slot** — a field of the body, where v3.1
    keeps five bytes at blob offset 820. It is **milliseconds in both**, which is settled rather than
    assumed: `MappingConfigParserV31` scales that byte by ten going into the bean and by ten coming
    out, and this store reads and writes it raw.
  * **A macro can be named**, twenty bytes, which the v3.1 page has no room for.

The capability limits move with the same version test:

| | v3.1 | v3.2 |
|---|---|---|
| `GetMaxMacroCount` | 5 | **10** |
| `GetMaxMacroActionCount` | 128 | **256** |
| `GetMinMacroInterval` | 10 ms | **1 ms** |

**Write order is the profile first, the store second**, which is Space Station's own:
`WriteMappingConfigPartial`, then `WriteMacroConfigPartial` from its completion handler, gated on
`ProtoVersion >= 770`, then `SaveConfig`.

**None of this section has been sent to hardware.** It is transcribed from `MacroConfigParser` and
`WriteMarcoConfigCommandFactory`, and the only pad that speaks it is one nobody here owns — so the
layout is on the footing this project has found the decompile reliable for, and nothing about the
firmware's *behaviour* is claimed. The desktop app marks the page experimental for that reason.

---

## 10. The CD2 charging dock

A different device on the same desk, not part of the pad: **`37d7:6001`**, one HID interface, two
64-byte interrupt endpoints, a 34-byte report descriptor with one 64-byte input report, one 64-byte
output report, usage page `0xffa0` and **no report ids**. Source: `Flydigi.ChargerSdk.dll`,
decompiled the same way as the rest. Measured on firmware **0.0.3.9**, charger type 0.

### 10a. Framing

The `5a a5` envelope of §2, with report id `0x00` instead of `0x03` and the reply checksum one
slot earlier than the request's:

```
request   [0] 0x00   [1] 0x5A  [2] 0xA5  [3] cmd  [4] len  [5..] payload
          checksum at [3 + len],  8-bit sum over [3, 3 + len)
reply     [0] 0x5A   [1] 0xA5   [2] cmd  [3] len  [4..] payload
          checksum at [2 + len],  8-bit sum over [2, 2 + len)
```

`len` counts `[3]` and `[4]` themselves: `len = 2 + len(payload)`. A command buffer is 32 bytes;
the two data-pack commands use 64.

The reply position was checked against the five reads below and predicted the byte the dock sent in
each. The command-97 ack is the one exception seen, placing it at `[3 + len]`. Flydigi validate no
reply checksum anywhere — `.Crc(` appears only on outgoing buffers, and nothing in `Flydigi.Basic`
or `Flydigi.Hid` sums received bytes. What matches a reply to its command is `IsAck`, overridden
fourteen times as `data[2] == CommandId()` and called from `HidCommunicationProtocol:71`;
`ParseAckData` does no matching, it only parses fields. So a caller should match on the command
byte and confirm writes by reading back.

Measured: a **short output report is accepted**. 32-, 64- and 65-byte writes of the same heartbeat
drew identical replies.

Measured: **a pad-framed packet is ignored.** Report id `0x03` shifts the magic by one byte, and the
dock answered nothing — twice, at two widths, against a correctly-framed control that answered every
time. This is the only thing that made the old `find_device` picking the dock survivable.

### 10b. Commands

| id | Direction | Payload | Notes |
|---|---|---|---|
| 1 | read | — | heartbeat: type `[6]`, chip `[15]&0xF`, firmware `[16]`,`[17]` as packed nibbles, then the four switches at `[18]`..`[21]` |
| 2 | read | — | nickname, present only when `[3] > 4`; the slice `[6 : 6+[3]-3]` runs one byte long |
| 4 | read | — | uid, 13 bytes at `[6]`..`[18]` |
| 17 | write | enable | sleep when charging ("Intelligent start") |
| 18 | write | enable | lighting sync |
| 19 | write | enable | close with system |
| 20 | read | — | LED header: mode `[4]`, brightness `[5]`, period `[6]`, direction `[7]`, colour count `[8]`, colours from `[9]`. **Never returns frames** |
| 22 / 23 | write | start / pack | single-frame RGB write. The host path is wired end to end — `ChargerRepository.UpdateFrame:551` → `WriteRgbConfig`, reached from IPC 24577 — but no renderer control sends it, so it is unreachable from Space Station's UI |
| 24 | write | name | nickname — and Flydigi's builder puts its checksum at a fixed `[6]`, corrupting anything longer than one character |
| 25 | write | enable | power display |
| 97 / 98 | write | start / pack | the LED config, below |
| 175 | write | — | reset mapping config; Space Station's own "restore defaults" does not send it |
| 224 | write | — | firmware upgrade mode |
| 254 | write | type | rewrite the device type |
| **239** | **unsolicited** | — | pushed roughly once a second: `[7]` a controller is docked, `[8]` its battery |

Note the read at 20 and the write at 97/98 **transpose their fields**:

```
write   frameCount, period, brightness, mode, direction, colourCount, palette…, frames…
read    mode, brightness, period, direction, colourCount, palette…
```

### 10c. The LED config transfer

`97` starts it — payload `0x0A, startIdx>>8, startIdx, nPkts>>8, nPkts`, `len` 7 — and `98` carries
the data in 50-byte packs: `len = packLen + 7`, then `nPkts>>8, nPkts, idx>>8, idx, packLen`, data
from `[10]`, checksum at `[packLen + 10]`. The final pack is sent short, not padded. Every pack
waits for its own ack; there is no inter-packet delay anywhere in Flydigi's stack.

Flydigi advertise `len // 50 + 1` packs while sending `ceil(len / 50)`. The two differ exactly when
the blob divides by 50, and there they promise the dock a pack that never arrives — a 4-frame custom
animation is 1950 bytes, 39 packs against an advertised 40. `flydigi/charger.py` sends the true
count.

### 10d. The dock plays frames; it has no effect generator

The same architecture as the pad's lighting in §9, and measured the same way. A header naming
`Breath` with the right colour, brightness and interval — all of which read back correctly — and
`frameCount: 0` did **not** breathe: the dock went on playing its previous animation's leftovers,
then a travelling band of wrong colours, then flat white. Frame memory is not cleared by a config
write, so with no valid count the dock walks those bytes at offsets that are not multiples of three
and every RGB triple rotates into its neighbour's channels. **The frame count in the header must
match the frames actually sent.**

The same mode with its computed frames played correctly, and a `Pulse` uploaded with 50 frames was
indistinguishable from what the dock had been showing before anything here touched it.

**162 LEDs**, 16 rows of 14, 15, 16, 15, 14, 13 … 3. A blob is 6 header bytes, 3 per palette
colour and 486 per frame, so a fifty-frame effect is 24,306 bytes with an empty palette (gradient,
rainbow), 24,309 with one colour (pulse) and 24,312 with two (wave-gradient, diagonal-flow) — 487
packets in every case, about five seconds.

The eight computable effects, their frame counts and the lattice the two geometric ones use are in
[docs/findings-other-devices.md](docs/findings-other-devices.md). `Default` is not computable by
anyone: Space Station uploads a file its installer ships.

---

## 11. Identity — telling one pad from another

Three names, and only one of them is worth keying anything on. All measured on the Apex 5 here,
firmware 7.0.4.5, on its 2.4 GHz dongle.

**Command 1 carries a four-byte address at raw 8..11, and this pad reports zeroes.**
`HeartBeatControllerCommandNewXInput.ParseAckData` reads them least-significant last and reverses:

```
TX  03 5a a5 01 02 47 …
RX  04 5a a5 01 01 00 80 02 | 00 00 00 00 | 05 45 01 00 70 45 21 31 45 25 00 00 01 28 00 00 11 31 1f 00
                              ^^^^^^^^^^^ DeviceMac    ^^ battery      ^^^^^ main firmware 7.0.4.5
```

Every neighbouring field decodes — device type 128, connect type 2 (dongle), battery 5, and all
seven firmware components — so the field is empty rather than misplaced. Whether a cable fills it
in is untested. `motion.parse_mac` answers None for all-zero, the same convention Flydigi already
use for an absent firmware version.

**Command 4 is the uid: 13 bytes at raw 6.** One exchange, and the only per-unit identifier this
pad actually gives up.

```
TX  03 5a a5 04 02 4a …
RX  04 5a a5 04 01 00 | 14 20 6e 7a 1c 00 00 00 00 dc ba 3e 00 | 00 …
```

`ReadUidControllerCommandNewXInput` puts it at its own data[5], which is raw 6 with the report-id
byte kept — the same slot as every other single-frame payload. The dock's uid read is the same
command with the same shape (§10b).

**Command 2 is the nickname, at raw 6 like every other single-frame payload.**
`ReadNickNameControllerCommandNewXInput` slices `data.Slice(4, data.Length - 6)`
where `Flydigi.ChargerSdk`'s equivalent slices from its own data[6], and an unnamed pad seemed to
side with the earlier one — it answers 0x00 at raw 5, which is Flydigi's own test for an erased
name. That was the index byte, zero in every single-frame reply. Writing a name and reading it back
is what settled it:

```
TX  03 5a a5 18 06 44 65 73 6b …          ("Desk")
RX  04 5a a5 02 01 00 44 65 73 6b 00 …
                       ^^ raw 6
```

**A pad that has never been named does not answer with zeroes.** This one shipped with
`01 01 09 09 09 64 04 5e` in the field, which passes Flydigi's emptiness test — so Space Station
shows a factory-fresh pad as having a name. `identity.read_nickname` keeps their test and adds
one: a field that does not decode as printable UTF-8 is not a name either.

**Command 24 writes a nickname. Three things about it are not what the SDK implies**, and all
three were measured by writing names and reading them back.

*It is not checksum-validated.* A packet with the checksum slot left at zero is acknowledged and
stored, where a mapping packet with a bad checksum draws no reply at all (§7).

*The pad stores `buf[4] - 1` bytes from buf[5]* — one more than the name — so anything sitting in
the slot after the name is stored **as part of the name**:

```
TX  03 5a a5 18 06 44 65 73 6b a5 …       ("Desk" + checksum at 3 + buf[4])
RX  04 5a a5 02 01 00 44 65 73 6b a5 …    -> b"Desk\xa5"

TX  03 5a a5 18 06 44 65 73 6b 00 …       (no checksum)
RX  04 5a a5 02 01 00 44 65 73 6b 00 …    -> "Desk"
```

So `identity.nickname_packet` writes no checksum at all. The same rule fixes the length: 27 bytes
fit after buf[5], but the pad reads one past the name, so **26 bytes is the limit** — 27 and 28
both came back truncated to 26. UTF-8 round-trips; Cyrillic and CJK names were written and read
back intact, so the limit is bytes rather than characters.

*Flydigi's own builder is broken for every name but a one-letter one*:

```csharp
array[4] = (byte)(2 + bytes.Length);
Array.Copy(bytes, 0, array, 5, bytes.Length);
array[6] = array.Crc(3, 3 + array[4]);     // fixed index, not 3 + array[4]
```

Index 6 is the right slot only when the name is one byte. For anything longer the checksum
overwrites the name's second character, and the pad stores that:

```
TX  03 5a a5 18 06 44 a5 73 6b 00 …       (their bytes for "Desk")
RX  04 5a a5 02 01 00 44 a5 73 6b 00 …    -> b"D\xa5sk"
```

Space Station's rename has never worked past one character, and the pad was never the reason.
`nickname_packet(..., reference=True)` reproduces their packet, which is how this was established.

