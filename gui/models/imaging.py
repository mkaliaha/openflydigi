# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Qt imaging chores both picture pages have to get right.

Not a model. `gui/models/screen.py` puts a picture on the pad's panel and
`gui/models/dock.py` puts one on the dock's LEDs, and both end up handing a
plain RGB888 buffer to `flydigi/`, which has no imaging library of its own.

Both also have to let someone *choose* which part of a picture goes there,
which is `CropFrame` below: the same drag-and-zoom against two different
windows.
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter


def rgb_bytes(image):
    """RGB888 for a QImage, with Qt's row padding removed.

    Qt aligns every row to four bytes. The pad's 160-wide panel is already a
    multiple of four so nothing is added there, and the dock's 334-wide crop
    canvas is not -- 334 * 3 is 1002, so every row carries two bytes of padding
    and a caller that indexed straight into `constBits()` would read each row
    two bytes further out of step than the last. On the panel that is an image
    sheared diagonally; on the dock it is 162 LEDs each showing a colour from
    somewhere else. Same bug, and only one of the two would look obviously wrong.
    """
    image = image.convertToFormat(QImage.Format_RGB888)
    stride = image.bytesPerLine()
    wanted = image.width() * 3
    raw = bytes(image.constBits())
    if stride == wanted:
        return raw
    return b"".join(raw[y * stride:y * stride + wanted]
                    for y in range(image.height()))


# -- framing a picture ------------------------------------------------------
#
# Space Station's dock DIY page lays the source image out on a stage with the
# crop window cut out of the middle of it, and lets you drag the image under
# that window and zoom it. The geometry below is a parameter because the pad's
# screen wants the same gesture against a 160x80 window, and Space Station
# offers no framing there at all -- their screen page takes the middle of the
# picture and that is that.

FIT_FILL, FIT_INSIDE, FIT_STRETCH = 0, 1, 2

# Space Station's zoom slider is 1..20 in whole steps and the factor it
# produces is `0.95 + 0.05 * value`, so the range is 1.00x to 1.95x and the
# bottom of the slider is exactly the fitted size. Kept for the screen too, so
# the two pages do not disagree about what a zoom of 7 means.
ZOOM_MIN, ZOOM_MAX = 1, 20


def zoom_factor(zoom):
    """Their slider value -> the scale it means. 1 is the fitted size."""
    return 0.95 + 0.05 * max(ZOOM_MIN, min(ZOOM_MAX, int(zoom)))


def clamp_pan(value, rendered, origin, extent):
    """Keep the crop window inside the picture, on one axis.

    An image at least as big as the window may be dragged until an edge meets
    the window's edge and no further. One smaller than the window -- which only
    "Fit inside" produces -- has no valid range at all, so it is centred and
    stops being draggable rather than being clamped to a nonsense endpoint.
    """
    if rendered <= extent:
        return origin + (extent - rendered) / 2.0
    return min(float(origin), max(origin + extent - rendered, float(value)))


class CropFrame:
    """Where a picture sits under a fixed window, and what that window sees.

    Pure geometry plus one render. It holds no images: a model owns the decoded
    frames and asks for each to be drawn through the window, because the two
    callers keep their frames on quite different terms -- the dock decodes
    every frame up front for its trim bar, the screen's panel has no trim bar.

    Coordinates are the stage's own, and the stage is measured in target
    pixels: `stage_width`/`stage_height` are the visible working area and the
    window sits in the middle of it, so the margin is what there is to drag
    into. QML scales the whole stage by one factor and so needs no arithmetic
    of its own.
    """

    def __init__(self, target_width, target_height, stage_width, stage_height):
        self.target_width = int(target_width)
        self.target_height = int(target_height)
        self.stage_width = int(stage_width)
        self.stage_height = int(stage_height)
        self.hole_x = (self.stage_width - self.target_width) // 2
        self.hole_y = (self.stage_height - self.target_height) // 2
        self.natural = (0, 0)
        self.fit = FIT_FILL
        self.zoom = ZOOM_MIN
        self.pan = (0.0, 0.0)

    # -- geometry ----------------------------------------------------------

    def fitted_size(self):
        """How big the source image is drawn on the stage at zoom 1."""
        natural_width, natural_height = self.natural
        if not natural_width or not natural_height:
            return (0.0, 0.0)
        if self.fit == FIT_STRETCH:
            return (float(self.target_width), float(self.target_height))
        pick = min if self.fit == FIT_INSIDE else max
        scale = pick(self.target_width / natural_width,
                     self.target_height / natural_height)
        return (natural_width * scale, natural_height * scale)

    def rendered_size(self):
        """How big it is drawn at this fit *and* zoom."""
        width, height = self.fitted_size()
        factor = zoom_factor(self.zoom)
        return (width * factor, height * factor)

    def can_pan(self):
        """False when the picture is smaller than the window on both axes."""
        width, height = self.rendered_size()
        return width > self.target_width or height > self.target_height

    # -- moving it ---------------------------------------------------------

    def recentre(self):
        """Put the picture in the middle of the window. Where a load starts.

        Also where a zoom lands, which is Space Station's behaviour: their
        slider throws the pan away and re-centres. Keeping the pan would be
        defensible, but zooming about a corner that is off-screen is worse than
        both, and matching them costs nothing.
        """
        width, height = self.rendered_size()
        self.pan = (self.hole_x + (self.target_width - width) / 2.0,
                    self.hole_y + (self.target_height - height) / 2.0)

    def clamp(self):
        """Pull the picture back until it covers the window again."""
        width, height = self.rendered_size()
        self.pan = (clamp_pan(self.pan[0], width, self.hole_x, self.target_width),
                    clamp_pan(self.pan[1], height, self.hole_y, self.target_height))

    def pan_by(self, dx, dy):
        self.pan = (self.pan[0] + float(dx), self.pan[1] + float(dy))
        self.clamp()

    def set_natural(self, width, height):
        """A new picture: its size, back to the opening fit and zoom."""
        self.natural = (int(width), int(height))
        self.fit = FIT_FILL
        self.zoom = ZOOM_MIN
        self.recentre()

    def set_fit(self, mode):
        """Returns whether anything moved, so a caller can skip a repaint."""
        mode = max(FIT_FILL, min(FIT_STRETCH, int(mode)))
        if mode == self.fit:
            return False
        self.fit = mode
        self.recentre()
        return True

    def set_zoom(self, value):
        value = max(ZOOM_MIN, min(ZOOM_MAX, int(value)))
        if value == self.zoom:
            return False
        self.zoom = value
        self.recentre()
        return True

    def zoom_label(self):
        return f"{zoom_factor(self.zoom):.2f}×"

    # -- what the window sees ----------------------------------------------

    def render(self, image):
        """One source frame, framed onto a target-sized canvas.

        Black underneath, always, so anything outside the picture -- which only
        "Fit inside" and a letterbox produce -- comes out as an unlit LED or an
        unlit pixel rather than as whatever was in the buffer.

        Translucency is one place this deliberately does not match Space
        Station. Theirs reads the canvas's un-premultiplied RGB and never looks
        at the alpha byte, so a 50% red PNG samples as full red; here it is
        composited onto the black first and comes out half red, which is what
        the picture looks like. Only reachable through a PNG -- a GIF's
        transparency is all or nothing, and both agree there.
        """
        canvas = QImage(self.target_width, self.target_height,
                        QImage.Format_RGB888)
        canvas.fill(Qt.black)
        if image is not None and not image.isNull():
            width, height = self.rendered_size()
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            # In the window's own coordinates, so the parts of the picture that
            # fall outside it are clipped by the canvas rather than by
            # arithmetic that has to get the source rectangle right.
            painter.drawImage(
                QRectF(self.pan[0] - self.hole_x, self.pan[1] - self.hole_y,
                       width, height), image)
            painter.end()
        return canvas

    def decode_limit(self, width, height):
        """This window's `decode_limit`, for a caller that holds a frame."""
        return decode_limit(self.target_width, self.target_height, width, height)


def decode_limit(target_width, target_height, width, height):
    """The biggest a source frame is ever drawn, so nothing larger is kept.

    A GIF is decoded whole, and a two-hundred-frame screen recording at 1080p
    is a great deal of QImage for a panel that is 162 dots. At full zoom the
    window covers `max(target)` source pixels exactly -- anything finer than
    that is resolution the sampler cannot reach at any pan position, and
    anything coarser loses detail that it can.

    Deliberately conservative about orientation: the limit is taken against the
    picture's *shorter* side, so a frame that `setAutoTransform` is about to
    turn on its side is still large enough afterwards.

    It bounds rather than solves. Measured on the dock: a 200-frame 1080p GIF
    opens in 1.5 seconds and holds 585 MB at 1158x651 a frame, because every
    one of those frames really is pannable at that resolution. What it stops is
    the same picture costing several times that. Bounding it harder would mean
    deciding that 162 dots do not deserve a 1:1 sample, which is a different
    argument from this one.

    Two things it does *not* do. It is not a peak-memory bound:
    `QImageReader.supportsOption(ScaledSize)` is false for Qt's GIF and PNG
    handlers, so those decode each frame at full size and scale it afterwards.
    And it says nothing about the copy QML loads for the crop stage -- that one
    is bounded on the page, by `sourceSize` and `cache: false`.
    """
    if width <= 0 or height <= 0:
        return (width, height)
    scale = (max(target_width, target_height)
             / min(width, height)) * zoom_factor(ZOOM_MAX)
    if scale >= 1.0:
        return (width, height)
    return (max(1, round(width * scale)), max(1, round(height * scale)))
