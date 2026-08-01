// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// A FormCard switch that goes on following the model after it has been clicked.
//
// **`checked: someModel.thing` stops working the moment somebody uses it.**
// Clicking a QQC2 switch assigns its own `checked`, and assigning a property is
// how a declarative binding to it is broken — so from the first click onwards
// the control is a local variable that happens to have started out agreeing
// with the model. Everything the model says afterwards is ignored.
//
// That is invisible while every write succeeds, and it is exactly what makes a
// failure unreportable. A setting the pad refuses is put back in the model —
// see `SettingsModel.writeFinished`, `DockModel.switchFinished` — and the
// switch went on showing the state the click had put it in, so the page said
// the write had worked. Measured with
// `tst_device.qml::test_a_refused_setting_puts_the_control_back`, which fails
// against a plain `checked:` binding on its last line and nowhere earlier.
//
// A `Binding` element re-applies itself whenever its source changes, so it
// survives being overwritten. `RestoreNone` because there is nothing to
// restore: the model is the only thing that ever decides what this shows.
//
// The contract is `SliderRow`'s: read `value`, report `moved`, and never assign
// `value` from a handler.
//
//     ModelSwitch {
//         text: "Switch profile from the pad"
//         value: App.settings.quickSwitch
//         onMoved: (wanted) => App.settings.quickSwitch = wanted
//     }

import QtQuick
import org.kde.kirigamiaddons.formcard as FormCard

FormCard.FormSwitchDelegate {
    id: root

    /// What the model says. The only thing that decides what is drawn.
    property bool value: false

    /// Emitted with the position the user asked for, for the parent to push
    /// into the model. Never assign `value` from here.
    signal moved(bool wanted)

    checked: root.value
    onToggled: root.moved(checked)

    Binding {
        target: root
        property: "checked"
        value: root.value
        restoreMode: Binding.RestoreNone
    }
}
