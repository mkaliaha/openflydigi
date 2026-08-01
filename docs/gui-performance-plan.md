<!--
SPDX-FileCopyrightText: 2026 Mikalai Kaliaha

SPDX-License-Identifier: GPL-3.0-or-later
-->

# GUI performance: findings and plan

Scrolling any page of the desktop app is visibly uneven, badly enough to be a
usability problem. This is the working document for that: what was measured,
what an architectural review found, and the order of work. It should be deleted
when the work lands and the durable parts have moved into
[PROGRESS.md](../PROGRESS.md) and [gui/README.md](../gui/README.md).

## The symptom, and how it behaves

  * **Uneven from the first device read onward.** Suppress `_read_the_rest()`
    and the window is smooth; let device data reach the models and it is not.
    Pressing **Reload from pad** reproduces it. It never recovers.
  * **Every page**, including ones showing no device data.
  * **Not the display.** Measured on a 165 Hz panel: 97% of frames land in
    6.06 ms and rendering is solid.
  * Reported while scrolling with a **touchpad**, which is dense continuous
    input, not a wheel.

## What was measured

`qmlprofiler` over a real session, 165 Hz, 38 seconds. The recipe took three
obstacles to work out and is written up under
[Profiling this app](#profiling-this-app).

**49 frames in 38 s took over 40 ms, averaging 315 ms — about 15 s of 38 with no
UI.** All 49 have input events *inside* them, so they are stalls and not idle
gaps. Every one sits at the same phase boundary:

```
RenderThread:swap -> GuiThread:polishAndSync    median 1.15 ms   p99 169.60 ms
RenderThread:render -> RenderThread:swap        median 4.82 ms   p99   6.41 ms
GuiThread:polishAndSync -> RenderThread:render  median 0.07 ms   p99   0.41 ms
```

So render and sync are healthy and the GUI thread is not *starting* the next
frame. During dense input bursts (10 of them, 21.6 s total) pacing is nearly
perfect — 96.6% of frames at full rate — so the stalls fall in the sparser
stretches.

**A correction, and it matters.** It was concluded from this trace that no QML,
JS, binding or delegate creation runs inside 45 of the 51 gaps, and therefore
that the thread "has nothing to do". *That inference does not hold.*
`qmlprofiler` records QML/JS ranges only, so Python running in a queued
cross-thread slot or a `QTimer` timeout is invisible to it — every `worker.*`
reply slot wired at `gui/app.py:179-202`, and `dsmode._repoll`. "Nothing to do"
and "doing something the profiler cannot see" look identical in that trace.
Anything below that was ruled out on that basis is not ruled out.

## Ruled out, with the caveat above

The GIL as CPU contention (0.5% CPU during a poll); GIL handover latency
(`sys.setswitchinterval(0.0005)` changes nothing); the GIL in render sync (a
control with Python-backed bindings notifying at 60 Hz is smooth); thread
affinity (`moveToThread` is correct); Kirigami; the QQC2 style as such (a
control under `org.kde.desktop` is smooth); Wayland versus XWayland; PySide6
itself (plain PySide6 + Kirigami is smooth); a worker thread doing the full
device read and holding the pad open (smooth); the three poll timers; the
garbage collector (no collection reached 15 ms); page *rendering* (only one page
is `visible`).

**Every "smooth control" above shares a flaw worth remembering:** they were
scrolled without inertia — *"no inertion scroll, but smooth"* — so none of them
exercised the animated scroll path that the real app uses.

## Findings

An architectural review of `gui/` (17 agents, code-reading with an adversarial
verification pass) produced the following. Every claim below was checked against
the code; empirical claims from that review are marked where they were made
against the mock bus and so are **not** trustworthy for timing.

### The root cause it names: there is no state layer

`gui/models/` declares **273 `@Property`**, of which **98 are notified by a
single per-model `changed`** — ten such signals (`profile.py:98`, `:200`,
`:398`, `:525`, `dock.py:195`, `screen.py:60`, `settings.py:100`,
`lighting.py:77`, `dsmode.py:56`, `setup.py:154`). One edit invalidates a
model's whole surface rather than the field that moved.

And the getters are decoders, not field reads:

  * `MotionModel._motion()` (`profile.py:531-540`) re-decodes the profile blob;
    twelve properties each call it, so one `changed` costs 20 decodes of the
    same bytes.
  * `KeyMapModel.data()` (`profile.py:751`) calls `_row()` *before* it looks at
    the role, so a 23-row × 9-role sweep decodes the key table 207 times.
  * `ProfileModel.dirty` (`profile.py:1320-1324`) is an 840-byte compare
    computed fresh per read, read three times per `ProfileFooter`, on seven
    simultaneously-live pages.
  * `effectParams` (`profile.py:248`), `bank` (`profile.py:461`),
    `frameColours` (`dock.py:932`), `previewFrames` (`screen.py:337`) build a
    fresh Python list per read.

With a C++ backend this is merely wasteful — a getter is a virtual call. Here
every read is an interpreter call wrapping real work. **The design depends on
reads being free, and in PySide6 they are not.**

### Pages are cached and never die

`pageFor` (`Main.qml:166-177`) memoises every page; `openSection`
(`:179-192`) only pushes or replaces. Nothing calls `pop`, `clear` or `destroy`.

**Kirigami does not destroy the replaced page**: `ColumnView::replaceItem`'s
`deleteLater` is gated on `shouldDeleteOnRemove`, false once an item has a
visual parent, and `createObject(pageStack)` gives it one. So all fifteen pages
stay alive **and their bindings re-evaluate on every notify regardless of
visibility** — checking `visible` was the wrong question. Fan-out measured at
`dirtyChanged` 22 Python calls cold, 77 warm; a stick slider step 67 → 122.

It also makes `Component.onCompleted` mean "once, forever", which is how
`App.dsmode.refresh()` at `ControllerPage.qml:36` arms a 2-second `/proc` scan
for the life of the process.

### Blocking work on the GUI thread that is not device I/O

The rule this project states is "no HID on the UI thread"; it should be "nothing
blocking".

  * `ScreenModel._reencode` + `_write_preview` (`screen.py:144-198`) — pure
    Python per-pixel encode of every held frame, then a decode and a
    `QImage.save(PNG)` each. ~1.3 s per gesture for a 200-frame animation,
    ~7 ms for a still, in a plain slot reached from `CropStage.qml:188`.
  * `dsmode.state()` walks `/proc` twice per call
    (`flydigi/dsmode.py:311-317` — `running()` is `bool(running_pids())`, and
    nothing reads the `pids` key), 4.6–11.3 ms, every 2 s, forever.
  * `CropStage.qml:85/101` — `cache: false` and `asynchronous: false` on the
    `AnimatedImage`, so each preview tick decodes synchronously. The file's own
    table records 8 ms/frame and compares it against a 100 ms timer rather than
    a 6 ms frame.
  * `framingSettled` (`dock.py:796-799`) is an empty body, while `_reframed`
    (`dock.py:647-651`) re-samples 162 colours per pointer move (0.868 ms
    each). The deferral hook exists and does nothing.

### A functional bug: the Triggers knobs cannot be dragged

`TriggerSide.qml:32` is `Repeater { model: root.side.effectParams }`, and
`effectParams` returns a **fresh list on every read**, notified by the same
model-wide `changed` that a knob move emits (`profile.py:271`, `:229`). Moving a
knob therefore replaces the Repeater's model, destroying the delegate mid-drag
along with its mouse grab. Driven with synthetic pointer events: a slider
outside the Repeater fires `moved` 40 times over a drag; inside it, **once**.

`tests/qml/tst_triggers.qml:100` calls `.moved(60)` directly, which is why no
test catches it. `DockPage.qml:594-598` is the same idiom on a cheaper path.

### Wheel handling: controls eat the scroll

`org.kde.desktop`'s `ComboBox.qml:31` and `SpinBox.qml:35` both set
**`wheelEnabled: true`**; Qt's own default is `false` and the Basic style does
not set it. `main.py:59` selects that style. **Verified directly.**

So scrolling with the pointer over any combo box or spin box does not scroll the
page — it edits the profile. That is every page, and each of those controls is
wired straight into a model write (`ButtonsPage.qml:144/162/176`,
`TriggerSide.qml:25`, `StickSide.qml:47`, `GamesPage.qml:219`, plus twelve
`FormComboBoxDelegate`/`FormSpinBoxDelegate`).

Beyond the frame cost this is a **data-integrity** problem: on Buttons, an
accidental scroll over a combo box silently rewrites a key mapping.

### Smaller, real

  * `GamesPage.qml:135` has no `reuseItems` over 94 rows whose delegates cost
    ~0.78 ms to build. One line.
  * 93 of those 94 rows build a `ComboBox` that cannot be opened — exactly one
    game has more than one route (`flydigi/prefs.py:53-71`). `Loader` is the fix.
  * `StickSide.qml:77-116` repaints a `Canvas` per drag step; a `Shape` would be
    scene-graph geometry.
  * `DockPage.qml:268-270` reads a rebuilt Python list twice per swatch;
    `LightingPage.qml:172-179` already does this correctly with a role.

## Plan

**Do step 0 before anything else.** Two entries in the ruled-out list rest on an
inference that does not hold, and every ranking below is a hypothesis until it
runs.

 0. **Instrument the stall.** A watchdog thread that pings the GUI thread on a
    short timer and calls `faulthandler.dump_traceback(all_threads=True)` when a
    heartbeat is more than ~30 ms late. Twenty lines, no profiler, no install.
    It names the Python frame the GUI thread is in during a stall — or proves it
    is in none, which sends the search to `QSGThreadedRenderLoop` and the
    compositor instead. *Half a day including reproduction.*
 1. **Wheel handling.** `wheelEnabled: false` on the six bare controls, and a
    `WheelHandler { onWheel: wheel.accepted = false }` on the FormCards, whose
    delegates expose no such property. Worth doing for the data-integrity
    reason alone. *Half a day.*
 2. **The Triggers Repeater.** `effectParams` becomes a `QAbstractListModel`, or
    its notify splits so a knob move does not replace the model. Un-breaks a
    control that currently cannot be dragged, and needs a test that drives real
    pointer events rather than calling `moved()`. *One to two days.*
 3. **View fixes.** `reuseItems` on Games; `Loader`s for the per-row combo, chip
    and switch; the `AnimatedImage` flags; `dsmode.state()`'s double `/proc`
    walk. Several are single lines. *One to two days.*
 4. **Threading.** `ScreenModel._reencode`/`_write_preview` and `dsmode.state()`
    onto short-lived worker threads on the `SetupWorker` pattern; split
    `_reframed`'s two notifications and give `framingSettled` its body.
    *Two to three days.*
 5. **Page lifecycle.** `Kirigami.PagePool` + `PagePoolAction`, drawer actions
    generated from `sections` by a `Repeater` instead of fifteen copy-pasted
    blocks. Removes ~140 lines from `Main.qml`. Do it after step 4 so the
    `/proc` poll's lifetime has somewhere to hang. *One to two days.*
 6. **The state layer.** Cache-and-narrow-notify across all ten `changed`-signal
    models and 98 properties: getters become field reads, decoding happens once
    where the blob moves, signals are edge-triggered and per-property.
    `ProfileModel` first (1,559 lines, five façades), `DockModel` second.
    *Two to three weeks, mechanical but wide.*

Honest total: **four to six weeks**, of which steps 0–3 are under a week and
carry most of the user-visible benefit. Steps 0, 1 and 3 are worth doing
whatever the watchdog says.

### Target shape

  * **State crossing into QML.** Every getter is a field read. Decoding happens
    once, where the blob moves. Signals are edge-triggered and narrow — one per
    property or per genuinely-coupled group, never one per model.
  * **Model exposure.** `App`'s `@Property(Model, constant=True)` graph is right
    and stays. What goes is list-valued properties on the boundary: anything a
    view iterates is a `QAbstractListModel` with roles, updated with
    `dataChanged(row, row, [Role])`. Anything QML needs one element of gets an
    indexed `@Slot`.
  * **Page management.** Kirigami's own `PagePool`. Where a page must be cheap
    when hidden, liveness is a binding — `Binding { target: App.dsmode;
    property: "polling"; value: page.visible }` — not a construction side effect.
  * **Where work runs.** Nothing blocking on the GUI thread, HID or otherwise.

## Profiling this app

Three obstacles, all now solved:

  * `qDebug` is compiled out of Fedora's Qt, so `QSG_RENDER_TIMING`, `QSG_INFO`
    and `console.log` print nothing however the logging rules are set.
    `console.warn` and above still work.
  * `python3 -m gui` makes the interpreter eat `qmlprofiler`'s
    `-qmljsdebugger=…` as `-q -m`. Launch through a wrapper that puts it after a
    script path.
  * PySide6 has no `QT_QML_DEBUG` build flag, so the port stays shut until the
    process calls `QQmlDebuggingEnabler.enableDebugging(True)` before any QML
    engine exists.

A mock-bus run tells you nothing about any of this: `FLYDIGI_MOCK_BUS` answers
instantly, so a full read finishes in milliseconds instead of the ~5 s it takes
on the pad, and there is no ~970 Hz input stream on the node.

## Already fixed, for context

Committed in `4f1cfa9`: three identify-path reads given `until` predicates
(bus enumeration 1900 → 100 ms), `DevicesModel` no longer resetting the sidebar
picker every 10 s when nothing changed, and `preventStealing` on the crop stage's
drag.

Uncommitted at the time of writing: `DeviceModel.infoReceived`/`failed` writing
fields then notifying once each rather than five waves through the setters, and
`MacroModel.refresh()` no longer resetting when the rows are unchanged, with
tests for both.

**Not yet fixed and still true:** `_read_the_rest` takes **5.1 s on hardware**
because `mapping.read_status`, `motion.read_transport`, `settings.read_status`,
`lighting.read_config` and `mapping.read_config` all `send()` without an `until`
predicate — the same defect already fixed on the identify path. Measured:
`info 10 ms` (fixed) against `status 1005`, `transport 604`, `settings 504`,
`lighting 1504`, `profile 1504`. Those round numbers are timeouts expiring, not
the pad being slow.
