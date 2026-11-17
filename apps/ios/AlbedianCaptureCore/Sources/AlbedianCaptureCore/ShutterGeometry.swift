import Foundation

/// The approved capture button's own numbers (UIUX, arrive-approved 2026-11-12, the board's `ab-shut`: a 64 unit view,
/// in points on the phone), so the app draws it from them and a test holds them: a ring of radius 31 drawn 1.5 wide at
/// 55 percent ink; a disc of radius 26 (52 pt across) at the same centre; Albedian's star cut out of the disc, the
/// logo's star scaled 2.26 times about the logo moon's centre (15.2, 16), so the moon's centre is the disc's. Pressed,
/// the disc goes to 90 percent in 0.5 s and the star turns 45 degrees about its own centre in 1.2 s, both on the
/// curve (0.175, 1.4, 0.32, 1). Coordinates are in points, from the button's centre, y down.
public enum ShutterGeometry {
    public static let side = 64.0
    public static let ringRadius = 31.0
    public static let ringLineWidth = 1.5
    public static let ringOpacity = 0.55
    public static let discRadius = 26.0
    public static let starScale = 2.26
    public static let pressedDiscScale = 0.9
    public static let pressedStarTurnDegrees = 45.0
    public static let discSeconds = 0.5
    public static let starSeconds = 1.2
    public static let curve = (0.175, 1.4, 0.32, 1.0)

    /// The logo's moon centre, in its 32 unit drawing.
    static let moonCentreInLogo = (x: 15.2, y: 16.0)
    /// The logo's star: its four points from the top, clockwise, each joined to the next by a quadratic curve whose
    /// control point is the star's centre (`M18.2 2.2 Q18.2 13.8 29.8 13.8 ...` in the logo's drawing).
    static let starPointsInLogo = [(x: 18.2, y: 2.2), (x: 29.8, y: 13.8), (x: 18.2, y: 25.4), (x: 6.6, y: 13.8)]
    static let starCentreInLogo = (x: 18.2, y: 13.8)

    /// A point of the logo's drawing, in the button's points.
    public static func fromLogo(x: Double, y: Double) -> (x: Double, y: Double) {
        ((x - moonCentreInLogo.x) * starScale, (y - moonCentreInLogo.y) * starScale)
    }

    /// The star's centre, in the button's points.
    public static var starCentre: (x: Double, y: Double) { fromLogo(x: starCentreInLogo.x, y: starCentreInLogo.y) }

    /// The star's four points turned by `degrees` (clockwise on the screen) about the star's own centre.
    public static func starPoints(turnedBy degrees: Double) -> [(x: Double, y: Double)] {
        let centre = starCentre
        let angle = degrees * .pi / 180
        return starPointsInLogo.map { point in
            let p = fromLogo(x: point.x, y: point.y)
            let dx = p.x - centre.x, dy = p.y - centre.y
            return (centre.x + dx * cos(angle) - dy * sin(angle), centre.y + dx * sin(angle) + dy * cos(angle))
        }
    }
}
