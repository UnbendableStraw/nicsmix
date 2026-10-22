import SwiftUI
import WidgetKit

// The iPad app. Not the phone layout stretched: rooms live in a sidebar, and a room is one screen — the sound
// field as large as the display allows with everything else beside it (landscape) or under it (portrait), no
// sheets to open for volume, sound mode or scenes. Setup is two panes: sections on the left, the page on the
// right. With a narrow window (Split View, Slide Over, a small Stage Manager window) the size class turns
// compact and RootView hands over to the phone layout, which is what fits there.

struct PadRootView: View {
    @EnvironmentObject var fleet: Fleet
    @Binding var selection: Room.ID?
    @State private var columns = NavigationSplitViewVisibility.automatic
    @State private var addAddress = false
    @State private var manualIP = ""

    var body: some View {
        NavigationSplitView(columnVisibility: $columns) {
            sidebar
                .navigationSplitViewColumnWidth(min: 260, ideal: 300, max: 360)
        } detail: {
            if let room = fleet.rooms.first(where: { $0.id == selection }) {
                PadRoomView(room: room).id(room.id)          // a fresh field + models per room
            } else {
                PadEmpty(scanning: fleet.scanning, error: fleet.lastError, hasRooms: !fleet.rooms.isEmpty)
            }
        }
        .navigationSplitViewStyle(.balanced)
        .onAppear(perform: pickDefault)
        .onChange(of: fleet.rooms) { _ in pickDefault() }
        .alert("Add a speaker by address", isPresented: $addAddress) {
            TextField("192.168.1.23 or 10.86.0.1", text: $manualIP).keyboardType(.decimalPad)
            Button("Add") { fleet.addSeed(manualIP); manualIP = "" }
            Button("Cancel", role: .cancel) { manualIP = "" }
        }
    }

    /// Open something straight away: the room the widgets show, else the first one.
    private func pickDefault() {
        if let s = selection, fleet.rooms.contains(where: { $0.id == s }) { return }
        selection = fleet.rooms.first(where: { $0.answers(Shared.agentIP) })?.id ?? fleet.rooms.first?.id
    }

    private var sidebar: some View {
        List(selection: $selection) {
            if fleet.rooms.isEmpty {
                HStack(spacing: 10) {
                    if fleet.scanning { ProgressView().tint(.white) }
                    Text(fleet.scanning ? "Looking for your Cells…" : (fleet.lastError ?? "No Cells found yet."))
                        .font(.subheadline).foregroundStyle(Theme.muted)
                }
                .listRowBackground(Color.clear)
            }
            ForEach(fleet.rooms) { room in
                PadRoomRow(room: room, selected: selection == room.id)      // List tags it with room.id
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.sidebar)
        .tint(Color(white: 0.22))       // the sidebar paints the selected row in the tint; the app's is white
        .scrollContentBackground(.hidden)
        .background(Theme.bg.ignoresSafeArea())
        .navigationTitle("Rooms")
        .refreshable { await fleet.refresh() }
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                Menu {
                    Button { Task { await fleet.refresh(force: true) } } label: { Label("Rescan", systemImage: "arrow.clockwise") }
                    Button { addAddress = true } label: { Label("Add by address…", systemImage: "plus") }
                } label: { Image(systemName: "ellipsis.circle") }
            }
        }
    }
}

private struct PadRoomRow: View {
    let room: Room; let selected: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(room.name).font(.headline)
                Spacer()
                Text("\(room.cells.count)").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.muted)
            }
            HStack(spacing: -6) {
                ForEach(room.cells.prefix(8)) { c in Puck(size: 26, ring: c.role == "primary" ? .white : nil) }
            }
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        .hoverEffect(.highlight)
    }
}

private struct PadEmpty: View {
    let scanning: Bool; let error: String?; let hasRooms: Bool
    var body: some View {
        VStack(spacing: 14) {
            HStack(spacing: -10) { ForEach(0..<3, id: \.self) { _ in Puck(size: 64) } }
            Text(hasRooms ? "Pick a room" : (scanning ? "Looking for your Cells…" : "No Cells found yet"))
                .font(.title3.weight(.semibold))
            if !hasRooms {
                Text(error ?? "Same Wi-Fi as the speakers? Or plug one in over USB-C and add 10.86.0.1 from the ⋯ menu.")
                    .font(.subheadline).foregroundStyle(Theme.muted).multilineTextAlignment(.center).frame(maxWidth: 420)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg.ignoresSafeArea())
    }
}

// MARK: - one room

struct PadRoomView: View {
    let room: Room
    @State private var mode = 0                 // 0 = the room, 1 = setup
    var body: some View {
        NavigationStack {
            Group {
                if mode == 0 { PadRoomMain(room: room) } else { PadSetupView(room: room) }
            }
            .navigationTitle(room.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Text(room.name).font(.headline).lineLimit(1)      // the segmented control has the title's place
                }
                ToolbarItem(placement: .principal) {
                    Picker("", selection: $mode) {
                        Text("Room").tag(0)
                        Text("Setup").tag(1)
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 220)
                }
            }
            .toolbarBackground(Theme.bg, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .onAppear {
            Shared.remember(room)
            WidgetCenter.shared.reloadAllTimelines()
        }
    }
}

/// The field and the room's controls on one screen.
struct PadRoomMain: View {
    let room: Room
    @StateObject private var f: FieldModel
    @StateObject private var m: RoomModel
    @State private var renaming: Cell?
    @State private var newName = ""
    @State private var spread: Double = 90
    @State private var spreadDrag = false

    init(room: Room) {
        self.room = room
        _f = StateObject(wrappedValue: {
            let fm = FieldModel(agent: room.agent)
            fm.cfg.particles = 40_000          // a field four or five times the phone's area keeps the same density
            return fm
        }())
        _m = StateObject(wrappedValue: RoomModel(agent: room.agent))
    }

    var body: some View {
        GeometryReader { geo in
            let landscape = geo.size.width > geo.size.height * 1.1
            Group {
                if landscape {
                    HStack(spacing: 0) {
                        field
                        ScrollView { panels(columns: 1).padding(16) }
                            .frame(width: min(420, max(340, geo.size.width * 0.34)))
                            .background(Theme.surface.opacity(0.35))
                    }
                } else {
                    VStack(spacing: 0) {
                        field.frame(height: min(geo.size.height * 0.58, geo.size.width * 1.02))
                        ScrollView { panels(columns: 2).padding(16) }
                    }
                }
            }
        }
        .background(Theme.bg.ignoresSafeArea())
        .background(DisablesSwipeBack())
        .onAppear { f.start(); m.start() }
        .onDisappear { f.stop(); m.stop() }
        .alert("Name this speaker", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Kitchen window", text: $newName)
            Button("Save") { if let c = renaming { Task { try? await m.agent?.post("/api/cell/name", ["serial": c.serial, "name": newName]); await Fleet.shared.refresh() } } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var field: some View {
        FieldCanvas(model: f)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .overlay(alignment: .bottomLeading) {
                if let e = f.error ?? m.error {
                    Text(e).font(.caption2).foregroundStyle(Theme.bad).lineLimit(1).padding(12)
                }
            }
    }

    @ViewBuilder private func panels(columns: Int) -> some View {
        if columns == 1 {
            VStack(spacing: 14) {
                nowPlaying
                soundCard
                sourcesCard
                if !m.scenes.isEmpty { scenesCard }
                speakersCard
            }
        } else {
            HStack(alignment: .top, spacing: 14) {
                VStack(spacing: 14) {
                    nowPlaying
                    sourcesCard
                }
                VStack(spacing: 14) {
                    soundCard
                    if !m.scenes.isEmpty { scenesCard }
                    speakersCard
                }
            }
        }
    }

    // ---- now playing: big artwork, transport, the volume bar right here ----
    private var nowPlaying: some View {
        Card {
            HStack(alignment: .top, spacing: 14) {
                Artwork(agent: m.agent, source: m.state.active, size: 112)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.title3.weight(.bold)).lineLimit(2)
                    Text(subtitle).font(.subheadline).foregroundStyle(Theme.muted).lineLimit(2)
                    HStack(spacing: 5) {
                        Circle().fill(f.live ? Theme.live : Theme.muted.opacity(0.4)).frame(width: 7, height: 7)
                        Text(m.state.active?.displayName ?? "idle").font(.caption).foregroundStyle(Theme.muted)
                        FormatBadge(format: m.state.active?.format)
                    }
                    if let b = mixLine { Text(b).font(.caption2).foregroundStyle(Theme.muted).lineLimit(1) }
                    ProgressLine(source: m.state.active)
                }
                Spacer(minLength: 0)
            }
            HStack(spacing: 8) {
                let a = m.state.active, playing = a?.playing == true
                Button { m.transport("previous") } label: { Image(systemName: "backward.fill") }
                    .buttonStyle(Capsule_()).keyboardShortcut(.leftArrow, modifiers: .command).hoverEffect(.lift)
                    .disabled(a?.can("previous") == false)
                Button { m.transport(playing ? "pause" : "play") } label: {
                    Image(systemName: playing ? "pause.fill" : "play.fill")
                }
                .buttonStyle(Capsule_(filled: true)).keyboardShortcut(.space, modifiers: []).hoverEffect(.lift)
                .disabled(a?.can(playing ? "pause" : "play") == false)
                Button { m.transport("next") } label: { Image(systemName: "forward.fill") }
                    .buttonStyle(Capsule_()).keyboardShortcut(.rightArrow, modifiers: .command).hoverEffect(.lift)
                    .disabled(a?.can("next") == false)
            }
            .disabled(m.state.active == nil)
            HStack(spacing: 10) {
                Image(systemName: "speaker.fill").font(.caption).foregroundStyle(Theme.muted)
                RelativeSlider(value: m.volume, max: m.state.volumeLimit > 0 ? Double(m.state.volumeLimit) / 100 : 1) { v, final in m.setVolume(v, final: final) }
                Image(systemName: "speaker.wave.3.fill").font(.caption).foregroundStyle(Theme.muted)
            }
            HStack(spacing: 6) {
                Text("Ceiling").font(.caption).foregroundStyle(Theme.muted)
                ForEach([0, 50, 70, 85], id: \.self) { l in
                    Button(l == 0 ? "off" : "\(l)%") { m.volumeLimit(l) }.buttonStyle(Chip(filled: m.state.volumeLimit == l)).hoverEffect(.lift)
                }
                Spacer()
                Text("Sleep").font(.caption).foregroundStyle(Theme.muted)
                ForEach([0, 15, 30, 60, 90], id: \.self) { mins in
                    Button(mins == 0 ? "off" : "\(mins)") { m.sleep(mins) }.buttonStyle(Chip(filled: mins == 0 && m.state.sleepLabel == nil)).hoverEffect(.lift)
                }
            }
            if let sl = m.state.sleepLabel { Text("Sleep timer: \(sl)").font(.caption).foregroundStyle(Theme.warn) }
        }
    }
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
        return bits.isEmpty ? "Drag the puck to move the sound" : bits.joined(separator: " · ")
    }

    // ---- the field's own controls and the sound mode ----
    private var soundCard: some View {
        Card("Sound") {
            HStack(spacing: 8) {
                Button("Centre") { f.centrePuck() }.buttonStyle(Capsule_()).keyboardShortcut("0", modifiers: .command).hoverEffect(.lift)
                Button(f.spatialOn ? "All around" : "Spatial") { f.setSpatial(!f.spatialOn) }
                    .buttonStyle(Capsule_(filled: f.spatialOn)).keyboardShortcut("s", modifiers: .command).hoverEffect(.lift)
                Button(m.state.active?.mute == true ? "Muted" : "Mute") { m.mute() }
                    .buttonStyle(Capsule_(filled: m.state.active?.mute == true)).keyboardShortcut("m", modifiers: .command).hoverEffect(.lift)
            }
            Text(f.spatialOn ? "Spatial · \(f.widthDeg)° wide, aimed \(f.aimDeg)° · pinch to widen, twist to aim"
                             : "All around · drag the puck, double-tap to centre")
                .font(.caption).foregroundStyle(Theme.muted)
            if f.spatialOn {
                HStack {
                    Text("Spread").font(.caption).foregroundStyle(Theme.muted)
                    Slider(value: $spread, in: 50...240, step: 2, onEditingChanged: { editing in
                        spreadDrag = editing; f.setWidth(halfDeg: spread / 2, final: !editing)
                    })
                    .tint(.white)
                    .onChange(of: spread) { v in if spreadDrag { f.setWidth(halfDeg: v / 2, final: false) } }
                    Text("\(Int(spread))°").font(.caption.monospacedDigit()).frame(width: 44, alignment: .trailing)
                }
                .onAppear { spread = Double(f.widthDeg) * 2 }
                .onChange(of: f.widthDeg) { w in if !spreadDrag { spread = Double(w) * 2 } }
            }
            HStack(spacing: 8) {
                Toggle("Height", isOn: Binding(get: { m.state.rep?.height ?? false }, set: { m.spatialFlag("height", $0) })).font(.caption).tint(Theme.live)
                Toggle("Beyond stereo", isOn: Binding(get: { m.state.rep?.beyond ?? false }, set: { m.spatialFlag("beyond", $0) })).font(.caption).tint(Theme.live)
            }
            HStack(spacing: 8) {
                Button("Syng") { m.soundMode("syng") }.buttonStyle(Capsule_(filled: m.state.soundMode != "direct")).hoverEffect(.lift)
                Button("Direct") { m.soundMode("direct") }.buttonStyle(Capsule_(filled: m.state.soundMode == "direct")).hoverEffect(.lift)
            }
            Text(m.state.soundMode == "direct" ? "Direct: every speaker plays the whole mix through all three drivers at the same level."
                                              : "Syng steers the sound across the speakers and their drivers.")
                .font(.caption).foregroundStyle(Theme.muted)
        }
    }

    private var sourcesCard: some View {
        Card("Sources") {
            if m.state.sources.isEmpty {
                Text("No music source is up yet.").font(.caption).foregroundStyle(Theme.muted)
            } else {
                FlowRow(items: m.state.sources) { s in
                    Button(s.displayName) { m.select(s.id) }.buttonStyle(Capsule_(filled: s.active)).hoverEffect(.lift)
                }
            }
        }
    }

    private var scenesCard: some View {
        Card("Scenes") {
            FlowRow(items: m.scenes) { n in
                Button(n) { m.applyScene(n) }.buttonStyle(Capsule_(filled: m.lastScene == n)).hoverEffect(.lift)
            }
        }
    }

    private var speakersCard: some View {
        Card("Speakers") {
            ForEach(room.cells) { c in
                HStack(spacing: 12) {
                    Puck(size: 34, ring: c.role == "primary" ? .white : nil)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(c.title).font(.subheadline.weight(.semibold))
                        Text("\(c.shortSerial) · \(c.ip)\(c.role == "primary" ? " · primary" : "")\(c.calibrated == false ? " · uncalibrated" : "")")
                            .font(.caption2).foregroundStyle(Theme.muted)
                    }
                    Spacer()
                    Button("Ping") { m.ping(c.serial) }.buttonStyle(Capsule_()).frame(width: 74).hoverEffect(.lift)
                }
                .contentShape(Rectangle())
                .contextMenu {
                    Button { renaming = c; newName = c.name ?? "" } label: { Label("Rename…", systemImage: "pencil") }
                    Button { m.ping(c.serial) } label: { Label("Ping", systemImage: "wave.3.right") }
                }
            }
            Button("Ping all") { m.ping(nil) }.buttonStyle(Capsule_()).hoverEffect(.lift)
            Text("Touch and hold (or right-click) a speaker to name it.").font(.caption2).foregroundStyle(Theme.muted)
        }
    }
}

// MARK: - setup, two panes

struct PadSetupView: View {
    let room: Room
    @State private var section: SetupSection? = .room
    var body: some View {
        HStack(spacing: 0) {
            ScrollView {
                VStack(spacing: 6) {
                    ForEach(SetupSection.allCases) { sec in
                        Button { section = sec } label: {
                            HStack(spacing: 12) {
                                Image(systemName: sec.icon).font(.body).frame(width: 24)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(sec.title).font(.subheadline.weight(.semibold))
                                    Text(sec.subtitle).font(.caption2).foregroundStyle(Theme.muted).lineLimit(1)
                                }
                                Spacer(minLength: 0)
                            }
                            .foregroundStyle(.white)
                            .padding(.horizontal, 12).padding(.vertical, 10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(section == sec ? Theme.surface2 : Color.clear, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .hoverEffect(.highlight)
                    }
                    Text(room.agent.map { "Talking to \($0.ip) · port 8080" } ?? "No agent")
                        .font(.caption2).foregroundStyle(Theme.muted).padding(.top, 8)
                }
                .padding(12)
            }
            .frame(width: 300)
            .background(Theme.surface.opacity(0.35))
            Divider().overlay(Theme.surface2)
            Group {
                // the setup pages never push further screens, so they sit in the room's own navigation stack
                if let sec = section {
                    sec.page(room).id(sec)                // a fresh page (and its loader) per section
                } else {
                    Text("Pick a section").foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity, maxHeight: .infinity).background(Theme.bg)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Theme.bg.ignoresSafeArea())
    }
}
