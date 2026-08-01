// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// RGB lighting: its own config on the pad, so its own apply and save.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import QtQuick.Dialogs as Dialogs
import org.kde.kirigami as Kirigami
import org.kde.kirigamiaddons.formcard as FormCard
import Apex5

import "../components"

Kirigami.ScrollablePage {
    id: page
    objectName: "lightingPage"
    title: "Lighting"

    // Which swatch the colour dialog is editing.
    property int editingColour: -1

    footer: Controls.ToolBar {
        position: Controls.ToolBar.Footer

        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing

            Controls.Label {
                objectName: "lightingHint"
                text: {
                    if (App.lighting.dirty)
                        return "Unsaved changes.";
                    if (App.lighting.saveNeeded)
                        return "Applied, but only to the pad's memory — it "
                               + "will be lost when the pad sleeps.";
                    return "Matches what is on the pad.";
                }
                elide: Text.ElideRight
                Layout.fillWidth: true
                // Zero preferred width keeps this label out of the layout's
                // sizing, so the buttons beside it stay put when the text
                // changes length.
                Layout.preferredWidth: 0
            }

            Controls.Button {
                objectName: "lightingApplyButton"
                text: "Apply"
                icon.name: "dialog-ok-apply"
                enabled: App.lighting.dirty
                onClicked: App.lighting.write(false)
            }

            Controls.Button {
                objectName: "lightingSaveButton"
                // Not "Apply & save": a bare ampersand is taken as a mnemonic
                // and rendered as an underline on the next character.
                text: "Apply and save"
                icon.name: "document-save"
                // Also enabled once something has been applied but not yet
                // committed, or applying would make saving impossible.
                enabled: App.lighting.dirty || App.lighting.saveNeeded
                onClicked: App.lighting.write(true)
            }
        }
    }

    ColumnLayout {
        spacing: 0

        Kirigami.PlaceholderMessage {
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 4
            objectName: "lightingPlaceholder"
            visible: !App.lighting.loaded
            icon.name: "color-management"
            text: "Lighting not read yet"
            explanation: "Press Reload from pad, or press a button to wake the controller."
        }

        FormCard.FormCard {
            visible: App.lighting.loaded

            FormComboBox {
                objectName: "effectCombo"
                text: "Effect"
                // The stored mode byte uses Space Station's numbering, so on
                // load we cannot say which of our effects made what is on the
                // pad. The picker starts on "keep" rather than claiming one.
                description: App.lighting.effect === 0
                             ? "The pad's own frames are left alone until you pick one"
                             : "Choosing an effect rewrites the frames the pad plays"
                model: App.lighting.effectNames
                currentIndex: App.lighting.effect
                onActivated: App.lighting.effect = currentIndex
            }

            FormCard.FormDelegateSeparator {}

            SliderRow {
                objectName: "brightness"
                label: "Brightness"
                to: App.lighting.brightnessMax
                value: App.lighting.brightness
                onMoved: (newValue) => App.lighting.brightness = newValue
            }

            FormCard.FormDelegateSeparator {}

            SliderRow {
                objectName: "speed"
                label: "Speed"
                from: App.lighting.speedMin
                to: App.lighting.speedMax
                value: App.lighting.speed
                onMoved: (newValue) => App.lighting.speed = newValue
            }

            FormCard.FormDelegateSeparator {}

            // Flydigi's own name for it, rather than one invented here: their
            // string is "Vibration light effect", described as "there will be a
            // special light effect when the grip vibrates".
            //
            // This switch spent its whole life bound to the wrong byte, under
            // "React to rumble", writing byte 2 -- which is inert on this pad.
            // Byte 9 is the one that measurably dims the ring while a motor
            // runs. See docs/device-settings.md for the measurement.
            ModelSwitch {
                objectName: "gripSync"
                text: "Vibration light effect"
                description: "The pad dims part of the ring while a grip motor "
                             + "runs, on top of whatever effect is playing."
                value: App.lighting.gripSync
                onMoved: (wanted) => App.lighting.gripSync = wanted
            }
        }

        FormCard.FormHeader {
            visible: App.lighting.loaded && App.lighting.colours.allowed > 0
            title: "Colours"
        }

        FormCard.FormCard {
            visible: App.lighting.loaded && App.lighting.colours.allowed > 0

            // The swatches sit side by side in one delegate rather than being
            // a row each. Colours are easier to compare next to each other,
            // and it keeps the Repeater out of the FormCard itself: adding a
            // colour there creates a form delegate on the fly, which trips a
            // bug in kirigami-addons 1.12 where FormDelegateBackground reads
            // `control.parent.visibleChildren` without the null guard it uses
            // on the line above.
            FormCard.AbstractFormDelegate {
                objectName: "colourRow"
                background: null
                hoverEnabled: false

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: App.lighting.colours.count === 1 ? "Colour" : "Colours"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Repeater {
                        model: App.lighting.colours

                        delegate: Controls.AbstractButton {
                            id: swatch

                            required property int index
                            required property string colour

                            objectName: "colourSwatch" + swatch.index
                            implicitWidth: Kirigami.Units.gridUnit * 3
                            implicitHeight: Kirigami.Units.gridUnit * 1.75
                            hoverEnabled: true

                            Controls.ToolTip.visible: swatch.hovered
                            Controls.ToolTip.text: "Click to change this colour ("
                                                   + swatch.colour + ")"

                            onClicked: {
                                page.editingColour = swatch.index;
                                colourDialog.selectedColor = swatch.colour;
                                colourDialog.open();
                            }

                            background: Rectangle {
                                radius: Kirigami.Units.smallSpacing
                                color: swatch.colour
                                border.width: swatch.hovered ? 2 : 1
                                border.color: swatch.hovered
                                              ? Kirigami.Theme.highlightColor
                                              : Kirigami.Theme.disabledTextColor
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.lighting.colours.allowed > 1
            }

            FormCard.AbstractFormDelegate {
                visible: App.lighting.colours.allowed > 1
                background: null
                hoverEnabled: false

                contentItem: RowLayout {
                    spacing: Kirigami.Units.smallSpacing

                    Item { Layout.fillWidth: true }

                    Controls.Button {
                        objectName: "addColour"
                        text: "Add a colour"
                        icon.name: "list-add"
                        enabled: App.lighting.colours.canAdd
                        onClicked: App.lighting.colours.add()
                    }

                    Controls.Button {
                        objectName: "removeColour"
                        text: "Remove"
                        icon.name: "list-remove"
                        enabled: App.lighting.colours.canRemove
                        onClicked: App.lighting.colours.remove()
                    }
                }
            }
        }

        FormCard.FormCard {
            visible: App.lighting.loaded
            Layout.topMargin: Kirigami.Units.largeSpacing

            FormCard.FormTextDelegate {
                objectName: "lightingInfo"
                text: "About this config"
                description: App.lighting.info
            }
        }
    }

    Dialogs.ColorDialog {
        id: colourDialog
        objectName: "colourDialog"
        title: "Lighting colour"
        onAccepted: {
            if (page.editingColour >= 0)
                App.lighting.colours.setColour(page.editingColour, selectedColor);
            page.editingColour = -1;
        }
    }
}
