# Device settings

Everything the pad holds outside a profile: the command-3 status block, the command-19
and 20..23/29 writes behind it, the battery nibble and the firmware versions, the LED
config blob and the sequence that saves it, and the command inventory.

Index: [PROGRESS.md](../PROGRESS.md).

`flydigi/settings.py` is the read and every write; `tools/flydigi-settings` is the CLI; the app's
**Device** page is the UI, with the two screen bits (subs 8 and 9) on its **Screen** page.
`tests/test_settings.py` runs standalone — `python3 tests/test_settings.py`, no pytest — against
`tests/fake_pad.FakePad`, and covers the reply decode, the packet framing for 19, 20..23 and 29, the
read-back path, and the fact that the pad ACKs sub-ids it reports unsupported.

Five write commands are verified on hardware — 19 sub 1 (quick-switch), 19 sub 9 (always-on
display), 21 (precision), 22 (sensitivity) and 23 (sleep time) — each written and read back through
command 3. Command 29 (restart) is built and has never been sent; it is the only write with no
read-back, and any handle open across it has to be reopened (`flydigi/settings.py:246-253`,
`tools/flydigi-settings:139-147`). Report rate is read and shown; the app offers no control for it,
and `tools/flydigi-settings report-rate` sits behind `--i-know`.

The CLI has 16 subcommands: `show`, ten on/off switches (`quick-switch`, `xbox-home`,
`motion-debounce`, `mapping-switch`, `stick-debounce`, `auto-calibration`, `stick-rebound`,
`status-bar`, `always-on`, `audio`), `sleep <0..60|never>`, `precision`, `sensitivity`,
`report-rate` and `restart`. The three numeric ones take names, not the wire values below: `8bit`,
`9bit`, `10bit`, `11bit`, `12bit`, `14bit`, `16bit`; `highest`, `high`, `middlehigh`, `middle`,
`lowmiddle`, `low`, `lowest`; and a rate in Hz — 125, 250, 500 or 1000 — inverted to the wire value
on the way out (`tools/flydigi-settings:61-64`, `:160-161`). `report-rate` and `xbox-home` refuse to
run without `--i-know`. Every operation, `show` included, goes through `identity.require(ctrl)`
first: the pad opens by vendor id alone, so without that guard the tool would write a Vader 4 Pro's
settings block under this pad's field names.

## Command 3: the whole settings block in one read

`ReadHardwareFunctionStatus`, NewXInput command **3**, payload length 2 (no arguments). The reply
carries capability and enabled bits separately, so the pad reports both what it supports and what is
on:

```
data[5]  supported   bit0 quick-switch config   bit1 Xbox home button  bit2 motion debounce
                     bit3 mapping switch        bit4 stick debounce    bit5 stick auto-calibration
                     bit6 stick rebound         bit7 status bar always on
data[6]  enabled     same bit order
data[7]  supported   bit0 always-on display (SDK: `OffScreen`)   bit1 audio
data[8]  enabled     same
data[9]  sleep time        data[10] report rate
data[11] stick precision   data[12] stick sensitivity
```

A command-3 reply from a wired Apex 5:

```
reply  5a a5 03 01 00 fb 7b 01 00 0f 00 02 11 …
```

Everything is supported except **motion debounce** and **audio**, and everything supported is on
except **always-on display** — so the panel is dark — and **status bar always on**. Sleep time is
**15** (minutes), report rate **0**, stick precision **2**, stick sensitivity **17**.

Sleep time is a byte of minutes, readable as well as writable. 0 is Flydigi's "never", and
`settings.set_sleep_minutes` clamps to 60, which is their own picker's ceiling rather than the
byte's.

`audio` is unsupported, which matches `AudioUsable` being gated — so the audio sub-command is dead
on this pad. `motion debounce` is unsupported too, so sub-id 3 needs no UI. And **report rate
reads 0**, which is not in the Hz map `{1000=1, 500=2, 250=4, 125=8}` — the enum's only other member
is `ReportRate_None = 0`, so 0 reads as "default/unset". Do not write that field until a read on a
pad whose rate has actually been set confirms that.

**The endpoint descriptors argue for "default".** All three IN endpoints poll at the USB minimum;
the two OUT endpoints are at 4 ms:

```
3-4:1.0  xpad    ep_81 IN   interrupt  1 ms    the Xbox-compatible gamepad
                 ep_02 OUT  interrupt  4 ms
3-4:1.1  usbhid  ep_82 IN   interrupt  1 ms    keyboard/mouse node, `05 01 09 06` descriptor
3-4:1.2  usbhid  ep_83 IN   interrupt  1 ms    the vendor interface, `06 a0 ff` descriptor
                 ep_03 OUT  interrupt  4 ms
```

The pad is full-speed (12 Mbit/s), where 1 ms is the shortest frame, so 1000 Hz is the ceiling for
all three and the pad is already at it: a setting of 0 alongside a 1 ms endpoint reads as "default =
1000" rather than "unset". The 4 ms OUT interval bounds how fast commands can be written. The two
input paths differ in *delivery*, not in rate — evdev is event-driven and emits nothing while a
stick is still, whereas the vendor stream sends regardless, ~970 Hz wired. Interface 1.1's
keyboard/mouse descriptor is what exposes the `Flydigi Flydigi APEX5  Keyboard` and `… Mouse` evdev
devices; 1.2 is the vendor node this project opens.

Both numeric fields are enums in `Flydigi.SharedResources`, and neither is the number it looks like:

```
JoystickPrecision   None, 8Bit, 10Bit, 12Bit, 9Bit, 11Bit, 14Bit, 16Bit    (declaration order!)
JoystickSensitivity None=0, Highest=14, High=15, MiddleHigh=16,
                    Middle=17, LowMiddle=18, Low=19, Lowest=20
```

`JoystickPrecision` is ordered as it was **written**, not by bit depth: 9-bit and 11-bit were added
after 8/10/12, and 14/16 later still. So this pad's `precision = 2` is **10 bit**, and any mapping
that assumes the value climbs with resolution is wrong. `sensitivity = 17` is **Middle** — the
"Center sensitivity: Fast / Medium / Slow" control, which has seven wire values behind three UI
choices.

Three settings look alike, and only two exist on a k5:

| Setting | sub-id | on this pad | English UI string |
|---|---|---|---|
| Joystick debounce | 5 | supported, on | "Joystick debounce" — off makes sticks read subtle movement better but jitter at rest, and **disables auto-calibration** |
| Rebound algorithm | 7 | supported, on | "Rebounce algorithm" — filters the reverse spike a stick's inertia produces on release |
| Motion debounce | 3 | **unsupported** | none, in any of the twelve locales — only a dangling `IpcCommandEnum_EnableMotionDebounce` |

Space Station's debounce toggle is sub-id 5. The app greys its stick-auto-calibration row unless
stick debounce is on (`SettingsModel.autoCalibrationUsable`), following Flydigi's own
`disable_joy_debounce_desc` string: turning debounce off prevents automatic calibration from
working.

Precision is device state, not profile state: 21 and 22 are standalone commands read back through
command 3, while the control points live in the 840-byte blob, and all four factory profiles carry
identical ones. Profile 1 read at 12-bit and at 10-bit returns byte-identical curve regions:

```
curve  00003f3f7f7f7f 00003f3f7f7f7f      at 12-bit and at 10-bit
extra  00323e4b5764707d8996 0000 …        likewise
```

The stored control points are on their own fixed scale, so the stick's 0..127 and the trigger's
0..255 are two fixed normalisations of the stored format, with bitness changing only how finely the
output is quantised; a curve editor does not have to know the pad's bitness.

### Precision quantises the evdev report, not the vendor stream

Sampled by sweeping the sticks in full circles for a fixed 20 seconds and counting how many
*distinct* values each axis produced; quantising to N bits inside a 16-bit field caps that count
at 2^N.

| Stream | at 10-bit | at 12-bit |
|---|---|---|
| evdev gamepad node (`Flydigi Apex 5`) | **1008, 1014, 1013, 1020** — all four axes against 1024 = 2¹⁰, none over | **~3050 per axis** — far past 1024, heading for 4096 |
| vendor input report (hidraw, marker `0xEF`) | ~2100 per axis, no lattice | ~1800 per axis, no lattice |

The vendor stream shows no ceiling at either setting, and the gcd of its distinct values is 1 rather
than 64 or 16 — so **the raw stream is not quantised by this setting at all**. It carries the pad's
own resolution and the control sits downstream of it, on the XInput path: a relay or a probe reading
sticks off the vendor stream is unaffected by this setting, and an evdev reader is not.

The sticks themselves are in that report, at `motion.STICK_OFFSETS`, and nothing reads them —
[PROGRESS.md](../PROGRESS.md).

Measuring any of this needs third-party mode off: with it on, `controller_data` is off and the evdev
node sends nothing at all, so a 10-bit run reads as a dead pad rather than a quiet one —
[findings-steam.md](findings-steam.md).

## Command 19: the sub-command map

Command **19** is a generic "set feature N": `[4]=4, [5]=subId, [6]=value, [7]=crc`.

**Sub-id N is bit N-1 of the command-3 reply**, with 9 and 10 rolling into the second byte pair. The
read layout above and the sub-command map below are one list, `settings.FEATURES`, and
`tests/test_settings.py` asserts the invariant.

| sub | feature | sub | feature |
|---|---|---|---|
| 1 | **Quick-switch config** — Flydigi call it "Fast Swap Config"; their string templates the modifier as `{{key}} + A/B/X/Y` and does not name FN. Picks a profile on the pad, nothing running | 6 | joystick auto-calibration |
| 2 | Xbox home button — reachable on the wire, refused by Flydigi's wrapper; see below | 7 | joystick rebound |
| 3 | motion debounce | 8 | status bar always on — [findings-screen.md](findings-screen.md) |
| 4 | mapping switch (no UI string; *not* the third-party toggle) | 9 | **always-on display** — the SDK's `OffScreen` name is inverted against what the bit does; measured in [PROTOCOL.md](../PROTOCOL.md) §8c |
| 5 | joystick debounce | 10 | audio (gated on the `AudioUsable` bit from command 3) |

Standalone, `[4]=3, [5]=value, [6]=crc`: **20** report rate `{1000=1, 500=2, 250=4, 125=8}`,
**21** joystick precision, **22** joystick sensitivity, **23** sleep time in minutes. **29** restart
takes no argument: `[4]=2, [5]=crc`.

Replies to 20, 21, 22 and 23 are matched on the command byte alone: only command 19's reply has ever
been measured, so no success-flag position is asserted for the other four.

**Sub-id 2 is refused by the SDK, not by the pad, and `ControllerType` is not about the host OS.**
`ControllerHidManager.cs:35` decides the type from the HID device it opened:
`ManufacturerString == "Microsoft"` → `XInput`, else `VendorId != 14295` → `DInput`, else
`NewXInput`. `14295` is `0x37D7`, Flydigi's vendor id — what `flydigi/device.py` matches on. The
enum records *which interface the SDK talks through*: **XInput** is a pad or dongle enumerating as a
Microsoft device, commanded through that impersonated interface, where the Xbox-home write is
command **48** with `[2] = 10` to enable and `9` to disable; **NewXInput** is a device on Flydigi's
own vendor id, this pad's vendor interface and every `5a a5` packet this project sends. The Apex 5
is composite and is both at once — `1.0` is the Xbox-compatible gamepad `xpad` binds, `1.2` is the
vendor interface.

`EnableXboxHomeButtonCommandFactory` has a full NewXInput branch building command 19 with sub-id 2,
so the command exists for this mode. The wrapper stops it twice over: `ControllerSdk.cs:780` and
`EnableXboxHomeButtonImpl` at `:342` both guard the call with
`if (controller.ControllerType == ControllerType.XInput)`. There is no locale string for the setting
in any of the twelve languages, and their service forwards only the `Usable`/`Enabled` bits to the
UI. This pad reports it supported **and on**, and `19 / 2 / 0` takes the same path as every other
sub-command; what it does to the Home key is unmeasured.

**And it stays unmeasured** — [ruled out](../PROGRESS.md#ruled-out). Their renderer never receives
`xboxHomeButtonUsable` at all, only `Enabled`, which it stores and draws nowhere, so no v4 UI can
offer this however the SDK is built. `XboxHomeButtonUsable` is hardcoded true for `f4`, `fp3`, `fp4`
and DeviceType 102 (an Apex 4 SKU), and those are the pads whose Home button is also the power
button — measured on the Vader 4 on this desk: a short press is an ordinary Guide, a long one powers
the pad off. An Apex 5 has a rear power slider, so its Home is Guide and nothing else, which is why
there is nothing here to configure and why the flag reads supported and on.

**Command 48 is the XInput dialect's sub-command opcode**, the counterpart of 19's sub-id space:
`[0]=0xA5, [1]=48, [2]=sub`, fifteen bytes, no checksum, on the transport in
[findings-other-devices.md](findings-other-devices.md). Every other setting there carries its value
in `[3]`; Xbox home alone spends two sub-ids and no value byte, which is what an either/or mode flip
looks like rather than a stored preference.

| sub | function | sub | function |
|---|---|---|---|
| 1 | `ExtraInfo` — trigger, screen and switch chip versions | 9 | **Xbox home off** |
| 2 | read screen setting | 10 | **Xbox home on** |
| 3 | status bar always on | 11 | upgrade mode: screen chip, and the main chip on WCH |
| 4 | read auto-sleep period | 12 | upgrade mode: trigger chip |
| 5 | sleep time | 13 | motion debounce |
| 6 | force trigger, every mode but the grip bind | 14 | test vibration |
| 8 | force trigger, the grip bind — and `TestScreen`, which collides with it | | |

Every write goes through `settings.apply(ctrl, name, value)`, the single entry point the CLI and the
GUI share: it dispatches by setting name to either a command-19 sub-id or one of the four standalone
commands, writes, and returns the command-3 block as it reads afterwards; an unknown name raises
`SettingsError` rather than silently writing nothing. The read-back is load-bearing — a command-19
ACK is `5a a5 13 01 00 <value> <crc>`, echoing the value and never the sub-id, so it cannot say
which setting moved, and the pad acknowledges sub-ids it does not support at all. Flydigi's own
`data[5] == subId` test therefore compares the value against the sub-id and matches only where the
two coincide, at sub 1 written to 1; match on `data[2] == 19 && data[5] == value` instead, as
`flydigi/settings.py:193` does.

| Command | Written | Read back as |
|---|---|---|
| 19 sub 1 | 0, then 1 | quick-switch off, then on |
| 21 | 3 | precision 12-bit (from 10-bit) |
| 22 | 16 | sensitivity Middle-high (from Middle) |
| 23 | 30, then 15 | sleep 30 min, then 15 |

## Commands beyond the settings block

**Reset one profile slot to factory** — `ResetMappingConfigByCfgIdCommandFactory`, **175**,
`[4]=3, [5]=cfgId, [6]=crc`. A 10 s timeout like the save, so it is a flash operation. Gated on
`ResetAllMappingUsable`, which the SDK sets unconditionally in the NewXInput branch — this pad's
mode. This is the stock app's "Restore default". Destructive: test on slot 4 or the fake pad first.

**It works on this pad, and it takes the profile's name with it.** Observed through Space Station's
own "Restore default" on the Apex 5 here: a slot renamed out of its factory name came back carrying
that factory name again — which on this pad is Chinese. That follows from where a name lives: blob
offset **770**, twenty bytes of UTF-16LE (`MappingConfig.title`), so it is a field of the slot like
any other and a slot reset restores it along with the rest. Two consequences for a UI offering this.
The rename on the app's Controller page is undone by it, so the confirmation has to say more than
"restore defaults". And twenty bytes of UTF-16 is ten characters whichever script they are in, which
is why ten Chinese ones fit where a longer English name will not.

**Controller nickname** — write `UpdateNicknameCommandFactory` **24**, read
`ReadNicknameCommandFactory` **2**. The decompiled writer puts the CRC at `[6]`, which would
overwrite the second name byte — assume it belongs at `5 + len`.

**Cooperative lock** — `AcquireControllerCommandFactory` **28**, `[4]=23, [5]=acquire, [6..25]` an
ASCII tag truncated at 20 bytes, `[26]=crc`; the reply carries the grant in `data[5]`. Advisory
only, and unimplemented here. It is *not* a precondition for trigger commands: the SDK never calls
it before `SetForceTrigger`, and 81/82 are hardware-verified without it.

**Transport flags** — `motion.read_transport` (command **16**) returns the five flags
`controller_data`, `raw_data`, `keyboard`, `mouse` and `third_party`, plus the 20-byte `control_by`
tag, so "is something else driving the pad, and what" is one read. The tag fills in with `SDL` on
its own once third-party control is allowed — [findings-steam.md](findings-steam.md).

## Battery: a 0..5 level, and no finer figure

The pad reports charge as a level of 0..5 plus a separate charging state — six states, so 20%
granularity. Nothing in the command set carries a finer figure than that.

Every path resolves to the same 4-bit nibble, at raw index 12 of the command-1 reply: the high
nibble is 1 while charging, the low nibble is the level (`flydigi/motion.py:115-131`).
`HeartBeatCommandFactory`'s NewXInput branch is `Battery = (data[i] >> 4 == 1) ? 6 : (data[i] & 0xF)`,
and the XInput and DInput branches do the identical thing at `data[23]` and `data[10]`.
`ExtraInfoCommandFactory` carries no battery field at all, so a 1% figure exists nowhere in the
command set; if one exists it is in the dongle or the input report. The only richer variant is a
`DeviceCode == "f4"` special case remapping raw 3→2 and 5→6.

**The scale is 0..5, not 0..15.** The nibble is four bits, but the values are not a byte range:
Space Station ships exactly seven battery icons and picks one as `Power${level <= 6 ? level : 0}.svg`,
while the SDK turns the charging bit into the literal **6**. So the domain is 0..6 with 6 meaning
*charging*, leaving **0..5 for charge and 5 as a full pad**. `motion.CHARGING_LEVEL = 6` and
`motion.MAX_LEVEL = 5` (`flydigi/motion.py:91-92`) are the one source of truth; the GUI reads
`BATTERY_STEPS = motion.MAX_LEVEL` rather than repeating the number.

Confirmed on hardware: wired, `battery_level: 5, charging: False` — full.

The same multi-packet reply carries, in order after device type and connect type: MAC (4 bytes,
reversed), the battery nibble, chip type, motion chip type, then seven BCD firmware versions — main,
dongle, switch/SI, trigger, screen, ADC, NearLink. `IsAckFinished` is
`data[4] > data[3] || data[4] == data[3] - 1`, so it is fragmented.

`motion.parse_versions` / `read_versions` decode them: versions start at raw offset 16, two BCD bytes
each, one version field per nibble — `0x70 0x45` is 7.0.4.5. **All-zero means "not present"**, not
version zero, which is how a wired pad reports no dongle and an Apex 5 reports no ADC chip (that one
is a Vader 4 part).

`DeviceUtil.CompareVersion` is `string.Compare(new, old, Ordinal) >= 0` — an ordinal *string*
comparison, so their own gate rejects firmware 7.0.10.0 against a 7.0.3.0 minimum, because "1" sorts
below "3". `motion.version_at_least` compares numerically.

## The vibration bind

Tier 1 is one bind — game rumble drives the trigger motors — and each "supported game" is a
**preset** of numbers for it: `vibType`, `vibFilter`, `pwmScal`, and `vibParams` (stroke, pressure,
strength, frequency per side). The bind's four numbers — intensity coefficient, vibration threshold,
travel range and frequency — are the Vibration effect's knobs on the app's **Triggers** page
(`flydigi/effects.py:139-149`), and are also reachable from `tools/flydigi_cmd.py bind`. Its stored
form is the profile blob's `bind` sub-struct, J4 in
[findings-profile-blob.md](findings-profile-blob.md).

Storing it engages nothing: `effects.engage_stored` rebuilds a live command per side from the
profile's bytes after a 500 ms wait — **82** for a stored Vibration bind, 81 for the other effects —
and `tools/flydigi-mapping` and `gui/worker.py` call it after every mutating write. Never side 3, a
command addressed to `Both` ACKs and does nothing ([PROTOCOL.md](../PROTOCOL.md) §3a). A bind is
live state that does not survive the pad leaving the bus, so the route replays rather than stores —
[findings-games.md](findings-games.md).

## RGB: the LED config blob

`TestLedCommandFactory` (command **245**, `[4]=5, [5]=R, [6]=G, [7]=B, [8]=sum(3,3+5)`) ACKs cleanly
and echoes the exact RGB values back, but **the controller's lighting does not change** — tested
with 3-second holds per colour, re-sent at 4 Hz, so an overriding mode would have shown as a
flicker. 245 lives in `command.test/` alongside
TestScreen/TestJoystick/TestRF and is exposed as `IpcCommandEnum_TestRgb`; these are factory-test
commands and may require the device to be in a diagnostic state first.

The real path is the persistent config: `flydigi/lighting.py`, with a Lighting page in the app.
Three commands, sharing the blob transfer of [PROTOCOL.md](../PROTOCOL.md) §9:

  * `ReadLedConfigCommand` = **167**, `[4]=4, [5]=cfgId, [6]=pkgSize, [7]=sum`
  * `WriteRgbConfigStart` = **168**, `[4]=6, [5]=cfgId, [6]=startIdx, [7]=nPkts, [8]=pkgSize, [9]=crc`
  * `WriteRgbConfigCommand` = **169**, written in packs: `[4]=len+3, [5]=packNum, [6..]=pack data`

Both LED and mapping blobs move as contiguous runs of changed packets diffed against an `old` blob,
with a 168-style header per run announcing how many packets follow — so the pad is tracking a
sequence, and nothing else may interleave (`flydigi/blobs.write_blob` holds `ctrl.claim()` for the
whole transfer).

**The Apex 5's config is 380 bytes**, 19 packets of 20, and not the shape the older decompiled
struct describes — do not assume `id[16]` × 10 or a 490-byte blob:

```
 0, 1   config version, little endian (0x0300 on this pad)
 2      click feedback
 3, 4   loop start / loop end frame
 5      cycle time -- a larger number is a *slower* animation
 6      brightness, 0..100
 7      LED count (12 on an Apex 5)
 8      mode
 9      grip sync
10..20  0xFF fill, written deliberately by `RgbConfigParserV30`, not merely reserved
20..    animation frames: 10 x 12 LEDs, 3 bytes each, RGB
```

Derive the geometry from the blob rather than assuming it: `leds_per_frame` is byte 7 and `frames`
is `(len(blob) - 20) // (3 * leds_per_frame)`. Writing colours against assumed dimensions runs off
the end and silently grows the config.

**The mode byte does not select an effect.** The pad has no animation generator — it plays the
stored frames, cycling `loop_start`..`loop_end` every `cycle_time`. `mode` only records which of
Space Station's generators produced the data, so writing a different mode number changes nothing
visible. Its values are `LedType`: 0 Unknown, 1 Flow, 2 Breath, 3 Gradient, 4 Feedback, 5 On,
6 Close, 7 Default (`Flydigi.SharedResources.decompiled.cs:29278`, written as
`configBean.LedMode = (int)config.Mode` at `ControllerDataMapper.cs:185` and parsed back at `:72`).
An Apex 5 ships storing 7, `Default`, which is the first config
`ControllerDataMapper.GetDefaultLedConfigsByDevice` builds for a pad on this protocol.

Changing the lighting means **writing frames**. `LedConfig.apply_effect(effect, colours)` dispatches
to nine generators — `set_off`, `set_flow`, `set_rotation`, `set_breath`, `set_solid`,
`set_static_multi`, `set_rainbow`, `set_wave`, `set_flash` — and writes the effect id into the mode
byte alongside their frames. The ids are `RgbEffectTypeProto`'s (the cooler and dock enum), not
`LedType`'s, so the number a config records is not the one Space Station would map back to its own
picker.

So bridging the DualSense lightbar to the pad is a frame write (`set_solid`), not a mode write. The
lightbar is `data[45..47]` of the DualSense output report, R/G/B, in the same indexing
`flydigi/ds5.py` uses for everything else in that report. Nothing here decodes it —
`ds5.parse_output` takes only rumble and the two trigger-effect blocks.

### Byte 9 is GripSync; byte 2 is ClickFeedback

`LedConfig.grip_sync` writes byte 9; the Lighting page's switch is labelled "Vibration light
effect", Flydigi's own string for it, described as "There will be a special light effect when the
grip vibrates". `LedConfigParser`'s v3.0 branch reads it at index 9, and it is set to **1** on the
pad here.

Measured at brightness 80, the same left-grip-only rumble run twice with only byte 9 differing:

| byte 9 | what the ring did while the motor ran |
|---|---|
| 0 | nothing at all |
| 1 | **a segment of the bar dimmed**, and came back when the rumble stopped |

The same dimming appears at brightness 20. Which segment dims did not reproduce between runs — left
in one, right in another, not obviously following the motor that was running — so the side mapping
is open.

**Byte 2 is `ClickFeedback`, and Space Station sets it as a consequence rather than as a control**:
`configBean.ClickFeedback = config.Mode == LedType.Feedback` — true exactly when the user picks the
**Feedback** lighting mode, `LedType` 4. Feedback and `On` generate byte-identical frames — the one
chosen colour in frame 0 and every other frame black, since the v3 builder blacks out `frame != 0`
for both — so the only wire difference between the two modes is byte 2 and `loop_end` (1 for
Feedback, 0 for On).

**Space Station offers Feedback on a Vader 4 and on no other pad.**
`ControllerDataMapper.GetDefaultLedConfigsByDevice` builds the mode picker per device and adds
`LedType.Feedback` behind exactly one condition, `deviceCode == "f4"` — with `Brightness = 50`,
`Period = 15` and one colour, blue 100. The Apex 5 is `k5`, so the mode never reaches this pad's
picker. Whether it means anything beyond the latch below can only be answered on a Vader 4 Pro,
where it is offered; none of the twelve locales describes it, just the bare word.

**Byte 2 drives no input feedback on this pad.** Written as a one-packet diff over the pad's stock
Default frames and read back as 1, it produced no reaction to face buttons, shoulders, triggers,
paddles, the LM/RM shoulder pair, D-pad, stick clicks, stick movement or motion — with third-party control **on, and
again with it off** so the gamepad report was definitely live. Rumble and a live trigger-vibration
effect produced only the byte-9 dimming above. Space Station's whole Feedback write — undiffed
42-packet mapping write, RGB config with mode 4, byte 2 set, one colour in frame 0 and every other
frame black, `loop_end = 1`, then 171, 250 ms, 166 — renders the colour and holds it, with no
reaction to any of those inputs either.

**What byte 2 does on a k5 is latch the display.** With it set the ring freezes on the frame it is
showing and later frame writes do not appear — blue and white frames written while it was set left
the panel on the previous red, clearing the byte alone made the new frames appear at once, and
setting it again froze them. `write_config` sends packet 0 first, and byte 2 lives there, so setting
it in the same write freezes the pad **before its own frames arrive**. A save (171, then 166)
re-renders past the latch.

**The full sequence Space Station sends for a lighting change:**

| # | call | wire | note |
|---|---|---|---|
| 1 | `WriteMappingConfig` | 164/165 | **undiffed** — passes `null` as the old config, so all 42 packets go every time |
| 2 | `WriteRgbConfigById` | 168/169 | the LED blob |
| 3 | `PermanentSaveSwitchMappingConfig` | **171** | `[ver_lo, ver_hi, cfgId]`, 10 s timeout, a fresh random data version |
| 4 | `SaveConfig` → `PermanentSaveMappingConfig` | **166** | 250 ms later, a *second* new data version |

**171 is not implemented here** — `flydigi/mapping.py` has only 166. Both ACK on hardware; 171's
builder has the same self-contradiction as command 242, with a length byte of 4 implying a checksum
at offset 7 while `cfgId` sits there and the crc goes at 8 over a range that excludes it. The pad
answers the literal bytes, so their placement wins over their length byte.

**`OldLedConfig` is mapping-profile bytes 3..13**, ten bytes `flydigi/mapping.py` skips entirely —
it goes from offset 2 straight to the key table at 13. On the pad here it reads
`[32, 4, 40, 160, 0, 0, 255, 0, 0, 0]`; the `4` matches the LED config's `cycle_time` and `255, 0, 0`
looks like a colour, so it is probably a stale mirror of the older LED format. Nothing outside
`MappingConfigParser` computes it, and both applications preserve it unchanged.

## Command inventory, by feature

What a full Space Station replacement needs, and the command factories already recovered for
each (all in `decompiled/`).

| Feature | Commands |
|---|---|
| Screen image (gamepad + charging dock) | `TestScreen`, `OffScreen`, `ReadScreenSetting`, `EnableScreenStatusBarAlwaysOn`. The HID upload family (`UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`, 208..211) parses and ACKs on an Apex 5 and puts no picture on the panel, while still changing stored state — 211 commits picture metadata, and an upload leaves the status-bar flag on ([findings-screen.md](findings-screen.md)). The serial path of [PROTOCOL.md](../PROTOCOL.md) §8d is what drives this screen |
| Trigger config, game-independent | `SetForceTriggerCommandFactory`, `K6Trigger*` |
| Profile switching | `ApplyMappingConfigByCfgId`, `SaveCurrentMappingConfig`, `ReadCurrentMappingConfigId`, `WriteAllMappingConfig`, `ResetMappingConfigByCfgId` |
| RGB / LED | `WriteRgbConfig`, `WriteAllRgbConfig`, `ReadLedConfig`, `TestLed` |
| Macros | The profile's own page at 230, plus command 162 to make one live. `ReadMacroConfig` (172) and `WriteMarcoConfig` (173/174) belong to protocol 3.2 and later, which is not this pad; `SetHardwareMacroEnable` (80) is XInput/DInput-only dead code. → [findings-profile-blob.md](findings-profile-blob.md) |
| Device settings | 22 in `command.setting/`: report rate, stick sensitivity/precision, debounce, rebound, auto-calibration, motion debounce, sleep time, dock smart stop, mode switch, nickname |
| Dock / cooler | `Flydigi.ChargerSdk.dll`, `Flydigi.CoolerSdk.dll` (in `bundle/`, not decompiled) |
