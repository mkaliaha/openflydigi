# OpenFlydigi project status

This document records current implementation status, validation level, known
limitations, and planned work. It does not repeat the wire protocol or the
reverse-engineering record.

Detailed evidence lives in [PROTOCOL.md](PROTOCOL.md) and
[docs/](docs/). User setup and command examples are in
[README.md](README.md).

## Status labels

| Label | Meaning |
|---|---|
| Hardware-validated | Exercised on the named physical device |
| Game-validated | Exercised in a real game through the complete route |
| Simulated | Covered by mocks or protocol tests, without the target hardware |
| Experimental | Implemented from protocol evidence but not tested on the target hardware |
| Partial | Some required user-facing or end-to-end path is missing |
| Unsupported | Intentionally unavailable or refused |

## Hardware support

| Hardware | Status | Scope |
|---|---|---|
| Flydigi Apex 5 | Hardware-validated | Reference controller. Profiles, mappings, macros, sticks, gyro-to-stick, vibration, adaptive triggers, lighting, settings, display, game routes, and virtual-DualSense relays are implemented. |
| Flydigi Vader 5 Pro | Experimental and partial | Identity, capability gating, profiles, mappings, sticks, gyro, vibration, lighting, settings, v3.2 macro-store handling, and a derived factory profile are implemented. No Vader 5 hardware has been tested. |
| Flydigi Apex 6 / Apex 6 Pro | Experimental, emulator-tested | New transport, identity/capabilities, verified active-RAM snapshots, standalone waveforms and opt-in USB DualSense input, stereo audio and synthesized HID rumble. UUID-bound reconnect, profile-change restoration and bounded buffering are simulated. Grip output requires `hapticGrip`; the relay also requires Xbox-style evdev input. Configuration writes, adaptive triggers, motion, uhid and GUI integration remain unavailable. See [findings](docs/findings-apex6.md). |
| Flydigi CD2 dock | Hardware-validated with one noted gap | Device identity, four settings, generated lighting, and picture/GIF sampling are hardware-validated. Nickname read/write is implemented, but nickname writing is not hardware-verified. |
| Other Flydigi hardware | Unsupported | It may be discovered by the shared transport. Normal configuration commands refuse unsupported models; explicitly ungated developer bench tools are outside that guarantee. |

### Vader 5 boundaries

The Vader 5 must remain labeled experimental until these paths are verified:

- real device discovery and permissions;
- profile and settings reads/writes on hardware;
- v3.2 commands 172/173/174 and the separate macro store;
- the derived factory profile and per-slot restore;
- the physical evdev input-node name and product identifier;
- the virtual-DualSense relay, SDL ignore identifier, rumble, motion, and
  haptic-audio behavior;
- the trigger-motor configuration block.

Known capability differences are enforced:

- no ForceAdapt force triggers;
- no 160×80 display;
- a vibration motor in each trigger instead of force resistance;
- C and Z input buttons;
- protocol v3.2 macros in a separate store.

The Triggers, Screen, and Games pages are hidden when their required capability
is absent. The trigger-motor configuration backend exists, but no page exposes
it.

## Feature status

| Feature | Apex 5 | Vader 5 | CD2 | Notes |
|---|---|---|---|---|
| Device discovery and identity | Hardware-validated | Experimental | Hardware-validated | Legacy configuration supports `k5` and `f5`; `k6` discovery is separate from opt-in grip output. Selection is separate from model/capability permission. |
| UID, nickname, and registry selection | Hardware-validated | Simulated | Hardware-validated except nickname write | Standard selectors accept node, UID/prefix, MAC, or nickname. Dock nickname writing is implemented but not hardware-verified. Not every tool uses the registry selector. |
| Multiple devices in desktop/daemon | Simulated plus one real pad/dock | Simulated | Simulated plus one real dock | Mock buses cover multiple pads and docks. Full multi-physical-device testing remains outstanding. |
| Profile read/write/apply/save | Hardware-validated | Experimental | Not applicable | Reading a profile switches the running slot. Unsaved changes are volatile. CLI `apply` skips stored force-trigger engagement on models without adaptive triggers. |
| Profile backup/restore | Hardware-validated | Partial | Not applicable | Desktop packed backup includes a v3.2 macro store. The CLI raw-blob format does not. |
| Single-slot factory restore | Hardware-validated | Experimental | Not applicable | Writes the model-specific factory profile and saves it. It is not command 175. |
| All-slot reset | Hardware-validated | Unverified | Not applicable | Command 175 resets every profile and name, ignoring the requested slot. |
| Switch-bank copy | Hardware-validated | Unverified | Not applicable | Command 171 copies the active XInput profile into the matching Switch slot. |
| Button mapping and turbo | Hardware-validated | Experimental | Not applicable | Vader includes C and Z as sources. |
| Macros | Hardware-validated, v3.1 | Experimental, v3.2 | Not applicable | Recording, building, editing, binding, and clearing are implemented. |
| Stick configuration | Hardware-validated | Experimental | Not applicable | Preset curves and circular range are implemented. Custom-curve UI is pending. |
| Gyro-to-stick mapping | Hardware-validated | Experimental | Not applicable | Runs on the controller and works without a host process. |
| Grip vibration | Hardware-validated | Experimental | Not applicable | Vader trigger-motor interaction is unknown. |
| Adaptive trigger effects | Hardware- and game-validated | Unsupported | Not applicable | Six live effects, stored effects, and vibration binding are implemented for Apex 5. |
| Vader trigger motors | Not present | Partial | Not applicable | Stored block parsing/writing exists; editor and live-behavior verification do not. |
| Lighting | Hardware-validated | Experimental | Hardware-validated | Controller and dock lighting use uploaded/generated frames. |
| Apex display | Hardware-validated | Not present | Not applicable | Serial bootloader upload works when wired; about 25 seconds per frame. |
| Device settings | Hardware-validated except restart | Experimental | Hardware-validated | Restart command exists but has not been sent to hardware. |
| Per-game daemon | Game-validated for Apex routes | Unsupported for adaptive-trigger routes | Not applicable | Tier-1 vibration bind can fan out to compatible pads; other routes use the selected pad. |
| Virtual DualSense over uhid | Game-validated | Experimental, incomplete validation | Not applicable | No haptic-audio endpoint. |
| Virtual DualSense over USB/IP | Game-validated | Experimental, incomplete validation | Not applicable | Supports haptic audio through `vhci-hcd`. |
| Sleep/reconnect during DualSense mode | Game-validated | Unverified | Not applicable | On Apex 5, the virtual pad and game audio stream survive physical-pad sleep and reconnect. |
| Desktop application | Hardware-validated on Apex/CD2 | Experimental | Hardware-validated | Device-specific pages are capability-filtered. |
| System setup | Hardware-validated for Apex/CD2 | Partial | Hardware-validated | Current udev and SDL identifiers are Apex/CD2-specific; Vader setup is not validated. |

## Adaptive-trigger routes

The current fetched game list is external data, not a repository constant.
Route availability and counts may change when it is refreshed.

| Route | Mechanism | Validation |
|---|---|---|
| Vibration bind | Pad-side command 82, driven by game rumble | Game-validated in Death Stranding 2 |
| Forza telemetry | UDP Data Out on port 5300 into the rule engine | Game-validated in Forza |
| DSX listener | DSX-compatible UDP on port 7878 | Simulated by `tests/test_dsx.py`; used by third-party mods |
| Memory monitor | Reads a game process using Flydigi monitor configs | Game-validated in Dark Souls: Remastered |
| Virtual DualSense over uhid | DS5 reports and adaptive-effect translation | Game-validated in Deathloop |
| Virtual DualSense over USB/IP | DS5 USB device, effect translation, and haptic audio | Game-validated in Deathloop |
| Third-party game mods | Game-side mod sends DSX packets | Interoperates through the DSX listener; mods are not shipped or supported |

The two DualSense implementations are system-level relays, not per-game
preferences. The USB/IP path supersedes uhid where `vhci-hcd` is available
because it also exposes the audio endpoint used for PS5 haptics.

## Known limitations

| Area | Limitation | Current behavior or mitigation |
|---|---|---|
| Fresh clone | Game data is not tracked | Run `tools/fetch-configs` before the daemon, auto policy, Steam wrapper, Forza route, or a populated Games page. |
| Device selector | The registry selector is not universal | Multi-device runtime tools listed in README support it. Apex-specific `flydigi-haptics` and `flydigi-run` currently open the first controller-family node and only then apply an Apex capability gate. A Vader sorting first can therefore block a later Apex. `flydigi_cmd.py` accepts only a raw path; probes vary. |
| Vader 5 | No target hardware has been tested | Treat all Vader behavior as experimental. Do not infer support from mock results alone. |
| Vader DualSense | Input discovery and launch identifiers remain Apex-specific/unverified | Do not publish the Apex `37d7:2501` ignore option as a confirmed Vader value. |
| Vader macros | Commands and layout are transcribed but unverified | The app displays an experimental warning on v3.2 profiles. |
| Vader CLI backup | Raw profile backup omits the separate macro store | Use the desktop packed backup for complete v3.2 data. |
| Trigger motors | Stored configuration exists without a UI or live test | Build and validate a Vader-only editor before calling the feature supported. |
| DualSense enumeration | Games see both the physical pad and virtual DualSense | On the validated Apex path, add both SDL ignore variables from README. |
| DualSense startup | Games usually open the controller audio stream once | Start DualSense mode before starting the game. |
| Steam Input | It masks the physical pad as an Xbox controller | Disable it for games using either virtual-DualSense relay. |
| Physical-pad sleep | The Apex 5 leaves the USB bus entirely | Session relays and the daemon re-resolve nodes. Short commands may report no device until a button wakes it. |
| Unsaved configuration | Working-memory changes are discarded on sleep, profile change, or power loss | Use `--save` or **Apply and save** when persistence is intended. |
| Display upload | Wireless mode exposes no serial bootloader device | Normal uploads are refused unless the controller is wired. |
| Display speed | Upload is about 25 seconds per frame | Keep animations short and review the time estimate before starting. |
| Memory monitor | Container boundaries prevent the required host process read | Run the route on the host with `ptrace_scope = 0`. |
| Game configs | Monitor pointer chains are build-specific | Run `--probe` after game updates. |
| Flatpak | Host process and relay management are incomplete | No supported Flatpak package exists yet. |
| PySide project metadata | `pyproject.toml` omits current GUI models, pages, components, and `gui/i18n.py` | Do not treat `pyside6-project build` or PyPI packaging as supported until the manifest is synchronized and packaging metadata is defined. |
| Multiple virtual DualSense devices | Audio identity and hardware-address handling are single-device oriented | One virtual DualSense session is supported. |
| Third-party control | Buttons/Macros editing controls are disabled selectively, not as a whole-page invariant | Buttons Reset All and shared profile footer behavior still need alignment if full lockout is required. |

## Current test state

The intended test contract is a hardware-free backend suite plus three
Qt-dependent Python scripts.

Current inventory:

- 21 backend `tests/test_*.py` scripts;
- 3 Qt-dependent Python scripts: `test_models.py`, `test_shell.py`, and
  `test_qml.py`;
- 13 QML cases under `tests/qml/tst_*.qml`.

Run the [discovered backend set](README.md#tests) rather than maintaining a
hand-written filename list. All 21 backend scripts are hardware-free. The
documented loop continues after failures and returns nonzero if any script
fails.

Run Qt checks in an environment where distribution PySide6 and Kirigami are
installed:

~~~bash
python3 tests/test_models.py
python3 tests/test_shell.py
python3 tests/test_qml.py
tools/generate-qmltypes
qmllint-qt6 -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml
reuse lint
~~~

See [gui/README.md](gui/README.md) for distribution-specific Qt setup.

## Roadmap

| Priority | Work | Completion criterion |
|---|---|---|
| P0 | Complete CLI v3.2 backup/restore | `flydigi-mapping backup` uses packed profile + macro-store data and remains compatible with legacy raw files |
| P0 | Resolve selector gaps in Apex-specific tools | `flydigi-haptics` and `flydigi-run` find the first compatible Apex rather than failing when a Vader sorts first; probes either select explicitly or document their exact gate |
| P1 | Extend experimental Apex 6 support | Validate USB DualSense input/audio/rumble and RAM snapshots on hardware; recover motion/trigger translation, ordinary-game-audio synthesis, complete mapping codecs and transactional configuration writes before UI parity |
| P1 | Validate Vader 5 on hardware | Identity, permissions, profile/settings I/O, macros, factory restore, lighting, motion, rumble, and reconnect paths have recorded results |
| P1 | Generalize and validate Vader DualSense mode | Real evdev discovery, model-specific ignore ID, udev access, input, gyro, rumble, and haptic audio pass end to end |
| P1 | Add the Vader trigger-motor page | Editor matches the stored block and has hardware verification, including live-rumble interaction |
| P1 | Make third-party-control lockout internally consistent | Buttons/Macros actions and shared footer follow the documented policy and QML tests cover it |
| P1 | Synchronize PySide project metadata | The manifest covers every required Python/QML file and a clean `pyside6-project build` succeeds |
| P2 | Add custom stick-curve editing | UI exposes the two interior points and compiles the measured nine-point bank |
| P2 | Add stick diagnostics and grip-vibration test | Circularity, center offset, and vibration checks are available without bench probes |
| P2 | Cache profile titles safely | Cache is keyed by device UID, slot, and version tag without changing the active profile |
| P2 | Implement and validate cooperative command 28 lock | Write path is measured and failure/recovery behavior is documented |
| P2 | Validate controller restart command 29 | Hardware result is recorded before the desktop app exposes it |
| P2 | Tighten daemon process matching | Reduce confidence-zero cmdline false positives without regressing supported Proton launches |

## Safety invariants

These constraints should remain true through future changes:

- A configuration write is preceded by identity/capability validation unless
  the tool is explicitly documented as an ungated bench tool.
- Command 175 is always described as an all-profile reset.
- The normal screen-upload path checks for a wired controller before entering
  bootloader mode. The explicit `--i-know` escape hatch skips this check and is
  documented as potentially stranding a wireless pad until it is power-cycled.
- Firmware update for controller program chips is not implemented.
- A profile save commits the currently running slot; the UI refuses to imply
  that another slot can be saved in place.
- Stored trigger effects are engaged only after the profile write/apply
  sequence finishes.
- A lost physical controller does not detach the virtual DualSense or leave
  buttons held.
- Proprietary Flydigi code, assets, configs, and mod archives remain untracked.

## Architecture

| Path | Responsibility |
|---|---|
| `flydigi/` | Dependency-free transport, protocol, profiles, effects, relays, setup, registry, and mocks |
| `gui/` | PySide6/Kirigami desktop application |
| `tools/` | Runtime commands, setup utilities, probes, simulators, and generators |
| `tests/` | Backend, Qt model/shell, and QML tests |
| `udev/` | Device-access rules |
| `pipewire/` | Retained negative-result experiment; not installed |
| `docs/` | Long-form findings and design evidence |

The public repository does not contain `gamelist.json`, `configs/`, `mods/`,
`decompiled/`, `bundle/`, `asar/`, `bin/`, or research scratch data. The first
three are fetched on demand; the rest are local analysis artifacts.

## Detailed documentation

| Document | Subject |
|---|---|
| [PROTOCOL.md](PROTOCOL.md) | Wire protocol and verification status |
| [docs/findings-profile-blob.md](docs/findings-profile-blob.md) | Profile layout, mappings, sticks, gyro, triggers, and macros |
| [docs/device-settings.md](docs/device-settings.md) | Settings, battery, vibration bind, lighting data, and command inventory |
| [docs/findings-steam.md](docs/findings-steam.md) | Locking, Steam interaction, and third-party takeover |
| [docs/findings-games.md](docs/findings-games.md) | Game detection, routes, and game validation |
| [docs/findings-haptics.md](docs/findings-haptics.md) | Virtual DualSense and haptic audio |
| [docs/findings-screen.md](docs/findings-screen.md) | Display upload paths, safety gates, and settings |
| [docs/findings-other-devices.md](docs/findings-other-devices.md) | Device codes, other controllers, and dock findings |
| [docs/findings-desktop-app.md](docs/findings-desktop-app.md) | Desktop state and apply/save behavior |
| [gui/README.md](gui/README.md) | GUI development, QML tests, and static checking |
| [docs/third-party-mods.md](docs/third-party-mods.md) | Unsupported third-party-mod route |

Licensing and third-party notices are summarized in
[README.md](README.md#licensing), [LICENSE](LICENSE), and [NOTICE](NOTICE).
