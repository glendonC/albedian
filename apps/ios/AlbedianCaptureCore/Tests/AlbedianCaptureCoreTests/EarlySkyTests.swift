import AlbedianCaptureCore
import Foundation
import XCTest

/// D31 (UIUX-iphone-lockon-READY, decided by the lead 12:32): the early marks on the iPhone's wait come from the phone's
/// own pointing record, the earliest pose of all: its moment, place, attitude and the camera's field of view, read from
/// the record as it was sent, judged on the pointing contract's fixture (tests/fixtures/pointing_contract.json).
final class EarlySkyTests: XCTestCase {
    private func records() throws -> [JSONValue] {
        try XCTUnwrap(ContractFixture.load("pointing_contract")["valid"]?.arrayValue).compactMap { $0["record"] }
    }

    func testAPoseOnlyFromARecordWithItsPlaceItsMeasuredAttitudeAndItsFieldOfView() throws {
        let all = try records()
        var posed = 0
        for record in all {
            let complete = record["location"]?["state"]?.stringValue == "allowed" && record["heading"]?["state"]?.stringValue == "measured"
                && record["camera"]?["fieldOfView"]?["state"]?.stringValue == "reported"
            let pose = EarlyPose.read(recordWire: JSONText.data(record))
            XCTAssertEqual(pose != nil, complete, record["capturedAtDeviceClockUTC"]?.stringValue ?? "?")
            guard let pose else { continue }
            posed += 1
            XCTAssertEqual(pose.latDeg, record["location"]?["latDeg"]?.numberValue)
            XCTAssertEqual(pose.lonDeg, record["location"]?["lonDeg"]?.numberValue)
            XCTAssertEqual(pose.longSideFovDeg, record["camera"]?["fieldOfView"]?["horizontalDeg"]?.numberValue)
            let q = try XCTUnwrap(record["heading"]?["attitude"]?["quaternion"])
            XCTAssertEqual([pose.attitude.x, pose.attitude.y, pose.attitude.z, pose.attitude.w],
                           ["x", "y", "z", "w"].compactMap { q[$0]?.numberValue })
            XCTAssertEqual(pose.moment, UTCInstant.date(try XCTUnwrap(record["capturedAtDeviceClockUTC"]?.stringValue)))
        }
        XCTAssertEqual(all.count, 15)
        XCTAssertEqual(posed, 6)
    }

    func testNothingFromARecordThatIsNotOneOrHoldsAnotherFrameOrABadValue() throws {
        let base = try XCTUnwrap(try records().first { EarlyPose.read(recordWire: JSONText.data($0)) != nil })
        func with(_ path: [String], _ value: JSONValue?) -> Data {
            func put(_ node: JSONValue, _ rest: ArraySlice<String>) -> JSONValue {
                guard let key = rest.first else { return value ?? .null }
                var copy = node
                copy[key] = rest.count == 1 ? value : put(node[key] ?? .null, rest.dropFirst())
                return copy
            }
            return JSONText.data(put(base, ArraySlice(path)))
        }
        XCTAssertNil(EarlyPose.read(recordWire: Data("not json".utf8)))
        XCTAssertNil(EarlyPose.read(recordWire: with(["heading", "attitude", "referenceFrame"], .string("x_arbitrary_z_vertical"))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["capturedAtDeviceClockUTC"], .string("yesterday"))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["location", "latDeg"], .number(JSONNumber(91)!))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["location", "lonDeg"], nil)))
        XCTAssertNil(EarlyPose.read(recordWire: with(["camera", "fieldOfView", "horizontalDeg"], .number(JSONNumber(0)!))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["camera", "fieldOfView", "horizontalDeg"], .number(JSONNumber(180)!))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["heading", "attitude", "quaternion", "w"], nil)))
        // A state that is not the measured one never lends its values, even if a number sits beside it
        XCTAssertNil(EarlyPose.read(recordWire: with(["location", "state"], .string("denied"))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["heading", "state"], .string("none"))))
        XCTAssertNil(EarlyPose.read(recordWire: with(["camera", "fieldOfView", "state"], .string("unknown"))))
        XCTAssertNotNil(EarlyPose.read(recordWire: JSONText.data(base)))
    }

    /// The sentence under the step row is C13's, the one D25 gives a pose from the iPhone's record; every mark predicted.
    func testTheSourceSentenceIsTheDecidedOne() {
        XCTAssertEqual(EarlyPose.sourceSentence, "Placed from where your iPhone pointed: predicted until matched.")
        XCTAssertEqual(EarlyPose.evidence, .predicted)
    }
}
