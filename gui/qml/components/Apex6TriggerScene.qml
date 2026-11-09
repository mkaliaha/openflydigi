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
    FormComboBox {
        objectName: "nativeScene_" + root.sideName
        text: "Scene"
        description: "Current mode: " + root.editor.modeText
        model: root.editor.sceneNames
        enabled: root.editor.editable
        Binding on currentIndex { value: root.editor.scene; restoreMode: Binding.RestoreNone }
        onActivated: (index) => root.editor.scene = index
    }
    FormCard.FormTextDelegate {
        objectName: "nativeSceneCustom_" + root.sideName
        visible: root.editor.editable && !root.editor.adjustable && root.editor.scene !== 0
        text: "Custom scene settings"
        description: "The stored settings are kept unchanged. Select a preset to replace them and adjust travel or frequency."
    }
    FormComboBox {
        objectName: "nativeSceneTravel_" + root.sideName
        text: "Travel"
        model: ["1 · Early", "2 · Middle", "3 · Late"]
        visible: root.editor.adjustable
        Binding on currentIndex { value: root.editor.travel - 1; restoreMode: Binding.RestoreNone }
        onActivated: (index) => root.editor.travel = index + 1
    }
    FormComboBox {
        objectName: "nativeSceneFrequency_" + root.sideName
        text: "Frequency"
        model: ["1 · Slow", "2 · Medium", "3 · Fast"]
        visible: root.editor.adjustable && root.editor.hasFrequency
        Binding on currentIndex { value: root.editor.frequency - 1; restoreMode: Binding.RestoreNone }
        onActivated: (index) => root.editor.frequency = index + 1
    }
}
