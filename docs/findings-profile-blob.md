# The 840-byte profile blob

What the mapping profile carries, block by block, and which of it the firmware actually reads.
Offsets from `MappingConfigParser.cs`, struct names from `data.model.config/`.
`flydigi/mapping.py` reads and writes the whole blob, carrying through byte-for-byte everything it
does not interpret. The `J`-numbers in the headings are the keys [PROGRESS.md](../PROGRESS.md)
cross-references by.

**Where this is edited.** The CLI is `tools/flydigi-mapping`, with subcommands `list`, `show`,
`set`, `clear`, `rename`, `apply`, `backup`, `restore`, `macros`, `macro-record`, `macro-set` and
`macro-clear`, plus a global `--profiles N` (default 4); `set` also takes `--turbo N` and
`--turbo-toggle`. `backup` writes the bare 840-byte blob with no header, and `restore` accepts a
file only if its length matches the pad's. Stick and trigger curves have no CLI — they are edited
in the app, on `gui/qml/pages/SticksPage.qml` and `TriggersPage.qml` over `gui/models/profile.py`.
The tests are `python3 tests/test_mapping.py` and `python3 tests/test_macros.py`; neither needs
hardware. The bench probes used below — `tools/joystick-curve-probe`, `tools/trigger-stroke-probe`
and `tools/stick-feel` — write into whichever profile the pad reports as active, never call
`save_config`, and put the original back before exit, so nothing they measure reaches flash
(`tools/joystick-curve-probe:257-305`, `tools/trigger-stroke-probe:369-415`,
`tools/stick-feel:115-135`).

## Blob layout

Mapping profile, 840 bytes (42 packets of 20), protocol v3.1:

```
0..2 version   2 package count   3..13 legacy LED   13..109 key table (32 x 3)
109..123 joystick curves      123..137 trigger travel curves
137..145 motion               145..154 grip vibration (master + 2 x 4)
154..183 trigger motors       183..185 wheel
185..225 force trigger (2 x 20)   225..227 data version   230..768 macros (J6)
770..790 title UTF-16LE       790..840 joystick extra, macro cycle, motion curve
```

The package count at offset 2 is **84** on v3.1 (79 on v3.0);
`MappingConfigParser.ParseConfigToArray` picks it from `ProtoVersion & 0xF`. It is not the
transfer's packet count — the NewXInput transfer moves 20 bytes per packet (`blobs.PKG_SIZE`) and
the pad streams back 42 of them for the 840. Read as 10-byte units it is exactly the blob length on
both versions, 840 and 790, but Flydigi's own writer allocates `PackageCount × perPkgCount` with a
`perPkgCount` of 20 on NewXInput, so do not size a buffer from it.

Four ranges are unaccounted for and nothing here reads or writes them: **227..230**, between the data
version and the macro page; **768..770**, between the macro page and the title; and **814..820** and
**825..830**, either side of the macro cycle bytes in the tail block.

Lighting is a separate 380-byte config with its own command family (167/168/169); its layout and
measurements are in [docs/device-settings.md](device-settings.md). Its bytes **10..20** are a
deliberate 0xFF fill written by `RgbConfigParserV30.ParseConfigBeanToArray`
(`LedConfigParser.cs:159-161`) rather than merely reserved.

## Factory defaults, read off the pad

All four slots of an untouched Apex 5, byte for byte, and identical across all four.
`tests/fake_pad.py` takes the curve blocks and the macro page from these bytes rather than leaving
them at its `0xFF` fill, and cites this document for them.

```
109 joystick core   0   0  63  63 127 127 127   |   0   0  63  63 127 127 127
                    type zero p1x p1y p2x p2y end        (same, right stick)
123 trigger travel  0   0   0   0 255 255 255   |   0   0   0   0 255 255 255
137 motion          0  12   0   4  25  20   0   0
145 grip vibration  0 | 0  60 255  50 | 0  80 255  50
154 trigger motors  0 | 1 30 80 5 1 50 0 · 255 40 120 5 0 50 0 | (same, right)
183 wheel           0   0
185 force trigger   0   0  10  10 100   1 255  70   0   ? | 0 0 0 0 0 0 0 0 0 0
                    left side only; the mixed-border byte and the right side at 205..225 unknown
790 joy extra L     0 | 50 62 75 87 100 112 125 137 150 | 0 | 0
802 joy extra R     0 | 50 62 75 87 100 112 125 137 150 | 0 | 0
814 unclaimed       255 255 255 255 255 255
820 macro cycle       3   3   3   3   3          5 intervals, stored as ms/10
825 unclaimed       255 255 255 255 255
830 motion curve    0  63  63 127 127 127
836 padding         255 255 255 255
```

From those bytes:

  * **Sticks and triggers do not share a scale.** A stick's curve runs to **127**, a trigger's to
    **255**, so one "0-100%" slider mapped straight to bytes is half range on one of them. Both ship
    on the identity line — the stick's `p1`/`p2` at `63,63` and `127,127` with `end = 127`, the
    trigger's at `0,0` and `255,255` — so both are genuinely curve control points, and the factory
    setting of each is "no curve".
  * **The motion smoothing block at 830 is the joystick block minus its type byte**: `zero, p1.x,
    p1.y, p2.x, p2.y, end`, carrying the identical `0 63 63 127 127 127`.
  * **The trigger-motor gears are two 7-byte structs per side**, and their lead bytes differ: the
    first gear's `type` is **1**, the micro gear's **255**. Neither is an enable byte — the block's
    own switch at 154 is written 0 for on and **1** for off by Flydigi's writer
    (`MappingConfigParser.cs:392`), unlike the grip block at 145, which uses 0 and 0xFF, and
    `set_trigger_motor` writes 0xFF for off.
  * **The force-trigger bind is populated at rest**: `bind.Filter = 10, bind.Scale = 10,
    bind.Param = [100, 1, 255, 70, 0]` with `Type = 0` and `bind.Type = 0`.
  * **`joy extra`'s bank[9] is a preset curve, not a blank.** `50 62 75 87 100 112 125 137 150` is
    evenly spaced, i.e. the straight line — an output level biased by 50, with 50 silent, 100 half
    output and 150 full. `isRound = 0` is Rectangle.

## Key table, at offset 13

32 entries of 3 bytes, indexed by `ControllerKey` id: **target, turbo mode, turbo frequency**. The
target byte carries three sentinels — `TARGET_IDENTITY = 255` (the key does what the shell says),
`TARGET_MACRO = 32` (the key runs a macro), `TARGET_KEYBOARD = 254` (keyboard/mouse, or
multi-function) — and turbo modes are `TURBO_OFF/WHILE_HELD/TOGGLE = 0/1/2`.

The frequency byte is what turns turbo on. At 0 the entry is written `[target, 0, 0]` and the mode
byte is dropped, so a mode with no frequency does nothing — `--turbo-toggle` on its own is a no-op.
A non-zero frequency needs a real target id, so `set_mapping` replaces the identity and keyboard
sentinels with the key's own id and clamps the frequency to 255 (`flydigi/mapping.py:546-553`).

The table takes effect as the packet lands, unlike the macro page beside it. M1–M6 are
remap *sources* only: they have no XInput id, so `APEX5_KEYS` is what may be remapped and
`XINPUT_TARGETS` is what a remap may point at.

## Sticks: dead zones, curves and circularity, at offset 109 (J1)

Two blocks hold a stick:

  * **offset 109**, 7 bytes per stick (left 109, right 116):
    `type, center, p1.x, p1.y, p2.x, p2.y, end`, on a **0..127** scale.
    `type` is `JoystickSensitivityType {Default=0, Quick=1, Slow=2, Custom=3}` — labelled
    Default / **Instant** / **Delay** / Custom in the UI. p1/p2 are the interior breakpoints of a
    four-node **polyline**, not Bezier controls; the editor draws three straight segments.
  * **offset 790**, 12 bytes per stick (left 790, right 802), `m_fdg_macro_joy_extra_v2_struct_t`:
    `type, bank[9], isRound, end` — the same curve resampled to nine evenly spaced points, plus
    `JoystickCircularityType {Rectangle=0, Circular=1}`. Bank values are **biased by 50**: 50 is no
    output, 150 is full, and a straight line is evenly spaced between them. The byte range is
    **0..150**, not 50..150 — the sampled output is clamped to −50..100 *before* the bias, so a 60%
    dead zone compiles to `[0, 0, 0, 0, 26, 56, 87, 118, 150]`, five bytes below 50.
    `set_joystick_shape` clamps each bank byte to 0..150.

**The pad plays the nine-point bank; nothing in the core block reaches it.** Measured with
`tools/joystick-curve-probe`, which writes a value that ought to silence the stick and watches evdev
to see whether it does. Five runs against a baseline (`--baseline`, `--flat-bank`, `--flat-core`,
`--custom-type`, `--raw-edge`; also `--raw-center`, `--circular`, `--side`, `--seconds`):

| what was written | stick output |
|---|---|
| nothing (baseline) | normal, 6778 deflections, reaches 100% |
| **bank at 790+1..9 flattened to all 50** | **completely silent** |
| core polyline at 109 flattened, **and type set to Custom** | normal, reaches 100% |
| `edge` byte at 801 = 236 | normal, reaches 100% |
| `edge` byte at 801 = 90 | normal, reaches 100% |
| `edge` byte at 801 = 100, a degenerate step | normal, reaches 100%, smooth from 2.3% |

**The pad does not apply the curve and then quietly renormalise it.** Command 3 reports stick
auto-calibration and the rebound algorithm both *enabled*, but renormalisation cannot be why the
core block looks inert: `--flat-core` sets the polyline to zero output across the whole range and
`edge = 100` collapses the end node onto the start node, and rescaling a constant zero gives zero
while rescaling a step gives a step — not the smooth sweep from 2.3% that both runs produced.
Nothing rescued the flat bank either.

So the core polyline, the type byte, `center` and `edge` are **the source form the bank is compiled
from**, not leftovers: the renderer builds a four-node polyline out of them and samples it. As with
the lighting config, the host computes and the pad plays. Space Station writes both blocks
unconditionally, so nothing in the app reveals which one the pad reads.

**The compiler, straight out of `index-DM6mSbRo.js`** (constants `Pe=127, Ie=100, ze=50, V=9,
ce=-50`):

```js
Ke(start,end,p1,p2) = [start, p1, p2, end]        // the node list
c={x:0,y:0}, y={x:100,y:100};
center > 0 ? c.x = center : c.y = -center;         // start node: dead zone, or Offset
edge   > 0 ? y.x = 100-edge : y.y = 100+edge;      // end node: outer dead zone, or ceiling
Xe(p,start,spanX,spanY) = {x: round(start.x + spanX*p.x/127),
                           y: round(start.y + spanY*p.y/127)}
                                                   // interior nodes, remapped into
                                                   // the span the two ends leave
qe(x0,y0,x1,y1,x) = y0 + (y1-y0)*(x-x0)/(x1-x0)    // plain lerp, three straight segments
for X in 0..8:  bank[X] = clamp(round(sample(100*X/8)), -50, 100) + 50
```

Everything is in 0..100 percent, including `center` and `edge`; `p1`/`p2` are stored as 0..127.
`Xe`'s y reduces to a plain `×100/127` whenever the start node sits at y=0 and the end node at
y=100, which is every curve this project writes because the negative half of `center`/`edge` is
refused. Its x never does: an interior point is always placed at `start.x + span × p.x/127`, the
same remap as Flydigi's `CalculatePoint`. Drop it and the nodes stop being ordered as soon as the
dead zone passes the first breakpoint — a `center` of 60 puts the start node at x=60 while `p1` sits
at x=49.6, the segment between them runs backwards, and the curve comes out at *full* output exactly
where it should be silent.

The sampler behind `sample` is not a plain lerp either. `ct(x, nodes, p1)` takes the remapped `p1`
as its third argument: it returns the end node's y for any x past the last node, treats an equal-x
pair as a step rather than lerping across a zero span, and skips the first segment when `p1.y` is 0.
`mapping._along` reimplements the step and the extrapolation, and `stick_nodes` drops both interior
points when the two ends leave no span. A naive lerp reports full output across the whole travel for
a dead zone of 100.

`type` is only a preset picker for `p1`/`p2` — Default (64,64), Instant (64,96), Delay (64,32), all
with `p2 = (127,127)` — and any manual edit to a node forces it to Custom. Flydigi's Default point
is device-dependent: `defaultSensitivityPoint1` is `(15, 23)` on a k2 and `(64, 64)` on everything
else. Picking a preset also zeroes `center` and `edge` — their handler and `set_stick` both do it —
so "reset to Default" restores the whole factory shape. **`STICK_PRESETS` writes Default as (63,63),
not (64,64)**: 63 is what the pad ships with, so resetting reproduces a factory blob byte for byte.
The compiled bank is identical either way; only the stored polyline differs.

**`center` and `edge` are cross-clamped so `center + edge <= 100`.** They consume the same travel,
and a zero span turns the curve into a step. The field being set is the one that gives way, so
moving one slider never moves the other.

**A GUI must compute the bank.** Writing `center` moves a number, dirties the profile and changes
nothing the hand can feel. Whatever curve the UI offers has to be sampled into nine points at
x = 0, 12.5, … 100 as `clamp(trunc(output_percent), -50, 100) + 50`. **Truncate, do not round**:
that reproduces the factory bank `50 62 75 87 100 112 125 137 150` exactly, where Space Station's
`Math.round` (the `round` in the listing above is theirs) gives `50 63 75 88 …`.
`stick_bank()` truncates on purpose.

**`isRound` is the exception in the extra block, and it is firmware-side.** Circularity is a
two-dimensional property and the bank is a one-dimensional magnitude curve, so the bank *cannot*
express it and something in the pad has to. Rolling the stick around its rim and into all four
diagonal corners:

| `isRound` | furthest corner | per-axis |
|---|---|---|
| 0, Rectangle (factory) | magnitude **1.19** | 1.00, 1.00 |
| 1, Circular | magnitude **1.00** | 1.00, 1.00 |

Rectangle lets the diagonal run past the unit circle; Circular pins it to exactly 1.00, i.e. about
0.71 per axis. A game that tests each axis against a threshold — "run if |x| > 0.8" — sees 0.71 on
the diagonal and stops the character running diagonally while the stick is hard over, so Circular is
a trade-off rather than a neutral preference. A true square output region would put the diagonal at
**1.41**, but the stick's own gate is octagonal and never reaches it, so a probe must threshold
between 1.00 and 1.19 to separate the modes; 1.25 reports both as circular.

So: **bank and `isRound` reach the firmware; the core polyline, the type byte, `center` and `edge`
do not.** The type byte is written into **both** blocks anyway, because the SDK regenerates the
extra block's copy from the core one on every write and a blob where they disagree is a state no
vendor tool produces.

Because evdev is event-driven, a silenced stick and an untouched one produce the same trace — no
events — so `joystick-curve-probe` starts on a button press and counts button events throughout the
window. Buttons are an input no stick curve can suppress, so zero axis events alongside live button
events is the proof.

Four traps in these two blocks:

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

    So there is no negative dead zone. **Only the positive half is written**, because the SDK's
    reader folds a byte over 127 to `127 - byte` at four sites (`MappingConfigParser.cs:475, 476,
    689, 700`) while every one of its writers emits a plain two's-complement cast: −20 is written as
    236 and reads back as −109. Positive values encode identically under both readings, so
    `set_joystick_curve` accepts 0..100 and raises on the rest rather than picking one of two
    incompatible encodings. Neither byte reaches the firmware, so the encoding decides only what
    Space Station displays for a profile written here.
  * **Core `end` is not the UI's "Edge" and is left read-only.** Edge writes the *extra* block's
    trailing byte, a different protobuf field. Nothing in Flydigi's application ever assigns core
    `end`, and their reader corrupts it above 127. The pad ships with 127 there.
  * **The bank must be exactly nine.** Flydigi's writer loops over however many points it is given
    with no bound, so a tenth lands on `isRound`, an eleventh on `edge`, and a thirteenth starts
    overwriting the other stick. `set_joystick_shape` refuses instead.

`tools/stick-feel` (`--dead-zone`, `--side`, `--seconds`) applies a curve and drives the grip motors
from the stick's own evdev output, so the buzz is the reading. At the default 60% dead zone the
bottom of the travel went audibly, tangibly silent before the rumble came in. A felt dead zone reads
smaller than the figure written, for two reasons: in Rectangle mode a traced circle pokes over a
magnitude threshold at the 45° points where a straight push at the same radius does not, an artefact
of deriving magnitude as `sqrt(x²+y²)`; and the threshold is in the output domain, where sticks
saturate electrically before the mechanical stop, so 60% of range arrives before 60% of the throw.

Measuring the threshold numerically needs the raw stick position to compare the curved output
against, and xpad only ever shows the curved one. The vendor input stream carries the sticks
alongside the IMU — `motion.STICK_OFFSETS = (4, 6, 8, 10)`, four signed 16-bit little-endian axes,
left X/Y then right X/Y, `00 80` = −32768 and `ff 7f` = +32767 — though `motion.parse` still returns
only gyro and accel. Whether those axes are the pad's raw position or the curved output is untested,
and that is what a numeric dead-zone measurement needs.

The GUI offers the presets and the resulting bank, on `SticksPage.qml` over `StickModel` in
`gui/models/profile.py`: a 25% dead zone compiles, writes in two packets and reads back off the pad
byte-identical. Dragging `p1`/`p2` (`JoystickSensitivityType.Custom`) is not built, though
`set_stick()` already takes `point1`/`point2` and `stick()` returns both blocks as one dict. In
Space Station the curve stays read-only until Custom is selected.

## Trigger travel, at offset 123 (J3)

7 bytes per trigger, the same 7-byte struct as the joystick core block but on a **0..255** scale,
and with no sign convention at all — the parser reads `zero` and `end` raw where the joystick folds
them.

Writing `zero` and `end` alone leaves the two control points where they were, stranding the
breakpoints outside the window they are meant to bound. Flydigi writes six bytes from two numbers —
`Point1 = (Start, Start)`, `Point2 = (End, End)` in `ControllerRepository.cs:885-890` — and the
factory blob agrees exactly: `0 0 0 0 255 255 255`. `set_trigger_curve()` mirrors by default, sorts
the pair, and allows them to be equal, since Space Station's range slider passes neither `pushable`
nor `allowCross` and dragging one handle onto the other is reachable. `mirror_points=False` is there
for a caller deliberately shaping the curve. `type` stays read-only: a bare `int32` the SDK
round-trips and never decodes, reaching no UI, and the pad ships with 0.

**This block is Space Station's "Stroke Setting", and the pad plays it.** Measured with
`tools/trigger-stroke-probe` (`--curve`, `--param`, `--baseline`, `--start`, `--end`, `--side`,
`--seconds`, plus `--under-race`, `--lock`, `--lock-at`), which writes a degenerate 0..16 window to
one trigger and leaves the other as an in-run control: the written side produced **17 distinct evdev
values** against the control's **240**, in the same sweep, with 84% of the pull pinned at full scale.
17 is exactly a 0..16 window. Both triggers still spanned the full 0..255 output, so the window
moves the *physical* travel and not the range the game reads — which is what Flydigi's own tooltip
claims (`effect_travel_range`: "Sets the physical actuation range of the triggers without affecting
the upper and lower limits of the trigger data in-game."). Bringing `end` in is a software hair
trigger.

**And the neighbouring candidate is inert.** The same probe wrote the same window into `Param[0]`/
`Param[1]` of the force-trigger block — 195/196 and 215/216, the `AdapterTriggerTypeNormal
{ Start, End }` record — and got **238 against 239**: no difference at all.

**One stroke slider, two destinations.** Space Station has a single stroke control, and
`supportAdaptTrigger` chooses where it lands:

  * **off** — `TriggerConfig.Start`/`End` → `configBean.Zero`/`.End` → this block, at 123.
  * **on** — `AdapterTriggerTypeNormal.Start`/`End` → `TriggerAdapterConfigBean.Param[0..1]` →
    195/196 and 215/216.

Both halves are in `ControllerRepository.SaveTriggerConfig` and in the renderer's read effect. But
the renderer also sets `triggerStrokeUsable` to **`!supportAdaptTrigger`** (`index-DM6mSbRo.js`,
alongside `Me(!S.supportAdaptTrigger)`), so on a pad *with* adaptive triggers the slider is hidden
outright, and the panel holding it is itself gated on `triggerType === Normal`, so on a k5 the
General effect draws an empty box. Space Station therefore never edits the stroke window on this pad
**by either route**. `Param[0..1]` under mode Normal are only ever round-tripped through the blob,
and the live command for that mode, `ForceTriggerConfigNormal`, carries no parameters at all — just
`[side, 0]`. The Triggers page writes 123, offering the pair as **Travel start / Travel end**.

`Controller` carries nine capability flags — `LinearButton`, `Motion`, `Wheel`, `Led`, `Vibration`,
`TriggerVibration`, `ForceTrigger`, `Ns` and `Screen` — and none describes a mechanical trigger
stop. This pad honours the block regardless.

## Gyro mapped to a stick, at offset 137 (J2)

8 bytes, `m_fdg_macro_motion_mapping_struct_t`, with a 6-byte response curve away at **830**:

```
137  type  keyid  method  zero  sensity_x  sensity_y  mode  keyid_ext
     0     12     0       4     25         20         0     0          ← factory
830  zero  p1.x   p1.y    p2.x  p2.y       end
     0     63     63      127   127        127                         ← factory
```

The struct's field names are the firmware's; what they carry is
`MappingConfigParser.ParseToMotionConfig` and `ParseMotionConfigToArray`:

| byte | field | is |
|---|---|---|
| 0 | `type` | `MotionMapType {Off=0, LeftJoystick=1, RightJoystick=2, Mouse=3}` |
| 1 | `keyid` | `MappingTypeJoystick.EnableKey[0]`, a `ControllerKey`; 255 is None |
| 2 | `method` | `MotionEnableType {Click=0, Press=1}` — toggle, or on while held |
| 3 | `zero` | `DeadZone`, 0..100 |
| 4, 5 | `sensity_x/y` | `Sensitivity`, 0..100, written to both from one number |
| 6 | `mode` | `MotionUseMode {FPS=0, Racer=1}` |
| 7 | `keyid_ext` | `EnableKey[1]`, the second enable key |

The mapping runs on the pad, so it works in any game with nothing running on the host — which on
Linux is otherwise Steam Input only. The pad's own UI warns that enabling it lowers the polling
rate. `MotionMapType.Mouse` is not a pad feature — see *Ruled out* in [PROGRESS.md](../PROGRESS.md)
— but it is written here like any other value: refusing it in a byte layout would leave a profile
brought over from Windows uneditable, so it is the *app* that does not offer it.

### Measured on the pad

`tools/gyro-map-probe` writes the block, then reads both sticks and the enable key off evdev while
the pad's own motion stream counts how far it was actually tilted. The mapped stick is the reading
and the other one is the control; a hand on the shell shows up as the control moving and the window
is scored spoilt. Five windows, on a wired Apex 5, firmware 7.0.3.0:

| Window | What was written | Reading |
|---|---|---|
| 1 | nothing | 962 tilt samples, both sticks flat. Tilting alone drives nothing. |
| 2 | right stick, Hold, key LB | **peak 0.97 of full travel, driven** while LB held; nothing while released |
| 3 | right stick, Hold, first key None, **second key** RB | **peak 0.91, driven** while RB held. Byte 7 works on its own. |
| 4 | right stick, **Click**, key LB | 2.9 s driven · 2.7 s quiet · 4.6 s driven · 3.8 s quiet · 1.9 s driven |
| 5 | window 2 again, **curve at 830 flattened to zero output** | peak 1.10, driven — unchanged from window 2 |

So: **the pad plays the block, the enable key gates it, byte 7 is honoured by itself, Click toggles,
and the response curve at 830 is inert.** Window 4 is the weakest of these as a finding — Click is
the antithesis of Hold and was never really in doubt — and it is here because the harness made it
nearly free once the other four existed.

Three things came out of the runs that matter more than the table does:

  * **Releasing the enable key does not re-centre the stick.** It parks wherever the gyro last put
    it — 0.27 of full travel in window 2, 0.26 in window 3 — and stays exactly there: both post-
    release samples were identical and the axis sent nothing more. A game goes on reading a stick
    held a quarter over until the gyro is switched back on and tilted back.
  * **The block at 830 is a feature Flydigi never finished** — see its own section below. Not
    merely unmeasured, and not waiting for an interface to be written for it.
  * **An off-stretch is invisible to anything that counts events.** evdev only reports a changing
    axis, so a gyro that has been switched off produces *no samples at all* — a 3.8 s silence
    arrives as one event. Two verdicts in the probe were wrong before this was accounted for: a
    parked stick read as "still driven" because it sat far from centre, and Click read as "not a
    toggle" because its off-stretches were discarded as too short. Stretches are measured in
    seconds and liveness as mean step between samples, never as a count.

**The two sensitivity bytes ship different.** 25 and 20 at the factory, which their own software
cannot produce: `ParseMotionConfigToArray` assigns `Sensitivity` to both, and their reader collapses
the pair with `Math.Max`. So the block leaves the factory in a state no round trip through Space
Station preserves, and one number is the honest thing to show.

**The mode is derived, not chosen.** Nothing in Space Station's gyro panel sets `UseMode`; its save
path picks it from the target — `RightJoystick` or `Mouse` → FPS, `LeftJoystick` → Racer — and
leaves it alone when the target is Off. What the firmware does differently between the two is
unmeasured. `set_motion` derives it the same way and takes an override.

**The factory's enable keys are live buttons, not blanks.** Byte 1 is 12 (`Lt`) and byte 7 is 0
(`Up`) on an untouched pad — not the 255 that means None. Their writer only ever assigns byte 7 when
the enable type is Press, re-emitting whatever it read otherwise, so turning gyro mapping on in
Space Station on a fresh pad hands D-pad Up a share of it. Which of the two the firmware honours,
and whether it reads the second at all under Click, is unmeasured. `set_motion` reproduces their
rule rather than overriding it: byte 1 is written unconditionally, byte 7 only under Press. What
the app does differently is show what is really stored — a key stranded in byte 7 is named on the
page instead of reading as "none".

## The response curve at 830 is a feature Flydigi never finished

The block is the joystick core block with its type byte removed — `zero, p1.x, p1.y, p2.x, p2.y,
end` on the same 0..127 scale — and it ships as the same identity line, `0 63 63 127 127 127`. Two
independent lines of evidence say nothing plays it.

**The firmware does not read it.** Measured with `tools/gyro-map-probe --window 5`: the curve
written flat to zero output, with the mapping otherwise byte-identical to a window that drove the
stick to 0.97 of full travel, and the stick still reached **1.10**. Three things rule out the
obvious ways to be wrong about a null result:

  * **The bytes arrive.** Written, applied and read straight back off the pad: `830..836` returns
    `0 0 0 127 0 0`, exactly what was sent.
  * **The tail block is live on apply**, so this is not a "needs command 166 first" result — the
    nine-point bank at 790 is in the same tail, and flattening *it* silences the stick outright
    (`tools/joystick-curve-probe`, table above).
  * **The pad evaluates no polyline at all.** The same probe found the stick's core polyline at 109
    equally inert: flattened to zero output it changes nothing, because the host compiles the curve
    to the bank and the pad plays the bank. 830 is that same source form — and there is no compiled
    companion for it anywhere in the blob. The unclaimed ranges are 227..230, 768..770, 814..820 and
    825..830, none of them nine points.

**And Space Station never authors it.** `Smoothness` occurs **five times in the whole application**,
which is the entire evidence and worth listing: the child component reads `i.smoothness` and emits
`callback("smoothness", …)`; the parent's read handler stores `O.mappingTypeJoystick.smoothness`;
its callback updates that state; and its render passes the value back down as `Smoothness`. The
round trip is fully wired — and then:

  * the panel is drawn inside the **literal** `className: "trigger-mode-right none"`. Not a
    conditional: the same file hides things two other ways when it means to, `U("x", {none: cond})`
    and `+(i.vibrationUsable ? "" : " none")`. This one is unconditional by construction.
  * the parent passes `Smoothness` and the child reads `smoothness`, so the slider's value is
    `undefined`. `connect(i => i.global)` cannot rescue it: those five occurrences are exhaustive,
    so no reducer or store key supplies the lowercase name either.
  * the save path never assigns the field, writing `sensitivity`, `deadZone`, `enableType` and the
    enable keys and nothing else.

Their slider is also a 0..255 scalar over a field that is a four-node curve —
`MotionSmoothnessConfig {Zero, Point1, Point2, End}`, not an int. So this is not three bugs in a
working feature; it is a feature abandoned partway through wiring, left in the struct and
round-tripped by `MappingConfigParserV31` ever since, which re-emits all six bytes on every write
from whatever its reader put there.

`set_motion_curve` stays because the block is real and its layout is confirmed against that writer.
Nothing should offer a control for it.

## Grip vibration, at offset 145

9 bytes: a master switch, then per side `switch, min, max, scale`. The switches are **inverted** —
0 is on, 0xFF is off. `min`/`max` bound how hard the motor is allowed to run, and the pad clamps the
game's rumble into that window, so they are the intensity control; `set_vibration` keeps
`min <= max` rather than letting a slider produce an inverted range.

## Trigger vibration, at offset 154 (J5)

29 bytes: one shared enable, then per side two 7-byte gears
`{type, min, max, filter, min_start, scale, min_time}`. **The Apex 5 does not have these motors** —
`GenerateControllerApex5` sets seven capability flags and `IsSupportTriggerVibration` is not among
them, while Vader 3, 4 and 5 all set it, and `ConvertTriggerConfigBean` only reads the block when
that flag is on. The blob carries it regardless because it is one struct shared across the range,
and the factory bytes are populated (`1 30 80 5 1 50 0`), so gate a feature on the capability flag
rather than on the presence or the contents of the bytes.

`SaveTriggerVibrationConfig` writes four fields of the first gear and never touches the second:
`min`/`max` as an amplitude window (grip rumble above the ceiling acts as the ceiling, below the
floor as the floor), `scale` as strength, `filter` as a threshold below which the trigger stays
still. Two traps in it:

  * **`scale` is stored as the percentage their slider shows** (1..100) while `min`/`max` beside it
    are that same slider's percent scaled to a byte (`floor(pct * 255 / 100)`).
  * **Space Station syncs `scale` and `filter` across both triggers** while leaving the amplitude
    per side, though the bytes are per side either way. Whether the firmware reads the right
    trigger's copy is open, and answering it needs a pad that has the motors — a Vader 4 Pro. The
    sync is deliberate: `trigger_vibration_level_sync_tips` is "Adjusting one trigger syncs the
    other's vibration intensity.", `trigger_vibration_block_value_sync_tips` the same for the
    shielding value, and `trigger_vibration_enable_status_sync_tips` "Trigger Vibration Switch
    affects both left and right triggers." — so the single enable byte is shared by design.

`MappingConfig.trigger_motor()` reads and writes those four fields, and `tests/test_mapping.py`
asserts the layout against Flydigi's writer; nothing in the app calls it. `min_start`, `min_time`,
the gear `type` byte and the whole micro gear are unexplained, and a bench sweep for them needs a
pad that has the hardware.

## Stored trigger effects, at offset 185 (J4)

**The stored bind is the same structure as live command 82.** `ParseTriggerConfigToArray` writes, at
offset 185 + 20 per side:
`Type, bind.Type, bind.Filter, bind.Scale, bind.Param[5], MixedBorder, Param[10]`
(`MappingConfigParser.cs:376-386`). Live 82 takes 3 + 4 parameters; the stored form is 3 + **5** —
the same structure with one spare byte. The writer sets `bind.Type = (Type == 5) ? 2 : 0`, so bind
type 2 appears exactly when the stored effect is `Vibration`.

The block diagram, the per-effect parameter-slot table, the shared-slot rule and the constants an
effect writes into slots it does not use are in PROTOCOL.md §3c. `set_trigger_effect()` writes the
bind half and the bind type byte.

All six of Flydigi's `AdapterTriggerType` effects are in `SetForceTriggerCommandFactory` with their
own parameters, and `SaveTriggerAdapterConfig` says which byte each one lands in. They are in
`flydigi/effects.py` as one vocabulary — labels, Space Station's own slider bounds, and the slot
map — read by the profile editor, the live commands and `tools/flydigi_cmd.py` alike, so the wire
form and the stored form cannot drift.

**Lock, Recoil and Sniper are felt on hardware** at their default settings, and a stored
`Vibration` leaves as command 82, which is physically confirmed; the runs are in PROTOCOL.md §7.
Live mode **5** is the one nothing in Flydigi's stack ever sends: `ForceTriggerConfigVibration`
(`SetForceTriggerCommandFactory.cs:197`) is defined and never constructed, the config path turns
stored type 5 into command 82, `relay.translate_ds5` emits only modes 0-3, and pads with real
trigger motors use command **18**. What the firmware does with a mode-5 command is not settled;
PROTOCOL.md §3a has the runs and the rules for testing an effect here without a stale bind
invalidating it.

## Macros, at offset 230 (J6)

A macro is a sequence of button events the *firmware* plays: the key table entry for its trigger key
holds `TARGET_MACRO` (32), the steps live here, and nothing on the host is involved once it is
written. `m_fdg_macro_unit_struct_t` (`btn, count_l, count_h, type, step[64]`) and
`m_fdg_macro_state_struct_t` (`active`, a unit pointer, `cur_step`, `cur_time`, `keystate`) are the
firmware's own structs carried into the SDK.

The page is `m_fdg_macro_page_struct_t`, 538 bytes:

```
[0]        how many macros, 1..5; anything else means none
[1..6]     each macro's offset into the bodies, in 4-byte words
[6..538]   the bodies, each  [0] trigger key id   [1..3] step count, LE
                             [3] type             then 4 bytes per step:
                             cumulative time (16-bit, 10 ms ticks), key id, event
820..825   one repeat interval per slot, milliseconds / 10
```

Five macros and 128 steps between them, which is not two limits but one: 538 − 6 header = 532 bytes
= 133 words of body space, each macro spending one on its own header.

The repeat interval byte is `0xFF` where a slot has never been written — `MACRO_INTERVAL_UNSET`,
carried through as "unset" rather than reported as 2550 ms — and the settable range is
`MACRO_INTERVAL_MAX = 2540` ms.

`MacroEnableType` is `None=0, Once=1, Press=2, Click=3` — 2 repeats while the key is held and 3
toggles, which `mapping.MACRO_WHILE_HELD` and `MACRO_TOGGLE` name by behaviour. `MacroActionEvent`
is `Release=0, Press=1, LeftJoystick=2, RightJoystick=3, Hold=5` — the enum **skips 4**, and
guessing it from position would write an event the firmware does not know.

**Where this lives depends on the protocol version.** From v3.2 macros move out of the blob into
their own store behind commands 172/173/174, ten of them at 1 ms resolution. An Apex 5 reports
v3.1 and keeps them here. Confirmed twice over: `MappingConfigParser` branches on
`(ProtoVersion & 0xF) < 2`, and the hardware holds five cycle bytes at 820.
`MappingConfig.macros_in_blob` is the guard that refuses to write this region from 3.2 on.

**`controller_data` must be on for any of this to be measurable.** Third-party control switches it
off and leaves the evdev node present and silent, so a capture in that state cannot tell a firmware
that will not play a macro from a pad that is not reporting. `motion.read_transport` reports it.

Measured on hardware, with four paddles each given a signature no finger can produce — three taps of
one letter in 300 ms:

  * **They play.** `M1` produced `a a a` at exactly the 40/60 ms gaps written, `M3` held down
    produced `x x x` seven times over. The stored timings come back to the millisecond.
  * **A macro is stored by the write and played by the apply** — command **162** — while saving with
    166 only decides whether it survives a sleep; measurements in PROTOCOL.md §9. A caller compares
    `MappingConfig.macro_page` to know whether it owes an apply, rather than applying on every write
    and making the pad re-seat its trigger motors over a remap that never needed it.
  * **The repeat interval at 820 is the gap between repeats**, not a delay before starting or a
    step scale: 300 ms written, 300 ms measured between passes of a held macro.
  * **An orphaned body still runs, *alongside* the key table rather than instead of it.** The two
    are read independently and both fire. M1 with a table entry of `a` and an orphaned body of three
    X taps produced `press a`, `x x x`, then `release a` when the paddle came up — three times
    across two runs, identical every time. With the same key on both they coalesce into what looks
    like the macro alone. Neither application produces the state: Space Station drops the macro when
    the key is remapped away from Macro (`ControllerRepository.cs:765-773`) and `set_mapping` does
    the same, while their follow-up `WriteMacroConfigPartial` is gated on `ProtoVersion >= 770`,
    below which — this pad — the removal rides along inside the mapping blob write. Nothing in the
    firmware cleans it up.
  * **`Once` plays to the end whether or not the key is still down.** Visible in the same trace: the
    paddle came up between the second and third tap and the third tap arrived anyway, so a long
    macro keeps going after the key is released.

Steps are limited to keys XInput can carry, for the same reason `XINPUT_TARGETS` is: a step that
presses M1 is a step the host never sees. The trigger key has no such limit, so a paddle runs one.

**Recording is `flydigi/macros.py`**, which turns xpad evdev events into steps. `BUTTONS` and `HATS`
cover only the keys XInput carries; LT and RT are recorded as buttons crossing
`TRIGGER_THRESHOLD = 0.5`, because a step has no room for travel. Recording needs third-party
control off, for the same reason the measurements above did.

On the CLI, `macro-set` takes steps as `press:<key>`, `release:<key>` or `wait:<ms>`; both it and
`macro-record` take `--type {held,once,toggle}`, `--interval MS` and `--save`, and `macro-record`
also takes `--seconds` (default 10). In the app, `MacrosPage.qml` records a sequence off the pad,
sets its type and repeat gap, and deletes it, where Space Station also edits a recorded macro's
steps — output key, duration, interval — and builds one from nothing without recording at all.
`set_macro()` takes arbitrary steps already.

## Protocol versions, and what a v3.2 profile is

`ProtoVersion` at blob offset 0 is Flydigi's own *profile format* version, not protobuf's. An
Apex 5 reports **769 = v3.1**; a Vader 5's factory profile is **770 = v3.2**. This matters because
restoring a single slot means writing a factory profile, and that means writing the right shape.

**The blob is built in layers**, `MappingConfigParser.cs:866`:

```csharp
int num = config.ProtoVersion & 0xF;
int num2 = (num >= 2) ? 84 : ((num < 1) ? 79 : 84);   // packets of 10 bytes
MappingConfigParserV30.ParseConfigToArray(config, array);
if (num >= 1) MappingConfigParserV31.ParseConfigToArray(config, array);
if (num >= 2) MappingConfigParserV32.ParseConfigToArray(config, array);
```

  * **84 packets of 10 bytes is the 840 this project reads**, and 79 is the 790 recorded from the
    Vader 4 in [findings-other-devices.md](findings-other-devices.md). v3.1 and v3.2 are both 840,
    so a Vader 5 profile is the same size as an Apex 5 one.
  * **`MappingConfigParserV32` is empty** -- both `ParseDataToConfig` and `ParseConfigToArray` are
    no-op bodies. So v3.2 *adds* nothing to the blob.
  * **V31 is what writes 790, 820 and 830**, and its 820 write is gated on `ProtoVersion < 770`. So
    the only layout difference at v3.2 is that the macro-cycle block at 820 is absent.

**What v3.2 really changes is macro capability, not layout** (`:780`):

| | v3.1 | v3.2 |
|---|---|---|
| `GetMaxMacroCount` | 5 | **10** |
| `GetMaxMacroActionCount` | 128 | **256** |
| `GetMinMacroInterval` | 10 ms | **1 ms** |

and the bodies move out of the blob into commands 172/173/174, which nothing here implements.
`MappingConfig.macros_in_blob` already refuses to write 230..768 from 3.2 on.

**Built.** `mapping.macro_limits(proto_version)` is that table, `MappingConfig.macro_limits` reads
it off the profile's own version, and the Macros page takes its slot count, step budget and interval
ceiling from there rather than from a constant. Versioned off `ProtoVersion` rather than off the
model, since that is what Flydigi branch on -- a pad that gains v3.2 in a firmware update needs
nothing changed here.

The bodies moving out of the blob is built too: `mapping.MacroStore` is the store behind commands
172/173/174, laid out in [PROTOCOL.md](../PROTOCOL.md) §9a, and `MappingConfig.macro_store` carries
one so that `macros()` and `set_macros()` mean the same thing on either protocol version. Reading a
v3.2 profile fetches it; writing one writes it back, profile first, as Space Station do. A profile
and its store travel as a single value through `mapping.pack_config` -- which is what lets the
desktop app's worker signals, its per-slot cache, its dirty compare and its backup files carry a
v3.2 profile without each of them growing a second argument to forget.

**The split point is not the package-count byte**, which is the obvious rule and is wrong: the Apex 5
here reports **77** at blob offset 2 while its profile is 840 bytes, so `blob[2] * 10` is 770 and
would cut seventy bytes off the end of every profile. `MappingConfigParser`'s 84 is the packet count
of the *transfer*. The store is a fixed 1620 bytes and a profile is smaller, so length alone splits
them.

**Untested on hardware, and marked as such in the app.** No Vader 5 exists here, and unlike the rest
of the f5 support this path does not share a code route with anything measured -- it is new bytes
down a new command, where every macro measurement in this project was made against the v3.1 page.
The Macros page says so when the open profile is v3.2.

## The bean behind the blob, for translating a factory profile

Space Station's per-slot restore writes a bundled `Configs/Controller/<code>/default/
default_mapping_<DeviceType>.dat`, which is a protobuf `ControllerMappingConfigBeans` -- four
`ControllerMappingConfigBean`s under repeated field 1. Translating one into the wire blob is how a
model nobody owns gets a factory profile: the files ship for every model, `f5` included.

Top-level field numbers (`Flydigi.SharedResources.decompiled.cs:7882`) against the blob regions
this document already measures:

| # | field | where it lands |
|---|---|---|
| 1 | `CfgId` | not in the blob |
| 2, 3, 4, 5 | `ProtoVersion`, `PackageCount`, `DataVersion`, `Title` | 0, 2, 225, 770 |
| 6 | `JoystickConfigBean` | 109..123, and 790..820 via V31 |
| 7 | `KeyConfigBeans` | 13..109 |
| 8 | `VibrationConfigBean` | 145..154 |
| 9 | `TriggerConfigBean` | 123..137, 154..183, 185..225 |
| 10 | `MotionConfigBean` | 137..145, and 830 via V31 |
| 11 | `LedConfigBean` | **not in the blob** -- it goes out over 168/169 |
| 12 | `MacroConfigBean` | 230..768, and 820 via V31, v3.1 only |
| 13 | `OldLedConfig` | 3..13, the ten bytes this project skips |
| 14 | `Lunpan` | 183..185, the wheel block |

**The k5 file is the test, and it is committed.** `flydigi/factory_config.py` holds the Apex 5's
factory blob as read off the pad, so the translator is checked by running it over
`default_mapping_128.dat` and comparing: every field mapped wrongly shows up as a byte mismatch,
so it cannot be quietly wrong. `tools/mapping_bean.py` is the translator -- a schema-free protobuf
wire reader plus `MappingConfigParserV30` and `V31`'s emit paths -- and `tools/gen-factory-config
--check` is the gate.

**828 of 840 bytes match, and the twelve that do not are Flydigi's own, not the translator's.** Each
was pinned down by running all six k5 SKU files rather than by reasoning about one:

| Bytes | What | Why it differs |
|---|---|---|
| 142 | the gyro's second sensitivity axis | **Their format cannot express it.** The pad ships (25, 20); `MotionMapTypeJoystick` holds a *single* `Sensitivity`, read as `Math.Max(data[4], data[5])` and written back to both axes. A round trip through Space Station flattens the two to 25, in any version of the file. Structural, not stale data |
| 154 | the trigger-motor enable, for motors an Apex 5 has none of | **Authored, not read.** Within one Space Station release the files for DeviceType 128, 129, 133 and 134 say "disabled" and those for 135 and 136 say "enabled" — same model. The pad holds 0 |
| 776..786 | ten bytes of title padding | The pad zero-fills the title field to 786 and leaves 786..790 at 0xFF; their emitter copies the title into an 0xFF-filled buffer and pads with nothing. A convention, and both readers strip both fillers |

A *thirteenth* fails the check, which is what makes the gate worth having.

**All six Apex 5 SKUs are one profile.** 128, 129, 133 and 134 emit a byte-identical blob and
135/136 differ only at 154 -- so one k5 factory blob covers every edition, and the one committed
here, read off a DeviceType 128, is it. The files themselves differ by up to 2 KB and almost all of
it is `LedConfigBean`, which is **not in the blob**: factory brightness is 20 on the base model and
100 on the Eva edition. That is why `reset_config` restores the profile and not the lighting --
matching Space Station, whose restore also writes the LED config over 168/169, would mean shipping
one LED blob per model, and a single k5 one would put the base model's lighting on every themed pad
that restored a slot. The ten legacy LED bytes that *are* in the blob, at offset 3, are identical
across all six.

**How far apart the two models actually are.** Comparing the k5 and f5 beans field by field: 494
leaf fields, of which 336 are inside `LedConfigBean` and irrelevant, and **21 differ** among the
rest -- a different factory stick bank (`2d 3a 47 54 ...` against the Apex's `4b 57 64 70 ...`),
different trigger values (51/114 against 30/80), an extra key-table entry at index 20, the
`ProtoVersion` and `DataVersion`, and the macro-cycle block replaced by a single field, which is
the 3.2 move showing up in the bean.

**Provenance was the open question, and it is settled: the derived bytes are committed as device
state.** The k5 blob came off the hardware and is on the same footing as `flydigi/ds5_usb.py`; the
f5 blob is `default_mapping_130.dat` put through the translator above. They do not have equal
standing and `factory_config.py` says so -- the Vader's is what Space Station *would write* to
restore a slot, which is not provably what a factory Vader holds in flash. It is also the same bytes
their own restore sends, which is the standard the feature has to meet rather than a lower one. The
reasoning, and the fact that neither blob is claimed as this project's work, is in
[NOTICE](../NOTICE); the translator is committed beside them so the derivation can be re-run rather
than trusted.

The f5 blob differs from the k5's in 37 bytes: `ProtoVersion` 770, a different stick bank
(`45 58 71 84 ...` against `50 62 75 87 ...`), different trigger travel and trigger-motor values,
the trigger motors enabled, and the macro regions at 230 and 820 left at 0xFF because a v3.2 profile
does not have them.
