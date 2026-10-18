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
    func fire(_ p: String, _ body: [String: Any] = [:], note: String? = nil) {
        Task {
            let ok = await post(p, body)
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
    case room, layout, leds, sound, stream, scenes, network, firmware, system, page
    var id: String { rawValue }
    var title: String {
        switch self {
        case .room: return "Room"
        case .layout: return "Layout & Soundcheck"
        case .leds: return "LEDs"
        case .sound: return "Sound & controls"
        case .stream: return "Group stream"
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
    @EnvironmentObject var fleet: Fleet
    @State private var renaming = false
    @State private var newName = ""
    @State private var confirmLeave = false
    init(room: Room) { self.room = room; _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/room", every: 8)) }

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
                                Text(c.ip).font(.caption).foregroundStyle(Theme.muted)
                            }
                            Spacer()
                            Button("Add") { add(c) }.buttonStyle(Chip(filled: true))
                        }
                        .padding(.vertical, 3)
                    }
                    Text("Adding asks that speaker to join this room; it keeps playing once the primary admits it.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
            }
            Card("Careful") {
                Button("Leave this room") { confirmLeave = true }.buttonStyle(Capsule_())
                Text("This speaker goes back to a room of its own. The others stay together.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .alert("Room name", isPresented: $renaming) {
            TextField("Ball Room", text: $newName)
            Button("Save") { e.fire("/api/room/rename", ["name": newName], note: "Renamed") }
            Button("Cancel", role: .cancel) {}
        }
        .alert("Leave the room?", isPresented: $confirmLeave) {
            Button("Leave", role: .destructive) { e.fire("/api/room/leave", [:], note: "Left the room") }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func memberRow(_ c: [String: Any]) -> some View {
        let serial = c.str("serial") ?? ""
        let isPri = (c.str("role") ?? "").lowercased() == "primary"
        return HStack(spacing: 12) {
            Puck(size: 34, ring: isPri ? .white : nil)
            VStack(alignment: .leading, spacing: 2) {
                Text(c.str("name") ?? String(serial.suffix(5))).font(.subheadline.weight(.semibold))
                Text([String(serial.suffix(5)), c.str("ip") ?? "", isPri ? "primary" : "secondary",
                      c.flag("calibrated") ? "" : "uncalibrated"].filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Spacer()
            Menu {
                if !isPri {
                    Button("Make primary") { e.fire("/api/room/pin", ["serial": serial, "role": "primary"], note: "Primary pinned") }
                    Button("Remove from room", role: .destructive) { e.fire("/api/room/remove", ["serial": serial], note: "Removed") }
                }
                Button("Identify") { e.fire("/api/ping", ["serial": serial]) }
            } label: {
                Image(systemName: "ellipsis.circle").foregroundStyle(Theme.muted)
            }
        }
        .padding(.vertical, 3)
    }

    /// Cells the app can see that are not in this room.
    private var nearby: [Cell] {
        let mine = Set(cells.compactMap { $0.str("serial") })
        return fleet.rooms.flatMap(\.cells).filter { !mine.contains($0.serial) }
    }
    private func add(_ c: Cell) {
        guard let primary = room.agent?.ip else { return }
        Task {
            e.busy = true
            do { _ = try await Agent(ip: c.ip).post("/api/room/join", ["ip": primary], timeout: 30); e.note = "\(c.title) joined" }
            catch { e.error = error.localizedDescription }
            e.busy = false
            await e.load()
            await Fleet.shared.refresh(force: true)
        }
    }
}

// MARK: - LEDs

struct LEDView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @State private var wholeRoom = true
    @State private var colour = Color(red: 0.2, green: 0.7, blue: 1.0)
    @State private var white: Double = 0
    init(room: Room) { self.room = room; _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/led", every: 6)) }

    private var modes: [String] { e.data.strings("reactiveModes") }
    private var mode: String? { e.data.flag("reactive") ? e.data.str("mode") : nil }
    private var scope: [String: Any] { wholeRoom ? ["scope": "group"] : [:] }

    var body: some View {
        SetupPage(title: "LEDs", e: e) {
            Card("Applies to") {
                HStack {
                    Button("Whole room") { wholeRoom = true }.buttonStyle(Capsule_(filled: wholeRoom))
                    Button("This speaker") { wholeRoom = false }.buttonStyle(Capsule_(filled: !wholeRoom))
                }
            }
            Card("Reactive") {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 8)], spacing: 8) {
                    ForEach(modes, id: \.self) { m in
                        Button(m.capitalized) {
                            e.fire("/api/led/reactive", scope.merging(["mode": m]) { a, _ in a })
                        }
                        .buttonStyle(Capsule_(filled: mode == m))
                    }
                    Button("Off") { e.fire("/api/led/reactive", scope.merging(["mode": "off"]) { a, _ in a }) }
                        .buttonStyle(Capsule_(filled: mode == nil))
                }
                Text("The LEDs follow what is playing. The primary feeds the others, so a mode set room-wide stays in step.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Palette") {
                HStack {
                    Button("Per speaker") { e.fire("/api/led/palette", scope.merging(["palette": "free"]) { a, _ in a }) }
                        .buttonStyle(Capsule_(filled: (e.data.str("palette") ?? "free") == "free"))
                    Button("Shared") { e.fire("/api/led/palette", scope.merging(["palette": "shared"]) { a, _ in a }) }
                        .buttonStyle(Capsule_(filled: e.data.str("palette") == "shared"))
                }
            }
            Card("Colour") {
                ColorPicker("Pick a colour", selection: $colour, supportsOpacity: false).font(.subheadline)
                HStack {
                    Text("White").font(.subheadline).foregroundStyle(Theme.muted)
                    Slider(value: $white, in: 0...1).tint(.white)
                    Text(String(format: "%.0f%%", white * 100)).font(.caption.monospacedDigit()).frame(width: 44, alignment: .trailing)
                }
                Button("Set this colour") { setColour() }.buttonStyle(Capsule_(filled: true))
                Text("Turns the reactive modes off on whatever this applies to.").font(.caption).foregroundStyle(Theme.muted)
            }
            if !e.data.strings("visions").isEmpty {
                Card("Animations on the cell") {
                    Text(e.data.strings("visions").joined(separator: " · ")).font(.caption).foregroundStyle(Theme.muted)
                }
            }
        }
    }

    private func setColour() {
        let ui = UIColor(colour)
        var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        ui.getRed(&r, green: &g, blue: &b, alpha: &a)
        var body: [String: Any] = ["r": Double(r), "g": Double(g), "b": Double(b), "w": white]
        if wholeRoom { body["scope"] = "group" }
        e.fire("/api/led/color", body, note: "Colour set")
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
    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/net", every: 10))
        _wifi = StateObject(wrappedValue: Endpoint(room.agent, "/api/wifi", every: 10))
        _link = StateObject(wrappedValue: Endpoint(room.agent, "/api/link", every: 6))
    }

    var body: some View {
        SetupPage(title: "Network", e: e) {
            Card("Wi-Fi") {
                HStack {
                    Text(wifi.data.str("ssid") ?? "Not connected").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(wifi.data.str("ip") ?? "").font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                }
                ForEach(wifiServices.indices, id: \.self) { i in
                    let s = wifiServices[i]
                    HStack {
                        Image(systemName: s.flag("connected") ? "wifi.circle.fill" : "wifi")
                            .foregroundStyle(s.flag("connected") ? Color.white : Theme.muted)
                        Text(s.str("name") ?? "(hidden)").font(.subheadline).lineLimit(1)
                        Spacer()
                        if s.flag("connected") {
                            Button("Forget") { wifi.fire("/api/wifi/forget", ["ssid": s.str("name") ?? ""], note: "Forgotten") }
                                .buttonStyle(Chip())
                        } else {
                            Button("Join") { joining = s.str("name"); psk = "" }.buttonStyle(Chip())
                        }
                    }
                    .padding(.vertical, 2)
                }
                Button("Scan again") { Task { _ = try? await room.agent?.get("/api/wifi/scan", timeout: 25); await wifi.load() } }
                    .buttonStyle(Capsule_())
            }
            Card("Radios and addresses") {
                HStack {
                    Button("Wi-Fi off") { e.fire("/api/net/power", ["tech": "wifi", "on": false], note: "Wi-Fi off") }.buttonStyle(Chip())
                    Button("Wi-Fi on") { e.fire("/api/net/power", ["tech": "wifi", "on": true], note: "Wi-Fi on") }.buttonStyle(Chip())
                    Button("Ethernet on") { e.fire("/api/net/power", ["tech": "ethernet", "on": true], note: "Ethernet on") }.buttonStyle(Chip())
                }
                ForEach(e.data.list("interfaces").indices, id: \.self) { i in
                    let n = e.data.list("interfaces")[i]
                    HStack {
                        Text(n.str("ifname") ?? "").font(.caption.weight(.semibold))
                        Spacer()
                        Text(n.str("ip") ?? "—").font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                    }
                }
            }
            Card("Cell-to-cell link") {
                HStack {
                    Button("SyFi") { link.fire("/api/link", ["mode": "syfi"], note: "Switching to SyFi…") }
                        .buttonStyle(Capsule_(filled: (link.data.str("mode") ?? "syfi") == "syfi"))
                    Button("Wired") { link.fire("/api/link", ["mode": "wired"], note: "Switching to wired…") }
                        .buttonStyle(Capsule_(filled: link.data.str("mode") == "wired"))
                }
                if link.data.flag("busy") { Text("Working…").font(.caption).foregroundStyle(Theme.warn) }
                ForEach(link.data.list("members").indices, id: \.self) { i in
                    linkRow(link.data.list("members")[i])
                }
                Text("Wired moves the audio and the shared clock onto Ethernet (ntp time-sync + PTP). Every speaker needs a wired address first.")
                    .font(.caption).foregroundStyle(Theme.muted)
                if let err = link.error { Text(err).font(.caption).foregroundStyle(Theme.bad) }
            }
        }
        .onAppear { wifi.start(); link.start() }
        .onDisappear { wifi.stop(); link.stop() }
        .alert("Wi-Fi password", isPresented: Binding(get: { joining != nil }, set: { if !$0 { joining = nil } })) {
            SecureField("Password", text: $psk)
            Button("Join") { if let s = joining { wifi.fire("/api/wifi/connect", ["ssid": s, "psk": psk], note: "Joining \(s)…") } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(joining ?? "")
        }
    }

    private var wifiServices: [[String: Any]] { wifi.data.list("services") }

    private func linkRow(_ m: [String: Any]) -> some View {
        let ptp = m.obj("ptp")
        let offset = ptp.num("offset_ns")
        let state = ptp.str("state") ?? (ptp.flag("running") ? "running" : "—")
        return HStack {
            Circle().fill(m.flag("time_sync_good") ? Theme.live : Theme.warn).frame(width: 7, height: 7)
            Text(m.str("name") ?? String((m.str("serial") ?? "").suffix(5))).font(.caption.weight(.semibold))
            Spacer()
            Text(m.str("wired_ip") ?? "no wire").font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
            Text(offset.map { String(format: "%@ %.0fµs", state, $0 / 1000) } ?? state)
                .font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
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
    init(room: Room) { self.room = room; _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/firmware", every: 4)) }

    private var running: [String: Any] { e.data.obj("running") }
    private var image: [String: Any] { e.data.obj("image") }

    var body: some View {
        SetupPage(title: "Firmware", e: e) {
            Card("Running now") {
                row("Artifact", running.str("artifact") ?? "—")
                row("Version", running.str("version") ?? "—")
                row("Partition", running.int("part").map { String($0) } ?? "—")
                row("Agent", running.int("agent").map { "b\($0)" } ?? "—")
                if let other = e.data["other"] as? [String: Any] {
                    row("Other slot", other.str("artifact") ?? other.str("version") ?? "—")
                }
            }
            if !image.isEmpty {
                Card("Image ready to install") {
                    row("File", image.str("name") ?? "—")
                    row("Size", image.num("bytes").map { String(format: "%.0f MB", $0 / 1_048_576) } ?? "—")
                    Button("Install on this speaker") { confirmInstall = true }.buttonStyle(Capsule_(filled: true))
                    Button("Install across the room") { e.fire("/api/firmware/room", ["action": "install"], note: "Room update started") }
                        .buttonStyle(Capsule_())
                }
            }
            Card("Get an image") {
                Button("Upload from this phone…") { picking = true }.buttonStyle(Capsule_())
                Text("A `.img.gz` from Files or iCloud Drive. The speaker writes it to the spare slot; nothing changes until you install.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            if e.data.flag("busy") || !e.data.strings("log").isEmpty {
                Card(e.data.flag("busy") ? "Working" : "Last run") {
                    if let p = e.data.num("progress") {
                        ProgressView(value: min(max(p, 0), 1)).tint(.white)
                    }
                    Text(e.data.strings("log").suffix(8).joined(separator: "\n"))
                        .font(.caption2.monospaced()).foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            Card("Careful") {
                Button("Roll back to the other slot") { e.fire("/api/firmware/rollback", [:], note: "Rolling back") }.buttonStyle(Capsule_())
                Text("Switches which partition boots next. The speaker restarts.").font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .alert("Install and restart?", isPresented: $confirmInstall) {
            Button("Install", role: .destructive) { e.fire("/api/firmware/install", ["reboot": true, "carry": true], note: "Installing") }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The image goes into the spare slot and the speaker reboots into it. Troubadour's agent and overlay are carried across.")
        }
        .fileImporter(isPresented: $picking, allowedContentTypes: [.data], allowsMultipleSelection: false) { result in
            guard case .success(let urls) = result, let url = urls.first else { return }
            upload(url)
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k).font(.caption).foregroundStyle(Theme.muted)
            Spacer()
            Text(v).font(.caption.monospacedDigit()).lineLimit(1)
        }
    }

    private func upload(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        Task {
            e.busy = true
            do {
                _ = try await room.agent?.upload("/api/firmware/upload", fileURL: url, name: url.lastPathComponent)
                e.note = "Uploaded \(url.lastPathComponent)"
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
    @StateObject private var stats: Endpoint
    @StateObject private var house: Endpoint
    @StateObject private var cloud: Endpoint
    @State private var sleepMinutes: Double = 0
    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/fleet/live", every: 8))
        _stats = StateObject(wrappedValue: Endpoint(room.agent, "/api/stats", every: 10))
        _house = StateObject(wrappedValue: Endpoint(room.agent, "/api/housekeeping", every: 0))
        _cloud = StateObject(wrappedValue: Endpoint(room.agent, "/api/cloudblock/all", every: 0))
    }

    private var cloudSummary: String {
        let cells = cloud.data.list("cells")
        guard !cells.isEmpty else { return "" }
        let blocked = cells.filter { ($0["blocked"] as? Bool) == true }.count
        return "\(blocked) of \(cells.count) blocked"
    }

    var body: some View {
        SetupPage(title: "System", e: e) {
            Card("Speakers, live") {
                ForEach(e.data.list("cells").indices, id: \.self) { i in
                    fleetRow(e.data.list("cells")[i])
                }
            }
            Card("Sleep timer") {
                HStack {
                    Text(sleepMinutes <= 0 ? "Off" : "\(Int(sleepMinutes)) min").font(.subheadline.weight(.semibold))
                    Spacer()
                    Button("Set") { e.fire("/api/sleep", ["minutes": Int(sleepMinutes)], note: sleepMinutes <= 0 ? "Sleep off" : "Sleeping in \(Int(sleepMinutes)) min") }
                        .buttonStyle(Chip(filled: true))
                }
                Slider(value: $sleepMinutes, in: 0...120, step: 5).tint(.white)
            }
            Card("Syng's cloud") {
                HStack {
                    Button("Block, room-wide") { cloud.fire("/api/cloudblock", ["on": true, "scope": "group"], note: "Blocked") }.buttonStyle(Chip())
                    Button("Unblock") { cloud.fire("/api/cloudblock", ["on": false, "scope": "group"], note: "Unblocked") }.buttonStyle(Chip())
                }
                Text(cloudSummary).font(.caption).foregroundStyle(Theme.muted)
                Text("Blocked, the speakers keep the room document we write and Syng's cloud cannot overwrite it. Unblocking lets the cloud push its own last idea of the room back.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Housekeeping") {
                HStack {
                    Button("Journal") { house.fire("/api/housekeeping", ["action": "vacuum_journal"], note: "Journal trimmed") }.buttonStyle(Chip())
                    Button("Coredumps") { house.fire("/api/housekeeping", ["action": "clear_coredumps"], note: "Coredumps cleared") }.buttonStyle(Chip())
                    Button("Load") { house.reload() }.buttonStyle(Chip())
                }
                if let j = house.data.num("journal_bytes") {
                    Text(String(format: "journal %.0f MB · coredumps %.0f MB", j / 1_048_576,
                                (house.data.num("coredump_bytes") ?? 0) / 1_048_576))
                        .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                }
            }
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
        .onAppear { stats.start(); house.reload(); cloud.reload() }
        .onDisappear { stats.stop() }
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
            Text(c.int("agent").map { "b\($0)" } ?? "").font(.caption2).foregroundStyle(Theme.muted)
        }
        .padding(.vertical, 2)
    }
}
