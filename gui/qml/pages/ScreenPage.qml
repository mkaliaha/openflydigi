// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The 160x80 screen. Two halves with very different costs: the display
// settings are one packet, and an upload is about 25 seconds a frame over a
// serial link that cannot be interrupted once it starts. The page puts the
// frame count and the estimate next to the button for that reason.

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
    objectName: "screenPage"
    title: "Screen"

    // Which frame the preview is showing. An animation is played here rather
    // than shown as its first frame, because the upload takes minutes and is
    // far too expensive to be how you find out what you picked.
    property int previewFrame: 0

    Timer {
        objectName: "screenPreviewTimer"
        running: App.screen.animated && page.visible
        // The pad's own frame interval, so the preview runs at the speed the
        // picture will. Floored because a spin box can ask for faster than a
        // QML timer will honour.
        interval: Math.max(20, App.screen.interval)
        repeat: true
        onTriggered: page.previewFrame =
            (page.previewFrame + 1) % Math.max(1, App.screen.frameCount)
    }

    Connections {
        target: App.screen
        // A new picture, or a different fit, restarts the loop -- otherwise the
        // index would point into the middle of something else, or past its end.
        function onChanged() { page.previewFrame = 0; }
    }

    Dialogs.FileDialog {
        id: fileDialog
        objectName: "screenFileDialog"
        title: "Choose an image or animation"
        nameFilters: ["Images and animations (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
                      "All files (*)"]
        onAccepted: App.screen.open(selectedFile)
    }

    ColumnLayout {
        spacing: 0

        FormCard.FormHeader {
            title: "Picture"
        }

        FormCard.FormCard {
            // The preview is the encoded frame read back, not a scaled copy of
            // the file, so what is shown is what the pad will hold -- crop,
            // letterboxing and the 16-bit colour included.
            FormCard.AbstractFormDelegate {
                background: null
                contentItem: ColumnLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Rectangle {
                        objectName: "screenPreviewFrame"
                        // Takes the column's width and keeps the panel's own
                        // 2:1 shape, so the preview is never wider than the
                        // card it sits in -- a fixed 640 was, on a narrow
                        // window. Capped at four times the real 160x80, past
                        // which it is only showing bigger pixels.
                        Layout.fillWidth: true
                        Layout.maximumWidth: 640
                        Layout.alignment: Qt.AlignHCenter
                        // A floor as well as a ceiling: with fillWidth and no
                        // implicit width of its own the card would have nothing
                        // to size itself from and could collapse.
                        implicitWidth: 320
                        implicitHeight: width / 2
                        color: "black"
                        radius: Kirigami.Units.smallSpacing
                        border.width: 1
                        border.color: Kirigami.Theme.disabledTextColor

                        Image {
                            objectName: "screenPreview"
                            anchors.fill: parent
                            anchors.margins: 1
                            source: {
                                const frames = App.screen.previewFrames;
                                if (frames.length > page.previewFrame)
                                    return frames[page.previewFrame];
                                return App.screen.previewSource;
                            }
                            visible: source.toString() !== ""
                            fillMode: Image.PreserveAspectFit
                            // The panel is 160x80 and this is drawn at 4x, so
                            // smoothing would show an interpolation the pad
                            // cannot produce.
                            smooth: false
                            // Synchronous on purpose. Asynchronous loading
                            // leaves the item blank for a frame or two while
                            // the next image arrives, which at ten frames a
                            // second reads as a flicker on light pictures.
                            // These are 160x80 PNGs already on local disk --
                            // there is nothing to wait for.
                            asynchronous: false
                            cache: true
                        }

                        Controls.Label {
                            objectName: "screenPreviewPlaceholder"
                            anchors.centerIn: parent
                            visible: App.screen.previewSource === ""
                            text: "No picture chosen"
                            color: Kirigami.Theme.disabledTextColor
                        }
                    }

                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        spacing: Kirigami.Units.largeSpacing

                        Controls.Button {
                            objectName: "screenChooseButton"
                            text: "Choose picture…"
                            icon.name: "document-open"
                            enabled: !App.screen.busy
                            onClicked: fileDialog.open()
                        }

                        Controls.Button {
                            objectName: "screenClearButton"
                            text: "Clear"
                            icon.name: "edit-clear"
                            enabled: App.screen.frameCount > 0 && !App.screen.busy
                            onClicked: App.screen.clear()
                        }
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.screen.frameCount > 0
            }

            // Where the picture sits under the panel. Above the fit picker
            // rather than below it because the fit is where a framing starts
            // and the drag is what finishes it — and Space Station has neither,
            // so there is no precedent to follow on the order.
            FormCard.AbstractFormDelegate {
                objectName: "screenCropRow"
                background: null
                hoverEnabled: false
                visible: App.screen.frameCount > 0

                contentItem: CropStage {
                    prefix: "screen"
                    frame: App.screen
                    currentFrame: page.previewFrame
                    // 320x160 model units drawn at 640 real pixels: two pixels
                    // per unit. What is on the stage is the source picture
                    // rather than the encoded frame, so the extra resolution is
                    // real — the preview above is the one that must not be
                    // smoothed past what the panel can hold.
                    maxWidth: 640
                }
            }

            FormCard.FormDelegateSeparator {}

            FormComboBox {
                objectName: "screenFitMode"
                text: "How it fits"
                description: "The panel is 160x80 — twice as wide as it is tall, "
                             + "so most pictures have to give something up."
                model: App.screen.fitModes
                currentIndex: App.screen.fitMode
                enabled: App.screen.frameCount > 0 && !App.screen.busy
                onCurrentIndexChanged: App.screen.fitMode = currentIndex
            }

            FormCard.FormDelegateSeparator {}

            FormSpinBox {
                objectName: "screenInterval"
                label: "Milliseconds per frame"
                from: 10
                to: 2550
                stepSize: 10
                value: App.screen.interval
                enabled: App.screen.animated && !App.screen.busy
                onValueChanged: App.screen.interval = value
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "screenSummary"
                text: {
                    if (App.screen.frameCount === 0)
                        return "Nothing loaded";
                    if (App.screen.frameCount === 1)
                        return "One still picture";
                    return App.screen.frameCount + " frames";
                }
                description: {
                    if (App.screen.message !== "")
                        return App.screen.message;
                    if (App.screen.frameCount === 0)
                        return "Choose a PNG, JPEG or GIF.";
                    // The estimate is the point of this line. An upload cannot
                    // be stopped once the pad is switched over, so the cost
                    // belongs on screen before the button is pressed.
                    return "Writing this takes " + App.screen.estimate
                           + ", and the pad restarts itself afterwards.";
                }
            }
        }

        FormCard.FormHeader {
            title: "While the pad is idle"
        }

        FormCard.FormCard {
            ModelSwitch {
                objectName: "screenAlwaysOn"
                text: "Keep the picture on screen"
                // Named for what it does rather than for the bit behind it: the
                // SDK calls this OffScreen, and setting it is what keeps the
                // display lit. Off is a real blank -- which Space Station
                // offers no way to do at all.
                description: checked
                             ? "The screen shows your picture."
                             : "The screen stays dark; the logo button wakes the "
                               + "status view for a couple of seconds."
                value: App.screen.alwaysOn
                enabled: App.screen.loaded && App.screen.supported && !App.screen.busy
                onMoved: (wanted) => App.screen.setAlwaysOn(wanted)
            }

            FormCard.FormDelegateSeparator {}

            ModelSwitch {
                objectName: "screenStatusBar"
                text: "Keep the status bar up"
                description: "Otherwise it hides itself after a moment."
                value: App.screen.statusBarAlwaysOn
                enabled: App.screen.loaded && !App.screen.busy
                onMoved: (wanted) => App.screen.setStatusBarAlwaysOn(wanted)
            }
        }
    }

    footer: Controls.ToolBar {
        position: Controls.ToolBar.Footer

        contentItem: RowLayout {
            spacing: Kirigami.Units.largeSpacing

            Controls.ProgressBar {
                objectName: "screenProgress"
                visible: App.screen.busy
                // Indeterminate until the first packet lands, because the mode
                // switch and the wait for the serial device take five seconds
                // during which a bar sitting at zero looks stuck.
                indeterminate: App.screen.progress === 0
                value: App.screen.progress
                Layout.preferredWidth: Kirigami.Units.gridUnit * 8
            }

            Controls.Label {
                objectName: "screenHint"
                text: {
                    if (App.screen.busy)
                        return App.screen.progressText
                               + " — leave the pad plugged in.";
                    if (App.screen.frameCount === 0)
                        return "Choose a picture to send.";
                    // Before the generic line, because it is the one reason the
                    // button is off that the rest of the page does not show.
                    if (App.screen.uploadBlocked !== "")
                        return App.screen.uploadBlocked;
                    return "Sends over the pad's own upgrade link; it restarts "
                           + "itself when done.";
                }
                // Wraps rather than elides: the reason an upload is refused is
                // an instruction, and half of one is no use.
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.preferredWidth: 0
            }

            Controls.Button {
                objectName: "screenUploadButton"
                text: "Send to the pad"
                icon.name: "document-send"
                enabled: App.screen.canUpload
                onClicked: App.screen.upload()
            }
        }
    }
}
