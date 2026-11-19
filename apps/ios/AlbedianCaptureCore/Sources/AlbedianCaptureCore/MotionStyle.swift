import Foundation

/// The iPhone's motion inside a screen (MOTION-READY-iphone, decided by the lead under the owner's delegation of 19
/// November; measured sources in `.lead/uiux/motion/measurements/`): each named timing, in one place. Between screens the
/// system's own push and sheet move, unstyled; these are only what changes inside a screen.
public enum MotionStyle {
    public struct Timing: Equatable, Sendable {
        /// The cubic-bezier control points.
        public let x1, y1, x2, y2: Double
        public let seconds: Double
        /// Waited before it starts.
        public let delaySeconds: Double

        init(_ curve: (Double, Double, Double, Double), _ seconds: Double, delay: Double = 0) {
            (x1, y1, x2, y2) = curve
            self.seconds = seconds
            self.delaySeconds = delay
        }
    }

    static let standard = (0.4, 0.0, 0.2, 1.0)
    static let springCurve = (0.175, 1.4, 0.32, 1.0)

    /// The approved spring: the capture button's star turning, the main button's circle and arrow.
    public static let spring = Timing(springCurve, 1.2)
    /// The capture disc going to 90 percent while pressed.
    public static let disc = Timing(springCurve, 0.5)
    /// A fill, a control becoming current.
    public static let colour = Timing(standard, 0.2)
    /// A line of words arriving: from 6 pt below, faded in, after 0.12 s.
    public static let wordsIn = Timing(standard, 0.3, delay: 0.12)
    /// A line leaving: faded out toward 6 pt below.
    public static let wordsOut = Timing(standard, 0.1)
    /// An overlay or a block arriving, and leaving.
    public static let frameIn = Timing((0.16, 1.0, 0.3, 1.0), 0.4)
    public static let frameOut = Timing((0.4, 0.0, 1.0, 1.0), 0.3)
    /// How far a line travels arriving or leaving, in points.
    public static let wordsOffsetPt = 6.0
    /// Between two lines of a list.
    public static let staggerSeconds = 0.02
    /// With Reduce Motion on: an in-screen change is this dissolve, opacity alone, never nothing, so it is still seen.
    public static let reduceMotionSeconds = 0.27
    /// The capture pressed: the aiming pieces leave and the hold-still ones arrive this long after the press.
    public static let captureSequenceDelaySeconds = 0.15

    /// The wait before line `index` of `count` moves: in order arriving, last to first leaving.
    public static func staggerDelay(index: Int, count: Int, leaving: Bool) -> Double {
        guard count > 0, (0..<count).contains(index) else { return 0 }
        return Double(leaving ? count - 1 - index : index) * staggerSeconds
    }
}

/// Where the companion's screens sit in the system's navigation stack (MOTION-READY-iphone section 1: forward screens by
/// NavigationStack's own push, unstyled). Home is the root; aiming, holding still and after the shot are one place (the
/// capture), as the wait and its result are another (sent), so a change between them is a change inside a screen.
public enum CompanionPlace: Hashable, Sendable {
    case credits, kept, capture, pairing, sent

    /// The path after moving to `place` (nil for home): back to it when it is already on the path, which the system shows as a
    /// pop; pushed on top when it is not; empty for home.
    public static func path(_ path: [CompanionPlace], to place: CompanionPlace?) -> [CompanionPlace] {
        guard let place else { return [] }
        if let at = path.firstIndex(of: place) { return Array(path[...at]) }
        return path + [place]
    }
}
