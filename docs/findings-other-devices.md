# Other Flydigi hardware

How a device is told from another one of the same kind, what is gated on a device this project does
not drive, the charging dock, and what command 31 may and may not be aimed at.

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
| `f3` | `GenerateControllerVader3` | Vader 3 | 28 |
| `f3p` | `GenerateControllerVader3` | Vader 3 Pro | 80, 81, 88 |
| `f4` | `GenerateControllerVader4` | Vader 4 | 85, 91 |
| `f5` | `GenerateControllerVader5` | Vader 5 Pro | 130, 144, 145 |
| `fp1` | `GenerateControllerDirewolf` | Direwolf | 25, 30, 31 |
| `fp2` | `GenerateControllerDirewolf` | Direwolf 2 | 82, 83, 89, 90, 94 |
| `fp3` | `GenerateControllerDirewolf` | Direwolf 3 | 95, 97 |
| `fp4` | `GenerateControllerDirewolf` | Direwolf 4 | 132, 146, 147, 148 |

The code column and the number column are not in step. `GetDeviceCodeById` maps neither
`K5LZ = 136` nor `F5_DBZ = 144`, and no `fp1`/`fp2` at all: those reach the dispatch only through
`RecognizeDeviceCodeFromProductName`, which derives a code from the product name — "APEX", "VADER"
or "DireWolf" plus a digit. `Fp3PNaruto = 97` is mapped by neither, and is put with `fp3` here on
its enum name.

**Four rows of that table were wrong until this was checked against the dispatch.** The three
Vader 3 Pro SKUs are `f3p`, not `f3`; and the whole Direwolf family collapsed onto `fp1`, so a
Direwolf 4 would have been refused under the name of a Direwolf 1. The table exists so a refusal
can say what it found, which makes a wrong name the one defect that defeats its purpose.

## Which pads speak `5a a5`, and how to tell without one in hand

`IsOldProtocol()` is `VendorId != 0x37D7`, which is a runtime test — so the question "would this
model be reachable on Linux" cannot be answered by reading a device's own record. It can be
answered from `ControllerHidManager`, which partitions on exactly that vendor id and names the
other side:

```csharp
CreateDeviceFromHid:
    ManufacturerString == "Microsoft" ? XInput
  : VendorId != 0x37D7               ? DInput
  :                                    NewXInput

FindSpecialHidDevice:
    if (VendorId == 0x37D7)
        return UsagePage == 0xFFA0 && pid >> 12 == 2 && pid >> 8 != 8;
    if (!ProductString.Contains("Direwolf 3") && !…("Direwolf 4")
        && !…("VADER3") && !…("VADER4") && !…("APEX 4") && !…("APEX4"))
        return false;
```

The second branch is the list of models Space Station reaches by **product string** because they
do not carry Flydigi's vendor id: **Direwolf 3, Direwolf 4, Vader 3, Vader 4, Apex 4**. Everything
else Flydigi drive is `0x37D7` and NewXInput. Apex 3 and Direwolf 1/2 are in neither branch, which
is why `GetDeviceCodeById` never returns `fp1` or `fp2` — Space Station cannot find those at all.
(`"VADER3"` is tested twice in that condition, a harmless slip.)

**A `DeviceType` in the high band does not mean the new protocol, and this nearly went in the
docs as though it did.** `FP4` is 132, above the Apex 5's 128, and the Direwolf 4 is still old —
its two model-specific code paths, the `DockSmartStopUsable` flag and the `Serial != 3` carve-out,
sit in the XInput and DInput classes and appear nowhere in the NewXInput one. The number band
tracks release order, not dialect.

So the reachable set is **Apex 5, Vader 5, Apex 6** and nothing else.

## What separates the models that are reachable

Straight out of `FlydigiControllerFactory`:

| | Apex 5 (`k5`) | Vader 5 (`f5`) | Apex 6 (`k6`) |
|---|---|---|---|
| Keys | 27 | 29 | 27 |
| `Serial` | 1 | 2 | 1 |
| `IsSupportForceTrigger` | ✅ | — | ✅ (commands 83–87, a different family) |
| `IsSupportScreen` | ✅ | — | — |
| `IsSupportLed` | ✅ | ✅ | — |
| `IsSupportTriggerVibration` | — | ✅ | — |

**`Serial` is the product line, not a protocol version** — 1 Apex, 2 Vader, 3 Direwolf — and in the
whole SDK it is read in three places, all the same `Serial != 3` test, deciding whether report rate,
joystick precision and joystick sensitivity come from the reply or read 0. A Direwolf carve-out and
nothing else, so it says nothing about an Apex 5 against a Vader 5.

**Four capability flags are consumed and three are inert.** `IsSupportForceTrigger`,
`IsSupportScreen`, `IsSupportLed` and `IsSupportTriggerVibration` are read across the service layer
and mapped into the protobuf the renderer receives (`ChargerDataMapper`'s controller twin renames
them `SupportAdaptTrigger`, `SupportLed`, `SupportScreen`). `IsSupportMotion`, `HasAdcChip` and
`IsSupportWheel` are set by the factory and read nowhere at all — every pad declares motion, so it
gates nothing and implies nothing about how gyro is driven.

Two consequences worth having before any per-model work:

  * **PS5 mode is gated on force triggers in Flydigi's own code** — `if (controller.IsSupportForceTrigger && EnablePs5)`. A pad without them has nothing for a virtual DualSense to translate, so DualSense mode is an Apex feature rather than a general one. Everything in [findings-games.md](findings-games.md)'s tier table is trigger-effect delivery and follows the same flag.
  * **`IsSupportTriggerVibration` changes a live command**, not just a page: `SetVibration` sends `VibrationType.Both` where it is set and `VibrationType.Grip` where it is not. A Vader 5's vibration addresses trigger motors as well as grips.

**Gyro does not distinguish anything.** It reaches a host two ways and neither is protocol-specific:
the profile blob's motion block at offset 137 maps it onto a stick pad-side, and
`EnableRawDataTransportIn` streams it live with a variant per dialect — NewXInput **17**, XInput
**80**, DInput **245**.

## Multiple pads

Nothing here drives a Vader 4 Pro, and after measuring what that would take, nothing will. The SDK
gives it 26 keys to the Apex 5's 27 over an
identical 20-key standard core; beyond that core only M1-M4 are common to both, the Vader adds C
and Z, the Apex 5 adds Turbo, M5 and M6.

The two are not the same pair renamed. On an Apex 5, M1-M4 are the back paddles and M5/M6 are a
shoulder pair at the top edge either side of the triggers, labelled **LM** and **RM**; on a Vader,
C and Z sit on the front face beside the ABXY block. A Vader 5 declares C, Z, M5 *and* M6
together, so the SDK treats them as six distinct extras rather than two namings of one pair.

**The factory list is the capability list. Space Station's k5 file is not.** That file
(`asar/.vite/renderer/main_window/assets/device_config_k5-*.js`) is a UI layout table — `id`,
`name`, `position`, `size`, `rotation` and a `clickable` flag — describing how to draw the
interactive controller picture, one absolutely-positioned element per entry. `clickable` says
whether that rectangle reacts to a click, nothing more. It is false for Fn (id 24, the SDK's
`Menu`, which switches profiles), Turbo (25) and Home (27), so clicking those three returns
`ControllerKey_None` and their picture offers no way to select them.

**An inert rectangle in their app is not the firmware refusing.** Measured on a wired Apex 5, both
directions:
`m1 -> home` made M1 fire the Guide button, and `home -> a` made Home send A with no Guide event
reaching the pad's evdev node at all. So `APEX5_KEYS` keeps `home` — the remap is worth having for
a pad whose Home button has failed, which is the one case their UI leaves no way out of. Fn and
Turbo are likely the same and are untried, but the argument does not carry across: Home takes a
press every time a Steam overlay is opened, while Fn and Turbo take a few dozen in half a year, so
neither is offered.

`JsLeft` (240) and `JsRight` (241) are in that same file and are `clickable`, but they are the
stick *bodies* — the rectangles over the two sticks, distinct from the `Thl`/`Thr` clicks at ids 14
and 15, which are ordinary remappable keys. Clicking one switches the panel to the joystick tab for
that side; right-clicking a stick click or a trigger jumps to its settings page.

What that tab configures — `JoystickMapType {Joystick=0, Keyboard=1, Mouse=2, DPad=3}` — never
reaches the pad, and is ruled out in [PROGRESS.md](../PROGRESS.md#ruled-out) along with the rest of
the keyboard and mouse mapping. Two details specific to sticks: `MappingConfigParser` hardcodes
`MapType = Joystick` when reading a blob, so a stick's binding cannot be recovered from one at all,
and the single consequence the pad does store is 127 in the centre byte, meaning "not acting as a
stick" ([findings-profile-blob.md](findings-profile-blob.md)).

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
vendor id, the product id's top nibble and the `06 a0 ff` report-descriptor prefix — which
together pick out *a Flydigi pad of the `5a a5` generation* and say nothing about which one, so
an Apex 5 config would otherwise go into a **Vader 5 or an Apex 6**. Not into anything older:
those carry no Flydigi vendor id in either transport and never reach this code at all. The
module is `DEVICE_TYPES` (the table above,
asserted by `tests/test_identity.py:50-60`), `PRODUCT_NAMES`, `code_for`, `name_for`,
`identify(ctrl)` (one command-1 read, raising `WrongDevice` when the pad does not answer at all),
`require(ctrl, *codes)` raising `WrongDevice`, and `SUPPORTED = ("k5",)`. Writes are gated; reads
deliberately are not, since asking an unknown pad what it is cannot damage it. Driving another
model deliberately means naming its code: `identity.require(ctrl, "f4")`. Three call sites gate —
`tools/flydigi-mapping:51`, `tools/flydigi-settings:136` and `gui/worker.py:91` — one check per
connection rather than per write; `flydigi-settings` gates its read-only `show` too, because
another pad's settings block printed under this one's field names would mislead.
`tools/flydigi-screen`, `tools/flydigi-haptics`, `tools/flydigid` and `tools/flydigi_cmd.py` do
not call `identity.require` at all.

The guard refuses; `flydigi/registry.py` chooses. That split is deliberate — "which device" and
"may this be written to" are different questions, and a tool may quite legitimately point itself
at a Vader.

### Choosing between devices

**What identifies a Flydigi device**, cheapest first, and every one of these is a valid selector:

| Name | How | Cost | Stable | Measured |
|---|---|---|---|---|
| node | `/dev/hidrawN` | free | **no** — moves on every reconnect | — |
| mac | command 1, raw 8..11, reversed | free, inside the heartbeat | yes | **reads all zeroes on this pad** |
| uid | command 4, 13 bytes at raw 6 | one exchange | yes | **yes, on the pad and the dock** |
| nickname | command 2 to read, 24 to write | one exchange | until renamed | read yes, **write never acked** |

The address was going to be the cheap answer, since it rides the same command-1 reply as the
battery that anything holding a pad already polls. It is not one. Measured on this pad, on its
dongle, firmware 7.0.4.5:

```
04 5a a5 01 01 00 80 02 | 00 00 00 00 | 05 45 01 00 70 45 21 31 45 25 ...
                          ^^^^^^^^^^^ DeviceMac
```

Every surrounding field decodes correctly there — device type 128, connect type 2, battery 5,
firmware 7.0.4.5 — so this is the field being empty rather than the offset being wrong. Whether a
cable fills it in is untested; the pad was on its dongle. `motion.parse_mac` returns None for
all-zero, following the same convention Flydigi already use for an all-zero firmware version, so
that nothing can key a config file on a value every pad shares.

Command 4 works and is the one to use:

```
04 5a a5 04 01 00 | 14 20 6e 7a 1c 00 00 00 00 dc ba 3e 00 | 00 00 ...
```

**The two SDKs disagree about where a nickname starts, and the pad settles it.**
`ReadNickNameControllerCommandNewXInput` slices `data.Slice(4, data.Length - 6)` — raw 5 with the
report-id byte kept — while `Flydigi.ChargerSdk`'s slices from its own data[6]. An unnamed pad
answered `04 5a a5 02 01 00 01 01 09 09 09 64 04 5e 00 ...`, and only the earlier slice reads that
as unset: raw 5 is 0x00, which is Flydigi's own test for an erased name, where raw 6 is 0x01 and
would have decoded as the first byte of a name that is not there. Each SDK is right about its own
device.

**Flydigi's nickname *write* is broken in both SDKs**, byte for byte the same code in
`Flydigi.ControllerSdk` and `Flydigi.ChargerSdk`:

```csharp
array[4] = (byte)(2 + bytes.Length);
Array.Copy(bytes, 0, array, 5, bytes.Length);
array[6] = array.Crc(3, 3 + array[4]);     // <- fixed index
```

The checksum belongs at `3 + array[4]`, which is 6 only for a one-byte name. For anything longer
their packet writes the checksum *into the name*, over its second byte, and leaves the real
checksum slot at zero — and a config-family packet with a bad checksum draws no reply from this
pad at all. So either command 24 is not checksummed, or Space Station's rename has never worked
past one character. `identity.nickname_packet` sends the packet the framing says is right and
takes `reference=True` for theirs; `tools/flydigi-devices name --reference` is the one run that
settles it.

**The dock's nickname read is one byte long, and this project shortens it.** Flydigi slice
`data[6 : 6 + data[3] - 3]`, and the measured framing says that runs onto the checksum: the unset
reply is `5a a5 02 04 01 00 07`, so a length byte of 4 covers a two-byte payload with the checksum
at `[2 + length]`. An *n*-byte name makes the length `4 + n` and puts the checksum at `[6 + n]`,
immediately after it — so their slice takes the name and then the checksum, which shows up as one
replacement character on the end of every name. Differing from the reference only where it is
demonstrably wrong is the call `motion.version_at_least` already makes.

### What the selection layer is

`flydigi/registry.py` enumerates both families into one list, probes each device with one exchange
(three with `deep`, which adds the uid and the nickname), and resolves a selector — node, uid or a
prefix of one, mac, or nickname — to exactly one device, **refusing an ambiguous one rather than
guessing**. A bare selector still takes the first device of its kind, because that is what every
caller that has only ever seen one device passes.

`registry.key()` writes the best available name in `uid:`/`mac:`/`path:` form, which is what goes
in a config file: `prefs.primary_pad` is the daemon's, written by the app's picker.

Every tool takes `--device` with the same meaning, from `registry.add_device_argument`, and
`tools/flydigi-devices list` prints all four names. `flydigi-charger --uid` still exists and now
accepts anything `--device` does. `list_docks` and `open_dock` are thin wrappers over the shared
layer, so pads and docks are chosen the same way.

**The daemon splits by tier, and the split is a property of the routes rather than a preference.**
The tier-1 vibration bind is command 82 and nothing else — a pad-side setting driven by the pad's
own rumble, with no host process in the loop once written — so `flydigid` writes it to *every*
attached pad that supports it, and two pads in a local co-op game both get adaptive triggers.
Every other route holds one pad for the length of a session: a driver rewriting trigger effects at
20 Hz, or a relay presenting one DualSense. Those act on the primary pad alone.

**A relay has to pin both of its halves to one device.** Two Apex 5s share a vendor id, a product
id and all three input-node names, so every filter `evdev.find_device` has matches both — a relay
that chose its vendor node by uid and its input node by name would read one pad's sticks while
writing the other pad's triggers. What links them is the USB device the two nodes hang off:
`evdev.usb_device_of_hidraw` walks up from the hidraw node to the last ancestor with no colon in
its name, and `find_device(usb_root=...)` accepts only input nodes under it.

### Mock devices

There is one pad and one dock on this desk, so every path above is exercised against
`flydigi/mock/`, behind `FLYDIGI_MOCK_BUS`. It hooks `device.find_nodes`, which is the one place
the tools, the daemon and the app all ask what is attached, and `Controller.__new__` hands back an
in-process fake for a `mock:` path. Nothing appears unless the variable is set, and everything
that does is marked `mock` everywhere a person can see it. The JSON form is re-read on every
enumeration, so flipping `present` is a device being unplugged.

### An older pad is one dialect away, and still not worth it

**`IsOldProtocol()` is `VendorId != 0x37D7`.** The Apex 5, Apex 6 and Vader 5 speak the `5a a5`
protocol in [PROTOCOL.md](../PROTOCOL.md); the Apex 3, Apex 4, Vader 3, Vader 4 and Direwolf speak
an older dialect of it — same blob, same parsers, different envelope and different command numbers.
Measured on a Vader 4 Pro, `DeviceType 85`, firmware 6.9.3.3, on its 2.4 GHz dongle:

  * **No HID interface, so nothing here can see it.** `045e:028e`, product string `Flydigi VADER4`,
    one configuration, one interface — class `ff` sub `5d` proto `01`, claimed by `xpad` — and two
    64-byte interrupt endpoints. `wTotalLength` is 49 and all 49 bytes are accounted for, so nothing
    hides behind an alternate setting. Without `06 a0 ff`, `find_device` cannot match it, and
    neither can Space Station: its transport is hidapi and nothing else (`HidApi.Hid.Enumerate`,
    `Read(64)`, `Write`). Windows reaches the pad anyway because `xusb22` synthesises the HID front
    end that makes Xbox pads visible to DirectInput, and Space Station talks to that — which is why
    configuring it there costs no XInput. `xpad` synthesises nothing. String descriptor `0xEE`
    STALLs, so this is not a pad hiding a second personality from Linux. **Nor is a cable the
    difference.** Every pad of this generation is an XInput device wired or wireless — measured
    both ways on this desk, `045e:028e` each time — which follows from `IsOldProtocol()` being
    `VendorId != 0x37D7`: an old pad does not carry the Flydigi vendor id in either mode, so
    `find_device` cannot match one however it is attached.

    **The kernel log looks like it says otherwise, and it does not.** The 2.4 GHz dongle
    enumerates on its own before the pad attaches through it: `04b4:2412` — Cypress, product
    string `Flydigi VADER4`, `SerialNumber=0` — carrying four HID interfaces that Linux binds to
    hidraw nodes, one of them a non-input collection. It then disconnects and the composite comes
    back as `045e:028e` with a serial. So those hidraw nodes belong to a dongle with no pad
    behind it yet, and they last seconds: nine once, under one twice. Anyone grepping a boot log
    for "VADER" will find them and conclude an old pad is reachable over hidraw. It is not — the
    window closes before anything can use it, and what replaces it has no vendor collection at
    all.
    `FindSpecialHidDevice`'s `0xFFA0` collection at interface 1 or 2 is Space Station matching the
    front end `xusb22` synthesises on Windows, not a node Linux ever sees. What `identity.py`
    guards against is therefore a **Vader 5 or an Apex 6** — the other `5a a5` pads, which do
    carry `37d7` and would open exactly like an Apex 5 — and not anything older.
  * **The commands answer on the xpad endpoints.** usbfs, `USBDEVFS_DISCONNECT` to take the
    interface from `xpad`, then a claim on interface 0 — no root, since the uaccess ACL on
    `/dev/bus/usb/*` is enough. A frame is 15 bytes, `[0]=0xA5`, `[1]=cmdId`, `[2]=sub`, no
    checksum, written to endpoint `0x05`. Replies come back on `0x81` **inside the 32-byte gamepad
    input report, from offset 14**, which is why the SDK reads acks at `data[15]`. Identify is
    command **16** — `DeviceType` at `data[16]`, uid 17..20, firmware 21/22, battery 23, connect
    type 25 (1 wired, else dongle), active slot 27 — and the config version read is **80 sub 4**
    with the ids at 17..24. Both answered twice. The Apex's `5a a5` envelope and the DInput framing
    went out in the same run and drew nothing, so the pad answers one dialect rather than echoing
    whatever it is sent.
  * **The blob is this pad's blob, ten bytes to a packet instead of twenty.** Read is command **33**,
    ack **34**, packet index at `data[16]`, payload 17..26. The packet count comes from the first
    packet: `data[18] == 3` with `data[17]` of 0 or 1 means 79 or 84 packets, so 790 or 840 bytes.
    No parser in the SDK branches on device code, type or serial, so the layout, key ids, stick bank
    and macro pages carry over, and `blobs.py` already takes a `pkg_size`. Writes over this dialect
    were not tried and are unverified.

**What rules it out is the pad, not the work.** The dialect is transcription rather than discovery:
of the SDK's 81 command files, 61 carry a legacy class — 58 an XInput one, 58 a DInput one — against
49 carrying a NewXInput one, and 36 carry both a NewXInput and an XInput. So most of the old dialect
is already written out beside the new one, and 24 legacy files have no new twin at all. But a Vader 4 has neither adaptive triggers nor a screen, so the two features that
earn this pad an app do not exist on that one, and what is left is configured once and forgotten.
Reaching it also means taking interface 0 from `xpad` for every read and write, so it stops being a
gamepad for as long as the app holds it. Serving its input from a virtual pad in the meantime is
exactly what tiers 4 and 4b do here, and is explicitly not wanted.

**Only an Apex 5 and a Vader 4 Pro are available to test with**, so everything above about the Vader
is measured and everything said here about any other model is read out of the SDK.

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

## The CD2 charging dock

**Driven, and measured.** `flydigi/charger.py` and `tools/flydigi-charger`. The SDK came from
`~/.dotnet/tools/ilspycmd -o decompiled/Flydigi.ChargerSdk bundle/Flydigi.ChargerSdk.dll` in the
`wine-arch` distrobox; everything below marked *measured* came off the dock on this desk,
firmware **0.0.3.9**, charger type 0, uid `1960f0f1f2cdab52efe7bc0658`.

The Vader 4's older dock is a different device. It has never been on this bus, nothing here
describes it, and it must not be assumed to speak the `cd2` protocol —
see [Telling a CD2 from something else](#telling-a-cd2-from-something-else).

### On the bus

`37d7:6001`, one interface, HID, two 64-byte interrupt endpoints, a 34-byte report descriptor
declaring one 64-byte input and one 64-byte output report under usage page `0xffa0` and **no
report ids at all**. So the leading byte of a write is `0x00` where the pad's is `0x03`, and a
reply carries no report-id byte.

**It is not the pad, and matching on the vendor id alone said it was.** Vendor `37d7` and a
descriptor beginning `06 a0 ff` describe both devices, so `find_device` returned the dock
whenever the dock's node sorted first — reproduced here with the pad asleep, which is not an
exotic state since the pad leaves the USB bus when it sleeps. The fix is Flydigi's own rule:
the product id's top nibble is the device family — `2` controller, `6` charger, `1` cooler — and
each of Flydigi's three `HidManager`s tests it, though only the charger's tests it alone:
`ChargerHidManager` is vendor plus `pid >> 12 == 6` and nothing else, `ControllerHidManager` adds
a usage-page test and a `pid >> 8 != 8` clause that the nibble already makes unreachable, and
`CoolerHidManager` masks the top *byte*, `pid & 0xff00 == 0x1000`. `flydigi/device.py:FAMILY_*`.

Measured, and the reason this was confusion rather than corruption: **the dock ignores
pad-framed packets.** A heartbeat sent with report id `0x03` instead of `0x00` shifts the magic
by one byte and drew no reply, twice, at two packet widths, with a correctly-framed control
either side that answered every time.

### Framing

Same `5a a5` envelope as the pad, with one asymmetry that is easy to get backwards:

```
request   [0] 0x00  [1] 0x5A  [2] 0xA5  [3] cmd  [4] len  [5..] payload
          checksum at [3 + len], the 8-bit sum over [3, 3 + len)
reply     [0] 0x5A  [1] 0xA5  [2] cmd  [3] len  [4..] payload
          checksum at [2 + len], over [2, 2 + len)
```

`len` counts bytes `[3]` and `[4]` themselves, so it is `2 + len(payload)`. The reply checksum
position was verified against every read this project makes — heartbeat, uid, nickname, LED
config and the unsolicited status report — and predicted the byte the dock actually sent in all
five cases. The one exception seen is the command-97 ack, which puts it at `[3 + len]`. Flydigi
never check a reply checksum anywhere (`ParseAckData` matches on the command byte alone), so
neither does this; a write is confirmed by reading it back.

Measured: **a short output report is accepted.** 32-, 64- and 65-byte writes of the same
heartbeat drew identical replies, so Linux hidraw not zero-padding the way hidapi-on-Windows
does costs nothing.

Commands: heartbeat **1**, read nickname **2**, read uid **4**, sleep-when-charging **17**,
lighting sync **18**, close-with-system **19**, read LED config **20**, RGB write start/pack
**22**/**23**, write nickname **24**, power display **25**, LED write start/pack **97**/**98**,
reset mapping **175**, firmware upgrade mode **224**, write device type **254**. The last three
are transcribed and deliberately unwrapped: Space Station's own "restore defaults" does not send
175 (it re-uploads the shipped default file), and 224 and 254 are one-way trips on a device with
no documented recovery.

Flydigi's own `UpdateNicknameCommand` writes its checksum at a fixed index `[6]` rather than
`[3 + len]`, so it corrupts any nickname longer than one character. Nothing here writes a
nickname; worth remembering if anything ever does.

### The dock plays frames; it does not generate effects

This is the whole shape of the device, and it is the same architecture as
[the pad's lighting](../flydigi/lighting.py): the host computes every frame and uploads them,
and the mode byte only records which effect produced the data.

Measured, in two halves:

  * A config with the header for `Breath` — correct mode, colour, brightness and interval, all
    of which read back correctly — and **`frameCount: 0`** did not breathe. The dock kept
    playing its previous animation's leftovers: fragments of the old effect, then a travelling
    band of wrong colours, then flat white. The band is the giveaway. Frame memory is not
    cleared by a config write, so with no valid frame count the dock walks those bytes at
    offsets that are not multiples of three, rotating every RGB triple into its neighbour's
    channels. **The frame count in the header must match the frames actually sent.**
  * The same mode with its 40 computed frames played correctly, and a `Pulse` written with its
    50 frames was indistinguishable from the animation the dock had before anything here touched
    it. Side by side against Space Station's own `Breath`, at identical parameters, there was no
    visible difference; changing only the colour changed only the colour, which is what rules out
    the upload having silently done nothing.

**162 LEDs** in a wedge — 16 rows of 14, 15, 16, 15, 14, 13 … down to 3.

The write is `ConfigParser.ParseFrameLedConfigToArray` and its field order is **not** the read's:

```
write (97/98)   frameCount, period, brightness, mode, direction, useColorCount,
                then palette RGB triples, then every frame's 162 RGB triples
read  (20)      mode, brightness, period, direction, useColorCount, then palette
```

A read never returns frames, so what the dock is playing cannot be recovered from it — only the
header. `useColorCount` is a field of its own rather than the palette's length, and Flydigi's own
preset table has the two disagreeing in both directions, which is itself evidence the dock does
not use the byte to locate the frame data.

One deliberate divergence. Flydigi advertise `len // 50 + 1` packs while sending
`ceil(len / 50)`; the two agree unless the blob divides by 50 exactly, and there they promise the
dock a pack they never send. This sends the true count. Their arithmetic is reachable — a custom
animation of four frames is 1950 bytes, 39 packs against an advertised 40.

Every pack waits for its own ack. There is no inter-packet delay anywhere in Flydigi's stack and
no blind streaming either. A fifty-frame effect is 24,306 bytes, 487 packets, about five seconds.

### The effects, and where their geometry comes from

Ported from Space Station's own generators in `useLedEffectRenderer`, faithfully enough that two
of Flydigi's oddities are reproduced rather than corrected: the HSL conversion uses
`q = l + s - l*s` unconditionally where the usual formula branches below mid-lightness (every
call site passes lightness 0.5, where they agree), and the breath stepper takes the frame
interval and ignores it, so a breath's step size does not depend on its speed.

`ChargerLedType` has ten members — Close 0, Solid 1, Default 2, Custom 3, DiagonalFlow 4,
Breath 5, Gradient 6, WaveGradient 7, Rainbow 8, Pulse 9. Space Station's dropdown offers nine,
omitting Solid. Eight are computable here; `Default` is not, and `Custom` takes frames from the
caller. Frame counts: 50 for gradient, rainbow, wave-gradient, diagonal-flow and pulse; 1 for
close and solid; **2N for breath**, where N depends on the colour, because each step scales what
is left by `1 - step/50` and a darker colour reaches black sooner.

Direction is read by **rainbow and wave-gradient only**, and they do not even divide the same
way: rainbow uses row ÷ row-count and column ÷ row-length, wave-gradient uses the same two minus
one.

**Pulse and diagonal-flow do not use the LED positions the image sampler uses.** They build their
own lattice around whichever preview circle sits nearest the middle of a 450x420 box — that is
`point_115`, index 114, at 47.56% / 50.48%, i.e. (214.02, 212.016) — with a horizontal pitch of
width/20 and a vertical pitch of that times √3/2. Transcribed rather than reasoned about.

**`Default` cannot be computed and is refused rather than approximated.** Space Station does not
compute it either: `read_default_config` reads
`Configs/Charger/cd2/default/default_mapping_<deviceType>.dat`, a serialised
`ChargerMappingConfigBean` its installer ships, and uploads those frames. Confirmed from the
other side — applying `Default` in Space Station puts the Flydigi logo animation on the dock. The
file is not in this repository and `tools/fetch-configs` does not fetch it. Copying one off a
Windows install is all it would take: the field numbers are `ChargerMappingConfigBean
{cfgId 1, title 2, currentLedConfig 7, ledConfigOptions 8, advancedLedConfig 9}`,
`ChargerLedConfig {mode 1, period 2, brightness 3, color[] 4, useColorCount 5, direction 6,
frames[] 7}`, `FramedLedColor {brightness 1, colors[] 2}`, `Color {red 1, green 2, blue 3}`.

### The images path, which is not built here

The DIY page accepts png/jpeg/gif, crops on a 334x304 canvas and samples **one pixel per LED**
— nearest pixel, no averaging, no gamma, alpha discarded — into a `FramedLedColor` per GIF
frame, capped at 200 frames, then sends an ordinary `UpdateConfig`. `period` there is the frame
interval in centiseconds (`Math.round(frameInterval / 10)`); the preview animator disagrees with
itself and plays at `20 * period` ms.

It uses **no `SwitchUsb`, no firmware console and no command 31** — the ordinary config path.
That decoding cannot live in a zero-dependency backend, which is why it is scoped to `gui/` where
Qt already reads both formats. → [PROGRESS.md](../PROGRESS.md#whats-next)

### The four switches, and what the dock says on its own

`cd2_sleep_when_charging` "Intelligent start" (17), `cd2_led_sync` "Lighting Sync" (18),
`cd2_close_with_system` "Close When Shutdown" (19), `cd2_show_animation_when_charging`
"Power Display" (25). Space Station's UI makes the first and last mutually exclusive, forcing one
off when the other goes on; nothing in the SDK enforces it, so nothing here does either.

Lighting sync is one enable byte and no host-side traffic moves lighting between the two devices,
so the pad and dock arrange it between themselves. Measured with sync on: the dock's stored
colour and brightness did not match the pad's, so whatever it syncs is not the stored config.

**The dock pushes an unsolicited report `239` about once a second** — the only thing it sends
without being asked. `ChargerProtocol.ParseData` singles it out and hands it to a raw-data
listener rather than treating it as an ack. `data[7]` is whether a controller is docked and
`data[8]` its battery. Measured with nothing in the dock: `data[7]` reads 0, which fits. The
battery byte has not been seen with a pad actually seated.

### The dock's switches interact

**"Intelligent start" turns the lighting off on both devices.** Observed on the hardware here:
with `sleep_when_charging` on, docking a pad takes the lighting down on the pad *and* on the dock
for as long as it sits there. That makes it exclusive in practice with the other two lighting
switches — `led_sync` has nothing to keep in step and `show_animation_when_charging` has nothing
to play, during exactly the window either of them is for.

Space Station forces `sleep_when_charging` and `show_animation_when_charging` apart in its own UI,
forcing one off whenever the other goes on. That was written up here as a house style, on the
grounds that nothing in the SDK enforces it. It is not: their UI is enforcing something the
firmware does. Nothing in `flydigi/charger.py` enforces it either — a library that quietly turned
off a switch its caller did not mention would be worse than one that sets both and says what
happens — so the app shows which one wins and the CLI says so in its help. The app also calls it
"Sleep while docked", since Flydigi's own label describes none of this.

**The dock's battery byte is a controller's charge on the controller's scale.** `charger.
describe_battery` reads it as 0..5 with 6 meaning charging, which is how command 1's battery
nibble is read for the pad itself. It has never been seen with a pad actually seated, so the scale
is inferred — but a bare number would repeat the bug that reported a full pad as five-eighths, and
worse: a docked pad is charging, so the value it is most likely to carry is the one that renders
as "battery 6".

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

### Telling a CD2 from something else

`FlydigiChargerUtil.GetDeviceCodeById` returns the literal string `"cd2"` for any argument
whatsoever, and the `Charger` constructor hardcodes the same — so Flydigi's SDK drives anything
in the `0x6xxx` family as a CD2 without ever asking. `charger.require` asks. Two layers:

  * **It must answer `5a a5` at all.** All sixteen charger commands are built
    `isNewProtocol: true` and, unlike the controller SDK — which carries a legacy twin for 62 of
    its 80 commands — the charger SDK has no legacy dialect anywhere. A dock old enough to
    predate `5a a5` has nothing to answer with.
  * **Its charger type must be a known CD2.** `ChargerDeviceType` is CD2 0, CD2_EVA 1, CD2_SRS 2,
    CD2_GS 3, CD2_MHY_HK 4 — five editions of one model, differing in artwork rather than
    protocol. An unknown type is refused by name.

The product string is deliberately **not** a gate. `HID_NAME` does name the generation — this
dock reports `flydigi Flydigi CD2` — but whether the four collaboration editions carry the same
string is unmeasured, and refusing a real CD2 over its artwork would be worse than the case being
guarded against. It goes in the refusal message instead.

`cfgId` is host bookkeeping and never reaches the wire: no charger command carries an index, and
the dock stores exactly one LED config. Two docks are told apart by uid — `flydigi-charger
--device`, the app's picker, or `registry.find` — and only one has ever been on this bus, so every
multi-dock path is covered by `flydigi/mock/dock.py` and by nothing else.

### The pad's own dock setting, and the cooler

The pad carries one dock-related setting: `EnableDockSmartStop`, command **80** with sub-id
`[2]=16` and `[3]=enable`, in the legacy envelope rather than `5a a5`. There is no NewXInput
builder: the XInput and DInput classes are byte-identical, and a NewXInput controller is handed
the DInput one, so a request for this setting reaches any pad as a legacy packet. Space Station
never makes that request for a `k5` — `ReadHardwareFunctionStatus` sets `DockSmartStopUsable` for
`fp4` alone, and its NewXInput branch never sets the flag. Nothing here sends 80 sub 16, and what
an Apex 5 does with one is unverified.

`Category` is Unknown, Controller, Cooler, Keyboard, Mouse, Headset, Charger, and `bundle/` ships
`Flydigi.CoolerSdk.dll` and `Flydigi.KeyboardSdk.dll` alongside the charger's. There is no keyboard
repository; `CoolerRepository` exposes fan mode and speed, an accelerate level, smart mode, gear and
RGB lighting, a speed curve, auto-start and intelligent start/stop. No cooler is available to test
with, so none is driven.

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
