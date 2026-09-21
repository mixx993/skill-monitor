// Offscreen renderer: draws the island's three states over a mock notched
// desktop and writes PNGs, so the design can be reviewed without a screenshot.
import AppKit
import SwiftUI

let demoCalls: [Call] = [
    Call(kind: "skill", server: nil, name: "hyperframes", count: 1, time: "04:52:30",
         origin: "user", ms: 120),
    Call(kind: "skill", server: nil, name: "hyperframes-core", count: 1, time: "04:52:58",
         origin: "auto", ms: 95),
    Call(kind: "skill", server: nil, name: "mixx-video-craft", count: 1, time: "04:53:04",
         origin: "auto", ms: 88),
    Call(kind: "mcp", server: "blender", name: "get_scene_info", count: 1, time: "04:53:10",
         origin: "auto", ms: 340),
    Call(kind: "mcp", server: "docs", name: "guide", count: 3, time: "04:53:44",
         origin: "auto", ms: 2180),
    Call(kind: "mcp", server: "Claude_Browser", name: "tabs_context", count: 1, time: "04:54:02",
         origin: "auto", ms: 610),
]

let demoState = TurnState(
    prompt: "/hyperframes 做个 15 秒的产品短片",
    status: "running",
    started: "04:52:30",
    updated: "04:54:02",
    cwd: "/Users/mixx/code/skill-monitor",
    calls: demoCalls
)

struct Mock: View {
    let store: Store
    let caption: String

    var body: some View {
        ZStack(alignment: .top) {
            LinearGradient(
                colors: [Color(red: 0.16, green: 0.18, blue: 0.26),
                         Color(red: 0.29, green: 0.24, blue: 0.33)],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )

            // menu bar text, so the island can be judged against real chrome
            HStack(spacing: 14) {
                Image(systemName: "applelogo").font(.system(size: 11))
                Text("Claude").font(.system(size: 11, weight: .semibold))
                Text("File").font(.system(size: 11))
                Text("Edit").font(.system(size: 11))
                Spacer()
                Text("100%").font(.system(size: 11))
                Image(systemName: "battery.100").font(.system(size: 11))
                Image(systemName: "wifi").font(.system(size: 11))
                Text("周日 4:55").font(.system(size: 11))
            }
            .foregroundColor(.white.opacity(0.85))
            .padding(.horizontal, 14)
            .frame(height: 33)

            // the notch
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .fill(Color.black)
                .frame(width: 190, height: 44)
                .offset(y: -11)

            IslandView(store: store, topInset: 34)

            VStack {
                Spacer()
                Text(caption)
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundColor(.white.opacity(0.5))
                    .padding(.bottom, 8)
            }
        }
        .frame(width: 700, height: 330)
    }
}

func render<V: View>(_ view: V, width: CGFloat, height: CGFloat, to path: String) {
    let hosting = NSHostingView(rootView: view)
    hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)
    hosting.layoutSubtreeIfNeeded()
    guard let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else {
        print("failed rep for \(path)"); return
    }
    hosting.cacheDisplay(in: hosting.bounds, to: rep)
    guard let data = rep.representation(using: .png, properties: [:]) else {
        print("failed png for \(path)"); return
    }
    try? data.write(to: URL(fileURLWithPath: path))
    print("wrote \(path)")
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."

render(Mock(store: Store(preview: demoState, mode: .collapsed, flash: nil),
            caption: "collapsed — 5 次调用，其中 2 个 skill 是模型自己决定的"),
       width: 700, height: 330, to: "\(out)/island-collapsed.png")

render(Mock(store: Store(preview: demoState, mode: .flash, flash: demoCalls[2]),
            caption: "flash — 模型自己触发了 mixx-video-craft"),
       width: 700, height: 330, to: "\(out)/island-flash.png")

render(Mock(store: Store(preview: demoState, mode: .expanded, flash: nil),
            caption: "expanded — 琥珀点 = 你没要求，模型自己调的"),
       width: 700, height: 330, to: "\(out)/island-expanded.png")
