# Apex 5 on Linux — project state

Goal: replace Flydigi's Windows-only Space Station app on Linux. Started with adaptive triggers;
now covers five delivery mechanisms plus a virtual DualSense.

## Read this first

**Status: adaptive triggers are done and validated in real games, and the desktop app — now QML on
Kirigami — covers profiles, button remapping, vibration, per-profile trigger config and RGB
lighting.** What remains is the screen/GIF upload, the charging dock, macros, the device
settings, a daemon that picks the right tier per game, and the gyro block the profile already
carries — see "Next". The joystick block and the third-party takeover toggle are both done, and both
known UI bugs are fixed.

| Tier | Games | Validated in |
|---|---|---|
| 1. Vibration bind (cmd 82) | 33 | Death Stranding 2 |
| 2. Forza telemetry | 4 | Forza Horizon 6 |
| 2b. DSX listener (UDP 7878) | third-party mod ecosystem | hardware |
| 3. XGameMonitor (memory) | 31 | Dark Souls: Remastered |
| 4. Virtual DualSense | **any DS5-aware game** | Deathloop |
| 5. Third-party mods | 11 | works via the DSX listener; not supported |

The backend is pure Python with zero dependencies — that is a feature worth defending, since it
means `flydigi-ds5` runs on any machine with Python 3.9 and no Qt. `PROTOCOL.md` has the wire
protocol and what is hardware-verified. Nothing Flydigi-owned is committed; `tools/fetch-configs`
restores it.

Licensing is per-file via REUSE: MIT backend, CC0 protocol docs and system config, GPL-3.0-or-later
for `gui/` only. `LICENSE` explains why, `gui/README.md` states the rule that keeps it true
(`gui/` may import `flydigi/`, never the reverse). Verify with `reuse lint`.

## The desktop app

**QML on Kirigami, in `gui/`**, calling the backend in-process — no D-Bus. Run it with:

```bash
sudo dnf install python3-pyside6 kf6-kirigami kf6-kirigami-addons kf6-qqc2-desktop-style
python3 -m gui
```

PySide6 specifically, **not PyQt6** — PyQt is GPL-only and would force the whole tree copyleft,
which is where the "Qt means GPL" belief comes from. Avoid the add-ons that are GPL-3.0-only
(Charts, Data Visualization, Virtual Keyboard); draw graphs with a QML `Canvas`.

**Not from pip.** A PySide6 wheel bundles its own Qt, tagging private symbols `Qt_6_PRIVATE_API`
where Fedora's Qt 6.11.1 tags them `Qt_6.11_PRIVATE_API`, so Kirigami will not link against it —
in either direction, and RPATH puts it beyond `LD_LIBRARY_PATH`. PySide6 has to come from wherever
Kirigami does. On this immutable system that is the `apex-dev` distrobox; no layering, no reboot.
Flatpak is the shipping target: `io.qt.PySide.BaseApp//6.11` on `org.kde.Platform//6.11` builds
PySide6 against the runtime's own Qt, so the mismatch cannot arise.

The widgets are gone. `gui/models/` holds view-agnostic state — no `QtWidgets`, no `QtQuick` — and
QML binds to it through the `App` singleton of the `Apex5` module, registered by `QmlElement`
decorators rather than injected as context properties, which is what lets qmllint type-check the
QML. See `gui/README.md`.

| Tab | What works |
|---|---|
| Profiles → Buttons | remap, turbo + hold/toggle, rename, back up / restore to file |
| Profiles → Vibration | master switch, per-grip enable, min/max window, strength |
| Controller → Other software | let Steam and similar take the pad over, and who currently holds it |
| Profiles → Sticks | dead zone, outer dead zone, sensitivity curve presets, circular range |
| Profiles → Triggers | stored effect (off / constant resistance), dead zone, trigger motor |
| Adaptive triggers | all 94 games, searchable, filtered by route; vibration presets load onto the pad from here |
| Lighting | effect, up to 5 colours, brightness, cycle time, react-to-rumble |

**Everything device-facing runs on a worker thread** (`gui/worker.py`) and requests cross as
signals. Calling a worker slot directly runs it on the caller's thread, which silently puts blocking
HID traffic back on the UI thread — that bug was written twice already.

**Apply vs save.** "Apply" writes the changed packets (164/165) and takes effect immediately;
"Apply and save" additionally sends 166, which Flydigi's SDK gives a 10 s timeout where everything
else gets 500 ms.

Confirmed on hardware: **an applied-but-unsaved change is lost when the pad sleeps** — not merely
on a power cycle. Applied lighting reverted after the pad idled out, and a profile renamed with
only "Apply" was gone after a hard power switch. So "apply" is working memory in the literal
sense, and anything meant to last needs the save.

That is why the two buttons do not share an enabled state. "Apply" follows *dirty* — is there an
edit not yet written. "Apply and save" follows dirty **or** *saveNeeded* — has anything been
written that has not reached flash. Binding both to dirty, as the first cut did, meant pressing
Apply immediately greyed out the only way to keep the change you had just made. The footer says
which of the two states you are in rather than leaving it to be inferred.

The label is spelled out rather than "Apply & save": a bare `&` in a button label is taken as a
mnemonic, swallowed, and drawn as an underline on the next character. The widget app escaped it as
`&&`; in QML it is simpler to avoid the ampersand.

**166 is now verified on hardware.** Slot 4 (factory, `data_version` 65535) was renamed to
`SAVETEST`, written with 164/165 and committed with 166; the pad was then switched off at its own
power switch, observed leaving the USB bus for 16 seconds, and woken with the logo button. The title
read back as `SAVETEST`. So the whole write path — apply, commit, survive a power cut — is proven
end to end, and the slot was restored from backup afterwards.

Two things fell out of that run:

  * **The active slot survives a power cycle too.** The pad came back running slot 4, the one it was
    switched to for the test, not slot 1. So "which profile is live" is itself flash state.
  * **The version tag did not move.** `read_status` reported `[23224, 65078, 65535, 65535]` before
    and after. We pass the config's own `data_version` precisely so the tag is left alone — which
    means it stays 65535 across a save, and `read_status`'s per-slot version cannot be used to spot
    a config we changed ourselves. Whether the firmware takes the tag from the command payload or
    from the blob's own field at 225..227 is not distinguishable from this run, because the write
    carried the same value in both. To make the tag useful as a cache key, something has to write a
    *new* one; presumably that is what Space Station's random-looking values are.

### Tests, and how to run them without hardware

`tests/fake_pad.py` answers reads, diffed writes, apply and save, and refuses a bad checksum by
staying silent exactly as the pad does — unchanged by the QML rewrite. The desktop tests come in
three layers, cheapest first:

```bash
for t in tests/test_{dsx,forza,mapping,monitor,relay}.py; do python3 "$t"; done  # backend, no Qt
python3 tests/test_models.py     # 108, headless -- no engine, no display
python3 tests/test_shell.py      # the window, loaded the way main.py loads it
python3 tests/test_qml.py        # QtQuickTest: real clicks on real delegates
```

Each skips with exit 0 when PySide6 is absent, so the backend run stays dependency-free.

Most assertions live in `test_models.py`, because most of the logic does. The QML layer exists for
what only a running view can answer, and it has to be QtQuickTest: **PySide6 cannot see an item
created by a delegate** — a Repeater and a ListView both really build theirs, QML counts them, and
`findChild` finds none. Nor can a test drive its own window: Qt only delivers synthetic clicks once
the *main* window is shown, so a page under test is instantiated inside the `TestCase`.

Two traps worth remembering, both of which cost real time:

  * `tryVerify(() => !button.enabled)` never sees a binding update. Wait on the model with
    `tryCompare(App.lighting, "dirty", false)` instead — a closure over a binding has no notify
    signal to watch, so it times out on work that already succeeded.
  * recursing `QObject.children()` over QML objects aborts the interpreter with a shiboken
    assertion. `findChildren` is fine.

`qmllint` is clean and worth keeping that way:

```bash
tools/generate-qmltypes
qmllint -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml
```

`pyside6-project build` would be the framework's own route, but it shells out to
`pyside6-metaobjectdump`, which *parses the source* and so cannot see `constant=True` — every
constant property then reads as merely read-only and qmllint reports 99 bogus "binding might not
update" warnings. `tools/generate-qmltypes` dumps the live `QMetaObject` instead, where the flag is
correct. With those gone it immediately found a real bug: `pageStack.currentItem` is typed
`QQuickItem`, which has no `title`.

## Next

Agreed feature list, roughly in the order it came up. Each is a fresh-context-sized piece of work.

### The profile blob has more in it than we edit

Everything in this section is **already carried through** by `flydigi/mapping.py` — we read and
write the whole 840-byte blob, so these are accessors and a page, not new commands and not new
risk. Offsets from `MappingConfigParser.cs`, struct names from `data.model.config/`.

**J1 is validated by hand, not just by byte.** `tools/stick-feel` applies a curve and drives the
grip motors from the stick's own output, so the buzz is the reading — no terminal to watch. A 60%
dead zone compiled to `[0, 0, 0, 0, 26, 56, 87, 118, 150]`, and the bottom of the travel went
audibly, tangibly silent before the rumble came in. Compiler → bank → firmware → hand, with nothing
assumed in between.

The dead zone read as somewhat smaller than 60% by feel, which is expected rather than suspicious --
eyeballing what fraction of a stick's throw you have used is not something a hand is good at. Two
real effects push the same way, and both are worth knowing before anyone reads too much into a
felt measurement:

  * **A circle is not a constant magnitude in Rectangle mode.** The diagonal reaches 1.19 where the
    axes reach 1.00, so tracing a physical circle just under the threshold pokes over it at the 45°
    points while a straight push at the same radius does not. That is an artifact of deriving
    magnitude as `sqrt(x²+y²)`, not of the dead zone.
  * **The threshold is in the output domain, not the travel domain.** Sticks saturate electrically
    before the mechanical stop, so 60% of range arrives before 60% of the throw.

Measuring the threshold *properly* needs the raw stick position to compare the curved output
against, and xpad only ever shows the curved one. The vendor input stream is the place to look —
`flydigi/motion.py` already parses it for the IMU at ~300 Hz, and the raw axes are plausibly in the
same report. Worth doing before anyone tries to calibrate a curve by numbers rather than by feel.

**J1. Joystick dead zones, curves, circularity. — DONE, backend and GUI.**
`stick()` / `set_stick()` and the compiler `stick_bank()` are in `flydigi/mapping.py`; the page is
`gui/qml/pages/SticksPage.qml`, over `StickModel` in `gui/models/profile.py`. Verified end to end on
hardware: a 25% dead zone compiles, writes in two packets and reads back byte-identical. Two blocks:

  * **offset 109**, 7 bytes per stick (left 109, right 116):
    `type, center, p1.x, p1.y, p2.x, p2.y, end`, on a **0..127** scale.
    `type` is `JoystickSensitivityType {Default=0, Quick=1, Slow=2, Custom=3}` — labelled
    Default / **Instant** / **Delay** / Custom in the UI. p1/p2 are the interior breakpoints of a
    four-node **polyline**, not Bezier controls; the editor draws three straight segments and
    samples with a plain lerp.
  * **offset 790**, 12 bytes per stick (left 790, right 802), `m_fdg_macro_joy_extra_v2_struct_t`:
    `type, bank[9], isRound, end` — the same curve resampled to nine evenly spaced points, plus
    `JoystickCircularityType {Rectangle=0, Circular=1}`. Bank values are **biased by 50**: 50 is no
    output, 150 is full, and a straight line is evenly spaced between them.

**The pad plays the nine-point bank, and nothing else in either block.** Established on hardware
with `tools/joystick-curve-probe`, which writes a value that ought to silence the stick and watches
evdev to see whether it does. Five runs against a baseline, each proving somebody was working the
pad — see "the liveness trap" below:

| what was written | stick output |
|---|---|
| nothing (baseline) | normal, 6778 deflections, reaches 100% |
| **bank at 790+1..9 flattened to all 50** | **completely silent** |
| core polyline at 109 flattened, **and type set to Custom** | normal, reaches 100% |
| `edge` byte at 801 = 236 | normal, reaches 100% |
| `edge` byte at 801 = 90 | normal, reaches 100% |
| `edge` byte at 801 = 100, a degenerate step | normal, reaches 100%, smooth from 2.3% |

**"Maybe the pad applies it and then recalibrates, opaquely?"** A fair objection, and not an idle
one — command 3 reports stick auto-calibration and the rebound algorithm both *enabled*, so there
really is a normalisation stage in there. It is ruled out, and the last two rows are why. The tests
that matter are not the ones that nudge the curve but the ones that make it **degenerate**:
`--flat-core` sets the polyline to zero output across the whole range, and `edge = 100` collapses
the end node onto the start node. No renormalisation can rescue either — rescaling a constant zero
gives zero, and rescaling a step function gives a step function, not the smooth sweep from 2.3%
upward that both runs actually produced. Whereas the flat *bank* silenced the stick and nothing
rescued that, which is the same argument in reverse. Nudge tests would have been ambiguous here;
degenerate ones are not.

So the core polyline, the type byte, `center` and `edge` are **the source form the bank is compiled
from** — not leftovers. The renderer builds a four-node polyline out of them and samples it, and the
bank is that sampled output. This is the same architecture as the lighting config: the pad has no
curve evaluator any more than it has an animation generator, and in both cases the host computes and
the pad plays.

**The compiler, straight out of `index-DM6mSbRo.js`** (constants `Pe=127, Ie=100, ze=50, V=9,
ce=-50`), which is what a GUI has to reimplement rather than invent:

```js
Ke(start,end,p1,p2) = [start, p1, p2, end]        // the node list
c={x:0,y:0}, y={x:100,y:100};
center > 0 ? c.x = center : c.y = -center;         // start node: dead zone, or Offset
edge   > 0 ? y.x = 100-edge : y.y = 100+edge;      // end node: outer dead zone, or ceiling
qe(x0,y0,x1,y1,x) = y0 + (y1-y0)*(x-x0)/(x1-x0)    // plain lerp, three straight segments
for X in 0..8:  bank[X] = clamp(round(sample(100*X/8)), -50, 100) + 50
```

Everything is in 0..100 percent, including `center` and `edge`; `p1`/`p2` are stored as 0..127 and
converted with `×100/127`. `type` is only a preset picker for `p1`/`p2` — Default (64,64), Instant
(64,96), Delay (64,32), all with `p2 = (127,127)` — and any manual edit to a node forces it to
Custom.

Note what this does *not* tell you, and why the hardware runs above were still needed: Space Station
writes both blocks unconditionally, so nothing in the app reveals which one the pad reads. The app
answers "what is computed from what"; only the pad answers "what does it act on".

Two consequences worth the space:

  * **A GUI must compute the bank.** Offering a dead-zone slider that writes `center` would move a
    number, dirty the profile, write successfully — and change nothing the hand can feel. Whatever
    curve the UI offers has to be sampled into nine points the way Space Station samples it:
    `clamp(round(output_percent), -50, 100) + 50` at x = 0, 12.5, … 100.
  * **It makes the sign question much less urgent.** `center` and `edge` are the two fields whose
    negative encoding is ambiguous, and neither reaches the firmware. They matter for what Space
    Station *displays* if someone opens a profile we wrote, not for how the pad behaves.

**`isRound` is the exception, and it is firmware-side.** Predicted, then measured: circularity is a
two-dimensional property and the bank is a one-dimensional magnitude curve, so the bank *cannot*
express it and something in the pad has to. Rolling the stick around its rim and into all four
diagonal corners:

| `isRound` | furthest corner | per-axis |
|---|---|---|
| 0, Rectangle (factory) | magnitude **1.19** | 1.00, 1.00 |
| 1, Circular | magnitude **1.00** | 1.00, 1.00 |

Rectangle lets the diagonal run past the unit circle; Circular pins it to exactly 1.00, i.e. about
0.71 per axis. **That is a user-visible bug generator, and it has bitten in practice**: a game that
tests each axis against a threshold — "run if |x| > 0.8" — sees 0.71 on the diagonal and stops the
character running diagonally while the stick is hard over. Report it as a trade-off in any UI rather
than as a neutral preference.

Note the measurement, because the textbook number is wrong: a true square output region would put
the diagonal at **1.41**, but the stick's own gate is octagonal and never reaches it — Rectangle
tops out around **1.19** in practice. What separates the two modes is only whether the diagonal is
allowed past the unit circle at all. A probe thresholding at 1.25 reports both modes as round, which
this one did until it was corrected.

So the live/inert map for the two blocks is complete: **bank and `isRound` reach the firmware; the
core polyline, the type byte, `center` and `edge` do not.**

**The liveness trap, which cost two runs.** A stick nobody is touching and a stick the pad has
silenced produce byte-identical evdev traces: nothing at all, because evdev is event-driven. The
first two silencing runs were therefore unreadable — the answer looked like a result and was
indistinguishable from an empty room. The probe now starts on a button press and counts button
events throughout the window; buttons are an input no stick curve can suppress, so zero axis events
*alongside* live button events is proof. Any future hardware probe with a "nothing happened"
outcome needs the same treatment.

Four things the implementation had to get right, none of them guessable from the offsets:

  * **`center` is a sentinel as well as a number.** The firmware forces it to exactly **127** when
    the stick is mapped to keyboard, mouse or d-pad — so 127 means "this is not a stick", not "a
    dead zone of 127". `joystick_curve()` reports `is_stick` rather than handing a UI a number to
    draw. Space Station's renderer guards identically, zeroing anything over 100.
  * **`center` and `edge` are not dead zones — each is two controls in one byte, chosen by sign.**
    They position the curve's start and end nodes, and the sign says which *axis* the node slides
    along (`index-DM6mSbRo.js`: `r>0?c.x=r:c.y=-r, n>0?y.x=100-n:y.y=100+n`). Positive `center`
    moves the start node along x, so input below it produces nothing — a dead zone. Negative moves
    it up y, so the smallest input already produces `-center`: Space Station labels that end of the
    slider **"Offset"**, and it exists to cancel a *game's* dead zone rather than add one. Likewise
    positive `edge` is an outer dead zone; negative lowers the end node so full travel only reaches
    `100+edge`, an output ceiling. The two locale keys behind the one slider are literally
    `"deadzone": "Dead zone"` and `"compensastion": "Offset"` (their typo).

    So there is no negative dead zone, and both halves are wanted — Offset especially, since
    cancelling a game's built-in dead zone is a thing Linux has no other tool for. **We write only
    the positive half**, because the SDK's reader folds a byte over 127 to `127 - byte` at four
    sites while every one of its writers emits a plain two's-complement cast: −20 is written as 236
    and reads back as −109. Positive values encode identically under both readings, so
    `set_joystick_curve` accepts 0..100 and raises on the rest rather than picking one of two
    incompatible encodings.

    **Settling it does not need Space Station.** What matters is how the *firmware* decodes the
    byte, not what their app writes — and the pad will answer directly. Write 236 to byte 110 with
    apply-and-no-save, then sweep the stick and watch evdev: an Offset of 20 shows up as the axis
    jumping straight to ~20% on the smallest deflection, where −109 would pin it near full and an
    unsigned 236 would swallow most of the travel. Repeat with 147. The three outcomes are
    unmistakable, and leaving the save off means the pad forgets the experiment the next time it
    sleeps.
  * **Core `end` is not the UI's "Edge" and is left read-only.** Edge writes the *extra* block's
    trailing byte, a different protobuf field. Nothing in Flydigi's application ever assigns core
    `end`, and their reader corrupts it above 127. The pad ships with 127 there.
  * **The bank must be exactly nine.** Flydigi's writer loops over however many points it is given
    with no bound, so a tenth lands on `isRound`, an eleventh on `edge`, and a thirteenth starts
    overwriting the other stick. `set_joystick_shape` refuses instead.

The type byte is written into **both** blocks, because the SDK regenerates the extra block's copy
from the core one on every write and a blob where they disagree is a state no vendor tool produces.
Whether the *firmware* branches on it at all, or whether it is a UI label and the bank is what
actually plays, is still unknown — testable by writing a bank that sharply disagrees with the 109
curve and feeling the stick.

**J2. Gyro mapped to a stick, on the pad.** Works in any game with nothing running, which on Linux
is otherwise Steam Input only. **Offset 137**, 8 bytes, `m_fdg_macro_motion_mapping_struct_t`:
`type, keyid, method, zero, sensity_x, sensity_y, mode, keyid_ext`, where `type` is
`MotionMapType {Off=0, LeftJoystick=1, RightJoystick=2, Mouse=3}` and `mode` is
`MotionUseMode {FPS=0, Racer=1}`. Smoothing curve at **offset 830**, 6 bytes. The pad's own UI warns
that enabling this lowers the polling rate. `MotionMapType.Mouse` is not a pad feature — see below.

**J3. Finish the trigger travel block. — done, and it was a bug, not a gap.** **Offset 123**,
7 bytes per trigger, same 7-byte struct as the joystick core block but on a **0..255** scale, and
with no sign convention at all — the parser reads `zero` and `end` raw where the joystick folds
them.

The old `set_trigger_curve()` wrote `zero` and `end` and left the two points where they were, which
produces a blob no vendor tool would ever emit: breakpoints stranded outside the window they are
meant to bound. Flydigi writes six bytes from two numbers —
`Point1 = (Start, Start)`, `Point2 = (End, End)` in `ControllerRepository.cs:885-890` — and the
factory blob agrees exactly: `0 0 0 0 255 255 255`. The setter now mirrors by default, sorts the
pair, and allows them to be equal, since Space Station's range slider passes neither `pushable` nor
`allowCross` and dragging one handle onto the other is reachable. `mirror_points=False` is there for
a caller deliberately shaping the curve.

`type` stays read-only: it is a bare `int32` the SDK round-trips and never decodes, it reaches no
UI, and the pad ships with 0. **And a warning before building any UI for this**: on an Apex 5 Space
Station never shows this block at all — `IsSupportForceTrigger` routes the same two UI numbers into
the force-trigger block at 195/215 instead — so anything we write into 123..137 survives every
subsequent edit in their app, and the only repair is a whole-profile "Restore default".

**J4. Persist the vibration bind — and an open question, answered.** PROGRESS.md used to say the
profile's force-trigger `bind` sub-struct "may be" the stored form of command 82 but "the counts do
not match". They do. `ParseTriggerConfigToArray` writes, at **offset 185** + 20 per side:
`Type, bind.Type, bind.Filter, bind.Scale, bind.Param[5], MixedBorder, Param[10]`. Live 82 takes
3 + 4 parameters; the stored form is 3 + **5** — the same structure with one spare byte. And the
writer sets `bind.Type = (Type == 5) ? 2 : 0`, so bind type 2 appears exactly when the stored effect
is `Vibration`. The per-game preset *is* effect type 5 and can be made to survive a sleep instead of
needing re-application. `set_trigger_effect()` writes `[+0]` and `[+10..+20]` and leaves the bind
alone, so that is the gap.

**J5. The second trigger-motor gear.** **Offset 154**: master enable, then per side two 7-byte
gears (linear and micro), `m_fdg_motor_trig_setting_struct_t = {type, min, max, filter, vibr_limit,
scale, time_limit}`. `mapping.trigger_motor()` reads `min/max/scale` of the linear gear only. Trivial
in code; what `filter`, `vibr_limit` and `time_limit` do needs a bench sweep.

### Small commands worth having

**S1. Reset one profile slot to factory** — `ResetMappingConfigByCfgIdCommandFactory`, **175**,
`[4]=3, [5]=cfgId, [6]=crc`. A 10 s timeout like the save, so it is a flash operation. Gated on
`ResetAllMappingUsable`, which the SDK sets unconditionally in the NewXInput branch — our mode.
Gives us the stock app's "Restore default". Destructive: test on slot 4 or the fake pad first.

**S2. Controller nickname** — write `UpdateNicknameCommandFactory` **24**, read
`ReadNicknameCommandFactory` **2**. Self-verifying, and it makes a two-pad setup legible. Note the
decompiled writer puts the CRC at `[6]`, which would overwrite the second name byte — assume it
belongs at `5 + len`.

**S3. Cooperative lock** — `AcquireControllerCommandFactory` **28**, `[5]=acquire`, `[6..25]` an
ASCII tag; read the current holder from command **16**. Advisory only. **This also closes an open
question in PROTOCOL.md §5:** it is *not* a precondition for trigger commands — the SDK never calls
it before `SetForceTrigger`, and our hardware tests already prove 81/82 work without it.

**S4. Device settings — the sub-command map.** Command **19** is a generic "set feature N":
`[4]=4, [5]=subId, [6]=value, [7]=crc`, ACK matched on `data[2]==19 && data[5]==subId`.

| sub | feature | sub | feature |
|---|---|---|---|
| 1 | **Quick-switch config** — `FN + A/B/X/Y` picks a profile, on the pad, nothing running | 6 | joystick auto-calibration |
| 2 | Xbox home button (XInput-gated; unreachable in our mode) | 7 | joystick rebound |
| 3 | motion debounce | 8 | status bar always on |
| 4 | mapping switch (no UI string; *not* the third-party toggle) | 9 | off screen |
| 5 | joystick debounce | 10 | audio (gated on the `AudioUsable` bit from command 3) |

Standalone, all `[4]=3, [5]=value, [6]=crc`: **20** report rate `{1000=1, 500=2, 250=4, 125=8}`,
**21** joystick precision, **22** joystick sensitivity, **23** sleep time in minutes, **29** restart.

**Do sub-ID 1 first** — quick-switch is the only one here that gives a Linux user something
otherwise unobtainable: switching profiles from the pad with nothing running.

### Belongs to another pad, not to this one

Not dead — just gated on a device we do not drive yet. A Vader 4 Pro is to hand, so the ADC item in
particular is testable the moment multi-pad support exists; see "Multiple pads" below.

  * **ADC / stick calibration** — `CalibrationAdcCommandFactory`, command **240**,
    `[5] = start ? 1 : 2`, and a NewXInput builder does exist. `HasAdcChip` is set on exactly one
    controller in the whole factory: `GenerateControllerVader4`. So this is a **Vader 4 feature**,
    and a good one — recalibrating stick centres is the classic fix for drift. Sending it to an
    Apex 5 is probably a harmless no-op, but there is no reason to.
  * **The K6 trigger family** — commands 83/85/87 belong to `DeviceCode == "k6"`, the Apex 6. The
    Apex 5 is `k5` and `SetForceTrigger` is its family, which **closes the other open question in
    PROTOCOL.md §5**. `K6TriggerMode.Local` is not a route to autonomous effects on *this* pad.
  * **The wheel block (183..185)** — `m_fdg_macro_lunpan_struct_t {type, rev}`. `IsSupportWheel` is
    never set for the Apex 5. Keep carrying the bytes; build UI only for a pad that declares it.

### Ruled out, so nobody looks again

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
  * **Firmware update — deliberately not implemented, and command 31 must never be sent.**
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

    The scale is the other half of the argument. `ChipModule` is `{ChipMain, ChipScreen,
    ChipTrigger, ChipDongle}` — and the info reply carries ADC and NearLink versions too — across
    **two silicon vendors**, `ChipType.{Telink, Wch}`, with `ChipId.{WCH_582, WCH_547, WCH_571}`
    selecting among WCH parts. Implementing this means implementing two third-party bootloader
    protocols correctly, first time, with no recovery when wrong. The payoff is a convenience wanted
    perhaps twice in a pad's life. It is not worth one brick, and it would cost the hardware
    everything here is validated against.

    **If a firmware update is genuinely needed, use real Windows hardware — not a VM.** Flashing
    drops the device off USB and brings it back as a bootloader with a different identity, and that
    re-enumeration is precisely where USB passthrough loses a device: mid-flash. Whether the Apex 5
    has a button-combination recovery mode is **unknown**, so assume it does not.

### Multiple pads

Wanted later, not now. A Vader 4 Pro is on the desk, and the two are closer than "fewer features"
suggests: the SDK gives Vader 4 26 keys to the Apex 5's 27, with the same six extra buttons placed
differently. What actually differs is the trigger technology —

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

The work would be almost entirely in `flydigi/`: per-model key tables, offsets and capability flags.
`gui/models/` only knows `mapping.APEX5_KEYS`. The prerequisite is the device-type guard — see
`flydigi/device.py`, which today matches on vendor id alone and would happily write an Apex 5 config
to a Vader 4.

**Mode switch (27)** — `BluetoothMode {Switch=1, Xbox=2, Flashplay=3, DInput=4}` — is real and
`IsSupportNs` is true, but it changes the report descriptor and probably the hidraw node. Treat as a
one-way trip until proven otherwise; it is the one item here where a bad guess costs the session.

**1. Screen image / GIF upload.** `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`,
`TestScreen`, `OffScreen`, `ReadScreenSetting`. Note Space Station only offers this **over a wired
connection** — worth assuming the dongle cannot carry it, and testing wired first rather than
debugging a dongle failure that is by design. The image encoding may live in the Electron layer
rather than the SDK, so check `asar/` as well as `decompiled/`.

**2. ~~A real battery reading.~~ Settled: there is not one, in this SDK.** Every path resolves to
the same 4-bit nibble. `HeartBeatCommandFactory`'s NewXInput branch is
`Battery = (data[i] >> 4 == 1) ? 6 : (data[i] & 0xF)`, and the XInput and DInput branches do the
identical thing at `data[23]` and `data[10]`. `ExtraInfoCommandFactory` carries no battery field at
all. The only richer variant is a `DeviceCode == "f4"` special case remapping raw 3→2 and 5→6. So
x/8 is what Space Station itself shows; if a percentage exists it is in the dongle or the input
report, not the command set, and that is where to look.

What the same multi-packet reply *does* carry, in order after device type and connect type: MAC
(4 bytes, reversed), the battery nibble, chip type, motion chip type, then seven BCD firmware
versions — main, dongle, switch/SI, trigger, screen, ADC, NearLink. `IsAckFinished` is
`data[4] > data[3] || data[4] == data[3] - 1`, so it is fragmented. That is a better device page
than the one we have, at no protocol risk.

**3. Charging dock, and syncing it with the pad.** **The newer Apex 5 dock is on the desk**, so this
is blocked only on decompiling the DLL, not on hardware. (The Vader 4's older dock is probably a
dumb USB hub with a charger — do not assume it speaks anything.) `Flydigi.ChargerSdk.dll` and
`Flydigi.CoolerSdk.dll` are in `bundle/` and **not yet decompiled** — that is step one
(`~/.dotnet/tools/ilspycmd -o decompiled/Flydigi.ChargerSdk bundle/Flydigi.ChargerSdk.dll` in the
`wine-arch` distrobox). The Electron locales already show what the feature looks like:
`cd2_charger_led_type_{breath,custom,default,diagonal_flow,gradient,pulse,rainbow,wave_gradient}`,
and `cd2_led_sync` — "Keep the lighting mode of the controller and dock in sync". So the dock has
its own effect set plus a sync toggle, which is the integration the user wants.

**4. "Allow third-party apps to take over mappings" — DONE, and it is what makes Steam recognise
the pad.** Command 16 reads it, command 17 writes it, and the switch is on the Controller page
behind the firmware gate. A pad-side setting, not Steam's. Space
Station's own words:

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
    recognition comes from the acquire, not from any change of USB identity — which is why the
    earlier guess that this was about descriptors or double-remapping was wrong.

**Steam then lists the pad twice, and that is not our bug.** Reported on Windows as well as here.
Both paths are legitimately supported and Steam does not merge them:

```
xpad on 3-4:1.0  -> event-joystick -> "generic XInput controller"
Steam hidapi     -> hidraw4        -> "Apex 5"
```

`steamwebhelper` holds both hidraw nodes open while this is on. Nothing sent to the pad changes it;
the toggle only makes the second path exist.

**Mostly cosmetic, though.** Enabling Steam Input for the pad makes Steam grab the physical device
and hand the game a single virtual controller, so the duplicate is visible in Steam's settings list
and not to anything launched through Steam. It matters for games started outside Steam, and it
matters if Steam Input is off — which is exactly the state Tier 4 requires.

If it does need removing, unbinding xpad is the local fix
(`echo -n "3-4:1.0" | sudo tee /sys/bus/usb/drivers/xpad/unbind`), and it does not survive a wake
since re-enumeration rebinds. **Do not make it permanent with a udev rule**: the evdev node is where
everything else here reads sticks and buttons — `tools/flydigi-ds5` relays them into the virtual
DualSense, and `joystick-curve-probe` and `stick-feel` both depend on it.

**Tested, and it is a clean trade rather than a catch.** With the flag on:

| | third-party off | third-party on |
|---|---|---|
| Steam's view | generic XInput controller | **Apex 5** |
| standard gamepad path (xpad / evdev) | works | **dead** — the XInput entry accepts no input in Steam |
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

**There is no plan attached to this, and an earlier draft of this section wrongly implied one.**
Reading sticks from here instead of from evdev was proposed to stop Tier 4 conflicting with the
third-party toggle — but Tier 4 requires Steam Input *off* and the toggle exists to let Steam take
over, so they conflict a level above the input source and were never going to be used together.
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
What is missing is the reader — command **16**, `ReadRawDataReportStatusCommandFactory`, which
decodes `data[5..8]` as the four transport flags and `data[9]` as the third-party flag — plus a UI
toggle.

**One gate to honour.** `ControllerBusinessService.cs:1128` only offers this for `DeviceCode == "k5"`
when the firmware is at or above **7.0.3.0**. Below that, hide it.

`EnableMappingSwitchCommandFactory` (19 sub-function 4) is something else entirely and has no
English UI string at all — the earlier guess that it was this feature was wrong.

Also relevant to the "extra buttons and gyro" part: `DeviceMaskCommandFactory` (**16**) takes
`maskController`, `maskMedia`, `maskGyro`, which is how the pad decides what to expose to the host.

### Command 3: the whole settings block in one read

Found while chasing the above, and it covers most of item 6 by itself. `ReadHardwareFunctionStatus`,
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
| off screen | yes | off | | audio | **no** | — |

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
| Motion debounce | 3 | **unsupported** | none, in any of the ten locales — only a dangling `IpcCommandEnum_EnableMotionDebounce` |

So Space Station's debounce toggle is sub-id 5. Sub-id 3 needs no UI.

**Precision is device state, not profile state**, so it does not make the profile's curve bytes
multi-scale: 21 and 22 are standalone commands read back through command 3, while the control points
live in the 840-byte blob, and all four factory profiles carry identical ones. The stick's 0..127
and the trigger's 0..255 therefore read as two fixed normalisations of the stored format, with
bitness changing only how finely the output is quantised. Assumed, not proven — falsifiable in a
minute by writing a different precision and re-reading a profile.

**4b. An editor for the vibration bind.** Tier 1 is one bind — game rumble drives the trigger
motors — and each "supported game" is a **preset** of numbers for it: `vibType`, `vibFilter`,
`pwmScal`, and `vibParams` (stroke, pressure, strength, frequency per side). That is a sensible
design; the labels just have to say so, or it reads as a per-game integration like the other four
routes. Wording was fixed; the numbers still cannot be edited from the GUI, only through
`tools/flydigi_cmd.py bind`.

A lead worth checking first: the profile blob's force-trigger section (185..225) holds a `bind`
sub-struct of **type, filter, scale + 5 params**, and the live bind command (82) takes bindType,
filter, scale, stroke, pressure, strength, frequency. Same fields, with one param byte spare.
**Unverified** — the counts do not match exactly — but if it is the persistent form of the same
setting, an editor could write the bind into the profile so it survives a sleep instead of needing
re-applying every session. Test by applying a game bind, reading the profile, and diffing 185..225.

**5. Auto-launch per game — the daemon.** This is what the games tab is missing, and what its
"Preference" column should really be: a per-game **Auto** toggle meaning *when this game starts, do
the right thing for it without me*. Concretely, on detecting the game:

  * vibration → load its preset onto the pad
  * telemetry / monitor / ps5 / bespoke → start `flydigi-forza`, `flydigi-monitor`, `flydigi-ds5`
    or `flydigi-dsx`, and stop it again when the game exits

`flydigid` already does detect-and-apply for the vibration route, so the work is generalising it to
launch and supervise the other four, plus a UI toggle and somewhere to persist it
(`gui/triggers.py` already writes `~/.config/flydigi/games.json`).

**How Space Station does it**, from `AdapterTriggerRunner.CheckGameRunning` — worth knowing before
inventing something cleverer, because it is deliberately dull:

  * a loop with `Task.Delay(1000)`: plain **1 Hz polling**, no WMI event watcher, no ETW
  * `GameHelper.IsProcessRunning` wraps `Process.GetProcessesByName` behind a **5 second cache**,
    so the poll is cheap even with the whole game list to check
  * tries `ProcessGameName` first, then each entry in `ProcessGameNames`, and latches whichever
    matched
  * separately checks whether the mod process is already running, so it does not start it twice
  * `ModStartType` says where the mod executable lives: 0 = game directory + mod path,
    1 = Space Station's own directory + mod path

**Detection covers every game.** All 94 entries carry a process name — 72 have only the singular
`processGameName` with an empty `processGameNames` list, which is why `games.process_index()` reads
both. The plural list is for one game shipping several executables — typically one per graphics API —
not per-store variants: Apex Legends (`r5apex` / `r5apex_dx12`) is the only entry genuinely using
it, 21 others merely repeat
the singular, and most multi-store titles have none because their executable name is the same
everywhere. Polling can reach the whole list, so `flydigi-run` is a convenience (instant, no 1 Hz lag,
survives a renamed process) rather than a requirement for coverage.

So 1 Hz is enough and `flydigid`'s approach is already the right one. Two things they do not have
to deal with that we do: Proton wrappers carrying the game's path in their cmdline (see
`monitor.find_process`, which requires the PE to actually be mapped), and no equivalent of their
"launch the game from our UI" path — which is what `flydigi-run` replaces.

One thing that will bite:

  * **Per-game mode preference** — six titles support both Flydigi's mod and PS5 mode (Cyberpunk
    2077, Death Stranding DC, Jedi Survivor, Spider-Man Remastered, Miles Morales, Uncharted 4).
    Auto has to know which to start; the storage exists, the UI does not.

**Small and worth doing first:**
  * ~~verify command 166 on hardware~~ — **done, it works**; see "Apply vs save" above.
  * `UpdateSleepTimeCommandFactory` (**23**) — the pad ships at **15 minutes**, read straight off
    command 3. Raising it would stop the pad dropping out mid-session, which has interrupted nearly
    every test; and since sleeping means leaving the USB bus entirely, the drop-out is not a nuisance
    to work around but a disconnect to recover from.
  * macros (`ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable`); the profile blob at
    230..768 is already carried through untouched.

## Space Station exclusives — what is done and what is not

All command factories are decompiled under `decompiled/Flydigi.ControllerSdk/`.

| Feature | Commands | State |
|---|---|---|
| Mapping profiles | status **161**, apply **162**, read **163**, write **164**/**165**, save **166** | **done** — `flydigi/mapping.py`, `tools/flydigi-mapping`, GUI |
| Vibration + triggers | inside the profile blob | **done** — same module |
| RGB / lighting | read **167**, write **168**/**169** | **done** — `flydigi/lighting.py`, GUI |
| Macros | `ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable` | not started; blob at 230..768 is carried through untouched |
| Screen image (pad + dock) | `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`, `TestScreen`, `OffScreen` | not started; **wired only in Space Station**; encoding may live in the Electron layer |
| Device settings | read them all with **3**; writes are the 22 factories in `command.setting/` | not started — but command 3 already returns supported/enabled bits plus sleep time, report rate and stick precision/sensitivity in one reply, see "Next" |
| Dock / cooler | `Flydigi.ChargerSdk.dll`, `Flydigi.CoolerSdk.dll` in `bundle/` | not decompiled — now in scope, including `cd2_led_sync` (dock/pad lighting sync) |

### Config blobs, both verified on hardware

Mapping profile, 840 bytes (42 packets of 20), protocol v3.1:

```
0..2 version   2 package count   3..13 legacy LED   13..109 key table (32 x 3)
109..123 joystick curves      123..137 trigger travel curves
137..145 motion               145..154 grip vibration (master + 2 x 4)
154..183 trigger motors       183..185 wheel
185..225 force trigger (2 x 20)   225..227 data version   230..768 macros
770..790 title UTF-16LE       790..840 joystick extra, macro cycle, motion curve
```

Lighting, 380 bytes (19 packets of 20):

```
0..2 version   2 click feedback   3 loop start   4 loop end   5 cycle time
6 brightness   7 LED count (12)   8 mode   9..20 reserved
20.. frames of `LED count` RGB triples -- 10 x 12 on an Apex 5
```

Config structures for mapping/macro/RGB are already decompiled as `m_fdg_*_struct_t` types.

### Factory defaults, read off the pad

All four slots of an untouched Apex 5, byte for byte. Far more use than the fake pad's `0xFF` fill
when writing accessors for a block, because it shows what a *valid* value looks like — and all four
slots are identical, so anything here is the factory shape rather than one profile's taste.

```
109 joystick core   0   0  63  63 127 127 127   |   0   0  63  63 127 127 127
                    type zero p1x p1y p2x p2y end        (same, right stick)
123 trigger travel  0   0   0   0 255 255 255   |   0   0   0   0 255 255 255
137 motion          0  12   0   4  25  20   0   0
145 grip vibration  0 | 0  60 255  50 | 0  80 255  50
154 trigger motors  0 | 1 30 80 5 1 50 0 · 255 40 120 5 0 50 0 | (same, right)
183 wheel           0   0
185 force trigger   0   0  10  10 100   1 255  70   0 | 0 0 0 0 0 0 0 0 0 0
790 joy extra L     0 | 50 62 75 87 100 112 125 137 150 | 0 | 0
802 joy extra R     0 | 50 62 75 87 100 112 125 137 150 | 0 | 0
814 macro cycle     255 255 255 255 255 255   3   3   3   3   3 255 255 255 255 255
830 motion curve    0  63  63 127 127 127
836 padding         255 255 255 255
```

What this settles without a single guess:

  * **Sticks and triggers do not share a scale.** A stick's curve runs to **127** (`p2 = 127,127`,
    `end = 127`), a trigger's to **255**. A single "0-100%" slider mapped to bytes would be half
    range on one of them.
  * **The trigger's p1/p2 are `0,0` and `255,255`** — the identity line — where the stick's are
    `63,63` and `127,127`, again the identity line on its own scale. So both are genuinely curve
    control points, and the factory setting of both is "no curve".
  * **The motion smoothing block at 830 is the joystick block minus its type byte**: `zero, p1.x,
    p1.y, p2.x, p2.y, end`, and it carries the identical `0 63 63 127 127 127`. Same code can read
    all three.
  * **The trigger-motor gears are two 7-byte structs per side**, and the second one leads with
    **255**, not 1 — so `type` there is an enable flag stored inverted, like every other switch in
    this blob. `trigger_motor()` reads the first gear only, which is the enabled one.
  * **The force-trigger bind is populated at rest**: `bind.Filter = 10, bind.Scale = 10,
    bind.Param = [100, 1, 255, 70, 0]` with `Type = 0` and `bind.Type = 0`. So J4's claim that the
    stored bind mirrors live command 82 has real numbers behind it to diff against.
  * **`joy extra`'s bank[9] is a preset curve, not a blank.** `50 62 75 87 100 112 125 137 150` is
    evenly spaced, i.e. the straight line, with 100 at the centre point — so 100 reads as unity and
    the bank is a gain per zone rather than an output level. `isRound = 0` is Rectangle.


## Hard-won facts worth not rediscovering

  * **Report id is `0x03`** on the vendor interface, not the `6` the decompiled
    `TakeEndpointByDevice()` suggests. Find the node by report-descriptor prefix `06 a0 ff`; it moves
    between wired and dongle.
  * **Wine maps game PEs at their image base** (`0x140000000`), same as Windows, so Flydigi's memory
    offsets work unmodified.
  * **Never match a game process by cmdline alone** — Steam/Proton wrappers (`reaper`, `bwrap`,
    `pv-adverb`, `steam.exe`) all carry the game's path. Require the PE to be mapped.
  * **Effects persist in controller state** until changed; there is no timeout.
  * **The pad discards unsaved config when it sleeps.** Not just on a power cycle — idling out is
    enough, observed with lighting. Applying is working memory; command 166 is what makes it last.
  * **`effects.rumble()` must use `wait=0`** when driven continuously, or the 100 ms ACK wait puts
    the motors far behind.
  * **Steam Input must be off** for Tier 4 — it masks the pad and breaks DualSense semantics.
  * **A sleeping Apex 5 leaves the USB bus.** It does not go quiet on HID — it disconnects, wired
    included: `usb 3-4: USB disconnect, device number 27` with no matching connect, no `37d7:2501`
    in `lsusb`, no hidraw node. So "the pad is asleep" and "the cable is dead" are the same symptom
    at this level, and `find_device` raises `DeviceNotFound` rather than any read timing out.
    Pressing a button re-enumerates it, which is why node numbers change on reconnect — resolve by
    name/descriptor, never by path.
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
  * **Never combine a `pkill -f` with the relaunch in one shell command** — the pattern matches the
    shell running it and kills the session (exit 144). Two separate commands, and the `'[p]attern'`
    bracket trick.

## Environment

- Host: Aurora DX (nvidia-open), Fedora 44 atomic, KDE/Wayland
- `wine-arch` distrobox (Arch + wine-staging 11.14, winetricks, innoextract, dotnet-sdk 10,
  ilspycmd, sfextract, nodejs). Created with `distrobox create --name wine-arch --image archlinux:latest --nvidia`
- Wine prefix: `~/.local/share/wineprefixes/flydigi` — Space Station 4.2.1.4 installs and runs
  (UI connects to its service over the named pipe), but **does not detect the controller**
  under Wine. Not needed; kept for reference only.
- Controller: wired. `hidraw3` = keyboard/mouse composite, `hidraw4` = vendor command interface.
  Nodes are `0666`, no udev rule needed.

## Repo contents

| Path | What |
|---|---|
| `PROTOCOL.md` | Full wire protocol + hardware verification results |
| `flydigi/` | Library — `device.py` (transport), `blobs.py` (packetised config transfer), `effects.py` (live trigger commands), `mapping.py` (profiles, remapping, vibration, stored triggers), `lighting.py` (RGB), `games.py`, `forza.py` |
| `gui/` | PySide6 desktop app (GPL-3.0-or-later) — `main.py`, `worker.py` (all device I/O), `profiles.py`, `triggers.py`, `lighting.py` |
| `tools/flydigi-mapping` | CLI for profiles — list/show/set/clear/rename/apply/backup/restore |
| `tools/flydigi-forza` | Forza driver — UDP 5300 → rules → triggers (`--dump` for telemetry only) |
| `tools/flydigi-dsx` | DSX protocol listener on UDP 7878 — drives triggers from any DSX-compatible mod |
| `tools/flydigi-monitor` | Memory-reading driver using Flydigi's XGameMonitor configs (`--probe` to debug offsets) |
| `flydigi/uhid.py` | Pure-Python `/dev/uhid` binding (no dependencies) — creates kernel-side HID devices |
| `flydigi/ps5_data.py` | Generated DualSense descriptor + feature blobs (from MIT inputtino) |
| `tools/gen_ps5_data.py` | Regenerates the above from inputtino's `ps5.hpp` |
| `work/ref/inputtino/` | MIT reference clone — DS5 output report layout, canned feature reports |
| `tests/` | `test_forza.py` (7), `test_dsx.py` (9), `test_monitor.py`, `test_relay.py` (37), `test_mapping.py` (105), `test_gui.py` (24) — all pass without hardware |
| `tests/fake_pad.py` | Stand-in controller: multi-packet reads, diffed writes, apply, save, checksum rejection |
| `tools/forza-simulate` | Synthetic telemetry generator, for testing without the game |
| `tests/test_forza.py` | Self-test for the parser and rule engine (no hardware needed) |
| `configs/forza.json` | Flydigi's own 15-rule Forza config, reused verbatim |
| `tools/flydigid` | Polling daemon — auto-detects a running game and applies its config |
| `tools/flydigi-run` | Steam launch wrapper — `flydigi-run "<name>" -- %command%` |
| `tools/hid_probe.py` | Passive HID descriptor dump (writes nothing) |
| `tools/flydigi_cmd.py` | Manual command tool — `info`, `race`, `normal`, `bind`, `rumble`, `game`, `k6*`, `raw` |
| `gamelist.json` | All 94 games + per-game configs (from the public API) |
| `mods/` | All 46 downloadable mod zips (44 MB) |
| `bundle/` | 250 .NET assemblies extracted from `SpaceStationService.exe` |
| `decompiled/` | C# source for AdapterTriggerService, ControllerSdk, Hid, Basic, SpaceStationService |
| `asar/` | Extracted Electron app (`main.pretty.js` is the beautified main process) |

## Implementation tiers

| Tier | Games | Mechanism | State |
|---|---|---|---|
| 1. Vibration bind | 33 | cmd `82` SyncWithGrip, config from API, driven by game rumble | **Done & automated** — verified in Death Stranding 2, triggers buzz with in-game rumble, daemon auto-detects and applies |
| 2. ForzaDualSense | 4 | Forza "Data Out" UDP telemetry → JSON rule engine → cmd `81` | **Done — validated in Forza Horizon 6.** All 7 distinct rules fired in-game and the effects are felt on the pad |
| 3. XGameMonitor | 31 | Generic engine + per-game config; reads game process memory | **Done — validated in Dark Souls: Remastered.** Weapon-specific filters fire from live memory reads; resistance differs correctly per weapon |
| 4. PS5 emulation | 15 listed, **any DS5-aware game in practice** | Game natively speaks DualSense; needs uhid virtual DS5 | **Validated in Deathloop** — adaptive triggers work in-game. Input relay, DS5 binding and effect translation all confirmed. Rumble and gyro outstanding, see notes |
| 5. Third-party mods | 11 | Game-side mods (REFramework, ScriptHookV, F4SE, Bannerlord module, F1 telemetry) | **No work needed** — they send DSX JSON to 127.0.0.1:7878, which `tools/flydigi-dsx` already accepts. Deliberately not shipped or supported; see [docs/third-party-mods.md](docs/third-party-mods.md) |

## Owned games (for prioritisation)

- **Tier 1**: Death Stranding 2 *(downloading)*, Silksong, Uncharted: Lost Legacy, Space Marine 2
  *(200 GB — skipped, disk limited to 512 GB)*
- **Tier 2**: Forza Horizon 4, 5, 6 (all three)
- **Tier 3**: everything except Starfield, AC Odyssey/Origins/Valhalla, Hitman, Sniper Elite 5,
  Atomic Heart, 7 Days to Die, Mafia, Hunter: Call of the Wild
- **Tier 4**: Deathloop, GTA5 Enhanced. *(Marvel Rivals does not run on Linux — anti-cheat)*
- **Tier 5**: DMC5

## Forza notes

- **FH6 uses the 324-byte Data Out format**, same as FH5 — no `--accept` override needed.
- In-game: HUD and Gameplay → Data Out → ON, IP `127.0.0.1`, port `5300`.
- All four Forza mods (FH4, FH5, FH6, Motorsport) ship byte-identical rule configs (`af0961d95b34`),
  so one `configs/forza.json` covers every one of them.
- Validation run: 162 effect writes, all 7 behaviours exercised — traction loss/regain, gear shift,
  low- and high-speed braking, manual and automatic reverse.
- **FH6 itself is unstable under Proton**, unrelated to this project: it hits an NVIDIA-only sparse
  model-buffer bug (vkd3d-proton#3053, Xid 109 / `NVRM: can't update VA space`). Root cause is still
  unidentified upstream. Disabling DLSS/Reflex avoids the early splash crash; low geometry quality
  reduces sparse buffer pressure. FH5 is the calmer target and exercises identical code.

## Tier 4 is not limited to Flydigi's game list

The virtual DualSense is game-agnostic. Nothing in `tools/flydigi-ds5` is per-game, and
`relay.translate_ds5` maps DualSense effect **types**, not titles. So it works with any PC game that
natively supports DualSense adaptive triggers — Metro Exodus Enhanced, Ghostwire Tokyo, FF7 Remake,
Returnal, Ratchet & Clank, Stellar Blade, the Spider-Man ports, God of War Ragnarok, and whatever
ships next.

That is a better proposition than the mod-based tiers: Flydigi must author a mod per title, while
this covers every DualSense-aware game for free, including ones released after any given Space
Station update. The 15 in the game list are only the ones *Flydigi* flagged as PS5-mode.

**What works, and what to tell users:**

| Feature | Status |
|---|---|
| Adaptive triggers | Works — proven in Deathloop |
| Rumble via HID motor fields | Works — the path most games use |
| Gyro / motion aiming | Works |
| Battery reporting | Works, including the desktop battery widget |
| Touchpad click | Works, mapped to SELECT |
| HD / audio haptics | **Does not work** — structurally blocked, see below |
| Touchpad gestures, finger position | Does not work — the Apex has no touchpad |

Requirements per game: Steam Input **disabled** (it masks the pad and breaks DualSense semantics)
and `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501` so the game binds the virtual pad.

## Deathloop / Tier 4 findings

Adaptive triggers **work in game**. Confirmed the transcribed mapping behaves exactly like
Flydigi's: the game sent `type=0x25 p[0]=12` → `mode 3 [70,0,12]` and `type=0x21 p[1]=3` →
`mode 1 [140,1]`, both matching their table's branches. Zero unmapped patterns.

Two gaps remain, neither in the transport:

- **No rumble — investigated and closed as a known limitation of virtual DualSense emulation.**

  The DualSense has no conventional rumble motors; its voice coils do both jobs. Games can drive
  them two ways: `motor_left`/`motor_right` in the HID output report (the compatibility path, which
  we already support and which most PC ports use), or arbitrary waveforms written to the
  controller's USB *audio* device (the rich PS5 haptics). Deathloop uses the audio path.

  Confirmed by testing: with the real Apex 5 as a plain Xbox pad the game vibrates readily, even on
  a menu button press — so it does emit motor rumble, just not to a DualSense. Our own output path
  is proven: a direct cmd `0x12` rumbles the pad and ACKs.

  **Superseded — see "Haptic audio" below.** Original finding retained for context:

  **Built, tested, negative result.** Neither Flydigi nor DSX implements audio haptics — verified
  by decompilation: Space Station bundles no audio libraries at all, and its `EnableAudio` command
  is a device feature toggle, not PC audio capture.

  We built the missing piece anyway (`tools/flydigi-haptics`): a fake 4-channel DualSense sink
  (`pipewire/99-dualsense-haptics.conf`) plus a bridge that measures haptic-channel energy and
  converts it to motor rumble. **The bridge works** — verified with `tools/haptics-simulate`, which
  plays synthetic gunshots and engine rumble and produces correctly decaying motor values.

  **But games do not use it.** With the sink present and named "Wireless Controller", Deathloop
  opened exactly one audio stream and routed it to the speakers; our sink measured absolute silence
  (peak 0.00000). A virtual pad has no OS-level link between its HID device and an audio endpoint,
  and an unassociated sink is not picked up.

  Cannot distinguish "looked for a controller endpoint and rejected ours" from "never looks on PC".
  The outcome is the same either way. Tooling is kept because it is proven working — if a game is
  ever found that does write to such a sink, only the sink config needs reinstalling.

  Two notes for anyone re-running this: `pw-record` prepends a file header and will silently
  misalign a raw reader (use `parec --raw`), and `paplay --raw` declares no channel map so PipeWire
  remixes the channels — do not assume fixed haptic channel indices.

  **Practical consequence, per game:** for titles using haptic audio, choose adaptive triggers
  (DS5 mode) or rumble (plain Xbox mode). Titles using the HID motor path get both, and that is
  the majority.
- **Gyro/accel: implemented.** The vendor input stream (command 17, "raw data transport in")
  carries the IMU at ~300 Hz, and enabling it does **not** disturb the xpad node, so sticks and
  buttons still come from evdev. Offsets follow `OperatorDataParser` for `NewXInput`, shifted by
  one because we keep the report-id byte. Accelerometer is scaled by 2.441: the pad reports
  ~4096/g while the DualSense calibration we advertise implies 10000/g — verified by the pad
  reading exactly 1.00 g flat. Gyro scale is left at 1.0 and is the one value worth tuning by feel
  (`--gyro-scale`). M1-M4 buttons are in the same stream and still to do.
- **Battery: implemented.** Command 1 returns device type, connection type and battery; polled
  every 30 s and mapped to the DualSense's 0-10 scale (Flydigi reports 0-5, with a high nibble
  flagging charging). Also fixed `BATTERY_FULL`, which was 0x01 (= charging) rather than 0x02, so
  the pad had been reporting "charging" permanently.

## Haptic audio

Deathloop *does* drive DualSense haptics on PC, and it works under Proton — verified with a real
DualSense connected over USB (haptic audio needs wired USB; over Bluetooth the endpoint does not
exist). The game opens a **dedicated second stream** to the controller's audio device alongside its
normal game audio, so this is real haptic output rather than misrouted sound.

**DualSense audio channel map**, established by playing tones into each channel and having a human
report what happened — identified by pulse count rather than play order, after an off-by-one made
the first attempt wrong:

    ch0  headphone jack        ch2  left haptic actuator
    ch1  speaker               ch3  right haptic actuator

Deathloop writes **ch3 only** (active in 87% of 373 sampled windows; ch1 never touched). Treat
haptics as mono rather than assuming stereo.

**Conversion** (`flydigi/haptics.py`, `tools/flydigi-haptics`): the DualSense's actuators are
full-range voice coils, but the Apex 5's motors are not interchangeable — left is a large
low-frequency mass, right a small high-frequency one. Mapping left-to-left would throw away the
character of the waveform, so the signal is split by frequency instead: low band drives the left
motor, high band the right. Confirmed working against live game haptics.

Three things dominated latency, all of which made it feel sluggish and "keep going" after effects
ended:
  * `effects.rumble()` waited 100 ms for an ACK on every update. Pass `wait=0.0` when driving
    continuously — the ACK carries nothing useful.
  * `parec` buffers generously by default; ask for `--latency-msec`.
  * When falling behind, **drop stale audio** rather than working through the backlog.

Useful settings: `--gain 1.5 --crossover 250`.

**What this does not do:** it requires a real DualSense present as the haptic source. Making the
Apex work standalone needs the game to write haptics to a device we control — see the USB gadget
note below.

## Dual-mode games

Six titles are both `XGameMonitor` and `isPS5`, so Space Station lets the user choose between
Flydigi's memory-reading mod and DualSense emulation (`AutoTriggerMapMode { Flydigi, PS5 }`,
stored per game as `MapMode`):

    Cyberpunk 2077          Spider-Man Remastered
    Death Stranding DC      Spider-Man: Miles Morales
    Jedi Survivor           Uncharted 4

We expose no equivalent choice — `tools/flydigi-ds5` and `tools/flydigi-monitor` are run manually.
A per-game mode preference belongs in the daemon (and in the GUI later). Note the tradeoff differs
per mode: PS5 mode gives the game full DualSense semantics including battery reporting, while
Flydigi mode uses their hand-tuned per-game effects.

Also worth knowing: **battery already reaches the desktop**. `hid-playstation` turns the virtual
pad's reported battery into a power-supply device, so it appears in KDE's battery widget as
"Wireless Controller" — verified via `upower`.

## M1-M4 buttons: no DualSense destination

Reading M1-M4 from the vendor stream is easy, but there is nowhere in the DualSense protocol to
deliver them, so the scope is smaller than it first looks.

Emulating a **DualSense Edge is the wrong answer**:
  * it has two back buttons, not four;
  * even on a real Edge they have no HID inputs of their own -- they must be remapped onto existing
    buttons in the controller;
  * its different hardware ID *loses* native DualSense support in some games. DSX has a "DualSense
    Emulation" mode and Special K an "Identify DualSense Edge as DualSense" option precisely to undo
    this, and `ds5-edge-relay` exists to convert an Edge into a plain DualSense.

What reading them is still worth:
  * **M1 -> touchpad click**, which frees SELECT to be Create (its correct mapping). Today we
    sacrifice Create because there is no other source for touchpad-click.
  * **daemon-side actions** that never reach the game: profile switching, toggling the relay,
    cycling trigger presets.

For anything else the pad's own onboard remapping is the better mechanism -- it works with no
software running and persists in controller memory.

## Prior art (researched)

  * **`DualSense-haptic-helper`** (MIT) — real hardware; independently found haptics on channels
    2 and 3 of a 4.0 stream, matching our tone probing. Warns that **Steam Input masks the
    DualSense as an Xbox pad and breaks 4-channel audio**, so it must be disabled.
  * **`Haptic-Feedback-Linux`** and **`xzn/proton-ds5-haptic`** — Wine/Proton patches enabling DS5
    haptics, plus a udev rule setting `SOUND_DESCRIPTION="Wireless Controller"`.
  * **GE-Proton 11-2** and **proton-cachyos** now ship wired PS5 haptics natively for real
    controllers. A WirePlumber rule may be needed to stop PipeWire collapsing the DS5 node to mono.

**The mechanism**, from the patch discussions: games locate the haptic device by name, and the Wine
patches "fetch the audio-side ContainerId from setupapi so HID and MMDevice agree by construction".
That is precisely the association our null sink lacked — a uhid device and an unrelated PipeWire
sink can never share a ContainerId.

**Nobody has emulated a virtual DualSense with a working audio device.** Every project either uses
real hardware or emulates HID only (inputtino, DSX). The audio half of virtual emulation is
unexplored, consistent with the blockers below.

## Virtual USB composite device (not built)

Our PipeWire null sink was ignored by the game even when named "Wireless Controller", while a real
DualSense was used immediately. That points at device identity/association rather than name
matching: a game finds the haptic endpoint via the OS-level link between the HID device and the
audio device, which a null sink does not have.

The architecturally correct fix is one virtual USB composite device exposing both interfaces, so
the kernel creates the hidraw node and the ALSA card from the same device:

    dummy_hcd   provides a virtual UDC (this laptop is USB host-only, so there is no real one)
    configfs    gadget with hid.usb0 + uac2.usb0, VID:PID 054c:0ce6

Both modules are present on the kernel. Target spec, from the real device:
`s16le 4ch 48000Hz`, `alsa.components = USB054c:0ce6`, `device.bus = usb`, haptics on ch3.

**Tested and ruled out: PipeWire property spoofing.** Wine synthesises the Windows device instance
id from the underlying Linux device — USB devices become `USB\VID_xxxx&PID_xxxx\...`, everything
else `ROOT\MEDIA\N`, and that string is what ties an audio endpoint to a HID device. A null sink
was given every property the real device carries (`device.bus=usb`, `device.vendor.id=0x054c`,
`device.product.id=0x0ce6`, `sysfs.path`, `alsa.components`), then the node name and description
were made byte-identical to the real device's. Wine still assigned `ROOT\MEDIA\N` and the game
never opened the sink. Per Wine development discussion, winepulse resolves identity through the
**sysfs path** and looks it up in setupapi — a virtual node has no kernel device to find.

**Why uhid cannot close this.** uhid creates HID devices only; it has no audio concept and no way to
attach one. A real DualSense is a composite USB device whose HID and audio interfaces are siblings
under one USB device. Only real (or emulated) USB device topology produces that.

This is not Linux-specific: a virtual audio device on Windows needs an audio driver, and DSX ships
a virtual gamepad bus driver rather than one — consistent with DSX's virtual pad also failing to
produce haptics in Death Stranding DC.

**Untested idea worth revisiting.** Plug in a real DualSense purely as a haptic transducer, but
unbind its HID interface so the game cannot see it as a gamepad:

    echo -n "0003:054C:0CE6.00XX" | sudo tee /sys/bus/hid/drivers/playstation/unbind

The audio card stays (snd-usb-audio is untouched), so there is a genuine USB DualSense audio
endpoint with a proper instance id, while input comes from our virtual pad. If the game then writes
haptics to it, matching is **by name** and a cleverer virtual device might work; if not, matching is
**by association** and only real USB topology will ever do. Either way it answers the question we
could not settle, because the earlier fake-sink test failed for a different reason (no USB instance
id at all). Note `SDL_GAMECONTROLLER_IGNORE_DEVICES` cannot be used to hide the real pad -- our
virtual one shares its VID/PID.

Of limited practical value on its own (it needs a DualSense physically attached), but diagnostically
decisive.

**Blocked on this kernel.** Fedora ships neither `usb_f_uac2` nor `raw_gadget`, so there is no way
to present a USB audio interface without building and signing a kernel module — an ongoing chore on
a Secure Boot, auto-updating, ostree system. `dummy_hcd`, `vhci-hcd`, `usb_f_hid` and `usb_f_fs`
are all present and Fedora-signed, so the HID half is easy; only audio is missing.

**Open question: does a gaming distro ship these?** Not answered — searching returned only generic
distro comparisons. Worth checking directly rather than guessing, since some ship custom kernels for
handheld hardware that needs gadget mode (the Steam Deck has a real dual-role USB port, so SteamOS
plausibly enables UAC2 gadget). To check on any candidate:

    zcat /proc/config.gz | grep -E 'F_UAC2|RAW_GADGET'      # on a live/booted system
    # or inspect the distro's kernel spec/config in its repo

Candidates: SteamOS, Bazzite, CachyOS, Nobara. If one ships `usb_f_uac2`, the whole gadget route
becomes a rebase instead of a build-and-sign treadmill.

Remaining routes, none cheap: build `usb_f_uac2` and sign it; implement UAC2 over FunctionFS
including isochronous endpoints (no reference implementation exists); or rebase to an image that
ships the module.

**Deliberately not pursued:** deriving rumble from the game's own audio output. It fires on music
and dialogue and does not resemble real haptics.

**Status: parked.** The conversion works and is proven against real game haptics; it needs a real
DualSense present as the source. Reviving this means solving the audio-device emulation above.

## RGB: not working via the test command

`TestLedCommandFactory` (command **245**, `[4]=5, [5]=R, [6]=G, [7]=B, [8]=sum(3,3+5)`) ACKs
cleanly and echoes the exact RGB values back, but **the controller's lighting does not change** --
tested with 3-second holds per colour, re-sent at 4 Hz, so an overriding mode would have shown as a
flicker.

Most likely explanation: 245 lives in `command.test/` alongside TestScreen/TestJoystick/TestRF and
is exposed as `IpcCommandEnum_TestRgb`. These are factory-test commands and may require the device
to be in a diagnostic state first.

**The real path is the persistent config**, which is how Space Station does it:

  * `ReadLedConfigCommand` = **167**, `[4]=4, [5]=cfgId, [6]=pkgSize, [7]=sum`. Confirmed working --
    the pad replied `04 5a a5 a7 0c 00 00 00 03 00 00 09 04 14 0c 07 01 ff ff ff ...`
  * `WriteRgbConfigCommand` = **169**, written in packs: `[4]=len+3, [5]=packNum, [6..]=pack data`
  * Structure `m_fdg_mapping_rgb_sturct_t`: `version[2], type, loop_start, loop_end, loop_time,
    light_scale, rgb_num, rgb_type, reserve[11], id[16]` where each id is 10 x `{r,g,b}`.
    `type` / `rgb_type` select the lighting mode -- that is what needs setting to a static mode
    before a colour will stick.

So bridging the DualSense lightbar to the pad means decoding that config, setting a static mode and
writing it back -- a real job, not the one-command bridge originally assumed. The lightbar bytes
themselves are already parsed (`data[45..47]` of the DS5 output report).

## Open issues

- **Game detection**: many entries have empty `processGameNames` (incl. Silksong, Space Marine 2).
  Death Stranding 2 (`['DS2', 'DEATH STRANDING 2: ON THE BEACH']`) and Uncharted (`['u4','tll']`)
  do have them. Need a fallback — likely resolving the exe from the Steam manifest, which is what
  Flydigi's bundled `GameFinder.StoreHandlers.Steam` does.
- **Steam not yet installed** (`flatpak install -y flathub com.valvesoftware.Steam`).
- **Steam Input contention**: Steam/SDL also claim the hidraw node and send their own
  acquire/heartbeat (`0x1C`). May need to disable Steam Input for the pad or tolerate it.
- Which command family the K6 path needs (`83`/`85`/`87`) — untested; `81`/`82` were sufficient so far.

## Next-session runbook

Start by reading this file and `PROTOCOL.md`. Everything gitignored is reproducible:
`tools/fetch-configs --monitor-configs --all-mods` restores `gamelist.json`, `configs/` and `mods/`.
The decompile toolchain lives in the `wine-arch` distrobox (see Environment above); the decompiled
sources under `decompiled/` are only needed for new protocol work, not to run anything.

### Where the desktop app stands

Everything is committed and green. To check:

```bash
distrobox enter apex-dev -- bash -lc 'cd ~/Projects/ApexExperiments && \
  python3 tests/test_models.py && python3 tests/test_shell.py && python3 tests/test_qml.py'
for t in tests/test_{dsx,forza,mapping,monitor,relay}.py; do python3 "$t"; done   # no Qt needed
tools/generate-qmltypes && qmllint -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml
```

172 model tests, 50 shell, 71 QML, 299 backend; qmllint and `reuse lint` clean.

**Both known bugs are fixed**, each with a test that fails without the fix.

  * **The Buttons and Games placeholders could never render.** Each was a sibling of a `ListView`
    inside a `Kirigami.ScrollablePage`, and `ScrollablePage.qml` reparents *only* the Flickable
    child then sets `scrollingArea.visible = false` (line 362) — the placeholder stayed in the
    hidden subtree. Both now sit *inside* their `ListView`, which is KDE's own idiom
    (`kirigami/dialogs/SearchDialog.qml:250`) and works because an empty view leaves `contentHeight`
    at zero, which sizes the content item to the viewport, so centring in it centres on screen.
    Buttons additionally takes `model: App.profile.loaded ? App.profile.keys : null`, because
    `KeyMapModel.rowCount` is a constant 23 and `_row()` fabricates an identity mapping with no
    config — merely un-hiding the list would have drawn 23 rows of fiction. Games grew
    `GameFilterModel.total` so "never downloaded the list" and "your search matched nothing" stop
    looking identical; the second offers to clear the filters rather than to re-download Flydigi's
    list because someone mistyped a game's name.

    Testing this needed a way to say *would this be drawn*, which `visible` does not answer: an
    explicit binding overrides the value a hidden ancestor propagates, so an item stranded in a
    hidden subtree still reports `visible === true`. Both suites walk the parent chain instead. The
    Buttons case reproduces the original symptom exactly — `Pad.failReads` makes the fake pad go
    silent the way a sleeping one does, and the page must then say so rather than showing nothing.
  * **`read_config_preserving` now restores in a `finally`**, and decides where to go back to before
    the read rather than after. The pad switches on the first read packet, so a read that raises has
    still moved it — and the retry laundered that: the next `read_status` truthfully reported the
    browsed slot as active, the restore was skipped as unnecessary, and the call reported success.
    `tests/fake_pad.py` now models switch-on-read and can be told to answer nothing, because code
    that avoids disturbing the pad can only be tested by a fake that actually gets disturbed.

**Not settleable by reading; needs the pad.** Whether the firmware accepts 164/165 aimed at a slot
it is not running (the app now sidesteps this by always editing the running profile), and what
`filter` / `vibr_limit` / `time_limit` do in the trigger-motor gears.

### Working on the GUI

`gui/README.md` has the detail. Three things that will cost an hour each if rediscovered:

  * **The venv cannot load Kirigami.** Wheel Qt tags private symbols `Qt_6_PRIVATE_API`, Fedora's
    tags them `Qt_6.11_PRIVATE_API`, and it is mutual. Work in the `apex-dev` distrobox.
  * **`tryVerify` over a QML binding never updates.** `tryVerify(() => !button.enabled)` sits until
    it times out on work that already succeeded. Use `tryCompare(App.profile, "dirty", false)` —
    a closure over a binding has no notify signal to watch.
  * **PySide6 cannot see delegate-created items**, and recursing `QObject.children()` over QML
    objects aborts the interpreter. That is why UI tests are QtQuickTest and live in QML.

When a QML symptom makes no sense, reproduce it in plain Python before theorising — that is what
finally found `FakePad` missing `ack_ok`, after three wrong guesses about layout and click delivery.

### Deathloop — validates Tier 4 (virtual DualSense, 15 games)

Deathloop is `isPS5` with no mod: the game speaks DualSense natively, so there is nothing to
install. The whole job is the relay.

1. Connect the pad (it sleeps on idle — wake it first) and confirm all three interfaces:
   `python3 tools/hid_probe.py` should show the vendor node (`usage pages 0xffa0`), and
   `/dev/input/by-id/` should list `...-event-joystick`.
2. Steam launch options for Deathloop:
   `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 %command%`
   Without this the game may bind the real Apex 5 instead of the virtual DualSense.
3. Run `tools/flydigi-ds5` before launching the game. It logs each decoded DS5 effect and what
   it translated to.
4. In game, check the button prompts show PlayStation glyphs — that confirms it bound the virtual pad.
5. The effect mapping in `flydigi/relay.py::translate_ds5` is transcribed from Flydigi's
   `PS5DataManager.ProcessDataWithResult`, so it should be right rather than approximate. If
   something feels wrong, diff against that decompiled method before adjusting by feel.
   Unmapped effect patterns are logged as "unmapped, trigger unchanged" — those are new byte
   patterns Flydigi never handled, and are worth recording.

What to watch for:
- Double input (both pads registering) → the SDL ignore variable is not taking effect.
- Effects logged but not felt → EFFECT_MAP mapping is wrong, not the transport; the transport is
  the same cmd 81 that Forza already proved.
- Touchpad-click is on the touchpad *sub-device*, which needs `udev/99-flydigi-apex5.rules`
  installed or the node stays root-owned.

### Dark Souls: Remastered — VALIDATED

Confirmed working. Notes from doing it:

  * **Wine maps the PE at its image base** (`0x140000000` for a 64-bit game), the same value
    `Module32Next` reports on Windows, so Flydigi's offsets work unmodified. This was the
    assumption flagged as riskiest and it turned out fine.
  * **Process selection cannot match on command line alone.** Under Steam and Proton a chain of
    wrappers (`reaper`, `bwrap`, `pv-adverb`, `steam.exe`) all carry the game's path in their
    cmdline. `find_process` now requires the candidate to have actually mapped the PE, which also
    yields the module base.
  * Dark Souls: Remastered keys off `move` (an animation id encoding weapon + attack). Black Knight
    Halberd swings produced `1123300`/`1123310`, matching the config's 黑骑士钺 entries, and the
    right trigger resisted heavily while the shield side stayed light — exactly as configured.

### Original notes

Chosen for the shortest pointer chain (3 hops vs 6-12 elsewhere) and the smallest download.

1. `tools/fetch-configs --monitor-configs` → `configs/monitor/DarkSoulsRemastered.default.json`
2. Start the game, get in-world, then:
   `tools/flydigi-monitor --probe configs/monitor/DarkSoulsRemastered.default.json`
   `--probe` reads memory and prints values without touching the controller.
3. Success looks like: the `move` define changing as you swing a weapon or roll.
4. If it reads 0 or a constant, the prime suspect is **module-base resolution under Proton**.
   `find_module_base()` takes the lowest mapped address of a `.exe` in `/proc/<pid>/maps`; Wine may
   map the PE differently from how `Module32Next` reports it on Windows. Inspect the maps directly
   and compare against the config's first offset (`0x1A31768` for DS:R).
5. Once values move sensibly, drop `--probe` to drive the triggers.

Pointer chains are build-specific: a game patch will break a config until Flydigi ships new offsets.

## End goal: Qt/KDE app replacing Space Station

Not just triggers — a full replacement covering what Steam Input and input-remapper cannot do.
The library/CLI split exists so a GUI can sit on top without rework.

Target features and the commands already recovered for them (all in `decompiled/`):

| Feature | Commands |
|---|---|
| Screen image (gamepad + charging dock) | `UploadPic2K2Start/Data/End/Finish`, `UploadPicCommandK1/K2`, `TestScreen`, `OffScreen`, `ReadScreenSetting`, `EnableScreenStatusBarAlwaysOn` |
| Trigger config, game-independent | `SetForceTriggerCommandFactory` (working), `K6Trigger*` |
| Profile switching | `ApplyMappingConfigByCfgId`, `SaveCurrentMappingConfig`, `ReadCurrentMappingConfigId`, `WriteAllMappingConfig`, `ResetMappingConfigByCfgId` |
| RGB / LED | `WriteRgbConfig`, `WriteAllRgbConfig`, `ReadLedConfig`, `TestLed` |
| Macros | `ReadMacroConfig`, `WriteMarcoConfig`, `SetHardwareMacroEnable` |
| Device settings | 22 in `command.setting/`: report rate, stick sensitivity/precision, debounce, rebound, auto-calibration, motion debounce, sleep time, dock smart stop, mode switch, nickname |
| Dock / cooler | `Flydigi.ChargerSdk.dll`, `Flydigi.CoolerSdk.dll` (in `bundle/`, not yet decompiled) |

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

## Next steps

All five engines are built. What remains needs games, not code:

1. **Forza Horizon 6** — enable Data Out (127.0.0.1:5300), run `tools/flydigi-forza --dump`
   first to confirm telemetry crosses the Proton boundary, then run it for real.
2. **Dark Souls: Remastered** — validates Tier 3 (31 games). Run
   `tools/flydigi-monitor --probe <config>` and check the `move` define changes as you swing.
   If it reads 0, suspect module-base resolution under Proton.
3. **Deathloop** — validates Tier 4. Launch with
   `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501` so it binds the virtual DualSense, then
   tune `relay.EFFECT_MAP` by feel.
4. **Decompile ChargerSdk / CoolerSdk** for the gen2 dock.
5. **Qt/KDE app** — see the end-goal section above.
