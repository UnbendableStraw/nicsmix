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

/// While the field is on screen the navigation controller's edge-swipe is off: a drag that starts
/// near the left edge belongs to the puck, not to "back to Rooms". The Back button still works.
struct DisablesSwipeBack: UIViewControllerRepresentable {
    final class Holder: UIViewController {
        private var nav: UINavigationController? {
            var p: UIViewController? = parent
            while let c = p {
                if let n = c as? UINavigationController { return n }
                p = c.parent
            }
            return navigationController
        }
        override func viewWillAppear(_ animated: Bool) {
            super.viewWillAppear(animated)
            nav?.interactivePopGestureRecognizer?.isEnabled = false
        }
        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            nav?.interactivePopGestureRecognizer?.isEnabled = true
        }
    }
    func makeUIViewController(context: Context) -> Holder { Holder() }
    func updateUIViewController(_ c: Holder, context: Context) {}
}
