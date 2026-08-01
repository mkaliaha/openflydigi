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
remapping, macros, sticks, the gyro mapped to a stick, vibration, per-profile trigger effects, the
pad's own device settings, lighting, the screen, the game list and its own setup. The daemon detects
a running game and applies its route unattended.

The **CD2 charging dock** is driven too, as a device of its own: its four switches, all eight of its
computable lighting effects, and **a picture or a GIF sampled onto its 162 LEDs** — the effects from
`tools/flydigi-charger` and from the app's Dock page, the picture from the app, where Qt does the
decoding.

**More than one device is handled properly**, which used to be the largest hole in all of this.
Every pad and dock on the bus is enumerated and named, a device is chosen by uid, nickname or node
rather than by whichever hidraw minor sorted first, the app has a picker and shows the pages of
whichever device is selected, and the daemon fans a tier-1 vibration bind out to every pad that
takes one while every other route acts on the pad the picker chose. There is one pad and one dock
on this desk, so `FLYDIGI_MOCK_BUS` serves the rest — see
[Mock devices](#mock-devices-for-the-ones-nobody-owns).

**Both picture pages frame the same way.** The Dock page's stage — a window cut out of a black
stage, the picture dragged under it and zoomed — is `gui/qml/components/CropStage.qml` and
`imaging.CropFrame`, and the Screen page drives one against the panel's 160×80 window. Space
Station has no framing there at all: their screen page takes the middle of the picture and that is
the whole of it.

**The Vader 5 Pro is driven too, and it has never been tested on one.** It speaks the same
protocol -- same vendor id, same frame, same 840-byte blob -- so profiles, buttons, sticks,
the gyro, vibration, lighting and the pad's own settings are the paths already measured here. What
it does *not* have is stated rather than discovered: no force triggers and no screen, so the
Triggers, Screen and Games pages are hidden on it, `identity.require_capability` refuses
every command that needs hardware it lacks, and the daemon's tier-1 bind skips it. Its two extra
buttons, C and Z, are in its key list. What it has and the Apex 5 does not is a motor in each
trigger; `MappingConfig.trigger_motor` reads and writes that block and **no page offers it yet**.

**Macros are the one place a Vader is not on a measured path, and the app says so.** Its profiles
are protocol **3.2**, where the macros are not in the profile at all: they move to a store of their
own behind commands 172/173/174, ten of them instead of five, 256 steps instead of 128, and a 1 ms
clock instead of 10 ms. All of that is built -- `mapping.MacroStore`, and limits read off the
profile's own `ProtoVersion` rather than hardcoded -- and all of it is a transcription of a
decompiled parser that has never been sent to hardware. Every other f5 path at least shares a route
with something measured on the Apex 5; this one is new bytes down a new command, so the Macros page
carries a warning when the open profile is v3.2 rather than presenting ten slots with the same
confidence as five.

**It has a factory profile now, so its per-slot restore works.** Translated from the
`default_mapping_130.dat` Flydigi ship, by a translator held against the Apex 5's blob read off the
hardware here: 828 of 840 bytes, with each of the other twelve explained rather than waved through.
That makes it what Space Station *would write* to restore a slot, which is not provably what a
factory Vader holds -- stated in `factory_config.py` and in [NOTICE](NOTICE), where the decision to
commit derived bytes is set out.
**DualSense mode stays available on it**, because only the trigger half of that relay is
Apex-specific: input, rumble, haptic audio to the motors and the gyro are the same on both pads, so
a Vader relays usefully and `PadLink.has_triggers` skips the effect translation rather than the
session.

**And the gyro is the reason to want it, which is not the same argument as coverage.** The pad's own
gyro-to-stick mapping works in every game with nothing running on the host, and DS mode's true gyro
axes only work in DS5-aware ones — but the narrower one is the *better* one where it applies.
Emulated stick motion is indistinguishable from a real stick, so a game cannot gate it: it keeps
aiming through menus, cutscenes and vehicles. Motion a game knows is motion can be switched off in
its own options, ratcheted, or bound to aim-down-sights alone. So the two are not a wide path and a
narrow copy of it; they are an always-on one and a context-aware one, and that is why this is worth
keeping on a pad with no adaptive triggers at all.

Anyone debugging a Vader should doubt `identity.CAPABILITIES` before doubting the transport.

**Left to build:** a trigger-motor page for the Vader, and the smaller pieces under
[What's next](#whats-next). Driving those motors *live* is [ruled out](#ruled-out) for want
of a Vader to test on.
Supporting an older pad is [ruled out](#ruled-out).

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

**A trigger-motor page for the Vader.** The stored block
is already read and written -- `MappingConfig.trigger_motor`, blob offset 154 -- so the missing
piece is a page: an enable, an amplitude window, a strength and a threshold, laid out like the
Vibration page and shown only where `identity.CAPABILITIES` says `trigger_motors`. Four controls,
and three traps in them:

  * **The enable is one byte for both triggers**, at 154 rather than in either side's block, so a
    page drawing one switch per trigger offers a state the pad cannot hold.
  * **The other four are per side and Space Station writes both the same**, its tooltip saying
    adjusting one syncs the other -- so whether the firmware reads the right side's copy at all is
    untested, and a page offering them separately is offering something unmeasured.
  * **`min`/`max` are 0..255 in the blob and percent in their UI**, floored on the way in and
    ceiled on the way out (`SaveTriggerVibrationConfig`).

The page is ordinary work and not a leap, which is the difference between it and the live half
below: the stored block's meaning comes from `SaveTriggerVibrationConfig` and
`ConvertTriggerConfigBean` reading it back, so every control has a named source. It is the same
standing as the rest of the f5 support -- layout from the decompile, which is the half that has held
up -- and it writes a region an Apex 5 does not read, so it cannot be got wrong on the pad here.
**Testing it needs a Vader**; building it does not.

**Single commands, each verifiable on the hardware here.** The write-ups are under "Commands beyond
the settings block" in [docs/device-settings.md](docs/device-settings.md).

  * **The cooperative lock** — `AcquireController`, command **28**, with a 20-byte ASCII tag. The
    read half is built as `motion.read_transport`; the write half is not.
  * **Show every profile's name without reading it.** Nothing returns a title but a config read, and
    a config read switches the pad — which is why Space Station keeps a per-device cache file and
    re-reads only the slots whose version tag has moved (`PrepareMappingConfigs`). The same is now
    open to this app, since saves finally roll a fresh tag: cache titles against `uid + slot + tag`
    and the profile list costs no device traffic at all.
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
  * ~~**Macro editing.**~~ Built — see [What's done](#whats-done).
  * **Restart the controller** — command **29**, no argument. Built as `settings.restart` and
    `tools/flydigi-settings restart`; never sent to hardware, and not in the app until it has been.

**A stray argument can trigger the vibration route, and the fix is a judgement call.** The daemon
acts on a confidence-0 name match — the game's name merely appearing in some process's argument
list — and `candidate_names` takes the basename of every argument, against a list with names as
short as `wrc` and `ds2`. Twelve of those are vibration-tier and so auto-on with no configuration,
which makes an ordinary process with `/opt/tools/wrc` in its command line enough to write command 82
over whatever trigger effect was set. Demonstrated with a sleeping `python3` and nothing else.
Unfixed because requiring more confidence overrides a documented decision and trades a false
positive nobody has hit for a false negative on the one route that currently always fires.
→ [docs/findings-games.md](docs/findings-games.md)

**Whether the firmware accepts 164/165 aimed at a slot it is not running stops mattering, because
such a write could never be made to stick.** An apply is working memory: it dies on a sleep, on a
power cycle, and on a profile change. Command 166 commits whichever profile is *live*. So a write
aimed at another slot would have to be saved by switching to that slot, and switching is precisely
what discards it. Editing a profile without switching to it is not something the firmware leaves
room for, which is why the app opens a profile in order to edit it.

**A trigger still reads as an analogue axis while it is the gyro's enable key** — answered in use
rather than by a probe, and so no longer a question worth a probe window. The pad ships with `Lt`
in that byte, the enable key is not swallowed (the probe correlated stick movement against `BTN_TL`
arriving on evdev throughout), and the axis goes on reporting while it gates the gyro. Which is the
point of shipping `Lt` as the default: gyro aim belongs on aim-down-sights, and ADS is that trigger
in most games, so an enable key that cost you the trigger's travel would defeat the pairing it was
chosen for. `tools/gyro-map-probe` still cannot be pointed at `Lt` — the gate keys are hardcoded to
LB and RB and every entry in `WINDOWS` is written `gate="lb"`, because evdev reports those as
buttons — and there is now no reason to build the window that could.

## What's done

All command factories are decompiled under `decompiled/Flydigi.ControllerSdk/`.

| Feature | Commands | Where |
|---|---|---|
| Mapping profiles | status 161, apply 162, read 163, write 164/165, save 166 | `flydigi/mapping.py`, `tools/flydigi-mapping`, GUI |
| Device-type guard | identify read 1 | `flydigi/identity.py` — `require()` refuses anything but a k5, and `flydigi/` calls it nowhere itself; `flydigi-mapping`, `flydigi-settings` and the app take it once per connection, so their reads are refused too |
| **Choosing between devices** | uid 4, nickname 2 / 24, and the command-1 address | `flydigi/registry.py` — one list over pads and docks, selection by node, uid, mac or nickname with an ambiguous name refused; `tools/flydigi-devices`, `--device` on every tool, the app's picker, and `prefs.primary_pad` for the daemon. Uid, nickname read *and* nickname write all **measured on the pad**; the address reads all-zero and is not usable — [detail](docs/findings-other-devices.md) |
| **Devices that are not there** | — | `flydigi/mock/` behind `FLYDIGI_MOCK_BUS` — the fakes moved out of `tests/` so the app, the tools and the daemon can all run against a bus with several pads and docks on it. Off unless the variable is set |
| Buttons, sticks, vibration, stored triggers | inside the 840-byte profile blob | same module — [detail](docs/findings-profile-blob.md) |
| Gyro mapped to a stick | the profile's motion block at 137 | `flydigi/mapping.py` (`motion`/`set_motion`), `tools/flydigi-mapping gyro`, GUI — **measured on the pad** with `tools/gyro-map-probe`: it plays the block, both enable keys gate it, Click toggles, and the response curve at 830 is inert — [detail](docs/findings-profile-blob.md) J2 |
| **Protocol 3.2, and the macro store** | read 172, write 173/174 | `flydigi/mapping.py` (`MacroStore`, `read_macro_store`, `write_macro_store`, `macro_limits`), GUI — from v3.2 the macros leave the profile for a 1620-byte store of their own, with ten slots, 256 steps and a 1 ms clock where v3.1 has five, 128 and 10 ms. A Vader 5 is v3.2 and an Apex 5 is not, so **none of it has been on hardware** and the Macros page says so — [PROTOCOL.md](PROTOCOL.md) §9a |
| **A factory profile per model** | inside 164/165 | `flydigi/factory_config.py`, `tools/mapping_bean.py`, `tools/gen-factory-config` — the Apex 5's read off the pad, the Vader 5's translated from the file Space Station ships, and the translator proved against the first before it emitted the second: 828 of 840 bytes, with the other twelve explained. `identity.CAPABILITIES` now answers `factory_profile` for both — [detail](docs/findings-profile-blob.md) |
| **The Switch bank, and restoring a slot** | save-to-slot 171, reset 175 | `flydigi/mapping.py` (`save_switch_config`, `reset_config`, `normalise_for_switch`), `tools/flydigi-mapping to-switch`/`reset` — **both measured on the pad**. The pad stores *eight* profiles: 0..3 XInput, 4..7 Switch. 171 is 166 with a destination, and 175 restores a whole slot including its name — [detail](docs/device-settings.md) |
| Macros, played by the pad | the profile's macro page at 230, plus 162 to make one live | `flydigi/mapping.py`, `flydigi/macros.py` (the recorder), GUI — [detail](docs/findings-profile-blob.md) |
| **Editing a macro's steps, and building one without recording** | inside the same page | `gui/models/profile.py` (`MacroStepsModel`), `gui/qml/components/MacroStepRow.qml`, the Macros page — each step's output key, press-or-release and the gap before it, plus insert and delete; **Build a macro** makes one from nothing, which is the only way to write a sequence no hand can play. A step's key is `XINPUT_TARGETS` because a macro step is a `ControllerKey` and nothing else fits in the byte |
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
| The CD2 charging dock | heartbeat 1, nickname 2/24, uid 4, switches 17/18/19/25, LED read 20, LED write 97/98, RGB write 22/23 | `flydigi/charger.py`, `tools/flydigi-charger`, the app's Dock page — **measured on the dock**: firmware 0.0.3.9, the reply checksum position predicted correctly on all five reads (the command-97 ack is the one exception, a slot later), and its eight computable effects reproduced closely enough that Space Station's own Breath and this port's were indistinguishable side by side — [detail](docs/findings-other-devices.md) |
| **A picture on the dock** | the same 97/98, mode `custom` | `charger.LED_PIXELS` and `sample_frame` (the sampler, transcribed from Space Station's own pixel table), `charger.wedge_centres` and `WEDGE_OUTLINE` (the preview's geometry, which is a *different* grid), `gui/models/dock.py` (Qt decodes, frames the picture and samples it), `gui/qml/components/LedWedge.qml` and the Dock page's Picture section. **One period unit is 20 ms, measured on the dock** — Space Station writes it as 10 and their animations play at half speed. Corrected and run against Space Station's own output on the same GIF, where the two looked about the same — the side-by-side that Breath was held to, and the one comparison that covers the sampler, the LED order and the pace together — [detail](docs/findings-other-devices.md) |

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
controller. Haptics are the reason to expect it cost more than the input: a game opens its audio
stream to the DualSense at launch, so a DualSense that goes away and comes back might have come back
mute even once the input worked again. It does not — see below. `flydigi/relay.py`'s `PadLink` holds
the physical pad as
something that may be absent — a failed read means "gone", not "fatal", and the nodes are looked
for again once a second under whatever numbers they come back as. Meanwhile the virtual pad stays
attached, fed a released state so a pad that vanished mid-press does not leave a button held, and
the trigger effects are sent again when the pad returns, since it forgets them when it sleeps.
`pad=` and `drops=` in the status line are the physical pad, never the virtual one; the app's
DualSense page shows them as their own row. **Confirmed in a real game**: the pad slept mid-session,
came back, and the game's haptics came back with it — which is the half that was reasoned about
rather than measured, since a game opens its audio stream to the DualSense once at launch. The loop
is also covered in `tests/test_relay.py` against a fake bus.

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

## Scrolling was uneven

**Fixed, confirmed on the pad: pressing Reload no longer makes it jelly.** Kept here because the
measurements are worth having and because a regression would come back the same way. Scrolling the
window was uneven from the first device read onward — smooth with `_read_the_rest()` suppressed, uneven
once device data reached the models, on every page, and it did not recover. Pressing **Reload from
pad** reproduced it.

Measured with `qmlprofiler`, on a 165 Hz display: 97% of frames land in 6 ms and rendering is
solid, but **49 frames in 38 seconds take over 40 ms**, and all 49 sit in one place —

```
RenderThread:swap -> GuiThread:polishAndSync    median 1.15 ms   p99 169.60 ms
RenderThread:render -> RenderThread:swap        median 4.82 ms   p99   6.41 ms
GuiThread:polishAndSync -> RenderThread:render  median 0.07 ms   p99   0.41 ms
```

So the GUI thread does not *begin* the next frame after a swap. Every one of those gaps has input
inside it, so they are stalls and not an idle window.

**A correction, because it is the kind that wastes days.** It was concluded from the same trace
that no QML, JavaScript, binding or delegate creation runs inside 45 of the 51 gaps, and therefore
that the thread "has nothing to do". *That does not follow.* `qmlprofiler` records QML and JS
ranges only, so Python running in a queued cross-thread slot or a `QTimer` timeout leaves no mark
in it — every `worker.*` reply slot, and the DS-mode poll. "Nothing to do" and "doing something the
profiler cannot see" draw the same picture there, and two entries in the ruled-out list below rest
on the difference.

**Ruled out by measurement**, subject to that: the GIL as CPU contention (0.5% CPU during a poll),
GIL handover latency (`sys.setswitchinterval(0.0005)` changes nothing), the GIL in render sync (a
control with Python-backed bindings notifying at 60 Hz is smooth), thread affinity (`moveToThread`
is correct), Kirigami, the QQC2 style (a control under `org.kde.desktop` is smooth), Wayland versus
XWayland, PySide6 itself (plain PySide6 + Kirigami is smooth), a worker thread doing the full device
read and holding the pad open (smooth), all three poll timers, and the garbage collector (no
collection reached 15 ms). Every "smooth control" there was scrolled *without* inertia, so none of
them exercised the animated scroll path the real window uses.

**What an architectural review then found, and what was done about it.** Seventeen agents read
`gui/` against the question; every finding below was checked against the code before it was acted
on.

  * **Reads were not free, and the design assumed they were.** `gui/models/` declared 273
    properties, 98 of them notified by a single per-model `changed` signal — and the getters
    decoded rather than read. One `changed` on `MotionModel` cost thirteen decodes of the same
    eight bytes; a view sweeping the key table across its nine roles decoded it 207 times to fill
    23 rows; `ProfileModel.dirty` was an 840-byte compare computed per read, three times per
    footer, on seven pages that were all alive at once. Every model now decodes once where the
    bytes move and reads a field thereafter. `tests/test_models.py` counts the decodes, because
    nothing else can see the difference.
  * **Fifteen pages were built and never destroyed**, and a hidden page's bindings re-evaluate like
    any other. `pageFor` memoised them and nothing called `pop`, `clear` or `destroy`; Kirigami does
    not destroy a replaced page either, since `ColumnView::replaceItem` gates its `deleteLater` on
    `shouldDeleteOnRemove`, false as soon as an item has a visual parent. One page exists now.
  * **Blocking work sat on the GUI thread that was not device I/O.** The Screen page's encode is
    about 1.3 s for a 200-frame animation and it ran at the end of every crop gesture; the dock's
    re-sample is 162 colours off a repainted canvas, 0.868 ms, and it ran on every pointer move
    while a hook to defer it sat there empty. Both are fixed, and the rule this project states is
    now "nothing blocking on the GUI thread", not "no HID".
  * **A functional bug, not a performance one: the Triggers knobs could not be dragged.**
    `effectParams` was a list rebuilt on every read and notified by the signal a knob move emits, so
    the first move replaced the Repeater's model and destroyed the delegate under the pointer along
    with its mouse grab. Measured with synthetic pointer events: a slider outside the Repeater
    reported `moved` forty times across a drag, the same slider inside it reported once. The page's
    own test missed it by calling `moved(60)` rather than dragging.
  * **Combo boxes and spin boxes ate the wheel.** `org.kde.desktop` sets `wheelEnabled: true` where
    Qt's default is false, so a scroll with the pointer over one silently *edited the profile* — one
    notch over a row on Buttons remapped a key. Worth fixing whatever it did for frame rate.

**Which one of those fixed it is not known**, and it is worth saying so rather than picking a
favourite. They landed together, the confirmation is one session on the pad, and the honest reading
is that the window no longer stalls after a Reload — not that any single line above was the cause.
Anyone bisecting a regression should start from the whole list.

The instrument built to answer that question is still there and still the right first move if it
comes back: `FLYDIGI_STALL_WATCHDOG` — see
[gui/README.md](gui/README.md#watching-for-stalls) — reports what `qmlprofiler` cannot, namely
which Python frame the GUI thread is in during a stall. A file holding only the startup dump is
itself an answer, and points at `QSGThreadedRenderLoop` and the compositor rather than at anything
here.

**Profiling this app**, which took three obstacles to work out and is worth writing down:

  * `qDebug` is compiled out of Fedora's Qt, so `QSG_RENDER_TIMING`, `QSG_INFO` and `console.log`
    all print nothing however the logging rules are set. `console.warn` and above still work.
  * `python3 -m gui` makes the interpreter eat qmlprofiler's `-qmljsdebugger=…` as `-q -m`. Launch
    through a wrapper that puts it after a script path.
  * PySide6 has no `QT_QML_DEBUG` build flag, so the port stays shut until the process calls
    `QQmlDebuggingEnabler.enableDebugging(True)` before any QML engine exists.

A `FLYDIGI_MOCK_BUS` run tells you nothing about any of this: the fake pad answers instantly, so a
full read finishes in milliseconds instead of the seconds it takes on hardware, and there is no
~970 Hz input stream on the node.

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
  * **A pad's own address is all zeroes**, so the free identifier is not one. The command-1 reply
    carries four MAC bytes at raw 8..11 and this pad answers `00 00 00 00` on its dongle, with
    every surrounding field decoding correctly — measured, firmware 7.0.4.5. Whether a cable fills
    it in is untested. `identity.read_uid` (command 4) is the one that works: thirteen bytes,
    one exchange, and `registry.key` prefers it for exactly this reason.
    → [docs/findings-other-devices.md](docs/findings-other-devices.md)

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
| Devices | every pad and dock attached, with its model, node, uid, firmware and battery; which one the window is showing, and a note when any of them is a mock. The picker in the sidebar header is the same selection |
| Controller | Device (connection, battery level, reload from the pad); Profiles (the four slots — opening one is how you switch the running profile); Selected profile (rename the open profile, back up / restore it to file); Other software (let Steam and similar take the pad over, and who currently holds it) |
| Device | the pad's own settings, not a profile's: switching profile from the pad with `FN + A/B/X/Y`, sleep time, the mapping switch (sub-id 4, undocumented in every locale Flydigi ships), stick debounce, auto-calibration, the rebound filter, stick resolution and centre sensitivity — plus the polling rate, shown and not offered |
| Buttons | remap, turbo + hold/toggle, reset all to default. A key that runs a macro or sends a keystroke says which — it used to show "(default)", which was a claim that the key did what the shell says about a key running a macro |
| Macros | record a sequence off the pad and bind it to any key, or **build one with no recording at all**; pick once / while held / toggle, set the repeat gap, **edit every step** — its output key, whether it presses or releases, and the gap before it — insert, delete, and delete the macro. A macro that ends with a key still held is named on the card and in the editor, with one button to release what is held; a macro written by other software is shown and marked unwritable rather than being offered an edit that would be refused. The slot count, step budget and interval floor come off the open profile's protocol version — five and 128 on an Apex 5, ten and 256 on a v3.2 pad, where the page also warns that the store behind it is untested and offers each macro a name |
| Sticks | dead zone, outer dead zone, sensitivity curve presets, circular range |
| Gyro | map the gyro onto either stick, the button that turns it on and how, sensitivity and the dead-zone offset — plus the motion mode, shown and not offered, because Flydigi derives it from the stick |
| Vibration | master switch, per-grip enable, min/max window, strength |
| Triggers | stored effect — all six of Flydigi's, each with its own controls, engaged on the pad as well as stored — plus the travel window, Flydigi's "Stroke Setting", as a start/end pair |
| Lighting | effect, up to 5 colours, brightness, cycle time, react-to-rumble |
| Screen | pick a picture or GIF, choose how it fits, **drag and zoom it under the 160×80 window**, preview the encoded frame, and send it over the serial link — with the frame count and a time estimate before you start; plus the always-on display and the status bar |
| Dock | whichever dock is selected: its identity and uid, the four switches (written as they move, read back afterwards), and its lighting — eight effects with colours, brightness, frame interval and direction, computed here and uploaded with a progress bar. Says which switch wins when Sleep-while-docked is on beside the other two. **“Picture” is the ninth effect**: choose a picture or a GIF, drag and zoom it under the 334×304 window the LEDs are read from, trim which GIF frames to send, and watch the result play on a wedge of 162 dots before spending the packets |
| Games | all 94 games, searchable, filtered by route; **Update list** refetches the gamelist from Flydigi's public API; vibration presets load onto the pad from here; per-game **Auto** toggle, a route picker where a game really has a choice, and a DualSense marker on the 23 games Flydigi lists as DS5-aware |
| DualSense | the tier-4b switch: vhci-hcd's state, haptic audio to the motors, what the relay is doing, and the launch option to copy |
| Setup | the daemon's unit, "running now" and "start at login" as separate switches, the application-menu entry, and the udev rules behind one authentication prompt |

**Everything device-facing runs on the worker thread** (`gui/worker.py`) and requests cross as
signals. Calling a worker slot directly runs it on the caller's thread, which silently puts blocking
HID traffic back on the UI thread.

**The sidebar shows the sections of the device that is selected** — a pad's twelve, or the dock's
one — because a window offering Buttons and Macros while a dock is on screen is offering to edit
something that is not there. A pad and a dock are remembered separately, so choosing a dock does
not make the pad pages go blank and choosing a pad does not forget which of two docks was being
worked on. Only a change of *kind* moves the page; switching between two pads leaves you where you
were, on the same page about a different device.

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

## Mock devices, for the ones nobody owns

One Apex 5 and one CD2 are on this desk, and every multi-device path needs more. `FLYDIGI_MOCK_BUS`
puts fakes on the bus that the tools, the daemon and the desktop app all see, because
`device.find_nodes` is the one place any of them asks what is attached:

```bash
FLYDIGI_MOCK_BUS='pad=Desk,pad:f5=Couch,dock:1=Shelf' tools/flydigi-devices list
FLYDIGI_MOCK_BUS=~/bus.json distrobox enter apex-dev -- python3 -m gui
```

The JSON form is re-read on every enumeration, so editing `"present": false` and saving is a
device being unplugged — which is how the app's reconnect path and a picker whose device vanishes
get exercised at all. State survives that: a mock pad keeps its profiles across an absence, the way
a real one keeps everything but an unsaved config across a sleep. Identity is derived from a
device's place in the spec, so a `uid:` selector stored in a config file still resolves next run.

**Nothing appears unless the variable is set**, and everything that comes out of it is marked
`mock` — in `flydigi-devices list`, in the picker, and on the Devices page. The fakes are
`flydigi/mock/pad.py` and `flydigi/mock/dock.py`, which is where `tests/fake_pad.py` moved to;
the test files are re-export shims and the tests import them by the old names.

## Facts about the pad, the wire and this code

  * **A pad answers command 4 with a real uid, and its address field with nothing.** Measured on
    this pad: `04 5a a5 04 01 00 | 14 20 6e 7a 1c 00 00 00 00 dc ba 3e 00`, thirteen bytes at the
    offset the SDK predicts, and command 1's four address bytes are all zero. So the identifier
    that costs an exchange is the one that works and the free one is not usable.
  * **Naming a pad works, and Space Station's own rename does not.** Command 24, measured. The
    pad acknowledges it whatever is in the packet -- **it does not check the checksum** -- and
    stores `buf[4] - 1` bytes from buf[5], one *more* than the name, so a checksum written after
    the name is kept as part of it: "Desk" plus a checksum reads back as `44 65 73 6b a5`. So the
    packet carries none, and the limit is 26 bytes rather than the 27 that fit. Flydigi's own
    builder puts the checksum at a fixed index 6, which is the right slot only for a
    one-character name — their "Desk" is stored as `44 a5 73 6b`, with the `e` eaten. UTF-8
    round-trips.
  * **A pad that has never been named holds `01 01 09 09 09 64 04 5e`, not zeroes**, and the
    nickname payload is at raw 6 like every other single-frame reply — not raw 5, where the
    controller SDK's own slice points and where an unnamed pad's index byte made it look right.
    Both were transcribed from the reference, both were wrong, and both were caught by writing a
    name and reading it back. Flydigi's emptiness test calls that factory field a name, so
    `read_nickname` adds a second test: a field that is not printable UTF-8 is not a name.
  * **The dock's "Intelligent start" turns the lighting off on both devices.** Observed on the
    hardware here: with it on, docking a pad takes the lighting down on the pad *and* on the dock
    for as long as it sits there — so it overrides Lighting sync and Power display during the only
    window either of them matters in. Space Station forcing it and Power display apart in its UI
    is enforcing something the firmware does, not a house style; this project sets both as asked
    and says which wins. Named "Sleep while docked" in the app, since their label describes none
    of that.
  * **The dock's battery byte is a controller's charge, on the controller's scale** — 0..5 with 6
    meaning charging, decoded by `charger.describe_battery`. Never seen with a pad actually
    seated, so the scale is inferred from the pad's own; printing the raw byte would repeat the
    bug that reported a full pad as five-eighths, and worse, since a docked pad is charging and
    would render as "battery 6".
  * **Report id is `0x03`** on the vendor interface, not the `6` the decompiled
    `TakeEndpointByDevice()` suggests. Find the node by report-descriptor prefix `06 a0 ff`; it moves
    between wired and dongle.
  * **Wine maps game PEs at their image base** (`0x140000000`), same as Windows, so Flydigi's memory
    offsets work unmodified.
  * **The pad publishes keyboard, mouse and gamepad evdev nodes under one vendor/product id, and the
    keyboard sorts first.** Resolve with `axes=True` (non-empty abs capabilities) or a relay binds a
    node that never sends a gamepad event.
  * **A key bound to "keyboard" is not inert on the pad — it is a keystroke with no key.** Measured
    with `tools/keyboard-target-probe`: writing 254 into A's target byte stops A arriving as
    `BTN_SOUTH` anywhere, while **200 in the same byte leaves it reporting normally** — so this is
    not a firmware dropping targets it cannot resolve, it recognises that one value. (And an
    unrecognised target being identity is itself measured there, which is what `mapping()` had
    always assumed.) It types nothing, and could not: the key code is discarded by Flydigi's own
    serialiser on the branch that writes 254, and candidate codes poked into the entry's two spare
    bytes — HID usage, Windows virtual key, the pad's own key id, either position — produced no
    keystroke. On Windows a pair of kernel filter drivers supplies the other half. **What the pad's
    own keyboard and mouse interfaces are for is still unknown**, and Flydigi's software never opens
    them. → [docs/findings-steam.md](docs/findings-steam.md)
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
    lighting — **and on a profile change too**. Applying is working memory in the literal sense;
    command 166 is what makes it last, and it commits whichever profile is live.
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
  * **81 and 82 never go to a Vader, and the gate is Flydigi's own.**
    `SetForceTriggerConfigImpl` returns early on `!IsSupportForceTrigger` and the stored-effect
    replay is behind the same test, so Space Station sends neither command to a pad that declares
    itself without force triggers -- which a Vader 5 does. The tempting reading is that
    `SyncWithGrip` is the Vader's version of the same feature, driving its trigger motors instead of
    the resistance; it is not. Those motors are **command 18**, the ordinary rumble, with two more
    level bytes at `[7]`/`[8]` beside the grips' `[5]`/`[6]` under the same length byte
    ([PROTOCOL.md](PROTOCOL.md) §3a) -- so `effects.rumble()` as written can never reach them, and a
    trigger-motor editor is a rumble change rather than a trigger-effect one.
    `effects.engage_stored` takes the DeviceCode and skips a pad without force triggers; the worker
    caches it off the identify read it already pays for.
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
  * **A screen upload on the dongle strands the pad in upgrade mode, and nothing on the PC says
    so.** Measured: the pad accepts command 31 over the dongle and switches its screen chip over,
    and no serial device appears — the dongle does not relay the bootloader's USB CDC device and has
    no notion that there is one. The upload waits for a tty that cannot arrive, reports a timeout,
    and the pad stays in upgrade mode until its own power switch is used. So the wired test comes
    before the command, not after it: `canUpload`/`upload()` on the Screen page, a re-read in
    `worker.upload_screen` that also catches a cable pulled mid-press, and `--i-know` on
    `tools/flydigi-screen`. Space Station refuses a wireless upload too, and this is why.
    → [docs/findings-screen.md](docs/findings-screen.md)
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
  * **The two picture pages share a stage and reframe at different moments.** Sampling the dock's
    162 LEDs is about half a millisecond, so its wedge follows the pointer all the way through a
    drag. The pad's panel is an encode plus a preview file *per frame* — 12,800 pixels of pure
    Python each — so a 255-frame animation would be seconds of work per pointer event. Both stages
    track the pointer for free either way, since that only moves an item already on the scene
    graph; the Screen page re-encodes on `framingSettled`, which the drag and the zoom slider each
    call when they end, and `DockModel.framingSettled` is deliberately empty.
  * **Do not send anything slow through the worker's `_attempt`.** It retries once, which is right
    for a sulking pad and wrong for a screen upload: that runs for minutes and has already switched
    the pad into upgrade mode.
  * **Steam Input must be off** for either DualSense tier — it masks the pad as an Xbox controller
    and breaks DualSense semantics.
  * **The package-count byte in a profile is 77, and the profile is 840 bytes.** Measured -- the
    factory blob read off this pad holds `0x4d` at offset 2. `MappingConfigParser`'s 84 is the
    packet count of the *transfer*, 84 packets of ten, and the byte in the blob is a different
    number that happens to look like one. Anything splitting a profile on `blob[2] * 10` cuts
    seventy bytes off the end of it; `mapping.unpack_config` splits on length instead.
  * **Restoring a profile to factory does not restore the lighting, and should not.** The LED config
    is not in the profile blob -- the ten bytes at offset 3 are `OldLedConfig`, a legacy mirror
    nothing here decodes -- and Space Station's own per-slot restore writes it separately over
    168/169, from a per-SKU file. Their six Apex 5 files carry six different LED configs, with the
    base model at brightness 20 and the Eva edition at 100, so a single committed k5 LED blob would
    put the base model's lighting on every themed pad that restored a slot. The legacy bytes that
    *are* in the blob are identical across all six, so writing them carries nothing across. The app
    and the CLI both say lighting is left alone.
  * **All six Apex 5 SKUs share one factory profile.** DeviceTypes 128, 129, 133 and 134 emit a
    byte-identical 840-byte blob and 135/136 differ only at 154, the enable byte for trigger motors
    this pad does not have. So the blob read off a DeviceType 128 here covers every edition, and the
    2 KB by which the six files differ is almost entirely `LedConfigBean`, which is not in the blob.
  * **A macro is stored by the write and played by the apply** (command 162), independently of the
    save: macros written and not applied sit on the pad and do nothing, and saving only decides
    whether they survive a sleep. `MappingConfig.macro_page` is what a writer compares to know
    whether it owes an apply, since applying makes the pad audibly re-seat its trigger motors.
    The macro page and the key table are read independently and **both** fire, so removing the body
    at 230 is part of remapping a key; `set_mapping` does it, as Flydigi's own repository does.
    → [docs/findings-profile-blob.md](docs/findings-profile-blob.md) J6
  * **The pad has four more profiles than it appears to, and a Switch is the only thing that can
    read them.** Slots 4..7 are the Switch bank; command 171 commits the running profile into one of
    them, and reads of 4..7 alias onto 0..3 so nothing on a host can fetch one back. In Switch mode
    the pad enumerates as `057e:2009` — Nintendo's own Pro Controller, no `37d7`, no vendor
    collection — so neither this project nor Space Station can address it at all. Verified with the
    control in the same run: a Switch copy carrying `m1 → start` and `y → select` left XInput
    profile 3 untouched in flash and then sent **Plus and Minus** on the Switch. **The paddles work
    there**, which a Pro Controller has none of, so M1..M6 are four buttons a Switch cannot
    otherwise offer. → [docs/device-settings.md](docs/device-settings.md)
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

  * **Driving a Vader's trigger motors *live*, and whether the DualSense relay already does.**
    The stored rule -- the block a config page would edit, under
    [What's next](#whats-next) -- is documented by Flydigi's own code. The live side is not, and it
    cannot be settled without the pad. Two questions, one of which may be worth a great deal.

    The rule reads as a pad-side mapping -- threshold, scale, amplitude window -- from *grip* rumble
    into the trigger motors, the same shape as the Apex 5's `SyncWithGrip` bind. **If that is what
    it is, the DualSense relay already drives them and nothing in `flydigi/relay.py` needs a line:**
    a game writes rumble to the virtual DualSense, the relay forwards it as command 18 grip levels
    exactly as it does today, and the pad's own rule buzzes the triggers off the back of it. Haptic
    audio would arrive the same way, since `flydigi/haptics.py` ends in the same call. A Vader's only
    trigger feedback would then work in DS mode with the config page as the entire implementation.

    **And it could go the other way.** `effects.rumble()` leaves `[7]`/`[8]` -- the trigger levels of
    that same command 18, under the same length byte ([PROTOCOL.md](PROTOCOL.md) §3a) -- at zero. If
    those are direct level writes rather than something the rule overrides, the relay is *actively
    holding the trigger motors off* every time it forwards rumble, which would look like the feature
    being broken precisely while it is in use. Flydigi's own `VibrationType.Grip` command has the
    identical shape, zeros included, which is mild evidence for the harmless reading and no more --
    their app only ever sends it from a test slider, never in a game loop.

    So the run that settles it is one session: enable the stored block, pull rumble through the
    relay, and see whether the triggers buzz. If they do not, resend with `[7]`/`[8]` carrying the
    grip levels and see whether that is the difference. That also decides whether `effects.rumble()`
    grows a trigger pair and whether the config page is worth a test button.

    **Unbuilt because there is no Vader 5 here and buying one for this is not the trade.** Everything
    needed is written down -- the command, the bytes, the two hypotheses and the run that separates
    them -- so this is an invitation rather than a rejection. `PadLink.has_triggers` stays right
    whatever the answer: a DS5's *adaptive* trigger effects cannot be reproduced on a pad with no
    resistance, and only the rumble half was ever in question.
  * **Adaptive triggers for a second pad are one MAC away, and still not worth it.** Measured with
    the virtual DualSense attached beside a real one: they coexist, and a rumble report written to
    both nodes at once drove both — the real pad's motors and, through the relay, the Apex 5's. So
    input, rumble and adaptive triggers are all per-device, since triggers ride the same report
    `0x02` as the rumble. Haptic **audio** is the exception, and it is the game's decision rather
    than the stack's: Deathloop with both attached opened one stream at a time and rebuilt it three
    times in thirty seconds, landing on a different sink each time, while the real pad never
    vibrated. Two *virtual* pads would evict each other outright, sharing the committed `0x09`
    address, so multi-pad would need that derived per pad from the physical uid. It stays unbuilt
    because the case is two Apex 5s in a local co-op game that also does DualSense haptics, and
    those barely exist. What does work per-pad for free is tier 1, the vibration bind: it is a
    pad-side setting with nothing host-side in the loop, so every pad drives its own triggers from
    its own rumble. → [docs/findings-haptics.md](docs/findings-haptics.md)
  * **Older pads are ruled out by the pad, not by the protocol.** `IsOldProtocol()` is
    `VendorId != 0x37D7`: everything before this generation speaks an older dialect of the same
    protocol — same blob and parsers, but a 15-byte `a5 <cmd> <sub>` frame, renumbered commands,
    replies at offset 14 of the gamepad report, and 10-byte blob packets. Measured on a Vader 4
    Pro, and transcribable rather than unknown. (Which blob *length* that pad carries was not
    pinned down: the first packet reports either 79 or 84 packets, so 790 or 840 bytes, and both
    are versions of the same layout.) It stays unbuilt because that pad has
    neither adaptive triggers nor a screen, and because reaching it takes interface 0 from `xpad`
    for every operation. Only an Apex 5 and a Vader 4 Pro are available to test with.
    → [docs/findings-other-devices.md](docs/findings-other-devices.md)
  * **Keyboard and mouse remapping is not a pad feature on any of them.** `KeyMapType.Keyboard` and
    `MultiFunction` both serialise to the single byte `254`, with no key code anywhere in the blob.
    The injection is host-side, in `KeyboardMouseInjectRunner.cs`. Same for `MotionMapType.Mouse`.
    On Linux that is a uinput daemon, and a different project from configuring a pad.
  * **`EnableDS5Data` (232) is dead code** — DInput builder only, no callers anywhere in
    `SpaceStationService`. It looks like it would replace the whole virtual-DualSense tier. It would
    not.
  * **What the Xbox home toggle does: don't know, don't care.** Command 19 sub 2,
    `EnableXboxHomeButton`. Flydigi ship no control for it on any pad — no key in any of the twelve
    locale files, and `xboxHomeButtonUsable` never reaches their renderer at all, so their own v4 UI
    cannot draw one whatever the SDK carries. The SDK guards the write to `ControllerType.XInput`,
    where it is command **48** sub **10** on / **9** off, and that same XInput read parses eleven
    fields without ever reading `XboxHomeButtonEnabled` back — so the interface that may write it
    cannot read it, and the interface that reads it may not write it. `XboxHomeButtonUsable` is
    hardcoded true for `f4`, `fp3`, `fp4` and DeviceType 102, which are the pads whose Home button
    is also the power button: measured on the Vader 4 here, a short press is an ordinary Guide and a
    long one powers the pad off. An Apex 5 has a rear power slider, so its Home is Guide and nothing
    else, and it reports the flag supported and on. Whether the flag gates the Guide half or the
    power half would take a write on a pad this project does not drive, for a setting the Apex 5
    does not have. `tools/flydigi-settings xbox-home` stays behind `--i-know`.
    → [docs/device-settings.md](docs/device-settings.md)
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

Tests, cheapest first. The seventeen backend tests import no Qt. The three Qt tests skip with exit 0
when PySide6 is absent, so the backend run stays dependency-free. `test_registry` and `test_daemon`
run against the mock bus and set `FLYDIGI_MOCK_BUS` themselves, so a pad on the desk changes
nothing about what they assert:

```bash
for t in tests/test_{charger,daemon,device,dsmode,dsx,forza,games,identity,macros,mapping,monitor,prefs,registry,relay,screen,screen_ota,settings}.py; do python3 "$t"; done
distrobox enter apex-dev -- bash -lc 'cd ~/Projects/ApexExperiments && \
  python3 tests/test_models.py && python3 tests/test_shell.py && python3 tests/test_qml.py && \
  tools/generate-qmltypes && \
  qmllint-qt6 -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml && \
  reuse lint'
```

`flydigi/mock/pad.py` — imported by the tests as `tests/fake_pad.py` — answers reads, diffed writes,
apply and save, models switch-on-read, and refuses a bad checksum by staying silent exactly as the
pad does. It answers the identity commands too, so two of them can be told apart.

The Qt tests set `XDG_CONFIG_HOME` and `FLYDIGI_MOCK_BUS` before importing `gui`: the app
enumerates the bus at startup and writes the chosen pad into the preferences file, and neither
probing the developer's hardware nor rewriting their auto-mode preferences is a thing a test may
do.

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
| `gui/` | PySide6/QML desktop app (GPL-3.0-or-later) — `app.py` (the object graph), `main.py` (entry point), `worker.py` (all device I/O, on its own thread), `i18n.py` (the `i18n*()` shim the engine needs; without it every Kirigami form delegate throws `ReferenceError: i18ndc is not defined`, so `main.py` installs it unconditionally), `models/` (view-agnostic state; `screen.py`, `dock.py` and `imaging.py` are the ones that touch QtGui, for image decoding and for `CropFrame`, the framing both picture pages share), `qml/` (`Main.qml`, `pages/`, `components/`) |
| `tools/flydigi-devices` | Every device attached — `list`, `show`, and `name` to write a nickname (measured on the pad; it asks first). The `--device` selector every other tool takes is defined here |
| `tools/flydigi-mapping` | CLI for profiles — list/show/set/clear/rename/apply/backup/restore, plus `macros`, `macro-record`, `macro-set`, `macro-clear`, `gyro` |
| `tools/gyro-map-probe` | What the pad does with the motion block at 137: five windows answering whether it plays it, whether each enable key gates it, whether Click toggles, and whether the curve at 830 is read. Transitions counted out on the motors, in `stick-feel`'s grammar |
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
| `tests/` | `test_charger.py`, `test_daemon.py`, `test_device.py`, `test_dsmode.py`, `test_dsx.py`, `test_forza.py`, `test_games.py`, `test_identity.py`, `test_macros.py`, `test_mapping.py`, `test_monitor.py`, `test_prefs.py`, `test_registry.py`, `test_relay.py`, `test_screen.py`, `test_screen_ota.py`, `test_settings.py` need no Qt; `test_models.py` needs PySide6, and `test_shell.py` and `test_qml.py` with `qml_harness.py` and `qml/tst_*.qml` need Kirigami as well, so those two run inside `apex-dev` — all pass without hardware, each printing its own count |
| `tests/fake_pad.py`, `tests/fake_dock.py` | Re-export shims. The fakes themselves are `flydigi/mock/pad.py` and `flydigi/mock/dock.py`, so the app and the tools can run against them too |
| `tools/forza-simulate` | Synthetic telemetry generator, for testing without the game |
| `configs/forza.json` | Flydigi's own 15-rule Forza config, reused verbatim |
| `tools/flydigid` | Polling daemon — auto-detects a running game and applies its config |
| `tools/apex5-setup` | Setup checklist: udev rules, the daemon's unit, start at login, menu entry |
| `tools/flydigi-auto` | Per-game auto mode and route — `list`, `on`, `off`, `reset`, `route` |
| `flydigi/setup.py` | What the two above share: checks, unit generation, escalation. The unit and the menu entry are generated with this checkout's path in them, and `unit_installed()` is a byte comparison against what the checkout would write now, so moving the repository makes Setup report the daemon unit as out of date until it is installed again |
| `flydigi/registry.py` | Every Flydigi device attached, and which one a caller meant: one list over pads and docks, `list_devices`/`drivable_pads`, selection by node, uid, mac or nickname, and the `--device` argument every tool shares |
| `flydigi/mock/` | Devices that are not there, behind `FLYDIGI_MOCK_BUS` — the bus (`__init__.py`), the fake pad and the fake dock. Nothing appears unless the variable is set |
| `flydigi/prefs.py` | Per-game preferences in `$XDG_CONFIG_HOME/flydigi/games.json`, falling back to `~/.config`. Keyed by the gamelist's `id`, which is unique across all 94 entries where names are not, and rewritten atomically because the daemon re-reads it while the app is editing it |
| `tools/flydigi-run` | Steam launch wrapper — `flydigi-run "<name>" -- %command%` |
| `tools/gen-factory-config` | Regenerates `flydigi/factory_config.py`. Two sources: a pad that has been factory reset (`--from-pad`), and the `default_mapping_<DeviceType>.dat` Space Station ships, for a model nobody here owns. `--check` proves the translator against the Apex 5 blob read from hardware, and refuses to emit anything if a byte it cannot explain has appeared |
| `tools/mapping_bean.py` | Flydigi's protobuf config bean turned into the 840-byte wire blob — a schema-free protobuf wire reader with no dependency on `protobuf`, plus `MappingConfigParserV30`/`V31`'s emit paths |
| `tools/fetch-configs` | Restores everything gitignored — `gamelist.json`, `configs/`, `mods/` |
| `tools/hid_probe.py` | Passive HID descriptor dump (writes nothing) |
| `tools/ds5-channel-probe` | Plays a tone into one DualSense audio channel at a time, to map channel index to actuator or speaker (needs `pactl` and `paplay`) |
| `tools/gyro-probe` | Vendor-stream IMU check — gyro and accel, live |
| `tools/trigger-stroke-probe` | Which trigger-travel bytes the pad plays: a degenerate window on one trigger, the other left as an in-run control |
| `tools/keyboard-target-probe` | What the pad does with a key bound to keyboard (target 254): whether it suppresses its own gamepad output, whether anything reaches its keyboard and mouse nodes, and whether candidate key codes in the entry's two spare bytes do anything. Needs root, because systemd gives the seat user an ACL on a joystick node and deliberately not on a keyboard one |
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
