# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Entry point: `python -m gui` from the repository root."""
import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
