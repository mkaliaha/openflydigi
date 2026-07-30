# Sharing the pad with Steam and SDL

Four things, three of which turn on the same fact: the vendor hidraw node has more than one
writer. Our own arbitration, the pad-side toggle that hands Steam the device, and why
filling SDL's Flydigi trigger stub upstream was ruled out. The fourth is the odd one — why
that toggle being on is not enough to make Steam call the pad an Apex 5, and why disabling
and re-enabling it is a real fix rather than folklore.

Index: [PROGRESS.md](../PROGRESS.md).

## Steam Input contention — theirs, and not fixable from here

Steam and SDL claim the vendor hidraw node too and send their own acquire/heartbeat (`0x1C`, our
command 28, on a 30-second timer). This is **distinct from our own two-writer problem** below: a
lock fixes ours and cannot fix theirs, since Steam will not take one. The options are to disable
Steam Input for the pad or to tolerate it, and tolerating it is fine — see the trade table further
down, where everything this project drives over the vendor interface keeps working.

## Two writers on one hidraw node — done

**Was the one known-broken thing, and it was seen rather than predicted.** Nothing coordinated
access to `/dev/hidraw4`. The desktop app holds it open and polls `Get info` every 30
seconds; a memory-driven route (`flydigi-monitor`, `flydigi-forza`, `flydigi-dsx`) rewrites trigger
effects as often as every 50 ms; both write whole 32-byte packets and then read.

Two distinct failure modes, and only the first is obvious:

1. **Interleaved writes.** Two packets can reach the pad in either order. For trigger effects that
   is self-correcting — the next frame overwrites it — but a config write (164/165 streams a blob in
   packets, 166 commits it) interleaved with anything else is not.
2. **Misattributed replies.** Replies are *broadcast to every reader of the node*, so a process
   receives ACKs for commands it never sent. Caught live while testing the new effects: a
   `Get info` ACK belonging to the app's poll landed in `flydigi_cmd`'s read, which had sent a
   rumble command:

   ```
   TX 03 5a a5 12 06 00 00 …          ← our rumble stop
   RX 04 5a a5 01 01 00 80 01 …       ← command 0x01, nobody here asked
   ```

   `Controller.send()` collects every reply for 300 ms and `ack_ok()` matches on the command byte,
   so an overlapping exchange can hand the wrong answer to the wrong caller. Today that means a
   command reported as failed when it worked, or as succeeded when its own reply never came.

**The fix is `Controller.claim()`, an advisory `flock(2)` on the open node.** `send` takes it for a
single packet; `blobs.read_blob` and `blobs.write_blob` hold it across a whole packet stream, and
the app's write-then-save holds it across both, since the save command commits whatever is in the
pad's working memory and would otherwise commit someone else's write too. It is re-entrant, so a
claimed sequence can send freely. `_drain()` runs inside the claim before each write and throws away
anything already waiting: under the claim, a reply that arrived before we asked provably belongs to
an exchange that is over.

**Advisory is the right kind of lock here, not a compromise.** It binds only processes that ask,
which covers ours completely — everything in this project goes through `flydigi/device.py`, and
`tools/flydigi_cmd.py` takes the same lock by hand. Steam and SDL hold the same node open, will not
take it, and **must not be shut out**: the vendor interface keeps working with Steam Input on, which
is what lets trigger effects run in games Steam has taken the pad for (see the third-party toggle
section — commands 81 and 82 are felt with `controller_data = False`). A lock that excluded Steam
would break a working configuration to fix nothing. What remains is that Steam's writes can land
between ours: harmless for effects, which the next frame overwrites, and a risk only for a config
write, which is a deliberate action rather than something a game triggers.

**Verified on hardware**, with Steam and steamwebhelper holding the node open throughout and
unaffected:

```
node free:      0.44s
held for 1.0s:  1.36s   ← the waiter is granted the moment the holder lets go
held for 2.0s:  2.36s
claim(timeout=0.3) while held -> DeviceBusy: another process has held /dev/hidraw4 for more than 0.3s
```

`DeviceBusy` is in the worker's retry tuple, so a busy pad reads as a transient in the UI rather
than as a crash. Threads sharing one Controller get an in-process `RLock` as well, because `flock`
attaches to the open file description: two threads on one handle would both be granted it.

## "Allow third-party apps to take over mappings"

**Done, and it is what lets Steam recognise the pad — necessary, but on its own not sufficient.**
Command 16 reads it, command 17 writes it, and the switch is on the Controller page
behind the firmware gate. A pad-side setting, not Steam's. Whether Steam then actually *names* the
pad depends on a second thing entirely — see "Recognition depends on which identity the pad lands
on" below. Space Station's own words:

> When the switch is turned on and a third-party application (such as Steam, reWASD, etc.) is
> opened, the controller mapping will be taken over, and all Space Station settings will be invalid
> at this time.

**Verified on hardware, and it does more than the wording suggests.** Space Station describes this
as "third-party apps take over the mapping", which reads like a conflict-resolution setting. What it
actually gates is whether the pad will **hand itself to another driver at all**. Reading command 16
before and after flipping it:

```
before   third_party=False  control_by=''      Steam shows "generic XInput controller"
after    third_party=True   control_by='SDL'   Steam shows "Apex 5 connected"
```

Three things fall out of that run:

  * **SDL claims the pad the instant it is allowed to.** `control_by` is the same 20-byte ASCII tag
    the cooperative-lock command carries, and it filled in with `SDL` by itself. Steam Input has a
    native Flydigi driver; this flag is what stands between it and the pad.
  * **SDL then reconfigures the transport on its own.** `controller_data` went True→False and
    `raw_data` False→True, and *we did not ask for that* — both were sent as 0xFF, "leave alone". So
    the new holder switched the pad into raw-report mode, which is what its own driver reads.
  * **Nothing re-enumerates.** Same bus address, same evdev names, same VID/PID. So Steam's native
    recognition comes from the acquire, not from any change of USB identity — nothing to do with
    descriptors or double-remapping.

**Our switch does exactly what Space Station's does, byte for byte.** Checked call site by call
site:

  * Space Station's toggle reaches `ControllerRepository.EnableThirdPartyAppControl`
    (`ControllerRepository.cs:1533`), which calls
    `EnableRawDataInput(controller, null, null, null, null, enable, ...)` — command 17, `[4]=7`,
    `[5..8]=0xFF`, `[9]`=1/0. `gui/worker.py:149` sends the same bytes with the same checksum range.
  * The whole service has **three** `EnableRawDataInput` call sites (`ControllerRepository.cs:1490`,
    `1542`, `1563`) and none of them ever writes the keyboard or mouse flags — both go as `null`,
    which the factory turns into `0xFF`, "leave alone".
  * The Electron UI fires exactly one IPC command for the switch
    (`case "controlByThirdPartyAppEnabled": IpcCommandEnum_EnableControlByThirdPartyApp`). No second
    call.
  * `KeyboardMouseInjectRunner` is host-side Windows `SendInput` simulation for keyboard/mouse
    *mappings*. It sends nothing to the pad, and `ControllerBusinessService.cs:76` gates it **off**
    while third-party control is active.

**The keyboard/mouse composite is a separate, persistent pad mode.** Observed here with the pad
presenting `if01-event-kbd`, `if01-event-mouse` and `if02-hidraw` while command 16 read
`keyboard: False, mouse: False, third_party: False`. So the extra HID nodes coexist with the flag
being off and with the transport flags for keyboard and mouse being off. Whatever puts the pad into
that composite, it is not command 17 and not this switch.

**What Space Station does that we do not, in full:**

  * a read-only 30-second poll after the ACK (`StartThirdPartyMonitor`,
    `ControllerRepository.cs:147`) that refreshes its own UI via command 16 and writes nothing;
  * on Windows only, `DevconHelper.ExecuteDevconCommand` shells out to
    `devcon.exe enable|disable "USB\VID_37D7&PID_2501&MI_00"` — interface 0, the XInput interface —
    on every device connect and around DS mode. There is no Linux counterpart; the nearest
    equivalent is unbinding xpad, which we deliberately do not do.

Neither re-asserts the flag. **Space Station's connect handler only reads it** — `OnDeviceUpdateImpl`
(`ControllerRepository.cs:92`) calls `StartThirdPartyMonitor` and nothing else third-party related,
and the config-apply path (`PrepareMappingConfigs`) deals only with mapping blobs.

**Steam then lists the pad twice, and that is not our bug** — when the flag is toggled on while
Steam is already running. A reconnect with the flag already on produces **one** entry, the HIDAPI
one; see the identity section below for why. Reported on Windows as well as here.
Both paths are legitimately supported and Steam does not merge them:

```
xpad on 3-4:1.0  -> event-joystick -> "generic XInput controller"
Steam hidapi     -> hidraw4        -> "Apex 5"
```

`steamwebhelper` holds both hidraw nodes open while this is on. Nothing sent to the pad changes it;
the toggle only makes the second path exist.

**The duplicate is deliberate, and upstream SDL says why.** `HIDAPI_IsDevicePresent`
(`src/joystick/hidapi/SDL_hidapijoystick.c`) is what the Linux evdev backend calls, via
`SDL_JoystickHandledByAnotherDriver`, to decide whether a raw joystick duplicates a HIDAPI-handled
one. Every other claimed device gets its evdev twin suppressed. The Flydigi V2 is skipped on purpose:

```c
/* The HIDAPI functionality will be available when the FlyDigi Space Station app has
   enabled third party controller mapping, so the driver needs to be active to watch
   for that change. Since this is dynamic and we don't have a way to re-trigger device
   changes when that happens, we'll pretend the driver isn't available so the XInput
   interface will always show up (but won't have any input when the controller is in
   enhanced mode) */
if (device->vendor_id == USB_VENDOR_FLYDIGI_V2 && device->driver == &SDL_HIDAPI_DriverFlydigi) {
    continue;
}
```

Three things follow from that comment. The double listing is designed. The dead XInput entry is a
known, accepted cost, and is exactly the `controller_data = False` behaviour measured below. And
**SDL states outright that it cannot react to the flag changing** — which is the whole of the next
section.

**Mostly cosmetic, though.** Enabling Steam Input for the pad makes Steam grab the physical device
and hand the game a single virtual controller, so the duplicate is visible in Steam's settings list
and not to anything launched through Steam. It matters for games started outside Steam, and it
matters if Steam Input is off — which is exactly the state Tier 4 requires.

If it does need removing, unbinding xpad is the local fix
(`echo -n "3-4:1.0" | sudo tee /sys/bus/usb/drivers/xpad/unbind`), and it does not survive a wake
since re-enumeration rebinds. **Do not make it permanent with a udev rule**: the evdev node is where
everything else here reads sticks and buttons — `tools/flydigi-ds5` relays them into the virtual
DualSense, and `joystick-curve-probe` and `stick-feel` both depend on it.

## Recognition depends on *which identity* the pad lands on, not just on the flag

**The weird one, and it is reproducible.** The flag being on is necessary but not sufficient. Whether
Steam shows "Apex 5" depends on which of two synthetic identities the HIDAPI device gets, and that is
decided by a race at enumeration.

**The pad reports no serial number, so Steam invents one.** Evidence throughout this section is
Steam's own controller log —
`~/.var/app/com.valvesoftware.Steam/.local/share/Steam/logs/controller.txt` on this Flatpak install,
`~/.steam/steam/logs/` on a native one, with the display side in `controller_ui.txt` beside it:

```
Controller has an Invalid or missing unit serial number, setting to '37d7-2501-79acb62'
```

The base `37d7-2501-79acb62` is stable. When it is already taken by another controller at that
moment, Steam appends a suffix and mints `37d7-2501-79acb62g` instead — a *separate* identity with a
separate `configset_37d7-2501-79acb62<suffix>.vdf`. Whoever registers first gets the base.

**Two orderings, and they give different results.** Observed live, with the two representations
distinguishable in the log by mapping GUID — `...086804` with `paddle1-4`/`misc2`/`misc3` and a
`Controller using HIDAPI driver` line is the native path; `...080000` named `Flydigi Apex 5` with no
driver line is xpad:

```
toggle off -> on, Steam already running
  01:24:30  flag off   index 0  GUID ...080000  (xpad)    -> 37d7-2501-79acb62
  01:24:37  flag on    index 1  GUID ...086804  (HIDAPI)  -> 37d7-2501-79acb62g   <- shown as Apex 5
  two entries

reconnect with the flag already on
  01:08:07             index 0  GUID ...086804  (HIDAPI)  -> 37d7-2501-79acb62
  01:15:52             index 0  GUID ...086804  (HIDAPI)  -> 37d7-2501-79acb62
  one entry
```

| | entries | holds `...79acb62` | holds `...62g` |
|---|---|---|---|
| flag off | 1 | xpad — "generic XInput" | — |
| toggled on while Steam runs | 2 | xpad | **HIDAPI — "Apex 5"** |
| reconnect, flag already on | 1 | **HIDAPI** | — |

**So a reconnect leaves the native driver wearing the generic entry's clothes.** The HIDAPI device
registers first, takes the base identity, and inherits `configset_37d7-2501-79acb62.vdf` — the config
set that belongs to the xpad representation.

**Nothing is actually broken when this happens, and that is the important part.** The identity is
wrong; the driver is not. It is the native Flydigi HIDAPI driver in every case — `Controller using
HIDAPI driver, vid=0x37d7, pid=0x2501` in `controller.txt`, `Type: 30` in `controller_ui.txt`, mapping
GUID `...086804` with the paddles and `misc2`/`misc3` that only the native path exposes. Verified on
hardware while in this state:

  * **the full button set binds**, paddles included — the whole Steam Input binding UI works;
  * **the pad reads as acquired**: command 16 returns `third_party=True`, `control_by='SDL'`;
  * **SDL is live on it**, sending its 30-second acquire heartbeat — command 28 and command 1 ACKs
    seen on the vendor stream;
  * **everything this project drives over the vendor interface keeps working** — adaptive triggers,
    profiles, lighting, curves, the motion stream. Same as the trade table below.

What is lost is cosmetic plus one real consequence: the entry is not *labelled* Apex 5, and because
it inherits the other identity's config set, Steam Input bindings saved under the `...62g` identity
are not the ones applied. Re-toggling restores both. So this is a naming and config-set problem, not
a functionality one — worth knowing before anyone goes hunting for a fault that is not there.

**This is what the disable/re-enable ritual is actually for.** Reported first on Windows and
reproduced here: toggling off and on reverses the order, letting xpad claim the base identity so the
HIDAPI device is pushed onto `...62g` and its own config set. It is not superstition and it is not
platform-specific — it is the only way to force the second ordering. It also lines up with SDL's own
admission above that it cannot re-trigger device changes when the flag moves: a command-17 ACK
crossing the wire is the only thing that makes `HandleStatusUpdate` re-query status.

**Space Station does not solve this either.** Its connect handler only polls, so on Windows the same
reconnect produces the same wrong identity, which is why the ritual was needed there in the first
place. An option we have and it does not: re-assert the flag on connect — read command 16, and if it
is already on, send off then on after SDL has enumerated. Not implemented, and it would be a
workaround for Steam's identity assignment rather than a fix. The real fixes are upstream: a stable
serial for the device, or Steam not reusing one config set across both representations.

**Loose end.** Across the whole log a HIDAPI block and an xpad block for `37d7/2501` never appear at
the same timestamp — they alternate — which sits awkwardly with a carve-out whose stated purpose is
to make the XInput interface *always* show up. The evdev node itself is definitely still there
(`usb-Flydigi_Flydigi_APEX5-event-joystick`, xpad bound to `3-4:1.0`, and Steam holding
`/dev/input/event16` open). Not explained.

**Tested, and it is a clean trade rather than a catch.** With the flag on:

| | third-party off | third-party on |
|---|---|---|
| Steam's view | generic XInput controller | **Apex 5** |
| standard gamepad path (xpad / evdev) | works | **dead** — where the XInput entry exists at all, it accepts no input in Steam; after a reconnect there is no second entry |
| adaptive triggers over the vendor interface | works | **works** — commands 81 and 82 ACK *and are felt* |
| profiles, lighting, curves (config commands) | work | work |

So `controller_data = False` really does silence the ordinary controller report, confirmed by hand.
What survives is everything this project drives over the vendor interface — which is tiers 1, 2, 3
and 5, all of them. The trigger effects were verified by feel, not by ACK: command 245 already
taught us that this pad ACKs commands it then ignores.

**What it costs is exactly Tier 4 and our own evdev tools.** `tools/flydigi-ds5` relays sticks and
buttons from evdev into the virtual DualSense, and `joystick-curve-probe` and `stick-feel` read the
same node — none of them work while the flag is on. Tier 4 needed Steam Input off anyway, so the two
were already mutually exclusive in practice; this makes it explicit.

**Recorded because it was measured, not because anything should be built on it.** In the exact
state above — third-party on, `controller_data` off — the vendor operator-data stream was still
delivering **3870 reports in 4 seconds** (~970 Hz), decoding to gyro ≈ 0 at rest and accel Z ≈ 4096,
the 1 g already verified. So the vendor stream stays alive precisely when evdev dies.

Offsets from `OperatorDataParser`, NewXInput branch, **+1 for the report-id byte we keep**:

```
raw  4,5    left stick X    little-endian 16-bit, subtract 65535 if over 32767
raw  6,7    left stick Y    same, then negate
raw  8,9    right stick X
raw 10,11   right stick Y   negate
raw 16      left trigger    linear, one byte
raw 17      right trigger   linear, one byte
raw 18..29  gyro and accel  already implemented and hardware-verified
```

Their `data[17]`/`data[23]` land on our proven `GYRO_OFFSET`/`ACCEL_OFFSET`, so the +1 shift is
established and the stick offsets inherit that confidence. Buttons are in the same report, offset
not yet located.

**There is no plan to use this, and the obvious reason to want it does not hold.** Reading sticks
from here instead of from evdev would stop Tier 4 conflicting with the third-party toggle — except
Tier 4 requires Steam Input *off* and the toggle exists to let Steam take over, so they conflict a
level above the input source and cannot be used together anyway.
M1-M4 do not justify it either: the pad already remaps them onto real XInput buttons onboard, with
nothing running. The only thing left that onboard remapping cannot do is a pad button the game never
sees, for a host-side hotkey. Narrow, and not a reason to rework the relay.

**Reversible with the one flag, and nothing needs cleaning up.** Turning it off releases the holder
and restores the transport by itself — `controller_data` back on, `raw_data` back off, `control_by`
empty — even though only `third_party` was sent and the other four went as 0xFF, "leave alone". The
flags follow the takeover symmetrically in both directions, so neither the UI nor a caller has to
put them back.

**Consequence worth stating in any UI**: this is not a preference, it is a handover. With it on,
Steam drives the pad and our own onboard mapping stops being what the host sees. With
`controller_data` switched off by the new holder, anything reading the ordinary gamepad path may get
nothing — check that before recommending it as a default.

**Correction — it is command 17, and we already have the writer.**
`ControllerRepository.cs:1542` calls `ControllerSdk.EnableRawDataInput(..., enableThirdPartyControl, ...)`,
which is `EnableRawDataTransportInCommandFactory` — **17**, `[4]=7`, `[5]`=controllerData,
`[6]`=rawData, `[7]`=keyboard, `[8]`=mouse, **`[9]`=thirdPartyControl**, `[10]`=crc, with `0xFF`
meaning "leave alone". `flydigi/motion.py:34` `set_raw_data(..., third_party=...)` already sends it.
The reader is command **16**, `ReadRawDataReportStatusCommandFactory`, which decodes `data[5..8]`
as the four transport flags and `data[9]` as the third-party flag. Both halves are built:
`flydigi/motion.py` has `CMD_READ_TRANSPORT = 16`, `parse_transport` and `read_transport`, and the
switch is on the Controller page (`gui/models/device.py`, `thirdParty`).

**One gate to honour, and it is per device code.** `ControllerBusinessService.cs:1128` offers this
for **`k5` (Apex 5) at 7.0.3.0** and **`f5` (Vader 5) at 7.1.4.1** — both transcribed into
`motion.THIRD_PARTY_MIN_FIRMWARE`. Below the threshold, hide it. The version compared is the `main`
component of the seven-component command-1 reply. Note the GUI currently applies the `k5` threshold
whichever pad answered.

**We compare versions numerically, and that is a deliberate divergence.** Flydigi's
`DeviceUtil.CompareVersion` is `string.Compare(new, old, Ordinal) >= 0`, an ordinal *string*
comparison, so their own gate rejects firmware **7.0.10.0** against a 7.0.3.0 minimum — "1" sorts
below "3". `motion.version_at_least` parses the fields as integers, which differs from Space Station
only where Space Station is wrong.

`EnableMappingSwitchCommandFactory` (19 sub-function 4) is something else entirely and has no
English UI string at all. It is **not** this feature.

Also relevant to the "extra buttons and gyro" part: `DeviceMaskCommandFactory` (**16**) takes
`maskController`, `maskMedia`, `maskGyro`, which is how the pad decides what to expose to the host.

## SDL's own Flydigi driver, and why filling its trigger stub was ruled out

`src/joystick/hidapi/SDL_hidapi_flydigi.c` upstream. It knows this pad well: device ids **128/129**
are the Apex 5, and its sensor rates (**970 Hz wired**, 295 Hz dongle) are the same stream at two
transports — the ~300 Hz in `flydigi/motion.py`, which the DS5 relay's sensor-timestamp arithmetic is
derived from, is the dongle figure. The wired number matches what we measured
independently. `FLYDIGI_ACQUIRE_CONTROLLER_COMMAND 0x1C` on a 30-second heartbeat is our command 28,
which is where the `0x1C` traffic in "Steam Input contention" comes from.

**Input is complete; every output beyond rumble is a stub.**
`SetJoystickLED`, `RumbleJoystickTriggers` and `SendJoystickEffect` all return `SDL_Unsupported()`,
and `GetJoystickCapabilities` reports only `SDL_JOYSTICK_CAP_RUMBLE`. Its command vocabulary stops
at `0x01` info, `0x10`/`0x11` status, `0x12` haptic, `0x1C` acquire — 81 and 82 appear nowhere.
Twenty-two commits, all input-side; nothing has ever gone near effects. **Note "Joystick" in these
names is SDL's word for the device, not the analog stick** -- `SendJoystickEffect` is the backend
for `SDL_SendGamepadEffect` and has nothing to do with the sticks, which have no actuator anyway.

**Considered, scoped, and dropped.** A fork was set up and a handoff written before the argument
against it landed; both are kept at `~/Projects/sdl-flydigi/` with the conclusion recorded. Read the
next three paragraphs before reviving it.

**A passthrough is only worth anything if something sends Flydigi-format packets, and nothing will.**
Games emit *DualSense* reports, because that is the format with an ecosystem behind it. So a game's
trigger effects reach an Apex only if the game special-cases Flydigi — which no one is going to do —
or if something translates DS5 into Flydigi's vocabulary. `SDL_SendGamepadEffect` is documented as
"a gamepad specific effect packet", so a driver quietly accepting *another device's* format would be
inventing a convention SDL has not blessed.

**And we already solve it better.** Tier 4 is the answer to "how do DualSense trigger effects reach
this pad": present a virtual DualSense, let the game send DS5 reports to something it recognises, and
translate in `relay.translate_ds5`. Verified in Deathloop, works with any DS5-aware game, and needs
neither SDL nor upstream cooperation. An SDL patch would be a worse version of something that
already works.

**And the one plausible universal consumer has nowhere to put it.** Steam Input is the only layer
that could carry trigger effects across devices, and its abstraction is Xbox-shaped: it hands the
game a virtual controller, and an Xbox pad has impulse trigger *rumble*, not adaptive resistance.
There is no slot in that model for an effect. We have this documented already from the other
direction — **Steam Input must be off for Tier 4**, because it masks the DualSense as an Xbox pad
and breaks DS5 semantics, and the prior-art note below records the same for haptics. Steam Input
does not merely lack a path for trigger effects; it destroys the one that exists.

**The omission upstream is a judgement, not an oversight.** Whoever implemented acquire, status,
battery and per-model gyro rates knew this protocol well enough to add effects and did not.

What follows is what an implementation *would* have needed, kept because it is the part upstream
lacks and because the same facts would apply to any future attempt.

**The driver already has**
`HIDAPI_DriverFlydigi_WritePacket`, the report-id and magic constants, and a convention for
stripping a leading report id (`HandlePacketV2`). What upstream lacks is the effect vocabulary,
which §3a of PROTOCOL.md has hardware-verified. Three things to get right if anyone picks this up:

  * **Gate it, do not pass bytes through.** The same envelope carries **31** (firmware upgrade
    mode), **166** (flash write) and **253** (factory reset). A naive passthrough hands every
    application a brick button. Whitelist 81 and 82 and refuse the rest.
  * **Per-model dispatch is unvalidated.** Ours is Apex 5 knowledge. Vader trigger vibration is
    command **18**, not 81/82, and the not-yet-shipped `k6` would use `K6Trigger*` (83/85/87). We have an
    Apex 5 and a Vader 4 Pro, and the Vader 4 has no force triggers to test against.
  * **Expect "who calls it?"** and have an answer. SDL has **no typed adaptive-trigger API** -- no
    `adaptive` or `TriggerEffect` anywhere in `SDL_gamepad.h` -- so `SDL_SendGamepadEffect` is a raw
    byte passthrough and a caller must hard-code the packet layout. That works for the DualSense
    because everyone knows its format; nobody knows this one.

**The version that would actually matter is a typed API**, something like
`SDL_SetGamepadTriggerEffect(gamepad, side, effect, params)` over both the DualSense and this pad.
We are unusually well placed to argue for it: `flydigi/relay.py`'s `translate_ds5` already maps
DualSense effects onto Flydigi modes and is verified on hardware in both directions. It is also a
much bigger ask -- a cross-device abstraction over two different parameter spaces, which SDL has so
far deliberately not attempted.

**None of it buys us anything.** We reach 81/82 over hidraw directly, and that keeps working with
the third-party toggle on and SDL holding the pad. An upstream patch would only help other software
that wants Flydigi triggers without opening hidraw itself.

