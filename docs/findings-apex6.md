# Apex 6 / Apex 6 Pro and xHaptics

Initial research: 2026-09-22. **Hardware update: 2026-11-07, Apex 6 Pro over receiver
and direct USB, firmware 9.0.6.8.** Identity, snapshots, global setting writes,
lighting, physical button remapping, quick profile switching, stick precision
and XInput Home are verified on that unit.
CLI settings/profile editing is implemented; GUI editing is not.
Left/right/stereo grip output stopped correctly, but mode-1 restoration was
refused. Ordinary game rumble still worked afterward. Some setting effects and
the complete USB DualSense relay remain unverified. See the hardware findings
below for the precise scope.

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
not only neutral samples. The grip subset is recovered below; broader mode/target semantics and physical
trigger routing remain unverified.

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

## Original implementation and test plan (2026-09-22, before hardware)

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

## Implemented first milestone

`flydigi/gpa6.py` owns the new protocol; `flydigi/waveform.py` owns stereo DSP,
pacing and session cleanup; `flydigi/mock/apex6.py` models the device.
`tools/flydigi-apex6` provides identity reads and opt-in tone/WAV playback.
The registry names all three SKUs and preserves the entire 16-byte UUID.
`identity.SUPPORTED` deliberately continues to describe legacy configuration
support (`k5`, `f5`), keeping the GUI, daemon and older relays off the new dialect.
The `37d7:2502` permission rule is included. The experimental USB relay prints
SDL launch options including `37d7:2502`; default GUI/legacy options are unchanged.

Descriptor parsing selects the vendor output report ID and byte count, accepts
32–64 byte outputs and rejects ambiguous or smaller layouts. Hidraw writes
include a report ID or zero placeholder; unnumbered reads do not. The command-7
versions are exposed for diagnostics; no version is interpreted as an Apex 5
profile format. Motor RAM layout validation, not a guessed mapping-file
version, gates volatile grip output.

Additional static anchors from the same pinned native executable:

| RVA | Recovered behavior |
|---|---|
| `21e27d` | Grip stream setup: command `53`, payload `01 12 02 40 00` |
| `21e5b5` | Grip disable: command `53`, payload `01 12 00` |
| `222ee0` | Read RAM ID 6 using command `A3` (native chunk size 20; web default 16) |
| `22cd00`, `22c780` | Motor-map layout 2; left/right grip modes at offsets 23/25, mode 0 has no params, mode 1 has one following gain byte |
| `223366` | Restore using area 1, targets `10`/`11`, saved mode and parameters |
| `223f70` | Command-53 ACK payload status 0 means success; nonzero is failure |

The emulator explicitly assumes area-1 writes are volatile, RAM 6 retains the
persistent profile mapping, changing profiles resets live modes, and an enabled
non-neutral grip waveform requires realtime setup. Those state transitions are
software acceptance contracts; they are not firmware observations. Only the
grips are configured/restored. Trigger modes and the trigger waveform column
are not driven. Neutral samples are sent before disabling; an all-channels-off
frame is also sent, followed by acknowledged mode disable. The native streaming
builder skips output entirely when all channels are disabled, so that explicit
off frame is an emulator assumption rather than a native fixture case.

The DSP accepts 48 kHz stereo PCM or the two haptic channels from four-channel
DualSense PCM. It uses a 769-tap Blackman-windowed sinc low-pass at 350 Hz before
48:1 decimation, with 8 ms group delay. Packet pacing is nominally 8 ms. The
40 ms queue cap, stale-data discard, neutral underruns and periodic UUID/profile
checks are tested using a fake clock. Claims exclude this project's other
writers for the whole session. Hardware timing and third-party software that
does not honor advisory locks remain untested.

`tests/test_apex6.py` covers identity variants, descriptors, format versions,
legacy-write refusal, independent encoder fixtures, stereo isolation and
anti-alias filtering, chunk boundaries, pacing, underruns, bounded backlog,
setup/ACK failures, partial cleanup, profile changes, UUID replacement,
reconnect, contention, and the command-line mock route. The tests run without
research files or vendor binaries.

`tests/fixtures/apex6-encoder.json` stores 71 native quantizer results and 48
native streaming packets with their input samples. `tools/gen-apex6-fixtures`
regenerates these by calling the pinned executable's routines under Unicorn
through the local research harness; it never imports the production encoder.
Regeneration requires the ignored research directory and its optional research
dependencies. Only input/output protocol facts, not vendor implementation or
assets, are stored in the fixture.

## Implemented USB DualSense integration

`flydigi/ds5_apex6.py` connects the existing virtual USB DualSense to the grip
backend, enabled only by `tools/flydigi-ds5-usbip --experimental-apex6 --device
SELECTOR`. `--motors` enables game haptic audio; HID rumble remains independent.
This uses the existing evdev Xbox-layout mapping, gated on the required axes,
and resolves input under the selected vendor node's USB root. It does not claim
to decode new vendor input or motion reports. The hardware checks below cover
selected buttons and stick axes; the complete input layout and in-game relay
remain unverified. Both input and feedback are pinned to the full UUID on reconnect.

The output worker owns its HID handle and claim from setup through restoration.
USB callbacks enqueue PCM with arrival timestamps into a 40 ms inbox, respecting
isochronous packet offsets and substituting silence for failed packets. The
worker consumes every fresh block in order; overflow or stale data resets
queued samples and filter history. The existing session independently bounds
the filtered queue and checks sample age after blocking identity reads. These
are software queue bounds, not a measured end-to-end hardware latency guarantee.

HID rumble intensities synthesize left/right 80/160 Hz tones at maximum amplitude
0.35 and mix additively with the audio before clipping. Those frequencies and
gain are our approximation, not recovered vendor behavior. Zero rumble updates
stop that source; audio underruns do not clear HID levels. Disconnects, profile
changes and reconnects discard both sources' previous state. A single worker
performs setup, output and cleanup, while USB enumeration/input serving continues
independently. A physical-pad disconnect releases buttons and axes without
detaching the virtual USB controller.

`tests/test_ds5_apex6.py` exercises the actual DualSense endpoint callbacks,
stereo continuity, simultaneous sources, packet offsets, queue overflow,
underruns, malformed input, worker ownership, UUID replacement, lost replies
during setup/streaming/cleanup, profile changes, runtime disconnect/reconnect,
privilege-drop ordering and final restoration. No kernel module, hardware or
vendor binary is required for these tests.

Adaptive-trigger translation, new motion reports, ordinary-game-audio synthesis
and GUI configuration remain separate milestones; CLI configuration editing is
documented below. Bluetooth
HID waveform transport is not implemented by this USB relay. Neither the uhid
relay nor the GUI enables the Apex 6 route. No configuration/UI parity or
hardware acceptance is implied.

## Implemented read-only configuration snapshots

`tools/flydigi-apex6 snapshot FILE` captures global RAM and the currently active
slot's mapping, lighting and motor RAM. This is an active-state diagnostic
snapshot, including unsaved changes, not a backup of all stored profiles.
It requires a recognized Apex 6 SKU but does not require `hapticGrip`. It never
selects another slot or sends configuration/write/save commands.

Additional anchors in the pinned web bundles:

| Bundle/function | Recovered behavior |
|---|---|
| core `ee` | A3 QUERY_RAM request `[1, ramId]`; response `[1, status, ramId, activeSlot, len:u16le, crc32:u32le]` |
| core `_t`, `te` | A3 READ_CHUNK request `[0, ramId, index, 16]`; response `[0, status, ramId, activeSlot, total:u16le, index, count, data...]` |
| core `Zt`, `Je`, `Jt`, `Qe` | RAM IDs 1=global, 4=lighting, 5=mapping, 6=motors; corrected and confirmed on hardware |
| core `vt` | A1 active slot, four slot CRC16s and global CRC16 |
| core `Ct`, `qt`, `K` | Four-byte length/CRC16 envelope; CRC16-CCITT with initial FFFF; reflected IEEE CRC32 for RAM fingerprints |
| core `tn`, contract `ir` | Global RAM payload at bytes 2–81, including name/settings offsets |
| codec `Ne`, `be`, `ge`, `Te` | XMUL versions 8/9, slot header and runtime timing fields |
| codec `Mn`, `$n`, `wn` | LGHT v2 header/data sums and off/solid/breathing/gradient/streaming/default modes |

`Protocol.read_ram` validates every chunk's status and echoed fields, verifies
the assembled CRC32, and compares before/after RAM fingerprints. It accepts a
short or padded final chunk, trimming to the reported total length. Reads are
limited to 4096 bytes per block: a 16-byte chunk and one-byte index cannot
address more without wrapping. No unproven paging extension is used.

`flydigi/gpa6_config.py` holds an advisory claim throughout capture, checks all
four fingerprints again after reading them, compares A1 state and format
versions, and verifies the full UUID. These checks detect observed changes;
they do not make the firmware reads atomic or exclude third-party writers.
The emulator's composition of the A1 CRC16s is a test assumption; the firmware's
exact checksum coverage has not been recovered.

Snapshots preserve each block's raw bytes and CRC32. Read-only inspectors add
v22 global settings, XMUL v1.8/v1.9 **header metadata only**, LGHT v2 settings,
and the known layout-2 grip entries. The remaining mappings, macros, TLVs and
trigger entries stay opaque. Unknown layouts and failed inner-format checks
produce a `decode_error` alongside the unmodified bytes. A verified transfer
therefore does not imply that its contents are understood or safe to restore.
Files are published atomically after capture and never replace an existing path.

`tests/test_apex6_config.py` covers boundaries, numbered/compact replies,
short/padded chunks, malformed echoes, corrupt data with valid packet checksums,
timeouts, profile/UUID/configuration changes, contention and file publication.
`tests/fixtures/apex6-config.json` contains synthetic inputs and outputs from the
pinned vendor JavaScript: all six lighting modes, both vibration states,
v1.8/v1.9 mapping headers, envelope checksums and global settings offsets.
`node tools/gen-apex6-config-fixtures` regenerates it from SHA-256-checked raw
research bundles without importing the Python implementation. Ordinary tests
need neither Node nor the ignored vendor files.

## Configuration format reference

This section consolidates the formats used by `flydigi/gpa6.py`,
`gpa6_config.py` and `gpa6_profile.py`. Layouts come from the pinned vendor
core/codec/contract above; the hardware findings below distinguish what was
actually exercised on Pro firmware 9.0.6.8. A recovered field or enum does not
by itself mean its physical behavior has been tested or that the editor exposes
it. Reserved and uninterpreted bytes must be preserved.

All offsets and enum values below are **decimal**, except numbers prefixed with
`0x` and byte strings in code blocks. `u8`, `u16` and `u32` mean unsigned integers;
multi-byte configuration values are little-endian. Offsets in an XMUL or LGHT
body start at its four-byte magic, excluding the optional outer envelope.

### Transport and configuration commands

The 32-byte GPA6 request body, excluding any HID report-ID prefix, is:

| Body offset | Size | Meaning |
|---|---|---|
| 0 | 2 | Magic `5a a5` |
| 2 | 1 | Command |
| 3 | 1 | Payload length + 2 |
| 4 | 0–27 | Payload, then zero padding through offset 30 |
| 31 | 1 | Sum of bytes 2–30, modulo 256 |

On the measured receiver descriptor, hidraw writes prepend `00` to this body;
input reports are unnumbered and arrive without that byte. Other report layouts
must be derived from the HID descriptor, not borrowed from Apex 5.

Normal success replies have prefix `5a a5 command 01 00`, then the command's
payload. A3/A4 use the vendor's v21 reply variant, whose payload starts directly
after `5a a5 command`. Payload length comes from the command format, not a
request-style length byte. Compact replies place the checksum immediately after
the payload; padded replies put it at offset 31. The checksum sums bytes from
offset 2 up to, but excluding, the checksum. Padding is zero. Normal error
replies use byte 3 = 0 and an error byte at body offset 5.

| Command | Request payload | Success payload |
|---|---|---|
| `0x07` | Empty | Global, mapping and LED protocol versions: three `u16`; observed `(22, 19, 10)` |
| `0xA0` | `fieldId:u8, encodedValue...` | `0, fieldId` |
| `0xA1` | Empty | `activeSlot:u8, slotCRC16[4]:u16, globalCRC16:u16` (11 bytes) |
| `0xA2` | `slot:u8` | Same state layout as A1 |
| `0xA3` query | `1, ramId` | `1, status, ramId, slot, length:u16, crc32:u32` |
| `0xA3` chunk | `0, ramId, chunkIndex, 16` | `0, status, ramId, slot, totalLength:u16, chunkIndex:u8, count:u8, data...` |
| `0xA7` | `1, slot` | `0` |

A3 status 0 means success. Chunk indices address 16-byte units; the final
chunk can be short or padded, so trim to the total length before checking CRC32.
The one-byte index limits this reader to 4096 bytes. Slots are 0–3 on the wire
and 1–4 in the CLI. The firmware's exact A1 CRC16 coverage remains unknown;
do not substitute those values for A3's full-block CRC32.

RAM IDs are **1 global, 4 lighting, 5 mapping, 6 motors**. A4 stages a complete
block and commits it to active RAM; the hardware findings below give every A4
request and ACK layout. A7 requests saving the current profile. A2 selects
resident profile RAM; edits made without A7 survive both same-slot selection
and switching away and back. Neither A4 nor A7 is a way to
write an Apex 5 profile blob.

### XMUL/LGHT outer envelope

The captured RAM 4/5 blocks have this envelope; the format readers also accept
bare bodies beginning with their magic:

| Block offset | Size | Meaning |
|---|---|---|
| 0 | 2 | Total block length, including this four-byte envelope |
| 2 | 2 | CRC16-CCITT of the body, polynomial `0x1021`, initial value `0xffff`, no final XOR |
| 4 | Remaining | XMUL or LGHT body |

A3/A4 CRC32 covers the **entire block**, including this envelope, using the
standard reflected IEEE CRC32 (`zlib.crc32`). These checksums are independent of
the GPA6 packet checksum and LGHT's inner sums.

### Global settings: RAM 1, format 22

The block is 82 bytes: a two-byte prefix and an 80-byte payload. The captured
prefix is `01 00`; it is **not** the XMUL/LGHT length/CRC envelope, and its
semantics are not established. The table's offsets start at the 80-byte payload;
add two for an offset in the full RAM block. A0 field IDs are independent of
those offsets. Values/ranges listed are what the current editor accepts.

| Payload offset | Size | Field | A0 field ID | Encoding / editor values |
|---|---|---|---|---|
| 3 | 1 | Sleep time | 32 | Minutes, integer 0–255; special meaning of zero not hardware-tested |
| 4 | 1 | Quick switching | 20 | Boolean, write 0/1 |
| 7 | 32 | Display name | Not exposed | UTF-8 up to first NUL; read only |
| 39 | 1 | Stick center auto-adjust | 25 | Boolean, write 0/1 |
| 40 | 1 | Local mapping | 23 | Boolean, write 0/1 |
| 41 | 2 | USB report rate | 33 | `u16` Hz: 125, 250, 500, 1000 |
| 43 | 1 | Stick resolution | 34 | 8–12 bits |
| 47 | 1 | Allow third-party access | 37 | Boolean, write 0/1 |
| 52 | 1 | XInput Home | 38 | Boolean, write 0/1 |

The pinned decoder treats boolean value 1 as true. Other bytes remain opaque.
A0 applies a field directly; it does not take an XMUL blob or require the
profile editor's A4/A7 sequence. Read back the whole global block and compare
all bytes, not just the requested setting.

### Mapping profiles: RAM 5, XMUL v1.8/v1.9

The mapping protocol version reported by command 7 is 18/19; the XMUL body's
version field is 8/9. These are different encodings of the version.

| Body offset | Size | Meaning |
|---|---|---|
| 0 | 4 | ASCII `XMUL` |
| 4 | 4 | Version `u32`: 8 or 9 |
| 8 | 1 | Slot count, exactly 1 |
| 9 | 3 | Reserved |
| 12 | 2 | Long-press threshold in milliseconds |
| 14 | 2 | Double-press threshold in milliseconds |
| 16 | 4 | Reserved |
| 20 | 1 | Internal slot ID, 0; not the currently selected hardware slot |
| 21 | 3 | Reserved |
| 24 | 1 | Layer count, 1–4 |
| 25 | 3 | Reserved |
| 28 | Variable | Layers in sequence, then optional TLVs |

The long/double-press thresholds are shared by every button and layer in this
profile. Each hardware profile can hold different values. They are not per-button
fields or controller-wide global settings. The captured defaults were 300/400 ms.
The vendor's mapping UI describes long press as activation after holding for the
configured duration, and double press as two presses within the time limit.
Our Apex 6 CLI edits these fields; our GUI does not expose them or timed activators.
No equivalent fields have been identified in the decoded Apex 5 profile format;
macro delays and repeat intervals are separate settings.

Each layer has an eight-byte header:

| Layer-relative offset | Size | Meaning |
|---|---|---|
| 0 | 1 | Layer ID, unique within the profile, 0–3 |
| 1 | 1 | Reserved |
| 2 | 2 | Number of source-button mapping entries |
| 4 | 2 | Number of macro definitions |
| 6 | 2 | Reserved |

Following that header, sections appear in this exact order: mapping entries,
macro definitions, left trigger zones, right trigger zones, left stick zones,
right stick zones. Their variable lengths determine where the next layer starts.

Each **mapping entry** starts with `source:u8, activatorCount:u8, reserved:u16`,
followed by `activatorCount` five-byte records: `activatorType:u8, target[4]`.
The vendor limit is five activators per source. Activator types are simple (0),
timing-single (1), timing-long (2), timing-double (3), pulse-press (4),
pulse-release (5) and toggle (6). The current CLI writes only simple activators.

The supported source and gamepad-target IDs are:

| ID | Button | ID | Button |
|---|---|---|---|
| 0 | A | 11 | D-pad down |
| 1 | B | 12 | D-pad left |
| 2 | X | 13 | D-pad right |
| 3 | Y | 14 | C |
| 4 | LB | 15 | Z |
| 5 | RB | 16 | M1 |
| 6 | Back | 17 | M2 |
| 7 | Start | 18 | M3 |
| 8 | Left stick click | 19 | M4 |
| 9 | Right stick click | 20 | M5 |
| 10 | D-pad up | 21 | M6 |

A gamepad target is `[0, buttonId, 0, 0]`. Other four-byte targets are kept
verbatim by this editor; it does not convert them to simple targets unless
that source is explicitly remapped. For example, a complete M1-to-B entry is:

```text
10 01 00 00  00 00 01 00 00
source/count  simple + gamepad B
```

Removing an override removes the whole entry and decrements the layer's entry
count. Replacing an entry replaces all of its activators. Other source entries,
layer headers and the following sections retain their original bytes.

Each **macro definition** has `macroId:u8, triggerMode:u8, loopIntervalMs:u16,
actionCount:u8, reserved:u8`, followed by `actionCount` seven-byte actions:
`actionType:u8, target[4], timestampMs:u16`. Action types are press (0) and
release (1). Trigger modes are press-once (0), immediate-loop (1), wait-loop (2)
and press-hold (3). Macro definitions are traversed and preserved, not edited.

Each **trigger-zone section** starts with a presence byte. Zero ends the section;
one is followed by `count:u8` and `count` six-byte records: `start:u8, stop:u8,
target[4]`. The recovered limit is four zones per trigger.

Each **stick-zone section** also starts with a presence byte. When present,
it contains `angleMode:u8, count:u8` followed by **eight fixed seven-byte records**,
including padding for unused zones. Each record is `start:u8, stop:u8, direction:u8,
target[4]`. Count is at most eight. Angle modes are 4-way (0), 8-way (1) and
overlapping 4-way (2). Directions are up/down/left/right (0–3), then
up-left/up-right/down-left/down-right (4–7). Do not skip only `count * 7` bytes:
the reserved records still occupy space.

### XMUL extension tail

After all layers, optional records use `type:u8, length:u8, value[length]`.
The terminator is `ff 00`. A body with no tail is also valid; the vendor's v1.8
encoder omits it when no extensions are present. Once a tail is present, this
editor requires a complete terminator and no trailing bytes. Unknown TLV types
are preserved if structurally valid.

| Type | Value length | Recovered meaning |
|---|---|---|
| 1, 2 | 12 | Left/right stick curve plus flags |
| 3, 4 | 11 | Left/right trigger curve |
| 5 | 24 | Gyroscope configuration |
| 6, 7 | 12 | Left/right trigger vibration curve, prefixed by a byte |
| 8, 9 | 12 | Left/right grip vibration flags and curve |
| 32 | Variable | v1.9 profile and macro names |
| 255 | 0 | End |

The common 11-byte curve is `startX, startY, endX, endY, midpointCount,
midpoint1X, midpoint1Y, midpoint2X, midpoint2Y, reserved[2]`, all bytes, with at
most two midpoints. Stick flags follow the curve: invert-X bit 0, invert-Y bit 1,
round bit 2. Grip vibration flags precede the curve, with enabled at bit 0.
These layouts are vendor-derived; the current profile editor preserves the
entire TLV tail rather than decoding or rewriting these settings.

Type 32 contains `nameLength:u8, UTF8Name[nameLength], namedMacroCount:u8`,
then records `layerId:u8, macroId:u8, nameLength:u8, UTF8Name[nameLength]`.
The whole value must fit the one-byte TLV length. v1.8 has no such name metadata.

### Lighting: RAM 4, LGHT v2

Command 7's LED protocol version (observed 10) is separate from the LGHT
body's version field (2), just as the mapping protocol and body versions differ.

| Body offset | Size | Meaning |
|---|---|---|
| 0 | 4 | ASCII `LGHT` |
| 4 | 2 | Version `u16`, exactly 2 |
| 6 | 4 | Payload length `L:u32` |
| 10 | 2 | Sum of bytes 0–9, modulo 65536 |
| 12 | L | Mode/configuration payload |
| 12 + L | 2 | Sum of the payload bytes, modulo 65536 |

Total bare-body size is `14 + L`. The payload begins with a mode byte:

| Mode | Meaning | Bytes after the mode, before optional vibration flag |
|---|---|---|
| 0 | Off | None |
| 1 | Solid | `R, G, B, brightness` |
| 2 | Breathing | `brightness, speed, colorCount`, then RGB triples |
| 3 | Gradient | `brightness, speed, colorCount`, then RGB triples |
| 4 | Streaming | `brightness, speed` |
| 5 | Default | `brightness, speed` |

An optional final byte is the vibration flag (0/1); absent means false.
The encoder emits it explicitly. RGB channels are 0–255; the editor accepts
brightness/speed 0–100 and one to eight colors for modes 2/3. Other trailing
bytes or unknown modes are refused for editing. Full encoded outputs for all
six modes, with both vibration states, are compared with independent vendor
fixtures in `tests/fixtures/apex6-config.json`.

## Apex 6 Pro hardware findings, 2026-11-07

The user confirmed the controller was connected through its receiver, with no
USB cable attached to the controller. The original "wired" label was an incorrect
reuse of Apex 5's connection enum. GPA6 mode 1 means receiver. A subsequent
direct-cable check with the receiver unplugged confirmed mode 0 means wired.
The UUID stayed identical and all configuration blocks matched across transports.
Unverified mode values remain unknown rather than inheriting the legacy enum.

The tested device reports SKU 150, USB `37d7:2502`, firmware `9.0.6.8`, format
versions `(22, 19, 10)` and feature bits `0x9f`. Its vendor collection uses
unnumbered 32-byte input/output reports: hidraw writes need a leading zero;
reads do not. Battery level is on a 0–10 scale, unlike Apex 5's 0–5 scale.
The device list now keeps each model's scale separately.

Captured descriptor, identity replies and initial RAM blocks are in
`tests/fixtures/apex6-hardware.json`. Local traces and before/after snapshots
are in ignored `work/apex6-hardware-20261107/` and
`work/apex6-acceptance-20261107/`.

Two assumptions in the first implementation were wrong: RAM 4 is LGHT lighting
and RAM 5 is XMUL mapping; and identical control requests within 100 ms are
silently suppressed. Repeated queries at 0/5/20/50 ms timed out; 100/200 ms
succeeded. The transport now spaces only identical GPA6 control requests by
100 ms. Legacy packets and the 8 ms waveform stream are unaffected. This also
fixes consecutive UUID reads when opening a UUID-selected controller.

The new editor implements the following recovered transactions:

| Command | Observed behavior |
|---|---|
| A0 | Global field/value write; ACK `[0, fieldId]`. Sleep time changed and restored, with all other global bytes preserved. |
| A2 | Select slot 0–3; 11-byte state ACK. Unsaved RAM survives both same-slot selection and switching away/back. |
| A4 begin | Request `[0, ramId, length:u16le, crc32:u32le]`; 10-byte v21 ACK `[op, status, ramId, slot, length:u16le, crc32:u32le]`. |
| A4 chunk | Request `[1, byteOffset:u16le, count:u8, data...]`, at most 16 data bytes. ACK length is the cumulative offset. Repeating the last chunk is idempotent. |
| A4 commit | Request `[2, length:u16le, crc32:u32le]`; ACK echoes length and CRC32. Full RAM read-back verifies the result. |
| A4 abort | Request `[3]`; successful ACK starts `[3, 0]`. Remaining target fields are not used. |
| A7 | Request saving the active profile with `[1, slot]`; ACK payload `[0]`. A modified 600 ms long-press timing survived A2 selection, which alone does not prove persistence. |

One mapping chunk initially timed out. The editor now retries a timed-out
offset-addressed chunk up to three attempts; begin and commit are not replayed.
A separate hardware probe confirmed that a duplicate chunk does not append
data twice. Six mapping edit/restore round trips then passed. The physical
M1-to-B check produced Linux `BTN_EAST` (305) press/release events from the
upper-right rear paddle. Solid lighting edits and restoration also read back
correctly. The timing edit was restored, saved and the slot reselected; all original
RAM blocks matched the starting snapshot afterward.

`gpa6_profile.py` edits XMUL v1.8/v1.9 mappings without rebuilding unrelated
sections: other layers, macros, trigger/stick zones, curves, metadata and
reserved bytes are preserved. `map` replaces one source's activators with one
simple gamepad target, or removes the override with `default`. Timing changes
touch only the two u16 fields and enclosing checksum. The fixture generator
also executes the independent vendor encoder on complex two-layer profiles
with macros and zones; tests compare complete edited bytes for both versions.

Writes are restricted to global fields and RAM 4/5. UUID/model/format checks,
slot fingerprints and advisory claims prevent common targeting mistakes.
After an ambiguous error, rollback is attempted only if the same device/slot
still contains our exact candidate bytes. An external edit is not overwritten.
A7 is explicit and addresses the current profile as a whole, but **A4 edits can
already persist without it**: the user restarted the controller and receiver,
and an A-to-B edit made without A7 was still present. Exact flash-write timing
is not established. The fake pad conservatively models persistence at A4 commit;
it no longer promises volatile previews or discard-on-selection. Lost A7 ACKs
are reported; read-back alone would not establish exact flash behavior.
Unplugging or a third-party writer can still prevent restoration.

All eight implemented global fields passed temporary write/read-back/restoration
on hardware, including all supported report rates and stick resolutions. FN+B
selected profile 2 with quick switching enabled and stayed on profile 1 when
disabled, confirmed both by A1 and the built-in screen. With XInput Home off,
two Logo/Home taps produced no guide events; with it on, two `BTN_MODE` (316)
press/release pairs arrived over direct USB. A one-minute sleep timer put the
wireless controller to sleep: the user observed its disappearance from the
system while the receiver remained plugged in, and USB inventory showed no
Flydigi device at that point. Waking it restored the same UUID; the original
15-minute timer and every active configuration block were then restored.
Local mapping and center auto-adjust have only been verified by
write/read-back/restoration, accepted for settings support without further
physical checks. Third-party permission was subsequently tested as described
below. Snapshot import, macro/curve editing, motor
configuration editing and GUI configuration remain outside this implementation.

### Third-party takeover permission

A receiver-connected test on the same firmware exercised the cooperative
request used by SDL and the older SDK: GPA6 command `0x1c`, a one-byte
acquire/release flag followed by a 20-byte ASCII owner tag. The new descriptor's
framing and checksum were used. Command `0x10` status has active at payload byte
0 and permission at byte 4, matching the web client's decoder. These bytes must
not be interpreted as the legacy Apex 5 transport flags.

| Operation | Observed result |
|---|---|
| Acquire with permission off | Grant byte 0; status inactive |
| Enable permission, then acquire | Grant byte 1; status active |
| Release while permission stays on | Grant byte 1; status inactive |
| Acquire again, then disable permission | Status immediately inactive with permission off |

Acquisition also started unsolicited `0xef` input reports; release/revocation
stopped them apart from a pending report during release. Their input/motion
layout was not decoded by this check. The owner name was not echoed in status.
Only one requesting client was exercised: owner arbitration and timeout expiry
remain untested. The official browser UI's response was not exercised, although
its inspected code uses the same active/allowed status bits.

The original permission was restored to off. All configuration blocks and the
active slot matched the pre-test snapshot, and final takeover status was inactive.
`tests/fixtures/apex6-takeover.json` holds captured request/reply bytes and status
payloads. The fake pad models the measured permission/grant/release/revocation
sequence; it does not model competing owners, expiry or raw input streaming.

### Remaining acceptance observations

The one-second left-grip test at 80 Hz/amplitude 0.12 was felt by the user;
the controller was quiet afterward. Neutral/disable succeeded, but restoring
mode 1 on targets `0x10`/`0x11` returned error 1 with both tested parameter
lengths. Motor RAM stayed unchanged. A later direct-USB sequence separately
confirmed left-only, right-only and both grips, with quiet pauses and a quiet
ending. Both mode-1 restore requests still returned error 1. Ordinary Linux
`FF_RUMBLE` pulses were felt and stopped before and after that sequence, so the
restore errors did not prevent ordinary rumble in this test. Exact live motor
mode restoration remains unresolved. The default emulator accepts restoration;
a regression now injects both observed refusals and checks that disable still
occurs, both errors are reported, the claim is released and no profile write or
selection is used to hide the failure.

During guided acceptance testing, two reads went unanswered around profile
changes. RAM queries/chunks now retry unanswered A3 requests up to three attempts,
while retaining echo, fingerprint and CRC checks. Profile selection retries
only its A1 verification read, never A2. Malformed or refused replies are not
retried. Fake-pad tests cover recovery and bounded failure.

Physical input checks confirmed A-to-B on both receiver and direct USB, then
A-to-X after an explicit A7 save and profile switches, and A-to-A after removing
the override and saving. All three presses and releases in each phase arrived
with the expected Linux button code. Original mapping and timing bytes were
restored. Direct-USB stick sweeps produced 255 distinct values per axis and
256-unit steps at 8-bit precision, versus thousands of values at 12 bits.
All supported report-rate values were accepted and read back correctly. This
satisfies the agreed acceptance criterion for exposing the setting; it is not
a blocker for settings support. The 125 Hz setting did not change the observed
approximately 1 ms evdev intervals in that run. What the firmware controls with
this field remains undetermined: radio-link cadence and battery savings are
unverified hypotheses. Separate power-cycle persistence of a changed report
rate was not tested.

All six lighting modes were checked visually: solid red/green/blue, off, blue
breathing, red/blue gradient, streaming rainbow and default (visually the same
rainbow as streaming). Brightness 5 to 70 visibly increased intensity. Holding
breathing speed 5 produced an approximately 8–10 second rise from dark to bright;
speed 95 took about one second. With vibration response off, ordinary rumble left
solid blue steady. With it on, the user saw a brief brightness increase, without
the motor-dependent spatial response or dimming they recalled from Apex 5.
This establishes a visible response, not the complete lighting algorithm.

The final audit compared mapping, lighting and motor blocks in all four profiles
with their pre-test backups, then returned to profile 1. All matched. The active
profile and global block also matched the original snapshot byte for byte.

Regression verification at the acceptance commit: all 22 backend test scripts
passed. The subsequent takeover addition passed all four Apex 6 suites: 43
protocol/lifecycle, 28 snapshot, 23 captured-wire/editing and 27 USB DualSense
integration tests. The earlier GUI suites passed 762 model checks, 131 window
checks and 138 QML tests (none skipped). New tests explicitly
refuse Apex 5 in the GPA6 editors and leave legacy transport pacing unchanged.
An Apex 5 was not physically attached during this session; its preservation
claim is based on the existing software regressions, not a new hardware run.
