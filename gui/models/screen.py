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

**Framing a picture is not free and does not happen on the GUI thread.** Turning
a framing into frames is a pure-Python per-pixel encode of all 12,800 pixels of
every held frame, and then a decode and a PNG write each for the preview. See
`EncodeWorker`: what the page asks for is a framing, and the frames arrive when
they arrive.
"""
import os
import traceback

from PySide6.QtCore import (Property, QCoreApplication, QObject, QSize,
                            QStandardPaths, QThread, QUrl, Signal, Slot)
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtQml import QmlElement

from flydigi import screen, screen_ota

from . import imaging
from .imaging import ZOOM_MAX, ZOOM_MIN, CropFrame, rgb_bytes

# See gui/models/device.py for what these two names do.
QML_IMPORT_NAME = "Apex5"
QML_IMPORT_MAJOR_VERSION = 1

# In `imaging`'s order: fill, fit inside, stretch.
FIT_MODES = ["Fill the screen", "Fit inside", "Stretch"]

# The stage the picture is framed on, in panel pixels: the 160x80 window with
# half a panel of margin all round to drag into. Twice the target rather than
# the dock's near-square proportions, because a 2:1 window is the shape most
# photographs have the least of -- there is nearly always something above and
# below worth choosing between.
STAGE_WIDTH, STAGE_HEIGHT = screen.WIDTH * 2, screen.HEIGHT * 2

# The frame count and index are one byte each on the wire.
MAX_FRAMES = 255

# Measured on a wired Apex 5: ~19 exchanges a second, 466 writes and its share
# of the erases per frame. Used for the estimate, which exists because a
# six-minute upload with no warning reads as a hang.
SECONDS_PER_FRAME = 25.0

DEFAULT_INTERVAL_MS = 100


def estimate_text(count):
    """How long an upload of `count` frames takes, in words.

    Kept apart from the property that shows it so it can be worked out once,
    where the frame count moves, rather than once per read -- see `_set_frames`.
    """
    if not count:
        return ""
    seconds = int(count * SECONDS_PER_FRAME)
    if seconds < 90:
        return f"about {seconds} seconds"
    minutes = seconds / 60.0
    return f"about {minutes:.0f} minutes" if minutes >= 2 else "about a minute and a half"


def unlink_all(paths):
    """Drop preview files, ignoring the ones that are already gone."""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


class EncodeWorker(QThread):
    """One framing, turned into frames and previews, off the GUI thread.

    Measured by the review it was written for: about 1.3 seconds for a
    200-frame animation and about 7 ms for a still. Seven milliseconds is a
    dropped frame; 1.3 seconds is a window that stops answering the mouse, and
    it was being paid at the end of every drag and every zoom.

    **It reads nothing that belongs to the model.** Everything it needs is
    handed over at construction: its own list of source `QImage`s, its own
    `CropFrame` copied off the live one, and the folder to write into. That is
    the whole of the thread-safety argument, and it is why the copy exists --
    the model goes on mutating its `CropFrame` under the next drag while this
    runs. `QImage` and `QPainter`-onto-a-`QImage` are the two pieces of QtGui Qt
    documents as usable away from the GUI thread, which is what makes rendering
    here legal at all; nothing else Qt-ish is touched.

    Follows `gui/models/setup.py`'s `SetupWorker`: a short-lived `QThread`
    carrying one job, reporting once by signal. What is new here is that a job
    can be overtaken, so this one can be told to stop -- see `cancel`.
    """

    done = Signal(int, object, object)   # serial, frames, preview paths

    def __init__(self, serial, images, frame, folder, parent=None):
        super().__init__(parent)
        self._serial = int(serial)
        self._images = images
        self._frame = frame
        self._folder = folder
        self._cancelled = False
        # Accumulated as the work goes rather than returned at the end, so that
        # a frame the encoder chokes on still leaves the files already written
        # somewhere `run` can find and remove them.
        self._frames = []
        self._written = []

    def cancel(self):
        """Give up at the next frame boundary, leaving nothing behind.

        A plain attribute rather than a mutex or a `QAtomicInt`: binding a name
        is atomic under the GIL, so the worst a racing read can do is miss the
        flag for one more frame, and one more frame is 7 ms of a job that is
        being thrown away anyway. Called from the GUI thread only.
        """
        self._cancelled = True

    def run(self):
        try:
            self._work()
        except Exception:                    # a bad frame must not strand
            traceback.print_exc()            # the page in "encoding" for ever
        if self._cancelled:
            # Superseded: the files written so far are the wrong picture and
            # nothing will ever ask for them, so they go here rather than being
            # left for the model to notice. Nothing is emitted either -- see
            # `ScreenModel._encoded` for why a late one would be harmless anyway.
            unlink_all(self._written)
            return
        self.done.emit(self._serial, list(self._frames), list(self._written))

    def _work(self):
        for image in self._images:
            if self._cancelled:
                return
            self._frames.append(
                screen.encode_frame(rgb_bytes(self._frame.render(image))))
        self._previews()

    def _previews(self):
        """Render every frame back out, so the preview is what will be sent.

        Deliberately a round trip through the encoder rather than a scaled copy
        of the source: this shows the 565 quantisation and the crop the pad will
        actually get. Written to the cache directory because the models may not
        import QtQuick, so an image provider would have to live in main.py and
        this does not need one.

        All the frames rather than the first, because the page plays them: a
        still frame of an animation tells you almost nothing about it, and the
        upload is far too long to be the thing that shows you what you chose.
        Written once per framing rather than regenerated per tick -- a file
        write every 100 ms to animate a preview would be absurd.

        The serial is in the *name*, not a query string: Qt caches by URL, and a
        new picture must not show the previous one's pixels. It doubles as what
        keeps two overlapping jobs from writing over each other, since the model
        never hands out the same serial twice.
        """
        try:
            os.makedirs(self._folder, exist_ok=True)
        except OSError:
            # No preview, but the frames are still good and still uploadable.
            return
        for index, frame in enumerate(self._frames):
            if self._cancelled:
                return
            width, height, rgb = screen.decode_frame(frame)
            image = QImage(rgb, width, height, width * 3, QImage.Format_RGB888).copy()
            path = os.path.join(
                self._folder, f"screen-preview-{self._serial}-{index}.png")
            if not image.save(path, "PNG"):
                return
            self._written.append(path)


@QmlElement
class ScreenModel(QObject):
    """What the screen shows, and what it does when nobody is looking.

    **Every property here returns a field.** One model-wide `changed` covers
    thirty-odd bindings across `ScreenPage.qml` and `CropStage.qml`, and a
    property read from QML is an interpreter call wrapping whatever the getter
    does -- so anything a getter computes is computed once per binding rather
    than once per change. That was not academic on this page: a drag emits
    `changed` per mouse move, and `previewFrames` built a fresh list of file
    URLs on every read, so panning a 200-frame animation constructed 200 `QUrl`s
    per binding evaluation per pointer event. The four stage-geometry properties
    each recomputed the rendered size, and `canPan` recomputed it twice more.

    So the derived state is worked out where its inputs move, and `_notify` is
    the only place in this file that emits `changed`. That is what makes it
    impossible to forget: every path that changes anything ends there, and it
    refreshes the framing-derived fields before the emission goes out. The
    fields whose inputs are more expensive to follow -- the source path, the
    encoded frames, the preview files -- are refreshed by the one setter that
    owns each (`_set_source`, `_set_frames`, `_set_previews`), so a caller
    cannot assign the underlying list without also updating what is read from
    it. Nothing outside this class touches any of them.
    """

    changed = Signal()
    uploadRequested = Signal(list, int, bool)
    settingRequested = Signal(int, bool)     # sub-id, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images = []
        self._frames = []
        self._interval = DEFAULT_INTERVAL_MS
        self._frame = CropFrame(screen.WIDTH, screen.HEIGHT,
                                STAGE_WIDTH, STAGE_HEIGHT)
        self._source = ""
        self._source_url = ""
        self._source_name = ""
        self._natural = (0, 0)
        self._estimate = ""
        self._preview = ""
        self._preview_files = []
        self._preview_urls = []
        # What the stage's bindings read, refreshed by `_notify`.
        self._pan = (0.0, 0.0)
        self._drawn = (0.0, 0.0)
        self._can_pan = False
        self._zoom_label = self._frame.zoom_label()
        self._busy = False
        self._done = 0
        self._total = 0
        self._always_on = False
        self._status_bar = False
        self._supported = False
        self._loaded = False
        self._message = ""
        # Empty until the info poll answers, and treated as "not wired" until
        # it does. See `canUpload`.
        self._connection = ""
        # The encode running now, the one waiting for it, and the number that
        # says which results are still wanted. See `_reencode`.
        self._worker = None
        self._queued = None
        self._encode_serial = 0
        self._encoding = False
        # `gui/app.py`'s shutdown waits for the models that own threads, and
        # this one is not on its list; until it is, the model looks after
        # itself. Dropping a running QThread is a qFatal, and an encode is
        # exactly the sort of thing that is in flight when a window is closed.
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_encoding)

    # -- the state everything else is derived from -------------------------

    def _notify(self):
        """Refresh what the framing implies, then tell QML. The only emitter.

        Cheap enough to run on every notification -- a rendered size, a pan and
        one short format -- and being on *every* notification is the point:
        there is then no path that can move the picture and leave the stage
        reading last frame's numbers. The alternative, recomputing only where
        the `CropFrame` is touched, is one forgotten call site away from a stage
        that lags a gesture, and this page has six of them.
        """
        self._pan = self._frame.pan
        self._drawn = self._frame.rendered_size()
        self._can_pan = bool(self._images) and self._frame.can_pan()
        self._zoom_label = self._frame.zoom_label()
        self.changed.emit()

    def _set_source(self, path):
        """The file, and the two spellings of it the page asks for."""
        self._source = path
        self._source_url = QUrl.fromLocalFile(path).toString() if path else ""
        self._source_name = os.path.basename(path) if path else ""

    def _set_images(self, images):
        """The decoded source frames, and the natural size that follows.

        `CropFrame.set_natural` goes with them because a new picture's size is
        what the framing is rebuilt from; separating the two would allow a
        moment where the stage is laid out for the previous picture.
        """
        self._images = images
        self._natural = ((images[0].width(), images[0].height()) if images
                         else (0, 0))
        self._frame.set_natural(*self._natural)

    def _set_frames(self, frames):
        """The encoded frames, and the estimate that follows from how many."""
        self._frames = frames
        self._estimate = estimate_text(len(frames))

    def _set_previews(self, paths):
        """The preview files, the URLs for them, and the removal of the last set.

        One call, so the files on disk and the URLs QML is holding cannot
        disagree, and so nothing can leave the cache directory filling up.
        """
        unlink_all(self._preview_files)
        self._preview_files = list(paths)
        self._preview_urls = [QUrl.fromLocalFile(path).toString()
                              for path in self._preview_files]
        self._preview = self._preview_urls[0] if self._preview_urls else ""

    # -- reading a file ----------------------------------------------------

    @Slot(QUrl, result=bool)
    def open(self, url):
        """Load an image or animation. Returns False and sets `message` if not.

        Every frame is resampled and encoded now rather than at upload time, so
        the count and the estimate are honest before anyone commits to six
        minutes, and so a file Qt cannot read fails here instead of half way
        across the wire.

        The return value is about the *file*: True means Qt read it and the
        stage can show it. Encoding it is what happens next and off this
        thread, so `frameCount`, `estimate` and `previewSource` follow a moment
        later on `changed` rather than being true when this returns.
        """
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        if not path:
            return False
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        # What is kept is bounded by what the window can reach at full zoom --
        # see `imaging.decode_limit`. A 160x80 panel needs very little of a
        # photograph, and every frame of an animation is held at once.
        stated = reader.size()
        limit = imaging.decode_limit(screen.WIDTH, screen.HEIGHT,
                                     stated.width(), stated.height())
        if limit != (stated.width(), stated.height()):
            reader.setScaledSize(QSize(*limit))
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
            self._abandon()
            self._encoding = False
            self._set_source("")
            self._set_images([])
            self._set_frames([])
            self._set_previews([])
            self._notify()
            return False

        truncated = reader.imageCount() > MAX_FRAMES
        self._set_source(path)
        self._set_images(frames)
        # A GIF states its own delay; a still image has none, and 100 ms is what
        # Space Station assumes too.
        moving = [d for d in delays if d]
        self._interval = max(moving) if moving else DEFAULT_INTERVAL_MS
        self._message = (
            f"Only the first {MAX_FRAMES} frames were taken — the frame count is "
            "one byte on the wire" if truncated else "")
        self._reencode()
        return True

    # -- turning a framing into frames -------------------------------------
    #
    # **Why this is a queue of one and not a queue.** A gesture that lands while
    # an encode is running makes that encode's answer worthless: it is a picture
    # of where the drag used to be. So the running job is told to stop, the new
    # framing takes its place, and the old result -- if the worker got it out
    # before it noticed -- is dropped on arrival. At most one job runs and at
    # most one waits, and the one that waits is always the newest.
    #
    # That is the whole ordering argument, and it rests on the serial:
    #
    #   * every dispatch bumps `_encode_serial` and the job carries the new
    #     value, so a job's serial is only ever the current one until the next
    #     dispatch;
    #   * `_encoded` applies a result only when its serial is still current, and
    #     the serial only ever counts up;
    #   * therefore a result that has been overtaken can never be applied, in
    #     particular not after the result that overtook it. This is not the
    #     cancellation being reliable -- the cancellation is an optimisation, and
    #     a worker that emits between `cancel()` and its next check is expected
    #     and handled.
    #
    # And the newest framing is never lost: it is either running or in
    # `_queued`, and `_worker_finished` starts whatever is in `_queued`.

    def _reencode(self):
        """Ask for the current framing, in the pad's format, off this thread."""
        self._abandon()
        self._encode_serial += 1
        self._encoding = True
        job = EncodeWorker(
            self._encode_serial,
            # Its own list and its own framing. The model goes on mutating both
            # while this runs -- that is the entire point of doing it elsewhere.
            list(self._images), self._frame_snapshot(),
            # Resolved here rather than in the worker only because it is the one
            # thing it would otherwise have to ask Qt for.
            QStandardPaths.writableLocation(QStandardPaths.CacheLocation),
            self)
        job.done.connect(self._encoded)
        job.finished.connect(self._worker_finished)
        if self._worker is None:
            self._start(job)
        else:
            self._queued = job
        self._notify()

    def _abandon(self):
        """Nothing already in flight may land after this returns.

        The serial bump is what makes that true; telling the worker to stop only
        makes it cheap. Both are needed -- see the note above.
        """
        self._encode_serial += 1
        if self._queued is not None:
            # Built and parented but never started, so nothing will ever emit
            # `finished` for it and nothing else would ever take it away.
            self._queued.deleteLater()
            self._queued = None
        if self._worker is not None:
            self._worker.cancel()

    def _start(self, job):
        self._worker = job
        job.start()

    def _frame_snapshot(self):
        """A private copy of the framing, for a worker to render through.

        `CropFrame` is a plain object this model mutates in place, so handing
        the live one over would let the next drag move the window half way
        through an encode. Only the four fields that move are copied; the rest
        is geometry fixed at construction.

        It belongs on `CropFrame` itself -- `gui/models/dock.py` wants exactly
        the same thing for exactly the same reason -- and it is written out here
        because `gui/models/imaging.py` is shared and not mine to change this
        round.
        """
        copy = CropFrame(screen.WIDTH, screen.HEIGHT, STAGE_WIDTH, STAGE_HEIGHT)
        copy.natural = self._frame.natural
        copy.fit = self._frame.fit
        copy.zoom = self._frame.zoom
        copy.pan = self._frame.pan
        return copy

    @Slot(int, object, object)
    def _encoded(self, serial, frames, paths):
        """A worker's answer, on the GUI thread. Kept only if it is still wanted."""
        if serial != self._encode_serial:
            # Overtaken between the cancel and the emit. Its preview files are
            # this model's to remove -- nothing else knows their names.
            unlink_all(paths)
            return
        self._encoding = False
        self._set_frames(frames)
        self._set_previews(paths)
        self._notify()

    @Slot()
    def _worker_finished(self):
        """The thread has ended: release it, and start whatever was waiting.

        `deleteLater` rather than dropping the reference: the worker is parented
        to the model so that Python's collector cannot destroy a running
        QThread, and without this every gesture would leave one behind. Deferred
        deletion also keeps the object alive past the tail of `run()`.
        """
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        if self._queued is not None:
            job, self._queued = self._queued, None
            self._start(job)

    @Slot()
    def _stop_encoding(self):
        self.wait()

    def wait(self, msecs=5000):
        """For shutdown: dropping a running QThread is a qFatal.

        Bounded, and the bound is generous: `cancel` is checked once per frame,
        so what is being waited for is one frame's encode and not the job.

        The handle is kept when the wait times out, the way `AppModel.shutdown`
        keeps the worker thread's -- a thread that did not finish is exactly the
        one that must not be let go of.
        """
        self._abandon()
        self._encoding = False
        if self._worker is not None and self._worker.wait(msecs):
            self._worker = None

    @Property(bool, notify=changed)
    def encoding(self):
        """Whether the frames on hand are still the ones being looked at.

        True from the moment a framing changes until its encode lands. The page
        does not need to say anything about it -- it is milliseconds for a still
        -- but `canUpload` does need it, because sending during that window
        would put the *previous* framing on the pad and take minutes doing it.
        """
        return self._encoding

    @Slot()
    def clear(self):
        self._abandon()
        self._encoding = False
        self._set_source("")
        self._set_images([])
        self._set_frames([])
        self._set_previews([])
        self._message = ""
        self._notify()

    # -- framing it --------------------------------------------------------
    #
    # The same stage the Dock page uses, against this panel's 160x80 window.
    # Space Station offers no framing here at all: their screen page takes the
    # middle of the picture and that is the whole of it.

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
        return screen.WIDTH

    @Property(int, constant=True)
    def holeHeight(self):
        return screen.HEIGHT

    @Property(str, notify=changed)
    def imageSource(self):
        """The file itself, for the stage to draw."""
        return self._source_url

    # The size the page must ask Qt to decode the same file at. **Not
    # cosmetic**: the stage loads the picture a second time through QML, and
    # `decode_limit` bounds only this model's copy.

    @Property(int, notify=changed)
    def sourceWidth(self):
        return self._natural[0]

    @Property(int, notify=changed)
    def sourceHeight(self):
        return self._natural[1]

    @Property(float, notify=changed)
    def imageX(self):
        return self._pan[0]

    @Property(float, notify=changed)
    def imageY(self):
        return self._pan[1]

    @Property(float, notify=changed)
    def imageDrawWidth(self):
        return self._drawn[0]

    @Property(float, notify=changed)
    def imageDrawHeight(self):
        return self._drawn[1]

    @Property(bool, notify=changed)
    def canPan(self):
        """False when the picture is smaller than the window on both axes."""
        return self._can_pan

    @Slot(float, float)
    def panBy(self, dx, dy):
        """The picture moved under the window. The stage follows; the encoded
        frames do not, until the gesture ends."""
        if not self._images:
            return
        self._frame.pan_by(dx, dy)
        self._notify()

    @Slot()
    def framingSettled(self):
        """A drag or a zoom ended: now re-encode, and not before."""
        if self._images:
            self._reencode()

    @Property(int, notify=changed)
    def zoom(self):
        return self._frame.zoom

    @zoom.setter
    def zoom(self, value):
        if self._frame.set_zoom(value):
            self._notify()

    @Property(int, constant=True)
    def zoomMin(self):
        return ZOOM_MIN

    @Property(int, constant=True)
    def zoomMax(self):
        return ZOOM_MAX

    @Property(str, notify=changed)
    def zoomLabel(self):
        return self._zoom_label

    # -- what QML binds to -------------------------------------------------

    @Property(str, notify=changed)
    def source(self):
        return self._source

    @Property(str, notify=changed)
    def sourceName(self):
        return self._source_name

    @Property(str, notify=changed)
    def previewSource(self):
        """Frame one. What the page shows for a still, and the fallback."""
        return self._preview

    @Property(list, notify=changed)
    def previewFrames(self):
        """Every frame as a file URL, in order, so the page can play them.

        Still a list rather than a `QAbstractListModel`, because no view
        iterates it: `ScreenPage.qml` indexes one element of it per preview
        tick, and a row model would buy nothing while costing the page a
        rewrite. What it no longer does is build that list per read -- the URLs
        are made once, where the files are.
        """
        return self._preview_urls

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
            self._notify()

    @Property(int, notify=changed)
    def fitMode(self):
        return self._frame.fit

    @fitMode.setter
    def fitMode(self, index):
        # Re-encoded straight away rather than on a settle: picking from a combo
        # box is one discrete act, so there is no gesture to wait for the end of.
        if self._frame.set_fit(index):
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
        return self._estimate

    @Property(bool, notify=changed)
    def wired(self):
        return self._connection == "wired"

    @Property(bool, notify=changed)
    def canUpload(self):
        """Wired, loaded, idle -- and not holding last framing's frames.

        `encoding` joined the list when the encode moved off this thread. The
        frames are only replaced when a worker's answer lands, so between a
        gesture and its answer what is held is the framing *before* the gesture;
        sending then would spend minutes putting the wrong crop on the pad, and
        the pad cannot be interrupted once it is across.
        """
        return (bool(self._frames) and not self._busy and not self._encoding
                and self.wired)

    @Property(str, notify=changed)
    def uploadBlocked(self):
        """Why the button is off, when it is off for a reason worth stating.

        Only the connection: an empty picture and a running upload both explain
        themselves from the rest of the page.
        """
        if self._frames and not self._busy and not self.wired:
            return ("Plug the pad in with a cable — the screen is written over "
                    "a USB serial link the dongle does not carry.")
        return ""

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
        """Refuses what `canUpload` refuses, rather than trusting the button.

        The connection test is the one that matters here. Measured on the
        dongle: the pad takes command 31 and switches its screen chip into
        upgrade mode, and nothing then reaches the PC, because the dongle does
        not relay the bootloader's serial device. The pad sits in upgrade mode
        until it is power-cycled at its own switch, with the upload having
        reported nothing worse than a timeout.
        """
        if not self.canUpload:
            return
        self._busy = True
        self._done = self._total = 0
        self._notify()
        self.uploadRequested.emit(list(self._frames), self._interval, False)

    @Slot(bool)
    def setAlwaysOn(self, value):
        self.settingRequested.emit(screen.SUB_OFF_SCREEN, bool(value))

    @Slot(bool)
    def setStatusBarAlwaysOn(self, value):
        self.settingRequested.emit(screen.SUB_STATUS_BAR, bool(value))

    # -- replies from the worker -------------------------------------------

    @Slot(dict)
    def infoReceived(self, info):
        """The same command-1 reply the header lives off, for `connect_type`.

        This page needs it as a precondition rather than as a label, so it
        takes the reply itself instead of reaching across to `DeviceModel`.
        """
        connection = info.get("connect_type", "")
        if connection != self._connection:
            self._connection = connection
            self._notify()

    @Slot(dict)
    def statusReceived(self, state):
        self._always_on = bool(state.get("always_on"))
        self._status_bar = bool(state.get("status_bar_always_on"))
        self._supported = bool(state.get("always_on_usable"))
        self._loaded = True
        self._notify()

    @Slot(int, int)
    def progressReceived(self, done, total):
        self._done, self._total = done, total
        self._notify()

    @Slot(bool)
    def uploadFinished(self, _ok):
        self._busy = False
        self._done = self._total = 0
        self._notify()
