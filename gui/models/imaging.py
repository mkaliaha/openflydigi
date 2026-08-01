# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The one Qt imaging chore both picture pages have to get right.

Not a model. `gui/models/screen.py` puts a picture on the pad's panel and
`gui/models/dock.py` puts one on the dock's LEDs, and both end up handing a
plain RGB888 buffer to `flydigi/`, which has no imaging library of its own.
"""
from PySide6.QtGui import QImage


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
