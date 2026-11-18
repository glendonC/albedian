import AlbedianCaptureCore
import SwiftUI

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

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        let height = CGFloat(ControlStyle.mainHeight)
        let circle = CGFloat(ControlStyle.mainCircleSide)
        let pad = CGFloat(ControlStyle.mainPillPadding)
        let dockedTrailing = CGFloat(ControlStyle.mainPillDockedTrailingPadding)
        let outline = CGFloat(ControlStyle.outlineWidth)
        return HStack(spacing: pressed ? -circle : 0) {
            Text(title)
                .font(AlbedianFont.regular(ControlStyle.mainFontSize))
                .tracking(CGFloat(ControlStyle.letterSpacingEm * ControlStyle.mainFontSize))
                .lineLimit(1)
                .fixedSize()
                .foregroundStyle(Color(ControlStyle.night))
                .padding(.leading, pad)
                .padding(.trailing, pressed ? dockedTrailing : pad)
                .frame(height: height)
                .background(Capsule().fill(Color(ControlStyle.ink)))
                .overlay(Capsule().strokeBorder(Color(ControlStyle.ink), lineWidth: outline))
            ZStack {
                Circle().fill(pressed ? Color(ControlStyle.ink) : .clear)
                Circle().strokeBorder(pressed ? Color(ControlStyle.ink) : Color(ControlStyle.line), lineWidth: outline)
                icon.image(ControlStyle.iconSize)
                    .foregroundStyle(pressed ? Color(ControlStyle.night) : Color(ControlStyle.ink))
                    .rotationEffect(.degrees(icon == .arrow && !pressed ? ControlStyle.arrowRestDegrees : 0))
            }
            .frame(width: circle, height: circle)
        }
        .animation(.control(ControlStyle.springSeconds, ControlStyle.springCurve, reduceMotion: reduceMotion), value: pressed)
        .contentShape(Rectangle())
        .scaleEffect(pressed ? CGFloat(ControlStyle.pressScale) : 1)
        .animation(.control(ControlStyle.pressSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
    }
}
