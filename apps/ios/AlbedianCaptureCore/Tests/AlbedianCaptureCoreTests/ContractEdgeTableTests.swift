import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// Every bounded number, pattern, length, list and literal of the pointing record from one table: each row takes a
/// sample at every inside end and refuses one just outside, on a valid shared sample where its field exists. A
/// completeness test fails when any leaf of any valid sample has no row, so a new field gets its edges as soon as the
/// fixture holds it and the table names it. Then every object of every valid sample refuses an unknown key, each of
/// its camelCase keys written in Python's snake_case, each required key left out, and null for each optional one.
final class ContractEdgeTableTests: XCTestCase {
    private static let measured = "measured_heading_and_place"
    private static let outsideLimit = "heading_outside_the_limit"
    private static let noHeadingEvent = "no_heading_event"
    private static let noPlace = "place_not_allowed"
    private static let unknownFieldOfView = "field_of_view_unknown_zoomed_ultra_wide"

    struct Row {
        let path: [String]
        let base: String
        let inside: [JSONValue]
        let outside: [JSONValue]
    }

    private static let max = Double.greatestFiniteMagnitude
    private static let tiny = Double.leastNonzeroMagnitude
    private static let infinite = literal("1e400")
    private static let notNumbers: [JSONValue] = [infinite, literal("-1e400"), .bool(true), .string("1"), .null]

    private static func n(_ value: Double) -> JSONValue { .double(value) }
    private static func s(_ text: String) -> JSONValue { .string(text) }
    private static func vector(_ x: Double, _ y: Double, _ z: Double) -> JSONValue {
        .object(JSONObject(["x": n(x), "y": n(y), "z": n(z)]))
    }
    /// The smallest and largest w whose quaternion (0, 0, 0, w) the contract's arithmetic takes, found by stepping one
    /// double at a time from one minus and one plus the tolerance.
    private static let quaternionEnds: (low: Double, high: Double) = {
        let tolerance = PointingContract.unitQuaternionTolerance
        var low = 1 - tolerance
        while abs(low - 1) > tolerance { low = low.nextUp }
        while abs(low.nextDown - 1) <= tolerance { low = low.nextDown }
        var high = 1 + tolerance
        while abs(high - 1) > tolerance { high = high.nextDown }
        while abs(high.nextUp - 1) <= tolerance { high = high.nextUp }
        return (low, high)
    }()
    private static func quaternion(_ w: Double) -> JSONValue { .object(JSONObject(["x": n(0), "y": n(0), "z": n(0), "w": n(w)])) }

    private static func numberRow(_ path: [String], _ base: String, inside: [Double], outside: [Double], whole: Bool = false) -> Row {
        Row(path: path, base: base, inside: inside.map(n),
            outside: outside.map(n) + notNumbers + (whole ? [n(1.5)] : []))
    }
    private static func finiteRow(_ path: [String], _ base: String = measured) -> Row {
        numberRow(path, base, inside: [-max, max], outside: [])
    }
    private static func listRow(_ path: [String], _ base: String, _ names: [String], refused: [String] = []) -> Row {
        let first = names[0]
        return Row(path: path, base: base, inside: names.map(s),
                   outside: (refused + [first.uppercased(), first + " ", " " + first, "", first + "\u{7}"]).map(s) + [n(1), .null])
    }
    private static func booleanRow(_ path: [String]) -> Row {
        Row(path: path, base: measured, inside: [.bool(true), .bool(false)], outside: [n(1), n(0), s("true"), .null])
    }

    static let table: [Row] = {
        let limit = PointingContract.headingAccuracyLimitDeg
        let offset = PointingContract.motionSampleOffsetLimitSec
        let side = Double(PointingContract.dimensionMaxPx)
        let count = Double(PointingContract.sampleCountMax)
        let longModel = String(repeating: "a", count: PointingContract.labelMaxUTF16 - 3) + "1,1"
        var rows: [Row] = [
            Row(path: ["format"], base: measured, inside: [s(PointingContract.format)],
                outside: [s(PointingContract.format + " "), s("Albedian.pointing-record"), s(""), n(1)]),
            Row(path: ["formatVersion"], base: measured, inside: [n(1), literal("1.0"), literal("1e0")],
                outside: [n(0), n(2), n((1.0).nextUp), n((1.0).nextDown), s("1"), .bool(true), .null]),
            Row(path: ["sourceAssetSha256"], base: measured,
                inside: [s(String(repeating: "0", count: 63) + "1"), s(String(repeating: "f", count: 64))],
                outside: [s(String(repeating: "0", count: 64)), s(String(repeating: "a", count: 63)), s(String(repeating: "a", count: 65)),
                          s(String(repeating: "A", count: 64)), s("g" + String(repeating: "a", count: 63)), n(1), .null]),
            Row(path: ["capturedAtDeviceClockUTC"], base: measured,
                inside: [s("0001-01-01T00:00:00Z"), s("9999-12-31T23:59:59.999Z"), s("2028-02-29T00:00:00Z"), s("2030-12-31T23:59:59Z")],
                outside: [s("0000-12-31T23:59:59.999Z"), s("10000-01-01T00:00:00Z"), s("2030-01-01T02:00:00.99Z"),
                          s("2030-01-01T02:00:00.9999Z"), s("2100-02-29T00:00:00Z"), s("2030-13-01T00:00:00Z"),
                          s("2030-00-01T00:00:00Z"), s("2030-01-00T00:00:00Z"), s("2030-01-01T24:00:00Z"), s("2030-01-01T23:60:00Z"),
                          s("2030-01-01T23:59:60Z"), s("2030-01-01T02:00:00Z\n"), s("2030-01-01T02:00:00+00:00"), n(0), .null]),
            Row(path: ["device", "hardwareModel"], base: measured, inside: [s("a1,1"), s(longModel)],
                outside: [s("a" + longModel), s("1,1"), s("a,1"), s("a1,"), s("a1;1"), s("a1,1,1"), s(""), n(1)]),
            Row(path: ["device", "systemVersion"], base: measured,
                inside: [s("0"), s("9999.9999.9999"), s("27.1")],
                outside: [s(""), s("9999.9999.99999"), s("99999"), s("1.2.3.4"), s("1."), s(".1"), n(27)]),
            listRow(["camera", "position"], measured, CameraPosition.allCases.map(\.rawValue)),
            listRow(["camera", "deviceType"], measured, CameraDeviceType.allCases.map(\.rawValue)),
            Row(path: ["camera", "fieldOfView", "state"], base: measured, inside: [s("reported")], outside: [s("unknown"), s("Reported"), s(""), .null]),
            Row(path: ["camera", "fieldOfView", "state"], base: unknownFieldOfView, inside: [s("unknown")], outside: [s("reported"), s("Unknown")]),
            numberRow(["camera", "fieldOfView", "horizontalDeg"], measured, inside: [tiny, (180.0).nextDown], outside: [0, -tiny, 180]),
            booleanRow(["camera", "fieldOfView", "distortionCorrected"]),
            numberRow(["camera", "videoZoomFactor"], measured, inside: [1, max], outside: [(1.0).nextDown, 0]),
            booleanRow(["camera", "contentAwareDistortionCorrection"]),
            numberRow(["motion", "sampleOffsetFromPhotoSec"], measured, inside: [-offset, offset, 0], outside: [-offset.nextUp, offset.nextUp]),
            // Gravity's bound is its length, so the vector carries the edges and each component only its type
            Row(path: ["motion", "gravityDeviceG"], base: measured,
                inside: [vector(0, 0, PointingContract.gravityLengthMinG), vector(0, 0, PointingContract.gravityLengthMaxG),
                         vector(0, 0, -PointingContract.gravityLengthMaxG)],
                outside: [vector(0, 0, PointingContract.gravityLengthMinG.nextDown), vector(0, 0, PointingContract.gravityLengthMaxG.nextUp),
                          vector(0, 0, 0), .null]),
            Row(path: ["motion", "window", "sampleCount"], base: measured, inside: [n(1), n(count)],
                outside: [n(0), n(count + 1), n(-1), n(1.5)] + notNumbers),
            numberRow(["motion", "window", "startOffsetSec"], measured, inside: [-max, 10.5], outside: [(10.5).nextUp]),
            numberRow(["motion", "window", "endOffsetSec"], measured, inside: [-0.5, max], outside: [(-0.5).nextDown]),
            numberRow(["motion", "window", "maxRotationRateRadPerSec"], measured, inside: [0, max], outside: [-tiny]),
            Row(path: ["heading", "state"], base: measured, inside: [s("measured")], outside: [s("none"), s("Measured"), s(""), .null]),
            Row(path: ["heading", "attitude", "referenceFrame"], base: measured, inside: [s("x_true_north_z_vertical")],
                outside: [s("x_magnetic_north_z_vertical"), s("x_true_north_z_vertical "), s(""), .null]),
            // The quaternion's bound is its norm, so the object carries the edges and each component only its type
            Row(path: ["heading", "attitude", "quaternion"], base: measured,
                inside: [quaternion(quaternionEnds.low), quaternion(quaternionEnds.high), quaternion(-1)],
                outside: [quaternion(quaternionEnds.low.nextDown), quaternion(quaternionEnds.high.nextUp), quaternion(0), .null]),
            numberRow(["heading", "trueHeadingDeg"], measured, inside: [0, (360.0).nextDown], outside: [-tiny, 360]),
            numberRow(["heading", "headingAccuracyDeg"], measured, inside: [0, limit], outside: [-tiny, limit.nextUp]),
            listRow(["heading", "headingOrientation"], measured, HeadingOrientation.allCases.map(\.rawValue), refused: ["face_up", "unknown"]),
            finiteRow(["heading", "eventOffsetFromPhotoSec"]),
            Row(path: ["heading", "state"], base: noHeadingEvent, inside: [s("none")], outside: [s("measured"), s("None")]),
            listRow(["heading", "reason"], noHeadingEvent, ["no_heading_event", "no_magnetometer", "heading_invalid"],
                    refused: ["no_place", "accuracy_outside_limit", "no_fix"]),
            listRow(["heading", "reason"], noPlace, ["no_place", "no_magnetometer", "no_heading_event", "heading_invalid"],
                    refused: ["accuracy_outside_limit"]),
            listRow(["heading", "reason"], outsideLimit, ["accuracy_outside_limit"], refused: ["heading_invalid", "no_place"]),
            numberRow(["heading", "reportedHeadingAccuracyDeg"], outsideLimit, inside: [limit.nextUp, max], outside: [limit, 0, -tiny]),
            Row(path: ["location", "state"], base: measured, inside: [s("allowed")], outside: [s("none"), s("Allowed"), s(""), .null]),
            numberRow(["location", "latDeg"], measured, inside: [-90, 90], outside: [(-90.0).nextDown, (90.0).nextUp]),
            numberRow(["location", "lonDeg"], measured, inside: [-180, 180], outside: [(-180.0).nextDown, (180.0).nextUp]),
            numberRow(["location", "horizontalAccuracyM"], measured, inside: [0, max], outside: [-tiny]),
            listRow(["location", "accuracyAuthorization"], measured, AccuracyAuthorization.allCases.map(\.rawValue)),
            finiteRow(["location", "altitude", "aboveMeanSeaLevelM"]),
            finiteRow(["location", "altitude", "aboveEllipsoidM"]),
            numberRow(["location", "altitude", "verticalAccuracyM"], measured, inside: [tiny, max], outside: [0, -tiny]),
            finiteRow(["location", "fixOffsetFromPhotoSec"]),
            Row(path: ["location", "state"], base: noPlace, inside: [s("none")], outside: [s("allowed"), s("None")]),
            listRow(["location", "reason"], noPlace, NoPlaceReason.allCases.map(\.rawValue), refused: ["allowed", "no_place"]),
            Row(path: ["evidence", "role"], base: measured, inside: [s("pointing_hint")], outside: [s("observed"), s("pointing_hint "), s(""), .null]),
            Row(path: ["evidence", "statement"], base: measured, inside: [s(PointingContract.statement)],
                outside: [s(PointingContract.statement + " "), s(String(PointingContract.statement.dropLast())), s(""), .null]),
        ]
        for key in ["width", "height"] {
            for dimensions in ["activeFormatDimensionsPx", "photoDimensionsPx"] {
                rows.append(numberRow(["camera", dimensions, key], measured, inside: [1, side], outside: [0, side + 1, -1], whole: true))
            }
        }
        // Components of the two bounded vectors and the quaternion: their own value inside, anything not a number out
        for (path, keys) in [(["motion", "gravityDeviceG"], ["x", "y", "z"]), (["heading", "attitude", "quaternion"], ["x", "y", "z", "w"])] {
            for key in keys { rows.append(Row(path: path + [key], base: measured, inside: [], outside: notNumbers)) }
        }
        for key in ["x", "y", "z"] { rows.append(finiteRow(["motion", "rotationRateDeviceRadPerSec", key])) }
        return rows
    }()

    private func leaves(_ value: JSONValue, _ path: [String] = []) -> [[String]] {
        guard let object = value.objectValue else { return [path] }
        return object.members.flatMap { leaves($0.value, path + [$0.key.string ?? "?"]) }
    }

    func testEveryLeafOfEveryValidSampleHasARow() throws {
        let named = Set(Self.table.map { $0.path.joined(separator: ".") })
        var missing: Set<String> = []
        for sample in try SharedFixture.samples("valid") {
            for leaf in leaves(try XCTUnwrap(sample["record"])) where !named.contains(leaf.joined(separator: ".")) {
                missing.insert(leaf.joined(separator: "."))
            }
        }
        XCTAssertEqual(missing.sorted(), [], "leaves with no edge row")
        XCTAssertGreaterThanOrEqual(Self.table.count, 50)
    }

    func testEveryRowTakesItsInsideEndsAndRefusesJustOutside() throws {
        var wrong: [String] = []
        var samples = 0
        for row in Self.table {
            let base = try SharedFixture.validRecord(named: row.base)
            let field = row.path.joined(separator: ".")
            guard base.at(row.path) != nil else { wrong.append("\(field): not in \(row.base)"); continue }
            for (values, accepted) in [(row.inside, true), (row.outside, false)] {
                for value in values {
                    var record = base
                    record.set(row.path, value)
                    samples += 1
                    if PointingValidator.accepts(record) != accepted {
                        wrong.append("\(field) on \(row.base) = \(String(decoding: JSONText.units(value).prefix(60), as: UTF16.self)): "
                                     + (accepted ? "refused" : "accepted"))
                    }
                }
            }
        }
        XCTAssertEqual(wrong, [], "edge samples judged against the table")
        XCTAssertGreaterThanOrEqual(samples, 400)
        print("edge table: \(Self.table.count) rows, \(samples) samples")
    }

    func testEveryObjectRefusesAnUnknownKeyAPythonNameAMissingKeyAndANullAtEveryLevel() throws {
        let optional: Set<String> = ["location.altitude", "heading.reportedHeadingAccuracyDeg"]
        var wrong: [String] = []
        var samples = 0
        func check(_ record: JSONValue, _ label: String) {
            samples += 1
            if PointingValidator.accepts(record) { wrong.append(label) }
        }
        /// The Python field name of a wire key, as the mirror's fields are named: a run of capitals is one word
        /// (capturedAtDeviceClockUTC is captured_at_device_clock_utc).
        func snake(_ key: String) -> String {
            var out = ""
            var previousUpper = false
            for character in key {
                if character.isUppercase {
                    if !previousUpper { out += "_" }
                    out += character.lowercased()
                } else {
                    out.append(character)
                }
                previousUpper = character.isUppercase
            }
            return out
        }
        func objects(_ value: JSONValue, _ path: [String] = []) -> [[String]] {
            guard let object = value.objectValue else { return [] }
            return [path] + object.members.flatMap { objects($0.value, path + [$0.key.string ?? "?"]) }
        }
        for sample in try SharedFixture.samples("valid") {
            let name = sample["name"]?.stringValue ?? "?"
            let base = try XCTUnwrap(sample["record"])
            for path in objects(base) {
                let object = try XCTUnwrap(base.at(path)?.objectValue)
                let at = path.isEmpty ? "record" : path.joined(separator: ".")
                var unknown = object
                unknown["unknownKey"] = .int(1)
                var record = base
                record.set(path, .object(unknown))
                check(record, "\(name): \(at) took an unknown key")
                for member in object.members {
                    let key = try XCTUnwrap(member.key.string)
                    let field = (path + [key]).joined(separator: ".")
                    if snake(key) != key {
                        var renamed = object
                        renamed[key] = nil
                        renamed[snake(key)] = member.value
                        record = base
                        record.set(path, .object(renamed))
                        check(record, "\(name): \(field) took the Python name \(snake(key))")
                    }
                    record = base
                    record.set(path + [key], .null)
                    check(record, "\(name): \(field) took null")
                    if !optional.contains(field) {
                        var without = object
                        without[key] = nil
                        record = base
                        record.set(path, .object(without))
                        check(record, "\(name): \(field) was left out and the record was taken")
                    }
                }
            }
        }
        XCTAssertEqual(wrong, [])
        XCTAssertGreaterThanOrEqual(samples, 1000)
        print("key faults: \(samples) samples over every object of every valid sample")
    }
}
