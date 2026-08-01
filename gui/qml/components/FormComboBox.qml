// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// A FormCard combo box that leaves the scroll wheel alone.
//
// **The KDE style turns the wheel on; Qt's own default is off.**
// `org.kde.desktop`'s `ComboBox.qml` sets `wheelEnabled: true` -- the Basic
// style does not, and `gui/main.py` asks for the KDE one. So a combo box under
// the pointer swallows a scroll meant for the page and changes its own value
// with it. That is a data-integrity problem before it is a scrolling one: on
// the Buttons page it silently rewrites a key mapping.
//
// A bare `Controls.ComboBox` takes `wheelEnabled: false` at the call site.
// `FormComboBoxDelegate` does not: the ComboBox it draws is private to the
// delegate and no version of kirigami-addons aliases the property out, so it
// has to be found in the object tree once the delegate exists. Indexing into
// the delegate's layout would break the moment the addon rearranges it, so
// this turns the flag off wherever it is already on. The style switches it on
// for exactly two controls, ComboBox and SpinBox, so nothing else is touched
// -- and if a later addons release does alias the property up to the root,
// that alias is found and cleared by the same walk.
//
// Off, the wheel event goes unaccepted and delivery carries on to the page's
// Flickable, which is what scrolls the page.

import QtQuick
import org.kde.kirigamiaddons.formcard as FormCard

FormCard.FormComboBoxDelegate {
    id: root

    Component.onCompleted: {
        function silence(item) {
            if (item.wheelEnabled === true)
                item.wheelEnabled = false;
            for (let i = 0; i < item.children.length; ++i)
                silence(item.children[i]);
        }
        silence(root);
    }
}
