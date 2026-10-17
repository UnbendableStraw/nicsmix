import Foundation
import MetalKit
import UIKit
import simd

// Mirrors of the shader structs. Only SIMD4<Float> members, so the Swift and Metal layouts match
// without any padding guesswork.
struct FieldUniforms {
    var res = SIMD4<Float>(), geom = SIMD4<Float>(), puck = SIMD4<Float>()
    var axis = SIMD4<Float>(), torus = SIMD4<Float>(), caps = SIMD4<Float>()
    var env = SIMD4<Float>(), gains = SIMD4<Float>(), pts = SIMD4<Float>(), pulse = SIMD4<Float>()
    var cDeep = SIMD4<Float>(), cCyan = SIMD4<Float>(), cLight = SIMD4<Float>(), cWhite = SIMD4<Float>()
    var cBlue = SIMD4<Float>(), cTeal = SIMD4<Float>(), cRoom = SIMD4<Float>(), cRoomEdge = SIMD4<Float>()
}

struct SimUniforms {
    var axis = SIMD4<Float>(), torus = SIMD4<Float>(), caps = SIMD4<Float>()
    var cfg = SIMD4<Float>(), cfg2 = SIMD4<Float>()
}

struct OverlayUniforms {
    var res = SIMD4<Float>(), geom = SIMD4<Float>(), puck = SIMD4<Float>()
    var hA = SIMD4<Float>(), hB = SIMD4<Float>(), nt = SIMD4<Float>()
    var ntSize = SIMD4<Float>(), lift = SIMD4<Float>(), flags = SIMD4<Float>()
}

struct ParticleGPU {
    var pos = SIMD4<Float>(), vel = SIMD4<Float>(), org = SIMD4<Float>(), aux = SIMD4<Float>()
}

/// The four passes of the sound field, on the GPU. Called from MTKView's own draw callback, on the
/// main thread, so it can read FieldModel directly.
final class FieldRenderer: NSObject, MTKViewDelegate {
    let model: FieldModel
    private let device: MTLDevice
    private let queue: MTLCommandQueue
    private let scenePS: MTLRenderPipelineState
    private let blitPS: MTLRenderPipelineState
    private let pointPS: MTLRenderPipelineState
    private let overlayPS: MTLRenderPipelineState
    private let simPS: MTLComputePipelineState
    private let smpRepeat: MTLSamplerState
    private let smpPulse: MTLSamplerState
    private let smpClamp: MTLSamplerState
    private let noiseTex: MTLTexture
    private let pulseTex: MTLTexture
    private var glyphTex: MTLTexture
    private var glyphName = ""
    private var parts: MTLBuffer
    private var count: Int
    private var sceneLow: MTLTexture?
    private var sceneFull: MTLTexture?
    private var lowSize = CGSize.zero
    private var fullSize = CGSize.zero
    private var last: CFTimeInterval = 0
    private var simAcc: Double = 0

    init?(model: FieldModel, pixelFormat: MTLPixelFormat = .bgra8Unorm) {
        guard let dev = MTLCreateSystemDefaultDevice(),
              let q = dev.makeCommandQueue(),
              let lib = dev.makeDefaultLibrary() else { return nil }
        self.model = model
        device = dev
        queue = q

        func pipeline(_ vs: String, _ fs: String, blend: Bool, format: MTLPixelFormat) -> MTLRenderPipelineState? {
            let d = MTLRenderPipelineDescriptor()
            d.vertexFunction = lib.makeFunction(name: vs)
            d.fragmentFunction = lib.makeFunction(name: fs)
            let at = d.colorAttachments[0]!
            at.pixelFormat = format
            if blend {
                at.isBlendingEnabled = true
                at.rgbBlendOperation = .add; at.alphaBlendOperation = .add
                at.sourceRGBBlendFactor = .one; at.destinationRGBBlendFactor = .one
                at.sourceAlphaBlendFactor = .one; at.destinationAlphaBlendFactor = .one
            }
            return try? dev.makeRenderPipelineState(descriptor: d)
        }
        guard let sp = pipeline("fullscreenVertex", "sceneFragment", blend: false, format: .bgra8Unorm),
              let bp = pipeline("fullscreenVertex", "blitFragment", blend: false, format: .bgra8Unorm),
              let pp = pipeline("particleVertex", "particleFragment", blend: true, format: .bgra8Unorm),
              let op = pipeline("fullscreenVertex", "overlayFragment", blend: false, format: pixelFormat),
              let sim = lib.makeFunction(name: "simulateParticles"),
              let simState = try? dev.makeComputePipelineState(function: sim) else { return nil }
        scenePS = sp; blitPS = bp; pointPS = pp; overlayPS = op; simPS = simState

        func sampler(_ s: MTLSamplerAddressMode, _ t: MTLSamplerAddressMode) -> MTLSamplerState? {
            let d = MTLSamplerDescriptor()
            d.minFilter = .linear; d.magFilter = .linear
            d.sAddressMode = s; d.tAddressMode = t
            return dev.makeSamplerState(descriptor: d)
        }
        guard let s1 = sampler(.repeat, .repeat), let s2 = sampler(.clampToEdge, .repeat),
              let s3 = sampler(.clampToEdge, .clampToEdge) else { return nil }
        smpRepeat = s1; smpPulse = s2; smpClamp = s3

        // grain
        let nd = MTLTextureDescriptor()
        nd.pixelFormat = .r8Unorm; nd.width = 128; nd.height = 128; nd.usage = .shaderRead
        guard let nt = dev.makeTexture(descriptor: nd) else { return nil }
        var noise = [UInt8](repeating: 0, count: 128 * 128)
        for i in 0..<noise.count { noise[i] = UInt8.random(in: 0...255) }
        nt.replace(region: MTLRegionMake2D(0, 0, 128, 128), mipmapLevel: 0, withBytes: noise, bytesPerRow: 128)
        noiseTex = nt

        // the spectrogram the particles ride: 64 angle columns x 128 time rows
        let pd = MTLTextureDescriptor()
        pd.pixelFormat = .r8Unorm; pd.width = FieldModel.pulseW; pd.height = FieldModel.pulseRows; pd.usage = .shaderRead
        guard let pt = dev.makeTexture(descriptor: pd) else { return nil }
        let zero = [UInt8](repeating: 0, count: FieldModel.pulseW * FieldModel.pulseRows)
        pt.replace(region: MTLRegionMake2D(0, 0, FieldModel.pulseW, FieldModel.pulseRows), mipmapLevel: 0,
                   withBytes: zero, bytesPerRow: FieldModel.pulseW)
        pulseTex = pt

        guard let gt = FieldRenderer.glyphTexture(dev, "airplayaudio") else { return nil }
        glyphTex = gt
        glyphName = "airplayaudio"

        count = max(1000, model.cfg.particles)
        guard let buf = dev.makeBuffer(length: count * MemoryLayout<ParticleGPU>.stride, options: .storageModeShared) else { return nil }
        parts = buf
        super.init()
        seedParticles()
    }

    // MARK: - resources

    /// Spawn exactly as the app's kernel does, so the starting cloud looks the same.
    private func seedParticles() {
        func rand1(_ x: Double) -> Double { let s = sin(x) * 43758.5453; return s - floor(s) }
        let p = parts.contents().bindMemory(to: ParticleGPU.self, capacity: count)
        for i in 0..<count {
            let t = Double(i) * 0.125
            let x = Float(rand1(t) - 0.5), y = Float(rand1(t + 1) - 0.5), z = Float(rand1(t + 2) - 0.5)
            let life = Float(10 + 90 * rand1(Double(i) * 0.37 + 1.3))
            var q = ParticleGPU()
            q.pos = SIMD4<Float>(x, y, z, life)
            q.vel = SIMD4<Float>(0, 0, 0, 0)
            q.org = SIMD4<Float>(x, y, z, 0)
            q.aux = SIMD4<Float>(Float.random(in: 0...1), 0, x, y)
            p[i] = q
        }
    }
    func resetParticles() { seedParticles() }

    /// The source's SF Symbol, rendered once into a texture: the glyph the puck wears.
    private static func glyphTexture(_ dev: MTLDevice, _ symbol: String) -> MTLTexture? {
        let side = 192
        let cfg = UIImage.SymbolConfiguration(pointSize: CGFloat(side) * 0.52, weight: .medium)
        let img = UIImage(systemName: symbol, withConfiguration: cfg) ?? UIImage(systemName: "waveform")
        guard let glyph = img else { return nil }
        let bytes = UnsafeMutableRawPointer.allocate(byteCount: side * side * 4, alignment: 16)
        defer { bytes.deallocate() }
        bytes.initializeMemory(as: UInt8.self, repeating: 0, count: side * side * 4)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: bytes, width: side, height: side, bitsPerComponent: 8,
                                  bytesPerRow: side * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        UIGraphicsPushContext(ctx)
        ctx.translateBy(x: 0, y: CGFloat(side)); ctx.scaleBy(x: 1, y: -1)
        let w = glyph.size.width, h = glyph.size.height
        let rect = CGRect(x: (CGFloat(side) - w) / 2, y: (CGFloat(side) - h) / 2, width: w, height: h)
        glyph.withTintColor(.white, renderingMode: .alwaysOriginal).draw(in: rect)
        UIGraphicsPopContext()
        let d = MTLTextureDescriptor()
        d.pixelFormat = .rgba8Unorm; d.width = side; d.height = side; d.usage = .shaderRead
        guard let tex = dev.makeTexture(descriptor: d) else { return nil }
        tex.replace(region: MTLRegionMake2D(0, 0, side, side), mipmapLevel: 0, withBytes: bytes, bytesPerRow: side * 4)
        return tex
    }

    private func target(_ w: Int, _ h: Int) -> MTLTexture? {
        let d = MTLTextureDescriptor()
        d.pixelFormat = .bgra8Unorm; d.width = max(1, w); d.height = max(1, h)
        d.usage = [.renderTarget, .shaderRead]
        d.storageMode = .private
        return device.makeTexture(descriptor: d)
    }

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {
        sceneLow = nil; sceneFull = nil
    }

    // MARK: - the frame

    func draw(in view: MTKView) {
        guard let drawable = view.currentDrawable, let pass = view.currentRenderPassDescriptor else { return }
        let dsz = view.drawableSize
        guard dsz.width > 2, dsz.height > 2 else { return }

        let now = CACurrentMediaTime()
        let dt = last == 0 ? 1.0 / 60.0 : min(0.05, now - last)
        last = now
        let scale = Double(dsz.width) / max(Double(view.bounds.width), 1)
        model.contentsScale = scale
        model.layout(view.bounds.size)
        model.advance(dt)

        if sceneFull == nil || fullSize != dsz {
            fullSize = dsz
            let lw = max(1, Int(dsz.width * CGFloat(model.cfg.sceneScale)))
            let lh = max(1, Int(dsz.height * CGFloat(model.cfg.sceneScale)))
            lowSize = CGSize(width: lw, height: lh)
            sceneFull = target(Int(dsz.width), Int(dsz.height))
            sceneLow = target(lw, lh)
        }
        guard let full = sceneFull, let low = sceneLow else { return }

        for r in model.drainPulseRows() {
            r.data.withUnsafeBytes { raw in
                pulseTex.replace(region: MTLRegionMake2D(0, r.row, FieldModel.pulseW, 1), mipmapLevel: 0,
                                 withBytes: raw.baseAddress!, bytesPerRow: FieldModel.pulseW)
            }
        }
        let symbol = FieldPalette.symbol(model.source)
        if symbol != glyphName, let t = FieldRenderer.glyphTexture(device, symbol) {
            glyphTex = t; glyphName = symbol
        }

        guard let cmd = queue.makeCommandBuffer() else { return }

        // ---- 1. the particle simulation, at the app's fixed step rate ----
        let sdf = model.sdfParams()
        let h = 1.0 / model.cfg.simHz
        simAcc += dt
        var steps = 0
        if simAcc >= h, let ce = cmd.makeComputeCommandEncoder() {
            ce.setComputePipelineState(simPS)
            ce.setBuffer(parts, offset: 0, index: 0)
            var flux = model.flux.map { Float($0) }
            ce.setBytes(&flux, length: 4 * 24, index: 2)
            var n = UInt32(count)
            ce.setBytes(&n, length: 4, index: 3)
            let tg = min(simPS.maxTotalThreadsPerThreadgroup, 256)
            while simAcc >= h, steps < 2 {
                var s = SimUniforms()
                s.axis = sdf.axis; s.torus = sdf.torus; s.caps = sdf.caps
                s.cfg = SIMD4<Float>(Float(h), Float(model.time), Float(model.cfg.puffBeat * model.beat),
                                     Float(1 - min(max(model.cfg.reactiveFrac, 0.05), 1)))
                s.cfg2 = SIMD4<Float>(Float(model.cfg.puffGain), Float(model.cfg.puffVmax),
                                      Float(model.cfg.puffFloat), Float(model.cfg.puffReach))
                ce.setBytes(&s, length: MemoryLayout<SimUniforms>.stride, index: 1)
                ce.dispatchThreads(MTLSize(width: count, height: 1, depth: 1),
                                   threadsPerThreadgroup: MTLSize(width: tg, height: 1, depth: 1))
                simAcc -= h
                steps += 1
            }
            ce.endEncoding()
            if simAcc > h { simAcc = h * 0.99 }
        }
        let lerp = min(max(simAcc / h, 0), 1)

        // ---- 2. the scene, at a fraction of the resolution (it is all soft gradients) ----
        var blobs = [SIMD4<Float>](repeating: .zero, count: 24)
        for (i, b) in model.blobs.enumerated() where i < 24 {
            blobs[i] = SIMD4<Float>(Float(b.x), Float(b.y), Float(b.r), Float(b.a))
        }
        var halo = [SIMD4<Float>](repeating: .zero, count: 12)
        for k in 0..<6 { halo[k] = model.pulseE[k]; halo[6 + k] = model.pulseP[k] }

        let ss = Double(lowSize.width) / Double(dsz.width)
        var uLow = uniforms(scale: scale * ss, size: lowSize, lerp: lerp, sdf: sdf)
        var uFull = uniforms(scale: scale, size: dsz, lerp: lerp, sdf: sdf)

        let lowPass = MTLRenderPassDescriptor()
        lowPass.colorAttachments[0].texture = low
        lowPass.colorAttachments[0].loadAction = .dontCare
        lowPass.colorAttachments[0].storeAction = .store
        if let e = cmd.makeRenderCommandEncoder(descriptor: lowPass) {
            e.setRenderPipelineState(scenePS)
            e.setFragmentBytes(&uLow, length: MemoryLayout<FieldUniforms>.stride, index: 0)
            e.setFragmentBytes(&blobs, length: MemoryLayout<SIMD4<Float>>.stride * 24, index: 1)
            e.setFragmentBytes(&halo, length: MemoryLayout<SIMD4<Float>>.stride * 12, index: 2)
            e.setFragmentTexture(noiseTex, index: 0)
            e.setFragmentSamplerState(smpRepeat, index: 0)
            e.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            e.endEncoding()
        }

        // ---- 3. upscale, then the particles on top at full resolution ----
        let fullPass = MTLRenderPassDescriptor()
        fullPass.colorAttachments[0].texture = full
        fullPass.colorAttachments[0].loadAction = .dontCare
        fullPass.colorAttachments[0].storeAction = .store
        if let e = cmd.makeRenderCommandEncoder(descriptor: fullPass) {
            e.setRenderPipelineState(blitPS)
            e.setFragmentTexture(low, index: 0)
            e.setFragmentSamplerState(smpClamp, index: 0)
            e.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)

            e.setRenderPipelineState(pointPS)
            e.setVertexBuffer(parts, offset: 0, index: 0)
            e.setVertexBytes(&uFull, length: MemoryLayout<FieldUniforms>.stride, index: 1)
            e.setVertexTexture(pulseTex, index: 0)
            e.setVertexSamplerState(smpPulse, index: 0)
            e.setFragmentBytes(&uFull, length: MemoryLayout<FieldUniforms>.stride, index: 0)
            e.drawPrimitives(type: .point, vertexStart: 0, vertexCount: count)
            e.endEncoding()
        }

        // ---- 4. the glass orbs, the speaker rings and the volume rim, into the drawable ----
        var ov = overlayUniforms(scale: scale, size: dsz)
        var rings = [SIMD4<Float>](repeating: .zero, count: 8)
        for (i, r) in model.rings.enumerated() where i < 8 {
            let snapped: Float = model.snapped == r.serial && !r.serial.isEmpty ? 1 : 0
            rings[i] = SIMD4<Float>(Float(Double(r.pos.x) * scale), Float(Double(r.pos.y) * scale),
                                    r.active ? 1 : 0, snapped)
        }
        if let e = cmd.makeRenderCommandEncoder(descriptor: pass) {
            e.setRenderPipelineState(overlayPS)
            e.setFragmentBytes(&ov, length: MemoryLayout<OverlayUniforms>.stride, index: 0)
            e.setFragmentBytes(&rings, length: MemoryLayout<SIMD4<Float>>.stride * 8, index: 1)
            e.setFragmentTexture(full, index: 0)
            e.setFragmentTexture(glyphTex, index: 1)
            e.setFragmentSamplerState(smpClamp, index: 0)
            e.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            e.endEncoding()
        }
        cmd.present(drawable)
        cmd.commit()
    }

    // MARK: - uniforms

    private func uniforms(scale: Double, size: CGSize,
                          lerp: Double,
                          sdf: (axis: SIMD4<Float>, torus: SIMD4<Float>, caps: SIMD4<Float>)) -> FieldUniforms {
        var u = FieldUniforms()
        u.res = SIMD4<Float>(Float(size.width), Float(size.height),
                             Float(Double(model.centre.x) * scale), Float(Double(model.centre.y) * scale))
        u.geom = SIMD4<Float>(Float(model.roomR * scale), Float(model.fieldR * scale),
                              Float(model.puckR * scale), Float(model.time))
        u.puck = SIMD4<Float>(Float(Double(model.puck.x) * scale), Float(Double(model.puck.y) * scale), 0, 0)
        u.axis = sdf.axis; u.torus = sdf.torus; u.caps = sdf.caps
        u.env = SIMD4<Float>(Float(model.energy), Float(model.bass), Float(1 - model.omni), Float(model.cfg.halo))
        u.gains = SIMD4<Float>(Float(model.cfg.glow), Float(model.cfg.grain), Float(model.cfg.flurry),
                               Float(model.contentsScale))
        u.pts = SIMD4<Float>(Float(model.cfg.pointScale * max(0.7, model.roomR / 180)),
                             Float(model.cfg.pointGain), Float(lerp),
                             Float(1 - min(max(model.cfg.reactiveFrac, 0.05), 1)))
        u.pulse = SIMD4<Float>(Float(model.pulseHeadNorm), Float(model.cfg.pulseScale),
                               Float(model.cfg.pulseEmitter), Float(model.cfg.pulseGain))
        let p = model.palette
        u.cDeep = p.deep; u.cCyan = p.cyan; u.cLight = p.light; u.cWhite = p.white
        u.cBlue = p.blue; u.cTeal = p.teal; u.cRoom = p.room; u.cRoomEdge = p.roomEdge
        return u
    }

    private func overlayUniforms(scale: Double, size: CGSize) -> OverlayUniforms {
        var o = OverlayUniforms()
        func px(_ p: CGPoint) -> SIMD2<Float> {
            SIMD2<Float>(Float(Double(p.x) * scale), Float(Double(p.y) * scale))
        }
        o.res = SIMD4<Float>(Float(size.width), Float(size.height), px(model.centre).x, px(model.centre).y)
        o.geom = SIMD4<Float>(Float(model.roomR * scale), Float(model.puckR * scale),
                              Float(model.handleR * scale), Float(model.volume))
        let grab = model.grab
        var isDotA: Float = 0, isDotB: Float = 0
        if case .dot(let s)? = grab { if s > 0 { isDotA = 1 } else { isDotB = 1 } }
        let pk = px(model.puck)
        o.puck = SIMD4<Float>(pk.x, pk.y, 1, Float(model.omni))
        let a = px(model.handlePos(model.dir + model.half))
        let b = px(model.handlePos(model.dir - model.half))
        let n = px(model.handlePos(model.dir))
        o.hA = SIMD4<Float>(a.x, a.y, isDotA, 0)
        o.hB = SIMD4<Float>(b.x, b.y, isDotB, 0)
        o.nt = SIMD4<Float>(n.x, n.y, Float(model.dir), grab == .notch ? 1 : 0)
        let ns = model.notchSize
        o.ntSize = SIMD4<Float>(Float(ns.0 * scale), Float(ns.1 * scale),
                                grab == .lift ? 1 : 0, model.cfg.showVolume ? 1 : 0)
        let ld = px(model.liftDot ?? model.liftDotPos)
        o.lift = SIMD4<Float>(ld.x, ld.y, 1, 0)
        let l = model.listener.map(px) ?? SIMD2<Float>(0, 0)
        o.flags = SIMD4<Float>(Float(model.cfg.showRings ? min(model.rings.count, 8) : 0),
                               l.x, l.y, model.listener != nil ? 1 : 0)
        return o
    }
}
