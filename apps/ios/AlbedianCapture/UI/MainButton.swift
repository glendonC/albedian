import AlbedianCaptureCore
import SwiftUI
import UIKit

/// The screen's one main action (`arrive/build.mjs`, `.ab-split.ab-sz-m`): a filled ink pill 44 pt high and as wide as
/// its word, with an outlined 44 pt circle touching beside it holding the icon. While pressed, the pill's trailing
/// padding grows by 44 pt and the circle moves left by 44 pt and fills, so total width never changes; an arrow turns
/// from -45 degrees to 0 on the 1.2 s spring (other icons do not turn), and the whole button scales to 0.97.
struct MainButton: View {
    let title: String
    let icon: AlbedianIcon
    let action: () -> Void

    var body: some View {
        Button(action: action) { EmptyView() }
            .buttonStyle(MainButtonStyle(title: title, icon: icon))
            .accessibilityLabel(title)
    }
}

struct MainButtonStyle: ButtonStyle {
    let title: String
    let icon: AlbedianIcon
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// mainbutton-scale-m: the circle's icon grows with the text, as Apple asks of meaningful icons
    @ScaledMetric(relativeTo: .body) private var iconSize = ControlStyle.iconSize

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        // mainbutton-scale-m (UIUX-iphone-largertext-sweep-READY item 6, DECIDED-BY-LEAD 24 Nov 19:56): one line of the
        // pill is the word's line now with the default's space over and under it (44 pt at Large); the circle keeps it.
        // The word stands on one line, as before, while the button fits within the margins, and wraps inside the pill,
        // centred, only when it does not
        let size = ButtonLine.size(fontSize: ControlStyle.mainFontSize, controlHeight: ControlStyle.mainHeight, dynamicTypeSize)
        return ViewThatFits(in: .horizontal) {
            row(pressed: pressed, size: size, wraps: false)
            row(pressed: pressed, size: size, wraps: true)
        }
        .animation(.control(ControlStyle.springSeconds, ControlStyle.springCurve, reduceMotion: reduceMotion), value: pressed)
        .contentShape(Rectangle())
        .scaleEffect(pressed ? CGFloat(ControlStyle.pressScale) : 1)
        .animation(.control(ControlStyle.pressSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
    }

    private func row(pressed: Bool, size: (oneLine: Double, space: Double), wraps: Bool) -> some View {
        let height = CGFloat(size.oneLine)
        let circle = height
        let pad = CGFloat(ControlStyle.mainPillPadding)
        let outline = CGFloat(ControlStyle.outlineWidth)
        return HStack(spacing: pressed ? -circle : 0) {
            word(wraps: wraps, height: height, space: CGFloat(size.space))
                .padding(.leading, pad)
                .padding(.trailing, pressed ? pad + circle : pad)
                .background(RoundedRectangle(cornerRadius: height / 2).fill(Color(ControlStyle.ink)))
                .overlay(RoundedRectangle(cornerRadius: height / 2).strokeBorder(Color(ControlStyle.ink), lineWidth: outline))
            ZStack {
                Circle().fill(pressed ? Color(ControlStyle.ink) : .clear)
                Circle().strokeBorder(pressed ? Color(ControlStyle.ink) : Color(ControlStyle.line), lineWidth: outline)
                icon.image(iconSize)
                    .foregroundStyle(pressed ? Color(ControlStyle.night) : Color(ControlStyle.ink))
                    .rotationEffect(.degrees(icon == .arrow && !pressed ? ControlStyle.arrowRestDegrees : 0))
            }
            .frame(width: circle, height: circle)
        }
    }

    /// The word on one line, the pill exactly one line high; or wrapped, centred, with the space kept over and under it.
    @ViewBuilder private func word(wraps: Bool, height: CGFloat, space: CGFloat) -> some View {
        let text = Text(title)
            .font(AlbedianFont.regular(ControlStyle.mainFontSize))
            .tracking(CGFloat(ControlStyle.letterSpacingEm * ControlStyle.mainFontSize))
            .foregroundStyle(Color(ControlStyle.night))
        if wraps {
            text.multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, space).frame(minHeight: height)
        } else {
            text.lineLimit(1).fixedSize().frame(height: height)
        }
    }
}

/// A button's word at the current text size (Geist on the body style, as `AlbedianFont.regular` sets it): its one-line
/// height from the font Dynamic Type scales, and the pill's one-line height and the space over and under the word.
enum ButtonLine {
    static func height(fontSize: Double, _ size: DynamicTypeSize) -> Double {
        let traits = UITraitCollection(preferredContentSizeCategory: UIContentSizeCategory(size))
        let scaled = UIFontMetrics(forTextStyle: .body).scaledValue(for: CGFloat(fontSize), compatibleWith: traits)
        return Double(UIFont(name: "Geist-Regular", size: scaled)?.lineHeight ?? scaled * 1.2)
    }

    static func size(fontSize: Double, controlHeight: Double, _ size: DynamicTypeSize) -> (oneLine: Double, space: Double) {
        let atLarge = height(fontSize: fontSize, .large)
        return (ButtonGeometry.oneLineHeight(lineHeight: height(fontSize: fontSize, size), lineHeightAtLarge: atLarge, controlHeight: controlHeight),
                ButtonGeometry.spaceOverUnder(lineHeightAtLarge: atLarge, controlHeight: controlHeight))
    }
}
