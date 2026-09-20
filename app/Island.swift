import AppKit
import SwiftUI

// MARK: - Model

struct Call: Decodable, Identifiable, Equatable {
    let kind: String
    let server: String?
    let name: String
    let count: Int
    let time: String

    var id: String { "\(kind)|\(server ?? "-")|\(name)" }
    var isSkill: Bool { kind == "skill" }

    /// Keeps the island from stretching across the screen on long MCP tool names.
    var shortName: String {
        name.count > 32 ? String(name.prefix(31)) + "…" : name
    }

    /// MCP connector ids are sometimes raw UUIDs — keep the prefix legible.
    var shortServer: String? {
        guard let server = server, !server.isEmpty else { return nil }
        return server.count > 14 ? String(server.prefix(8)) + "…" : server
    }

    /// "server / tool" for MCP, bare skill name otherwise.
    var label: Text {
        let name = Text(shortName).foregroundColor(.white.opacity(0.92))
        guard let server = shortServer else { return name }
        return Text(server + " / ").foregroundColor(.white.opacity(0.3)) + name
    }
}

struct TurnState: Decodable, Equatable {
    var prompt: String? = nil
    var status: String? = nil
    var started: String? = nil
    var updated: String? = nil
    var cwd: String? = nil
    var calls: [Call]? = nil
}

enum PillMode: Equatable {
    case collapsed   // idle: just a dot and a count
    case flash       // a call just landed: show its name for a beat
    case expanded    // pointer is over the island: full list
}

// MARK: - Store

final class Store: ObservableObject {
    @Published private(set) var state = TurnState()
    @Published private(set) var mode: PillMode = .collapsed
    @Published private(set) var flashCall: Call?

    private let url = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".claude/skill-monitor/state.json")
    private var poll: Timer?
    private var flashTimer: Timer?
    private var lastStamp: Date?
    private var hovering = false
    private var seeded = false

    init() {}

    /// Offscreen rendering only (tools/preview.swift).
    init(preview: TurnState, mode: PillMode, flash: Call?) {
        self.state = preview
        self.mode = mode
        self.flashCall = flash
    }

    var calls: [Call] { state.calls ?? [] }
    var isRunning: Bool { state.status == "running" }

    var project: String? {
        guard let cwd = state.cwd, !cwd.isEmpty else { return nil }
        return (cwd as NSString).lastPathComponent
    }

    func start() {
        reload(force: true)
        let t = Timer.scheduledTimer(withTimeInterval: 0.3, repeats: true) { [weak self] _ in
            self?.reload(force: false)
        }
        RunLoop.main.add(t, forMode: .common)
        poll = t
    }

    func setHover(_ value: Bool) {
        guard hovering != value else { return }
        hovering = value
        flashTimer?.invalidate()
        withAnimation(.spring(response: 0.36, dampingFraction: 0.82)) {
            mode = value ? .expanded : .collapsed
        }
    }

    private func reload(force: Bool) {
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        guard let stamp = attrs?[.modificationDate] as? Date else { return }
        if !force, let last = lastStamp, last == stamp { return }
        lastStamp = stamp

        guard let data = try? Data(contentsOf: url),
              let next = try? JSONDecoder().decode(TurnState.self, from: data) else { return }

        let fresh = newestCall(before: state.calls ?? [], after: next.calls ?? [])
        withAnimation(.spring(response: 0.36, dampingFraction: 0.82)) {
            state = next
        }

        // Don't flash the whole backlog on launch — only live arrivals.
        guard seeded else { seeded = true; return }
        if let fresh = fresh, !hovering {
            flash(fresh)
        }
    }

    /// The call that was added, or whose count went up, between two snapshots.
    private func newestCall(before: [Call], after: [Call]) -> Call? {
        let old = Dictionary(uniqueKeysWithValues: before.map { ($0.id, $0.count) })
        for call in after.reversed() where call.count > (old[call.id] ?? 0) {
            return call
        }
        return nil
    }

    private func flash(_ call: Call) {
        flashTimer?.invalidate()
        flashCall = call
        withAnimation(.spring(response: 0.38, dampingFraction: 0.78)) {
            mode = .flash
        }
        let t = Timer.scheduledTimer(withTimeInterval: 2.2, repeats: false) { [weak self] _ in
            guard let self = self, !self.hovering else { return }
            withAnimation(.spring(response: 0.42, dampingFraction: 0.85)) {
                self.mode = .collapsed
            }
        }
        RunLoop.main.add(t, forMode: .common)
        flashTimer = t
    }
}

// MARK: - Hover tracking
// SwiftUI's .onHover only fires for the active app; this island never activates,
// so it needs a tracking area registered as .activeAlways.

struct HoverTracker: NSViewRepresentable {
    let onChange: (Bool) -> Void

    func makeNSView(context: Context) -> NSView { TrackingView(onChange: onChange) }
    func updateNSView(_ nsView: NSView, context: Context) {}

    final class TrackingView: NSView {
        private let onChange: (Bool) -> Void

        init(onChange: @escaping (Bool) -> Void) {
            self.onChange = onChange
            super.init(frame: .zero)
        }

        required init?(coder: NSCoder) { fatalError() }

        override func updateTrackingAreas() {
            super.updateTrackingAreas()
            trackingAreas.forEach(removeTrackingArea)
            addTrackingArea(NSTrackingArea(
                rect: bounds,
                options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect],
                owner: self,
                userInfo: nil
            ))
        }

        override func mouseEntered(with event: NSEvent) { onChange(true) }
        override func mouseExited(with event: NSEvent) { onChange(false) }
    }
}

// MARK: - Pieces

private let skillColor = Color(red: 0.64, green: 0.51, blue: 0.99)
private let mcpColor = Color(red: 0.30, green: 0.80, blue: 0.76)
private let liveColor = Color(red: 0.32, green: 0.86, blue: 0.47)

struct Glyph: View {
    let call: Call

    var body: some View {
        Image(systemName: call.isSkill ? "diamond.fill" : "diamond")
            .font(.system(size: 7.5, weight: .semibold))
            .foregroundColor(call.isSkill ? skillColor : mcpColor)
            .frame(width: 9)
    }
}

struct CallRow: View {
    let call: Call

    var body: some View {
        HStack(spacing: 7) {
            Glyph(call: call)
            call.label
                .font(.system(size: 11.5))
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer(minLength: 4)
            if call.count > 1 {
                Text("×\(call.count)")
                    .font(.system(size: 9.5, weight: .semibold, design: .rounded))
                    .foregroundColor(.white.opacity(0.45))
            }
            Text(call.time)
                .font(.system(size: 9, design: .monospaced))
                .foregroundColor(.white.opacity(0.3))
        }
        .padding(.vertical, 2.5)
    }
}

// MARK: - Island

struct IslandView: View {
    @ObservedObject var store: Store
    let topInset: CGFloat

    private var radius: CGFloat {
        switch store.mode {
        case .collapsed: return 11
        case .flash: return 15
        case .expanded: return 18
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Spacer(minLength: 0)
                island
                Spacer(minLength: 0)
            }
            Spacer(minLength: 0)
        }
        .padding(.top, topInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(Color.clear)
    }

    private var island: some View {
        body(for: store.mode)
            .background(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(Color.black.opacity(0.93))
                    .overlay(
                        RoundedRectangle(cornerRadius: radius, style: .continuous)
                            .strokeBorder(Color.white.opacity(0.07), lineWidth: 0.5)
                    )
                    .shadow(color: .black.opacity(0.32), radius: 10, y: 3)
            )
            .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(HoverTracker { store.setHover($0) })
            .contextMenu {
                Button("打开数据目录") {
                    NSWorkspace.shared.open(
                        FileManager.default.homeDirectoryForCurrentUser
                            .appendingPathComponent(".claude/skill-monitor")
                    )
                }
                Divider()
                Button("退出 SkillMonitor") { NSApp.terminate(nil) }
            }
            .animation(.spring(response: 0.36, dampingFraction: 0.82), value: store.mode)
    }

    @ViewBuilder
    private func body(for mode: PillMode) -> some View {
        switch mode {
        case .collapsed: collapsed
        case .flash: flash
        case .expanded: expanded
        }
    }

    // Idle: a dot, and the number of distinct skills / tools this turn touched.
    private var collapsed: some View {
        HStack(spacing: 5) {
            Circle()
                .fill(store.isRunning ? liveColor : Color.white.opacity(0.28))
                .frame(width: 5.5, height: 5.5)
            if !store.calls.isEmpty {
                Text("\(store.calls.count)")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundColor(.white.opacity(0.88))
                    .monospacedDigit()
            }
        }
        .padding(.horizontal, store.calls.isEmpty ? 8 : 9)
        .frame(height: 21)
    }

    // A call just landed.
    private var flash: some View {
        HStack(spacing: 7) {
            if let call = store.flashCall {
                Glyph(call: call)
                call.label
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(1)
                Text(call.time)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.white.opacity(0.38))
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 28)
    }

    private var callList: some View {
        VStack(alignment: .leading, spacing: 1) {
            ForEach(store.calls) { call in
                CallRow(call: call)
            }
        }
    }

    // Pointer is on the island: the whole turn.
    private var expanded: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Circle()
                    .fill(store.isRunning ? liveColor : Color.white.opacity(0.28))
                    .frame(width: 5.5, height: 5.5)
                Text("本次任务")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.white)
                if let project = store.project {
                    Text(project)
                        .font(.system(size: 9))
                        .foregroundColor(.white.opacity(0.35))
                        .lineLimit(1)
                }
                Spacer(minLength: 6)
                Text("\(store.calls.count)")
                    .font(.system(size: 10, weight: .semibold, design: .rounded))
                    .foregroundColor(.white.opacity(0.45))
                Button(action: { NSApp.terminate(nil) }) {
                    Image(systemName: "xmark")
                        .font(.system(size: 7.5, weight: .bold))
                        .foregroundColor(.white.opacity(0.35))
                }
                .buttonStyle(.plain)
            }

            if let prompt = store.state.prompt, !prompt.isEmpty {
                Text(prompt)
                    .font(.system(size: 9.5))
                    .foregroundColor(.white.opacity(0.32))
                    .lineLimit(1)
                    .padding(.top, 3)
            }

            if store.calls.isEmpty {
                Text("本次任务未调用 skill / MCP")
                    .font(.system(size: 10.5))
                    .foregroundColor(.white.opacity(0.3))
                    .padding(.top, 10)
                    .padding(.bottom, 2)
            } else {
                Rectangle()
                    .fill(Color.white.opacity(0.08))
                    .frame(height: 0.5)
                    .padding(.vertical, 7)
                if store.calls.count > 9 {
                    ScrollView(.vertical, showsIndicators: false) { callList }
                        .frame(height: 200)
                } else {
                    callList
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(width: 292, alignment: .leading)
    }
}

