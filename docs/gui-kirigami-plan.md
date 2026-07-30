# Plan: rewrite the GUI in QML/Kirigami

> **Done, 2026-07-28.** Kept for the reasoning; `gui/README.md` and `PROGRESS.md` describe what
> exists. Four things went differently from what is written below, and they are the interesting
> part:
>
>   * **Phase 0 failed, and that was the useful result.** The venv's PySide6 cannot load Kirigami:
>     the wheel's bundled Qt versions private symbols `Qt_6_PRIVATE_API` where Fedora's tags them
>     `Qt_6.11_PRIVATE_API`, and it is mutual — preloading the system Qt breaks PySide6 instead.
>     The fallback below assumed `rpm-ostree` layering and a reboot; a distrobox needs neither.
>   * **The widgets were deleted rather than kept.** Phase 1's "extract models, keep the widgets"
>     hedge existed in case the QML work stalled. It did not, and the project was a day old, so
>     there was no installed base to protect and no `--legacy-ui`.
>   * **Models are a QML module, not context properties.** Phase 2 proposed context properties.
>     They work, but qmllint cannot see them, so all 117 model references were unqualified
>     accesses it had to ignore. Registering the models with `QmlElement` and generating type
>     information got qmllint to zero and made it find a real bug.
>   * **The testing section below is right that QML can be driven, and wrong about how.** It is left
>     as written, like the rest of the plan. Python's `findChild` cannot see an item created by a
>     delegate, and Qt only delivers synthesised clicks to a window it showed itself — so layer 3 is
>     not `QtTest` driven from Python. Every interaction test is a QtQuickTest case in `tests/qml/`,
>     with only the model and load layers left in Python.

The old app was QtWidgets with default layouts — a 2012 config-dialog idiom. Breeze styled it,
so it did not look alien, but the visual grammar was a decade old: a 23-row table of combo boxes,
`QGroupBox` + `QFormLayout` panels, text-only buttons in box layouts, no header, no icons.

This plans the replacement. **Nothing in `flydigi/` changes**, and `gui/worker.py` should survive
untouched — it already imports only `QtCore` and has no widget dependency.

## What is available

Verified on this machine:

  * `kf6-kirigami` 6.28 and `kirigamiaddons`, under `/usr/lib64/qt6/qml/org/kde/`
  * PySide6-Essentials in `.venv` ships `QtQuick`, `QtQml`, `QtQuickControls2`
  * system Qt is 6.11.1, and the venv's bundled Qt is also 6.11.1

## Phase 0 — settle the runtime before writing any UI

The venv's PySide6 bundles its own Qt while Kirigami is built against the system's. The versions
match, so importing system QML modules should work, but this must be proven first because the
fallback changes the install story.

Spike: a QML file that does nothing but `import org.kde.kirigami as Kirigami` and show an
`ApplicationWindow`, loaded from the venv with
`QML2_IMPORT_PATH=/usr/lib64/qt6/qml`.

  * **works** → keep the venv, add the import path in `gui/__main__.py`, note it in
    `gui/requirements.txt`
  * **fails** → layer Fedora's `python3-pyside6` with `rpm-ostree` (a reboot on this ostree system)
    and drop the venv. `README.md`, `PROGRESS.md` and `gui/requirements.txt` all describe the venv
    install, so they change together.

Decide this before anything else. Do not start porting pages against an unproven runtime.

## Phase 1 — extract models, keep the widgets

**This is the phase that carries the value, and it is worth shipping even if the rest stalls.**

Today `profiles.py`, `triggers.py` and `lighting.py` each mix three things: device state, display
formatting, and widget poking. QML cannot use any of it. So first pull the state out into
view-agnostic objects that the existing widgets drive, then swap the view later.

New `gui/models.py` (or a `gui/models/` package):

| Model | Kind | Holds |
|---|---|---|
| `DeviceModel` | `QObject` | connected, battery, charging, connection type, active profile |
| `ProfileListModel` | `QAbstractListModel` | the four slots: title, loaded, dirty, isActive |
| `KeyMapModel` | `QAbstractListModel` | one row per `APEX5_KEYS` entry: key, target, turbo, turboMode, isRemapped |
| `VibrationModel` | `QObject` | master switch, per-side enable/min/max/strength |
| `TriggerModel` | `QObject` | per-side effect, start, strength, dead zone, motor |
| `LightingModel` | `QObject` | effect, brightness, cycleTime, clickFeedback |
| `ColourListModel` | `QAbstractListModel` | the up-to-five colours, with add/remove |
| `GameListModel` | `QAbstractListModel` | name, route, routeLabel, canApply; filter with `QSortFilterProxyModel` |

Rules for these, because they are what makes the port possible:

  * **Q_PROPERTY with notify signals** for everything a view shows. camelCase names — QML convention,
    and renaming later is churn.
  * `roleNames()` returns **bytes** keys.
  * **No `QtWidgets` import anywhere in `models.py`.** That is the check that the extraction is real.
  * Dirty tracking lives here, not in the view. It is currently `_is_dirty()` comparing blobs; keep
    that, expose it as a property.
  * Colours stay as RGB tuples in Python; convert at the QML boundary.

Port the 24 tests down onto these models as you go. Most survive almost unchanged — "editing marks
dirty", "one remap writes one packet", "reading restores the active profile" are all model-level
facts. What disappears is the widget poking (`combo.setCurrentIndex`), replaced by setting a model
property. **`tests/fake_pad.py` needs no changes at all.**

At the end of Phase 1 the app still looks identical and every test still passes. That is the point:
if the QML work goes badly, nothing is lost.

## Phase 2 — QML shell

`Kirigami.ApplicationWindow` with a global drawer, and one page per section: Controller, Buttons,
Vibration, Triggers, Lighting, Games. Register the models as context properties or via
`QmlElement`.

  * `QQuickStyle.setStyle("org.kde.desktop")` or the app will render in the default style and look
    wrong on KDE.
  * Keep the widget app runnable behind a flag (`--legacy-ui`) until Phase 4, so there is always a
    working app to compare against.
  * Device status (battery, connection, active profile) goes in the header, not a status bar.
  * Errors become `Kirigami.InlineMessage`, replacing `statusBar().showMessage`.

## Phase 3 — port pages, simplest first

**Lighting first** — one object, few controls, the most visible payoff, and it exercises the colour
list and the QML `ColorDialog`.

Then **Profiles**, which is the real design work. The 23-row table should not be recreated as a
table. Options worth trying: `FormCard` sections from kirigami-addons grouped by button cluster
(face, d-pad, shoulders, sticks, paddles), or a `ListView` with delegates. A **custom-drawn
controller schematic** with clickable buttons would do more for perceived quality than anything
else here — and it must be our own drawing, since Flydigi's agreement claims their interface design
and artwork.

Then **Games**, which is mostly a `ListView` over `GameListModel` plus a search field.

Dialogs change: `QColorDialog` → `QtQuick.Dialogs.ColorDialog`, and the backup/restore
`QFileDialog` → `QtQuick.Dialogs.FileDialog`.

## Phase 4 — remove the widgets

Delete the widget pages and the `--legacy-ui` flag, drop `QtWidgets` from the imports (`QApplication`
may still be needed if any widget dialog survives — prefer not), and update the README screenshots
and `PROGRESS.md`.

## Testing after the rewrite

Nothing is lost. An earlier draft of this plan claimed QML could not be driven the way widgets can,
which is false — all of the following is available on this machine:

  * **`PySide6.QtQuickTest`** — the official framework. `TestCase` in QML with `mouseClick`,
    `keyClick`, `tryCompare` and `SignalSpy`, launched from Python. Tests are written in QML/JS.
  * **`PySide6.QtTest`** — `QTest.mouseClick`, `QTest.keyClicks`, `QSignalSpy`. Load the page, find
    an item with `rootObject().findChild(QObject, "applyButton")`, click it, assert. Keeps the
    tests in Python beside the existing ones, which is the cheaper continuation.
  * the system ships the `QtTest` QML plugin, so `import QtTest` resolves in QML
  * `dogtail` over AT-SPI is the nearest true Selenium analogue, since QML exposes accessibility;
    heavier and more brittle, worth knowing exists rather than reaching for

Four layers:

  1. **Model tests, headless, no QML** — the bulk, and where most current assertions move. Fast,
     no display, no rendering.
  2. **A QML load test** — instantiate each page with `QQmlApplicationEngine`, assert no errors and
     a root object. Catches typos, missing imports and bad property names cheaply.
  3. **Interaction tests** via `QtTest` from Python, replacing the widget-poking the current suite
     does. This is the layer the earlier draft wrongly wrote off.
  4. `qmllint` in CI — static type checking for QML, which has no widget equivalent, so the rewrite
     actually gains a check here.

**Design requirement that follows:** give every interactive element an `objectName`. Python can only
find items by that or by exported properties, and retrofitting names across a finished UI is
tedious. Do it while writing each component.

Headless gotcha: rendering QML under `QT_QPA_PLATFORM=offscreen` may also need
`QT_QUICK_BACKEND=software` and `QSG_RENDER_LOOP=basic`. Settle that during Phase 0's spike, since
every later test run depends on it.

## What must not change

  * `flydigi/` — the backend stays MIT, dependency-free, and unaware of any of this
  * `gui/worker.py` — already view-agnostic; if the rewrite wants to change it, that is a signal the
    models are leaking view concerns
  * `tests/fake_pad.py`
  * the licensing boundary: everything new lives in `gui/` and is GPL-3.0-or-later; `gui/` may
    import `flydigi/`, never the reverse

## Order of work, condensed

```
0  spike Kirigami import from the venv        -> decides venv vs layered PySide6
1  extract models, port tests, widgets stay   -> shippable on its own
2  QML shell behind --legacy-ui
3  port Lighting, then Profiles, then Games
4  delete widgets, add the controller schematic
```

**Step 4's schematic was dropped.** The Buttons page groups the 23 keys by where they sit on the
shell instead: a drawing accurate enough to be useful is hard to keep clearly distinct from Flydigi's
own artwork, which their service agreement claims.
