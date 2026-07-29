# Other Flydigi hardware

What is gated on a device we do not drive yet — a second pad, the charging dock — and
the one thing on every device that must never be sent.

Index: [PROGRESS.md](../PROGRESS.md).

## Device codes: what `k5`, `k6` and `f4` mean

Flydigi's SDK identifies a model by a short `DeviceCode` string, and every capability check in
their source keys off it. The mapping is **not** guessable from the name — `k2` is the Apex *4* —
so here it is, straight from `FlydigiControllerFactory`'s dispatch:

| `DeviceCode` | Factory | Product | `DeviceType` values |
|---|---|---|---|
| `k1` | `GenerateControllerApex3` | Apex 3 | 24, plus 26 / 29 special editions |
| `k2` | `GenerateControllerApex4` | **Apex 4** — not the Apex 2 | 84, 86, 87, 92, 93, 102, 103, 104 |
| `k5` | `GenerateControllerApex5` | **Apex 5 — this pad** | 128, 129, 133, 134, 135, 136 |
| `k6` | `GenerateControllerApex6` | Apex 6 — see below, it has not shipped | 149, 150 (`K6Pro`) |
| `f3`, `f3p` | `GenerateControllerVader3` | Vader 3 | 28, 80, 81, 88 |
| `f4` | `GenerateControllerVader4` | Vader 4 | 85, 91 |
| `f5` | `GenerateControllerVader5` | Vader 5 | 130, 144, 145 |
| `fp1`–`fp4` | `GenerateControllerDirewolf` | Direwolf | 25, 30, 31, 82, 83, 95, 132, 146–148 |

There is no `k3` or `k4`: the Apex line's codes skip, so the digit in the code is not the digit in
the product name for anything before the Apex 5. `DeviceType` is the numeric form of the same
thing, one per SKU rather than per model — which is why SDL's Flydigi driver recognises the Apex 5
as ids **128/129**, the base model and the Eva edition.

`RecognizeDeviceCodeFromProductName` goes the other way, matching "APEX" plus a digit, so a product
name is enough to derive the code.

**Why `k6` appears in this repository at all**: `PROTOCOL.md` §3b transcribes the `K6Trigger*`
command family (83/85/87) because it is in the SDK next to the family we do use. As of **July 2026
no Apex 6 has shipped** — Flydigi's flagship is still the Apex 5 — so none of §3b has ever been
sent to a device. It is coming rather than hypothetical: an FCC registration appeared in July 2026
and an "Apex6 Haptics Elite" manual is dated 2026-07-17. Nothing in this project sends 83/85/87 to
an Apex 5 by default, and `tools/flydigi_cmd.py`'s `k6mode` / `k6realtime` exist only to poke at it
by hand.

## Multiple pads

Wanted later, not now. A Vader 4 Pro is on the desk, and the two are closer than "fewer features"
suggests: the SDK gives Vader 4 26 keys to the Apex 5's 27 over an identical 20-key standard core.
Only M1-M4 are common to both: the Vader adds C and Z, the Apex 5 adds Turbo, M5 and M6. What actually differs is the trigger technology —

```
GenerateControllerVader4 ("f4")        GenerateControllerApex5 ("k5")
  IsSupportTriggerVibration = true       IsSupportForceTrigger = true
  HasAdcChip = true                      IsSupportScreen       = true
```

— impulse-style trigger vibration on one, adaptive force resistance plus a screen on the other. Both
have trigger haptics; the Apex 5 reaches them *through* the force-trigger subsystem (command 82's
`SyncWithGrip`, which is our tier 1), so the commands differ even where the capability overlaps.

Scope for this is **config only** — writing settings to the pad. Driving impulse triggers during a
game is explicitly not wanted: on Linux there is no XInput to carry it, and almost nothing but Forza
uses it.

**The udev rules become per-model too, and today they are not.** They exist for two features and
neither is universal: DualSense emulation (`/dev/uhid` plus the DualSense input nodes) applies to the
Apex 4 and 5, and the screen chip's bootloader tty to the **Apex 5 alone**. A Vader or a Direwolf
needs none of it. `setup.checks()` currently **fails** an absent rules file unconditionally — right
for a one-model project, a false alarm the moment a Vader is plugged in — so gate it on what the
connected pad actually supports. The comment at that check says the same thing where it will be read.

The work would be almost entirely in `flydigi/`: per-model key tables, offsets and capability flags.
`gui/models/` only knows `mapping.APEX5_KEYS`. The prerequisite is the device-type guard — see
`flydigi/device.py`, which today matches on vendor id plus the vendor report-descriptor prefix —
neither of which tells the models apart — and would happily write an Apex 5 config to a Vader 4.

**Mode switch (27)** — `BluetoothMode {Switch=1, Xbox=2, Flashplay=3, DInput=4}` — is real and
`IsSupportNs` is true, but it changes the report descriptor and probably the hidraw node. Treat as a
one-way trip until proven otherwise; it is the one item here where a bad guess costs the session.

## Belongs to another pad, not to this one

Not dead — just gated on a device we do not drive yet. A Vader 4 Pro is to hand, so the ADC item in
particular is testable the moment multi-pad support exists; see *Multiple pads* above.

  * **ADC / stick calibration** — `CalibrationAdcCommandFactory`, command **240**,
    `[5] = start ? 1 : 2`, and a NewXInput builder does exist. `HasAdcChip` is set on exactly one
    controller in the whole factory: `GenerateControllerVader4`. So this is a **Vader 4 feature**,
    and a good one — recalibrating stick centres is the classic fix for drift. Sending it to an
    Apex 5 is probably a harmless no-op, but there is no reason to.
  * **The K6 trigger family** — commands 83/85/87 are gated on `DeviceCode == "k6"`, which the
    SDK's factory resolves to `GenerateControllerApex6` (`DeviceType.K6 = 149`, `K6Pro = 150`).
    **No Apex 6 had shipped as of July 2026**, so unlike the ADC item this is not waiting on a pad
    we could go and buy. It is close, though — FCC registration in July 2026, and an "Apex6 Haptics
    Elite" manual dated 2026-07-17 — so PROTOCOL.md §3b is worth keeping current rather than
    letting rot. The Apex 5 is `k5` and `SetForceTrigger` is its family, which **closes the other
    open question in PROTOCOL.md §5**. `K6TriggerMode.Local` is not a route to autonomous effects
    on *this* pad.
  * **The wheel block (183..185)** — `m_fdg_macro_lunpan_struct_t {type, rev}`. `IsSupportWheel` is
    never set for the Apex 5. Keep carrying the bytes; build UI only for a pad that declares it.

## Charging dock, and syncing it with the pad

**The newer Apex 5 dock is on the desk**, so this
is blocked only on decompiling the DLL, not on hardware. (The Vader 4's older dock is probably a
dumb USB hub with a charger — do not assume it speaks anything.) `Flydigi.ChargerSdk.dll` and
`Flydigi.CoolerSdk.dll` are in `bundle/` and **not yet decompiled** — that is step one
(`~/.dotnet/tools/ilspycmd -o decompiled/Flydigi.ChargerSdk bundle/Flydigi.ChargerSdk.dll` in the
`wine-arch` distrobox).

**The dock takes images and animations, and it is a lighting problem rather than a screen one.**
Worth stating plainly because the feature *looks* like the pad's screen — the DIY page accepts
png/jpeg/gif, crops on a 334x304 canvas, decodes GIF frames and shows an "animation generating"
spinner — and it shares the pad's `screen_*` locale strings. It is not a screen. The CD2 has **162
addressable LEDs** in a wedge: 16 rows of 14, 15, 16, 15, 14, 13 … down to 3, at fixed coordinates
the page carries as a literal array. Conversion samples **one pixel per LED** into
`Color {red, green, blue}`, builds a `FramedLedColor {brightness, colors[162]}` per GIF frame, and
sends the lot as an ordinary `IpcCommandEnum_UpdateConfig` carrying
`ChargerLedConfig {mode: ChargerLedType_Custom, period, direction, brightness, frames[]}` →
`ChargerSdk.WriteRgbConfig`.

So: **no `SwitchUsb`, no firmware console, no command 31** — the ordinary config path, and the same
host-computes-frames/device-plays-them architecture as `flydigi/lighting.py`. `ChargerRepository`
has no screen or picture method at all, and `Configs/Charger/cd2/default/` holds only mapping
`.dat` files. Its whole API is mapping configs, `UpdateFrame(FramedLedColor)`, `EnableLedSync`,
`EnableCloseWithSystem`, `EnableSleepWhenCharging`, `EnableShowAnimationWhenCharging` — plus a
`SwitchUsb` that is firmware update and nothing to do with images.

The locales show the effect set: `cd2_charger_led_type_{breath,custom,default,diagonal_flow,
gradient,pulse,rainbow,wave_gradient}`, and `cd2_led_sync` — "Keep the lighting mode of the
controller and dock in sync", which is the integration the user wants.

## Firmware update — deliberately not implemented, and command 31 must never be sent

`SwitchToFirmwareUpgradeModeCommandFactory` is **31**, `[4]=3, [5]=chipModule, [6]=crc`: three
bytes, trivially sendable, and a **one-way door**. It only puts a chip *into* upgrade mode. The
protocol that then flashes an image is **not in anything we have decompiled** — so a pad sent
into upgrade mode by this project cannot be brought out of it by this project.

**And it multiplies per device, not just per chip.** There are three separate SDKs with their
own entry point — `ControllerRepository.cs:2192`, `ChargerRepository.cs:635` and
`CoolerRepository.cs:1083` each call their own `SwitchToFirmwareUpgradeMode` — so it is not one
updater with a chip argument. Times two pads with different silicon (the Vader 4 has an ADC chip
and no screen; the Apex 5 has a screen and force triggers), times their dongles, times two dock
generations. Comfortably a dozen independently flashable images, and we have the flashing
protocol for none of them.

The scale is the other half of the argument. `ChipModule` has eight members, and the Apex 5's
own factory entry (`GenerateControllerApex5`) declares five of them — `ChipMain`, `ChipRf`,
`ChipScreen`, `ChipDongle`, `ChipSi` — across **four silicon vendors**, `ChipType.{Megahunt,
NearLink, Freq, Jieli}`. The `ChipType.{Telink, Wch}` split, with `ChipId.{WCH_582, WCH_547,
WCH_571}` selecting among WCH parts, is a branch only the legacy XInput/DInput builders carry;
this pad declares neither vendor, and its NewXInput builder sends the chip module alone.
Implementing this means implementing four third-party bootloader protocols correctly, first
time, with no recovery when wrong. The payoff is a convenience wanted
perhaps twice in a pad's life. It is not worth one brick, and it would cost the hardware
everything here is validated against.

**If a firmware update is genuinely needed, use real Windows hardware — not a VM.** Flashing
drops the device off USB and brings it back as a bootloader with a different identity, and that
re-enumeration is precisely where USB passthrough loses a device: mid-flash.

**There is a button-combination recovery**, so the hardware is not entirely without a way back.
Space Station's own failure dialog for the SI chip: "If the controller behaves abnormally, hold the
START button (lower right of LOGO) for 8 seconds to restore controller function." The Apex 5
declares `ChipSi` among its five chip modules, so it applies to this pad. It recovers **one chip**,
not the device, and it does not make flashing a program image sensible.
