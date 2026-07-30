# The 160x80 screen

How the panel is driven, why the SDK's HID picture family is a dead end on this
pad, and the serial route that works. The wire protocol is [PROTOCOL.md](../PROTOCOL.md) §8 —
read §8d before touching the upload.

Index: [PROGRESS.md](../PROGRESS.md).

## Upload: done, and validated by putting Bad Apple on the pad

`flydigi/screen.py` (format and settings), `flydigi/screen_ota.py` (the upload that works),
`tools/flydigi-screen`, `tests/test_screen.py` + `tests/test_screen_ota.py`. Full write-up in
PROTOCOL.md §8, and read §8d before touching it.

A test card and a 14-frame Bad Apple are both on a wired Apex 5's screen, written from Linux at base
`0x002ff000`, each followed by the pad rebooting itself. **It is slow and that is inherent**: ~19
exchanges a second, ~25 s a frame, 5 m 46 s for fourteen, and about 1.8 hours at the 255-frame
ceiling. Space Station is stuck with the same arithmetic.

**Where the 255-frame ceiling actually lives, because it is not where it reads.** The limit is the
one-byte picture-count field. It is *enforced* in two places only: the dead HID path
(`flydigi/screen.py`, `if len(frames) > 255: raise ScreenError`) and the GUI, which truncates at
`MAX_FRAMES = 255` and says so. The serial path that works has **no check** —
`flydigi/screen_ota.py` sends `len(frames) & 0xFF` as the count, and `tools/flydigi-screen
animate/send` pass frames through uncapped. So a 300-frame GIF writes all 300 frames of data while
telling the chip there are 44. The guard belongs in `screen_ota.upload`; until it is there, treat the
ceiling as a rule the CLI does not keep for you.

That is why the GUI page is shaped the way it is: the frame count and the time estimate sit next to
the button rather than in a tooltip, because an upload runs for minutes and **cannot be stopped**;
and the preview is the encoded frame decoded back, not a scaled copy of the source, so what you
check is what the pad will get. **Every** frame is encoded and decoded back to a cached PNG at load
time, and the page plays them at the chosen frame interval — an animation is checked before a
multi-minute upload, not after.

**The image format is settled, and settled *hard*.** A frame is 25604 bytes: a 4-byte LVGL v8
header (`cf=4 TRUE_COLOR, 160x80`, the constant `04 80 02 0A`) then 160x80 RGB565 with the **high
byte first**, and a `.bin` is frames concatenated with no container at all. All 14 files Flydigi
ships under `Configs/Controller/{k2,k5}/default/` — 686 frames — decode and re-encode
**byte-identical** through our codec. `tools/flydigi-screen check` runs that against their files and
`preview` renders one back to PNG, which is how the byte order was settled: one way gives an anime
face, the other gives colour noise.

The hint in the old version of this entry was right — the encoding *is* in the Electron layer. It is
the LVGL image converter, and their picker carries `ICF_TRUE_COLOR_ARGB8332 / 8565 / 8565_RBSWAP /
8888` and `CF_RAW` verbatim.

**The transport was the open question, and the answer was that Space Station does not upload to an
Apex 5 over HID at all.** `upload_pic2screen` branches on the device code: everything
else gets `IpcCommandEnum_UploadPic` and the SDK's 208/209/210/211 family, and `k5` gets
`SwitchUsb` — which is `SwitchToFirmwareUpgradeMode`, **command 31**, `chipModule = CHIP_SCREEN`,
`chipType = FREQ` — followed five seconds later by `firmware/FirmwareConsole.exe --upgrade_type 2`
over a temp file of the frames.

So the pad's screen is written by the firmware updater, and that is what this project does.
`flydigi/screen_ota.py` sends command 31 (`CMD_SWITCH_USB`, `enter_upgrade_mode`), reached from
`tools/flydigi-screen` and the GUI's Screen page. It targets **the screen chip only**
(`CHIP_SCREEN`) — `enter_upgrade_mode` takes no chip argument, so no other chip is reachable through
it. Full protocol in PROTOCOL.md §8d.

`FirmwareConsole.exe` unpacks with `sfextract` and decompiles cleanly, and the screen work is all
managed code in `FirmwareLibrary.dll`. It dispatches on chip type, and the screen's — `ChipType.Freq`
— is **the one branch with no vendor blob in it**: `OtaNewUpdater`, a plain request/response UART
protocol. JieLi, WCH, Megahunt and NearLink shell out to `firmware/tool/*`; Freq does not. After
command 31 the pad re-enumerates as a **USB CDC serial device, VID `FFAA` PID `5555`**, 921600 8N1,
and the upload is five opcodes: read the picture base address back from the device, read a version,
erase 4096 at a time, write 55 bytes at a time, then reset with a length and a CRC.

Three things make a *picture* upload much safer than the phrase "firmware upgrade mode" implies. The
picture base address is **read back from the device**, and every erase and write is `base + offset`,
so the program region is only reachable through `ScreenUpgradeType.PROGRAM` — which a picture upload
never sends. `isRestoreDefault = 1` with the stock `default_screen_image_<deviceType>.bin` puts the
factory animation back, so a botched upload has a documented repair. And **coming back out is
something Flydigi document four ways** — a successful upload reboots the pad by itself, a failed one
is cleared by the power switch, a failed flash is expected to be retried, and holding START for 8
seconds restores a misbehaving pad. PROTOCOL.md §8e quotes all four.

The one window where cutting power is worse than waiting is the **~15 second resource sync** after a
successful write: "It will restart automatically when done. Please do not turn off the device."

**Everything past command 31 is proven.** The pad enumerates as `cdc_acm`, `flydigi/screen_ota.py`
finds the port with `wait_for_port()` and drives it through `OtaLink`, and each completed upload
rebooted the pad by itself. The pad's own `37d7:2501` hidraw nodes stay enumerated with the
bootloader tty live, so the two interfaces coexist.

One thing is still open: whether the upgrade-mode flag is volatile — whether a pad switched with 31
but never written to comes back on its own or needs the power switch.

**The HID family answers on this pad and does not drive the screen. Settled on hardware, and it is
a dead end — do not spend another session on it.** All four commands parse: the pad ACKs every
packet and echoes every field back (PROTOCOL.md §8b). Two complete uploads went out, 9623 packets,
no errors — a single test card and an eight-frame animation — and **the display never changed**.
The only persistent trace either left was the status-bar flag flipping to always-on, which survives
a power cycle and needs 19/8 to put back.

Everything that could be a missing step was checked rather than guessed at, because "the pad
accepted it" is worth nothing here:

  * the sequence we send is exactly `UploadPicCommandK2Factory` — start, data x N, end per frame,
    one finish for the set;
  * nothing wraps it: `UploadPicAsync` calls the repository with a 300 s timeout and
    `AddCommandsToCommunicationManager` queues the list, with no preamble or epilogue;
  * and **there is no commit command anywhere in the id space** — every command id in the SDK was
    mapped, and 208..211 is the whole picture family. No 166 equivalent exists for pictures.

The reason is upstream of all that and was in the first file read: **for `k5`, Space Station never
sends the HID family at all.** `upload_pic2screen` branches `deviceCode == "k5" ? SwitchUsb +
FirmwareConsole : IpcCommandEnum_UploadPic`. Every other screen pad takes the HID route; this one
does not. Protocol conformance with no visible effect is what writing to a chip that does not drive
the panel looks like — and note the k2 has the *same* separate `ChipScreen`/`ChipType.Freq`, so
"separate screen chip" is not the discriminator and no source-backed reason for the split was found.

One trap in it: **the reply command byte is not always the command's own.** 210 and 211 answer as
themselves, but 208 and 209 answer as `0x18`/`0x19` — real command ids elsewhere, so not a
"no such command". `screen.ACK_ID` maps it, and the fake pad models the pad rather than the SDK.

**A second trap, and this one cost a picture. 211 is a commit, not punctuation.** 208 followed by
211 commits picture metadata for a frame that was never sent, and on a wired Apex 5 that
**destroys a stored custom image** — the screen falls back to its status view after the next reboot.
`probe()` therefore sends only the start, and even that leaves an announced-but-unsent frame behind.

Two things worth running first, neither of which uploads anything: `flydigi-screen status` (the
screen bits out of command 3, read-only) and `flydigi-screen test ff8000` (command 242).

**242 works, and it is a trap.** It floods the **RGB LEDs as well as the screen**, and `test off`
**does not clear it** — the command ACKs and the pad stays flooded. The only exit found was the
pad's own power switch. Both facts are hardware; the SDK says neither.

Also confirmed: upload is **wired only**, refused in their UI before it reaches the device.

## The two screen settings, and the one whose SDK name is a lie

**`19` sub `9` is an always-on-display switch, not a screen-off switch — the SDK name is inverted.**
Flydigi call the bit `OffScreen` (息屏显示). Measured on a wired Apex 5 while watching the panel:

```
19/9 = 1   the stored picture plays continuously — an always-on display
19/9 = 0   the panel is dark; the logo button wakes the status view for ~2 seconds
```

So `enabled=False` is **a real screen blank**, and it is a control this project exposes that Space
Station does not surface at all. `flydigi/screen.py` reports the command-3 bits as
`always_on_usable`/`always_on` for this reason, and `set_off_screen` — which took the wire value
under the SDK's name — was exactly backwards before it was measured. Anyone re-implementing this
from the SDK name, from PROTOCOL.md §8c, or from `docs/device-settings.md` will get it inverted;
that has already happened once here.

`19` sub `8` is the other one, and is what puts the status-bar always-on flag back after the dead HID
uploads flipped it.

**An ACK to command 19 does not tell you which sub-setting was written.** The reply carries the
command id, not the sub-id, so the family cannot be told apart by its acknowledgement — read command
3 back if you need to know what actually landed.

## Why this sends command 31 when nothing else may

**Command 31 for the screen chip is now something this project does**, and
[findings-other-devices.md](findings-other-devices.md) still says not to send 31. Both are right,
and the distinction is the whole argument: that section is about *program* images across four bootloader vendors with no recovery. A picture
upload shares the transport and nothing else — the device hands back the picture base address so
the program region is never addressed, `ScreenUpgradeType.PROGRAM` is not implemented and should not
be, and the factory image is on disk to restore. `screen_ota.enter_upgrade_mode` takes no chip
argument for exactly this reason: it can reach the screen and nothing else.

**And it is milder than the name.** Measured mid-upload: the pad's own `37d7:2501` hidraw nodes were
still enumerated with the bootloader tty live, so command 31 for the screen *adds* a CDC interface
beside the gamepad rather than replacing the device. The main firmware keeps running. (Nodes
observed, not input — take it at that strength.)

**The one thing that will bite a new machine is the udev rule.** The bootloader tty lands as
`root:dialout`, so without `udev/72-flydigi-apex5.rules` an upload gets as far as finding the port
and then cannot open it, with the pad already switched over. `flydigi/setup.py` therefore **fails**
an absent rules file rather than skipping it, even when every other device is reachable without —
this is the one node that cannot be tested until it is too late to fix. Recovering from that costs
nothing: retry with `--port`, no second command 31 needed.

