# Other Flydigi hardware

What is gated on a device this project does not drive — a second pad, the charging dock — and what
command 31 may and may not be aimed at.

Index: [PROGRESS.md](../PROGRESS.md).

## Device codes: what `k5`, `k6` and `f4` mean

Flydigi's SDK identifies a model by a short `DeviceCode` string, and every capability check in
their source keys off it. The codes do not follow the product names: `k2` is the Apex *4*, and
there is no `k3` or `k4`. Codes below come from `FlydigiControllerFactory`'s dispatch, numbers from
the `DeviceType` enum — one entry per SKU rather than per model, so 128 and 129 are the Apex 5 base
model and the Eva edition.

| `DeviceCode` | Factory | Product | `DeviceType` values (enum members) |
|---|---|---|---|
| `k1` | `GenerateControllerApex3` | Apex 3 | 24, plus 26 / 29 special editions |
| `k2` | `GenerateControllerApex4` | **Apex 4** — not the Apex 2 | 84, 86, 87, 92, 93, 102, 103, 104 |
| `k5` | `GenerateControllerApex5` | **Apex 5 — this pad** | 128, 129, 133, 134, 135, 136 |
| `k6` | `GenerateControllerApex6` | Apex 6 — not shipped as of July 2026 | 149, 150 (`K6Pro`) |
| `f3`, `f3p` | `GenerateControllerVader3` | Vader 3 | 28, 80, 81, 88 |
| `f4` | `GenerateControllerVader4` | Vader 4 | 85, 91 |
| `f5` | `GenerateControllerVader5` | Vader 5 | 130, 144, 145 |
| `fp1`–`fp4` | `GenerateControllerDirewolf` | Direwolf | 25, 30, 31, 82, 83, 95, 132, 146–148 |

The code column and the number column are not in step. `GetDeviceCodeById` maps neither
`K5LZ = 136` nor `F5_DBZ = 144`, and no `fp1`/`fp2` at all: those reach the dispatch only through
`RecognizeDeviceCodeFromProductName`, which derives a code from the product name — "APEX", "VADER"
or "DireWolf" plus a digit. The enum also carries `Fp2Wired = 89`, `Fp2Switch = 90`, `Fp2M = 94`
and `Fp3PNaruto = 97`, which neither the table above nor `flydigi/identity.py` maps.

## Multiple pads

Nothing here drives a Vader 4 Pro yet. The SDK gives it 26 keys to the Apex 5's 27 over an
identical 20-key standard core; beyond that core only M1-M4 are common to both, the Vader adds C
and Z, the Apex 5 adds Turbo, M5 and M6.

The two are not the same pair renamed. On an Apex 5, M1-M4 are the back paddles and M5/M6 are a
shoulder pair at the top edge either side of the triggers, labelled **LM** and **RM**; on a Vader,
C and Z sit on the front face beside the ABXY block. A Vader 5 declares C, Z, M5 *and* M6
together, so the SDK treats them as six distinct extras rather than two namings of one pair.

**A key the pad has is not necessarily a key Space Station will rebind.** The factory list
enumerates the hardware; the k5 hitbox map then carries a `clickable` flag deciding what the
remapping UI offers, and three are false there: Fn (id 24, the SDK's `Menu`, which switches
profiles), Turbo (25) and Home (27). Clicking any of them on the device image returns
`ControllerKey_None` and never reaches the callback.

**That flag is their policy, not the firmware's.** Measured on a wired Apex 5, both directions:
`m1 -> home` made M1 fire the Guide button, and `home -> a` made Home send A with no Guide event
reaching the pad's evdev node at all. So `APEX5_KEYS` keeps `home` — the remap is worth having for
a pad whose Home button has failed, which is the one case their UI leaves no way out of. Fn and
Turbo are likely the same, untried, and deliberately not offered.

The same map treats `JsLeft` (240) and `JsRight` (241) as rebindable — stick-as-button, which this
project does not implement.

The trigger technology differs:

```
GenerateControllerVader4 ("f4")        GenerateControllerApex5 ("k5")
  IsSupportTriggerVibration = true       IsSupportForceTrigger = true
  HasAdcChip = true                      IsSupportScreen       = true
```

Impulse-style trigger vibration on one, adaptive force resistance plus a screen on the other. Both
have trigger haptics; the Apex 5 reaches them *through* the force-trigger subsystem (command 82's
`SyncWithGrip`), so the commands differ even where the capability overlaps. Scope is **config
only** — writing settings to the pad; driving impulse triggers during a game is explicitly not
wanted, since on Linux there is no XInput to carry it and almost nothing but Forza uses it.

The device-type guard is `flydigi/identity.py`. It exists because `flydigi/device.py` matches on
vendor id plus the `06 a0 ff` report-descriptor prefix, neither of which tells the models apart, so
an Apex 5 config would otherwise go into a Vader 4. The module is `DEVICE_TYPES` (the table above,
asserted by `tests/test_identity.py:50-60`), `PRODUCT_NAMES`, `code_for`, `name_for`,
`identify(ctrl)` (one command-1 read, raising `WrongDevice` when the pad does not answer at all),
`require(ctrl, *codes)` raising `WrongDevice`, and `SUPPORTED = ("k5",)`. Writes are gated; reads
deliberately are not, since asking an unknown pad what it is cannot damage it. Driving another
model deliberately means naming its code: `identity.require(ctrl, "f4")`. Three call sites gate —
`tools/flydigi-mapping:51`, `tools/flydigi-settings:136` and `gui/worker.py:91` — one check per
connection rather than per write; `flydigi-settings` gates its read-only `show` too, because a
Vader's settings block printed under this pad's field names would mislead. `tools/flydigi-screen`,
`tools/flydigi-haptics`, `tools/flydigid` and `tools/flydigi_cmd.py` do not call
`identity.require` at all.

The guard can only refuse, never choose. With two Flydigi pads attached, `find_device()` returns the
first match in sorted-by-name `/dev/hidraw*` order, where `hidraw10` comes before `hidraw2`
(`flydigi/device.py:96-106`). `Controller(path=...)` accepts a node (`flydigi/device.py:131-132`)
and nothing passes one: `flydigi-mapping:47`, `flydigi-settings:130`, `flydigi-screen:55` and
`gui/worker.py:89` all construct `Controller()` bare, and the one `--device` flag over the vendor
interface (`tools/flydigi_cmd.py:342`) opens the node itself rather than through `Controller`.

The rest of the work is almost entirely in `flydigi/`: per-model key tables, offsets and capability
flags. `gui/models/` only knows `mapping.APEX5_KEYS`.

The udev rules would have to become per-model too. They serve three things and none is universal:
the pad's own vendor hidraw node (`37d7:2501`, already model-specific), DualSense emulation
(`/dev/uhid` plus the DualSense input nodes), which applies to the Apex 4 and 5, and the screen
chip's bootloader tty (`ffaa:5555`) for the Apex 5 alone — the Apex 4 declares the same
`ChipScreen`/`ChipType.Freq`, but Space Station sends it down the HID picture route instead, so
only a `k5` ever produces the tty. A Vader or a Direwolf needs none of it, yet `setup.checks()`
fails an absent rules file unconditionally.

**Mode switch (27)** — `[4]=3, [5]=mode, [6]=Crc(3, 3+3)`, with
`BluetoothMode {Switch=1, Xbox=2, Flashplay=3, DInput=4}`; the enum starts at `None = 0`, which is
why Switch is 1. NewXInput only — there is no XInput or DInput builder. `IsSupportNs` is true, but
the switch changes the report descriptor and probably the hidraw node. Treat as a one-way trip
until proven otherwise.

## Features that belong to other models

  * **ADC / stick calibration** — `CalibrationAdcCommandFactory`, command **240**,
    `[4]=3, [5] = start ? 1 : 2, [6]=Crc(3, 3+3)`, with an `IsAck` that only checks
    `data[2] == 240`; legacy ids 20 (XInput) and 226 (DInput). `HasAdcChip` is set on exactly one
    controller in the whole factory, `GenerateControllerVader4`, so this is a **Vader 4 feature**:
    recalibrating stick centres against drift.
  * **The K6 trigger family** — commands **83–87**: mode 83, local mode 84, waveform 85, strength
    mapping 86, realtime 87, all gated on `DeviceCode == "k6"`, which the SDK's factory resolves
    to `GenerateControllerApex6` (`DeviceType.K6 = 149`, `K6Pro = 150`). Packet layouts are in
    [PROTOCOL.md](../PROTOCOL.md) §3b. The Apex 5 is `k5` and `SetForceTrigger` is its family, so
    the SDK never offers `K6TriggerMode.Local`'s autonomous effects here; what an Apex 5 does with
    83–87 is unverified. Nothing here sends 83–87 to an Apex 5 by default; `tools/flydigi_cmd.py`'s
    `k6mode` and `k6realtime` poke at it by hand.
  * **The wheel block (183..185)** — `m_fdg_macro_lunpan_struct_t {type, rev}`. `IsSupportWheel` is
    never set for the Apex 5. Keep carrying the bytes; build UI only for a pad that declares it.

## Charging dock, and syncing it with the pad

The gen-2 dock is `cd2`. Driving it is blocked only on decompiling `Flydigi.ChargerSdk.dll` and
`Flydigi.CoolerSdk.dll` in `bundle/`: `~/.dotnet/tools/ilspycmd -o decompiled/Flydigi.ChargerSdk
bundle/Flydigi.ChargerSdk.dll` in the `wine-arch` distrobox. The Vader 4's older dock is a different
device; nothing here describes it, and it must not be assumed to speak the `cd2` protocol.

**The dock takes images and animations, and it is a lighting problem rather than a screen one.**
The DIY page accepts png/jpeg/gif, crops on a 334x304 canvas, decodes GIF frames one at a time
behind a "Saving your settings, please wait..." modal (`loading_message_config_saving`), and shares
the pad's `screen_*` locale strings — but the `cd2` has **162 addressable LEDs** in a wedge: 16
rows of 14, 15, 16, 15, 14, 13 … down to 3, at fixed coordinates the page carries as a literal
array. Conversion samples **one pixel per LED** into `Color {red, green, blue}`, builds a
`FramedLedColor {brightness, colors[162]}` per GIF frame, and sends the lot as an ordinary
`IpcCommandEnum_UpdateConfig` carrying `ChargerLedConfig {mode: ChargerLedType_Custom, period,
direction, brightness, frames[]}` → `ChargerRepository.UpdateConfig` → `ChargerSdk.WriteLedConfig`.
(`ChargerSdk.WriteRgbConfig` is the single-frame call, reached from `UpdateFrame(FramedLedColor)`.)

Protobuf field numbers: `ChargerLedConfig {mode 1, period 2, brightness 3, color[] 4,
useColorCount 5, direction 6, frames[] 7}` and `FramedLedColor {brightness 1, colors[] 2}`.
`UpdateConfig` accepts a cfgId of 0..8. The DIY page sends `brightness: 100`, `useColorCount: 0`,
`color: []`, `direction: ChargerLedDirection_NONE`, and `period: 1` for a still image or
`Math.round(frameInterval / 10)` for a GIF — so `period` is the frame interval in centiseconds,
not a frame count.

The image path uses **no `SwitchUsb`, no firmware console and no command 31** — the ordinary config
path, and the same host-computes-frames/device-plays-them architecture as `flydigi/lighting.py`.
`ChargerRepository` has no screen or picture method, and `Configs/Charger/cd2/default/` holds
`default_mapping_0.dat` … `default_mapping_4.dat` and no image at all, against the pad's own
`Configs/Controller/k5/default/default_screen_image_*.bin`. The dock's whole API is mapping
configs, `UpdateFrame(FramedLedColor)`, `EnableLedSync`, `EnableCloseWithSystem`,
`EnableSleepWhenCharging`, `EnableShowAnimationWhenCharging`, plus a `SwitchUsb` that is firmware
update and nothing to do with images.

`ChargerLedType` has ten members — Close 0, Solid 1, Default 2, Custom 3, DiagonalFlow 4, Breath 5,
Gradient 6, WaveGradient 7, Rainbow 8, Pulse 9 — of which the locales expose eight,
`cd2_charger_led_type_{breath,custom,default,diagonal_flow,gradient,pulse,rainbow,wave_gradient}`,
omitting Close and Solid. `cd2_led_sync` is "Lighting Sync", tooltip "Keep the lighting mode of the
controller and dock in sync" — the integration wanted here.

The pad carries one dock-related setting of its own: `EnableDockSmartStop`, command **80** with
sub-id `[2]=16` and `[3]=enable`, in the legacy envelope rather than `5a a5`. There is no NewXInput
builder: the XInput and DInput classes are byte-identical, and a NewXInput controller is handed the
DInput one, so a request for this setting reaches any pad as a legacy packet. Space Station never
makes that request for a `k5` — `ReadHardwareFunctionStatus` sets `DockSmartStopUsable` for `fp4`
alone, and its NewXInput branch never sets the flag. Nothing here sends 80 sub 16, and what an
Apex 5 does with one is unverified.

`Category` is Unknown, Controller, Cooler, Keyboard, Mouse, Headset, Charger, and `bundle/` ships
`Flydigi.CoolerSdk.dll` and `Flydigi.KeyboardSdk.dll` alongside the charger's. There is no keyboard
repository; `CoolerRepository` exposes fan mode and speed, an accelerate level, smart mode, gear and
RGB lighting, a speed curve, auto-start and intelligent start/stop.

## Firmware update: not implemented, and command 31 only for the screen chip

`SwitchToFirmwareUpgradeModeCommandFactory` is command **31**, `[4]=3, [5]=chipModule, [6]=crc`. It
puts **one named chip** into upgrade mode and only into it; what brings a chip back out is the
flashing protocol for that chip. `[5]` is `ChipModule`: ChipMain 0, ChipRf 1, ChipSi 2,
ChipScreen 4, ChipTrigger 5, ChipDongle 6, ChipAdc 7, ChipLed 8 — there is no 3. 31 is the
NewXInput id and sends the chip module alone; the same operation is **48** on legacy XInput, which
varies on `ChipType.{Telink, Wch}`, and **245** on DInput, which varies on
`ChipId.{WCH_582 = 130, WCH_547 = 71, WCH_571 = 113}`.

**The rule is: send 31 for `ChipScreen`, and for no other chip.** This project does send it, as
step 2 of a picture upload — `flydigi/screen_ota.py:45-46` defines `CMD_SWITCH_USB = 31` and
`CHIP_SCREEN = 4`, `:139-156` sends it — and `enter_upgrade_mode` **takes no chip argument**, so no
other chip is reachable through it. The argument for that one case is in
[findings-screen.md](findings-screen.md).

What has no way back is aiming 31 at a program chip. Flashing a program image is one updater per
`ChipType`, chosen in `decompiled/FirmwareConsole/FirmwareConsole.decompiled.cs:151-186` and
implemented in `FirmwareLibrary.dll`: `Megahunt` shells out to `tool/mhtool/hid_boot_command.exe`,
`NearLink` to `tool/hsh_tool/BurnTool.exe`, `Jieli` runs the downloaded file itself with
`Process.Start` unless it is a `.ufw` (a managed `JieLiUpdater`), `Telink` is a managed HID updater
and `Wch` P/Invokes `CH375DLL64.dll`. `ChipType.Freq` is `OtaNewUpdater` — the screen, and the one
branch `flydigi/screen_ota.py` implements, restricted there to the picture region.

`GenerateControllerApex5` declares five of the eight `ChipModule` members across **four silicon
vendors**: ChipMain/Megahunt, ChipRf/NearLink, ChipScreen/Freq, ChipDongle/NearLink, ChipSi/Jieli.
Only `Freq` is implemented here, and only for pictures, so the other four modules would mean
reimplementing three third-party bootloader protocols with no recovery when wrong.
`GenerateControllerVader4` shares not one of those vendors: ChipMain/Telink, ChipDongle/Telink,
ChipAdc/Puya, ChipSi/Krly — four modules, three vendors. It multiplies per device as well as per
chip: four entry points call `SwitchToFirmwareUpgradeMode`, one per SDK plus the console —
`…/Flydigi.ControllerService.data/ControllerRepository.cs:2192`, `ChargerRepository.cs:635`,
`CoolerRepository.cs:1083` and `decompiled/FirmwareConsole/FirmwareConsole.decompiled.cs:445` —
times two pads with different silicon, times their dongles, times two dock generations.

**If a firmware update is genuinely needed, use real Windows hardware — not a VM.** Flashing drops
the device off USB and brings it back as a bootloader with a different identity, and that
re-enumeration is precisely where USB passthrough loses a device: mid-flash.

The Apex 5 declares `ChipSi`, so the START-for-8-seconds recovery in
[PROTOCOL.md](../PROTOCOL.md) §8e applies to it. Space Station's SI-chip failure dialog
(`setting_firmware_update_si_failed_message`) reads: "If the controller behaves abnormally, hold the
START button (lower right of LOGO) for 8 seconds to restore controller function." It recovers
**one chip**, not the device.
