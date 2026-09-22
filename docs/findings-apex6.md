# Apex 6 / Apex 6 Pro and xHaptics

Research date: 2026-09-22. **Static analysis and offline encoder verification;
no Apex 6/6 Pro hardware available.** Experimental implementation can use an
emulated pad and tests as its acceptance target. Runtime support is not enabled
by this investigation.

The new haptics are explicitly present in Flydigi's shipping software. Support
looks feasible, including a route from our virtual DualSense audio to linear
grip haptics. It requires a new transport/configuration dialect and a waveform
output backend. Merely accepting `k6` or reusing the old SDK's `K6Trigger*`
packets would be insufficient.

## Evidence and versions

| Component inspected | Version | What it establishes |
|---|---|---|
| Updated installed Space Station | 4.2.2.3 | Old .NET SDK, architecture probe, desktop compatibility boundaries |
| Official web Space Station | V5.0.1.1 | Model/capability handling, new configuration codec, xHaptics UI |
| Separate Flydigi xHaptics Engine | 5.0.1.4 | Native C++ HID output, motor sample rate, stereo DSP and lifecycle |

The desktop update completed, including replacement of `hidapi.dll`. Its ASAR
package metadata reports 4.2.2.3; the installation's plain `version` file is
the Electron version. Desktop assemblies were decompiled. The separate engine
was downloaded and **unpacked without installing or starting it**; selected
native routines were disassembled and emulated offline. All 119 files in the
engine release manifest matched their SHA-256 hashes. Its generation date is
2026-09-21.

First-party inputs:

- [Web Space Station](https://space_station.flydigi.com/).
- [Device/core protocol](https://space_station.flydigi.com/assets/vendor-gpa6-core-D5jdJfEw.js).
- [Configuration codec](https://space_station.flydigi.com/assets/vendor-gpa6-codec-C4fNnifj.js).
- [xHaptics page](https://space_station.flydigi.com/assets/XHapticsPage-BwKsY_vv.js).
- [Engine installer](https://tencent-api.cdn.flydigi.com/app/Flydigi-Engine-5.0.1.4-x64-setup.exe), from
  `POST /api/Update/software` on the web host with
  `{"version":"5.0.0","app_class_type":22}`. The PC update API returned the same release.

Local evidence is under ignored `work/apex6-research/`. Vendor code/binaries are
not distributed with these notes. `evidence-manifest.json` pins the raw inputs:

| File | SHA-256 |
|---|---|
| Desktop `SpaceStationService.exe` | `9116d63a340268f24eb20f64207e8fd1cf47213e917a91d26b4a5742a5f7cf76` |
| Desktop `app.asar` | `0d7eeb0b87598cfc33be64e36b62a594075c205c1ed563bb9ade11bb32a7c494` |
| Engine installer | `5c8f450113a96ef1715a4fb11ae79995702e832b6996b4308774a55c73e81d42` |
| Extracted `xHaptics Engine.exe` | `176f4ee36ba66d818d2c785cc7688ff7608d2a3e50b5d87ce20d1cbd49c444a6` |

## What the new haptics expose

The web table names device IDs **149 = Apex 6**, **150 = Apex 6 Pro**, and
**152 = Apex 6 Pro Phantom Blade Zero**, all family `k6`. Its HID filter is VID
`37d7`, PID `2502`, usage page `ffa0`. These are software references, not a
measured descriptor or complete list of connection modes.

The `k6` profile calls both trigger actuators `vcm`. Grips default to `rotor`,
then change to `vcm` when the device reports `hapticGrip`. Diagnostics call that
capability “LRA grip.” The driver must read capabilities instead of assigning
identical motors to every `k6` SKU. This evidence does not prove the flag values
returned by every physical SKU/firmware combination.

New command-1 device information has a feature byte at payload offset 24:

| Bit | Decoder field |
|---:|---|
| 0 | `featureImu` |
| 1 | `featureLed` |
| 2 | `featureSwitch` |
| 3 | `featureScreen` |
| 4 | `featureTriggerHaptic` |
| 5 | `featureTriggerVibrate` |
| 6 | `featureAudio` |
| 7 | `featureHaptic` → runtime `hapticGrip` |

That decoder has seven firmware-version fields. Command 4 returns a 16-byte
UUID, unlike our old 13-byte UID assumption.

The UI and engine support game-audio conversion, separate trigger/grip gains
and enable switches, grip attenuation while triggers are pressed, inactivity
sleep/resume, adaptive-trigger configurations, and racing telemetry effects.
The browser delegates to a local engine using HTTP port 18757, WebSocket port
18758, and launch URI `flydigi-xhaptics://start`.

The packaged grip presets select a stereo `hpss-mapper-v2` algorithm. Their
parameters separate harmonic/percussive content and map band envelopes to
low-frequency carriers; one balanced preset uses 60, 80 and 120 Hz carriers.
There are attack/release, phase and resonance-limit controls. These are
synthesized waveforms, not just two RMS levels. The engine requires 48 kHz PCM
input and reports a **1,000 Hz motor sample rate**.

For a first DualSense bridge, Flydigi's ordinary-game-audio synthesis is optional:
games already supply their intended waveform through our USB/IP audio endpoint.
We can preserve those haptic channels and filter/resample them for the pad.
Reproducing Flydigi's conversion of ordinary sound is a separate feature.

## Recovered real-time packet

The ordinary streaming path builds a **32-byte private frame** and passes it to
Windows HID as a **33-byte write with a leading zero report-ID placeholder**.
Our old transport instead builds 32 bytes including report ID 3. Linux framing
must follow the device descriptor; the emulator must model that distinction.

Offsets below exclude the leading HID placeholder:

| Offset | Value |
|---:|---|
| 0–1 | `5a a5` |
| 2 | `57` (command 87) |
| 3 | `1b` (25-byte payload + 2) |
| 4 | Control byte |
| 5–28 | Eight interleaved `(trigger, left grip, right grip)` sample triples |
| 29–30 | Zero padding |
| 31 | Sum of bytes 2 through 30, modulo 256 |

Control is `0x80 | (selector & 3)`, plus bit 2 for trigger, bit 3 for left grip,
and bit 4 for right grip. The physical meaning of each trigger-selector value
remains unverified. There is one trigger sample column per packet, not four
independently encoded actuator columns.

Samples are clamped to `[-1, 1]`, scaled as `(x + 1) * 127.5` with float32
arithmetic, rounded half up, and clamped to `[0, 255]`. Neutral is **128**.
Disabled channels and missing samples receive 128. The builder selects the
last eight samples of a longer buffer.
Eight samples at the reported 1 kHz imply nominal **8 ms / 125 packets per
second**; actual delivery cadence, receiver buffering and jitter tolerance are
unmeasured.

A separate racing reference-report path emits 33 bytes beginning `03 5a a5`,
with a calculated checksum at report offset 32. It must not be confused with
the ordinary path.

Mode command **0x53 / 83** now takes `[area, target, mode, ...params]`, confirmed
in both web and native code. The engine checks ACK errors for invalid selections
and insufficient parameters. It also reads persistent motor mapping through
0xA3 and restores it after a session. Implementation must cover restoration,
not only neutral samples. Full mode/target semantics remain to be recovered
and tested against the emulator's explicit assumptions.

### Offline verification

RVA anchors in the pinned executable (image base `0x140000000`):

| RVA | Observed function |
|---|---|
| `224370` | Streaming control byte and eight-sample payload builder |
| `22d160` | Float-to-byte conversion |
| `22cbf0` | Private-frame padding, length and checksum |
| `2268d0` | Zero HID placeholder + private frame → Windows `WriteFile` |
| `19f0a0`, `19eea0` | Racing reference-report builder and checksum |
| `223ac0` | Generic 0x53 configuration + ACK path |
| `0e573e`, `0ef6a8` | 1000 Hz values in status/DSP diagnostics |

`work/apex6-research/verify_engine_packets.py` uses Unicorn to exercise the
actual streaming builder and quantizer. Allocation, diagnostic copies, locks,
device-open and HID calls are replaced by in-memory stubs. **71 quantizer cases
and 48 streaming cases passed**, covering clipping, neutral, short/long buffers,
channel enables, and selectors 0–2. Results are in
`engine-packet-verification.json`. This is verification of encoding, not a full
pad emulator, firmware acceptance test, or test of physical motor response.

## Why desktop 4.2.2.3 is not enough

All 18 compared `K6*.cs`/`ControllerSdk.cs` files are byte-for-byte unchanged
from the local 4.2.1.5 decompilation. No `K6Trigger*`/`K6Realtime*` call sites were
found in the decompiled desktop service or adaptive-trigger service.

The old SDK mode command has two mode bytes, whereas the current engine uses
area/target/mode. Its real-time packet uses length 28 and checksum at old-buffer
offset 30 over different bytes. The engine uses length 27 and the format above.
The [older SDK transcription](../PROTOCOL.md#3b-k6trigger--low-level--waveform-gated-to-k6-in-the-sdk)
is historical evidence rather than the shipping implementation reference.

`ControllerCommunicationThread.ProbeNewArchitecture` probes command 7. On
success it accepts PID `2401` into the new-architecture branch and reports
other PIDs unsupported. `ControllerRepository` skips old profile initialization
in that branch. Updating the desktop alone therefore does not expose the whole
current Apex 6 implementation.

The web configuration stack also changes substantially: command 7 reads format
versions, 0xA3 reads RAM blocks, and 0xA4 stages/commits chunks with length/CRC32
checks. `XMUL` mapping files have v1.8/v1.9 codecs, layers, activators, macros,
zones and TLV blocks; `LGHT` is a separate lighting format. The current fixed
Apex 5 profile blob cannot be reused. Legacy profile writes should stay refused
even when experimental haptic streaming is enabled.

## Implementation and test plan without hardware

| Area | Reusable | Required work |
|---|---|---|
| Discovery/identity | Registry and selectors | Dialect probe, framing, UUID, SKU names, runtime motor flags, permissions/SDL IDs |
| DualSense input/audio | USB/IP device and haptic PCM capture | Model new input/motion reports separately where evidence differs |
| Waveform output | Audio acquisition and worker structure | Stereo anti-alias filtering/resampling to 1 kHz, eight-sample pacing, bounded buffers and underruns |
| Trigger effects | DualSense effect decoding | New motor configuration/translation and selector routing |
| Stop/reconnect | Relay lifecycle structure | Neutral/disable, saved motor-map restoration, reopen/reconfigure |
| Persistent configuration/UI | General UI concepts | New codecs/transfers, capability handling and defaults |

`flydigi/haptics.py` sums both channels into low/high energy for rotary motors.
`tools/flydigi-ds5-usbip` drives that path at 60 Hz with a latest-value mailbox,
discarding earlier PCM blocks. Keep that behavior for rotary pads. Linear grips
need a separate sample-stream backend that preserves stereo and continuity,
filters before downsampling, and handles delayed audio explicitly.

The first implementation milestone should be an opt-in experimental backend
and a stateful emulated pad. Hardware availability is **not** its acceptance
gate. Its test suite should include:

1. Identity variants with/without `hapticGrip`, unknown feature bits, different
   report descriptors and format versions. Reject legacy configuration writes.
2. Byte-for-byte comparison against vendor-derived fixture packets. Keep the
   fixture generator independent of the production encoder so a shared mistake
   cannot make both encoder and mock agree.
3. Fake-clock streaming checks: sample order, left/right isolation, resampling
   of known signals, neutral/disabled channels, bounded latency and underruns.
4. Stateful start/stop: require motor setup before accepting waveform output;
   verify neutral/disable and restoration of the original motor map. Inject
   failed/malformed ACKs, mapping-read failures and partial setup.
5. Disconnect/reconnect, pad replacement, profile changes, output contention,
   and cleanup during exceptions. Ensure writes follow the selected UID.
6. Regression tests keeping Apex 5/Vader 5 on their existing paths. Add codec
   round-trips and transactional rollback tests when profile support is added.

This can establish software correctness against the recovered protocol. Label
the result **experimental, emulator-tested**: actual actuator response, trigger
selector behavior, USB/receiver timing and firmware quirks remain unknown. Do
not silently claim the emulator proves these physical properties.

Planning estimate for a developer familiar with this repository: **2–4
engineering days** for the protocol/emulator and standalone waveform prototype,
**1–2 additional weeks** for DualSense streaming with lifecycle/trigger integration,
and **several more weeks** for broad configuration/UI parity. These are scope
estimates; unresolved mode semantics may change them. The most useful first
deliverable is experimental grip-waveform support, with trigger translation
and full configuration tracked separately.

## Local reproduction

Desktop sources: `work/apex6-research/4.2.2.3/decompiled/`. Formatted web bundles:
`work/apex6-research/web/pretty/`; anchors `Tn` (info), `rn` (0x53), `On.k6`
(defaults), router `ut` (runtime grip override). Engine presets:
`engine-unpacked/{app}/config/profile-packages/global/realtime-audio/`.

The engine installer uses Inno Setup 7.0.0.3. Maintainer-distributed `innounp`
2.71.1 extracted it under Wine with `-x -b -q -y -dengine-unpacked`; the old
installed `innoextract` could not parse it. Offline verification dependencies
were installed only under ignored `work/apex6-research/python-tools/`.

The working desktop launch was recovered from Claude logs and repeated here:

```bash
distrobox enter wine-arch -- bash -lc '
  export WINEPREFIX=$HOME/.local/share/wineprefixes/flydigi
  export WINEDEBUG=-all
  cd "$WINEPREFIX/drive_c/Program Files/Flydigi Space Station"
  setsid nohup wine "Flydigi Space Station.exe" >/tmp/ss-launch.log 2>&1 </dev/null &
' </dev/null
```

The successful run logged an established UI/backend socket. No single root
cause was proven for the earlier failed attached launches.
