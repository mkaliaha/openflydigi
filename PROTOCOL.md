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
   mod ecosystem. Flydigi did not invent an ingress protocol for it; they adopted DSX's, which is
   why the 11 third-party mods in their 94-game list drive the pad with no work from us.

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
- **Whether this pad implements the picture-upload family (208..211) at all**, and in which of the
  three envelopes — §8b. Space Station uploads to a k5 through the firmware console instead, so
  nothing in their software exercises it here. One start packet settles it.
- **Whether mode 5 (`Vibration`) does anything** — a curiosity, not a gap: nothing in Flydigi's
  stack sends it (§3a), so no route depends on the answer. Modes 0–4 are settled and felt (§7).
- Whether the pad honours a stored effect on profile switch without the host re-sending it. Effects
  applied live persist until changed, but that is not the same claim.

---

## 6. Implementation path (Linux)

1. `hidraw` writer implementing §2 framing + §3 commands.
2. Bench-verify with `Race` (`SetForceTrigger`) — directly observable, and done: §7. Not `K6`
   realtime; `83`/`85`/`87` are gated on `DeviceCode == "k6"` (the Apex 6, §5), so that family is
   not reachable on this pad.
3. UDP listener on 7878 speaking §4 DSX JSON → existing DSX mods work under Proton
   (Wine shares the host loopback, so `127.0.0.1:7878` from inside the prefix reaches a Linux daemon).
4. Optional: `/tmp/fcs.sock` protobuf server to drive the stock Electron UI on Linux.
5. Optional: telemetry providers (e.g. Forza "Data Out" UDP) for titles with no mod.

---

## 8. The screen

The Apex 5 has a 160×80 colour screen (`IsSupportScreen`, set only for this pad and the k2
family). Two halves, and their confidence levels are very different: the **image format is
settled**, the **upload transport is not**.

### 8a. Image format — verified offline, against Flydigi's own files

A frame is **25604 bytes** and is an **LVGL v8 binary image**:

```
0..4    header: little-endian uint32 of bit fields
        cf (5) | always_zero (3) | reserved (2) | width (11) | height (11)
        An Apex 5 frame is cf=4 (LV_IMG_CF_TRUE_COLOR), 160x80 -- the constant 04 80 02 0A
4..     160 x 80 pixels, RGB565, high byte first, row-major
```

A `.bin` is frames concatenated with no container of any kind: file size is always an exact
multiple of 25604.

Three independent things pin this down. `always_zero` and `reserved` really are zero, which a wrong
bit layout would not produce. Width falls out as 160, and an autocorrelation over the pixel bytes
finds its lowest inter-row difference at a stride of exactly 320 = 160 × 2. And decoding this way
gives a picture where the other byte order gives colour noise. All **14** files Flydigi ships under
`Configs/Controller/{k2,k5}/default/default_screen_image_*.bin` — 686 frames — decode and re-encode
**byte-identical** through `flydigi/screen.py`.

Byte order is worth stating because LVGL treats it as a build option rather than part of the
format: this is `LV_COLOR_16_SWAP = 1`. Space Station's own converter is the LVGL one — its image
picker carries `ICF_TRUE_COLOR_ARGB8332 / 8565 / 8565_RBSWAP / 8888` and `CF_RAW` verbatim.

`default_screen_image_<deviceType>.bin` is per device *type*, not per model: an Apex 5 has six
(128, 129, 133, 134, 135, 136), and 134 is the EVA edition.

### 8b. Picture upload — the SDK's HID path, and why the Apex 5 may not use it

Four commands, in the *legacy* envelope rather than the `5A A5` one — they predate it, and the SDK
has **no NewXInput branch for them at all**. Its XInput and DInput branches are the same packet with
a different prefix, so all three envelopes are one builder:

```
new     03 5A A5 <cmd> <len> <payload…> <crc>      every other command on this pad
a5      03    A5 <cmd> <len> <payload…> <crc>      the SDK's DInput branch
bare    03       <cmd> <len> <payload…> <crc>      the SDK's XInput branch
```

`len` counts the command and length bytes as well as the payload; the checksum is the usual 8-bit
sum from the command byte up to it.

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
and adds one to `picType` and `picIdx` on top of the caller's numbering. Only one of those can be
what the firmware wants, and the branch that carries the frame interval is the one that can
describe an animation at all.

**Space Station never sends any of this to an Apex 5.** `upload_pic2screen` in the Electron layer
branches on the device code: for `k5` it sends `SwitchUsb` — which is `SwitchToFirmwareUpgradeMode`,
**command 31**, with `chipModule = CHIP_SCREEN` and `chipType = FREQ` — waits five seconds, and then
runs `firmware/FirmwareConsole.exe --upgrade_type 2 --pic_type … --pic_num … --frame_rate …` over a
temp file of the frames. Every other pad takes the HID path above. `ControllerSdk.UploadPicImpl`
gates only on `IsSupportScreen`, so the SDK *would* send 208..211 to a k5 if asked — nothing in
their UI asks.

**All four are live on a wired Apex 5, in the `new` envelope.** Measured, because nothing in the
SDK predicts it — there is no NewXInput branch for this family at all. Every field varied in a
payload comes back echoed, which is the same signature the trigger commands show:

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

So Space Station's choice to route k5 through the firmware console looks like a speed decision
rather than a capability one — 24 bytes of payload per 32-byte packet is ~1067 packets a frame,
where the serial path moves 55 bytes at 921600 baud.

Upload is **wired only** in Space Station — its UI refuses with "please use wired connection"
before it gets as far as the device.

### 8d. The serial path — decompiled, implemented, and **verified on hardware**

**This is the one that works.** A test card and a 14-frame Bad Apple both went onto a wired Apex 5's
screen from Linux, each written at base `0x002ff000`, each followed by the pad rebooting itself and
coming back on the HID bus. `flydigi/screen_ota.py`.

Numbers worth having before planning anything around it:

| | |
|---|---|
| Exchange rate | **~19 a second, ~52 ms each** — steady, and the same for one frame or fourteen |
| One frame | 7 erases + 466 writes = 473 exchanges, **~25 s** |
| 14 frames | 88 erases + 6518 writes = 6606 exchanges, **5 m 46 s** (predicted 346 s, measured 346 s) |
| The 255-frame ceiling | about **1.8 hours** |

It is slow because the unit is 55 bytes and every one waits for its reply. Space Station is stuck
with exactly the same arithmetic, which is worth remembering before assuming a faster route exists.

**The pad does not leave the HID bus.** Observed mid-upload: with the bootloader tty live, the pad's
own `37d7:2501` hidraw nodes were still enumerated and `find_device` resolved normally. So command
31 for the screen *adds* a CDC interface beside the gamepad rather than replacing the device with a
bootloader — the main firmware keeps running throughout. (Only the nodes were checked, not whether
input still flowed, so take it at that strength.) This is a materially smaller risk than the phrase
"firmware upgrade mode" suggests.

**The tty needs a udev rule.** It lands as `root:dialout`, and a screen upload without one gets as
far as finding the port and then cannot open it — with the pad already switched over. See
`udev/72-flydigi-apex5.rules`; `flydigi/setup.py` fails an absent rules file for this reason even
when every other device is already reachable, because this is the one node that cannot be tested
until it is too late to fix.

#### How Space Station does it

`FirmwareConsole.exe` is a .NET single-file bundle (`sfextract`, then `ilspycmd`), and the screen
work is all managed code in `FirmwareLibrary.dll`. It dispatches on chip type, and only some
branches shell out to a vendor tool:

| `ChipType` | Updater | Implementation |
|---|---|---|
| `Freq` — **the screen** | `OtaNewUpdater` | **managed C#, UART OTA over a serial port** |
| `Telink` | `HidUpdater` | managed, HID |
| `Megahunt` / `NearLink` / `Jieli` | `MhExeUpdater` / `HshExeUpdater` / `ExeUpdater` | shells out to `firmware/tool/*` |
| `Wch` | `CH375Updater` / `WCH59XUpdater` | the WCH DLLs |

**So the screen chip is the one branch with no vendor blob in it.** After command 31 the pad
re-enumerates as a **USB CDC serial device, VID `FFAA` PID `5555`**, and the upload is a plain
request/response protocol at **921600 8N1**.

#### The whole chain, end to end

Traced through all four layers rather than inferred, because guessing at it is expensive: the HID
family answers every packet on a k5 and changes nothing, so "the pad accepted it" is not evidence.

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

On Linux the port lookup is the only part that does not transfer: theirs is a WMI query against
`Win32_PnPEntity`, ours is `/dev/serial/by-id/` or the `idVendor`/`idProduct` files under
`/sys/class/tty/ttyACM*/device/../`. Everything after that is bytes on a tty.

#### The state machine

Out: `[opcode][length uint16 LE][payload…]`. In: `[result][opcode][length uint16][payload…]`, with
a short (<5 byte) reply carrying opcode 12 as the end-of-session signal. A 300 ms timer drives it,
and 60 ticks without a reply — 18 seconds — aborts.

```
10  PicGetBaseAddr   len=4   picType, picNum, frameRate, isRestoreDefault -> baseAddr uint32 at [4]
11  PicGetVersion    len=6   --                                           -> version
 3  EraseSector      len=6   addr uint32       x ceil(size/4096), addr = base + i*4096
 5  WriteData        len=64  addr uint32, 55, 0, then 55 data bytes  x ceil(size/55)
12  PicResetDevice   len=8   totalLength uint32, crc32 uint32         -> done
```

The opcode enum is `PicGetVersion = 10, PicGetBaseAddr = 11`, but **the base address is fetched
first** — the state machine goes 10 → 11 in its own numbering, which is `PicGetBaseAddr` then
`PicGetVersion`. Read the transitions, not the enum order.

**Do not compute the length field.** It means something different per opcode and is inconsistent
with the bytes that follow in three of the five: `PicGetVersion` says 6 and sends no payload,
`EraseSector` says 6 and sends 4, `WriteData` says 64 which is the *whole packet* rather than its
payload. Only `PicGetBaseAddr` and `PicResetDevice` state their own payload length. Copy the
constants.

The CRC is a CRC-32 variant of their own: the standard reflected table, but fed MSB-first
(`crc = (crc << 8) ^ table[((crc >> 24) ^ byte) & 0xFF]`), seeded 0, no final xor, computed over the
data in 256-byte chunks. Note their `crc / 256` is signed integer division in C#, which is not
`>> 8` for negative values — port it as an explicit unsigned shift and mask.

After the last reply the screen **syncs for about 15 seconds and reboots itself**; Space Station's
own dialog says so and warns against cutting power during it.

**Three things make CUSTOM_PIC much safer than "flashing firmware" sounds.** The picture base
address is **read back from the device** (`PicGetBaseAddr`), and every erase and write is
`base + offset`, so the program region is only reachable through `ScreenUpgradeType.PROGRAM`, which
a picture upload never sends. There is a defined way out — `PicResetDevice` ends the session and
resets the chip — on top of Flydigi's own "toggle the power switch on the back of the controller".
And a botched upload is recoverable: `isRestoreDefault = 1` with the stock
`default_screen_image_<deviceType>.bin` puts the factory animation back.

### 8e. Coming back from command 31

Four statements from Space Station's own dialogs, which is the best evidence available short of
trying it. Together they describe a state its designers expect users to get into and out of
unaided.

| When | What their UI says |
|---|---|
| Screen upload **succeeded** | "Screen needs ~15 seconds to sync resources. **It will restart automatically** when done. Please do not turn off the device." |
| Screen upload **failed** | "Slide the power switch on the back to restart the controller and retry connection" |
| Any firmware update failed | "Upgrade failed with {type}. Please attempt the upgrade again" — with a Retry button |
| Controller abnormal after an SI flash | "**Hold the START button (lower right of LOGO) for 8 seconds** to restore controller function" |

So: a successful upload **reboots the pad by itself**; a failed one is cleared by the power switch;
a failed flash is expected to be retried rather than mourned, which means upgrade mode stays
addressable; and there is a hardware escape hatch on the pad itself.

**All of that is now backed by having done it.** Two uploads went across and back with no drama, the
pad rebooted itself both times, and — the part none of the dialogs say — the HID nodes never went
away at all (§8d). A failed *picture* upload leaves a pad that is still a gamepad with a stale
picture region, retryable with `--port` and without a second command 31.

The one window where a power cycle is *contraindicated* is the ~15 s resource sync after a
successful write — their warning is explicit about it, and it is the only place in the flow where
cutting power is worse than waiting.

Two supporting details, weaker but pointing the same way. Flydigi's own name for the command is
**`SwitchUsb`**, not "enter bootloader" — it reads as a change of USB personality. And the upload is
a *UART* OTA reached over a USB CDC device, which is what a main firmware bridging to the screen
chip's serial port looks like, rather than a main chip that has replaced itself with a bootloader.

**What none of this proves** is whether the mode flag is volatile. Nothing in the decompiled code
writes it to flash, but the flag lives in firmware we do not have. The evidence is Flydigi telling
their own users to power-cycle out of exactly this state — strong, and still not the same as having
done it.

What is unproven on Linux specifically: whether the pad enumerates as `cdc_acm`, and whether it
comes back. That is one cheap experiment — send 31, watch `/dev/serial/by-id` and `dmesg`, never
open the port, power-cycle — not a decompile.

### 8c. Screen settings — ordinary NewXInput commands, no upload involved

| Cmd | What | Layout |
|---|---|---|
| `242` | flood the screen with a colour | `[4]=len, [5]=on, [6]=R, [7]=G, [8]=B, [9]=crc` |
| `19` sub `8` | status bar always on | `[4]=4, [5]=8, [6]=enable, [7]=crc` |
| `19` sub `9` | screen off | `[4]=4, [5]=9, [6]=enable, [7]=crc` |
| `3` | reads both back | `data[5] bit7`/`data[6] bit7` status bar supported/on; `data[7] bit0`/`data[8] bit0` off-screen supported/on |

**242 is confirmed on a wired Apex 5, and it is stickier than its name suggests.** Sent with our
length-6 reading, it ACKed and the screen went solid orange immediately — so the screen does take
host commands, and the corrected length is the right one. Two things the SDK does not say:

  * **it floods the RGB LEDs as well as the screen.** This is a whole-device indicator test, not a
    screen test — which also explains why Space Station keeps it in `data.command.test` beside the
    factory-line commands rather than on any user-facing page.
  * **`on=0` does not clear it.** The command ACKs, the pad stays flooded, and the only exit found
    was the pad's own power switch. So entering this mode is a deliberate act with a physical undo.
    An earlier draft of this section said "`on=0` puts it back"; that was written from the builder,
    not from the pad.

**Flydigi's 242 builder disagrees with itself**, which matters because there is no third source.
It writes four payload bytes (on, R, G, B), sets the length byte to **5**, then puts the checksum at
offset **9** and sums it over the range a length of 5 implies. A length of 5 means three payload
bytes and a checksum at offset 8; a length of 6 means four and a checksum at 9. The placement says
6, the length byte says 5, and one of them is a typo. `flydigi/screen.py` defaults to 6 — it is the
reading that keeps the blue byte — and can send their exact bytes instead.
