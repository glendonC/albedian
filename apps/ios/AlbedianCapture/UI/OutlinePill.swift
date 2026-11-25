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
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        // mainbutton-scale-m: grown with its word as the main button's pill is; on one line while it fits, else wrapped
        let size = ButtonLine.size(fontSize: fontSize, controlHeight: height, dynamicTypeSize)
        return ViewThatFits(in: .horizontal) {
            pill(pressed: pressed, size: size, wraps: false)
            pill(pressed: pressed, size: size, wraps: true)
        }
        .frame(minHeight: CGFloat(ControlStyle.minTouchTarget))
        .contentShape(Rectangle())
        .scaleEffect(pressed ? CGFloat(ControlStyle.pressScale) : 1)
        .animation(.control(ControlStyle.pressSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
    }

    private func pill(pressed: Bool, size: (oneLine: Double, space: Double), wraps: Bool) -> some View {
        let oneLine = CGFloat(size.oneLine)
        return word(wraps: wraps, height: oneLine, space: CGFloat(size.space))
            .padding(.horizontal, 20)
            .background(RoundedRectangle(cornerRadius: oneLine / 2).fill(pressed ? Color(ControlStyle.wash) : .clear))
            .overlay(RoundedRectangle(cornerRadius: oneLine / 2).strokeBorder(Color(ControlStyle.line), lineWidth: CGFloat(ControlStyle.outlineWidth)))
            .animation(.control(ControlStyle.colourSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
    }

    /// The word on one line, the pill exactly one line high; or wrapped, centred, with the space kept over and under it.
    @ViewBuilder private func word(wraps: Bool, height: CGFloat, space: CGFloat) -> some View {
        let text = Text(title)
            .font(AlbedianFont.regular(fontSize))
            .tracking(CGFloat(ControlStyle.letterSpacingEm * fontSize))
            .foregroundStyle(Color(ControlStyle.ink))
        if wraps {
            text.multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, space).frame(minHeight: height)
        } else {
            text.lineLimit(1).fixedSize().frame(height: height)
        }
    }
}
