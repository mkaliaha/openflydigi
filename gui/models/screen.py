# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The pad's 160x80 screen: what to put on it, and what it does when idle.

Two unrelated halves, and the page keeps them apart because their costs are
nothing alike. The **display setting** is one packet and instant. An **upload**
takes about 25 seconds a frame and cannot be cancelled once the pad is across,
so everything here is arranged to make that cost visible before it is paid: the
frame count and the estimate are on screen next to the button, not discovered
afterwards.

Qt does the image work rather than Pillow. `flydigi/screen.py` has a pure-Python
resampler for callers with no imaging library, but this application already
links Qt and `QImageReader` reads animated GIFs frame by frame, which is exactly
the awkward part.
"""
import os

from PySide6.QtCore import (Property, QObject, QSize, QStandardPaths, Qt, QUrl,
                            Signal, Slot)
from PySide6.QtGui import QImage, QImageReader, QPainter
from PySide6.QtQml import QmlElement

from flydigi import screen, screen_ota

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

FIT_MODES = ["Fill the screen", "Fit inside", "Stretch"]
FIT_KEYS = ["fill", "fit", "stretch"]

# The frame count and index are one byte each on the wire.
MAX_FRAMES = 255

# Measured on a wired Apex 5: ~19 exchanges a second, 466 writes and its share
# of the erases per frame. Used for the estimate, which exists because a
# six-minute upload with no warning reads as a hang.
SECONDS_PER_FRAME = 25.0

DEFAULT_INTERVAL_MS = 100


def rgb_bytes(image):
    """RGB888 for a QImage, with Qt's row padding removed.

    Qt aligns every row to four bytes. At 160 wide that is already a multiple of
    four so nothing is added, but a caller that assumed it would be is one
    resolution change away from an image sheared diagonally, which is a
    memorable afternoon.
    """
    image = image.convertToFormat(QImage.Format_RGB888)
    stride = image.bytesPerLine()
    wanted = image.width() * 3
    raw = bytes(image.constBits())
    if stride == wanted:
        return raw
    return b"".join(raw[y * stride:y * stride + wanted] for y in range(image.height()))


def fit_image(image, mode):
    """One image, resampled to the screen the way `mode` says."""
    size = QSize(screen.WIDTH, screen.HEIGHT)
    if mode == "stretch":
        return image.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if mode == "fill":
        scaled = image.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        return scaled.copy((scaled.width() - screen.WIDTH) // 2,
                           (scaled.height() - screen.HEIGHT) // 2,
                           screen.WIDTH, screen.HEIGHT)
    scaled = image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QImage(size, QImage.Format_RGB888)
    out.fill(Qt.black)
    painter = QPainter(out)
    painter.drawImage((screen.WIDTH - scaled.width()) // 2,
                      (screen.HEIGHT - scaled.height()) // 2, scaled)
    painter.end()
    return out


@QmlElement
class ScreenModel(QObject):
    """What the screen shows, and what it does when nobody is looking."""

    changed = Signal()
    uploadRequested = Signal(list, int, bool)
    settingRequested = Signal(int, bool)     # sub-id, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frames = []
        self._interval = DEFAULT_INTERVAL_MS
        self._fit = 0
        self._source = ""
        self._preview = ""
        self._preview_files = []
        self._preview_serial = 0
        self._busy = False
        self._done = 0
        self._total = 0
        self._always_on = False
        self._status_bar = False
        self._supported = False
        self._loaded = False
        self._message = ""

    # -- reading a file ----------------------------------------------------

    @Slot(QUrl, result=bool)
    def open(self, url):
        """Load an image or animation. Returns False and sets `message` if not.

        Every frame is resampled and encoded now rather than at upload time, so
        the count and the estimate are honest before anyone commits to six
        minutes, and so a file Qt cannot read fails here instead of half way
        across the wire.
        """
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        if not path:
            return False
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        frames, delays = [], []
        while len(frames) < MAX_FRAMES:
            image = reader.read()
            if image.isNull():
                break
            frames.append(image)
            delays.append(reader.nextImageDelay())
            if not reader.supportsAnimation():
                break
        if not frames:
            self._message = f"Qt could not read {os.path.basename(path)}"
            self._source = ""
            self._frames = []
            self._preview = ""
            self.changed.emit()
            return False

        truncated = reader.imageCount() > MAX_FRAMES
        self._source = path
        self._images = frames
        # A GIF states its own delay; a still image has none, and 100 ms is what
        # Space Station assumes too.
        moving = [d for d in delays if d]
        self._interval = max(moving) if moving else DEFAULT_INTERVAL_MS
        self._message = (
            f"Only the first {MAX_FRAMES} frames were taken — the frame count is "
            "one byte on the wire" if truncated else "")
        self._reencode()
        return True

    def _reencode(self):
        mode = FIT_KEYS[self._fit]
        self._frames = [screen.encode_frame(rgb_bytes(fit_image(image, mode)))
                        for image in getattr(self, "_images", [])]
        self._write_preview()
        self.changed.emit()

    def _write_preview(self):
        """Render every frame back out, so the preview is what will be sent.

        Deliberately a round trip through the encoder rather than a scaled copy
        of the source: this shows the 565 quantisation and the crop the pad will
        actually get. Written to the cache directory because the models may not
        import QtQuick, so an image provider would have to live in main.py and
        this does not need one.

        All the frames rather than the first, because the page plays them: a
        still frame of an animation tells you almost nothing about it, and the
        upload is far too long to be the thing that shows you what you chose.
        Written once at load rather than regenerated per tick -- a file write
        every 100 ms to animate a preview would be absurd.
        """
        self._clear_previews()
        if not self._frames:
            self._preview = ""
            self._preview_files = []
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.CacheLocation)
        os.makedirs(folder, exist_ok=True)
        # The serial is in the *name*, not a query string: Qt caches by URL, and
        # a new picture must not show the previous one's pixels.
        self._preview_serial += 1
        paths = []
        for index, frame in enumerate(self._frames):
            width, height, rgb = screen.decode_frame(frame)
            image = QImage(rgb, width, height, width * 3, QImage.Format_RGB888).copy()
            path = os.path.join(
                folder, f"screen-preview-{self._preview_serial}-{index}.png")
            if not image.save(path, "PNG"):
                break
            paths.append(path)
        self._preview_files = paths
        self._preview = QUrl.fromLocalFile(paths[0]).toString() if paths else ""

    def _clear_previews(self):
        """Delete the last picture's frames rather than filling the cache."""
        for path in getattr(self, "_preview_files", []):
            try:
                os.unlink(path)
            except OSError:
                pass
        self._preview_files = []

    @Slot()
    def clear(self):
        self._images = []
        self._frames = []
        self._source = ""
        self._clear_previews()
        self._preview = ""
        self._message = ""
        self.changed.emit()

    # -- what QML binds to -------------------------------------------------

    @Property(str, notify=changed)
    def source(self):
        return self._source

    @Property(str, notify=changed)
    def sourceName(self):
        return os.path.basename(self._source) if self._source else ""

    @Property(str, notify=changed)
    def previewSource(self):
        """Frame one. What the page shows for a still, and the fallback."""
        return self._preview

    @Property(list, notify=changed)
    def previewFrames(self):
        """Every frame as a file URL, in order, so the page can play them."""
        return [QUrl.fromLocalFile(path).toString() for path in self._preview_files]

    @Property(int, notify=changed)
    def frameCount(self):
        return len(self._frames)

    @Property(bool, notify=changed)
    def animated(self):
        return len(self._frames) > 1

    @Property(int, notify=changed)
    def interval(self):
        return self._interval

    @interval.setter
    def interval(self, value):
        value = max(10, min(2550, int(value)))
        if value != self._interval:
            self._interval = value
            self.changed.emit()

    @Property(int, notify=changed)
    def fitMode(self):
        return self._fit

    @fitMode.setter
    def fitMode(self, index):
        index = max(0, min(len(FIT_KEYS) - 1, int(index)))
        if index != self._fit:
            self._fit = index
            self._reencode()

    @Property(list, constant=True)
    def fitModes(self):
        return FIT_MODES

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(str, notify=changed)
    def estimate(self):
        """How long the upload will take, in words, before it is started."""
        if not self._frames:
            return ""
        seconds = int(len(self._frames) * SECONDS_PER_FRAME)
        if seconds < 90:
            return f"about {seconds} seconds"
        minutes = seconds / 60.0
        return f"about {minutes:.0f} minutes" if minutes >= 2 else "about a minute and a half"

    @Property(bool, notify=changed)
    def canUpload(self):
        return bool(self._frames) and not self._busy

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(float, notify=changed)
    def progress(self):
        return (self._done / self._total) if self._total else 0.0

    @Property(str, notify=changed)
    def progressText(self):
        if not self._busy:
            return ""
        if not self._total:
            return "Switching the screen into upgrade mode…"
        return f"{self._done} of {self._total} packets"

    @Property(bool, notify=changed)
    def alwaysOn(self):
        """Whether the pad keeps your picture up while it idles.

        The SDK calls this bit `OffScreen` and it is not a screen-off switch --
        see `flydigi/screen.py`. Named here for what it does, so that a checked
        box means a lit screen.
        """
        return self._always_on

    @Property(bool, notify=changed)
    def statusBarAlwaysOn(self):
        return self._status_bar

    @Property(bool, notify=changed)
    def supported(self):
        return self._supported

    @Property(bool, notify=changed)
    def loaded(self):
        return self._loaded

    # -- actions -----------------------------------------------------------

    @Slot()
    def upload(self):
        if not self._frames or self._busy:
            return
        self._busy = True
        self._done = self._total = 0
        self.changed.emit()
        self.uploadRequested.emit(list(self._frames), self._interval, False)

    @Slot(bool)
    def setAlwaysOn(self, value):
        self.settingRequested.emit(screen.SUB_OFF_SCREEN, bool(value))

    @Slot(bool)
    def setStatusBarAlwaysOn(self, value):
        self.settingRequested.emit(screen.SUB_STATUS_BAR, bool(value))

    # -- replies from the worker -------------------------------------------

    @Slot(dict)
    def statusReceived(self, state):
        self._always_on = bool(state.get("always_on"))
        self._status_bar = bool(state.get("status_bar_always_on"))
        self._supported = bool(state.get("always_on_usable"))
        self._loaded = True
        self.changed.emit()

    @Slot(int, int)
    def progressReceived(self, done, total):
        self._done, self._total = done, total
        self.changed.emit()

    @Slot(bool)
    def uploadFinished(self, _ok):
        self._busy = False
        self._done = self._total = 0
        self.changed.emit()
