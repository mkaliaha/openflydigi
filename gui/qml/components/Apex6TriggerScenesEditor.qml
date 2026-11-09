// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
// SPDX-License-Identifier: GPL-3.0-or-later

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

ColumnLayout {
    spacing: Kirigami.Units.largeSpacing
    Kirigami.InlineMessage {
        objectName: "nativeSceneError"
        Layout.fillWidth: true
        visible: App.profile.nativeHaptics.error !== ""
        text: App.profile.nativeHaptics.error
        type: Kirigami.MessageType.Error
    }
    FormCard.FormCard {
        FormCard.FormTextDelegate {
            text: "Trigger scenes"
            description: "Scenes play on the controller when you pull the trigger. Choosing a scene replaces its trigger-bound feedback and clears that feedback's strength curve."
        }
        FormCard.FormTextDelegate {
            text: "Returning to Normal"
            description: "Leaving a scene resets that trigger's response curve and clears its Base-layer action zones. Shift-layer zones stay in place. To turn off trigger-bound feedback, use the Vibration page. All edits wait for Apply or Save profile."
        }
    }
    FormCard.FormHeader { title: "Left trigger scene" }
    Apex6TriggerScene { editor: App.profile.nativeHaptics.left; sideName: "left" }
    FormCard.FormHeader { title: "Right trigger scene" }
    Apex6TriggerScene { editor: App.profile.nativeHaptics.right; sideName: "right" }
}
