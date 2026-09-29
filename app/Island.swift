import AppKit
import SwiftUI

/// Shared display preferences. The old Claude-only configuration still works.
enum MonitorConfig {
    static let home = FileManager.default.homeDirectoryForCurrentUser
    static let url = home.appendingPathComponent(".config/skill-monitor/config.json")
    static let legacyURL = home.appendingPathComponent(".claude/skill-monitor/config.json")

    static func read(_ url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return value
    }

    static var stateURLs: [URL] {
        let defaults = [home.appendingPathComponent(".claude/skill-monitor/state.json").path,
                        home.appendingPathComponent(".codex/skill-monitor/state.json").path]
        let paths = read(url)["stateFiles"] as? [String] ?? defaults
        return Array(Set(paths)).map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
    }

    static var hostBundleIDs: Set<String> {
        let settings = read(url)
        let legacy = read(legacyURL)
        let ids = settings["showWhenFrontmost"] as? [String]
            ?? legacy["showWhenFrontmost"] as? [String]
            ?? ["com.anthropic.claudefordesktop"]
        return Set(ids)
    }
}

// MARK: - Model

struct Call: Decodable, Identifiable, Equatable {
    let kind: String
    let server: String?
    let name: String
    let count: Int
    let time: String
    /// "auto" = the model reached for it; "user" = you typed /name.
    /// nil on records written before origin tracking existed.
    let origin: String?
    /// Wall time of the call in milliseconds, when known.
    let ms: Int?
    var path: String? = nil
    var evidence: String? = nil

    /// Only skills carry this signal — MCP tools are always model-chosen.
    var isAutoSkill: Bool { isSkill && origin == "auto" }

    var duration: String? {
        guard let ms = ms else { return nil }
        if ms < 1000 { return "\(ms)ms" }
        return String(format: "%.1fs", Double(ms) / 1000)
    }

    var id: String { "\(kind)|\(server ?? "-")|\(name)|\(path ?? "")" }
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

/// An instruction file in effect for the session: CLAUDE.md, memory, settings.
struct Instruction: Decodable, Identifiable, Equatable {
    let kind: String        // claude_md | memory | settings
    let scope: String?      // User | Project | Local | AutoMem
    let path: String
    let hash: String?
    let reason: String?     // scan | session_start | nested_traversal | …
    let count: Int?         // memory entries

    var id: String { path }

    var tag: String {
        if reason == "candidate" { return "候选文件" }
        switch kind {
        case "memory": return "记忆"
        case "settings":
            switch scope {
            case "Project": return "项目设置"
            case "Local": return "本地设置"
            default: return "用户设置"
            }
        default:
            if reason == "nested_traversal" { return "子目录" }
            if reason == "include" { return "引用" }
            switch scope {
            case "User": return "全局"
            case "Local": return "本地"
            default: return "项目"
            }
        }
    }

    var displayPath: String {
        // The memory path is long and says nothing the tag doesn't; show the count instead.
        if kind == "memory" {
            return "MEMORY.md" + (count.map { " · \($0) 条" } ?? "")
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path.hasPrefix(home) ? "~" + path.dropFirst(home.count) : path
    }
}

struct TurnState: Decodable, Equatable {
    var prompt: String? = nil
    var status: String? = nil
    var started: String? = nil
    var updated: String? = nil
    var cwd: String? = nil
    var calls: [Call]? = nil
    var instructions: [Instruction]? = nil
    var hooks: Int? = nil
    var source: String? = nil
    var session: String? = nil
    var turn_id: String? = nil
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

    private let urls = MonitorConfig.stateURLs
    @Published private(set) var selectedSource = "all"
    private(set) var activeURL: URL?
    private var poll: Timer?
    private var flashTimer: Timer?
    private var lastStamp: Date?
    private var hovering = false
    private var seeded = false

    init() {}

    /// Offscreen rendering only (tools/preview.swift).
    init(preview: TurnState, mode: PillMode, flash: Call?, showInstructions: Bool = false) {
        self.state = preview
        self.mode = mode
        self.flashCall = flash
        self.showInstructions = showInstructions
    }

    /// Whether the instruction-file list under the calls is open. Sticks
    /// across hovers, like a preference.
    @Published private(set) var showInstructions = false

    var calls: [Call] { state.calls ?? [] }
    var instructions: [Instruction] { state.instructions ?? [] }
    var sourceLabel: String {
        if state.source == "codex" || (activeURL == nil && selectedSource == "codex") { return "Codex" }
        return activeURL == nil && state.prompt == nil ? "等待事件" : "Claude"
    }
    var dataDirectory: URL {
        activeURL?.deletingLastPathComponent() ?? MonitorConfig.url.deletingLastPathComponent()
    }

    func selectSource(_ source: String) {
        selectedSource = source
        activeURL = nil
        lastStamp = nil
        seeded = false
        reload(force: true)
    }

    /// "CLAUDE.md 2 · 记忆 20 · 设置 1 · hook 5"
    var instructionSummary: String {
        let docs = instructions.filter { $0.kind == "claude_md" }.count
        let agents = instructions.filter { $0.kind == "agents_md" }.count
        let memory = instructions.filter { $0.kind == "memory" }.compactMap { $0.count }.reduce(0, +)
        let settings = instructions.filter { $0.kind == "settings" }.count
        var parts: [String] = []
        if docs > 0 { parts.append("CLAUDE.md \(docs)") }
        if agents > 0 { parts.append("AGENTS 候选 \(agents)") }
        if memory > 0 { parts.append("记忆 \(memory)") }
        if settings > 0 { parts.append("设置 \(settings)") }
        if let hooks = state.hooks, hooks > 0 { parts.append("hook \(hooks)") }
        return parts.isEmpty ? "无指令文件" : parts.joined(separator: " · ")
    }

    func toggleInstructions() {
        withAnimation(.spring(response: 0.34, dampingFraction: 0.84)) {
            showInstructions.toggle()
        }
    }
    var isRunning: Bool { state.status == "running" }
    /// How many skills the model reached for without being asked.
    var autoSkillCount: Int { calls.filter { $0.isAutoSkill }.count }

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
        let candidates: [(URL, Date, TurnState)] = urls.compactMap { url in
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
                  let stamp = attrs[.modificationDate] as? Date,
                  let data = try? Data(contentsOf: url),
                  let next = try? JSONDecoder().decode(TurnState.self, from: data) else { return nil }
            let source = next.source ?? "claude"
            guard selectedSource == "all" || selectedSource == source else { return nil }
            return (url, stamp, next)
        }
        guard let (url, stamp, next) = candidates.max(by: { $0.1 < $1.1 }) else {
            state = TurnState()
            activeURL = nil
            lastStamp = nil
            flashTimer?.invalidate()
            flashCall = nil
            if !hovering { mode = .collapsed }
            return
        }
        if !force, activeURL == url, lastStamp == stamp { return }
        let sameTurn = activeURL == url && state.session == next.session && state.turn_id == next.turn_id
        let fresh = newestCall(before: sameTurn ? state.calls ?? [] : [], after: next.calls ?? [])
        activeURL = url
        lastStamp = stamp
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
private let autoColor = Color(red: 0.98, green: 0.72, blue: 0.25)

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
        HStack(spacing: 6) {
            Circle()
                .fill(call.isAutoSkill ? autoColor : Color.clear)
                .frame(width: 3.5, height: 3.5)
            Glyph(call: call)
            call.label
                .font(.system(size: 11.5))
                .lineLimit(1)
                .truncationMode(.middle)
            if call.isSkill && call.origin == "unknown" {
                Text("?")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(.white.opacity(0.45))
                    .help("已观察到技能文件读取请求；无法确定是否由用户指定，也不代表技能已成功执行")
            }
            if call.evidence == "explicit_request" {
                Text("指定")
                    .font(.system(size: 8))
                    .foregroundColor(.white.opacity(0.45))
                    .help("用户以 $名称 引用了本地技能，尚未观察到读取请求")
            } else if call.evidence == "read_attempt" {
                Text("读请求")
                    .font(.system(size: 8))
                    .foregroundColor(.white.opacity(0.45))
            }
            Spacer(minLength: 4)
            if call.count > 1 {
                Text("×\(call.count)")
                    .font(.system(size: 9.5, weight: .semibold, design: .rounded))
                    .foregroundColor(.white.opacity(0.45))
            }
            Text(call.duration ?? call.time)
                .font(.system(size: 9, design: .monospaced))
                .foregroundColor(.white.opacity(0.32))
                .frame(width: 38, alignment: .trailing)
        }
        .padding(.vertical, 2.5)
    }
}

struct InstructionRow: View {
    let item: Instruction

    var body: some View {
        HStack(spacing: 6) {
            Text(item.tag)
                .font(.system(size: 8.5, weight: .medium))
                .foregroundColor(.white.opacity(0.55))
                .frame(width: 44, alignment: .leading)
            Text(item.displayPath)
                .font(.system(size: 10))
                .foregroundColor(.white.opacity(0.72))
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer(minLength: 4)
            if let hash = item.hash {
                Text(hash)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.white.opacity(0.3))
            }
        }
        .padding(.vertical, 1.5)
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
                    NSWorkspace.shared.open(store.dataDirectory)
                }
                Button("全部来源（最近活动）") { store.selectSource("all") }
                Button("只看 Claude") { store.selectSource("claude") }
                Button("只看 Codex") { store.selectSource("codex") }
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
            if store.autoSkillCount > 0 {
                HStack(spacing: 1.5) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 7, weight: .bold))
                    Text("\(store.autoSkillCount)")
                        .font(.system(size: 10, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                }
                .foregroundColor(autoColor)
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

    // What steers every turn without ever being a tool call.
    private var instructionSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(height: 0.5)
                .padding(.top, 7)
                .padding(.bottom, 6)
            Button(action: { store.toggleInstructions() }) {
                HStack(spacing: 6) {
                    Image(systemName: "doc.text")
                        .font(.system(size: 8.5))
                        .foregroundColor(.white.opacity(0.4))
                    Text(store.instructionSummary)
                        .font(.system(size: 10))
                        .foregroundColor(.white.opacity(0.5))
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    Image(systemName: store.showInstructions ? "chevron.up" : "chevron.down")
                        .font(.system(size: 7, weight: .bold))
                        .foregroundColor(.white.opacity(0.3))
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if store.showInstructions {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(store.instructions) { item in
                        InstructionRow(item: item)
                    }
                }
                .padding(.top, 5)
                .transition(.opacity)
            }
        }
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
                Text("\(store.sourceLabel) · 本次任务")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.white)
                if let project = store.project {
                    Text(project)
                        .font(.system(size: 9))
                        .foregroundColor(.white.opacity(0.35))
                        .lineLimit(1)
                }
                Spacer(minLength: 6)
                if store.autoSkillCount > 0 {
                    HStack(spacing: 2) {
                        Image(systemName: "bolt.fill").font(.system(size: 7, weight: .bold))
                        Text("\(store.autoSkillCount) 自动")
                            .font(.system(size: 9, weight: .medium))
                    }
                    .foregroundColor(autoColor)
                }
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
            if !store.instructions.isEmpty {
                instructionSection
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(width: 292, alignment: .leading)
    }
}
