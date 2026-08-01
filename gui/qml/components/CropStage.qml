// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// Choosing which part of a picture goes onto a device: drag it, zoom it.
//
// A stage with the target window cut out of the middle, the picture drawn under
// it, and everything outside the window dimmed. The picture is dragged under the
// window rather than the window over the picture, which is Space Station's
// gesture on their dock page and the one this borrows.
//
// The model behind it is `gui/models/imaging.py`'s `CropFrame`, and both the
// Dock and Screen pages drive one. Every position here is in the model's own
// stage coordinates and scaled by a single factor, so a narrow window scales the
// whole thing and changes no arithmetic anywhere.
//
// `frame` is the *model* — the dock's or the screen's — not the CropFrame
// itself, since QML binds to properties and a plain Python object has none.
// What it has to offer is the block of properties used below, which both models
// expose under the same names.

pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

ColumnLayout {
    id: root

    // The model driving this stage.
    required property var frame
    // Prefixes every objectName, so two stages in one window are still
    // addressable apart and the existing dock names do not move.
    required property string prefix
    // Which source frame to show. The page decides, because an animation's
    // current frame means different things on the two pages — the dock's is an
    // offset into a trimmed range and the screen's is not.
    property int currentFrame: 0
    // How wide the stage is allowed to get in real pixels. Defaults to one
    // stage unit per pixel, which is what the dock wants; a smaller window in
    // model coordinates passes something larger and gets a sharper scale-up,
    // since what is drawn is the source image rather than the rendered target.
    property real maxWidth: root.frame.stageWidth

    spacing: Kirigami.Units.largeSpacing

    Rectangle {
        id: stage
        objectName: root.prefix + "CropStage"
        Layout.fillWidth: true
        Layout.maximumWidth: root.maxWidth
        Layout.alignment: Qt.AlignHCenter
        implicitWidth: root.maxWidth
        implicitHeight: width * root.frame.stageHeight / root.frame.stageWidth
        color: "black"
        radius: Kirigami.Units.smallSpacing
        clip: true

        // One stage unit in screen pixels.
        readonly property real unit: width / root.frame.stageWidth

        // An AnimatedImage even for a still, which it handles as a one-frame
        // animation: an animation being framed has to show the frame the
        // preview is showing, or the two disagree about what is being cropped.
        // Space Station's own stage plays the whole GIF *ignoring* the crop and
        // zoom while the export honours both, which is worth not reproducing.
        AnimatedImage {
            objectName: root.prefix + "CropImage"
            source: root.frame.imageSource
            // The same bound the model decodes at, and no frame cache. This
            // item loads the file a second time, through QMovie, and a preview
            // timer walks the whole animation — so by default it quietly builds
            // a second full-resolution copy of every frame beside the model's.
            // Measured on a 200-frame 1080p GIF, two full loops:
            //
            //   as written                945 MB, 4 ms/frame
            //   + sourceSize              345 MB, 4 ms/frame
            //   + cache: false              5 MB, 8 ms/frame
            //
            // Four milliseconds against a timer that fires every hundred, for
            // two thirds of a gigabyte.
            sourceSize: Qt.size(root.frame.sourceWidth, root.frame.sourceHeight)
            cache: false
            paused: true
            currentFrame: root.currentFrame
            // Paired with `QImageReader.setAutoTransform` in the model. Without
            // both, a photo carrying an EXIF rotation is framed one way up and
            // sampled the other.
            autoTransform: true
            x: root.frame.imageX * stage.unit
            y: root.frame.imageY * stage.unit
            width: root.frame.imageDrawWidth * stage.unit
            height: root.frame.imageDrawHeight * stage.unit
            // The model has already decided the aspect: “Fit inside” and “Fill”
            // both hand over a box the picture's own shape, and “Stretch” hands
            // over one that is deliberately not.
            fillMode: Image.Stretch
            smooth: true
            asynchronous: false
        }

        // Everything outside the window, dimmed. Four rectangles rather than
        // one shape with a hole in it, which QML has no primitive for.
        Repeater {
            model: [
                {x: 0, y: 0, w: root.frame.stageWidth, h: root.frame.holeY},
                {x: 0, y: root.frame.holeY + root.frame.holeHeight,
                 w: root.frame.stageWidth,
                 h: root.frame.stageHeight - root.frame.holeY - root.frame.holeHeight},
                {x: 0, y: root.frame.holeY,
                 w: root.frame.holeX, h: root.frame.holeHeight},
                {x: root.frame.holeX + root.frame.holeWidth,
                 y: root.frame.holeY,
                 w: root.frame.stageWidth - root.frame.holeX - root.frame.holeWidth,
                 h: root.frame.holeHeight}
            ]

            delegate: Rectangle {
                id: shade
                required property var modelData

                x: shade.modelData.x * stage.unit
                y: shade.modelData.y * stage.unit
                width: shade.modelData.w * stage.unit
                height: shade.modelData.h * stage.unit
                color: "#000000"
                opacity: 0.6
            }
        }

        Rectangle {
            objectName: root.prefix + "CropWindow"
            x: root.frame.holeX * stage.unit
            y: root.frame.holeY * stage.unit
            width: root.frame.holeWidth * stage.unit
            height: root.frame.holeHeight * stage.unit
            color: "transparent"
            border.width: 1
            border.color: Kirigami.Theme.highlightColor
        }

        // The whole stage drags, not only the window. Space Station's own
        // overlays swallow the press outside it, so a drag there does nothing at
        // all — an accident of their stacking rather than a decision, and not
        // one worth reproducing.
        MouseArea {
            id: dragger
            objectName: root.prefix + "CropDrag"
            anchors.fill: parent
            enabled: root.frame.canPan
            cursorShape: root.frame.canPan
                ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                : Qt.ArrowCursor

            property real lastX: 0
            property real lastY: 0

            onPressed: (mouse) => {
                dragger.lastX = mouse.x;
                dragger.lastY = mouse.y;
            }
            // Incremental, against the last event rather than against the
            // press: the model clamps every move, so a delta measured from the
            // press would keep accumulating travel the picture never made and
            // the drag would come unstuck from the pointer.
            onPositionChanged: (mouse) => {
                root.frame.panBy((mouse.x - dragger.lastX) / stage.unit,
                                 (mouse.y - dragger.lastY) / stage.unit);
                dragger.lastX = mouse.x;
                dragger.lastY = mouse.y;
            }
            // Where anything expensive waits for. The stage itself follows the
            // pointer for free, because it only moves an item that is already
            // on the scene graph; re-rendering what the window sees is not
            // free, and on the Screen page it is a re-encode per frame.
            onReleased: root.frame.framingSettled()
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.largeSpacing

        Controls.Label {
            text: "Zoom"
            Layout.minimumWidth: Kirigami.Units.gridUnit * 5
        }

        Controls.Slider {
            id: zoomSlider
            objectName: root.prefix + "Zoom"
            Layout.fillWidth: true
            from: root.frame.zoomMin
            to: root.frame.zoomMax
            stepSize: 1
            snapMode: Controls.Slider.SnapAlways
            value: root.frame.zoom
            onMoved: root.frame.zoom = value
            // A slider dragged across its range fires `onMoved` at every step,
            // which is the same problem the stage's drag has: the picture must
            // follow, and what the window sees need not be recomputed twenty
            // times on the way past. A click on the groove moves and releases
            // in one go, so this covers that too.
            onPressedChanged: {
                if (!zoomSlider.pressed)
                    root.frame.framingSettled();
            }
        }

        Controls.Label {
            objectName: root.prefix + "ZoomLabel"
            text: root.frame.zoomLabel
            Layout.minimumWidth: Kirigami.Units.gridUnit * 3
        }
    }
}
