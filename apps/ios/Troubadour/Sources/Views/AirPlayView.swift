import SwiftUI

/// AirPlay & Atmos: what the AirPlay source is really producing (Syng's own badge says "Stereo" even during 12-channel
/// Atmos); the height layer of each speaker (Syng's renderer lifts Atmos' height channels onto speakers marked high and
/// turns height processing on itself); fill surround, an experiment; and quick recovery (no 150 MB core dump after a crash).
struct AirPlayView: View {
    let room: Room
    @StateObject private var e: Endpoint
    @StateObject private var box = Box()
    final class Box: ObservableObject { @Published var fill = false; @Published var quick = false; @Published var layers: [String: Int] = [:] }

    init(room: Room) {
        self.room = room
        _e = StateObject(wrappedValue: Endpoint(room.agent, "/api/airplay", every: 5, timeout: 12))
    }

    private var cells: [[String: Any]] { e.data.list("cells") }
    private var playing: [[String: Any]] { cells.filter { $0.flag("active") } }

    var body: some View {
        SetupPage(title: "AirPlay & Atmos", e: e) {
            Card("Playing now") {
                if e.data.isEmpty {
                    HStack { ProgressView().tint(.white); Text("Asking the speakers…").font(.caption).foregroundStyle(Theme.muted) }
                } else if playing.isEmpty {
                    Text("Nothing is playing over AirPlay right now").font(.subheadline).foregroundStyle(Theme.muted)
                } else {
                    ForEach(playing.indices, id: \.self) { i in playingRow(playing[i]) }
                }
            }
            Card("Height") {
                Text(heightStatus).font(.subheadline)
                Text("Atmos' four overhead channels always play. With every speaker on the floor layer they are spread around the ring at ear height; mark the speakers that sit up high and Syng lifts the height channels onto them, turning height processing on by itself.")
                    .font(.caption).foregroundStyle(Theme.muted)
                ForEach(layerSerials, id: \.self) { serial in
                    HStack {
                        Text(speakerName(serial)).font(.subheadline)
                        Spacer()
                        Picker("", selection: layerBinding(serial)) {
                            Text("Floor / shelf").tag(0)
                            Text("Up high").tag(1)
                            Text("Ceiling").tag(2)
                        }
                        .pickerStyle(.menu).tint(.white)
                    }
                }
            }
            Card("Experiment") {
                Toggle(isOn: user(\.fill, "fill", "Fill surround on", "Fill surround off")) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Fill surround channels").font(.subheadline)
                        Text("Syng's enableBeyondStereoDuplication — nothing in Syng's firmware or apps sets it").font(.caption).foregroundStyle(Theme.muted)
                    }
                }
                .tint(Theme.live)
                Text("Applies to every speaker. Flip it while a surround or Atmos track plays and listen.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("If AirPlay crashes") {
                Toggle(isOn: user(\.quick, "quick_recover", "Quick recovery on", "Quick recovery off")) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Quick recovery").font(.subheadline)
                        Text(recoverLine).font(.caption).foregroundStyle(Theme.muted)
                    }
                }
                .tint(Theme.live)
                Text("Syng's AirPlay receiver can crash as a spatial stream starts; it then spends minutes writing a 150 MB crash file before it restarts. This skips the file.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            if !cells.isEmpty {
                Card("Speakers") {
                    ForEach(cells.indices, id: \.self) { i in
                        HStack {
                            Text(name(cells[i])).font(.subheadline)
                            Spacer()
                            Text(status(cells[i])).font(.caption).foregroundStyle(Theme.muted).multilineTextAlignment(.trailing)
                        }
                    }
                }
            }
        }
        .onReceive(e.$data) { d in
            guard !e.busy, let me = d.list("cells").first else { return }
            let s = me.obj("settings")
            var hl: [String: Int] = [:]
            for (k, v) in d.obj("height_layers") { hl[k] = (v as? Int) ?? (v as? Double).map { Int($0.rounded()) } ?? 0 }
            if box.layers != hl { box.layers = hl }
            if box.fill != s.flag("fill") { box.fill = s.flag("fill") }
            if box.quick != s.flag("quick_recover") { box.quick = s.flag("quick_recover") }
        }
    }

    private func playingRow(_ c: [String: Any]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(c.str("format") ?? "—").font(.title3.weight(.semibold))
                Text("\(c.int("channels") ?? 0) ch").font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
            }
            if let b = c.str("badge"), !b.isEmpty, b != c.str("format") {
                Text("Syng's badge says \u{201C}\(b)\u{201D}").font(.caption).foregroundStyle(Theme.muted)
            }
            if let l = c.str("layout") { Text(l).font(.system(.caption2, design: .monospaced)).foregroundStyle(Theme.muted) }
        }
    }
    private var recoverLine: String {
        var n = 0; var last: Double = 0
        for c in cells {
            let x = c.obj("quick_recover"); n += x.int("count") ?? 0
            let t = x.num("last") ?? 0; if t > last { last = t }
        }
        if n == 0 { return "Back in ~3 s instead of a 2-minute core dump" }
        var t = "Recovered \(n)×"
        if last > 0 { t += " · last " + Date(timeIntervalSince1970: last).formatted(date: .omitted, time: .shortened) }
        return t
    }
    private var layerSerials: [String] { box.layers.keys.sorted { speakerName($0) < speakerName($1) } }
    private var heightStatus: String {
        guard let hp = e.data["height_processing"] as? Bool else { return "Height processing: —" }
        return hp ? "Height processing on (set by Syng: a speaker is up high)" : "Height processing off (set by Syng: every speaker is on the floor layer)"
    }
    private func speakerName(_ serial: String) -> String {
        if let c = cells.first(where: { $0.str("serial") == serial }), let n = c.str("name"), !n.isEmpty { return n }
        return "Cell " + String(serial.suffix(5))
    }
    /// A speaker's height layer; changing it saves the room layout on the primary.
    private func layerBinding(_ serial: String) -> Binding<Int> {
        Binding(get: { box.layers[serial] ?? 0 }, set: { v in
            box.layers[serial] = v
            Task { if await e.post("/api/room/layout", ["cells": [serial: ["height_layer": v]]], timeout: 30) { e.note = "Height layer saved" } }
        })
    }
    /// A switch that posts only when the user flips it (a poll updating the state never echoes back).
    private func user(_ k: ReferenceWritableKeyPath<Box, Bool>, _ key: String, _ on: String, _ off: String) -> Binding<Bool> {
        Binding(get: { box[keyPath: k] }, set: { v in box[keyPath: k] = v; send([key: v], v ? on : off) })
    }
    private func send(_ body: [String: Any], _ note: String) {
        Task { if await e.post("/api/airplay", body, timeout: 40) { e.note = note + " — every speaker" } }
    }
    private func name(_ c: [String: Any]) -> String {
        if let n = c.str("name"), !n.isEmpty { return n }
        let serial = c.str("serial") ?? ""
        return "Cell " + String(serial.suffix(5)) + (c.str("role").map { " (\($0))" } ?? "")
    }
    private func status(_ c: [String: Any]) -> String {
        let q = c.obj("quick_recover").flag("on") ? "quick recovery on" : "quick recovery off"
        if c["height"] == nil || c["height"] is NSNull { return "AirPlay runs on the primary · " + q }
        return "height processing \(c.flag("height") ? "on" : "off") · fill \(c.flag("fill") ? "on" : "off") · " + q
    }
}

/// A small pill for a stream that is more than stereo ("ATMOS 7.1.4", "5.1"); nothing for stereo, mono or idle.
struct FormatBadge: View {
    let format: String?
    var body: some View {
        if let f = format, f.hasPrefix("Dolby Atmos") || f.hasPrefix("Surround") {
            Text(f.replacingOccurrences(of: "Dolby Atmos", with: "ATMOS").replacingOccurrences(of: "Surround ", with: ""))
                .font(.caption2.weight(.bold).monospacedDigit())
                .padding(.horizontal, 6).padding(.vertical, 2)
                .overlay(Capsule().stroke(Theme.muted.opacity(0.6), lineWidth: 1))
                .foregroundStyle(.white)
                .fixedSize()
        }
    }
}
