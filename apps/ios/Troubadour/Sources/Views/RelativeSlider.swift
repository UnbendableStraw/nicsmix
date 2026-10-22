import SwiftUI

/// A volume bar you drag rather than tap: the value moves by how far the finger travels, never to where it
/// touched, and a tap changes nothing — so it cannot suddenly jump the volume up. Haptic tick every 5 %.
struct RelativeSlider: View {
    let value: Double
    var track: CGFloat = 8              // the bar's thickness
    var knob: CGFloat = 26              // the handle
    var showsNumber = true
    var ceiling: Double = 1             // the volume ceiling (settings.volume_limit / 100): the drag stops there
    let onChange: (Double, Bool) -> Void
    init(value: Double, track: CGFloat = 8, knob: CGFloat = 26, showsNumber: Bool = true, max: Double = 1, onChange: @escaping (Double, Bool) -> Void) {
        self.value = value; self.track = track; self.knob = knob; self.showsNumber = showsNumber; self.ceiling = Swift.max(0.05, Swift.min(max, 1)); self.onChange = onChange
    }
    @State private var base: Double?
    @State private var shown: Double?
    @State private var lastTick = -1

    var body: some View {
        GeometryReader { geo in
            let w = max(geo.size.width, 1)
            let v = shown ?? value
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.surface2).frame(height: track)
                if ceiling < 0.999 {        // the ceiling, as a tick on the track
                    Rectangle().fill(Theme.muted).frame(width: 2, height: track + 6).offset(x: (w - knob) * CGFloat(ceiling) + knob / 2 - 1)
                }
                Capsule().fill(Color.white).frame(width: max(track, w * CGFloat(v)), height: track)
                Circle().fill(Color.white).frame(width: knob, height: knob)
                    .shadow(color: .black.opacity(0.4), radius: 3, y: 1)
                    .offset(x: (w - knob) * CGFloat(v))
            }
            .frame(height: knob + 4)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 6)
                    .onChanged { g in
                        let start = base ?? value
                        if base == nil { base = value }
                        let nv = min(max(start + Double(g.translation.width / w), 0), ceiling)
                        shown = nv
                        let step = Int(nv * 20)
                        if step != lastTick { lastTick = step; Haptics.tap(0.4) }
                        onChange(nv, false)
                    }
                    .onEnded { _ in
                        if let v = shown { onChange(v, true) }
                        base = nil; shown = nil
                    }
            )
        }
        .frame(height: knob + 4)
        .overlay(alignment: .trailing) {
            if showsNumber {
                Text("\(Int(((shown ?? value) * 100).rounded()))")
                    .font(.caption.monospacedDigit()).foregroundStyle(Theme.muted)
                    .offset(y: 24)
            }
        }
        .padding(.bottom, showsNumber ? 14 : 0)
    }
}
