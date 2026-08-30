# Desktop application behavior

Reference for write semantics, model registration, and empty-state behavior.

Related documentation: [GUI development](../gui/README.md),
[project status](../PROGRESS.md).

Implementation sources: [profile model](../gui/models/profile.py),
[device worker](../gui/worker.py), and
[mapping protocol](../flydigi/mapping.py).

## Apply and save

Profile changes have two persistence levels:

| Action | Commands | Result |
|---|---|---|
| **Apply** | 164/165; 173/174 for v3.2 macro-store changes; 162 when changed macros require a profile reload | Updates the controller's working state immediately. The change is lost when the pad sleeps or powers off. |
| **Apply and save** | Apply sequence followed by 166 | Commits the controller's current working configuration to flash. `mapping.save_config()` waits up to 2 seconds for the acknowledgement. |

Button gates:

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

Saving changes flash state. Command 166 is hardware-validated on Apex 5 to
persist a profile across a physical power cycle, including when the target is a
factory slot. Do not use factory slots for destructive write tests unless
overwriting them is intentional.

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

## Test guidance

Test commands, harness architecture, and QML lifecycle constraints are
maintained in [gui/README.md](../gui/README.md#tests).

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
