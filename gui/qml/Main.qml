// SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
//
// SPDX-License-Identifier: GPL-3.0-or-later

// The application shell: a persistent sidebar of sections with the device
// status at the top of it, and failures as an inline message over the page
// rather than a status bar.
//
// No i18n() on our own strings. Kirigami apps get it from a KLocalizedContext
// installed on the engine from C++, which a PySide6 app has no equivalent for,
// so gui/i18n.py installs a shim for Kirigami's own components. i18n() does
// resolve -- it just does not translate. Strings are plain until there is a
// translation story.

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

    // `kinds` is which sort of device a section belongs to, or null for one
    // that belongs to the installation rather than to a device. The sidebar
    // shows the sections of whichever device the picker is on -- see
    // `sectionVisible` -- so choosing a dock does not leave Buttons and
    // Macros on offer for a pad nobody is looking at.
    readonly property var sections: [
        // Every Flydigi device attached, and which pad and dock the rest of the
        // window is showing. First because it is the answer to "what am I
        // looking at", which is a question that did not exist while there
        // could only be one of everything.
        {name: "Devices", icon: "network-card", url: "pages/DevicesPage.qml",
         kinds: null},
        {name: "Controller", icon: "input-gaming", url: "pages/ControllerPage.qml",
         kinds: ["pad"]},
        // Next to Controller because the two are the same scope: what the pad
        // itself holds, as opposed to what one of its four profiles does.
        {name: "Device", icon: "preferences-system", url: "pages/DeviceSettingsPage.qml",
         kinds: ["pad"]},
        {name: "Buttons", icon: "input-keyboard", url: "pages/ButtonsPage.qml",
         kinds: ["pad"]},
        {name: "Macros", icon: "media-record", url: "pages/MacrosPage.qml",
         kinds: ["pad"]},
        {name: "Sticks", icon: "input-gamepad-symbolic", url: "pages/SticksPage.qml",
         kinds: ["pad"]},
        // Beside Sticks because that is what it drives, and the two are read
        // together: a gyro mapping lands on a stick that already has a curve.
        {name: "Gyro", icon: "gnumeric-object-arrow", url: "pages/GyroPage.qml",
         kinds: ["pad"]},
        {name: "Vibration", icon: "media-playback-start", url: "pages/VibrationPage.qml",
         kinds: ["pad"]},
        {name: "Triggers", icon: "input-gamepad", url: "pages/TriggersPage.qml",
         kinds: ["pad"]},
        {name: "Lighting", icon: "color-management", url: "pages/LightingPage.qml",
         kinds: ["pad"]},
        {name: "Screen", icon: "video-display", url: "pages/ScreenPage.qml",
         kinds: ["pad"]},
        // A pad section, not a global one: what it does is write a game's preset
        // into a controller and choose that game's route.
        {name: "Games", icon: "applications-games", url: "pages/GamesPage.qml",
         kinds: ["pad"]},
        {name: "DualSense", icon: "input-gaming-symbolic", url: "pages/DualSensePage.qml",
         kinds: ["pad"]},
        // The charging dock's own pages. `kinds` is what keeps them and the pad's
        // apart: a sidebar offering Buttons and Macros while a dock is
        // selected would be offering to edit something that is not on screen.
        {name: "Dock", icon: "battery-full-charging", url: "pages/DockPage.qml",
         kinds: ["dock"]},
        // Neither device's: udev rules, the daemon's unit and the menu entry
        // belong to the installation.
        {name: "Setup", icon: "configure", url: "pages/SetupPage.qml",
         kinds: null}
    ]

    property int currentSection: -1

    function indexOfSection(name) {
        for (let i = 0; i < sections.length; ++i)
            if (sections[i].name === name)
                return i
        return -1
    }

    // Whether a section belongs to the device the picker is on. Sections are
    // hidden rather than removed: the list stays the same length, so the page
    // cache and every action's index stay valid, and a section reappears the
    // moment the other kind of device is selected again.
    function sectionVisible(index) {
        const kinds = sections[index].kinds
        return !kinds || kinds.indexOf(App.devices.currentKind) >= 0
    }

    // Which kind of device the window last moved itself to. Picking a dock in
    // the header opens the Dock page and picking a pad brings you back, which
    // is what "one picker, one device" has to mean when the two kinds have
    // different pages -- but only when the *kind* changes. Choosing the other
    // pad while editing its lighting should not throw you back to Controller.
    property string shownKind: ""

    // A bound property with its own change handler rather than a Connections
    // block: `Connections.target` is typed QObject, and qmllint cannot see that
    // a QAbstractListModel is one -- the generated qmltypes names the prototype
    // and stops there, so it reads as assigning the wrong type.
    readonly property string deviceKind: App.devices.currentKind

    onDeviceKindChanged: {
        if (deviceKind === shownKind)
            return
        shownKind = deviceKind
        openSection(indexOfSection(deviceKind === "dock" ? "Dock" : "Controller"))
    }

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

    // What the sidebar actually offers, and a way to press one of its entries.
    // Both exist for `test_the_drawer_offers_every_section`: the drawer's
    // actions are written out one by one and nothing keeps them in step with
    // `sections`, so the test has to read the drawer rather than the list it
    // is supposed to mirror. Reaching in from Python cannot do it -- the
    // drawer's own type has no Python converter.
    // `globalDrawer` is typed as the base OverlayDrawer, which has no actions,
    // so it is asserted back to what it really is -- the same move the page
    // stack's `currentItem` needs below.
    //
    // Only the visible ones, and only they are pressable, because that is what
    // the sidebar is: with a dock selected it offers three entries, and a test
    // that read the other twelve would be asserting about a menu nobody is
    // being shown.
    readonly property var drawerActions:
        (globalDrawer as Kirigami.GlobalDrawer).actions.filter(a => a.visible)

    readonly property var drawerSections: drawerActions.map(a => a.text)

    function pressDrawerAction(index) {
        drawerActions[index].trigger()
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

        // One action per entry in `sections`, in the same order, and there is no
        // mechanism keeping them in step -- `actions` is a list of Action
        // objects, not something a Repeater can fill. Adding the Screen page
        // without adding its action here shifted every label after it by one
        // and pushed Setup off the end entirely, where it stayed unreachable
        // from the sidebar until a screenshot showed it missing. So
        // `test_the_drawer_offers_every_section` asserts the pairing.
        actions: [
            Kirigami.Action {
                objectName: "sectionDevices"
                text: root.sections[0].name
                icon.name: root.sections[0].icon
                visible: root.sectionVisible(0)
                checkable: true
                checked: root.currentSection === 0
                onTriggered: root.openSection(0)
            },
            Kirigami.Action {
                objectName: "sectionController"
                text: root.sections[1].name
                icon.name: root.sections[1].icon
                visible: root.sectionVisible(1)
                checkable: true
                checked: root.currentSection === 1
                onTriggered: root.openSection(1)
            },
            Kirigami.Action {
                objectName: "sectionDevice"
                text: root.sections[2].name
                icon.name: root.sections[2].icon
                visible: root.sectionVisible(2)
                checkable: true
                checked: root.currentSection === 2
                onTriggered: root.openSection(2)
            },
            Kirigami.Action {
                objectName: "sectionButtons"
                text: root.sections[3].name
                icon.name: root.sections[3].icon
                visible: root.sectionVisible(3)
                checkable: true
                checked: root.currentSection === 3
                onTriggered: root.openSection(3)
            },
            Kirigami.Action {
                objectName: "sectionMacros"
                text: root.sections[4].name
                icon.name: root.sections[4].icon
                visible: root.sectionVisible(4)
                checkable: true
                checked: root.currentSection === 4
                onTriggered: root.openSection(4)
            },
            Kirigami.Action {
                objectName: "sectionSticks"
                text: root.sections[5].name
                icon.name: root.sections[5].icon
                visible: root.sectionVisible(5)
                checkable: true
                checked: root.currentSection === 5
                onTriggered: root.openSection(5)
            },
            Kirigami.Action {
                objectName: "sectionGyro"
                text: root.sections[6].name
                icon.name: root.sections[6].icon
                visible: root.sectionVisible(6)
                checkable: true
                checked: root.currentSection === 6
                onTriggered: root.openSection(6)
            },
            Kirigami.Action {
                objectName: "sectionVibration"
                text: root.sections[7].name
                icon.name: root.sections[7].icon
                visible: root.sectionVisible(7)
                checkable: true
                checked: root.currentSection === 7
                onTriggered: root.openSection(7)
            },
            Kirigami.Action {
                objectName: "sectionTriggers"
                text: root.sections[8].name
                icon.name: root.sections[8].icon
                visible: root.sectionVisible(8)
                checkable: true
                checked: root.currentSection === 8
                onTriggered: root.openSection(8)
            },
            Kirigami.Action {
                objectName: "sectionLighting"
                text: root.sections[9].name
                icon.name: root.sections[9].icon
                visible: root.sectionVisible(9)
                checkable: true
                checked: root.currentSection === 9
                onTriggered: root.openSection(9)
            },
            Kirigami.Action {
                objectName: "sectionScreen"
                text: root.sections[10].name
                icon.name: root.sections[10].icon
                visible: root.sectionVisible(10)
                checkable: true
                checked: root.currentSection === 10
                onTriggered: root.openSection(10)
            },
            Kirigami.Action {
                objectName: "sectionGames"
                text: root.sections[11].name
                icon.name: root.sections[11].icon
                visible: root.sectionVisible(11)
                checkable: true
                checked: root.currentSection === 11
                onTriggered: root.openSection(11)
            },
            Kirigami.Action {
                objectName: "sectionDualSense"
                text: root.sections[12].name
                icon.name: root.sections[12].icon
                visible: root.sectionVisible(12)
                checkable: true
                checked: root.currentSection === 12
                onTriggered: root.openSection(12)
            },
            Kirigami.Action {
                objectName: "sectionDock"
                text: root.sections[13].name
                icon.name: root.sections[13].icon
                visible: root.sectionVisible(13)
                checkable: true
                checked: root.currentSection === 13
                onTriggered: root.openSection(13)
            },
            Kirigami.Action {
                objectName: "sectionSetup"
                text: root.sections[14].name
                icon.name: root.sections[14].icon
                visible: root.sectionVisible(14)
                checkable: true
                checked: root.currentSection === 14
                onTriggered: root.openSection(14)
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
        // Controller, not the first section: Devices sits first in the sidebar
        // because it answers "what is attached", but the pad is what the app is
        // for, and a desk with one of everything should not have to click past
        // an inventory of it on every launch.
        openSection(indexOfSection("Controller"))
        // Opening the device is deliberately not something App's constructor
        // does -- see gui/app.py. This is a no-op if a test already started it.
        //
        // Nothing kicks a first read off here any more. `start` begins the poll
        // and asks the pad how it is doing; the answer is what fills the window,
        // and a pad that is not there yet fills it whenever it arrives instead.
        // One path for both, so the second one cannot quietly stop working.
        App.start()
    }
}
