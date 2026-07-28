# Plan: rewrite the GUI in QML/Kirigami

The current app is QtWidgets with default layouts — a 2012 config-dialog idiom. Breeze styles it,
so it does not look alien, but the visual grammar is a decade old: a 23-row table of combo boxes,
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

Three layers, replacing the current 24 widget tests:

  1. **Model tests, headless, no QML** — the bulk of it, and where the current assertions move.
  2. **A QML load test** — instantiate each page with `QQmlApplicationEngine`, assert no errors and
     a root object. Catches typos, missing imports, bad property names, which are the QML failure
     modes that hurt.
  3. Optionally `qmltestrunner`/QtQuickTest for interaction later. Not required to reach parity.

Behaviour coverage is preserved; what is genuinely lost is widget-wiring coverage, replaced by (2).

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
