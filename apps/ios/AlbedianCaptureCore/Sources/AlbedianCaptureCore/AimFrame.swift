import Foundation

/// largertext-m1 (UIUX-iphone-largertext-READY, Aim re-ruled 24 Nov 15:52, DECIDED-BY-LEAD): at accessibility sizes the
/// aiming preview and its rings are drawn whole in a smaller 3:4 frame, its top kept at 118 pt and centred across, as tall
/// as what is left above the column under it, so the column stands readable under the frame and clear of the capture
/// button. The frame is sized once for the text size, from the most the column can show, never from the lines shown.
public enum AimFrame {
    /// The preview's top on every screen (arrive iPhone 2).
    public static let top = 118.0
    /// Between the frame's foot and the column, and between the column's foot and the capture button.
    public static let gap = 16.0

    /// The preview's frame: at default sizes (`columnRoom` nil) the screen's width and 4:3 of it; at accessibility sizes,
    /// given the room the column needs, the tallest 3:4 frame whose foot leaves that room and both gaps above the capture
    /// button's top, never wider than the screen and never less than nothing.
    public static func size(screenWidth: Double, captureButtonTop: Double, columnRoom: Double?) -> (width: Double, height: Double) {
        guard let columnRoom else { return (screenWidth, screenWidth * 4 / 3) }
        let height = min(screenWidth * 4 / 3, max(0, captureButtonTop - gap - columnRoom - gap - top))
        return (height * 3 / 4, height)
    }

    /// The most the column can show at once, which the frame keeps room for: the longest source line the sky context's
    /// sources make (both catalog releases and the ephemeris, at the heading accuracy limit) and the coach's two longest
    /// lines together (the compass, and the Sun at 18 degrees, the deepest twilight). Measured hidden; never shown.
    public static let mostLine = AimLine.source(["Hipparcos 2007", "Gaia DR3", SkyOverlayWords.ephemeris,
                                                 "heading ±\(Int(PointingContract.headingAccuracyLimitDeg))°"].joined(separator: " · "))
    public static let mostCoach: [CoachLine] = [.compass, .sun("The Sun is 18 degrees below the horizon: still twilight.")]
}
