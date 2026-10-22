import SwiftUI

/// Where the speakers are in the room, and measuring it. The same document the Syng app edits:
/// each cell's position in metres (x, y, z) and its rotation, whether it hangs from the ceiling and which
/// height layer it is on, the reference circle the sound field is drawn against, and the listener marker —
/// plus Soundcheck, which measures all of it (docs/SETUP.md). Only what changed is sent on save, as the
/// web page does; the circle is previewed on the speakers while it is being changed.
struct LayoutView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @StateObject private var cal: Endpoint
    @State private var pos: [String: CGPoint] = [:]       // metres, y up
    @State private var z: [String: Double] = [:]          // metres
    @State private var rot: [String: Double] = [:]        // degrees
    @State private var ceiling: [String: Bool] = [:]      // is_upside_down
    @State private var layer: [String: Int] = [:]         // height_layer
    @State private var listener: CGPoint?
    @State private var circle = Circle4(x: 0, y: 0, r: 1.15, rot: 0)
    @State private var selected: String?
    @State private var dirtyCells: Set<String> = []
    @State private var dirtyCircle = false
    @State private var dirtyListener = false
    @State private var dragStart: CGPoint?
    @State private var circleStart: CGPoint?
    @State private var confirmCal: Int?
    @State private var identifying = false
    @State private var identifyTask: Task<Void, Never>?
    @State private var identifyIndex = 0
    @State private var highlighted: String?
    @State private var previewAt = Date.distantPast
    @State private var showLog = false
    @State private var confirmRemove: String?

    struct Circle4 { var x: Double; var y: Double; var r: Double; var rot: Double }

    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/room", every: 10))
        _cal = StateObject(wrappedValue: Endpoint(room.agent, "/api/calibration/status", every: 3))
    }

    private var cells: [[String: Any]] { e.data.list("cells") }
    private var running: Bool { cal.data.flag("running") }
    private var dirty: Bool { !dirtyCells.isEmpty || dirtyCircle || dirtyListener }

    var body: some View {
        SetupPage(title: "Layout", e: e) {
            Card(nil) {
                GeometryReader { geo in
                    canvas(geo.size)
                }
                .frame(height: 340)
                Text(dirty ? "Changed — save to send it to the speakers." : "Drag a speaker to move it, tap one for its details. Drag the dot to move the listener; drag the background to move the circle.")
                    .font(.caption).foregroundStyle(dirty ? Theme.warn : Theme.muted)
            }
            if let s = selected, let cell = cells.first(where: { $0.str("serial") == s }) {
                cellCard(s, cell)
            }
            Card("The circle") {
                HStack {
                    Text("Radius").font(.subheadline).foregroundStyle(Theme.muted).frame(width: 64, alignment: .leading)
                    Slider(value: Binding(get: { circle.r }, set: { circle.r = $0; dirtyCircle = true; preview() }), in: 0.3...4.0, step: 0.01).tint(.white)
                    Text(String(format: "%.2f m", circle.r)).font(.caption.monospacedDigit()).frame(width: 56, alignment: .trailing)
                }
                HStack {
                    Text("Rotation").font(.subheadline).foregroundStyle(Theme.muted).frame(width: 64, alignment: .leading)
                    Slider(value: Binding(get: { circle.rot }, set: { circle.rot = $0; dirtyCircle = true; preview() }), in: 0...360, step: 1).tint(.white)
                    Text("\(Int(circle.rot))°").font(.caption.monospacedDigit()).frame(width: 56, alignment: .trailing)
                    Button { circle.rot = (circle.rot + 270).truncatingRemainder(dividingBy: 360); dirtyCircle = true; preview() } label: { Image(systemName: "arrow.counterclockwise") }.buttonStyle(Chip())
                    Button { circle.rot = (circle.rot + 90).truncatingRemainder(dividingBy: 360); dirtyCircle = true; preview() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(Chip())
                }
                HStack {
                    Button(dirty ? "Save layout" : "Saved") { save() }.buttonStyle(Capsule_(filled: dirty)).disabled(!dirty)
                    Button("Revert") { load(e.data, force: true) }.buttonStyle(Capsule_()).disabled(!dirty)
                }
                HStack {
                    Button("Fit a circle") { e.fire("/api/room/layout", ["auto_circle": true, "auto_listener": true], note: "Circle fitted") }
                        .buttonStyle(Capsule_())
                    Button("Hear it") { e.fire("/api/room/preview", ["reference_circle": circleBody]) }
                        .buttonStyle(Capsule_())
                    Button(identifying ? "Stop" : "Identify in turn") { identifying ? stopIdentify() : startIdentify() }
                        .buttonStyle(Capsule_(filled: identifying))
                }
                Text(String(format: "circle (%.2f, %.2f) r %.2f m · rotation %.0f°", circle.x, circle.y, circle.r, circle.rot) + (listener.map { String(format: " · listener (%.2f, %.2f)", $0.x, $0.y) } ?? ""))
                    .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
            }
            Card("Speakers") {
                ForEach(cells.indices, id: \.self) { i in tableRow(cells[i]) }
                Text("Tap a row for its position, height, facing, ceiling mount and height layer. The speakers you cannot see on the plan are off its edge.")
                    .font(.caption).foregroundStyle(Theme.muted)
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
                } else if let ok = cal.data["ok"] as? Bool {
                    Text(lastLine(ok)).font(.caption).foregroundStyle(ok ? Theme.live : Theme.bad)
                }
                if let c = cal.data["combine"] as? [String: Any] {
                    Text(String(format: "residual %.3f · reference %@ · %d runs",
                                c.num("residual") ?? 0, c.str("reference") ?? "—", c.int("runs") ?? 1))
                        .font(.caption.monospacedDigit())
                    ForEach(pairRows(c).prefix(6), id: \.self) { line in
                        Text(line).font(.caption2.monospacedDigit()).foregroundStyle(Theme.muted)
                    }
                }
                if !cal.data.strings("log").isEmpty {
                    Button(showLog ? "Hide the log" : "Show the log (\(cal.data.strings("log").count) lines)") { showLog.toggle() }.buttonStyle(Chip())
                    if showLog {
                        ScrollView { Text(cal.data.strings("log").joined(separator: "\n")).font(.caption2.monospaced()).foregroundStyle(Theme.muted).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled) }
                            .frame(maxHeight: 260)
                    } else {
                        Text(cal.data.strings("log").suffix(4).joined(separator: "\n"))
                            .font(.caption2.monospaced()).foregroundStyle(Theme.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                Text("Each speaker chirps in turn and they listen to each other; three passes are combined on the median, which is what fixed the layout drifting between runs. Playback pauses and comes back on its own.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .onAppear { cal.start(); load(e.data, force: false) }
        .onDisappear { cal.stop(); stopIdentify() }
        .onReceive(e.$data.dropFirst()) { fresh in load(fresh, force: false) }
        .onReceive(cal.$data.dropFirst()) { d in
            // the room document changes under a finished Soundcheck: pick it up (unless we are mid-edit)
            if !d.flag("running"), let f = d.num("finished"), Date().timeIntervalSince1970 - f < 30, !dirty { e.reload() }
        }
        .alert("Run Soundcheck?", isPresented: Binding(get: { confirmCal != nil }, set: { if !$0 { confirmCal = nil } })) {
            Button("Measure") { if let r = confirmCal { cal.fire("/api/calibration/run", ["runs": r], note: "Measuring…") } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The speakers play test sounds for a minute or two" + (confirmCal == 3 ? ", three times over." : "."))
        }
        .alert("Remove from the room?", isPresented: Binding(get: { confirmRemove != nil }, set: { if !$0 { confirmRemove = nil } })) {
            Button("Remove", role: .destructive) { if let s = confirmRemove { e.fire("/api/room/remove", ["serial": s], note: "Removed") } }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var runLabel: String {
        let r = cal.data.int("run") ?? 1
        let n = cal.data.int("runs") ?? 1
        var t = n > 1 ? "Measuring, pass \(r) of \(n)…" : "Measuring…"
        if let s = cal.data.num("started") { t += " \(Int(Date().timeIntervalSince1970 - s)) s" }
        return t
    }
    private func lastLine(_ ok: Bool) -> String {
        var t = ok ? "Last Soundcheck ok" : "Last Soundcheck failed"
        if let f = cal.data.num("finished"), let s = cal.data.num("started"), f > s { t += String(format: " · took %.0f s", f - s) }
        if let f = cal.data.num("finished") { t += " · " + Date(timeIntervalSince1970: f).formatted(date: .abbreviated, time: .shortened) }
        return t
    }

    private func pairRows(_ c: [String: Any]) -> [String] {
        c.list("pairs").compactMap { p in
            guard let a = p.str("a"), let b = p.str("b") else { return nil }
            let d = p.num("distance_m") ?? 0, sp = p.num("spread_m") ?? 0, rc = p.num("reciprocity_m") ?? 0
            return String(format: "%@–%@  %.2f m  ±%.2f  rec %.2f", String(a.suffix(5)), String(b.suffix(5)), d, sp, rc)
        }
    }

    // MARK: - the selected speaker, and the table

    private func cellCard(_ s: String, _ cell: [String: Any]) -> some View {
        Card(cell.str("name") ?? String(s.suffix(5))) {
            HStack {
                numberField("X", Binding(get: { Double(pos[s]?.x ?? 0) }, set: { pos[s] = CGPoint(x: CGFloat($0), y: pos[s]?.y ?? 0); dirtyCells.insert(s) }))
                numberField("Y", Binding(get: { Double(pos[s]?.y ?? 0) }, set: { pos[s] = CGPoint(x: pos[s]?.x ?? 0, y: CGFloat($0)); dirtyCells.insert(s) }))
                numberField("Z", Binding(get: { z[s] ?? 0 }, set: { z[s] = $0; dirtyCells.insert(s) }))
            }
            HStack {
                Text("Facing").font(.subheadline).foregroundStyle(Theme.muted)
                Slider(value: Binding(get: { rot[s] ?? 0 }, set: { rot[s] = $0; dirtyCells.insert(s) }), in: 0...360, step: 1).tint(.white)
                Text("\(Int(rot[s] ?? 0))°").font(.caption.monospacedDigit()).frame(width: 42, alignment: .trailing)
            }
            Toggle("Hangs from the ceiling (upside down)", isOn: Binding(get: { ceiling[s] ?? false }, set: { ceiling[s] = $0; dirtyCells.insert(s) }))
                .font(.subheadline).tint(Theme.live)
            HStack {
                Text("Height layer").font(.subheadline).foregroundStyle(Theme.muted)
                Spacer()
                Picker("", selection: Binding(get: { layer[s] ?? 0 }, set: { layer[s] = $0; dirtyCells.insert(s) })) {
                    Text("Floor / shelf").tag(0); Text("Up high").tag(1); Text("Ceiling").tag(2)
                }
                .pickerStyle(.menu).tint(.white)
            }
            HStack {
                Button("Identify") { e.fire("/api/ping", ["serial": s]) }.buttonStyle(Chip())
                if (cell.str("role") ?? "").lowercased() != "primary" {
                    Button("Make primary") { e.fire("/api/room/pin", ["serial": s, "role": "primary"], note: "Primary pinned") }.buttonStyle(Chip())
                    Button("Remove") { confirmRemove = s }.buttonStyle(Chip())
                }
                Spacer()
                Button("Done") { selected = nil }.buttonStyle(Chip())
            }
        }
    }
    private func numberField(_ label: String, _ v: Binding<Double>) -> some View {
        HStack(spacing: 4) {
            Text(label).font(.caption).foregroundStyle(Theme.muted)
            TextField("0", value: v, format: .number.precision(.fractionLength(2))).font(.subheadline.monospacedDigit())
                .keyboardType(.numbersAndPunctuation).textFieldStyle(.roundedBorder)
        }
    }
    private func tableRow(_ c: [String: Any]) -> some View {
        let s = c.str("serial") ?? ""
        let p = pos[s] ?? .zero
        let isPri = (c.str("role") ?? "").lowercased() == "primary"
        return Button { selected = (selected == s) ? nil : s } label: {
            HStack(spacing: 10) {
                Puck(size: 26, ring: isPri ? .white : nil)
                VStack(alignment: .leading, spacing: 1) {
                    Text(c.str("name") ?? String(s.suffix(5))).font(.subheadline.weight(.semibold)).foregroundStyle(.white)
                    Text(String(format: "x %.2f  y %.2f  z %.2f  · %.0f°%@%@%@", p.x, p.y, z[s] ?? 0, rot[s] ?? 0,
                                (ceiling[s] ?? false) ? " · ceiling" : "", (layer[s] ?? 0) > 0 ? " · layer \(layer[s] ?? 0)" : "",
                                c["online"] as? Bool == false ? " · offline" : ""))
                        .font(.caption2.monospacedDigit()).foregroundStyle(dirtyCells.contains(s) ? Theme.warn : Theme.muted)
                }
                Spacer()
                Image(systemName: selected == s ? "chevron.up" : "chevron.down").font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .buttonStyle(.plain)
        .padding(.vertical, 3)
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
                // the reference circle and its heading tick
                let cr = circle.r * m.scale
                let cc = m.point(CGPoint(x: circle.x, y: circle.y))
                ctx.stroke(Path(ellipseIn: CGRect(x: cc.x - cr, y: cc.y - cr, width: cr * 2, height: cr * 2)),
                           with: .color(.white.opacity(dirtyCircle ? 0.5 : 0.22)), lineWidth: 1.5)
                let a = circle.rot * .pi / 180
                var tick = Path(); tick.move(to: cc); tick.addLine(to: CGPoint(x: cc.x + sin(a) * cr, y: cc.y - cos(a) * cr))
                ctx.stroke(tick, with: .color(.white.opacity(0.18)), style: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                // walls the speakers heard (nearby_walls on each cell)
                for c in cells {
                    guard let s = c.str("serial"), let p = pos[s] else { continue }
                    for w in c.list("nearby_walls") where w.flag("close_to_wall") || (w.num("likelihood") ?? 0) > 0.5 {
                        let ang = ((w.num("angle_deg") ?? 0) + (rot[s] ?? 0)) * .pi / 180, d = w.num("distance_m") ?? 0
                        let foot = CGPoint(x: p.x + sin(ang) * d, y: p.y + cos(ang) * d)
                        let along = CGPoint(x: cos(ang), y: -sin(ang))
                        let p0 = m.point(CGPoint(x: foot.x - along.x * 0.6, y: foot.y - along.y * 0.6))
                        let p1 = m.point(CGPoint(x: foot.x + along.x * 0.6, y: foot.y + along.y * 0.6))
                        var wall = Path(); wall.move(to: p0); wall.addLine(to: p1)
                        ctx.stroke(wall, with: .color(Theme.warn.opacity(0.35 + 0.5 * (w.num("likelihood") ?? 0.5))), lineWidth: 3)
                    }
                }
                // scale bar: one metre
                let bx = size.width - 16 - m.scale, by = size.height - 12
                var bar = Path(); bar.move(to: CGPoint(x: bx, y: by)); bar.addLine(to: CGPoint(x: bx + m.scale, y: by))
                ctx.stroke(bar, with: .color(.white.opacity(0.35)), lineWidth: 1)
                ctx.draw(Text("1 m").font(.system(size: 9)).foregroundColor(Theme.muted), at: CGPoint(x: bx + m.scale / 2, y: by - 7))
            }
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 2)
                    .onChanged { g in
                        if circleStart == nil { circleStart = CGPoint(x: circle.x, y: circle.y) }
                        let s = circleStart ?? .zero
                        circle.x = s.x + g.translation.width / m.scale
                        circle.y = s.y - g.translation.height / m.scale
                        dirtyCircle = true; preview()
                    }
                    .onEnded { _ in circleStart = nil; Haptics.tap(0.4) }
            )
            if let l = listener {
                let lp = m.point(l)
                Circle().fill(Color.white.opacity(dirtyListener ? 1 : 0.8)).frame(width: 12, height: 12)
                    .overlay(Circle().stroke(Color.black.opacity(0.5), lineWidth: 1))
                    .position(lp)
                    .gesture(
                        DragGesture(minimumDistance: 1)
                            .onChanged { g in
                                if dragStart == nil { dragStart = l }
                                let s = dragStart ?? l
                                listener = CGPoint(x: s.x + g.translation.width / m.scale, y: s.y - g.translation.height / m.scale)
                                dirtyListener = true
                            }
                            .onEnded { _ in dragStart = nil; Haptics.tap(0.5) }
                    )
            }
            ForEach(cells.indices, id: \.self) { i in
                speaker(cells[i], m)
            }
        }
        .clipped()
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
        let hot = highlighted == serial
        return ZStack {
            if hot { Circle().stroke(Theme.live, lineWidth: 2).frame(width: 52, height: 52) }
            Puck(size: 34, ring: selected == serial ? Theme.live : (isPri ? .white : nil))
                .opacity(c["online"] as? Bool == false ? 0.45 : 1)
            Circle().fill(Color.white.opacity(0.85)).frame(width: 5, height: 5)
                .offset(x: CGFloat(sin(heading)) * 21, y: CGFloat(-cos(heading)) * 21)
            if ceiling[serial] ?? false {
                Image(systemName: "arrow.down.to.line").font(.system(size: 8)).foregroundStyle(Theme.muted).offset(x: 16, y: -16)
            }
            Text(c.str("name").map { String($0.prefix(10)) } ?? String(serial.suffix(5)))
                .font(.caption2).foregroundStyle(dirtyCells.contains(serial) ? Theme.warn : Theme.muted)
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
                    dirtyCells.insert(serial)
                }
                .onEnded { _ in dragStart = nil; Haptics.tap(0.5) }
        )
        .onTapGesture { selected = (selected == serial) ? nil : serial; Haptics.select() }
    }

    // MARK: - identify in turn, preview

    private func startIdentify() {
        identifying = true
        identifyTask = Task { @MainActor in
            while !Task.isCancelled {
                let list = cells.compactMap { $0.str("serial") }
                guard !list.isEmpty else { break }
                let s = list[identifyIndex % list.count]; identifyIndex += 1
                highlighted = s
                _ = try? await room.agent?.post("/api/ping", ["serial": s], timeout: 8)
                try? await Task.sleep(nanoseconds: 2_600_000_000)
            }
            highlighted = nil
        }
    }
    private func stopIdentify() { identifying = false; identifyTask?.cancel(); identifyTask = nil; highlighted = nil }

    private var circleBody: [String: Any] { ["x_m": circle.x, "y_m": circle.y, "radius_m": circle.r, "rotation_deg": circle.rot] }
    /// While the circle is being changed the speakers hear it live (throttled like the web page's 90 ms).
    private func preview() {
        guard Date().timeIntervalSince(previewAt) > 0.12 else { return }
        previewAt = Date()
        Task { _ = try? await room.agent?.post("/api/room/preview", ["reference_circle": circleBody], timeout: 4) }
    }

    // MARK: - document in, document out

    private func load(_ d: [String: Any], force: Bool) {
        guard force || !dirty else { return }
        let fresh = d.list("cells")
        guard !fresh.isEmpty else { return }
        var np: [String: CGPoint] = [:], nz: [String: Double] = [:], nr: [String: Double] = [:], nc: [String: Bool] = [:], nl: [String: Int] = [:]
        for c in fresh {
            guard let s = c.str("serial") else { continue }
            let p = c.obj("position")
            np[s] = CGPoint(x: p.num("x") ?? 0, y: p.num("y") ?? 0)
            nz[s] = p.num("z") ?? 0
            nr[s] = p.num("rotation") ?? 0
            nc[s] = c.flag("is_upside_down")
            nl[s] = c.int("height_layer") ?? 0
        }
        pos = np; z = nz; rot = nr; ceiling = nc; layer = nl
        let rs = d.obj("room_state")
        let rc = rs.obj("reference_circle")
        circle = Circle4(x: rc.num("x_m") ?? 0, y: rc.num("y_m") ?? 0,
                         r: max(rc.num("radius_m") ?? 1.15, 0.2), rot: rc.num("rotation_deg") ?? 0)
        let lp = rs.obj("listener_position")
        listener = (lp["x_m"] as? Double).map { CGPoint(x: $0, y: (lp.num("y_m") ?? 0)) }
        dirtyCells = []; dirtyCircle = false; dirtyListener = false
    }

    /// Only what changed goes out, the way the web page does it; the primary fans it out.
    private func save() {
        var body: [String: Any] = [:]
        if !dirtyCells.isEmpty {
            var cellBody: [String: Any] = [:]
            for serial in dirtyCells {
                let p = pos[serial] ?? .zero
                cellBody[serial] = ["x": Double(p.x), "y": Double(p.y), "z": z[serial] ?? 0, "rotation": rot[serial] ?? 0,
                                    "is_upside_down": ceiling[serial] ?? false, "height_layer": layer[serial] ?? 0]
            }
            body["cells"] = cellBody
        }
        if dirtyCircle { body["reference_circle"] = circleBody }
        if dirtyListener, let l = listener { body["listener_position"] = ["x_m": Double(l.x), "y_m": Double(l.y)] }
        guard !body.isEmpty else { return }
        dirtyCells = []; dirtyCircle = false; dirtyListener = false
        Task {
            guard let a = room.agent else { return }
            e.busy = true
            do {
                let r = try await a.post("/api/room/layout", body, timeout: 40)
                let n = (r["fanout"] as? [String: Any])?.count ?? 0
                e.note = n > 0 ? "Layout saved · \(n) speakers updated" : "Layout saved"
            } catch { e.error = error.localizedDescription }
            e.busy = false
            await e.load()
        }
    }
}
