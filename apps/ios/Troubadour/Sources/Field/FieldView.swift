import SwiftUI
import MetalKit
import UIKit

#if os(tvOS)
/// No haptics on the Apple TV (and nothing to touch).
enum Haptics {
    static func tap(_ intensity: Double = 0.6) {}
    static func select() {}
}
#else
enum Haptics {
    private static let impact = UIImpactFeedbackGenerator(style: .light)
    private static let selection = UISelectionFeedbackGenerator()
    static func tap(_ intensity: Double = 0.6) {
        impact.prepare()
        impact.impactOccurred(intensity: CGFloat(min(max(intensity, 0), 1)))
    }
    static func select() { selection.prepare(); selection.selectionChanged() }
}
#endif

/// The Metal view itself. Touches go straight to the model here rather than through SwiftUI
/// gestures: a drag on the puck has to track the finger with no recognizer delay at all.
final class FieldMTKView: MTKView {
    weak var model: FieldModel?
    #if os(tvOS)
    func install() {}                              // the Apple TV only watches; the remote drives the app, not the field
    #else
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
    #endif
}

#if !os(tvOS)
extension FieldMTKView: UIGestureRecognizerDelegate {
    func gestureRecognizer(_ g: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
}
#endif

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
        #if os(tvOS)
        // A 4K Apple TV's drawable is 3840×2160; the field is soft gradients and glitter, so 1080p upscaled by the TV
        // costs a quarter of the GPU time for every full-screen pass and looks the same from the couch.
        v.contentScaleFactor = 1
        v.preferredFramesPerSecond = 60
        #else
        v.preferredFramesPerSecond = 120          // ProMotion; the system caps it on other screens
        #endif
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

#if !os(tvOS)
/// While the field is on screen, nothing the navigation controller owns may turn a drag into "back to
/// Rooms": not the edge swipe, and not the full-screen swipe-back recent iOS added (a horizontal drag
/// anywhere in the content — which is what kept catching the puck). Every pan recogniser on the
/// navigation controller's view is switched off while the Room tab is showing and put back exactly as it
/// was when it goes away. The Back button (ours, in RoomView) still works.
struct DisablesSwipeBack: UIViewControllerRepresentable {
    final class Holder: UIViewController {
        private var disabled: [UIGestureRecognizer] = []
        private var nav: UINavigationController? {
            var p: UIViewController? = parent
            while let c = p {
                if let n = c as? UINavigationController { return n }
                p = c.parent
            }
            return navigationController
        }
        // iPad: the split view's own swipe (revealing the Rooms sidebar from the left edge) is a pan too
        private var split: UISplitViewController? {
            var p: UIViewController? = parent
            while let c = p {
                if let s = c as? UISplitViewController { return s }
                p = c.parent
            }
            return splitViewController
        }
        private var splitGesture: Bool?
        private func block() {
            if splitGesture == nil, let s = split, s.presentsWithGesture {
                splitGesture = true; s.presentsWithGesture = false
            }
            guard disabled.isEmpty, let n = nav else { return }
            var found: [UIGestureRecognizer] = []
            if let edge = n.interactivePopGestureRecognizer { found.append(edge) }
            for g in n.view.gestureRecognizers ?? [] where g is UIPanGestureRecognizer {
                if !found.contains(where: { $0 === g }) { found.append(g) }
            }
            for g in found where g.isEnabled {
                g.isEnabled = false
                disabled.append(g)
            }
        }
        private func unblock() {
            if splitGesture == true { split?.presentsWithGesture = true }
            splitGesture = nil
            for g in disabled { g.isEnabled = true }
            disabled.removeAll()
        }
        override func viewWillAppear(_ animated: Bool) { super.viewWillAppear(animated); block() }
        override func viewDidAppear(_ animated: Bool) { super.viewDidAppear(animated); block() }   // first load: the nav is only reachable now
        override func viewWillDisappear(_ animated: Bool) { super.viewWillDisappear(animated); unblock() }
    }
    func makeUIViewController(context: Context) -> Holder { Holder() }
    func updateUIViewController(_ c: Holder, context: Context) {}
}
#endif
