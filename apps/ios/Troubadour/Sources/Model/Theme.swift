import SwiftUI

/// The Syng app's palette: black pages, borderless grey cards, white as the only accent.
enum Theme {
    static let bg = Color.black
    static let surface = Color(white: 0.10)
    static let surface2 = Color(white: 0.165)
    static let muted = Color(white: 0.56)
    static let live = Color(red: 0.47, green: 0.87, blue: 0.59)
    static let warn = Color(red: 0.88, green: 0.70, blue: 0.35)
    static let bad = Color(red: 0.88, green: 0.32, blue: 0.30)
    static let amber = Color(red: 0.89, green: 0.64, blue: 0.30)   // the three dots on a Cell
}

struct Card<Content: View>: View {
    let title: String?
    let content: Content
    init(_ title: String? = nil, @ViewBuilder content: () -> Content) { self.title = title; self.content = content() }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let t = title {
                Text(t.uppercased()).font(.caption.weight(.medium)).tracking(1.2).foregroundStyle(Theme.muted)
            }
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}

/// White capsule = primary action; outlined = secondary; filled when selected (the app's buttons).
struct Capsule_: ButtonStyle {
    var filled = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 16).padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(filled ? Color.white : Theme.surface2, in: Capsule())
            .foregroundStyle(filled ? Color.black : Color.white)
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

/// A Cell as the app draws it: black puck, three amber dots.
struct Puck: View {
    var size: CGFloat = 44
    var ring: Color? = nil
    var body: some View {
        ZStack {
            Circle().fill(RadialGradient(colors: [Color(white: 0.23), Color(white: 0.03)], center: .init(x: 0.42, y: 0.36), startRadius: 0, endRadius: size * 0.6))
            ForEach(0..<3, id: \.self) { i in
                let a = Double(i) * 2 * .pi / 3 - .pi / 2
                Circle().fill(Theme.amber).frame(width: size * 0.12, height: size * 0.12)
                    .offset(x: CGFloat(cos(a)) * size * 0.2, y: CGFloat(sin(a)) * size * 0.2)
            }
            if let r = ring { Circle().stroke(r, lineWidth: 1.5).frame(width: size + 6, height: size + 6) }
        }
        .frame(width: size, height: size)
    }
}
