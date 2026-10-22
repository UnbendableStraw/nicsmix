import Foundation

/// The agent's JSON over HTTP on port 8080 (docs/SETUP.md has the table). Everything here is plain
/// URLSession + JSONSerialization so the app has no dependencies.
struct Agent: Hashable {
    /// The address to try first — the speaker's wired address when it has one (b74+ report `wired_ip`).
    let ip: String
    /// Another address for the same speaker (its Wi-Fi when `ip` is the wire), used when `ip` can't be reached.
    let fallback: String?
    init(ip: String, fallback: String? = nil) { self.ip = ip; self.fallback = (fallback == ip || fallback?.isEmpty == true) ? nil : fallback }
    var base: URL { Self.base(ip) }
    static func base(_ host: String) -> URL { URL(string: "http://\(host):8080")! }
    /// A path with a query string intact ("/api/underruns?hours=168"): appendingPathComponent would encode the "?".
    func url(_ path: String) -> URL { url(path, host: ip) }
    func url(_ path: String, host: String) -> URL {
        let b = Self.base(host); return URL(string: path, relativeTo: b)?.absoluteURL ?? b.appendingPathComponent(path)
    }

    /// Addresses that recently failed to connect, so the next call goes straight to the fallback
    /// instead of waiting out a dead wire every time. Retried after a couple of minutes.
    final class Health: @unchecked Sendable {
        private let lock = NSLock(); private var down: [String: Date] = [:]
        func isDown(_ h: String) -> Bool { lock.lock(); defer { lock.unlock() }; return (down[h].map { Date().timeIntervalSince($0) < 120 }) ?? false }
        func mark(_ h: String, down d: Bool) { lock.lock(); defer { lock.unlock() }; down[h] = d ? Date() : nil }
    }
    static let health = Health()
    /// The order to try: wire first, unless it failed lately and there's somewhere else to go.
    var order: [String] {
        guard let f = fallback else { return [ip] }
        return Self.health.isDown(ip) ? [f, ip] : [ip, f]
    }
    /// Connection-level failures worth trying the other address for. A timed-out POST may already have
    /// been carried out, so it isn't repeated.
    static func retryable(_ e: URLError, idempotent: Bool) -> Bool {
        switch e.code {
        case .cannotConnectToHost, .cannotFindHost, .networkConnectionLost, .notConnectedToInternet, .dnsLookupFailed, .resourceUnavailable: return true
        case .timedOut: return idempotent
        default: return false
        }
    }
    private func send(idempotent: Bool, _ make: (String) throws -> URLRequest) async throws -> [String: Any] {
        let hosts = order
        for (i, h) in hosts.enumerated() {
            do {
                let (d, resp) = try await URLSession.shared.data(for: try make(h))
                if hosts.count > 1 { Self.health.mark(h, down: false) }
                return try Self.decode(d, resp)
            } catch let e as URLError where i + 1 < hosts.count && Self.retryable(e, idempotent: idempotent) {
                Self.health.mark(h, down: true)
            }
        }
        throw Err.bad   // unreachable: the last host's error propagates from the loop
    }

    enum Err: LocalizedError { case http(Int, String); case bad
        var errorDescription: String? { switch self { case .http(let c, let m): return "HTTP \(c): \(m)"; case .bad: return "unexpected reply" } }
    }

    /// The optional per-cell API token (agent §1.6). Empty by default; set by the user in Settings and
    /// stored under "troubadour.apiToken". Sent as X-Troubadour-Token so writes work when a token is set.
    static var apiToken: String { UserDefaults.standard.string(forKey: "troubadour.apiToken") ?? "" }
    private func authed(_ r: inout URLRequest) {
        let t = Self.apiToken
        if !t.isEmpty { r.setValue(t, forHTTPHeaderField: "X-Troubadour-Token") }
    }

    func get(_ path: String, timeout: TimeInterval = 6) async throws -> [String: Any] {
        // with a second address, give the first a shorter leash so a dead wire costs seconds, not the whole timeout
        let lead = order.first, first = fallback == nil ? timeout : min(timeout, 3)
        return try await send(idempotent: true) { h in
            var r = URLRequest(url: url(path, host: h)); r.timeoutInterval = h == lead ? first : timeout
            authed(&r); return r
        }
    }
    @discardableResult
    func post(_ path: String, _ body: [String: Any] = [:], timeout: TimeInterval = 10) async throws -> [String: Any] {
        let payload = try JSONSerialization.data(withJSONObject: body)
        return try await send(idempotent: false) { h in
            var r = URLRequest(url: url(path, host: h)); r.httpMethod = "POST"; r.timeoutInterval = timeout
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            authed(&r); r.httpBody = payload; return r
        }
    }
    /// Raw upload (firmware image) with progress through a delegate-free streaming request.
    func upload(_ path: String, fileURL: URL, name: String) async throws -> [String: Any] {
        var r = URLRequest(url: url(path)); r.httpMethod = "POST"; r.timeoutInterval = 1800
        r.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        authed(&r)
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
    let serial: String; var ip: String; var wiredIP: String?; var name: String?; var role: String?; var agent: String?; var isMe: Bool; var calibrated: Bool?
    var id: String { serial }
    var shortSerial: String { String(serial.suffix(5)) }
    var title: String { name ?? "Cell \(shortSerial)" }
    init?(_ d: [String: Any]) {
        guard let s = d["serial"] as? String, let ip = d["ip"] as? String else { return nil }
        serial = s; self.ip = ip; wiredIP = (d["wired_ip"] as? String).flatMap { $0.isEmpty ? nil : $0 }; name = d["name"] as? String; role = d["role"] as? String; agent = d["agent"] as? String
        isMe = (d["isMe"] as? Bool) ?? false; calibrated = d["calibrated"] as? Bool
    }
    /// Talk to this speaker over its wire when it has one, its Wi-Fi otherwise (and as the fallback).
    var conn: Agent { wiredIP.map { Agent(ip: $0, fallback: ip) } ?? Agent(ip: ip) }
}

struct Room: Identifiable, Hashable {
    let id: String; var name: String; var primaryIP: String?; var primaryWiredIP: String?; var cells: [Cell]
    init?(_ d: [String: Any]) {
        guard let rid = d["room_id"] as? String else { return nil }
        id = rid; name = (d["room_name"] as? String) ?? (rid == "?" ? "Not in a room" : "Room")
        primaryIP = d["primary_ip"] as? String
        primaryWiredIP = (d["primary_wired_ip"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        cells = ((d["cells"] as? [[String: Any]]) ?? []).compactMap(Cell.init).filter { $0.agent != nil || $0.isMe }
    }
    /// The agent to talk to for this room: the primary (it owns the sources); any member relays anyway.
    /// Prefers the primary's wired address when it has one, with its Wi-Fi address as the fallback.
    var agent: Agent? {
        if let w = primaryWiredIP { return Agent(ip: w, fallback: primaryIP) }
        if let p = primaryIP { return Agent(ip: p) }
        return cells.first?.conn
    }
    /// Whether an address saved earlier (Shared.agentIP, a widget) belongs to this room's primary.
    func answers(_ ip: String?) -> Bool {
        guard let ip else { return false }
        return ip == primaryIP || ip == primaryWiredIP || ip == agent?.ip
    }
}

struct Source: Identifiable, Hashable {
    let id: String; var active: Bool; var playing: Bool; var title: String; var artist: String; var album: String; var volume: Double; var hasArtwork: Bool; var mute: Bool; var music: Bool
    /// What the source really produces, from its channel layout ("Dolby Atmos 7.1.4"); nil when idle. Syng's badge can't be trusted.
    var format: String?
    /// Playback position / length in seconds when the source reports them (AirPlay does).
    var position: Double?; var duration: Double?
    /// What the source lets us do right now ("play", "pause", "next", "previous"); empty = unknown, allow everything.
    var controls: [String]
    /// Syng's own metadata badges (the web page's `#mixbadge`): badging, mix type, layout.
    var badging: String?; var mixType: String?; var layout: String?
    /// Spatial flags on the source (enableHeightProcessing / enableBeyondStereoDuplication).
    var height: Bool?; var beyond: Bool?
    var displayName: String {
        switch id {
        case "airplay2": return "AirPlay"; case "spotify": return "Spotify"; case "tidal": return "TIDAL"
        case "hdmi", "hdmisource": return "HDMI"; case "usbgadget", "usbgadgetsource": return "USB-C"
        case "usbhost", "usbhostsource": return "USB in"; case "pd", "pdaudiosource": return "Pure Data"
        case "CalibrationClient": return "Calibrate"
        default: return id.capitalized
        }
    }
    /// The badge the web page shows: Syng's badging · mix type · layout, minus "none"/"unknown".
    var mixBadge: String {
        var seen: [String] = []
        for v in [badging, mixType, layout].compactMap({ $0 }) where !v.isEmpty && v != "none" && v != "unknown" && !seen.contains(v) { seen.append(v) }
        return seen.joined(separator: " · ")
    }
    func can(_ action: String) -> Bool { controls.isEmpty || controls.contains(action) }
    init(id: String, _ d: [String: Any]) {
        self.id = id; active = (d["active"] as? Bool) ?? false; playing = (d["playing"] as? Bool) ?? false
        title = (d["title"] as? String) ?? ""; artist = (d["artist"] as? String) ?? ""; album = (d["album"] as? String) ?? ""
        volume = (d["volume"] as? Double) ?? 0; hasArtwork = (d["artwork"] as? Bool) ?? false; mute = (d["mute"] as? Bool) ?? false
        music = (d["music"] as? Bool) ?? !["CalibrationClient", "saplayer"].contains(id)
        format = d["format"] as? String
        position = d["position"] as? Double; duration = d["duration"] as? Double
        controls = (d["controls"] as? [String]) ?? []
        badging = d["badging"] as? String; mixType = d["mixType"] as? String; layout = d["layout"] as? String
        height = d["height"] as? Bool; beyond = d["beyond"] as? Bool
    }
}

struct RoomState {
    var sources: [Source] = []; var soundMode = "syng"; var roomName = ""; var viaPrimary: String?; var sleepState = "off"
    /// Sleep timer: when it fires (epoch seconds) and the speaker's own clock, so "off in N min" is right whatever the phone's clock says.
    var sleepUntil: Double?; var sleepNow: Double?
    var volumeLimit = 0
    /// Soundcheck readiness (nil when the speaker does not report it): ready, room noise, long-term noise (dB).
    var calibReady: Bool?; var noiseRMS: Double?; var noiseLongTerm: Double?
    /// The speaker's own identity and health, straight from the state document (`core`, `system`, `sensors`).
    var core: [String: Any] = [:]; var system: [String: Any] = [:]; var sensors: [String: Any] = [:]
    var cloudOnline: Bool?
    var fleet: [[String: Any]] = []
    var tone: (bass: Double, tilt: Double) = (0, 0)
    init() {}
    init(_ d: [String: Any]) {
        sources = ((d["sources"] as? [String: [String: Any]]) ?? [:]).map { Source(id: $0.key, $0.value) }.filter(\.music).sorted { $0.id < $1.id }
        let settings = (d["settings"] as? [String: Any]) ?? [:]
        soundMode = (settings["sound_mode"] as? String) ?? "syng"
        volumeLimit = (settings["volume_limit"] as? Int) ?? (settings["volume_limit"] as? Double).map { Int($0) } ?? 0
        let room = (d["room"] as? [String: Any]) ?? [:]
        roomName = (room["name"] as? String) ?? ""
        let t = (room["tone"] as? [String: Any]) ?? [:]
        tone = ((t["bass"] as? Double) ?? 0, (t["tilt"] as? Double) ?? 0)
        viaPrimary = d["viaPrimary"] as? String
        let sl = (d["sleep"] as? [String: Any]) ?? [:]
        sleepState = (sl["state"] as? String) ?? "off"
        sleepUntil = sl["until"] as? Double; sleepNow = sl["now"] as? Double
        let cal = (d["calibration"] as? [String: Any]) ?? [:]
        calibReady = cal["ready"] as? Bool; noiseRMS = cal["noiseRMS"] as? Double; noiseLongTerm = cal["noiseLongTerm"] as? Double
        core = (d["core"] as? [String: Any]) ?? [:]
        system = (d["system"] as? [String: Any]) ?? [:]
        sensors = (d["sensors"] as? [String: Any]) ?? [:]
        cloudOnline = d["cloudOnline"] as? Bool
        fleet = (d["fleet"] as? [[String: Any]]) ?? []
    }
    var active: Source? { sources.first(where: \.active) }
    /// The source the web page's controls act on: the active one, else the first music source (mute, spatial flags).
    var rep: Source? { active ?? sources.first }
    var volume: Double { active?.volume ?? sources.first?.volume ?? 0 }
    /// "off in 12 min" / "fading out…" / nil when the timer is off.
    var sleepLabel: String? {
        if sleepState == "fading" { return "fading out…" }
        guard let u = sleepUntil, let n = sleepNow, u > n else { return nil }
        return "off in \(Int(((u - n) / 60).rounded())) min"
    }
}

/// Latest-wins sender for a stream of control values (the volume slider, a puck drag). At most one request is
/// in flight; while it runs, the newest value replaces whatever was queued and goes out the moment the reply
/// lands. Order is guaranteed (no reordered fire-and-forget Tasks) and the effective rate adapts to the
/// network's round trip instead of a fixed timer. `minGap` just keeps a fast LAN from flooding the cell.
/// Not tied to an actor: state sits behind a lock, so it can be used from FieldModel (nonisolated) and
/// RoomModel (@MainActor) alike. The lock is only taken in the small synchronous helpers below.
final class LatestSender: @unchecked Sendable {
    private let lock = NSLock()
    private var pending: [String: Any]?
    private var running = false
    private var lastSent = Date.distantPast
    private var activity = Date.distantPast
    private let minGap: TimeInterval
    init(minGap: TimeInterval = 0.04) { self.minGap = minGap }

    /// When the user last touched the control — callers use it to ignore stale polled state for a while.
    var lastActivity: Date { lock.lock(); defer { lock.unlock() }; return activity }

    func submit(_ body: [String: Any], _ send: @escaping ([String: Any]) async -> Void) {
        guard enqueue(body) else { return }          // a sender loop is already running; it will pick this up
        Task.detached(priority: .userInitiated) { [self] in
            while let item = next() {
                let (b, gap) = item
                if gap > 0 { try? await Task.sleep(nanoseconds: UInt64(gap * 1e9)) }
                markSent()
                await send(b)
            }
        }
    }

    /// Store the newest body; true when the caller must start the loop.
    private func enqueue(_ body: [String: Any]) -> Bool {
        lock.lock(); defer { lock.unlock() }
        pending = body; activity = Date()
        if running { return false }
        running = true; return true
    }
    /// The next body to send and how long to wait first, or nil (and the loop ends) when nothing is queued.
    private func next() -> ([String: Any], TimeInterval)? {
        lock.lock(); defer { lock.unlock() }
        guard let b = pending else { running = false; return nil }
        pending = nil
        return (b, minGap - Date().timeIntervalSince(lastSent))
    }
    private func markSent() { lock.lock(); lastSent = Date(); lock.unlock() }
}
