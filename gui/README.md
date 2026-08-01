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

It also keeps the backend dependency-free: `tools/flydigi-ds5` runs on any
machine with Python 3.9 and no Qt installed. PySide6 is imported by this
directory, by `tools/generate-qmltypes` and by the four GUI test files
(`tests/test_models.py`, `tests/test_shell.py`, `tests/test_qml.py`,
`tests/qml_harness.py`), and nowhere else. Code outside `gui/` that needs to
know whether PySide6 is present asks a subprocess (`python3 -c "import
PySide6"`) rather than importing it.

## Toolkit

PySide6 (LGPLv3), not PyQt6 (GPL-only). Avoid the Qt add-ons that ship
GPL-3.0-only under the open-source licence — Charts, Data Visualization,
Virtual Keyboard. A response curve is what would otherwise pull in Qt Charts;
`gui/qml/components/StickSide.qml` draws one in a QML `Canvas` instead, plotting
the nine-point bank the pad actually plays for a stick.

The interface is QML on Kirigami, and no file under `gui/` imports `QtWidgets`.
`QQuickStyle.setStyle("org.kde.desktop")` must run before the first
`QQuickWindow` exists, not merely before the window is shown, or Controls render
in the default style. `tests/test_shell.py` and `tests/test_qml.py` set the same
style, so a page that only lays out under the Basic style fails in the tests.

## Runtime

**PySide6 must come from wherever Kirigami comes from — a distribution package,
or `io.qt.PySide.BaseApp//6.11` on `org.kde.Platform//6.11` — never from pip.**
A PyPI wheel bundles its own Qt build, and Kirigami's plugin will not link
against it; the symbol versions and the failed workarounds are in
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
resolves without an import-path environment variable; no module under `gui/`
reads an environment variable. `tests/qml_harness.py` repeats the same list.

The application identifies itself as application and organisation
`flydigi-apex5`, display name "Flydigi Apex 5", desktop file `flydigi-apex5`,
window icon from the theme name `input-gaming`.

`flydigi.setup` refuses to write the application-menu entry from the host when
the host's `python3` cannot import PySide6, since the launcher would point at an
interpreter that cannot load Kirigami; install it from inside the distrobox, or
from the app's own Setup page. The entry hard-codes
`cd <repo> && exec python3 -m gui`, and inside a container it re-enters that
container by name — renaming or deleting the box turns the launcher into a dead
icon.

## Layout

    __main__.py       `python3 -m gui`, from the repository root
    main.py           QML engine setup: import paths, i18n shim, style, the window
    app.py            the application graph: models, worker thread, the wiring
    worker.py         device access, on its own thread
    i18n.py           the i18n*() shim the engine needs -- PySide6 has no KLocalizedContext
    models/           view-agnostic state -- no QtWidgets, no QtQuick
    qml/              Main.qml, pages/ (fifteen), components/ (seven)
    requirements.txt  what the runtime must provide -- documentation, not a pip file

QML constructs `App`, so opening the device is a separate `start()`, and
`beginPolling()` is a further seam so a test can put a fake pad behind the
worker before anything is asked of a real one.

Polling is one `Get info` timer at two intervals: 30 s while the pad is
answering (`INFO_INTERVAL_MS`, watching battery and charge) and 2 s while it is
not (`SEARCH_INTERVAL_MS`, looking for it, since a sleeping pad leaves the USB
bus and so is missing rather than silent). It is stopped for the duration of a
screen upload.

**The poll is also how the window fills.** There is no separate first read:
`beginPolling` asks how the pad is doing, and the pad going from missing to
answering is what reads the rest — profile, lighting, transport, settings. A pad
that was there at launch and a pad plugged in ten minutes later come down the
same path, so the second one cannot quietly stop working. The re-read keeps
unsaved edits (`_read_the_rest(keep_edits=True)`): the pad sleeps in minutes and
an editing session never touches it, so waking it must not cost a half-finished
remap. Pressing **Reload from pad** is the deliberate version and discards them.
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
drawer and one page per section. Pages are listed in its `sections` array as
`{name, icon, url, kinds}` and built once by `pageFor()` with
`Qt.createComponent(url, Component.PreferSynchronous)`, parented to the page
stack and kept, so a section remembers its scroll position. Handing `pageStack`
a URL or a `Component` instead creates the page with no visual parent, which the
engine reports as an object "not placed in the graphics scene".
`pageStack.replace()` on an empty stack has nothing to replace and drops the
page silently, so `openSection()` pushes at depth 0 and replaces thereafter.

`Kirigami.GlobalDrawer.actions` is a list of `Action` objects and cannot be
filled by a `Repeater`, so the drawer's actions are written out one per section,
in the same order, with nothing keeping them in step: adding a page means adding
both the `sections` entry and its `Kirigami.Action`, or every label after it
shifts by one and the last section becomes unreachable from the sidebar.
`test_the_drawer_offers_every_section` asserts the pairing.

**A section belongs to a kind of device.** `kinds` is `["pad"]`, `["dock"]`, or
null for one that belongs to the installation, and `sectionVisible()` hides the
ones that do not match `App.devices.currentKind` — a sidebar offering Buttons
and Macros while a dock is selected is offering to edit something that is not on
screen. Hidden rather than removed, so the list stays the same length and every
action's index and the page cache stay valid. `drawerActions` filters to the
visible ones, which is what the tests read and press.

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
| `dock.py` | `DockModel` | the selected charging dock: its four switches, its lighting, what is sitting in it, and the picture half — a source image framed on the 334x304 window the LEDs are read from, and the 162 colours that come out of it |
| `dsmode.py` | `DsModeModel` | the DualSense switch's state |
| `games.py` | `GameListModel`, `GameFilterModel` | one row per game with its route resolved; the search text and route filter |
| `lighting.py` | `LightingModel`, `ColourListModel` | one lighting config, edited in memory until written; the up-to-five colours an effect cycles through |
| `profile.py` | `ProfileModel`, `ProfileListModel`, `KeyMapModel`, `MacroModel`, `StickModel`, `StickSideModel`, `TriggerModel`, `TriggerSideModel`, `VibrationModel`, `VibrationSideModel` | the open profile and the edits held against it; the four slots and what was read from each; one row per key, with what it sends and its turbo; the stored macros; and per side, a stick's response curve, a trigger's stored effect and travel window, and a grip motor's window |
| `imaging.py` | — | not a model: the one Qt chore `dock.py` and `screen.py` share, which is getting RGB888 out of a QImage without its row padding |
| `screen.py` | `ScreenModel` | the picture queued for upload, and the idle display setting |
| `settings.py` | `SettingsModel` | the command-3 block |
| `setup.py` | `SetupModel`, `SetupChecksModel` | the Setup page; one row per requirement, in the order a person would fix them |

Rules for these:

  * `Property` with a notify signal for everything a view shows, or
    `constant=True` where it cannot change. camelCase names, as QML expects.
  * `roleNames()` returns **bytes** keys.
  * No `QtWidgets` and no `QtQuick` anywhere under `models/`;
    `tests/test_models.py` asserts it. `models/screen.py`, `models/dock.py` and
    `models/imaging.py` are the ones that import a Qt GUI module — `QImage`,
    `QImageReader`, `QPainter`, for image decoding — which the rule allows.
    That is also why the dock's LED preview is a QML `Repeater` fed a list of
    colour strings rather than a `QQuickPaintedItem`: the painting has to live
    in the view, and the model may not reach it.
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
`QSG_RENDER_LOOP=basic` with `setdefault` (`tests/test_qml.py:28-32`,
`tests/test_shell.py:24-26`), so a value already exported in the shell wins — an
exported `QT_QPA_PLATFORM` puts the run back on a real display. None of the three
is optional: an offscreen run has no GPU context to share, and the threaded
render loop waits for one that never comes. `import QtTest` in the QML cases
resolves against the distribution's `qt6-qtdeclarative`
(`/usr/lib64/qt6/qml/QtTest`), not against anything PySide6 ships.

`test_models.py` and `test_shell.py` run a tuple of functions written out in
`main()` (`tests/test_models.py:1636`, `tests/test_shell.py:428`): a test not
added to its tuple never runs, and the count still reads `N/N passed`. The QML
suite is handed the `tests/qml` directory instead and finds its own cases.

Every interactive element carries an `objectName`: QtQuickTest addresses items
with `findChild(page, "name")`, and there is no other handle on an item a
delegate created.

`tests/fake_pad.py` is shared with the backend tests and stays MIT and Qt-free,
which is why the two commands only the desktop app asks for — device info and
the live trigger bind — live in `tests/qml_harness.py`'s `TestPad` subclass
instead. The harness wraps that in `Pad`, a `PadProbe` exposing what QML has no
other way to see — packets received, saved count, bad checksums, the reads and
switches, the binds and the effect live on each trigger, and `failReads` to make
config reads go unanswered as a sleeping pad does. `Fixture` adds per-family read
counters, `seedGames`, `testImage` and `shell()`, the real window built the way
`main.py` builds it; `Setup` is the QtQuickTest entry point, and its
`qmlEngineAvailable()` installs the import paths and the i18n shim.

The QML cases, the traps they hit, and what apply and save mean to the pad are in
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
anything a view binds to. A new model class is not picked up automatically:
`collect()` enumerates the exported types by hand, and a type absent from that
list is invisible to qmllint however it is decorated.

`pyside6-project build --qml-module` is the framework's own route and is
deliberately not used: `pyside6-metaobjectdump` parses the Python source, so it
cannot see `constant=True`. Every constant property then reaches qmllint looking
merely read-only, and every use of one is reported as "binding might not update
when the property changes" — 99 warnings across `gui/qml`, all of them wrong.
`tools/generate-qmltypes` dumps the live `QMetaObject` instead, where the flag is
correct, and hands the result to Qt's own `qmltyperegistrar` unchanged.

The repository root's `pyproject.toml` describes `gui/` to `pyside6-project`
(`build`, `qmllint`, `run`) and pins `requires-python = ">=3.9"`. Its `files`
list omits `gui/i18n.py`, `gui/models/dsmode.py`, `gui/models/settings.py`,
`gui/qml/components/StickSide.qml` and the DeviceSettings, DualSense and Macros
pages; the two commands above are the supported route.
