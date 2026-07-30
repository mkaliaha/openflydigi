# Haptic audio on a virtual DualSense

Tier 4's structural gap is closed: a game writes haptics to a DualSense that is a userspace
process, and the Apex 5's motors reproduce them. This is what was measured on the way, what
turned out to be wrong, and what it cost to find out.

Index: [PROGRESS.md](../PROGRESS.md).

## Deathloop: what tier 4 does and does not deliver

Adaptive triggers **work in game**. Confirmed the transcribed mapping behaves exactly like
Flydigi's: the game sent `type=0x25 p[0]=12` → `mode 3 [70,0,12]` and `type=0x21 p[1]=3` →
`mode 1 [140,1]`, both matching their table's branches. Zero unmapped patterns.

- **Rumble: the gap that defined this document, and it is closed.**

  The DualSense has no conventional rumble motors; its voice coils do both jobs. Games can drive
  them two ways: `motor_left`/`motor_right` in the HID output report (the compatibility path, which
  we already support and which most PC ports use), or arbitrary waveforms written to the
  controller's USB *audio* device (the rich PS5 haptics). Deathloop uses the audio path.

  Confirmed by testing: with the real Apex 5 as a plain Xbox pad the game vibrates readily, even on
  a menu button press — so it does emit motor rumble, just not to a DualSense. Our own output path
  is proven: a direct cmd `0x12` rumbles the pad and ACKs.

  **On the uhid tier (4) this cannot be fixed**, for the reason worked out below: a uhid node has no
  USB parent, so no audio endpoint can ever share its container id. On the USB/IP tier (4b) it
  works, because the virtual pad *is* a USB device with an audio interface beside its HID one. So
  the per-game trade-off this section used to state — adaptive triggers or rumble, pick one — no
  longer exists; it applied to `tools/flydigi-ds5` and applies to nothing now that
  `tools/flydigi-ds5-usbip` is the tier the app switches on.

  The null-sink attempt that came first is kept because its negative result still stands and still
  explains why the composite device was necessary:

  > **Built, tested, negative result.** Neither Flydigi nor DSX implements audio haptics — verified
  > by decompilation: Space Station bundles no audio libraries at all, and its `EnableAudio` command
  > is a device feature toggle, not PC audio capture.
  >
  > We built the missing piece anyway (`tools/flydigi-haptics`): a fake 4-channel DualSense sink
  > (`pipewire/99-dualsense-haptics.conf`) plus a bridge that measures haptic-channel energy and
  > converts it to motor rumble. **The bridge works** — verified with `tools/haptics-simulate`, which
  > plays synthetic gunshots and engine rumble and produces correctly decaying motor values.
  >
  > **But games do not use it.** With the sink present and named "Wireless Controller", Deathloop
  > opened exactly one audio stream and routed it to the speakers; our sink measured absolute silence
  > (peak 0.00000). A virtual pad has no OS-level link between its HID device and an audio endpoint,
  > and an unassociated sink is not picked up.
  >
  > Cannot distinguish "looked for a controller endpoint and rejected ours" from "never looks on PC".
  > The outcome is the same either way. Tooling is kept because it is proven working — if a game is
  > ever found that does write to such a sink, only the sink config needs reinstalling.
  >
  > Two notes for anyone re-running this: `pw-record` prepends a file header and will silently
  > misalign a raw reader (use `parec --raw`), and `paplay --raw` declares no channel map so PipeWire
  > remixes the channels — do not assume fixed haptic channel indices.

  The DSP that bridge proved is not scaffolding any more: `flydigi/haptics.py` is what the virtual
  device now feeds, unchanged.
- **Gyro/accel: implemented.** The vendor input stream (command 17, "raw data transport in")
  carries the IMU at ~300 Hz, and enabling it does **not** disturb the xpad node, so sticks and
  buttons still come from evdev. Offsets follow `OperatorDataParser` for `NewXInput`, shifted by
  one because we keep the report-id byte. Accelerometer is scaled by 2.441: the pad reports
  ~4096/g while the DualSense calibration we advertise implies 10000/g — verified by the pad
  reading exactly 1.00 g flat. Gyro scale is left at 1.0 and is the one value worth tuning by feel
  (`--gyro-scale`). M1-M4 buttons are in the same stream and still to do.
- **Battery: implemented.** Command 1 returns device type, connection type and battery; polled
  every 30 s and mapped to the DualSense's 0-10 scale (Flydigi reports 0-5, with a high nibble
  flagging charging). Also fixed `BATTERY_FULL`, which was 0x01 (= charging) rather than 0x02, so
  the pad had been reporting "charging" permanently.

## Haptic audio

Deathloop *does* drive DualSense haptics on PC, and it works under Proton — verified with a real
DualSense connected over USB (haptic audio needs wired USB; over Bluetooth the endpoint does not
exist). The game opens a **dedicated second stream** to the controller's audio device alongside its
normal game audio, so this is real haptic output rather than misrouted sound.

**DualSense audio channel map**, established by playing tones into each channel and having a human
report what happened — identified by pulse count rather than play order, after an off-by-one made
the first attempt wrong:

    ch0  headphone jack        ch2  left haptic actuator
    ch1  speaker               ch3  right haptic actuator

Deathloop was first measured writing **ch3 only** (active in 87% of 373 sampled windows; ch1 never
touched), through a real DualSense's PipeWire monitor. Measured again at the device itself, both
actuator channels carry signal and track each other closely, with ch2 often slightly stronger --
while ch0 and ch1 stay at *exactly* zero. The direct observation is the better one, and the fact
that the two silent channels are silent to the last bit is also the check that the channel offsets
are aligned: misalignment would smear energy across all four.

**Conversion** (`flydigi/haptics.py`, `tools/flydigi-haptics`): the DualSense's actuators are
full-range voice coils, but the Apex 5's motors are not interchangeable — left is a large
low-frequency mass, right a small high-frequency one. Mapping left-to-left would throw away the
character of the waveform, so the signal is split by frequency instead: low band drives the left
motor, high band the right. Confirmed working against live game haptics.

Three things dominated latency, all of which made it feel sluggish and "keep going" after effects
ended:
  * `effects.rumble()` waited 100 ms for an ACK on every update. Pass `wait=0.0` when driving
    continuously — the ACK carries nothing useful.
  * `parec` buffers generously by default; ask for `--latency-msec`.
  * When falling behind, **drop stale audio** rather than working through the backlog.

Useful settings: `--gain 1.5 --crossover 250`.

**Superseded as a delivery mechanism**, but not as work: this bridge needed a real DualSense present
as the source, sampling the buzz off a controller sitting on the desk. The conversion it proved is
exactly what the virtual device now feeds. What was scaffolding is now the second half of a working
feature.

  * **`DualSense-haptic-helper`** (MIT) — real hardware; independently found haptics on channels
    2 and 3 of a 4.0 stream, matching our tone probing. Warns that **Steam Input masks the
    DualSense as an Xbox pad and breaks 4-channel audio**, so it must be disabled.
  * **`Haptic-Feedback-Linux`** and **`xzn/proton-ds5-haptic`** — Wine/Proton patches enabling DS5
    haptics, plus a udev rule setting `SOUND_DESCRIPTION="Wireless Controller"`.
  * **GE-Proton 11-2** and **proton-cachyos** now ship wired PS5 haptics natively for real
    controllers. A WirePlumber rule may be needed to stop PipeWire collapsing the DS5 node to mono.

**The mechanism**, from the patch discussions: games locate the haptic device by name, and the Wine
patches "fetch the audio-side ContainerId from setupapi so HID and MMDevice agree by construction".
That is precisely the association our null sink lacked — a uhid device and an unrelated PipeWire
sink can never share a ContainerId.

**Nobody had emulated a virtual DualSense with a working audio device.** Every project either used
real hardware or emulated HID only -- inputtino, DSX, and `VIIPER`, the one USB/IP DualSense, which
declares `bInterfaceClass = 0x03` and no audio interfaces at all. That is no longer true; the rest
of this document is how.

## Virtual USB composite device

Our PipeWire null sink was ignored by the game even when named "Wireless Controller", while a real
DualSense was used immediately. That points at device identity/association rather than name
matching: a game finds the haptic endpoint via the OS-level link between the HID device and the
audio device, which a null sink does not have.

The architecturally correct fix is one USB composite device exposing both interfaces, so the kernel
creates the hidraw node and the ALSA card from the same device — a configfs gadget with
`hid.usb0` + an audio function, VID:PID `054c:0ce6`.

### What the real DualSense actually is

Descriptors read off the hardware (`bcdDevice 0100`), which settled several things that had been
guessed:

    iface 0     Audio Control      bcdADC 1.00   <- UAC *1*, not UAC2
    iface 1/1   Audio Streaming    4ch s16le 48000, EP 0x01 OUT iso ADAPTIVE, 392 B, 1 ms
    iface 2/1   Audio Streaming    2ch s16le 48000, EP 0x82 IN  iso ASYNC,    196 B, 1 ms  (mic)
    iface 3     HID                EP 0x84 IN + EP 0x03 OUT, interrupt, 64 B
    self-powered, 500 mA, iSerial 0 (no serial string)

**It is USB Audio Class 1.** Everything written here before assumed `usb_f_uac2` was the missing
module; the module actually needed is `usb_f_uac1`. Fedora ships neither, so the conclusion below
does not change — but the target does.

**The playback endpoint is Adaptive, with no explicit feedback endpoint.** So the whole device needs
only four endpoints, and three if the mic is dropped. That matters on any controller with a small
endpoint budget.

Channel config is `0x0033` = FL FR RL RR, consistent with the tone probing above: the haptic
actuators are the RL/RR pair, ch2 and ch3.

**The HID report descriptor is 289 bytes, and used not to be the one we emulated.** inputtino's copy
was 273 bytes; the two are identical for 145 bytes and then diverge, because inputtino lacks feature
reports `0x0B` and `0x0C` (usages `0x41`/`0x42`, 41 bytes each). Presumably an older firmware —
its firmware string reads "Jun 19 2023" against this unit's "Jul  4 2025", 42 of 63 bytes apart.
Nothing has been observed to care, but the real one is what a host compares against, so
`flydigi/ds5_usb.py` holds the captured descriptor and both tiers use it. The inputtino data, and
the `flydigi/ps5_data.py` that held it, are gone.

Re-capture with:

    cp /sys/bus/usb/devices/<n>/descriptors .
    cp /sys/bus/hid/devices/0003:054C:0CE6.*/report_descriptor .
    cat /proc/asound/Controller/stream0

### How Proton actually matches audio to a gamepad

Read out of the Proton source rather than inferred. **There are two strings, computed independently,
and earlier notes here conflated them.**

**1. The MMDevice instance id** — `USB\VID_054C&PID_0CE6\...` versus `{1}.ROOT\MEDIA\NNNN`. In
`winepulse.drv` this is **not** derived from sysfs. `get_device_path()` switches on `bus_type`,
which `fill_device_info()` sets from the **PulseAudio proplist**: `device.bus` must be literally
`"usb"`, plus `device.vendor.id` and `device.product.id`. Anything else falls through to
`ROOT\MEDIA\%04u`. Sysfs parsing exists only in **winealsa**, which Proton does not use. So the
older note here — "winepulse resolves identity through the sysfs path and looks it up in setupapi" —
named the wrong driver and the wrong mechanism.

**2. ContainerId** — `DEVPKEY_Device_ContainerId`, and *this* is the HID↔audio join. It is
**Proton-only**; upstream Wine has never merged it. `winepulse.drv` takes the `sysfs.path` proplist
value, resolves it through udev, walks up to the `usb_device` ancestor, and builds a GUID from
`(vid, pid, busnum, devnum, usec_initialized)`. `winebus.sys` computes **the identical GUID by the
identical formula** from the hidraw device's USB parent. Nothing looks anything up: the association
is value equality of two GUIDs each side derives independently from the same physical `usb_device`.

Shipped in **Proton 10.0-4** ("Fixed haptics support for DualSense controllers"). Relevant commits
in `ValveSoftware/wine`: `e179606` (winepulse container id from udev) and `961d16f` (winebus bus
specific container ids).

**Why the null sink really failed.** Not the sysfs path — `device.bus` was not seen as `"usb"` by
winepulse. `alsa.components` and the node name/description are never consulted for identity at all,
so making them byte-identical to the real device was effort spent on fields nobody reads.

**Why uhid can never close this — now confirmed from code, not argued.** A uhid node's parent chain
is `/devices/virtual/misc/uhid/...`, so `udev_device_get_parent_with_subsystem_devtype(dev, "usb",
"usb_device")` returns NULL, `winebus` logs "Failed to get parent device", and falls back to
`make_unique_container_id()` — a **random GUID, re-rolled every run**. Our virtual DualSense cannot
match any audio endpoint, ever, by construction.

**Not every game needs this.** Three observed behaviours: match the audio endpoint by *name*
containing "Wireless Controller" (FF14, FF7R — these tolerate a fake); read the HID device's
container id and take the MMDevice with the same one (**Deathloop**, Ghostwire); or container id
plus SetupDi enumeration, which additionally needs the `DeviceContainers` registry database that
only the xzn patches populate.

**Deathloop works here on stock Proton with a real DualSense** — no GE-Proton, no
`compatibilitytools.d`, no per-game compat override. So it uses the middle path, and the synthetic
device below feeds *exactly the code path already observed working on this machine*. No patched
Proton is required.

One caveat worth knowing before debugging a silent test: Wine
[bug 59557](https://bugs.winehq.org/show_bug.cgi?id=59557) reports DualSense speaker and haptics
broken since Wine 11.4, from a commit that hardcoded `DEVPKEY_Device_DeviceDesc`. Untriaged, and not
affecting this machine's current default Proton. If a virtual device ever goes silent, re-verify
with the real DualSense on the same Proton build before blaming the device.

**An unvalidated seam, recorded but not a solution.** `get_container_id()` never checks that
`sysfs.path` belongs to the sink that declared it — it resolves the string and walks up. A virtual
PipeWire node pointed at a *real* USB pad's sysfs subtree would therefore inherit that pad's
container id with no patching. It still needs something enumerating as `054C:0CE6` on the HID side,
so it cannot remove the real DualSense; it would only stop it buzzing, since the game would write to
our sink instead of its actuators. A cleaner transducer rig, not a way out of one.

This is not Linux-specific: a virtual audio device on Windows needs an audio driver, and DSX ships
a virtual gamepad bus driver rather than one — consistent with DSX's virtual pad also failing to
produce haptics in Death Stranding DC.

**The HID-unbind experiment is retired — it cannot discriminate.** The idea was to keep a real
DualSense as a haptic transducer while unbinding its HID interface, so the game could not see it as
a gamepad:

    echo -n "0003:054C:0CE6.00XX" | sudo tee /sys/bus/hid/drivers/playstation/unbind

It was written up as diagnostically decisive: writes anyway means matching is **by name**, silence
means **by association**. That framing predates the setupapi finding above and does not survive it.
Unbinding `hid-playstation` removes the hidraw node but leaves the USB device and its interfaces
intact, so the audio side keeps the same sysfs path, instance id and ContainerId either way. The
only thing that changes is that the game stops seeing a second gamepad — leaving it our uhid pad,
whose ContainerId matches no audio endpoint. Silence is the predicted outcome, and it would confirm
nothing that the mechanism does not already say.

The same mechanism also re-explains the working bridge: the game finds the *real* DualSense as a
controller, matches its ContainerId to its audio sibling and writes there. Which pad the player is
actually holding never enters into it.

**So there is no cheap substitute for the composite device — and equally, it is no longer a gamble.**
A configfs gadget is a real USB device: one ContainerId, HID and audio as siblings, a genuine sysfs
path. That is precisely what the mechanism requires. The open risk is the UDC's isochronous support,
not the concept.

Note `SDL_GAMECONTROLLER_IGNORE_DEVICES` cannot be used to hide a real pad -- our virtual one shares
its VID/PID.

The transducer trick remains of limited practical value on its own (it needs a DualSense physically
attached), and is no longer diagnostically decisive.

**What this laptop ships.** Fedora turns off every audio gadget function, and `raw_gadget` with them:

    CONFIG_USB_DUMMY_HCD=m                          present, but see below
    CONFIG_USBIP_VHCI_HCD=m                         present — this is the one that matters
    CONFIG_USB_CONFIGFS_F_FS=y                      present
    CONFIG_USB_CONFIGFS_F_UAC1        is not set
    CONFIG_USB_CONFIGFS_F_UAC1_LEGACY is not set
    CONFIG_USB_CONFIGFS_F_UAC2        is not set
    CONFIG_USB_RAW_GADGET             is not set

There is also no real UDC here: `/sys/class/udc` is empty, the machine is USB host-only.

### Every soft-UDC route is blocked, and not for the reason first assumed

A first pass concluded `dummy_hcd` would rescue this: it loops a virtual UDC to a virtual HCD on one
machine, so a gadget bound to it enumerates as an ordinary USB device in that machine's own sysfs,
HID and audio as siblings. The topology reasoning is right. **It still fails, on isochronous.**

**`dummy_hcd` declares no isochronous endpoint.** Alan Stern removed them in v4.15 (commit
`c9f20aafc939`, "USB: dummy-hcd: remove unsupported isochronous endpoints"), out of a thread where
the UVC gadget hit exactly this wall. Verified against the shipped Fedora binary — `strings` on
`dummy_hcd.ko` yields `ep1in-bulk`, `ep5in-int`, `ep-aout` … and nothing `-iso`; the only `isoch`
token in it is `isoch_delay`, a SuperSpeed descriptor field. So `usb_gadget_ep_match_desc()` rejects
an iso descriptor, `usb_ep_autoconfig()` returns NULL, and the gadget refuses to bind at all. The
host half fails every iso URB with `-EINVAL` besides. Michael Grzeschik proposed re-enabling iso in
2022; it was rejected for not emulating real hardware.

**This voids the "which distros ship `usb_f_uac1`" question.** Debian, Arch, CachyOS, SteamOS and
Nobara all ship it (Fedora and Bazzite do not) — and on all of them it still cannot bind to
`dummy_udc.0`. A UAC function driver is useless without an isochronous-capable UDC, and no
mainstream distro ships one. **The missing `usb_f_uac1.ko` was never the binding constraint.**

**`usbip-vudc` is the other shipped soft UDC, and it is also out.** It advertises iso-capable
endpoints, so unlike `dummy_hcd` a gadget binds and enumerates — then `vudc_transfer.c` sets
`urb->status = -EXDEV` under a bare `/* TODO: support */`. It looks like it works right up until no
audio arrives, which makes it the worse trap of the two.

**FunctionFS cannot express UAC descriptors at all.** `ffs_do_single_desc()` validates against a
closed whitelist of `bDescriptorType`; UAC1 needs `CS_INTERFACE` (0x24) and `CS_ENDPOINT` (0x25),
neither of which has a case, so the descriptor blob write to ep0 fails with `EINVAL`. Each
class-specific type has had to be added by explicit kernel patch (CCID got one in 2018); none has
ever been proposed for audio. FunctionFS *does* support isochronous — it has since v2.6.36 — but
that never becomes relevant. `raw_gadget` would sidestep the whitelist entirely, and Fedora does not
ship it, and it would still need an iso-capable UDC underneath.

So: building `usb_f_uac1.ko` would not have helped on its own. Any gadget-stack solution needs *both*
a UAC function driver *and* an isochronous-capable UDC, and no distro ships the latter in software.

### What survives: userspace USB/IP + vhci-hcd

The one software route with no such wall, because **it has no UDC in it at all**. Rather than build
a gadget, fabricate the device in a userspace process speaking the USB/IP protocol, and let
`vhci-hcd` — the client-side virtual *host* controller — enumerate it locally.
`vhci-hcd` implements isochronous fully, and it is `=m` on every distro checked without exception.
None of the three blockers above applies: no descriptor whitelist, no endpoint autoconfiguration, no
gadget bind.

**And it needs no network.** The survey assumed loopback TCP, because that is what the `usbip` tool
does, and a loopback round trip was measured at p50 10.7 µs against the 1000 µs budget to show it
would fit. As built it does not use TCP at all: the vhci attach is a sysfs write of
`"port sockfd devid speed"`, and the kernel only requires that the fd be a `SOCK_STREAM` socket in
the writing process's table — it never checks the address family. An `AF_UNIX` socketpair therefore
works, so nothing listens on a port and there is nothing for anything else on the machine to connect
to. It also skips the `OP_REQ_IMPORT` negotiation entirely: the socket handed over is already
connected, so the conversation starts in the transfer phase.

The resulting device is an ordinary USB device in sysfs, so `snd-usb-audio` and `hid-playstation`
bind to it as true siblings and both container ids derive by construction. Prior art:
**`usbipdcpp`** (C++, LGPL-3.0, active) implements per-packet iso on the virtual-device path and
reports iso streaming through `vhci-hcd` demonstrated with a virtual UVC camera — the same transport
problem as UAC1.

Two cautions. `VIIPER` is the only existing USB/IP DualSense emulator and is **HID-only**
(`bInterfaceClass = 0x03`, no audio interfaces), so the device itself still has to be written. And
several popular USB/IP libraries `recv()` a fixed 48 bytes and never consume iso packet descriptors,
which desynchronises the stream rather than failing cleanly — check for iso handling before
adopting one.

## What was built, and what it cost to get right

`flydigi/usbip.py` is the transport (about 360 lines, no dependencies) and `flydigi/ds5_usbip.py` is
the device on top of it. `tools/flydigi-ds5-usbip` is the relay. Everything below was found by
running it, not by reading a spec.

  * **Endpoint numbers are not addresses.** `usbip_header_basic` carries `ep` as the plain number
    0..15 with direction in its own field, so the high bit of a descriptor's `bEndpointAddress`
    never appears on the wire. Comparing against `0x84` stalls every input report — and only the IN
    endpoints, because an OUT address already equals its number. It looks like "output works, input
    is broken" rather than like a masking error.
  * **`number_of_packets` is a signed field carrying -1 for a non-isochronous URB.** Spelling that
    `0xFFFFFFFF` is wrong twice: it will not pack, and it never compares equal to what comes back.
    Older kernels sent 0 instead, so the test is `> 0`.
  * **`actual_length` on an OUT transfer is what the host sent and we accepted**, not the length of
    any reply. Reporting 0 for an accepted 47-byte SET_FEATURE makes the host see a short transfer
    and retry it forever — visible as Wine logging
    `err:hid:hidraw_device_set_feature_report id 8 write failed` twice a second while the pad never
    finishes configuring.
  * **Stalling the microphone endpoint is a trap.** We declare it because the config descriptor is
    the real device's, served verbatim, and we have nothing to put in it. Stalling makes the host
    resubmit immediately: measured at 1.5 million stalls, with the haptic stream starving alongside.
    Answering with silence costs nothing and ends it.
  * **A parked URB is the only thing that can carry an input report.** The kernel keeps a few
    interrupt-IN URBs outstanding; each report completes the oldest. Answering inline from current
    state is what real hardware does and removes a clock, but it coincided with the game losing the
    pad, so it is still done on our own 4 ms cadence pending a bisect.
  * **Keep the vendor node off the loop that owes the host input reports.** Writing rumble or a
    trigger effect means `claim()` + drain + write, and the drain has to eat whatever the 300 Hz
    motion stream has queued. On the main loop that cost 36% of wall-clock and produced a 605 ms
    stall while the host was waiting on a report every 4 ms. It now has its own thread with a
    single-slot mailbox — latest value wins, because a superseded motor level is worthless.
  * **Two sources want those motors** — the output report's `motor_left`/`motor_right` and the
    haptic audio — and a game may use both. They are kept apart and combined, rather than letting
    whichever wrote last win.
  * **Drop stale audio rather than working through it.** The haptic queue is bounded and discards
    the oldest. Falling behind and catching up is what made the pad feel sluggish and keep buzzing
    after an effect ended.
  * **The DSP runs at the rate the motors can accept**, 60 Hz, not per URB. URBs arrive about 1100
    times a second, so per-URB processing was eighteen times more work than could ever reach a
    motor.
  * **Feature reports come from hardware and carry no report id.** `flydigi/ds5_usb.py` holds the
    real controller's blobs; whoever serves them prepends the id exactly once. inputtino's copies
    include it, and prefixing those shifted every byte of calibration by one — after which the pad
    still enumerated and Steam still showed the correct artwork, so nothing looked wrong.
  * **Do not serve a perfect twin of a real DualSense.** `hid-playstation` keys a controller by the
    MAC in feature report `0x09` and names its sysfs entries after it, so an identical one evicts
    the real pad: it binds usbhid, gets no hidraw node, and silently stops being a gamepad.
    Observed. The committed blobs carry inputtino's public addresses instead, which also keeps
    hardware identity out of the repo.

### Turning it on: one authentication, and no standing privilege

Attaching to vhci is a privileged sysfs write, and it is the *only* privileged step. The relay is
started through pkexec, loads `vhci-hcd`, takes a port and hands the kernel one end of the
socketpair — all before it opens a device or starts a thread — and then `setuid`s back to the
invoking user. What runs for the length of a play session is an ordinary user process holding a
socket the kernel has already accepted.

Three consequences worth knowing:

  * **Stopping it is a plain SIGTERM** from the session that started it. `setuid` from root sets the
    real, effective and saved uid together, so the process is genuinely the user's afterwards; a
    process that had only dropped its effective uid would keep root as its saved uid and refuse the
    signal.
  * **Detaching needs no privilege either.** vhci's receive loop sees the socket close, raises
    `VDEV_EVENT_DOWN` and resets the port to `VDEV_ST_NULL` — which is exactly the state a free port
    is in. The explicit detach write is a courtesy that makes it immediate.
  * **The pad is opened as the user, not as root.** This is deliberate: opening it before the drop
    would work regardless of the udev rules and make them look unnecessary until DS mode was started
    any other way. An unreadable node is reported with the rule to install.

**It is a switch, not a per-game route.** Tiers 1-3 need per-game data — vibration binds, telemetry
rules, memory offsets — which is why the gamelist exists and why the daemon picks a route per game.
This tier needs none of it, so it is one control for the whole system and `isPS5` is no longer a
route in `flydigi/prefs.py`. That also removes the hard problem: an unattended per-game attach would
mean granting the desktop session standing permission to emulate USB devices, which is a local
privilege-escalation primitive, since one of those devices is a keyboard.

### Using it

  * **Turn it on before starting the game.** A game opens its stream to the controller's audio
    device once, at launch. Switching DS mode on while it is already running gives it a pad it will
    happily use and an endpoint it will never look for again — triggers work, haptics stay silent,
    and it reads as a broken feature rather than a missed handshake. Restart the game.
  * **A game sees both pads**, and nothing can hide a physical one from a game that enumerates it.
    Launch with `SDL_GAMECONTROLLER_IGNORE_DEVICES=0x37d7/0x2501`, or set it globally in Steam.
  * **Steam Input must be off** for that game: it masks the pad as an Xbox controller, which breaks
    DualSense semantics and the four-channel audio the haptics arrive on.

### The SBC route, superseded

Before the USB/IP route worked, the plan was an SBC in peripheral mode: a real UDC, a configfs
gadget with `hid.usb0` + `uac1.usb0`, and one cable to the PC. It was proven as far as it could be
without a cable -- on an Orange Pi PC 2 (Allwinner H5, Armbian, kernel 6.18) every module ships
prebuilt (`CONFIG_USB_F_UAC1=m`, `CONFIG_USB_F_HID=m`, `CONFIG_USB_RAW_GADGET=m`), there is a real
UDC at `musb-hdrc.4.auto` with `dr_mode = otg`, and the gadget bound cleanly with no host attached,
producing `/dev/hidg0` and a 4-channel `UAC1Gadget` capture device.

Two things from that work are worth keeping:

  * **The configfs direction convention, settled by measurement.** `c_*` is host→gadget and arrives
    on the board as an ALSA **capture** device. Getting it backwards yields a gadget that enumerates
    perfectly and carries no audio, which is indistinguishable from success until you look for
    samples.
  * **A configfs binary attribute can store less than you wrote, silently.** The 289-byte report
    descriptor stored as 151 bytes; the gadget then bound, enumerated, and described itself as
    something else. Read attributes back and compare lengths.

The tooling is gone because the USB/IP route needs no hardware, no cable and no root on the SBC --
only `vhci-hcd`, which every distro ships. The route would still work; it is just strictly more
expensive. It remains the only option for presenting the pad to a machine that is not running our
software at all -- a console, a Windows box, or a Steam Deck.

## M1-M4 buttons: no DualSense destination

Reading M1-M4 from the vendor stream is easy, but there is nowhere in the DualSense protocol to
deliver them, so the scope is smaller than it first looks.

Emulating a **DualSense Edge is the wrong answer**:
  * it has two back buttons, not four;
  * even on a real Edge they have no HID inputs of their own -- they must be remapped onto existing
    buttons in the controller;
  * its different hardware ID *loses* native DualSense support in some games. DSX has a "DualSense
    Emulation" mode and Special K an "Identify DualSense Edge as DualSense" option precisely to undo
    this, and `ds5-edge-relay` exists to convert an Edge into a plain DualSense.

What reading them is still worth:
  * **M1 -> touchpad click**, which frees SELECT to be Create (its correct mapping). Today we
    sacrifice Create because there is no other source for touchpad-click.
  * **daemon-side actions** that never reach the game: profile switching, toggling the relay,
    cycling trigger presets.

For anything else the pad's own onboard remapping is the better mechanism -- it works with no
software running and persists in controller memory.

