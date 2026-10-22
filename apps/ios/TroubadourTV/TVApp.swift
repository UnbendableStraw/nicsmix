import SwiftUI
import UIKit

// Troubadour TV — the sound field on the big screen.
//
// Pick a room once; after that the app opens straight into that room's live, music-reactive sound field, full screen.
// The field follows the room (the puck, its width and aim, Spatial, the speakers' layout, the colours) and the Siri
// Remote steers it:
//   ◀ / ▶ (swipe or click the ring) ... the previous / next room
//   ▲ / ▼ ............................. volume
//   click ............................. the controls: listening mode, move the puck, width and aim, playback,
//                                       colours, particle physics, "always show what's playing", rooms
//   ⏯ ................................. play / pause the room's source
//   Menu / Back ....................... close the controls, else the room picker (Menu there leaves the app)
// "Move the puck" turns the touch surface into a trackpad for the puck (click or Menu when done).
// The screen saver stays off while a field is showing.

@main
struct TroubadourTVApp: App {
    @StateObject private var fleet = Fleet.shared
    var body: some Scene {
        WindowGroup {
            TVRoot()
                .environmentObject(fleet)
                .preferredColorScheme(.dark)
        }
    }
}

struct TVRoot: View {
    @EnvironmentObject var fleet: Fleet
    @AppStorage("tvRoom") private var roomID = ""
    @State private var picking = false

    private var current: Room? {
        if let r = fleet.rooms.first(where: { $0.id == roomID }) { return r }
        return fleet.rooms.count == 1 ? fleet.rooms.first : nil
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let room = current, !picking {
                TVFieldView(room: room,
                            onRooms: { withAnimation(.easeInOut(duration: 0.35)) { picking = true } },
                            onStep: { step($0, from: room) })
                    .id(room.id)
                    .transition(.opacity)
            } else {
                TVRoomPicker(current: current?.id) { r in
                    roomID = r.id
                    withAnimation(.easeInOut(duration: 0.35)) { picking = false }
                }
                .transition(.opacity)
            }
        }
        .task { fleet.start() }
        .onChange(of: picking) { p in UIApplication.shared.isIdleTimerDisabled = !p }
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
    }

    private func step(_ d: Int, from room: Room) {
        let rooms = fleet.rooms
        guard rooms.count > 1, let i = rooms.firstIndex(where: { $0.id == room.id }) else { return }
        withAnimation(.easeInOut(duration: 0.5)) { roomID = rooms[(i + d + rooms.count) % rooms.count].id }
    }
}

// MARK: - the room picker

struct TVRoomPicker: View {
    @EnvironmentObject var fleet: Fleet
    let current: Room.ID?
    let onPick: (Room) -> Void

    var body: some View {
        VStack(spacing: 50) {
            Text(fleet.rooms.isEmpty ? "Finding the speakers…" : "Choose a room")
                .font(.system(size: 64, weight: .bold))
                .padding(.top, 60)
            if fleet.rooms.isEmpty {
                Spacer()
                if fleet.localNetworkDenied {
                    Text("Troubadour needs Local Network access to find the speakers.\nSettings → Privacy → Local Network.")
                        .multilineTextAlignment(.center).foregroundStyle(Theme.muted)
                } else {
                    HStack(spacing: -20) { ForEach(0..<3, id: \.self) { _ in Puck(size: 120) } }
                    ProgressView().padding(.top, 20)
                }
                Spacer()
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 440), spacing: 48)], spacing: 48) {
                        ForEach(fleet.rooms) { room in
                            Button { onPick(room) } label: { TVRoomCard(room: room, chosen: room.id == current) }
                                .buttonStyle(.card)
                        }
                    }
                    .padding(.horizontal, 80).padding(.vertical, 30)
                }
            }
            Button { Task { await fleet.refresh(force: true) } } label: {
                Label("Look again", systemImage: "arrow.clockwise")
            }
            .padding(.bottom, 50)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct TVRoomCard: View {
    let room: Room; let chosen: Bool
    var body: some View {
        VStack(spacing: 24) {
            HStack(spacing: -16) { ForEach(room.cells.prefix(6)) { _ in Puck(size: 70) } }
                .frame(height: 76)
            Text(room.name).font(.system(size: 40, weight: .semibold)).lineLimit(1).minimumScaleFactor(0.6)
            Text("\(room.cells.count) speaker\(room.cells.count == 1 ? "" : "s")" + (chosen ? " · showing" : ""))
                .font(.system(size: 24)).foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 44)
        .background(Theme.surface)
    }
}

// MARK: - the field, full screen

/// What the remote is doing: watching the field, dragging the puck on the touch surface, or in the controls.
enum TVMode { case watching, steering, panel }

struct TVFieldView: View {
    let room: Room
    let onRooms: () -> Void
    let onStep: (Int) -> Void
    @StateObject private var f: FieldModel
    @StateObject private var np: TVNowPlaying
    @AppStorage("tv.infoAlways") private var infoAlways = false
    @AppStorage("field.colourMode") private var colourMode = "artwork"
    @AppStorage("field.physics") private var physics = "app"
    @AppStorage("field.idleDrift") private var idleDrift = true
    @AppStorage("field.puff") private var puff = true
    @AppStorage("field.fill") private var fill = false
    @AppStorage("field.halo") private var halo = true
    @State private var mode: TVMode = .watching
    @FocusState private var fieldFocused: Bool
    @State private var showInfo = true
    @State private var hide: Task<Void, Never>?
    @State private var toast: String?
    @State private var toastHide: Task<Void, Never>?
    @State private var tick = 0                       // bumps to re-read the model after a control changes it

    init(room: Room, onRooms: @escaping () -> Void, onStep: @escaping (Int) -> Void) {
        self.room = room; self.onRooms = onRooms; self.onStep = onStep
        _f = StateObject(wrappedValue: {
            let m = FieldModel(agent: room.agent)
            m.cfg.showVolume = false
            m.cfg.particles = 40_000
            return m
        }())
        _np = StateObject(wrappedValue: TVNowPlaying(agent: room.agent))
    }

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            fieldLayer
            if mode == .panel {
                TVControlPanel(f: f, np: np, tick: $tick,
                               infoAlways: $infoAlways, colourMode: $colourMode, physics: $physics,
                               idleDrift: $idleDrift, puff: $puff, fill: $fill, halo: $halo,
                               onSteer: { mode = .steering; flash("Swipe to move the puck · click when done") },
                               onRooms: onRooms,
                               onToast: { flash($0) })
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .trailing)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
                    .onExitCommand { closePanel() }        // Menu inside the panel closes it
            }
        }
        .onPlayPauseCommand { np.togglePlay(); flashInfo() }
        .onAppear {
            f.setColourMode(FieldModel.ColourMode(rawValue: colourMode) ?? .artwork)
            f.setPhysics(FieldModel.Physics(rawValue: physics) ?? .app)
            f.setIdleDrift(idleDrift); f.setPuff(puff); f.setFillScreen(fill); f.setHalo(halo)
            f.start(); np.start(); flashInfo()
            fieldFocused = true
        }
        .onDisappear { f.stop(); np.stop(); hide?.cancel(); toastHide?.cancel() }
        // whenever the panel goes away, the field takes focus back — without it the remote talks to nothing
        .onChange(of: mode) { m in if m != .panel { DispatchQueue.main.async { fieldFocused = true } } }
    }

    /// The field and its overlays, as one big borderless Button: on tvOS a Button is the one thing that reliably
    /// gets focus and the Select click (a plain focusable view's tap / long-press gestures often never arrive).
    /// It is disabled while the panel is open, so focus has nowhere to go but the panel's buttons.
    private var fieldLayer: some View {
        Button(action: click) {
            ZStack(alignment: .bottomLeading) {
                FieldCanvas(model: f)
                    .ignoresSafeArea()
                // the touch surface as a trackpad for the puck while steering
                TVTouchPad(active: mode == .steering,
                           onPan: { d in f.movePuck(by: CGSize(width: d.width * 0.5, height: d.height * 0.5)) },
                           onEnd: { f.commitPuck() })
                    .allowsHitTesting(false)
                if showInfo || infoAlways || mode != .watching {
                    info
                        .padding(.leading, 90).padding(.bottom, 70)
                        .transition(.opacity)
                }
                if let t = toast {
                    Text(t)
                        .font(.system(size: 30, weight: .semibold))
                        .padding(.horizontal, 30).padding(.vertical, 16)
                        .background(.ultraThinMaterial, in: Capsule())
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                        .padding(.top, 70)
                        .transition(.opacity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .buttonStyle(TVBareButton())
        .disabled(mode == .panel)
        .focused($fieldFocused)
        .ignoresSafeArea()
        .onMoveCommand { d in
            switch mode {
            case .panel: break
            case .steering:
                let n: CGFloat = 24
                switch d {
                case .left: f.movePuck(by: CGSize(width: -n, height: 0))
                case .right: f.movePuck(by: CGSize(width: n, height: 0))
                case .up: f.movePuck(by: CGSize(width: 0, height: -n))
                case .down: f.movePuck(by: CGSize(width: 0, height: n))
                @unknown default: break
                }
                f.commitPuck()
            case .watching:
                switch d {
                case .left: onStep(-1)
                case .right: onStep(1)
                case .up: volume(+0.03)
                case .down: volume(-0.03)
                @unknown default: flashInfo()
                }
            }
        }
        .onExitCommand {
            switch mode {
            case .panel: closePanel()
            case .steering: f.commitPuck(); withAnimation { mode = .watching }; flashInfo()
            case .watching: onRooms()
            }
        }
    }

    /// Select on the field: opens the controls; while steering the puck, finishes.
    private func click() {
        switch mode {
        case .steering: f.commitPuck(); withAnimation { mode = .watching }; flashInfo()
        case .watching: openPanel()
        case .panel: break
        }
    }

    private func openPanel() {
        withAnimation(.easeOut(duration: 0.3)) { mode = .panel }
    }
    private func closePanel() {
        withAnimation(.easeOut(duration: 0.25)) { mode = .watching }
        flashInfo()
    }

    private var info: some View {
        HStack(alignment: .center, spacing: 28) {
            if let cg = f.artworkImage, f.cfg.puckArtwork {
                Image(decorative: cg, scale: 1)
                    .resizable().aspectRatio(contentMode: .fill)
                    .frame(width: 150, height: 150)
                    .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    .shadow(color: .black.opacity(0.5), radius: 16)
            }
            VStack(alignment: .leading, spacing: 10) {
                Text(room.name).font(.system(size: 54, weight: .bold))
                if let t = np.title {
                    HStack(spacing: 14) {
                        Image(systemName: np.playing ? "play.fill" : "pause.fill").font(.system(size: 26))
                        Text(t).font(.system(size: 32, weight: .semibold)).lineLimit(1)
                        if let b = np.badge {
                            Text(b).font(.system(size: 20, weight: .bold).monospacedDigit())
                                .padding(.horizontal, 10).padding(.vertical, 4)
                                .overlay(Capsule().stroke(Color.white.opacity(0.6), lineWidth: 1.5))
                        }
                    }
                    if let a = np.subtitle { Text(a).font(.system(size: 26)).foregroundStyle(Theme.muted).lineLimit(1) }
                } else {
                    Text("Nothing playing").font(.system(size: 28)).foregroundStyle(Theme.muted)
                }
                Text(hint)
                    .font(.system(size: 20)).foregroundStyle(Theme.muted.opacity(0.8)).padding(.top, 8)
            }
        }
        .padding(.horizontal, 34).padding(.vertical, 26)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 28, style: .continuous))
        .shadow(color: .black.opacity(0.4), radius: 30)
        .id(tick)
    }

    private var hint: String {
        switch mode {
        case .watching: return "◀ ▶ rooms   ·   ▲ ▼ volume   ·   ⏯ play / pause   ·   click: controls   ·   Menu: all rooms"
        case .steering: return "Swipe on the remote to move the puck   ·   click or Menu when done"
        case .panel: return "Swipe to move between controls · click to change · Menu closes"
        }
    }

    /// Show what's playing, then fade back to the bare field (unless it is set to stay).
    private func flashInfo() {
        withAnimation(.easeOut(duration: 0.25)) { showInfo = true }
        hide?.cancel()
        guard !infoAlways else { return }
        hide = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 6_000_000_000)
            if !Task.isCancelled, mode == .watching { withAnimation(.easeIn(duration: 1.2)) { showInfo = false } }
        }
    }
    private func flash(_ t: String) {
        withAnimation(.easeOut(duration: 0.2)) { toast = t }
        toastHide?.cancel()
        toastHide = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            if !Task.isCancelled { withAnimation(.easeIn(duration: 0.6)) { toast = nil } }
        }
    }
    private func volume(_ d: Double) {
        f.adjustVolume(by: d)
        flash("Volume \(Int((f.volTarget * 100).rounded()))%")
    }
}

/// A ButtonStyle that draws its label untouched: the field must not lift, scale or tint when focused.
struct TVBareButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View { configuration.label }
}

// MARK: - the controls (click on the field)

struct TVControlPanel: View {
    @ObservedObject var f: FieldModel
    @ObservedObject var np: TVNowPlaying
    @Binding var tick: Int
    @Binding var infoAlways: Bool
    @Binding var colourMode: String
    @Binding var physics: String
    @Binding var idleDrift: Bool
    @Binding var puff: Bool
    @Binding var fill: Bool
    @Binding var halo: Bool
    let onSteer: () -> Void
    let onRooms: () -> Void
    let onToast: (String) -> Void
    @FocusState private var firstFocused: Bool

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 34) {
                section("Listening") {
                    row {
                        pick("Classic", on: !f.spatial) { f.setSpatial(false); bump() }
                            .focused($firstFocused)
                        pick("Spatial", on: f.spatial) { f.setSpatial(true); bump() }
                        act("Centre the puck", "scope") { f.centrePuck(); bump(); onToast("Puck centred") }
                        act("Move the puck…", "hand.draw") { onSteer() }
                    }
                    if f.spatial {
                        row {
                            act("Narrower", "arrow.right.and.line.vertical.and.arrow.left") { f.widenBy(0.85); f.commitPuck(); bump() }
                            act("Wider", "arrow.left.and.line.vertical.and.arrow.right") { f.widenBy(1.18); f.commitPuck(); bump() }
                            act("Aim ⟲", "arrow.counterclockwise") { f.rotateBy(-.pi / 12); f.commitPuck(); bump() }
                            act("Aim ⟳", "arrow.clockwise") { f.rotateBy(.pi / 12); f.commitPuck(); bump() }
                        }
                    }
                }
                section("Playback") {
                    row {
                        act("Previous", "backward.end.fill") { np.transport("previous") }
                        act(np.playing ? "Pause" : "Play", np.playing ? "pause.fill" : "play.fill") { np.togglePlay() }
                        act("Next", "forward.end.fill") { np.transport("next") }
                        act("Quieter", "speaker.minus.fill") { f.adjustVolume(by: -0.05); onToast("Volume \(Int((f.volTarget * 100).rounded()))%") }
                        act("Louder", "speaker.plus.fill") { f.adjustVolume(by: 0.05); onToast("Volume \(Int((f.volTarget * 100).rounded()))%") }
                    }
                }
                section("Colours follow") {
                    row {
                        pick("The speakers' LEDs", on: f.cfg.colourMode == .led) { setColour(.led) }
                        pick(f.hasArtwork ? "The album artwork" : "The album artwork (none now)", on: f.cfg.colourMode == .artwork) { setColour(.artwork) }
                        pick("The source's palette", on: f.cfg.colourMode == .source) { setColour(.source) }
                    }
                }
                section("Particles") {
                    row {
                        pick("Syng app physics", on: f.cfg.physics == .app) { f.setPhysics(.app); physics = "app"; bump() }
                        pick("Previous (puff) physics", on: f.cfg.physics == .legacy) { f.setPhysics(.legacy); physics = "legacy"; bump() }
                    }
                    row {
                        pick("Drift gently when idle", on: f.cfg.idleDrift > 0) { idleDrift.toggle(); f.setIdleDrift(idleDrift); bump() }
                        pick("Puff with the music", on: f.cfg.puffMix > 0) { puff.toggle(); f.setPuff(puff); bump() }
                        pick("Band ring around the puck", on: f.cfg.halo > 0) { halo.toggle(); f.setHalo(halo); bump() }
                        pick("Artwork in the puck", on: f.cfg.puckArtwork) { f.objectWillChange.send(); f.cfg.puckArtwork.toggle(); bump() }
                    }
                }
                section("On screen") {
                    row {
                        pick("Fill the screen", on: f.cfg.fillScreen) { fill.toggle(); f.setFillScreen(fill); bump() }
                        pick("Always show what's playing", on: infoAlways) { infoAlways.toggle(); bump() }
                        act("Choose a room", "rectangle.3.group") { onRooms() }
                    }
                }
            }
            .padding(.horizontal, 60).padding(.vertical, 50)
        }
        .frame(width: 900)
        .frame(maxHeight: .infinity)
        .background(.ultraThinMaterial)
        .focusSection()
        .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { firstFocused = true } }
    }

    private func bump() { tick += 1 }
    private func setColour(_ m: FieldModel.ColourMode) { f.setColourMode(m); colourMode = m.rawValue; bump() }

    @ViewBuilder private func section<C: View>(_ title: String, @ViewBuilder _ c: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title).font(.system(size: 26, weight: .bold)).foregroundStyle(Theme.muted)
            c()
        }
    }
    @ViewBuilder private func row<C: View>(@ViewBuilder _ c: () -> C) -> some View {
        ScrollView(.horizontal, showsIndicators: false) { HStack(spacing: 18) { c() } }
    }
    private func pick(_ title: String, on: Bool, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: on ? "checkmark.circle.fill" : "circle")
                .font(.system(size: 26, weight: .semibold))
                .lineLimit(1)
        }
    }
    private func act(_ title: String, _ symbol: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(.system(size: 26, weight: .semibold))
                .lineLimit(1)
        }
    }
}

// MARK: - the touch surface as a trackpad

/// While `active`, a pan recogniser for the Siri Remote's touch surface (indirect touches) sits on the window and
/// reports deltas; the focus engine keeps getting the clicks. Removed as soon as steering ends.
struct TVTouchPad: UIViewRepresentable {
    var active: Bool
    var onPan: (CGSize) -> Void
    var onEnd: () -> Void

    final class Pad: UIView {
        var onPan: (CGSize) -> Void = { _ in }
        var onEnd: () -> Void = {}
        var active = false { didSet { sync() } }
        private var pan: UIPanGestureRecognizer?
        override func didMoveToWindow() { super.didMoveToWindow(); sync() }
        private func sync() {
            if active, pan == nil, let w = window {
                let g = UIPanGestureRecognizer(target: self, action: #selector(onPanG(_:)))
                g.allowedTouchTypes = [NSNumber(value: UITouch.TouchType.indirect.rawValue)]
                w.addGestureRecognizer(g)
                pan = g
            } else if !active, let g = pan {
                g.view?.removeGestureRecognizer(g)
                pan = nil
            }
        }
        @objc private func onPanG(_ g: UIPanGestureRecognizer) {
            switch g.state {
            case .changed:
                let t = g.translation(in: nil)
                onPan(CGSize(width: t.x, height: t.y))
                g.setTranslation(.zero, in: nil)
            case .ended, .cancelled, .failed: onEnd()
            default: break
            }
        }
    }
    func makeUIView(context: Context) -> Pad {
        let p = Pad(); p.isUserInteractionEnabled = false; p.backgroundColor = .clear; return p
    }
    func updateUIView(_ p: Pad, context: Context) {
        p.onPan = onPan; p.onEnd = onEnd
        if p.active != active { p.active = active }
    }
    static func dismantleUIView(_ p: Pad, coordinator: ()) { p.active = false }
}

// MARK: - what's playing, and transport

@MainActor
final class TVNowPlaying: ObservableObject {
    let agent: Agent?
    @Published private(set) var title: String?
    @Published private(set) var subtitle: String?
    @Published private(set) var badge: String?
    @Published private(set) var playing = false
    private var source: String?
    private var timer: Timer?

    init(agent: Agent?) { self.agent = agent }

    func start() {
        Task { await poll() }
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in Task { await self?.poll() } }
    }
    func stop() { timer?.invalidate(); timer = nil }

    private func poll() async {
        guard let a = agent, let d = try? await a.get("/api/state", timeout: 6) else { return }
        let src = (d["sources"] as? [String: [String: Any]]) ?? [:]
        guard let act = src.first(where: { ($0.value["active"] as? Bool) == true && ($0.value["music"] as? Bool) != false }) else {
            title = nil; subtitle = nil; badge = nil; playing = false; source = nil; return
        }
        let id = act.key, s = act.value
        source = id
        let t = (s["title"] as? String) ?? ""
        let names = ["airplay2": "AirPlay", "spotify": "Spotify", "tidal": "TIDAL"]
        title = t.isEmpty ? (names[id] ?? id) : t
        let bits = [s["artist"] as? String, s["album"] as? String].compactMap { $0 }.filter { !$0.isEmpty }
        subtitle = bits.isEmpty ? nil : bits.joined(separator: " · ")
        playing = (s["playing"] as? Bool) ?? false
        if let f = s["format"] as? String, f.hasPrefix("Dolby Atmos") || f.hasPrefix("Surround") {
            badge = f.replacingOccurrences(of: "Dolby Atmos", with: "ATMOS").replacingOccurrences(of: "Surround ", with: "")
        } else { badge = nil }
    }

    func togglePlay() {
        guard source != nil else { return }
        let action = playing ? "pause" : "play"
        playing.toggle()
        transport(action)
    }
    /// play / pause / next / previous on the room's source
    func transport(_ action: String) {
        guard let a = agent, let s = source else { return }
        Task {
            _ = try? await a.post("/api/transport", ["source": s, "action": action], timeout: 6)
            try? await Task.sleep(nanoseconds: 800_000_000)
            await poll()
        }
    }
}
