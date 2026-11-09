// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

FormCard.FormCard {
    id: root
    required property Apex6FeedbackModel editor
    required property string sideName

    FormCard.FormTextDelegate {
        visible: root.editor.issue !== ""
        text: "Stored feedback is read-only"
        description: root.editor.issue
    }
    ModelSwitch {
        objectName: "nativeFeedbackEnabled_" + root.sideName
        text: root.editor.isGrip ? "Grip vibration" : "Trigger-bound feedback"
        description: root.editor.isGrip ? "Controls both grip motors together."
            : "Enabling replaces this trigger's scene. Pull this trigger while a game sends rumble to feel its feedback."
        enabled: root.editor.editable
        value: root.editor.enabled
        onMoved: (wanted) => root.editor.enabled = wanted
    }
    FormCard.FormDelegateSeparator {}
    SliderRow {
        objectName: "nativeFeedbackLevel_" + root.sideName
        label: "Strength"
        description: root.editor.isGrip ? "Choosing a level also enables both grips."
            : root.editor.strengthZero ? "Stored feedback strength is zero. Choose a level to restore it."
            : "Changes strength without enabling feedback or replacing a scene."
        from: 1
        to: 5
        enabled: root.editor.editable
        value: root.editor.level
        onMoved: (wanted) => root.editor.level = wanted
    }
    FormCard.FormButtonDelegate {
        objectName: "nativeFeedbackRestore_" + root.sideName
        visible: !root.editor.isGrip && root.editor.strengthZero && root.editor.editable
        text: "Restore strength to level 1"
        description: "Keeps the current mode. Enable trigger-bound feedback separately when ready."
        onClicked: root.editor.level = 1
    }
    FormCard.FormTextDelegate {
        visible: !root.editor.isGrip
        text: "Current mode: " + root.editor.modeText
        description: "Choose a scene on the Triggers page. Changes take effect with Apply or Save profile."
    }
}
