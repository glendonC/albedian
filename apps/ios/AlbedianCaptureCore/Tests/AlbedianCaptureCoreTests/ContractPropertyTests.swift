import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// P4 (REQ-IOS-7.1) in Swift: records generated with and without a heading and a place are accepted exactly when
/// the contract's rules allow, by the same oracle and the same record recipe the Python and Zod properties use
/// (tests/integration/test_pointing_contract.py, packages/contracts/src/__tests__/pointing.test.ts), with the
/// fixture's shared examples run first. The generator is seeded (`propertySeed()`, ALBEDIAN_SWIFT_SEED).
final class ContractPropertyTests: XCTestCase {
    struct PropertyCase: CustomStringConvertible {
        var heading: String
        var headingAccuracyDeg: Double?
        var reported: Double?
        var location: String
        var altitude: Bool
        var quaternionScale: Double
        var gravityScale: Double
        var sampleOffsetSec: Double
        var windowEndOffsetSec: Double
        var variant: Int?

        var description: String {
            "heading \(heading) accuracy \(String(describing: headingAccuracyDeg)) reported \(String(describing: reported)) location \(location) altitude \(altitude) q \(quaternionScale) g \(gravityScale) offset \(sampleOffsetSec) end \(windowEndOffsetSec) variant \(String(describing: variant))"
        }

        init(_ object: JSONObject) throws {
            heading = try XCTUnwrap(object["heading"]?.stringValue)
            headingAccuracyDeg = object["headingAccuracyDeg"]?.numberValue
            reported = object["reported"]?.numberValue
            location = try XCTUnwrap(object["location"]?.stringValue)
            altitude = try XCTUnwrap(object["altitude"]?.boolValue)
            quaternionScale = try XCTUnwrap(object["quaternionScale"]?.numberValue)
            gravityScale = try XCTUnwrap(object["gravityScale"]?.numberValue)
            sampleOffsetSec = try XCTUnwrap(object["sampleOffsetSec"]?.numberValue)
            windowEndOffsetSec = try XCTUnwrap(object["windowEndOffsetSec"]?.numberValue)
            variant = object["variant"]?.numberValue.map { Int($0) }
        }

        init<G: RandomNumberGenerator>(using generator: inout G, variantCount: Int) {
            // Half the draws measured and placed, so accepted measured headings come up often
            heading = generator.bool() ? "measured" : generator.pick(["measured"] + NoHeadingReasonName.allCases.map(\.rawValue))
            // Each rule field half the time inside what the rules take
            headingAccuracyDeg = generator.bool() ? generator.double(in: 0...25) : generator.double(in: -5...40)
            reported = generator.bool() ? nil : generator.double(in: 0...50)
            location = generator.bool() ? "allowed" : generator.pick(["allowed"] + NoPlaceReason.allCases.map(\.rawValue))
            altitude = generator.bool()
            // One, or well away from one so the oracle needs no arithmetic: the tolerance is 1e-6
            switch generator.int(in: 0...3) {
            case 0, 1: quaternionScale = 1
            case 2: quaternionScale = generator.double(in: 0.5...0.9999)
            default: quaternionScale = generator.double(in: 1.0001...1.5)
            }
            // One, inside the band or outside it, kept 0.05 g from its edges
            switch generator.int(in: 0...4) {
            case 0: gravityScale = 1
            case 1: gravityScale = generator.double(in: 0.95...1.05)
            case 2: gravityScale = generator.double(in: -1.05 ... -0.95)
            case 3: gravityScale = generator.double(in: 0...0.85)
            default: gravityScale = generator.double(in: 1.15...12)
            }
            sampleOffsetSec = generator.bool() ? generator.double(in: -0.05...0.05) : generator.double(in: -0.07...0.07)
            windowEndOffsetSec = generator.double(in: -1...12)
            variant = generator.bool() ? nil : generator.int(in: 0...(variantCount - 1))
        }
    }

    /// The record a case describes, built from the base record as the Python property builds it, and whether its
    /// field variant applied (only where its field is in the record).
    private func build(_ spec: PropertyCase, base: JSONValue, variants: [JSONObject]) throws -> (JSONValue, Bool) {
        var record = base
        if spec.location != "allowed" {
            record.set(["location"], .object(JSONObject(["state": .string("none"), "reason": .string(spec.location)])))
        } else if !spec.altitude {
            record.set(["location", "altitude"], nil)
        }
        if spec.heading == "measured" {
            record.set(["heading", "headingAccuracyDeg"], .double(try XCTUnwrap(spec.headingAccuracyDeg)))
            for key in ["x", "y", "z", "w"] {
                let value = try XCTUnwrap(base.at(["heading", "attitude", "quaternion", key])?.numberValue)
                record.set(["heading", "attitude", "quaternion", key], .double(value * spec.quaternionScale))
            }
        } else {
            var heading = JSONObject(["state": .string("none"), "reason": .string(spec.heading)])
            if let reported = spec.reported { heading["reportedHeadingAccuracyDeg"] = .double(reported) }
            record.set(["heading"], .object(heading))
        }
        for key in ["x", "y", "z"] {
            let value = try XCTUnwrap(base.at(["motion", "gravityDeviceG", key])?.numberValue)
            record.set(["motion", "gravityDeviceG", key], .double(value * spec.gravityScale))
        }
        record.set(["motion", "sampleOffsetFromPhotoSec"], .double(spec.sampleOffsetSec))
        record.set(["motion", "window", "endOffsetSec"], .double(spec.windowEndOffsetSec))
        var applied = false
        if let index = spec.variant {
            let variant = variants[index]
            let path = try XCTUnwrap(variant["path"]?.arrayValue).compactMap(\.stringValue)
            if record.at(Array(path.dropLast()))?.objectValue?[path.last!] != nil {
                record.set(path, variant["value"])
                applied = true
            }
        }
        return (record, applied)
    }

    /// Whether the record a case describes is valid, from the rules and the variant's own verdict (the oracle of the
    /// other two readings).
    private func oracle(_ spec: PropertyCase, applied: Bool, base: JSONValue, variants: [JSONObject]) throws -> Bool {
        let limit = PointingContract.headingAccuracyLimitDeg
        let placed = spec.location == "allowed"
        let headingOk: Bool
        if spec.heading == "measured" {
            let accuracy = try XCTUnwrap(spec.headingAccuracyDeg)
            headingOk = placed && accuracy >= 0 && accuracy <= limit && spec.quaternionScale == 1
        } else if spec.heading == "accuracy_outside_limit" {
            headingOk = spec.reported.map { $0 > limit } ?? false
        } else {
            headingOk = spec.reported == nil && (spec.heading != "no_place" || !placed)
        }
        let start = try XCTUnwrap(base.at(["motion", "window", "startOffsetSec"])?.numberValue)
        let motionOk = PointingContract.gravityLengthMinG <= abs(spec.gravityScale)
            && abs(spec.gravityScale) <= PointingContract.gravityLengthMaxG
            && abs(spec.sampleOffsetSec) <= PointingContract.motionSampleOffsetLimitSec
            && start <= spec.windowEndOffsetSec
        let variantOk = try !applied || XCTUnwrap(variants[try XCTUnwrap(spec.variant)]["ok"]?.boolValue)
        return headingOk && motionOk && variantOk
    }

    private func check(_ spec: PropertyCase, base: JSONValue, variants: [JSONObject], seed: UInt64?) throws {
        let (record, applied) = try build(spec, base: base, variants: variants)
        let expected = try oracle(spec, applied: applied, base: base, variants: variants)
        let issues = PointingValidator.issues(in: record)
        XCTAssertEqual(issues.isEmpty, expected, "seed \(seed.map(String.init) ?? "example"): \(spec): \(issues)")
    }

    func testP4GeneratedRecordsAreAcceptedExactlyWhenTheRulesAllow() throws {
        let base = try SharedFixture.base()
        let variants = try SharedFixture.samples("fieldVariants")
        let examples = try SharedFixture.samples("propertyExamples").map(PropertyCase.init)
        XCTAssertGreaterThanOrEqual(examples.count, 20)
        for example in examples { try check(example, base: base, variants: variants, seed: nil) }
        // The shared examples reach every heading and place state and both verdicts
        XCTAssertEqual(Set(examples.map(\.heading)), Set(["measured"] + NoHeadingReasonName.allCases.map(\.rawValue)))
        XCTAssertEqual(Set(examples.map(\.location)), Set(["allowed"] + NoPlaceReason.allCases.map(\.rawValue)))
        let verdicts = try Set(examples.map { try oracle($0, applied: build($0, base: base, variants: variants).1, base: base, variants: variants) })
        XCTAssertEqual(verdicts, [true, false])

        let seed = propertySeed()
        var generator = SeededGenerator(seed: seed)
        var accepted = 0
        for _ in 0..<300 {
            let spec = PropertyCase(using: &generator, variantCount: variants.count)
            try check(spec, base: base, variants: variants, seed: seed)
            if try oracle(spec, applied: build(spec, base: base, variants: variants).1, base: base, variants: variants) { accepted += 1 }
        }
        // Both verdicts come up among the generated cases too
        XCTAssertGreaterThan(accepted, 0, "seed \(seed)")
        XCTAssertLessThan(accepted, 300, "seed \(seed)")
    }
}
