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

// The sidebar's actions are built from `sections` inside a Component, and
// reaching `root` from there is what this pragma is for -- without it the
// reference resolves at runtime and qmllint reports it as unqualified, which is
// a warning this project does not carry.
pragma ComponentBehavior: Bound

import QtQuick
import org.kde.kirigami as Kirigami
import Apex5

import "components"

Kirigami.ApplicationWindow {
    id: root
    objectName: "mainWindow"

    title: "OpenFlydigi"
    width: Kirigami.Units.gridUnit * 48
    height: Kirigami.Units.gridUnit * 38
    minimumWidth: Kirigami.Units.gridUnit * 30
    minimumHeight: Kirigami.Units.gridUnit * 24

    // `kinds` is which sort of device a section belongs to, or null for one
    // that belongs to the installation rather than to a device. The sidebar
    // shows the sections of whichever device the picker is on -- see
    // `sectionVisible` -- so choosing a dock does not leave Buttons and
    // Macros on offer for a pad nobody is looking at.
    //
    // `needs` is the second filter and a different question: not what sort of
    // device this is, but what that particular model actually has. Both pads
    // this project drives are pads, and only one of them has a screen or force
    // triggers. See `identity.CAPABILITIES`.
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
         kinds: ["pad"], needs: "adaptive_triggers"},
        {name: "Lighting", icon: "color-management", url: "pages/LightingPage.qml",
         kinds: ["pad"]},
        {name: "Screen", icon: "video-display", url: "pages/ScreenPage.qml",
         kinds: ["pad"], needs: "screen"},
        // A pad section, not a global one: what it does is write a game's preset
        // into a controller and choose that game's route.
        {name: "Games", icon: "applications-games", url: "pages/GamesPage.qml",
         kinds: ["pad"], needs: "adaptive_triggers"},
        {name: "DualSense", icon: "input-gaming-symbolic", url: "pages/DualSensePage.qml",
         kinds: ["pad"], needs: "adaptive_triggers"},
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
        const section = sections[index]
        const kinds = section.kinds
        if (kinds && kinds.indexOf(App.devices.currentKind) < 0)
            return false
        // `needs` is hardware, where `kinds` is which sort of device. A Vader
        // is a pad and gets the pad's pages, but it has no screen and no force
        // triggers -- so a Screen page for a panel it does not have, or the six
        // trigger effects it cannot play, would be the window offering to
        // configure something that is not there. Games and DualSense go with
        // the triggers: every route in both exists to deliver trigger effects.
        return !section.needs || App.devices.capabilities[section.needs] === true
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
    // Both exist for `test_the_drawer_offers_every_section`, which reads the
    // drawer rather than the list it is drawn from -- so it still asserts that
    // the sidebar shows what it should, in the order this file chose, and not
    // merely that a loop ran. Reaching in from Python cannot do it: the
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

    // One page exists at a time: the one being shown.
    //
    // Handing pageStack a URL or a Component makes it create the page with no
    // visual parent, which the engine reports as an object "not placed in the
    // graphics scene" -- invisible unless something is listening to
    // QQmlEngine::warnings, but there all the same. Creating it here, parented
    // and synchronously, is quiet.
    //
    // **They used to be kept, and that was the expensive mistake.** The cache
    // below never dropped anything and nothing called `pop`, `clear` or
    // `destroy`; Kirigami does not destroy a replaced page either, because
    // `ColumnView::replaceItem` gates its `deleteLater` on
    // `shouldDeleteOnRemove`, which is false once an item has a visual parent
    // -- and `createObject(pageStack)` gives it one. So every section visited
    // stayed alive for the session, and a live page's bindings re-evaluate
    // whether or not anyone can see them. Being hidden is not being idle. With
    // seven profile pages open, one `dirtyChanged` ran the footer's bindings
    // seven times over for six footers nobody was looking at; the review
    // measured the fan-out of a single stick-slider step going from 67 Python
    // calls on a cold window to 122 on a warm one.
    //
    // What this costs is the scroll position of the section you leave, and
    // rebuilding a page on each visit. That is paid once per section change,
    // which is a thing a person does occasionally, against a cost that was
    // being paid on every notification, which is a thing the pad does
    // constantly.
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

    // Drop a page the window has navigated away from.
    //
    // `destroy` takes a delay because the page is still on screen when this
    // runs: `replace` animates the outgoing page away, and deleting an item
    // mid-transition is how a window crashes rather than how it saves work.
    // A second is far longer than the transition and costs nothing, since
    // nothing is bound to the page any more by then.
    function releaseSection(index) {
        const page = pageCache[index]
        if (!page)
            return
        delete pageCache[index]
        page.destroy(1000)
    }

    function openSection(index) {
        if (index === currentSection)
            return
        const page = pageFor(index)
        if (!page)
            return
        const leaving = currentSection
        currentSection = index
        // replace() on an empty stack has nothing to replace and drops the
        // page on the floor -- it still gets created, just never shown.
        if (pageStack.depth === 0)
            pageStack.push(page)
        else
            pageStack.replace(page)
        if (leaving >= 0)
            releaseSection(leaving)
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

        // One action per entry in `sections`, built from the list rather than
        // written out fifteen times over.
        //
        // **They were copy-pasted, and nothing kept them in step.** Adding the
        // Screen page without adding its action shifted every label after it by
        // one and pushed Setup off the end entirely, where it stayed
        // unreachable from the sidebar until a screenshot showed it missing.
        // `test_the_drawer_offers_every_section` was written to catch that;
        // generated from the same list the pages come from, it cannot happen,
        // and the test now asserts something true by construction rather than
        // by vigilance. It is kept because it also asserts the *order*, which
        // is still a choice this file makes.
        //
        // Built in `Component.onCompleted` rather than by a Repeater because
        // `actions` is a list property of Action objects, and a Repeater fills
        // a visual parent instead. The Action is not an Item, so the drawer is
        // a perfectly good owner for it.
        Component {
            id: sectionAction

            Kirigami.Action {
                required property int section

                objectName: "section" + root.sections[section].name
                text: root.sections[section].name
                icon.name: root.sections[section].icon
                visible: root.sectionVisible(section)
                checkable: true
                checked: root.currentSection === section
                onTriggered: root.openSection(section)
            }
        }

        Component.onCompleted: {
            const built = []
            for (let i = 0; i < root.sections.length; ++i)
                built.push(sectionAction.createObject(this, {section: i}))
            actions = built
        }

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

    // DS mode's state comes from reading the process table, which is real work
    // on the GUI thread -- 4.6 to 11.3 ms a call, measured -- so it runs only
    // while a section that shows it is open.
    //
    // **Here rather than on the pages, because a page cannot own this.** Both
    // pages that read DS mode used to arm the poll from `Component.onCompleted`
    // with a comment saying it ran "while a page that cares is up". It did not:
    // Kirigami keeps a replaced page alive, so `Component.onCompleted` means
    // once and forever, and a single visit left /proc being scanned every two
    // seconds from every other page for the rest of the session. Two pages each
    // binding the same property would not fix it either -- both bindings would
    // be live at once, since both pages stay alive, and the later one would
    // simply win. The window is the one thing that knows which section is
    // actually open.
    Binding {
        target: App.dsmode
        property: "polling"
        value: root.currentSection === root.indexOfSection("Controller")
               || root.currentSection === root.indexOfSection("DualSense")
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
