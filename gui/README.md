<!--
SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
SPDX-License-Identifier: GPL-3.0-or-later
-->

# GUI

The desktop frontend. **GPL-3.0-or-later**, as are the Qt-dependent files under
`tools/` and `tests/`. The file-by-file table is in [../README.md](../README.md),
the reasoning in [../LICENSE](../LICENSE).

## Import direction

**`gui/` may import `flydigi/`. `flydigi/` must never import `gui/`.**

MIT is one-way compatible with GPL: a GPL frontend importing an MIT library
leaves that library MIT, and anyone can still lift `flydigi/` on its own. An
import in the other direction would pull GPL code into the backend and destroy
that property.

It also keeps the backend dependency-free: `tools/flydigi-ds5` needs no Qt or
third-party Python package. It still requires Linux, writable `/dev/uhid`, and
access to the physical controller and its input nodes. PySide6 is imported by
this directory, by `tools/generate-qmltypes` and by the four GUI test files
(`tests/test_models.py`, `tests/test_shell.py`, `tests/test_qml.py`,
`tests/qml_harness.py`), and nowhere else. Code outside `gui/` that needs to
know whether PySide6 is present asks a subprocess (`python3 -c "import
PySide6"`) rather than importing it.

## Toolkit

PySide6 (LGPLv3), not PyQt6 (GPL-only). Avoid the Qt add-ons that ship
GPL-3.0-only under the open-source licence — Charts, Data Visualization,
Virtual Keyboard. `gui/qml/components/StickSide.qml` draws response curves with
`QtQuick.Shapes`, plotting the nine-point bank the pad plays for a stick. Do not
replace it with a `Canvas`: that path paints a texture in JavaScript on the GUI
thread and recompiles the plot on every slider step.

The interface is QML on Kirigami, and no file under `gui/` imports `QtWidgets`.
`QQuickStyle.setStyle("org.kde.desktop")` must run before the first
`QQuickWindow` exists, not merely before the window is shown, or Controls render
in the default style. `tests/test_shell.py` and `tests/test_qml.py` set the same
style, so a page that only lays out under the Basic style fails in the tests.

**Every combo box and spin box must ignore the wheel.** The desktop style sets
`wheelEnabled: true` in its own `ComboBox.qml` and `SpinBox.qml`. Because these
controls write their activated value to a model, an ordinary page scroll would
otherwise edit controller state. Bare `Controls.ComboBox` and
`Controls.SpinBox` therefore carry `wheelEnabled: false`, and FormCard rows use
`components/FormComboBox.qml` and
`components/FormSpinBox.qml`, which exist only to reach the control inside the
kirigami-addons delegate — it is private and no version aliases the property
out. `tests/qml/tst_wheel.qml` sends real wheel events and asserts both halves:
the model did not move and the page did.

Sliders require a separate mechanism because the desktop style's `Slider.qml`
does not use `wheelEnabled`; its background `MouseArea` changes the value and
emits `moved()`. Every slider writes `moved` into its model.
`components/SliderWheelGuard.qml` must therefore remain inside each slider,
above that background, and perform the page scroll itself after accepting the
event. `RangeSlider` already passes wheel events through and needs no guard.

**Interactive controls must preserve their model bindings.** Clicking a QQC2
switch assigns its own `checked` property and breaks a direct
`checked: someModel.thing` binding. A failed device write can then restore the
model while the control continues to show the clicked value.
`components/ModelSwitch.qml` applies the binding through a `Binding` element,
which survives the control's assignment;
`tst_device.qml::test_a_switch_still_follows_the_model_after_it_is_clicked`
guards this behavior. For the same reason, handlers must not assign
`components/SliderRow.qml`'s `value`.

The crop stage's zoom is the deliberate exception: scrolling a picture's zoom is
what a person means by it, so that slider keeps the wheel and settles on a timer
instead of on a release it never gets.

## Runtime

**PySide6 must come from wherever Kirigami comes from — a distribution package,
or `io.qt.PySide.BaseApp//6.11` on `org.kde.Platform//6.11` — never from pip.**
A PyPI wheel bundles its own Qt build, and Kirigami's plugin will not link
against it; the ABI incompatibility details are documented in
[requirements.txt](requirements.txt).

On an immutable system, a container avoids layering anything:

```bash
distrobox create --name apex-dev --image registry.fedoraproject.org/fedora-toolbox:44
distrobox enter apex-dev -- sudo dnf install -y python3-pyside6 kf6-kirigami \
    kf6-kirigami-addons kf6-qqc2-desktop-style qt6-qtdeclarative-devel
distrobox enter apex-dev -- python3 -m gui
```

`python3 -m gui` must be run from the repository root; the line above works
because `distrobox enter` inherits the caller's working directory.

`qt6-qtdeclarative-devel` is for [static checking](#static-checking) only — it
provides `/usr/bin/qmllint-qt6` and `/usr/lib64/qt6/libexec/qmltyperegistrar`.
Running the app needs only `python3-pyside6`, `kf6-kirigami`,
`kf6-kirigami-addons` and `kf6-qqc2-desktop-style`.

`build_engine()` in `main.py` adds `/usr/lib64/qt6/qml`, `/usr/lib/qt6/qml` and
`/usr/lib/qml` to the engine's import path where they exist, so Kirigami
resolves without an import-path environment variable; the only environment
variable any module under `gui/` reads is the stall watchdog's, below.
`tests/qml_harness.py` repeats the same list.

The application and organisation identifiers are `openflydigi`; the display
name is "OpenFlydigi", the desktop file is `openflydigi`, and the window icon is
the theme icon `input-gaming`. `flydigi.setup.STALE_DESKTOP` identifies the
legacy `flydigi-apex5` menu entry, which setup removes to prevent duplicate
launchers after an upgrade.

`flydigi.setup` refuses to write the application-menu entry from the host when
the host's `python3` cannot import PySide6, since the launcher would point at an
interpreter that cannot load Kirigami; install it from inside the distrobox, or
from the app's own Setup page. The entry hard-codes
`cd <repo> && exec python3 -m gui`, and inside a container it re-enters that
container by name — renaming or deleting the box turns the launcher into a dead
icon.

## Watching for stalls

`watchdog.py` records Python thread stacks when the GUI heartbeat is late. It is
disabled unless `FLYDIGI_STALL_WATCHDOG` is set:

```bash
FLYDIGI_STALL_WATCHDOG=30:/tmp/stalls.txt distrobox enter apex-dev -- python3 -m gui
```

The value is `[<milliseconds>][:<path>]`. A value without a leading number is
treated as the output path and uses the default threshold. Each detected stall
writes a `faulthandler` stack dump followed by `=== stall:` and
`=== stall over:` timing lines.

The `Current thread` block is the watchdog. The GUI thread is the block that
contains `gui/__main__.py`; inspect its deepest frame. If that frame is the
`qt_app.exec()` call, no Python frame was active on the GUI thread at the sample
and investigation should include Qt's render loop and the compositor.

```bash
grep -c '^=== stall:' /tmp/stalls.txt
grep '^=== stall over:' /tmp/stalls.txt
```

The second value in each completion line is the observed stall duration. See
`gui/watchdog.py` for sampling limits and interpretation. A startup-only dump
confirms that the watchdog was armed but does not prove that rendering was
smooth.

## Layout

    __main__.py       `python3 -m gui`, from the repository root
    main.py           QML engine setup: import paths, i18n shim, style, the window
    app.py            the application graph: models, worker thread, the wiring
    worker.py         device access, on its own thread
    watchdog.py       off by default: what the GUI thread is in when a frame is late
    i18n.py           the i18n*() shim the engine needs -- PySide6 has no KLocalizedContext
    models/           view-agnostic state -- no QtWidgets, no QtQuick
    qml/              Main.qml, pages/, components/
    requirements.txt  what the runtime must provide -- documentation, not a pip file

QML constructs `App`, so opening the device is a separate `start()`, and
`beginPolling()` is a further seam so a test can put a fake pad behind the
worker before anything is asked of a real one.

Polling is one `Get info` timer at two intervals: 30 s while the pad is
answering (`INFO_INTERVAL_MS`, watching battery and charge) and 2 s while it is
not (`SEARCH_INTERVAL_MS`, looking for it, since a sleeping pad leaves the USB
bus and so is missing rather than silent). It is stopped for the duration of a
screen upload.

**Polling also performs initial and reconnect reads.** There is no separate
startup read: when `beginPolling` observes a pad change from missing to
answering, it reads the profile, lighting, transport, and settings. Devices
present at launch and devices attached later therefore follow the same
initialization path. Reconnect reads retain unsaved edits through
`_read_the_rest(keep_edits=True)`; **Reload from pad** explicitly discards them.
`PROFILE_COUNT` is 4.

Requests reach the worker as signals rather than direct calls: calling a slot on
an object living in another thread runs it on the caller's thread, which puts
blocking HID traffic back on the UI thread. The two exceptions are
`DeviceWorker.request_stop_recording()` and `DeviceWorker.request_stop()`, plain
calls that set a bool, because the worker is inside a poll loop and a queued call
would arrive only once the loop had ended.

`worker.py` imports only `QtCore` from Qt and has one view dependency: it builds
user-facing status strings from `SETTING_LABELS` and `describe_setting` in
`models/settings.py`, so a wire field's name never reaches the status line.

Shutdown order is fixed: `DeviceThread.stop()` calls `request_stop()`, quits the
thread, waits `STOP_TIMEOUT_MS` (10 s), and only then calls `worker.shutdown()`.
`@Slot` adds a metaobject entry and nothing else, so `shutdown()` invoked
directly would close the descriptor on the caller's thread while the worker could
still be in `select()` on it. On a timeout the handle is left open deliberately.

## The QML shell

`qml/Main.qml` is a `Kirigami.ApplicationWindow` with a persistent global
drawer and one live section page. Pages are listed in its `sections` array as
`{name, icon, url, kinds, needs?}` and created by `pageFor()` with
`Qt.createComponent(url, Component.PreferSynchronous)`. `openSection()` pushes
at depth 0 and replaces thereafter. Once the replacement is established,
`releaseSection()` removes the previous page from the cache and destroys it on
a short delay. Page-local state therefore does not survive navigation; state
that must survive belongs in a model.

`Kirigami.GlobalDrawer.actions` is a list of `Action` objects and cannot be
filled by a `Repeater`. `Component.onCompleted` therefore creates one
`Kirigami.Action` per `sections` entry from a shared `Component` and assigns the
resulting list to `actions`. Add, remove, or reorder pages only in `sections`;
the drawer follows that list. `test_the_drawer_offers_every_section` verifies
the resulting order.

**A section belongs to a device kind and may require a capability.** `kinds` is
`["pad"]`, `["dock"]`, or null for an installation-wide section. The optional
`needs` field names a hardware capability. `sectionVisible()` checks both
`App.devices.currentKind` and the selected device's capabilities: Triggers and
Games require `adaptive_triggers`, and Screen requires `screen`. Hidden actions
remain in the generated action list, while `drawerActions` exposes only the
visible ones to the drawer and tests.

Only a change of *kind* moves the page: `onDeviceKindChanged` opens Controller
or Dock, so picking a dock takes you to it and picking a pad brings you back,
while switching between two pads leaves you on the page you were on. That
handler is a bound `readonly property` rather than a `Connections` block on
`App.devices`, because `Connections.target` is typed `QObject` and qmllint
cannot see that a `QAbstractListModel` is one — the generated qmltypes names the
prototype and stops there.

**The device picker is the status block**, not a combo box above it: a combo
would have named the device twice, once in the control and once in the heading
under it. With one device attached it is an ordinary block with no chevron and
no menu, which is the state most desks are in.

Device status — battery, connection, active profile — is the global drawer's
header rather than `ApplicationWindow.header`: with the drawer as a persistent
sidebar, that header takes the width of the content area and the x of the
window, so it hangs off the left edge. Errors are one `Kirigami.InlineMessage`
parented to `pageStack` and bound to `App.device.error`; transient progress goes
through `showPassiveNotification`.

**No controller schematic.** Flydigi's service agreement claims their interface
design and artwork, and a drawing accurate enough to be useful cannot be kept
clearly distinct from theirs. The Buttons page is a `ListView` sectioned by the
`cluster` role instead, over the six groups of `KEY_CLUSTERS` in
`models/profile.py` — face buttons, d-pad, shoulders and triggers, sticks,
system, paddles and extra buttons — so the 23 keys read in the order they sit on
the controller.

`i18n.py` is not a translation feature. Kirigami's `FormTextFieldDelegate`
evaluates `i18ndc(...)` inside a `TextMetrics` whose binding runs whether or not
the label it feeds is visible, so without the shim **every text field in the
application throws** `ReferenceError: i18ndc is not defined`. The shim is
JavaScript rather than Python because a Python callable placed in a context
property is not callable from QML, whereas a QJSValue function is; it
substitutes `%1`, `%2` …, translates nothing, and installs the KUIT `x`-prefixed
variants (`xi18n`, `xi18nc`, …) under the same implementations. `main.py`'s
`build_engine()` installs it unconditionally, and any other engine must do the
same, `tests/qml_harness.py` included.

## Models

`models/` holds view-agnostic state, one module per subject area:

| Module | Classes | What they hold |
|---|---|---|
| `device.py` | `DeviceModel` | connection, battery, and the transient status and error line, for the selected pad |
| `devices.py` | `DevicesModel` | every Flydigi device attached, and which pad and dock the window is showing. Two selections behind one picker — see below |
| `dock.py` | `DockModel`, `DockColoursModel` | the selected charging dock: its four switches, its lighting, what is sitting in it, and the picture half — a source image framed on the 334x304 window the LEDs are read from, and the 162 colours that come out of it |
| `dsmode.py` | `DsModeModel` | the DualSense switch's state |
| `games.py` | `GameListModel`, `GameFilterModel` | one row per game with its route resolved; the search text and route filter |
| `lighting.py` | `LightingModel`, `ColourListModel` | one lighting config, edited in memory until written; the up-to-five colours an effect cycles through |
| `profile.py` | `ProfileModel`, `ProfileListModel`, `KeyMapModel`, `MacroModel`, `MacroStepsModel`, `StickModel`, `StickSideModel`, `TriggerModel`, `TriggerSideModel`, `VibrationModel`, `VibrationSideModel`, `EffectParamsModel`, `MotionModel` | the open profile and the edits held against it; the four slots and what was read from each; one row per key, with what it sends and its turbo; the stored macros and their steps; per-side stick, trigger and vibration state; effect parameters; and motion settings |
| `imaging.py` | `CropFrame` | shared QImage-to-RGB888 conversion and crop/framing geometry for dock and screen images |
| `screen.py` | `ScreenModel` | the picture queued for upload, and the idle display setting |
| `settings.py` | `SettingsModel` | the command-3 block |
| `setup.py` | `SetupModel`, `SetupChecksModel` | the Setup page; one row per requirement, in the order a person would fix them |

Rules for these:

  * `Property` with a notify signal for everything a view shows, or
    `constant=True` where it cannot change. camelCase names, as QML expects.
  * `roleNames()` maps integer role IDs to byte-string role names.
  * No `QtWidgets` and no `QtQuick` anywhere under `models/`;
    `tests/test_models.py` asserts it. `models/screen.py`, `models/dock.py` and
    `models/imaging.py` are the ones that import a Qt GUI module — `QImage`,
    `QImageReader`, `QPainter`, for image decoding — which the rule allows.
    The dock's LED preview remains a QML `Repeater` fed a list of colour strings
    rather than a `QQuickPaintedItem`, keeping painting in the view.
  * Dirty tracking lives here, not in the view. `ProfileModel.dirty` is a byte
    comparison, `bytes(self._edited.blob) != self._slots.stored(self._cfg_id)`.
    `canSaveToFlash` is a separate gate, because command 166 carries no slot id
    and commits whichever config the pad is running.
  * Colours stay as RGB tuples in Python and convert to `"#rrggbb"` at the QML
    boundary.
  * Filtering is a model, not a view: `GameFilterModel` is a
    `QSortFilterProxyModel` over `GameListModel`, holding the search text, the
    route filter and `total`.

QML reaches them as the `App` singleton of module Apex5 1.0. Every module
containing a `@QmlElement` must define `QML_IMPORT_NAME = "Apex5"` and
`QML_IMPORT_MAJOR_VERSION = 1` in its own globals — the decorator reads them out
of the module, not from a package-level setting.

## Tests

```bash
python3 tests/test_models.py           # headless, no engine
python3 tests/test_shell.py            # window smoke test, the way main.py loads it
python3 tests/test_qml.py              # QtQuickTest: real clicks on real delegates
python3 tests/test_qml.py -functions   # list the cases
```

`test_shell.py` and `test_qml.py` need Kirigami and so run inside `apex-dev`;
`test_models.py` needs PySide6 only, and no test needs a display or a
controller. All three print a message and exit 0 when PySide6 is absent, so a
backend-only run stays dependency-free. The two window suites set
`QT_QPA_PLATFORM=offscreen`, `QT_QUICK_BACKEND=software` and
`QSG_RENDER_LOOP=basic` with `setdefault` in both test entry points, so a value
already exported in the shell wins — an
exported `QT_QPA_PLATFORM` puts the run back on a real display. None of the three
is optional: an offscreen run has no GPU context to share, and the threaded
render loop waits for one that never comes. `import QtTest` in the QML cases
resolves against the distribution's `qt6-qtdeclarative`
(`/usr/lib64/qt6/qml/QtTest`), not against anything PySide6 ships.

`test_models.py` and `test_shell.py` run a tuple of functions written out in
their `main()` functions: a test not added to its tuple never runs, and the
count still reads `N/N passed`. The QML suite is handed the `tests/qml`
directory instead and finds its own cases.

Test lifecycle rules:

- Wait on a model property or fixture counter, not a JavaScript closure around
  a control property with no notify signal.
- A case that starts an asynchronous profile read must wait for the matching
  fixture read counter before ending.
- Stop every worker thread during cleanup; destroying a running `QThread`
  aborts the process.
- Create test objects with `createTemporaryObject()` inside the QtQuickTest
  `TestCase`; synthetic clicks are dropped by a separate offscreen window.
- From PySide, use supported `findChild`/`findChildren` paths rather than
  recursing through QML objects with `QObject.children()`.
- Stub `dsmode.stop` in model tests. The real operation searches the process
  table and can stop a relay outside the test. `tests/test_dsmode.py` is safe
  because it passes only a relay PID that the test started.

Every interactive element carries an `objectName`: QtQuickTest addresses items
with `findChild(page, "name")`, and there is no other handle on an item a
delegate created.

**Reach content inside a `Kirigami.Dialog` through `contentItem`.**
`findChild(dialog, "…")` finds nothing, and neither does `findChild(page, …)` or
`findChild(suite, …)` — the dialog keeps its content in a control that is not a
QObject child of the dialog, so the recursion never reaches it.
Use `findChild(dialog.contentItem, "…")`.

**Do not wait for `opened` under the offscreen platform.** It remains false
because nothing activates the window, although the content is built and laid
out.
So `tryVerify(() => dialog.opened, …)` waits forever on a dialog that is
demonstrably there; wait on `visible`, or on a delegate appearing.

**Close modal dialogs before clicking the page behind them.** The overlay
intercepts those clicks, including clicks on the footer's Apply action.

`tests/fake_pad.py` is shared with the backend tests and stays MIT and Qt-free.
The desktop-only device-info and live-trigger-bind commands therefore live in
`tests/qml_harness.py`'s `TestPad` subclass. The harness wraps that in `Pad`, a
`PadProbe` exposing packets received, saved count, bad checksums, reads,
switches, trigger binds and effects, and `failReads` for unanswered config
reads. `Fixture` adds per-family read counters, `seedGames`, `testImage`, and
`shell()`, the real window built through the same path as `main.py`; `Setup` is
the QtQuickTest entry point, and its `qmlEngineAvailable()` installs the import
paths and the i18n shim.

Desktop apply/save semantics and additional QML behavior are documented in
[../docs/findings-desktop-app.md](../docs/findings-desktop-app.md).

## Static checking

```bash
tools/generate-qmltypes
qmllint-qt6 -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml
```

`tools/generate-qmltypes` writes `Apex5/Apex5.qmltypes`,
`Apex5/apex5metatypes.json` and `Apex5/qmldir` into the repository root, which is
what `-I .` points at. That directory is gitignored, so a fresh checkout has no
type information until the script is run, and without it every model reference is
an unqualified access qmllint cannot check. Re-run it after adding or renaming
anything a view binds to. `collect()` discovers QObject subclasses exported by
`gui.models.__all__`, so a new QML model must be imported and listed in
`gui/models/__init__.py`; there is no second hand-maintained generator list.

`pyside6-project build --qml-module` is not the supported static-checking route:
`pyside6-metaobjectdump` parses Python source and cannot see `constant=True`.
Constant properties consequently reach qmllint as merely read-only and produce
false "binding might not update when the property changes" warnings.
`tools/generate-qmltypes` instead dumps the live `QMetaObject`, where the flag is
correct, and passes the result to Qt's own `qmltyperegistrar` unchanged.

The repository root's `pyproject.toml` describes `gui/` to `pyside6-project`
(`build`, `qmllint`, `run`) and pins `requires-python = ">=3.9"`. Its explicit
`files` list does not cover every current model, component, and page, so it is
not the source of truth for static checking. The two commands above scan the
current tree and are the supported route.
