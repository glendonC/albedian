import AlbedianCaptureCore
import SwiftUI

/// The capture button's disc: a circle of the approved radius at the button's centre, with Albedian's star cut out
/// of it at the approved scale, turned by `starTurn` about the star's own centre (`ShutterGeometry` holds the numbers).
struct ShutterDiscShape: Shape {
    var starTurn: Double = 0

    var animatableData: Double {
        get { starTurn }
        set { starTurn = newValue }
    }

    func path(in rect: CGRect) -> Path {
        // The rectangle is the disc's; points are the button's, from its centre
        let scale = min(rect.width, rect.height) / (2 * ShutterGeometry.discRadius)
        func place(_ point: (x: Double, y: Double)) -> CGPoint {
            CGPoint(x: rect.midX + point.x * scale, y: rect.midY + point.y * scale)
        }
        let radius = ShutterGeometry.discRadius * scale
        let disc = Path(ellipseIn: CGRect(x: rect.midX - radius, y: rect.midY - radius, width: 2 * radius, height: 2 * radius))
        let points = ShutterGeometry.starPoints(turnedBy: starTurn).map(place)
        let centre = place(ShutterGeometry.starCentre)
        var star = Path()
        star.move(to: points[0])
        for next in points.dropFirst() + [points[0]] { star.addQuadCurve(to: next, control: centre) }
        star.closeSubpath()
        return disc.subtracting(star)
    }
}

/// The capture button as the owner approved it (UIUX arrive-approved, 2026-11-12), from `ShutterGeometry`: a 64 pt
/// button, the ring of radius 31 drawn 1.5 wide at 55 percent, the 52 pt disc at its centre with the star cut out.
/// Pressed, the disc goes to 90 percent in 0.5 s and the star turns 45 degrees in 1.2 s, on the approved curve; with
/// Reduce Motion on, the pressed state shows with no transition. Colour: the system's primary (black on a light
/// screen) on this plain check screen [proposed], where the approved screens draw it in ink on black.
struct CaptureButton: View {
    let action: () -> Void
    var label: String = "Take photo"

    var body: some View {
        Button(action: action) { EmptyView() }
            .buttonStyle(CaptureButtonStyle())
            .accessibilityLabel(label)
    }
}

struct CaptureButtonStyle: ButtonStyle {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private func curve(_ seconds: Double) -> Animation? {
        let c = ShutterGeometry.curve
        return reduceMotion ? nil : .timingCurve(c.0, c.1, c.2, c.3, duration: seconds)
    }

    func makeBody(configuration: Configuration) -> some View {
        let pressed = configuration.isPressed
        let side = CGFloat(ShutterGeometry.side)
        let disc = CGFloat(2 * ShutterGeometry.discRadius)
        return ZStack {
            Circle()
                .stroke(Color.primary.opacity(ShutterGeometry.ringOpacity), lineWidth: CGFloat(ShutterGeometry.ringLineWidth))
                .frame(width: CGFloat(2 * ShutterGeometry.ringRadius), height: CGFloat(2 * ShutterGeometry.ringRadius))
            ShutterDiscShape(starTurn: pressed ? ShutterGeometry.pressedStarTurnDegrees : 0)
                .fill(Color.primary)
                .animation(curve(ShutterGeometry.starSeconds), value: pressed)
                .frame(width: disc, height: disc)
                .scaleEffect(pressed ? CGFloat(ShutterGeometry.pressedDiscScale) : 1)
                .animation(curve(ShutterGeometry.discSeconds), value: pressed)
        }
        .frame(width: side, height: side)
        .contentShape(Circle())
    }
}
