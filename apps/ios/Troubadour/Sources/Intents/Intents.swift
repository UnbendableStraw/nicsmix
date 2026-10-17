import AppIntents

/// Siri / Shortcuts: "Set Ball Room to 30 percent", "Play scene Night in Ball Room", "Pause Ball Room".
/// They run inside the app process: discovery happens first, then the room's agent gets the call.
@available(iOS 16.0, *)
struct SetRoomVolume: AppIntent {
    static var title: LocalizedStringResource = "Set room volume"
    static var description = IntentDescription("Sets the volume of a Troubadour room (0–100).")
    @Parameter(title: "Room") var room: String
    @Parameter(title: "Volume", inclusiveRange: (0, 100)) var volume: Int
    static var parameterSummary: some ParameterSummary { Summary("Set \(\.$room) to \(\.$volume)%") }
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let a = try await Rooms.agent(named: room)
        try await a.post("/api/volume", ["source": "all", "volume": Double(volume) / 100])
        return .result(dialog: "\(room) at \(volume) percent")
    }
}

@available(iOS 16.0, *)
struct ApplyScene: AppIntent {
    static var title: LocalizedStringResource = "Play a scene"
    @Parameter(title: "Room") var room: String
    @Parameter(title: "Scene") var scene: String
    static var parameterSummary: some ParameterSummary { Summary("Play scene \(\.$scene) in \(\.$room)") }
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let a = try await Rooms.agent(named: room)
        try await a.post("/api/scenes/apply", ["name": scene])
        return .result(dialog: "Scene \(scene) on in \(room)")
    }
}

@available(iOS 16.0, *)
struct PauseRoom: AppIntent {
    static var title: LocalizedStringResource = "Play / pause a room"
    @Parameter(title: "Room") var room: String
    @Parameter(title: "Action", default: .pause) var action: TransportAction
    static var parameterSummary: some ParameterSummary { Summary("\(\.$action) \(\.$room)") }
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let a = try await Rooms.agent(named: room)
        let d = try await a.get("/api/state"); let st = RoomState(d)
        guard let src = st.active else { return .result(dialog: "Nothing is playing in \(room)") }
        try await a.post("/api/transport", ["source": src.id, "action": action.rawValue])
        return .result(dialog: "\(action.rawValue.capitalized)d \(room)")
    }
}

@available(iOS 16.0, *)
enum TransportAction: String, AppEnum {
    case play, pause, next, previous
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Action")
    static var caseDisplayRepresentations: [TransportAction: DisplayRepresentation] = [.play: "Play", .pause: "Pause", .next: "Next", .previous: "Previous"]
}

@available(iOS 16.0, *)
struct TroubadourShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: SetRoomVolume(), phrases: ["Set \(.applicationName) volume"], shortTitle: "Room volume", systemImageName: "speaker.wave.2")
        AppShortcut(intent: ApplyScene(), phrases: ["Play a \(.applicationName) scene"], shortTitle: "Scene", systemImageName: "sparkles")
        AppShortcut(intent: PauseRoom(), phrases: ["Pause \(.applicationName)"], shortTitle: "Play / pause", systemImageName: "playpause")
    }
}

enum Rooms {
    struct NotFound: LocalizedError { let name: String; var errorDescription: String? { "No room called \(name) was found on this network" } }
    @MainActor static func agent(named name: String) async throws -> Agent {
        let f = Fleet.shared
        if f.rooms.isEmpty { await f.refresh() }
        guard let r = f.rooms.first(where: { $0.name.lowercased() == name.lowercased() }), let a = r.agent else { throw NotFound(name: name) }
        return a
    }
}
