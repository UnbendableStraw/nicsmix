import SwiftUI
import MetalKit
import UIKit

enum Haptics {
    private static let impact = UIImpactFeedbackGenerator(style: .light)
    private static let selection = UISelectionFeedbackGenerator()
    static func tap(_ intensity: Double = 0.6) {
        impact.prepare()
        impact.impactOccurred(intensity: CGFloat(min(max(intensity, 0), 1)))
    }
    static func select() { selection.prepare(); selection.selectionChanged() }
}

/// The Metal view itself. Touches go straight to the model here rather than through SwiftUI
/// gestures: a drag on the puck has to track the finger with no recognizer delay at all.
final class FieldMTKView: MTKView {
    weak var model: FieldModel?
    private var dragging: UITouch?

    func install() {
        isMultipleTouchEnabled = true
        isUserInteractionEnabled = true
        let pinch = UIPinchGestureRecognizer(target: self, action: #selector(onPinch(_:)))
        let rotate = UIRotationGestureRecognizer(target: self, action: #selector(onRotate(_:)))
        let twoTap = UITapGestureRecognizer(target: self, action: #selector(onDoubleTap(_:)))
        twoTap.numberOfTapsRequired = 2
        for g in [pinch, rotate, twoTap] as [UIGestureRecognizer] {
            g.delegate = self
            g.cancelsTouchesInView = false
            g.delaysTouchesBegan = false
            addGestureRecognizer(g)
        }
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard dragging == nil, let t = touches.first else { return }
        dragging = t
        model?.begin(t.location(in: self))
    }
    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let t = dragging, touches.contains(t) else { return }
        model?.move(t.location(in: self))
    }
    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let t = dragging, touches.contains(t) else { return }
        dragging = nil
        model?.end()
    }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard let t = dragging, touches.contains(t) else { return }
        dragging = nil
        model?.end()
    }

    @objc private func onPinch(_ g: UIPinchGestureRecognizer) {
        guard let m = model else { return }
        if g.state == .began { dragging = nil; m.end() }
        if g.state == .changed {
            m.widenBy(Double(g.scale))
            g.scale = 1
        }
        if g.state == .ended || g.state == .cancelled { m.end() }
    }
    @objc private func onRotate(_ g: UIRotationGestureRecognizer) {
        guard let m = model else { return }
        if g.state == .began { dragging = nil; m.end() }
        if g.state == .changed {
            m.rotateBy(Double(g.rotation))
            g.rotation = 0
        }
        if g.state == .ended || g.state == .cancelled { m.end() }
    }
    @objc private func onDoubleTap(_ g: UITapGestureRecognizer) {
        model?.centrePuck()
    }
}

extension FieldMTKView: UIGestureRecognizerDelegate {
    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
}

struct FieldCanvas: UIViewRepresentable {
    let model: FieldModel

    final class Coordinator {
        var renderer: FieldRenderer?
    }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> MTKView {
        let v = FieldMTKView(frame: .zero, device: MTLCreateSystemDefaultDevice())
        v.model = model
        v.colorPixelFormat = .bgra8Unorm
        v.framebufferOnly = true
        v.isOpaque = true
        v.backgroundColor = .black
        v.layer.isOpaque = true
        v.enableSetNeedsDisplay = false
        v.isPaused = false
        v.preferredFramesPerSecond = 120          // ProMotion; the system caps it on other screens
        v.autoResizeDrawable = true
        v.install()
        if let r = FieldRenderer(model: model, pixelFormat: v.colorPixelFormat) {
            context.coordinator.renderer = r
            v.delegate = r
        }
        return v
    }

    func updateUIView(_ v: MTKView, context: Context) {
        (v as? FieldMTKView)?.model = model
    }
}

/// The Sound Field tab: the field itself, drawn and driven natively, plus the controls that are
/// clearer as buttons than as gestures.
struct SoundFieldView: View {
    let room: Room
    @StateObject private var m: FieldModel
    init(room: Room) {
        self.room = room
        _m = StateObject(wrappedValue: FieldModel(agent: room.agent))
    }

    var body: some View {
        GeometryReader { geo in
            VStack(spacing: 0) {
                FieldCanvas(model: m)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipped()
                controls
                    .padding(.horizontal, 16)
                    .padding(.bottom, max(geo.safeAreaInsets.bottom, 8))
            }
        }
        .background(Theme.bg.ignoresSafeArea())
        .onAppear { m.start() }
        .onDisappear { m.stop() }
    }

    private var controls: some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                Circle().fill(m.live ? Theme.live : Theme.muted.opacity(0.4)).frame(width: 7, height: 7)
                Text(m.sourceTitle).font(.subheadline.weight(.semibold))
                Spacer()
                Text(m.spatialOn ? "\(m.widthDeg)° wide · \(m.aimDeg)°" : "All around")
                    .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                Text("\(m.volPct)").font(.subheadline.weight(.semibold).monospacedDigit()).frame(width: 30, alignment: .trailing)
            }
            HStack(spacing: 8) {
                Button("Centre") { m.centrePuck() }.buttonStyle(Capsule_())
                Button(m.spatialOn ? "All around" : "Spatial") { m.setSpatial(!m.spatialOn) }
                    .buttonStyle(Capsule_(filled: m.spatialOn))
            }
            Text(m.spatialOn
                 ? "Drag the puck to move the sound, the notch to aim it, a dot to widen it. Pinch for width, twist to aim, double-tap to centre. The rim at the bottom is volume."
                 : "Drag the puck to move the sound, or pull the dot above it out to steer the sound in one direction. The rim at the bottom is volume.")
                .font(.caption).foregroundStyle(Theme.muted).fixedSize(horizontal: false, vertical: true)
            if let e = m.error {
                Text(e).font(.caption).foregroundStyle(Theme.bad).lineLimit(2)
            }
        }
        .padding(.top, 10)
    }
}
