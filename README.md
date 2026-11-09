# OpenFlydigi

OpenFlydigi provides native Linux support for the Flydigi Apex 5 controller and
CD2 charging dock. It replaces the controller-facing parts of Flydigi's
Windows-only Space Station application: profiles, button remapping, macros,
sticks, gyro-to-stick mapping, vibration, ForceAdapt adaptive triggers,
lighting, device settings, the 160×80 display, per-game trigger routes, and the
dock's settings and lighting.

The backend under `flydigi/` uses only the Python standard library. The desktop
application adds PySide6, Qt, Kirigami, and Kirigami Addons.

## Supported hardware

| Hardware | Support level | Available features | Important limits |
|---|---|---|---|
| Flydigi Apex 5 | Hardware-validated | All controller features, adaptive triggers, display, game routes, and both virtual-DualSense relays | Display uploads require a wired connection. Steam Input must be disabled for virtual-DualSense games. |
| Flydigi Vader 5 Pro | Experimental, partially implemented | Identity, profiles, buttons, sticks, gyro, vibration, lighting, settings, the v3.2 macro-store implementation, and a derived factory profile | No Vader 5 has been tested. It has trigger motors rather than force triggers and has no display. The trigger-motor editor is not built. The v3.2 macro store is unverified on hardware. The DualSense relay, evdev discovery, permissions, and SDL ignore identifier are not validated end to end. |
| Flydigi Apex 6 / Apex 6 Pro | Experimental; Pro partly verified over receiver and direct USB | SKU/UUID/capabilities, active-RAM snapshots, CLI global settings, button remapping, press timings, lighting, profile selection/saving, stick and trigger response curves, macros, gyro configuration, native vibration and trigger scenes, restorable profile packages and local library; experimental grip waveforms and USB DualSense relay | Tested on Pro firmware 9.0.6.8; selected stick/trigger response effects are physically verified through evdev. The GUI supports settings, profile selection/save, button/Shift mappings, stick/trigger response and zones, macro authoring, native vibration/scenes, gyro and lighting with fake-controller coverage; backup/library and screen GUI workflows, diagnostic-snapshot restoration, adaptive-trigger translation and relay motion remain unavailable. Grip cleanup follows native best-effort mode replay; exact live-mode restoration is unverified, although ordinary rumble worked afterward. Some setting effects and the complete DualSense relay remain unverified. |
| Flydigi CD2 charging dock | Hardware-validated with one noted gap | Identity, four settings, eight generated lighting effects, and picture/GIF sampling onto 162 LEDs; nickname read/write is implemented | Nickname writing is not hardware-verified. The dock is a separate USB device and uses its own selector. |
| Other Flydigi controllers and accessories | Not driven | Devices may be discovered and identified when they use the current transport | Normal configuration commands, including nickname writes, refuse unsupported models. Developer bench tools explicitly documented as ungated can send raw commands. |

The Apex 5 is the reference hardware. Treat Vader 5 support as an invitation to
test and report results, not as a hardware-validated compatibility claim.

## Requirements

### Base requirements

- Linux with hidraw support.
- Python 3.9 or newer.
- Read and write access to the controller or dock's vendor hidraw node.
- The udev rules in `udev/72-flydigi-apex5.rules`. They cover:
  - the Apex 5 vendor interface (`37d7:2501`);
  - the experimental Apex 6 vendor interface (`37d7:2502`);
  - the CD2 dock (`37d7:6001`);
  - the display bootloader serial device (`ffaa:5555`);
  - `/dev/uhid`;
  - the virtual DualSense input sub-devices.

Install the rules with:

~~~bash
tools/apex5-setup install-rules
~~~

The command escalates through `pkexec` only for the system rules. The rule file
must sort before systemd's `73-seat-late.rules` so that `TAG+="uaccess"` is
acted on. The setup command also removes the obsolete
`99-flydigi-apex5.rules` and reloads udev.

### Feature-specific requirements

| Feature | Additional requirements |
|---|---|
| Desktop application | Distribution PySide6, Kirigami, Kirigami Addons, and the desktop Qt Quick Controls style |
| Display image/GIF conversion and preview | Pillow |
| Memory-monitor game route | `kernel.yama.ptrace_scope = 0` and execution on the host, not inside a container |
| Virtual DualSense over uhid | Writable `/dev/uhid` |
| Virtual DualSense with USB audio/haptics | The `vhci-hcd` kernel module and one authentication to attach the virtual USB device |
| Standalone haptic-audio bridge | `parec` and `pactl` from pulseaudio-utils |

Steam Input must be disabled for a game using either virtual-DualSense route.
It otherwise masks the physical controller as an Xbox controller and breaks
DualSense semantics.

A packaged Flatpak is not currently supported. Host process discovery, the
memory-monitor route, and relay-process management need a host-side service
before that packaging target is viable.

## Setup

Run commands from the repository root.

1. Inspect the current machine:

   ~~~bash
   tools/apex5-setup
   ~~~

2. Install or refresh the udev rules:

   ~~~bash
   tools/apex5-setup install-rules
   ~~~

3. Optionally install the user service and desktop-menu entry:

   ~~~bash
   tools/apex5-setup install-unit
   tools/apex5-setup enable
   tools/apex5-setup start
   tools/apex5-setup install-desktop
   ~~~

The service unit and menu entry contain the absolute path of this checkout.
Reinstall both after moving the repository. This repository is not currently a
pip-installable application and does not provide PyPI packaging.

## Fetch game data

Game lists, rule files, monitor configs, and mod archives belong to Flydigi and
are not committed. A fresh clone must fetch the data before using the daemon,
`flydigi-auto`, `flydigi-run`, the Forza route, or the populated Games page.

~~~bash
# gamelist.json and configs/forza.json
tools/fetch-configs

# also extract the memory-monitor configs
tools/fetch-configs --monitor-configs

# optionally download every upstream mod archive
tools/fetch-configs --all-mods
~~~

The desktop Games page can fetch the current game list with **Update list**.
Counts in the upstream list are not project constants and may change whenever
the data is refreshed.

## Select a device

List the bus, then inspect one device in full:

~~~bash
tools/flydigi-devices list
tools/flydigi-devices show --device uid:1420
tools/flydigi-devices show --device Couch
~~~

`list` prints a stable key, node, display label, firmware, and battery state.
`show` prints the full model, UID, reported address, nickname, and selector.

The standard registry selector accepts a hidraw path, UID or UID prefix, MAC
address, or exact nickname. It is supported by the day-to-day multi-device
commands:

- `flydigi-charger`
- `flydigi-ds5` and `flydigi-ds5-usbip`
- `flydigi-dsx`, `flydigi-forza`, and `flydigi-monitor`
- `flydigi-mapping`, `flydigi-screen`, and `flydigi-settings`
- `flydigid`

Some Apex-specific tools do not yet select by compatible model:

- `flydigi-haptics` and `flydigi-run` open the first new-protocol controller
  node and apply their Apex capability gate only afterwards. If a Vader sorts
  first, it is refused and a later Apex is not tried. They do not support Vader.
- `flydigi_cmd.py --device` accepts a raw hidraw path only.
- `hid_probe.py` accepts optional positional hidraw paths.
- Bench probes vary and some are ungated; use each tool's `--help` before
  attaching multiple pads.

The desktop application has separate remembered selections for a pad and a
dock. The daemon reads the selected primary pad for single-pad routes; the
pad-side vibration-bind route is applied to every compatible pad.

To exercise multi-device paths without hardware:

~~~bash
FLYDIGI_MOCK_BUS='pad=Desk,pad:f5=Couch,dock:1=Shelf' tools/flydigi-devices list
~~~

The same environment variable works with the daemon and desktop application.
No mock devices appear unless it is set.

## Common workflows

### Inspect the controller

~~~bash
python3 tools/hid_probe.py
tools/flydigi-devices show
tools/flydigi-settings show
tools/flydigi-mapping list
~~~

Reading a profile switches the controller to that profile and audibly re-seats
the Apex 5 trigger motors. A device information read is otherwise non-writing.

### Back up and edit an Apex 5 profile

Back up a profile before writing it:

~~~bash
tools/flydigi-mapping backup 0 profile0.bin
tools/flydigi-mapping set 0 m1 a --save
tools/flydigi-mapping macro-record 0 m1 --seconds 8 --save
tools/flydigi-mapping gyro 0 right --key m1 --sensitivity 60 --save
~~~

`--save` commits the change to flash. Without it, the change is working memory
and is lost when the pad sleeps, changes profile, or powers off.

The current CLI backup format is the raw 840-byte profile blob. On a Vader 5,
macros live in a separate v3.2 macro store, so CLI backup/restore does **not**
back up or restore those macros. Use the desktop application's packed profile
backup for the complete v3.2 profile until the CLI adopts the same format.

`tools/flydigi-mapping apply SLOT` switches profiles on both supported pad
models. On a Vader 5 it reports that stored force-trigger engagement was
skipped because the hardware has no adaptive triggers.

Safety-sensitive reset commands are deliberately separate:

~~~bash
# restore one slot by writing this model's factory profile
tools/flydigi-mapping reset 0

# command 175: reset every profile and every profile name
tools/flydigi-mapping reset-all
~~~

`reset-all` is destructive, resets all four XInput profiles, removes their
names, and cannot be undone. Both commands ask for confirmation unless their
explicit bypass option is supplied. Lighting is not part of a profile restore
and is left unchanged.

### Device settings

~~~bash
tools/flydigi-settings sleep 30
tools/flydigi-settings sleep never
tools/flydigi-settings show
~~~

Every settings write except `restart` is followed by a full read-back. The
`report-rate` and `xbox-home` commands require `--i-know` because their behavior
is unmeasured or ambiguous on the reference hardware.

### Per-game routes

After fetching game data:

~~~bash
# daemon and per-game policy
tools/flydigid
tools/flydigi-auto on "Death Stranding"

# Steam launch wrapper for vibration-preset games
/path/to/flydigi-run "DEATH STRANDING 2" -- %command%

# Forza Data Out: configure 127.0.0.1:5300 in the game
tools/flydigi-forza

# a DualSenseX-compatible mod
tools/flydigi-dsx

# game-memory route; probe offsets before enabling writes
tools/flydigi-monitor --probe configs/monitor/<game>.json
tools/flydigi-monitor configs/monitor/<game>.json
~~~

The Forza parser has built-in layouts for 311-byte packets at offset 0 and
324-byte packets at offset 12. Use `--accept LEN:OFFSET` only after verifying a
newer packet layout.

Memory-monitor pointer chains are game-build-specific. Probe after a game
update before allowing the route to write effects.

### Virtual DualSense

The desktop application's DualSense switch starts the USB relay. The equivalent
manual commands are:

~~~bash
# USB/IP + vhci-hcd, including haptic audio
sudo tools/flydigi-ds5-usbip --haptics --motors

# uhid input/effects only, without haptic audio
tools/flydigi-ds5
~~~

Start the relay before the game. For the validated Apex 5 path, add both SDL
ignore-variable spellings to the game's launch options:

~~~bash
SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501 SDL_JOYSTICK_IGNORE_DEVICES=0x37d7/0x2501 %command%
~~~

The physical Apex 5 may sleep and leave the USB bus while the virtual DualSense
stays attached. Reconnection, input recovery, trigger-state recovery, and
haptic-audio recovery have been confirmed in a real game.

The launch identifier and complete relay path are not validated for Vader 5.
Do not treat the Apex-specific launch option as a confirmed Vader instruction.

### Display

The Apex 5 display upload must be wired. A wireless upload can switch the
display chip into bootloader mode without exposing the serial device needed to
finish the transfer.

~~~bash
tools/flydigi-screen status
tools/flydigi-screen show photo.jpg
tools/flydigi-screen animate spin.gif
tools/flydigi-screen off
~~~

Encoding and preview require Pillow. Uploads take about 25 seconds per frame,
and the controller reboots after an upload. Keep animations short.

### CD2 charging dock

~~~bash
tools/flydigi-charger show
tools/flydigi-charger light pulse
tools/flydigi-charger light breath --colour ff0055 --brightness 80
tools/flydigi-charger lighting-sync on
tools/flydigi-charger list
~~~

Dock lighting effects are generated as frame data and uploaded; they are not a
single firmware mode byte. Uploads therefore take several seconds.

## Command reference

| Command | Purpose |
|---|---|
| `tools/apex5-setup` | Check and manage udev rules, user service, autostart, and desktop entry |
| `tools/fetch-configs` | Fetch the current upstream game list and optional configs/mods |
| `tools/flydigi-devices` | List, inspect, and nickname attached pads and docks |
| `tools/flydigi-mapping` | Profiles, backup/restore, remapping, macros, gyro mapping, single-slot reset, all-slot reset, and Switch-bank copy |
| `tools/flydigi-settings` | Read and write device settings |
| `tools/flydigi-screen` | Inspect, encode, preview, and upload Apex 5 display content |
| `tools/flydigi-charger` | Read and configure the CD2 dock |
| `tools/flydigid` / `tools/flydigi-auto` | Per-game detection, route policy, and automatic application |
| `tools/flydigi-run` | Apex-specific Steam launch wrapper for vibration presets |
| `tools/flydigi-forza` | Forza Data Out telemetry route |
| `tools/flydigi-dsx` | DSX UDP listener |
| `tools/flydigi-monitor` | Game-memory route |
| `tools/flydigi-ds5` | Virtual DualSense over uhid |
| `tools/flydigi-ds5-usbip` | Virtual USB DualSense with optional haptic audio |
| `tools/flydigi-haptics` | Apex-specific bridge from a real DualSense audio sink to grip motors |
| `tools/flydigi-apex6` | Experimental Apex 6 settings, profile editing, backup/restore, local library, snapshots and grip-waveform playback |
| `tools/flydigi_cmd.py` | Ungated manual vendor-command bench tool; raw node selection only |

Developer generators, simulators, and hardware probes also live under `tools/`.
Their help text is the authoritative command reference; they are not part of
the normal setup path.

## Desktop application

Install PySide6 and Kirigami from the same distribution. Do not combine a PyPI
PySide6 wheel, which bundles its own Qt, with distribution Kirigami libraries.

For Fedora/KDE:

~~~bash
sudo dnf install python3-pyside6 kf6-kirigami kf6-kirigami-addons \
                 kf6-qqc2-desktop-style
python3 -m gui
~~~

The application provides device selection, profiles, remapping, macros, sticks,
gyro, vibration, triggers, lighting, display, game routes, DualSense mode, dock
configuration, and system setup. Pages that require adaptive triggers or a
display are hidden on models without that capability.

Vader-specific pages remain experimental. In particular, the trigger-motor
editor is not implemented and DualSense mode is not hardware-validated.

Development setup, QML architecture, type generation, and static checks are in
[gui/README.md](gui/README.md). The current `pyproject.toml` is PySide tooling
metadata, not a complete installable-package definition. Its file list is also
incomplete and must be synchronized with the current models, pages, components,
and `gui/i18n.py` before relying on `pyside6-project build` or its static checks.

## Experimental Apex 6 / Apex 6 Pro

The desktop app now reuses its **Controller**, **Device**, **Buttons**, **Sticks**,
**Triggers**, **Macros**, **Vibration** and **Lighting** pages
for Apex 6. It reads the active profile, selects any of the four slots, saves
the selected profile, edits supported global settings, and configures all six
native lighting effects with up to eight colours. Settings apply immediately;
lighting retains the existing Apply / Apply and save workflow. Buttons supports
the base layer and three Shift layers, up to five actions per button, all seven
native press behaviors, typed outputs and profile-wide press timings. Hold/toggle
Shift targets use existing layers; macro targets use definitions already present
in the selected layer. Unknown actions are preserved until the button is explicitly
reset. Mapping edits use Apply / Save profile. **Applied Apex 6
changes may persist even without an explicit save.**

Sticks and Triggers edit either side's exact 0–255 input/output endpoints and
zero, one or two interior points, with a preview of the stored curve. Stick
presets, circular range and axis inversion are supported. Response applies across
all layers; zone actions are edited separately in the base or three Shift layers.
Each trigger supports four travel zones; each stick supports eight direction/radius
zones and three direction modes. Zone outputs share the Buttons editor's typed
fields and existing layer/macro references. Unsupported response records or zones
remain preserved; clearing zones explicitly removes only the selected side/layer.
The response and zone controls are separate from trigger scenes on the same page.

Vibration controls paired grip enable/strength and each trigger's independent
rumble binding and strength, using five native levels. Grip strength selection
also enables the grips. Enabling trigger feedback replaces that trigger's scene;
changing strength alone keeps its mode. The Triggers page offers all 12 native
scenes, three travel levels and frequency levels for the four repeating scenes.
Selecting a scene clears its trigger-feedback strength curve. Returning from a
scene to Normal resets the selected trigger's response and Base-layer zones;
Shift-layer zones remain intact. A zero feedback curve is shown explicitly and
can be restored to level 1 without changing mode. Unknown non-restorable motor
settings remain read-only. These edits share **Apply / Save profile** with
mapping edits, including full prior-state checks, read-back and rollback.

Macros supports manual creation, copying and deletion by ID in each layer, all
four native playback modes, repeat gaps, names on compatible profiles, and up to
32 press/release steps per definition. Steps can send gamepad, keyboard, mouse
or consumer buttons, trigger positions and stick movement. Edit delays and
outputs, add/remove or reorder steps, then choose **Update macro** to validate
the complete sequence. **Apply / Save profile** writes the updated profile.
Unfinished drafts survive page navigation and reconnect and block profile writes
until updated or discarded. Bind definitions on Buttons, Sticks or Triggers in
the same layer. Deleting a definition removes only its matching bindings in that
layer. Unsupported stored bodies remain readable and unchanged when renamed or
given a new repeat gap. Native recording is not part of this editor.

The native GUI route checks the controller UUID, active slot and previously
read profile bytes before writing. Reconnects retain unsent mapping, feedback
and lighting edits; Reload
reads the controller's current active slot. Profile names, factory reset,
Switch copying, gyro, file/library workflows, screen uploads
and DualSense remain outside this GUI slice; their existing CLI support is
unchanged. The new GUI flow is fake-controller and QML-tested, with no new
hardware GUI acceptance claim.

The Apex 6 implementation is **experimental**, with receiver and direct-USB
Apex 6 Pro checks on firmware **9.0.6.8**. All supported global setting writes
read back and restore correctly. Lighting modes and controls, physical button
remapping, quick profile switching, stick precision and XInput Home have been
confirmed. Report-rate setting writes and read-back are verified; the setting's
physical effect is undetermined. Profile timing bytes and save acknowledgment are verified; activator
timing behavior and other untested effects are distinguished in the
[hardware findings](docs/findings-apex6.md#apex-6-pro-hardware-findings-2026-11-07).
The implementation uses the
new GPA6 transport and the reported `hapticGrip` flag; the SKU name alone never
enables linear grips. Apex 6, Apex 6 Pro and Pro Phantom Blade Zero are named
separately. Unknown HID layouts and motor-map layouts are refused.

Try it without hardware:

~~~bash
FLYDIGI_MOCK_BUS=pad:k6 tools/flydigi-apex6 --device mock:pad0 info
FLYDIGI_MOCK_BUS=pad:k6 tools/flydigi-apex6 --device mock:pad0 snapshot apex6-mock.json
FLYDIGI_MOCK_BUS=pad:k6 tools/flydigi-apex6 --device mock:pad0 --experimental tone --duration 2
~~~

For a physical pad, list devices first, then select its UUID:

~~~bash
tools/flydigi-devices list
tools/flydigi-apex6 --device uid:YOUR_UUID info
tools/flydigi-apex6 --device uid:YOUR_UUID snapshot apex6-active.json
tools/flydigi-apex6 --device uid:YOUR_UUID settings
tools/flydigi-apex6 --device uid:YOUR_UUID profile
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental set-setting sleep_time 15
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental map m1 b
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental timing --long-ms 600
tools/flydigi-apex6 --device uid:YOUR_UUID bind m1
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental bind m1 --action timing_single a --action timing_long b --action timing_double x
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental layer-add 1
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental bind m1 --action simple layer-hold:1
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental bind a --layer 1 --action simple x
tools/flydigi-apex6 --device uid:YOUR_UUID trigger-zones left
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental trigger-zones left --zone 64 159 b --zone 160 255 x
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental stick-zones right --mode 8way --zone up 128 255 a --zone right 128 255 b
tools/flydigi-apex6 --device uid:YOUR_UUID stick left
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental stick left --center 10 --edge 5 --circular on --invert-y off
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental trigger right --zero 30 --end 200
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental trigger right --curve-start 0 0 --curve-end 255 255 --point 50 230
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental light --mode solid --color 0030FF --brightness 25
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental save
~~~

Profile edits apply immediately and **can persist without an explicit save**:
on the tested firmware an A4 mapping edit survived restarting both the controller
and receiver without A7. `save` or an edit's `--save` flag also sends the explicit
save command for the current profile. `slot 1` through `slot 4`
selects a resident profile: edits made without saving are retained both when
reselecting it and when switching away and back. Profile selection is not an
undo operation. Global settings apply directly. `map m1 default` removes the M1 override;
`--layer` selects an existing layer (0–3). Remapping replaces all activators for
that one button while preserving other mappings, macros, zones, curves and
metadata. The desktop Buttons page also edits these mappings.

`macros` lists definitions in the selected `--layer` (default 0). `macro ID`
reads one definition; editing flags create or update it. IDs are 0–255, with
up to ten definitions per layer and 32 steps per definition, matching the web
editor. Macros and their bindings are separate; `--bind BUTTON` replaces that
button's activators with this macro in the same verified write.

~~~bash
tools/flydigi-apex6 --device uid:YOUR_UUID macros
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental macro 5 --name 'A then B' --step 0 press a --step 80 release a --step 100 press b --step 80 release b --bind m4
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental macro 5 --mode press_loop_wait --interval 250
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental macro 6 --copy-from 5 --name 'Copy'
tools/flydigi-apex6 --device uid:YOUR_UUID --experimental macro 5 --clear
~~~

Each `--step` specifies the delay **before** the step in milliseconds, then
`press`/`release` and a button name or native target JSON. The shared recorder
format is also accepted through `--steps-file`: a JSON list of
`{"delay":0,"event":1,"key":"a"}` entries, where event 1 means press and 0 means
release. Use `target` instead of `key` for keyboard, mouse, consumer, trigger
or stick targets. `select`/`thl`/`thr` retain their existing recorder meanings;
`lt`/`rt` become native trigger targets. Home has no supported native target ID
and is refused. Total duration must fit 65,535 ms; delays are not quantized.

Playback modes are `press_once`, `press_loop_immediate`, `press_loop_wait` and
`press_hold`. These native modes are separate from the legacy playback enums.
The repeat interval accepts 0–10,000 ms. Normal sequences require paired
presses/releases (stick movements end at center); hold mode accepts press
steps and excludes stick movement. Changing modes does not silently rewrite
the steps. `--empty` creates an empty definition or clears its steps.
`--clear` removes the definition and matching button/trigger/stick-zone
references in that layer, preserving unrelated actions. Names require XMUL
v1.9; all profile/macro labels together must fit its 255-byte metadata value.

The macro editor has independent vendor fixtures, fake-pad failure tests,
eight hardware storage/restoration checks and six guided playback checks on Pro
9.0.6.8. Selected checks verified all four playback modes, Shift-layer trigger
and stick output, and deletion preserving an unrelated button action. Each
check restored the original settings. Other typed targets, GUI wiring and an
Apex 6 recording command remain separate work. See the
[macro interface and validation](docs/findings-apex6.md#macro-editor).

`bind BUTTON` reads its explicit activators; add repeated `--action TYPE TARGET`
to replace the complete list (up to five). Types are `simple`, `timing_single`,
`timing_long`, `timing_double`, `pulse_press`, `pulse_release` and `toggle`.
Use the three `timing_*` actions for single/long/double-press distinctions;
their thresholds come from the profile's `timing` settings. Multiple actions
of the same type are allowed. `--clear` removes the override; a `none` target
instead writes an explicit no-button action. Reads need no experimental opt-in.

Targets accept button names, `layer-hold:N`, `layer-toggle:N`, or a quoted JSON
object using the [native action fields](docs/findings-apex6.md#richer-button-mappings).
For example, `--action simple '{"type":"btn_keyboard","key_id":4,"modifier":2}'`
encodes a HID keyboard target; firmware/connection-mode support for host keyboard
and mouse output is not established by encoding it. `layer-add 1/2/3` creates an
empty Shift layer without changing existing layers; layer references must already
exist. Macro targets must refer to an existing macro in the selected layer.
The desktop Buttons page uses the same Apex 6 backend for these controls.

`trigger-zones left/right` and `stick-zones left/right` read action zones in
`--layer 0/1/2/3` (default 0). Repeated `--zone START STOP TARGET` replaces all
zones for a trigger (up to four). A stick uses `--zone DIR START STOP TARGET`
(up to eight total). Ranges use native 0–255 travel/radius units; direction
names are `up`, `down`, `left`, `right`, `up_left`, `up_right`, `down_left` and
`down_right`. Targets use the same button names, layer shortcuts and JSON as
`bind`. Zone actions do not have button-style press/hold/toggle activators.

Stick `--mode 4way/8way/4way_overlap` selects the angular partition, retaining
the existing mode if omitted and using `4way` for a new section. A mode-only edit
requires existing stick zones. `--clear` removes the selected side/layer's zone
section. Zones may overlap and repeat directions; their supplied order is kept.
Edits preserve the response curves, other sections and inactive stick records.
`--save` is optional; all writes still require `--experimental`. See the
[zone findings](docs/findings-apex6.md#trigger-and-stick-action-zone-editor) for
validation scope and the reusable backend interface.
Guided physical checks verified trigger ranges, stick direction/radius modes
and selected Shift-zone configurations, with original settings restored after
every phase. The findings retain an initial ambiguous Shift event and the
successful focused follow-ups; exact physical cutoffs and every target type
are not covered by this acceptance round.

`stick left/right` and `trigger left/right` read or edit response settings.
Stick deadzones use percentages; `--preset default/instant/delay/custom` reuses
the existing application's curve choices. Stick `--circular`, `--invert-x` and
`--invert-y` take `on/off`. Trigger `--zero/--end` selects a linear travel window
on the 0–255 scale. For either control, `--curve-start X Y`, `--curve-end X Y`
and repeated `--point X Y` edit exact 0–255 input/output coordinates, with up to
two midpoints. `--clear-points` removes the midpoints. Choose either exact
coordinates or the preset/deadzone/travel controls in one command. Flags may be
combined with either. Unspecified fields and unrelated profile bytes survive.
These response edits are verified against independent vendor-encoder fixtures,
fake-pad transactions and hardware write/read-back/restoration. Guided physical
sweeps also verified left-stick inversion, output limits, custom midpoints,
circular output and inner/outer deadzones, plus left-trigger output limits,
custom midpoints and travel windows, with the right side as an unchanged
reference. Exact deadzone percentages were not calibrated against physical
stick position. Original settings were restored and verified after each test.
The backend's familiar profile methods are documented in the
[response editor reference](docs/findings-apex6.md#stick-and-trigger-response-editor).

Writes require `--experimental` and check the model, UUID, format, slot and
read-back. Transfers stage chunks before committing. Failed edits roll back only
when the same controller/profile still contains the exact bytes we wrote.
An uncertain save acknowledgment is reported as an error. Omitting `--save`
does not provide a temporary preview; restore the original values explicitly.

Gyro configuration uses the native web controls:

```bash
tools/flydigi-apex6 --device uid:YOUR_UUID gyro
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID gyro right --key m4 --enable hold
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID gyro --key2 lt --enable toggle
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID gyro --curve-end 200 240 --point 100 120
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID gyro --invert-x on
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID gyro mouse --mouse-window-radius 32
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID gyro off
```

Targets are `off`, `left`, `right` and `mouse`. `--enable hold` activates while
an assigned button is held; `toggle`/`click` toggles with a press. `--key2 none`
clears the secondary button. LT/RT can activate gyro. Settings are profile-wide.
Curves and mouse radius use native 0..255 values; `--clear-points` removes active
midpoints. A target change selects the web's default curve for that target;
explicit curve options in the same command override it. Selecting off or the
same target retains the curve. The web's separate remembered-curve cache is not
implemented. Read-only queries need no opt-in; edits require `--experimental`.
Original settings must be explicitly restored for a temporary test because
writes may persist without `--save`. See the [gyro findings](docs/findings-apex6.md#gyro-configuration)
for independent fixtures, hardware verification and physical scope. GUI wiring
remains pending.

Native feedback settings match the web Space Station controls:

```bash
tools/flydigi-apex6 --device uid:YOUR_UUID vibration
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID vibration grips --enabled on --level 2
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID vibration left --enabled on --level 3
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID vibration right --enabled off
tools/flydigi-apex6 --device uid:YOUR_UUID trigger-scene
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID trigger-scene both --scene rifle --travel 2 --frequency 3
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID trigger-scene left --travel 1
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID vibration-test --duration 60
```

Grip enable/strength controls both sides together; each trigger has its own
enable and strength for the web's trigger-bound grip feedback. Strength uses
levels 1–5, with capability-specific grip
presets. A trigger strength edit alone preserves its current mode. Enabling
trigger vibration replaces a haptic scene; selecting a scene disables the
selected trigger's conflicting vibration curve. Setting both `--enabled on`
and `--level` restores vibration strength after leaving a scene.

The twelve scenes are `machine-gun`, `rifle`, `submachine-gun`,
`light-machine-gun`, `pistol`, `shotgun`, `sniper-rifle`, `sword`, `blade`,
`dagger`, `axe` and `club`. Travel uses levels 1–3. Only the first four looping
scenes expose frequency levels 1–3. Omitting `--scene` adjusts the selected
existing scene(s). **Selecting `--scene normal` from haptics also resets the
selected base-layer trigger response curve to linear and clears its action
zones**, matching the web Pro page; other layers are preserved.

The test uses ordinary Linux game rumble on the selected controller's input
node. Hold either trigger beyond 20% to request rumble; to feel trigger feedback,
pull the trigger being tested. On the tested Pro, feedback followed that trigger's
own movement even with a constant rumble request. Release both to stop the output,
and press Ctrl-C to end the test early. It stops/erases its effect on exit and
does not change configuration or enter waveform streaming mode. Read-only
settings need no opt-in; writes and tests require `--experimental`.

The backend/CLI has independent encoder comparisons, fake-pad failure coverage
and Pro 9.0.6.8 hardware write/read-back/restoration checks. Guided physical checks
also covered grip output, trigger binding, selected scene travel/frequency and
mode transitions, with original settings restored. GUI wiring remains pending. See the
[native feedback findings](docs/findings-apex6.md#native-vibration-and-trigger-scenes).

Restorable profile packages include mapping (with macros, layers, zones, gyro and
curves), lighting and the complete motor block. The reusable
`gpa6_backup.ProfileConfig`, `pack_config`/`unpack_config` and
`read_config`/`write_config` interfaces follow the existing packed-profile pattern;
GPA6 writes remain separate from the legacy backend.

~~~bash
tools/flydigi-apex6 --device uid:YOUR_UUID backup profile.json --name "My profile"
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID restore profile.json --slot 0 --save
tools/flydigi-apex6 --device uid:YOUR_UUID library backup --name "Game preset"
tools/flydigi-apex6 library list
tools/flydigi-apex6 library show ENTRY_ID
tools/flydigi-apex6 library export ENTRY_ID exported.json
tools/flydigi-apex6 library import exported.json --name "Another copy"
tools/flydigi-apex6 --experimental --device uid:YOUR_UUID library restore ENTRY_ID --slot 0
tools/flydigi-apex6 library remove ENTRY_ID
~~~

`restore` always targets the **active slot**. Optional `--slot 0..3` checks that
selection; it does not switch profiles. Source UUID/slot are provenance, so a
package can be reused in another slot or compatible controller. Restore requires
the same SKU, format versions and profile-related capabilities. Globals such as
sleep time and report rate are outside profile packages and remain unchanged.
The OpenFlydigi JSON format is distinct from diagnostic snapshots, legacy binary
backups and vendor browser exports; those files are refused here.

The library stores named, immutable entries under
`$XDG_DATA_HOME/openflydigi/apex6/profiles` (default `~/.local/share`). Use
`library --directory DIR ...` to choose another location. Every CLI restore that
changes data first saves the destination profile as a recovery entry, printing
its path even if a later write fails. `restore --directory DIR` controls that
recovery location. Imports and exports preserve raw bytes; existing export files
are never overwritten. Offline list/show/import/export/remove need no controller.

Restoration validates the whole package and all transitions before writing,
quiesces affected trigger modes, writes mapping/lighting, then applies saved
trigger modes through persistent area-0 commands. It checks UUID, active slot,
formats and every RAM block between steps. Failures roll back only a complete
state still matching this operation; concurrent/partial changes are reported
without overwriting them. Grip-mode/reserved motor bytes have no verified
persistent write path, so differences in those bytes are refused. Unchanged
opaque data is retained. A4 edits may persist without `--save`; explicit save
failures report uncertainty without reverting the already applied RAM. See
[backup findings](docs/findings-apex6.md#restorable-profile-packages-and-library).

`snapshot` reads global settings and the active slot's mapping, lighting and
motor RAM into a new JSON file. It works with rotary and linear grips and needs
no output opt-in. It verifies transfer lengths, checksums, UUID and configuration
fingerprints, then publishes the complete file without overwriting an existing
path. Another session holding the controller claim prevents the read.

The snapshot retains every raw byte, with decoded global settings, XMUL v1.8/v1.9
headers, LGHT v2 lighting and known grip modes where available. Unknown formats
are retained with a `decode_error`. This captures **active RAM**, including any
unsaved changes; it does not switch slots, read all flash profiles, or support
restoration. Blocks above 4096 bytes are refused because chunk indices are one
byte. Changes detected during capture abort without creating an output file.

`play` accepts 48 kHz signed 16-bit PCM WAV: two channels for left/right grips,
or four channels from a DualSense capture (only channels 2/3 are used). Output
preserves stereo, filters before resampling to 1 kHz, and sends eight samples
per packet. The queue holds at most 40 ms; scheduler stalls discard stale
samples and underruns emit neutral samples. The filter adds 8 ms of delay.

The tool holds an exclusive session claim and follows xHaptics Engine 5.0.1.4's
grip-only motor lifecycle. Setup disables trigger output and enables grip
streaming. On exit, including Ctrl-C and SIGTERM, it disables triggers and grips,
then reads the current motor RAM and replays both trigger entries followed by
both grip entries. All mode writes use no ACK waits; transport write failures
and restoration-read failures are reported. A disconnected device cannot receive
cleanup. `--reconnect` resumes only the same full UUID within the remaining
playback time and drops audio missed during the absence. A profile change stops
output unless reconnect is enabled; cleanup always uses the current profile's
map. Do not infer physical motor behavior or USB/receiver timing from the emulator.

On the tested Pro firmware, left-only, right-only and stereo waveforms were felt
and neutral/disable stopped them. Restoring mode 1 still returned firmware error
1. Ordinary game rumble worked and stopped both before and after waveform
playback over direct USB; this does not establish exact motor-mode restoration.
The same replay rejection was reported on the Phantom Blade Zero variant with
firmware 9.0.4.1. Successful cleanup means the stop and replay writes completed,
not that firmware accepted the replay. No profile selection or configuration
write is used as a substitute. See the
[native-engine comparison](docs/findings-apex6.md#native-engine-restoration-investigation).
The complete DualSense relay has not been hardware-verified.

The USB DualSense relay also has an explicit Apex 6 route:

~~~bash
sudo tools/flydigi-ds5-usbip --experimental-apex6 --device uid:YOUR_UUID --motors --haptics --gain 1
~~~

Use XInput mode; the selected device must expose Xbox-style evdev axes. Both
input and output are pinned to its full UUID, and input must belong to the same
USB device. The relay drops root after attaching the virtual USB controller.
Disable Steam Input for the game and use these launch options:

~~~text
SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501,0x37d7/0x2502 SDL_JOYSTICK_IGNORE_DEVICES=0x37d7/0x2501,0x37d7/0x2502 %command%
~~~

The relay preserves fresh PCM blocks on channels 2/3 and streams them through
the stereo filter. `--motors` enables this audio source; `--gain` controls its
amplitude (0–4). HID rumble works independently: left/right intensities produce
80/160 Hz grip tones with maximum amplitude 0.35, added to the audio and clipped
to the motor range. This is an approximate application-defined rumble conversion,
not a reproduction of the original motors. Legacy `--crossover`, `--gate` and
`--curve` controls do not apply to this route.

The worker bounds incoming PCM to 40 ms, discards stale samples, and resets its
filter after overflow. Disconnects release input while keeping the virtual
DualSense attached. Reconnect and profile changes reopen the same UUID, reread
the motor map, and discard feedback from the previous session. Motor output,
setup and restoration run on their own thread. Trigger reports are counted but
not translated; motion remains disabled. The uhid relay and GUI DualSense
switch do not enable this experimental route.

Image and GIF uploads share the existing screen converter and serial transport:

~~~bash
tools/flydigi-screen show picture.png --experimental-apex6 --device uid:YOUR_UUID
tools/flydigi-screen animate animation.gif --interval 100 --experimental-apex6 --device uid:YOUR_UUID
tools/flydigi-screen send default_screen_image_150.bin --restore-default --experimental-apex6 --device uid:YOUR_UUID
~~~

Connect by USB cable. This replaces the stored screen material; the current
image cannot be read back or automatically restored. Keep a copy of any custom
material you want to preserve. The Apex 6 route accepts 1–150 frames and frame
intervals of 1–2500 ms, rounded to the nearest 10 ms with a 10 ms minimum.
It checks identity and connection before switching the screen chip, binds the
bootloader to the selected USB port, and validates each erase/write reply.
After reset is acknowledged, leave the controller connected while it syncs.
An interrupted upload can be restarted with the complete source file and
`--experimental-apex6 --port /dev/ttyACM0` (use the actual bootloader port),
instead of `--device`. Recovery accepts only an FFAA:5555 bootloader; that mode
cannot report the controller UUID. `--restore-default` marks the supplied file
as stock; it does not download or locate that file.

Still-image, two-frame animation and stock-restore uploads were verified on
Apex 6 Pro firmware 9.0.6.8, with display output confirmed and the original
configuration preserved. The 137-frame stock upload took about four minutes,
followed by synchronization/restart. Other variants remain experimental.

LCD animation-display/status-bar flags are documented only. Screen GUI wiring
remains pending. Legacy Apex 5 commands remain refused on Apex 6. Nickname
editing, trigger translation, motion, and ordinary-game-audio synthesis are
later milestones. Complete input layouts, receiver timing and
the remaining actuator behavior still require hardware validation.
Protocol evidence, emulator assumptions and
fixture reproduction are in [the Apex 6 findings](docs/findings-apex6.md).

## Tests

Tests are plain Python scripts rather than a single test-runner suite.

Run every backend test, including newly added files, with:

~~~bash
status=0
for t in tests/test_*.py; do
    case "$t" in
        tests/test_models.py|tests/test_qml.py|tests/test_shell.py|tests/test_apex6_gui.py|tests/test_apex6_analog_gui.py|tests/test_apex6_macros_gui.py|tests/test_apex6_haptics_gui.py) continue ;;
    esac
    python3 "$t" || status=1
done
test "$status" -eq 0
~~~

The loop continues after a failure so every backend script runs, then returns a
nonzero status if any script failed. Backend tests use Python only and require
no controller or dock.

The Qt-dependent tests are:

- `tests/test_models.py`, `tests/test_apex6_gui.py`,
  `tests/test_apex6_analog_gui.py`, `tests/test_apex6_macros_gui.py`, and
  `tests/test_apex6_haptics_gui.py`:
  distribution PySide6;
- `tests/test_shell.py` and `tests/test_qml.py`: PySide6 and Kirigami.

`tests/test_qml.py` discovers the `tests/qml/tst_*.qml` cases. Exact commands,
`tools/generate-qmltypes`, and qmllint setup are documented in
[gui/README.md](gui/README.md).

## Documentation

| Document | Contents |
|---|---|
| [PROGRESS.md](PROGRESS.md) | Current feature status, validation level, limitations, and roadmap |
| [PROTOCOL.md](PROTOCOL.md) | Wire protocol and hardware-verification results |
| [docs/findings-profile-blob.md](docs/findings-profile-blob.md) | Profiles, mappings, sticks, gyro, trigger blocks, and macros |
| [docs/device-settings.md](docs/device-settings.md) | Device settings, battery, vibration bind, lighting config, and command inventory |
| [docs/findings-steam.md](docs/findings-steam.md) | Steam interaction, device takeover, and locking |
| [docs/findings-games.md](docs/findings-games.md) | Game detection, routes, and validation notes |
| [docs/findings-haptics.md](docs/findings-haptics.md) | Virtual DualSense, USB/IP, and haptic audio |
| [docs/findings-screen.md](docs/findings-screen.md) | Display upload paths, safeguards, and settings |
| [docs/findings-other-devices.md](docs/findings-other-devices.md) | Device codes, other Flydigi hardware, and dock findings |
| [docs/findings-desktop-app.md](docs/findings-desktop-app.md) | Apply/save behavior and desktop implementation findings |
| [docs/third-party-mods.md](docs/third-party-mods.md) | Unsupported third-party-mod route |

## Flydigi content not shipped by this repository

No decompiled source, extracted assemblies, game configs, mod binaries, or
Flydigi assets are tracked. The following paths are local and ignored:

- `gamelist.json`, `configs/`, and `mods/`, fetched on demand;
- `decompiled/`, `bundle/`, `asar/`, `bin/`,
  `installer-listing.txt`, and `work/`, produced during local protocol research.

These are not part of the public repository layout and are not required for
normal controller use, except for the fetched game data needed by game routes.

## Disclaimer

This is an independent project. It is **not affiliated with, authorized by, or
endorsed by Flydigi**. “Flydigi,” “Space Station,” and product names are used
only to identify interoperable hardware and software; their trademarks belong
to their respective owners.

The protocol was determined by reverse engineering for interoperability. Laws
vary by jurisdiction; check what applies where you are.

No Flydigi code, assembly, asset, game config, or mod binary is redistributed.
`tools/fetch-configs` contacts Flydigi's public API only when a user runs it and
stores the result locally.

The software is provided as-is, without warranty. Writing configuration or
factory data to a controller carries the ordinary risk of writing device flash.

## Licensing

The repository follows [REUSE](https://reuse.software/). Code carries inline
SPDX headers; prose and generated/non-source files are annotated in
`REUSE.toml`; complete license texts are under `LICENSES/`.

| Paths | License |
|---|---|
| `flydigi/`, `tools/`, and non-Qt tests | MIT |
| `gui/`, Qt-dependent tools/tests, QML tests, and `pyproject.toml` | GPL-3.0-or-later |
| `README.md`, `PROGRESS.md`, and `docs/` | MIT |
| `PROTOCOL.md` | CC0-1.0 |
| `udev/` and `pipewire/` | CC0-1.0 |
| `tests/qml/four-frames.gif` | CC0-1.0 |
| Repository metadata listed in `REUSE.toml` | CC0-1.0 |

The Qt-linked desktop layer may import the dependency-free MIT backend; the
backend does not import the desktop layer. See [LICENSE](LICENSE) for the
licensing rationale and [NOTICE](NOTICE) for third-party attribution.
