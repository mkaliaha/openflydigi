// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The dock's "Apply lighting" button, its progress bar and the line explaining
// what pressing it costs.
//
// A component because the Dock page needs it in two places and must show it in
// exactly one. The button has to sit at the *bottom of everything it applies*,
// and what that is depends on the effect: for a computed effect the Lighting
// card is the whole story, but a picture adds a second card underneath — a
// preview, a crop stage, a trim bar — and a button above all of that is a
// button you scroll away from to do the work and scroll back to press.
//
// It stays inside a card rather than becoming a page footer, which is the older
// argument and still holds: the four switches above write the moment they move,
// so a footer button would be claiming to apply those too.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

FormCard.AbstractFormDelegate {
    id: root

    // What pressing it will cost, in words. Different for a computed effect
    // and for a picture, which is most of why this is a property.
    property string hint: ""

    background: null
    hoverEnabled: false

    contentItem: RowLayout {
        spacing: Kirigami.Units.largeSpacing

        Controls.Label {
            objectName: "dockHint"
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.disabledTextColor
            text: App.dock.busy
                ? "Uploading — the dock plays frames, so this is going over "
                  + "in packets, each one waiting for its own ack."
                : root.hint
        }

        Controls.ProgressBar {
            objectName: "dockProgress"
            visible: App.dock.busy
            from: 0
            to: 1
            value: App.dock.progress
            Layout.preferredWidth: Kirigami.Units.gridUnit * 8
        }

        Controls.Button {
            objectName: "dockApplyButton"
            text: "Apply lighting"
            icon.name: "dialog-ok-apply"
            enabled: !App.dock.busy
                     && (!App.dock.isPicture || App.dock.canApplyImage)
            onClicked: App.dock.apply()
        }
    }
}
