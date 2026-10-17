import Foundation

/// The agent's JSON over HTTP on port 8080 (docs/SETUP.md has the table). Everything here is plain
/// URLSession + JSONSerialization so the app has no dependencies.
struct Agent: Hashable {
    let ip: String
    var base: URL { URL(string: "http://\(ip):8080")! }

    enum Err: LocalizedError { case http(Int, String); case bad
        var errorDescription: String? { switch self { case .http(let c, let m): return "HTTP \(c): \(m)"; case .bad: return "unexpected reply" } }
    }

    func get(_ path: String, timeout: TimeInterval = 6) async throws -> [String: Any] {
        var r = URLRequest(url: base.appendingPathComponent(path)); r.timeoutInterval = timeout
        let (d, resp) = try await URLSession.shared.data(for: r)
        return try Self.decode(d, resp)
    }
    @discardableResult
    func post(_ path: String, _ body: [String: Any] = [:], timeout: TimeInterval = 10) async throws -> [String: Any] {
        var r = URLRequest(url: base.appendingPathComponent(path)); r.httpMethod = "POST"; r.timeoutInterval = timeout
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (d, resp) = try await URLSession.shared.data(for: r)
        return try Self.decode(d, resp)
    }
    /// Raw upload (firmware image) with progress through a delegate-free streaming request.
    func upload(_ path: String, fileURL: URL, name: String) async throws -> [String: Any] {
        var r = URLRequest(url: base.appendingPathComponent(path)); r.httpMethod = "POST"; r.timeoutInterval = 1800
        r.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        r.setValue(name.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? name, forHTTPHeaderField: "X-Filename")
        let (d, resp) = try await URLSession.shared.upload(for: r, fromFile: fileURL)
        return try Self.decode(d, resp)
    }
    static func decode(_ d: Data, _ resp: URLResponse) throws -> [String: Any] {
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let obj = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
        if code >= 400 { throw Err.http(code, (obj?["error"] as? String) ?? String(data: d, encoding: .utf8) ?? "") }
        guard let o = obj else { throw Err.bad }
        if let e = o["error"] as? String { throw Err.http(code, e) }
        return o
    }
}

// MARK: - models the views use (hand-decoded; the agent's JSON is loose)

struct Cell: Identifiable, Hashable {
    let serial: String; var ip: String; var name: String?; var role: String?; var agent: String?; var isMe: Bool; var calibrated: Bool?
    var id: String { serial }
    var shortSerial: String { String(serial.suffix(5)) }
    var title: String { name ?? "Cell \(shortSerial)" }
    init?(_ d: [String: Any]) {
        guard let s = d["serial"] as? String, let ip = d["ip"] as? String else { return nil }
        serial = s; self.ip = ip; name = d["name"] as? String; role = d["role"] as? String; agent = d["agent"] as? String
        isMe = (d["isMe"] as? Bool) ?? false; calibrated = d["calibrated"] as? Bool
    }
}

struct Room: Identifiable, Hashable {
    let id: String; var name: String; var primaryIP: String?; var cells: [Cell]
    init?(_ d: [String: Any]) {
        guard let rid = d["room_id"] as? String else { return nil }
        id = rid; name = (d["room_name"] as? String) ?? (rid == "?" ? "Not in a room" : "Room")
        primaryIP = d["primary_ip"] as? String
        cells = ((d["cells"] as? [[String: Any]]) ?? []).compactMap(Cell.init).filter { $0.agent != nil || $0.isMe }
    }
    /// The agent to talk to for this room: the primary (it owns the sources); any member relays anyway.
    var agent: Agent? { (primaryIP ?? cells.first?.ip).map(Agent.init) }
}

struct Source: Identifiable, Hashable {
    let id: String; var active: Bool; var playing: Bool; var title: String; var artist: String; var album: String; var volume: Double; var hasArtwork: Bool; var mute: Bool; var music: Bool
    var displayName: String {
        switch id { case "airplay2": return "AirPlay"; case "spotify": return "Spotify"; case "tidal": return "TIDAL"; case "hdmi": return "HDMI"; default: return id.capitalized }
    }
    init(id: String, _ d: [String: Any]) {
        self.id = id; active = (d["active"] as? Bool) ?? false; playing = (d["playing"] as? Bool) ?? false
        title = (d["title"] as? String) ?? ""; artist = (d["artist"] as? String) ?? ""; album = (d["album"] as? String) ?? ""
        volume = (d["volume"] as? Double) ?? 0; hasArtwork = (d["artwork"] as? Bool) ?? false; mute = (d["mute"] as? Bool) ?? false
        music = (d["music"] as? Bool) ?? !["CalibrationClient", "saplayer"].contains(id)
    }
}

struct RoomState {
    var sources: [Source] = []; var soundMode = "syng"; var roomName = ""; var viaPrimary: String?; var sleepState = "off"
    init() {}
    init(_ d: [String: Any]) {
        sources = ((d["sources"] as? [String: [String: Any]]) ?? [:]).map { Source(id: $0.key, $0.value) }.filter(\.music).sorted { $0.id < $1.id }
        soundMode = ((d["settings"] as? [String: Any])?["sound_mode"] as? String) ?? "syng"
        roomName = ((d["room"] as? [String: Any])?["name"] as? String) ?? ""
        viaPrimary = d["viaPrimary"] as? String
        sleepState = ((d["sleep"] as? [String: Any])?["state"] as? String) ?? "off"
    }
    var active: Source? { sources.first(where: \.active) }
    var volume: Double { active?.volume ?? sources.first?.volume ?? 0 }
}
