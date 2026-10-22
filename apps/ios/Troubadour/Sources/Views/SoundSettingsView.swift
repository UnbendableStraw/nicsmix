import SwiftUI

// MARK: - tone, volume limit, the control ring

/// The things that shape how the room sounds and how the speakers behave under your hand, rather
/// than what is playing: tone (`/api/tone`), the volume ceiling (`/api/settings`) and the control
/// ring on top of the cell (`/api/ring`).
struct SoundSettingsView: View {
    let room: Room
    @StateObject private var e: Endpoint          // /api/state — carries room.tone
    @StateObject private var settings: Endpoint   // /api/settings — volume limit, and ring
    @State private var bass: Double = 0
    @State private var tilt: Double = 0
    @State private var limit: Double = 0
    @State private var steps: Double = 100
    @State private var clockwiseUp = true
    @State private var uiSound: Double = 25
    @State private var loaded = false

    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/state", every: 8))
        _settings = StateObject(wrappedValue: Endpoint(room.agent, "/api/settings", every: 0))
    }

    var body: some View {
        SetupPage(title: "Sound & controls", e: e) {
            Card("Tone") {
                ToneCurve(bass: bass, tilt: tilt).frame(height: 66)
                sliderRow("Lows", $bass) { send() }
                sliderRow("Highs", $tilt) { send() }
                HStack {
                    Button("Flat") { bass = 0; tilt = 0; send() }.buttonStyle(Chip())
                    Spacer()
                    Text(String(format: "%+.0f / %+.0f", bass * 100, tilt * 100))
                        .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                }
                Text("Applied to every source on the room, the same control the app's Tone sliders drive.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Volume ceiling") {
                HStack {
                    Slider(value: $limit, in: 0...100, step: 5, onEditingChanged: { if !$0 { sendLimit() } }).tint(.white)
                    Text(limit <= 0 ? "off" : "\(Int(limit))%").font(.caption.monospacedDigit()).frame(width: 40, alignment: .trailing)
                }
                Text("The agent refuses anything above this, wherever the request comes from — the app, the ring, or AirPlay.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Control ring") {
                HStack {
                    Text("Steps per turn").font(.subheadline).foregroundStyle(Theme.muted)
                    Spacer()
                    Text("\(Int(steps))").font(.caption.monospacedDigit())
                }
                Slider(value: $steps, in: 10...400, step: 10, onEditingChanged: { if !$0 { sendRing() } }).tint(.white)
                Toggle("Clockwise turns it up", isOn: $clockwiseUp)
                    .font(.subheadline).tint(.white)
                    .onChange(of: clockwiseUp) { _ in sendRing() }
                HStack {
                    Text("Touch sounds").font(.subheadline).foregroundStyle(Theme.muted)
                    Slider(value: $uiSound, in: 0...100, step: 5, onEditingChanged: { if !$0 { sendRing() } }).tint(.white)
                    Text("\(Int(uiSound))%").font(.caption.monospacedDigit()).frame(width: 40, alignment: .trailing)
                }
                Text("Fewer steps means a bigger jump per click. These are celldirector's own options, applied to this speaker.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .onAppear { settings.start() }
        .onReceive(e.$data.dropFirst()) { d in
            let tone = d.obj("room").obj("tone")
            guard !loaded else { return }
            bass = tone.num("bass") ?? 0
            tilt = tone.num("tilt") ?? 0
            loaded = true
        }
        .onReceive(settings.$data.dropFirst()) { d in
            limit = Double(d.int("volume_limit") ?? 0)
            let ring = d.obj("ring")
            steps = Double(ring.int("steps") ?? 100)
            clockwiseUp = (ring["clockwise_up"] as? Bool) ?? true
            uiSound = Double(ring.int("ui_sound_pct") ?? 25)
        }
    }

    private func sliderRow(_ label: String, _ value: Binding<Double>, _ done: @escaping () -> Void) -> some View {
        HStack {
            Text(label).font(.subheadline).foregroundStyle(Theme.muted).frame(width: 52, alignment: .leading)
            Slider(value: value, in: -1...1, step: 0.02, onEditingChanged: { if !$0 { done() } }).tint(.white)
        }
    }

    private func send() { e.fire("/api/tone", ["bass": bass, "tilt": tilt]) }
    private func sendLimit() { settings.fire("/api/settings", ["volume_limit": Int(limit)], note: limit <= 0 ? "Ceiling off" : "Ceiling \(Int(limit))%") }
    private func sendRing() {
        settings.fire("/api/ring", ["steps": Int(steps), "clockwise_up": clockwiseUp, "ui_sound_pct": Int(uiSound)], note: "Ring updated")
    }
}

/// The shape the two sliders make (the web page's `#tonecurve`): a low shelf and a high tilt around a flat middle.
struct ToneCurve: View {
    let bass: Double, tilt: Double
    private func smooth(_ a: Double, _ b: Double, _ x: Double) -> Double {
        let t = min(max((x - a) / (b - a), 0), 1); return t * t * (3 - 2 * t)
    }
    var body: some View {
        Canvas { ctx, size in
            let mid = size.height / 2
            var base = Path(); base.move(to: CGPoint(x: 0, y: mid)); base.addLine(to: CGPoint(x: size.width, y: mid))
            ctx.stroke(base, with: .color(Theme.surface2), lineWidth: 1)
            var p = Path()
            for i in 0...60 {
                let x = Double(i) / 60
                let g = bass * (1 - smooth(0.12, 0.5, x)) + tilt * smooth(0.5, 0.9, x)
                let pt = CGPoint(x: x * size.width, y: mid - g * 24)
                if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
            }
            ctx.stroke(p, with: .color(.white), style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
            ctx.draw(Text("LOWS").font(.system(size: 9)).foregroundColor(Theme.muted), at: CGPoint(x: 16, y: size.height - 8))
            ctx.draw(Text("HIGHS").font(.system(size: 9)).foregroundColor(Theme.muted), at: CGPoint(x: size.width - 18, y: size.height - 8))
        }
    }
}

// MARK: - scenes

/// Scenes are the room's settings under a name — sources, volume, sound mode, LEDs, the field.
/// Save one, apply it, or have the primary apply it at a time of day.
struct ScenesView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @State private var saving = false
    @State private var newName = ""
    @State private var scheduling: String?
    @State private var at = Date()
    @State private var days: Set<Int> = []
    private let dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    init(room: Room) { self.room = room; _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/scenes", every: 10)) }

    private struct SceneItem: Identifiable { let id: String; let body: [String: Any] }
    private var scenes: [SceneItem] {
        let all = e.data.obj("scenes")
        return all.keys.sorted().map { SceneItem(id: $0, body: all.obj($0)) }
    }

    var body: some View {
        SetupPage(title: "Scenes", e: e) {
            Card("Saved") {
                if scenes.isEmpty {
                    Text("No scenes yet. Set the room up the way you like it, then save it below.")
                        .font(.caption).foregroundStyle(Theme.muted)
                }
                ForEach(scenes) { s in
                    sceneRow(s.id, s.body)
                }
            }
            Card("Save the room as it is") {
                Button("Save a scene…") { newName = ""; saving = true }.buttonStyle(Capsule_(filled: true))
                Text("Takes what is playing, the volume, sound mode, the LEDs and the sound field as they are now.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            if let tz = e.data.str("tz_now") {
                Text("The speakers think it is \(tz).").font(.caption2).foregroundStyle(Theme.muted)
            }
        }
        .onAppear { sendTimeZone() }
        .alert("Name this scene", isPresented: $saving) {
            TextField("Evening", text: $newName)
            Button("Save") { if !newName.isEmpty { e.fire("/api/scenes/save", ["name": newName], note: "Saved \(newName)") } }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: Binding(get: { scheduling.map { Named(id: $0) } }, set: { scheduling = $0?.id })) { item in
            scheduleSheet(item.id)
        }
    }

    private struct Named: Identifiable { let id: String }

    private func sceneRow(_ name: String, _ body: [String: Any]) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(name).font(.subheadline.weight(.semibold))
                Text(scheduleLabel(body) + (e.data.str("last") == name ? " · last applied" : "")).font(.caption).foregroundStyle(Theme.muted)
                let d = describe(body)
                if !d.isEmpty { Text(d).font(.caption2).foregroundStyle(Theme.muted).lineLimit(1) }
            }
            Spacer()
            Button("Apply") { e.fire("/api/scenes/apply", ["name": name], note: "Applied \(name)") }
                .buttonStyle(Chip(filled: e.data.str("last") == name))
            Menu {
                Button("Schedule…") { load(body); scheduling = name }
                Button("Overwrite with the room as it is now") { e.fire("/api/scenes/save", ["name": name], note: "Updated \(name)") }
                Button("Delete", role: .destructive) { e.fire("/api/scenes/delete", ["name": name], note: "Deleted \(name)") }
            } label: { Image(systemName: "ellipsis.circle").foregroundStyle(Theme.muted) }
        }
        .padding(.vertical, 3)
    }

    /// What the scene holds, the way the web page lists it: vol 40 · tone 10/-5 · spatial · LED pulse · limit 70%.
    private func describe(_ sc: [String: Any]) -> String {
        var parts: [String] = []
        if let v = sc.num("volume") { parts.append("vol \(Int((v * 100).rounded()))") }
        if let t = sc["tone"] as? [String: Any] { parts.append("tone \(Int(((t.num("bass") ?? 0) * 100).rounded()))/\(Int(((t.num("tilt") ?? 0) * 100).rounded()))") }
        if let f = sc["field"] as? [String: Any] { parts.append(f.flag("spatial") ? "spatial" : "classic") }
        if let l = sc["led"] as? [String: Any], let m = l.str("mode") { parts.append("LED \(m)") }
        if let lim = sc.int("volume_limit"), lim > 0 { parts.append("limit \(lim)%") }
        return parts.joined(separator: " · ")
    }

    private func scheduleLabel(_ body: [String: Any]) -> String {
        guard let at = body.str("at") else { return "not scheduled" }
        let d = (body["days"] as? [Int]) ?? []
        if d.isEmpty || d.count == 7 { return "every day at \(at)" }
        return d.sorted().map { dayNames[$0 % 7] }.joined(separator: " ") + " at \(at)"
    }

    private func load(_ body: [String: Any]) {
        days = Set((body["days"] as? [Int]) ?? [])
        var c = DateComponents()
        let parts = (body.str("at") ?? "20:00").split(separator: ":")
        c.hour = Int(parts.first ?? "20") ?? 20
        c.minute = parts.count > 1 ? (Int(parts[1]) ?? 0) : 0
        at = Calendar.current.date(from: c) ?? Date()
    }

    private func scheduleSheet(_ name: String) -> some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    Card(name) {
                        DatePicker("Time", selection: $at, displayedComponents: .hourAndMinute)
                            .datePickerStyle(.wheel).labelsHidden().environment(\.colorScheme, .dark)
                            .frame(maxWidth: .infinity)
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 64), spacing: 6)], spacing: 6) {
                            ForEach(0..<7, id: \.self) { i in
                                Button(dayNames[i]) {
                                    if days.contains(i) { days.remove(i) } else { days.insert(i) }
                                }
                                .buttonStyle(Chip(filled: days.contains(i)))
                            }
                        }
                        Text(days.isEmpty ? "Every day." : "Only on the days you picked.")
                            .font(.caption).foregroundStyle(Theme.muted)
                    }
                    Card(nil) {
                        Button("Save the schedule") { saveSchedule(name) }.buttonStyle(Capsule_(filled: true))
                        Button("Clear it") {
                            e.fire("/api/scenes/schedule", ["name": name], note: "Schedule cleared")
                            scheduling = nil
                        }.buttonStyle(Capsule_())
                    }
                }
                .padding(16)
            }
            .background(Theme.bg.ignoresSafeArea())
            .navigationTitle("Schedule")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .navigationBarTrailing) { Button("Done") { scheduling = nil } } }
        }
        .presentationDetents([.large])
    }

    private func saveSchedule(_ name: String) {
        let c = Calendar.current.dateComponents([.hour, .minute], from: at)
        let hhmm = String(format: "%02d:%02d", c.hour ?? 20, c.minute ?? 0)
        var body: [String: Any] = ["name": name, "at": hhmm]
        if !days.isEmpty { body["days"] = days.sorted() }
        e.fire("/api/scenes/schedule", body, note: "\(name) at \(hhmm)")
        scheduling = nil
    }

    /// The cells run on UTC; they need this phone's offset to fire a schedule at the right local time.
    private func sendTimeZone() {
        let offset = -TimeZone.current.secondsFromGMT() / 60
        Task { try? await room.agent?.post("/api/scenes/tz", ["offset": offset], timeout: 6) }
    }
}
