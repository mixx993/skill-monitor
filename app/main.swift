import AppKit
import SwiftUI

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var panel: NSPanel?
    private let store = Store()

    /// The island only shows while one of these apps is frontmost.
    /// Editable at ~/.claude/skill-monitor/config.json — add a terminal's
    /// bundle id (com.apple.Terminal, com.googlecode.iterm2, …) to see it
    /// while running the CLI there.
    private var hostBundleIDs: Set<String> = ["com.anthropic.claudefordesktop"]

    private var configURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude/skill-monitor/config.json")
    }

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
        self.panel = panel

        loadConfig()
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(frontmostAppChanged),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )
        updateVisibility(animated: false)

        store.start()
    }

    private func loadConfig() {
        if let data = try? Data(contentsOf: configURL),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let ids = obj["showWhenFrontmost"] as? [String], !ids.isEmpty {
            hostBundleIDs = Set(ids)
            return
        }
        // Write the default out so the setting is discoverable.
        let defaults = ["showWhenFrontmost": Array(hostBundleIDs).sorted()]
        try? FileManager.default.createDirectory(
            at: configURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(
            withJSONObject: defaults, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: configURL)
        }
    }

    @objc private func frontmostAppChanged() {
        updateVisibility(animated: true)
    }

    private func updateVisibility(animated: Bool) {
        guard let panel = panel else { return }
        let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
        let show = hostBundleIDs.contains(front)
        let duration = animated ? 0.16 : 0.0

        if show {
            if !panel.isVisible {
                panel.alphaValue = 0
                panel.orderFrontRegardless()
            }
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = duration
                panel.animator().alphaValue = 1
            }
        } else {
            // Don't come back mid-hover.
            store.setHover(false)
            NSAnimationContext.runAnimationGroup({ ctx in
                ctx.duration = duration
                panel.animator().alphaValue = 0
            }, completionHandler: { [weak panel] in
                // A re-show may have landed while this was fading out.
                if let panel = panel, panel.alphaValue == 0 { panel.orderOut(nil) }
            })
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
