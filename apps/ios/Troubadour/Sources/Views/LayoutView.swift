import SwiftUI

/// Where the speakers are in the room, and measuring it. The same document the Syng app edits:
/// each cell's position in metres and its rotation, the reference circle the sound field is drawn
/// against, and the listener marker — plus Soundcheck, which measures all of it (docs/SETUP.md).
struct LayoutView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @StateObject private var cal: Endpoint
    @State private var pos: [String: CGPoint] = [:]       // metres, y up
    @State private var rot: [String: Double] = [:]        // degrees
    @State private var listener: CGPoint?
    @State private var circle = Circle4(x: 0, y: 0, r: 1.15, rot: 0)
    @State private var selected: String?
    @State private var dirty = false
    @State private var dragStart: CGPoint?
    @State private var confirmCal: Int?

    struct Circle4 { var x: Double; var y: Double; var r: Double; var rot: Double }

    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/room", every: 10))
        _cal = StateObject(wrappedValue: Endpoint(room.agent, "/api/calibration/status", every: 3))
    }

    private var cells: [[String: Any]] { e.data.list("cells") }
    private var running: Bool { cal.data.flag("running") }

    var body: some View {
        SetupPage(title: "Layout", e: e) {
            Card(nil) {
                GeometryReader { geo in
                    canvas(geo.size)
                }
                .frame(height: 320)
                Text(dirty ? "Moved — save to send it to the speakers." : "Drag a speaker to move it. Tap one to turn it.")
                    .font(.caption).foregroundStyle(dirty ? Theme.warn : Theme.muted)
            }
            if let s = selected, let cell = cells.first(where: { $0.str("serial") == s }) {
                Card(cell.str("name") ?? String(s.suffix(5))) {
                    HStack {
                        Text("Facing").font(.subheadline).foregroundStyle(Theme.muted)
                        Slider(value: Binding(get: { rot[s] ?? 0 }, set: { rot[s] = $0; dirty = true }), in: 0...360, step: 1).tint(.white)
                        Text("\(Int(rot[s] ?? 0))°").font(.caption.monospacedDigit()).frame(width: 42, alignment: .trailing)
                    }
                    HStack {
                        Button("Identify") { e.fire("/api/ping", ["serial": s]) }.buttonStyle(Chip())
                        Button("Done") { selected = nil }.buttonStyle(Chip())
                    }
                }
            }
            Card("The room") {
                HStack {
                    Button(dirty ? "Save layout" : "Saved") { save() }.buttonStyle(Capsule_(filled: dirty)).disabled(!dirty)
                    Button("Revert") { load(e.data, force: true); dirty = false }.buttonStyle(Capsule_()).disabled(!dirty)
                }
                HStack {
                    Button("Fit a circle") { e.fire("/api/room/layout", ["auto_circle": true, "auto_listener": true], note: "Circle fitted") }
                        .buttonStyle(Capsule_())
                    Button("Hear it") { e.fire("/api/room/preview", ["reference_circle": ["x_m": circle.x, "y_m": circle.y, "radius_m": circle.r, "rotation_deg": circle.rot]]) }
                        .buttonStyle(Capsule_())
                }
                Text(String(format: "circle (%.2f, %.2f) r %.2f m · rotation %.0f°", circle.x, circle.y, circle.r, circle.rot))
                    .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
            }
            Card("Soundcheck") {
                HStack {
                    Button("Measure ×3") { confirmCal = 3 }.buttonStyle(Capsule_(filled: true)).disabled(running)
                    Button("Once") { confirmCal = 1 }.buttonStyle(Capsule_()).disabled(running)
                }
                if running {
                    HStack(spacing: 8) {
                        ProgressView().tint(.white)
                        Text(runLabel).font(.caption).foregroundStyle(Theme.warn)
                    }
                }
                if let c = cal.data["combine"] as? [String: Any] {
                    Text(String(format: "residual %.3f · reference %@ · %d runs",
                                c.num("residual") ?? 0, c.str("reference") ?? "—", c.int("runs") ?? 1))
                        .font(.caption.monospacedDigit())
                    ForEach(pairRows(c).prefix(4), id: \.self) { line in
                        Text(line).font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
                    }
                }
                if !cal.data.strings("log").isEmpty {
                    Text(cal.data.strings("log").suffix(6).joined(separator: "\n"))
                        .font(.caption2.monospaced()).foregroundStyle(Theme.muted)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                Text("Each speaker chirps in turn and they listen to each other; three passes are combined on the median, which is what fixed the layout drifting between runs. Playback pauses and comes back on its own.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .onAppear { cal.start(); load(e.data, force: false) }
        .onDisappear { cal.stop() }
        .onReceive(e.$data.dropFirst()) { fresh in load(fresh, force: false) }
        .alert("Run Soundcheck?", isPresented: Binding(get: { confirmCal != nil }, set: { if !$0 { confirmCal = nil } })) {
            Button("Measure") { if let r = confirmCal { cal.fire("/api/calibration/run", ["runs": r], note: "Measuring…") } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The speakers play test sounds for a minute or two" + (confirmCal == 3 ? ", three times over." : "."))
        }
    }

    private var runLabel: String {
        let r = cal.data.int("run") ?? 1
        let n = cal.data.int("runs") ?? 1
        return n > 1 ? "Measuring, pass \(r) of \(n)…" : "Measuring…"
    }

    private func pairRows(_ c: [String: Any]) -> [String] {
        c.list("pairs").compactMap { p in
            guard let a = p.str("a"), let b = p.str("b") else { return nil }
            let d = p.num("distance_m") ?? 0, sp = p.num("spread_m") ?? 0, rc = p.num("reciprocity_m") ?? 0
            return String(format: "%@–%@  %.2f m  ±%.2f  rec %.2f", String(a.suffix(5)), String(b.suffix(5)), d, sp, rc)
        }
    }

    // MARK: - the plan

    private func canvas(_ size: CGSize) -> some View {
        let m = metrics(size)
        return ZStack(alignment: .topLeading) {
            Canvas { ctx, _ in
                // metre rings around the circle centre
                for r in stride(from: 1.0, through: 6.0, by: 1.0) {
                    let rr = r * m.scale
                    if rr > max(size.width, size.height) { break }
                    let rect = CGRect(x: m.centre.x - rr, y: m.centre.y - rr, width: rr * 2, height: rr * 2)
                    ctx.stroke(Path(ellipseIn: rect), with: .color(.white.opacity(0.05)), lineWidth: 1)
                }
                // the reference circle
                let cr = circle.r * m.scale
                let cc = m.point(CGPoint(x: circle.x, y: circle.y))
                ctx.stroke(Path(ellipseIn: CGRect(x: cc.x - cr, y: cc.y - cr, width: cr * 2, height: cr * 2)),
                           with: .color(.white.opacity(0.22)), lineWidth: 1.5)
                // the listener
                if let l = listener {
                    let p = m.point(l)
                    ctx.fill(Path(ellipseIn: CGRect(x: p.x - 4, y: p.y - 4, width: 8, height: 8)),
                             with: .color(.white.opacity(0.8)))
                }
            }
            ForEach(cells.indices, id: \.self) { i in
                speaker(cells[i], m)
            }
        }
        .contentShape(Rectangle())
    }

    private struct Metrics {
        var centre: CGPoint
        var scale: Double
        var originM: CGPoint
        func point(_ p: CGPoint) -> CGPoint {
            CGPoint(x: centre.x + (p.x - originM.x) * scale, y: centre.y - (p.y - originM.y) * scale)
        }
    }

    private func metrics(_ size: CGSize) -> Metrics {
        let o = CGPoint(x: circle.x, y: circle.y)
        var span = circle.r + 0.6
        for p in pos.values { span = max(span, hypot(Double(p.x - o.x), Double(p.y - o.y)) + 0.5) }
        if let l = listener { span = max(span, hypot(Double(l.x - o.x), Double(l.y - o.y)) + 0.4) }
        let scale = Double(min(size.width, size.height)) / (2 * max(span, 0.8))
        return Metrics(centre: CGPoint(x: size.width / 2, y: size.height / 2), scale: scale, originM: o)
    }

    private func speaker(_ c: [String: Any], _ m: Metrics) -> some View {
        let serial = c.str("serial") ?? ""
        let p = m.point(pos[serial] ?? .zero)
        let isPri = (c.str("role") ?? "").lowercased() == "primary"
        let heading = (rot[serial] ?? 0) * .pi / 180
        return ZStack {
            Puck(size: 34, ring: selected == serial ? Theme.live : (isPri ? .white : nil))
            Circle().fill(Color.white.opacity(0.85)).frame(width: 5, height: 5)
                .offset(x: CGFloat(sin(heading)) * 21, y: CGFloat(-cos(heading)) * 21)
            Text(c.str("name").map { String($0.prefix(10)) } ?? String(serial.suffix(5)))
                .font(.caption2).foregroundStyle(Theme.muted)
                .fixedSize()
                .offset(y: 30)
        }
        .position(p)
        .gesture(
            DragGesture(minimumDistance: 1)
                .onChanged { g in
                    if dragStart == nil { dragStart = pos[serial] ?? .zero; selected = serial }
                    let s = dragStart ?? .zero
                    pos[serial] = CGPoint(x: s.x + g.translation.width / CGFloat(m.scale),
                                          y: s.y - g.translation.height / CGFloat(m.scale))
                    dirty = true
                }
                .onEnded { _ in dragStart = nil; Haptics.tap(0.5) }
        )
        .onTapGesture { selected = (selected == serial) ? nil : serial; Haptics.select() }
    }

    // MARK: - document in, document out

    private func load(_ d: [String: Any], force: Bool) {
        guard force || !dirty else { return }
        let fresh = d.list("cells")
        guard !fresh.isEmpty else { return }
        var np: [String: CGPoint] = [:], nr: [String: Double] = [:]
        for c in fresh {
            guard let s = c.str("serial") else { continue }
            let p = c.obj("position")
            np[s] = CGPoint(x: p.num("x") ?? 0, y: p.num("y") ?? 0)
            nr[s] = p.num("rotation") ?? 0
        }
        pos = np; rot = nr
        let rs = d.obj("room_state")
        let rc = rs.obj("reference_circle")
        circle = Circle4(x: rc.num("x_m") ?? 0, y: rc.num("y_m") ?? 0,
                         r: max(rc.num("radius_m") ?? 1.15, 0.2), rot: rc.num("rotation_deg") ?? 0)
        let lp = rs.obj("listener_position")
        listener = (lp["x_m"] as? Double).map { CGPoint(x: $0, y: (lp.num("y_m") ?? 0)) }
    }

    private func save() {
        var body: [String: Any] = [:]
        var cellBody: [String: Any] = [:]
        for (serial, p) in pos {
            cellBody[serial] = ["x": Double(p.x), "y": Double(p.y), "rotation": rot[serial] ?? 0]
        }
        body["cells"] = cellBody
        if let l = listener { body["listener_position"] = ["x_m": Double(l.x), "y_m": Double(l.y)] }
        dirty = false
        e.fire("/api/room/layout", body, note: "Layout saved")
    }
}
