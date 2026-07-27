<!--
SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
SPDX-License-Identifier: GPL-3.0-or-later
-->

# GUI

The desktop frontend. **GPL-3.0-or-later**, unlike the rest of the repository.

## Why this directory is licensed differently

`flydigi/` and `tools/` are MIT because reuse of the protocol work is welcome
without conditions — any project, any license. Copyleft there would only get in
the way of that.

That reasoning does not apply here. This directory links Qt and is a desktop
application, so copyleft costs nothing and there is nothing anyone needs to
embed.

## The one rule

**`gui/` may import `flydigi/`. `flydigi/` must never import `gui/`.**

MIT is one-way compatible with GPL: a GPL frontend importing an MIT library
leaves that library MIT, and anyone can still lift `flydigi/` on its own. An
import in the other direction would pull GPL code into the backend and destroy
that property.

The same rule protects a second property worth keeping: the backend has no
dependencies at all, so `tools/flydigi-ds5` runs on any machine with Python
3.9 and no Qt installed. PySide6 is a dependency of this directory only.

## Toolkit

PySide6 (LGPLv3), not PyQt6 (GPL-only). Avoid the Qt add-ons that ship
GPL-3.0-only under the open-source license — Charts, Data Visualization,
Virtual Keyboard. A trigger-response curve is the obvious reason someone would
reach for Qt Charts; draw it with `QPainter` or a QML `Canvas` instead.
