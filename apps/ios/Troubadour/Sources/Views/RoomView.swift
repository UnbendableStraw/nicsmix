import SwiftUI
import UIKit
import WebKit
import WidgetKit

/// One room, three tabs: **Room** (the sound field drawn natively, with playback under it), **Speakers**, and
/// **Setup** — all native, with the cell's own page one tap away for anything not surfaced here.
struct RoomView: View {
    let room: Room
    @State private var tab = 0
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        TabView(selection: $tab) {
            RoomMainView(room: room).tabItem { Label("Room", systemImage: "circle.dotted") }.tag(0)
            SpeakersView(room: room).tabItem { Label("Speakers", systemImage: "hifispeaker.2") }.tag(2)
            SetupView(room: room).tabItem { Label("Setup", systemImage: "slider.horizontal.3") }.tag(3)
        }
        .navigationTitle(room.name)
        .navigationBarTitleDisplayMode(.inline)
        // On the Room tab the system back button is swapped for ours: without the system one iOS starts no
        // back-swipe at all, so dragging the puck can never pop to Rooms. The other tabs keep the stock one.
        .navigationBarBackButtonHidden(tab == 0)
        .toolbar {
            if tab == 0 {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button { dismiss() } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "chevron.left").font(.body.weight(.semibold))
                            Text("Rooms")
                        }
                    }
                    .tint(.white)
                }
            }
        }
        .onAppear {
            // the widgets and their buttons act on the room last opened here
            Shared.agentIP = room.agent?.ip; Shared.roomName = room.name
            WidgetCenter.shared.reloadAllTimelines()
        }
        .toolbarBackground(Theme.bg, for: .navigationBar, .tabBar)
        .toolbarBackground(.visible, for: .navigationBar, .tabBar)
    }
}

// MARK: - playback

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

// MARK: - the room page: field on top, what is playing under it

struct RoomMainView: View {
    let room: Room
    @StateObject private var f: FieldModel
    @StateObject private var m: RoomModel
    @State private var sheet: Sheet?

    enum Sheet: String, Identifiable { case mode, scenes; var id: String { rawValue } }

    init(room: Room) {
        self.room = room
        _f = StateObject(wrappedValue: FieldModel(agent: room.agent))
        _m = StateObject(wrappedValue: RoomModel(agent: room.agent))
    }

    var body: some View {
        VStack(spacing: 0) {
            FieldCanvas(model: f)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped()
            footer
        }
        .background(Theme.bg.ignoresSafeArea())
        .background(DisablesSwipeBack())
        .onAppear { f.start(); m.start() }
        .onDisappear { f.stop(); m.stop() }
        .sheet(item: $sheet) { which in
            switch which {
            case .mode: SoundModeSheet(m: m)
            case .scenes: ScenesSheet(m: m)
            }
        }
    }

    private var nowPlaying: some View {
        HStack(spacing: 12) {
            Artwork(agent: m.agent, source: m.state.active, size: 46)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold)).lineLimit(1)
                HStack(spacing: 5) {
                    Circle().fill(f.live ? Theme.live : Theme.muted.opacity(0.4)).frame(width: 6, height: 6)
                    Text(subtitle).font(.caption).foregroundStyle(Theme.muted).lineLimit(1)
                }
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 1) {
                Text("\(f.volPct)").font(.subheadline.weight(.semibold).monospacedDigit())
                Text(f.spatialOn ? "\(f.widthDeg)° · \(f.aimDeg)°" : "all around")
                    .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
            }
        }
    }

    private var title: String {
        if let a = m.state.active, !a.title.isEmpty { return a.title }
        return m.state.active?.displayName ?? "Nothing playing"
    }
    private var subtitle: String {
        let bits = [m.state.active?.artist, m.state.active?.album].compactMap { $0 }.filter { !$0.isEmpty }
        if !bits.isEmpty { return bits.joined(separator: " · ") }
        return m.state.active.map { $0.displayName } ?? "drag the puck to move the sound"
    }

    private var footer: some View {
        VStack(spacing: 10) {
            nowPlaying
            HStack(spacing: 8) {
                Button { m.transport("previous") } label: { Image(systemName: "backward.fill") }.buttonStyle(Capsule_())
                Button { m.transport(m.state.active?.playing == true ? "pause" : "play") } label: {
                    Image(systemName: m.state.active?.playing == true ? "pause.fill" : "play.fill")
                }.buttonStyle(Capsule_(filled: true))
                Button { m.transport("next") } label: { Image(systemName: "forward.fill") }.buttonStyle(Capsule_())
            }
            .disabled(m.state.active == nil)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    Button("Centre") { f.centrePuck() }.buttonStyle(Chip())
                    Button(f.spatialOn ? "All around" : "Spatial") { f.setSpatial(!f.spatialOn) }
                        .buttonStyle(Chip(filled: f.spatialOn))
                    Button(m.state.soundMode == "direct" ? "Direct" : "Syng") { sheet = .mode }.buttonStyle(Chip())
                    if !m.scenes.isEmpty { Button("Scenes") { sheet = .scenes }.buttonStyle(Chip()) }
                    Button(m.state.active?.mute == true ? "Muted" : "Mute") { m.mute() }
                        .buttonStyle(Chip(filled: m.state.active?.mute == true))
                    ForEach(m.state.sources) { s in
                        Button(s.displayName) { m.select(s.id) }.buttonStyle(Chip(filled: s.active))
                    }
                }
                .padding(.horizontal, 16)
            }
            .padding(.horizontal, -16)
            if let e = f.error ?? m.error {
                Text(e).font(.caption2).foregroundStyle(Theme.bad).lineLimit(1)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 4)
    }
}

/// A capsule that hugs its label — the footer row scrolls, so these are not full width.
struct Chip: ButtonStyle {
    var filled = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.footnote.weight(.semibold))
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(filled ? Color.white : Theme.surface2, in: Capsule())
            .foregroundStyle(filled ? Color.black : Color.white)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

struct SoundModeSheet: View {
    @ObservedObject var m: RoomModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    Card("Sound mode") {
                        HStack {
                            Button("Syng") { m.soundMode("syng") }.buttonStyle(Capsule_(filled: m.state.soundMode == "syng"))
                            Button("Direct") { m.soundMode("direct") }.buttonStyle(Capsule_(filled: m.state.soundMode == "direct"))
                        }
                        Text("Syng steers the sound across the speakers and their drivers. Direct: every speaker plays the whole mix through all three drivers at the same level.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    Card("Volume") {
                        RelativeSlider(value: m.volume) { v, final in m.setVolume(v, final: final) }
                        Text("Drag anywhere on the bar: the volume moves with your finger from where it is, and a tap does nothing. The rim at the bottom of the field works the same way.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Sound")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium])
    }
}

struct ScenesSheet: View {
    @ObservedObject var m: RoomModel
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                Card("Scenes") {
                    FlowRow(items: m.scenes) { n in
                        Button(n) { m.applyScene(n); dismiss() }.buttonStyle(Capsule_(filled: m.lastScene == n))
                    }
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Scenes")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.medium])
    }
}

struct Artwork: View {
    let agent: Agent?; let source: Source?
    var size: CGFloat = 72
    var body: some View {
        Group {
            if let a = agent, let s = source, s.hasArtwork {
                AsyncImage(url: a.base.appendingPathComponent("/api/artwork").appending(queryItems: [.init(name: "source", value: s.id), .init(name: "k", value: s.title)])) { img in img.resizable().scaledToFill() } placeholder: { Theme.surface2 }
            } else {
                ZStack { Theme.surface2; Image(systemName: "music.note").foregroundStyle(Theme.muted) }
            }
        }
        .frame(width: size, height: size).clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
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
