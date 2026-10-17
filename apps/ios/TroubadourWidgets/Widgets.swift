import WidgetKit
import SwiftUI
import AppIntents

// Two home-screen widgets for the room the app last opened: the sound field (where the puck / the spatial dots
// sit, with buttons to centre it, switch classic/spatial and widen or narrow it) and the LEDs (current mode,
// buttons for each reactive mode and off). Small sizes are a picture that opens the app; medium adds the buttons.

@main
struct TroubadourWidgets: WidgetBundle {
    var body: some Widget { FieldWidget(); LEDWidget() }
}

// MARK: - timeline (shared by both widgets)

struct Entry: TimelineEntry { let date: Date; let snap: RoomSnapshot }

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> Entry { Entry(date: .now, snap: Self.sample) }
    func getSnapshot(in context: Context, completion: @escaping (Entry) -> Void) {
        if context.isPreview { completion(Entry(date: .now, snap: Self.sample)); return }
        Task { completion(Entry(date: .now, snap: await RoomSnapshot.fetch())) }
    }
    func getTimeline(in context: Context, completion: @escaping (Timeline<Entry>) -> Void) {
        Task {
            let e = Entry(date: .now, snap: await RoomSnapshot.fetch())
            completion(Timeline(entries: [e], policy: .after(.now.addingTimeInterval(e.snap.reachable ? 15 * 60 : 5 * 60))))
        }
    }
    static var sample: RoomSnapshot {
        var s = RoomSnapshot(); s.room = "Ball Room"; s.reachable = true; s.ledMode = "flow"; s.playing = "Something good"
        s.field.ux = 0.15; s.field.uy = 0.2
        s.speakers = (0..<6).map { i in let a = Double(i) / 6 * 2 * .pi + .pi / 2; return (x: cos(a), y: sin(a), primary: i == 0) }
        return s
    }
}

// MARK: - intents behind the buttons

struct FieldAction: AppIntent {
    static var title: LocalizedStringResource = "Sound field"
    @Parameter(title: "Action") var action: String
    init() {}
    init(_ a: String) { action = a }
    func perform() async throws -> some IntentResult {
        guard let ip = Shared.agentIP else { Shared.lastAction = "no room — open one in the app"; return .result() }
        let a = Agent(ip: ip)
        do {
            let st = try await a.get("/api/state", timeout: 4)
            let f = FieldState(((st["room"] as? [String: Any])?["field"] as? [String: Any]) ?? [:])
            let step = 0.2
            var body: [String: Any] = [:]
            switch action {
            case "center": body = ["ux": 0, "uy": 0]
            case "classic": body = ["spatial": false]
            case "spatial": body = ["spatial": true]
            case "narrower": body = ["width": max(20, f.width - 15)]
            case "wider": body = ["width": min(180, f.width + 15)]
            case "up": body = ["uy": min(1, f.uy + step)]
            case "down": body = ["uy": max(-1, f.uy - step)]
            case "left": body = ["ux": max(-1, f.ux - step)]
            case "right": body = ["ux": min(1, f.ux + step)]
            default: return .result()
            }
            try await a.post("/api/soundfield", body, timeout: 6)
            Shared.note(action, nil)
        } catch { Shared.note(action, error) }
        return .result()
    }
}

struct LEDAction: AppIntent {
    static var title: LocalizedStringResource = "LED mode"
    @Parameter(title: "Mode") var mode: String
    init() {}
    init(_ m: String) { mode = m }
    func perform() async throws -> some IntentResult {
        guard let ip = Shared.agentIP else { Shared.lastAction = "no room — open one in the app"; return .result() }
        do { try await Agent(ip: ip).post("/api/led/reactive", ["mode": mode, "scope": "group"], timeout: 20); Shared.note("LED \(mode)", nil) }
        catch { Shared.note("LED \(mode)", error) }
        return .result()
    }
}

// MARK: - sound field

struct FieldWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "space.troubadour.field", provider: Provider()) { e in FieldView(snap: e.snap) }
            .configurationDisplayName("Sound Field")
            .description("Where the sound sits in the room you last opened. Centre, classic / spatial, width, nudge.")
            .supportedFamilies([.systemSmall, .systemMedium])
            .contentMarginsDisabled()
    }
}

struct FieldView: View {
    let snap: RoomSnapshot
    @Environment(\.widgetFamily) var family
    @Environment(\.widgetRenderingMode) var mode
    var body: some View {
        let mono = mode == .accented
        HStack(spacing: 10) {
            ZStack {
                FieldPicture(snap: snap, mono: mono)
                if !snap.reachable { Text("not reachable").font(.caption2).foregroundStyle(Theme.muted).padding(4).background(.black.opacity(0.6), in: Capsule()) }
            }
            .aspectRatio(1, contentMode: .fit)
            if family == .systemMedium {
                VStack(alignment: .leading, spacing: 6) {
                    Text(snap.room).font(.footnote.weight(.semibold)).lineLimit(1)
                    Text(snap.field.spatial ? "Spatial · \(Int(snap.field.width))°" : "Classic").font(.caption2).foregroundStyle(Theme.muted)
                    if let l = Shared.lastAction { Text(l).font(.system(size: 9)).foregroundStyle(Theme.muted).lineLimit(1) }
                    Spacer(minLength: 0)
                    HStack(spacing: 6) {
                        Pill("Centre", intent: FieldAction("center"))
                        Pill(snap.field.spatial ? "Classic" : "Spatial", intent: FieldAction(snap.field.spatial ? "classic" : "spatial"))
                    }
                    HStack(spacing: 6) {
                        Pill("−", intent: FieldAction("narrower"), filled: false)
                        Pill("+", intent: FieldAction("wider"), filled: false)
                        Pill("↑", intent: FieldAction("up"), filled: false)
                        Pill("↓", intent: FieldAction("down"), filled: false)
                    }
                    HStack(spacing: 6) {
                        Pill("←", intent: FieldAction("left"), filled: false)
                        Pill("→", intent: FieldAction("right"), filled: false)
                        Spacer()
                    }
                }
            }
        }
        .padding(12)
        .containerBackground(Theme.bg, for: .widget)
        .widgetURL(URL(string: "troubadour://room"))
    }
}

/// The room from above: reference circle, speakers as pucks, the listener, and the puck (classic) or the two
/// spatial dots at rot ± width on the field radius.
struct FieldPicture: View {
    let snap: RoomSnapshot
    var mono = false
    var body: some View {
        Canvas { ctx, size in FieldPainter(snap: snap, size: size, mono: mono).draw(&ctx) }
    }
}

/// Kept out of the view body in small typed steps — one big Canvas closure makes the type-checker give up.
struct FieldPainter {
    let snap: RoomSnapshot
    let size: CGSize
    var mono = false          // tinted / clear home screen: every fill becomes a white blob, so draw outlines
    private var R: CGFloat { min(size.width, size.height) / 2 * 0.86 }
    private var c: CGPoint { CGPoint(x: size.width / 2, y: size.height / 2) }
    private func P(_ x: Double, _ y: Double) -> CGPoint { CGPoint(x: c.x + CGFloat(x) * R, y: c.y - CGFloat(y) * R) }
    private func circle(_ p: CGPoint, _ r: CGFloat) -> Path { Path(ellipseIn: CGRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r)) }
    private func radial(_ a: Color, _ b: Color, _ p: CGPoint, _ r: CGFloat) -> GraphicsContext.Shading {
        .radialGradient(Gradient(colors: [a, b]), center: p, startRadius: 0, endRadius: r)
    }

    func draw(_ ctx: inout GraphicsContext) {
        if mono { drawMono(&ctx); return }
        drawRoom(&ctx); drawSound(&ctx); drawListener(&ctx)
        for s in snap.speakers { drawSpeaker(&ctx, s) }
    }
    private func drawMono(_ ctx: inout GraphicsContext) {
        ctx.stroke(circle(c, R), with: .color(.white.opacity(0.6)), lineWidth: 1)
        let f = snap.field
        if f.spatial {
            let rr: Double = max(0.15, f.radius == 0 ? 0.7 : f.radius)
            for sgn in [-1.0, 1.0] {
                let a: Double = (f.rot + sgn * f.width) * Double.pi / 180
                ctx.fill(circle(P(cos(a) * rr, sin(a) * rr), R * 0.08), with: .color(.white))
            }
        } else {
            let p = P(f.ux, f.uy)
            ctx.stroke(circle(p, R * 0.22), with: .color(.white.opacity(0.7)), lineWidth: 1)
            ctx.fill(circle(p, R * 0.07), with: .color(.white))
        }
        ctx.stroke(circle(P(snap.listener.x, snap.listener.y), 3), with: .color(.white), lineWidth: 1)
        for s in snap.speakers {
            let p = P(s.x, s.y)
            let pr: CGFloat = R * 0.1
            ctx.stroke(circle(p, pr), with: .color(.white), lineWidth: s.primary ? 2 : 1)
            for i in 0..<3 {
                let a: Double = Double(i) * 2 * Double.pi / 3 - Double.pi / 2
                let d = CGPoint(x: p.x + CGFloat(cos(a)) * pr * 0.45, y: p.y + CGFloat(sin(a)) * pr * 0.45)
                ctx.fill(circle(d, pr * 0.12), with: .color(.white))
            }
        }
    }
    private func drawRoom(_ ctx: inout GraphicsContext) {
        ctx.fill(circle(c, R), with: radial(Color(white: 0.16), Color(white: 0.06), c, R))
        ctx.stroke(circle(c, R), with: .color(Color(white: 0.3)), lineWidth: 1)
    }
    private func drawSound(_ ctx: inout GraphicsContext) {
        let f = snap.field
        if f.spatial {
            let rr: Double = max(0.15, f.radius == 0 ? 0.7 : f.radius)
            for sgn in [-1.0, 1.0] {
                let a: Double = (f.rot + sgn * f.width) * Double.pi / 180
                let p = P(cos(a) * rr, sin(a) * rr)
                let blue = Color(red: 0.35, green: 0.6, blue: 1).opacity(0)
                ctx.fill(circle(p, R * 0.14), with: radial(.white, blue, p, R * 0.14))
            }
        } else {
            let p = P(f.ux, f.uy)
            let inner = Color(red: 0.55, green: 0.75, blue: 1).opacity(0.9)
            let outer = Color(red: 0.2, green: 0.4, blue: 0.9).opacity(0)
            ctx.fill(circle(p, R * 0.3), with: radial(inner, outer, p, R * 0.3))
            ctx.fill(circle(p, R * 0.07), with: .color(.white))
        }
    }
    private func drawListener(_ ctx: inout GraphicsContext) {
        let l = P(snap.listener.x, snap.listener.y)
        ctx.stroke(circle(l, 3), with: .color(Color(white: 0.7)), lineWidth: 1)
    }
    private func drawSpeaker(_ ctx: inout GraphicsContext, _ s: (x: Double, y: Double, primary: Bool)) {
        let p = P(s.x, s.y)
        let pr: CGFloat = R * 0.1
        let hl = CGPoint(x: p.x - pr * 0.2, y: p.y - pr * 0.3)
        ctx.fill(circle(p, pr), with: radial(Color(white: 0.28), Color(white: 0.04), hl, pr * 1.2))
        if s.primary { ctx.stroke(circle(p, pr + 1.5), with: .color(.white), lineWidth: 1) }
        for i in 0..<3 {
            let a: Double = Double(i) * 2 * Double.pi / 3 - Double.pi / 2
            let d = CGPoint(x: p.x + CGFloat(cos(a)) * pr * 0.45, y: p.y + CGFloat(sin(a)) * pr * 0.45)
            ctx.fill(circle(d, pr * 0.11), with: .color(Theme.amber))
        }
    }
}

// MARK: - LEDs

struct LEDWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "space.troubadour.led", provider: Provider()) { e in LEDView(snap: e.snap) }
            .configurationDisplayName("LEDs")
            .description("The light mode of the room you last opened; tap a mode to switch the whole room.")
            .supportedFamilies([.systemSmall, .systemMedium])
            .contentMarginsDisabled()
    }
}

struct LEDView: View {
    let snap: RoomSnapshot
    @Environment(\.widgetFamily) var family
    @Environment(\.widgetRenderingMode) var mode
    static let names: [String: String] = ["pulse": "Pulse", "party": "Party", "flow": "Flow", "groove": "Groove", "spectrum": "Spectrum", "vu": "VU", "beat": "Beat"]
    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                ZStack {
                    Circle().fill(RadialGradient(colors: [Color(white: 0.25), Color(white: 0.04)], center: .init(x: 0.42, y: 0.36), startRadius: 0, endRadius: 30))
                    Circle().stroke(snap.ledMode == nil ? Color(white: 0.3) : Theme.amber, lineWidth: 2).blur(radius: snap.ledMode == nil ? 0 : 1)
                    Circle().stroke(snap.ledMode == nil ? Color(white: 0.3) : Theme.amber.opacity(0.9), lineWidth: 1)
                }.frame(width: 46, height: 46)
                Spacer(minLength: 0)
                Text(snap.room).font(.footnote.weight(.semibold)).lineLimit(1)
                Text(snap.ledMode.map { Self.names[$0] ?? $0 } ?? "Lights off").font(.caption).foregroundStyle(snap.ledMode == nil ? Theme.muted : Theme.amber)
                if !snap.reachable { Text("not reachable").font(.caption2).foregroundStyle(Theme.muted) }
                if let l = Shared.lastAction, l.hasPrefix("LED") || l.hasPrefix("no room") { Text(l).font(.system(size: 9)).foregroundStyle(Theme.muted).lineLimit(1) }
            }
            if family == .systemMedium {
                let modes = Array(snap.ledModes.prefix(7)) + ["off"]
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 4), spacing: 6) {
                    ForEach(modes, id: \.self) { m in
                        Pill(m == "off" ? "Off" : (Self.names[m] ?? m), intent: LEDAction(m), filled: (snap.ledMode ?? "off") == m)
                    }
                }
            } else {
                Spacer()
            }
        }
        .padding(12)
        .containerBackground(Theme.bg, for: .widget)
        .widgetURL(URL(string: "troubadour://room"))
    }
}

// MARK: - the app's capsule button, as an intent button

struct Pill<I: AppIntent>: View {
    let text: String; let intent: I; var filled = true
    @Environment(\.widgetRenderingMode) var mode
    init(_ text: String, intent: I, filled: Bool = true) { self.text = text; self.intent = intent; self.filled = filled }
    var body: some View {
        let mono = mode == .accented
        Button(intent: intent) {
            Text(text).font(.caption2.weight(.semibold)).lineLimit(1).minimumScaleFactor(0.7)
                .frame(maxWidth: .infinity).padding(.vertical, 6)
                .background(pillBackground(mono))
                .foregroundStyle(mono ? Color.white : (filled ? Color.black : Color.white))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
    @ViewBuilder private func pillBackground(_ mono: Bool) -> some View {
        if mono { Capsule().stroke(Color.white, lineWidth: filled ? 2 : 1).background(Color.white.opacity(filled ? 0.25 : 0.08), in: Capsule()) }
        else { Capsule().fill(filled ? Color.white : Theme.surface2) }
    }
}
