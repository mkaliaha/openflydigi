# Apex 5 on Linux — project state

Replacing Flydigi's Windows-only Space Station on Linux: a zero-dependency Python backend, a
Kirigami desktop app on top of it, and six routes for getting adaptive-trigger effects into a game.
Not just triggers: profiles, remapping, macros, sticks, vibration, lighting, the screen and the
pad's own settings.

This file is the index — status, what is next, and the hardware facts that have no other home. The
wire protocol is [PROTOCOL.md](PROTOCOL.md); the long write-up behind each finding is in
[docs/](docs/), listed under [Where the detail lives](#where-the-detail-lives).

## Status

Adaptive triggers are done and validated in real games. The desktop app covers profiles, button
remapping, macros, sticks, vibration, per-profile trigger effects, the pad's own device settings,
lighting, the screen, the game list and its own setup. The daemon detects a running game and applies
its route unattended.

**Left to build:** gyro mapped to a stick, the charging dock, driving a second pad deliberately, an
interactive crop for the Screen page, and the smaller pieces under What's next.

| Tier | Mechanism | Games | State |
|---|---|---|---|
| 1. Vibration bind | cmd 82 `SyncWithGrip`, driven by game rumble | 33 | validated in Death Stranding 2 |
| 2. Forza telemetry | Data Out UDP 5300 → rule engine → cmd 81 | 4 | validated in Forza Horizon 6 |
| 2b. DSX listener | UDP 7878 | **any DSX-compatible mod** | self-tested (`tests/test_dsx.py`) |
| 3. XGameMonitor | reads game process memory | 31 | validated in Dark Souls: Remastered |
| 4. Virtual DualSense | uhid DS5, effects translated | **any DS5-aware game** | validated in Deathloop |
| 4b. …over USB | usbip + vhci DS5, **plus haptic audio** | **any DS5-aware game** | validated in Deathloop |
| 5. Third-party mods | game-side mods speaking DSX | 11 | works via 2b; not supported |

Tier 4b is what the app's **DualSense** switch turns on, and it supersedes tier 4 wherever both
work: same input, same triggers, and a game's PS5 haptics reach the pad's motors as well. Tier 4
remains for a machine with no `vhci-hcd`.

Long-form state per tier, and the notes from each validating game, are in
[docs/findings-games.md](docs/findings-games.md).

The backend is **pure Python with zero dependencies**, so `flydigi-ds5` runs on any machine with
Python 3.9 and no Qt. Licensing is per-file via REUSE: the table is in
[README.md](README.md#licensing), the reasoning in [LICENSE](LICENSE). Two rules keep the split
true — `gui/` may import `flydigi/` and never the reverse, and nothing Flydigi-owned is committed
(`tools/fetch-configs` restores it). Verify with `reuse lint` inside `apex-dev`.

## What's next

Roughly in order of value.

 1. **Gyro mapped to a stick (J2).** Offset 137, 8 bytes, smoothing curve at 830. Works in any game
    with nothing running, which on Linux is otherwise Steam Input only.
 2. **The charging dock (`cd2`).** Blocked only on decompiling `Flydigi.ChargerSdk.dll` /
    `Flydigi.CoolerSdk.dll`. It is a *lighting* problem, not a screen one — 162 addressable LEDs
    over the ordinary config path, and `cd2_led_sync` keeps it in step with the pad.
    → [docs/findings-other-devices.md](docs/findings-other-devices.md)
 3. **Multiple pads.** The device-type guard in `flydigi/identity.py` refuses to write anything that
    is not a k5, and the app, the mapping CLI and the settings CLI all go through it, so an Apex 5
    config cannot reach a Vader. Refusing is not selecting:
    `find_device` returns the first `/dev/hidraw*` in sorted-by-name order carrying the vendor
    descriptor prefix — `hidraw10` before `hidraw2` — and only `tools/flydigi_cmd.py` surfaces the
    `Controller(path=...)` override as `--device`, so with both pads attached everything else opens
    whichever the sort reaches first. What remains is driving
    the Vader deliberately, which `identity.require(ctrl, "f4")` already allows. It also unlocks
    the **trigger-vibration editor** (J5, offset 154), written in `MappingConfig.trigger_motor()`
    with the layout asserted in tests and no UI, because `IsSupportTriggerVibration` is a Vader
    flag and this pad has no such motors. The Vader is likewise the machine for the ADC calibration
    command, which `GenerateControllerVader4` is the only factory to set.
    → [docs/findings-profile-blob.md](docs/findings-profile-blob.md) J5
 4. **An interactive crop for the Screen page.** Everything else there is done.
 5. **Third-party mode: optional polish.** Command 17 here is byte-identical to Space Station's.
    After a reconnect with the flag already on, Steam stops *labelling* the pad Apex 5 while
    everything keeps working — cosmetic, plus a bindings-storage nuisance. The optional workaround,
    which neither app does, is to re-assert the flag off then on once SDL has enumerated; the real
    fixes are upstream. → [docs/findings-steam.md](docs/findings-steam.md)

**Single commands, each verifiable on the hardware here.** The write-ups are under "Commands beyond
the settings block" in [docs/device-settings.md](docs/device-settings.md).

  * **Restore a profile slot to factory** — `ResetMappingConfigByCfgId`, command **175**. The
    Buttons page's "reset all" only clears key mappings in the in-memory blob; Space Station's
    resets the whole slot on the pad.
  * **Controller nickname** — read **2**, write **24**. Self-verifying, and it makes a two-pad
    desk legible.
  * **The cooperative lock** — `AcquireController`, command **28**, with a 20-byte ASCII tag. The
    read half is built as `motion.read_transport`; the write half is not.
  * **Custom stick curves.** The page offers presets and a Custom label; Space Station drags the two
    interior breakpoints. `set_stick()` already takes `point1`/`point2`, so this is GUI-only work.
  * **Stick diagnostics** — "Test circularity" with an average-error readout, and a centre-offset
    check whose tolerance decides whether a stick needs calibrating before its dead zone is touched.
    `tools/joystick-curve-probe` and `tools/stick-feel` cover this on the bench; nothing does in the
    app.
  * **A grip vibration test.** Space Station's is a toggle, not a button: while it is on, pulling a
    trigger vibrates that side's grip, harder the further it goes. Host-driven — triggers off evdev
    into `effects.rumble()`, which is what `tools/stick-feel` already does from the sticks — and it
    makes the Vibration page self-checking. Their tooltip has a second form for pads that also
    buzz the trigger itself, which is the Vader's hardware, not this one's.
  * **Macro editing.** The app records and deletes. Space Station also edits a recorded macro's
    steps — output key, duration, interval — and builds one without recording at all. `set_macro()`
    already takes arbitrary steps, so this is GUI-only too.
  * **Restart the controller** — command **29**, no argument. Built as `settings.restart` and
    `tools/flydigi-settings restart`; never sent to hardware, and not in the app until it has been.

**Unsettled by measurement.** Whether the firmware accepts 164/165 aimed at a slot it is not
running. What the **Xbox home button** toggle does (19 sub 2): the command is built and gated behind
`--i-know`, so `tools/flydigi-settings xbox-home off --i-know` and one evdev capture settle it.
→ [docs/device-settings.md](docs/device-settings.md)

## What's done

All command factories are decompiled under `decompiled/Flydigi.ControllerSdk/`.

| Feature | Commands | Where |
|---|---|---|
| Mapping profiles | status 161, apply 162, read 163, write 164/165, save 166 | `flydigi/mapping.py`, `tools/flydigi-mapping`, GUI |
| Device-type guard | identify read 1 | `flydigi/identity.py` — `require()` refuses anything but a k5, and `flydigi/` calls it nowhere itself; `flydigi-mapping`, `flydigi-settings` and the app take it once per connection, so their reads are refused too |
| Buttons, sticks, vibration, stored triggers | inside the 840-byte profile blob | same module — [detail](docs/findings-profile-blob.md) |
| Macros, played by the pad | the profile's macro page at 230, plus 162 to make one live | `flydigi/mapping.py`, `flydigi/macros.py` (the recorder), GUI — [detail](docs/findings-profile-blob.md) |
| Live trigger effects, all six | 81, 82 | `flydigi/effects.py` — [PROTOCOL.md](PROTOCOL.md) §3a |
| Device settings | read 3, write 19 by sub-id, 20/21/22/23, restart 29 | `flydigi/settings.py`, `tools/flydigi-settings`, GUI — [detail](docs/device-settings.md) |
| RGB lighting | read 167, write 168/169 | `flydigi/lighting.py`, GUI — including the vibration light effect at LED-blob byte 9 |
| The screen | 31 + UART OTA over CDC; `TestScreen` 242; 19/9 and 19/8 | `flydigi/screen_ota.py` (the upload), `flydigi/screen.py` (242 and the two settings), GUI — [detail](docs/findings-screen.md) |
| Arbitration between this project's writers | advisory `flock` on the node | `Controller.claim()` — [detail](docs/findings-steam.md) |
| Third-party takeover toggle | read 16, write 17 | `flydigi/motion.py`, GUI — gated on firmware (`THIRD_PARTY_MIN_FIRMWARE`: k5 7.0.3.0, f5 7.1.4.1), and the app hides the toggle below that — [detail](docs/findings-steam.md) |
| Battery, gyro, accel | 1, and the vendor input stream | `flydigi/motion.py` |
| Per-game auto mode | — | `tools/flydigid`, `tools/flydigi-auto` — [detail](docs/findings-games.md) |
| Virtual DualSense (tier 4) | — | `flydigi/uhid.py`, `tools/flydigi-ds5` — [detail](docs/findings-haptics.md) |
| Virtual DualSense over USB (tier 4b) | — | `flydigi/usbip.py`, `tools/flydigi-ds5-usbip` — adds haptic audio |
| DualSense mode as one switch for the whole system, not a per-game route | — | `flydigi/dsmode.py`, the app's DualSense page |

**The vibration bind is live state, and is re-applied when the pad comes back.** It is command 82
with no blob write, so `tools/flydigid` watches for the pad leaving the bus mid-game and re-applies
the bind once it is back, retrying each poll until it takes. `--reassert` is a separate timer
against Steam Input clobbering the state, and is off by default.
→ [docs/findings-games.md](docs/findings-games.md)

### DualSense mode

**Haptic audio works**, over tier 4b only: a game writes haptics to a virtual DualSense and the
Apex 5's motors reproduce them. Proton joins an audio endpoint to a gamepad by a ContainerId that
only genuine USB topology satisfies, so uhid can never carry it, and every soft-UDC shortcut is
blocked on isochronous transfer. What works is the USB/IP *client*: `flydigi/usbip.py` serves a
device from userspace and `vhci-hcd` enumerates it locally. Measured against Deathloop with
`tools/flydigi-ds5-usbip --haptics --motors`.
→ [docs/findings-haptics.md](docs/findings-haptics.md)

**DS mode is a switch, not a route.** The DualSense tiers need no per-game data, so `isPS5` is not
a route in `flydigi/prefs.py` and the daemon never starts this tier. `flydigi/dsmode.py` is the
module behind the app's switch and there is no DS-mode CLI. The haptics-to-motors switch beside it
is read once at start, so it has to be set *before* DS mode is turned on.
→ [docs/findings-games.md](docs/findings-games.md)

**A sleeping Apex 5 no longer ends the session.** The pad leaves the USB bus when it sleeps, and
both relays used to go with it — the first `ENODEV` off the evdev node fell through to the cleanup
that detaches the virtual pad, so putting the pad down during a cutscene cost the game its
controller. It cost more than that: a game binds its audio stream to the DualSense once, so a
DualSense that goes away and comes back is a DualSense with no haptics for the rest of the run,
even though the input works again. `flydigi/relay.py`'s `PadLink` holds the physical pad as
something that may be absent — a failed read means "gone", not "fatal", and the nodes are looked
for again once a second under whatever numbers they come back as. Meanwhile the virtual pad stays
attached, fed a released state so a pad that vanished mid-press does not leave a button held, and
the trigger effects are sent again when the pad returns, since it forgets them when it sleeps.
`pad=` and `drops=` in the status line are the physical pad, never the virtual one; the app's
DualSense page shows them as their own row. Not yet observed in a real game — the loop is covered
in `tests/test_relay.py` against a fake bus, and what wants confirming on hardware is that the
game's haptics really do survive the nap.

**DS mode requires third-party mode off**, and the two switches are in the same app. Both relays take
sticks and buttons from evdev, and the third-party toggle switches `controller_data` off — the report
the evdev node is built from. Motion keeps arriving on the vendor stream, so the symptom is a
DualSense that tilts with dead sticks and buttons: the relay's `evdev=` counter sits at 0 while
`motion=` climbs. → [docs/findings-steam.md](docs/findings-steam.md)

**The privilege model.** The attach is the only privileged step, so the relay is started through
pkexec, does the module load and the attach as root, and then `setuid`s back to the invoking user
before it opens a device or starts a thread. What runs for the length of a play session is an
ordinary user process; stopping it is a plain SIGTERM, and the vhci port frees itself when the socket
closes. A socket-passing helper is not an alternative: `SCM_RIGHTS` does not survive `host-spawn`, so
one would fail exactly where the app runs — in the `apex-dev` distrobox.

## Known limitations

  * **With DS mode on, a game sees both the Apex 5 and the virtual DualSense.** Nothing can hide the
    physical pad from a game that enumerates it, so the launch option that ignores the Apex 5 is part
    of the feature. → [README.md](README.md), [docs/findings-haptics.md](docs/findings-haptics.md)
  * **Turn DS mode on before starting the game.** A game opens its stream to the controller's audio
    device once, at launch, so switching DS mode on mid-game gives triggers that work and haptics
    that stay silent until the game is restarted.
  * **Steam lists the pad twice** with the third-party flag on, and after a reconnect stops labelling
    it "Apex 5" while it keeps working on the native driver. Neither is fixable from here.
    → [docs/findings-steam.md](docs/findings-steam.md)

## The desktop app

**QML on Kirigami, in `gui/`**, calling the backend in-process — no D-Bus.

```bash
distrobox enter apex-dev -- python3 -m gui
```

**PySide6 must come from wherever Kirigami comes from** — the distribution, or the Flatpak runtime,
but never a PyPI wheel, whose bundled Qt tags its private symbols differently. That is why the
distrobox exists. Setup, the package list and the symbol detail are in
[gui/README.md](gui/README.md) and `gui/requirements.txt`.

| Tab | What works |
|---|---|
| Controller | Device (connection, battery level, reload from the pad); Profiles (the four slots — opening one is how you switch the running profile); Selected profile (rename the open profile, back up / restore it to file); Other software (let Steam and similar take the pad over, and who currently holds it) |
| Device | the pad's own settings, not a profile's: switching profile from the pad with `FN + A/B/X/Y`, sleep time, the mapping switch (sub-id 4, undocumented in every locale Flydigi ships), stick debounce, auto-calibration, the rebound filter, stick resolution and centre sensitivity — plus the polling rate, shown and not offered |
| Buttons | remap, turbo + hold/toggle, reset all to default |
| Macros | record a sequence off the pad and bind it to any key, pick once / while held / toggle, set the repeat gap, see every step, delete |
| Sticks | dead zone, outer dead zone, sensitivity curve presets, circular range |
| Vibration | master switch, per-grip enable, min/max window, strength |
| Triggers | stored effect — all six of Flydigi's, each with its own controls, engaged on the pad as well as stored — plus the travel window, Flydigi's "Stroke Setting", as a start/end pair |
| Lighting | effect, up to 5 colours, brightness, cycle time, react-to-rumble |
| Screen | pick a picture or GIF, choose how it fits, preview the encoded frame, and send it over the serial link — with the frame count and a time estimate before you start; plus the always-on display and the status bar |
| Games | all 94 games, searchable, filtered by route; **Update list** refetches the gamelist from Flydigi's public API; vibration presets load onto the pad from here; per-game **Auto** toggle, a route picker where a game really has a choice, and a DualSense marker on the 23 games Flydigi lists as DS5-aware |
| DualSense | the tier-4b switch: vhci-hcd's state, haptic audio to the motors, what the relay is doing, and the launch option to copy |
| Setup | the daemon's unit, "running now" and "start at login" as separate switches, the application-menu entry, and the udev rules behind one authentication prompt |

**Everything device-facing runs on the worker thread** (`gui/worker.py`) and requests cross as
signals. Calling a worker slot directly runs it on the caller's thread, which silently puts blocking
HID traffic back on the UI thread.

**Three pages are shut off while another driver holds the pad** — **Buttons**, **Macros** and the
**DualSense** switch. Everything else stays live: triggers, lighting, sticks and the screen all keep
working in that state. The measured half is recording — with `third_party` on and SDL holding the
pad, a 60-second capture of the evdev node returned nothing at all, and the same capture with it off
caught everything, so a recording made in that state is silently empty. Whether the pad still applies
its key table under another driver is untested either way.

**Apply vs save**: "Apply" writes 164/165 and takes effect immediately; "Apply and save" also sends
166. Because 166 commits whichever profile is live, the app refuses "Apply and save" when the open
profile is not the one the pad is running and says so (`canSaveToFlash` / `saveRefused`). The full
argument, the hardware proof of 166, and the QML testing traps are in
[docs/findings-desktop-app.md](docs/findings-desktop-app.md); how to work in `gui/` is
[gui/README.md](gui/README.md).

## Facts about the pad, the wire and this code

  * **Report id is `0x03`** on the vendor interface, not the `6` the decompiled
    `TakeEndpointByDevice()` suggests. Find the node by report-descriptor prefix `06 a0 ff`; it moves
    between wired and dongle.
  * **Wine maps game PEs at their image base** (`0x140000000`), same as Windows, so Flydigi's memory
    offsets work unmodified.
  * **The pad publishes keyboard, mouse and gamepad evdev nodes under one vendor/product id, and the
    keyboard sorts first.** Resolve with `axes=True` (non-empty abs capabilities) or a relay binds a
    node that never sends a gamepad event.
  * **Never match a game process by cmdline alone** — Steam/Proton wrappers (`reaper`, `bwrap`,
    `pv-adverb`, `steam.exe`) all carry the game's path. Require the PE to be mapped.
  * **A sleeping Apex 5 leaves the USB bus.** It does not go quiet on HID — it disconnects, wired
    included: `usb 3-4: USB disconnect, device number 27` with no matching connect, no `37d7:2501`
    in `lsusb`, no hidraw node. So "the pad is asleep" and "the cable is dead" are the same symptom
    at this level, and `find_device` raises `DeviceNotFound` rather than any read timing out.
    Pressing a button re-enumerates it, which is why node numbers change on reconnect — resolve by
    name/descriptor, never by path. Anything that holds the pad for a whole session has to expect
    this: `flydigi/relay.py`'s `PadLink` is that expectation for both DualSense relays.
  * **The pad discards unsaved config when it sleeps**, not merely on a power cycle — observed with
    lighting. Applying is working memory in the literal sense; command 166 is what makes it last.
  * **Effects persist in controller state** until changed; there is no timeout.
  * **The trigger travel window is the block at blob offset 123, and this pad plays it — the
    force-trigger `Param[0..1]` at 195/215 is inert.** Measured with `tools/trigger-stroke-probe`.
    Both triggers still span the full 0..255 output, so the window moves *physical* travel and not
    what the game reads: on this pad, bringing the end in is a software hair trigger.
    → [PROTOCOL.md](PROTOCOL.md) §3c, [docs/findings-profile-blob.md](docs/findings-profile-blob.md) J3
  * **A stored trigger effect does nothing until a live command starts it, and the order of the two
    matters.** Writing the force-trigger block (offset 185, 2 × 20 bytes) and applying the config
    stores the effect and leaves the triggers loose. `effects.engage_stored` rebuilds the live
    command per side from the stored bytes — 82 for a stored `Vibration` bind, 81 for the rest
    ([docs/device-settings.md](docs/device-settings.md)) — as Space Station does 500 ms after every
    applied-config read, and the app calls it after a profile write and after opening one. **Write
    the blob first and send the live commands after** — the reverse order silently loses the write,
    because those ACKs are still in flight when the write handshake starts.
    → [PROTOCOL.md](PROTOCOL.md) §3c
  * **The Lock effect makes the trigger digital** — the axis reports 0 or 255 and nothing between —
    so it is a hair trigger in the button sense, and there is no analogue reading left for the travel
    window to rescale. Under *Racing* the same window works normally.
    → [PROTOCOL.md](PROTOCOL.md) §7
  * **One command per trigger.** Side 3 (`Both`) ACKs and leaves the triggers loose, so
    `device.SIDE_BOTH` is a trap. → [PROTOCOL.md](PROTOCOL.md) §3a
  * **Trigger effects 2 and 3 are named the other way round by Flydigi's own UI.** The SDK enum
    says `Sniper=2, Recoil=3`; Space Station's picker shows mode 2 as "Recoil" (zh 机枪, machine
    gun) and mode 3 as "Sniper" (狙击), and the behaviour follows the label — 2 rattles, 3 breaks
    through. Code and wire here use the enum name, the UI uses theirs, so that advice given for one
    application lands on the same effect in the other. → [PROTOCOL.md](PROTOCOL.md) §3a
  * **On the wire `bindType` is 2** — every `SyncWithGrip` Flydigi constructs passes 2, and all 34
    gamelist entries flagged `isVibration` carry `vibType: 2`; the stored block writes 2 for the
    Vibration effect and 0 for every other. The tier table above says 33 because that is the
    vibration *tier*: Fallout 4 carries the flag and also ships a mod, so `games.tier()` classifies
    it as bespoke. → [PROTOCOL.md](PROTOCOL.md) §3a
  * **A config apply does not restore live trigger state.** Bind and effect state set by 81/82
    survive `apply_config`, so re-applying a profile does not undo an experiment. Set it back
    explicitly, with `bindType 2`.
  * **Stored trigger type 5 is *not* a Vader feature, unlike the control sitting next to it.**
    Space Station's trigger-mode picker emits all six modes with no `deviceCode` test, gated only on
    `supportAdaptTrigger`. The Vader-only block in the same panel is `vibrationTriggerConfigParam` —
    the trigger *motors*, J5 at offset 154, gated on `IsSupportTriggerVibration` — not
    `adapterTriggerConfigParam`, where type 5 lives. Stored type 5 becomes command **82**, which is
    tier 1; what the firmware does with *live* mode 5 is unmeasured
    ([PROTOCOL.md](PROTOCOL.md) §3a).
  * **hidraw replies go to every reader of the node.** An ACK you receive is not necessarily an
    answer to anything you sent — hence `Controller.claim()` and the drain before each write.
  * **`flock` attaches to the open file description, not the fd or the process.** A `dup`'d handle
    is the same lock holder and is granted the lock unconditionally; two `open()`s of one path are
    not.
  * **`Controller.claim()` does not hold Steam off**, because Steam takes no lock on the node — which
    is wanted, since the vendor interface works with Steam Input on.
    → [docs/findings-steam.md](docs/findings-steam.md)
  * **`effects.rumble()` must use `wait=0`** when driven continuously, or the 100 ms ACK wait puts
    the motors far behind.
  * **A command answering is not a command working.** The screen's picture family (208..211) parses
    every packet on an Apex 5 and echoes the fields back, and no picture reaches the panel — though
    211 still commits, so a 208/211 pair with no frame between them destroys a stored custom image;
    command 242 ACKs `off` and stays lit; command 245 ACKs and is ignored. On this pad an ACK means
    the firmware understood the *shape* of what you sent. Only the hardware says whether it did it.
  * **`Controller.send` takes an `until` predicate**, and without one it always burns its full
    timeout. Right when a reply may arrive in several packets and no caller can say how many; wrong
    for a long stream of one-for-one exchanges. Any fake that stands in for a Controller has to
    accept the keyword. → [PROTOCOL.md](PROTOCOL.md) §7
  * **Qt reads animated GIFs and cannot write them.** No `gif` in
    `QImageWriter.supportedImageFormats()` at all, and multi-page tiff and webp both write happily
    and then read back as a single frame. An animation for a test has to be committed, not generated.
  * **Do not send anything slow through the worker's `_attempt`.** It retries once, which is right
    for a sulking pad and wrong for a screen upload: that runs for minutes and has already switched
    the pad into upgrade mode.
  * **Steam Input must be off** for either DualSense tier — it masks the pad as an Xbox controller
    and breaks DualSense semantics.
  * **A macro is stored by the write and played by the apply** (command 162), independently of the
    save: macros written and not applied sit on the pad and do nothing, and saving only decides
    whether they survive a sleep. `MappingConfig.macro_page` is what a writer compares to know
    whether it owes an apply, since applying makes the pad audibly re-seat its trigger motors.
    The macro page and the key table are read independently and **both** fire, so removing the body
    at 230 is part of remapping a key; `set_mapping` does it, as Flydigi's own repository does.
    → [docs/findings-profile-blob.md](docs/findings-profile-blob.md) J6
  * **Reading a mapping config switches the pad to it** — the firmware pages it in as the live one,
    audibly re-seating the trigger motors. The desktop app leans on this rather than fighting it:
    opening a profile is how you switch to it, as Space Station does, so the profile on screen is
    always the one running, which also keeps saving correct since command 166 commits whichever
    profile is live. `read_config_preserving` restores the previous slot for a caller that genuinely
    wants to peek; prefer command **161**, which reports the active slot and a version id per slot
    with no side effect at all. → [PROTOCOL.md](PROTOCOL.md) §9
  * **The config commands are checksummed and the trigger-effect family — 81, 82 and rumble 18 — is
    not.** A mapping or lighting packet with a bad checksum gets no reply: the pad stays silent
    rather than erroring.
  * **The lighting's rumble reaction is LED-blob byte 9, not byte 2** — `GripSync`, written by
    `LedConfig.grip_sync` and labelled "Vibration light effect" on the Lighting page. Byte 2 is
    `ClickFeedback`, which latches the ring and does nothing as a control. Space Station's lighting
    write is four steps, of which command **171** is not implemented here.
    → [docs/device-settings.md](docs/device-settings.md)
  * **Lighting effects are frame data, not a mode byte.** The pad has no animation generator; it
    plays the stored frames, which the host computes from (mode, colours) and uploads, so writing a
    different mode number alone changes nothing visible, and the frame geometry has to be derived
    from the blob. → [docs/device-settings.md](docs/device-settings.md)
  * **`JoystickPrecision` is in declaration order, not by bit depth** — `None, 8, 10, 12, 9, 11, 14,
    16`. So this pad's `2` is **10-bit**, not 12, and any picker sorted the way a person expects
    disagrees with the wire from 9-bit on. What goes out has to be the enum index.
    → [docs/device-settings.md](docs/device-settings.md)
  * **A command-19 reply echoes the value and never the sub-id**, so an ACK means "a setting was
    written" and not which — and the pad acknowledges sub-ids it reports as unsupported. Every write
    in `flydigi/settings.py` therefore goes through `apply()`, which the GUI and the CLI both use,
    and that ends in a command-3 read, so the UI shows what the pad reports rather than what was
    asked for. → [PROTOCOL.md](PROTOCOL.md) §8c
  * **The pad plays the nine-point stick bank at offset 790 and reads nothing in the polyline at
    109.** Flattening the bank silences the stick; flattening the polyline changes nothing even with
    the type byte forced to Custom, and `edge` at 801 is inert at 236, 90 and 100. So `center`,
    `edge`, `p1` and `p2` are the host-side source form, and a stick UI has to compile the bank with
    `mapping.stick_bank()` — writing the fields it edits moves a slider and changes nothing the hand
    can feel. Same architecture as the lighting frames: the host computes, the pad plays. Measured
    with `tools/joystick-curve-probe`.
    → [docs/findings-profile-blob.md](docs/findings-profile-blob.md)
  * **The factory stick bank is `[50, 62, 75, 87, 100, 112, 125, 137, 150]`** — the compiler
    truncates where Space Station's Electron JS rounds.
    → [docs/findings-profile-blob.md](docs/findings-profile-blob.md)
  * **Stick precision quantises the evdev report and leaves both the vendor stream and the profile's
    curve bytes alone.** It sits on the XInput path, downstream of the pad's own resolution: a relay
    reading sticks off the vendor stream is unaffected by it, a game reading evdev is, and a curve
    editor does not have to know the pad's bitness.
    → [docs/device-settings.md](docs/device-settings.md)
  * **The sticks are in the vendor input report, at offsets 4, 6, 8 and 10** — signed 16-bit
    little-endian, left X/Y then right X/Y, `00 80` being −32768 and `ff 7f` +32767. `motion.parse`
    only ever took gyro (18) and accel (24) out of this report. The relays take sticks from evdev and
    so go blind when another driver switches `controller_data` off, while this report keeps carrying
    them on the `raw_data` side.
  * **M1-M6 are remap sources, not targets.** They have no XInput equivalent, so mapping a
    face button onto one makes it send nothing. `APEX5_KEYS` is the source list, 23 entries;
    `XINPUT_TARGETS` is what a remap may point at, 17. C and Z are Vader keys and are not on this
    pad. Space Station rebinds neither Fn, Turbo nor Home, but that is its policy and not the
    firmware's: a Home remap works in both directions on hardware, so this project offers it and
    leaves the other two alone. → [docs/findings-other-devices.md](docs/findings-other-devices.md)
  * **The decompiled source is reliable for layout, not for meaning.** Offsets, field order, sizes
    and stride taken from `MappingConfigParser` have matched this pad; declared semantics, defaults
    and capabilities have not, and were settled on the device.

## Ruled out

  * **Keyboard and mouse remapping is not a pad feature on any of them.** `KeyMapType.Keyboard` and
    `MultiFunction` both serialise to the single byte `254`, with no key code anywhere in the blob.
    The injection is host-side, in `KeyboardMouseInjectRunner.cs`. Same for `MotionMapType.Mouse`.
    On Linux that is a uinput daemon, and a different project from configuring a pad.
  * **`EnableDS5Data` (232) is dead code** — DInput builder only, no callers anywhere in
    `SpaceStationService`. It looks like it would replace the whole virtual-DualSense tier. It would
    not.
  * **Usage counters and `DeviceMask`** — XInput and DInput builders only, no NewXInput path, so
    they are unreachable in the mode this project uses.
  * **`TestRecoverFactoryCommand` (253)** is a factory reset with no confirmation flow. Do not send it.
  * **Firmware update is deliberately not implemented, and command 31 must not be aimed at a program
    chip.** Command 31 is `SwitchToFirmwareUpgradeMode` / `SwitchUsb` — `[4]=3`, `[5]=chipModule`,
    `[6]=crc` — and puts one named chip into upgrade mode. This project does send it, for the screen
    chip only, as step 2 of a picture upload (`flydigi/screen_ota.py`), and `enter_upgrade_mode()`
    takes no chip argument so it cannot address another. Aimed at a program chip it is a one-way
    door: four bootloader vendors, a dozen independently flashable chips, no flashing protocol
    decompiled for any of them, and no recovery.
    → [docs/findings-other-devices.md](docs/findings-other-devices.md)

## Runbook

Everything gitignored is reproducible: `tools/fetch-configs --monitor-configs --all-mods` restores
`gamelist.json`, `configs/` and `mods/`. The decompile toolchain lives in the `wine-arch` distrobox;
`decompiled/` is only needed for new protocol work, not to run anything.

Tests, cheapest first. The fourteen backend tests import no Qt. The three Qt tests skip with exit 0
when PySide6 is absent, so the backend run stays dependency-free:

```bash
for t in tests/test_{device,dsmode,dsx,forza,games,identity,macros,mapping,monitor,prefs,relay,screen,screen_ota,settings}.py; do python3 "$t"; done
distrobox enter apex-dev -- bash -lc 'cd ~/Projects/ApexExperiments && \
  python3 tests/test_models.py && python3 tests/test_shell.py && python3 tests/test_qml.py && \
  tools/generate-qmltypes && \
  qmllint-qt6 -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml && \
  reuse lint'
```

`tests/fake_pad.py` answers reads, diffed writes, apply and save, models switch-on-read, and refuses
a bad checksum by staying silent exactly as the pad does.

## Environment

- Host: Aurora DX (nvidia-open), Fedora 44 atomic, KDE/Wayland
- `apex-dev` distrobox, package list in [gui/README.md](gui/README.md). **The desktop app runs here,
  not on the host.** Created with
  `distrobox create --name apex-dev --image registry.fedoraproject.org/fedora-toolbox:44`
- `wine-arch` distrobox (Arch + wine-staging 11.14, winetricks, innoextract, dotnet-sdk 10,
  ilspycmd, sfextract, nodejs). Created with `distrobox create --name wine-arch --image archlinux:latest --nvidia`
- **`sfextract` needs `DOTNET_ROLL_FORWARD=Major`** in that box. It targets .NET 8 and the box has
  10, so it fails with "To install missing framework" and extracts nothing — which reads as a broken
  bundle rather than a missing runtime. Unpacking `FirmwareConsole.exe` (194 files, the whole screen
  upgrade path) is one line:

  ```bash
  DOTNET_ROLL_FORWARD=Major ~/.dotnet/tools/sfextract "<prefix>/firmware/FirmwareConsole.exe" -o work/firmware-console
  ~/.dotnet/tools/ilspycmd -o decompiled/FirmwareLibrary work/firmware-console/FirmwareLibrary.dll
  ```
- Wine prefix: `~/.local/share/wineprefixes/flydigi` — Space Station 4.2.1.4 installs and runs
  (UI connects to its service over the named pipe), but **does not detect the controller**
  under Wine. Not needed; kept for reference only.
- Controller: wired. Two hidraw nodes — a keyboard/mouse composite and the vendor command interface.
  The numbers move on every reconnect, so find the vendor node by the report-descriptor prefix
  `06 a0 ff`, never by path. Nodes are `0666`, no udev rule needed **for HID** — the screen's
  bootloader tty is the exception and lands `root:dialout`, which is why the rules are no longer
  optional here.

## Repo contents

| Path | What |
|---|---|
| `PROTOCOL.md` | Full wire protocol + hardware verification results |
| `flydigi/` | Library — `device.py` (transport), `identity.py` (the command-1 device-type guard), `blobs.py` (packetised config transfer), `effects.py` (live trigger commands), `mapping.py` (profiles, remapping, macros, vibration, stored triggers), `macros.py` (recording one off the pad's evdev node), `lighting.py` (RGB), `screen.py` (160x80 screen: LVGL image format, settings, and the HID upload that puts no picture on this pad), `screen_ota.py` (the serial upload that works), `settings.py` (the pad's own settings: command 3 and the small writes behind it), `games.py`, `forza.py`, `evdev.py` (the xpad evdev reader every relay's input comes from), `ds5.py` (DualSense report codec), `dsx.py` (DSX UDP protocol), `monitor.py` (process-memory engine), `motion.py` (battery, gyro/accel and the third-party toggle), `relay.py` (Apex 5 → DualSense translation, and `PadLink`: holding a pad that leaves the bus every time it sleeps) |
| `gui/` | PySide6/QML desktop app (GPL-3.0-or-later) — `app.py` (the object graph), `main.py` (entry point), `worker.py` (all device I/O, on its own thread), `i18n.py` (the `i18n*()` shim the engine needs; without it every Kirigami form delegate throws `ReferenceError: i18ndc is not defined`, so `main.py` installs it unconditionally), `models/` (view-agnostic state; `screen.py` is the one that touches QtGui, for image decoding), `qml/` (`Main.qml`, `pages/`, `components/`) |
| `tools/flydigi-mapping` | CLI for profiles — list/show/set/clear/rename/apply/backup/restore, plus `macros`, `macro-record`, `macro-set`, `macro-clear` |
| `tools/flydigi-forza` | Forza driver — UDP 5300 → rules → triggers (`--port`, `--config` for a rule file other than `configs/forza.json`, `--dump` for telemetry only, `--quiet`; `--accept LEN:OFFSET`, e.g. `--accept 331:12`, for a newer Forza shipping an unknown packet size) |
| `tools/flydigi-dsx` | DSX protocol listener on UDP 7878 — drives triggers from any DSX-compatible mod (`--dump` to decode only, `--forward PORT` to relay datagrams onward; Flydigi uses 8787) |
| `tools/flydigi-monitor` | Memory-reading driver using Flydigi's XGameMonitor configs (`--probe` to debug offsets) |
| `tools/flydigi-settings` | The pad's own settings — `show`, then one subcommand per setting. Every write but `restart` ends in a read-back, because a command-19 ack cannot say which setting it belonged to. `report-rate` and `xbox-home` need `--i-know`: the pad reports a rate of 0, which is not in Flydigi's map, and already polls at the 1 ms endpoint ceiling, and `xbox-home` is unmeasured |
| `tools/flydigi-screen` | The screen — `check`/`preview`/`convert` need no pad; then `status`, `probe` (does this pad know the upload commands?), `test`, `show`, `animate`, `send`, `on`/`off`, `statusbar`. Sending goes over the serial route by default (`--via hid` is for other models, and puts no picture on this one) |
| `flydigi/uhid.py` | Pure-Python `/dev/uhid` binding (no dependencies) — creates kernel-side HID devices |
| `flydigi/usbip.py` | Pure-Python USB device served to this machine's own kernel via `vhci-hcd` — no dependencies, no `usbip` tool |
| `flydigi/ds5_usbip.py` | The DualSense on top of it: descriptors, feature reports, endpoints, the haptic stream |
| `flydigi/ds5_usb.py` | Generated DualSense descriptors + feature blobs, captured off hardware |
| `flydigi/dsmode.py` | DualSense mode as a switch: the module, what counts as running, start/stop, and giving root back |
| `flydigi/haptics.py` | Haptic audio → motor levels: channel energy, the frequency split, the shaping |
| `tools/flydigi-ds5` | Tier 4 — the uhid relay, for a machine with no `vhci-hcd` |
| `tools/flydigi-haptics` | The original bridge, sampling a *real* DualSense's audio. Superseded as a delivery mechanism; the DSP it proved is what tier 4b feeds |
| `tools/gen_ds5_usb.py` | Regenerates the above from a connected DualSense. Scrubs the Bluetooth addresses in report `0x09` **and the hardware address in `0x0B`**, unconditionally. Report `0x05`, this unit's IMU calibration, is kept deliberately — per-unit but not an identifier |
| `tools/flydigi-ds5-usbip` | Tier 4b — the relay, with `--haptics` and `--motors` |
| `tools/ds5-dump-features` | Re-reads a real DualSense and diffs it against what this project serves |
| `tests/` | `test_device.py`, `test_dsmode.py`, `test_dsx.py`, `test_forza.py`, `test_games.py`, `test_identity.py`, `test_macros.py`, `test_mapping.py`, `test_monitor.py`, `test_prefs.py`, `test_relay.py`, `test_screen.py`, `test_screen_ota.py`, `test_settings.py` need no Qt; `test_models.py` needs PySide6, and `test_shell.py` and `test_qml.py` with `qml_harness.py` and `qml/tst_*.qml` need Kirigami as well, so those two run inside `apex-dev` — all pass without hardware, each printing its own count |
| `tests/fake_pad.py` | Stand-in controller: multi-packet reads, diffed writes, apply, save, checksum rejection |
| `tools/forza-simulate` | Synthetic telemetry generator, for testing without the game |
| `configs/forza.json` | Flydigi's own 15-rule Forza config, reused verbatim |
| `tools/flydigid` | Polling daemon — auto-detects a running game and applies its config |
| `tools/apex5-setup` | Setup checklist: udev rules, the daemon's unit, start at login, menu entry |
| `tools/flydigi-auto` | Per-game auto mode and route — `list`, `on`, `off`, `reset`, `route` |
| `flydigi/setup.py` | What the two above share: checks, unit generation, escalation. The unit and the menu entry are generated with this checkout's path in them, and `unit_installed()` is a byte comparison against what the checkout would write now, so moving the repository makes Setup report the daemon unit as out of date until it is installed again |
| `flydigi/prefs.py` | Per-game preferences in `$XDG_CONFIG_HOME/flydigi/games.json`, falling back to `~/.config`. Keyed by the gamelist's `id`, which is unique across all 94 entries where names are not, and rewritten atomically because the daemon re-reads it while the app is editing it |
| `tools/flydigi-run` | Steam launch wrapper — `flydigi-run "<name>" -- %command%` |
| `tools/fetch-configs` | Restores everything gitignored — `gamelist.json`, `configs/`, `mods/` |
| `tools/hid_probe.py` | Passive HID descriptor dump (writes nothing) |
| `tools/ds5-channel-probe` | Plays a tone into one DualSense audio channel at a time, to map channel index to actuator or speaker (needs `pactl` and `paplay`) |
| `tools/gyro-probe` | Vendor-stream IMU check — gyro and accel, live |
| `tools/trigger-stroke-probe` | Which trigger-travel bytes the pad plays: a degenerate window on one trigger, the other left as an in-run control |
| `tools/haptics-inspect`, `tools/haptics-simulate`, `tools/joystick-curve-probe`, `tools/stick-feel` | The remaining bench probes: per-channel haptic energy (needs `pactl` and `parec`), synthetic haptic playback (`paplay` or `pw-play`), stick-curve capture, stick feel |
| `tools/generate-qmltypes` | Regenerates the `Apex5` module's qmltypes from the live `QMetaObject`s (needs `qmltyperegistrar`) |
| `tools/flydigi_cmd.py` | Manual command tool — `info`, `listen`, all six effects (`normal`, `race`, `sniper`, `recoil`, `lock`, `vibrate`), `bind`, `rumble`, `game`, `raw`, plus `k6*` for the trigger family belonging to an Apex 6, which has not shipped ([device codes](docs/findings-other-devices.md)) |
| `udev/72-flydigi-apex5.rules` | The udev rules `tools/apex5-setup install-rules` copies into `/etc` |
| `pipewire/99-dualsense-haptics.conf` | The fake 4-channel DualSense sink of the null-sink experiment. Nothing installs it; kept for the negative result — [docs/findings-haptics.md](docs/findings-haptics.md) |
| `LICENSE`, `LICENSES/`, `NOTICE`, `REUSE.toml`, `pyproject.toml` | Licensing and build metadata; `NOTICE` carries the inputtino attribution for `flydigi/ds5.py`'s report layouts and `flydigi/ds5_usb.py`'s placeholder Bluetooth addresses |
| `gamelist.json` | All 94 games + per-game configs (from the public API) |
| `mods/` | All 46 downloadable mod zips (44 MB) |
| `bundle/` | 248 .NET assemblies (plus `deps.json` / `runtimeconfig.json`) extracted from `SpaceStationService.exe` |
| `decompiled/` | C# source for AdapterTrigger(Service), ControllerSdk, Hid, Basic, SharedResources, SpaceStationService, the two mods — ForzaDualSense and XGameMonitor — plus FirmwareConsole/FirmwareLibrary from the screen upgrade path |
| `asar/` | Extracted Electron app — `.vite/build/main.pretty.js` is the beautified main process, `.vite/renderer/main_window/assets/index-DM6mSbRo.js` the renderer bundle that holds the stick-curve compiler |

## Where the detail lives

| Document | What is in it |
|---|---|
| [PROTOCOL.md](PROTOCOL.md) | The wire protocol, and what is hardware-verified |
| [docs/findings-profile-blob.md](docs/findings-profile-blob.md) | The 840-byte profile: layout, factory defaults, the key table, sticks, gyro, the trigger blocks, macros |
| [docs/device-settings.md](docs/device-settings.md) | Command 3, the command-19 sub-ids, battery, the vibration bind, the LED config blob, the command inventory |
| [docs/findings-screen.md](docs/findings-screen.md) | Uploading a picture, the two screen settings, command 242, and command 31 for the screen chip |
| [docs/findings-steam.md](docs/findings-steam.md) | Locking the hidraw node, the third-party takeover toggle, why Steam still misnames the pad after a reconnect, and SDL's own driver |
| [docs/findings-games.md](docs/findings-games.md) | Game detection, routes, and the per-game validation notes |
| [docs/findings-haptics.md](docs/findings-haptics.md) | Tiers 4 and 4b: haptic audio, the USB gadget question, the USB/IP relay, M1-M6 |
| [docs/findings-other-devices.md](docs/findings-other-devices.md) | **What `k5`/`k6`/`f4` mean**, the Vader 4, the charging dock, and firmware update |
| [docs/findings-desktop-app.md](docs/findings-desktop-app.md) | Apply vs save, model registration and qmllint, the QML testing traps, placeholders and visibility |
| [gui/README.md](gui/README.md) | Working in `gui/`: import direction, toolkit, runtime, layout, the QML shell, models, tests, static checking |
| [docs/third-party-mods.md](docs/third-party-mods.md) | Tier 5, and why it is not shipped |
