# Haptic audio on a virtual DualSense

A game writes PS5 haptics to a DualSense that is a userspace process, and the Apex 5's motors
reproduce them.

Index: [PROGRESS.md](../PROGRESS.md).

The relay is `tools/flydigi-ds5-usbip`, started as root:

    sudo tools/flydigi-ds5-usbip --haptics --motors

| file | what it holds |
| --- | --- |
| `flydigi/usbip.py` | the USB/IP transport and the vhci attach; no dependencies |
| `flydigi/ds5_usbip.py` | the DualSense device served over that transport |
| `flydigi/ds5_usb.py` | descriptors and feature reports captured off real hardware |
| `flydigi/haptics.py` | the haptic-audio DSP |
| `flydigi/dsmode.py` | the DualSense-mode switch: the relay's command line, log and privilege drop |

## What the virtual DualSense delivers

Adaptive triggers **work in game**. The transcribed mapping behaves exactly like Flydigi's: the game
sent `type=0x25 p[0]=12` → `mode 3 [70,0,12,0,0]` (right trigger) and `type=0x21 p[1]=3` →
`mode 1 [140,1,0,0,0]` (left trigger), both matching their table's branches. Zero unmapped patterns.
Parameter blocks are logged padded to five elements (`_pad5` in `flydigi/relay.py`).

- **Rumble: both paths.** The HID output report's `motor_left`/`motor_right`, and the PS5 haptic
  audio stream — see *Rumble and haptic audio* below.
- **Gyro/accel: implemented.** `flydigi/motion.py`. The vendor input stream (command 17, "raw data
  transport in") carries the IMU at ~295 Hz over the dongle and ~970 Hz wired, and enabling it does
  **not** disturb the xpad node, so sticks and buttons still come from evdev. Command 17 carries
  five transport flags — `controller_data`, `raw`, `keyboard`, `mouse`, `third_party`, each 1 (on),
  0 (off) or 0xFF (leave alone). Input report id `0x04` with marker byte `0xEF`: sticks at offsets
  4/6/8/10, gyro at 18/20/22, accel at 24/26/28. Offsets follow `OperatorDataParser` for
  `NewXInput`, shifted by one because the report-id byte is kept. Accelerometer is scaled by 2.441:
  the pad reports ~4096/g while the advertised DualSense calibration implies 10000/g — verified by
  the pad reading exactly 1.00 g flat. Gyro scale is left at 1.0 and is the one value worth tuning
  by feel (`--gyro-scale`). M1-M6 are in the same report and nothing parses them — see *M1-M6
  buttons* below.
- **Battery: implemented.** Command 1 returns device type, connection type and battery; polled
  every 30 s. The level is the low nibble of byte 12, with the high nibble flagging charging; levels
  run 0..5 (`motion.MAX_LEVEL`) and 6 is Flydigi's charging sentinel. The relay maps it onto the
  DualSense's 0-10 scale as `min(10, level * 2)`, and reports `BATTERY_CHARGING` with charge 10
  while charging. `ds5.BATTERY_FULL` is 0x02; 0x01 is charging.
- **The digital L2/R2 buttons are synthesised.** xpad exposes no `BTN_TL2`/`BTN_TR2`, so
  `relay.build_state` presses them from the analog axes past `TRIGGER_DIGITAL_THRESHOLD`, 0.12 of
  full travel; the analog values themselves are the whole 0..255 range.

## Rumble and haptic audio

The DualSense has no conventional rumble motors; its voice coils do both jobs. Games can drive them
two ways: `motor_left`/`motor_right` in the HID output report (the compatibility path, which most PC
ports use and both relays implement), or arbitrary waveforms written to the controller's USB *audio*
device (the rich PS5 haptics). Deathloop uses the audio path. With the real Apex 5 as a
plain Xbox pad the game vibrates readily, even on a menu button press — so it does emit motor
rumble, just not to a DualSense. A direct cmd `0x12` rumbles the pad and ACKs.

Deathloop *does* drive DualSense haptics on PC, and it works under Proton — verified with a real
DualSense connected over USB (haptic audio needs wired USB; over Bluetooth the endpoint does not
exist). The game opens a **dedicated second stream** to the controller's audio device alongside its
normal game audio, so this is real haptic output rather than misrouted sound.

**On the uhid tier (4) this cannot be delivered**: a uhid node has no USB parent, so no audio
endpoint can ever share its container id. On the USB/IP tier (4b) it works, because the virtual pad
*is* a USB device with an audio interface beside its HID one.

### DualSense audio channel map

    ch0  headphone jack        ch2  left haptic actuator
    ch1  speaker               ch3  right haptic actuator

`tools/ds5-channel-probe` plays an audible tone into each channel in turn: `--freq` (default 70 Hz),
`--channel N` for one channel only, `--amp` (default 0.7), `--seconds` (default 2.0). It declares
`--channel-map=front-left,front-right,rear-left,rear-right` so the tone lands where intended.
`tools/haptics-inspect` measures per-channel energy from a live capture of the sink's monitor,
averaged over `--interval` (0.4 s by default); `--streams` also names the applications connected to
that sink as they appear.

With no `--sink`, `tools/ds5-channel-probe`, `tools/haptics-inspect` and `tools/flydigi-haptics`
each take the first sink whose name contains the literal `DualSense`, matched case-sensitively
against `pactl list short sinks`. A sink named anything else — including `dualsense_haptics` — has to
be passed explicitly.

Measured at the device itself, both actuator channels carry signal and track each other closely,
with ch2 often slightly stronger, while ch0 and ch1 stay at *exactly* zero. Silence to the last bit
on those two is also the check that the channel offsets are aligned; misalignment smears energy
across all four.

The same game measured through a real DualSense's PipeWire monitor instead reads as **ch3 only** —
active in 87% of 373 sampled windows, ch1 never touched. `--haptics` reports activity fractions in
those terms; the device-side reading is the direct one.

### Conversion to Apex 5 motors

`flydigi/haptics.py` is the DSP both the bridge and the virtual device feed; it has an
s16/decimating front end for the latter. Constants: `RATE` 48000, `CHANNELS` 4, `CROSSOVER_HZ`
150.0, `GATE` 0.015, `CURVE` 0.7, `DECIMATE` 8, `SILENCE` 5e-5, `HAPTIC_CHANNELS = (2, 3)`.

The DualSense's actuators are full-range voice coils, but the Apex 5's motors are not
interchangeable — left is a large low-frequency mass, right a small high-frequency one. Mapping
left-to-left would throw away the character of the waveform, so the signal is split by frequency
instead: low band drives the left motor, high band the right. Confirmed working against live game
haptics.

Three things dominate latency; without all three the rumble lags and keeps running after an effect
ends:
  * `effects.rumble()` waits 100 ms for an ACK on every update. Pass `wait=0.0` when driving
    continuously — the ACK carries nothing useful.
  * `parec` buffers generously by default; ask for `--latency-msec`.
  * Drop stale audio rather than working through the backlog. The relay's haptic queue is bounded
    at 8 and discards the oldest.

An ERM motor has no perceptual floor where the DualSense's voice coils do: below a threshold it does
not spin, it just whines. So a noise gate is applied (`GATE = 0.015`, `--gate`) and the level is
**rescaled above it** rather than merely clipped, or everything quiet lands in a dead band. The
shaping exponent is `CURVE = 0.7` (`--curve`); 0.5, a square root, lifts quiet content hard enough
to make the pad feel markedly more sensitive than the DualSense it is imitating.

All four knobs exist on both tools. `tools/flydigi-ds5-usbip` already defaults to the useful values
— `--gain 1.5 --crossover 250 --gate 0.015 --curve 0.7`. `tools/flydigi-haptics` defaults to
`--gain 3.0 --crossover 150` (the library's `CROSSOVER_HZ`) and needs them passed.

**Ruled out:** deriving rumble from the *game's own main audio output*. It fires on music and
dialogue and does not resemble haptics.

### The bridge from a real DualSense

`tools/flydigi-haptics` captures a real DualSense's audio sink monitor and drives the Apex 5's
motors from it. A game writes to the real DualSense's audio sibling because their ContainerIds
match; which pad the player is actually holding never enters into it. So this bridge needs a real
DualSense present as the source. Its conversion is what the virtual device feeds. Besides the four
DSP knobs it takes `--sink`, `--quiet`, `--dump` (print levels and drive nothing, which is also what
it falls back to when no Apex 5 is found) and `--watch` (report applications as they connect to the
sink).

`tools/haptics-simulate` plays synthetic gunshots and engine rumble into the sink and the bridge
produces correctly decaying motor values; `--channels` plays its channel-identification sequence
instead. Its `tone()`/`sequence()` play into ch0/ch1 — headphone and speaker — which the DSP
ignores, so they produce no motor output until moved onto channels 2 and 3. It passes no
`--channel-map` either, so its channel indices are whatever PipeWire remixed them to;
`tools/ds5-channel-probe` is the probe that declares the map. It also plays into a sink named
literally `dualsense_haptics` and takes no `--sink`, while `pipewire/99-dualsense-haptics.conf`
names its node after the real device — the two do not match.

Two capture-side facts: `pw-record` prepends a file header and will silently misalign a raw reader
(use `parec --raw`), and neither `paplay --raw` nor `parec --raw` declares a channel map, so PipeWire
remixes the channels. Pass `--channel-map=front-left,front-right,rear-left,rear-right` or the ch0-3
readings are whatever the remix produced; `tools/haptics-inspect` does not pass it.

### The null sink: negative result

Neither Flydigi nor DSX implements audio haptics — verified by decompilation: Space Station bundles
no audio libraries at all, and its `EnableAudio` is command 19, subcommand 10, with a single enable
byte on NewXInput (command 162 on XInput, 250 on DInput) — a device feature toggle, not PC audio
capture.

`tools/flydigi-haptics` plus a fake 4-channel DualSense sink (`pipewire/99-dualsense-haptics.conf`)
measure haptic-channel energy and convert it to motor rumble. **A game that matches by container id
does not use it.** With the sink present and named "Wireless Controller", Deathloop opened exactly
one audio stream and routed it to the speakers; the sink measured absolute silence (peak 0.00000).
The link such a game follows is a ContainerId shared between the HID device and the audio endpoint —
see *How Proton matches audio to a gamepad* below — and a uhid node and an unrelated PipeWire sink
can never share one. Games that match the audio endpoint by *name* are a separate path, listed
there. Nothing in the repo installs `pipewire/99-dualsense-haptics.conf`:
`tools/apex5-setup install-rules` copies the udev rules only, and no tool names a destination for
it.

### Prior art

  * **`DualSense-haptic-helper`** (MIT) — real hardware; independently found haptics on channels
    2 and 3 of a 4.0 stream, matching the tone probing above. Warns that **Steam Input masks the
    DualSense as an Xbox pad and breaks 4-channel audio**, so it must be disabled.
  * **`Haptic-Feedback-Linux`** and **`xzn/proton-ds5-haptic`** — Wine/Proton patches enabling DS5
    haptics, plus a udev rule setting `SOUND_DESCRIPTION="Wireless Controller"`.
  * **GE-Proton 11-2** and **proton-cachyos** now ship wired PS5 haptics natively for real
    controllers. A WirePlumber rule may be needed to stop PipeWire collapsing the DS5 node to mono.

## Virtual USB composite device

One USB composite device exposing both interfaces gives the kernel the hidraw node and the ALSA card
from the same device — VID:PID `054c:0ce6`, HID and audio as sibling interfaces. That is the
association a null sink cannot have.

### What the real DualSense is

Descriptors read off the hardware (`bcdDevice 0100`):

    iface 0     Audio Control      bcdADC 1.00   <- UAC *1*, not UAC2
    iface 1/1   Audio Streaming    4ch s16le 48000, EP 0x01 OUT iso ADAPTIVE, 392 B, 1 ms
    iface 2/1   Audio Streaming    2ch s16le 48000, EP 0x82 IN  iso ASYNC,    196 B, 1 ms  (mic)
    iface 3     HID                EP 0x84 IN + EP 0x03 OUT, interrupt, 64 B
    self-powered, 500 mA, iSerial 0 (no serial string)

**It is USB Audio Class 1**, so the module needed is `usb_f_uac1`, not `usb_f_uac2`. Fedora ships
neither.

**The playback endpoint is Adaptive, with no explicit feedback endpoint.** So the whole device needs
only four endpoints, and three if the mic is dropped. That matters on any controller with a small
endpoint budget.

Channel config is `0x0033` = FL FR RL RR, consistent with the tone probing above: the haptic
actuators are the RL/RR pair, ch2 and ch3.

**The HID report descriptor is 289 bytes, captured from hardware.** Use the real one: a host
compares against it, and the widely-copied inputtino descriptor is 273 bytes from older firmware —
identical for 145 bytes, then divergent, because it lacks feature reports `0x0B` and `0x0C`
(usages `0x41`/`0x42`, 41 bytes each). `flydigi/ds5_usb.py` holds the capture and both tiers use it.

Feature reports served, with the lengths the report descriptor declares: `0x05` calibration (40 B),
`0x09` pairing info/MAC (19 B), `0x0B` (41 B), `0x20` firmware build string (63 B). The generator
also asks for `0x0C` (41 B); no `0x0C` is present in the committed blobs. String descriptors are
served for LANGID `0x0409`: "Sony Interactive Entertainment" (index 1) and "DualSense Wireless
Controller" (index 2).

Re-capture goes through `tools/gen_ds5_usb.py`, with a real DualSense attached on hidraw:

    mkdir -p work/ds5-usb
    cp /sys/bus/usb/devices/<n>/descriptors            work/ds5-usb/descriptors.bin
    cp /sys/bus/hid/devices/0003:054C:0CE6.*/report_descriptor  work/ds5-usb/report_descriptor.bin
    tools/gen_ds5_usb.py            # reads the feature reports from the live device

Find `<n>` with `grep -l 054c /sys/bus/usb/devices/*/idVendor`. The two `.bin` files are the only
inputs taken from sysfs — the **feature reports are read from the attached device**, so the pad has
to be connected at generation time, not merely have been once.

`tools/ds5-dump-features` re-reads a live DualSense's feature reports and diffs them against the
committed blobs. It writes nothing to the device: HIDIOCGFEATURE is a read. Bluetooth addresses are
expected to differ, because they are deliberately replaced.

### How Proton matches audio to a gamepad

Read out of the Proton source. **Two strings are computed independently, and mean different things.**

**1. The MMDevice instance id** — `USB\VID_054C&PID_0CE6\...` versus `{1}.ROOT\MEDIA\NNNN`. In
`winepulse.drv` this is **not** derived from sysfs. `get_device_path()` switches on `bus_type`,
which `fill_device_info()` sets from the **PulseAudio proplist**: `device.bus` must be literally
`"usb"`, plus `device.vendor.id` and `device.product.id`. Anything else falls through to
`ROOT\MEDIA\%04u`. Sysfs parsing exists only in **winealsa**, which Proton does not use — so this
string comes from neither sysfs nor setupapi. The ContainerId below is the one that reaches sysfs.

**2. ContainerId** — `DEVPKEY_Device_ContainerId`, and *this* is the HID↔audio join. It is
**Proton-only**; upstream Wine has never merged it. `winepulse.drv` takes the `sysfs.path` proplist
value, resolves it through udev, walks up to the `usb_device` ancestor, and builds a GUID from
`(vid, pid, busnum, devnum, usec_initialized)`. `winebus.sys` computes **the identical GUID by the
identical formula** from the hidraw device's USB parent. Nothing looks anything up: the association
is value equality of two GUIDs each side derives independently from the same physical `usb_device`.

Shipped in **Proton 10.0-4** ("Fixed haptics support for DualSense controllers"). Relevant commits
in `ValveSoftware/wine`: `e179606` (winepulse container id from udev) and `961d16f` (winebus bus
specific container ids).

**Why the null sink failed.** Not the sysfs path — `device.bus` was not seen as `"usb"` by
winepulse. The sink config already declares `device.bus`, `device.vendor.id` and `device.product.id`
in `node.props` (`pipewire/99-dualsense-haptics.conf`); those node properties do not reach the
PulseAudio-compat proplist that `fill_device_info()` reads. `alsa.components` and the node
name/description are never consulted for identity at all.

**Why uhid can never close this.** A uhid node's parent chain is `/devices/virtual/misc/uhid/...`,
so `udev_device_get_parent_with_subsystem_devtype(dev, "usb", "usb_device")` returns NULL, `winebus`
logs "Failed to get parent device", and falls back to `make_unique_container_id()` — a **random
GUID, re-rolled every run**. A uhid DualSense cannot match any audio endpoint, ever, by
construction.

**Not every game needs this.** Three observed behaviours: match the audio endpoint by *name*
containing "Wireless Controller" (FF14, FF7R — these tolerate a fake); read the HID device's
container id and take the MMDevice with the same one (**Deathloop**, Ghostwire); or container id
plus SetupDi enumeration, which additionally needs the `DeviceContainers` registry database that
only the xzn patches populate.

**Deathloop, on the middle path, works here on stock Proton with a real DualSense** — no GE-Proton,
no `compatibilitytools.d`, no per-game compat override, so no patched Proton is required.

Wine [bug 59557](https://bugs.winehq.org/show_bug.cgi?id=59557) reports DualSense speaker and
haptics broken since Wine 11.4, from a commit that hardcoded `DEVPKEY_Device_DeviceDesc`. Untriaged,
and not affecting the current default Proton here.

`get_container_id()` never checks that `sysfs.path` belongs to the sink that declared it — it
resolves the string and walks up, so a virtual PipeWire node pointed at a *real* USB pad's sysfs
subtree inherits that pad's container id with no patching. Something must still enumerate as
`054C:0CE6` on the HID side, so this cannot replace the real DualSense, only silence its actuators.

This is not Linux-specific: a virtual audio device on Windows needs an audio driver, and DSX ships
a virtual gamepad bus driver rather than one — consistent with DSX's virtual pad also failing to
produce haptics in Death Stranding DC.

Unbinding `hid-playstation` from a real DualSense:

    echo -n "0003:054C:0CE6.00XX" | sudo tee /sys/bus/hid/drivers/playstation/unbind

removes the hidraw node but leaves the USB device and its interfaces intact, so the audio side keeps
the same sysfs path, instance id and ContainerId; the pad simply stops being a gamepad.

### What Fedora ships

Fedora turns off every UAC gadget function, and `raw_gadget` with them:

    CONFIG_USB_DUMMY_HCD=m                          present, but see below
    CONFIG_USBIP_VHCI_HCD=m                         present — this is the one that matters
    CONFIG_USB_CONFIGFS_F_FS=y                      present
    CONFIG_USB_CONFIGFS_F_UAC1        is not set
    CONFIG_USB_CONFIGFS_F_UAC1_LEGACY is not set
    CONFIG_USB_CONFIGFS_F_UAC2        is not set
    CONFIG_USB_RAW_GADGET             is not set

There is also no real UDC here: nothing registers one, so `/sys/class/udc` does not exist; the
machine is USB host-only.

### Why no software UDC can carry the audio interface

`dummy_hcd` loops a virtual UDC to a virtual HCD on one machine, so a gadget bound to it enumerates
as an ordinary USB device in that machine's own sysfs, HID and audio as siblings. The topology is
right and it still fails, on isochronous.

**`dummy_hcd` declares no isochronous endpoint.** Alan Stern removed them in v4.15 (commit
`c9f20aafc939`, "USB: dummy-hcd: remove unsupported isochronous endpoints"), out of a thread where
the UVC gadget hit exactly this wall. `dummy_hcd.ko`'s endpoint names are all bulk or interrupt —
`ep1in-bulk`, `ep5in-int`, `ep-aout` … — with no isochronous entry. (`strings` on the shipped Fedora
binary also emits `-iso` and `type_iso`: those are the URB-type debug format and the `usb_ep_caps`
field name, not endpoint names, and `isoch_delay` is a SuperSpeed descriptor field.) So
`usb_gadget_ep_match_desc()` rejects an iso descriptor, `usb_ep_autoconfig()` returns NULL, and the
gadget refuses to bind at all. The host half fails every iso URB with `-EINVAL` besides. Michael
Grzeschik proposed re-enabling iso in 2022; it was rejected for not emulating real hardware.

Debian, Arch, CachyOS, SteamOS and Nobara all ship `usb_f_uac1` (Fedora and Bazzite do not) — and on
all of them it still cannot bind to `dummy_udc.0`.

**`usbip-vudc` is the other shipped soft UDC, and it is also out.** It advertises iso-capable
endpoints, so unlike `dummy_hcd` a gadget binds and enumerates — then `vudc_transfer.c` sets
`urb->status = -EXDEV` under a bare `/* TODO: support */`, so no audio ever arrives.

**FunctionFS cannot express UAC descriptors at all.** `ffs_do_single_desc()` validates against a
closed whitelist of `bDescriptorType`; UAC1 needs `CS_INTERFACE` (0x24) and `CS_ENDPOINT` (0x25),
neither of which has a case, so the descriptor blob write to ep0 fails with `EINVAL`. Each
class-specific type has had to be added by explicit kernel patch (CCID got one in 2018); none has
ever been proposed for audio. FunctionFS *does* support isochronous — it has since v2.6.36 — but
that never becomes relevant. `raw_gadget` would sidestep the whitelist entirely, and Fedora does not
ship it, and it would still need an iso-capable UDC underneath.

Any gadget-stack solution needs *both* a UAC function driver *and* an isochronous-capable UDC, and
no distro ships the latter in software.

## Virtual DualSense over USB/IP

This route has **no UDC in it at all**. Rather than build a gadget, fabricate the device in a
userspace process speaking the USB/IP protocol, and let `vhci-hcd` — the client-side virtual *host*
controller — enumerate it locally. `vhci-hcd` implements isochronous fully, and it is `=m` on every
distro checked without exception. None of the three blockers above applies: no descriptor whitelist,
no endpoint autoconfiguration, no gadget bind.

vhci is driven entirely through `/sys/devices/platform/vhci_hcd.0`: `attach`, `detach` and `status`.
A port is free when its `sta` column reads `004` (VDEV_ST_NULL). High-speed and super-speed ports
are separate ranges — a high-speed device will not attach to an `ss` port. Whether the kernel *has*
the module is read out of `/lib/modules/<release>/modules.dep` rather than by running modinfo;
whether it is *loaded* is read from the presence of the platform directory. `modprobe` spells it
`vhci-hcd`, sysfs and lsmod `vhci_hcd`.

**It needs no network.** The vhci attach is a sysfs write of `"port sockfd devid speed"`, and the
kernel only requires that the fd be a `SOCK_STREAM` socket in the writing process's table — it never
checks the address family. An `AF_UNIX` socketpair therefore works, so nothing listens on a port and
there is nothing for anything else on the machine to connect to. It also skips the `OP_REQ_IMPORT`
negotiation entirely: the socket handed over is already connected, so the conversation starts in the
transfer phase. The device is attached as `devid` `0x00010002` at speed 3 (`USB_SPEED_HIGH`). Either
socket has headroom: loopback TCP, which the `usbip` tool uses, measures a round trip at p50 10.7 µs
against the 1000 µs per-URB budget.

The resulting device is an ordinary USB device in sysfs, so `snd-usb-audio` and `hid-playstation`
bind to it as true siblings and both container ids derive by construction. Prior art:
**`usbipdcpp`** (C++, LGPL-3.0, active) implements per-packet iso on the virtual-device path and
reports iso streaming through `vhci-hcd` demonstrated with a virtual UVC camera — the same transport
problem as UAC1.

`VIIPER`, the only existing USB/IP DualSense emulator, is **HID-only** (`bInterfaceClass = 0x03`, no
audio interfaces), so the device itself still has to be written; and several popular USB/IP
libraries `recv()` a fixed 48 bytes and never consume iso packet descriptors, which desynchronises
the stream rather than failing cleanly.

### The device

Endpoint numbers `flydigi/ds5_usbip.py` answers on: 1 = iso OUT (haptic audio, addr `0x01`),
2 = iso IN (microphone, addr `0x82`), 3 = HID OUT (addr `0x03`), 4 = HID IN (addr `0x84`).

  * **The haptic stream is s16le and is decimated before the DSP sees it.** The isochronous OUT on
    endpoint `0x01` is 4 channels of **s16le at 48 kHz** — not the float32 `parec` is asked for on
    the bridge — so the DSP has an s16 front end. It is then decimated 8:1 **by block averaging**,
    not by dropping samples: plain decimation aliases content above the new Nyquist down into the
    high band and drives the wrong motor. `Splitter` is therefore constructed at `RATE // DECIMATE`,
    i.e. 6 kHz. `unpack_s16` fills only the haptic channels and leaves ch0/ch1 zero in its output,
    so its result cannot be used to measure the headphone or speaker channels; `channel_energy` is
    the function that reports all four, and it clamps peaks to 32767 because `abs(-32768)` reads as
    louder than a sample can be.
  * **Endpoint numbers are not addresses.** `usbip_header_basic` carries `ep` as the plain number
    0..15 with direction in its own field, so the high bit of a descriptor's `bEndpointAddress`
    never appears on the wire. Comparing against `0x84` stalls every input report — and only the IN
    endpoints, because an OUT address already equals its number.
  * **`number_of_packets` is a signed field carrying -1 for a non-isochronous URB.** Spelling that
    `0xFFFFFFFF` is wrong twice: it will not pack, and it never compares equal to what comes back.
    Older kernels sent 0 instead, so the test is `> 0`.
  * **`actual_length` on an OUT transfer is what the host sent and the device accepted**, not the
    length of any reply. Reporting 0 for an accepted 47-byte SET_FEATURE makes the host see a short
    transfer and retry it forever — visible as Wine logging
    `err:hid:hidraw_device_set_feature_report id 8 write failed` twice a second while the pad never
    finishes configuring.
  * **The microphone endpoint answers with silence.** It is declared because the config descriptor
    is the real device's, served verbatim. Stalling it instead makes the host resubmit immediately:
    measured at 1.5 million stalls, with the haptic stream starving alongside.
  * **Audio class control requests are answered with zeros.** GET_CUR and friends return a
    zero-filled buffer of the requested length, which is enough for `snd-usb-audio`'s mixer probe to
    succeed; nothing here has real controls.
  * **Input reports ride parked URBs.** The kernel keeps a few interrupt-IN URBs outstanding; each
    report completes the oldest. Reports go out on the relay's own 4 ms cadence.
  * **The vendor node is kept off the loop that owes the host input reports.** Writing rumble or a
    trigger effect means `claim()` + drain + write, and the drain has to eat whatever the motion
    stream has queued. On the main loop, rumble cost 36% of wall-clock and produced a 601 ms stall
    and a burst of trigger effects a 605 ms one, while the host was waiting on a report every 4 ms.
    One worker thread owns the vendor node and does both. Rumble reaches it through a single-slot
    mailbox — latest value wins, because a superseded motor level is worthless — while trigger
    effects go through a queue, since each is a one-shot state change and must not be gated on the
    motor value changing.
  * **Two sources want those motors** — the output report's `motor_left`/`motor_right` and the
    haptic audio — and a game may use both. They are held in separate slots and **combined per motor
    with `max`, not summed**: a loud HID rumble is never doubled by concurrent haptic audio, and no
    clamp-after-addition is needed.
  * **The DSP and that thread both run at the rate the motors can accept** — `RUMBLE_HZ` = 60, not
    per URB. URBs arrive about 1100 times a second, so per-URB processing is eighteen times more
    work than can reach a motor.
  * **Feature reports come from hardware and carry no report id.** `flydigi/ds5_usb.py` holds the
    real controller's blobs; whoever serves them prepends the id exactly once. inputtino's copies
    include it, and a doubled id shifts every byte of calibration by one with no visible symptom —
    the pad still enumerates and Steam still shows the correct artwork.
  * **A perfect twin of a real DualSense evicts it.** `hid-playstation` keys a controller by the MAC
    in feature report `0x09` and names its sysfs entries after it, so with an identical one the real
    pad binds usbhid, gets no hidraw node, and silently stops being a gamepad. Observed. The
    committed blobs carry inputtino's public addresses instead, which also keeps hardware identity
    out of the repo.
  * **Report `0x0B` carries a third six-byte field**, of unestablished meaning, sitting where
    another address entry would; the generator replaces it by position with inputtino's host
    address, unconditionally. Report `0x05` — this unit's real gyro and accelerometer calibration —
    is committed **deliberately**: it is per-unit but it is not an identifier, and serving someone
    else's calibration would make the motion wrong.

### Privilege model

Attaching to vhci is a privileged sysfs write, and with the `vhci-hcd` module load it is all the
root the relay ever needs. The relay is started through pkexec — wrapped in `flatpak-spawn --host`
under Flatpak and in `host-spawn`/`distrobox-host-exec` inside a container (`flydigi/setup.py`,
`escalation_for`) — loads `vhci-hcd`, takes a port and hands the kernel one end of the socketpair,
all before it opens a device or starts a thread, and then `setuid`s back to the invoking user.
`--stay-root` opts out, for debugging. What runs for the length of a play session is an ordinary
user process holding a socket the kernel has already accepted.

  * **Stopping it is a plain SIGTERM** from the session that started it. `setuid` from root sets the
    real, effective and saved uid together, so the process is genuinely the user's afterwards; a
    process that had only dropped its effective uid would keep root as its saved uid and refuse the
    signal.
  * **Detaching needs no privilege either.** vhci's receive loop sees the socket close, raises
    `VDEV_EVENT_DOWN` and resets the port to `VDEV_ST_NULL` — which is exactly the state a free port
    is in. The explicit detach write is a courtesy that makes it immediate.
  * **The pad is opened as the user, after the drop**, so the udev rules are exercised rather than
    bypassed. An unreadable node is reported with the rule to install.

DS mode is one switch for the whole system, not a per-game route, and the daemon never starts it —
reasoning in [findings-games.md](findings-games.md). An unattended per-game attach would also mean
granting the desktop session standing permission to emulate USB devices, which is a local
privilege-escalation primitive, since one of those devices is a keyboard.

### Using it

`flydigi/dsmode.py` is the switch the desktop app drives: `state()`, `start()`, `stop()`, `tail()`,
`latest_status()`, `parse_status()`. The relay itself uses only its `drop_privileges()` and
`IGNORE_DEVICES`, and `tools/apex5-setup` has no DS-mode subcommand — by hand, the relay is started
directly. The self-test needs no controller, no root and no vhci port:

    python3 tests/test_dsmode.py

In the command line at the top of this file, `--motors` drives the Apex 5's motors from the haptic
stream and `--haptics` only measures and reports per-channel energy. Other flags: `--dump` (decode
effects without driving the pad), `--quiet`, `--no-motion`, `--gyro-scale`, `--accel-scale`,
`--gain`, `--crossover`, `--gate`, `--curve`, `--stay-root`, `--verify`. The desktop app starts it
with `--motors` per its switch and never with `--haptics`, so the per-channel report exists only
when the relay is run by hand. `tools/flydigi-ds5`, the uhid relay, takes the input-side flags only:
`--dump`, `--quiet`, `--no-motion`, `--gyro-scale`, `--accel-scale`.

  * **Turn it on before starting the game.** A game opens its stream to the controller's audio
    device once, at launch. Switching DS mode on while it is already running gives it a pad it will
    happily use and an endpoint it will never look for again: triggers work, haptics stay silent.
    Restart the game.
  * **A game sees both pads.** `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501`, on the launch
    command or set globally in Steam, makes SDL ignore the Apex 5; it cannot hide a real
    *DualSense*, whose `054c:0ce6` the virtual pad shares.
  * **Steam Input must be off** for that game: it masks the pad as an Xbox controller, which breaks
    DualSense semantics and the four-channel audio the haptics arrive on.
  * **Third-party mode must be off.** It switches `controller_data` off, which silences the evdev
    node both relays read sticks and buttons from, while motion survives — the pad tilts with dead
    sticks. The tell is the relay's status line: `evdev=` stays at 0 while `motion=` climbs. Nothing
    enforces it; neither relay reads command 16 before starting. Detail in
    [findings-steam.md](findings-steam.md).
  * **`--verify` reads the relay's own `054c:0ce6` evdev node back** and counts events, which
    separates "the relay's reports never reach the OS" from "the game bound the wrong pad" — two
    failures that look identical from inside the game.
  * **An unreadable evdev node is fatal and named.** The relay checks readability after dropping
    root and refuses to start, naming the fix: `tools/apex5-setup install-rules`. An unusable vendor
    node is reported separately and is not fatal — input still reaches the game, only the Apex 5's
    own motors and triggers go unwritten.
  * **The relay logs to `~/.local/state/flydigi/ds5-relay.log`** (`XDG_STATE_HOME` is honoured),
    truncated per run. The parsed counters are `reports`, `evdev`, `motion`, `out`, `iso_urbs` and
    `loopback`; `out` rises when the game is driving the virtual pad, and `iso_urbs` rises when
    haptic audio is arriving. It is a file rather than a pipe because the status line every ten
    seconds fills an undrained pipe at about 4 kB and blocks the writer — here, the process serving
    URBs on a 4 ms deadline.
  * **Closing the desktop app does not stop the relay.** Shutdown waits for the model's own thread
    and deliberately leaves the relay running, so a game in progress keeps the pad. Only the switch
    or a signal stops it.
  * **The relay prints a self-check on the first isochronous URB**: packet count, data length, the
    sum of the packet lengths and whether the descriptors tile the buffer. If
    `sum(length) != data_len` the unpacking is wrong and all-zero channel peaks mean nothing.
  * **`--haptics` counts a window as active above RMS 200 per channel** and reports `rms`, `active%`
    and `peak` for all four. Peak is set by one sample and never lowered, so on its own a momentary
    click at stream open looks identical to continuous haptics.

## Two DualSenses at once: what is per-device and what is not

Measured with the virtual pad attached over USB/IP beside a real DualSense, both `054c:0ce6`.

**They coexist.** Both bound `hid-playstation` and both kept a hidraw node, because the committed
feature report `0x09` carries inputtino's placeholder address rather than the capture's, so no two
addresses collide. Each got its own 4-channel sink:

    …-00.Direct__Direct__sink     card1   /devices/platform/vhci_hcd.0/usb5/5-1/…   virtual
    …-00.2.Direct__Direct__sink   card2   /devices/pci…/usb3/3-1/…                  real

**HID output reports are per-device.** A rumble report written to both nodes at once drove both:
the real pad's motors, and — through the relay — the Apex 5's. Adaptive trigger effects ride the
same report `0x02` that carried the rumble, so input, rumble and triggers are all per-device.

**Haptic audio is not, and that is the game's doing.** Deathloop with both attached opened exactly
one DualSense stream at a time and rebuilt it three times in thirty seconds, landing on a different
sink each time — new stream id every open, so the game was tearing it down rather than PipeWire
moving it. The real pad had a stream opened against its sink three times and never vibrated once.
A title that assumes a single DualSense re-resolving which pad is *the* pad is what that looks
like, and no amount of emulation changes it: two endpoints exist, and the game asks for one.

**Two virtual DualSenses would evict each other.** They would be perfect twins — the same committed
`0x09` address — which is the eviction above turned inward. Multi-instance would need the address
derived per pad, from the physical pad's uid (command 4).

That is measured rather than built: see [PROGRESS.md](../PROGRESS.md#ruled-out).

## Peripheral-mode SBC

An SBC in peripheral mode — a real UDC, a configfs gadget with `hid.usb0` + `uac1.usb0`, one cable
to the PC — works and costs hardware. On an Orange Pi PC 2 (Allwinner H5, Armbian, kernel 6.18)
every module ships prebuilt (`CONFIG_USB_F_UAC1=m`, `CONFIG_USB_F_HID=m`, `CONFIG_USB_RAW_GADGET=m`),
there is a real UDC at `musb-hdrc.4.auto` with `dr_mode = otg`, and the gadget bound cleanly with no
host attached, producing `/dev/hidg0` and a 4-channel `UAC1Gadget` capture device.

  * **`c_*` is host→gadget**, measured, and arrives on the board as an ALSA **capture** device.
    Backwards, the gadget enumerates perfectly and carries no audio.
  * **A configfs binary attribute can store less than it was given, silently.** The 289-byte report
    descriptor stored as 151 bytes, and the gadget still bound and enumerated, describing itself as
    something else. Read attributes back and compare lengths.

The SBC is the only route to a machine not running this software at all — a console, a Windows box,
or a Steam Deck. USB/IP needs no hardware, no cable and no root beyond the attach.

## M1-M6 buttons: no DualSense input of their own

The vendor input report carries them in its third button byte (offsets in
[findings-steam.md](findings-steam.md)); the DualSense protocol has no extra buttons to deliver them
on, so reaching a game means re-using a control it already has.

A DualSense Edge would not supply them either: it has two back buttons, not four; even on a real Edge
they have no HID inputs of their own and must be remapped onto existing buttons in the controller;
and its different hardware ID *loses* native DualSense support in some games — DSX has a "DualSense
Emulation" mode and Special K an "Identify DualSense Edge as DualSense" option to undo that, and
`ds5-edge-relay` converts an Edge into a plain DualSense.

**They cannot act as relay sources either, so no software here can route them anywhere.**
`relay.build_state` folds *evdev* state, and `flydigi/evdev.py` defines only the standard gamepad
set — xpad exposes no code for an extra button. What arrives at evdev is whichever standard button
the pad's own remapping sends, indistinguishable from a real press of that button. The extras exist
only in the vendor report's byte 14, which nothing parses; any use of them, whether a game-facing
control or a host-side hotkey, needs that parsed first.

What the relay does today is unrelated to them: SELECT drives touchpad click and **SELECT+START**
sends Create, with Options suppressed while the chord is held. Passing `select_is_touchpad=False`
to `relay.build_state` drops the touchpad click and leaves SELECT unmapped, but both relays call it
at the default and no flag exposes it.

So the pad's own onboard remapping is the whole story for M1-M6: it works with no software running
and persists in controller memory.

