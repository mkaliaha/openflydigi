# The 840-byte profile blob

What the mapping profile carries, block by block, and which of it the firmware
actually reads. Offsets from `MappingConfigParser.cs`, struct names from
`data.model.config/`. Everything here is already carried through by
`flydigi/mapping.py` — we read and write the whole blob — so these are
accessors and pages, not new commands and not new risk.

Index: [PROGRESS.md](../PROGRESS.md).

## Layouts, both verified on hardware

Mapping profile, 840 bytes (42 packets of 20), protocol v3.1:

```
0..2 version   2 package count   3..13 legacy LED   13..109 key table (32 x 3)
109..123 joystick curves      123..137 trigger travel curves
137..145 motion               145..154 grip vibration (master + 2 x 4)
154..183 trigger motors       183..185 wheel
185..225 force trigger (2 x 20)   225..227 data version   230..768 macros (J6)
770..790 title UTF-16LE       790..840 joystick extra, macro cycle, motion curve
```

Lighting, 380 bytes (19 packets of 20):

```
0..2 version   2 click feedback   3 loop start   4 loop end   5 cycle time
6 brightness   7 LED count (12)   8 mode   9 grip sync   10..20 reserved (0xFF fill)
20.. frames of `LED count` RGB triples -- 10 x 12 on an Apex 5
```

Byte 9 is **grip sync** — lighting follows the grip motors, which Space Station exposes as
`SyncWithGripEnable`. `flydigi/lighting.py` carries it through untouched with no accessor, so it is
an editable switch nothing currently edits.

Config structures for mapping/macro/RGB are already decompiled as `m_fdg_*_struct_t` types.

## Factory defaults, read off the pad

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
185 force trigger   0   0  10  10 100   1 255  70   0   ? | 0 0 0 0 0 0 0 0 0 0
                    left side only, and one byte short: [9] mixed border was not
                    transcribed, nor was the right side (205..225)
790 joy extra L     0 | 50 62 75 87 100 112 125 137 150 | 0 | 0
802 joy extra R     0 | 50 62 75 87 100 112 125 137 150 | 0 | 0
814 unclaimed       255 255 255 255 255 255
820 macro cycle       3   3   3   3   3          5 intervals, stored as ms/10
825 unclaimed       255 255 255 255 255
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
  * **The trigger-motor gears are two 7-byte structs per side**, and their lead bytes differ: the
    first gear's `type` is **1**, the micro gear's **255**. Nothing in the SDK decodes either — both
    are read as plain ints with no consumer — and neither fits the blob's inverted-switch convention
    (0 on, 0xFF off), under which 1 is the *off* value the enable byte itself writes. So `type` stays
    unexplained, as J5 says. `trigger_motor()` reads the first gear, the only one
    `SaveTriggerVibrationConfig` touches.
  * **The force-trigger bind is populated at rest**: `bind.Filter = 10, bind.Scale = 10,
    bind.Param = [100, 1, 255, 70, 0]` with `Type = 0` and `bind.Type = 0`. So J4's claim that the
    stored bind mirrors live command 82 has real numbers behind it to diff against.
  * **`joy extra`'s bank[9] is a preset curve, not a blank.** `50 62 75 87 100 112 125 137 150` is
    evenly spaced, i.e. the straight line, with 100 at the centre point — so 100 reads as unity and
    the bank is a gain per zone rather than an output level. `isRound = 0` is Rectangle.


## J1 — sticks: dead zones, curves and circularity

**Done, backend and GUI.** `stick()` / `set_stick()` and the compiler `stick_bank()` are in
`flydigi/mapping.py`; the page is `gui/qml/pages/SticksPage.qml`, over `StickModel` in
`gui/models/profile.py`. Verified end to end on hardware: a 25% dead zone compiles, writes in two
packets and reads back byte-identical.

**One thing the page does not offer that Space Station does: editing the curve itself.** Ours picks
between the presets below and shows the result; theirs lets you drag `p1` and `p2`, the two interior
breakpoints, which is what `JoystickSensitivityType.Custom` is for. `set_stick()` already takes
`point1`/`point2` and the compiler already samples whatever polyline it is given, so this is a GUI
gap rather than a protocol one. Their own UI makes the curve read-only until Custom is selected, and
any manual edit forces the type to Custom — worth copying, since a shape that no longer matches a
preset must not go on claiming to be one.

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

Two blocks hold it:

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
Custom. **`STICK_PRESETS` writes Default as (63,63), not (64,64)**: 63 is what the pad ships with, so
resetting to Default reproduces a factory blob byte for byte. The compiled bank is identical either
way; only the stored polyline differs.

**`center` and `edge` are cross-clamped so `center + edge <= 100`.** They consume the same travel,
and a zero span turns the curve into a step. The field being set is the one that gives way, so
moving one slider never moves the other.

Note what this does *not* tell you, and why the hardware runs above were still needed: Space Station
writes both blocks unconditionally, so nothing in the app reveals which one the pad reads. The app
answers "what is computed from what"; only the pad answers "what does it act on".

Two consequences worth the space:

  * **A GUI must compute the bank.** Offering a dead-zone slider that writes `center` would move a
    number, dirty the profile, write successfully — and change nothing the hand can feel. Whatever
    curve the UI offers has to be sampled into nine points at x = 0, 12.5, … 100 as
    `clamp(trunc(output_percent), -50, 100) + 50`. Space Station's JavaScript rounds there (the
    `round` in the listing above is theirs, faithfully), but an untouched pad holds
    `50 62 75 87 100 112 125 137 150` and only truncation reproduces it, so `stick_bank()`
    truncates on purpose — see `flydigi/mapping.py`.
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

**The liveness trap.** A stick nobody is touching and a stick the pad has
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

## J6 — macros, and the pad really does play them

**Done, and verified on hardware — recording, playing and clearing. Editing is the gap**: the app
records a sequence off the pad and deletes it, where Space Station also edits a recorded macro's
steps (output key, duration, interval) and builds one from nothing without recording at all.
`set_macro()` takes arbitrary steps already, so that too is GUI work rather than protocol work.

A macro is a sequence of button events the *firmware* plays: the
key table entry for its trigger key holds `TARGET_MACRO` (32), the steps live at **offset 230**, and
nothing on the host is involved once it is written. `m_fdg_macro_unit_struct_t` (`btn, count_l,
count_h, type, step[64]`) and `m_fdg_macro_state_struct_t` (`active`, a unit pointer, `cur_step`,
`cur_time`, `keystate`) are the firmware's own structs carried into the SDK — the second is a
running state machine no host would keep.

The page is `m_fdg_macro_page_struct_t`, 538 bytes:

```
[0]        how many macros, 1..5; anything else means none
[1..6]     each macro's offset into the bodies, in 4-byte words
[6..538]   the bodies, each  [0] trigger key id   [1..3] step count, LE
                             [3] type             then 4 bytes per step:
                             cumulative time (16-bit, 10 ms ticks), key id, event
820..825   one repeat interval per slot, milliseconds / 10
```

Five macros and 128 steps between them, which is not two limits but one: 133 words of body space,
each macro spending one on its own header. `MacroEnableType` is `None=0, Once=1, WhileHeld=2,
Toggle=3`; `MacroActionEvent` is `Release=0, Press=1, LeftJoystick=2, RightJoystick=3, **Hold=5**` —
the enum skips 4, and guessing it from position would write an event the firmware does not know.

**Where this lives depends on the protocol version.** From v3.2 macros move out of the blob into
their own store behind commands 172/173/174, ten of them at 1 ms resolution. An Apex 5 reports
v3.1 and keeps them here. Confirmed twice over: `MappingConfigParser` branches on
`(ProtoVersion & 0xF) < 2`, and the hardware holds five cycle bytes at 820.

**What the hardware settled**, with four paddles each given a signature no finger can produce —
three taps of one letter in 300 ms:

  * **They play.** `M1` produced `a a a` at exactly the 40/60 ms gaps written, `M3` held down
    produced `x x x` seven times over. The stored timings come back to the millisecond.
  * **A write is not enough — the profile has to be applied.** The same macros, read back off the
    pad to prove they were stored, sat through a whole test window and did nothing, while a plain
    remap written in the same packet run worked throughout — that control is what makes the result
    mean anything. Command **162** made them live. So the firmware parses this page into its structs
    when a profile is *loaded*, while the key table is read as it stands. `MappingConfig.macro_page`
    exists for exactly this: a caller compares it and applies when it moved, rather than applying on
    every write and making the pad re-seat its trigger motors over a remap that never needed it.
  * **Saving is not required.** Isolated with a fifth macro written and applied and never committed:
    it played. So 166 only decides whether a macro survives a sleep, the ordinary rule.

**Before re-running any of this, check the transport.** The first two attempts here measured
nothing and meant nothing: third-party control was on, which switches `controller_data` off and
leaves the evdev node present and silent. A capture in that state cannot tell a firmware that will
not play a macro from a pad that is not reporting. `motion.read_transport` says which state you are
in, and the third window — with it off, a control remap in place, and the macros read back first —
is the one that answered anything.
  * **The repeat interval at 820 is the gap between repeats**, not a delay before starting or a
    step scale: 300 ms written, 300 ms measured between passes of a held macro. It had been
    documented from its name alone.
  * **An orphaned body still runs, *alongside* the key table rather than instead of it.** The two
    are read independently and both fire. The first sighting had the key table and the body both
    aimed at `b`, which coalesces into what looks like the macro alone; setting them to different
    keys shows what is really happening — M1 with a table entry of `a` and an orphaned body of three
    X taps produced `press a`, `x x x`, then `release a` when the paddle came up. Reproduced three
    times across two runs, identical every time.

    **Space Station prevents this rather than repairing it**, and at exactly the same moment we do.
    `ControllerRepository` drops the macro as the key is remapped:

    ```csharp
    if (keyConfig.MapType != KeyMapType.Macro
        && config.KeyConfigBeans.KeyConfigBean[keyId].MapType == KeyMapType.Macro
        && config.MacroConfigBean.Macros.Count > 0) {
        macroUpdated = true;
        var macroItem = config.MacroConfigBean.Macros.FirstOrDefault(i => i.KeyId == keyConfig.KeyId);
        if (macroItem != null) config.MacroConfigBean.Macros.Remove(macroItem);
    }
    ```

    The follow-up `WriteMacroConfigPartial` is gated on `ProtoVersion >= 770`, because below that —
    our pad — the removal rides along inside the mapping blob write. So neither application ever
    produces the state, and nothing in the firmware cleans it up: it has to be built deliberately,
    which is how it was measured here. `set_mapping` does the same removal, so a key remapped away
    from its macro stops running it.

  * **`Once` plays to the end whether or not the key is still down.** Visible in the same trace: the
    paddle came up between the second and third tap and the third tap arrived anyway. So a long
    macro keeps going after you let go, which is worth knowing before binding a ten-second one.

Steps are limited to keys XInput can carry, for the same reason `XINPUT_TARGETS` is: a step that
presses M1 is a step the host never sees. The trigger key has no such limit — a paddle runs one
perfectly well, which is the usual way to bind one.

## J2 — gyro mapped to a stick, on the pad

**Not started.** Works in any game with nothing running, which on Linux
is otherwise Steam Input only. **Offset 137**, 8 bytes, `m_fdg_macro_motion_mapping_struct_t`:
`type, keyid, method, zero, sensity_x, sensity_y, mode, keyid_ext`, where `type` is
`MotionMapType {Off=0, LeftJoystick=1, RightJoystick=2, Mouse=3}` and `mode` is
`MotionUseMode {FPS=0, Racer=1}`. Smoothing curve at **offset 830**, 6 bytes. The pad's own UI warns
that enabling this lowers the polling rate. `MotionMapType.Mouse` is not a pad feature — see
*Ruled out, so nobody looks again* in [PROGRESS.md](../PROGRESS.md).

## J3 — the trigger travel block

**Done, and it was a bug rather than a gap.** **Offset 123**,
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
UI, and the pad ships with 0.

**This block is Space Station's "Stroke Setting", and the pad plays it.** Measured with
`tools/trigger-stroke-probe`, which writes a degenerate 0..16 window to one trigger and leaves the
other as an in-run control: the written side produced **17 distinct evdev values** against the
control's **240**, in the same sweep, with 84% of the pull pinned at full scale. 17 is exactly a
0..16 window. Both triggers still spanned the full 0..255 output, so what the window moves is the
*physical* travel and not the range the game reads — which is precisely what Flydigi's own tooltip
claims (`effect_travel_range`: "sets the physical press interval over which the trigger takes
effect; does not affect the upper and lower limits of the in-game trigger data"). On this pad,
bringing `end` in is a software hair trigger.

**And the neighbouring candidate is inert.** The same probe wrote the same window into `Param[0]`/
`Param[1]` of the force-trigger block — 195/196 and 215/216, the `AdapterTriggerTypeNormal
{ Start, End }` record — and got **238 against 239**: no difference at all. Three separate readings
of the decompile had pointed at 195/215 as the real destination and at 123 as a byte this pad does
not read. That was exactly backwards, and only the hardware said so.

**Why the source misled, in detail**, since the same trap is set for anything else read out of the
renderer. Space Station has *one* stroke slider with *two* destinations, chosen by
`supportAdaptTrigger`:

  * **off** — `TriggerConfig.Start`/`End` → `configBean.Zero`/`.End` → this block, at 123.
  * **on** — `AdapterTriggerTypeNormal.Start`/`End` → `TriggerAdapterConfigBean.Param[0..1]` →
    195/196 and 215/216.

Both halves are in `ControllerRepository.SaveTriggerConfig` and in the renderer's read effect, which
is what made 195/215 look like the answer for an Apex 5. But the renderer also sets
`triggerStrokeUsable` to **`!supportAdaptTrigger`** (`index-DM6mSbRo.js`, alongside
`Me(!S.supportAdaptTrigger)`), so on a pad *with* adaptive triggers the slider is hidden outright —
and the panel holding it is itself gated on `triggerType === Normal`, so on a k5 the General effect
draws an empty box. Space Station therefore never edits the stroke window on this pad **by either
route**. Two more things point the same way: `Param[0..1]` under mode Normal are only ever
round-tripped through the blob, and the live command for that mode,
`ForceTriggerConfigNormal`, carries no parameters at all — just `[side, 0]`.

So "do what Space Station does" was not available here: copying them exactly means shipping no
stroke control, and copying their *k5* write path means writing the byte that measures inert. The
Triggers page now offers the pair as **Travel start / Travel end**, writing 123 as it always did.

There is no capability flag for a mechanical trigger stop — `IsSupportForceTrigger`, `Led`, `Ns`,
`Screen` and `TriggerVibration` are the whole set — so `!supportAdaptTrigger` is likely standing in
for the pads that have one, where the firmware needs telling how much travel the stop left. That is
a guess about intent and nothing rests on it: this pad honours the block whatever it was for.

None of this changes `effects.stored()`, which still returns no parameters for General rather than
zeroing them: the slots hold whatever the last effect left, and clearing them would throw away
numbers someone tuned before switching the effect off.

## J4 — the vibration bind, and the four effects that were missing

**Done.** The page
offered two of the six effects the pad has, because only `Normal` and `Race` had a mode number
written down; `Sniper`, `Recoil`, `Lock` and `Vibration` are all in
`SetForceTriggerCommandFactory` with their own parameters, and `SaveTriggerAdapterConfig` says
which byte each one lands in. All six are now in `flydigi/effects.py` as one vocabulary — labels,
Space Station's own slider bounds, and the slot map — which the profile editor, the live commands
and `tools/flydigi_cmd.py` all read, so the wire form and the stored form cannot drift.

Three things that only show up once all six exist. The ten parameter slots are **shared**: every
effect writes into the same bytes, so switching effect reads back whatever the last one left, and a
value out of the new effect's range is its default rather than a number clipped into range.
Slots an effect does not use are **not** free space — Lock's 255/1 and Vibration's 1/90 are
constants Flydigi's writer emits. And `Sniper` and `Vibration` take the *same* parameters and are
not the same effect — see below. `set_trigger_effect()` writes the bind half and the bind type byte.

**All four new modes are felt on hardware**, at their default settings: Lock stops the trigger dead,
Recoil resists and gives way, Sniper vibrates past the travel point. Every one ACKed *and echoed
its own parameters back* — `[success=1][mode][params…]`, side dropped — so the pad parses the
payload rather than merely acknowledging the command id.

**Mode 5 is dead code in Flydigi's own stack**, which is why the pad does nothing with it.
Nothing constructs `ForceTriggerConfigVibration`; the config path turns stored type 5 into
command 82; the DualSense relay emits only modes 0-3 (ours transcribes theirs, `relay.translate_ds5`,
and agrees); and pads that have real trigger motors drive them with command **18**, not 81. So the
firmware has never been asked to do anything with mode 5, and a mode-5 command that produces nothing
is the expected result rather than a puzzle.

The vibration effect in real use is **mode 2** — the DualSense vibration/automatic-gun effect maps
to it and Space Station calls it 机枪, machine gun — and the "Vibration" a user picks in their UI is
the *stored* type 5, delivered as command 82, which works. The name is a red herring twice over.

**How to test an effect on this pad**, since a bind left in the wrong state invalidates the run:
control the bind explicitly, prove the path is alive with a known-good effect such as mode 2, and
put byte-identical parameters in both arms so the mode byte is the only variable. `bindType 0` is
not a quieter bind — Flydigi never sends it and it appears to mean *no* bind, so a run using it
measures silence rather than the mode. And a config apply does not restore live bind state: it
survives the switch, so "I re-applied the profile" is not a reset.

**The stored bind is the same structure as live command 82.** `ParseTriggerConfigToArray` writes, at
**offset 185** + 20 per side:
`Type, bind.Type, bind.Filter, bind.Scale, bind.Param[5], MixedBorder, Param[10]`. Live 82 takes
3 + 4 parameters; the stored form is 3 + **5** — the same structure with one spare byte. And the
writer sets `bind.Type = (Type == 5) ? 2 : 0`, so bind type 2 appears exactly when the stored effect
is `Vibration`. The per-game preset *is* effect type 5 and can be made to survive a sleep instead of
needing re-application — that last part is still not wired up: the Games page applies a preset with
live command 82, which the pad forgets on sleep, rather than storing it in a profile.

## J5 — the trigger-motor block, which is not this pad's

**Moved to multi-pad support.** Offset 154 is the
trigger *vibration* block, and the Apex 5 has no such motors: `IsSupportTriggerVibration` is a Vader
flag. `MappingConfig.trigger_motor()` reads and writes the four fields Flydigi's own writer touches,
with the layout asserted in tests, and nothing in the app calls it. The full write-up, including the
percentage-versus-byte trap and the open question about whether the right trigger's copy is read at
all, is *The trigger-vibration editor* just below. `min_start`, `min_time`, the gear `type` byte and the
whole micro gear stay unexplained, and a bench sweep for them needs a pad that has the hardware.

### The trigger-vibration editor, written and then taken back out

It belongs to the Vader. The
profile blob's 29-byte block at **offset 154** holds, per side, two 7-byte gears
`{type, min, max, filter, min_start, scale, min_time}` behind one shared enable.
`SaveTriggerVibrationConfig` writes four fields of the first gear and never touches the second:
`min`/`max` as an amplitude window (grip rumble above the ceiling acts as the ceiling, below the
floor as the floor), `scale` as strength, `filter` as a threshold below which the trigger stays
still. Two traps in it: **`scale` is stored as the percentage their slider shows** (1..100) while
`min`/`max` beside it are that same slider's percent scaled to a byte (`floor(pct * 255 / 100)`);
and **Space Station syncs `scale` and `filter` across both triggers** while leaving the amplitude
per side, though the bytes are per side either way — so whether the firmware reads the right
trigger's copy is an open question a bench test would answer.

`MappingConfig.trigger_motor()` reads and writes all four, and `tests/test_mapping.py` asserts the
layout against Flydigi's writer, so the protocol half is done and verified against the decompile.
There is no UI, because **the Apex 5 does not have these motors.** `GenerateControllerApex5` sets
seven capability flags and `IsSupportTriggerVibration` is not among them, while Vader 3, 4 and 5 all
set it, and `ConvertTriggerConfigBean` only reads the block when that flag is on. The blob carries it
regardless because it is one struct shared across the range.

**Gate a feature on the capability flags, never on the presence of bytes in the blob** — and **the
factory bytes are not evidence either**: ours ships that block populated with `1 30 80 5 1 50 0`,
which looks exactly like a feature in use. When a Vader 4 Pro is supported this becomes a page gated
on `IsSupportTriggerVibration`, and the Vader is the machine to verify the sync question on.

