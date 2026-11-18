import AlbedianCaptureCore
import SwiftUI

/// An icon only button (close, take again): an outlined circle 44 pt across, 1 pt of ink at 88 percent; pressed, it
/// fills with ink and the icon turns night, in 0.2 s, and the whole scales to 0.97.
struct IconButton: View {
    let icon: AlbedianIcon
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) { EmptyView() }
            .buttonStyle(IconButtonStyle(icon: icon))
            .accessibilityLabel(label)
    }
}

struct IconButtonStyle: ButtonStyle {
    let icon: AlbedianIcon
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        let side = CGFloat(ControlStyle.iconButtonSide)
        return ZStack {
            Circle().fill(pressed ? Color(ControlStyle.ink) : .clear)
            Circle().strokeBorder(pressed ? Color(ControlStyle.ink) : Color(ControlStyle.line), lineWidth: CGFloat(ControlStyle.outlineWidth))
            icon.image(ControlStyle.iconSize).foregroundStyle(pressed ? Color(ControlStyle.night) : Color(ControlStyle.ink))
        }
        .animation(.control(ControlStyle.colourSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
        .frame(width: side, height: side)
        .contentShape(Circle())
        .scaleEffect(pressed ? CGFloat(ControlStyle.pressScale) : 1)
        .animation(.control(ControlStyle.pressSeconds, ControlStyle.standardCurve, reduceMotion: reduceMotion), value: pressed)
    }
}
