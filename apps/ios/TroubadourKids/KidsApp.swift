import SwiftUI
import UIKit

// Troubadour Kids — the sound field for small hands.
//
// Two screens and nothing else: pick a room (found by scanning; no settings, no adding speakers), then the room's
// live, music-reactive sound field with a volume slider. The puck, snapping, Spatial, pinch-to-widen and
// twist-to-aim all work and look exactly as in the main app, but the field model runs visual-only — nothing is
// sent to the speakers and the room's real sound field never moves. Volume is the one real control, capped (the
// whole slider is `KidsVolume.cap`), on top of the volume ceiling set in the main app, which the speakers enforce.

@main
struct TroubadourKidsApp: App {
    @StateObject private var fleet = Fleet.shared
    var body: some Scene {
        WindowGroup {
            KidsRoot()
                .environmentObject(fleet)
                .preferredColorScheme(.dark)
                .tint(.white)
                .statusBarHidden()
                .persistentSystemOverlays(.hidden)
        }
    }
}

struct KidsRoot: View {
    @EnvironmentObject var fleet: Fleet
    @AppStorage("kidsRoom") private var roomID = ""          // the last room picked, so it opens straight there
    @State private var picking = false

    private var current: Room? {
        if let r = fleet.rooms.first(where: { $0.id == roomID }) { return r }
        return fleet.rooms.count == 1 ? fleet.rooms.first : nil   // one room: no need to ask
    }

    var body: some View {
        ZStack {
            Theme.bg.ignoresSafeArea()
            if let room = current, !picking {
                KidsRoomView(room: room, canSwitch: true) { withAnimation(.spring(response: 0.4)) { picking = true } }
                    .id(room.id)
                    .transition(.opacity)
            } else {
                KidsRoomPicker(current: current?.id) { r in
                    roomID = r.id
                    withAnimation(.spring(response: 0.4)) { picking = false }
                }
                .transition(.opacity)
            }
        }
        .task { fleet.start() }
    }
}

// MARK: - pick a room

struct KidsRoomPicker: View {
    @EnvironmentObject var fleet: Fleet
    let current: Room.ID?
    let onPick: (Room) -> Void

    var body: some View {
        VStack(spacing: 28) {
            Text(fleet.rooms.isEmpty ? "Finding the speakers…" : "Pick a room")
                .font(.system(size: 44, weight: .heavy, design: .rounded))
                .padding(.top, 40)
            if fleet.rooms.isEmpty {
                Spacer()
                HStack(spacing: -14) {
                    ForEach(0..<3, id: \.self) { _ in
                        Puck(size: 90).opacity(fleet.scanning ? 1 : 0.6)
                    }
                }
                if fleet.scanning { ProgressView().scaleEffect(1.6).tint(.white).padding(.top, 10) }
                Spacer()
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 22)], spacing: 22) {
                        ForEach(fleet.rooms) { room in
                            Button { onPick(room) } label: { KidsRoomCard(room: room, chosen: room.id == current) }
                                .buttonStyle(KidsPress())
                        }
                    }
                    .padding(.horizontal, 28)
                }
            }
            Button {
                Task { await fleet.refresh(force: true) }
            } label: {
                Label("Look again", systemImage: "arrow.clockwise")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .padding(.horizontal, 30).padding(.vertical, 16)
                    .background(Theme.surface2, in: Capsule())
            }
            .buttonStyle(KidsPress())
            .padding(.bottom, 34)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct KidsRoomCard: View {
    let room: Room; let chosen: Bool
    var body: some View {
        VStack(spacing: 18) {
            HStack(spacing: -12) {
                ForEach(room.cells.prefix(6)) { _ in Puck(size: 58) }
            }
            .frame(height: 64)
            Text(room.name)
                .font(.system(size: 30, weight: .heavy, design: .rounded))
                .lineLimit(1).minimumScaleFactor(0.6)
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity).padding(.vertical, 34)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 34, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 34, style: .continuous).stroke(Color.white.opacity(chosen ? 0.8 : 0), lineWidth: 3))
    }
}

/// Big, squishy press feedback.
struct KidsPress: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.94 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.6), value: configuration.isPressed)
    }
}

// MARK: - the room

struct KidsRoomView: View {
    let room: Room
    let canSwitch: Bool
    let onRooms: () -> Void
    @StateObject private var f: FieldModel
    @StateObject private var vol: KidsVolume

    init(room: Room, canSwitch: Bool, onRooms: @escaping () -> Void) {
        self.room = room; self.canSwitch = canSwitch; self.onRooms = onRooms
        _f = StateObject(wrappedValue: {
            let m = FieldModel(agent: room.agent)
            m.visualOnly = true                   // look, don't touch: the speakers' sound field never changes
            m.cfg.showVolume = false              // volume is the big slider below, capped
            if UIDevice.current.userInterfaceIdiom == .pad { m.cfg.particles = 40_000 }
            return m
        }())
        _vol = StateObject(wrappedValue: KidsVolume(agent: room.agent))
    }

    var body: some View {
        ZStack {
            FieldCanvas(model: f)
                .ignoresSafeArea()
            VStack {
                HStack(alignment: .top) {
                    if canSwitch {
                        RoundButton(icon: "square.grid.2x2.fill", action: onRooms)
                    }
                    Spacer()
                    Text(room.name)
                        .font(.system(size: 22, weight: .heavy, design: .rounded))
                        .foregroundStyle(.white.opacity(0.85))
                        .padding(.top, 14)
                    Spacer()
                    RoundButton(icon: f.spatialOn ? "sparkles" : "sparkle", lit: f.spatialOn) {
                        f.setSpatial(!f.spatialOn)
                    }
                }
                .padding(.horizontal, 24).padding(.top, 14)
                Spacer()
                volumeBar
                    .padding(.horizontal, 28).padding(.bottom, 26)
            }
        }
        .onAppear { f.start(); vol.start() }
        .onDisappear { f.stop(); vol.stop() }
    }

    private var volumeBar: some View {
        HStack(spacing: 18) {
            Image(systemName: "speaker.fill").font(.system(size: 26, weight: .bold))
            RelativeSlider(value: vol.shown, track: 18, knob: 46, showsNumber: false) { v, final in vol.set(v, final: final) }
            Image(systemName: "speaker.wave.3.fill").font(.system(size: 26, weight: .bold))
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 26).padding(.vertical, 18)
        .background(.ultraThinMaterial, in: Capsule())
        .frame(maxWidth: 720)
    }
}

private struct RoundButton: View {
    let icon: String
    var lit = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(lit ? Color.black : Color.white)
                .frame(width: 72, height: 72)
                .background(lit ? AnyShapeStyle(Color.white) : AnyShapeStyle(.ultraThinMaterial), in: Circle())
        }
        .buttonStyle(KidsPress())
    }
}

// MARK: - volume, the one real control

@MainActor
final class KidsVolume: ObservableObject {
    /// The top of the kids' slider: all the way right is this much of the speakers' volume, never more.
    static let cap = 0.25
    let agent: Agent?
    @Published private(set) var shown = 0.0          // 0..1 along the slider (= volume / cap)
    private var timer: Timer?
    private var dragging = false
    private let sender = LatestSender(minGap: 0.1)   // one request in flight, newest value wins, release lands last

    init(agent: Agent?) { self.agent = agent }

    func start() {
        Task { await poll() }
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in Task { await self?.poll() } }
    }
    func stop() { timer?.invalidate(); timer = nil }

    private func poll() async {
        guard !dragging, let a = agent, let d = try? await a.get("/api/state", timeout: 4) else { return }
        let src = (d["sources"] as? [String: [String: Any]]) ?? [:]
        if let v = src.values.first(where: { ($0["active"] as? Bool) == true })?["volume"] as? Double {
            shown = min(1, max(0, v / Self.cap))
        }
    }

    /// From the slider: moved by the finger only (it never jumps). Latest-wins, one request in flight — a wiggled
    /// slider no longer floods the speakers with hundreds of posts (2026-10-21: ~300 in 90 s while music played).
    func set(_ s: Double, final: Bool) {
        dragging = !final
        shown = min(1, max(0, s))
        let v = (shown * Self.cap * 1000).rounded() / 1000
        sender.submit(["source": "all", "volume": v]) { [agent] b in _ = try? await agent?.post("/api/volume", b, timeout: 4) }
    }
}
