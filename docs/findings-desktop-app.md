# Desktop application behavior

Current write semantics, model registration, and GUI test coverage. Verified
against the repository on 2026-08-23.

Related documentation: [GUI development](../gui/README.md),
[project status](../PROGRESS.md).

## Apply and save

Profile changes have two persistence levels:

| Action | Commands | Result |
|---|---|---|
| **Apply** | 164/165 and, when needed, 162 | Updates the controller's working state immediately. The change is lost when the pad sleeps or powers off. |
| **Apply and save** | Apply sequence followed by 166 | Commits the controller's current working configuration to flash. `mapping.save_config()` waits up to 2 seconds for the acknowledgement. |

The buttons deliberately use different gates:

- **Apply** is enabled while the model is dirty.
- **Apply and save** is enabled while the model is dirty or `saveNeeded` is
  set. Applying a change clears `dirty` but leaves `saveNeeded`, so saving must
  remain available.
- A profile can be saved only when the edited slot is also the slot currently
  running on the pad (`canSaveToFlash`).

Command 166 has no slot identifier and commits whichever profile the pad is
running. The controller can change profiles independently through its quick
switch setting. `ProfileModel.write(save=True)` therefore refuses a save when
the edited and active slots differ; this guard is in the model as well as the
view. Command 171 is implemented only for copying the running profile into its
matching Switch-bank slot (4-7); it is not a substitute for saving an XInput
slot with command 166, and writes to XInput destinations 0-3 remain unmeasured.

The worker holds one controller claim across the complete profile write,
optional save, and trigger-effect replay. This prevents another application
write from changing working memory between the update and command 166.

Lighting uses the same two labels but is not profile-slot-specific. Its save
button is gated by `dirty || saveNeeded`, without `canSaveToFlash`. Hardware
verification of lighting persistence remains open.

### Hardware safety

Saving changes flash state. Validation confirmed that command 166 persists a
profile across a physical power cycle, including when the target was a factory
slot. Do not use factory slots for destructive write tests unless overwriting
them is intentional.

The per-slot version reported by status is the cache-invalidation key. Before a
flash save, the profile writer generates a fresh value with
`mapping.next_data_version()` and passes it to command 166. Reusing the old
value would leave other clients' cached profiles looking current.

## QML model registration

QML binds to the `App` singleton in module `Apex5 1.0`. Models are registered
with `QmlElement` decorators rather than context properties so generated type
information is visible to `qmllint`.

Run static checking as documented in [gui/README.md](../gui/README.md). The
generated module is derived from QObject subclasses exported through
`gui.models.__all__`.

`pageStack.currentItem` is statically a `QQuickItem`; `Main.qml` casts it to
`Kirigami.Page` before accessing `title`.

## Test coverage

The Python model and shell suites are described in
[gui/README.md](../gui/README.md). The QML suite currently contains 13
`tests/qml/tst_*.qml` cases:

- 12 page-oriented cases: Buttons, Controller, Device Settings, DualSense,
  Games, Gyro, Lighting, Macros, Screen, Sticks, Triggers, and Vibration.
- `tst_wheel.qml`, which checks wheel behavior across controls and pages.

`DevicesPage`, `DockPage`, and `SetupPage` do not yet have dedicated QML cases.
Shell tests verify that the pages can be opened.

`tests/qml_harness.py` registers the models, installs the QML import paths and
i18n shim, and exposes `Pad`, `Fixture`, and `QmlDir`. `App.start(False)` keeps
the normal polling timer stopped while a fake pad is installed. QML cases then
request their own reads; the shell suite uses `beginPolling()` because polling
is the application's startup read path.

### Test constraints

- Wait on a model property or fixture counter, not a JavaScript closure around
  `button.enabled`; the latter has no notify signal for `tryVerify()`.
- A case that initiates an asynchronous profile read must wait for the matching
  fixture read counter before it ends.
- Always stop worker threads during cleanup. Destroying a running `QThread`
  aborts the test process instead of reporting a normal failure.
- Use `createTemporaryObject()` inside the QtQuickTest `TestCase`. A separate
  offscreen window does not become active, so synthetic clicks into it are
  dropped.
- Do not recurse through QML objects with `QObject.children()` from PySide;
  use the supported `findChild`/`findChildren` paths.
- Stub `dsmode.stop` in model tests. The real stop operation searches the
  process table because the relay may outlive the app, and can stop a relay
  outside the test. `tests/test_dsmode.py` is safe because it passes only a
  relay PID started by that test.

Dialogs require two additional rules:

- Search through `dialog.contentItem`; dialog content is not a QObject child of
  the dialog itself.
- Under the offscreen platform, wait on `visible` or a child delegate rather
  than `opened`. Close modal dialogs before clicking the page behind them.

## Empty and hidden states

- Place a `PlaceholderMessage` inside its `ListView` on a
  `Kirigami.ScrollablePage`. A sibling is left in the hidden subtree when
  Kirigami reparents the scrolling area.
- Buttons binds its list model only while a profile is loaded. `KeyMapModel`
  otherwise has 23 identity rows and would display editable placeholder data.
- `GameFilterModel.total` distinguishes an unavailable game list from a filter
  with no matches.
- An item's own `visible` property does not prove that it is drawable when an
  ancestor is hidden. QML tests use a parent-chain check where this distinction
  matters.
- Failed or sleeping-pad reads must produce an explicit status rather than an
  unexplained empty page.

Hardware-dependent open questions remain in [PROGRESS.md](../PROGRESS.md).
