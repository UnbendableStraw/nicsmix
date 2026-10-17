import Foundation
import SwiftUI
import simd

/// Everything the sound field knows, on the CPU: layout, the springs the app uses, the live audio
/// feed, the flurry/pulse animation state, and the two endpoints it drives (/api/soundfield,
/// /api/volume). The renderer reads this straight out every frame — no SwiftUI redraws involved.
/// Everything here runs on the main thread (gestures, and the Metal view's own draw callback).
final class FieldModel: ObservableObject {

    // MARK: config (the web field's cfg, trimmed to what the native renderer uses)
    struct Cfg {
        var particles = 24_000          // the GPU does not care; the web had to stop at 12k on a phone
        var sceneScale: Double = 0.62   // the soft background renders at this fraction of the drawable
        var reactiveFrac: Double = 0.5
        var simHz: Double = 30          // the sim keeps the app's step rate; frames interpolate
        var weightSpatial: Double = 0.88
        var weightOmni: Double = 2.0
        var handleRadius: Double = 0.60
        var defaultHalfDeg: Double = 95
        var minHalfDeg: Double = 12, maxHalfDeg: Double = 178
        var snapToCell = true, snapFrac: Double = 0.13
        var liftFrac: Double = 0.26, collapseFrac: Double = 0.2
        var pulseScale: Double = 2.0, pulseGain: Double = 0.35, pulseEmitter: Double = 0.06
        var pulseJitter: Double = 0.25
        var puffGain: Double = 0.005, puffBeat: Double = 0.005
        var puffFloat: Double = 0.9, puffVmax: Double = 0.008, puffReach: Double = 0.2
        var glow: Double = 1.0, grain: Double = 1.0, flurry: Double = 1.0, halo: Double = 1.0
        var pointScale: Double = 1.0, pointGain: Double = 1.0
        var radiusUnits: Double = 0.7
        var showVolume = true, showRings = true
    }
    var cfg = Cfg()

    // MARK: layout, in points; the view sets this on every layout pass
    var size = CGSize(width: 360, height: 338)
    var centre = CGPoint(x: 180, y: 155)
    var roomR: Double = 138
    var contentsScale: Double = 3

    func layout(_ s: CGSize) {
        guard s.width > 1, s.height > 1 else { return }
        let box = min(s.width, s.height / 0.94)
        let R = Double(box) * 0.385
        let newC = CGPoint(x: s.width / 2, y: (s.height - box * 0.94) / 2 + CGFloat(0.045 * Double(box) + R))
        if roomR > 0, abs(R - roomR) > 0.5 || abs(newC.x - centre.x) > 0.5 || abs(newC.y - centre.y) > 0.5 {
            // keep the puck where it was, in room units, across a re-layout
            let u = units(of: puckTarget)
            size = s; centre = newC; roomR = R
            puckTarget = point(ofUnits: u); puck = puckTarget; puckVel = .zero
        } else {
            size = s; centre = newC; roomR = R
        }
        if !placed { puck = centre; puckTarget = centre; placed = true }
    }
    private var placed = false

    // MARK: field state (the app's own)
    var puck = CGPoint.zero, puckTarget = CGPoint.zero, puckVel = CGPoint.zero
    var puckScale: Double = 1, puckScaleVel: Double = 0
    var spatial = false
    var dir: Double = 0, dirTarget: Double = 0, dirVel: Double = 0
    var half: Double = 0, halfTarget: Double = 0, halfVel: Double = 0
    var weight: Double = 2.0, weightVel: Double = 0
    var volume: Double = 0.25, volTarget: Double = 0.25
    var snapped: String?
    var time: Double = 0

    // MARK: audio
    var energy: Double = 0.25, bass: Double = 0, beat: Double = 0
    var spec = [Double](repeating: 0, count: 24)
    var rawSpec = [Double](repeating: 0, count: 24)
    var emaSpec = [Double](repeating: 0, count: 24)
    var flux = [Double](repeating: 0, count: 24)
    var seq = -1
    var feedFPS: Double = 30
    var lastRow = Date.distantPast
    @Published var live = false
    @Published var source: String = "airplay"

    // MARK: what the controls below the field show (published only when the value really changes,
    // so SwiftUI is not asked to redraw at the frame rate)
    @Published var volPct = 25
    @Published var widthDeg = 95
    @Published var aimDeg = 0
    @Published var spatialOn = false
    @Published var sourceTitle = "Sound field"

    // MARK: the pulse texture (64 angle columns x 128 time rows, written one row per feed packet)
    static let pulseW = 64, pulseRows = 128
    var pulseHead = 0
    private var pulseQueue: [[UInt8]] = []

    // MARK: flurry blobs + pulse field, as the web animates them
    struct Blob { var x = 0.0, y = 0.0, r = 0.4, a = 0.0, vx = 0.0, vy = 0.0, life = 0.0, ph = 0.0 }
    var blobs: [Blob] = []
    struct Phase { var ang = 0.0, w = 0.0, p1 = 0.0, p2 = 0.0, w1 = 0.0, w2 = 0.0, off = 0.0 }
    var phases: [Phase] = []
    var bands = [Double](repeating: 0, count: 6)
    var pulseE = [SIMD4<Float>](repeating: .zero, count: 6)
    var pulseP = [SIMD4<Float>](repeating: .zero, count: 6)

    // MARK: speakers
    struct Ring: Identifiable { var pos: CGPoint; var active: Bool; var serial: String; var id: String { serial } }
    var rings: [Ring] = []
    var listener: CGPoint?
    private var reference = (x: 0.0, y: 0.0, r: 1.15, rot: 0.0)
    private var cellsM: [(x: Double, y: Double, active: Bool, serial: String)] = []

    // MARK: palette
    var palette = FieldPalette.airplay
    private var paletteTarget = FieldPalette.airplay

    // MARK: input
    enum Grab: Equatable { case puckOrb, lift, notch, dot(Int), volume }
    var grab: Grab?
    var liftDot: CGPoint?
    private var grabOffset = CGSize.zero

    let agent: Agent?
    @Published var error: String?
    private var touched = Date.distantPast
    private var lastPost = Date.distantPast
    private var postPending = false
    private var vizTask: Task<Void, Never>?
    private var stateTask: Task<Void, Never>?

    init(agent: Agent?) {
        self.agent = agent
        var rng = SystemRandomNumberGenerator()
        for _ in 0..<24 {
            var b = Blob()
            b.x = .random(in: -0.4...0.4, using: &rng); b.y = .random(in: -0.4...0.4, using: &rng)
            b.r = .random(in: 0.36...0.45, using: &rng); b.life = .random(in: 0...10, using: &rng)
            b.ph = .random(in: 0...(2 * .pi), using: &rng)
            blobs.append(b)
        }
        for k in 0..<6 {
            let s: Double = k % 2 == 0 ? -1 : 1
            phases.append(Phase(ang: .random(in: 0...(2 * .pi), using: &rng),
                                w: .random(in: 0.25...0.6, using: &rng) * s,
                                p1: .random(in: 0...(2 * .pi), using: &rng),
                                p2: .random(in: 0...(2 * .pi), using: &rng),
                                w1: .random(in: 0.4...0.9, using: &rng) * -s,
                                w2: .random(in: 0.3...0.7, using: &rng),
                                off: .random(in: 0.10...0.22, using: &rng)))
        }
    }

    // MARK: - geometry helpers (the web's, one for one)

    var fieldR: Double {
        let d = hypot(Double(puck.x - centre.x), Double(puck.y - centre.y))
        return max(roomR * 0.2, roomR - d)
    }
    var puckR: Double { roomR * 0.165 * puckScale }
    var handleR: Double { roomR * 0.052 }
    var notchSize: (Double, Double) { (roomR * 0.042, roomR * 0.115) }
    func dirVec(_ a: Double) -> CGPoint { CGPoint(x: sin(a), y: -cos(a)) }
    func handlePos(_ angle: Double) -> CGPoint {
        let v = dirVec(angle)
        let r = CGFloat(cfg.handleRadius * fieldR)
        return CGPoint(x: puck.x + v.x * r, y: puck.y + v.y * r)
    }
    var liftDotPos: CGPoint { CGPoint(x: puck.x, y: puck.y - CGFloat(puckR + handleR + roomR * 0.015)) }
    func angleOf(_ p: CGPoint) -> Double { atan2(Double(p.x - puck.x), -Double(p.y - puck.y)) }
    func units(of p: CGPoint) -> CGPoint {
        CGPoint(x: (p.x - centre.x) / CGFloat(roomR), y: -(p.y - centre.y) / CGFloat(roomR))
    }
    func point(ofUnits u: CGPoint) -> CGPoint {
        CGPoint(x: centre.x + u.x * CGFloat(roomR), y: centre.y - u.y * CGFloat(roomR))
    }
    private func clampPuck(_ p: CGPoint) -> CGPoint {
        let dx = Double(p.x - centre.x), dy = Double(p.y - centre.y)
        let d = (dx * dx + dy * dy).squareRoot(), m = roomR * 0.78
        guard d > m else { return p }
        return CGPoint(x: centre.x + CGFloat(dx / d * m), y: centre.y + CGFloat(dy / d * m))
    }

    // MARK: - the app's spring (UISpringTimingParameters mass 1, stiffness 218.566, damping 29.568)

    private static let K = 218.566, Cd = 29.568
    private func spring(_ x: inout Double, _ v: inout Double, _ target: Double, _ dt: Double) {
        let h = 1.0 / 240.0
        var t = 0.0
        while t < dt {
            let a = -Self.K * (x - target) - Self.Cd * v
            v += a * h; x += v * h; t += h
        }
    }
    private func wrapPi(_ a: Double) -> Double {
        let tau = 2 * Double.pi
        var x = (a + .pi).truncatingRemainder(dividingBy: tau)
        if x < 0 { x += tau }
        return x - .pi
    }

    /// One frame of CPU state: springs, palette, the pulse field and the flurry blobs.
    func advance(_ dt: Double) {
        time += dt
        var px = Double(puck.x), pvx = Double(puckVel.x)
        spring(&px, &pvx, Double(puckTarget.x), dt)
        var py = Double(puck.y), pvy = Double(puckVel.y)
        spring(&py, &pvy, Double(puckTarget.y), dt)
        puck = CGPoint(x: px, y: py); puckVel = CGPoint(x: pvx, y: pvy)
        spring(&puckScale, &puckScaleVel, grab == .puckOrb ? 1.1 : 1.0, dt)
        dir = dirTarget + wrapPi(dir - dirTarget)
        spring(&dir, &dirVel, dirTarget, dt)
        spring(&half, &halfVel, halfTarget, dt)
        spring(&weight, &weightVel, spatial ? cfg.weightSpatial : cfg.weightOmni, dt)
        volume += (volTarget - volume) * min(1, dt * 14)

        palette.blend(toward: paletteTarget, min(1, dt * 1.6))

        // the feed went quiet: keep the rows scrolling so the shimmer fades out instead of freezing
        if Date().timeIntervalSince(lastRow) > 0.4 {
            pulseAcc += dt * 30
            while pulseAcc >= 1 {
                for i in 0..<24 { flux[i] *= 0.5 }
                writePulseRow()
                pulseAcc -= 1
            }
        }

        // pulse field: 6 bands, fast attack / slow release, wobbling and drifting around the puck
        for k in 0..<6 {
            var lv = 0.0
            for i in (k * 4)..<(k * 4 + 4) { lv += 0.55 * spec[i] + 0.9 * flux[i] }
            lv = min(1, lv / 4)
            let tgt = lv + 0.6 * beat * (k < 2 ? 1 : 0.4)
            bands[k] += (tgt > bands[k] ? 0.55 : 0.10) * (tgt - bands[k])
            phases[k].ang += phases[k].w * dt
            phases[k].p1 += phases[k].w1 * dt
            phases[k].p2 += phases[k].w2 * dt
            let q = phases[k]
            let r = 1.18 + 0.22 * Double(k) / 5 + 0.9 * bands[k]
            let amp = q.off * (0.6 + bands[k])
            pulseE[k] = SIMD4<Float>(Float(cos(q.ang) * amp), Float(sin(q.ang) * amp), Float(r), Float(0.22 + 0.30 * bands[k]))
            pulseP[k] = SIMD4<Float>(Float(q.p1), Float(q.p2), Float(0.10 + 0.14 * bands[k]), Float(0.06 + 0.10 * bands[k]))
        }

        for i in blobs.indices {
            blobs[i].life -= dt
            if blobs[i].life <= 0 {
                blobs[i].life = 10 * Double.random(in: 0.5...1.5)
                let a = Double.random(in: 0...(2 * .pi)), rr = Double.random(in: 0...0.42)
                blobs[i].x = cos(a) * rr; blobs[i].y = sin(a) * rr
                blobs[i].r = 0.36 + Double.random(in: 0...0.09)
                blobs[i].ph = Double.random(in: 0...(2 * .pi))
                let va = Double.random(in: 0...(2 * .pi))
                blobs[i].vx = cos(va) * 0.02; blobs[i].vy = sin(va) * 0.02
            }
            blobs[i].x += blobs[i].vx * dt; blobs[i].y += blobs[i].vy * dt
            let lf = min(max(blobs[i].life / 2, 0), 1) * min(max((10 - blobs[i].life) / 2, 0), 1)
            blobs[i].a = lf * (0.55 + 0.45 * sin(time * 0.7 + blobs[i].ph))
        }

        refreshRings()
        publishLabels()
    }
    private var pulseAcc: Double = 0

    private func publishLabels() {
        let vp = Int((volume * 100).rounded())
        if vp != volPct { volPct = vp }
        let wd = Int((halfTarget * 180 / .pi).rounded())
        if wd != widthDeg { widthDeg = wd }
        let rot = ((90 - dirTarget * 180 / .pi).truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
        let ad = Int(rot.rounded()) % 360
        if ad != aimDeg { aimDeg = ad }
        if spatial != spatialOn { spatialOn = spatial }
    }

    /// 0 = fully spatial, 1 = sound all around (the web's uSpatial, inverted).
    var omni: Double {
        min(max((weight - cfg.weightSpatial) / (cfg.weightOmni - cfg.weightSpatial), 0), 1)
    }

    /// prepSDF: the capped-torus parameters for the current direction / spread / weight.
    func sdfParams() -> (axis: SIMD4<Float>, torus: SIMD4<Float>, caps: SIMD4<Float>) {
        let halfRad = spatial ? half : Double.pi
        let spread = min(max(halfRad / .pi, 0.05), 1.0)
        let sinA = sin(spread * .pi), cosA = cos(spread * .pi)
        let nx = sin(dir), ny = -cos(dir)
        let w = weight * 0.25
        let ra = 0.5 - w, rb = w
        let sp2 = min(max(halfRad / .pi, 0), 1)
        let t = min(max((sp2 - 0.5) / 0.2, 0), 1)
        let capR = rb * (t * t * (3 - 2 * t))
        let blend = sp2 > 0.5 ? 0.1 : 1e-5
        let ca = cos(halfRad), sa = sin(halfRad)
        let c1x = ra * (nx * ca - ny * sa), c1y = ra * (nx * sa + ny * ca)
        let c2x = ra * (nx * ca + ny * sa), c2y = ra * (-nx * sa + ny * ca)
        return (SIMD4<Float>(Float(nx), Float(ny), Float(sinA), Float(cosA)),
                SIMD4<Float>(Float(ra), Float(rb), Float(capR), Float(blend)),
                SIMD4<Float>(Float(c1x), Float(c1y), Float(c2x), Float(c2y)))
    }

    // MARK: - gestures (called from the SwiftUI layer)

    func begin(_ p: CGPoint) {
        touched = Date()
        let hr = handleR * 2.2
        if spatial {
            if near(p, handlePos(dir), hr * 1.3) { grab = .notch; return }
            for sgn in [1, -1] where near(p, handlePos(dir + Double(sgn) * half), hr) { grab = .dot(sgn); return }
        } else if near(p, liftDotPos, hr) {
            grab = .lift; liftDot = liftDotPos; return
        }
        if near(p, puck, puckR * 1.25) {
            grab = .puckOrb
            grabOffset = CGSize(width: puck.x - p.x, height: puck.y - p.y)
            Haptics.tap(0.5)
            return
        }
        let d = hypot(Double(p.x - centre.x), Double(p.y - centre.y))
        let a = atan2(Double(p.y - centre.y), Double(p.x - centre.x))
        if cfg.showVolume, d > roomR * 0.92, abs(a - .pi / 2) < Self.volHalf + 0.1 {
            grab = .volume; volTarget = volFrom(p); sendVolume(false)
        }
    }

    func move(_ p: CGPoint) {
        guard let g = grab else { return }
        touched = Date()
        switch g {
        case .puckOrb:
            puckTarget = clampPuck(CGPoint(x: p.x + grabOffset.width, y: p.y + grabOffset.height))
            let was = snapped
            snapped = nil
            if cfg.snapToCell {
                var best: Ring?
                var bd = cfg.snapFrac * roomR
                for r in rings {
                    let d = hypot(Double(r.pos.x - puckTarget.x), Double(r.pos.y - puckTarget.y))
                    if d < bd { bd = d; best = r }
                }
                if let b = best { puckTarget = b.pos; snapped = b.serial }
            }
            if snapped != was, snapped != nil { Haptics.select() }
            send(false)
        case .lift:
            liftDot = p
            let dd = hypot(Double(p.x - puck.x), Double(p.y - puck.y))
            if dd > cfg.liftFrac * fieldR {
                spatial = true
                dir = angleOf(p); dirTarget = dir
                halfTarget = cfg.defaultHalfDeg * .pi / 180
                half = min(halfTarget, 0.35); halfVel = 0
                grab = .notch; liftDot = nil
                Haptics.tap(0.9)
                send(false)
            }
        case .notch:
            dirTarget = angleOf(p); send(false)
        case .dot(let sgn):
            let dd = hypot(Double(p.x - puck.x), Double(p.y - puck.y))
            if dd < cfg.collapseFrac * fieldR {
                spatial = false; grab = nil; Haptics.tap(0.9); send(true); return
            }
            _ = sgn
            let h = abs(wrapPi(angleOf(p) - dirTarget))
            halfTarget = min(max(h, cfg.minHalfDeg * .pi / 180), cfg.maxHalfDeg * .pi / 180)
            send(false)
        case .volume:
            let v = volFrom(p)
            if Int(v * 20) != Int(volTarget * 20) { Haptics.tap(0.4) }
            volTarget = v; sendVolume(false)
        }
    }

    func end() {
        guard let g = grab else { return }
        grab = nil; liftDot = nil
        if g == .volume { sendVolume(true) } else if g != .lift { send(true) }
    }

    /// Two-finger rotate aims the field; pinch sets its width. Neither is possible on the web page.
    func rotateBy(_ delta: Double) {
        guard spatial else { return }
        touched = Date(); dirTarget += delta; send(false)
    }
    func widenBy(_ factor: Double) {
        guard spatial else { return }
        touched = Date()
        halfTarget = min(max(halfTarget * factor, cfg.minHalfDeg * .pi / 180), cfg.maxHalfDeg * .pi / 180)
        send(false)
    }
    func centrePuck() {
        touched = Date(); puckTarget = centre; snapped = nil; Haptics.tap(0.6); send(true)
    }
    func setSpatial(_ on: Bool) {
        touched = Date()
        spatial = on
        if on {
            halfTarget = max(halfTarget, cfg.minHalfDeg * .pi / 180)
            if halfTarget < 0.2 { halfTarget = cfg.defaultHalfDeg * .pi / 180 }
            if half < 0.05 { half = min(halfTarget, 0.35) }
        }
        Haptics.tap(0.8); send(true)
    }

    private func near(_ a: CGPoint, _ b: CGPoint, _ r: Double) -> Bool {
        hypot(Double(a.x - b.x), Double(a.y - b.y)) <= r
    }
    static let volHalf = 52.0 * Double.pi / 180
    private func volFrom(_ p: CGPoint) -> Double {
        let a = atan2(Double(p.y - centre.y), Double(p.x - centre.x))
        let lo = Double.pi / 2 - Self.volHalf, hi = Double.pi / 2 + Self.volHalf
        let aa = min(max(a, lo), hi)
        return (hi - aa) / (hi - lo)
    }

    // MARK: - the two endpoints

    private func body() -> [String: Any] {
        let u = units(of: puckTarget)
        let rot = ((90 - dirTarget * 180 / .pi).truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360)
        return ["ux": (Double(u.x) * 1000).rounded() / 1000, "uy": (Double(u.y) * 1000).rounded() / 1000,
                "rot": (rot * 10).rounded() / 10,
                "width": (halfTarget * 180 / .pi * 10).rounded() / 10,
                "radius": spatial ? cfg.radiusUnits : 0,
                "spatial": spatial, "snap": snapped ?? ""]
    }
    private func send(_ final: Bool) {
        let b = body()
        if final || Date().timeIntervalSince(lastPost) > 0.11 {
            lastPost = Date()
            Task { [agent] in
                do { try await agent?.post("/api/soundfield", b, timeout: 4) }
                catch { await MainActor.run { self.error = error.localizedDescription } }
            }
        } else if !postPending {
            postPending = true
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 120_000_000)
                postPending = false
                send(true)
            }
        }
    }
    private func sendVolume(_ final: Bool) {
        guard final || Date().timeIntervalSince(lastPost) > 0.11 else { return }
        lastPost = Date()
        let v = (volTarget * 1000).rounded() / 1000
        Task { [agent] in try? await agent?.post("/api/volume", ["source": "all", "volume": v], timeout: 4) }
    }

    // MARK: - live state

    func start() {
        guard vizTask == nil else { return }
        vizTask = Task { [weak self] in await self?.streamViz() }
        stateTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollState()
                try? await Task.sleep(nanoseconds: 2_000_000_000)
            }
        }
    }
    func stop() {
        vizTask?.cancel(); vizTask = nil
        stateTask?.cancel(); stateTask = nil
    }

    /// The agent's SSE feed: one frame per audio packet (~30/s) straight off the primary.
    private func streamViz() async {
        guard let a = agent else { return }
        let url = a.base.appendingPathComponent("/api/viz/stream")
        while !Task.isCancelled {
            do {
                var req = URLRequest(url: url)
                req.timeoutInterval = 3600
                req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                let (bytes, _) = try await URLSession.shared.bytes(for: req)
                for try await line in bytes.lines {
                    if Task.isCancelled { return }
                    guard line.hasPrefix("data: ") else { continue }
                    let json = String(line.dropFirst(6))
                    guard let d = json.data(using: .utf8),
                          let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { continue }
                    await MainActor.run { self.ingest(o) }
                }
            } catch {
                if Task.isCancelled { return }
            }
            try? await Task.sleep(nanoseconds: 1_500_000_000)
        }
    }

    private func ingest(_ v: [String: Any]) {
        let e = (v["energy"] as? Double) ?? 0
        let b = (v["bass"] as? Double) ?? 0
        energy += (e - energy) * 0.35
        bass += (b - bass) * 0.4
        let sp = (v["spec"] as? [Double]) ?? []
        for i in 0..<24 {
            let t = sp.isEmpty ? 0 : sp[min(sp.count - 1, i * sp.count / 24)]
            rawSpec[i] = t
            spec[i] += (t - spec[i]) * 0.5
        }
        let newSeq = v["seq"] as? Int
        let isNew = newSeq == nil ? !sp.isEmpty : (newSeq != seq)
        live = (v["live"] as? Bool) ?? false
        if let s = v["src"] as? String, !s.isEmpty { setSource(s) }
        guard isNew else { return }
        if let s = newSeq { seq = s }
        let fx = (v["flux"] as? [Double]) ?? []
        for i in 0..<24 {
            var f: Double
            if !fx.isEmpty {
                f = fx[min(fx.count - 1, i * fx.count / 24)]
            } else {
                f = max(0, rawSpec[i] - emaSpec[i]) * 3.0
                emaSpec[i] += (rawSpec[i] - emaSpec[i]) * 0.12
            }
            flux[i] = min(1, f)
        }
        let fallback = min(1, (flux[1] + flux[2] + flux[3]) / 3 * 1.6)
        beat = max(beat * 0.8, (v["beat"] as? Double) ?? fallback)
        writePulseRow()
        lastRow = Date()
    }

    /// One row of the spectrogram the particles ride: mirrored (bass at the notch), lightly jittered.
    private func writePulseRow() {
        let w = FieldModel.pulseW
        var row = [UInt8](repeating: 0, count: w)
        for c in 0..<w {
            let u = Double(c) / Double(w - 1)
            let bi = abs(u * 2 - 1) * 23
            let i0 = Int(bi), i1 = min(23, Int(bi) + 1), f = bi - Double(i0)
            let base = flux[i0] * (1 - f) + flux[i1] * f
            let j = 1 - cfg.pulseJitter * 0.5 + cfg.pulseJitter * Double.random(in: 0...1)
            row[c] = UInt8(min(255, max(0, (base * j * 255).rounded())))
        }
        pulseQueue.append(row)
        if pulseQueue.count > 6 { pulseQueue.removeFirst() }
    }
    /// Rows the renderer still has to upload, each with the texture row it belongs in.
    func drainPulseRows() -> [(row: Int, data: [UInt8])] {
        guard !pulseQueue.isEmpty else { return [] }
        var out: [(row: Int, data: [UInt8])] = []
        for data in pulseQueue {
            out.append((row: pulseHead, data: data))
            pulseHead = (pulseHead - 1 + FieldModel.pulseRows) % FieldModel.pulseRows
        }
        pulseQueue.removeAll()
        return out
    }
    /// uHead: the newest row, slid forward by how far we are into the next feed packet.
    var pulseHeadNorm: Double {
        let age = min(max(Date().timeIntervalSince(lastRow) * feedFPS, 0), 1)
        return (Double(pulseHead) + 1 - age) / Double(FieldModel.pulseRows)
    }

    private func pollState() async {
        guard let a = agent else { return }
        do {
            let d = try await a.get("/api/state", timeout: 5)
            await MainActor.run { self.apply(d) }
        } catch {
            await MainActor.run { self.error = error.localizedDescription }
        }
    }

    private func apply(_ d: [String: Any]) {
        if error != nil { error = nil }
        let room = (d["room"] as? [String: Any]) ?? [:]
        if let ref = room["reference"] as? [String: Any], let r = ref["radius_m"] as? Double, r > 0.05 {
            reference = (x: (ref["x_m"] as? Double) ?? 0, y: (ref["y_m"] as? Double) ?? 0,
                         r: r, rot: (ref["rotation_deg"] as? Double) ?? 0)
        }
        cellsM = ((d["fleet"] as? [[String: Any]]) ?? []).compactMap { c in
            guard let p = c["position"] as? [String: Any], let x = p["x"] as? Double, let y = p["y"] as? Double else { return nil }
            return (x: x, y: y, active: (c["activeSource"] as? String) != nil, serial: (c["serial"] as? String) ?? "")
        }
        if let l = room["listener"] as? [String: Any], let lx = l["x_m"] as? Double, let ly = l["y_m"] as? Double {
            listenerM = (lx, ly)
        } else {
            listenerM = nil
        }
        if let src = d["sources"] as? [String: [String: Any]] {
            if let act = src.first(where: { ($0.value["active"] as? Bool) == true }) { setSource(act.key) }
        }
        refreshRings()
        // the agent is the source of truth while the user is not touching the field
        guard Date().timeIntervalSince(touched) > 2.5, grab == nil else { return }
        guard let f = room["field"] as? [String: Any] else { return }
        if let vol = (d["sources"] as? [String: [String: Any]])?.values.first(where: { ($0["active"] as? Bool) == true })?["volume"] as? Double {
            volTarget = vol; volume = vol
        }
        let sp = (f["spatial"] as? Bool) ?? false
        if sp != spatial { spatial = sp }
        if let w = f["width"] as? Double {
            halfTarget = min(max(w, cfg.minHalfDeg), cfg.maxHalfDeg) * .pi / 180
            if half < 0.05 { half = halfTarget }
        }
        if let rot = f["rot"] as? Double {
            dirTarget = (90 - rot) * .pi / 180
            if abs(wrapPi(dir - dirTarget)) > .pi * 0.9 { dir = dirTarget }
        }
        if let ux = f["ux"] as? Double, let uy = f["uy"] as? Double {
            let p = clampPuck(point(ofUnits: CGPoint(x: ux, y: uy)))
            if hypot(Double(p.x - puckTarget.x), Double(p.y - puckTarget.y)) > 0.5 { puckTarget = p }
        }
        snapped = (f["snap"] as? String).flatMap { $0.isEmpty ? nil : $0 }
    }
    private var listenerM: (Double, Double)?

    private func toUnits(_ xm: Double, _ ym: Double) -> CGPoint {
        let dx = (xm - reference.x) / reference.r, dy = (ym - reference.y) / reference.r
        let a = -reference.rot * .pi / 180
        return CGPoint(x: dx * cos(a) - dy * sin(a), y: dx * sin(a) + dy * cos(a))
    }
    private func refreshRings() {
        let edge = roomR - roomR * 0.085 * 0.3
        rings = cellsM.compactMap { c in
            let p = point(ofUnits: toUnits(c.x, c.y))
            guard hypot(Double(p.x - centre.x), Double(p.y - centre.y)) <= edge else { return nil }
            return Ring(pos: p, active: c.active, serial: c.serial)
        }
        listener = listenerM.map { point(ofUnits: toUnits($0.0, $0.1)) }
    }

    private func setSource(_ id: String) {
        let s = id.lowercased()
        let name: String
        if s.hasPrefix("spotify") { name = "spotify" }
        else if s.hasPrefix("tidal") { name = "tidal" }
        else if s.contains("hdmi") { name = "hdmi" }
        else if s.contains("pd") || s.contains("pure") { name = "puredata" }
        else if s.contains("usb") { name = "usb" }
        else { name = "airplay" }
        guard name != source else { return }
        source = name
        paletteTarget = FieldPalette.named(name)
        switch name {
        case "spotify": sourceTitle = "Spotify"
        case "tidal": sourceTitle = "TIDAL"
        case "hdmi": sourceTitle = "HDMI"
        case "usb": sourceTitle = "USB"
        case "puredata": sourceTitle = "Pure Data"
        default: sourceTitle = "AirPlay"
        }
    }
}

// MARK: - palettes (decoded from the app's Auras_* colour histograms)

struct FieldPalette {
    var deep: SIMD4<Float>, cyan: SIMD4<Float>, light: SIMD4<Float>, white: SIMD4<Float>
    var blue: SIMD4<Float>, teal: SIMD4<Float>, room: SIMD4<Float>, roomEdge: SIMD4<Float>

    static func rgb(_ r: Double, _ g: Double, _ b: Double) -> SIMD4<Float> {
        SIMD4<Float>(Float(r), Float(g), Float(b), 1)
    }
    mutating func blend(toward t: FieldPalette, _ k: Double) {
        let f = Float(min(max(k, 0), 1))
        deep += (t.deep - deep) * f; cyan += (t.cyan - cyan) * f
        light += (t.light - light) * f; white += (t.white - white) * f
        blue += (t.blue - blue) * f; teal += (t.teal - teal) * f
        room += (t.room - room) * f; roomEdge += (t.roomEdge - roomEdge) * f
    }
    static let airplay = FieldPalette(deep: rgb(0, 0.36, 0.54), cyan: rgb(0.14, 0.64, 0.84), light: rgb(0.54, 0.84, 0.95),
                                      white: rgb(0.90, 0.98, 1.0), blue: rgb(0.10, 0.42, 0.86), teal: rgb(0.08, 0.60, 0.70),
                                      room: rgb(0.10, 0.26, 0.33), roomEdge: rgb(0.07, 0.19, 0.23))
    static let spotify = FieldPalette(deep: rgb(0, 0.30, 0.18), cyan: rgb(0.13, 0.56, 0.33), light: rgb(0.55, 0.86, 0.66),
                                      white: rgb(0.92, 1.0, 0.95), blue: rgb(0.08, 0.46, 0.30), teal: rgb(0.12, 0.62, 0.40),
                                      room: rgb(0.08, 0.22, 0.15), roomEdge: rgb(0.06, 0.16, 0.11))
    static let tidal = FieldPalette(deep: rgb(0, 0.33, 0.33), cyan: rgb(0.10, 0.58, 0.58), light: rgb(0.55, 0.86, 0.86),
                                    white: rgb(0.90, 1.0, 1.0), blue: rgb(0.05, 0.45, 0.50), teal: rgb(0.08, 0.60, 0.58),
                                    room: rgb(0.08, 0.24, 0.24), roomEdge: rgb(0.06, 0.17, 0.17))
    static let hdmi = FieldPalette(deep: rgb(0.10, 0.08, 0.40), cyan: rgb(0.30, 0.28, 0.80), light: rgb(0.65, 0.62, 0.95),
                                   white: rgb(0.95, 0.93, 1.0), blue: rgb(0.20, 0.20, 0.75), teal: rgb(0.35, 0.25, 0.70),
                                   room: rgb(0.12, 0.10, 0.30), roomEdge: rgb(0.08, 0.07, 0.22))
    static let puredata = FieldPalette(deep: rgb(0.45, 0.17, 0.08), cyan: rgb(0.93, 0.48, 0.33), light: rgb(1.0, 0.78, 0.62),
                                       white: rgb(1.0, 0.96, 0.92), blue: rgb(0.85, 0.35, 0.20), teal: rgb(0.95, 0.55, 0.30),
                                       room: rgb(0.30, 0.16, 0.10), roomEdge: rgb(0.22, 0.11, 0.07))
    static let usb = FieldPalette(deep: rgb(0.25, 0.25, 0.26), cyan: rgb(0.55, 0.55, 0.56), light: rgb(0.80, 0.80, 0.82),
                                  white: rgb(0.98, 0.98, 1.0), blue: rgb(0.40, 0.42, 0.48), teal: rgb(0.50, 0.52, 0.55),
                                  room: rgb(0.20, 0.21, 0.22), roomEdge: rgb(0.14, 0.15, 0.16))
    static func named(_ n: String) -> FieldPalette {
        switch n {
        case "spotify": return .spotify
        case "tidal": return .tidal
        case "hdmi": return .hdmi
        case "puredata": return .puredata
        case "usb": return .usb
        default: return .airplay
        }
    }
    /// The SF Symbol the puck wears for this source (the app puts the service's glyph there).
    static func symbol(_ n: String) -> String {
        switch n {
        case "spotify", "tidal": return "music.note"
        case "hdmi": return "tv"
        case "usb": return "cable.connector"
        case "puredata": return "waveform"
        default: return "airplayaudio"
        }
    }
}
