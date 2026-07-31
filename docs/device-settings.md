# Device settings

Everything the pad exposes that is not a profile: the one read that returns the lot,
the small write commands behind it, the battery nibble, and the RGB test command that
does nothing.

Index: [PROGRESS.md](../PROGRESS.md).

## Command 3: the whole settings block in one read

One read covers most of a device-settings page by itself. `ReadHardwareFunctionStatus`,
NewXInput command **3**, payload length 2 (no arguments). The reply carries capability and enabled
bits separately, so the pad tells you both what it supports and what is on:

```
data[5]  supported   bit0 quick-switch config   bit1 Xbox home button  bit2 motion debounce
                     bit3 mapping switch        bit4 stick debounce    bit5 stick auto-calibration
                     bit6 stick rebound         bit7 status bar always on
data[6]  enabled     same bit order
data[7]  supported   bit0 off-screen   bit1 audio
data[8]  enabled     same
data[9]  sleep time        data[10] report rate
data[11] stick precision   data[12] stick sensitivity
```

So sleep time is readable as well as writable — worth reading before `UpdateSleepTime` writes it.

**Run on hardware, and the layout above is right.** Command 3 answered first try on a wired Apex 5:

```
reply  90 165   3   1   0 251 123   1   0  15   0   2  17 ...
```

| bit | supported | enabled |  | bit | supported | enabled |
|---|---|---|---|---|---|---|
| quick-switch config | yes | **on** | | stick debounce | yes | on |
| Xbox home button | yes | on | | stick auto-calibration | yes | on |
| motion debounce | **no** | — | | stick rebound | yes | on |
| mapping switch | yes | on | | status bar always on | yes | **off** |
| always-on display (SDK: "off screen") | yes | off — **the panel is dark** | | audio | **no** | — |

sleep time **15** (minutes), report rate **0**, stick precision **2**, stick sensitivity **17**.

Three things to note. `audio` is unsupported, which matches `AudioUsable` being gated — so the
audio sub-command is dead on this pad. `motion debounce` is unsupported too, so sub-id 3 is not
worth a UI. And **report rate reads 0**, which is not in the documented `{1000=1, 500=2, 250=4,
125=8}` map — either 0 means "default/unset" or the map is incomplete. Do not write that field
until a read on a pad whose rate has actually been set says which.

**The endpoint descriptors argue for "default".** Both input endpoints poll at the USB minimum:

```
3-4:1.0  xpad    ep_81 IN  interrupt  interval=1ms    the gamepad
3-4:1.2  usbhid  ep_83 IN  interrupt  interval=1ms    the vendor interface
```

The pad is full-speed (12 Mbit/s), where 1 ms is the shortest frame, so 1000 Hz is the ceiling for
both and the pad is already at it. A setting of 0 alongside a 1 ms endpoint reads as "default =
1000" rather than "unset". Note the two paths differ in *delivery*, not in rate: evdev is
event-driven and emits nothing while a stick is still, whereas the vendor stream sends regardless —
measured at ~970 Hz, essentially saturating its endpoint.

**Decoding the two numeric fields — and a trap in one of them.** Both are enums in
`Flydigi.SharedResources`, and neither is the number it looks like:

```
JoystickPrecision   None, 8Bit, 10Bit, 12Bit, 9Bit, 11Bit, 14Bit, 16Bit    (declaration order!)
JoystickSensitivity None=0, Highest=14, High=15, MiddleHigh=16,
                    Middle=17, LowMiddle=18, Low=19, Lowest=20
```

`JoystickPrecision` is ordered as it was **written**, not by bit depth: 9-bit and 11-bit were added
after 8/10/12, and 14/16 later still. So our pad's `precision = 2` is **10 bit**, and any mapping
that assumes the value climbs with resolution is wrong. `sensitivity = 17` is **Middle** — the
"Center sensitivity: Fast / Medium / Slow" control, which has seven wire values behind three UI
choices.

**Which debounce is which.** Three settings look alike and only two exist on a k5:

| Setting | sub-id | on this pad | English UI string |
|---|---|---|---|
| Joystick debounce | 5 | supported, on | "Joystick debounce" — off makes sticks read subtle movement better but jitter at rest, and **disables auto-calibration** |
| Rebound algorithm | 7 | supported, on | "Rebounce algorithm" — filters the reverse spike a stick's inertia produces on release |
| Motion debounce | 3 | **unsupported** | none, in any of the twelve locales — only a dangling `IpcCommandEnum_EnableMotionDebounce` |

So Space Station's debounce toggle is sub-id 5. Sub-id 3 needs no UI.

**Precision is device state, not profile state**, so it does not make the profile's curve bytes
multi-scale: 21 and 22 are standalone commands read back through command 3, while the control points
live in the 840-byte blob, and all four factory profiles carry identical ones. The stick's 0..127
and the trigger's 0..255 therefore read as two fixed normalisations of the stored format, with
bitness changing only how finely the output is quantised. Assumed, not proven — falsifiable in a
minute by writing a different precision and re-reading a profile.

## Small commands worth having

**S1. Reset one profile slot to factory** — `ResetMappingConfigByCfgIdCommandFactory`, **175**,
`[4]=3, [5]=cfgId, [6]=crc`. A 10 s timeout like the save, so it is a flash operation. Gated on
`ResetAllMappingUsable`, which the SDK sets unconditionally in the NewXInput branch — our mode.
Gives us the stock app's "Restore default". Destructive: test on slot 4 or the fake pad first.

**S2. Controller nickname** — write `UpdateNicknameCommandFactory` **24**, read
`ReadNicknameCommandFactory` **2**. Self-verifying, and it makes a two-pad setup legible. Note the
decompiled writer puts the CRC at `[6]`, which would overwrite the second name byte — assume it
belongs at `5 + len`.

**S3. Cooperative lock** — `AcquireControllerCommandFactory` **28**, `[5]=acquire`, `[6..25]` an
ASCII tag. Advisory only, and still outstanding. **This also closes an open
question in PROTOCOL.md §5:** it is *not* a precondition for trigger commands — the SDK never calls
it before `SetForceTrigger`, and our hardware tests already prove 81/82 work without it.

The read half is **built**: `motion.read_transport` (command **16**) returns the four transport
flags plus the 20-byte `control_by` tag, so "is something else driving the pad, and what" is one
read. The tag fills in with `SDL` on its own once third-party control is allowed —
[findings-steam.md](findings-steam.md).

**S4. Device settings — the sub-command map.** Command **19** is a generic "set feature N":
`[4]=4, [5]=subId, [6]=value, [7]=crc`.

**The reply echoes the value, not the sub-id.** Measured: `5a a5 13 01 00 <value> <crc>`. So
Flydigi's own `data[5]==subId` test never matches on this pad — match on `data[2]==19 &&
data[5]==value` instead. The consequence matters more than the fix: **nothing in the reply says
which sub-setting was written**, so an ACK means "a setting was written", not "this setting was
written". Read command 3 back when it matters which, as `screen.read_screen_status` does.

| sub | feature | sub | feature |
|---|---|---|---|
| 1 | **Quick-switch config** — `FN + A/B/X/Y` picks a profile, on the pad, nothing running | 6 | joystick auto-calibration |
| 2 | Xbox home button — reachable on the wire, refused by Flydigi's wrapper; see below | 7 | joystick rebound |
| 3 | motion debounce | 8 | status bar always on |
| 4 | mapping switch (no UI string; *not* the third-party toggle) | 9 | **always-on display** — the SDK calls it `OffScreen` and the name is inverted: 1 keeps the picture up, 0 blanks the panel |
| 5 | joystick debounce | 10 | audio (gated on the `AudioUsable` bit from command 3) |

Standalone, `[4]=3, [5]=value, [6]=crc`: **20** report rate `{1000=1, 500=2, 250=4, 125=8}`,
**21** joystick precision, **22** joystick sensitivity, **23** sleep time in minutes. **29** restart
takes no argument: `[4]=2, [5]=crc`.

**Sub-id 2 is refused by the SDK, not by the pad — and `ControllerType` is not about the host OS.**
Worth getting straight, because the obvious reading of the name is wrong. `ControllerHidManager`
decides the type from the HID device it opened, in one line:

```csharp
// ControllerHidManager.cs:35
ControllerType controllerType = (hid.ManufacturerString == "Microsoft") ? ControllerType.XInput
                              : (hid.VendorId != 14295)                 ? ControllerType.DInput
                              :                                           ControllerType.NewXInput;
```

`14295` is `0x37D7`, Flydigi's vendor id — what `flydigi/device.py` matches on. So the enum records
*which interface the SDK is talking through*, not a mode the pad is in and nothing to do with
Windows: **XInput** is an older pad or dongle that enumerates as a Microsoft device and takes its
commands through that impersonated interface (hence the separate command 48 framing); **NewXInput**
is a device on Flydigi's own vendor id, which is this pad's vendor interface and every `5a a5`
packet we send. The Apex 5 is a composite device and is both at once — `1.0` is the Xbox-compatible
gamepad that `xpad` binds and evdev exposes, `1.2` is the vendor interface — so "XInput to the
kernel" and "NewXInput to the SDK" describe two endpoints, not a contradiction.

`EnableXboxHomeButtonCommandFactory` has a full NewXInput branch building command 19 with sub-id 2,
so the command exists for our mode. What stops it is the wrapper, twice over:

```csharp
// ControllerSdk.cs:779, and again in EnableXboxHomeButtonImpl at :342
if (controller.ControllerType == ControllerType.XInput)
    Instance.EnableXboxHomeButtonImpl(controller, enable);
```

A branch of their own code that can never run on a pad like ours. Space Station's service forwards
only the `Usable`/`Enabled` bits to the UI, and there is no locale string for the setting in any of
the twelve languages, so nothing in their app calls it either. Our pad reports it supported **and
on**, and sending `19 / 2 / 0` goes through the same path as every other sub-command — so what it
does is a one-line experiment rather than a dead end. The legacy framing (command 48, `byte[2] =
9/10`) hints at whether Home reaches the host or is handled on the pad, but that is inference; only
the pad settles it.

**Do sub-ID 1 first** — quick-switch is the only one here that gives a Linux user something
otherwise unobtainable: switching profiles from the pad with nothing running.

## Battery — settled: there is no percentage in this SDK

Every path resolves to
the same 4-bit nibble. `HeartBeatCommandFactory`'s NewXInput branch is
`Battery = (data[i] >> 4 == 1) ? 6 : (data[i] & 0xF)`, and the XInput and DInput branches do the
identical thing at `data[23]` and `data[10]`. `ExtraInfoCommandFactory` carries no battery field at
all. The only richer variant is a `DeviceCode == "f4"` special case remapping raw 3→2 and 5→6. If a
percentage exists it is in the dongle or the input report, not the command set, and that is where to
look.

**The scale is 0..5, not 0..15.** The nibble is four bits, but the values are not a byte range:
Space Station ships exactly seven battery icons and picks one as `Power${level <= 6 ? level : 0}.svg`,
while the SDK turns the charging bit into the literal **6**. So the domain is 0..6 with 6 meaning
*charging*, leaving **0..5 for charge and 5 as a full pad**. `motion.MAX_LEVEL` is the one source of
truth; the GUI reads `BATTERY_STEPS = motion.MAX_LEVEL` rather than repeating the number.

Confirmed against the pad on the desk: wired, `battery_level: 5, charging: False` — full.

What the same multi-packet reply *does* carry, in order after device type and connect type: MAC
(4 bytes, reversed), the battery nibble, chip type, motion chip type, then seven BCD firmware
versions — main, dongle, switch/SI, trigger, screen, ADC, NearLink. `IsAckFinished` is
`data[4] > data[3] || data[4] == data[3] - 1`, so it is fragmented.

**The version decode is built**: `motion.parse_versions` / `read_versions`. Versions start at raw
offset 16, two BCD bytes each, one version field per nibble — `0x70 0x45` is 7.0.4.5. **All-zero
means "not present"**, not version zero, which is how a wired pad reports no dongle and an Apex 5
reports no ADC chip (that one is a Vader 4 part).

**Comparing them, do not copy Flydigi.** `DeviceUtil.CompareVersion` is
`string.Compare(new, old, Ordinal) >= 0` — an ordinal *string* comparison, so their own gate rejects
firmware 7.0.10.0 against a 7.0.3.0 minimum, because "1" sorts below "3". `motion.version_at_least`
compares numerically, which differs from them only where they are wrong.

## An editor for the vibration bind

Tier 1 is one bind — game rumble drives the trigger
motors — and each "supported game" is a **preset** of numbers for it: `vibType`, `vibFilter`,
`pwmScal`, and `vibParams` (stroke, pressure, strength, frequency per side). That is a sensible
design; the labels just have to say so, or it reads as a per-game integration like the other four
routes. Wording was fixed; the numbers still cannot be edited from the GUI, only through
`tools/flydigi_cmd.py bind`.

**The persistent form exists, and it is settled** — see J4 in
[findings-profile-blob.md](findings-profile-blob.md). The profile blob's force-trigger section holds
a `bind` sub-struct at **offset 185** + 20 per side of `type, filter, scale + 5 params`, against live
command 82's `bindType, filter, scale + 4 params`: the same structure with one spare byte. So an
editor can write the bind into the profile and have it survive a sleep, instead of being re-applied
every session. That is the work remaining here — today the Games page applies a preset with live
command 82, which the pad forgets when it sleeps.

## RGB: not working via the test command

`TestLedCommandFactory` (command **245**, `[4]=5, [5]=R, [6]=G, [7]=B, [8]=sum(3,3+5)`) ACKs
cleanly and echoes the exact RGB values back, but **the controller's lighting does not change** --
tested with 3-second holds per colour, re-sent at 4 Hz, so an overriding mode would have shown as a
flicker.

Most likely explanation: 245 lives in `command.test/` alongside TestScreen/TestJoystick/TestRF and
is exposed as `IpcCommandEnum_TestRgb`. These are factory-test commands and may require the device
to be in a diagnostic state first.

**The real path is the persistent config, and it is built** — `flydigi/lighting.py`, with a Lighting
page in the app. Three commands, sharing the blob transfer of PROTOCOL.md §9:

  * `ReadLedConfigCommand` = **167**, `[4]=4, [5]=cfgId, [6]=pkgSize, [7]=sum`
  * `WriteRgbConfigStart` = **168**, `[cfgId, startIdx, nPkts, pkgSize]`
  * `WriteRgbConfigCommand` = **169**, written in packs: `[4]=len+3, [5]=packNum, [6..]=pack data`

**The Apex 5's config is 380 bytes**, and not the shape the older decompiled struct describes — do
not assume `id[16]` × 10 or a 490-byte blob:

```
 2      click feedback        7      LED count
 3, 4   loop start / end      8      mode
 5      cycle time            9      grip sync
 6      brightness           20..    frames: 10 x 12 LEDs, 3 bytes each, RGB
```

**The mode byte does not select an effect.** The pad has no animation generator — it plays the
stored frames, cycling `loop_start`..`loop_end` every `cycle_time`. `mode` only records which of
Space Station's generators produced the data, so writing a different mode number changes nothing
visible. Changing the lighting means **writing frames**, which is what `set_breath`, `set_flow`,
`set_rainbow` and `set_solid` do.

So bridging the DualSense lightbar to the pad is a frame write (`set_solid`), not a mode write. The
lightbar bytes themselves are already parsed (`data[45..47]` of the DS5 output report).


## Command inventory, by feature

What a full Space Station replacement needs, and the command factories already recovered for
each (all in `decompiled/`).

| Feature | Commands |
|---|---|
| Screen image (gamepad + charging dock) | `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`, `TestScreen`, `OffScreen`, `ReadScreenSetting`, `EnableScreenStatusBarAlwaysOn` |
| Trigger config, game-independent | `SetForceTriggerCommandFactory` (working), `K6Trigger*` |
| Profile switching | `ApplyMappingConfigByCfgId`, `SaveCurrentMappingConfig`, `ReadCurrentMappingConfigId`, `WriteAllMappingConfig`, `ResetMappingConfigByCfgId` |
| RGB / LED | `WriteRgbConfig`, `WriteAllRgbConfig`, `ReadLedConfig`, `TestLed` |
| Macros | **done** — the profile's own page at 230, plus command 162 to make one live. `ReadMacroConfig` (172) and `WriteMarcoConfig` (173/174) belong to protocol 3.2 and later, which is not this pad; `SetHardwareMacroEnable` (80) is XInput/DInput-only dead code and was not needed. → [findings-profile-blob.md](findings-profile-blob.md) |
| Device settings | 22 in `command.setting/`: report rate, stick sensitivity/precision, debounce, rebound, auto-calibration, motion debounce, sleep time, dock smart stop, mode switch, nickname |
| Dock / cooler | `Flydigi.ChargerSdk.dll`, `Flydigi.CoolerSdk.dll` (in `bundle/`, not yet decompiled) |
