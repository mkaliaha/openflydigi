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
reach for Qt Charts; draw it with a QML `Canvas` instead.

The interface is QML on Kirigami. There are no widgets left.

## Runtime

**PySide6 must come from the distribution, not from pip.** A PyPI wheel bundles
its own Qt build whose private symbols are versioned differently from the
system's, and Kirigami — linked against the system Qt — will not load against
it. See `requirements.txt` for the detail.

On an immutable system, a container avoids layering anything:

```bash
distrobox create --name apex-dev --image registry.fedoraproject.org/fedora-toolbox:44
distrobox enter apex-dev -- sudo dnf install -y python3-pyside6 kf6-kirigami \
    kf6-kirigami-addons kf6-qqc2-desktop-style qt6-qtdeclarative-devel
distrobox enter apex-dev -- python3 -m gui
```

## Layout

    app.py        the application graph: models, worker thread, the wiring
    main.py       QML entry point
    worker.py     device access, on its own thread
    models/       view-agnostic state -- no QtWidgets, no QtQuick
    qml/          Main.qml, pages/, components/

`models/` is where the logic lives, which is why most of the tests need no
display and no QML engine at all. The QML binds to it through the `App`
singleton of the `Apex5` module, registered by the `QmlElement` decorators.

## Tests

    python3 tests/test_models.py    # headless, no engine
    python3 tests/test_shell.py     # window smoke test, the way main.py loads it
    python3 tests/test_qml.py       # QtQuickTest: real clicks on real delegates

    tools/generate-qmltypes         # then:
    qmllint -I . -I /usr/lib64/qt6/qml gui/qml/Main.qml gui/qml/*/*.qml

`tools/generate-qmltypes` is what makes qmllint useful — without the generated
type information every model reference is an unqualified access it has to shrug
at. Re-run it after adding or renaming anything a view binds to.
