import Foundation

/// The capture coach (D29, UIUX-coach-DECIDED-BY-LEAD.md, decided by the lead under the owner's delegation; build note
/// UIUX-coach-READY.md): up to two plain lines under the aiming screen's source line, each from real data the phone has
/// or the laptop's sky context gives. Nothing here is invented: no line without its data.
public enum CoachLine: Equatable, Sendable {
    /// iOS itself asked for a heading calibration.
    case compass
    /// The Moon, from the sky context (`SkyConditions`).
    case moon(String)
    /// The Sun in twilight, from the sky context (skycontext-3); it takes the Moon line's place while it shows.
    case sun(String)
    /// The phone moves enough to smear the stars in the coming exposure.
    case steadiness

    public var words: String {
        switch self {
        case .compass: "Move the phone in a figure eight to settle the compass."
        case .moon(let words): words
        case .sun(let words): words
        case .steadiness: "The phone is moving: rest it on something steady."
        }
    }

    /// Phosphor's icon for the line (moon, sun-horizon, hand, compass).
    public var icon: String {
        switch self {
        case .compass: "compass"
        case .moon: "moon"
        case .sun: "sun-horizon"
        case .steadiness: "hand"
        }
    }
}

public enum Coach {
    /// At most this many lines at once.
    public static let maxLines = 2

    /// The Moon's line, or none without a sky context: up and adding light, up with none, or down.
    public static func moonWords(_ conditions: SkyConditions?) -> String? {
        guard let conditions else { return nil }
        guard conditions.moonAltDeg > 0 else { return "The Moon is down." }
        guard conditions.lunarVeilDeltaMag > 0 else { return "The Moon is up but adds no light to the sky." }
        let lit = Int((conditions.moonIlluminationFraction * 100).rounded())
        return "The Moon is up, \(lit) percent lit: fewer stars show."
    }

    /// The Sun's line while it is in one of the three twilights (D29 item 5, LEAD ANSWER 12:44): its depth below the
    /// horizon rounded to a whole degree as services/science/predicted_sky.py `sky_phase` rounds it, "1 degree" in the
    /// singular as it writes it; none by day, none at night, none without the Sun in the sky context.
    public static func sunWords(_ conditions: SkyConditions?) -> String? {
        guard let sun = conditions?.sun,
              [.civilTwilight, .nauticalTwilight, .astronomicalTwilight].contains(sun.band) else { return nil }
        let whole = Int((abs(sun.altDeg) + 0.5).rounded(.down))
        return "The Sun is \(whole) \(whole == 1 ? "degree" : "degrees") below the horizon: still twilight."
    }

    /// The lines in the record's order (compass, then the Sun or else the Moon, then steadiness), the compass taking
    /// steadiness's place and the Sun the Moon's while each shows, at most two.
    public static func lines(compassAsked: Bool, conditions: SkyConditions?, moving: Bool) -> [CoachLine] {
        var out: [CoachLine] = []
        if compassAsked { out.append(.compass) }
        if let sun = sunWords(conditions) {
            out.append(.sun(sun))
        } else if let moon = moonWords(conditions) {
            out.append(.moon(moon))
        }
        if moving && !compassAsked { out.append(.steadiness) }
        return Array(out.prefix(maxLines))
    }
}

/// How far the stars would smear in the coming exposure, from the phone's own motion (D29 item 3): the rotation rate's
/// size (radians per second, bias removed, as the pointing record reads it) times the exposure's length (seconds) times
/// the lens's focal length in pixels.
public enum CoachSteadiness {
    /// The smear past which the line shows, in pixels.
    public static let limitPx = 1.0
    /// [proposed in the build note] The window: shown while any sample of the last half second smears past the limit,
    /// so it hides once the smear has stayed below it for half a second.
    public static let windowSec = 0.5

    public static func smearPx(rotationRateRadPerSec r: PointingRecord.Vector3, exposureSec: Double, focalPx: Double) -> Double? {
        let rate = (r.x * r.x + r.y * r.y + r.z * r.z).squareRoot()
        guard rate.isFinite, exposureSec.isFinite, exposureSec > 0, focalPx.isFinite, focalPx > 0 else { return nil }
        return rate * exposureSec * focalPx
    }

    /// The focal length in pixels of an image `longSidePx` wide across a field of view of `longSideFovDeg`.
    public static func focalPx(longSidePx: Double, longSideFovDeg: Double) -> Double? {
        guard longSidePx > 0, longSidePx.isFinite, longSideFovDeg > 0, longSideFovDeg < 180 else { return nil }
        return longSidePx / 2 / tan(longSideFovDeg * .pi / 360)
    }

    /// The smears of the last half second, each at its time on one clock.
    public struct Window: Sendable {
        private var samples: [(sec: Double, smearPx: Double)] = []
        public init() {}

        public mutating func add(sec: Double, smearPx: Double) {
            guard sec.isFinite, smearPx.isFinite else { return }
            samples.append((sec, smearPx))
            samples.removeAll { $0.sec <= sec - windowSec }
        }

        /// Whether the line shows at `sec`: some sample within the last half second smeared past the limit.
        public func moving(at sec: Double) -> Bool {
            samples.contains { $0.sec > sec - windowSec && $0.sec <= sec && $0.smearPx > limitPx }
        }
    }
}

extension CoachSteadiness {
    /// Whether the steadiness line shows: some sample of the last half second before the newest smears past the limit
    /// in an exposure this long through a lens this long. False without samples, an exposure or a focal length.
    public static func moving(_ samples: [MotionSample], exposureSec: Double?, focalPx: Double?) -> Bool {
        guard let exposureSec, let focalPx, let newest = samples.last else { return false }
        var window = Window()
        for sample in samples {
            if let smear = smearPx(rotationRateRadPerSec: sample.rotationRateRadPerSec, exposureSec: exposureSec, focalPx: focalPx) {
                window.add(sec: sample.bootClockSec, smearPx: smear)
            }
        }
        return window.moving(at: newest.bootClockSec)
    }
}
