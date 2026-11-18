import AlbedianCaptureCore
import SwiftUI

/// The logo's mark: the moon (a circle of radius 11.5 at (15.2, 16) in the logo's 32 unit drawing) with Albedian's
/// star cut out of it, drawn from the same numbers as the capture button's disc (`ShutterGeometry`).
struct LogoMarkShape: Shape {
    func path(in rect: CGRect) -> Path {
        let scale = min(rect.width, rect.height) / 32
        let origin = CGPoint(x: rect.midX - 16 * scale, y: rect.midY - 16 * scale)
        func place(_ x: Double, _ y: Double) -> CGPoint { CGPoint(x: origin.x + x * scale, y: origin.y + y * scale) }
        let moon = Path(ellipseIn: CGRect(x: origin.x + (15.2 - 11.5) * scale, y: origin.y + (16 - 11.5) * scale, width: 23 * scale, height: 23 * scale))
        // The star's points and centre in the logo's units, back from the button's (`fromLogo` divided out)
        let points = ShutterGeometry.starPoints(turnedBy: 0).map { place($0.x / ShutterGeometry.starScale + 15.2, $0.y / ShutterGeometry.starScale + 16) }
        let centre = place(18.2, 13.8)
        var star = Path()
        star.move(to: points[0])
        for next in points.dropFirst() + [points[0]] { star.addQuadCurve(to: next, control: centre) }
        star.closeSubpath()
        return moon.subtracting(star)
    }
}

/// The name, always as the website's logo (`ControlStyle`): "albedian" in white Geist 600, the mark at its shoulder.
struct Logo: View {
    var size: Double = ControlStyle.logoDefaultSize

    var body: some View {
        let mark = size * ControlStyle.logoMarkScale
        HStack(alignment: .top, spacing: 0) {
            Text(ControlStyle.logoWord)
                .font(AlbedianFont.semibold(size))
                .tracking(CGFloat(ControlStyle.logoLetterSpacingEm * size))
            LogoMarkShape()
                .fill(Color(ControlStyle.logoInk))
                .frame(width: CGFloat(mark), height: CGFloat(mark))
                .padding(.leading, CGFloat(ControlStyle.logoMarkLeft * size))
                .offset(y: CGFloat(ControlStyle.logoMarkUp * size))
        }
        .foregroundStyle(Color(ControlStyle.logoInk))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Albedian")
        .accessibilityAddTraits(.isImage)
    }
}
