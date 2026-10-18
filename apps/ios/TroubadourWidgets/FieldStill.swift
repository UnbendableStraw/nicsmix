import Foundation
import Dispatch
#if canImport(CoreGraphics)
import CoreGraphics
#endif

// The sound field as one still picture, drawn on the CPU.
//
// Widgets cannot run the Metal view (iOS draws a widget once, in the background, where GPU work is not
// allowed), so this is the same four passes done in Swift: the scene fragment line for line, the particle
// kernel stepped on the CPU until the cloud has settled into the field's shape, the points splatted on
// top, then the glass orbs and the speaker rings. The model is the app's own FieldModel, fed one
// /api/state and one /api/viz/frame — so the shape, the speakers and the LED colour are exactly what the
// app shows. Only the motion is missing.

typealias V2 = SIMD2<Float>
typealias V3 = SIMD3<Float>

@inline(__always) private func clampf(_ x: Float, _ a: Float, _ b: Float) -> Float { min(max(x, a), b) }
@inline(__always) private func smooth(_ e0: Float, _ e1: Float, _ x: Float) -> Float {
    let t = clampf((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t)
}
@inline(__always) private func mixf(_ a: Float, _ b: Float, _ t: Float) -> Float { a + (b - a) * t }
@inline(__always) private func mix3(_ a: V3, _ b: V3, _ t: Float) -> V3 { a + (b - a) * t }
@inline(__always) private func len2(_ v: V2) -> Float { (v.x * v.x + v.y * v.y).squareRoot() }
@inline(__always) private func len3(_ v: V3) -> Float { (v.x * v.x + v.y * v.y + v.z * v.z).squareRoot() }
@inline(__always) private func rgb(_ v: SIMD4<Float>) -> V3 { V3(v.x, v.y, v.z) }
@inline(__always) private func fract(_ x: Float) -> Float { x - x.rounded(.down) }

/// An alpha mask (the source glyph the puck wears), side x side, 0..1.
struct GlyphMask { let side: Int; let alpha: [Float] }

/// What the widget gets back: RGBA8, row-major, top row first.
struct StillImage { let width: Int; let height: Int; let rgba: [UInt8] }

enum FieldStill {

    // MARK: - the model

    /// A headless FieldModel for a picture `size` points across: the room centred and as large as the halo
    /// allows, geometry and the field from /api/state, audio and LED colour from /api/viz/frame.
    static func model(state: [String: Any]?, frame: [String: Any]?, size: CGSize, scale: Double) -> FieldModel {
        let m = FieldModel(agent: nil)
        m.cfg.showVolume = false
        m.contentsScale = scale
        m.layout(size)
        // the app leaves room for the volume rim under the circle; a widget has none, so centre it
        m.centre = CGPoint(x: size.width / 2, y: size.height / 2)
        m.roomR = Double(min(size.width, size.height)) * 0.40
        m.puck = m.centre; m.puckTarget = m.centre
        if let s = state { m.apply(s) }
        if let f = frame { for _ in 0..<12 { m.ingest(f) } }   // the feed smooths; let it reach the frame
        m.settle()
        m.advance(1.0 / 30)
        m.settle()
        return m
    }

    // MARK: - the picture

    static func render(_ m: FieldModel, glyph: GlyphMask? = nil, settleSteps: Int = 90) -> StillImage {
        let scale = Float(m.contentsScale)
        let W = max(1, Int((Double(m.size.width) * m.contentsScale).rounded()))
        let H = max(1, Int((Double(m.size.height) * m.contentsScale).rounded()))
        let G = Geo(m, scale: scale)
        var scene = [V3](repeating: .zero, count: W * H)
        scene.withUnsafeMutableBufferPointer { buf in
            let p = buf.baseAddress!
            DispatchQueue.concurrentPerform(iterations: H) { y in
                for x in 0..<W { p[y * W + x] = G.scene(V2(Float(x) + 0.5, Float(y) + 0.5)) }
            }
        }
        splatParticles(m, G, &scene, W, H, steps: settleSteps)
        var out = [UInt8](repeating: 255, count: W * H * 4)
        scene.withUnsafeBufferPointer { sb in
            out.withUnsafeMutableBufferPointer { ob in
                let s = sb.baseAddress!, o = ob.baseAddress!
                DispatchQueue.concurrentPerform(iterations: H) { y in
                    for x in 0..<W {
                        let c = G.overlay(V2(Float(x) + 0.5, Float(y) + 0.5), s, W, H, glyph)
                        let i = (y * W + x) * 4
                        o[i] = UInt8(clampf(c.x, 0, 1) * 255 + 0.5)
                        o[i + 1] = UInt8(clampf(c.y, 0, 1) * 255 + 0.5)
                        o[i + 2] = UInt8(clampf(c.z, 0, 1) * 255 + 0.5)
                        o[i + 3] = 255
                    }
                }
            }
        }
        return StillImage(width: W, height: H, rgba: out)
    }

    // MARK: - geometry + uniforms, in pixels (the renderer's FieldUniforms / OverlayUniforms)

    struct Geo {
        let C: V2, P: V2, R: Float, Rf: Float, puckR: Float, handleR: Float, time: Float
        let axis: SIMD4<Float>, torus: SIMD4<Float>, caps: SIMD4<Float>
        let energy: Float, bass: Float, spatialMix: Float, haloGain: Float
        let glow: Float, grain: Float, flurry: Float
        let cDeep: V3, cCyan: V3, cWhite: V3, cBlue: V3, cTeal: V3, cRoom: V3, cRoomEdge: V3
        let blobs: [SIMD4<Float>], pulseE: [SIMD4<Float>], pulseP: [SIMD4<Float>]
        let omni: Float, hA: V2, hB: V2, notch: V2, dir: Float, ntW: Float, ntL: Float
        let lift: V2?, rings: [SIMD4<Float>], listener: V2?

        init(_ m: FieldModel, scale s: Float) {
            func px(_ p: CGPoint) -> V2 { V2(Float(p.x) * s, Float(p.y) * s) }
            C = px(m.centre); P = px(m.puck)
            R = Float(m.roomR) * s; Rf = Float(m.fieldR) * s; puckR = Float(m.puckR) * s; handleR = Float(m.handleR) * s
            time = Float(m.time)
            let sdf = m.sdfParams(); axis = sdf.axis; torus = sdf.torus; caps = sdf.caps
            energy = Float(m.energy); bass = Float(m.bass); spatialMix = Float(1 - m.omni); haloGain = Float(m.cfg.halo)
            glow = Float(m.cfg.glow); grain = Float(m.cfg.grain); flurry = Float(m.cfg.flurry)
            let pal = m.palette
            cDeep = rgb(pal.deep); cCyan = rgb(pal.cyan); cWhite = rgb(pal.white)
            cBlue = rgb(pal.blue); cTeal = rgb(pal.teal); cRoom = rgb(pal.room); cRoomEdge = rgb(pal.roomEdge)
            var b = [SIMD4<Float>](repeating: .zero, count: 24)
            for (i, x) in m.blobs.enumerated() where i < 24 { b[i] = SIMD4<Float>(Float(x.x), Float(x.y), Float(x.r), Float(x.a)) }
            blobs = b
            pulseE = m.pulseE; pulseP = m.pulseP
            omni = Float(m.omni)
            hA = px(m.handlePos(m.dir + m.half)); hB = px(m.handlePos(m.dir - m.half)); notch = px(m.handlePos(m.dir))
            dir = Float(m.dir)
            ntW = Float(m.notchSize.0) * s; ntL = Float(m.notchSize.1) * s
            lift = m.spatial ? nil : px(m.liftDotPos)
            rings = m.cfg.showRings ? m.rings.prefix(8).map { r in
                let p = px(r.pos)
                return SIMD4<Float>(p.x, p.y, r.active ? 1 : 0, (m.snapped == r.serial && !r.serial.isEmpty) ? 1 : 0)
            } : []
            listener = m.listener.map(px)
        }

        // the shared capped-torus SDF, exactly as the shaders have it
        func sdf(_ p: V3) -> Float {
            let n = V2(axis.x, axis.y), sinA = axis.z, cosA = axis.w
            let ra = torus.x, rb = torus.y, capR = torus.z, blend = torus.w
            let yr = p.x * n.x + p.y * n.y
            let xr = abs(p.x * (-n.y) + p.y * n.x)
            let k = (cosA * xr > sinA * yr) ? (xr * sinA + yr * cosA) : (xr * xr + yr * yr).squareRoot()
            let k2 = (xr * xr + yr * yr).squareRoot()
            let pp = xr * xr + yr * yr + p.z * p.z
            let d1 = max(0, pp + ra * ra - 2 * ra * k).squareRoot() - rb
            let d2 = max(0, pp + ra * ra - 2 * ra * k2).squareRoot() - rb
            let dS = len3(V3(p.x - caps.x, p.y - caps.y, p.z)) - capR
            let dE = len3(V3(p.x - caps.z, p.y - caps.w, p.z)) - capR
            let h = clampf(0.5 * (dE - dS) / blend + 0.5, 0, 1)
            let sm = mixf(dE, dS, h) - blend * h * (1 - h)
            return max(min(sm, d1), d2)
        }

        // sceneFragment
        func scene(_ px: V2) -> V3 {
            let Rr = max(R, 1), Rff = max(Rf, 1), pR = max(puckR, 1)
            let dC = len2(px - C)
            let rr = dC / Rr
            let inRoom = 1 - smooth(Rr - 1, Rr + 1, dC)
            let f = (px - P) / (2 * Rff)
            let d = sdf(V3(f.x, f.y, 0))
            let fr = len2(f)
            let px2f = 1 / (2 * Rff)

            var col = mix3(cRoom, cRoomEdge, smooth(0.35, 1.0, rr))
            col *= 1 - 0.28 * smooth(-0.2, 1.0, (px.y - C.y) / Rr)

            let inside = 1 - smooth(-6 * px2f, 2 * px2f, d)
            let spill = exp(-max(d, 0) * 16)
            let core = exp(-fr * 7)
            var g = inside * (0.11 + 0.12 * exp(-fr * 3)) + spill * 0.12 * (1 - inside)
            g += exp(-abs(d) * 30) * 0.16 * inside
            g *= 0.80 + 0.45 * energy
            g += core * 0.22 * (1 + 0.5 * bass)

            var fl: Float = 0
            var flc = V3.zero
            for i in 0..<24 {
                let bb = blobs[i]
                if bb.w <= 0 { continue }
                let q = len2(f - V2(bb.x, bb.y)) / max(bb.z, 0.001)
                let w = bb.w * (1 - smooth(0.1, 1.0, q))
                fl += w
                flc += (i % 2 == 0 ? cBlue : cTeal) * w
            }
            g += fl * 0.10 * flurry * inside
            g *= glow

            let aura = mix3(mix3(cDeep, cCyan, clampf(g * 2, 0, 1)), cWhite, clampf((g - 0.5) * 2, 0, 1))
            var gc = inside * (0.55 + 0.30 * exp(-fr * 2.2)) + spill * 0.25 * (1 - inside)
            gc *= 0.85 + 0.3 * energy
            var classic = mix3(cBlue, mix3(cCyan, cWhite, 0.35), clampf((gc - 0.55) * 2.2, 0, 1))
            classic = mix3(classic, flc / max(fl, 0.001), clampf(fl * 0.35 * flurry, 0, 0.45))
            classic *= 0.62 + 0.38 * exp(-fr * 2.6)      // depth: bright at the puck, deeper toward the rim
            classic += cWhite * core * 0.6 * (1 + 0.4 * bass)

            let auraS = mix3(col, aura, smooth(0, 0.55, g))
            var auraC = mix3(col, classic, smooth(0, 0.5, gc))
            auraC = mix3(auraC, auraC + flc * 0.12 * flurry, inside)
            col = mix3(auraC, auraS, spatialMix)

            var acc: Float = 0
            for k in 0..<6 {
                let pe = pulseE[k], ph = pulseP[k]
                let c = P + V2(pe.x, pe.y) * pR
                let dv = px - c
                let dd = len2(dv) / pR
                let th = atan2(dv.y, dv.x)
                let rad = pe.z * (1 + ph.z * cos(2 * th + ph.x) + ph.w * cos(3 * th + ph.y))
                let cov = 1 - smooth(rad - 0.22, rad + 0.22, dd)
                acc = acc + cov * pe.w * (1 - acc)
            }
            acc *= haloGain * mixf(0.75, 1.0, spatialMix)
            col = mix3(col, mix3(cWhite, V3(1, 1, 1), 0.5), clampf(acc, 0, 0.97))

            let outer = exp(-max(dC - Rr, 0) / (0.05 * Rr)) * (1 - inRoom) * smooth(0, 0.4, g) * 0.35
            col += cCyan * outer

            let n = grainNoise(px / 96 + V2(time * 0.013, -time * 0.009))
            col *= 1 + (n - 0.5) * 0.22 * grain

            col += V3(0.10, 0.22, 0.28) * smooth(0.90, 0.985, rr) * (1 - smooth(0.985, 1.0, rr))
            col = mix3(col, cCyan * outer, 1 - inRoom)
            return col
        }

        /// The 128x128 white-noise texture, sampled with linear filtering and repeat (a hash lattice here).
        private func grainNoise(_ uv: V2) -> Float {
            let x = uv.x * 128 - 0.5, y = uv.y * 128 - 0.5
            let x0 = x.rounded(.down), y0 = y.rounded(.down)
            let fx = x - x0, fy = y - y0
            func h(_ i: Float, _ j: Float) -> Float {
                let ii = Int32(truncatingIfNeeded: Int(i)) & 127, jj = Int32(truncatingIfNeeded: Int(j)) & 127
                var v = UInt32(bitPattern: ii &* 73856093 ^ jj &* 19349663)
                v = (v ^ (v >> 13)) &* 0x5bd1e995; v ^= v >> 15
                return Float(v & 0xffff) / 65535
            }
            let a = mixf(h(x0, y0), h(x0 + 1, y0), fx), b = mixf(h(x0, y0 + 1), h(x0 + 1, y0 + 1), fx)
            return mixf(a, b, fy)
        }

        // MARK: overlay

        private func lensAt(_ r: Float) -> Float {
            let x = clampf(r, 0, 1) * 63
            let i = Int(x), j = min(63, i + 1)
            return mixf(FieldStill.lens[i], FieldStill.lens[j], x - Float(i))
        }
        private func sample(_ s: UnsafePointer<V3>, _ W: Int, _ H: Int, _ p: V2) -> V3 {
            let x = clampf(p.x - 0.5, 0, Float(W - 1)), y = clampf(p.y - 0.5, 0, Float(H - 1))
            let x0 = Int(x), y0 = Int(y), x1 = min(W - 1, x0 + 1), y1 = min(H - 1, y0 + 1)
            let fx = x - Float(x0), fy = y - Float(y0)
            let a = mix3(s[y0 * W + x0], s[y0 * W + x1], fx), b = mix3(s[y1 * W + x0], s[y1 * W + x1], fx)
            return mix3(a, b, fy)
        }
        private func glyphAlpha(_ g: GlyphMask, _ uv: V2) -> Float {
            let x = clampf(uv.x * Float(g.side) - 0.5, 0, Float(g.side - 1)), y = clampf(uv.y * Float(g.side) - 0.5, 0, Float(g.side - 1))
            let x0 = Int(x), y0 = Int(y), x1 = min(g.side - 1, x0 + 1), y1 = min(g.side - 1, y0 + 1)
            let fx = x - Float(x0), fy = y - Float(y0)
            let a = mixf(g.alpha[y0 * g.side + x0], g.alpha[y0 * g.side + x1], fx)
            let b = mixf(g.alpha[y1 * g.side + x0], g.alpha[y1 * g.side + x1], fx)
            return mixf(a, b, fy)
        }

        /// glassOrb: refracts the scene through the app's lens table, plus the gloss.
        private func orb(_ px: V2, _ centre: V2, _ radius: Float, _ halfLen: Float, _ rot: Float,
                         _ darken: Float, _ tint: Float, _ glyph: GlyphMask?,
                         _ s: UnsafePointer<V3>, _ W: Int, _ H: Int) -> (V3, Float)? {
            let c = cos(-rot), sn = sin(-rot)
            let dv = px - centre
            var l = V2(dv.x * c - dv.y * sn, dv.x * sn + dv.y * c) / max(radius, 0.5)
            var along: Float = 0
            if halfLen > radius {
                let ext = halfLen / radius - 1
                along = clampf(l.y, -ext, ext)
                l.y -= along
            }
            let r = len2(l)
            let aa = 1 - smooth(1 - 2 / max(radius, 2), 1, r)
            if aa <= 0.001 { return nil }
            let dir = r > 0.0001 ? l / r : V2.zero
            let off = lensAt(r) * 2 - 0.5
            let local = V2(dir.x * off, along + dir.y * off) * radius
            let cr = cos(rot), sr = sin(rot)
            let sp = centre + V2(local.x * cr - local.y * sr, local.x * sr + local.y * cr)
            var bg = sample(s, W, H, sp)
            bg = mix3(bg, bg * V3(0.30, 0.62, 1.0) + V3(0.02, 0.16, 0.38), tint)
            let h = l - V2(-0.34, -0.42)
            let spec = exp(-(h.x * h.x + h.y * h.y) * 5.5)
            var col = bg * mixf(1, 0.72, darken * (1 - smooth(0.7, 0.95, r)))
            col += V3(0.86, 0.95, 1.0) * spec * (0.26 + 0.20 * darken)
            let rim = smooth(0.74, 0.90, r) * (1 - smooth(0.93, 1.0, r))
            col += V3(0.90, 0.98, 1.0) * rim * (0.35 + 1.05 * darken)
            col *= 1 - 0.18 * smooth(0.55, 1.0, r)
            if let g = glyph {
                let a = glyphAlpha(g, V2(0.5 + 0.5 * l.x * 1.30, 0.5 + 0.5 * l.y * 1.30))
                col = mix3(col, V3(1, 1, 1), a * 0.95)
            }
            return (col, aa)
        }
        private func ringMask(_ px: V2, _ c: V2, _ radius: Float, _ w: Float) -> Float {
            let d = abs(len2(px - c) - radius)
            return 1 - smooth(w * 0.5, w * 0.5 + 1.5, d)
        }

        // overlayFragment (no volume rim on a widget)
        func overlay(_ px: V2, _ s: UnsafePointer<V3>, _ W: Int, _ H: Int, _ glyph: GlyphMask?) -> V3 {
            var col = s[min(H - 1, Int(px.y)) * W + min(W - 1, Int(px.x))]
            let Rr = max(R, 1)
            let scale = max(Rr / 138.6, 0.5)
            // nothing near: the scene as it is (most of the picture)
            let dsh = len2(px - P) / max(puckR * 2, 1)
            col = mix3(col, V3(0.02, 0.08, 0.14), 0.40 * smooth(1.0, 0.30, dsh))
            if omni < 0.999 {
                let alpha = 1 - omni
                let hr = max(handleR, 1)
                if let a = orb(px, hA, hr, 0, 0, 0.35, 0, nil, s, W, H) { col = mix3(col, a.0, a.1 * alpha) }
                if let b = orb(px, hB, hr, 0, 0, 0.35, 0, nil, s, W, H) { col = mix3(col, b.0, b.1 * alpha) }
                if let n = orb(px, notch, ntW, ntL, dir, 0.40, 0, nil, s, W, H) { col = mix3(col, n.0, n.1 * alpha) }
            } else if let ld = lift {
                if let o = orb(px, ld, max(handleR * 0.85, 1), 0, 0, 0.50, 0, nil, s, W, H) { col = mix3(col, o.0, o.1) }
            }
            if let pk = orb(px, P, puckR, 0, 0, 0.25, 0.85, glyph, s, W, H) { col = mix3(col, pk.0, pk.1) }
            let rr = Rr * 0.085
            for rg in rings {
                let c = V2(rg.x, rg.y)
                if len2(px - c) > rr * 1.4 + 4 { continue }
                let snapped = rg.w
                let rad = rr * mixf(1.0, 1.18, snapped)
                let a = ringMask(px, c, rad, mixf(2.0, 2.6, snapped) * scale)
                let alpha = mixf(mixf(0.16, 0.26, rg.z), 0.75, snapped)
                col = mix3(col, V3(0.92, 0.98, 1.0), a * alpha)
                col = mix3(col, V3(0.80, 0.88, 0.92), ringMask(px, c, rr * 0.62, 1.2 * scale) * 0.09)
                col = mix3(col, V3(0.78, 0.88, 0.92), (1 - smooth(rad - 1, rad, len2(px - c))) * 0.04)
            }
            if let l = listener {
                col = mix3(col, V3(0.90, 0.95, 0.97), ringMask(px, l, 4 * scale, 1.2 * scale) * 0.45)
            }
            return col
        }
    }

    // MARK: - particles: the kernel on the CPU, then additive points

    private struct Part { var pos: V3; var life: Float; var vel: V3; var flt: Float; var org: V3; var home: Float; var seed: Float; var rt: Float }

    private static func splatParticles(_ m: FieldModel, _ G: Geo, _ img: inout [V3], _ W: Int, _ H: Int, steps: Int) {
        let s = Float(m.contentsScale)
        // as many points as keeps the app's density at this size (its 24k fill a ~414 px room at 3x)
        let pts = Float(m.cfg.pointScale) * max(0.7, Float(m.roomR) / 180)
        let sizeW = 0.9 * s * pts, sizeApp: Float = 0.9 * 3 * max(0.7, 138.0 / 180)
        let rel = (G.R / 414) * (G.R / 414) * (sizeApp / sizeW) * (sizeApp / sizeW)
        let count = Int(clampf(24_000 * rel, 1_500, 16_000))

        func rand1(_ x: Double) -> Float { let v = sin(x) * 43758.5453; return Float(v - v.rounded(.down)) }
        var ps = [Part](); ps.reserveCapacity(count)
        var rng = SplitMix(seed: 0x5eed)
        for i in 0..<count {
            let t = Double(i) * 0.125
            let o = V3(rand1(t) - 0.5, rand1(t + 1) - 0.5, rand1(t + 2) - 0.5)
            ps.append(Part(pos: o, life: 10 + 90 * rand1(Double(i) * 0.37 + 1.3), vel: .zero, flt: 0,
                           org: o, home: 0, seed: rng.next01(), rt: 0))
        }
        let h = Float(1.0 / m.cfg.simHz)
        let flux = m.flux.map { Float($0) }
        let lowEdge = Float(1 - min(max(m.cfg.reactiveFrac, 0.05), 1))
        let puffGain = Float(m.cfg.puffGain), puffVmax = Float(m.cfg.puffVmax)
        let puffFloat = Float(m.cfg.puffFloat), puffReach = Float(m.cfg.puffReach)
        let kick = Float(m.cfg.puffBeat * m.beat)
        let n = V2(G.axis.x, G.axis.y)
        ps.withUnsafeMutableBufferPointer { buf in
            let p = buf.baseAddress!
            DispatchQueue.concurrentPerform(iterations: max(1, count / 512 + 1)) { chunk in
                let lo = chunk * 512, hi = min(count, lo + 512)
                if lo >= hi { return }
                for step in 0..<steps {
                    let time = Float(m.time) + Float(step) * h
                    for i in lo..<hi { simulate(&p[i], G, n, h, time, flux, lowEdge, kick, puffGain, puffVmax, puffFloat, puffReach) }
                }
            }
        }
        // particleVertex / particleFragment: a cone of radius size/2, pale cyan, added on
        img.withUnsafeMutableBufferPointer { ib in
            let o = ib.baseAddress!
            for q in ps {
                let sp = G.P + V2(q.pos.x, q.pos.y) * 2 * G.Rf
                let size = sizeW * (0.7 + 0.6 * q.seed)
                let energy = Float.pi / 3 * size * size / 4
                let c = V3(0.66, 0.92, 1.0) * 0.75 * 0.5 * Float(m.cfg.pointGain) * min(energy, 4)
                let x = sp.x - 0.5, y = sp.y - 0.5
                let x0 = Int(x.rounded(.down)), y0 = Int(y.rounded(.down))
                let fx = x - Float(x0), fy = y - Float(y0)
                for (dx, dy, w) in [(0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)] {
                    let xx = x0 + dx, yy = y0 + dy
                    if xx < 0 || yy < 0 || xx >= W || yy >= H { continue }
                    o[yy * W + xx] += c * w
                }
            }
        }
    }

    @inline(__always)
    private static func simulate(_ p: inout Part, _ G: Geo, _ n: V2, _ dt: Float, _ time: Float, _ flux: [Float],
                                 _ lowEdge: Float, _ kick: Float, _ gain: Float, _ vmax: Float, _ float: Float, _ reach: Float) {
        var x = p.pos, v = p.vel
        var life = p.life - dt * 0.05 * 60
        var flt = p.flt, home = p.home, rt = p.rt
        let sdr = fract(p.seed * 7.13 + 0.37)
        let tt = clampf((sdr - lowEdge) / 0.15, 0, 1)
        let react = 0.12 + 0.88 * tt * tt * (3 - 2 * tt)
        let dist = len2(V2(x.x, x.y))
        if dist > 0.02 {
            let ang = atan2(-x.x * n.y + x.y * n.x, x.x * n.x + x.y * n.y)
            let ub = clampf((0.5 + 0.5 * ang / .pi) * 24 - 0.5, 0, 23.999)
            let b0 = Int(ub), b1 = min(23, b0 + 1)
            let tb = ub - Float(b0)
            let fl = 0.75 * (flux[b0] * (1 - tb) + flux[b1] * tb)
                   + 0.25 * (flux[max(0, b0 - 1)] * (1 - tb) + flux[min(23, b1 + 1)] * tb)
            let home0 = home > 0.02 ? home : dist
            let att = clampf(1 - (dist - home0) / max(0.05, reach), 0, 1)
            let imp = react * (gain * fl + kick) * att
            let vr = (v.x * x.x + v.y * x.y) / dist
            if imp > 0.0002 && vr < vmax && dist < 0.6 {
                let add = min(imp, vmax - vr)
                v.x += x.x / dist * add; v.y += x.y / dist * add
                if flt <= 0 && rt <= 0 { home = home > 0.02 ? home + clampf(dist - home, -0.02, 0.02) : dist }
                flt = max(flt, float * react)
                rt = float * 1.5
            }
        }
        var nx: V3
        if life > 0 {
            let eps: Float = 0.001
            let pr = x + v
            let d0 = G.sdf(pr)
            var grad = V3(G.sdf(pr + V3(eps, 0, 0)) - d0, G.sdf(pr + V3(0, eps, 0)) - d0, G.sdf(pr + V3(0, 0, eps)) - d0)
            let gl = len3(grad)
            grad = gl > 1e-9 ? grad / gl : .zero
            let ks: Float = flt > 0.5 ? 0.08 : (flt > 0 ? 0.08 + 0.92 * (1 - flt / 0.5) : 1)
            let f = -((d0 > 0 ? 1.2 : 0.5) * d0 * ks) * grad
            v += f
            nx = x + f + v
        } else {
            let sv = sin(life + time * 0.01)
            nx = p.org * sv
            life = 100
        }
        let l = len3(nx)
        if l > 1 { nx /= l }
        if rt > 0 && home > 0.02 {
            let d2 = len2(V2(nx.x, nx.y))
            if d2 > 0.02 {
                let g = (home - d2) * (flt > 0 ? 0.012 : 0.04)
                v.x += nx.x / d2 * g; v.y += nx.y / d2 * g
            }
            if flt <= 0 { rt -= dt }
        }
        var damp: Float = flt > 0 ? 0.94 : 0.99
        if flt > 0 && nx.x * nx.x + nx.y * nx.y > 0.42 { damp = 0.85 }
        x = nx
        p.pos = x; p.life = life; p.vel = v * damp; p.flt = flt > 0 ? flt - dt : 0; p.home = home; p.rt = rt
    }

    private struct SplitMix {
        var s: UInt64
        init(seed: UInt64) { s = seed }
        mutating func next01() -> Float {
            s &+= 0x9E3779B97F4A7C15
            var z = s
            z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
            z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
            z ^= z >> 31
            return Float(z >> 40) / Float(1 << 24)
        }
    }

    /// Aurora Orb lens table (refraction offset by radius), from the decrypted app — same as the shader's.
    static let lens: [Float] = [
        0.251,0.261,0.271,0.281,0.291,0.302,0.312,0.322,0.332,0.342,0.352,0.362,0.372,0.383,0.393,0.403,
        0.413,0.423,0.433,0.443,0.453,0.463,0.472,0.482,0.492,0.502,0.512,0.521,0.531,0.541,0.550,0.560,
        0.569,0.579,0.588,0.597,0.606,0.616,0.625,0.633,0.642,0.651,0.659,0.667,0.676,0.684,0.691,0.699,
        0.706,0.712,0.719,0.725,0.730,0.734,0.738,0.741,0.742,0.741,0.738,0.731,0.719,0.697,0.657,0.570
    ]
}
