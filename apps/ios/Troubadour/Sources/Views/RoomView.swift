import SwiftUI
import UIKit
import WebKit
import WidgetKit

/// One room: a native now-playing card (haptic volume, sources, transport, sound mode, scenes) and the agent's own
/// pages for the sound field and setup, embedded — they are already the app's look and the single source of truth.
struct RoomView: View {
    let room: Room
    @State private var tab = 0
    var body: some View {
        TabView(selection: $tab) {
            NowPlayingView(room: room).tabItem { Label("Now Playing", systemImage: "waveform") }.tag(0)
            AgentPage(agent: room.agent, path: "/", anchor: "fieldwrap").tabItem { Label("Sound Field", systemImage: "circle.dotted") }.tag(1)
            SpeakersView(room: room).tabItem { Label("Speakers", systemImage: "hifispeaker.2") }.tag(2)
            AgentPage(agent: room.agent, path: "/setup").tabItem { Label("Setup", systemImage: "slider.horizontal.3") }.tag(3)
        }
        .navigationTitle(room.name)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            // the widgets and their buttons act on the room last opened here
            Shared.agentIP = room.agent?.ip; Shared.roomName = room.name
            WidgetCenter.shared.reloadAllTimelines()
        }
        .toolbarBackground(Theme.bg, for: .navigationBar, .tabBar)
        .toolbarBackground(.visible, for: .navigationBar, .tabBar)
    }
}

// MARK: - now playing

@MainActor
final class RoomModel: ObservableObject {
    let agent: Agent?
    @Published var state = RoomState()
    @Published var scenes: [String] = []
    @Published var lastScene: String?
    @Published var volume: Double = 0
    @Published var error: String?
    private var timer: Timer?
    private var dragging = false
    private var lastSend = Date.distantPast
    init(agent: Agent?) { self.agent = agent }

    func start() { kick(); timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in Task { await self?.tick() } } }
    func stop() { timer?.invalidate() }
    func tick() async {
        guard let a = agent else { return }
        do {
            let d = try await a.get("/api/state")
            state = RoomState(d); if !dragging { volume = state.volume }
            let s = try await a.get("/api/scenes")
            scenes = ((s["scenes"] as? [String: Any]) ?? [:]).keys.sorted(); lastScene = s["last"] as? String
            error = nil
        } catch { self.error = error.localizedDescription }
    }
    private func kick() { Task { await tick() } }

    /// Volume: send while dragging at ~12 Hz, always the final value, light haptic every 5 %.
    func setVolume(_ v: Double, final: Bool) {
        volume = v; dragging = !final
        if final || Date().timeIntervalSince(lastSend) > 0.08 {
            lastSend = Date()
            Task { try? await agent?.post("/api/volume", ["source": "all", "volume": v]) }
        }
    }
    func transport(_ action: String) { Task { if let s = state.active { try? await agent?.post("/api/transport", ["source": s.id, "action": action]); await tick() } } }
    func select(_ id: String) { Task { try? await agent?.post("/api/source", ["id": id, "on": !(state.sources.first { $0.id == id }?.active ?? false)]); await tick() } }
    func mute() { Task { try? await agent?.post("/api/mute", ["on": !(state.active?.mute ?? false)]); await tick() } }
    func soundMode(_ m: String) { Task { try? await agent?.post("/api/soundmode", ["mode": m]); await tick() } }
    func applyScene(_ n: String) { Task { try? await agent?.post("/api/scenes/apply", ["name": n]); await tick() } }
    func ping(_ serial: String?) { Task { try? await agent?.post("/api/ping", serial.map { ["serial": $0] } ?? ["all": true]) } }
}

struct NowPlayingView: View {
    let room: Room
    @StateObject private var m: RoomModel
    @State private var lastHaptic = -1
    init(room: Room) { self.room = room; _m = StateObject(wrappedValue: RoomModel(agent: room.agent)) }

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Card("Playback") {
                    HStack(spacing: 14) {
                        Artwork(agent: m.agent, source: m.state.active)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(m.state.active?.title.isEmpty == false ? m.state.active!.title : (m.state.active?.displayName ?? "Nothing playing")).font(.headline).lineLimit(2)
                            Text([m.state.active?.artist, m.state.active?.album].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")).font(.subheadline).foregroundStyle(Theme.muted).lineLimit(1)
                            if let v = m.state.viaPrimary { Text("via \(v)").font(.caption2).foregroundStyle(Theme.muted) }
                        }
                        Spacer()
                    }
                    HStack(spacing: 10) {
                        Button { m.transport("previous") } label: { Image(systemName: "backward.fill") }.buttonStyle(Capsule_())
                        Button { m.transport(m.state.active?.playing == true ? "pause" : "play") } label: { Image(systemName: m.state.active?.playing == true ? "pause.fill" : "play.fill") }.buttonStyle(Capsule_(filled: true))
                        Button { m.transport("next") } label: { Image(systemName: "forward.fill") }.buttonStyle(Capsule_())
                    }
                    .disabled(m.state.active == nil)
                    HStack {
                        ForEach(m.state.sources) { s in
                            Button(s.displayName) { m.select(s.id) }.buttonStyle(Capsule_(filled: s.active))
                        }
                    }
                    HStack {
                        Text("Volume").font(.subheadline).foregroundStyle(Theme.muted); Spacer()
                        Text("\(Int((m.volume * 100).rounded()))").font(.subheadline.weight(.semibold).monospacedDigit())
                    }
                    Slider(value: Binding(get: { m.volume }, set: { v in
                        m.setVolume(v, final: false)
                        let step = Int(v * 20); if step != lastHaptic { lastHaptic = step; UIImpactFeedbackGenerator(style: .light).impactOccurred(intensity: 0.6) }
                    }), in: 0...1, onEditingChanged: { editing in if !editing { m.setVolume(m.volume, final: true) } })
                    .tint(.white)
                    Button(m.state.active?.mute == true ? "Muted" : "Mute") { m.mute() }.buttonStyle(Capsule_(filled: m.state.active?.mute == true))
                }
                Card("Sound mode") {
                    HStack {
                        Button("Syng") { m.soundMode("syng") }.buttonStyle(Capsule_(filled: m.state.soundMode == "syng"))
                        Button("Direct") { m.soundMode("direct") }.buttonStyle(Capsule_(filled: m.state.soundMode == "direct"))
                    }
                    Text("Syng steers the sound across the speakers and their drivers. Direct: every speaker plays the whole mix through all three drivers at the same level.").font(.caption).foregroundStyle(Theme.muted)
                }
                if !m.scenes.isEmpty {
                    Card("Scenes") {
                        FlowRow(items: m.scenes) { n in Button(n) { m.applyScene(n) }.buttonStyle(Capsule_(filled: m.lastScene == n)) }
                    }
                }
                if let e = m.error { Text(e).font(.caption).foregroundStyle(Theme.bad) }
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .onAppear { m.start() }
        .onDisappear { m.stop() }
    }
}

struct Artwork: View {
    let agent: Agent?; let source: Source?
    var body: some View {
        Group {
            if let a = agent, let s = source, s.hasArtwork {
                AsyncImage(url: a.base.appendingPathComponent("/api/artwork").appending(queryItems: [.init(name: "source", value: s.id), .init(name: "k", value: s.title)])) { img in img.resizable().scaledToFill() } placeholder: { Theme.surface2 }
            } else {
                ZStack { Theme.surface2; Image(systemName: "music.note").foregroundStyle(Theme.muted) }
            }
        }
        .frame(width: 72, height: 72).clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

/// Wrapping row of capsules.
struct FlowRow<Item: Hashable, Content: View>: View {
    let items: [Item]; @ViewBuilder var content: (Item) -> Content
    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 8)], spacing: 8) { ForEach(items, id: \.self) { content($0) } }
    }
}

// MARK: - speakers

struct SpeakersView: View {
    let room: Room
    @StateObject private var m: RoomModel
    @State private var renaming: Cell?
    @State private var newName = ""
    init(room: Room) { self.room = room; _m = StateObject(wrappedValue: RoomModel(agent: room.agent)) }
    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Card("Speakers") {
                    ForEach(room.cells) { c in
                        HStack(spacing: 12) {
                            Puck(size: 40, ring: c.role == "primary" ? .white : nil)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(c.title).font(.subheadline.weight(.semibold))
                                Text("\(c.shortSerial) · \(c.ip) · \(c.role ?? "")\(c.calibrated == false ? " · uncalibrated" : "")").font(.caption).foregroundStyle(Theme.muted)
                            }
                            Spacer()
                            Button { m.ping(c.serial) } label: { Text("Ping") }.buttonStyle(Capsule_()).frame(width: 74)
                        }
                        .contentShape(Rectangle())
                        .onLongPressGesture { renaming = c; newName = c.name ?? "" }
                        .padding(.vertical, 4)
                    }
                    Button("Ping all") { m.ping(nil) }.buttonStyle(Capsule_())
                    Text("Long-press a speaker to name it.").font(.caption).foregroundStyle(Theme.muted)
                }
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .alert("Name this speaker", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Kitchen window", text: $newName)
            Button("Save") { if let c = renaming { Task { try? await m.agent?.post("/api/cell/name", ["serial": c.serial, "name": newName]); await Fleet.shared.refresh() } } }
            Button("Cancel", role: .cancel) {}
        }
    }
}

// MARK: - the agent's pages

struct AgentPage: UIViewRepresentable {
    let agent: Agent?; let path: String; var anchor: String? = nil
    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration(); cfg.allowsInlineMediaPlayback = true
        let w = WKWebView(frame: .zero, configuration: cfg)
        w.isOpaque = false; w.backgroundColor = .black; w.scrollView.backgroundColor = .black
        w.scrollView.contentInsetAdjustmentBehavior = .never
        return w
    }
    func updateUIView(_ w: WKWebView, context: Context) {
        guard let a = agent, w.url == nil else { return }
        var url = a.base.appendingPathComponent(path)
        if let an = anchor { url = URL(string: url.absoluteString + "#" + an)! }
        w.load(URLRequest(url: url))
    }
}
