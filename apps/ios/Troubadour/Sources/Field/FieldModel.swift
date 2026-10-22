import Foundation
import SwiftUI
import simd
import CoreGraphics
#if canImport(UIKit)
import UIKit
#endif
#if canImport(WidgetKit) && !os(tvOS)
import WidgetKit
#endif

/// Everything the sound field knows, on the CPU: layout, the springs the app uses, the live audio
/// feed, the aura (flurry) and pulse animation state, the colour source, and the two endpoints it
/// drives (/api/soundfield, /api/volume). The renderer reads this straight out every frame — no
/// SwiftUI redraws involved. Everything here runs on the main thread (gestures, and the Metal
/// view's own draw callback).
///
/// The geometry and the audio mapping follow the Syng app (SyngSpace.UpmixTrack / ProtoTools.Aerosol /
/// AudioAnalysisTools.PulseData, read out of the decrypted 2.1.0 binary — docs/SOUND-FIELD.md):
///   * the field (outer) radius is the room radius minus the puck's offset from the centre
///   * the two focus dots and the notch sit at (puckRadius + fieldRadius) / 2 from the puck
///   * the arc half-angle is NOT the dot angle: spreadPercentage = p(spread/π) with
///     p(x) = x·(24x² − 10x + 11)/10, so 45° dots → 45°, 90° → 108°, 120° → 180° (a full ring)
///   * the capped torus' weight = 1 − (puckRadius + 2pt) / fieldRadius
///   * the glitter is pushed by the per-channel (L / C / R …) loudness around the arc, not by frequency
final class FieldModel: ObservableObject {

    enum ColourMode: String, CaseIterable { case source, artwork, led }
    enum Physics: String, CaseIterable { case app, legacy }

    // MARK: config
    struct Cfg {
        var particles = 262_144         // the app's Aerosol: 0x40000 particles into a 512² target
        var physics: Physics = .app     // .legacy = the previous renderer's puff physics, for comparison
        var sceneScale: Double = 0.62   // the soft background renders at this fraction of the drawable
        var reactiveFrac: Double = 0.5  // legacy
        var simHz: Double = 30          // legacy (the app's kernel runs once per frame)
        var weightSpatial: Double = 0.88, weightOmni: Double = 2.0   // legacy (the app derives weight from the radii)
        var handleRadius: Double = 0.60 // legacy (the app: (puckR + fieldR) / 2)
        var defaultHalfDeg: Double = 45 // the app's default spread (π/4)
        var minHalfDeg: Double = 25, maxHalfDeg: Double = 120        // the app's limits (0.436 / 2.094 rad)
        var snapToCell = true, snapFrac: Double = 0.13
        var snapShrinks = true          // the app shrinks the field to ≤ 60 pt while the puck is snapped to a speaker
        var liftFrac: Double = 0.26, collapseFrac: Double = 0.2
        var pulseScale: Double = 2.0, pulseGain: Double = 1.0, pulseEmitter: Double = 0.3   // emitterRadius 0.6 × 0.5
        var pulseJitter: Double = 0.0
        // ---- Troubadour's additions to the app's Aerosol (each 0 = exactly the app) ----
        /// The app's glitter sits still once the kernel has settled (its only idle term, `org·sin(life + t/100)`,
        /// is dead code: `life` is a half and never reaches 0). This is a slow, coherent flow along the torus so the
        /// field drifts gently with nothing playing. 1 ≈ 12 px/s on a phone.
        var idleDrift: Double = 1.0
        /// The app moves particles only in the vertex shader (±2–3 px). This also feeds the pulse into the kernel as a
        /// radial kick, so a beat physically puffs the band outward and the torus spring brings it back — the feel of
        /// the previous "puff" physics on top of the app's rings.
        var puffMix: Double = 1.0
        /// Every particle (not only where a channel's pulse passes) gets a small radial kick on each beat, scaled by a
        /// per-particle random weight, so the whole cloud breathes with the music. 0 = off.
        var beatPuff: Double = 1.0
        /// How far into the torus' tube the particles may rest (0 = all on its skin, the app's look). A hollow shell
        /// seen edge-on bunches up along the band's rim and around the puck; 1 spreads a tail of particles inward so
        /// the band reads evenly across its width. Music reactions don't depend on it.
        var tubeSpread: Double = 1.0
        /// Fill the screen draws at most this many particles (the count scales with the field's area so the glitter
        /// keeps its density instead of being magnified).
        var fillParticleCap = 1_000_000
        /// Classic (the 360° ring map): the stems' Gaussians are sized for the app's ±120° arc and leave dead sectors
        /// at ±60° and 180° on a full ring; widen them so every particle has a stem to follow.
        var classicSpreadScale: Double = 1.5
        /// Classic draws the aura at full brightness (the app shows the aura alone there); the kept glitter needs more
        /// gain than in spatial mode to read against it.
        var revealClassic: Double = 1.6
        var puffGain: Double = 0.005, puffBeat: Double = 0.005      // legacy
        var puffFloat: Double = 0.9, puffVmax: Double = 0.008, puffReach: Double = 0.2
        var blurSigma: Double = 5.0, blurFade: Double = 0.7         // Aerosol: Gaussian σ (px of 512) + the black-rect fade alpha
        var glow: Double = 1.0, grain: Double = 0.5, flurry: Double = 1.0
        /// The six music bands as soft blobs breathing around the puck (Aurora PulseField) — what the web field draws.
        /// Not in the Syng app; on by default because it is ours and people like it. 0 = off.
        var halo: Double = 1.0
        var auraGain: Double = 1.0, revealGain: Double = 1.0, auraDim: Double = 0.42, auraDimClassic: Double = 1.0
        var pointScale: Double = 1.0, pointGain: Double = 1.0
        var radiusUnits: Double = 0.7
        var showVolume = true, showRings = true
        /// The field fills the whole screen (the room disc circumscribes it) instead of sitting in its circle; the
        /// puck, dots and rings keep their normal size. The Aerosol target doubles to 1024² so the glitter stays fine.
        var fillScreen = false
        var colourMode: ColourMode = .artwork   // the app's own flow: the cover's colours, else the source's Auras; LEDs is the toggle
        var puckArtwork = true          // the album cover in the puck when the source has one (the app does)
        /// Troubadour's one deliberate difference from the Syng app: the app removes the Aerosol in "sound all around"
        /// (classic) mode; we keep it, as a full ring whose pulse reacts around the whole circle (360° channel map).
        var particlesInClassic = true
        /// legacy alias: `followLed` == (colourMode == .led)
        var followLed: Bool {
            get { colourMode == .led }
            set { colourMode = newValue ? .led : .source }
        }
    }
    var cfg = Cfg()
    var visualOnly = false
    var readOnly = false

    // MARK: layout, in points; the view sets this on every layout pass
    var size = CGSize(width: 360, height: 338)
    var centre = CGPoint(x: 180, y: 155)
    var roomR: Double = 138          // the room disc (the whole screen's circumscribed circle in fillScreen)
    var uiR: Double = 138            // what the controls are sized from: the room's normal radius in this view
    var contentsScale: Double = 3
    private var lastFill = false

    func layout(_ s: CGSize) {
        guard s.width > 1, s.height > 1 else { return }
        let box = min(s.width, s.height / 0.94)
        let R0 = Double(box) * 0.385
        let fill = cfg.fillScreen
        let R = fill ? hypot(Double(s.width), Double(s.height)) / 2 * 1.02 : R0
        let newC = fill ? CGPoint(x: s.width / 2, y: s.height / 2)
                        : CGPoint(x: s.width / 2, y: (s.height - box * 0.94) / 2 + CGFloat(0.045 * Double(box) + R0))
        if roomR > 0, abs(R - roomR) > 0.5 || abs(newC.x - centre.x) > 0.5 || abs(newC.y - centre.y) > 0.5 || fill != lastFill {
            let u = units(of: puckTarget)
            size = s; centre = newC; roomR = R; uiR = R0; lastFill = fill
            puckTarget = point(ofUnits: u); puck = puckTarget; puckVel = .zero
        } else {
            size = s; centre = newC; roomR = R; uiR = R0
        }
        if !placed { puck = centre; puckTarget = centre; placed = true }
    }
    private var placed = false
    func setHalo(_ on: Bool) { objectWillChange.send(); cfg.halo = on ? 1 : 0 }
    /// How much bigger the field is than it would be in its normal circle: 1 normally, ~2.5–3.5 filling the screen.
    /// The renderer keeps everything that should stay a fixed size on screen (particle size, glow, ring spacing,
    /// displacement, drift) constant by dividing by this, instead of magnifying it.
    /// Filling the screen the field's circle is mapped onto the screen rectangle, so its size is the screen's
    /// (geometric mean of the half-extents) against the normal circle's radius.
    var fillScale: Double {
        guard cfg.fillScreen else { return 1 }
        return max(1, (Double(size.width) * Double(size.height)).squareRoot() / 2 / max(uiR, 1))
    }
    /// Particles to draw: the configured count, scaled by the screen's area against the normal circle's when it fills
    /// the screen (every particle lands on screen now, none is wasted past an edge).
    var particleTarget: Int {
        let base = max(1000, cfg.particles)
        guard cfg.fillScreen else { return base }
        let ratio = Double(size.width) * Double(size.height) / (Double.pi * uiR * uiR)
        return min(max(base, Int(Double(base) * ratio)), cfg.fillParticleCap)
    }
    func setFillScreen(_ on: Bool) {
        objectWillChange.send()
        cfg.fillScreen = on
        layout(size)
    }

    // MARK: field state (the app's own)
    var puck = CGPoint.zero, puckTarget = CGPoint.zero, puckVel = CGPoint.zero
    var puckScale: Double = 1, puckScaleVel: Double = 0
    var spatial = false
    var dir: Double = 0, dirTarget: Double = 0, dirVel: Double = 0
    var half: Double = 0, halfTarget: Double = 0, halfVel: Double = 0
    var weight: Double = 2.0, weightVel: Double = 0        // legacy
    var expansion: Double = 0, expansionVel: Double = 0    // the app's expansionProgress: the Aerosol fades with it
    var snapAmount: Double = 0, snapVel: Double = 0        // the app's snapToCellAmount
    var volume: Double = 0.25, volTarget: Double = 0.25
    var snapped: String?
    var time: Double = 0

    // MARK: audio
    var energy: Double = 0.25, bass: Double = 0, beat: Double = 0
    var spec = [Double](repeating: 0, count: 24)
    var rawSpec = [Double](repeating: 0, count: 24)
    var emaSpec = [Double](repeating: 0, count: 24)
    var flux = [Double](repeating: 0, count: 24)
    var stemDb: [Double] = []          // per-stem RMS, dB (the agent's `rms`, b68)
    var stemNames: [String] = []
    var seq = -1
    var feedFPS: Double = 30
    var lastRow = Date.distantPast
    @Published var live = false
    @Published var source: String = "airplay"
    @Published var hasArtwork = false
    @Published var artworkLoaded = false

    // MARK: labels
    @Published var volPct = 25
    @Published var widthDeg = 45
    @Published var aimDeg = 0
    @Published var spatialOn = false
    @Published var sourceTitle = "Sound field"

    // MARK: the pulse texture — SyngSpace.AudioDataHistory, the production path (not the PulseData prototype):
    // 512 columns × 32 rows, 8-bit. A row is written at 30 Hz (display-driven); each row is the sum of one Gaussian
    // per layout emitter, exp(−((u − loc)/spread)²/0.05), with brightness = that channel's last detected PEAK of
    // ((dB+100)/100)³, decaying ×0.9 per row. Rows are written at decreasing indices; (nextRow+1)/32 is the newest.
    static let pulseW = 512, pulseRows = 32
    var pulseHead = 31                     // AudioDataHistory.nextRow
    private var pulseQueue: [[UInt8]] = []
    private var pulseTickAcc: Double = 0
    /// per-channel PeakDetector (p0, fresh0, p1, fresh1) and the emitter brightness it feeds (SurroundVisualizer)
    private var peakDet: [String: (p0: Float, f0: Bool, p1: Float, f1: Bool)] = [:]
    private var emitBright: [String: Float] = [:]
    struct LayoutEmitter { let name: String; let loc: Float; let spread: Float }   // degrees (−180…180), degrees
    private(set) var layoutEmitters: [LayoutEmitter] = FieldModel.layout(for: ["L", "R", "C"])
    private var layoutKey = ""

    // MARK: legacy flurry blobs + the pulse field, as the web animates them (the widget's still renderer uses these)
    struct Blob { var x = 0.0, y = 0.0, r = 0.4, a = 0.0, vx = 0.0, vy = 0.0, life = 0.0, ph = 0.0 }
    var blobs: [Blob] = []
    struct Phase { var ang = 0.0, w = 0.0, p1 = 0.0, p2 = 0.0, w1 = 0.0, w2 = 0.0, off = 0.0 }
    var phases: [Phase] = []
    var bands = [Double](repeating: 0, count: 6)
    var pulseE = [SIMD4<Float>](repeating: .zero, count: 6)
    var pulseP = [SIMD4<Float>](repeating: .zero, count: 6)

    // MARK: the app's flurry (AuraGenerator.FlurryGenerator): soft additive colour discs drifting in the unit square
    struct FlurryParticle {
        var start = 0.0, end = 1.0
        var color = SIMD3<Float>(0, 0, 0)
        var pos = SIMD2<Double>(0.5, 0.5), vel = SIMD2<Double>(0, 0)
        var radius = 0.4, stroke = 0.0, feather = 0.95
    }
    struct FlurrySettings {
        var particleCount = 52, birthRate = 0.12, lifetime = 10.0, lifetimeVariance = 0.5, velocity = 0.5
        var minRadius = 0.3557843863964081, maxRadius = 0.4496040642261505
        var minFeather = 0.8854160904884338, maxFeather = 1.0, minStroke = 0.0, maxStroke = 1.0
        var normalization = 0.3, saturation = 2.0
    }
    var flurrySettings = FlurrySettings()
    /// FlurrySettings per source, straight from the app's Auras_<source>.json (the first port used AirPlay's for all).
    static let flurryTable: [String: FlurrySettings] = [
        "airplay": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.3557843863964081, maxRadius: 0.4496040642261505, minFeather: 0.8854160904884338, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.30000001192092896, saturation: 2.0),
        "hdmi": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.35503366589546204, maxRadius: 0.4394303858280182, minFeather: 0.9132594466209412, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.30000001192092896, saturation: 2.0),
        "puredata": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.19618670642375946, maxRadius: 0.4394303858280182, minFeather: 0.8145926594734192, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.3794642984867096, saturation: 2.0),
        "spotify": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.3397024869918823, maxRadius: 0.4961128532886505, minFeather: 0.8559892773628235, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.24314837157726288, saturation: 1.7512590885162354),
        "systemaudio": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.3457929193973541, maxRadius: 0.4394303858280182, minFeather: 0.9271798133850098, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.30000001192092896, saturation: 0.8635340929031372),
        "tidal": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.19618670642375946, maxRadius: 0.4394303858280182, minFeather: 0.8145926594734192, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.3794642984867096, saturation: 2.0),
        "usb": FlurrySettings(particleCount: 52, birthRate: 0.11999999731779099, lifetime: 10.0, lifetimeVariance: 0.5, velocity: 0.5, minRadius: 0.3457929193973541, maxRadius: 0.4394303858280182, minFeather: 0.9271798133850098, maxFeather: 1.0, minStroke: 0.0, maxStroke: 1.0, normalization: 0.30000001192092896, saturation: 0.8635340929031372),
    ]
    var flurry: [FlurryParticle] = []
    var flurryMax: Float = 1          // the brightest spot of the summed blobs (AuraColorAdjustment's minMax)

    // MARK: speakers
    struct Ring: Identifiable { var pos: CGPoint; var active: Bool; var serial: String; var id: String { serial } }
    var rings: [Ring] = []
    var listener: CGPoint?
    private var reference = (x: 0.0, y: 0.0, r: 1.15, rot: 0.0)
    private var cellsM: [(x: Double, y: Double, active: Bool, serial: String)] = []

    // MARK: colour — where the aura's colours come from
    var palette = FieldPalette.airplay
    private var sourcePalette = FieldPalette.airplay
    private var ledPalette: FieldPalette?
    private var paletteTarget: FieldPalette {
        switch cfg.colourMode {
        case .led: return ledPalette ?? sourcePalette
        case .artwork: return artworkPalette ?? sourcePalette
        case .source: return sourcePalette
        }
    }
    /// The colour generator in use (a 16³ RGB histogram, as weighted colour bins): the source's own
    /// (`Auras_<source>.json`), the album artwork's (ArtworkProcessor), or one synthesised around the LED colour.
    var colourBins: [ColourBin] = ColourGenerators.bins(for: "airplay")
    private var sourceBins: [ColourBin] = ColourGenerators.bins(for: "airplay")
    private var artworkBins: [ColourBin]?
    private var artworkPalette: FieldPalette?
    private var ledBins: [ColourBin]?
    private var ledColour: (Double, Double, Double, Double)?
    private var artKey = ""
    var artworkImage: CGImage?          // for the puck
    var artworkVersion = 0              // bumps when artworkImage changes (the renderer re-uploads)
    private var artTask: Task<Void, Never>?

    // MARK: input
    enum Grab: Equatable { case puckOrb, lift, notch, dot(Int), volume }
    var grab: Grab?
    var liftDot: CGPoint?
    private var grabOffset = CGSize.zero

    let agent: Agent?
    @Published var error: String?
    private var touched = Date.distantPast
    private let fieldSender = LatestSender()
    private let volSender = LatestSender()
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
        // the app seeds half the flurry two seconds into their lives so the aura is never empty
        for _ in 0..<(flurrySettings.particleCount / 2) { spawnFlurry(timeOffset: -2.0) }
    }

    // MARK: - geometry (the app's UpmixTrack bindings)

    /// The app's `radius`: room radius − the puck's offset from the centre; ≤ 60 pt while snapped to a speaker.
    var fieldR: Double {
        let d = hypot(Double(puck.x - centre.x), Double(puck.y - centre.y))
        let r = max(roomR * 0.2, roomR - d)
        if cfg.snapShrinks { return r + (min(r, 60 * uiR / 150) - r) * snapAmount }
        return r
    }
    var puckR: Double { uiR * 0.165 * puckScale }
    var handleR: Double { uiR * 0.052 }
    var notchSize: (Double, Double) { (uiR * 0.042, uiR * 0.115) }
    /// The focus dots and the notch sit at (puckRadius + fieldRadius) / 2 from the puck (expanded & mixed).
    var dotRadius: Double { (puckR + fieldR) / 2 }
    func dirVec(_ a: Double) -> CGPoint { CGPoint(x: sin(a), y: -cos(a)) }
    func handlePos(_ angle: Double) -> CGPoint {
        let v = dirVec(angle)
        let r = CGFloat(dotRadius)
        return CGPoint(x: puck.x + v.x * r, y: puck.y + v.y * r)
    }
    var liftDotPos: CGPoint { CGPoint(x: puck.x, y: puck.y - CGFloat(puckR + handleR + uiR * 0.015)) }
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

    /// spreadPercentage: the arc the particles fill, as a fraction of π, from the dot half-angle (radians).
    static func spreadPercentage(_ half: Double) -> Double {
        let x = half / .pi
        return x * ((x * x * 24.0 - x * 10.0) + 11.0) / 10.0
    }
    /// The Aerosol's weight: 1 − innerRadius / outerRadius, inner = puckRadius + 2 pt, outer = fieldRadius.
    var appWeight: Double {
        let s = fieldR, h = puckR + 2.0 * uiR / 150
        return s > 0.01 ? max(0.2, min(0.95, 1 - h / s)) : 0.8
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

    /// One frame of CPU state: springs, palette, the pulse field, the flurry.
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
        spring(&expansion, &expansionVel, spatial ? 1 : 0, dt)
        spring(&snapAmount, &snapVel, snapped != nil ? 1 : 0, dt)
        volume += (volTarget - volume) * min(1, dt * 14)

        palette.blend(toward: paletteTarget, min(1, dt * (cfg.colourMode == .led && ledPalette != nil ? 2.4 : 1.6)))
        refreshColourBins()

        playViz()

        // the app writes a history row every 1/30 s whatever the packet timing (sampleInterval), then decays the
        // emitters ×0.9 — with no packets the rows simply fade out
        pulseTickAcc += dt * 30
        if pulseTickAcc > 3 { pulseTickAcc = 3 }
        while pulseTickAcc >= 1 {
            writePulseRow()
            for k in emitBright.keys { emitBright[k]! *= 0.9 }
            pulseTickAcc -= 1
        }
        if Date().timeIntervalSince(lastRow) > 0.4 { for i in 0..<24 { flux[i] *= pow(0.5, dt * 30) } }

        // pulse field: 6 bands, fast attack / slow release, wobbling and drifting around the puck. The rates are per
        // 60 Hz frame scaled by dt, so the ring answers the same at 60 (Apple TV) and 120 fps (ProMotion).
        let attack = 1 - pow(0.45, dt * 60), release = 1 - pow(0.90, dt * 60)
        for k in 0..<6 {
            var lv = 0.0
            for i in (k * 4)..<(k * 4 + 4) { lv += 0.55 * spec[i] + 0.9 * flux[i] }
            lv = min(1, lv / 4)
            let tgt = lv + 0.6 * beat * (k < 2 ? 1 : 0.4)
            bands[k] += (tgt > bands[k] ? attack : release) * (tgt - bands[k])
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

        advanceFlurry(dt)
        refreshRings()
        publishLabels()
    }
    func settle() {
        puck = puckTarget; puckVel = .zero
        puckScale = 1; puckScaleVel = 0
        dir = dirTarget; dirVel = 0
        half = halfTarget; halfVel = 0
        weight = spatial ? cfg.weightSpatial : cfg.weightOmni; weightVel = 0
        expansion = spatial ? 1 : 0; expansionVel = 0
        snapAmount = snapped != nil ? 1 : 0; snapVel = 0
        volume = volTarget
        palette = paletteTarget
        refreshColourBins(force: true)
        for _ in 0..<120 { advanceFlurry(1.0 / 60) }
        refreshRings()
    }

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

    /// 0 = fully spatial, 1 = sound all around.
    var omni: Double {
        if cfg.physics == .legacy {
            return min(max((weight - cfg.weightSpatial) / (cfg.weightOmni - cfg.weightSpatial), 0), 1)
        }
        return 1 - min(max(expansion, 0), 1)
    }

    /// The app's screen-space direction (radians, y down, −π/2 = up) from the model's "0 = up, clockwise".
    var screenDir: Double { dir - .pi / 2 }

    /// The Aerosol uniforms the app would compute: axis (angle, spreadAngle, spreadPercentage, weight),
    /// torus (ra, rb, capR, blend) and the two cap positions (field units, +y up).
    func appParams() -> (axis: SIMD4<Float>, torus: SIMD4<Float>, caps: SIMD4<Float>) {
        let halfRad = spatial || expansion > 0.001 ? half : halfTarget
        var sp = min(max(Self.spreadPercentage(halfRad), 0.05), 1.0)
        if cfg.particlesInClassic { sp = 1.0 + (sp - 1.0) * min(max(expansion, 0), 1) }   // classic: the full ring
        let w = appWeight
        let ra = 0.5 - w * 0.25, rb = w * 0.25
        let t = min(max((sp - 0.5) * 5, 0), 1)
        let capR = rb * (t * t * (3 - 2 * t))
        let blend = sp > 0.5 ? 0.1 : 1e-5
        let s = max(fieldR, 1), rc = dotRadius / (2 * s)          // the caps sit on the dot radius, at the arc's ends
        let a = screenDir
        let a1 = a - sp * .pi, a2 = a + sp * .pi
        let c1 = SIMD2<Double>(rc * cos(a1), -rc * sin(a1)), c2 = SIMD2<Double>(rc * cos(a2), -rc * sin(a2))
        return (SIMD4<Float>(Float(a), Float(halfRad), Float(sp), Float(w)),
                SIMD4<Float>(Float(ra), Float(rb), Float(capR), Float(blend)),
                SIMD4<Float>(Float(c1.x), Float(c1.y), Float(c2.x), Float(c2.y)))
    }

    /// Legacy prepSDF (the widget's still renderer and the legacy physics use this shape).
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

    // MARK: - gestures

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
        if cfg.showVolume, !cfg.fillScreen, d > roomR * 1.0, d < roomR * 1.5, abs(a - .pi / 2) < Self.volHalf + 0.12 {
            grab = .volume
            volGrabFinger = volFrom(p)
            volGrabBase = volTarget
            volEngaged = false
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
                var bd = cfg.snapFrac * uiR
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
            let delta = volFrom(p) - volGrabFinger
            if !volEngaged {
                guard abs(delta) > 0.012 else { return }
                volEngaged = true
                volGrabFinger = volFrom(p)
                Haptics.tap(0.5)
                return
            }
            let v = min(max(volGrabBase + delta, 0), 1)
            if Int(v * 20) != Int(volTarget * 20) { Haptics.tap(0.4) }
            volTarget = v; sendVolume(false)
        }
    }

    func end() {
        guard let g = grab else { return }
        grab = nil; liftDot = nil
        if g == .volume { if volEngaged { sendVolume(true) } } else if g != .lift { send(true) }
    }

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
    /// The spread as a number (the web page's Spread slider): the dot half-angle in degrees, clamped to the app's limits.
    func setWidth(halfDeg: Double, final: Bool) {
        guard spatial else { return }
        touched = Date()
        halfTarget = min(max(halfDeg, cfg.minHalfDeg), cfg.maxHalfDeg) * .pi / 180
        send(final)
    }
    /// Move the puck by a screen delta (points) — the Apple TV's touch surface, which has no finger on the field.
    /// Snaps to a speaker the same way a drag does; `commitPuck()` sends the release value.
    func movePuck(by d: CGSize) {
        touched = Date()
        puckTarget = clampPuck(CGPoint(x: puckTarget.x + d.width, y: puckTarget.y + d.height))
        snapped = nil
        if cfg.snapToCell {
            var best: Ring?
            var bd = cfg.snapFrac * uiR
            for r in rings {
                let dd = hypot(Double(r.pos.x - puckTarget.x), Double(r.pos.y - puckTarget.y))
                if dd < bd { bd = dd; best = r }
            }
            if let b = best { puckTarget = b.pos; snapped = b.serial }
        }
        send(false)
    }
    func commitPuck() { touched = Date(); send(true) }
    /// Volume by a step (0…1), sent as a final value.
    func adjustVolume(by d: Double) {
        touched = Date()
        volTarget = min(max(volTarget + d, 0), 1)
        sendVolume(true)
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
    func setColourMode(_ m: ColourMode) {
        objectWillChange.send()
        cfg.colourMode = m
        if m == .artwork { fetchArtworkIfNeeded(force: true) }
        refreshColourBins(force: true)
    }
    func setPhysics(_ p: Physics) {
        objectWillChange.send()
        cfg.physics = p
    }
    func setIdleDrift(_ on: Bool) { objectWillChange.send(); cfg.idleDrift = on ? 1 : 0 }
    func setPuff(_ on: Bool) { objectWillChange.send(); cfg.puffMix = on ? 1 : 0; cfg.beatPuff = on ? 1 : 0 }

    private func near(_ a: CGPoint, _ b: CGPoint, _ r: Double) -> Bool {
        hypot(Double(a.x - b.x), Double(a.y - b.y)) <= r
    }
    static let volHalf = 52.0 * Double.pi / 180
    private var volGrabFinger: Double = 0
    private var volGrabBase: Double = 0
    private var volEngaged = false
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
        if visualOnly || readOnly { return }
        if final { FieldModel.widgetsChanged() }
        var b = body(); b["final"] = final
        // latest-wins: one POST in flight, the newest puck/notch/dot position replaces any queued one, and the
        // release value is always the last thing sent. The agent coalesces its own cellbroker push the same way.
        fieldSender.submit(b) { [agent] b in
            do { _ = try await agent?.post("/api/soundfield", b, timeout: 4) }
            catch {
                let msg = error.localizedDescription
                await MainActor.run { self.error = msg }
            }
        }
    }
    private static var widgetReload: DispatchWorkItem?
    static func widgetsChanged() {
        #if canImport(WidgetKit) && !os(tvOS)
        widgetReload?.cancel()
        let w = DispatchWorkItem { WidgetCenter.shared.reloadTimelines(ofKind: "space.troubadour.aura") }
        widgetReload = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0, execute: w)
        #endif
    }
    private func sendVolume(_ final: Bool) {
        if readOnly { return }
        let v = (volTarget * 1000).rounded() / 1000
        volSender.submit(["source": "all", "volume": v]) { [agent] b in _ = try? await agent?.post("/api/volume", b, timeout: 4) }
    }

    // MARK: - live state

    func start() {
        guard vizTask == nil else { return }
        vizTask = Task { [weak self] in await self?.streamViz() }
        stateTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.pollState()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }
    func stop() {
        vizTask?.cancel(); vizTask = nil
        stateTask?.cancel(); stateTask = nil
        artTask?.cancel(); artTask = nil
    }

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
                    await MainActor.run { self.receive(o) }
                }
            } catch {
                if Task.isCancelled { return }
            }
            try? await Task.sleep(nanoseconds: 1_500_000_000)
        }
    }

    // MARK: viz jitter buffer

    /// The speaker stamps every /api/viz/stream frame with its own clock (`t`, ~30 a second, evenly spaced), but over
    /// Wi-Fi they reach the app in clumps — a 100–300 ms gap, then three to five at once. Ingested on arrival, the band
    /// ring held still and then jumped, whatever the frame rate. Each frame is now played out at its stamp plus a small
    /// delay that follows the network's recent jitter (20–150 ms), so the ring sees an even 30 Hz.
    private var vizQueue: [(at: Double, frame: [String: Any])] = []
    private var vizLag: [Double] = []          // arrival − stamp of recent frames (s; includes the clock offset)
    private var vizLagNext = 0
    private var vizDelay = 0.06
    private var vizLastSeq: Int?

    func receive(_ o: [String: Any]) {
        let now = Date().timeIntervalSince1970
        guard (o["live"] as? Bool) == true, let t = o["t"] as? Double, let sq = o["seq"] as? Int else {
            vizQueue.removeAll(); ingest(o); return              // idle / keepalive / an older agent: as before
        }
        if let last = vizLastSeq, sq == last { ingest(o); return }   // a repeat (keepalive): nothing to schedule
        if let last = vizLastSeq, sq < last || sq > last + 300 { vizLag.removeAll(); vizQueue.removeAll() }  // restart
        vizLastSeq = sq
        let lag = now - t
        if vizLag.count < 90 { vizLag.append(lag) } else { vizLag[vizLagNext] = lag; vizLagNext = (vizLagNext + 1) % 90 }
        let sorted = vizLag.sorted()
        let base = sorted[0]                                         // the quickest path = the clock offset
        let jitter = sorted[min(sorted.count - 1, Int(Double(sorted.count) * 0.9))] - base
        let target = min(max(jitter + 0.01, 0.02), 0.15)
        vizDelay += (target - vizDelay) * (target > vizDelay ? 0.2 : 0.02)   // widen quickly, narrow slowly
        let at = min(t + base + vizDelay, now + 0.3)
        vizQueue.append((at, o))
        if vizQueue.count > 20 { vizQueue.removeFirst(vizQueue.count - 20) }
    }
    /// Called every render frame: hand over the frames whose time has come, in order.
    private func playViz() {
        guard !vizQueue.isEmpty else { return }
        let now = Date().timeIntervalSince1970
        var n = 0
        while n < vizQueue.count, vizQueue[n].at <= now { ingest(vizQueue[n].frame); n += 1 }
        if n > 0 { vizQueue.removeFirst(n) }
    }

    func ingest(_ v: [String: Any]) {
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
        if let led = v["led"] as? [Double], led.count >= 3 {
            let w = led.count > 3 ? led[3] : 0
            ledPalette = FieldPalette.from(led[0], led[1], led[2], w)
            if ledColour == nil || abs(ledColour!.0 - led[0]) + abs(ledColour!.1 - led[1]) + abs(ledColour!.2 - led[2]) > 0.02 {
                ledColour = (led[0], led[1], led[2], w)
                ledBins = ColourGenerators.ledBins(led[0], led[1], led[2], w)
            }
        } else {
            ledPalette = nil; ledBins = nil; ledColour = nil
        }
        let newSeq = v["seq"] as? Int
        let isNew = newSeq == nil ? !sp.isEmpty : (newSeq != seq)
        let nowLive = (v["live"] as? Bool) ?? false
        if nowLive != live { live = nowLive }
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
        if let r = v["rms"] as? [Double], !r.isEmpty {
            stemDb = r
            stemNames = (v["stems"] as? [String]) ?? []
        } else {
            // an older agent: no per-stem levels — ride the overall level as one centre stem plus two sides
            let db = (v["rms_db"] as? Double) ?? (-50 + 50 * e)
            stemDb = [db - 3, db - 3, db]
            stemNames = ["L", "R", "C"]
        }
        detectPeaks()
        lastRow = Date()
    }

    // MARK: SurroundVisualizer — per packet: level → peak detector → emitter brightness

    /// ChannelLayout tables from the app (location°, spread°): Stereo L/R ±120 C 0 (spread 120); 5.1 L/R ±50 C 0
    /// Ls/Rs ±130 (spread 50); 7.1.x L/R ±50 C 0 side ±105 rear ±150 (spread 50). LFE, heights and wides never drive
    /// the pulse. Names are the cell's channel-layout tokens (what the agent sends as `stems`).
    static func layout(for names: [String]) -> [LayoutEmitter] {
        let set = Set(names)
        if set.contains("Lss") || set.contains("Rss") || set.contains("Lrs") || set.contains("Rrs") || names.count >= 8 {
            let t: [(String, Float)] = [("L", -50), ("R", 50), ("C", 0), ("Lss", -105), ("Rss", 105), ("Lrs", -150), ("Rrs", 150),
                                        ("Ls", -105), ("Rs", 105)]     // 7.1 with the classic Ls/Rs names as sides
            return t.filter { set.contains($0.0) }.map { LayoutEmitter(name: $0.0, loc: $0.1, spread: 50) }
        }
        if set.contains("Ls") || set.contains("Rs") {
            let t: [(String, Float)] = [("L", -50), ("R", 50), ("C", 0), ("Ls", -130), ("Rs", 130)]
            return t.filter { set.contains($0.0) }.map { LayoutEmitter(name: $0.0, loc: $0.1, spread: 50) }
        }
        let t: [(String, Float)] = [("L", -120), ("R", 120), ("C", 0)]
        return t.filter { set.contains($0.0) }.map { LayoutEmitter(name: $0.0, loc: $0.1, spread: 120) }
    }

    /// The app's per-packet step: level = ((dB + 100) / 100)³ per channel; a two-sample peak detector; when the
    /// previous sample was a local maximum its value becomes the channel's emitter brightness (a new, smaller
    /// peak replaces it — it is an assignment, not a max).
    private func detectPeaks() {
        var names = stemNames
        if names.count != stemDb.count {
            names = [[], ["C"], ["L", "R"], ["L", "R", "C"]][min(3, stemDb.count)]
            if stemDb.count > 3 { names = ["L", "R", "C"] + (0..<(stemDb.count - 3)).map { "S\($0)" } }
        }
        let key = names.joined(separator: " ")
        if key != layoutKey { layoutKey = key; layoutEmitters = FieldModel.layout(for: names) }
        for (i, n) in names.enumerated() where i < stemDb.count {
            let x = Float(stemDb[i]) / 100 + 1
            let level = max(0, x * x * x)
            var d = peakDet[n] ?? (p0: 0, f0: true, p1: 0, f1: true)
            if !d.f0 && !d.f1 {
                let falling = level - d.p1 < 0
                let wasRising = (level - d.p1) * (d.p1 - d.p0) <= 0
                if falling && wasRising { emitBright[n] = d.p1 }
            }
            d = (p0: d.p1, f0: d.f1, p1: level, f1: false)
            peakDet[n] = d
        }
    }

    /// One AudioDataHistory row: black, then every layout emitter adds brightness·exp(−((u − loc)/spread)²/0.05)
    /// with u = (deg + 180)/360 across the 512 columns (plus-lighter blend, 8-bit, clamped).
    private func writePulseRow() {
        let w = FieldModel.pulseW
        var row = [Float](repeating: 0, count: w)
        // on the full-ring (classic) map the stems are widened so the three bumps meet instead of leaving dead sectors
        let widen = Float(1 + (cfg.classicSpreadScale - 1) * pulseWrap)
        for e in layoutEmitters {
            guard let b = emitBright[e.name], b > 0.002 else { continue }
            let loc = (e.loc + 180) / 360, sp = e.spread / 360 * widen
            // the Gaussian is ~0 past 4σ (σ² = 0.025·spread²): only touch that span
            let reach = sp * 0.65
            let wrap = pulseWrap > 0.5          // classic: the row is a ring, a tail past either end continues at the other
            let c0 = Int(floor((loc - reach) * Float(w))), c1 = Int(ceil((loc + reach) * Float(w)))
            for c in c0...c1 {
                let u = (Float(c) + 0.5) / Float(w)
                let x = (u - loc) / sp
                let ci = wrap ? ((c % w) + w) % w : c
                if ci < 0 || ci >= w { continue }
                row[ci] += b * exp(-(x * x) / 0.05)
            }
        }
        var bytes = [UInt8](repeating: 0, count: w)
        let jitter = cfg.pulseJitter
        for c in 0..<w {
            let j = jitter > 0 ? 1 - jitter * 0.5 + jitter * Double.random(in: 0...1) : 1
            bytes[c] = UInt8(min(255, max(0, (Double(row[c]) * j * 255).rounded())))
        }
        pulseQueue.append(bytes)
        if pulseQueue.count > 4 { pulseQueue.removeFirst() }
    }
    /// Rows to upload: each goes to nextRow, which then steps DOWN (the app's `nextRow = (nextRow + 31) % 32`).
    func drainPulseRows() -> [(row: Int, data: [UInt8])] {
        guard !pulseQueue.isEmpty else { return [] }
        var out: [(row: Int, data: [UInt8])] = []
        for data in pulseQueue {
            out.append((row: pulseHead, data: data))
            pulseHead = (pulseHead + FieldModel.pulseRows - 1) % FieldModel.pulseRows
        }
        pulseQueue.removeAll()
        return out
    }
    /// pulseTextureOffset: the newest row, (nextRow + 1) / 32 — the UpmixTrack feeds exactly this to the Aerosol.
    var pulseHeadNorm: Double { Double((pulseHead + 1) % FieldModel.pulseRows) / Double(FieldModel.pulseRows) }
    /// angleTransform.x: spatial = (120 / spread°) / −2π (the arc spans ±60° of channel space); classic = 1 / −2π (the
    /// whole ring is the 360° channel map). Blended with expansionProgress so the map opens up as spatial turns on.
    var pulseAngleTransform: Double {
        let spreadDeg = max((spatial || expansion > 0.001 ? half : halfTarget) * 180 / .pi, 1)
        let spatialX = (120 / spreadDeg) / (-2 * Double.pi)
        let classicX = 1 / (-2 * Double.pi)
        if !cfg.particlesInClassic { return spatialX }
        let t = min(max(expansion, 0), 1)
        return classicX + (spatialX - classicX) * t
    }
    /// 1 = the pulse texture wraps around the ring (classic 360° map), 0 = clamped (the app's arc).
    var pulseWrap: Double { cfg.particlesInClassic ? 1 - min(max(expansion, 0), 1) : 0 }

    private func pollState() async {
        guard let a = agent else { return }
        do {
            let d = try await a.get("/api/state", timeout: 5)
            await MainActor.run { self.apply(d) }
        } catch {
            await MainActor.run { self.error = error.localizedDescription }
        }
    }

    func apply(_ d: [String: Any]) {
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
            if let act = src.first(where: { ($0.value["active"] as? Bool) == true }) {
                setSource(act.key)
                let has = (act.value["artwork"] as? Bool) ?? false
                let key = "\(act.key)|\((act.value["title"] as? String) ?? "")|\((act.value["album"] as? String) ?? "")"
                if has != hasArtwork { hasArtwork = has }
                if !has { artworkBins = nil; artworkPalette = nil; artKey = ""; artworkImage = nil; artworkVersion += 1; artworkLoaded = false }
                else if key != artKey { artKey = key; fetchArtworkIfNeeded(force: true, source: act.key) }
            } else if hasArtwork {
                hasArtwork = false; artworkBins = nil; artworkPalette = nil; artKey = ""; artworkImage = nil; artworkVersion += 1; artworkLoaded = false
            }
        }
        refreshRings()
        guard Date().timeIntervalSince(touched) > 2.5, grab == nil else { return }
        guard let f = room["field"] as? [String: Any] else { return }
        if let vol = (d["sources"] as? [String: [String: Any]])?.values.first(where: { ($0["active"] as? Bool) == true })?["volume"] as? Double {
            volTarget = vol; volume = vol
        }
        if visualOnly { return }
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

    private var sourceId = "airplay2"
    private func setSource(_ id: String) {
        let s = id.lowercased()
        let name: String
        if s.hasPrefix("spotify") { name = "spotify" }
        else if s.hasPrefix("tidal") { name = "tidal" }
        else if s.contains("hdmi") { name = "hdmi" }
        else if s.contains("pd") || s.contains("pure") { name = "puredata" }
        else if s.contains("usb") { name = "usb" }
        else { name = "airplay" }
        sourceId = id
        guard name != source else { return }
        source = name
        sourcePalette = FieldPalette.named(name)
        flurrySettings = FieldModel.flurryTable[name] ?? FlurrySettings()
        sourceBins = ColourGenerators.bins(for: name)
        refreshColourBins(force: true)
        switch name {
        case "spotify": sourceTitle = "Spotify"
        case "tidal": sourceTitle = "TIDAL"
        case "hdmi": sourceTitle = "HDMI"
        case "usb": sourceTitle = "USB"
        case "puredata": sourceTitle = "Pure Data"
        default: sourceTitle = "AirPlay"
        }
    }

    // MARK: - colour generators

    private var binsKey = ""
    private func refreshColourBins(force: Bool = false) {
        let key: String
        let bins: [ColourBin]
        switch cfg.colourMode {
        case .artwork:
            if let a = artworkBins { bins = a; key = "art|\(artKey)" } else { bins = sourceBins; key = "src|\(source)" }
        case .led:
            if let l = ledBins { bins = l; key = "led|\(ledColour!.0)|\(ledColour!.1)|\(ledColour!.2)" } else { bins = sourceBins; key = "src|\(source)" }
        case .source:
            bins = sourceBins; key = "src|\(source)"
        }
        guard force || key != binsKey else { return }
        binsKey = key
        colourBins = bins
        colourCDF = ColourGenerators.cdf(bins)
    }
    private var colourCDF: [Double] = ColourGenerators.cdf(ColourGenerators.bins(for: "airplay"))

    /// Pick a colour the way the app's ColorGenerator does: a bin with probability ∝ its count, a random colour inside it.
    private func sampleColour() -> SIMD3<Float> {
        guard !colourBins.isEmpty, colourCDF.count == colourBins.count else { return SIMD3<Float>(0.2, 0.6, 0.9) }
        let u = Double.random(in: 0..<1)
        var lo = 0, hi = colourCDF.count - 1
        while lo < hi { let mid = (lo + hi) / 2; if colourCDF[mid] < u { lo = mid + 1 } else { hi = mid } }
        let b = colourBins[lo]
        let j = Float(1.0 / 16.0)
        return SIMD3<Float>(min(1, b.r + Float.random(in: 0..<j)), min(1, b.g + Float.random(in: 0..<j)), min(1, b.b + Float.random(in: 0..<j)))
    }

    private func spawnFlurry(timeOffset: Double) {
        let s = flurrySettings
        var p = FlurryParticle()
        let now = time
        p.start = now + timeOffset
        p.end = p.start + s.lifetime * (1 + s.lifetimeVariance * Double.random(in: -1...1))
        p.color = sampleColour()
        p.pos = SIMD2<Double>(Double.random(in: 0...1), Double.random(in: 0...1))
        p.vel = SIMD2<Double>(Double.random(in: -1...1), Double.random(in: -1...1)) * (s.velocity * 0.001)
        p.radius = Double.random(in: min(s.minRadius, s.maxRadius)...max(s.minRadius, s.maxRadius))
        p.stroke = Double.random(in: min(s.minStroke, s.maxStroke)...max(s.minStroke, s.maxStroke))
        p.feather = Double.random(in: min(s.minFeather, s.maxFeather)...max(s.minFeather, s.maxFeather))
        flurry.append(p)
    }

    /// FlurryGenerator.update: a weak pull to the centre, pairwise repulsion scaled by both radii², per-frame
    /// velocity integration (the app runs this once per frame, so the step is in frames, not seconds).
    private func advanceFlurry(_ dt: Double) {
        let s = flurrySettings
        let now = time
        flurry.removeAll { $0.end <= now }
        if flurry.isEmpty {
            for _ in 0..<(s.particleCount / 2) { spawnFlurry(timeOffset: -2.0) }
        } else if flurry.count < s.particleCount, Double.random(in: 0..<1) <= s.birthRate * (dt * 60) {
            spawnFlurry(timeOffset: 0)
        }
        let steps = max(1, Int((dt * 60).rounded()))
        for _ in 0..<min(steps, 3) {
            var forces = [SIMD2<Double>](repeating: .zero, count: flurry.count)
            for i in flurry.indices {
                let pi = flurry[i].pos
                var f = (pi - SIMD2<Double>(0.5, 0.5)) * -1e-5
                let ri2 = flurry[i].radius * flurry[i].radius
                for j in flurry.indices where j != i {
                    let d = flurry[j].pos - pi
                    let len = simd_length(d)
                    if len > 0.0001 {
                        let rj2 = flurry[j].radius * flurry[j].radius
                        f += d * (-0.0001 * ri2 * rj2) / (len * len)
                    }
                }
                forces[i] = f
            }
            for i in flurry.indices {
                flurry[i].vel += forces[i]
                flurry[i].pos += flurry[i].vel
            }
        }
        // the envelope normalisation the app applies before drawing: k = min(1, 30 / Σ envelope)
        var sum = 0.0
        for p in flurry {
            let t = (now - p.start) / max(0.001, p.end - p.start)
            sum += (1 - cos(2 * .pi * t)) * 0.5
        }
        flurryK = sum > 0 ? min(1, 30 / sum) : 1
    }
    var flurryK = 1.0

    /// The blobs for the renderer: (x, y, radius, alpha) + (r, g, b, feather), in the unit square.
    func flurryBlobs() -> [(SIMD4<Float>, SIMD4<Float>)] {
        let now = time
        var out: [(SIMD4<Float>, SIMD4<Float>)] = []
        out.reserveCapacity(flurry.count)
        var centres: [(SIMD2<Float>, Float, Float, SIMD3<Float>)] = []
        for p in flurry {
            let t = min(max((now - p.start) / max(0.001, p.end - p.start), 0), 1)
            let env = (1 - cos(2 * .pi * t)) * 0.5
            let a = Float(flurryK * env)
            let r = Float(p.radius * (env + 0.16))
            let geo = SIMD4<Float>(Float(p.pos.x), Float(p.pos.y), r, a * a * Float(cfg.flurry))
            let col = SIMD4<Float>(p.color.x, p.color.y, p.color.z, Float(p.feather))
            out.append((geo, col))
            centres.append((SIMD2<Float>(Float(p.pos.x), Float(p.pos.y)), r, geo.w, p.color))
        }
        // the brightest point (approximately: evaluated at each blob's centre) for the normalisation step
        var mx: Float = 0
        for (c, _, _, _) in centres {
            var acc = SIMD3<Float>(0, 0, 0)
            for (c2, r2, a2, col2) in centres {
                let d = simd_length(c - c2)
                let cov = 1 - min(max((d - r2 * 0.05) / max(r2 * 0.95, 1e-4), 0), 1)
                acc += col2 * (a2 * cov)
            }
            mx = max(mx, simd_length(acc))
        }
        flurryMax = mx
        return out
    }

    // MARK: - artwork → colour generator (ArtworkProcessor: CalculateColorBins on the cover)

    private func fetchArtworkIfNeeded(force: Bool, source: String? = nil) {
        guard let a = agent, hasArtwork || force else { return }
        let sid = source ?? sourceId
        let key = artKey
        artTask?.cancel()
        artTask = Task { [weak self] in
            var comps = URLComponents(url: a.base.appendingPathComponent("/api/artwork"), resolvingAgainstBaseURL: false)
            comps?.queryItems = [URLQueryItem(name: "source", value: sid), URLQueryItem(name: "k", value: key)]
            guard let url = comps?.url else { return }
            guard let (data, _) = try? await URLSession.shared.data(from: url), !Task.isCancelled else { return }
            guard let img = UIImage(data: data)?.cgImage else { return }
            let bins = ColourGenerators.histogram(of: img, side: 48)
            let pal = FieldPalette.fromBins(bins)
            await MainActor.run {
                guard let self = self, self.artKey == key else { return }
                self.artworkBins = bins
                self.artworkPalette = pal
                self.artworkImage = img
                self.artworkVersion += 1
                self.artworkLoaded = true
                self.refreshColourBins(force: true)
            }
        }
    }
}

// MARK: - colour bins (the app's 16×16×16 histograms)

struct ColourBin { var r: Float, g: Float, b: Float, w: Float }

enum ColourGenerators {
    /// The app's own `Auras_<source>.json` colour generators (`_re/auras/*_colorgen.png`, 16³ histograms), as
    /// "rrggbb:weight" lists; the weights are per mille of the total. Decoded by tools in the repo (docs/SOUND-FIELD.md).
    static func bins(for source: String) -> [ColourBin] {
        let key: String
        switch source {
        case "spotify": key = "spotify"
        case "tidal": key = "tidal"
        case "hdmi": key = "hdmi"
        case "puredata": key = "puredata"
        case "usb": key = "usb-c"
        default: key = "airplay"
        }
        if let cached = cache[key] { return cached }
        let txt = ColourGeneratorData.table[key] ?? ColourGeneratorData.table["airplay"]!
        var out: [ColourBin] = []
        for item in txt.split(separator: " ") {
            let parts = item.split(separator: ":")
            guard parts.count == 2, let hex = UInt32(parts[0], radix: 16), let w = Float(parts[1]) else { continue }
            out.append(ColourBin(r: Float((hex >> 8) & 0xf) / 15, g: Float((hex >> 4) & 0xf) / 15, b: Float(hex & 0xf) / 15, w: w))
        }
        cache[key] = out
        return out
    }
    private static var cache: [String: [ColourBin]] = [:]

    static func cdf(_ bins: [ColourBin]) -> [Double] {
        var acc = 0.0
        let total = max(1e-6, bins.reduce(0.0) { $0 + Double($1.w) })
        return bins.map { acc += Double($0.w) / total; return acc }
    }

    /// A generator around one LED colour: the structure of the app's own palettes (hue spread, a range of
    /// saturation and value) rebuilt around the light's hue.
    static func ledBins(_ r: Double, _ g: Double, _ b: Double, _ w: Double) -> [ColourBin]? {
        let mx = max(r, max(g, b)), mn = min(r, min(g, b)), d = mx - mn
        guard mx > 0.03 else { return nil }
        var h = 0.0
        if d > 1e-6 {
            if mx == r { h = ((g - b) / d + (g < b ? 6 : 0)) / 6 }
            else if mx == g { h = ((b - r) / d + 2) / 6 }
            else { h = ((r - g) / d + 4) / 6 }
        }
        let sat = (d / mx) * (1 - 0.5 * w)
        guard sat > 0.12 else { return nil }
        var out: [ColourBin] = []
        for i in 0..<48 {
            let dh = Double(i % 6 - 2) * 0.012
            let s = 0.45 + Double((i / 6) % 4) * 0.18
            let v = 0.55 + Double((i / 24) % 2) * 0.4
            let c = hsv(h + dh, min(1, s), v)
            out.append(ColourBin(r: Float(c.0), g: Float(c.1), b: Float(c.2), w: 1))
        }
        return out
    }

    /// CalculateColorBins on the artwork, on the CPU: a 16³ histogram of a small downscale of the cover.
    static func histogram(of img: CGImage, side: Int) -> [ColourBin] {
        var px = [UInt8](repeating: 0, count: side * side * 4)
        let cs = CGColorSpaceCreateDeviceRGB()
        guard let ctx = CGContext(data: &px, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side * 4,
                                  space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return [] }
        ctx.interpolationQuality = .medium
        ctx.draw(img, in: CGRect(x: 0, y: 0, width: side, height: side))
        var counts = [Int](repeating: 0, count: 4096)
        for i in 0..<(side * side) {
            let r = Int((Float(px[i * 4]) / 255 * 15).rounded()), g = Int((Float(px[i * 4 + 1]) / 255 * 15).rounded()), b = Int((Float(px[i * 4 + 2]) / 255 * 15).rounded())
            counts[(r * 16 + g) * 16 + b] += 1
        }
        var out: [ColourBin] = []
        for (i, c) in counts.enumerated() where c > 0 {
            out.append(ColourBin(r: Float(i / 256) / 15, g: Float((i / 16) % 16) / 15, b: Float(i % 16) / 15, w: Float(c)))
        }
        return out
    }

    static func hsv(_ h0: Double, _ s: Double, _ v: Double) -> (Double, Double, Double) {
        let h = (h0.truncatingRemainder(dividingBy: 1) + 1).truncatingRemainder(dividingBy: 1)
        let i = Int(h * 6) % 6
        let f = h * 6 - (h * 6).rounded(.down)
        let p = v * (1 - s), q = v * (1 - s * f), t = v * (1 - s * (1 - f))
        switch i {
        case 0: return (v, t, p)
        case 1: return (q, v, p)
        case 2: return (p, v, t)
        case 3: return (p, q, v)
        case 4: return (t, p, v)
        default: return (v, p, q)
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
    static func from(_ r: Double, _ g: Double, _ b: Double, _ w: Double) -> FieldPalette? {
        let mx = max(r, max(g, b)), mn = min(r, min(g, b)), d = mx - mn
        guard mx > 0.03 else { return nil }
        var h = 0.0
        if d > 1e-6 {
            if mx == r { h = ((g - b) / d + (g < b ? 6 : 0)) / 6 }
            else if mx == g { h = ((b - r) / d + 2) / 6 }
            else { h = ((r - g) / d + 4) / 6 }
        }
        var sat = (d / mx) * (1 - 0.5 * w)
        guard sat > 0.12 else { return nil }
        sat = min(max(sat, 0.45), 1.0)
        func p(_ sm: Double, _ v: Double, _ dh: Double = 0) -> SIMD4<Float> {
            let c = ColourGenerators.hsv(h + dh, min(max(sat * sm, 0), 1), v)
            return rgb(c.0, c.1, c.2)
        }
        let raw = FieldPalette(deep: p(1.00, 0.54), cyan: p(0.83, 0.84), light: p(0.43, 0.95), white: p(0.10, 1.00),
                               blue: p(0.88, 0.86, -0.045), teal: p(0.89, 0.70, 0.035),
                               room: p(0.70, 0.33), roomEdge: p(0.70, 0.23))
        return raw.luminanceMatched(to: .airplay)
    }
    /// A palette from a colour generator (the artwork's): the room floor takes the histogram's mean, darkened.
    static func fromBins(_ bins: [ColourBin]) -> FieldPalette? {
        guard !bins.isEmpty else { return nil }
        var r = 0.0, g = 0.0, b = 0.0, tw = 0.0
        for x in bins { r += Double(x.r * x.w); g += Double(x.g * x.w); b += Double(x.b * x.w); tw += Double(x.w) }
        guard tw > 0 else { return nil }
        r /= tw; g /= tw; b /= tw
        let mx = max(r, max(g, b)), mn = min(r, min(g, b)), d = mx - mn
        var h = 0.0
        if d > 1e-6 {
            if mx == r { h = ((g - b) / d + (g < b ? 6 : 0)) / 6 }
            else if mx == g { h = ((b - r) / d + 2) / 6 }
            else { h = ((r - g) / d + 4) / 6 }
        }
        let sat = min(max(mx > 0 ? d / mx : 0, 0.25), 0.9)
        func p(_ sm: Double, _ v: Double, _ dh: Double = 0) -> SIMD4<Float> {
            let c = ColourGenerators.hsv(h + dh, min(max(sat * sm, 0), 1), v)
            return rgb(c.0, c.1, c.2)
        }
        return FieldPalette(deep: p(1.00, 0.54), cyan: p(0.83, 0.84), light: p(0.43, 0.95), white: p(0.10, 1.00),
                            blue: p(0.88, 0.86, -0.045), teal: p(0.89, 0.70, 0.035),
                            room: p(0.70, 0.30), roomEdge: p(0.70, 0.20)).luminanceMatched(to: .airplay)
    }
    func luminanceMatched(to ref: FieldPalette) -> FieldPalette {
        func y(_ c: SIMD4<Float>) -> Float { 0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z }
        func fit(_ c: SIMD4<Float>, _ r: SIMD4<Float>) -> SIMD4<Float> {
            let cap = y(r) * 1.15, have = y(c)
            guard have > cap, have > 1e-4 else { return c }
            let k = cap / have
            return SIMD4<Float>(c.x * k, c.y * k, c.z * k, c.w)
        }
        return FieldPalette(deep: fit(deep, ref.deep), cyan: fit(cyan, ref.cyan), light: fit(light, ref.light),
                            white: fit(white, ref.white), blue: fit(blue, ref.blue), teal: fit(teal, ref.teal),
                            room: fit(room, ref.room), roomEdge: fit(roomEdge, ref.roomEdge))
    }
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
