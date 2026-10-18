import Foundation

/// Shared between the app and the widget extension through an App Group: the room the app last opened.
/// Widgets and their buttons act on this room.
enum Shared {
    static let group = "group.space.troubadour"
    static var defaults: UserDefaults { UserDefaults(suiteName: group) ?? .standard }
    static var agentIP: String? {
        get { defaults.string(forKey: "lastAgent") }
        set { defaults.set(newValue, forKey: "lastAgent") }
    }
    static var roomName: String? {
        get { defaults.string(forKey: "lastRoom") }
        set { defaults.set(newValue, forKey: "lastRoom") }
    }
    /// What the last widget button did ("Centre ✓ 09:58" / "Wider ✗ HTTP 500…"), shown on the widget.
    static var lastAction: String? {
        get { defaults.string(forKey: "lastAction") }
        set { defaults.set(newValue, forKey: "lastAction") }
    }
    static func note(_ what: String, _ error: Error?) {
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"
        lastAction = error == nil ? "\(what) ✓ \(f.string(from: .now))" : "\(what) ✗ \(error!.localizedDescription)"
    }
}

// MARK: - what the widgets draw (decoded from /api/state, /api/room, /api/led)

struct FieldState {
    var ux = 0.0, uy = 0.0, rot = 90.0, width = 95.0, radius = 0.0, spatial = false
    init() {}
    init(_ d: [String: Any]) {
        ux = (d["ux"] as? Double) ?? 0; uy = (d["uy"] as? Double) ?? 0; rot = (d["rot"] as? Double) ?? 90
        width = (d["width"] as? Double) ?? 95; radius = (d["radius"] as? Double) ?? 0; spatial = (d["spatial"] as? Bool) ?? false
    }
}

struct RoomSnapshot {
    var room = "Troubadour"
    var field = FieldState()
    var speakers: [(x: Double, y: Double, primary: Bool)] = []   // in reference-circle units, circle centre = 0, y up
    var listener: (x: Double, y: Double) = (0, 0)
    var circleRotation = 0.0
    var ledMode: String? = nil      // nil = manual / off
    var ledModes: [String] = ["pulse", "party", "flow", "groove", "bloom", "shimmer", "spectrum", "vu", "beat"]
    var playing: String? = nil
    var reachable = false

    /// One round trip per endpoint, in parallel; anything that fails just leaves its part blank.
    static func fetch() async -> RoomSnapshot {
        var s = RoomSnapshot(); s.room = Shared.roomName ?? "Troubadour"
        guard let ip = Shared.agentIP else { return s }
        let a = Agent(ip: ip)
        async let st = try? a.get("/api/state", timeout: 4)
        async let rm = try? a.get("/api/room", timeout: 4)
        async let ld = try? a.get("/api/led", timeout: 4)
        if let st = await st {
            s.reachable = true
            let room = (st["room"] as? [String: Any]) ?? [:]
            if let f = room["field"] as? [String: Any] { s.field = FieldState(f) }
            if let n = room["name"] as? String, !n.isEmpty { s.room = n }
            if let src = st["sources"] as? [String: [String: Any]],
               let act = src.first(where: { ($0.value["active"] as? Bool) == true }) {
                let t = (act.value["title"] as? String) ?? ""
                s.playing = ((act.value["playing"] as? Bool) == true) ? (t.isEmpty ? act.key : t) : nil
            }
        }
        if let rm = await rm {
            let rs = (rm["room_state"] as? [String: Any]) ?? [:]
            let rc = (rs["reference_circle"] as? [String: Any]) ?? [:]
            let cx = (rc["x_m"] as? Double) ?? 0, cy = (rc["y_m"] as? Double) ?? 0, r = max((rc["radius_m"] as? Double) ?? 1, 0.1)
            s.circleRotation = (rc["rotation_deg"] as? Double) ?? 0
            let lp = (rs["listener_position"] as? [String: Any]) ?? [:]
            let lx = (lp["x_m"] as? Double) ?? cx, ly = (lp["y_m"] as? Double) ?? cy
            s.listener = (x: (lx - cx) / r, y: (ly - cy) / r)
            for c in (rm["cells"] as? [[String: Any]]) ?? [] {
                guard let p = c["position"] as? [String: Any], let x = p["x"] as? Double, let y = p["y"] as? Double else { continue }
                s.speakers.append((x: (x - cx) / r, y: (y - cy) / r, primary: (c["role"] as? String)?.lowercased() == "primary"))
            }
        }
        if let ld = await ld {
            s.ledMode = (ld["reactive"] as? Bool) == true ? (ld["mode"] as? String) : nil
            if let m = ld["reactiveModes"] as? [String], !m.isEmpty { s.ledModes = m }
        }
        return s
    }
}
