// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The CD2 charging dock: its four switches and its lighting.
//
// Whichever dock the picker has selected — there can be more than one, and they
// are told apart by uid, so nothing here says "the dock".
//
// The switches take effect the moment they move and are read back afterwards,
// like the pad's own device settings and for the same reason: an ack carries the
// command id and nothing about what it changed. Lighting is different — the dock
// plays frames rather than generating them, so an effect is about 24 kB computed
// here and uploaded in 487 packets, which is a button and a progress bar rather
// than a switch.

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
    objectName: "dockPage"
    title: "Dock"

    // Which swatch the colour dialog is editing.
    property int editingColour: -1

    // The wedge plays a chosen animation at the interval the dock will play it,
    // because a still frame of one says almost nothing and the upload is far
    // too long to be how you find out what you picked. Each tick samples one
    // frame and remembers it, so a second time round the loop costs nothing.
    Timer {
        objectName: "dockPreviewTimer"
        running: App.dock.isPicture && App.dock.animated && page.visible
        // Floored, because the spin box goes below what a QML timer honours.
        interval: Math.max(20, App.dock.intervalMs)
        repeat: true
        onTriggered: App.dock.previewFrame = App.dock.previewFrame + 1
    }

    Dialogs.FileDialog {
        id: pictureDialog
        objectName: "dockPictureDialog"
        title: "Choose a picture for the dock"
        nameFilters: ["Images and animations (*.png *.jpg *.jpeg *.gif *.bmp *.webp)",
                      "All files (*)"]
        onAccepted: App.dock.openImage(selectedFile)
    }

    ColumnLayout {
        spacing: Kirigami.Units.largeSpacing

        Kirigami.PlaceholderMessage {
            objectName: "noDock"
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 3
            visible: App.devices.dockCount === 0
            icon.name: "battery-full-charging"
            text: "No charging dock"
            explanation: "A CD2 has to be plugged into the computer, not only "
                       + "into power. With one attached, pick it in the sidebar."
        }

        Kirigami.PlaceholderMessage {
            objectName: "dockNotSelected"
            Layout.fillWidth: true
            Layout.topMargin: Kirigami.Units.gridUnit * 3
            visible: App.devices.dockCount > 0 && !App.dock.present
            icon.name: "battery-full-charging"
            text: "Reading the dock…"
            explanation: App.dock.error !== "" ? App.dock.error : ""
        }

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present
            title: "Device"
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormTextDelegate {
                objectName: "dockModel"
                text: App.dock.model !== "" ? App.dock.model : "Charging dock"
                description: {
                    const bits = [];
                    if (App.dock.nickname !== "")
                        bits.push("named “" + App.dock.nickname + "”");
                    if (App.dock.firmware !== "")
                        bits.push("firmware " + App.dock.firmware);
                    bits.push(App.dock.dockedState);
                    return bits.join(" · ");
                }
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormTextDelegate {
                objectName: "dockUid"
                text: "Uid"
                // The name this dock keeps across reconnects, and what
                // `flydigi-charger --device` takes.
                description: App.dock.uid !== "" ? App.dock.uid : "not reported"
            }
        }

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present
            title: "Switches"
        }

        // Said rather than left to be inferred. These four take effect as they
        // move -- they are standalone commands with nothing to commit, exactly
        // like the pad's own device settings -- while the lighting below has an
        // Apply button, because an effect is 24 kB of frames and several
        // seconds. One page doing both without saying so reads as a page whose
        // top half you forgot to apply.
        Controls.Label {
            objectName: "dockSwitchNote"
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            visible: App.dock.present
            wrapMode: Text.WordWrap
            font: Kirigami.Theme.smallFont
            color: Kirigami.Theme.disabledTextColor
            text: "These take effect as you move them, and what each one shows "
                + "is what the dock reported afterwards — not what was asked for."
        }

        // Not a rule this enforces -- both switches are set as asked, because a
        // page that silently turned one off would be lying about the device's
        // state. What it does is say which one wins, at the moment the two are
        // on together, rather than leaving a switch that visibly does nothing.
        Kirigami.InlineMessage {
            objectName: "dockSleepConflict"
            Layout.fillWidth: true
            Layout.leftMargin: Kirigami.Units.largeSpacing
            Layout.rightMargin: Kirigami.Units.largeSpacing
            visible: App.dock.present && App.dock.sleepWhenCharging
                     && (App.dock.ledSync || App.dock.showAnimationWhenCharging)
            type: Kirigami.MessageType.Information
            text: {
                const shadowed = [];
                if (App.dock.ledSync)
                    shadowed.push("Lighting sync");
                if (App.dock.showAnimationWhenCharging)
                    shadowed.push("Power display");
                return "“Sleep while docked” wins over "
                     + shadowed.join(" and ")
                     + " for as long as a pad is in the dock, since both "
                     + "devices' lighting is off then. Space Station forces "
                     + "these apart in its own window for the same reason.";
            }
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormSwitchDelegate {
                objectName: "dockSleepWhenCharging"
                // Named for what it does. Flydigi's own label is "Intelligent
                // start", which says nothing about taking two devices' lighting
                // down -- and that is the whole of what you notice.
                text: "Sleep while docked"
                description: "Both the pad and the dock go dark while a pad "
                           + "sits in it. Flydigi call this “Intelligent start”."
                checked: App.dock.sleepWhenCharging
                onToggled: App.dock.sleepWhenCharging = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dockLedSync"
                text: "Lighting sync"
                description: "keep the dock's lighting in step with the pad's — "
                           + "the two arrange it between themselves, with nothing "
                           + "host-side in the loop"
                checked: App.dock.ledSync
                onToggled: App.dock.ledSync = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dockCloseWithSystem"
                text: "Close when shut down"
                description: "go dark when the host powers off"
                checked: App.dock.closeWithSystem
                onToggled: App.dock.closeWithSystem = checked
            }

            FormCard.FormDelegateSeparator {}

            FormCard.FormSwitchDelegate {
                objectName: "dockPowerDisplay"
                text: "Power display"
                // Space Station forces this and Intelligent start apart in its
                // own UI. Nothing in the firmware does, so nothing here does.
                description: "play the charge animation while a pad is docked"
                checked: App.dock.showAnimationWhenCharging
                onToggled: App.dock.showAnimationWhenCharging = checked
            }
        }

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present
            title: "Lighting"
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormComboBox {
                objectName: "dockModeBox"
                text: "Effect"
                description: "162 LEDs, computed here and uploaded as frames"
                model: App.dock.modeNames
                currentIndex: App.dock.modeIndex
                onActivated: (index) => App.dock.modeIndex = index
            }

            FormCard.FormDelegateSeparator {
                visible: !App.dock.isPicture
            }

            FormCard.AbstractFormDelegate {
                objectName: "dockColourRow"
                background: null
                hoverEnabled: false
                visible: App.dock.coloursUsed > 0

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: App.dock.coloursUsed === 1 ? "Colour" : "Colours"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    // One row per swatch, and the colour comes off the row.
                    // This used to run to `coloursUsed` and index into
                    // `App.dock.colours`, which rebuilt a Python list of hex
                    // strings per read and was read twice per swatch -- once
                    // for the length, once for the element. `DockColoursModel`
                    // is the same shape LightingPage's swatches already had.
                    Repeater {
                        model: App.dock.colours

                        delegate: Controls.AbstractButton {
                            id: swatch

                            required property int index
                            required property string colour

                            objectName: "dockColourSwatch" + swatch.index
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
                                color: swatch.colour
                                radius: Kirigami.Units.smallSpacing
                                border.width: 1
                                border.color: Kirigami.Theme.disabledTextColor
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.coloursUsed > 0
            }

            FormCard.AbstractFormDelegate {
                objectName: "dockBrightnessRow"
                background: null
                hoverEnabled: false
                // A picture goes up at full brightness with no control over it,
                // which is what Space Station does too. A slider that did
                // nothing would be worse than none.
                visible: !App.dock.isPicture

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: "Brightness"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Controls.Slider {
                        objectName: "dockBrightness"
                        Layout.fillWidth: true
                        from: 1
                        to: 100
                        stepSize: 1
                        value: App.dock.brightness
                        onMoved: App.dock.brightness = value
                    }

                    Controls.Label {
                        text: App.dock.brightness
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 2
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.periodMax > App.dock.periodMin
            }

            FormCard.AbstractFormDelegate {
                objectName: "dockIntervalRow"
                background: null
                hoverEnabled: false
                // A mode with one allowed value has nothing to offer here.
                visible: App.dock.periodMax > App.dock.periodMin

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: "Frame interval"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Controls.Slider {
                        objectName: "dockInterval"
                        Layout.fillWidth: true
                        from: App.dock.periodMin
                        to: App.dock.periodMax
                        stepSize: 1
                        value: App.dock.period
                        onMoved: App.dock.period = value
                    }

                    Controls.Label {
                        // Flydigi's own number, and bigger is slower — said
                        // rather than inverted, because their UI says it too.
                        text: App.dock.period + " (bigger is slower)"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 9
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.usesDirection
            }

            FormComboBox {
                objectName: "dockDirectionBox"
                text: "Direction"
                visible: App.dock.usesDirection
                model: App.dock.directionNames
                currentIndex: App.dock.directionIndex
                onActivated: (index) => App.dock.directionIndex = index
            }

            FormCard.FormDelegateSeparator {
                visible: !App.dock.isPicture
            }

            // Applying a computed effect is the end of this card, so the button
            // belongs here. A picture is not: it has a whole card of its own
            // below, and its copy of this row sits at the bottom of that.
            DockApplyRow {
                objectName: "dockApplyRow"
                visible: !App.dock.isPicture
                hint: "The dock has no effect generator: applying computes "
                    + "every frame here and uploads the lot, which takes a few "
                    + "seconds."
            }
        }

        // -- the picture ---------------------------------------------------
        //
        // Only while “Picture” is the chosen effect. It is a section of the
        // Lighting card's business rather than a page of its own: what it
        // produces is the frames that card's Apply button sends, and a separate
        // page with its own Apply would have two buttons writing one config.

        FormCard.FormHeader {
            Layout.fillWidth: true
            visible: App.dock.present && App.dock.isPicture
            title: "Picture"
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present && App.dock.isPicture

            // What the dock will show, on the panel it will show it on. One
            // pixel per LED is a drastic resampling — a photograph becomes 162
            // dots — so this is not a nicety: it is the only way to find out
            // whether a picture survives the trip before spending half a
            // minute of packets discovering that it does not.
            FormCard.AbstractFormDelegate {
                objectName: "dockWedgeRow"
                background: null
                hoverEnabled: false

                contentItem: ColumnLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.maximumWidth: Kirigami.Units.gridUnit * 24
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: Kirigami.Units.gridUnit * 18
                        implicitHeight: width * App.dock.wedgeViewHeight
                                              / App.dock.wedgeViewWidth
                        color: "#1b1c1e"
                        radius: Kirigami.Units.smallSpacing

                        LedWedge {
                            objectName: "dockWedge"
                            anchors.fill: parent
                            anchors.margins: Kirigami.Units.smallSpacing
                            centres: App.dock.wedgeCentres
                            outline: App.dock.wedgeOutline
                            viewWidth: App.dock.wedgeViewWidth
                            viewHeight: App.dock.wedgeViewHeight
                            ledRadius: App.dock.wedgeRadius
                            colours: App.dock.frameColours
                        }
                    }

                    Controls.Label {
                        objectName: "dockWedgeCaption"
                        Layout.alignment: Qt.AlignHCenter
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        text: App.dock.hasImage
                            ? (App.dock.animated
                               ? "frame " + (App.dock.previewFrame + 1) + " of "
                                 + App.dock.frameCount
                               : "162 LEDs, one pixel each")
                            : "No picture chosen"
                    }
                }
            }

            FormCard.FormDelegateSeparator {}

            FormCard.AbstractFormDelegate {
                objectName: "dockPictureFileRow"
                background: null
                hoverEnabled: false

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        elide: Text.ElideMiddle
                        text: App.dock.hasImage ? App.dock.imageName
                                                : "PNG, JPEG or an animated GIF"
                        color: App.dock.hasImage ? Kirigami.Theme.textColor
                                                 : Kirigami.Theme.disabledTextColor
                    }

                    Controls.Button {
                        objectName: "dockChoosePictureButton"
                        text: "Choose picture…"
                        icon.name: "document-open"
                        onClicked: pictureDialog.open()
                    }

                    Controls.Button {
                        objectName: "dockClearPictureButton"
                        text: "Clear"
                        icon.name: "edit-clear"
                        enabled: App.dock.hasImage
                        onClicked: App.dock.clearImage()
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.imageMessage !== ""
            }

            FormCard.FormTextDelegate {
                objectName: "dockPictureMessage"
                visible: App.dock.imageMessage !== ""
                text: App.dock.imageMessage
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.hasImage
            }

            // The crop stage. The window in the middle is the 334x304 canvas
            // the LEDs are read out of, at Space Station's own size, and the
            // picture is dragged under it rather than the window over it.
            FormCard.AbstractFormDelegate {
                objectName: "dockCropRow"
                background: null
                hoverEnabled: false
                visible: App.dock.hasImage

                contentItem: CropStage {
                    prefix: "dock"
                    frame: App.dock
                    // Clamped: a source that decoded fewer frames than the trim
                    // believes would otherwise be asked for a frame it does not
                    // have.
                    currentFrame: Math.min(App.dock.sourceFrameCount - 1,
                                           App.dock.trimMin + App.dock.previewFrame)
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.hasImage
            }

            FormComboBox {
                objectName: "dockFitBox"
                visible: App.dock.hasImage
                text: "Fit"
                description: "how the picture starts out in the window — "
                           + "Space Station offers no choice here and always "
                           + "fills"
                model: App.dock.imageFitModes
                currentIndex: App.dock.imageFitMode
                onActivated: (index) => App.dock.imageFitMode = index
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.hasImage && App.dock.sourceFrameCount > 1
            }

            // The trim bar. Both ends are inclusive frame indices, as Space
            // Station's are, and the range will not go below one frame or above
            // the two hundred their own bar stops at.
            FormCard.AbstractFormDelegate {
                objectName: "dockTrimRow"
                background: null
                hoverEnabled: false
                visible: App.dock.hasImage && App.dock.sourceFrameCount > 1

                contentItem: ColumnLayout {
                    spacing: Kirigami.Units.smallSpacing

                    Controls.Label {
                        text: "Frames"
                        Layout.fillWidth: true
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Kirigami.Units.gridUnit * 2.5
                        color: "black"
                        radius: Kirigami.Units.smallSpacing
                        clip: true

                        Image {
                            objectName: "dockFilmstrip"
                            anchors.fill: parent
                            source: App.dock.filmstripSource
                            fillMode: Image.Stretch
                            smooth: true
                            cache: true
                        }

                        // The frames that will not be sent, greyed where they
                        // sit rather than only implied by two handles below.
                        Repeater {
                            model: [
                                {a: 0, b: App.dock.trimMin},
                                {a: App.dock.trimMax + 1, b: App.dock.sourceFrameCount}
                            ]

                            delegate: Rectangle {
                                id: dropped
                                required property var modelData

                                readonly property real step:
                                    parent.width / Math.max(1, App.dock.sourceFrameCount)
                                x: dropped.modelData.a * dropped.step
                                width: Math.max(0, (dropped.modelData.b - dropped.modelData.a)
                                                   * dropped.step)
                                height: parent.height
                                color: "#000000"
                                opacity: 0.65
                            }
                        }
                    }

                    Controls.RangeSlider {
                        id: trimSlider
                        objectName: "dockTrim"
                        Layout.fillWidth: true
                        from: 0
                        to: Math.max(1, App.dock.sourceFrameCount - 1)
                        stepSize: 1
                        snapMode: Controls.RangeSlider.SnapAlways

                        // Set rather than bound. Dragging a handle assigns to
                        // its own `value`, which destroys any binding that was
                        // there — so the model pushes both ends back on every
                        // change instead, and a range the model refuses visibly
                        // springs back.
                        // Each end clamps against the other as it is written,
                        // so a pair that moves wholesale one way or the other
                        // needs the first end written again once the second has
                        // made room. Cheaper than deciding which order applies.
                        function showTrim() {
                            trimSlider.first.value = App.dock.trimMin;
                            trimSlider.second.value = App.dock.trimMax;
                            trimSlider.first.value = App.dock.trimMin;
                        }

                        Component.onCompleted: trimSlider.showTrim()

                        Connections {
                            target: App.dock
                            function onImageChanged() { trimSlider.showTrim(); }
                        }

                        first.onMoved: App.dock.setTrim(trimSlider.first.value,
                                                        trimSlider.second.value)
                        second.onMoved: App.dock.setTrim(trimSlider.first.value,
                                                         trimSlider.second.value)
                    }

                    Controls.Label {
                        objectName: "dockTrimLabel"
                        Layout.fillWidth: true
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        text: "frames " + (App.dock.trimMin + 1) + " to "
                            + (App.dock.trimMax + 1) + " of "
                            + App.dock.sourceFrameCount
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.animated
            }

            FormCard.AbstractFormDelegate {
                objectName: "dockIntervalPictureRow"
                background: null
                hoverEnabled: false
                visible: App.dock.animated

                contentItem: RowLayout {
                    spacing: Kirigami.Units.largeSpacing

                    Controls.Label {
                        text: "Frame time"
                        Layout.minimumWidth: Kirigami.Units.gridUnit * 7
                    }

                    Controls.SpinBox {
                        objectName: "dockPictureInterval"
                        from: App.dock.intervalMin
                        to: App.dock.intervalMax
                        // One unit of what the dock stores, so every step of
                        // the box is a step the hardware can actually take.
                        stepSize: App.dock.intervalStep
                        value: App.dock.intervalMs
                        editable: true
                        // `org.kde.desktop` turns the wheel on for SpinBox and
                        // Qt's own default leaves it off, so without this a
                        // scroll over the box retimes the animation instead of
                        // moving the page. See components/FormComboBox.qml.
                        wheelEnabled: false
                        onValueModified: App.dock.intervalMs = value
                    }

                    Controls.Label {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 0
                        wrapMode: Text.WordWrap
                        font: Kirigami.Theme.smallFont
                        color: Kirigami.Theme.disabledTextColor
                        // Said because the wire is coarser than the box, and
                        // because the number is measured rather than assumed:
                        // Space Station writes it as though a unit were 10 ms
                        // and their animations play at half speed on the dock.
                        text: "ms per frame — the GIF's own average to start "
                            + "with. The dock stores this in units of "
                            + App.dock.intervalStep + " ms, so that is what it "
                            + "rounds to."
                    }
                }
            }

            FormCard.FormDelegateSeparator {
                visible: App.dock.hasImage
            }

            FormCard.FormTextDelegate {
                objectName: "dockPictureCost"
                visible: App.dock.hasImage
                text: App.dock.frameCount === 1
                      ? "One frame" : App.dock.frameCount + " frames"
                description: App.dock.imageEstimate
            }

            FormCard.FormDelegateSeparator {}

            // The bottom of everything it applies. The Lighting card's copy is
            // hidden while a picture is the effect, so there is one of these on
            // screen and it is always below the work.
            DockApplyRow {
                objectName: "dockPictureApplyRow"
                hint: App.dock.hasImage
                    ? "The dock plays frames rather than generating them, so "
                      + "this sends every frame of the picture as it is framed "
                      + "above."
                    : "Choose a picture first — a custom effect with no frames "
                      + "would leave the dock playing whatever is still in its "
                      + "frame memory."
            }
        }

        FormCard.FormCard {
            Layout.fillWidth: true
            visible: App.dock.present

            FormCard.FormTextDelegate {
                objectName: "dockDefaultNote"
                text: "One of Flydigi's effects is missing"
                description: "“Default” is not computed by Space Station either "
                           + "— it uploads a file its installer ships, which "
                           + "this project does not have."
            }
        }
    }

    Dialogs.ColorDialog {
        id: colourDialog
        objectName: "dockColourDialog"
        title: "Dock lighting colour"
        onAccepted: {
            if (page.editingColour >= 0)
                App.dock.setColour(page.editingColour, selectedColor);
            page.editingColour = -1;
        }
    }
}
