import Foundation
import MetalKit
import MetalPerformanceShaders
import UIKit
import simd

// Mirrors of the shader structs. Only SIMD4<Float> members, so the Swift and Metal layouts match
// without any padding guesswork.
struct FieldUniforms {
    var res = SIMD4<Float>(), geom = SIMD4<Float>(), puck = SIMD4<Float>()
    var axis = SIMD4<Float>(), torus = SIMD4<Float>(), caps = SIMD4<Float>()
    var env = SIMD4<Float>(), gains = SIMD4<Float>(), pts = SIMD4<Float>(), pulse = SIMD4<Float>()
    var auraMap = SIMD4<Float>()
    var cRoom = SIMD4<Float>(), cRoomEdge = SIMD4<Float>(), cWhite = SIMD4<Float>(), cTint = SIMD4<Float>()
    var misc = SIMD4<Float>()
    var aeroMap = SIMD4<Float>(), aeroUV = SIMD4<Float>(), fill = SIMD4<Float>()
}

struct SimUniforms {
    var axis = SIMD4<Float>(), torus = SIMD4<Float>(), caps = SIMD4<Float>()
    var cfg = SIMD4<Float>(), cfg2 = SIMD4<Float>(), mode = SIMD4<Float>()
    var pulse = SIMD4<Float>()   // as FieldUniforms.pulse
    var extra = SIMD4<Float>()   // x: idle drift  y: puff mix  z: pulse u wraps (classic)  w: screen angle
    var extra2 = SIMD4<Float>()  // x: fill scale k  y: beat kick  z: energy  w: tube spread
}

struct OverlayUniforms {
    var res = SIMD4<Float>(), geom = SIMD4<Float>(), puck = SIMD4<Float>()
    var hA = SIMD4<Float>(), hB = SIMD4<Float>(), nt = SIMD4<Float>()
    var ntSize = SIMD4<Float>(), lift = SIMD4<Float>(), flags = SIMD4<Float>()
    var art = SIMD4<Float>()
}

struct ParticleGPU {
    var pos = SIMD4<Float>(), vel = SIMD4<Float>(), org = SIMD4<Float>(), aux = SIMD4<Float>()
}

struct FlurryBlobGPU {
    var geo = SIMD4<Float>(), col = SIMD4<Float>()
}

/// The sound field on the GPU, the way the Syng app does it (see FieldShaders.metal):
///   1. flurry   → the aura texture (256², the FlurryGenerator blobs + AuraColorAdjustment)
///   2. aerosol  → the particle kernel (once per frame, real dt), then into the never-cleared 512² half-float
///                 target: Gaussian blur σ 5 in place (MPS), a fade, the 262k additive points
///   3. scene    → the room disc (aura dimly everywhere, revealed where the aerosol is lit) at 0.62 resolution
///   4. blit     → full size
///   5. overlay  → glass orbs (puck with artwork or glyph), rings, volume rim, into the drawable
/// Called from MTKView's own draw callback, on the main thread, so it can read FieldModel directly.
final class FieldRenderer: NSObject, MTKViewDelegate {
    let model: FieldModel
    private let device: MTLDevice
    private let queue: MTLCommandQueue
    private let flurryPS: MTLRenderPipelineState
    private let scenePS: MTLRenderPipelineState
    private let blitPS: MTLRenderPipelineState
    private let fadePS: MTLRenderPipelineState
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
    private var artTex: MTLTexture?
    private var artVersion = -1
    private var artAspect: Float = 1
    private var parts: MTLBuffer
    private var count: Int
    private var auraTex: MTLTexture?
    private var aeroTex: MTLTexture?
    private var aeroScratch: MTLTexture?
    private var blur: MPSImageGaussianBlur?
    private var blurSigma: Float = 0
    private var sceneLow: MTLTexture?
    private var sceneFull: MTLTexture?
    private var lowSize = CGSize.zero
    private var fullSize = CGSize.zero
    private var last: CFTimeInterval = 0
    private var simAcc: Double = 0
    private var seededCount = 0
    private var seededPhysics: FieldModel.Physics = .app

    static let aeroSide = 512
    /// The Aerosol target's size in texels: 512² normally; filling the screen, the screen's shape at the normal
    /// circle's texel size (capped at 2048 on the long side), so the glitter is drawn finer, not magnified.
    private var aeroW = 512, aeroH = 512
    /// Normal-circle texels per target texel (1, or < 1 when the fill target hit its cap): sizes in texels scale by it.
    private var texelRatio: Double = 1
    static let auraSide = 256

    init?(model: FieldModel, pixelFormat: MTLPixelFormat = .bgra8Unorm) {
        guard let dev = MTLCreateSystemDefaultDevice(),
              let q = dev.makeCommandQueue(),
              let lib = dev.makeDefaultLibrary() else { return nil }
        self.model = model
        device = dev
        queue = q

        enum Blend { case none, additiveSrcAlpha, fade }
        func pipeline(_ vs: String, _ fs: String, blend: Blend, format: MTLPixelFormat) -> MTLRenderPipelineState? {
            let d = MTLRenderPipelineDescriptor()
            d.vertexFunction = lib.makeFunction(name: vs)
            d.fragmentFunction = lib.makeFunction(name: fs)
            let at = d.colorAttachments[0]!
            at.pixelFormat = format
            switch blend {
            case .none: break
            case .additiveSrcAlpha:
                // the app's BlendMode case 2: rgb = src.rgb * src.a + dst.rgb, alpha = src.a + dst.a
                at.isBlendingEnabled = true
                at.rgbBlendOperation = .add; at.alphaBlendOperation = .add
                at.sourceRGBBlendFactor = .sourceAlpha; at.destinationRGBBlendFactor = .one
                at.sourceAlphaBlendFactor = .one; at.destinationAlphaBlendFactor = .one
            case .fade:
                // a black rect at blurFade alpha over the previous frame: dst *= (1 - fade)
                at.isBlendingEnabled = true
                at.rgbBlendOperation = .add; at.alphaBlendOperation = .add
                at.sourceRGBBlendFactor = .sourceAlpha; at.destinationRGBBlendFactor = .oneMinusSourceAlpha
                at.sourceAlphaBlendFactor = .zero; at.destinationAlphaBlendFactor = .one
            }
            return try? dev.makeRenderPipelineState(descriptor: d)
        }
        guard let fl = pipeline("fullscreenVertex", "flurryFragment", blend: .none, format: .rgba16Float),
              let sp = pipeline("fullscreenVertex", "sceneFragment", blend: .none, format: .bgra8Unorm),
              let bp = pipeline("fullscreenVertex", "blitFragment", blend: .none, format: .bgra8Unorm),
              let fp = pipeline("fullscreenVertex", "fadeFragment", blend: .fade, format: .rgba16Float),
              let pp = pipeline("particleVertex", "particleFragment", blend: .additiveSrcAlpha, format: .rgba16Float),
              let op = pipeline("fullscreenVertex", "overlayFragment", blend: .none, format: pixelFormat),
              let sim = lib.makeFunction(name: "simulateParticles"),
              let simState = try? dev.makeComputePipelineState(function: sim) else { return nil }
        flurryPS = fl; scenePS = sp; blitPS = bp; fadePS = fp; pointPS = pp; overlayPS = op; simPS = simState

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

        // the pulse texture: 128 channel columns × 128 time rows (the app's PulseData is 128×128 float)
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

    /// Spawn exactly as the app's kernel does (sampleParticlePosition: rand3(id * 0.125) in [-0.5, 0.5]³,
    /// life rand(10..100)), so the starting cloud looks the same.
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
        seededCount = count
        seededPhysics = model.cfg.physics
    }
    func resetParticles() { seedParticles(); clearAerosol = true }
    private var clearAerosol = true

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

    /// The album cover, uploaded once per artwork change (the puck shows it, as the app's Orb does).
    private func artworkTexture(_ img: CGImage) -> MTLTexture? {
        let side = 256
        let bytes = UnsafeMutableRawPointer.allocate(byteCount: side * side * 4, alignment: 16)
        defer { bytes.deallocate() }
        bytes.initializeMemory(as: UInt8.self, repeating: 0, count: side * side * 4)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: bytes, width: side, height: side, bitsPerComponent: 8,
                                  bytesPerRow: side * 4, space: cs,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        // flip: CGContext draws bottom-up, the texture is sampled top-down
        ctx.translateBy(x: 0, y: CGFloat(side)); ctx.scaleBy(x: 1, y: -1)
        ctx.draw(img, in: CGRect(x: 0, y: 0, width: side, height: side))
        let d = MTLTextureDescriptor()
        d.pixelFormat = .rgba8Unorm; d.width = side; d.height = side; d.usage = .shaderRead
        guard let tex = device.makeTexture(descriptor: d) else { return nil }
        tex.replace(region: MTLRegionMake2D(0, 0, side, side), mipmapLevel: 0, withBytes: bytes, bytesPerRow: side * 4)
        return tex
    }

    private func target(_ w: Int, _ h: Int, format: MTLPixelFormat = .bgra8Unorm, readWrite: Bool = false) -> MTLTexture? {
        let d = MTLTextureDescriptor()
        d.pixelFormat = format; d.width = max(1, w); d.height = max(1, h)
        d.usage = readWrite ? [.renderTarget, .shaderRead, .shaderWrite] : [.renderTarget, .shaderRead]
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

        let wantCount = model.particleTarget
        if wantCount != seededCount || model.cfg.physics != seededPhysics {
            if wantCount != count, let buf = device.makeBuffer(length: wantCount * MemoryLayout<ParticleGPU>.stride, options: .storageModeShared) {
                parts = buf; count = wantCount
            }
            seedParticles(); clearAerosol = true
        }

        if sceneLow == nil || fullSize != dsz {
            fullSize = dsz
            let lw = max(1, Int(dsz.width * CGFloat(model.cfg.sceneScale)))
            let lh = max(1, Int(dsz.height * CGFloat(model.cfg.sceneScale)))
            lowSize = CGSize(width: lw, height: lh)
            sceneFull = nil                                   // no full-resolution copy any more (the overlay reads sceneLow)
            sceneLow = target(lw, lh)
        }
        if auraTex == nil { auraTex = target(Self.auraSide, Self.auraSide, format: .rgba16Float) }
        var wantW = Self.aeroSide, wantH = Self.aeroSide, ratio = 1.0
        if model.cfg.fillScreen {
            // one texel the size it is in the normal circle (2·uiR points over 512), across the whole view
            let t0 = 2 * model.uiR / Double(Self.aeroSide)
            var w = Double(view.bounds.width) / t0, h = Double(view.bounds.height) / t0
            let longSide = max(w, h)
            if longSide > 2048 { ratio = 2048 / longSide; w *= ratio; h *= ratio }
            wantW = max(64, Int(w.rounded())); wantH = max(64, Int(h.rounded()))
        }
        texelRatio = ratio
        if aeroTex == nil || aeroW != wantW || aeroH != wantH {
            aeroW = wantW; aeroH = wantH
            aeroTex = target(wantW, wantH, format: .rgba16Float, readWrite: true)
            aeroScratch = target(wantW, wantH, format: .rgba16Float, readWrite: true)
            blur = nil
            clearAerosol = true
        }
        guard let low = sceneLow, let aura = auraTex, let aero = aeroTex else { return }

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
        if model.artworkVersion != artVersion {
            artVersion = model.artworkVersion
            if let img = model.artworkImage, let t = artworkTexture(img) {
                artTex = t; artAspect = Float(img.width) / Float(max(1, img.height))
            } else { artTex = nil }
        }

        guard let cmd = queue.makeCommandBuffer() else { return }
        let app = model.cfg.physics == .app
        let sdf = app ? model.appParams() : model.sdfParams()

        // ---- 1. the particle simulation: once per frame with the real dt (the app), or the legacy 30 Hz steps ----
        var lerp: Double = 0
        if let ce = cmd.makeComputeCommandEncoder() {
            ce.setComputePipelineState(simPS)
            ce.setBuffer(parts, offset: 0, index: 0)
            var flux = model.flux.map { Float($0) }
            ce.setBytes(&flux, length: 4 * 24, index: 2)
            var n = UInt32(count)
            ce.setBytes(&n, length: 4, index: 3)
            ce.setTexture(pulseTex, index: 0)              // the puff reads the same pulse rows as the vertex shader
            ce.setSamplerState(smpPulse, index: 0)
            ce.setSamplerState(smpRepeat, index: 1)
            let tg = min(simPS.maxTotalThreadsPerThreadgroup, 256)
            func dispatch(_ s: inout SimUniforms) {
                ce.setBytes(&s, length: MemoryLayout<SimUniforms>.stride, index: 1)
                ce.dispatchThreads(MTLSize(width: count, height: 1, depth: 1),
                                   threadsPerThreadgroup: MTLSize(width: tg, height: 1, depth: 1))
            }
            if app {
                var s = SimUniforms()
                s.axis = sdf.axis; s.torus = sdf.torus; s.caps = sdf.caps
                s.cfg = SIMD4<Float>(Float(dt), Float(model.time), 0, 0)
                s.mode = SIMD4<Float>(1, Float(dt), 0, 0)
                s.pulse = SIMD4<Float>(Float(model.pulseHeadNorm), Float(model.pulseAngleTransform),
                                       Float(model.cfg.pulseEmitter), Float(model.cfg.pulseGain))
                s.extra = SIMD4<Float>(Float(model.cfg.idleDrift), Float(model.cfg.puffMix),
                                       Float(model.pulseWrap), sdf.axis.x)
                // the beat kick: ~0.004 field units a frame at a full beat (a few px outward, eased back ×0.92)
                s.extra2 = SIMD4<Float>(Float(model.fillScale), Float(0.004 * model.cfg.beatPuff * min(max(model.beat, 0), 1)),
                                        Float(min(max(model.energy, 0), 1)), Float(model.cfg.tubeSpread))
                dispatch(&s)
            } else {
                let h = 1.0 / model.cfg.simHz
                simAcc += dt
                var steps = 0
                while simAcc >= h, steps < 2 {
                    var s = SimUniforms()
                    s.axis = sdf.axis; s.torus = sdf.torus; s.caps = sdf.caps
                    s.cfg = SIMD4<Float>(Float(h), Float(model.time), Float(model.cfg.puffBeat * model.beat),
                                         Float(1 - min(max(model.cfg.reactiveFrac, 0.05), 1)))
                    s.cfg2 = SIMD4<Float>(Float(model.cfg.puffGain), Float(model.cfg.puffVmax),
                                          Float(model.cfg.puffFloat), Float(model.cfg.puffReach))
                    s.mode = SIMD4<Float>(0, Float(h), 0, 0)
                    dispatch(&s)
                    simAcc -= h
                    steps += 1
                }
                if simAcc > h { simAcc = h * 0.99 }
                lerp = min(max(simAcc / h, 0), 1)
            }
            ce.endEncoding()
        }

        // ---- 2. the aura: the flurry blobs into their own texture ----
        var blobs = [FlurryBlobGPU](repeating: FlurryBlobGPU(), count: 64)
        let fb = model.flurryBlobs()
        for (i, b) in fb.enumerated() where i < 64 { blobs[i] = FlurryBlobGPU(geo: b.0, col: b.1) }
        var fcfg = SIMD4<Float>(Float(min(fb.count, 64)), Float(model.flurrySettings.normalization),
                                Float(model.flurrySettings.saturation), model.flurryMax)
        let auraPass = MTLRenderPassDescriptor()
        auraPass.colorAttachments[0].texture = aura
        auraPass.colorAttachments[0].loadAction = .dontCare
        auraPass.colorAttachments[0].storeAction = .store
        if let e = cmd.makeRenderCommandEncoder(descriptor: auraPass) {
            e.setRenderPipelineState(flurryPS)
            e.setFragmentBytes(&blobs, length: MemoryLayout<FlurryBlobGPU>.stride * 64, index: 0)
            e.setFragmentBytes(&fcfg, length: MemoryLayout<SIMD4<Float>>.stride, index: 1)
            e.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            e.endEncoding()
        }

        // ---- 3. the Aerosol: blur what is there, fade it, add this frame's points ----
        let ss = Double(lowSize.width) / Double(dsz.width)
        var uLow = uniforms(scale: scale * ss, size: lowSize, lerp: lerp, sdf: sdf)
        var uFull = uniforms(scale: scale, size: dsz, lerp: lerp, sdf: sdf)
        let drawPoints = model.omni < 0.995 || !app || model.cfg.particlesInClassic   // we keep the Aerosol in classic
        if clearAerosol {
            let cp = MTLRenderPassDescriptor()
            cp.colorAttachments[0].texture = aero
            cp.colorAttachments[0].loadAction = .clear
            cp.colorAttachments[0].clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 1)
            cp.colorAttachments[0].storeAction = .store
            if let e = cmd.makeRenderCommandEncoder(descriptor: cp) { e.endEncoding() }
            clearAerosol = false
        }
        if drawPoints {
            let sigma = Float(model.cfg.blurSigma * texelRatio)   // σ in texels; texels are the normal circle's size
            if sigma > 0.01 {
                if blur == nil || abs(blurSigma - sigma) > 0.01 { blur = MPSImageGaussianBlur(device: device, sigma: sigma); blurSigma = sigma }
                if let b = blur, let scratch = aeroScratch {
                    // blur into the spare target and swap the two (ping-pong). The Gaussian can't run in place, so the
                    // old in-place call fell back to allocating a brand-new texture every frame.
                    b.encode(commandBuffer: cmd, sourceTexture: aero, destinationTexture: scratch)
                    aeroScratch = aero
                    aeroTex = scratch
                }
            }
            let aeroNow = aeroTex ?? aero
            let ap = MTLRenderPassDescriptor()
            ap.colorAttachments[0].texture = aeroNow
            ap.colorAttachments[0].loadAction = .load
            ap.colorAttachments[0].storeAction = .store
            if let e = cmd.makeRenderCommandEncoder(descriptor: ap) {
                var fade = SIMD4<Float>(Float(min(max(model.cfg.blurFade, 0), 1)), 0, 0, 0)
                e.setRenderPipelineState(fadePS)
                e.setFragmentBytes(&fade, length: MemoryLayout<SIMD4<Float>>.stride, index: 0)
                e.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)

                e.setRenderPipelineState(pointPS)
                e.setVertexBuffer(parts, offset: 0, index: 0)
                e.setVertexBytes(&uFull, length: MemoryLayout<FieldUniforms>.stride, index: 1)
                e.setVertexTexture(pulseTex, index: 0)
                e.setVertexSamplerState(smpPulse, index: 0)     // the app's: u clamp-to-edge, v repeat, linear
                e.setVertexSamplerState(smpRepeat, index: 1)    // classic: u wraps too (360° channel map)
                e.drawPrimitives(type: .point, vertexStart: 0, vertexCount: count)
                e.endEncoding()
            }
        }
        let aeroNow = aeroTex ?? aero

        // ---- 4. the scene, at a fraction of the resolution (it is all soft gradients) ----
        var halo = [SIMD4<Float>](repeating: .zero, count: 12)
        for k in 0..<6 { halo[k] = model.pulseE[k]; halo[6 + k] = model.pulseP[k] }
        let lowPass = MTLRenderPassDescriptor()
        lowPass.colorAttachments[0].texture = low
        lowPass.colorAttachments[0].loadAction = .dontCare
        lowPass.colorAttachments[0].storeAction = .store
        if let e = cmd.makeRenderCommandEncoder(descriptor: lowPass) {
            e.setRenderPipelineState(scenePS)
            e.setFragmentBytes(&uLow, length: MemoryLayout<FieldUniforms>.stride, index: 0)
            e.setFragmentBytes(&halo, length: MemoryLayout<SIMD4<Float>>.stride * 12, index: 1)
            e.setFragmentTexture(aura, index: 0)
            e.setFragmentTexture(aeroNow, index: 1)
            e.setFragmentTexture(noiseTex, index: 2)
            e.setFragmentSamplerState(smpClamp, index: 0)
            e.setFragmentSamplerState(smpRepeat, index: 1)
            e.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            e.endEncoding()
        }

        // ---- 5. (no separate upscale pass: the overlay samples the reduced scene with linear filtering, which is
        // exactly what the old full-resolution copy held — one full-screen write and read saved per frame) ----

        // ---- 6. the glass orbs, the speaker rings and the volume rim, into the drawable ----
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
            e.setFragmentTexture(low, index: 0)
            e.setFragmentTexture(glyphTex, index: 1)
            e.setFragmentTexture(artTex ?? glyphTex, index: 2)
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
        let app = model.cfg.physics == .app
        u.res = SIMD4<Float>(Float(size.width), Float(size.height),
                             Float(Double(model.centre.x) * scale), Float(Double(model.centre.y) * scale))
        u.geom = SIMD4<Float>(Float(model.roomR * scale), Float(model.fieldR * scale),
                              Float(model.puckR * scale), Float(model.time))
        // the app fades the Aerosol out with expansionProgress in classic; with particlesInClassic it stays, and the
        // aura keeps its spatial-mode dim (there are still particles to reveal it)
        let keep = app && model.cfg.particlesInClassic
        let expansion = Float(app && !keep ? min(max(model.expansion, 0), 1) : 1)
        u.puck = SIMD4<Float>(Float(Double(model.puck.x) * scale), Float(Double(model.puck.y) * scale), expansion, Float(keep ? 0 : model.omni))
        u.axis = sdf.axis; u.torus = sdf.torus; u.caps = sdf.caps
        u.env = SIMD4<Float>(Float(model.energy), Float(model.bass), Float(1 - model.omni), Float(model.cfg.halo))
        // classic keeps the glitter over a full-brightness aura: give it more gain there (blends with expansion)
        let classicBoost = keep ? 1 + (model.cfg.revealClassic - 1) * model.pulseWrap : 1
        u.gains = SIMD4<Float>(Float(model.cfg.auraGain * model.cfg.glow), Float(model.cfg.grain),
                               Float(model.cfg.revealGain * model.cfg.pointGain * classicBoost),
                               Float(model.contentsScale))
        // the app's points are sized in pixels of its 512 target; the fill target's texels are the same size (× texelRatio if capped)
        u.pts = SIMD4<Float>(Float(model.cfg.pointScale * texelRatio), 1.0, app ? 0 : Float(lerp),
                             Float(1 - min(max(model.cfg.reactiveFrac, 0.05), 1)))
        // x: pulseTextureOffset (newest row / 32)  y: angleTransform.x  z: emitterRadius  w: gain (1 = the app)
        u.pulse = SIMD4<Float>(Float(model.pulseHeadNorm), Float(model.pulseAngleTransform),
                               Float(model.cfg.pulseEmitter), Float(model.cfg.pulseGain))
        // the aura's unit square covers the room's bounding square
        let R = model.roomR * scale
        u.auraMap = SIMD4<Float>(Float(Double(model.centre.x) * scale - R), Float(Double(model.centre.y) * scale - R), Float(2 * R),
                                 Float(model.pulseWrap))   // w: 1 = the pulse texture wraps around the ring (classic)
        let p = model.palette
        u.cRoom = p.room; u.cRoomEdge = p.roomEdge; u.cWhite = p.white; u.cTint = p.cyan
        // where the Aerosol lives: field units -> its clip space, and its image in this pass's pixels
        let rf = model.fieldR, P = model.puck
        if model.cfg.fillScreen {
            let sw = max(Double(model.size.width), 1), sh = max(Double(model.size.height), 1)
            // the vertex shader maps the field's circle onto the screen rectangle around the puck (see discToSquare)
            u.aeroMap = SIMD4<Float>(Float(min(max(Double(P.x) / sw, 0.02), 0.98)), Float(min(max(Double(P.y) / sh, 0.02), 0.98)), 0, 0)
            u.aeroUV = SIMD4<Float>(0, 0, Float(size.width), Float(size.height))
        } else {
            u.aeroMap = SIMD4<Float>(2, 2, 0, 0)
            u.aeroUV = SIMD4<Float>(Float((Double(P.x) - rf) * scale), Float((Double(P.y) - rf) * scale), Float(2 * rf * scale), Float(2 * rf * scale))
        }
        u.fill = SIMD4<Float>(Float(model.fillScale), model.cfg.fillScreen ? 1 : 0, 0, 0)
        u.misc = SIMD4<Float>(Float(model.cfg.auraDim), Float(model.cfg.auraDimClassic), Float(model.cfg.pulseScale), app ? 0 : 1)
        return u
    }

    private func overlayUniforms(scale: Double, size: CGSize) -> OverlayUniforms {
        var o = OverlayUniforms()
        func px(_ p: CGPoint) -> SIMD2<Float> {
            SIMD2<Float>(Float(Double(p.x) * scale), Float(Double(p.y) * scale))
        }
        o.res = SIMD4<Float>(Float(size.width), Float(size.height), px(model.centre).x, px(model.centre).y)
        o.geom = SIMD4<Float>(Float(model.uiR * scale), Float(model.puckR * scale),    // x: the controls' scale, not the disc
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
                                grab == .lift ? 1 : 0, model.cfg.showVolume && !model.cfg.fillScreen ? 1 : 0)
        let ld = px(model.liftDot ?? model.liftDotPos)
        o.lift = SIMD4<Float>(ld.x, ld.y, 1, 0)
        let l = model.listener.map(px) ?? SIMD2<Float>(0, 0)
        o.flags = SIMD4<Float>(Float(model.cfg.showRings ? min(model.rings.count, 8) : 0),
                               l.x, l.y, model.listener != nil ? 1 : 0)
        let showArt = model.cfg.puckArtwork && artTex != nil
        o.art = SIMD4<Float>(showArt ? 1 : 0, artAspect, 0, 0)
        return o
    }
}
