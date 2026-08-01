# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The charging dock's own state: its four switches and its lighting.

A device of its own, not an accessory of the pad -- and there may be more than
one, so nothing here says "the dock". Which dock this is showing comes from
`gui/models/devices.py`; this holds what that one answered.

**Lighting is frames, not a mode byte.** The dock has no effect generator: it
plays what it is given, so choosing an effect computes fifty frames of 162 LEDs
here and uploads about 24 kB in 487 packets, which takes a few seconds. That is
why writing has a busy state and a progress signal, where the pad's lighting
needs neither. `flydigi/charger.py` owns the arithmetic.

`custom` is the mode with a picture behind it, and most of this file is that
half: a source image framed on a 334x304 canvas, one pixel per LED out of it,
and the result played back on the wedge before any of it goes to the dock.
`flydigi/charger.py` owns the sampler and the geometry; Qt does the decoding,
because a zero-dependency backend has no business reading a GIF.

One of Flydigi's ten modes is still missing, and stays missing: `default` needs
a file their installer ships and this repository does not have.
"""
import math
import os

from PySide6.QtCore import (Property, QObject, QRectF, QSize, QStandardPaths, Qt,
                            QUrl, Signal, Slot)
from PySide6.QtGui import QImage, QImageReader, QPainter
from PySide6.QtQml import QmlElement

from flydigi import charger

from . import imaging
# The three fit modes are imported rather than referred to through `imaging`
# because `IMAGE_FIT_MODES` below is a list indexed by them.
from .imaging import (FIT_FILL, FIT_INSIDE, FIT_STRETCH,  # noqa: F401
                      ZOOM_MAX, ZOOM_MIN, CropFrame, rgb_bytes)

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# Space Station's dropdown order, minus the two that cannot be computed, plus
# `solid` -- which is in the firmware's enum, absent from their dropdown, works,
# and is an obvious thing to want. Same list as `tools/flydigi-charger`.
MODES = [
    ("Off", charger.MODE_CLOSE),
    ("Solid", charger.MODE_SOLID),
    ("Breath", charger.MODE_BREATH),
    ("Diagonal flow", charger.MODE_DIAGONAL_FLOW),
    ("Gradient", charger.MODE_GRADIENT),
    ("Wave gradient", charger.MODE_WAVE_GRADIENT),
    ("Rainbow", charger.MODE_RAINBOW),
    ("Pulse", charger.MODE_PULSE),
    ("Picture", charger.MODE_CUSTOM),
]
MODE_NAMES = [name for name, _id in MODES]

DIRECTIONS = [("Right", charger.DIR_RIGHT), ("Left", charger.DIR_LEFT),
              ("Down", charger.DIR_DOWN), ("Up", charger.DIR_UP)]
DIRECTION_NAMES = [name for name, _id in DIRECTIONS]

# How many colours each mode's generator actually reads, and which read a
# direction. Straight off the generators, so the page can grey out a control
# that would do nothing rather than accept a setting the dock ignores.
USES_COLOUR = {charger.MODE_SOLID: 1, charger.MODE_BREATH: 1,
               charger.MODE_PULSE: 1, charger.MODE_DIAGONAL_FLOW: 2,
               charger.MODE_WAVE_GRADIENT: 2}
USES_DIRECTION = (charger.MODE_RAINBOW, charger.MODE_WAVE_GRADIENT)

# The switches, in the order the page shows them, with Flydigi's own labels and
# what each actually does.
# The switches, in the order the page shows them, with what each actually does
# and Flydigi's own name for it kept alongside -- their labels are what Space
# Station shows and what a search finds, and "Intelligent start" says nothing
# whatever about turning two devices' lighting off.
SWITCHES = [
    ("sleep_when_charging", "Sleep while docked",
     "both the pad and the dock go dark while a pad sits in it "
     "— Flydigi call this “Intelligent start”"),
    ("led_sync", "Lighting sync",
     "keep the dock's lighting in step with the pad's"),
    ("close_with_system", "Close when shut down",
     "go dark when the host powers off"),
    ("show_animation_when_charging", "Power display",
     "play the charge animation while a pad is docked"),
]

# The two that sleep-while-docked overrides. Named here rather than in the page
# so the CLI and the app agree about what conflicts with what.
DIMMED_BY_SLEEP = ("led_sync", "show_animation_when_charging")
SWITCH_LABELS = {name: label for name, label, _note in SWITCHES}


# -- framing a picture -----------------------------------------------------
#
# Space Station's DIY page lays the source image out on a 640x320 stage with the
# 334x304 crop window cut out of the middle of it, and lets you drag the image
# under that window and zoom it. Every number here is theirs; the gesture itself
# is `imaging.CropFrame`, which the Screen page drives against its own window.

STAGE_WIDTH, STAGE_HEIGHT = 640, 320

# **This page has a fit control and Space Station has none.** Theirs fits width
# for a portrait image and height for everything else, which is filling for most
# pictures and under-covers slightly for any landscape between square and
# 334:304 -- a square photo lands 304px wide in a 334px window. That gap turns
# out to reach nothing: the sampler's own columns run 30..306, the bare margin is
# at most 15px a side, and no LED has ever been in it at any aspect ratio. So
# filling here is their branch with a harmless gap closed, and the two other
# modes are offered because letterboxing is a reasonable thing to want rather
# than because anything was broken.
IMAGE_FIT_MODES = ["Fill the panel", "Fit inside", "Stretch"]

# **One period unit is 20 ms, measured on the dock.** This is the number Space
# Station gets wrong: their writer sends `round(ms / 10)` while their own
# on-page preview replays at `20 x period`, so one of the two had to be wrong
# about the firmware and it is the writer. An animation authored at 100 ms a
# frame and uploaded their way plays at 200 on the hardware here -- observed
# directly, the dock running at half the speed this page was previewing.
#
# So this divides by 20 where they divide by 10, and the preview and the dock
# then agree. A still still goes up with their `period: 1`, which is what it is
# for. Nothing about the *computed* effects changes: their periods are Space
# Station's own slider values, uploaded unaltered, and they already play at
# whatever the dock does with them.
PERIOD_MS = 20
DEFAULT_INTERVAL_MS = 100
INTERVAL_MIN_MS = PERIOD_MS                  # one unit
INTERVAL_MAX_MS = 255 * PERIOD_MS            # `period` is one byte

# The trim bar's filmstrip, drawn at twice Space Station's 590x36 so it stays
# sharp. One image rather than a thumbnail per frame: 200 files to draw a strip
# 3px per frame wide would be absurd.
FILMSTRIP_WIDTH, FILMSTRIP_HEIGHT = 1180, 72


def decode_limit(width, height):
    """`imaging.decode_limit` against this window. See it for the reasoning."""
    return imaging.decode_limit(charger.CROP_WIDTH, charger.CROP_HEIGHT,
                                width, height)


def _mean_delay(delays):
    """One frame interval for the whole animation, as Space Station takes it.

    The wire has a single period for an animation, so per-frame timing is
    thrown away by both applications. Theirs divides the last frame's end
    timestamp by the frame count, which is the mean whenever a GIF's frames run
    back to back -- and taking the mean rather than the longest delay matters
    for the ordinary GIF that holds its final frame: that one would otherwise
    play the whole thing at the speed of its pause.
    """
    usable = [d for d in delays if d > 0]
    if not usable:
        return DEFAULT_INTERVAL_MS
    mean = charger.js_round(sum(usable) / len(usable))
    return max(INTERVAL_MIN_MS, min(INTERVAL_MAX_MS, mean))


def _load_note(total, read):
    """What to say about a file with more frames than can be used."""
    if total > read:
        return (f"{total} frames in the file and {read} read — the frame count "
                f"is one byte on the wire, so {charger.MAX_FRAMES} is the "
                f"ceiling. The bar starts on the first {charger.SAFE_FRAMES}.")
    if read > charger.SAFE_FRAMES:
        return (f"{read} frames, and the bar starts on the first "
                f"{charger.SAFE_FRAMES} — as many as Space Station will send "
                f"at once, and the most this dock has been given.")
    return ""


def to_hex(colour):
    return "#{:02x}{:02x}{:02x}".format(*colour)


def from_hex(text):
    text = str(text).lstrip("#")
    # QML hands back "#AARRGGBB" whenever the colour carries an alpha channel.
    if len(text) == 8:
        text = text[2:]
    if len(text) != 6:
        return (0, 0, 0)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


@QmlElement
class DockModel(QObject):
    """What the Dock page binds to, for whichever dock is selected."""

    changed = Signal()
    lightingChanged = Signal()
    busyChanged = Signal()
    # The picture half. Kept apart from `lightingChanged` because it fires on
    # every pan event and the effect controls have no reason to re-evaluate.
    imageChanged = Signal()
    # And the preview cursor is kept apart from *that*, because it ticks ten
    # times a second while an animation plays. On one signal, every framing
    # property would re-evaluate per tick -- and the trim slider, which has to
    # be written to rather than bound, would be written to underneath a drag.
    previewChanged = Signal()
    # Requests out. The selector rides along on every one of them: the page
    # binds to whichever dock is chosen, and a write must not land on the dock
    # that happened to be selected when the worker last looked.
    refreshRequested = Signal(str)
    switchRequested = Signal(str, str, bool)          # selector, name, value
    lightingRequested = Signal(str, dict)             # selector, config

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selector = ""
        self._present = False
        self._info = {}
        self._uid = ""
        self._nickname = ""
        self._mode = charger.MODE_PULSE
        self._brightness = 50
        self._period = 2
        self._direction = charger.DIR_NONE
        self._colours = [charger.BLUE]
        self._docked = None
        self._dock_battery = -1
        self._busy = False
        self._progress = 0.0
        self._error = ""
        # -- the picture
        self._source = ""              # the file it came from
        self._images = []              # every decoded source frame
        self._frame = CropFrame(charger.CROP_WIDTH, charger.CROP_HEIGHT,
                                STAGE_WIDTH, STAGE_HEIGHT)
        self._trim = (0, 0)            # inclusive, into `_images`
        self._interval = DEFAULT_INTERVAL_MS
        self._preview_frame = 0
        # Sampling one frame costs about half a millisecond, so frames are
        # taken on demand and remembered rather than all recomputed whenever
        # the picture moves under the window. Dragging re-samples the one frame
        # on screen; playing an animation fills this in as it goes round.
        self._sampled = {}
        self._filmstrip = ""
        self._filmstrip_serial = 0
        self._image_message = ""

    # -- which dock --------------------------------------------------------

    @Slot(str)
    def setSelector(self, selector):
        """Point this model at a dock and read it. A no-op for the same one.

        Re-reading on every selection change rather than caching per dock: two
        docks are two devices with their own state, and showing the last one's
        lighting under the new one's name for a second is the kind of wrong a
        person acts on.
        """
        selector = str(selector or "")
        if selector == self._selector:
            return
        self._selector = selector
        self._present = False
        self.changed.emit()
        if selector:
            self.refreshRequested.emit(selector)

    @Property(str, notify=changed)
    def selector(self):
        return self._selector

    @Property(bool, notify=changed)
    def present(self):
        """Whether the selected dock has answered. False before the first read."""
        return self._present

    @Slot()
    def reload(self):
        if self._selector:
            self.refreshRequested.emit(self._selector)

    # -- what came back ----------------------------------------------------

    @Slot(dict)
    def stateReceived(self, state):
        """One whole read: heartbeat, uid, nickname and the LED header."""
        if state.get("selector") and state["selector"] != self._selector:
            # A reply for a dock that is no longer on screen. Dropped rather
            # than shown: the read was started before the picker moved.
            return
        self._present = True
        self._error = ""
        self._info = dict(state.get("info") or {})
        self._uid = state.get("uid") or ""
        self._nickname = state.get("nickname") or ""
        lighting = state.get("lighting") or {}
        if lighting:
            self._mode = int(lighting.get("mode", self._mode))
            self._brightness = int(lighting.get("brightness", self._brightness))
            self._period = int(lighting.get("period", self._period))
            self._direction = int(lighting.get("direction", self._direction))
            colours = lighting.get("colours")
            if colours:
                self._colours = [tuple(c) for c in colours]
        status = state.get("status")
        if status is None:
            self._docked, self._dock_battery = None, -1
        else:
            self._docked = bool(status.get("docked"))
            self._dock_battery = int(status.get("battery", -1))
        self.changed.emit()
        self.lightingChanged.emit()

    @Slot(str)
    def failed(self, message):
        self._error = str(message or "")
        self.changed.emit()

    @Slot(float)
    def progressReceived(self, fraction):
        self._progress = float(fraction)
        self.busyChanged.emit()

    @Slot(bool)
    def writeFinished(self, ok):
        self._busy = False
        self._progress = 0.0
        self.busyChanged.emit()
        if ok and self._selector:
            # Read the header back rather than trusting what was sent: the
            # dock's reply to a write says nothing about what it changed, which
            # is the same reason every device-settings write re-reads.
            self.refreshRequested.emit(self._selector)

    # -- the switches ------------------------------------------------------

    # One property per switch, rather than one `switchValue(name)` a view calls.
    #
    # **A binding on a method never updates.** QML re-evaluates a binding when a
    # property it read changes, and a slot call declares no dependency at all --
    # so `checked: App.dock.switchValue("led_sync")` evaluated once, before the
    # dock had answered, and sat at false for the rest of the session while the
    # dock said otherwise. Four properties notified by `changed` is more lines
    # and is the only shape that works.

    @Slot(str, result=bool)
    def switchValue(self, name):
        """For a caller that has the wire name in hand -- a test, not a binding."""
        return bool(self._info.get(name))

    @Slot(str, bool)
    def setSwitch(self, name, value):
        if not self._selector:
            return
        # Optimistic, then corrected by the read that follows: the page should
        # not feel like it lags a device that answers in milliseconds.
        self._info[name] = bool(value)
        self.changed.emit()
        self.switchRequested.emit(self._selector, str(name), bool(value))

    @Property(bool, notify=changed)
    def sleepWhenCharging(self):
        return bool(self._info.get("sleep_when_charging"))

    @sleepWhenCharging.setter
    def sleepWhenCharging(self, value):
        self.setSwitch("sleep_when_charging", value)

    @Property(bool, notify=changed)
    def ledSync(self):
        return bool(self._info.get("led_sync"))

    @ledSync.setter
    def ledSync(self, value):
        self.setSwitch("led_sync", value)

    @Property(bool, notify=changed)
    def closeWithSystem(self):
        return bool(self._info.get("close_with_system"))

    @closeWithSystem.setter
    def closeWithSystem(self, value):
        self.setSwitch("close_with_system", value)

    @Property(bool, notify=changed)
    def showAnimationWhenCharging(self):
        return bool(self._info.get("show_animation_when_charging"))

    @showAnimationWhenCharging.setter
    def showAnimationWhenCharging(self, value):
        self.setSwitch("show_animation_when_charging", value)

    # -- what the page shows -----------------------------------------------

    @Property(str, notify=changed)
    def firmware(self):
        return self._info.get("firmware") or ""

    @Property(str, notify=changed)
    def uid(self):
        return self._uid

    @Property(str, notify=changed)
    def nickname(self):
        return self._nickname

    @Property(int, notify=changed)
    def deviceType(self):
        return int(self._info.get("device_type", -1))

    @Property(str, notify=changed)
    def model(self):
        return charger.name_for(self._info.get("device_type")) or ""

    @Property(str, notify=changed)
    def dockedState(self):
        """One sentence about what is sitting in it.

        The charge goes through `charger.describe_battery`, which reads the byte
        the way a controller's own battery is read -- 0..5, with 6 meaning
        charging. A bare number here would be the same mistake this app made
        with the pad's own battery for months, and worse: a seated pad is
        charging, so the value it is most likely to carry is the one that would
        print as "battery 6".
        """
        if self._docked is None:
            return "no status report in the last second"
        if not self._docked:
            return "nothing docked"
        if self._dock_battery < 0:
            return "a controller is docked"
        return ("a controller is docked, "
                + charger.describe_battery(self._dock_battery))

    @Property(str, notify=changed)
    def error(self):
        return self._error

    # -- lighting ----------------------------------------------------------

    @Property(int, notify=lightingChanged)
    def modeIndex(self):
        for index, (_name, mode) in enumerate(MODES):
            if mode == self._mode:
                return index
        return -1

    @modeIndex.setter
    def modeIndex(self, index):
        index = int(index)
        if not 0 <= index < len(MODES):
            return
        mode = MODES[index][1]
        if mode == self._mode:
            return
        self._mode = mode
        # Every mode has its own defaults in Space Station -- a period, a
        # colour list, a direction -- and jumping between them without taking
        # those defaults leaves a rainbow running at a breath's frame interval.
        period, colours, direction = charger.MODE_DEFAULTS.get(
            mode, (1, (), charger.DIR_NONE))
        self._period = period
        self._direction = direction
        if colours:
            self._colours = [tuple(c) for c in colours]
        self.lightingChanged.emit()

    @Property(list, notify=lightingChanged)
    def modeNames(self):
        return MODE_NAMES

    @Property(int, notify=lightingChanged)
    def brightness(self):
        return self._brightness

    @brightness.setter
    def brightness(self, value):
        value = max(charger.BRIGHTNESS_MIN,
                    min(charger.BRIGHTNESS_MAX, int(value)))
        if value != self._brightness:
            self._brightness = value
            self.lightingChanged.emit()

    @Property(int, notify=lightingChanged)
    def period(self):
        """Flydigi's "frame interval": bigger is slower."""
        return self._period

    @period.setter
    def period(self, value):
        low, high = charger.MODE_PERIOD_RANGE.get(
            self._mode, charger.PERIOD_RANGE_FALLBACK)
        value = max(low, min(high, int(value)))
        if value != self._period:
            self._period = value
            self.lightingChanged.emit()

    @Property(int, notify=lightingChanged)
    def periodMin(self):
        return charger.MODE_PERIOD_RANGE.get(
            self._mode, charger.PERIOD_RANGE_FALLBACK)[0]

    @Property(int, notify=lightingChanged)
    def periodMax(self):
        return charger.MODE_PERIOD_RANGE.get(
            self._mode, charger.PERIOD_RANGE_FALLBACK)[1]

    @Property(bool, notify=lightingChanged)
    def isPicture(self):
        """Whether the chosen effect is a picture rather than a calculation.

        The page hides most of the lighting card in that state, brightness
        included: Space Station's picture path sends 100 with no control over
        it, and a slider that silently did nothing would be worse than no
        slider.
        """
        return self._mode == charger.MODE_CUSTOM

    @Property(int, notify=lightingChanged)
    def coloursUsed(self):
        """How many colours this mode's generator reads. Zero means it ignores them."""
        return USES_COLOUR.get(self._mode, 0)

    @Property(bool, notify=lightingChanged)
    def usesDirection(self):
        return self._mode in USES_DIRECTION

    @Property(list, notify=lightingChanged)
    def colours(self):
        return [to_hex(c) for c in self._colours]

    @Property(list, notify=lightingChanged)
    def directionNames(self):
        return DIRECTION_NAMES

    @Property(int, notify=lightingChanged)
    def directionIndex(self):
        for index, (_name, value) in enumerate(DIRECTIONS):
            if value == self._direction:
                return index
        return 0

    @directionIndex.setter
    def directionIndex(self, index):
        index = int(index)
        if 0 <= index < len(DIRECTIONS) and DIRECTIONS[index][1] != self._direction:
            self._direction = DIRECTIONS[index][1]
            self.lightingChanged.emit()

    @Slot(int, str)
    def setColour(self, index, text):
        colour = from_hex(text)
        while len(self._colours) <= index:
            self._colours.append(colour)
        if self._colours[index] != colour:
            self._colours[index] = colour
            self.lightingChanged.emit()

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(float, notify=busyChanged)
    def progress(self):
        return self._progress

    # -- a picture ---------------------------------------------------------
    #
    # The source image sits on a 640x320 stage with the 334x304 crop window cut
    # out of the middle. What this model holds is where the image sits on that
    # stage and how big it is drawn; the page shows exactly that and reports
    # drags back. Everything else -- the crop, the 162 samples, the frames that
    # go on the wire -- falls out of those two numbers, so there is one place
    # where framing is decided and the preview cannot disagree with the upload.

    @Slot(QUrl, result=bool)
    def openImage(self, url):
        """Load a picture or an animation. False, with a message, if Qt can't.

        Every frame is decoded now, because the trim bar needs a filmstrip and
        the page needs an honest frame count before anyone starts a two-minute
        upload. Sampling is *not* done now -- that waits for a frame to be
        wanted, since the framing is about to be changed anyway.
        """
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        if not path:
            return False
        reader = QImageReader(path)
        # Matches `autoTransform: true` on the page's own Image. Without the
        # pair, a photo with an EXIF rotation is framed upright and sampled
        # sideways.
        reader.setAutoTransform(True)
        # What is *kept* is bounded, which is the part that matters for a long
        # animation. Not the peak: `QImageReader.supportsOption(ScaledSize)` is
        # false for Qt's GIF and PNG handlers, so those decode each frame at
        # full size and scale it afterwards -- one frame's worth of peak, and a
        # 200-frame 1080p GIF still takes a noticeable second to open.
        stated = reader.size()
        limit = decode_limit(stated.width(), stated.height())
        if limit != (stated.width(), stated.height()):
            reader.setScaledSize(QSize(*limit))
        frames, delays = [], []
        while len(frames) < charger.MAX_FRAMES:
            image = reader.read()
            if image.isNull():
                break
            frames.append(image)
            delays.append(reader.nextImageDelay())
            if not reader.supportsAnimation():
                break
        if not frames:
            self.clearImage()
            self._image_message = f"Qt could not read {os.path.basename(path)}"
            self.imageChanged.emit()
            return False

        total = max(reader.imageCount(), len(frames))
        self._source = path
        self._images = frames
        self._frame.set_natural(frames[0].width(), frames[0].height())
        self._interval = _mean_delay(delays)
        # Space Station's own opening trim, and the reason it is not simply
        # every frame: their bar will not select more than 200 at a time.
        self._trim = (0, min(len(frames), charger.SAFE_FRAMES) - 1)
        self._preview_frame = 0
        self._image_message = _load_note(total, len(frames))
        self._sampled = {}
        self._write_filmstrip()
        self.imageChanged.emit()
        self.previewChanged.emit()
        self.lightingChanged.emit()
        return True

    @Slot()
    def clearImage(self):
        self._source = ""
        self._images = []
        self._frame.set_natural(0, 0)
        self._sampled = {}
        self._trim = (0, 0)
        self._preview_frame = 0
        self._image_message = ""
        self._clear_filmstrip()
        self.imageChanged.emit()
        self.previewChanged.emit()
        self.lightingChanged.emit()

    def _reframed(self):
        """The picture moved under the window: nothing sampled is still true."""
        self._sampled = {}
        self.imageChanged.emit()
        self.previewChanged.emit()

    def _render(self, index):
        """One source frame, framed onto the 334x304 crop canvas."""
        if not 0 <= index < len(self._images):
            return self._frame.render(None)
        return self._frame.render(self._images[index])

    def _sample(self, index):
        """One frame's 162 colours, computed once per framing."""
        if index not in self._sampled:
            self._sampled[index] = charger.sample_frame(
                rgb_bytes(self._render(index)))
        return self._sampled[index]

    def _write_filmstrip(self):
        """Every source frame in one strip, for the trim bar to sit on.

        One file rather than a thumbnail apiece: at two hundred frames the strip
        gives each of them three pixels, and writing two hundred files to draw
        that would be silly.
        """
        self._clear_filmstrip()
        if not self._images:
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        os.makedirs(folder, exist_ok=True)
        strip = QImage(FILMSTRIP_WIDTH, FILMSTRIP_HEIGHT, QImage.Format_RGB888)
        strip.fill(Qt.black)
        painter = QPainter(strip)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        step = FILMSTRIP_WIDTH / len(self._images)
        for index, image in enumerate(self._images):
            painter.drawImage(QRectF(index * step, 0, step, FILMSTRIP_HEIGHT),
                              image)
        painter.end()
        # The serial is in the name, not a query string: Qt caches an Image by
        # its URL, and a new picture must not show the last one's strip.
        self._filmstrip_serial += 1
        path = os.path.join(folder, f"dock-filmstrip-{self._filmstrip_serial}.png")
        self._filmstrip = path if strip.save(path, "PNG") else ""

    def _clear_filmstrip(self):
        if self._filmstrip:
            try:
                os.unlink(self._filmstrip)
            except OSError:
                pass
        self._filmstrip = ""

    # what the page binds to

    @Property(bool, notify=imageChanged)
    def hasImage(self):
        return bool(self._images)

    @Property(str, notify=imageChanged)
    def imageName(self):
        return os.path.basename(self._source) if self._source else ""

    @Property(str, notify=imageChanged)
    def imageSource(self):
        """The file itself, for the page to draw on the stage."""
        return QUrl.fromLocalFile(self._source).toString() if self._source else ""

    # The size the page must ask Qt to decode the same file at. **Not
    # cosmetic**: the crop stage loads the picture a second time through QML,
    # and `decode_limit` bounds only this model's copy. See the comment on
    # `sourceSize` in `gui/qml/pages/DockPage.qml` for what that costs.

    @Property(int, notify=imageChanged)
    def sourceWidth(self):
        return self._images[0].width() if self._images else 0

    @Property(int, notify=imageChanged)
    def sourceHeight(self):
        return self._images[0].height() if self._images else 0

    @Property(str, notify=imageChanged)
    def imageMessage(self):
        return self._image_message

    @Property(str, notify=imageChanged)
    def filmstripSource(self):
        return QUrl.fromLocalFile(self._filmstrip).toString() if self._filmstrip else ""

    # the stage, in the coordinates the page lays it out in

    @Property(int, constant=True)
    def stageWidth(self):
        return STAGE_WIDTH

    @Property(int, constant=True)
    def stageHeight(self):
        return STAGE_HEIGHT

    @Property(int, constant=True)
    def holeX(self):
        return self._frame.hole_x

    @Property(int, constant=True)
    def holeY(self):
        return self._frame.hole_y

    @Property(int, constant=True)
    def holeWidth(self):
        return charger.CROP_WIDTH

    @Property(int, constant=True)
    def holeHeight(self):
        return charger.CROP_HEIGHT

    @Property("QVariantList", notify=imageChanged)
    def renderedSize(self):
        """How big the picture is drawn on the stage, at this fit and zoom."""
        return list(self._frame.rendered_size())

    @Property(float, notify=imageChanged)
    def imageX(self):
        return self._frame.pan[0]

    @Property(float, notify=imageChanged)
    def imageY(self):
        return self._frame.pan[1]

    @Property(float, notify=imageChanged)
    def imageDrawWidth(self):
        return self._frame.rendered_size()[0]

    @Property(float, notify=imageChanged)
    def imageDrawHeight(self):
        return self._frame.rendered_size()[1]

    @Property(bool, notify=imageChanged)
    def canPan(self):
        """False when the picture is smaller than the window on both axes."""
        return bool(self._images) and self._frame.can_pan()

    @Slot(float, float)
    def panBy(self, dx, dy):
        if not self._images:
            return
        self._frame.pan_by(dx, dy)
        self._reframed()

    @Slot()
    def framingSettled(self):
        """A drag ended. Nothing owed here: sampling one frame is half a
        millisecond, so the wedge has been keeping up the whole way down.
        The Screen page's is not free and this is the hook it needs."""

    @Property(int, notify=imageChanged)
    def zoom(self):
        return self._frame.zoom

    @zoom.setter
    def zoom(self, value):
        if self._frame.set_zoom(value):
            self._reframed()

    @Property(int, constant=True)
    def zoomMin(self):
        return ZOOM_MIN

    @Property(int, constant=True)
    def zoomMax(self):
        return ZOOM_MAX

    @Property(str, notify=imageChanged)
    def zoomLabel(self):
        return self._frame.zoom_label()

    @Property(int, notify=imageChanged)
    def imageFitMode(self):
        return self._frame.fit

    @imageFitMode.setter
    def imageFitMode(self, index):
        if self._frame.set_fit(index):
            self._reframed()

    @Property(list, constant=True)
    def imageFitModes(self):
        return IMAGE_FIT_MODES

    # the trim range, inclusive at both ends like Space Station's

    @Property(int, notify=imageChanged)
    def sourceFrameCount(self):
        return len(self._images)

    @Property(int, notify=imageChanged)
    def trimMin(self):
        return self._trim[0]

    @Property(int, notify=imageChanged)
    def trimMax(self):
        return self._trim[1]

    @Slot(int, int)
    def setTrim(self, low, high):
        """Both ends at once, so a drag cannot cross itself mid-update.

        Bounded at one frame and at `charger.SAFE_FRAMES`, which is Space
        Station's own ceiling. When the span has to give it is the end that did
        *not* move that gives, so the handle under the pointer stays under it.
        """
        if not self._images:
            return
        last = len(self._images) - 1
        low = max(0, min(last, int(low)))
        high = max(0, min(last, int(high)))
        if high < low:
            low, high = high, low
        if high - low + 1 > charger.SAFE_FRAMES:
            if low != self._trim[0]:
                high = low + charger.SAFE_FRAMES - 1
            else:
                low = high - charger.SAFE_FRAMES + 1
        if (low, high) != self._trim:
            self._trim = (low, high)
            if not 0 <= self._preview_frame < high - low + 1:
                self._preview_frame = 0
            self.imageChanged.emit()
            # The first selected frame may be a different frame now, so what the
            # wedge is showing has changed even though the cursor has not.
            self.previewChanged.emit()

    @Property(int, notify=imageChanged)
    def frameCount(self):
        """How many frames the dock would be sent."""
        if not self._images:
            return 0
        return self._trim[1] - self._trim[0] + 1

    @Property(bool, notify=imageChanged)
    def animated(self):
        return self.frameCount > 1

    @Property(int, notify=imageChanged)
    def intervalMs(self):
        return self._interval

    @intervalMs.setter
    def intervalMs(self, value):
        value = max(INTERVAL_MIN_MS, min(INTERVAL_MAX_MS, int(value)))
        if value != self._interval:
            self._interval = value
            self.imageChanged.emit()

    @Property(int, constant=True)
    def intervalMin(self):
        return INTERVAL_MIN_MS

    @Property(int, constant=True)
    def intervalMax(self):
        return INTERVAL_MAX_MS

    @Property(int, constant=True)
    def intervalStep(self):
        """One unit of what the dock stores, in ms. Measured, not assumed."""
        return PERIOD_MS

    # the preview

    @Property(int, notify=previewChanged)
    def previewFrame(self):
        """Which trimmed frame the wedge is showing. Not a source index."""
        return self._preview_frame

    @previewFrame.setter
    def previewFrame(self, index):
        index = int(index)
        if self.frameCount:
            index %= self.frameCount
        else:
            index = 0
        if index != self._preview_frame:
            self._preview_frame = index
            self.previewChanged.emit()

    @Property(list, notify=previewChanged)
    def frameColours(self):
        """The 162 colours on the wedge right now. Empty for a dark panel."""
        if not self._images or not self.frameCount:
            return []
        return [to_hex(c)
                for c in self._sample(self._trim[0] + self._preview_frame)]

    @Property(list, constant=True)
    def wedgeCentres(self):
        """[x0, y0, x1, y1, …] for the preview, flat so nothing is unpacked."""
        flat = []
        for x, y in charger.wedge_centres():
            flat += [x, y]
        return flat

    @Property(str, constant=True)
    def wedgeOutline(self):
        return charger.WEDGE_OUTLINE

    @Property(int, constant=True)
    def wedgeViewWidth(self):
        return charger.WEDGE_VIEW[0]

    @Property(int, constant=True)
    def wedgeViewHeight(self):
        return charger.WEDGE_VIEW[1]

    @Property(float, constant=True)
    def wedgeRadius(self):
        return charger.WEDGE_RADIUS

    # what it will cost

    @Property(int, notify=imageChanged)
    def imagePackets(self):
        """Packets, counted the way `charger.write_led_config` counts them."""
        if not self.frameCount:
            return 0
        blob = 6 + self.frameCount * charger.LED_COUNT * 3
        return math.ceil(blob / charger.PACK_BYTES)

    @Property(str, notify=imageChanged)
    def imageEstimate(self):
        """The upload's size in words, before anyone commits to waiting for it.

        Measured on this dock: about a hundred packets a second, each one
        waiting for its own ack. Fifty frames is the five seconds an effect
        already takes and two hundred is about twenty -- long enough to want
        saying beforehand rather than discovering.
        """
        packets = self.imagePackets
        if not packets:
            return ""
        seconds = packets / 100.0
        if seconds < 1.5:
            return f"{packets} packets, about a second"
        if seconds < 90:
            return f"{packets} packets, about {round(seconds)} seconds"
        return f"{packets} packets, about {seconds / 60.0:.0f} minutes"

    @Property(bool, notify=imageChanged)
    def canApplyImage(self):
        return bool(self._images) and self.frameCount > 0

    @Slot()
    def apply(self):
        """Compute the frames and send them. Several seconds of packets.

        The effects' frames are generated on the worker thread, not here: fifty
        frames of 162 LEDs is a real amount of arithmetic and the UI thread is
        the one place it must not happen. A picture's frames are the exception
        and are sampled here, because the source images live in this model and
        two hundred of them take about a tenth of a second.

        They cross as one flat `bytes`. As nested lists the same animation is
        ninety-seven thousand Python integers through a queued signal, and a
        value Qt cannot marshal makes a cross-thread call vanish with no error
        at all.
        """
        if self._busy or not self._selector:
            return
        wanted = {
            "mode": self._mode,
            "brightness": self._brightness,
            "period": self._period,
            "direction": self._direction,
            "colours": [list(c) for c in self._colours[:max(1, self.coloursUsed)]],
        }
        if self._mode == charger.MODE_CUSTOM:
            if not self.canApplyImage:
                return
            frames = [self._sample(self._trim[0] + offset)
                      for offset in range(self.frameCount)]
            wanted["frames"] = charger.pack_frames(frames)
            # Flydigi's own numbers for a picture, and there is no control for
            # either on their page: full brightness, and a period in
            # centiseconds -- 1 for a still.
            wanted["brightness"] = 100
            wanted["period"] = (
                1 if len(frames) == 1
                else max(1, charger.js_round(self._interval / PERIOD_MS)))
            wanted["direction"] = charger.DIR_NONE
            wanted["colours"] = []
        self._busy = True
        self._progress = 0.0
        self.busyChanged.emit()
        self.lightingRequested.emit(self._selector, wanted)
