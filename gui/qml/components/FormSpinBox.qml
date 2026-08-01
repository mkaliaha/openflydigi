// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// A FormCard spin box that leaves the scroll wheel alone.
//
// The same defect and the same cure as [FormComboBox](FormComboBox.qml), which
// carries the full explanation: `org.kde.desktop`'s `SpinBox.qml` sets
// `wheelEnabled: true`, and the SpinBox inside `FormSpinBoxDelegate` is private
// to the delegate, so the flag has to be found rather than assigned.
//
// The walk is repeated rather than shared. Reaching one copy from both wrappers
// would mean either a QML singleton -- which needs a `qmldir` in components/,
// and adding one turns every other file in this directory into a type that must
// be declared there too -- or a JS library file, which is a second language for
// a loop over `children`.

import QtQuick
import org.kde.kirigamiaddons.formcard as FormCard

FormCard.FormSpinBoxDelegate {
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
