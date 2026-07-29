# The desktop app: findings

Behaviour that had to be learned rather than designed: what "apply" really means to
the pad, what a test can and cannot see through QML, and the two bugs that needed a
test written before the fix made sense.

Index: [PROGRESS.md](../PROGRESS.md). How to work in `gui/` is
[gui/README.md](../gui/README.md) — toolkit, runtime, layout, licensing.

## Apply vs save

"Apply" writes the changed packets (164/165) and takes effect immediately;
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

## Why the models are registered rather than injected

The widgets are gone. `gui/models/` holds view-agnostic state — no `QtWidgets`, no `QtQuick` — and
QML binds to it through the `App` singleton of the `Apex5` module, registered by `QmlElement`
decorators **rather than injected as context properties**. That is what lets qmllint type-check the
QML: a context property is invisible to it, and every reference through one is an unqualified access
it can only shrug at.

## Tests, and how to run them without hardware

`tests/fake_pad.py` answers reads, diffed writes, apply and save, and refuses a bad checksum by
staying silent exactly as the pad does — unchanged by the QML rewrite. `tests/test_device.py` is
the exception to the fake: the claim is a kernel behaviour, so it runs against real descriptors —
a socket pair for what a send does, two separate `open()`s of one path for what the lock does.
The desktop tests come in three layers, cheapest first:

```bash
for t in tests/test_{device,dsx,forza,games,mapping,monitor,prefs,relay,screen,screen_ota}.py; do python3 "$t"; done  # backend, no Qt
python3 tests/test_models.py     # headless -- no engine, no display
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
  * **A case that starts a read must wait for it to land.** `App.profile.select()` empties the name
    field synchronously but asks the worker thread for the profile, so a case that only waits on the
    field ends with a read in flight. It arrives during a *later* case's cleanup, where the
    read-settled guard reports "a read was still arriving" against whichever test was running then —
    two runs in three, blamed on a name-capping case that touches no device at all. The guard is
    right; wait on `Fixture.profileReads` in the case that caused the traffic.

`qmllint` is clean and worth keeping that way:

```bash
tools/generate-qmltypes
qmllint-qt6 -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml
```

`pyside6-project build` would be the framework's own route, but it shells out to
`pyside6-metaobjectdump`, which *parses the source* and so cannot see `constant=True` — every
constant property then reads as merely read-only and qmllint reports 99 bogus "binding might not
update" warnings. `tools/generate-qmltypes` dumps the live `QMetaObject` instead, where the flag is
correct. With those gone it immediately found a real bug: `pageStack.currentItem` is typed
`QQuickItem`, which has no `title`.

**Suite counts are deliberately written down nowhere.** The last recorded one sat wrong for months,
and `test_mapping.py` moved twice in a single session while this file was being corrected. Every
suite prints its own count; read it there.

## Two bugs that needed the test before the fix

Both are fixed, each with a test that fails without the fix.

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
`filter` / `min_start` / `min_time` do in the trigger-motor gears.

## Three things that cost an hour each if rediscovered

`gui/README.md` has the detail. Three things that will cost an hour each if rediscovered:

  * **The venv cannot load Kirigami.** Wheel Qt tags private symbols `Qt_6_PRIVATE_API`, Fedora's
    tags them `Qt_6.11_PRIVATE_API`, and it is mutual — and RPATH puts the wheel's own Qt beyond
    `LD_LIBRARY_PATH`, so there is no overriding it. PySide6 has to come from wherever Kirigami
    does. On this immutable system that is the `apex-dev` distrobox; no layering, no reboot.
    **Flatpak is the shipping target**: `io.qt.PySide.BaseApp//6.11` on `org.kde.Platform//6.11`
    builds PySide6 against the runtime's own Qt, so the mismatch cannot arise there at all.
  * **`tryVerify` over a QML binding never updates.** `tryVerify(() => !button.enabled)` sits until
    it times out on work that already succeeded. Use `tryCompare(App.profile, "dirty", false)` —
    a closure over a binding has no notify signal to watch.
  * **PySide6 cannot see delegate-created items**, and recursing `QObject.children()` over QML
    objects aborts the interpreter. That is why UI tests are QtQuickTest and live in QML.

When a QML symptom makes no sense, reproduce it in plain Python before theorising — that is what
finally found `FakePad` missing `ack_ok`, after three wrong guesses about layout and click delivery.

