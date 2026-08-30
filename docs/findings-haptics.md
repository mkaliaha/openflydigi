# DualSense relay and haptic audio

Reference for presenting a Flydigi pad as a virtual DualSense and translating
DualSense output to the physical controller. Verified against the repository on
2026-08-23.

## Relay choices

| Relay | Virtual device | Haptic audio | Privilege |
|---|---|---|---|
| `tools/flydigi-ds5-usbip` | Composite USB DualSense through `vhci-hcd` | Yes | Starts as root to attach, then drops to the invoking user. |
| `tools/flydigi-ds5` | HID-only DualSense through uhid | No | Normal user with access to uhid and controller nodes. |

Use USB/IP when available:

```bash
sudo tools/flydigi-ds5-usbip --haptics --motors [--device SELECTOR]
```

The desktop application's DualSense switch starts the same relay with motor
conversion enabled and haptic-analysis reporting disabled.

Use the uhid fallback when the kernel has no `vhci-hcd` support:

```bash
tools/flydigi-ds5 [--device SELECTOR]
```

A selector may be a hidraw path, UID or UID prefix, MAC address, or nickname.
It pins the gamepad input node and vendor node to the same physical pad. The
desktop switch currently does not pass its saved primary-pad selector; on a
multi-pad system, start the relay manually with `--device` when deterministic
selection is required.

## Capabilities

| Capability | USB/IP | uhid | Notes |
|---|:---:|:---:|---|
| Buttons, sticks, analog triggers | Yes | Yes | Digital L2/R2 are synthesized at 12% analog travel because xpad exposes no digital trigger buttons. |
| Adaptive triggers | Yes | Yes | DualSense output effects are translated to Flydigi trigger modes. Pads without adaptive triggers keep the other relay functions but skip trigger writes. |
| Compatibility rumble | Yes | Yes | HID motor fields drive the physical pad's motors. |
| Gyroscope and accelerometer | Yes | Yes | Motion stream is enabled by default. |
| Battery | Yes | Yes | Flydigi levels 0-5 map to DualSense levels 0-10. Polled every 30 seconds only while motion streaming is enabled. |
| Touchpad click | Yes | Yes | SELECT maps to touchpad click; SELECT+START sends Create. No touch position or gestures are available. |
| Haptic audio | Yes | No | Requires a USB audio interface sharing the virtual controller's USB parent. uhid has no USB parent. |

The vendor motion report uses report ID `0x04` and marker `0xEF`. Sticks are at
offsets 4/6/8/10, gyro at 18/20/22, and acceleration at 24/26/28. The default
accelerometer scale is approximately 2.441; gyro scale defaults to 1.0 and can
be adjusted with `--gyro-scale`.

M1-M6 are not exposed by xpad and the DualSense protocol has no equivalent
extra buttons. They reach games only through the pad's onboard remapping to
standard controls.

## USB/IP architecture and privilege boundary

The USB/IP relay serves the captured DualSense USB descriptors in userspace and
attaches them locally to `vhci-hcd`. `hid-playstation` binds the HID interface
and `snd-usb-audio` binds the sibling audio interfaces.

No network socket is opened. The relay gives `vhci-hcd` one end of a local
`AF_UNIX` stream socketpair, so there is no listening TCP port and no import
service.

Attaching to vhci and loading `vhci-hcd` require root. The relay performs those
steps before opening the physical controller or starting its service threads,
then changes its real, effective, and saved UID/GID to the user identified by
`PKEXEC_UID` or `SUDO_UID`. `--stay-root` disables the drop for debugging and
should not be used for normal sessions.

Closing the socket makes vhci release the port. The long-running relay can be
stopped by an ordinary user signal after the privilege drop. The physical
controller is opened after the drop, so udev permissions remain required:

```bash
tools/apex5-setup install-rules
```

An unreadable evdev node is fatal. Failure to open the vendor node is reported
separately: virtual input may continue, but physical rumble and adaptive
triggers cannot be written.

Granting unattended permission to attach arbitrary USB devices would be a local
security risk because a process could emulate input devices such as a keyboard.
The game daemon therefore never enables DualSense mode automatically.

## Game setup

1. Start DualSense mode before the game. Games commonly enumerate the audio
   endpoint only at launch; enabling the relay later can give working input and
   triggers but silent haptics until the game is restarted.
2. Disable Steam Input for the title. Steam Input masks the pad as an Xbox
   controller and removes native DualSense semantics.
3. Prevent SDL games from binding the physical Flydigi pad as a second
   controller:

   ```text
   SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501
   SDL_JOYSTICK_IGNORE_DEVICES=0x37d7/0x2501
   ```

   The first name covers SDL2 and the second SDL3.
4. Confirm the physical pad's controller-data stream is enabled. The relay does
   not read or clear the controller's separate `third_party` flag. With motion
   enabled, startup sends command 17 with `controller_data=1` and `raw=1`, which
   restores the input and motion streams. `--no-motion` skips that write, so a
   prior mode that disabled controller data can leave `evdev` at zero.

`--verify` reads the relay's own virtual `054c:0ce6` evdev node and counts
events. Use it to distinguish virtual reports not reaching the OS from a game
binding the wrong controller.

## USB/IP options

| Option | Default | Meaning |
|---|---:|---|
| `--device SELECTOR` | first matching pad | Select and pin one physical pad. |
| `--dump` | off | Decode output effects without driving the physical pad. |
| `--quiet` | off | Suppress per-effect output. |
| `--no-motion` | off | Do not enable or relay the vendor motion stream. |
| `--gyro-scale` | `1.0` | Scale relayed gyro values. |
| `--accel-scale` | approximately `2.441` | Scale relayed acceleration. |
| `--haptics` | off | Report per-channel haptic-audio energy. |
| `--motors` | off | Convert haptic audio to the physical pad's two motors. |
| `--gain` | `1.5` | DSP output gain. |
| `--crossover` | `250` Hz | Low/high band split. |
| `--gate` | `0.015` | Ignore signal below this level. |
| `--curve` | `0.7` | Output shaping exponent. |
| `--verify` | off | Count looped-back virtual input events. |
| `--stay-root` | off | Keep root after attach; debugging only. |

The uhid relay accepts the input-side options: `--device`, `--dump`, `--quiet`,
`--no-motion`, `--gyro-scale`, and `--accel-scale`.

## Haptic audio conversion

A DualSense USB audio device has four channels:

| Channel | Function |
|---:|---|
| 0 | Headphone jack |
| 1 | Controller speaker |
| 2 | Left haptic actuator |
| 3 | Right haptic actuator |

The converter reads channels 2 and 3, sums their content, and splits it by
frequency. Low-frequency energy drives the larger left ERM motor; high-frequency
energy drives the smaller right motor. This preserves more of a voice-coil
waveform's character than a left-to-left channel mapping.

The DSP runs at 48 kHz, decimates by 8, applies a noise gate, rescales the
remaining range, and applies the shaping curve. Continuous motor updates use
zero-wait writes; waiting for the normal 100 ms acknowledgement introduces
unacceptable latency. The relay uses a bounded audio queue and drops the oldest
block when it falls behind.

Use `tools/ds5-channel-probe` to send a tone with an explicit
`front-left,front-right,rear-left,rear-right` channel map. Use
`tools/haptics-inspect` to measure a real sink's monitor. When a sink name does
not contain the case-sensitive word `DualSense`, pass it explicitly with
`--sink`.

### Standalone bridge

`tools/flydigi-haptics` captures the monitor of a real USB DualSense audio sink
and drives a Flydigi pad's motors. It requires the real DualSense as the audio
source. Options are `--sink`, `--gain`, `--gate`, `--curve`, `--crossover`,
`--dump`, `--watch`, and `--quiet`; defaults are gain 3.0 and crossover 150 Hz.

The bridge is intended for an Apex-class adaptive-trigger pad but currently has
no `--device` selector. It opens the first new-protocol Flydigi controller and
checks the capability only afterward. If a non-adaptive-trigger model is found
first, it falls back to dump mode even when a compatible Apex is also attached.
Multiple Apex pads also cannot be selected deterministically. This is a current
selection defect, not support for the first arbitrary Flydigi pad.

`tools/haptics-simulate` is not a valid bridge test in its current form. Its
waveforms are written to channels 0/1 while the DSP reads channels 2/3, and its
literal sink name does not match the automatically discovered real-device
sink. Use `tools/ds5-channel-probe` or correct the simulator before treating a
synthetic run as motor-path verification.

Several older hardware probes also call `Controller()` without the shared
`--device` selector. Some check a model or capability only after the first node
has been selected, and others use the project's broad supported-model gate.
Because stick, trigger, and gyro probes can change live controller state or the
active profile, leave only the intended pad attached unless the selected probe
explicitly accepts `--device`.

## Reconnection and status

The virtual DualSense remains attached when the physical pad sleeps or is
unplugged. `PadLink` retries missing evdev and vendor nodes, re-enables motion,
and reapplies the most recent trigger effects after reconnect. A selected
`--device` is resolved again so reconnects do not silently switch to another
pad.

The relay writes a per-run log to
`$XDG_STATE_HOME/flydigi/ds5-relay.log`, normally
`~/.local/state/flydigi/ds5-relay.log`. The desktop parser recognizes:

| Counter | Meaning |
|---|---|
| `reports` | Virtual input reports sent to the host. |
| `evdev` | Physical gamepad events consumed. |
| `motion` | Physical motion frames consumed. |
| `out` | DualSense output reports received from the host/game. |
| `iso_urbs` | Haptic-audio USB requests received. |
| `loopback` | Virtual input events read back with `--verify`. |
| `pad` | `1` while physical input is connected, otherwise `0`. |
| `drops` | Physical-pad disconnect/reconnect count. |

`out` increasing shows that software is driving the virtual controller;
`iso_urbs` increasing shows that audio haptics are arriving. With `--haptics`,
the relay also reports per-channel RMS, active-window percentage, and peak.
Peak is lifetime-high for the run, so use RMS and activity to distinguish a
continuous stream from a single startup click.

The desktop application deliberately leaves an active relay running when the
window closes. Use the DualSense switch or send a termination signal to stop it.

## Multi-controller limitations

- HID input, compatibility rumble, and adaptive-trigger output are per virtual
  device.
- A game may choose only one of several DualSense audio sinks; the relay cannot
  force a title to distribute haptic audio across controllers.
- Two virtual instances currently reuse the same captured pairing feature
  report. Safe multi-instance support would require a distinct address derived
  for each physical pad.
- The standalone audio bridge has no pad selector, as described above.

## Descriptor maintenance and tests

The virtual device serves captured DualSense descriptors and feature reports
from `flydigi/ds5_usb.py`. Implemented feature reports are `0x05`, `0x09`,
`0x0B`, and `0x20`; the captured data contains no `0x0C` report.

Refresh the blobs from a real USB DualSense with `tools/gen_ds5_usb.py` and use
`tools/ds5-dump-features` for a read-only comparison. Bluetooth/pairing
addresses are expected to differ because committed data uses a placeholder.

Run the relay model self-test without root, a pad, or a vhci port:

```bash
python3 tests/test_dsmode.py
```

Project-wide validation and hardware-dependent open work are tracked in
[PROGRESS.md](../PROGRESS.md).
