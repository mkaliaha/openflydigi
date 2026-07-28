// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Shared by every page that edits the open profile.
//
// Buttons, vibration and triggers all live in one profile blob on the pad, so
// they share one dirty state and one pair of write buttons rather than each
// pretending to be independently saveable.

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import Apex5

Controls.ToolBar {
    id: root
    position: Controls.ToolBar.Footer

    contentItem: RowLayout {
        spacing: Kirigami.Units.largeSpacing

        Controls.Label {
            objectName: "profileHint"
            text: App.profile.hint
            elide: Text.ElideRight
            Layout.fillWidth: true
            // The hint gets longer the moment something is edited. Without a
            // preferred width of zero its implicit width feeds into the
            // layout, and the buttons to its right jump sideways as you type.
            Layout.preferredWidth: 0
        }

        Controls.Button {
            objectName: "applyButton"
            text: "Apply"
            icon.name: "dialog-ok-apply"
            enabled: App.profile.dirty
            onClicked: App.profile.write(false)

            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: "Takes effect at once. Confirmed on hardware: "
                                   + "an applied change is lost when the pad sleeps."
        }

        Controls.Button {
            objectName: "saveButton"
            // Spelled out rather than "Apply & save": a bare ampersand in a
            // button label is taken as a mnemonic, swallowed, and shown as an
            // underline on the next character -- it read as "Apply _ save".
            text: "Apply and save"
            icon.name: "document-save"
            // Enabled for an applied-but-uncommitted change as well, not just
            // an unapplied one. Otherwise pressing Apply immediately greys
            // this out and there is no way to keep what you just applied.
            enabled: App.profile.dirty || App.profile.saveNeeded
            onClicked: App.profile.write(true)

            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: "Also commits to flash, so it survives sleep "
                                   + "and a power cycle."
        }
    }
}
