// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The application shell: a persistent sidebar of sections with the device
// status at the top of it, and failures as an inline message over the page
// rather than a status bar.
//
// No i18n() here. Kirigami apps get it from a KLocalizedContext installed on
// the engine from C++, which a PySide6 app has no equivalent for -- calling it
// would fail at load. Strings are plain until there is a translation story.

import QtQuick
import org.kde.kirigami as Kirigami
import Apex5

import "components"

Kirigami.ApplicationWindow {
    id: root
    objectName: "mainWindow"

    title: "Flydigi Apex 5"
    width: Kirigami.Units.gridUnit * 48
    height: Kirigami.Units.gridUnit * 38
    minimumWidth: Kirigami.Units.gridUnit * 30
    minimumHeight: Kirigami.Units.gridUnit * 24

    readonly property var sections: [
        {name: "Controller", icon: "input-gaming", url: "pages/ControllerPage.qml"},
        {name: "Buttons", icon: "input-keyboard", url: "pages/ButtonsPage.qml"},
        {name: "Sticks", icon: "input-gamepad-symbolic", url: "pages/SticksPage.qml"},
        {name: "Vibration", icon: "media-playback-start", url: "pages/VibrationPage.qml"},
        {name: "Triggers", icon: "input-gamepad", url: "pages/TriggersPage.qml"},
        {name: "Lighting", icon: "color-management", url: "pages/LightingPage.qml"},
        {name: "Screen", icon: "video-display", url: "pages/ScreenPage.qml"},
        {name: "Games", icon: "applications-games", url: "pages/GamesPage.qml"},
        {name: "Setup", icon: "configure", url: "pages/SetupPage.qml"}
    ]

    property int currentSection: -1

    // The window's own view of what it is showing. `pageStack` is a Kirigami
    // type, and anything outside QML that wants to know what is open should
    // ask the window rather than reach through it.
    readonly property int openPageCount: pageStack.depth
    readonly property string openPageTitle: {
        // currentItem is typed as a plain Item, so the page's own properties
        // are only reachable once it is asserted back to what it really is.
        const page = pageStack.currentItem as Kirigami.Page
        return page ? page.title : ""
    }

    // Each page is built once, parented, and kept.
    //
    // Handing pageStack a URL or a Component instead makes it create the page
    // with no visual parent, which the engine reports as an object "not placed
    // in the graphics scene" -- invisible unless something is listening to
    // QQmlEngine::warnings, but there all the same. Creating it here, parented
    // and synchronously, is quiet, and keeping the instance means a section
    // remembers its scroll position instead of being rebuilt on every visit.
    readonly property var pageCache: ({})

    function pageFor(index) {
        if (!pageCache[index]) {
            const url = Qt.resolvedUrl(sections[index].url)
            const component = Qt.createComponent(url, Component.PreferSynchronous)
            if (component.status === Component.Error) {
                console.error("cannot load " + url + ": " + component.errorString())
                return null
            }
            pageCache[index] = component.createObject(pageStack)
        }
        return pageCache[index]
    }

    function openSection(index) {
        if (index === currentSection)
            return
        const page = pageFor(index)
        if (!page)
            return
        currentSection = index
        // replace() on an empty stack has nothing to replace and drops the
        // page on the floor -- it still gets created, just never shown.
        if (pageStack.depth === 0)
            pageStack.push(page)
        else
            pageStack.replace(page)
    }

    globalDrawer: Kirigami.GlobalDrawer {
        objectName: "globalDrawer"
        isMenu: false
        modal: false
        collapsible: true

        // No title here: it would just repeat the window's own. The space is
        // worth more as the device status, which is the one thing that should
        // be visible from every section.
        header: DeviceStatus {
            objectName: "deviceStatus"
        }

        actions: [
            Kirigami.Action {
                objectName: "sectionController"
                text: root.sections[0].name
                icon.name: root.sections[0].icon
                checkable: true
                checked: root.currentSection === 0
                onTriggered: root.openSection(0)
            },
            Kirigami.Action {
                objectName: "sectionButtons"
                text: root.sections[1].name
                icon.name: root.sections[1].icon
                checkable: true
                checked: root.currentSection === 1
                onTriggered: root.openSection(1)
            },
            Kirigami.Action {
                objectName: "sectionSticks"
                text: root.sections[2].name
                icon.name: root.sections[2].icon
                checkable: true
                checked: root.currentSection === 2
                onTriggered: root.openSection(2)
            },
            Kirigami.Action {
                objectName: "sectionVibration"
                text: root.sections[3].name
                icon.name: root.sections[3].icon
                checkable: true
                checked: root.currentSection === 3
                onTriggered: root.openSection(3)
            },
            Kirigami.Action {
                objectName: "sectionTriggers"
                text: root.sections[4].name
                icon.name: root.sections[4].icon
                checkable: true
                checked: root.currentSection === 4
                onTriggered: root.openSection(4)
            },
            Kirigami.Action {
                objectName: "sectionLighting"
                text: root.sections[5].name
                icon.name: root.sections[5].icon
                checkable: true
                checked: root.currentSection === 5
                onTriggered: root.openSection(5)
            },
            Kirigami.Action {
                objectName: "sectionGames"
                text: root.sections[6].name
                icon.name: root.sections[6].icon
                checkable: true
                checked: root.currentSection === 6
                onTriggered: root.openSection(6)
            },
            Kirigami.Action {
                objectName: "sectionSetup"
                text: root.sections[7].name
                icon.name: root.sections[7].icon
                checkable: true
                checked: root.currentSection === 7
                onTriggered: root.openSection(7)
            }
        ]
    }

    // Over the page area rather than in ApplicationWindow.header, which is not
    // positioned correctly when the global drawer is a persistent sidebar: it
    // gets the width of the content area but the x of the window, so it hangs
    // off the left edge. Anchored to pageStack it lines up with the content by
    // construction, and no page has to know about it.
    Kirigami.InlineMessage {
        objectName: "errorMessage"
        parent: root.pageStack
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Kirigami.Units.smallSpacing
        z: 100
        type: Kirigami.MessageType.Error
        text: App.device.error
        visible: App.device.error !== ""
        actions: [
            Kirigami.Action {
                objectName: "dismissError"
                text: "Dismiss"
                icon.name: "dialog-close"
                onTriggered: App.device.error = ""
            }
        ]
    }

    // Progress messages are transient by nature, so they pass through rather
    // than occupying a permanent strip of the window.
    Connections {
        target: App.device
        function onStatusChanged() {
            if (App.device.status !== "")
                root.showPassiveNotification(App.device.status, "short")
        }
    }

    Component.onCompleted: {
        openSection(0)
        // Opening the device is deliberately not something App's constructor
        // does -- see gui/app.py. This is a no-op if a test already started it.
        App.start()
        // Kick the first read off once the window is up, so it appears
        // immediately rather than after a second of blocking HID traffic.
        Qt.callLater(App.reload)
    }
}
