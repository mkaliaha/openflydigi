# Haptic audio, and the virtual DualSense that cannot carry it

Tier 4 works; its one structural gap is haptics. What was measured, what was built,
what it is blocked on, and the untested experiment that would settle it.

Index: [PROGRESS.md](../PROGRESS.md).

## Deathloop: what tier 4 does and does not deliver

Adaptive triggers **work in game**. Confirmed the transcribed mapping behaves exactly like
Flydigi's: the game sent `type=0x25 p[0]=12` → `mode 3 [70,0,12]` and `type=0x21 p[1]=3` →
`mode 1 [140,1]`, both matching their table's branches. Zero unmapped patterns.

Two gaps remain, neither in the transport:

- **No rumble — investigated and closed as a known limitation of virtual DualSense emulation.**

  The DualSense has no conventional rumble motors; its voice coils do both jobs. Games can drive
  them two ways: `motor_left`/`motor_right` in the HID output report (the compatibility path, which
  we already support and which most PC ports use), or arbitrary waveforms written to the
  controller's USB *audio* device (the rich PS5 haptics). Deathloop uses the audio path.

  Confirmed by testing: with the real Apex 5 as a plain Xbox pad the game vibrates readily, even on
  a menu button press — so it does emit motor rumble, just not to a DualSense. Our own output path
  is proven: a direct cmd `0x12` rumbles the pad and ACKs.

  What the audio path needs is in *Haptic audio* below; the null-sink attempt that came first is
  recorded here because its negative result still stands.

  **Built, tested, negative result.** Neither Flydigi nor DSX implements audio haptics — verified
  by decompilation: Space Station bundles no audio libraries at all, and its `EnableAudio` command
  is a device feature toggle, not PC audio capture.

  We built the missing piece anyway (`tools/flydigi-haptics`): a fake 4-channel DualSense sink
  (`pipewire/99-dualsense-haptics.conf`) plus a bridge that measures haptic-channel energy and
  converts it to motor rumble. **The bridge works** — verified with `tools/haptics-simulate`, which
  plays synthetic gunshots and engine rumble and produces correctly decaying motor values.

  **But games do not use it.** With the sink present and named "Wireless Controller", Deathloop
  opened exactly one audio stream and routed it to the speakers; our sink measured absolute silence
  (peak 0.00000). A virtual pad has no OS-level link between its HID device and an audio endpoint,
  and an unassociated sink is not picked up.

  Cannot distinguish "looked for a controller endpoint and rejected ours" from "never looks on PC".
  The outcome is the same either way. Tooling is kept because it is proven working — if a game is
  ever found that does write to such a sink, only the sink config needs reinstalling.

  Two notes for anyone re-running this: `pw-record` prepends a file header and will silently
  misalign a raw reader (use `parec --raw`), and `paplay --raw` declares no channel map so PipeWire
  remixes the channels — do not assume fixed haptic channel indices.

  **Practical consequence, per game:** for titles using haptic audio, choose adaptive triggers
  (DS5 mode) or rumble (plain Xbox mode). Titles using the HID motor path get both, and that is
  the majority.
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

Deathloop writes **ch3 only** (active in 87% of 373 sampled windows; ch1 never touched). Treat
haptics as mono rather than assuming stereo.

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

**What this does not do:** it requires a real DualSense present as the haptic source. Making the
Apex work standalone needs the game to write haptics to a device we control — see the USB gadget
note below.

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

**Nobody has emulated a virtual DualSense with a working audio device.** Every project either uses
real hardware or emulates HID only (inputtino, DSX). The audio half of virtual emulation is
unexplored, consistent with the blockers below.

## Virtual USB composite device (not built)

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

**The HID report descriptor is 289 bytes and is not the one we emulate.** inputtino's copy in
`flydigi/ps5_data.py` is 273 bytes; the two are identical for 145 bytes and then diverge, because
inputtino lacks feature reports `0x0B` and `0x0C` (usages `0x41`/`0x42`, 41 bytes each). Presumably
an older firmware. Nothing has been observed to care, but the real one is what a host compares
against, so `tools/ds5-gadget` embeds the captured descriptor rather than the inputtino one.

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
`vhci-hcd` — the client-side virtual *host* controller — enumerate it locally over loopback TCP.
`vhci-hcd` implements isochronous fully, and it is `=m` on every distro checked without exception.
None of the three blockers above applies: no descriptor whitelist, no endpoint autoconfiguration, no
gadget bind.

The resulting device is an ordinary USB device in sysfs, so `snd-usb-audio` and `hid-playstation`
bind to it as true siblings and both container ids derive by construction. Prior art:
**`usbipdcpp`** (C++, LGPL-3.0, active) implements per-packet iso on the virtual-device path and
reports iso streaming through `vhci-hcd` demonstrated with a virtual UVC camera — the same transport
problem as UAC1. Loopback round-trip measured here at p50 10.7 µs against a 1000 µs budget.

Two cautions. `VIIPER` is the only existing USB/IP DualSense emulator and is **HID-only**
(`bInterfaceClass = 0x03`, no audio interfaces), so the device itself still has to be written. And
several popular USB/IP libraries `recv()` a fixed 48 bytes and never consume iso packet descriptors,
which desynchronises the TCP stream rather than failing cleanly — check for iso handling before
adopting one.

### The SBC route

Both blockers are properties of *this machine*, not of the idea. An SBC in peripheral mode has a
**real** UDC, and on Armbian building a module is `apt install linux-headers-*` — no Secure Boot, no
ostree, no signing.

That makes the pad a self-contained dongle. The Apex 5 plugs into a type-A port; a single cable goes
to the PC; the PC needs no software at all:

    Apex 5 --USB-A--> SBC --OTG--> PC
                      gadget 054c:0ce6 = hid.usb0 + uac1.usb0

The board reads the pad (evdev + vendor stream, as tier 4 does now), writes DualSense reports to
`/dev/hidg0`, receives haptic audio on the gadget's ALSA capture side, runs the conversion in
`flydigi/haptics.py`, and drives the pad's motors over its own USB. `/dev/hidg0` is read-write, so
output reports come back on the same fd — simpler than the uhid path, not harder. Steam's
double-listing problem disappears with it, since the pad is not on the PC at all.

`tools/ds5-gadget` sets this up: `up`, `down`, `status`.

### The board, and what it has already proved

**Orange Pi PC 2** (Allwinner H5, aarch64 — *not* the H3 "PC"), Armbian 26.5.1 trixie, kernel
6.18.33-current-sunxi64. It arrives with everything needed already built:

    CONFIG_USB_F_UAC1=m     CONFIG_USB_F_HID=m     CONFIG_USB_RAW_GADGET=m
    CONFIG_USB_CONFIGFS_F_UAC1=y                   CONFIG_USB_MUSB_DUAL_ROLE=y

    /sys/class/udc/musb-hdrc.4.auto -> .../soc/1c19000.usb/musb-hdrc.4.auto
    /proc/device-tree/soc/usb@1c19000/dr_mode = otg

No building, no headers, no signing. The whole reason this was parked on Fedora does not exist here.

**`tools/ds5-gadget up` binds cleanly with no host attached** — writing to `UDC` succeeds whether or
not anything is plugged into the OTG port, so most of the stack is testable without a cable. That
run produced:

  * `/dev/hidg0`
  * a new ALSA card, `UAC1Gadget`
  * `c_chmask` reading back `51` = `0x33`, the real device's channel config
  * `phy phy-1c19400.phy.0: Changing dr_mode to 2` — the PHY switching to peripheral on bind

**The configfs direction convention is settled, by measurement.** `c_*` is host→gadget and arrives
on the board as an ALSA **capture** device: `arecord -l` lists `card 2: UAC1Gadget [UAC1_Gadget],
device 0`. So the haptics bridge reads a capture device. Getting this backwards would have produced
a gadget that enumerates perfectly and carries no audio, which is indistinguishable from success
until you look for the samples.

**Only the type-A ports are host-only.** There is exactly one UDC and it is USB0 at `1c19000.usb`,
the micro-USB. The type-A ports hang off separate EHCI/OHCI blocks, which have no device-side state
machine in silicon — no software can make one act as a peripheral.

**`state: not attached` means no VBUS session**, and note a charge-only cable still carries VBUS. So
this reads as either nothing plugged in at the far end, or no `usb0_vbus_det` wired — common on
H3/H5 boards, and if so a perfect data cable will show the same thing. The fix in that case is
forcing `dr_mode = "peripheral"` so musb stops waiting for a session it cannot detect.

**Remaining risk is the controller.** musb, not dwc2; its gadget-side isochronous support is far
less travelled. sunxi musb has roughly five endpoints against the four this device wants — three
with `p_chmask=0` to drop the mic. If isochronous streaming fails there, dwc2 (Pi Zero 2 W, Pi 4) is
the well-trodden platform for gadget audio.

What is left, in order:

 1. **A micro-USB *data* cable.** The only thing blocking everything below. Not an OTG adapter —
    that is micro-B to A-*female* and selects host mode, the opposite of what is wanted.
 2. **Confirm enumeration.** `state` goes to `configured` and the PC shows `054c:0ce6`.
 3. **Does the game write ch3?** *The experiment nobody has run* — every existing project uses real
    hardware or emulates HID only. A positive result decides the whole question.
 4. **Wire the conversion**, `--gain 1.5 --crossover 250`, `rumble(wait=0)`.

**A real PS5 is out of scope regardless.** It does signed challenge/response over feature reports;
none of this touches that.

**Deliberately not pursued:** deriving rumble from the game's own audio output. It fires on music
and dialogue and does not resemble real haptics.

**Status: unparked, at step 1.** The conversion works and is proven against real game haptics; what
it lacked was a source. The SBC route above is the source, and it is blocked on nothing but a
micro-USB cable and an evening. The descriptors are captured and `tools/ds5-gadget` is written.

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

