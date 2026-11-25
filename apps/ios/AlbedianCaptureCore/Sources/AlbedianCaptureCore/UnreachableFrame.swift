import Foundation

/// largertext-unreachable-m (UIUX-iphone-largertext-sweep-READY item 4, DECIDED-BY-LEAD 24 Nov 19:56): at accessibility
/// sizes "Can't reach the laptop" stands in one column: the photo whole in a smaller 3:4 frame from 118, centred, as
/// tall as what is left above the lines and the stacked buttons, never less than 160 pt tall; past that floor the
/// column scrolls. Nothing is wider than the screen. Sized once for the text size from the column measured whole.
public enum UnreachableFrame {
    /// The photo's top on every screen (arrive iPhone 2).
    public static let top = 118.0
    /// Between the photo's foot and the first line, as at the default size (UIUX-unreachable).
    public static let gap = 16.0
    /// Between the second line and Try again (the record's picture).
    public static let linesToButtons = 20.0
    /// Between Try again and Change address (the record).
    public static let betweenButtons = 12.0
    /// The room kept at the screen's foot (the record's picture).
    public static let foot = 34.0
    /// The photo is never drawn less tall than this; the column scrolls instead.
    public static let floor = 160.0

    /// The photo's frame: at default sizes (`columnRoom` nil) the screen's width and 4:3 of it; at accessibility sizes,
    /// given the room the lines and the buttons need, the tallest 3:4 frame that leaves it, the gap and the foot, never
    /// wider than the screen and never less tall than the floor.
    public static func size(screenWidth: Double, screenHeight: Double, columnRoom: Double?) -> (width: Double, height: Double) {
        guard let columnRoom else { return (screenWidth, screenWidth * 4 / 3) }
        let height = min(screenWidth * 4 / 3, max(floor, screenHeight - foot - columnRoom - gap - top))
        return (height * 3 / 4, height)
    }

    /// Whether the column is taller than the screen, so it scrolls.
    public static func scrolls(screenHeight: Double, photoHeight: Double, columnRoom: Double) -> Bool {
        top + photoHeight + gap + columnRoom + foot > screenHeight + 1e-9
    }
}
