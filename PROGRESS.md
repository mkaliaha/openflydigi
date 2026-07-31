# Apex 5 on Linux — project state

Replacing Flydigi's Windows-only Space Station on Linux: a zero-dependency Python backend, a
Kirigami desktop app on top of it, and five ways of getting adaptive-trigger effects into a game.
Not just triggers — a full replacement, covering what Steam Input and input-remapper cannot do. The
library/CLI split exists so a GUI can sit on top without rework.

This file is the index — status, what is next, and the facts that cost the most to learn. The wire
protocol is [PROTOCOL.md](PROTOCOL.md); the long write-up behind each finding is in
[docs/](docs/), listed under [Where the detail lives](#where-the-detail-lives).

## Where things stand

Adaptive triggers are done and validated in real games. The desktop app covers profiles, button
remapping, macros, sticks, vibration, per-profile trigger effects, lighting, the screen, the game
list and its own setup. The daemon detects a running game and applies its route unattended.

**Left to build, in size order: the device-settings page and the charging dock** — plus the
device-type guard, which is a safety item rather than polish, and the smaller entries in What's
next.

| Tier | Mechanism | Games | Validated in |
|---|---|---|---|
| 1. Vibration bind | cmd 82 `SyncWithGrip`, driven by game rumble | 33 | Death Stranding 2 |
| 2. Forza telemetry | Data Out UDP 5300 → rule engine → cmd 81 | 4 | Forza Horizon 6 |
| 2b. DSX listener | UDP 7878, any DSX-compatible mod | mod ecosystem | self-tested (`tests/test_dsx.py`) |
| 3. XGameMonitor | reads game process memory | 31 | Dark Souls: Remastered |
| 4. Virtual DualSense | uhid DS5, effects translated | **any DS5-aware game** | Deathloop |
| 4b. …over USB | usbip + vhci DS5, **plus haptic audio** | **any DS5-aware game** | Deathloop |
| 5. Third-party mods | game-side mods speaking DSX | 11 | works via 2b; not supported |

Tier 4b is what the app's **DualSense** switch turns on, and it supersedes tier 4 wherever both
work: same input, same triggers, and a game's PS5 haptics reach the pad's motors as well. Tier 4
remains for a machine with no `vhci-hcd`.

Long-form state per tier, and the notes from each validating game, are in
[docs/findings-games.md](docs/findings-games.md).

The backend is **pure Python with zero dependencies** — a feature worth defending, since it means
`flydigi-ds5` runs on any machine with Python 3.9 and no Qt. Licensing is per-file via REUSE: MIT
backend, CC0 protocol docs and system config, GPL-3.0-or-later for `gui/` only. `LICENSE` explains
why and `gui/README.md` states the rule that keeps it true (`gui/` may import `flydigi/`, never the
reverse). Verify with `reuse lint`. Nothing Flydigi-owned is committed; `tools/fetch-configs`
restores it.

## What's next

Roughly in order of value. Each is a fresh-context-sized piece of work.

 0. **Third-party mode: optional polish, lowest value here.** Our command 17 is byte-identical to
    Space Station's, so there is nothing to catch up on. The oddity is Steam's: after a reconnect
    with the flag already on, the pad is not *labelled* Apex 5, because the native driver wins the
    race for Steam's synthetic serial and inherits the xpad entry's config set. **Everything still
    works in that state** — native HIDAPI driver attached, full button set including paddles, SDL
    holding the pad, adaptive triggers, profiles, lighting, curves and motion all live. Cosmetic,
    plus a bindings-storage nuisance. Optional workaround, which neither app does: re-assert the flag
    on connect, off then on once SDL has enumerated. The real fixes are upstream.
    → [docs/findings-steam.md](docs/findings-steam.md)
 1. **A device-settings page.** Command 3 returns the whole block in one read — supported *and*
    enabled bits, sleep time, report rate, stick precision and sensitivity. **Do quick-switch
    (sub-id 1) first**: picking a profile with `FN + A/B/X/Y` is the one thing here a Linux user
    cannot get any other way. Then sleep time (cmd 23) — the pad ships at 15 minutes and dropping
    off the bus mid-session has interrupted nearly every test.
    → [docs/device-settings.md](docs/device-settings.md)
 2. **Stroke Setting on the Triggers page.** 195/215 are `Param[0]` of the force-trigger blocks and
    the General effect's two parameters *are* the stroke window. The page's current "Dead zone"
    writes the curve block at 123, which on an Apex 5 Space Station never shows — so it is very
    likely writing where this pad does not read. One piece of work: add the start/end pair, then
    verify the dead zone by feel or drop it. → [docs/findings-profile-blob.md](docs/findings-profile-blob.md)
 3. **Persist the vibration bind.** The stored form is settled — same structure as live command 82
    with one spare byte — so a per-game preset can survive a sleep instead of being re-applied.
    Today the Games page applies it with live 82, which the pad forgets.
    → [docs/device-settings.md](docs/device-settings.md)
 4. **Gyro mapped to a stick (J2).** Offset 137, 8 bytes, smoothing curve at 830. Works in any game
    with nothing running, which on Linux is otherwise Steam Input only.
 5. **The charging dock.** The gen-2 dock is on the desk; blocked only on decompiling
    `Flydigi.ChargerSdk.dll` / `Flydigi.CoolerSdk.dll`. It is a *lighting* problem, not a screen
    one — 162 addressable LEDs over the ordinary config path, and `cd2_led_sync` keeps it in step
    with the pad. → [docs/findings-other-devices.md](docs/findings-other-devices.md)
 6. **Multiple pads.** A Vader 4 Pro is on the desk. The prerequisite is the device-type guard:
    `flydigi/device.py` matches on vendor id plus report-descriptor prefix today, neither of which
    tells the models apart, so it would happily write an Apex 5 config to a Vader.
 7. **An interactive crop for the Screen page.** Everything else there is done.

**Cheap experiments, each one sitting.** The `center`/`edge` sign encoding — write 236 to byte 110
with apply-and-no-save, sweep the stick, watch evdev; the three outcomes are unmistakable. Whether
stick precision rescales the profile's curve bytes — write a different precision, re-read a profile.
Whether the firmware accepts 164/165 aimed at a slot it is not running.

**Haptic audio: working.** A game writes haptics to a virtual DualSense, and the Apex 5's motors
reproduce them. Nothing else does this -- every other project either uses real hardware or emulates
HID only.

Proton joins an audio endpoint to a gamepad by a ContainerId that `winepulse` and `winebus` each
derive independently from the same `usb_device`, so only genuine USB topology matches; uhid gets a
random GUID, re-rolled per run, and never can. Every soft-UDC shortcut to that topology is a dead
end -- `dummy_hcd` declares no isochronous endpoint, `usbip-vudc` fails iso with `-EXDEV`, and
FunctionFS cannot express UAC descriptors at all -- so which distros ship `usb_f_uac1` turns out not
to matter. What works is the USB/IP *client*: `flydigi/usbip.py` serves a device from userspace and
`vhci-hcd` enumerates it locally. Measured against Deathloop with `tools/flydigi-ds5-usbip
--haptics --motors`. → [docs/findings-haptics.md](docs/findings-haptics.md)

**DS mode is a switch, not a route — and it is built.** Tiers 1-3 need per-game data -- vibration
binds, telemetry rules, memory offsets -- which is why the gamelist exists and why the daemon picks
a route per game. The DualSense tiers need none of it: they present a DualSense, and *any* DS5-aware
game gets it, including games Flydigi has never heard of. Treating it as one of nine per-game routes
was Flydigi's model leaking into a tier that does not share its constraints.

So `isPS5` is no longer a route in `flydigi/prefs.py`, the daemon never starts this tier, and the
app has a **DualSense** section: the mode switch, plus a haptics-to-motors switch that is read once at start and so has to be set *before* DS mode is turned on. `flydigi/dsmode.py` is what the switch and the CLI
share.

**DS mode requires third-party mode off**, and the two switches are in the same app. Both relays take
sticks and buttons from evdev, and the third-party toggle hands the pad to another driver which
switches `controller_data` off — the report the evdev node is built from. Motion keeps arriving on
the vendor stream, so the symptom is a DualSense that tilts with dead sticks and buttons rather than
an obvious dead source; the relay's `evdev=` counter sits at 0 while `motion=` climbs.
→ [docs/findings-steam.md](docs/findings-steam.md)

**Three pages are shut off while another driver holds the pad** — **Buttons**, **Macros** and the
**DualSense** switch — which is now enforced rather than merely warned about. The key-related ones,
in other words; everything else stays live, because triggers, lighting, sticks and the screen all
keep working in that state and blocking them would invent a restriction the hardware does not have.

What is measured is the recording half: with `third_party` on and SDL holding the pad, a 60-second
capture of the evdev node returned nothing at all, and the same capture with it off caught
everything — so a recording made in that state is silently empty, and that cost two test windows
here before it was noticed. The Buttons page is shut off on the same principle rather than on a
measurement of its own: whether the pad still applies its key table under another driver has not
been tested either way, and Space Station is unusable in that state too.

**The privilege model, as built.** The attach is the only privileged step, so the relay is started
through pkexec, does the module load and the attach as root, and then `setuid`s back to the invoking
user before it opens a device or starts a thread. What runs for the length of a play session is an
ordinary user process; stopping it is a plain SIGTERM, and the vhci port frees itself when the
socket closes. This is the intent of the earlier note about a socket-passing helper, reached without
one: `SCM_RIGHTS` does not survive `host-spawn`, so a helper would have failed exactly where the app
runs today -- in the `apex-dev` distrobox.

**Known, and not fixable from here:** with DS mode on, a game sees *both* the Apex 5 and the virtual
DualSense. Nothing can hide the physical pad from a game that enumerates it, so the instruction is
part of the feature: launch with `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501`, or set it
globally in Steam.

**Turn it on before starting the game.** A game opens its stream to the controller's audio device
once, at launch. Switching DS mode on mid-game gives it a pad it will use and an audio endpoint it
will never look for again, so triggers work and haptics stay silent until the game is restarted.

**Not ours to fix.** Steam lists the pad twice (xpad *and* its own hidapi path — reported on Windows
too), which upstream SDL does deliberately, and Steam takes no lock on the hidraw node, so its writes
can land between ours. Harmless for effects, which the next frame overwrites. Also Steam's: after a
reconnect the pad loses the "Apex 5" label while remaining fully functional on the native driver.
→ [docs/findings-steam.md](docs/findings-steam.md)

## What's done

All command factories are decompiled under `decompiled/Flydigi.ControllerSdk/`.

| Feature | Commands | Where |
|---|---|---|
| Mapping profiles | status 161, apply 162, read 163, write 164/165, save 166 | `flydigi/mapping.py`, `tools/flydigi-mapping`, GUI |
| Buttons, sticks, vibration, stored triggers | inside the 840-byte profile blob | same module — [detail](docs/findings-profile-blob.md) |
| Macros, played by the pad | the profile's macro page at 230, plus 162 to make one live | `flydigi/mapping.py`, `flydigi/macros.py` (the recorder), GUI — [detail](docs/findings-profile-blob.md) |
| Live trigger effects, all six | 81, 82 | `flydigi/effects.py` — [PROTOCOL.md](PROTOCOL.md) §3a |
| RGB lighting | read 167, write 168/169 | `flydigi/lighting.py`, GUI |
| The screen | 31 + UART OTA over CDC; `TestScreen` 242; 19/9 and 19/8 | `flydigi/screen_ota.py` — [detail](docs/findings-screen.md) |
| Arbitration between our own writers | advisory `flock` on the node | `Controller.claim()` — [detail](docs/findings-steam.md) |
| Third-party takeover toggle | read 16, write 17 | `flydigi/motion.py`, GUI — [detail](docs/findings-steam.md) |
| Battery, gyro, accel | 1, and the vendor input stream | `flydigi/motion.py` |
| Per-game auto mode | — | `tools/flydigid`, `tools/flydigi-auto` — [detail](docs/findings-games.md) |
| Virtual DualSense (tier 4) | — | `flydigi/uhid.py`, `tools/flydigi-ds5` — [detail](docs/findings-haptics.md) |
| Virtual DualSense over USB (tier 4b) | — | `flydigi/usbip.py`, `tools/flydigi-ds5-usbip` — adds haptic audio |
| DualSense mode as one switch for the whole system, not a per-game route | — | `flydigi/dsmode.py`, the app's DualSense page |

## The desktop app

**QML on Kirigami, in `gui/`**, calling the backend in-process — no D-Bus.

```bash
sudo dnf install python3-pyside6 kf6-kirigami kf6-kirigami-addons kf6-qqc2-desktop-style
python3 -m gui
```

| Tab | What works |
|---|---|
| Buttons | remap, turbo + hold/toggle, reset all to default |
| Macros | record a sequence off the pad and bind it to any key, pick once / while held / toggle, set the repeat gap, see every step, delete |
| Vibration | master switch, per-grip enable, min/max window, strength |
| Controller → Selected profile / Other software | rename the open profile, back up / restore it to file; let Steam and similar take the pad over, and who currently holds it |
| Sticks | dead zone, outer dead zone, sensitivity curve presets, circular range |
| Triggers | stored effect — all six of Flydigi's, each with its own controls — plus a dead zone that writes the curve block at 123 and is not confirmed to reach this pad |
| Games | all 94 games, searchable, filtered by route; vibration presets load onto the pad from here; per-game **Auto** toggle, a route picker where a game really has a choice, and a DualSense marker on the 23 games Flydigi lists as DS5-aware |
| DualSense | the tier-4b switch: vhci-hcd's state, haptic audio to the motors, what the relay is doing, and the launch option to copy |
| Setup | the daemon's unit, "running now" and "start at login" as separate switches, the application-menu entry, and the udev rules behind one authentication prompt |
| Lighting | effect, up to 5 colours, brightness, cycle time, react-to-rumble |
| Screen | pick a picture or GIF, choose how it fits, preview the encoded frame, and send it over the serial link — with the frame count and a time estimate before you start; plus the always-on display and the status bar |

**Everything device-facing runs on the worker thread** (`gui/worker.py`) and requests cross as
signals. Calling a worker slot directly runs it on the caller's thread, which silently puts blocking
HID traffic back on the UI thread — that bug has been written twice already.

**Apply vs save**: "Apply" writes 164/165 and takes effect immediately; "Apply and save" also sends
166. An applied-but-unsaved change is lost when the pad *sleeps*, not merely on a power cycle — so
apply is working memory in the literal sense. The full argument, the hardware proof of 166, and the
QML testing traps are in [docs/findings-desktop-app.md](docs/findings-desktop-app.md); how to work
in `gui/` is [gui/README.md](gui/README.md).

## Hard-won facts worth not rediscovering

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
  * **Effects persist in controller state** until changed; there is no timeout.
  * **Trigger effects 2 and 3 are named the other way round by Flydigi's own UI.** The SDK enum
    says `Sniper=2, Recoil=3`; Space Station's picker shows mode 2 as "Recoil" (zh 机枪, machine
    gun) and mode 3 as "Sniper" (狙击), and the behaviour follows the label — 2 rattles, 3 breaks
    through. Code and wire here use the enum name, the UI uses theirs, so that advice given for one
    application lands on the same effect in the other.
  * **`bindType` is always 2.** Every `SyncWithGrip` Flydigi constructs passes 2, and all 34
    vibration games in the gamelist carry `vibType: 2`. Sending 0 is not a quieter bind, it appears
    to be no bind: with it set, neither `Normal` nor mode 5 produced anything under rumble.
  * **A config apply does not restore live trigger state.** Bind and effect state set by 81/82
    survive `apply_config`, so "I re-applied the profile" does not undo an experiment. Set it back
    explicitly, with `bindType 2`.
  * **`Sniper` (2) and `Vibration` (5) send byte-identical parameters — the mode byte is the only
    difference.** Mode 2 vibrates on press with the bind suppressed and is settled. Mode 5 is not:
    see PROTOCOL.md §3a. Note that mode 5 ACKs and visibly seats the triggers either way, so "the
    pad took it" is not evidence that it does anything.
  * **hidraw replies go to every reader of the node.** An ACK you receive is not necessarily an
    answer to anything you sent — hence `Controller.claim()` and the drain before each write.
  * **`flock` attaches to the open file description, not the fd or the process.** A `dup`'d handle
    is the same lock holder and is granted the lock unconditionally; two `open()`s of one path are
    not. This makes a lock test easy to write so that it passes while proving nothing.
  * **Steam holds `/dev/hidraw4` open the whole time it runs** and takes no lock on it, so it is
    unaffected by ours — which is wanted, since the vendor interface works with Steam Input on.
  * **The pad discards unsaved config when it sleeps.** Not just on a power cycle — idling out is
    enough, observed with lighting. Applying is working memory; command 166 is what makes it last.
  * **`effects.rumble()` must use `wait=0`** when driven continuously, or the 100 ms ACK wait puts
    the motors far behind.
  * **A command answering is not a command working.** The screen's picture family (208..211) parses
    every packet on an Apex 5 and echoes the fields back, and nothing appears on the panel; command
    242 ACKs `off` and stays lit; command 245 ACKs and is ignored. On this pad an ACK means the
    firmware understood the *shape* of what you sent. Only the hardware says whether it did it.
  * **`Controller.send` takes an `until` predicate**, and without one it always burns its full
    timeout. Right when a reply may arrive in several packets and no caller can say how many; wrong
    for a long stream of one-for-one exchanges, where it turned a two-second upload into nine
    minutes. Any fake that stands in for a Controller has to accept the keyword.
  * **Do not restate a backend constant in the GUI.** A `BATTERY_STEPS = 8` beside
    `motion.MAX_LEVEL = 5` draws a full pad as five-eighths. Two sources of truth is the defect;
    the wrong digit is only the symptom.
  * **Qt reads animated GIFs and cannot write them.** No `gif` in
    `QImageWriter.supportedImageFormats()` at all, and multi-page tiff and webp both write happily
    and then read back as a single frame. An animation for a test has to be committed, not generated.
  * **Do not send anything slow through the worker's `_attempt`.** It retries once, which is right
    for a sulking pad and wrong for a screen upload: that runs for minutes and has already switched
    the pad into upgrade mode, so a silent second attempt is not a retry anyone asked for.
  * **Steam Input must be off** for Tier 4 — it masks the pad and breaks DualSense semantics.
  * **A sleeping Apex 5 leaves the USB bus.** It does not go quiet on HID — it disconnects, wired
    included: `usb 3-4: USB disconnect, device number 27` with no matching connect, no `37d7:2501`
    in `lsusb`, no hidraw node. So "the pad is asleep" and "the cable is dead" are the same symptom
    at this level, and `find_device` raises `DeviceNotFound` rather than any read timing out.
    Pressing a button re-enumerates it, which is why node numbers change on reconnect — resolve by
    name/descriptor, never by path.
  * **A macro is stored by the write and played by the apply.** The key table takes effect as the
    packet lands, but the macro page is parsed into the firmware's own structs when a profile is
    *loaded* — so macros written and not applied sit on the pad and do nothing. Measured in three
    steps: four macros written and read back off the pad produced nothing for a whole test window
    while an ordinary remap beside them worked; after a 162 they played to the millisecond; and a
    fifth macro written and applied with **no save at all** played too, which is what isolates the
    apply from the commit. Saving only decides whether they survive a sleep.
    `MappingConfig.macro_page` is what a writer compares to know whether it owes an apply, since
    applying makes the pad audibly re-seat its trigger motors.
  * **The macro page and the key table are read independently, and both fire.** A key whose table
    entry was changed to a plain remap goes on playing the macro body left behind at 230 — and sends
    the remap as well. Measured with the two set to different keys, which is the only way to see it:
    M1 remapped to A with an orphaned body of three X taps produced `press a`, then `x x x`, then
    `release a` when the paddle came up. With the same key on both they coalesce and look like the
    macro alone. So removing the body is part of remapping the key — `set_mapping` does it, as
    Flydigi's own repository does; without it a key you have "remapped" keeps firing its old macro
    underneath the new binding.
  * **Reading a mapping config switches the pad to it.** The firmware pages it in as the live one,
    audibly re-seating the trigger motors — that noise is the tell. Confirmed: after reading config
    2, `read_status` reports 2 as active. The desktop app leans on this rather than fighting it:
    opening a profile is how you switch to it, as Space Station does, so the profile on screen is
    always the one running. That also keeps saving correct, since command 166 commits whichever
    profile is live. `read_config_preserving` restores the previous slot instead, for a caller that
    genuinely wants to peek; prefer command **161**, which reports the active slot and a version id
    per slot with no side effect at all.
  * **The config commands are checksummed and the trigger-effect commands are not.** A mapping or
    lighting packet with a bad checksum gets no reply — the pad stays silent rather than erroring.
  * **Lighting effects are frame data, not a mode byte.** The pad has no animation generator; it
    plays the stored frames. Space Station computes them from (mode, colours) and uploads them, so
    writing a different mode number alone changes nothing visible.
  * **Frame geometry is not 16 x 10** despite what `LedConfigParser` walks — that is the older
    490-byte layout. An Apex 5 returns 380 bytes = 10 frames x 12 LEDs. Derive it from the blob.
  * **M1–M4 and C/Z are remap sources, not targets.** They have no XInput equivalent, so mapping a
    face button onto one makes it send nothing. `APEX5_KEYS` is the source list, `XINPUT_TARGETS`
    is what a remap may point at.
  * **A configfs binary attribute can store less than you wrote, and say nothing.** Writing the
    DualSense's 289-byte HID report descriptor to `functions/hid.usb0/report_desc` stored 151 bytes;
    the gadget then bound, enumerated and described itself as something else entirely. The write
    method was not at fault — four different hex-to-binary methods all produce 289 identical bytes
    off-device. So anything writing one reads it back and compares the length before trusting it. Any
    write of a NUL-laden blob through a shell deserves the same treatment: verify, do not assume.
  * **Never combine a `pkill -f` with the relaunch in one shell command** — the pattern matches the
    shell running it and kills the session (exit 144). Two separate commands, and the `'[p]attern'`
    bracket trick.

## Reading the decompile versus reading the device

**What a source survey settles, and what it does not.** Worth knowing before sending anything else
to read `decompiled/`. Structure has been reliable every single time: offsets, field order, sizes
and stride taken from `MappingConfigParser` have matched the hardware without exception, and the one
early discrepancy (report id `6` vs `0x03`) came from the HID descriptor, not the C#. Semantics and
defaults have not. Three examples, all found the same afternoon:

  * a survey listed command 21 as "joystick precision" without noting that `JoystickPrecision` is in
    **declaration order** — so the pad's `2` is 10-bit, not 12;
  * command 3 was documented down to the bit, but never run, so nothing knew that motion debounce
    and audio are *unsupported* on a k5 and their sub-ids are dead UI;
  * a reader deriving the factory stick curve from the Electron JS produced
    `[50, 63, 75, 88, 100, 113, 125, 138, 150]` via `Math.round`; the pad holds
    `[50, 62, 75, 87, 100, 112, 125, 137, 150]`. Truncation, not rounding — and that value would
    have become "reset to linear".

So: read the source for layout, read the *device* for meaning. A blob dump costs five seconds and
settles arguments no amount of decompiled C# can.

**On needing Windows USB capture:** probably not required. Every layout taken from the decompiled
source has been correct on hardware; the one discrepancy (report id `6` vs `0x03`) was resolved from
the HID report descriptor instead. Capture is a fallback for specific stuck points — most likely
screen-image encoding, where conversion may happen in the Electron layer before reaching HID, or
any undocumented command ordering.

## Ruled out, so nobody looks again

  * **Keyboard and mouse remapping is not a pad feature on any of them.** `KeyMapType.Keyboard` and
    `MultiFunction` both serialise to the single byte `254`, with no key code anywhere in the blob.
    The injection is host-side, in `KeyboardMouseInjectRunner.cs`. Same for `MotionMapType.Mouse`.
    On Linux that is a uinput daemon, and a different project from configuring a pad.
  * **`EnableDS5Data` (232) is dead code** — DInput builder only, no callers anywhere in
    `SpaceStationService`. It looks like it would replace our whole virtual-DualSense tier. It would
    not.
  * **Usage counters and `DeviceMask`** — XInput and DInput builders only, no NewXInput path, so
    they are unreachable in the mode we use.
  * **`TestRecoverFactoryCommand` (253)** is a factory reset with no confirmation flow. Do not send it.
  * **Firmware update — deliberately not implemented, and command 31 must never be sent** for
    anything but the screen chip. Four bootloader vendors, a dozen flashable images, no flashing
    protocol for any of them, and no way back. The full argument, and the one thing the screen
    upload does differently, are in
    [docs/findings-other-devices.md](docs/findings-other-devices.md).

## Runbook

Start by reading this file and [PROTOCOL.md](PROTOCOL.md). Everything gitignored is reproducible:
`tools/fetch-configs --monitor-configs --all-mods` restores `gamelist.json`, `configs/` and `mods/`.
The decompile toolchain lives in the `wine-arch` distrobox; `decompiled/` is only needed for new
protocol work, not to run anything.

Tests, cheapest first. Each skips with exit 0 when PySide6 is absent, so the backend run stays
dependency-free:

```bash
for t in tests/test_{device,dsmode,dsx,forza,games,macros,mapping,monitor,prefs,relay,screen,screen_ota}.py; do python3 "$t"; done
distrobox enter apex-dev -- bash -lc 'cd ~/Projects/ApexExperiments && \
  python3 tests/test_models.py && python3 tests/test_shell.py && python3 tests/test_qml.py'
tools/generate-qmltypes
qmllint-qt6 -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml
reuse lint
```

`tests/fake_pad.py` answers reads, diffed writes, apply and save, models switch-on-read, and refuses
a bad checksum by staying silent exactly as the pad does.

## Environment

- Host: Aurora DX (nvidia-open), Fedora 44 atomic, KDE/Wayland
- `apex-dev` distrobox (Fedora toolbox + python3-pyside6, kf6-kirigami, kf6-kirigami-addons,
  kf6-qqc2-desktop-style). **The desktop app runs here, not on the host** — see `gui/README.md`.
  Created with `distrobox create --name apex-dev --image registry.fedoraproject.org/fedora-toolbox:44`
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
- Controller: wired. `hidraw3` = keyboard/mouse composite, `hidraw4` = vendor command interface.
  Nodes are `0666`, no udev rule needed **for HID** — the screen's bootloader tty is the exception
  and lands `root:dialout`, which is why the rules are no longer optional here. The node number
  moves; find it by the report-descriptor prefix, not by name.

## Repo contents

| Path | What |
|---|---|
| `PROTOCOL.md` | Full wire protocol + hardware verification results |
| `flydigi/` | Library — `device.py` (transport), `blobs.py` (packetised config transfer), `effects.py` (live trigger commands), `mapping.py` (profiles, remapping, macros, vibration, stored triggers), `macros.py` (recording one off the pad's evdev node), `lighting.py` (RGB), `screen.py` (160×80 screen: LVGL image format, settings, and the HID upload that this pad ignores), `screen_ota.py` (the serial upload that works), `games.py`, `forza.py`, `evdev.py` (the xpad evdev reader every relay's input comes from), `ds5.py` (DualSense report codec), `dsx.py` (DSX UDP protocol), `monitor.py` (process-memory engine), `motion.py` (battery, gyro/accel and the third-party toggle), `relay.py` (Apex 5 → DualSense translation) |
| `gui/` | PySide6/QML desktop app (GPL-3.0-or-later) — `app.py` (the object graph), `main.py` (entry point), `worker.py` (all device I/O, on its own thread), `models/` (view-agnostic state; `screen.py` is the one that touches QtGui, for image decoding), `qml/` (`Main.qml`, `pages/`, `components/`) |
| `tools/flydigi-mapping` | CLI for profiles — list/show/set/clear/rename/apply/backup/restore, plus `macros`, `macro-record`, `macro-set`, `macro-clear` |
| `tools/flydigi-forza` | Forza driver — UDP 5300 → rules → triggers (`--dump` for telemetry only) |
| `tools/flydigi-dsx` | DSX protocol listener on UDP 7878 — drives triggers from any DSX-compatible mod |
| `tools/flydigi-monitor` | Memory-reading driver using Flydigi's XGameMonitor configs (`--probe` to debug offsets) |
| `tools/flydigi-screen` | The screen — `check`/`preview`/`convert` need no pad; then `status`, `test`, `show`, `animate`, `send`, `on`/`off`, `statusbar`. Sending goes over the serial route by default (`--via hid` is for other models, and inert here) |
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
| `tools/ds5-dump-features` | Re-reads a real DualSense and diffs it against what we serve |
| `tests/` | `test_device.py`, `test_dsmode.py`, `test_dsx.py`, `test_forza.py`, `test_games.py`, `test_macros.py`, `test_mapping.py`, `test_monitor.py`, `test_prefs.py`, `test_relay.py`, `test_screen.py`, `test_screen_ota.py` need no Qt; `test_models.py`, `test_shell.py`, `test_qml.py` need PySide6 — all pass without hardware, each printing its own count |
| `tests/fake_pad.py` | Stand-in controller: multi-packet reads, diffed writes, apply, save, checksum rejection |
| `tools/forza-simulate` | Synthetic telemetry generator, for testing without the game |
| `tests/test_forza.py` | Self-test for the parser and rule engine (no hardware needed) |
| `configs/forza.json` | Flydigi's own 15-rule Forza config, reused verbatim |
| `tools/flydigid` | Polling daemon — auto-detects a running game and applies its config |
| `tools/apex5-setup` | Setup checklist: udev rules, the daemon's unit, start at login, menu entry |
| `tools/flydigi-auto` | Per-game auto mode and route — `list`, `on`, `off`, `reset`, `route` |
| `flydigi/setup.py` | What the two above share: checks, unit generation, escalation |
| `flydigi/prefs.py` | Per-game preferences in `~/.config/flydigi/games.json` |
| `tools/flydigi-run` | Steam launch wrapper — `flydigi-run "<name>" -- %command%` |
| `tools/hid_probe.py` | Passive HID descriptor dump (writes nothing) |
| `tools/ds5-channel-probe` | Plays a tone into one DualSense audio channel at a time, to map channel index to actuator or speaker (needs `paplay`) |
| `tools/gyro-probe` | Vendor-stream IMU check — gyro and accel, live |
| `tools/haptics-inspect`, `tools/haptics-simulate`, `tools/joystick-curve-probe`, `tools/stick-feel` | The remaining bench probes: per-channel haptic energy, synthetic haptic playback, stick-curve capture, stick feel |
| `tools/flydigi_cmd.py` | Manual command tool — `info`, `race`, `normal`, `bind`, `rumble`, `game`, `raw`, plus `k6*` for the trigger family belonging to an Apex 6, which has not shipped ([device codes](docs/findings-other-devices.md)) |
| `gamelist.json` | All 94 games + per-game configs (from the public API) |
| `mods/` | All 46 downloadable mod zips (44 MB) |
| `bundle/` | 248 .NET assemblies (plus `deps.json` / `runtimeconfig.json`) extracted from `SpaceStationService.exe` |
| `decompiled/` | C# source for AdapterTriggerService, ControllerSdk, Hid, Basic, SpaceStationService |
| `asar/` | Extracted Electron app (`main.pretty.js` is the beautified main process) |

## Where the detail lives

| Document | What is in it |
|---|---|
| [PROTOCOL.md](PROTOCOL.md) | The wire protocol, and what is hardware-verified |
| [docs/findings-profile-blob.md](docs/findings-profile-blob.md) | The 840-byte profile: layouts, factory defaults, sticks, gyro, triggers, the trigger-motor block |
| [docs/device-settings.md](docs/device-settings.md) | Command 3, the small write commands, battery, the RGB test command, the command inventory |
| [docs/findings-screen.md](docs/findings-screen.md) | Image format, the serial upload, and why the SDK's HID picture family is inert here |
| [docs/findings-steam.md](docs/findings-steam.md) | Locking the hidraw node, the third-party takeover toggle, why Steam still misnames the pad after a reconnect, and SDL's own driver |
| [docs/findings-games.md](docs/findings-games.md) | Game detection, routes, and the per-game validation notes |
| [docs/findings-haptics.md](docs/findings-haptics.md) | Tier 4's limits: haptic audio, the USB gadget question, M1-M4 |
| [docs/findings-other-devices.md](docs/findings-other-devices.md) | **What `k5`/`k6`/`f4` mean**, the Vader 4, the charging dock, and firmware update |
| [docs/findings-desktop-app.md](docs/findings-desktop-app.md) | Apply vs save, the QML testing traps, the two fixed bugs |
| [gui/README.md](gui/README.md) | Working in `gui/`: toolkit, runtime, layout, licensing |
| [docs/gui-kirigami-plan.md](docs/gui-kirigami-plan.md) | The QML rewrite plan (finished; kept for the reasoning) |
| [docs/third-party-mods.md](docs/third-party-mods.md) | Tier 5, and why it is not shipped |
