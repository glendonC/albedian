import Foundation

/// One reason a record is refused: where, and in what words. The contract's own rules use the words of
/// `PointingContract.Rule`, the same words the other two readings use.
public struct PointingIssue: Equatable, Sendable, CustomStringConvertible {
    public let path: [String]
    public let message: String
    public init(path: [String], message: String) {
        self.path = path
        self.message = message
    }
    public var description: String { (path.isEmpty ? "record" : path.joined(separator: ".")) + ": " + message }
}

/// The third reading of the pointing record contract: judges wire JSON exactly as `PointingRecordSchema` (Zod) and
/// `PointingRecord` (Pydantic) judge it, on the same samples (tests/fixtures/pointing_contract.json).
///
/// - Every object takes exactly its keys (camelCase, no Python field name, no unknown key, at every level); an
///   optional field is absent, never null.
/// - Every number is a JSON number (never a boolean or a string) and finite; a whole-number field takes a whole
///   double (so 4032.0 and 4.032e3 are 4032) inside its stated bound, which keeps it a safe integer; minus zero is
///   zero. A number's value is the double JSON.parse reads, so 1e400 is infinity and is refused.
/// - Every text is a closed list, a literal or an exact ASCII pattern, compared unit by unit in UTF-16, so a lone
///   surrogate, a control character or a character outside the basic plane never fits.
/// - The contract's own rules run, as in the Pydantic mirror, once every field has passed, and each refuses in its
///   own words. (Zod also runs them beside some field faults; either way the verdict is the same.)
public enum PointingValidator {
    /// Every reason the record is refused; empty when it is accepted.
    public static func issues(in value: JSONValue) -> [PointingIssue] {
        var check = Checker()
        guard let record = check.object(value, [], keys: ["format", "formatVersion", "sourceAssetSha256", "capturedAtDeviceClockUTC",
                                                          "device", "camera", "motion", "heading", "location", "evidence"]) else {
            return check.issues
        }
        check.literal(record["format"], ["format"], PointingContract.format)
        if let version = check.number(record["formatVersion"], ["formatVersion"], .any), version != Double(PointingContract.formatVersion) {
            check.add(["formatVersion"], "Invalid literal value, expected \(PointingContract.formatVersion)")
        }
        check.text(record["sourceAssetSha256"], ["sourceAssetSha256"], "Invalid SHA-256: 64 lower-case hex characters") { text in
            text.units.count == 64 && text.units.allSatisfy { (0x30...0x39).contains($0) || (0x61...0x66).contains($0) }
        }
        if case .string(let sha)? = record["sourceAssetSha256"], !sha.units.isEmpty, sha.units.allSatisfy({ $0 == 0x30 }) {
            check.add(["sourceAssetSha256"], PointingContract.Rule.placeholderSha256.words)
        }
        check.text(record["capturedAtDeviceClockUTC"], ["capturedAtDeviceClockUTC"],
                   "Invalid instant: UTC with a trailing Z on a calendar day from year 1", UTCInstant.isValid)
        device(record["device"], &check)
        camera(record["camera"], &check)
        let motion = motion(record["motion"], &check)
        let heading = heading(record["heading"], &check)
        let place = place(record["location"], &check)
        if let evidence = check.object(record["evidence"], ["evidence"], keys: ["role", "statement"]) {
            check.literal(evidence["role"], ["evidence", "role"], "pointing_hint")
            check.literal(evidence["statement"], ["evidence", "statement"], PointingContract.statement)
        }
        if check.issues.isEmpty, let motion, let heading, let place {
            rules(motion: motion, heading: heading, placed: place, &check)
        }
        return check.issues
    }

    /// Reads wire bytes and judges them; text that is not JSON is refused with its reading error.
    public static func issues(inWire data: Data) -> [PointingIssue] {
        do {
            return issues(in: try JSONText.read(data))
        } catch {
            return [PointingIssue(path: [], message: error.description)]
        }
    }

    public static func accepts(_ value: JSONValue) -> Bool { issues(in: value).isEmpty }

    // MARK: The contract's own rules, in the order of the Zod superRefine

    private struct MotionFacts {
        var sampleOffset: Double
        var gravity: PointingRecord.Vector3
        var windowStart: Double
        var windowEnd: Double
    }

    private enum HeadingFacts {
        case measured(accuracy: Double)
        case none(reason: NoHeadingReasonName, reported: Double?)
    }

    private static func rules(motion: MotionFacts, heading: HeadingFacts, placed: Bool, _ check: inout Checker) {
        let limit = PointingContract.headingAccuracyLimitDeg
        switch heading {
        case .measured(let accuracy):
            // True north is computed from the place (xTrueNorthZVertical, CLHeading.trueHeading)
            if !placed { check.add(["heading"], PointingContract.Rule.headingNeedsPlace.words) }
            if accuracy > limit { check.add(["heading", "headingAccuracyDeg"], PointingContract.Rule.headingInsideLimit.words) }
        case .none(let reason, let reported):
            if reason == .noPlace, placed { check.add(["heading", "reason"], PointingContract.Rule.noPlaceReasonNeedsNoPlace.words) }
            if reason == .accuracyOutsideLimit {
                if reported == nil || reported! <= limit {
                    check.add(["heading", "reportedHeadingAccuracyDeg"], PointingContract.Rule.outsideLimitCarriesAccuracy.words)
                }
            } else if reported != nil {
                check.add(["heading", "reportedHeadingAccuracyDeg"], PointingContract.Rule.onlyOutsideLimitReportsAccuracy.words)
            }
        }
        let length = motion.gravity.length
        if length < PointingContract.gravityLengthMinG || length > PointingContract.gravityLengthMaxG {
            check.add(["motion", "gravityDeviceG"], PointingContract.Rule.gravityNearOneG.words)
        }
        if abs(motion.sampleOffset) > PointingContract.motionSampleOffsetLimitSec {
            check.add(["motion", "sampleOffsetFromPhotoSec"], PointingContract.Rule.sampleAtThePhoto.words)
        }
        if motion.windowEnd < motion.windowStart { check.add(["motion", "window"], PointingContract.Rule.windowRunsForward.words) }
    }

    // MARK: The fields

    private static func device(_ value: JSONValue?, _ check: inout Checker) {
        guard let device = check.object(value, ["device"], keys: ["hardwareModel", "systemVersion"]) else { return }
        check.text(device["hardwareModel"], ["device", "hardwareModel"], "Invalid hardware model", PointingLabels.isHardwareModel)
        check.text(device["systemVersion"], ["device", "systemVersion"], "Invalid system version", PointingLabels.isSystemVersion)
    }

    private static func camera(_ value: JSONValue?, _ check: inout Checker) {
        let path = ["camera"]
        guard let camera = check.object(value, path, keys: ["position", "deviceType", "activeFormatDimensionsPx", "photoDimensionsPx",
                                                            "fieldOfView", "videoZoomFactor", "contentAwareDistortionCorrection"]) else { return }
        check.oneOf(camera["position"], path + ["position"], CameraPosition.allCases.map(\.rawValue))
        check.oneOf(camera["deviceType"], path + ["deviceType"], CameraDeviceType.allCases.map(\.rawValue))
        for key in ["activeFormatDimensionsPx", "photoDimensionsPx"] {
            guard let dimensions = check.object(camera[key], path + [key], keys: ["width", "height"]) else { continue }
            for side in ["width", "height"] {
                check.number(dimensions[side], path + [key, side], .wholeCount(max: Double(PointingContract.dimensionMaxPx)))
            }
        }
        switch check.state(camera["fieldOfView"], path + ["fieldOfView"], ["reported", "unknown"]) {
        case "reported":
            let fieldOfView = camera["fieldOfView"]!.objectValue!
            check.keys(fieldOfView, path + ["fieldOfView"], ["state", "horizontalDeg", "distortionCorrected"])
            check.number(fieldOfView["horizontalDeg"], path + ["fieldOfView", "horizontalDeg"], .range(above: 0, below: 180))
            check.boolean(fieldOfView["distortionCorrected"], path + ["fieldOfView", "distortionCorrected"])
        case "unknown":
            check.keys(camera["fieldOfView"]!.objectValue!, path + ["fieldOfView"], ["state"])
        default:
            break
        }
        check.number(camera["videoZoomFactor"], path + ["videoZoomFactor"], .finite(atLeast: 1))
        check.boolean(camera["contentAwareDistortionCorrection"], path + ["contentAwareDistortionCorrection"])
    }

    private static func vector(_ value: JSONValue?, _ path: [String], _ check: inout Checker) -> PointingRecord.Vector3? {
        guard let vector = check.object(value, path, keys: ["x", "y", "z"]) else { return nil }
        let x = check.number(vector["x"], path + ["x"], .finite)
        let y = check.number(vector["y"], path + ["y"], .finite)
        let z = check.number(vector["z"], path + ["z"], .finite)
        guard let x, let y, let z else { return nil }
        return PointingRecord.Vector3(x: x, y: y, z: z)
    }

    private static func motion(_ value: JSONValue?, _ check: inout Checker) -> MotionFacts? {
        let path = ["motion"]
        guard let motion = check.object(value, path, keys: ["sampleOffsetFromPhotoSec", "gravityDeviceG", "rotationRateDeviceRadPerSec",
                                                            "window"]) else { return nil }
        let offset = check.number(motion["sampleOffsetFromPhotoSec"], path + ["sampleOffsetFromPhotoSec"], .finite)
        let gravity = vector(motion["gravityDeviceG"], path + ["gravityDeviceG"], &check)
        _ = vector(motion["rotationRateDeviceRadPerSec"], path + ["rotationRateDeviceRadPerSec"], &check)
        var start: Double?
        var end: Double?
        if let window = check.object(motion["window"], path + ["window"], keys: ["startOffsetSec", "endOffsetSec", "sampleCount",
                                                                                 "maxRotationRateRadPerSec"]) {
            start = check.number(window["startOffsetSec"], path + ["window", "startOffsetSec"], .finite)
            end = check.number(window["endOffsetSec"], path + ["window", "endOffsetSec"], .finite)
            check.number(window["sampleCount"], path + ["window", "sampleCount"], .wholeCount(max: Double(PointingContract.sampleCountMax)))
            check.number(window["maxRotationRateRadPerSec"], path + ["window", "maxRotationRateRadPerSec"], .finite(atLeast: 0))
        }
        guard let offset, let gravity, let start, let end else { return nil }
        return MotionFacts(sampleOffset: offset, gravity: gravity, windowStart: start, windowEnd: end)
    }

    private static func heading(_ value: JSONValue?, _ check: inout Checker) -> HeadingFacts? {
        let path = ["heading"]
        switch check.state(value, path, ["measured", "none"]) {
        case "measured":
            let heading = value!.objectValue!
            check.keys(heading, path, ["state", "attitude", "trueHeadingDeg", "headingAccuracyDeg", "headingOrientation", "eventOffsetFromPhotoSec"])
            if let attitude = check.object(heading["attitude"], path + ["attitude"], keys: ["referenceFrame", "quaternion"]) {
                check.literal(attitude["referenceFrame"], path + ["attitude", "referenceFrame"], "x_true_north_z_vertical")
                quaternion(attitude["quaternion"], path + ["attitude", "quaternion"], &check)
            }
            check.number(heading["trueHeadingDeg"], path + ["trueHeadingDeg"], .range(from: 0, below: 360))
            let accuracy = check.number(heading["headingAccuracyDeg"], path + ["headingAccuracyDeg"], .finite(atLeast: 0))
            check.oneOf(heading["headingOrientation"], path + ["headingOrientation"], HeadingOrientation.allCases.map(\.rawValue))
            check.number(heading["eventOffsetFromPhotoSec"], path + ["eventOffsetFromPhotoSec"], .finite)
            return accuracy.map { .measured(accuracy: $0) }
        case "none":
            let heading = value!.objectValue!
            check.keys(heading, path, ["state", "reason", "reportedHeadingAccuracyDeg"])
            let reason = check.oneOf(heading["reason"], path + ["reason"], NoHeadingReasonName.allCases.map(\.rawValue))
                .flatMap(NoHeadingReasonName.init(rawValue:))
            var reported: Double?
            if let given = heading["reportedHeadingAccuracyDeg"] {
                reported = check.number(given, path + ["reportedHeadingAccuracyDeg"], .finite(atLeast: 0))
            }
            return reason.map { .none(reason: $0, reported: reported) }
        default:
            return nil
        }
    }

    private static func quaternion(_ value: JSONValue?, _ path: [String], _ check: inout Checker) {
        guard let quaternion = check.object(value, path, keys: ["x", "y", "z", "w"]) else { return }
        let parts = ["x", "y", "z", "w"].map { check.number(quaternion[$0], path + [$0], .finite) }
        guard let x = parts[0], let y = parts[1], let z = parts[2], let w = parts[3], x.isFinite, y.isFinite, z.isFinite, w.isFinite else { return }
        // The same arithmetic, in the same order, as the other two readings
        if abs(PointingRecord.Quaternion(x: x, y: y, z: z, w: w).norm - 1) > PointingContract.unitQuaternionTolerance {
            check.add(path, PointingContract.Rule.unitQuaternion.words)
        }
    }

    /// Whether the record has a place; nil when the location field is refused.
    private static func place(_ value: JSONValue?, _ check: inout Checker) -> Bool? {
        let path = ["location"]
        switch check.state(value, path, ["allowed", "none"]) {
        case "allowed":
            let place = value!.objectValue!
            check.keys(place, path, ["state", "latDeg", "lonDeg", "horizontalAccuracyM", "accuracyAuthorization", "altitude", "fixOffsetFromPhotoSec"])
            check.number(place["latDeg"], path + ["latDeg"], .range(from: -90, to: 90))
            check.number(place["lonDeg"], path + ["lonDeg"], .range(from: -180, to: 180))
            check.number(place["horizontalAccuracyM"], path + ["horizontalAccuracyM"], .finite(atLeast: 0))
            check.oneOf(place["accuracyAuthorization"], path + ["accuracyAuthorization"], AccuracyAuthorization.allCases.map(\.rawValue))
            if let given = place["altitude"],
               let altitude = check.object(given, path + ["altitude"], keys: ["aboveMeanSeaLevelM", "aboveEllipsoidM", "verticalAccuracyM"]) {
                check.number(altitude["aboveMeanSeaLevelM"], path + ["altitude", "aboveMeanSeaLevelM"], .finite)
                check.number(altitude["aboveEllipsoidM"], path + ["altitude", "aboveEllipsoidM"], .finite)
                check.number(altitude["verticalAccuracyM"], path + ["altitude", "verticalAccuracyM"], .finite(above: 0))
            }
            check.number(place["fixOffsetFromPhotoSec"], path + ["fixOffsetFromPhotoSec"], .finite)
            return true
        case "none":
            let place = value!.objectValue!
            check.keys(place, path, ["state", "reason"])
            check.oneOf(place["reason"], path + ["reason"], NoPlaceReason.allCases.map(\.rawValue))
            return false
        default:
            return nil
        }
    }
}

/// What a number field takes. Every bound is inclusive unless it says above or below.
struct NumberRule {
    var lower: Double?
    var lowerExclusive = false
    var upper: Double?
    var upperExclusive = false
    var finite = true
    var whole = false

    /// Any number, finite or not, as a literal's value is compared afterwards.
    static let any = NumberRule(finite: false)
    static let finite = NumberRule()
    static func finite(atLeast lower: Double) -> NumberRule { NumberRule(lower: lower) }
    static func finite(above lower: Double) -> NumberRule { NumberRule(lower: lower, lowerExclusive: true) }
    static func range(from lower: Double, to upper: Double) -> NumberRule { NumberRule(lower: lower, upper: upper) }
    static func range(from lower: Double, below upper: Double) -> NumberRule { NumberRule(lower: lower, upper: upper, upperExclusive: true) }
    static func range(above lower: Double, below upper: Double) -> NumberRule {
        NumberRule(lower: lower, lowerExclusive: true, upper: upper, upperExclusive: true)
    }
    /// A whole number from 1 to `max`.
    static func wholeCount(max: Double) -> NumberRule { NumberRule(lower: 0, lowerExclusive: true, upper: max, whole: true) }
}

/// Collects the issues of one record, field by field.
struct Checker {
    private(set) var issues: [PointingIssue] = []

    mutating func add(_ path: [String], _ message: String) { issues.append(PointingIssue(path: path, message: message)) }

    /// An object with no key outside `keys`; nil, with an issue, when the value is missing or not an object.
    mutating func object(_ value: JSONValue?, _ path: [String], keys: [String]) -> JSONObject? {
        guard let value else { add(path, "Required"); return nil }
        guard case .object(let object) = value else { add(path, "Expected an object"); return nil }
        self.keys(object, path, keys)
        return object
    }

    mutating func keys(_ object: JSONObject, _ path: [String], _ allowed: [String]) {
        let unknown = object.keys.filter { key in !allowed.contains { JSONString($0) == key } }
        if !unknown.isEmpty { add(path, "Unrecognized key(s) in object: \(unknown.map(\.description).joined(separator: ", "))") }
    }

    /// The discriminator of a tagged object, when it names one of `states`.
    mutating func state(_ value: JSONValue?, _ path: [String], _ states: [String]) -> String? {
        guard let value else { add(path, "Required"); return nil }
        guard case .object(let object) = value else { add(path, "Expected an object"); return nil }
        guard case .string(let state)? = object["state"], let name = states.first(where: { JSONString($0) == state }) else {
            add(path + ["state"], "Invalid discriminator value. Expected \(states.joined(separator: " | "))")
            return nil
        }
        return name
    }

    @discardableResult
    mutating func number(_ value: JSONValue?, _ path: [String], _ rule: NumberRule) -> Double? {
        guard let value else { add(path, "Required"); return nil }
        guard case .number(let number) = value else { add(path, "Expected number"); return nil }
        let x = number.value
        if rule.whole, !(x.isFinite && x == x.rounded()) { add(path, "Expected integer") }
        if rule.finite, !x.isFinite { add(path, "Number must be finite") }
        if let lower = rule.lower, rule.lowerExclusive ? !(x > lower) : !(x >= lower) {
            add(path, "Number must be \(rule.lowerExclusive ? "greater than" : "at least") \(lower)")
        }
        if let upper = rule.upper, rule.upperExclusive ? !(x < upper) : !(x <= upper) {
            add(path, "Number must be \(rule.upperExclusive ? "less than" : "at most") \(upper)")
        }
        return x
    }

    mutating func boolean(_ value: JSONValue?, _ path: [String]) {
        guard let value else { add(path, "Required"); return }
        guard case .bool = value else { add(path, "Expected boolean"); return }
    }

    mutating func literal(_ value: JSONValue?, _ path: [String], _ expected: String) {
        guard let value else { add(path, "Required"); return }
        guard case .string(let text) = value, text == JSONString(expected) else { add(path, "Invalid literal value"); return }
    }

    /// The value when it is one of `names`, exactly, unit by unit.
    @discardableResult
    mutating func oneOf(_ value: JSONValue?, _ path: [String], _ names: [String]) -> String? {
        guard let value else { add(path, "Required"); return nil }
        guard case .string(let text) = value, let name = names.first(where: { JSONString($0) == text }) else {
            add(path, "Invalid enum value. Expected \(names.joined(separator: " | "))")
            return nil
        }
        return name
    }

    mutating func text(_ value: JSONValue?, _ path: [String], _ message: String, _ accepts: (JSONString) -> Bool) {
        guard let value else { add(path, "Required"); return }
        guard case .string(let text) = value else { add(path, "Expected string"); return }
        if !accepts(text) { add(path, message) }
    }
}
