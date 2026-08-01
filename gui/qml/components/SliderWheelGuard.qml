// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Stop a scroll over a slider from editing what the slider is bound to.
//
// **`wheelEnabled: false` does nothing to a slider under this style, and that
// is not a reason to leave them alone.** `org.kde.desktop`'s `Slider.qml` does
// not use the property: it puts a `MouseArea` in its own background and calls
// `increase()`/`decrease()` and then `moved()` from there, because
// `wheelEnabled` cannot snap to tick marks (QTBUG-93081). Every slider in this
// app writes `moved` straight into the model, so one notch with the pointer
// resting on one *edited the profile* — a trigger's travel window, the dock's
// brightness — with no confirmation and no undo. Exactly the defect the combo
// boxes had, reached a different way. `components/FormComboBox.qml` is the
// other half of the same job.
//
// A declared child of a `Control` sits above its background, so this is offered
// the event first and takes it. Taking it is what forces the page scroll below
// to be done by hand: anything passed on would be picked straight up by the
// very MouseArea being defeated. `Qt.NoButton` keeps presses, drags and
// releases falling through, so the slider is still draggable and still
// adjustable from the keyboard — only the wheel is taken away.
//
//     Controls.Slider {
//         onMoved: model.thing = value
//         SliderWheelGuard {}
//     }

import QtQuick

MouseArea {
    id: guard

    anchors.fill: parent
    acceptedButtons: Qt.NoButton

    /// Hand the event to whatever is scrolling behind the slider.
    ///
    /// The Flickable is found by walking up rather than being passed in, so a
    /// page using a slider does not have to know one is there; a slider that is
    /// not inside a Flickable scrolls nothing, which is the right answer for it.
    ///
    /// A touchpad sends pixels and a mouse sends angles, so both are honoured.
    /// 40 pixels a notch is what `QQuickFlickable` steps by itself, so a wheel
    /// over a slider moves the page as far as a wheel over the gap beside it.
    function scrollBehind(wheel) {
        for (let item = guard.parent; item; item = item.parent) {
            // Asserted back to what it is rather than tested for a `contentY`:
            // duck-typing works at runtime and qmllint cannot check a word of
            // it, and a guard is exactly the sort of code that has to go on
            // working while nobody is looking at it. A ListView is a Flickable
            // too, which is what the Buttons page scrolls.
            const flick = item as Flickable;
            if (!flick)
                continue;
            const step = wheel.pixelDelta.y || (wheel.angleDelta.y / 120) * 40;
            const most = Math.max(0, flick.contentHeight - flick.height);
            flick.contentY = Math.max(0, Math.min(most, flick.contentY - step));
            return;
        }
    }

    onWheel: (wheel) => {
        wheel.accepted = true;
        guard.scrollBehind(wheel);
    }
}
