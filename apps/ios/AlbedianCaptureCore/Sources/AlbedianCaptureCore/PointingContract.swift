/// The pointing record contract (spec `ios-capture-companion`, REQ-IOS-1.2, 1.3, 2, 6 and 7) as the phone writes
/// it: the third reading of `PointingRecordSchema` (packages/contracts/src/pointing.ts) and `PointingRecord`
/// (services/contracts/pointing.py). Every limit, pattern, list and sentence here is the same as theirs, and the tests
/// compare each with the shared fixture `tests/fixtures/pointing_contract.json`, so a change on one side only fails a
/// test. The reasons for each limit, and the Apple documentation each field rests on, are written in pointing.ts.
public enum PointingContract {
    public static let format = "albedian.pointing-record"
    public static let formatVersion = 1

    /// The largest heading accuracy a record keeps a heading with, in degrees [proposed in pointing.ts: the
    /// triangle solver's 68 deg cut less the main camera's 42.03 deg half diagonal, to the whole degree below].
    public static let headingAccuracyLimitDeg = 25.0
    /// How far from the photo's timestamp the motion sample may lie, in seconds [proposed in pointing.ts: five
    /// periods at 100 Hz].
    public static let motionSampleOffsetLimitSec = 0.05
    /// How far the attitude quaternion's norm may lie from one [proposed in pointing.ts].
    public static let unitQuaternionTolerance = 1e-6
    /// The band gravity's length must lie in, in g [proposed in pointing.ts; mobile-1 measures it on a device].
    public static let gravityLengthMinG = 0.9
    public static let gravityLengthMaxG = 1.1
    /// The largest side of a photo or camera format, in pixels, and the most samples a motion window holds
    /// [proposed in pointing.ts]: bounds that keep every whole number a safe integer.
    public static let dimensionMaxPx = 32768
    public static let sampleCountMax = 360_000
    /// The longest hardware model, in UTF-16 units [proposed in pointing.ts].
    public static let labelMaxUTF16 = 32
    /// The longest system version, in UTF-16 units: what its pattern allows, three groups of four digits and two dots.
    public static let systemVersionMaxUTF16 = 14

    /// The two device labels' patterns, as the other two readings write them. Swift matches them by code in
    /// `PointingLabels`, with the same meaning (ASCII only, anchored at both ends, no trailing newline).
    public static let hardwareModelPattern = "^[A-Za-z]+[0-9]+,[0-9]+$"
    public static let systemVersionPattern = "^[0-9]{1,4}(\\.[0-9]{1,4}){0,2}$"

    /// The record's fixed evidence statement.
    public static let statement =
        "The phone sensors say where the phone pointed. This record is a hint for the solvers and the basis of an approximate pose, never a measurement of the sky: anything placed from it is predicted, and a finding becomes catalog_matched only through held-out verification."

    /// The contract's own rules, in the words both other readings refuse them in.
    public enum Rule: String, CaseIterable, Sendable {
        case headingNeedsPlace
        case headingInsideLimit
        case noPlaceReasonNeedsNoPlace
        case outsideLimitCarriesAccuracy
        case onlyOutsideLimitReportsAccuracy
        case unitQuaternion
        case gravityNearOneG
        case sampleAtThePhoto
        case windowRunsForward
        case placeholderSha256

        public var words: String {
            switch self {
            case .headingNeedsPlace:
                "A heading needs the place: true north is found from the device location, so a record with no place carries no heading."
            case .headingInsideLimit:
                "A measured heading has an accuracy inside the limit; outside it, the record says there is no heading and why."
            case .noPlaceReasonNeedsNoPlace:
                "No heading for want of a place needs a record with no place."
            case .outsideLimitCarriesAccuracy:
                "A heading left out for its accuracy carries the accuracy the device reported, and that accuracy is outside the limit."
            case .onlyOutsideLimitReportsAccuracy:
                "Only a heading left out for its accuracy carries a reported accuracy."
            case .unitQuaternion:
                "The attitude is a unit quaternion, as Core Motion reports it."
            case .gravityNearOneG:
                "Gravity is given in g, so its length lies inside the stated band around one g: a zero vector, or one written in metres per second squared, is refused."
            case .sampleAtThePhoto:
                "The motion sample is the one at the photo: its time lies within the limit of the photo timestamp."
            case .windowRunsForward:
                "The motion window runs forward: it ends no earlier than it starts."
            case .placeholderSha256:
                "A SHA-256 of 64 zeros is a placeholder, not a hash."
            }
        }
    }
}

/// AVCaptureDevice.position of the camera that took the photo.
public enum CameraPosition: String, CaseIterable, Sendable {
    case back
    case front
}

/// The physical camera that took the photo; a virtual camera is recorded as its active primary constituent.
public enum CameraDeviceType: String, CaseIterable, Sendable {
    case wideAngle = "wide_angle"
    case ultraWide = "ultra_wide"
    case telephoto
}

/// The CLDeviceOrientation that sets the heading's reference axis; faceUp, faceDown and unknown set none, so they
/// are not here: the app records the orientation Core Location measured along.
public enum HeadingOrientation: String, CaseIterable, Sendable {
    case portrait
    case portraitUpsideDown = "portrait_upside_down"
    case landscapeLeft = "landscape_left"
    case landscapeRight = "landscape_right"
}

/// The wire names of the reasons a record has no heading, in the contract's order.
public enum NoHeadingReasonName: String, CaseIterable, Sendable {
    case noMagnetometer = "no_magnetometer"
    case noPlace = "no_place"
    case noHeadingEvent = "no_heading_event"
    case headingInvalid = "heading_invalid"
    case accuracyOutsideLimit = "accuracy_outside_limit"
}

/// Why a record has no place.
public enum NoPlaceReason: String, CaseIterable, Sendable {
    case notAllowed = "not_allowed"
    case noFix = "no_fix"
    case simulatedBySoftware = "simulated_by_software"
}

/// CLAccuracyAuthorization.
public enum AccuracyAuthorization: String, CaseIterable, Sendable {
    case full
    case reduced
}

/// The two device labels, matched by code to the contract's patterns: ASCII letters and digits only, so a name a
/// person gave the device never fits, and a length counts the same in UTF-16 units and in code points.
public enum PointingLabels {
    /// `^[A-Za-z]+[0-9]+,[0-9]+$` at most `labelMaxUTF16` units long, as `utsname.machine` gives it ("iPhone18,1").
    public static func isHardwareModel(_ text: JSONString) -> Bool {
        let units = text.units
        guard units.count <= PointingContract.labelMaxUTF16 else { return false }
        var index = 0
        func run(_ accepts: (UInt16) -> Bool) -> Int {
            let start = index
            while index < units.count, accepts(units[index]) { index += 1 }
            return index - start
        }
        guard run(isLetter) >= 1, run(isDigit) >= 1, index < units.count, units[index] == 0x2C else { return false }
        index += 1
        return run(isDigit) >= 1 && index == units.count
    }

    /// `^[0-9]{1,4}(\.[0-9]{1,4}){0,2}$`, as `UIDevice.systemVersion` gives it ("26.5").
    public static func isSystemVersion(_ text: JSONString) -> Bool {
        // The stated maximum, as the other two readings state it; the pattern below bounds it at the same 14
        guard text.units.count <= PointingContract.systemVersionMaxUTF16 else { return false }
        let groups = text.units.split(separator: 0x2E, omittingEmptySubsequences: false)
        return (1...3).contains(groups.count) && groups.allSatisfy { (1...4).contains($0.count) && $0.allSatisfy(isDigit) }
    }

    private static func isDigit(_ unit: UInt16) -> Bool { (0x30...0x39).contains(unit) }
    private static func isLetter(_ unit: UInt16) -> Bool { (0x41...0x5A).contains(unit) || (0x61...0x7A).contains(unit) }

    public static func isHardwareModel(_ text: String) -> Bool { isHardwareModel(JSONString(text)) }
    public static func isSystemVersion(_ text: String) -> Bool { isSystemVersion(JSONString(text)) }
}
