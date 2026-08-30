# Apex 5 screen

Reference for the 160×80 display, uploads, safety gates, and settings. The wire
format and protocol state machines are documented in
[PROTOCOL.md](../PROTOCOL.md) §8. Verified against the repository on
2026-08-23.

## Safety requirements

### Upload only over a wired connection

The working serial upload is **wired only**. Over the dongle, command 31 can put
the screen controller into upgrade mode, but the bootloader's USB CDC interface
cannot appear through the wireless transport. The upload then waits for a tty
that will never arrive.

The GUI refuses an upload unless the current connection is wired:

- `ScreenModel.canUpload` disables the action.
- `ScreenModel.upload()` repeats the gate for non-UI callers.
- `DeviceWorker.upload_screen()` re-reads connection state immediately before
  command 31, covering a cable removed after the button was pressed.

When it needs to send command 31, the CLI applies the same refusal. `--i-know`
bypasses it for serial `show`, `animate`, and `send`; use that flag only for
protocol recovery or development with the consequences understood. `--port`
means the pad is already switched and therefore skips both the check and
command 31.

If a pad was switched while wireless, power-cycle it with its physical switch
and reconnect by cable.

### Do not interrupt resource synchronization

After a successful write, allow approximately 15 seconds for resource
synchronization and automatic restart. Do not cut controller power during this
stage.

### Serial uploads over 255 frames are unsafe

The picture count is one byte. HID uploads reject more than 255 frames and the
GUI truncates to 255 with a warning. The current serial backend does **not**
enforce the limit: it sends `len(frames) & 0xff`, while CLI `animate` and `send`
can pass every frame. For example, 300 frames are announced as 44 while all 300
frame payloads are written.

Do not send more than 255 frames through the serial CLI. This is a current
validation gap, not a supported wraparound format.

### Command 242 is not a harmless screen test

Command 242 also floods the controller RGB LEDs. `test off` does not reliably
clear the state; the known recovery is a physical power cycle. Use the `test`
subcommand only on hardware that can be safely restarted.

## CLI reference

`tools/flydigi-screen` uses Pillow for `show`, `animate`, `convert`, and
`preview`. `check` is backend-only.

| Subcommand | Behavior |
|---|---|
| `check FILE` | Decode and re-encode every frame of a `.bin` and compare bytes. |
| `preview FILE OUT.png` | Decode a `.bin` to a vertical frame sheet, or one frame with `--frame N`. |
| `convert IMAGE OUT.bin` | Convert offline with `--mode fill|fit|stretch`. |
| `status` | Read screen-related command-3 flags. |
| `on` / `off` | Set the always-on-display behavior through command 19/9. |
| `statusbar on|off` | Set the status-bar flag through command 19/8. |
| `test RRGGBB` / `test off` | Send command 242; see the safety warning above. `--faithful` reproduces Flydigi's byte layout. |
| `probe` | Send only the HID-family start envelope for command 208. |
| `show IMAGE` | Upload the first source frame at a 100 ms frame interval. |
| `animate IMAGE` | Upload up to the caller-supplied frame set using the source delay or `--interval`. Keep it at 255 frames or fewer. |
| `send FILE.bin` | Upload encoded frames unchanged at `--interval` or 100 ms. Keep it at 255 frames or fewer. |

Controller-facing subcommands accept `--device SELECTOR` and `--wait`; a
selector may be a node path, UID or UID prefix, MAC address, or nickname.
`show`, `animate`, and `send` also accept:

| Option | Meaning |
|---|---|
| `--via serial|hid` | Upload backend. Serial is the default and the working display path. |
| `--port PATH` | Use an already-switched bootloader tty. This skips the connection check and command 31. |
| `--restore-default` | Restore the stock display resource after upload; CLI only. |
| `--dialect new|a5|bare` | HID command envelope. `new` is the default. |
| `--i-know` | Bypass the serial wired-connection refusal. |

`show` and `animate` additionally accept `--mode fill|fit|stretch`. Pillow uses
Lanczos resampling; `flydigi.screen.fit` provides a dependency-free box-down /
nearest-up implementation for backend callers.

## Serial upload path

`flydigi/screen_ota.py` implements the working path:

1. Re-read connection state.
2. Send command 31 to put the screen chip into upgrade mode.
3. Wait 5 seconds for the USB transition.
4. Find the matching `ttyACM*` or `ttyUSB*` by vendor/product in sysfs, for up
   to 30 seconds.
5. Open the port with DTR and RTS asserted and exchange bootloader packets with
   an 18-second reply timeout.
6. Synchronize resources and restart.

The controller commonly leaves the bus before acknowledging command 31, so a
missing ACK is not itself failure; appearance of the bootloader tty is the
decisive event.

The tty normally appears as `root:dialout`. Install the project udev rules
before uploading:

```bash
tools/apex5-setup install-rules
```

If permission fails after the pad has already switched, correct the rules and
retry with `--port`; a second command 31 is unnecessary. The normal port finder
also recognizes an already-switched pad.

`--restore-default` sets the bootloader restore flag and expects the stock
`default_screen_image_<deviceType>.bin` resource. The GUI always leaves this
flag false.

Frame interval fields differ between backends: HID stores `interval // 100`,
while serial stores `round(interval / 10)`; both clamp to one byte. Only the
serial form has been shown to update the panel.

## GUI behavior

The Screen page estimates upload time at 25 seconds per frame and does not offer
cancellation. Image decoding, fitting, encoding, and preview generation run
asynchronously after the file is opened. Upload remains disabled until the
encoded frame set matches the current crop and fit settings.

Each encoded frame is decoded back into the cached preview so the animation
shows the bytes the pad will receive rather than a separately scaled source.
The model clamps animation interval to 10-2550 ms and the preview timer to at
least 20 ms.

Screen upload bypasses the worker's ordinary retry wrapper. Repeating a
multi-minute upload automatically would be unsafe when the first attempt has
already switched the pad into bootloader mode.

## HID picture family

Commands 208-211 acknowledge and echo correctly on Apex 5 but have not produced
a visible picture. They can still modify persistent status-bar metadata, and a
208/211 sequence can replace stored metadata for a frame that was never sent.
Use `--via hid` only for protocol investigation, not normal uploads.

`probe` sends command 208 without the later commit for the least invasive
envelope check. The `new` (`5A A5`) dialect is the only envelope used by working
commands on this device.

## Display settings

- Command 19 subcommand 9 controls the always-on display. Its wire value is
  inverted relative to the English `OffScreen` name; the API exposes
  `set_always_on()` using user-facing meaning.
- Command 19 subcommand 8 controls the status bar and can restore the flag after
  an HID-family experiment.
- Command 31 targets the screen upgrade path used here. The picture uploader
  never sends the program-chip upgrade type.

Tests are in `tests/test_screen.py` and `tests/test_screen_ota.py`; the OTA suite
uses `FakeScreenChip` from `tests/fake_pad.py`.
