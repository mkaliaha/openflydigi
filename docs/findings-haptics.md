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

  **Superseded — see "Haptic audio" below.** Original finding retained for context:

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

The architecturally correct fix is one virtual USB composite device exposing both interfaces, so
the kernel creates the hidraw node and the ALSA card from the same device:

    dummy_hcd   provides a virtual UDC (this laptop is USB host-only, so there is no real one)
    configfs    gadget with hid.usb0 + uac2.usb0, VID:PID 054c:0ce6

Both modules are present on the kernel. Target spec, from the real device:
`s16le 4ch 48000Hz`, `alsa.components = USB054c:0ce6`, `device.bus = usb`, haptics on ch3.

**Tested and ruled out: PipeWire property spoofing.** Wine synthesises the Windows device instance
id from the underlying Linux device — USB devices become `USB\VID_xxxx&PID_xxxx\...`, everything
else `ROOT\MEDIA\N`, and that string is what ties an audio endpoint to a HID device. A null sink
was given every property the real device carries (`device.bus=usb`, `device.vendor.id=0x054c`,
`device.product.id=0x0ce6`, `sysfs.path`, `alsa.components`), then the node name and description
were made byte-identical to the real device's. Wine still assigned `ROOT\MEDIA\N` and the game
never opened the sink. Per Wine development discussion, winepulse resolves identity through the
**sysfs path** and looks it up in setupapi — a virtual node has no kernel device to find.

**Why uhid cannot close this.** uhid creates HID devices only; it has no audio concept and no way to
attach one. A real DualSense is a composite USB device whose HID and audio interfaces are siblings
under one USB device. Only real (or emulated) USB device topology produces that.

This is not Linux-specific: a virtual audio device on Windows needs an audio driver, and DSX ships
a virtual gamepad bus driver rather than one — consistent with DSX's virtual pad also failing to
produce haptics in Death Stranding DC.

**Untested idea worth revisiting.** Plug in a real DualSense purely as a haptic transducer, but
unbind its HID interface so the game cannot see it as a gamepad:

    echo -n "0003:054C:0CE6.00XX" | sudo tee /sys/bus/hid/drivers/playstation/unbind

The audio card stays (snd-usb-audio is untouched), so there is a genuine USB DualSense audio
endpoint with a proper instance id, while input comes from our virtual pad. If the game then writes
haptics to it, matching is **by name** and a cleverer virtual device might work; if not, matching is
**by association** and only real USB topology will ever do. Either way it answers the question we
could not settle, because the earlier fake-sink test failed for a different reason (no USB instance
id at all). Note `SDL_GAMECONTROLLER_IGNORE_DEVICES` cannot be used to hide the real pad -- our
virtual one shares its VID/PID.

Of limited practical value on its own (it needs a DualSense physically attached), but diagnostically
decisive.

**Blocked on this kernel.** Fedora ships neither `usb_f_uac2` nor `raw_gadget`, so there is no way
to present a USB audio interface without building and signing a kernel module — an ongoing chore on
a Secure Boot, auto-updating, ostree system. `dummy_hcd`, `vhci-hcd`, `usb_f_hid` and `usb_f_fs`
are all present and Fedora-signed, so the HID half is easy; only audio is missing.

**Open question: does a gaming distro ship these?** Not answered — searching returned only generic
distro comparisons. Worth checking directly rather than guessing, since some ship custom kernels for
handheld hardware that needs gadget mode (the Steam Deck has a real dual-role USB port, so SteamOS
plausibly enables UAC2 gadget). To check on any candidate:

    zcat /proc/config.gz | grep -E 'F_UAC2|RAW_GADGET'      # on a live/booted system
    # or inspect the distro's kernel spec/config in its repo

Candidates: SteamOS, Bazzite, CachyOS, Nobara. If one ships `usb_f_uac2`, the whole gadget route
becomes a rebase instead of a build-and-sign treadmill.

Remaining routes, none cheap: build `usb_f_uac2` and sign it; implement UAC2 over FunctionFS
including isochronous endpoints (no reference implementation exists); or rebase to an image that
ships the module.

**Deliberately not pursued:** deriving rumble from the game's own audio output. It fires on music
and dialogue and does not resemble real haptics.

**Status: parked.** The conversion works and is proven against real game haptics; it needs a real
DualSense present as the source. Reviving this means solving the audio-device emulation above.

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

