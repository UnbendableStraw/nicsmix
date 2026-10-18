import SwiftUI
import Charts

/// The group stream: the network buffer every speaker in the room keeps for the primary's stream (Syng's SourceCast
/// `maxNetworkLatency_ms`), and a history of the moments that stream ran dry on each speaker (jackd's
/// "FIFO underrun" lines), over time and by minute past the hour — where an hourly culprit stands out.
struct StreamView: View {
    let room: Room
    @StateObject private var nb: Endpoint
    @StateObject private var day: Endpoint
    @StateObject private var week: Endpoint
    @State private var hours = 24
    @State private var ms: Double = 50
    @State private var touched = false
    @State private var confirm = false

    init(room: Room) {
        self.room = room
        _nb = StateObject(wrappedValue: Endpoint(room.agent, "/api/netbuffer", every: 0))
        _day = StateObject(wrappedValue: Endpoint(room.agent, "/api/underruns?hours=24", every: 60, timeout: 60))
        _week = StateObject(wrappedValue: Endpoint(room.agent, "/api/underruns?hours=168", every: 0, timeout: 90))
    }

    private var current: Int? { nb.data.int("ms") }
    private var und: Endpoint { hours == 24 ? day : week }

    var body: some View {
        SetupPage(title: "Group stream", e: nb) {
            Card("Network buffer") {
                Text(bufferSummary).font(.subheadline)
                HStack {
                    Slider(value: $ms, in: 20...500, step: 10, onEditingChanged: { _ in touched = true })
                    Text("\(Int(ms)) ms").font(.subheadline.monospacedDigit()).frame(width: 64, alignment: .trailing)
                }
                Button("Apply to the room") { confirm = true }
                    .buttonStyle(Capsule_(filled: true))
                    .disabled(nb.busy || (current != nil && Int(ms) == current && nb.data["mixed"] as? Bool != true))
                Text("How far ahead the primary streams to the other speakers (stock 50 ms). A Wi-Fi gap shorter than this is inaudible; a longer one is a dropout. More buffer means more delay before sound starts, which music doesn't mind. Applying restarts the audio engine on every speaker in the room — a few seconds of silence.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
            Card("Stream dropouts") {
                HStack {
                    Button("24 hours") { hours = 24 }.buttonStyle(Capsule_(filled: hours == 24))
                    Button("7 days") { hours = 168; if week.data.isEmpty { week.reload() } }.buttonStyle(Capsule_(filled: hours != 24))
                }
                if und.data.isEmpty {
                    HStack { ProgressView().tint(.white); Text("Reading every speaker's log…").font(.caption).foregroundStyle(Theme.muted) }
                } else {
                    timeline.frame(height: 170)
                    Text("By minute past the hour — an hourly culprit is one tall bar").font(.caption).foregroundStyle(Theme.muted).padding(.top, 6)
                    byMinute.frame(height: 110)
                    Text(legendLine).font(.caption).foregroundStyle(Theme.muted)
                }
                Text("Each mark is a moment the stream from the primary ran dry on that speaker; taller is longer. The primary is the sender and never logs these; a speaker alone in its room has no stream.")
                    .font(.caption).foregroundStyle(Theme.muted)
            }
        }
        .onAppear { nb.start(); day.start() }
        .onDisappear { nb.stop(); day.stop(); week.stop() }
        .onReceive(nb.$data) { d in if !touched, let v = d.int("ms") { ms = Double(v) } }
        .confirmationDialog("Set the network buffer to \(Int(ms)) ms on every speaker in this room?", isPresented: $confirm, titleVisibility: .visible) {
            Button("Apply — audio restarts briefly") {
                Task { if await nb.post("/api/netbuffer", ["ms": Int(ms)], timeout: 40) { nb.note = "Buffer set to \(Int(ms)) ms — audio restarting"; touched = false } }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private var bufferSummary: String {
        let cells = nb.data.list("cells").filter { $0["ms"] != nil }
        if nb.data["mixed"] as? Bool == true {
            return "Not the same everywhere: " + cells.map { "\(name($0)) \($0.int("ms") ?? 0) ms" }.joined(separator: " · ")
        }
        guard let v = current else { return nb.data.isEmpty ? "Reading…" : "Unknown" }
        return "\(v) ms on \(cells.count) speaker\(cells.count == 1 ? "" : "s")" + (cells.first?.flag("dualPath") == true ? " · two paths (Wi-Fi + SyFi)" : "")
    }

    // ---- the data ----
    struct Mark: Identifiable { let id = UUID(); let date: Date; let speaker: String; let weight: Double }
    struct MinuteMark: Identifiable { let id = UUID(); let minute: Int; let speaker: String; let count: Int }

    private func name(_ c: [String: Any]) -> String {
        if let n = c.str("name"), !n.isEmpty { return n }
        return "Cell " + String((c.str("serial") ?? "?").suffix(5))
    }
    private var cells: [[String: Any]] { und.data.list("cells").filter { $0["events"] != nil } }
    private var start: Date { Date().addingTimeInterval(-Double(hours) * 3600) }

    private var marks: [Mark] {
        let binS: Double = 3600                     // hourly bars: 24 for a day, 168 for a week
        var out: [Mark] = []
        for c in cells {
            var bins: [Double: Double] = [:]
            for e in c.list("events") {
                guard let t = e.num("t"), t >= start.timeIntervalSince1970 else { continue }
                let b = (t / binS).rounded(.down) * binS
                bins[b, default: 0] += 1 + log2(Double(max(1, e.int("burst") ?? 1)))
            }
            for (b, w) in bins { out.append(Mark(date: Date(timeIntervalSince1970: b), speaker: name(c), weight: w)) }
        }
        return out
    }
    private var minutes: [MinuteMark] {
        var out: [MinuteMark] = []
        let cal = Calendar.current
        for c in cells {
            var m = [Int: Int]()
            for e in c.list("events") {
                guard let t = e.num("t"), t >= start.timeIntervalSince1970 else { continue }
                m[cal.component(.minute, from: Date(timeIntervalSince1970: t)), default: 0] += 1
            }
            for (k, v) in m { out.append(MinuteMark(minute: k, speaker: name(c), count: v)) }
        }
        return out
    }
    private var legendLine: String {
        let total = minutes.reduce(0) { $0 + $1.count }
        var per = [Int: Int](); for m in minutes { per[m.minute, default: 0] += m.count }
        guard let top = per.max(by: { $0.value < $1.value }), top.value >= 3 else { return "\(total) dropouts" }
        return "\(total) dropouts · most at :\(String(format: "%02d", top.key)) past the hour (\(top.value))"
    }

    // ---- the charts ----
    private var timeline: some View {
        Chart(marks) { m in
            BarMark(x: .value("Time", m.date, unit: .hour), y: .value("Dropouts", m.weight))
                .foregroundStyle(by: .value("Speaker", m.speaker))
        }
        .chartXScale(domain: start...Date())
        .chartLegend(position: .bottom)
        .chartYAxis(.hidden)
    }
    private var byMinute: some View {
        Chart(minutes) { m in
            BarMark(x: .value("Minute", m.minute), y: .value("Count", m.count))
                .foregroundStyle(by: .value("Speaker", m.speaker))
        }
        .chartXScale(domain: 0...59)
        .chartXAxis { AxisMarks(values: [0, 15, 30, 45]) { v in AxisGridLine(); AxisValueLabel { Text(":\(String(format: "%02d", v.as(Int.self) ?? 0))") } } }
        .chartLegend(.hidden)
        .chartYAxis(.hidden)
    }
}
