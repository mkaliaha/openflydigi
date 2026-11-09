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
        objectName: "nativeVibrationError"
        Layout.fillWidth: true
        visible: App.profile.nativeHaptics.error !== ""
        text: App.profile.nativeHaptics.error
        type: Kirigami.MessageType.Error
    }
    FormCard.FormHeader { title: "Grip motors" }
    Apex6FeedbackSide { editor: App.profile.nativeHaptics.grips; sideName: "grips" }
    FormCard.FormHeader { title: "Left trigger" }
    Apex6FeedbackSide { editor: App.profile.nativeHaptics.left; sideName: "left" }
    FormCard.FormHeader { title: "Right trigger" }
    Apex6FeedbackSide { editor: App.profile.nativeHaptics.right; sideName: "right" }
}
