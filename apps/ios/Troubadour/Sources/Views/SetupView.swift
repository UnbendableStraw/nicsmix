import SwiftUI
import UniformTypeIdentifiers

// MARK: - a tiny loader for one agent endpoint

/// Loads one `GET /api/...`, refreshes it on a timer, and posts back to the agent. Every setup
/// screen is this plus a layout; the agent's JSON is read straight out rather than modelled twice.
@MainActor
final class Endpoint: ObservableObject {
    @Published var data: [String: Any] = [:]
    @Published var error: String?
    @Published var busy = false
    @Published var note: String?
    let agent: Agent?
    private let path: String
    private let every: TimeInterval
    private let timeout: TimeInterval
    private var timer: Timer?

    init(_ agent: Agent?, _ path: String, every: TimeInterval = 6, timeout: TimeInterval = 8) {
        self.agent = agent; self.path = path; self.every = every; self.timeout = timeout
    }
    func start() {
        reload()
        guard every > 0 else { return }
        timer = Timer.scheduledTimer(withTimeInterval: every, repeats: true) { [weak self] _ in
            Task { await self?.load() }
        }
    }
    func stop() { timer?.invalidate(); timer = nil }
    func reload() { Task { await load() } }

    func load() async {
        guard let a = agent else { return }
        do {
            data = try await a.get(path, timeout: timeout)
            if error != nil { error = nil }
        } catch {
            self.error = error.localizedDescription
        }
    }
    @discardableResult
    func post(_ p: String, _ body: [String: Any] = [:], timeout: TimeInterval = 25) async -> Bool {
        guard let a = agent else { return false }
        busy = true
        var ok = false
        do { _ = try await a.post(p, body, timeout: timeout); ok = true; error = nil }
        catch { self.error = error.localizedDescription }
        busy = false
        await load()
        return ok
    }
    func fire(_ p: String, _ body: [String: Any] = [:], note: String? = nil, timeout: TimeInterval = 25) {
        Task {
            let ok = await post(p, body, timeout: timeout)
            if ok, let n = note { self.note = n; try? await Task.sleep(nanoseconds: 2_500_000_000); if self.note == n { self.note = nil } }
        }
    }
}

extension Dictionary where Key == String, Value == Any {
    func str(_ k: String) -> String? { self[k] as? String }
    func num(_ k: String) -> Double? { self[k] as? Double }
    func int(_ k: String) -> Int? { (self[k] as? Int) ?? (self[k] as? Double).map { Int($0) } }
    func flag(_ k: String) -> Bool { (self[k] as? Bool) ?? false }
    func obj(_ k: String) -> [String: Any] { (self[k] as? [String: Any]) ?? [:] }
    func list(_ k: String) -> [[String: Any]] { (self[k] as? [[String: Any]]) ?? [] }
    func strings(_ k: String) -> [String] { (self[k] as? [String]) ?? [] }
}

// MARK: - the setup index

/// The Setup sections, shared by the phone's index and the iPad's two-pane Setup.
enum SetupSection: String, CaseIterable, Identifiable {
    case room, layout, leds, sound, stream, airplay, scenes, network, firmware, system, page
    var id: String { rawValue }
    var title: String {
        switch self {
        case .room: return "Room"
        case .layout: return "Layout & Soundcheck"
        case .leds: return "LEDs"
        case .sound: return "Sound & controls"
        case .stream: return "Group stream"
        case .airplay: return "AirPlay & Atmos"
        case .scenes: return "Scenes"
        case .network: return "Network"
        case .firmware: return "Firmware"
        case .system: return "System"
        case .page: return "The speaker's own page"
        }
    }
    var subtitle: String {
        switch self {
        case .room: return "Name, members, who is primary"
        case .layout: return "Where the speakers are, and measuring it"
        case .leds: return "Reactive modes, colour, the whole room or one speaker"
        case .sound: return "Tone, the volume ceiling, the control ring"
        case .stream: return "The speakers' shared buffer, and when the stream drops out"
        case .airplay: return "What's really playing, height channels, quick recovery"
        case .scenes: return "Save the room, apply it, schedule it"
        case .network: return "Wi-Fi, radios, cell-to-cell link"
        case .firmware: return "What is flashed, install, roll back"
        case .system: return "Live fleet, stats, sleep, housekeeping"
        case .page: return "Everything, served by the cell"
        }
    }
    var icon: String {
        switch self {
        case .room: return "rectangle.3.group"
        case .layout: return "circle.grid.cross"
        case .leds: return "lightbulb"
        case .sound: return "dial.medium"
        case .stream: return "waveform.path.ecg"
        case .airplay: return "airplayaudio"
        case .scenes: return "theatermasks"
        case .network: return "wifi"
        case .firmware: return "arrow.down.circle"
        case .system: return "gauge"
        case .page: return "safari"
        }
    }
    @ViewBuilder func page(_ room: Room) -> some View {
        switch self {
        case .room: RoomSetupView(room: room)
        case .layout: LayoutView(room: room)
        case .leds: LEDView(room: room)
        case .sound: SoundSettingsView(room: room)
        case .stream: StreamView(room: room)
        case .airplay: AirPlayView(room: room)
        case .scenes: ScenesView(room: room)
        case .network: NetworkView(room: room)
        case .firmware: FirmwareView(room: room)
        case .system: SystemView(room: room)
        case .page:
            AgentPage(agent: room.agent, path: "/setup")
                .ignoresSafeArea(edges: .bottom)
                .navigationTitle("Setup page")
                .navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct SetupView: View {
    let room: Room
    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                ForEach(SetupSection.allCases) { sec in
                    SetupLink(sec.title, sec.subtitle, sec.icon) { sec.page(room) }
                }
                Text(room.agent.map { "Talking to \($0.ip) · port 8080" } ?? "No agent")
                    .font(.caption2).foregroundStyle(Theme.muted).padding(.top, 4)
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
    }
}

struct SetupLink<D: View>: View {
    let title: String, subtitle: String, icon: String
    let destination: () -> D
    init(_ title: String, _ subtitle: String, _ icon: String, @ViewBuilder destination: @escaping () -> D) {
        self.title = title; self.subtitle = subtitle; self.icon = icon; self.destination = destination
    }
    var body: some View {
        NavigationLink { destination() } label: {
            HStack(spacing: 14) {
                Image(systemName: icon).font(.title3).frame(width: 28).foregroundStyle(.white)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.white)
                    Text(subtitle).font(.caption).foregroundStyle(Theme.muted)
                }
                Spacer()
                Image(systemName: "chevron.right").font(.caption).foregroundStyle(Theme.muted)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

/// Shared chrome: dark page, an error line, a "working" line.
struct SetupPage<Content: View>: View {
    let title: String
    @ObservedObject var e: Endpoint
    let content: () -> Content
    init(title: String, e: Endpoint, @ViewBuilder content: @escaping () -> Content) {
        self.title = title; self.e = e; self.content = content
    }
    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                content()
                if let n = e.note { Text(n).font(.caption).foregroundStyle(Theme.live) }
                if let err = e.error { Text(err).font(.caption).foregroundStyle(Theme.bad) }
            }
            .padding(16)
        }
        .background(Theme.bg.ignoresSafeArea())
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if e.busy { ProgressView().tint(.white) } else {
                    Button { e.reload() } label: { Image(systemName: "arrow.clockwise") }
                }
            }
        }
        .onAppear { e.start() }
        .onDisappear { e.stop() }
    }
}

// MARK: - room

struct RoomSetupView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @StateObject private var me: Endpoint          // /api/whoami — this speaker's own view of itself
    @EnvironmentObject var fleet: Fleet
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmLeave = false
    @State private var creating = false
    @State private var createName = ""
    @State private var joinTarget: Room?
    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/room", every: 8))
        _me = StateObject(wrappedValue: Endpoint(room.agent, "/api/whoami", every: 20))
    }

    private var cells: [[String: Any]] { e.data.list("cells") }
    private var isPrimary: Bool { e.data.flag("i_am_primary") }
    private var roomName: String { e.data.obj("room_state").str("room_name") ?? room.name }

    var body: some View {
        SetupPage(title: "Room", e: e) {
            Card("This room") {
                HStack {
                    Text(roomName).font(.title3.weight(.bold))
                    Spacer()
                    Button("Rename") { newName = roomName; renaming = true }.buttonStyle(Chip())
                }
                Text(isPrimary ? "This speaker is the primary — it owns the room document and streams to the others."
                               : "A secondary. Membership changes are relayed to the primary.")
                    .font(.caption).foregroundStyle(Theme.muted)
                if !me.data.isEmpty { badges }
            }
            Card("Speakers in the room") {
                ForEach(cells.indices, id: \.self) { i in
                    memberRow(cells[i])
                }
            }
            if !nearby.isEmpty {
                Card("Nearby, in another room") {
                    ForEach(nearby, id: \.serial) { c in
                        HStack(spacing: 12) {
                            Puck(size: 32)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(c.title).font(.subheadline.weight(.semibold))
                                Text(c.ip + (c.agent == nil ? " · stock firmware" : "")).font(.caption).foregroundStyle(Theme.muted)
                            }
                            Spacer()
                            Button("Ping") { e.fire("/api/ping", ["serial": c.serial]) }.buttonStyle(Chip())
                            Button("Add") { add(c) }.buttonStyle(Chip(filled: true)).disabled(c.agent == nil)
                        }
                        .padding(.vertical, 3)
                    }
                    Text("Adding asks that speaker to join this room; it keeps playing once the primary admits it. A speaker on stock firmware has no agent to ask.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
            }
            if !otherRooms.isEmpty {
                Card("Move this speaker to another room") {
                    ForEach(otherRooms) { r in
                        HStack(spacing: 12) {
                            HStack(spacing: -8) { ForEach(r.cells.prefix(4)) { _ in Puck(size: 24) } }
                            VStack(alignment: .leading, spacing: 2) {
                                Text(r.name).font(.subheadline.weight(.semibold))
                                Text("\(r.cells.count) speaker\(r.cells.count == 1 ? "" : "s") · primary \(r.primaryIP ?? "?")").font(.caption).foregroundStyle(Theme.muted)
                            }
                            Spacer()
                            Button("Join") { joinTarget = r }.buttonStyle(Chip()).disabled(r.primaryIP == nil)
                        }
                        .padding(.vertical, 3)
                    }
                    Text("The speaker you are talking to (\(room.agent?.ip ?? "?")) leaves this room and joins that one's primary.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
            }
            Card("Careful") {
                HStack {
                    Button("Leave this room") { confirmLeave = true }.buttonStyle(Capsule_())
                    Button("New room…") { createName = ""; creating = true }.buttonStyle(Capsule_())
                }
                Text("Leave: this speaker goes back to a room of its own; the others stay together. New room: the same, with a name of your choosing.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .onAppear { me.start() }
        .onDisappear { me.stop() }
        .alert("Room name", isPresented: $renaming) {
            TextField("Ball Room", text: $newName)
            Button("Save") { e.fire("/api/room/rename", ["name": newName], note: "Renamed") }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Leave the room?", isPresented: $confirmLeave) {
            Button("Leave", role: .destructive) { e.fire("/api/room/leave", [:], note: "Left the room") }
            Button("Cancel", role: .cancel) {}
        }
        .alert("New room with this speaker", isPresented: $creating) {
            TextField("Study", text: $createName)
            Button("Create") { if !createName.isEmpty { e.fire("/api/room/create", ["name": createName], note: "Created \(createName)", timeout: 60); refreshFleet() } }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This speaker leaves its room and starts a new one.") }
        .alert("Join \(joinTarget?.name ?? "")?", isPresented: Binding(get: { joinTarget != nil }, set: { if !$0 { joinTarget = nil } })) {
            Button("Join", role: .destructive) { if let ip = joinTarget?.primaryIP { e.fire("/api/room/join", ["ip": ip], note: "Joining…", timeout: 60); refreshFleet() } }
            Button("Cancel", role: .cancel) {}
        } message: { Text("This speaker leaves \(roomName) and joins that room. It keeps playing once the primary admits it.") }
    }

    /// whoami, the way the setup page's hero shows it: role · calibrated · Wi-Fi · cloud · SyFi · agent · addresses.
    private var badges: some View {
        let d = me.data
        var bits: [String] = []
        if let r = d.str("role") { bits.append(r) }
        if let c = d["calibrated"] as? Bool { bits.append(c ? "calibrated" : "uncalibrated") }
        if let w = d.obj("wifi").str("ssid"), !w.isEmpty { bits.append("Wi-Fi \(w)") }
        if let cb = d["cloud_blocked"] as? Bool { bits.append(cb ? "cloud blocked" : "cloud open") }
        if let sg = d["syfiGood"] as? Bool { bits.append(sg ? "SyFi good" : "SyFi down") }
        if let a = d["agent"] { bits.append("agent \(a)") }
        let ips = d.list("ips").compactMap { $0.str("ip") ?? $0.str("addr") }
        if !ips.isEmpty { bits.append(ips.joined(separator: " · ")) }
        if let mem = d["members"] as? [Any], !mem.isEmpty { bits.append("\(mem.count) members") }
        return Text(bits.joined(separator: " · ")).font(.caption2).foregroundStyle(Theme.muted)
    }

    private func memberRow(_ c: [String: Any]) -> some View {
        let serial = c.str("serial") ?? ""
        let isPri = (c.str("role") ?? "").lowercased() == "primary"
        return HStack(spacing: 12) {
            Puck(size: 34, ring: isPri ? .white : nil)
            VStack(alignment: .leading, spacing: 2) {
                Text(c.str("name") ?? String(serial.suffix(5))).font(.subheadline.weight(.semibold))
                Text([String(serial.suffix(5)), c.str("ip") ?? "", isPri ? "primary" : "secondary",
                      c.flag("calibrated") ? "" : "uncalibrated", c["online"] as? Bool == false ? "offline" : ""].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Spacer()
            Menu {
                if !isPri {
                    Button("Make primary") { e.fire("/api/room/pin", ["serial": serial, "role": "primary"], note: "Primary pinned") }
                    Button("Remove from room", role: .destructive) { e.fire("/api/room/remove", ["serial": serial], note: "Removed") }
                }
                Button("Identify") { e.fire("/api/ping", ["serial": serial]) }
                if let ip = c.str("ip"), let u = URL(string: "http://\(ip):8080/") { Link("Open its page", destination: u) }
            } label: {
                Image(systemName: "ellipsis.circle").foregroundStyle(Theme.muted)
            }
        }
        .padding(.vertical, 3)
    }

    /// Cells the app can see that are not in this room (stock-firmware ones included, for the record).
    private var nearby: [Cell] {
        let mine = Set(cells.compactMap { $0.str("serial") })
        return fleet.rooms.flatMap(\.cells).filter { !mine.contains($0.serial) }
    }
    private var otherRooms: [Room] { fleet.rooms.filter { $0.id != room.id && $0.id != "?" } }
    private func add(_ c: Cell) {
        guard let primary = room.primaryIP ?? room.agent?.ip else { return }   // the room's own address for the join; the call itself goes over the wire
        Task {
            e.busy = true
            do { _ = try await c.conn.post("/api/room/join", ["ip": primary], timeout: 30); e.note = "\(c.title) joined" }
            catch { e.error = error.localizedDescription }
            e.busy = false
            await e.load()
            await Fleet.shared.refresh(force: true)
        }
    }
    private func refreshFleet() { Task { try? await Task.sleep(nanoseconds: 4_000_000_000); await Fleet.shared.refresh(force: true) } }
}

// MARK: - LEDs

struct LEDView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @State private var wholeRoom = true
    @State private var colour = Color(red: 0.2, green: 0.7, blue: 1.0)
    @State private var white: Double = 0
    @State private var beatSens: Double = 0.10
    @State private var floor: Double = 0.05
    @State private var silentFade: Double = 12
    @State private var beatFade: Double = 0.5
    @State private var tuneLoaded = false
    @State private var editing: String?
    @State private var visionJSON = ""
    init(room: Room) { self.room = room; _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/led", every: 6)) }

    private var modes: [String] { e.data.strings("reactiveModes") }
    private var reactive: Bool { e.data.flag("reactive") }
    private var mode: String? { reactive ? e.data.str("mode") : nil }
    /// Every LED post says what it applies to: the room, or this one speaker (the web page's `ledScope`).
    private var scope: [String: Any] { ["scope": wholeRoom ? "group" : "this"] }
    private var visions: [String] { e.data.strings("visions") }

    var body: some View {
        SetupPage(title: "LEDs", e: e) {
            if room.cells.count > 1 {
                Card("Applies to") {
                    HStack {
                        Button("Whole room") { wholeRoom = true }.buttonStyle(Capsule_(filled: wholeRoom))
                        Button("This speaker") { wholeRoom = false }.buttonStyle(Capsule_(filled: !wholeRoom))
                    }
                }
            }
            Card("Reactive") {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 8)], spacing: 8) {
                    Button("Manual") { e.fire("/api/led/reactive", scope.merging(["mode": "manual"]) { a, _ in a }) }
                        .buttonStyle(Capsule_(filled: mode == nil))
                    ForEach(modes, id: \.self) { m in
                        Button(m.capitalized) {
                            e.fire("/api/led/reactive", scope.merging(["mode": m]) { a, _ in a })
                        }
                        .buttonStyle(Capsule_(filled: mode == m))
                    }
                }
                Text(reactive ? "The LEDs follow what is playing (Room listens through the mics). The primary feeds the others, so a mode set room-wide stays in step. Manual turns this off and hands the LEDs back to you."
                              : "Manual: the LEDs hold the colour you set below, or run one of the cell's own animations.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            if reactive {
                Card("Reactive tuning") {
                    tune("Beat sensitivity", $beatSens, 0.02...0.40, 0.01, fmt: "%.2f", key: "beatSens")
                    tune("Floor brightness", $floor, 0...0.50, 0.01, fmt: "%.2f", key: "floor")
                    tune("Fade to black after silence", $silentFade, 0.2...8.0, 0.1, fmt: "%.1f s", key: "silentFade")
                    tune("Beat fade", $beatFade, 0.05...2.0, 0.05, fmt: "%.2f s", key: "beatFade")
                    Text("Each value is sent when you let go; the LED daemon restarts to take it, so the lights blink once.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
                if room.cells.count > 1 {
                    Card("Palette") {
                        HStack {
                            Button("Each its own") { e.fire("/api/led/palette", scope.merging(["palette": "free"]) { a, _ in a }) }
                                .buttonStyle(Capsule_(filled: (e.data.str("palette") ?? "free") == "free"))
                            Button("Match the room") { e.fire("/api/led/palette", scope.merging(["palette": "shared"]) { a, _ in a }) }
                                .buttonStyle(Capsule_(filled: e.data.str("palette") == "shared"))
                        }
                    }
                }
            } else {
                Card("Colour") {
                    ColorPicker("Pick a colour", selection: $colour, supportsOpacity: false).font(.subheadline)
                    HStack {
                        Text("White").font(.subheadline).foregroundStyle(Theme.muted)
                        Slider(value: $white, in: 0...1).tint(.white)
                        Text(String(format: "%.0f%%", white * 100)).font(.caption.monospacedDigit()).frame(width: 44, alignment: .trailing)
                    }
                    Button("Set this colour") { setColour() }.buttonStyle(Capsule_(filled: true))
                }
                if !visions.isEmpty {
                    Card("Animations on the cell") {
                        FlowRow(items: visions) { v in
                            Button(v) { e.fire("/api/led/anim", scope.merging(["action": "play", "name": v]) { a, _ in a }, note: "Playing \(v)") }
                                .buttonStyle(Chip())
                        }
                        HStack {
                            Button("Pause") { e.fire("/api/led/anim", scope.merging(["action": "pause"]) { a, _ in a }) }.buttonStyle(Chip())
                            Button("Resume") { e.fire("/api/led/anim", scope.merging(["action": "resume"]) { a, _ in a }) }.buttonStyle(Chip())
                            Button("Clear") { e.fire("/api/led/anim", scope.merging(["action": "clear"]) { a, _ in a }, note: "Cleared") }.buttonStyle(Chip())
                            Spacer()
                            Menu {
                                ForEach(visions, id: \.self) { v in Button(v) { openVision(v) } }
                            } label: { Text("Edit…").font(.footnote.weight(.semibold)).foregroundStyle(.white) }
                        }
                        Text("Syng's own ledmanager animations (JSON \u{201C}visions\u{201D} on the cell). Edit one to change its colours or timing; it is saved on the speaker.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                }
            }
        }
        .onReceive(e.$data.dropFirst()) { d in
            guard !tuneLoaded else { return }
            let t = d.obj("tune")
            if !t.isEmpty {
                beatSens = t.num("beatSens") ?? beatSens; floor = t.num("floor") ?? floor
                silentFade = t.num("silentFade") ?? silentFade; beatFade = t.num("beatFade") ?? beatFade
                tuneLoaded = true
            }
        }
        .sheet(item: Binding(get: { editing.map { Named(id: $0) } }, set: { editing = $0?.id })) { item in
            visionEditor(item.id)
        }
    }
    private struct Named: Identifiable { let id: String }

    private func tune(_ label: String, _ v: Binding<Double>, _ range: ClosedRange<Double>, _ step: Double, fmt: String, key: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(label).font(.subheadline).foregroundStyle(Theme.muted)
                Spacer()
                Text(String(format: fmt, v.wrappedValue)).font(.caption.monospacedDigit())
            }
            Slider(value: v, in: range, step: step, onEditingChanged: { if !$0 { sendTune(key, v.wrappedValue) } }).tint(.white)
        }
    }
    private func sendTune(_ key: String, _ v: Double) {
        e.fire("/api/led/tune", scope.merging([key: String(format: "%.2f", v)]) { a, _ in a }, note: "Tuning sent", timeout: 30)
    }

    private func setColour() {
        let ui = UIColor(colour)
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        ui.getRed(&r, green: &g, blue: &b, alpha: &a)
        let body: [String: Any] = ["r": Double(r), "g": Double(g), "b": Double(b), "w": white]
        e.fire("/api/led/color", scope.merging(body) { a, _ in a }, note: "Colour set")
    }

    private func openVision(_ name: String) {
        visionJSON = "loading…"; editing = name
        Task {
            if let d = try? await room.agent?.get("/api/vision?name=\(name.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? name)", timeout: 10) {
                visionJSON = d.str("content") ?? ""
            } else { visionJSON = "" }
        }
    }
    private func visionEditor(_ name: String) -> some View {
        NavigationStack {
            VStack(spacing: 10) {
                TextEditor(text: $visionJSON)
                    .font(.system(.caption, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .background(Theme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                Text(jsonProblem ?? "Valid JSON").font(.caption).foregroundStyle(jsonProblem == nil ? Theme.live : Theme.bad)
            }
            .padding(16)
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) { Button("Cancel") { editing = nil } }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Save") { e.fire("/api/vision", ["name": name, "content": visionJSON], note: "Saved \(name)"); editing = nil }
                        .disabled(jsonProblem != nil)
                }
            }
        }
    }
    private var jsonProblem: String? {
        guard let d = visionJSON.data(using: .utf8) else { return "not text" }
        do { _ = try JSONSerialization.jsonObject(with: d); return nil } catch { return "Not valid JSON: \(error.localizedDescription)" }
    }
}

// MARK: - network

struct NetworkView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @StateObject private var wifi: Endpoint
    @StateObject private var link: Endpoint
    @State private var joining: String?
    @State private var psk = ""
    @State private var manualJoin = false
    @State private var manualSSID = ""
    @State private var confirmTechOff: String?
    @State private var confirmLink: String?
    @State private var staticFor: String?           // the connman service being edited
    @State private var staticMethod = "dhcp"
    @State private var staticAddr = ""
    @State private var staticMask = "255.255.255.0"
    @State private var staticGW = ""
    @State private var staticDNS = ""
    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/net", every: 10))
        _wifi = StateObject(wrappedValue: Endpoint(room.agent, "/api/wifi", every: 10))
        _link = StateObject(wrappedValue: Endpoint(room.agent, "/api/link", every: 6))
    }

    private var tech: [String: Any] { e.data.obj("technologies") }
    private var linkBusy: String? {                   // the agent reports the target mode while switching
        if let b = link.data["busy"] as? String, !b.isEmpty { return b }
        return link.data.flag("busy") ? "…" : nil
    }

    var body: some View {
        SetupPage(title: "Network", e: e) {
            Card("Wi-Fi") {
                HStack {
                    Text(connectedSSID ?? "Not connected").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(wifiIP ?? "").font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                }
                ForEach(wifiServices.indices, id: \.self) { i in
                    let s = wifiServices[i]
                    let name = s.str("name") ?? ""
                    HStack {
                        Image(systemName: s.flag("connected") ? "wifi.circle.fill" : "wifi")
                            .foregroundStyle(s.flag("connected") ? Color.white : Theme.muted)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(name.isEmpty ? "(hidden)" : name).font(.subheadline).lineLimit(1)
                            Text([s.str("security"), s.flag("connected") ? "connected" : nil, s.flag("autoconnect") ? "saved" : nil]
                                    .compactMap { $0 }.joined(separator: " · ")).font(.caption2).foregroundStyle(Theme.muted)
                        }
                        Spacer()
                        if s.flag("autoconnect") || s.flag("connected") {
                            Button("Forget") { wifi.fire("/api/wifi/forget", ["ssid": name], note: "Forgot \(name)") }.buttonStyle(Chip())
                        }
                        if !s.flag("connected") {
                            Button("Join") { joining = name; psk = "" }.buttonStyle(Chip())
                        }
                    }
                    .padding(.vertical, 2)
                }
                HStack {
                    Button("Scan again") { Task { _ = try? await room.agent?.get("/api/wifi/scan", timeout: 25); await wifi.load() } }
                        .buttonStyle(Capsule_())
                    Button("Hidden network…") { manualSSID = ""; psk = ""; manualJoin = true }.buttonStyle(Capsule_())
                }
            }
            Card("Radios") {
                ForEach(["wifi", "ethernet"], id: \.self) { t in
                    let st = tech.obj(t)
                    HStack {
                        Text(t == "wifi" ? "Wi-Fi" : "Ethernet").font(.subheadline.weight(.semibold))
                        Text(st.isEmpty ? "" : (st.flag("powered") ? (st.flag("connected") ? "on · connected" : "on") : "off"))
                            .font(.caption).foregroundStyle(Theme.muted)
                        Spacer()
                        Button(st.flag("powered") ? "Turn off" : "Turn on") {
                            if st.flag("powered") { confirmTechOff = t } else { e.fire("/api/net/power", ["tech": t, "on": true], note: "\(t) on") }
                        }
                        .buttonStyle(Chip())
                    }
                }
                Text("Turning Wi-Fi off keeps this speaker out of any room: the firmware needs Wi-Fi for the room link (SyFi) and goes into \u{201C}Wi-Fi repair\u{201D} without it. Fine for a standalone wired speaker. The agent refuses to switch off the only link it is reached on.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Addresses") {
                ForEach(e.data.list("services").indices, id: \.self) { i in serviceRow(e.data.list("services")[i]) }
                ForEach(e.data.list("interfaces").indices, id: \.self) { i in
                    let n = e.data.list("interfaces")[i]
                    HStack {
                        Text(n.str("ifname") ?? "").font(.caption.weight(.semibold))
                        Spacer()
                        Text([n.str("ip").map { "\($0)/\(n.int("plen") ?? 0)" }, n.str("mac")].compactMap { $0 }.joined(separator: " · "))
                            .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
                    }
                }
                if let dr = e.data.str("default_route") { Text("Default route: \(dr)").font(.caption2).foregroundStyle(Theme.muted) }
                Text("A static address set here is kept by connman for that network; switching back to DHCP clears it.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Cell-to-cell link") {
                HStack {
                    Button("SyFi (stock)") { confirmLink = "syfi" }
                        .buttonStyle(Capsule_(filled: (link.data.str("mode") ?? "syfi") == "syfi")).disabled(linkBusy != nil)
                    Button("Wired") { confirmLink = "wired" }
                        .buttonStyle(Capsule_(filled: link.data.str("mode") == "wired")).disabled(linkBusy != nil)
                }
                if let b = linkBusy { Text("Switching to \(b)…").font(.caption).foregroundStyle(Theme.warn) }
                if link.data["members"] != nil {
                    ForEach(link.data.list("members").indices, id: \.self) { i in
                        linkRow(link.data.list("members")[i])
                    }
                } else {
                    Text(standaloneLine).font(.caption).foregroundStyle(Theme.muted)
                }
                Text("SyFi: the primary runs a hidden Wi-Fi network; audio and a hardware clock from the Wi-Fi chip go over it. Wired: every speaker on Ethernet; audio goes over the wire and the clocks are kept together with PTP (the primary is the grandmaster), SyFi stays up idle. Needs a wired address on every member; switching restarts each speaker's audio for a few seconds.")
                    .font(.caption).foregroundStyle(Theme.muted)
                if !link.data.strings("log").isEmpty {
                    Text(link.data.strings("log").suffix(8).joined(separator: "\n"))
                        .font(.caption2.monospaced()).foregroundStyle(Theme.muted).frame(maxWidth: .infinity, alignment: .leading)
                }
                if let err = link.error { Text(err).font(.caption).foregroundStyle(Theme.bad) }
            }
        }
        .onAppear { wifi.start(); link.start() }
        .onDisappear { wifi.stop(); link.stop() }
        .alert("Wi-Fi password", isPresented: Binding(get: { joining != nil }, set: { if !$0 { joining = nil } })) {
            SecureField("Password (empty for an open network)", text: $psk)
            Button("Join") { if let s = joining { wifi.fire("/api/wifi/connect", ["ssid": s, "psk": pskValue], note: "Joining \(s)…", timeout: 60) } }
            Button("Cancel", role: .cancel) {}
        } message: { Text(joining ?? "") }
        .alert("Join a hidden network", isPresented: $manualJoin) {
            TextField("Network name", text: $manualSSID)
            SecureField("Password", text: $psk)
            Button("Join") { if !manualSSID.isEmpty { wifi.fire("/api/wifi/connect", ["ssid": manualSSID, "psk": pskValue, "hidden": true], note: "Joining \(manualSSID)…", timeout: 60) } }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Turn \(confirmTechOff ?? "") off on this speaker?", isPresented: Binding(get: { confirmTechOff != nil }, set: { if !$0 { confirmTechOff = nil } })) {
            Button("Turn off", role: .destructive) { if let t = confirmTechOff { e.fire("/api/net/power", ["tech": t, "on": false], note: "\(t) off") } }
            Button("Cancel", role: .cancel) {}
        }
        .alert(confirmLink == "wired" ? "Switch the room to the wired link?" : "Switch the room back to SyFi?", isPresented: Binding(get: { confirmLink != nil }, set: { if !$0 { confirmLink = nil } })) {
            Button("Switch", role: .destructive) { if let m = confirmLink { link.fire("/api/link", ["mode": m], note: "Switching to \(m)…", timeout: 60) } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirmLink == "wired" ? "Every speaker needs an Ethernet address; each one restarts its audio for a few seconds." : "Each speaker restarts its audio for a few seconds.")
        }
        .sheet(item: Binding(get: { staticFor.map { Named(id: $0) } }, set: { staticFor = $0?.id })) { item in staticSheet(item.id) }
    }
    private struct Named: Identifiable { let id: String }

    private var wifiServices: [[String: Any]] { wifi.data.list("services") }
    /// An open network gets `psk: null`, as the web page sends it.
    private var pskValue: Any { psk.isEmpty ? (NSNull() as Any) : (psk as Any) }
    private var connectedSSID: String? { wifiServices.first { $0.flag("connected") }?.str("name") ?? wifi.data.str("ssid") }
    private var wifiIP: String? {
        if let ip = wifi.data.str("ip") { return ip }
        return e.data.list("interfaces").first { ($0.str("ifname") ?? "").hasPrefix("w") && $0.str("ip") != nil }?.str("ip")
    }
    private var standaloneLine: String {
        let d = link.data
        var bits: [String] = []
        if let w = d["wired"] as? [String: Any] { bits.append("wired \(w.str("ifname") ?? "") \(w.str("ip") ?? "")") } else { bits.append("no wire on this speaker") }
        bits.append("timesync \(d.str("timesync") ?? "?") on \(d.str("timesync_iface") ?? "?")")
        let ptp = d.obj("ptp")
        if ptp.flag("active") { bits.append("PTP \(ptp.str("state") ?? "?")" + (ptp.num("offset_ns").map { String(format: " offset %.0f µs", $0 / 1000) } ?? "")) }
        return bits.joined(separator: " · ")
    }

    private func serviceRow(_ S: [String: Any]) -> some View {
        let ip = S.obj("ipv4"), cfg = (S["ipv4.configuration"] as? [String: Any]) ?? [:], eth = S.obj("ethernet")
        let manual = (cfg.str("Method") ?? ip.str("Method")) == "manual"
        let ns = S.strings("nameservers").joined(separator: " ")
        return VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(S.str("name") ?? S.str("id") ?? "").font(.subheadline.weight(.semibold)).lineLimit(1)
                Text([S.str("type"), S.str("state"), eth.str("Interface")].compactMap { $0 }.joined(separator: " · ")).font(.caption2).foregroundStyle(Theme.muted)
                Spacer()
                Button(manual ? "Static…" : "DHCP…") { openStatic(S) }.buttonStyle(Chip())
            }
            Text("\(ip.str("Address") ?? "—") / \(ip.str("Netmask") ?? "—") via \(ip.str("Gateway") ?? "—") · DNS \(ns.isEmpty ? "—" : ns) · \(manual ? "static" : "DHCP")")
                .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
        }
        .padding(.vertical, 2)
    }
    private func openStatic(_ S: [String: Any]) {
        let ip = S.obj("ipv4"), cfg = (S["ipv4.configuration"] as? [String: Any]) ?? [:]
        let manual = (cfg.str("Method") ?? ip.str("Method")) == "manual"
        staticMethod = manual ? "manual" : "dhcp"
        staticAddr = manual ? (ip.str("Address") ?? "") : ""
        staticMask = ip.str("Netmask") ?? "255.255.255.0"
        staticGW = ip.str("Gateway") ?? ""
        staticDNS = S.strings("nameservers.configuration").joined(separator: " ")
        staticFor = S.str("id")
    }
    private func staticSheet(_ service: String) -> some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    Card("Address") {
                        Picker("Method", selection: $staticMethod) { Text("DHCP").tag("dhcp"); Text("Static").tag("manual") }.pickerStyle(.segmented)
                        if staticMethod == "manual" {
                            field("Address", $staticAddr, "192.168.1.50")
                            field("Netmask", $staticMask, "255.255.255.0")
                            field("Gateway", $staticGW, "192.168.1.1")
                            field("DNS (space-separated)", $staticDNS, "192.168.1.1 1.1.1.1")
                        }
                        Text("If this is the link you are talking over, the speaker moves to the new address and the app has to find it again.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    Button("Apply") {
                        var b: [String: Any] = ["service": service, "method": staticMethod]
                        if staticMethod == "manual" { b["address"] = staticAddr; b["netmask"] = staticMask; b["gateway"] = staticGW; b["dns"] = staticDNS }
                        e.fire("/api/net/config", b, note: "Applied", timeout: 40); staticFor = nil
                    }
                    .buttonStyle(Capsule_(filled: true))
                    .disabled(staticMethod == "manual" && staticAddr.isEmpty)
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle(service).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Cancel") { staticFor = nil } } }
        }
        .presentationDetents([.medium, .large])
    }
    private func field(_ label: String, _ v: Binding<String>, _ hint: String) -> some View {
        HStack {
            Text(label).font(.caption).foregroundStyle(Theme.muted).frame(width: 90, alignment: .leading)
            TextField(hint, text: v).font(.subheadline.monospacedDigit()).keyboardType(.numbersAndPunctuation).autocorrectionDisabled()
        }
    }

    private func linkRow(_ m: [String: Any]) -> some View {
        let ptp = m.obj("ptp")
        let state = ptp.str("state") ?? (ptp.flag("running") ? "running" : "—")
        var line: String
        if let err = m.str("error") { line = "unreachable: \(err)" } else {
            line = (m.str("wired_ip").map { "wired \(m.str("wired_if") ?? "") \($0)" } ?? "no wire") + " · main path → \(m.str("syfi_ip") ?? "—")"
            if ptp.flag("active") {
                line += " · PTP \(state)"
                if state == "SLAVE" { line += String(format: " offset %.0f µs · delay %.0f µs", (ptp.num("offset_ns") ?? 0) / 1000, (ptp.num("path_delay_ns") ?? 0) / 1000) }
            }
            line += " · sync \(m.flag("time_sync_good") ? "good" : "not good")"
        }
        return VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 6) {
                Circle().fill(m.str("error") != nil ? Theme.bad : (m.flag("time_sync_good") ? Theme.live : Theme.warn)).frame(width: 7, height: 7)
                Text(m.str("name") ?? "Cell " + String((m.str("serial") ?? "").suffix(5))).font(.caption.weight(.semibold))
                Text([m.str("role"), m.flag("isMe") ? "this speaker" : nil].compactMap { $0 }.joined(separator: " · ")).font(.caption2).foregroundStyle(Theme.muted)
            }
            Text(line).font(.caption2.monospacedDigit()).foregroundStyle(m.str("error") != nil ? Theme.warn : Theme.muted)
        }
        .padding(.vertical, 2)
    }
}

// MARK: - firmware

struct FirmwareView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @State private var picking = false
    @State private var confirmInstall = false
    @State private var confirmRoom = false
    @State private var confirmRollback = false
    init(room: Room) { self.room = room; _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/firmware", every: 4)) }

    private var running: [String: Any] { e.data.obj("running") }
    private var other: [String: Any] { e.data.obj("other") }
    private var image: [String: Any] { e.data.obj("image") }
    /// `busy` is the name of the step in progress ("upload", "install"…), or false.
    private var busy: String? { if let b = e.data["busy"] as? String, !b.isEmpty { return b }; return e.data.flag("busy") ? "working" : nil }
    private var roomBusy: Bool { e.data.obj("room").flag("busy") }
    private func slot(_ p: Int?) -> String { p == 2 ? "A (p2)" : p == 3 ? "B (p3)" : "?" }
    private func agentOf(_ d: [String: Any]) -> String? {
        if let a = d.str("agent") { return a == "none" ? nil : a }
        if let a = d.int("agent") { return "b\(a)" }
        return nil
    }

    var body: some View {
        SetupPage(title: "Firmware", e: e) {
            Card("Running now") {
                row("Artifact", running.str("artifact") ?? "?")
                row("Slot", slot(running.int("part")))
                row("Agent", agentOf(running) ?? "—")
                if e.data.obj("env").str("upgrade_available") == "1" {
                    Text("Not yet committed — the bootloader will fall back to the other slot if this one does not come up again.")
                        .font(.caption).foregroundStyle(Theme.warn)
                }
                row("Other slot", other.str("artifact") ?? other.str("error") ?? "empty")
                if let a = agentOf(other) { row("Other slot's agent", a) }
                if let last = e.data["last"] as? [String: Any] {
                    row("Last install", "\(last.str("state") ?? "") → \(last.str("to") ?? "") (slot p\(last.int("slot") ?? 0))" + (last.str("running").map { " · running \($0)" } ?? ""))
                }
                if let rm = e.data["room"] as? [String: Any] {
                    let steps = rm.obj("steps").map { "\($0.key.suffix(5)): \($0.value)" }.sorted().joined(separator: " · ")
                    row("Room", "\(rm.str("action") ?? "")\(rm.flag("busy") ? " · running" : "") — \(steps.isEmpty ? "…" : steps)")
                }
            }
            if !image.isEmpty {
                Card("Image on this speaker") {
                    row("File", image.str("name") ?? "—")
                    row("Size", (image.num("size") ?? image.num("bytes")).map { String(format: "%.0f MB", $0 / 1_048_576) } ?? "—")
                    if let a = image.str("artifact") { row("Artifact", a) }
                    if let sha = image.str("sha256") { row("SHA-256", String(sha.prefix(16)) + "…") }
                    Button("Install on this speaker") { confirmInstall = true }.buttonStyle(Capsule_(filled: true)).disabled(busy != nil)
                    HStack {
                        Button("Send to all speakers") { e.fire("/api/firmware/room", ["action": "distribute"], note: "Sending to the room", timeout: 120) }
                            .buttonStyle(Capsule_()).disabled(busy != nil)
                        Button("Install across the room") { confirmRoom = true }.buttonStyle(Capsule_()).disabled(busy != nil || roomBusy)
                    }
                    Text("Send copies the image to every speaker; Install across the room then updates the secondaries one by one (each reboots), this primary last. Music stops.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
            }
            Card("Get an image") {
                Button("Upload from this device…") { picking = true }.buttonStyle(Capsule_()).disabled(busy != nil)
                Text("A `troubadour-rootfs-<artifact>.img.gz` from Files or iCloud Drive. The speaker writes it to the spare slot; nothing changes until you install.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            if busy != nil || e.data.str("error") != nil || !e.data.strings("log").isEmpty {
                Card(busy.map { "Working: \($0)" } ?? "Last run") {
                    if let p = e.data.num("progress"), busy != nil {
                        ProgressView(value: min(max(p, 0), 1)).tint(.white)
                        Text("\(Int((p * 100).rounded()))%").font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                    }
                    if let err = e.data.str("error") { Text("error: \(err)").font(.caption).foregroundStyle(Theme.bad) }
                    Text(e.data.strings("log").suffix(10).joined(separator: "\n"))
                        .font(.caption2.monospaced()).foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            Card("Careful") {
                Button("Roll back to the other slot") { confirmRollback = true }.buttonStyle(Capsule_())
                Text("Switches which partition boots next (whatever is there now). The speaker restarts.").font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .alert("Install and restart?", isPresented: $confirmInstall) {
            Button("Install", role: .destructive) { e.fire("/api/firmware/install", ["reboot": true, "carry": true], note: "Installing", timeout: 60) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The image goes into the spare slot and the speaker reboots into it — 3 to 5 minutes. Troubadour's agent and overlay are carried across; the bootloader falls back by itself if the new slot does not come up.")
        }
        .alert("Install on every speaker?", isPresented: $confirmRoom) {
            Button("Install", role: .destructive) { e.fire("/api/firmware/room", ["action": "install"], note: "Room update started", timeout: 60) }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Secondaries one by one (each reboots), this primary last. Music stops.") }
        .alert("Reboot into the other slot?", isPresented: $confirmRollback) {
            Button("Roll back", role: .destructive) { e.fire("/api/firmware/rollback", [:], note: "Rebooting into the other slot") }
            Button("Cancel", role: .cancel) {}
        }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.data], allowsMultipleSelection: false) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            upload(url)
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top) {
            Text(k).font(.caption).foregroundStyle(Theme.muted)
            Spacer()
            Text(v).font(.caption.monospacedDigit()).multilineTextAlignment(.trailing)
        }
    }

    private func upload(_ url: URL) {
        guard url.lastPathComponent.lowercased().hasSuffix(".gz") else { e.error = "Expected troubadour-rootfs-<artifact>.img.gz"; return }
        let scoped = url.startAccessingSecurityScopedResource()
        Task {
            e.busy = true
            do {
                let r = try await room.agent?.upload("/api/firmware/upload", fileURL: url, name: url.lastPathComponent)
                e.note = "Image stored: \(r?.str("name") ?? url.lastPathComponent)"
            } catch {
                e.error = error.localizedDescription
            }
            if scoped { url.stopAccessingSecurityScopedResource() }
            e.busy = false
            await e.load()
        }
    }
}

// MARK: - system

struct SystemView: View {
    let room: Room
    @StateObject private var e: Endpoint          // /api/fleet/live
    @StateObject private var st: Endpoint         // /api/state — identity, system load, sensors, sleep
    @StateObject private var stats: Endpoint
    @StateObject private var house: Endpoint
    @StateObject private var cloud: Endpoint
    @State private var cpuHist: [Double] = []
    @State private var memHist: [Double] = []
    @State private var sleepMinutes: Double = 0
    @State private var confirmCloud: (on: Bool, group: Bool)?
    @State private var confirmRestore = false
    @State private var confirmService: String?
    @State private var roomDoc: String?
    @State private var showRaw = false
    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/fleet/live", every: 8))
        _st = StateObject(wrappedValue: Endpoint(room.agent, "/api/state", every: 5))
        _stats = StateObject(wrappedValue: Endpoint(room.agent, "/api/stats", every: 10))
        _house = StateObject(wrappedValue: Endpoint(room.agent, "/api/housekeeping", every: 0))
        _cloud = StateObject(wrappedValue: Endpoint(room.agent, "/api/cloudblock/all", every: 0))
    }

    private var state: RoomState { RoomState(st.data) }
    private var core: [String: Any] { state.core }
    private var sys: [String: Any] { state.system }
    private var me: [String: Any]? { state.fleet.first { $0.flag("isMe") } }

    var body: some View {
        SetupPage(title: "System", e: e) {
            identity
            Card("Sleep timer") {
                HStack(spacing: 6) {
                    ForEach([0, 15, 30, 60, 90], id: \.self) { mins in
                        Button(mins == 0 ? "Off" : "\(mins) min") { e.fire("/api/sleep", ["minutes": mins], note: mins == 0 ? "Sleep off" : "Sleeping in \(mins) min"); st.reload() }
                            .buttonStyle(Chip(filled: mins == 0 && state.sleepLabel == nil))
                    }
                }
                HStack {
                    Text(sleepMinutes <= 0 ? "Custom: off" : "Custom: \(Int(sleepMinutes)) min").font(.caption).foregroundStyle(Theme.muted)
                    Spacer()
                    Button("Set") { e.fire("/api/sleep", ["minutes": Int(sleepMinutes)], note: sleepMinutes <= 0 ? "Sleep off" : "Sleeping in \(Int(sleepMinutes)) min"); st.reload() }
                        .buttonStyle(Chip(filled: true))
                }
                Slider(value: $sleepMinutes, in: 0...120, step: 5).tint(.white)
                Text(state.sleepLabel.map { "Sleep timer: \($0)" } ?? "No sleep timer.").font(.caption).foregroundStyle(state.sleepLabel == nil ? Theme.muted : Theme.warn)
            }
            systemCard
            Card("Speakers, live") {
                ForEach(e.data.list("cells").indices, id: \.self) { i in
                    fleetRow(e.data.list("cells")[i])
                }
            }
            Card("Syng's cloud") {
                HStack {
                    Button("Block, room-wide") { confirmCloud = (true, true) }.buttonStyle(Chip())
                    Button("Unblock, room-wide") { confirmCloud = (false, true) }.buttonStyle(Chip())
                }
                ForEach(cloud.data.list("cells").indices, id: \.self) { i in
                    let c = cloud.data.list("cells")[i]
                    let blocked = c["blocked"] as? Bool
                    HStack {
                        Circle().fill(blocked == true ? Theme.live : (blocked == false ? Theme.warn : Theme.muted)).frame(width: 7, height: 7)
                        Text(String((c.str("serial") ?? "").suffix(5))).font(.caption.weight(.semibold).monospaced())
                        Text(c.str("ip") ?? "").font(.caption2).foregroundStyle(Theme.muted)
                        if c.flag("isMe") { Text("this speaker").font(.caption2).foregroundStyle(Theme.muted) }
                        Spacer()
                        Text(blocked == true ? "cloud blocked" : (blocked == false ? "cloud open" : "unknown")).font(.caption2).foregroundStyle(Theme.muted)
                        if c.flag("isMe") {
                            Button(blocked == true ? "Unblock" : "Block") { confirmCloud = (blocked != true, false) }.buttonStyle(Chip())
                        }
                    }
                }
                Text("Blocked, the speakers keep the room document we write and Syng's cloud cannot overwrite it. Unblocking lets the cloud push its own last idea of the room back.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Housekeeping") {
                HStack {
                    Button("Trim journal") { house.fire("/api/housekeeping", ["action": "vacuum_journal"], note: "Journal trimmed") }.buttonStyle(Chip())
                    Button("Clear coredumps") { house.fire("/api/housekeeping", ["action": "clear_coredumps"], note: "Coredumps cleared") }.buttonStyle(Chip())
                    Button("Load") { house.reload() }.buttonStyle(Chip())
                }
                if !house.data.isEmpty {
                    let data = house.data.obj("data")
                    Text(String(format: "journal %.0f MB · coredumps %ld (%.0f MB) · data %.0f of %.0f MB free",
                                (house.data.num("journal_bytes") ?? 0) / 1_048_576, house.data.list("coredumps").count,
                                (house.data.num("coredump_bytes") ?? 0) / 1_048_576, (data.num("free") ?? 0) / 1_048_576, (data.num("total") ?? 0) / 1_048_576))
                        .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                    ForEach(house.data.list("tmp").prefix(4).indices, id: \.self) { i in
                        let t = house.data.list("tmp")[i]
                        Text("\(t.str("path") ?? "")  \(String(format: "%.1f MB", (t.num("bytes") ?? 0) / 1_048_576))").font(.caption2.monospaced()).foregroundStyle(Theme.muted)
                    }
                    ForEach(house.data.list("top").prefix(5).indices, id: \.self) { i in
                        let t = house.data.list("top")[i]
                        Text(String(format: "%@  %.0f MB  %@", t.str("name") ?? "", (t.num("rss_kb") ?? 0) / 1024, t.str("elapsed") ?? "")).font(.caption2.monospaced()).foregroundStyle(Theme.muted)
                    }
                }
                Menu {
                    ForEach(["airplay2source", "spotifysource", "tidalsource", "cellbroker", "ledmanager", "pycalibrator", "syng-agent"], id: \.self) { svc in
                        Button(svc) { confirmService = svc }
                    }
                } label: { Text("Restart a service…").font(.footnote.weight(.semibold)).foregroundStyle(.white) }
            }
            Card("Room document") {
                HStack {
                    Button("Show") { loadDoc() }.buttonStyle(Chip())
                    Button("Restore pre-agent state") { confirmRestore = true }.buttonStyle(Chip())
                }
                if let d = roomDoc {
                    ScrollView(.horizontal) { Text(d).font(.caption2.monospaced()).foregroundStyle(Theme.muted).textSelection(.enabled) }
                        .frame(maxHeight: 260)
                }
                Text("Restore puts back the room document the agent saved before it first changed anything — Syng's own layout, calibration and membership — on this speaker.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Button(showRaw ? "Hide raw stats" : "Raw stats") { showRaw.toggle() }.buttonStyle(Chip())
            if showRaw {
                ForEach(statKeys, id: \.self) { k in
                    Card(k) {
                        let o = stats.data.obj(k)
                        ForEach(Array(o.keys.sorted().prefix(8)), id: \.self) { kk in
                            HStack {
                                Text(kk).font(.caption2).foregroundStyle(Theme.muted)
                                Spacer()
                                Text(describe(o[kk])).font(.caption2.monospacedDigit()).lineLimit(1)
                            }
                        }
                    }
                }
            }
        }
        .onAppear { st.start(); stats.start(); house.reload(); cloud.reload() }
        .onDisappear { st.stop(); stats.stop() }
        .onReceive(st.$data.dropFirst()) { d in
            let s = (d["system"] as? [String: Any]) ?? [:]
            if let c = s["cpuPct"] as? Double { cpuHist.append(c); if cpuHist.count > 60 { cpuHist.removeFirst() } }
            if let m = (s["mem"] as? [String: Any])?["usedPct"] as? Double { memHist.append(m); if memHist.count > 60 { memHist.removeFirst() } }
        }
        .alert(cloudTitle, isPresented: Binding(get: { confirmCloud != nil }, set: { if !$0 { confirmCloud = nil } })) {
            Button(confirmCloud?.on == true ? "Block" : "Unblock", role: .destructive) {
                if let c = confirmCloud {
                    var b: [String: Any] = ["on": c.on]
                    if c.group { b["scope"] = "group" }
                    cloud.fire("/api/cloudblock", b, note: c.on ? "Blocked" : "Unblocked", timeout: 90)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(confirmCloud?.on == true ? "The speakers stop talking to Syng's cloud; the room document stays as we wrote it." : "Syng's cloud will overwrite the local room document (6-cell rooms, local calibration) with what it last knew.")
        }
        .alert("Restart \(confirmService ?? "")?", isPresented: Binding(get: { confirmService != nil }, set: { if !$0 { confirmService = nil } })) {
            Button("Restart", role: .destructive) { if let s = confirmService { house.fire("/api/housekeeping", ["action": "restart_service", "name": s], note: "Restarted \(s)", timeout: 60) } }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Playback through that service stops for a few seconds.") }
        .alert("Restore the pre-agent room state?", isPresented: $confirmRestore) {
            Button("Restore", role: .destructive) { restore() }
            Button("Cancel", role: .cancel) {}
        } message: { Text("Puts Syng's own last room document back on this speaker. The room you built since is lost on it.") }
    }

    private var cloudTitle: String {
        guard let c = confirmCloud else { return "" }
        return (c.on ? "Block Syng's cloud" : "Unblock Syng's cloud") + (c.group ? " on every speaker in the room?" : " on this speaker?")
    }

    /// The web page's hero: hostname, role, what it plays, hardware, cloud reachability, build.
    private var identity: some View {
        Card("This speaker") {
            HStack(alignment: .firstTextBaseline) {
                Text(core.str("hostname") ?? room.agent?.ip ?? "—").font(.title3.weight(.bold)).lineLimit(1)
                Spacer()
                if let r = core.str("role") ?? me?.str("role") { Text(r).font(.caption.weight(.semibold)).padding(.horizontal, 8).padding(.vertical, 3).background(Theme.surface2, in: Capsule()) }
            }
            HStack(spacing: 6) {
                Circle().fill(state.cloudOnline == true ? Theme.live : Theme.muted).frame(width: 7, height: 7)
                Text(state.cloudOnline == true ? "Syng's cloud reachable" : "LAN only").font(.caption).foregroundStyle(Theme.muted)
                if let a = me?.str("activeSource"), !a.isEmpty { Text("· playing \(Source(id: a, [:]).displayName)").font(.caption).foregroundStyle(Theme.muted) }
            }
            Text([core.str("hardwareModel"), core.str("uniqueID"), core.str("buildName")].compactMap { $0 }.joined(separator: " · "))
                .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
            let net: [(String, String?, Bool)] = [
                ("Address", core.str("infraIP") ?? room.agent?.ip, true),
                ("SyFi mesh", core.str("syfiIP"), true),
                ("Time sync", (core["timeSyncGood"] as? Bool).map { $0 ? "locked" : "—" }, core["timeSyncGood"] as? Bool != false),
                ("SyFi link", (core["syfiGood"] as? Bool).map { $0 ? "good" : "down" }, core["syfiGood"] as? Bool != false),
                ("Orientation", (core["cellIsUpsideDown"] as? Bool).map { $0 ? "inverted" : "upright" }, true)]
            ForEach(net.indices, id: \.self) { i in
                if let v = net[i].1 { kv(net[i].0, v, ok: net[i].2) }
            }
        }
    }

    private var systemCard: some View {
        Card("Load and health") {
            if !sys.isEmpty {
                HStack(spacing: 14) {
                    metric("CPU", sys.num("cpuPct").map { "\(Int($0))%" } ?? "—", cpuHist)
                    metric("Memory", sys.obj("mem").num("usedPct").map { "\(Int($0))%" } ?? "—", memHist)
                }
                let mem = sys.obj("mem")
                kv("Memory", "\(mem.int("usedMB").map(String.init) ?? "—") / \(mem.int("totalMB").map(String.init) ?? "—") MB")
                kv("Load (1 m)", sys.num("load").map { String(format: "%.2f", $0) } ?? "—")
                kv("Data partition", sys.num("diskData").map { "\(Int($0))%" } ?? "—", ok: (sys.num("diskData") ?? 0) < 90)
                kv("Root fs", sys.num("diskRoot").map { "\(Int($0))%" } ?? "—", ok: (sys.num("diskRoot") ?? 0) < 90)
                kv("Uptime", uptime(sys.num("uptime")))
            }
            let temps = state.sensors.obj("temps")
            if !temps.isEmpty {
                Text("TEMPERATURES").font(.caption2.weight(.medium)).tracking(1).foregroundStyle(Theme.muted).padding(.top, 4)
                ForEach(temps.keys.sorted(), id: \.self) { k in
                    let v = temps.num(k) ?? 0
                    kv(tempName(k), String(format: "%.1f °C", v), ok: v < 80)
                }
            }
            let faults = state.sensors.obj("faults")
            if !faults.isEmpty {
                Text("SPEAKER FAULTS").font(.caption2.weight(.medium)).tracking(1).foregroundStyle(Theme.muted).padding(.top, 4)
                ForEach(faults.keys.sorted(), id: \.self) { k in
                    kv(k.replacingOccurrences(of: "_", with: " "), describe(faults[k]), ok: (faults.num(k) ?? 0) <= 0)
                }
            }
            let accel = state.sensors.obj("accel")
            if !accel.isEmpty {
                kv("Orientation (g)", accel.keys.sorted().map { "\($0.replacingOccurrences(of: "_", with: " ")) \(String(format: "%.2f", accel.num($0) ?? 0))" }.joined(separator: "  "))
            }
            let mmc = state.sensors.obj("mmc")
            if !mmc.isEmpty {
                Text("eMMC HEALTH").font(.caption2.weight(.medium)).tracking(1).foregroundStyle(Theme.muted).padding(.top, 4)
                let rows = mmcRows(mmc)
                ForEach(rows.indices, id: \.self) { i in kv(rows[i].0, rows[i].1, ok: rows[i].2) }
            }
        }
    }

    private func metric(_ k: String, _ v: String, _ hist: [Double]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(k).font(.caption2).foregroundStyle(Theme.muted)
            Text(v).font(.title3.weight(.semibold).monospacedDigit())
            Sparkline(values: hist).frame(height: 22)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    private func kv(_ k: String, _ v: String, ok: Bool = true) -> some View {
        HStack(alignment: .top) {
            Text(k).font(.caption2).foregroundStyle(Theme.muted)
            Spacer()
            Text(v).font(.caption2.monospacedDigit()).foregroundStyle(ok ? Color.white : Theme.warn).multilineTextAlignment(.trailing)
        }
    }
    private func uptime(_ s: Double?) -> String {
        guard let s = s else { return "—" }
        let d = Int(s) / 86400, h = Int(s) % 86400 / 3600, m = Int(s) % 3600 / 60
        return d > 0 ? "\(d)d \(h)h" : "\(h)h \(m)m"
    }
    private func tempName(_ k: String) -> String {
        let map = ["SoC_Temperature": "SoC", "PMIC_Temperature": "PMIC", "CPU_Temperature": "CPU", "eMMC_Temperature": "eMMC",
                   "Wireless_Module_Temperature": "Wi-Fi module", "PSAB_Woofer2_Amplifier_Temperature": "Woofer amp", "PSAB_MLB_Connector_Temperature": "MLB connector"]
        return map[k] ?? k.replacingOccurrences(of: "_", with: " ")
    }
    /// JEDEC eMMC health, decoded the way the web page does (`mmcHuman`).
    private func mmcRows(_ m: [String: Any]) -> [(String, String, Bool)] {
        func int(_ k: String) -> Int? { if let v = m[k] as? Int { return v }; if let v = m[k] as? Double { return Int(v) }; if let v = m[k] as? String { return Int(v) }; return nil }
        func band(_ v: Int?) -> (String, Bool) {
            guard let v = v, v >= 1 else { return ("n/a", true) }
            if v >= 11 { return ("over 100% — worn out", false) }
            return ("\((v - 1) * 10)–\(v * 10)% used", v < 7)
        }
        var rows: [(String, String, Bool)] = []
        let eol = int("eMMC PRE EOL Info")
        rows.append(("Overall health", eol == 1 ? "Normal" : eol == 2 ? "Warning — 80% of reserve used" : eol == 3 ? "Urgent — 90% of reserve used" : "unknown", eol == nil || eol == 1))
        let a = band(int("eMMC Life Time Estimation A")), b = band(int("eMMC Life Time Estimation B"))
        rows.append(("Life used · main storage", a.0, a.1)); rows.append(("Life used · boot area", b.0, b.1))
        if let bb = int("later BB") { rows.append(("Failed blocks in service", bb == 0 ? "0 — none" : "\(bb)", bb == 0)) }
        if let sp = m["spare blocks"] { rows.append(("Spare blocks left", describe(sp), true)) }
        if let am = m["avg P/E cnt MLC"] { rows.append(("Avg write/erase cycles", "main \(describe(am)) · boot \(describe(m["avg P/E cnt pSLC"]))", true)) }
        return rows
    }

    private var statKeys: [String] { stats.data.keys.sorted() }
    private func describe(_ v: Any?) -> String {
        guard let v = v else { return "—" }
        if let b = v as? Bool { return b ? "yes" : "no" }          // JSON bools are NSNumbers too
        if let d = v as? Double { return d == d.rounded() ? String(Int(d)) : String(format: "%.2f", d) }
        if let s = v as? String { return s }
        return String(String(describing: v).prefix(24))
    }

    private func fleetRow(_ c: [String: Any]) -> some View {
        HStack(spacing: 8) {
            Circle().fill(c.flag("ok") ? (c.flag("sync") ? Theme.live : Theme.warn) : Theme.bad).frame(width: 7, height: 7)
            Text(String((c.str("serial") ?? "").suffix(5))).font(.caption.weight(.semibold).monospaced())
            Text(c.str("role") ?? "").font(.caption2).foregroundStyle(Theme.muted)
            Spacer()
            if let r = c.int("rssi") { Text("\(r) dBm").font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted) }
            Text(c.flag("syfi") ? "SyFi" : "—").font(.caption2).foregroundStyle(Theme.muted)
            Text(c["agent"].map { describe($0) } ?? "").font(.caption2).foregroundStyle(Theme.muted)
        }
        .padding(.vertical, 2)
    }

    private func loadDoc() {
        Task {
            guard let d = try? await room.agent?.get("/api/room/doc", timeout: 10) else { roomDoc = "unavailable"; return }
            if let data = try? JSONSerialization.data(withJSONObject: d, options: [.prettyPrinted, .sortedKeys]) { roomDoc = String(decoding: data, as: UTF8.self) }
        }
    }
    private func restore() {
        Task {
            guard let a = room.agent else { return }
            house.busy = true
            do {
                let backup = try await a.get("/api/room/backup", timeout: 15)
                _ = try await a.post("/api/room/restore", ["state": backup], timeout: 60)
                house.note = "Restored the pre-agent room state"
            } catch { house.error = error.localizedDescription }
            house.busy = false
        }
    }
}

/// A 60-sample line, the web page's `spark()`.
struct Sparkline: View {
    let values: [Double]
    var body: some View {
        Canvas { ctx, size in
            guard values.count > 1 else { return }
            var p = Path()
            for (i, v) in values.enumerated() {
                let x = size.width * CGFloat(i) / CGFloat(max(values.count - 1, 1))
                let y = size.height - size.height * CGFloat(min(max(v, 0), 100)) / 100
                if i == 0 { p.move(to: CGPoint(x: x, y: y)) } else { p.addLine(to: CGPoint(x: x, y: y)) }
            }
            ctx.stroke(p, with: .color(.white.opacity(0.8)), lineWidth: 1.5)
        }
    }
}
