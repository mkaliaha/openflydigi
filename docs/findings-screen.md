# The 160x80 screen

How the panel is driven. The wire protocol — image format, both upload families, the settings and
the OTA state machine — is [PROTOCOL.md](../PROTOCOL.md) §8.

Index: [PROGRESS.md](../PROGRESS.md).

## Uploading a picture

`flydigi/screen.py` (image format, the HID picture family, command 242 and the two settings),
`flydigi/screen_ota.py` (the serial upload that works), `tools/flydigi-screen`, the GUI's Screen
page, and `tests/test_screen.py` + `tests/test_screen_ota.py`, whose `FakeScreenChip` in
`tests/fake_pad.py` stands in for `OtaLink` and the bootloader tty.

An upload runs at ~19 exchanges a second, ~25 s a frame. PROTOCOL.md §8d has the full timings, the
base address and the hardware runs; §8a has the 25604-byte LVGL frame that
`tools/flydigi-screen check` round-trips against Flydigi's own files. An upload is **wired only**, and that is
the pad's own behaviour rather than a house rule of Space Station's — theirs refuses a wireless one
before the request reaches the device, and **measured here, the reason they do is real**: on the
dongle the pad accepts command 31 and switches its screen chip into upgrade mode, and nothing
appears on the PC at all. The dongle does not relay the bootloader's USB CDC device and has no
notion that there is one, so the tty the upload waits for cannot arrive. What is left is a pad in
upgrade mode, a timeout for a diagnosis, and its own power switch for a fix.

So the refusal has to come **before** command 31, and it does, in three places: `canUpload` and
`upload()` in `gui/models/screen.py`, the connection re-read in `worker.upload_screen` — which also
catches a cable pulled between pressing the button and the command going out — and
`tools/flydigi-screen`, where `--i-know` sends it regardless. This was a real bug: every one of
those gates was missing, and sending a picture on the dongle was a supported-looking way to strand
the pad.

### The command line

`tools/flydigi-screen` needs Pillow for `show`, `animate`, `convert` and `preview`; `check` is pure
backend and needs nothing.

| Subcommand | What it does |
|---|---|
| `check FILE` | decode and re-encode every frame of a `.bin`, byte for byte |
| `preview FILE OUT.png` | frames back to a PNG — all of them stacked into one sheet 160 wide and 80×N tall, or one with `--frame N` |
| `convert IMAGE OUT.bin` | turn an image into a `.bin` offline, `--mode fill\|fit\|stretch` |
| `status` | the screen bits out of command 3, read-only — the only free probe here |
| `on` / `off` | 19 sub 9, the always-on display |
| `statusbar on\|off` | 19 sub 8 |
| `test RRGGBB` / `test off` | command 242; `--faithful` sends Flydigi's own byte layout |
| `probe` | ask each envelope whether the pad knows 208 — announces a frame it never sends |
| `show IMAGE` | upload **only the first frame** of whatever file it is given, always at 100 ms |
| `animate IMAGE` | every frame, at the file's own longest delay unless `--interval` overrides it |
| `send FILE.bin` | a `.bin` unchanged, at `--interval` or 100 ms |

`convert` resamples with Pillow's Lanczos. `flydigi/screen.fit` is the pure-Python resampler behind
the same three modes — box averaging down, nearest up — for a caller with no imaging library.

`show`, `animate` and `send` share `--via serial|hid`, `--port`, `--restore-default` and
`--dialect`, and `show`/`animate` take `--mode` as well; `serial` is the default and the route that
works. `--wait` (seconds to wait for each reply, default 0.5) is on every subcommand that talks to
the pad.

### Driving the serial route

`k5` is the one device code Space Station's `upload_pic2screen` sends over serial rather than over
the HID picture family (`IpcCommandEnum_UploadPic`, the branch every other pad takes), and this
project takes the same route: `flydigi/screen_ota.py` sends command 31 as `CMD_SWITCH_USB` /
`enter_upgrade_mode` and then writes the frames over the bootloader tty (PROTOCOL.md §8d), reached
from `tools/flydigi-screen` and the GUI's Screen page.

`screen_ota.upload_picture(ctrl, frames, interval_ms, restore_default, progress, settle, port)` is
the one-call entry point that switches the pad, waits, finds the port and writes; the CLI and the
GUI worker both open-code the same sequence instead. `find_port()` resolves the bootloader by
reading `idVendor`/`idProduct` under `/sys/class/tty/ttyACM*/device/../` (and `ttyUSB*`) and
`wait_for_port()` polls it, so `--port` is unnecessary on a retry — the tool finds an
already-switched pad by itself. `OtaLink` opens the tty with termios and asserts DTR and RTS, as
their `SerialPort` does. Timings: `SWITCH_SETTLE` 5.0 s after command 31, `PORT_TIMEOUT` 30.0 s
polled every 0.5 s for the tty, `REPLY_TIMEOUT` 18.0 s per exchange. `enter_upgrade_mode` catches
`OSError` and returns False, and False is not a failure — the pad usually leaves the bus before it
can ACK, so the real check is whether the tty appears.

The bootloader tty lands as `root:dialout`: without `udev/72-flydigi-apex5.rules` an upload gets as
far as finding the port and then cannot open it, with the pad already switched over, so
`flydigi/setup.py` **fails** an absent rules file rather than skipping it. Recovering costs nothing:
retry with `--port`, no second command 31 needed.

`--restore-default` sends `isRestoreDefault = 1`, which with the stock
`default_screen_image_<deviceType>.bin` puts the factory animation back. It is CLI-only —
`ScreenModel.upload` always emits False, so the GUI never sets it. The other ways back out are
PROTOCOL.md §8e.

Do not cut power during the **~15 second resource sync** after a successful write: "It will restart
automatically when done. Please do not turn off the device."

### The 255-frame ceiling and the interval fields

**The 255-frame ceiling is a one-byte picture-count field, and the path that works does not enforce
it.** It is checked in two places only: the HID path (`flydigi/screen.py`,
`if len(frames) > 255: raise ScreenError`) and the GUI, which truncates at `MAX_FRAMES = 255` and
says so. `screen_ota.upload` sends `len(frames) & 0xFF` as the count, and `animate`/`send` pass
frames through uncapped, so a 300-frame GIF writes all 300 frames of data while telling the chip
there are 44.

The frame interval is a one-byte field too, and the two paths scale it by different divisors: the
HID start packet uses `interval // 100` (`screen.period_from_interval`), the serial path uses
`round(interval / 10)` (`screen_ota.frame_rate`), both clamped to 1..255. Only the serial one
reaches the screen.

### The GUI page

`gui/models/screen.py` and `gui/qml/pages/ScreenPage.qml` put the frame count and the time estimate
(`SECONDS_PER_FRAME = 25.0`) beside the upload button: an upload runs for minutes and **cannot be
cancelled**. **Every** frame is encoded and decoded back to a cached PNG at load time, so the
preview is what the pad will get rather than a scaled copy of the source, and the page plays the
frames at the chosen interval — an animation is checked before a multi-minute upload rather than
after. The interval is clamped to 10..2550 ms and the preview timer runs at `max(20, interval)` ms.

The upload bypasses the worker's `_attempt` retry: a silent second attempt would re-run a
multi-minute write on a pad already in upgrade mode.

### The HID picture family, which puts no picture on the panel

**The HID picture family answers on this pad and puts no picture on the panel**: two complete
uploads, 9623 packets, no errors, every field echoed back, and no uploaded frame ever appeared
(PROTOCOL.md §8b). What it does change is stored state — the two uploads left the status-bar flag
flipped to always-on, which survives a power cycle and needs 19/8 to put back, and 211 commits
metadata for a frame that was never sent (below).

Nothing is missing from the sequence. The packet order is `UploadPicCommandK2Factory`'s; the chunk
size is not theirs, since they send `(XInput ? 32 : 64) - 6` bytes where this project sends
`device.PACKET_LEN - 8` = 24. Nothing wraps it either: `UploadPicAsync` calls the repository with a
300 s timeout and `AddCommandsToCommunicationManager` queues the list, with no preamble or
epilogue. And **the family carries no separate commit**: every command id in the SDK was mapped,
208..211 is the whole of it, and there is no picture equivalent of the mapping family's
save-to-flash 166. 211 is itself the commit, which is why `probe()` sends only the start — 208
followed by 211 destroys a stored custom image (§8b), and even the lone start leaves an
announced-but-unsent frame behind.

`--via hid` and `--dialect {new,a5,bare}` are the flags that reach this path. `new` (`5A A5`) is the
default and the only envelope any working command on this pad uses.

## Command 242

**242 floods the RGB LEDs as well as the screen, and `test off` does not clear it**: the command
ACKs, the pad stays flooded, and the only exit found is the pad's own power switch — both measured
on a wired Apex 5. The packet layout, and the length-byte disagreement `--faithful` reproduces, are
PROTOCOL.md §8c.

## The two screen settings

`19` sub `9` is an always-on-display switch, not a screen-off switch: Flydigi's name for the bit,
`OffScreen` (息屏显示, the standard Chinese term for an always-on display), inverts against its
English reading, and `enabled=False` is **a real screen blank**. The measurement and the wire layout
are PROTOCOL.md §8c; the rest of the command-19 sub-ids are
[device-settings.md](device-settings.md).

`flydigi/screen.py` reports the command-3 bits as `always_on_usable`/`always_on` and writes them
with `set_always_on`, which takes what you mean rather than the wire value. `19` sub `8` is the
status bar — `set_status_bar_always_on`, which is what puts that flag back after the HID uploads
flip it.

## Command 31 for the screen chip

Command 31 is `SwitchToFirmwareUpgradeMode` / `SwitchUsb`, and it puts one named chip into upgrade
mode. `screen_ota.enter_upgrade_mode` takes no chip argument, so the screen is the only chip it can
reach — [findings-other-devices.md](findings-other-devices.md) has the chip list and why a *program*
chip has no way back. The bounds on a picture upload — the base address read back from the device,
`ScreenUpgradeType.PROGRAM` never sent, `--restore-default` as the way home — are PROTOCOL.md §8d.

31 for the screen is milder than the name: the pad keeps its own `37d7:2501` hidraw nodes with the
bootloader tty live, so it *adds* a CDC interface beside the gamepad rather than replacing the
device, and the main firmware keeps running (PROTOCOL.md §8d — the nodes were checked, not whether
input still flowed).
