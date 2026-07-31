# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""The fake Apex 5, under the name the tests have always imported it by.

It lives in `flydigi/mock/pad.py` now. It moved because the mock bus needs it at
*runtime* -- the desktop app and the tools have to be runnable against several
devices, and there is one pad on this desk -- and a library cannot import its
own test suite. Nothing about it changed in the move except that it grew the
identity commands.

Kept as a module rather than as a sed across the tests: `from tests.fake_pad
import FakePad` says what the test means, and the tests are where a fake is
*supposed* to be reached from.
"""
from flydigi.mock.pad import (       # noqa: F401  -- re-exported on purpose
    BLOB_LEN,
    PACKAGE_COUNT,
    PROTO_V31,
    UNCHECKSUMMED,
    FakePad,
    FakeScreenChip,
    blank_blob,
)
