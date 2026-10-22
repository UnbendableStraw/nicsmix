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
            Shared.remember(room)
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
    private let volSender = LatestSender()
    private var scenesAt = Date.distantPast
    init(agent: Agent?) { self.agent = agent }

    func start() { kick(); timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in Task { await self?.tick() } } }
    func stop() { timer?.invalidate() }
    func tick() async {
        guard let a = agent else { return }
        do {
            let d = try await a.get("/api/state")
            state = RoomState(d)
            // Only take the polled volume once the user has been off the slider for a while: the agent's state
            // is cached ~1 s, so right after a drag it still reports the pre-drag value and the knob snapped back.
            if !dragging && Date().timeIntervalSince(volSender.lastActivity) > 2.5 { volume = state.volume }
            if Date().timeIntervalSince(scenesAt) > 30 {          // the web page asks every 30 s; the list hardly changes
                scenesAt = Date()
                let s = try await a.get("/api/scenes")
                scenes = ((s["scenes"] as? [String: Any]) ?? [:]).keys.sorted(); lastScene = s["last"] as? String
            }
            error = nil
        } catch { self.error = error.localizedDescription }
    }
    private func kick() { Task { await tick() } }

    /// Volume: latest-wins, one request in flight, the final value always lands last (see LatestSender).
    func setVolume(_ v: Double, final: Bool) {
        volume = v; dragging = !final
        let body: [String: Any] = ["source": "all", "volume": (v * 1000).rounded() / 1000]
        volSender.submit(body) { [agent] b in _ = try? await agent?.post("/api/volume", b, timeout: 4) }
    }
    func transport(_ action: String) { Task { if let s = state.active { try? await agent?.post("/api/transport", ["source": s.id, "action": action]); await tick() } } }
    func select(_ id: String) { Task { try? await agent?.post("/api/source", ["id": id, "on": !(state.sources.first { $0.id == id }?.active ?? false)]); await tick() } }
    /// Mute follows the source the controls represent (the active one, else the first), so un-muting works when nothing is active.
    func mute() { Task { try? await agent?.post("/api/mute", ["on": !(state.rep?.mute ?? false)]); await tick() } }
    func sleep(_ minutes: Int) { Task { try? await agent?.post("/api/sleep", ["minutes": minutes]); await tick() } }
    func volumeLimit(_ pct: Int) { Task { try? await agent?.post("/api/settings", ["volume_limit": pct]); await tick() } }
    /// Height processing / beyond-stereo duplication on the sources (`/api/spatial`).
    func spatialFlag(_ key: String, _ on: Bool) { Task { try? await agent?.post("/api/spatial", [key: on]); try? await Task.sleep(nanoseconds: 600_000_000); await tick() } }
    func roomEq(_ on: Bool) { Task { try? await agent?.post("/api/roomeq", ["on": on, "scope": "group"], timeout: 20); await tick() } }
    func refreshScenes() { scenesAt = .distantPast; kick() }
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
    @AppStorage("field.fill") private var fill = false     // the field fills the screen, the footer floats over it

    enum Sheet: String, Identifiable { case mode, scenes; var id: String { rawValue } }

    init(room: Room) {
        self.room = room
        _f = StateObject(wrappedValue: FieldModel(agent: room.agent))
        _m = StateObject(wrappedValue: RoomModel(agent: room.agent))
    }

    var body: some View {
        Group {
            if fill {
                ZStack(alignment: .bottom) {
                    FieldCanvas(model: f)
                        .ignoresSafeArea()
                    footer
                        .background(.ultraThinMaterial)
                }
            } else {
                VStack(spacing: 0) {
                    FieldCanvas(model: f)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .clipped()
                    footer
                }
            }
        }
        .background(Theme.bg.ignoresSafeArea())
        .background(DisablesSwipeBack())
        .onAppear { f.start(); m.start() }
        .onDisappear { f.stop(); m.stop() }
        .onChange(of: sheet) { if $0 == .scenes { m.refreshScenes() } }
        .sheet(item: $sheet) { which in
            switch which {
            case .mode: SoundModeSheet(m: m, f: f)
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
                    FormatBadge(format: m.state.active?.format)
                }
                if let b = mixLine { Text(b).font(.caption2).foregroundStyle(Theme.muted).lineLimit(1) }
                ProgressLine(source: m.state.active)
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 1) {
                Text("\(f.volPct)").font(.subheadline.weight(.semibold).monospacedDigit())
                Text(f.spatialOn ? "\(f.widthDeg)° · \(f.aimDeg)°" : "all around")
                    .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
                if let sl = m.state.sleepLabel { Text("sleep: \(sl)").font(.caption2).foregroundStyle(Theme.warn) }
            }
        }
    }
    /// Syng's own badges plus "via primary X" when a secondary answered (what the web page's `#mixbadge` shows).
    private var mixLine: String? {
        var bits: [String] = []
        if let v = m.state.viaPrimary, !v.isEmpty { bits.append("via primary \(v)") }
        if let b = m.state.active?.mixBadge, !b.isEmpty { bits.append(b) }
        return bits.isEmpty ? nil : bits.joined(separator: " · ")
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
                let a = m.state.active, playing = a?.playing == true
                Button { m.transport("previous") } label: { Image(systemName: "backward.fill") }.buttonStyle(Capsule_())
                    .disabled(a?.can("previous") == false)
                Button { m.transport(playing ? "pause" : "play") } label: {
                    Image(systemName: playing ? "pause.fill" : "play.fill")
                }.buttonStyle(Capsule_(filled: true))
                    .disabled(a?.can(playing ? "pause" : "play") == false)
                Button { m.transport("next") } label: { Image(systemName: "forward.fill") }.buttonStyle(Capsule_())
                    .disabled(a?.can("next") == false)
            }
            .disabled(m.state.active == nil)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    Button("Centre") { f.centrePuck() }.buttonStyle(Chip())
                    Button(f.spatialOn ? "All around" : "Spatial") { f.setSpatial(!f.spatialOn) }
                        .buttonStyle(Chip(filled: f.spatialOn))
                    FieldLookMenu(f: f)
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

/// The field's colour source and physics: the speakers' LEDs (Troubadour's own), the album artwork's colours
/// (what the Syng app shows — ArtworkProcessor), or the source's own palette (`Auras_<source>`); and the app's
/// Aerosol physics vs the previous renderer's. Remembered across launches.
struct FieldLookMenu: View {
    @ObservedObject var f: FieldModel
    @AppStorage("field.colourMode") private var colourMode = "artwork"
    @AppStorage("field.physics") private var physics = "app"
    @AppStorage("field.puckArtwork") private var puckArtwork = true
    @AppStorage("field.idleDrift") private var idleDrift = true
    @AppStorage("field.puff") private var puff = true
    @AppStorage("field.fill") private var fill = false
    @AppStorage("field.halo") private var halo = true

    private var label: String {
        switch f.cfg.colourMode {
        case .led: return "Colours: LEDs"
        case .artwork: return f.hasArtwork ? "Colours: Artwork" : "Colours: Artwork (none)"
        case .source: return "Colours: Source"
        }
    }
    var body: some View {
        Menu {
            Section("Colours follow") {
                Button { set(.led) } label: { Label("The speakers' LEDs", systemImage: f.cfg.colourMode == .led ? "checkmark" : "lightbulb") }
                Button { set(.artwork) } label: { Label("The album artwork (Syng)", systemImage: f.cfg.colourMode == .artwork ? "checkmark" : "photo") }
                Button { set(.source) } label: { Label("The source's own palette", systemImage: f.cfg.colourMode == .source ? "checkmark" : "paintpalette") }
            }
            Section("Particles") {
                Button { f.setPhysics(.app); physics = "app" } label: { Label("Syng app physics", systemImage: f.cfg.physics == .app ? "checkmark" : "sparkles") }
                Button { f.setPhysics(.legacy); physics = "legacy" } label: { Label("Previous (puff) physics", systemImage: f.cfg.physics == .legacy ? "checkmark" : "wind") }
                // Troubadour's two additions to the app's physics (off = exactly the Syng app)
                Toggle("Drift gently when idle", isOn: Binding(get: { f.cfg.idleDrift > 0 }, set: { f.setIdleDrift($0); idleDrift = $0 }))
                    .disabled(f.cfg.physics != .app)
                Toggle("Puff with the music", isOn: Binding(get: { f.cfg.puffMix > 0 }, set: { f.setPuff($0); puff = $0 }))
                    .disabled(f.cfg.physics != .app)
            }
            Toggle("Artwork in the puck", isOn: Binding(get: { f.cfg.puckArtwork }, set: { f.cfg.puckArtwork = $0; puckArtwork = $0 }))
            Toggle("Band ring around the puck", isOn: Binding(get: { f.cfg.halo > 0 }, set: { f.setHalo($0); halo = $0 }))
            Toggle("Fill the screen", isOn: Binding(get: { f.cfg.fillScreen }, set: { f.setFillScreen($0); fill = $0 }))
        } label: {
            Text(label)
                .font(.footnote.weight(.semibold))
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(Theme.surface2, in: Capsule())
                .foregroundStyle(Color.white)
        }
        .onAppear {
            f.setColourMode(FieldModel.ColourMode(rawValue: colourMode) ?? .artwork)
            f.setPhysics(FieldModel.Physics(rawValue: physics) ?? .app)
            f.cfg.puckArtwork = puckArtwork
            f.setIdleDrift(idleDrift); f.setPuff(puff)
            if f.cfg.fillScreen != fill { f.setFillScreen(fill) }
            f.setHalo(halo)
        }
    }
    private func set(_ m: FieldModel.ColourMode) { f.setColourMode(m); colourMode = m.rawValue }
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
    @ObservedObject var f: FieldModel
    @Environment(\.dismiss) private var dismiss
    @State private var spread: Double = 90          // the full angle between the dots, degrees
    @State private var dragging = false
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
                        RelativeSlider(value: m.volume, max: m.state.volumeLimit > 0 ? Double(m.state.volumeLimit) / 100 : 1) { v, final in m.setVolume(v, final: final) }
                        HStack(spacing: 6) {
                            Text("Ceiling").font(.caption).foregroundStyle(Theme.muted)
                            ForEach([0, 50, 70, 85], id: \.self) { l in
                                Button(l == 0 ? "off" : "\(l)%") { m.volumeLimit(l) }.buttonStyle(Chip(filled: m.state.volumeLimit == l))
                            }
                        }
                        Text("Drag anywhere on the bar: the volume moves with your finger from where it is, and a tap does nothing. The rim at the bottom of the field works the same way.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    Card("Spread") {
                        HStack {
                            Button("All around") { f.setSpatial(false) }.buttonStyle(Capsule_(filled: !f.spatialOn))
                            Button("Spatial") { f.setSpatial(true) }.buttonStyle(Capsule_(filled: f.spatialOn))
                            Button("Centre") { f.centrePuck() }.buttonStyle(Capsule_())
                        }
                        HStack {
                            Slider(value: $spread, in: 50...240, step: 2, onEditingChanged: { editing in
                                dragging = editing
                                f.setWidth(halfDeg: spread / 2, final: !editing)
                            })
                            .tint(.white).disabled(!f.spatialOn)
                            .onChange(of: spread) { v in if dragging { f.setWidth(halfDeg: v / 2, final: false) } }
                            Text("\(Int(spread))°").font(.caption.monospacedDigit()).frame(width: 44, alignment: .trailing)
                        }
                        Text(f.spatialOn ? "The angle between the two focus dots (25°–120° each side, the app's limits), aimed \(f.aimDeg)°. Pinch and twist on the field do the same."
                                         : "Spatial off: the sound comes from all around. Turn it on to shape it.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    Card("Sleep") {
                        HStack(spacing: 6) {
                            ForEach([0, 15, 30, 60, 90], id: \.self) { mins in
                                Button(mins == 0 ? "Off" : "\(mins) min") { m.sleep(mins) }
                                    .buttonStyle(Chip(filled: mins == 0 ? m.state.sleepLabel == nil : false))
                            }
                        }
                        Text(m.state.sleepLabel.map { "Sleep timer: \($0)" } ?? "No sleep timer. The room fades out and the sources stop when it fires.")
                            .font(.caption).foregroundStyle(m.state.sleepLabel == nil ? Theme.muted : Theme.warn)
                    }
                    Card("Spatial processing") {
                        Toggle("Height processing", isOn: Binding(get: { m.state.rep?.height ?? false }, set: { m.spatialFlag("height", $0) }))
                            .font(.subheadline).tint(Theme.live)
                        Toggle("Beyond-stereo duplication", isOn: Binding(get: { m.state.rep?.beyond ?? false }, set: { m.spatialFlag("beyond", $0) }))
                            .font(.subheadline).tint(Theme.live)
                        Text("Syng's own flags on the sources (enableHeightProcessing, enableBeyondStereoDuplication). Height is normally set by Syng from the speakers' height layers — see AirPlay & Atmos in Setup.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    if let ready = m.state.calibReady {
                        Card("Room correction") {
                            HStack {
                                Button("On") { m.roomEq(true) }.buttonStyle(Capsule_())
                                Button("Off") { m.roomEq(false) }.buttonStyle(Capsule_())
                            }
                            HStack {
                                Text(ready ? "Ready to calibrate" : "Not ready to calibrate").font(.caption).foregroundStyle(ready ? Theme.live : Theme.warn)
                                Spacer()
                                if let n = m.state.noiseRMS {
                                    Text(String(format: "room noise %.0f dB", n)).font(.caption.monospacedDigit()).foregroundStyle(n > 55 ? Theme.warn : Theme.muted)
                                }
                                if let l = m.state.noiseLongTerm {
                                    Text(String(format: "· long-term %.0f dB", l)).font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                                }
                            }
                            Text("Room correction is the EQ Soundcheck measured, applied room-wide. The speakers do not report whether it is on.")
                                .font(.caption).foregroundStyle(Theme.muted)
                        }
                    }
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Sound")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Done") { dismiss() } } }
        }
        .presentationDetents([.large])
        .onAppear { spread = Double(f.widthDeg) * 2 }
        .onChange(of: f.widthDeg) { w in if !dragging { spread = Double(w) * 2 } }
    }
}

/// A thin progress line with elapsed / total under the title when the source reports a position (AirPlay does).
struct ProgressLine: View {
    let source: Source?
    var body: some View {
        if let s = source, let d = s.duration, d > 0 {
            let p = min(max((s.position ?? 0) / d, 0), 1)
            VStack(alignment: .leading, spacing: 3) {
                GeometryReader { g in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Theme.surface2).frame(height: 3)
                        Capsule().fill(Color.white).frame(width: max(3, g.size.width * CGFloat(p)), height: 3)
                    }
                }
                .frame(height: 3)
                Text("\(ProgressLine.clock(s.position ?? 0)) / \(ProgressLine.clock(d))")
                    .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
            }
            .padding(.top, 2)
        }
    }
    static func clock(_ t: Double) -> String {
        let s = Int(max(t, 0).rounded())
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60) : String(format: "%d:%02d", s / 60, s % 60)
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
    @StateObject private var live: Endpoint       // /api/fleet/live — reachable, SyFi, time sync, signal
    @State private var renaming: Cell?
    @State private var newName = ""
    init(room: Room) {
        self.room = room
        _m = StateObject(wrappedValue: RoomModel(agent: room.agent))
        _live = StateObject(wrappedValue: Endpoint(room.agent, "/api/fleet/live", every: 20, timeout: 12))
    }
    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Card("Speakers") {
                    ForEach(room.cells) { c in
                        HStack(spacing: 12) {
                            Puck(size: 40, ring: c.role == "primary" ? .white : nil)
                            VStack(alignment: .leading, spacing: 2) {
                                HStack(spacing: 6) {
                                    Text(c.title).font(.subheadline.weight(.semibold))
                                    if let l = liveOf(c.serial) {
                                        Circle().fill(l.flag("ok") ? (l.flag("sync") ? Theme.live : Theme.warn) : Theme.bad).frame(width: 7, height: 7)
                                    }
                                }
                                Text("\(c.shortSerial) · \(c.ip) · \(c.role ?? "")\(c.calibrated == false ? " · uncalibrated" : "")").font(.caption).foregroundStyle(Theme.muted)
                                Text(detail(c)).font(.caption2).foregroundStyle(Theme.muted).lineLimit(1)
                            }
                            Spacer()
                            Button { m.ping(c.serial) } label: { Text("Ping") }.buttonStyle(Capsule_()).frame(width: 74)
                        }
                        .contentShape(Rectangle())
                        .onLongPressGesture { renaming = c; newName = c.name ?? "" }
                        .contextMenu {
                            Button { renaming = c; newName = c.name ?? "" } label: { Label("Rename…", systemImage: "pencil") }
                            Button { m.ping(c.serial) } label: { Label("Ping", systemImage: "wave.3.right") }
                            Link(destination: c.conn.base) { Label("Open its page", systemImage: "safari") }
                        }
                        .padding(.vertical, 4)
                    }
                    Button("Ping all") { m.ping(nil) }.buttonStyle(Capsule_())
                    Text("Long-press a speaker to name it. The dot: green = reachable and in sync, amber = reachable, red = not answering.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .onAppear { m.start(); live.start() }
        .onDisappear { m.stop(); live.stop() }
        .alert("Name this speaker", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Kitchen window", text: $newName)
            Button("Save") { if let c = renaming { Task { try? await m.agent?.post("/api/cell/name", ["serial": c.serial, "name": newName]); await Fleet.shared.refresh() } } }
            Button("Cancel", role: .cancel) {}
        }
    }
    private func liveOf(_ serial: String) -> [String: Any]? { live.data.list("cells").first { $0.str("serial") == serial } }
    /// What the state document says about this speaker: playing what, SyFi, signal, agent build.
    private func detail(_ c: Cell) -> String {
        var bits: [String] = []
        if let f = m.state.fleet.first(where: { $0.str("serial") == c.serial }) {
            if let src = f.str("activeSource"), !src.isEmpty { bits.append("playing \(Source(id: src, [:]).displayName)") }
            if f.flag("isMe") { bits.append("answering") }
        }
        if let l = liveOf(c.serial) {
            bits.append(l.flag("syfi") ? "SyFi" : "no SyFi")
            if let r = l.int("rssi") { bits.append("\(r) dBm") }
            if let a = l["agent"] { bits.append("agent \(a)") }
        }
        return bits.isEmpty ? "—" : bits.joined(separator: " · ")
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
