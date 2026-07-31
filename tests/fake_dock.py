# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""The fake CD2, under the name the tests have always imported it by.

It lives in `flydigi/mock/dock.py` now, for the reason `tests/fake_pad.py`
gives: the mock bus serves these to the app and the tools at runtime, and a
library cannot import its own test suite.
"""
from flydigi.mock.dock import (      # noqa: F401  -- re-exported on purpose
    FIRMWARE,
    UID,
    FakeDock,
)
