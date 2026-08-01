# Sharing the pad with Steam and SDL

Arbitration on the vendor hidraw node, the pad-side toggle that hands the device to another driver,
Steam's identity assignment, and SDL's own Flydigi driver with its unfilled trigger stub.

Index: [PROGRESS.md](../PROGRESS.md).

## Several writers on one hidraw node

Several of this project's processes write `/dev/hidraw4` at once. The desktop app holds it open and
polls `Get info` every 30 seconds; a memory-driven route (`flydigi-monitor`, `flydigi-forza`,
`flydigi-dsx`) rewrites trigger effects as often as every 50 ms; both write whole 32-byte packets and
then read. Steam and SDL claim the same node and send their own acquire/heartbeat (`0x1C`, our
command 28, every 30 seconds); no lock reaches them, so the choice there is to disable Steam Input
for the pad or to tolerate it.

Two hazards:

1. **Interleaved writes.** Two packets can reach the pad in either order. For trigger effects that
   is self-correcting — the next frame overwrites it — but a config write (164/165 streams a blob in
   packets, 166 commits it) interleaved with anything else is not.
2. **Misattributed replies.** Replies are *broadcast to every reader of the node*, so a process
   receives ACKs for commands it never sent. A `Get info` ACK belonging to the app's poll lands in
   `flydigi_cmd`'s read after a rumble command:

   ```
   TX 03 5a a5 12 06 00 00 …          ← rumble stop
   RX 04 5a a5 01 01 00 80 01 …       ← command 0x01, nobody here asked
   ```

   `Controller.send()` collects every reply for 300 ms and `ack_ok()` matches on the echoed command
   byte and a success flag (`body[2] == cmd_id and body[5] == 1`) — nothing ties a reply to a
   particular request, so an overlapping exchange can hand the wrong answer to the wrong caller: a
   command reported as failed when it worked, or as succeeded when its own reply never came.

`Controller.claim()` (`flydigi/device.py:153`) takes an advisory `flock(2)` on the open node. `send`
takes it for a single packet; `blobs.read_blob` and `blobs.write_blob` hold it across a whole packet
stream. The app holds it across a write and its commit: `write_profile` spans `mapping.write_config`,
a conditional `apply_config` when the macro page moved, `save_config` and `effects.engage_stored`,
and `write_lighting` spans `lighting.write_config` plus `mapping.save_config`. The save command
commits whatever is in the pad's working memory and would otherwise commit someone else's write too.
The claim is re-entrant, so a claimed sequence can send freely. `_drain()` runs inside the claim
before each write and throws away anything already waiting: under the claim, a reply that arrived
before the request provably belongs to an exchange that is over.

`CLAIM_TIMEOUT = 5.0` seconds, polled at `CLAIM_POLL = 0.002`. The timeout is sized for a config
write, which streams up to 42 packets and waits for an ACK on each. A `flock` is released by the
kernel when the holder's file closes, crash included, so a wait cannot be on a corpse.

**Advisory, deliberately.** It binds only processes that ask, which covers this project completely —
everything reaches the node through `flydigi/device.py` except `tools/flydigi_cmd.py`, which opens
it itself and takes the same `flock` by hand.
Steam and SDL **must not be shut out**: the vendor interface keeps working with Steam Input on, which
is what lets trigger effects run in games Steam has taken the pad for ([What works and what stops
with the flag on](#what-works-and-what-stops-with-the-flag-on)). Steam's writes can still land
mid-sequence: harmless for effects, which the next frame overwrites, and a risk only for a config
write, a deliberate action rather than something a game triggers.

**Verified on hardware**, with Steam and steamwebhelper holding the node open throughout and
unaffected:

```
node free:      0.44s
held for 1.0s:  1.36s   ← the waiter is granted the moment the holder lets go
held for 2.0s:  2.36s
claim(timeout=0.3) while held -> DeviceBusy: another process has held /dev/hidraw4 for more than 0.3s
```

`DeviceBusy` names the node and the timeout it waited. It is in the worker's retry tuple, which
reconnects and tries once more, so a busy pad reads as a transient in the UI rather than as a crash.
Threads sharing one Controller get an in-process `RLock` as well, because `flock` attaches to the
open file description: two threads on one handle would both be granted it.

`python3 tests/test_device.py` covers the locking without hardware: cross-process exclusion,
re-entrancy, stale-reply rejection, release-then-grant, and two threads on one Controller.

## "Allow third-party apps to take over mappings"

Command 16 reads this flag, command 17 writes it, and the switch is on the Controller page behind
the firmware gate. A pad-side setting, not Steam's. **Necessary for Steam to recognise the pad, and
on its own not sufficient**: whether Steam then actually *names* the pad depends on a second thing
entirely — see [Recognition depends on *which identity* the pad lands
on](#recognition-depends-on-which-identity-the-pad-lands-on-not-just-on-the-flag) below. Space
Station's own words:

> When the switch is turned on and a third-party application (such as Steam, reWASD, etc.) is
> opened, the controller mapping will be taken over, and all Space Station settings will be invalid
> at this time.

### Commands 16 and 17

The writer is command **17**, `EnableRawDataTransportInCommandFactory`, reached from Space Station's
`ControllerRepository.cs:1542` via
`ControllerSdk.EnableRawDataInput(..., enableThirdPartyControl, ...)`:

```
[4]  = 7                 payload length
[5]  = controllerData    1 on, 0 off, 0xFF leave alone
[6]  = rawData
[7]  = keyboard
[8]  = mouse
[9]  = thirdPartyControl
[10] = crc over (3, 3 + [4])
```

`flydigi/motion.py:43` `set_raw_data(..., third_party=...)` sends it, building the same bytes with
the same checksum range (`flydigi/motion.py:46-53`); `gui/worker.py:195-203` is what calls it,
sending only `third_party` and leaving the other four at `0xFF`.

The reader is command **16**, `ReadRawDataReportStatusCommandFactory`. Request `[4]=2`,
`[5]=crc(3, 3+2)`. The reply decodes as follows, in the SDK's indices and in the raw ones used here —
the SDK's HID layer strips the report-id byte and this code keeps it, so everything shifts by one:

| SDK | raw | field |
|---|---|---|
| `data[5]` | 6 | `XInputEnabled` — `controller_data` |
| `data[6]` | 7 | `PrivateDataEnabled` — `raw_data` |
| `data[7]` | 8 | `KeyboardEnabled` |
| `data[8]` | 9 | `MouseEnabled` |
| `data[9]` | 10 | `ThirdPartyAppControlConfig.Enabled` — the third-party flag |
| `data[10..29]` | 11..30 | `ControlBy`, 20-byte ASCII, NUL-trimmed |

`flydigi/motion.py:201-240` implements both halves as `CMD_READ_TRANSPORT = 16`, `parse_transport`
and `read_transport`; the switch is on the Controller page (`gui/models/device.py`, `thirdParty`).

**The firmware gate is per device code.** `ControllerBusinessService.cs:1129-1145` offers this for
**`k5` (Apex 5) at 7.0.3.0** and **`f5` (Vader 5) at 7.1.4.1** — both transcribed into
`motion.THIRD_PARTY_MIN_FIRMWARE`. For any other device code Space Station skips the firmware check
entirely and gates on vendor id alone (`flag = device != null && device.VendorId == 14295`, 0x37D7).
Below the threshold, hide the switch. The version compared is the `main` component of the
seven-component command-1 reply. The GUI applies the `k5` threshold whichever pad answered
(`gui/models/device.py:214`). Covered by `tests/test_models.py:1233` and by two QML cases against
the fake pad: `tests/qml/tst_controller.qml:169` drives the toggle, and `:194` checks the switch is
hidden below the minimum.

**Version comparison here is numeric, and that is a deliberate divergence.** Flydigi's
`DeviceUtil.CompareVersion` is `string.Compare(new, old, Ordinal) >= 0`, an ordinal *string*
comparison, so their own gate rejects firmware **7.0.10.0** against a 7.0.3.0 minimum — "1" sorts
below "3". `motion.version_at_least` parses the fields as integers, which differs from Space Station
only where Space Station is wrong.

Two commands that are **not** this feature:

  * `EnableMappingSwitchCommandFactory` (19 sub-function 4) is something else entirely and has no
    English UI string at all.
  * `DeviceMaskCommandFactory` reuses id **16** on the *DInput* protocol (`CreateSimpleCommand()`,
    `[2]=maskController [3]=maskMedia [4]=maskGyro`), which is how the pad decides what to expose to
    the host. It is a different packet from the NewXInput command 16 above, which is built with
    `CreateSimpleCommand(isNewProtocol: true)` and `[4]=2`.

### What the flag actually does

Despite the wording, what the flag gates is whether the pad will **hand itself to another driver at
all**. Reading command 16 before and after flipping it:

```
before   third_party=False  control_by=''      Steam shows "generic XInput controller"
after    third_party=True   control_by='SDL'   Steam shows "Apex 5"
```

Three consequences:

  * **SDL claims the pad the instant it is allowed to.** `control_by` is the same 20-byte ASCII tag
    the cooperative-lock command carries, and it filled in with `SDL` by itself. SDL's acquire packet
    is where that tag comes from: `[0]=0x03 [1]=0x5A [2]=0xA5 [3]=0x1C [4]=23 [5]=acquire?1:0
    [6..25]='S','D','L'` then NUL padding — exactly the field command 16 reads back at raw 11..30.
    SDL will not send it until it has seen the flag set: `HandleStatusResponse` reads `data[9] == 1`
    out of the 0x10 reply and calls `HIDAPI_DriverFlydigi_SetAvailable(device, false)` otherwise,
    with the in-source comment `// Click "Allow third-party apps to take over mappings" in the
    FlyDigi Space Station app`. That is the same field `parse_transport` reads at raw index 10.
  * **SDL then reconfigures the transport on its own.** `controller_data` went True→False and
    `raw_data` False→True, and neither was requested — both were sent as 0xFF, "leave alone". So
    the new holder switched the pad into raw-report mode, which is what its own driver reads.
  * **Nothing re-enumerates.** Same bus address, same evdev names, same VID/PID. So Steam's native
    recognition comes from the acquire, not from any change of USB identity — nothing to do with
    descriptors or double-remapping.

### What Space Station sends

**The switch here is byte for byte what Space Station's is:**

  * Space Station's toggle reaches `ControllerRepository.EnableThirdPartyAppControl`
    (`ControllerRepository.cs:1533`), which calls
    `EnableRawDataInput(controller, null, null, null, null, enable, ...)` — command 17 with the
    layout above, the four `null`s becoming `0xFF`, "leave alone".
  * All **three** `EnableRawDataInput` call sites (`ControllerRepository.cs:1490`, `1542`, `1563`)
    pass the keyboard and mouse flags as `null`; neither is ever written.
  * The Electron UI fires exactly one IPC command for the switch, and no second call
    (`case "controlByThirdPartyAppEnabled": IpcCommandEnum_EnableControlByThirdPartyApp`).
  * `KeyboardMouseInjectRunner` is host-side Windows injection for keyboard/mouse *mappings*. It
    sends nothing to the pad, and `ControllerBusinessService.cs:76` gates it **off** while
    third-party control is active. **Not `SendInput`**, which this file said for a long time and
    nobody had checked: `SendInput` and `user32` appear nowhere in `SpaceStationService`. It is a
    pair of kernel filter drivers — `FeizVKB64.sys` and `FeizVMO64.sys`, installed through
    `keyfdo.inf`/`mousefdo.inf` and driven via `FeizVKBComm.dll` with PS/2 set-1 scan codes
    (`FeizVkeyMouHelper.cs:9-60`, `KeyCodeMapDic.cs:13-38`). Wrong about the mechanism, right about
    the substance.

**What Space Station does in addition:**

  * a read-only 30-second poll after the ACK (`StartThirdPartyMonitor`,
    `ControllerRepository.cs:147`) that refreshes its own UI via command 16 and writes nothing;
  * on Windows only, `DevconHelper.ExecuteDevconCommand` shells out to
    `devcon.exe enable|disable "USB\VID_37D7&PID_2501&MI_00"` — interface 0, the XInput interface —
    on every device connect and around DS mode. There is no Linux counterpart; the nearest
    equivalent is unbinding xpad, which this project deliberately does not do.

Neither re-asserts the flag. **Space Station's connect handler only reads it** — `OnDeviceUpdateImpl`
(`ControllerRepository.cs:92`) calls `StartThirdPartyMonitor` and nothing else third-party related,
and the config-apply path (`PrepareMappingConfigs`) deals only with mapping blobs.

### The keyboard/mouse composite

**A separate, persistent pad mode.** Observed here with the pad presenting `if01-event-kbd`,
`if01-event-mouse` and `if02-hidraw` while command 16 read `keyboard: False, mouse: False,
third_party: False` — so the extra HID nodes coexist with the flag off and with both transport flags
off. Whatever puts the pad into that composite, it is not command 17 and not this switch.

**Nothing drives them, and that stays unexplained.** Worth stating as a hole rather than leaving it
to be rediscovered. `ControllerHidManager.FindSpecialHidDevice` accepts, for vendor 0x37D7, only
`UsagePage == 0xFFA0` — the vendor collection — so Flydigi's own software never opens these two
interfaces at all. The pad's self-advertised capability bitmap (command 3,
`ReadHardwareFunctionStatusCommandFactory.ParseAckData`) lists ten features and keyboard emulation
is not among them. So the firmware may be able to do something with them that Flydigi's software
never asks for; nothing here has found a way to ask.

### What a keyboard binding actually is, measured

**The pad acts on `TARGET_KEYBOARD` and does not type.** Both halves measured here with
`tools/keyboard-target-probe`, against a long-standing claim in this project that 254 was inert.

  * Set A's key-table target byte to 254 and A stops arriving as `BTN_SOUTH` — on the gamepad node,
    the keyboard node and the mouse node alike. Suppressing its own gamepad output is a decision only
    the firmware can make, so the sentinel is understood rather than ignored.
  * Nothing is typed, and nothing was ever going to be: a key-table entry is three bytes and
    `MappingConfigParser.cs:619-635` zeroes both companion bytes on the same branch that writes 254,
    with the key code (`MapTypeKey.MapKeyboardKeyId`) in scope and discarded. Candidate codes written
    into those two bytes by hand — HID usage, Windows virtual key, and the pad's own key id, in
    either position — produced no keystroke.
  * Flydigi's *reader* cannot recover the binding either: `MappingConfigParser.cs:596` collapses
    anything above 32 to identity, which is why `ControllerRepository.cs:496-506` re-injects keyboard
    bindings from the host's own file after every read. A feature whose configuration cannot be read
    back from the device is not stored on the device.

So on Windows the two halves meet: the pad suppresses the button, and the filter driver above puts a
keystroke in its place. On Linux only the first half happens, which makes a key left on 254 a key
that does nothing — and is why `mapping.normalise_for_switch` strips them.

**A macro step cannot carry a keyboard key either.** `m_fdg_macro_step_struct_t` is
`{time_l, time_h, btn, event}` and `MacroConfigParser.cs:96` parses `btn` as a `ControllerKey`, on
the v3.1 page and in the v3.2 store alike. That is why the app's macro editor offers only
`XINPUT_TARGETS` as a step's output key — a measured constraint rather than a cautious one.

### Steam's duplicate listing

**Steam lists the pad twice** when the flag is toggled on while Steam is already running. A reconnect
with the flag already on produces **one** entry, the HIDAPI one; see the identity section below for
why. Reported on Windows as well as here. Both paths are legitimately supported and Steam does not
merge them:

```
xpad on 3-4:1.0  -> event-joystick -> "generic XInput controller"
Steam hidapi     -> hidraw4        -> "Apex 5"
```

`steamwebhelper` holds both hidraw nodes open while this is on. No command merges the two entries;
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

So the double listing is designed, and the dead XInput entry is an accepted cost — exactly the
`controller_data = False` behaviour measured below. The comment also states that **SDL cannot react
to the flag changing**, which is what the identity section turns on.

Enabling Steam Input for the pad makes Steam grab the physical device and hand the game a single
virtual controller, so the duplicate is visible in Steam's settings list and not to anything launched
through Steam. It matters for games started outside Steam, and with Steam Input off — the state Tier
4 requires ([docs/findings-haptics.md](findings-haptics.md)).

If it does need removing, unbinding xpad is the local fix
(`echo -n "3-4:1.0" | sudo tee /sys/bus/usb/drivers/xpad/unbind`), and it does not survive a wake
since re-enumeration rebinds. **Do not make it permanent with a udev rule**: the evdev node is where
everything else here reads sticks and buttons — the full list is under [What works and what stops
with the flag on](#what-works-and-what-stops-with-the-flag-on) below.

### What works and what stops with the flag on

Measured on hardware, with the flag on:

| | third-party off | third-party on |
|---|---|---|
| Steam's view | generic XInput controller | **Apex 5** |
| standard gamepad path (xpad / evdev) | works | **dead** — where the XInput entry exists at all, it accepts no input in Steam; after a reconnect there is no second entry |
| adaptive triggers over the vendor interface | works | **works** — commands 81 and 82 ACK *and are felt* |
| profiles, lighting, curves (config commands) | work | work |

`controller_data = False` silences the ordinary controller report, so everything driven over the
vendor interface survives — tiers 1, 2, 2b, 3 and 5 — and everything built on the evdev node stops.
That is tiers 4 and 4b, and:

  * `tools/flydigi-ds5` and `tools/flydigi-ds5-usbip`, which relay sticks and buttons into the
    virtual DualSense;
  * `tools/joystick-curve-probe` and `tools/stick-feel`;
  * `tools/gyro-probe` and `tools/trigger-stroke-probe`;
  * macro recording — `flydigi/macros.py` reads the pad's own evdev node.

The trigger effects were verified by feel, not by ACK: this pad ACKs commands it then ignores
(command 245, [docs/device-settings.md](device-settings.md)).

**DS mode and third-party mode are mutually exclusive, and the failure is not obvious.** Motion
survives — the vendor stream keeps running with `controller_data` off, measured below — so a game
gets a DualSense that tilts and has dead sticks and buttons, which reads as a broken mapping rather
than a missing input source. The tell is the relay's status line: `evdev=` stuck at 0 while
`motion=` climbs. The CLI relays are unguarded — neither reads command 16 before starting.

**Reversible with the one flag, and nothing needs cleaning up.** Turning it off releases the holder
and restores the transport by itself — `controller_data` back on, `raw_data` back off, `control_by`
empty — even though only `third_party` was sent and the other four went as 0xFF, "leave alone". The
flags follow the takeover symmetrically in both directions, so neither the UI nor a caller has to
put them back.

**The app states the trade and enforces it.** The Controller page presents the switch as a handover:
Steam drives the pad, the onboard mapping stops being what the host sees, and the ordinary gamepad
path may give nothing. The Buttons page disables remapping, the Macros page blocks recording, the
DualSense switch cannot be turned on while the flag is set (`gui/qml/pages/DualSensePage.qml:87`),
and the Controller and DualSense pages each warn when the other is active.

### Sticks, triggers and buttons in the vendor report

In the exact state above — third-party on, `controller_data` off — the vendor operator-data stream
was still delivering **3870 reports in 4 seconds** (~970 Hz), decoding to gyro ≈ 0 at rest and
accel Z ≈ 4096, the 1 g already verified: it stays alive precisely when evdev dies.

Offsets from `OperatorDataParser`, NewXInput branch, **+1 for the report-id byte this code keeps**:

```
raw  4,5    left stick X    signed 16-bit little-endian, `00 80` = -32768, `ff 7f` = +32767
raw  6,7    left stick Y    same, then negate
raw  8,9    right stick X
raw 10,11   right stick Y   negate
raw 12..15  buttons         four bitmask bytes, mapped below
raw 16      left trigger    linear, one byte
raw 17      right trigger   linear, one byte
raw 18..29  gyro and accel  already implemented and hardware-verified
```

Their `data[17]`/`data[23]` land on the proven `GYRO_OFFSET`/`ACCEL_OFFSET`, which establishes the
+1 shift for the rest. Flydigi's own parser converts a stick axis with `num -= 65535`, off by one
against the `00 80` = -32768 extreme; subtract 65536, or unpack as signed.

`OperatorDataParser.IsButtonPressed` selects four button bytes per protocol; NewXInput uses
`data[11..14]`, which is raw 12..15 here:

| raw | bits |
|---|---|
| 12 | Up 0x01, Right 0x02, Down 0x04, Left 0x08, A 0x10, B 0x20, Select 0x40, X 0x80 |
| 13 | Y 0x01, Start 0x02, Lb 0x04, Rb 0x08, Lt 0x10, Rt 0x20, Thl 0x40, Thr 0x80 |
| 14 | C 0x01, Z 0x02, M1 0x04, M2 0x08, M3 0x10, M4 0x20, M5 0x40, M6 0x80 |
| 15 | Menu 0x01, Turbo 0x02, Home 0x08, Back 0x10 |

`flydigi/motion.py:36` records the stick offsets as `STICK_OFFSETS = (4, 6, 8, 10)` and nothing reads
them. Sourcing sticks here rather than from evdev would not free Tier 4 from the third-party toggle:
Tier 4 requires Steam Input *off* and the toggle exists to let Steam take over, so the two conflict a
level above the input source. M1-M6 are already remapped onto real XInput buttons onboard with
nothing running; only a pad button the game never sees — a host-side hotkey — would need this path.

## Recognition depends on *which identity* the pad lands on, not just on the flag

Whether Steam shows "Apex 5" depends on which of two synthetic identities the HIDAPI device gets,
and that is decided by a race at enumeration.

**The pad reports no serial number, so Steam invents one.** Evidence throughout this section is
Steam's own controller log —
`~/.var/app/com.valvesoftware.Steam/.local/share/Steam/logs/controller.txt` on this Flatpak install,
`~/.steam/steam/logs/` on a native one, with the display side in `controller_ui.txt` beside it:

```
Controller has an Invalid or missing unit serial number, setting to '37d7-2501-79acb62'
```

The form is `<vid>-<pid>-<machine>`. The trailing `79acb62` is a per-machine constant shared by
unrelated controllers — a DualSense on the same host logs `54c-ce6-79acb62` — so only another entry
for the same VID/PID can collide, which is why the collision is always between the pad's own two
representations. The base `37d7-2501-79acb62` is stable. When it is already taken at that moment,
Steam appends a suffix and mints `37d7-2501-79acb62g` instead — a *separate* identity with a
separate `configset_37d7-2501-79acb62<suffix>.vdf`. Whoever registers first gets the base.

The config set an identity binds to is a file on disk:

```
…/Steam/steamapps/common/Steam Controller Configs/<accountid>/config/configset_<serial>.vdf
```

with `configset_controller_generic.vdf` beside it in the same directory.

**Two orderings, two results.** The two representations are distinguishable in the log by mapping
GUID — `...086804` with `paddle1-4`/`misc2`/`misc3` and a `Controller using HIDAPI driver` line is
the native path; `...080000` named `Flydigi Apex 5` with no driver line is xpad:

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

**After a reconnect the native driver holds the generic entry's identity.** The HIDAPI device
registers first, takes the base identity, and inherits `configset_37d7-2501-79acb62.vdf` — the config
set that belongs to the xpad representation.

**The identity is wrong in this state; the driver is not.** It is the native Flydigi HIDAPI driver in
every case — `Controller using HIDAPI driver, vid=0x37d7, pid=0x2501` in `controller.txt`, `Type: 30`
in `controller_ui.txt`, mapping GUID `...086804` with the paddles and `misc2`/`misc3` that only the
native path exposes. Verified on hardware while in this state:

  * **the full button set binds**, paddles included — the whole Steam Input binding UI works;
  * **the pad reads as acquired**: command 16 returns `third_party=True`, `control_by='SDL'`;
  * **SDL is live on it**, sending its 30-second acquire heartbeat — command 28 and command 1 ACKs
    seen on the vendor stream;
  * **everything this project drives over the vendor interface keeps working** — adaptive triggers,
    profiles, lighting, curves, the motion stream. Same as the trade table above.

What is lost is the name plus one real consequence: the entry is not *labelled* Apex 5, and because
it inherits the other identity's config set, Steam Input bindings saved under the `...62g` identity
are not the ones applied. Re-toggling restores both.

**This is what the disable/re-enable ritual does.** Reported on Windows and reproduced here: toggling
off and on reverses the order, letting xpad claim the base identity so the HIDAPI device is pushed
onto `...62g` and its own config set. It is the only way to force the second ordering, on either
platform. It also lines up with SDL's own admission above that it cannot re-trigger device changes
when the flag moves: a command-17 ACK crossing the wire is the only thing that makes
`HandleStatusUpdate` re-query status. SDL's `0x10`/`0x11` are our commands 16 and 17 —
`case FLYDIGI_V2_SET_STATUS_COMMAND: HIDAPI_DriverFlydigi_HandleStatusUpdate(...)`, and
`HandleStatusUpdate` calls `SDL_HIDAPI_Flydigi_SendStatusRequest`.

**Space Station does not solve this either.** Its connect handler only polls, so on Windows the same
reconnect produces the same wrong identity. Re-asserting the flag on connect — read command 16 and,
if it is on, send off then on after SDL has enumerated — is not implemented here. The fixes are
upstream: a stable serial for the device, or Steam not reusing one config set across both
representations.

**The stable serial is a small patch, and the pad already carries the id it needs.**
`identity.read_uid` is command **4** — one exchange, thirteen bytes, measured on this pad — and
`registry.key` prefers it precisely because the free identifier, command 1's address field, reads
all zeroes here. SDL's driver never asks for it: its command vocabulary is `0x01` info, `0x10`/`0x11`
status, `0x12` haptic and `0x1C` acquire, with no command 4 anywhere, which is why the device
presents no serial for Steam to use. Reading the uid at init and setting the device serial from it
would give the HIDAPI representation a stable identity of its own — so it would stop colliding with
the xpad one, keep its own config set, and hold its label across a reconnect, with no ritual. Two
things are untested and both bear on whether it is worth doing: that Steam honours a supplied serial
at all — the log line says "Invalid or missing", which implies a valid one is used, but nothing here
has tried it — and how long an upstream merge would take to reach Steam Input, which carries its own
SDL. Parked rather than attempted; the label is cosmetic and the bindings are recoverable by
re-toggling.

**HIDAPI and xpad blocks never share a timestamp.** Across the whole log the two never appear at the
same timestamp for `37d7/2501`, though both can be live at once (01:24:30 and 01:24:37 above). That
sits awkwardly with a carve-out whose stated purpose is to make the XInput interface *always* show
up; the evdev node is still there (`usb-Flydigi_Flydigi_APEX5-event-joystick`, xpad bound to
`3-4:1.0`, Steam holding `/dev/input/event16` open). Unexplained.

## SDL's own Flydigi driver and its unfilled trigger stub

`src/joystick/hidapi/SDL_hidapi_flydigi.c` upstream. Device ids **128, 129, 133 and 134** are
recognised there as the Apex 5 (this pad's full set is 128, 129, 133-136 — see
[docs/findings-other-devices.md](findings-other-devices.md)), and its sensor rates (**970 Hz
wired**, 295 Hz dongle) are the same stream at two transports — the ~300 Hz in `flydigi/motion.py`,
which the DS5 relay's sensor-timestamp arithmetic is derived from, is the dongle figure. The wired
number matches what was measured here independently. `FLYDIGI_V2_ACQUIRE_CONTROLLER_COMMAND 0x1C`,
resent on `FLYDIGI_ACQUIRE_CONTROLLER_HEARTBEAT_TIME` (30 s), is our command 28 — the `0x1C` traffic
on the shared node.

**Input is complete but for two button bits; every output beyond rumble is a stub.**
`SetJoystickLED`, `RumbleJoystickTriggers` and `SendJoystickEffect` all return `SDL_Unsupported()`,
and `GetJoystickCapabilities` reports only `SDL_JOYSTICK_CAP_RUMBLE`. Acquire, status, battery and
per-model gyro rates are all implemented; `HandleStatePacketV2` forwards every button in the report
except raw 15's `0x02` and `0x10`, Turbo and `Back` in the table above. Its command vocabulary stops
at `0x01` info, `0x10` get status and `0x11` set status (our commands 16 and 17), `0x12` haptic,
`0x1C` acquire — 81 and 82 appear nowhere. Twenty-two commits on the file, all input-side.
**"Joystick" in these names is SDL's word for the device, not the analog stick**:
`SendJoystickEffect` is the backend for `SDL_SendGamepadEffect` and has nothing to do with the
sticks, which have no actuator anyway.

**A game's effects reach an Apex only through a translator.** Games emit DS5 reports, not Flydigi
ones, and `SDL_SendGamepadEffect` is documented as "a gamepad specific effect packet" — a format per
device, with no cross-device convention to accept another one under. Tier 4 is that translator:
`relay.translate_ds5` maps DualSense effects onto Flydigi modes behind a virtual DualSense
([docs/findings-games.md](findings-games.md)). Steam Input is the only other layer that could carry
trigger effects across devices, and its abstraction is Xbox-shaped: impulse trigger *rumble*, not
adaptive resistance. It also masks the DualSense as an Xbox pad, which is why **Steam Input must be
off for Tier 4** ([docs/findings-haptics.md](findings-haptics.md)).

**The driver already has** `HIDAPI_DriverFlydigi_WritePacket`, the report-id and magic constants,
and a convention for stripping a leading report id (`HandlePacketV2`). What upstream lacks is the
effect vocabulary, which §3a of [PROTOCOL.md](../PROTOCOL.md) has hardware-verified. Three
constraints stand between that vocabulary and an output path:

  * **The effect envelope also carries destructive commands.** The same envelope carries **31**
    (puts one named chip into firmware upgrade mode), **166** (flash write) and **253** (factory
    reset), so a passthrough hands every application a brick button. Only 81 and 82 belong on a
    whitelist.
  * **Per-model dispatch is unvalidated.** 81/82 is Apex 5 knowledge. Vader trigger vibration is
    `VibrationCommandFactory`, command **18** on NewXInput and **15** on DInput, gated on
    `controller.IsSupportTriggerVibration` — not 81/82 — and the not-yet-shipped `k6` would use
    `K6Trigger*` (83/85/87). The Vader 4 Pro on hand has no force triggers to test against.
  * **No typed caller exists.** SDL has **no typed adaptive-trigger API** — no `adaptive` or
    `TriggerEffect` anywhere in `SDL_gamepad.h` — so `SDL_SendGamepadEffect` is a raw byte
    passthrough and a caller must hard-code the packet layout. That works for the DualSense because
    everyone knows its format; nobody knows this one.
