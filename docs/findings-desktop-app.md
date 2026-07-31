# The desktop app: findings

What "apply" and "save" mean to the pad, and what a test can and cannot observe through QML.

Index: [PROGRESS.md](../PROGRESS.md). How to work in `gui/` is
[gui/README.md](../gui/README.md) — toolkit, runtime, layout, licensing.

## Apply vs save

"Apply" writes the changed packets (164/165) and takes effect immediately;
"Apply and save" additionally sends 166, which Flydigi's SDK gives a 10 s timeout against its 500 ms
default. `save_config` here waits 2 s (`flydigi/mapping.py:417`).

Confirmed on hardware: **an applied-but-unsaved change is lost when the pad sleeps** — not merely
on a power cycle. Applied lighting reverted after the pad idled out, and a profile renamed with
only "Apply" was gone after a hard power switch.

The two buttons therefore do not share an enabled state. "Apply" follows *dirty* — is there an edit
not yet written. "Apply and save" follows dirty **or** *saveNeeded* — has anything been written that
has not reached flash. Bind both to dirty and pressing Apply greys out the only way to keep the
change just made. The footer names which of the two states the app is in.

**Save has a third condition: the pad must be running the profile being edited** (`canSaveToFlash`).
Command 166 carries no slot id — it commits whichever config the pad is *running*. The
slot-addressed variant is a different command, 171 ([device-settings.md](device-settings.md)), which
neither `flydigi/` nor `gui/` implements: 166 is the only save this code can send. The pad can
switch profiles on its own with `FN + A/B/X/Y` (the `quick_switch` setting), so the running slot can
change underneath the app between status reads, and a mis-aimed 166 writes the wrong slot to flash
while reporting success. The button is disabled when the edited slot is not the active one, and
`ProfileModel.write(save=True)` refuses rather than committing, emitting `saveRefused` with "The pad
commits whichever profile it is running, so this would save the wrong one. Switch the pad to this
profile first, then save." The guard is in the model and not only in the view because `write` is a
public slot.

The same rule is why `mapping.read_config_preserving` — which browses a slot and puts the pad back —
is deliberately unused here: a profile browsed without switching to it could not then be saved. The
app opens profiles the way Space Station does, leaving the pad on the one being edited.

The whole write is taken under one `ctrl.claim()`: 164/165, a conditional 162, 166 and the
trigger-effect replay go as a single exchange, because 166 commits whatever is in working memory and
another write from this app landing between the write and the save would be committed with it
(`gui/worker.py:227-252`).

The label is spelled out rather than "Apply & save": a bare `&` in a button label is taken as a
mnemonic, swallowed, and drawn as an underline on the next character. `&&` escapes it; in QML it is
simpler to avoid the ampersand.

**166 is verified on hardware.** Slot 4 (factory, `data_version` 65535) was renamed to `SAVETEST`,
written with 164/165 and committed with 166; the pad was then switched off at its own power switch,
observed leaving the USB bus for 16 seconds, and woken with the logo button. The title read back as
`SAVETEST`. Repeating the run overwrites a factory profile.

Which profile is live is itself flash state: the pad came back running slot 4, the one it had been
switched to, not slot 1. A save does not move the version tag — `read_status` reported
`[23224, 65078, 65535, 65535]` before and after — because `write_profile` passes the config's own
`data_version`, so the tag is left alone. It stays 65535 across a save, and `read_status`'s per-slot
version cannot flag a config this app wrote. Whether the firmware takes the tag from the command
payload or from the blob's own field at 225..227 is undetermined: a write carrying the same value in
both cannot separate them. To make the tag useful as a cache key something has to write a *new* one:
Space Station rolls `Random().Next(65535)`, re-rolling while it matches the slot's current value,
and compares it against its cached copy to decide whether to re-read (`ControllerRepository.cs:1089`,
`:348`).

**Lighting has the same two buttons under a different rule.** `LightingPage.qml` binds them to
`App.lighting.dirty` and `App.lighting.dirty || App.lighting.saveNeeded`, and does *not* gate on
`canSaveToFlash`: the LED blob is not per-slot, so there is no wrong slot to commit. Saving lighting
shares command 166 but calls `mapping.save_config(ctrl)` with no version, so the command carries 0
rather than the running slot's own tag — the LED blob has no version tag of its own. What the pad
does with a zero there is unconfirmed, and saving lighting is unverified on hardware.

## Model registration and qmllint

`gui/models/` holds view-agnostic state — no `QtWidgets`, no `QtQuick` — and QML binds to it through
the `App` singleton of the `Apex5` module, registered by `QmlElement` decorators **rather than
injected as context properties**: a context property is invisible to qmllint, and every reference
through one is an unqualified access. `qmllint` is clean over `gui/qml`. The `qmllint-qt6`
invocation, the generated `Apex5/` module and why `pyside6-project build` is not the route are in
[gui/README.md](../gui/README.md); `tools/generate-qmltypes` additionally searches Qt's libexec and
PySide6's bundled copy for `qmltyperegistrar`, which does not ship on `PATH`, and pins
`OUTPUT_REVISION` to 68, the moc schema version qmltyperegistrar checks.

`pageStack.currentItem` is typed `QQuickItem`, which has no `title`; `Main.qml` casts it with
`as Kirigami.Page`.

## Tests

The suites and how to run them are in [gui/README.md](../gui/README.md) (the three Qt ones) and
[PROGRESS.md](../PROGRESS.md) (the backend loop); each prints its own count as `N/N passed`.
`tests/test_device.py` runs against real file descriptors rather than a stand-in pad, because the
claim is a kernel behaviour — a socket pair for what a send does, two *separately opened* handles on
one path for what the lock does.

Most assertions live in `test_models.py`, because most of the logic does. `test_shell.py` brings the
window up through `QQmlApplicationEngine` rather than creating it from QML: a top-level `Window`
instantiated inside a QML test is never placed in a graphics scene, and every page it pushes says
so. The QML layer exists for what only a running view can answer, and it has to be QtQuickTest:
**PySide6 cannot see an item created by a delegate** — a Repeater and a ListView both really build
theirs, QML counts them, and `findChild` finds none.

The QML cases are the eleven `tests/qml/tst_*.qml`, one per page; Macros and Setup have none, and
`tests/test_shell.py` only asserts that they open. `tests/qml_harness.py` drives them, putting `Pad`,
`Fixture` and `QmlDir` into the engine's root context; the fixtures are described in
[gui/README.md](../gui/README.md). Importing `gui.app` there is load-bearing — the decorators
register the types at import time, and without it the test files fail to compile with "module Apex5
is not installed". `App.start(False)` is the test seam: the harness starts the app with no polling
timer and swaps the fake pad in behind the worker, so QML's later `start()` is a no-op.

Traps:

  * `tryVerify(() => !button.enabled)` never sees a binding update: a closure over a binding has no
    notify signal to watch, so it times out on work that already succeeded. Wait on the model with
    `tryCompare(App.lighting, "dirty", false)` instead.
  * Recursing `QObject.children()` over QML objects aborts the interpreter with a shiboken
    assertion. `findChildren` is fine.
  * **A case that starts a read must wait for it to land.** `App.profile.select()` empties the name
    field synchronously but asks the worker thread for the profile, so a case that only waits on the
    field ends with a read in flight. It arrives during a *later* case's cleanup, where the
    read-settled guard reports "a read was still arriving" against whichever test was running then.
    Wait on `Fixture.profileReads` in the case that started the read.
  * **A test may not leave a worker thread running.** Qt calls `qFatal` if a `QThread` is still
    running when it is destroyed, so a test that raises part way through takes the interpreter down
    at exit with a core dump instead of a failure message.
  * **A window a test creates for itself never becomes active under the offscreen platform**, and
    clicks into it are dropped — showing up as an edit that applied but was never written,
    intermittently and on whichever case clicked first. `TestCase` is itself an Item in a window
    QtQuickTest shows and activates, so a page under test is instantiated inside the `TestCase` —
    with `createTemporaryObject`, so a case that fails an assertion cannot leave a page behind to
    confuse the next one.

## Placeholders and visibility

  * **A placeholder that is a sibling of a `ListView` inside a `Kirigami.ScrollablePage` never
    renders.** `ScrollablePage.qml` reparents *only* the Flickable child then sets
    `scrollingArea.visible = false` (line 362), leaving the placeholder in the hidden subtree. It
    must sit *inside* its `ListView`, which is KDE's own idiom
    (`kirigami/dialogs/SearchDialog.qml:250`, a `PlaceholderMessage` inside the view) and works
    because an empty view leaves `contentHeight` at zero, which sizes the content item to the
    viewport, so centring in it centres on screen.
  * Buttons binds `model: App.profile.loaded ? App.profile.keys : null`, because
    `KeyMapModel.rowCount` is a constant 23 and `_row()` fabricates an identity mapping with no
    config — a list left bound to it with no config open draws 23 editable rows of fiction.
    `GameFilterModel.total` reports the row count before filtering, so an empty Games page can tell
    "never downloaded the list" from "the filters match nothing"; the second offers to clear the
    filters rather than to re-download Flydigi's list.
  * `visible` does not answer *would this be drawn*: an explicit binding overrides the value a
    hidden ancestor propagates, so an item stranded in a hidden subtree still reports
    `visible === true`. `tests/qml/tst_buttons.qml` and `tst_games.qml` walk the parent chain
    instead (`drawable()`).
  * `Pad.failReads` makes the fake pad switch on the read and then go silent, the way a sleeping one
    does; the page must say so rather than showing nothing (`tests/qml/tst_buttons.qml`,
    `test_a_sleeping_pad_says_so_instead_of_showing_nothing`).

Open questions that still need the pad are listed in [PROGRESS.md](../PROGRESS.md); those about the
profile blob's own fields are in [findings-profile-blob.md](findings-profile-blob.md).

