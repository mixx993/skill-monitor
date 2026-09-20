import AppKit
import SwiftUI

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var panel: NSPanel?
    private let store = Store()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        let screen = NSScreen.main ?? NSScreen.screens[0]
        let width: CGFloat = 560
        let height: CGFloat = 400

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: width, height: height),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isMovable = false
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.acceptsMouseMovedEvents = true
        // Above the menu bar, so the island can sit in the notch strip.
        panel.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]

        // On a notched Mac, hang the island just below the notch; otherwise
        // tuck it into the empty middle of the menu bar.
        let notch = screen.safeAreaInsets.top
        let topInset: CGFloat = notch > 0 ? notch + 2 : 5

        let hosting = NSHostingView(rootView: IslandView(store: store, topInset: topInset))
        hosting.layer?.backgroundColor = NSColor.clear.cgColor
        panel.contentView = hosting

        panel.setFrame(
            NSRect(x: screen.frame.midX - width / 2,
                   y: screen.frame.maxY - height,
                   width: width,
                   height: height),
            display: false
        )
        panel.orderFrontRegardless()

        self.panel = panel
        store.start()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
