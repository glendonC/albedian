import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The contract's limits at their edges, the same cases packages/contracts/src/__tests__/pointing.test.ts runs on
/// the Zod side, read here by the Swift validator.
final class ContractBoundaryTests: XCTestCase {
    private func base() throws -> JSONValue { try SharedFixture.base() }

    func testKeepsAHeadingAtItsAccuracyLimitAndLeavesItOutJustPastIt() throws {
        let limit = PointingContract.headingAccuracyLimitDeg
        func at(_ accuracy: Double) throws -> [String]? {
            var record = try base()
            record.set(["heading", "headingAccuracyDeg"], .double(accuracy))
            return judge(record)
        }
        XCTAssertNil(try at(limit))
        XCTAssertNil(try at(0))
        XCTAssertEqual(try at(nextUp(limit)), [PointingContract.Rule.headingInsideLimit.words])
        func leftOut(_ reported: Double) throws -> [String]? {
            var record = try base()
            record.set(["heading"], .object(JSONObject(["state": .string("none"), "reason": .string("accuracy_outside_limit"),
                                                        "reportedHeadingAccuracyDeg": .double(reported)])))
            return judge(record)
        }
        XCTAssertNil(try leftOut(nextUp(limit)))
        XCTAssertEqual(try leftOut(limit), [PointingContract.Rule.outsideLimitCarriesAccuracy.words])
        // Any other reason carries no accuracy, and the outside-limit reason must carry one
        var other = try base()
        other.set(["heading"], .object(JSONObject(["state": .string("none"), "reason": .string("heading_invalid"),
                                                   "reportedHeadingAccuracyDeg": .double(30)])))
        XCTAssertEqual(judge(other), [PointingContract.Rule.onlyOutsideLimitReportsAccuracy.words])
        var bare = try base()
        bare.set(["heading"], .object(JSONObject(["state": .string("none"), "reason": .string("accuracy_outside_limit")])))
        XCTAssertEqual(judge(bare), [PointingContract.Rule.outsideLimitCarriesAccuracy.words])
    }

    func testAHeadingNeedsAPlaceAndNoPlaceNeedsNoPlace() throws {
        var measuredNoPlace = try base()
        measuredNoPlace.set(["location"], .object(JSONObject(["state": .string("none"), "reason": .string("not_allowed")])))
        XCTAssertEqual(judge(measuredNoPlace), [PointingContract.Rule.headingNeedsPlace.words])
        var noPlaceReasonWithPlace = try base()
        noPlaceReasonWithPlace.set(["heading"], .object(JSONObject(["state": .string("none"), "reason": .string("no_place")])))
        XCTAssertEqual(judge(noPlaceReasonWithPlace), [PointingContract.Rule.noPlaceReasonNeedsNoPlace.words])
        var both = noPlaceReasonWithPlace
        both.set(["location"], .object(JSONObject(["state": .string("none"), "reason": .string("no_fix")])))
        XCTAssertNil(judge(both))
    }

    func testTakesAPlaceOnlyInsideTheRangesOfLatitudeAndLongitude() throws {
        // Every edge, both sides: the shared fixture holds a latitude above 90 and a longitude below -180, not the
        // other two, so a lower latitude bound moved to -91 passed every other test (mutant 28 of mobile-0)
        let cases: [(String, Double, Bool)] = [("latDeg", -90, true), ("latDeg", 90, true), ("latDeg", (-90.0).nextDown, false),
                                               ("latDeg", (90.0).nextUp, false), ("lonDeg", -180, true), ("lonDeg", 180, true),
                                               ("lonDeg", (-180.0).nextDown, false), ("lonDeg", (180.0).nextUp, false)]
        for (key, value, accepted) in cases {
            var record = try base()
            record.set(["location", key], .double(value))
            XCTAssertEqual(judge(record) == nil, accepted, "\(key) = \(value)")
        }
    }

    func testTakesTheMotionSampleOnlyWithinItsOffsetLimitOfThePhoto() throws {
        let limit = PointingContract.motionSampleOffsetLimitSec
        for (offset, accepted) in [(limit, true), (-limit, true), (0, true), (nextUp(limit), false), (-nextUp(limit), false)] {
            var record = try base()
            record.set(["motion", "sampleOffsetFromPhotoSec"], .double(offset))
            XCTAssertEqual(judge(record) == nil, accepted, "\(offset)")
            if !accepted { XCTAssertEqual(judge(record), [PointingContract.Rule.sampleAtThePhoto.words]) }
        }
    }

    func testHoldsTheAttitudeToAUnitQuaternionWithinItsToleranceEitherSign() throws {
        let tolerance = PointingContract.unitQuaternionTolerance
        // Along w alone the norm is w: inside and outside by a tenth of the tolerance and ten times it. The pair at
        // 0.9 and 1.1 tells the norm from its square, which moves twice as far
        for factor in [0.1, 0.9, 1.1, 10] {
            for w in [1 + tolerance * factor, 1 - tolerance * factor] {
                var record = try base()
                record.set(["heading", "attitude", "quaternion"],
                           .object(JSONObject(["x": .int(0), "y": .int(0), "z": .int(0), "w": .double(w)])))
                XCTAssertEqual(judge(record) == nil, factor <= 1, "\(w)")
                if factor > 1 { XCTAssertEqual(judge(record), [PointingContract.Rule.unitQuaternion.words]) }
            }
        }
        var negated = try base()
        for key in ["x", "y", "z", "w"] {
            let value = try XCTUnwrap(negated.at(["heading", "attitude", "quaternion", key])?.numberValue)
            negated.set(["heading", "attitude", "quaternion", key], .double(-value))
        }
        XCTAssertNil(judge(negated))
    }

    func testHoldsGravityToItsBandAroundOneGTheEdgesExactAlongOneAxis() throws {
        func at(_ x: Double, _ y: Double, _ z: Double) throws -> [String]? {
            var record = try base()
            record.set(["motion", "gravityDeviceG"], .object(JSONObject(["x": .double(x), "y": .double(y), "z": .double(z)])))
            return judge(record)
        }
        let low = PointingContract.gravityLengthMinG
        let high = PointingContract.gravityLengthMaxG
        for z in [low, high, 1, -low, -high] { XCTAssertNil(try at(0, 0, z), "\(z)") }
        for z in [nextDown(low), nextUp(high), 9.81, -9.81, 100, 0.01, 1e-300, 0, 2, 1e200] {
            XCTAssertEqual(try at(0, 0, z), [PointingContract.Rule.gravityNearOneG.words], "\(z)")
        }
        XCTAssertNil(try at(0.5, -0.5, -0.7))
        XCTAssertNotNil(try at(5, -5, -7))
    }

    func testTakesTheWindowOnlyRunningForward() throws {
        var record = try base()
        let start = try XCTUnwrap(record.at(["motion", "window", "startOffsetSec"])?.numberValue)
        record.set(["motion", "window", "endOffsetSec"], .double(start))
        XCTAssertNil(judge(record))
        record.set(["motion", "window", "endOffsetSec"], .double(nextDown(start)))
        XCTAssertEqual(judge(record), [PointingContract.Rule.windowRunsForward.words])
    }

    func testRefusesThePlaceholderHashAndAnyHashOutsideTheExactPattern() throws {
        for (hash, accepted) in [(String(repeating: "0", count: 64), false), (String(repeating: "0", count: 63) + "1", true),
                                 (String(repeating: "a", count: 64), true), (String(repeating: "A", count: 64), false),
                                 (String(repeating: "a", count: 63), false), (String(repeating: "a", count: 65), false),
                                 (String(repeating: "g", count: 64), false)] {
            var record = try base()
            record.set(["sourceAssetSha256"], .string(hash))
            XCTAssertEqual(judge(record) == nil, accepted, hash)
        }
        var zeros = try base()
        zeros.set(["sourceAssetSha256"], .string(String(repeating: "0", count: 64)))
        XCTAssertEqual(judge(zeros), [PointingContract.Rule.placeholderSha256.words])
    }

    func testTakesTheTwoDeviceLabelsOnlyAsExactPatternsWhichCarryNoName() throws {
        func at(_ key: String, _ value: JSONString) throws -> [String]? {
            var record = try base()
            record.set(["device", key], .string(value))
            return judge(record)
        }
        XCTAssertEqual(PointingContract.labelMaxUTF16, 32)
        for model in ["iPhone18,1", "iPad16,10", "Synthetic1,1", String(repeating: "a", count: 29) + "1,1"] {
            XCTAssertNil(try at("hardwareModel", JSONString(model)), model)
        }
        let badModels: [JSONString] = [JSONString(String(repeating: "a", count: 30) + "1,1"), "My phone1,1", "Synthetic's1,1",
                                       "iPhone", "\u{e9}a1,1", "iPhone18,1\n", JSONString(units: [0xD800]), "\u{1F600}1,1",
                                       "arm64", "iPhone18,1 "]
        for model in badModels { XCTAssertNotNil(try at("hardwareModel", model), model.description) }
        for version in ["27", "27.1", "27.1.2", "0.0.0", "9999.9999.9999"] {
            XCTAssertNil(try at("systemVersion", JSONString(version)), version)
        }
        // The longest the pattern takes is the stated maximum, 14 units; one more is refused
        XCTAssertEqual("9999.9999.9999".utf16.count, PointingContract.systemVersionMaxUTF16)
        let badVersions: [JSONString] = ["27.1.2.3", "27.", ".27", "27..1", "v27", "27.1 ", "12345", "27.1\n", "\u{662}\u{667}", "",
                                         "9999.9999.99999", "99999.9999.9999"]
        for version in badVersions { XCTAssertNotNil(try at("systemVersion", version), version.description) }
    }

    func testBoundsEveryWholeNumberInsideTheSafeIntegers() throws {
        let cases: [([String], Int)] = [
            (["camera", "photoDimensionsPx", "width"], PointingContract.dimensionMaxPx),
            (["camera", "activeFormatDimensionsPx", "height"], PointingContract.dimensionMaxPx),
            (["motion", "window", "sampleCount"], PointingContract.sampleCountMax),
        ]
        for (path, bound) in cases {
            for (text, accepted) in [("\(bound)", true), ("\(bound).0", true), ("\(bound + 1)", false), ("9007199254740993", false),
                                     ("-0", false), ("0", false), ("1.5", false), ("1e0", true), ("1e400", false)] {
                var record = try base()
                record.set(path, literal(text))
                XCTAssertEqual(judge(record) == nil, accepted, "\(path.joined(separator: ".")) = \(text)")
            }
        }
    }

    func testRefusesAnInfiniteReadingATrueForANumberAStringForANumberAndANullForAnOptional() throws {
        let paths = [
            ["motion", "rotationRateDeviceRadPerSec", "x"],
            ["location", "altitude", "aboveEllipsoidM"],
            ["motion", "window", "startOffsetSec"],
            ["heading", "eventOffsetFromPhotoSec"],
            ["location", "fixOffsetFromPhotoSec"],
            ["camera", "videoZoomFactor"],
        ]
        for path in paths {
            for bad in ["1e400", "-1e400", "true", "\"1\"", "null", "[1]", "{}"] {
                var record = try base()
                record.set(path, literal(bad))
                XCTAssertNotNil(judge(record), "\(path.joined(separator: ".")) = \(bad)")
            }
        }
        var flagged = try base()
        flagged.set(["camera", "photoDimensionsPx", "width"], .bool(true))
        XCTAssertNotNil(judge(flagged))
        var nullAltitude = try base()
        nullAltitude.set(["location", "altitude"], .null)
        XCTAssertNotNil(judge(nullAltitude))
        var noAltitude = try base()
        noAltitude.set(["location", "altitude"], nil)
        XCTAssertNil(judge(noAltitude))
    }

    func testTakesMinusZeroAsZeroAndAWholeNumberWrittenAsAFraction() throws {
        var record = try base()
        record.set(["heading", "headingAccuracyDeg"], literal("-0"))
        record.set(["heading", "trueHeadingDeg"], literal("-0.0"))
        record.set(["formatVersion"], literal("1.0"))
        record.set(["camera", "photoDimensionsPx", "width"], literal("4.032e3"))
        XCTAssertNil(judge(record))
        record.set(["formatVersion"], literal("2"))
        XCTAssertNotNil(judge(record))
    }

    func testRefusesAnUnknownKeyAtEveryLevelAndAPythonFieldName() throws {
        let base = try base()
        for path in [[], ["device"], ["camera"], ["camera", "fieldOfView"], ["motion"], ["motion", "window"], ["heading"],
                     ["heading", "attitude"], ["heading", "attitude", "quaternion"], ["location"], ["location", "altitude"], ["evidence"]] {
            var record = base
            var target = try XCTUnwrap(record.at(path))
            target["unknownKey"] = .int(1)
            record.set(path, target)
            XCTAssertNotNil(judge(record), path.joined(separator: "."))
        }
        var snake = base
        let value = try XCTUnwrap(snake["sourceAssetSha256"])
        snake["sourceAssetSha256"] = nil
        snake["source_asset_sha256"] = value
        XCTAssertNotNil(judge(snake))
    }

    func testTakesTheInstantOnlyAsTheContractWritesIt() throws {
        for (instant, accepted) in [("2030-01-01T02:00:00Z", true), ("2030-01-01T02:00:00.123Z", true), ("2028-02-29T00:00:00Z", true),
                                    ("2000-02-29T00:00:00Z", true), ("2100-02-29T00:00:00Z", false), ("2031-02-29T00:00:00Z", false),
                                    ("2031-02-30T00:00:00Z", false), ("2031-04-31T00:00:00Z", false), ("0000-01-01T00:00:00Z", false),
                                    ("0001-01-01T00:00:00Z", true), ("9999-12-31T23:59:59.999Z", true), ("2030-01-01T24:00:00Z", false),
                                    ("2030-01-01T02:00:60Z", false), ("2030-01-01T02:00:00.12Z", false), ("2030-01-01T02:00:00.1234Z", false),
                                    ("2030-01-01T02:00:00z", false), ("2030-01-01T02:00:00+00:00", false), ("2030-01-01 02:00:00Z", false),
                                    ("2030-01-01T02:00Z", false), ("2030-1-01T02:00:00Z", false), ("2030-01-01T02:00:00Z\n", false)] {
            var record = try base()
            record.set(["capturedAtDeviceClockUTC"], .string(instant))
            XCTAssertEqual(judge(record) == nil, accepted, instant)
        }
        // The 31st of every month: only the long months have one (mutant 42 of mobile-0 took November 31)
        for month in 1...12 {
            var record = try base()
            let text = String(format: "2030-%02d-31T00:00:00Z", month)
            record.set(["capturedAtDeviceClockUTC"], .string(text))
            XCTAssertEqual(judge(record) == nil, ![2, 4, 6, 9, 11].contains(month), text)
        }
    }
}
