import AlbedianCaptureCore
import SwiftUI

/// The second action (UIUX-buttons-READY part 3, `.ab-btn.ab-line`): an outlined pill, 1 pt of ink at 88 percent,
/// padding 0 20, as wide as its word; pressed, it takes the ink wash in 0.2 s and scales to 0.97. The Credits pill is the
/// same at 36 pt high in Geist 15 with a 44 pt touch area (UIUX-credit-APPROVED); "Change address" is 44 pt in Geist 16.
struct OutlinePill: View {
    let title: String
    var height: Double = ControlStyle.mainHeight
    var fontSize: Double = ControlStyle.mainFontSize
    let action: () -> Void

    var body: some View {
        Button(action: action) { EmptyView() }
            .buttonStyle(OutlinePillStyle(title: title, height: height, fontSize: fontSize))
            .accessibilityLabel(title)
    }
}

struct OutlinePillStyle: ButtonStyle {
    let title: String
    let height: Double
    let fontSize: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        return Text(title)
            .font(AlbedianFont.regular(fontSize))
            .tracking(CGFloat(ControlStyle.letterSpacingEm * fontSize))
            .lineLimit(1)
            .fixedSize()
            .foregroundStyle(Color(ControlStyle.ink))
            .padding(.horizontal, 20)
            .frame(height: CGFloat(height))
            .background(Capsule().fill(pressed ? Color(ControlStyle.wash) : .clear))
            .overlay(Capsule().strokeBorder(Color(ControlStyle.line), lineWidth: CGFloat(ControlStyle.outlineWidth)))
            .animation(.control(ControlStyle.colourSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
            .frame(minHeight: CGFloat(ControlStyle.minTouchTarget))
            .contentShape(Rectangle())
            .scaleEffect(pressed ? CGFloat(ControlStyle.pressScale) : 1)
            .animation(.control(ControlStyle.pressSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
    }
}
