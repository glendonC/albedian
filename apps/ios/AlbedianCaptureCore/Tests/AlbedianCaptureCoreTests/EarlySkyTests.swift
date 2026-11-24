@testable import AlbedianCaptureCore
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

    /// Edge case 2.1: early marks count as drawn on the wait only when at least one projects wholly inside the 3:4 photo
    /// frame AND a step or queued row stands under the photo; above-horizon stars outside the FOV or an empty laptop cut
    /// never hold the wait for a lock-on animation that draws zero rings.
    func testMarksCountAsDrawnOnlyWhenAtLeastOneProjectsInsideTheCardUnderARow() throws {
        let record = try XCTUnwrap(try records().first { EarlyPose.read(recordWire: JSONText.data($0)) != nil })
        let pose = try XCTUnwrap(EarlyPose.read(recordWire: JSONText.data(record)))
        let q = pose.attitude
        let norm = (q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w).squareRoot()
        let unit = PointingRecord.Quaternion(x: q.x / norm, y: q.y / norm, z: q.z / norm, w: q.w / norm)
        // Back camera looks along device -Z; rotate (0, 0, -1) into xTrueNorthZVertical (X north, Y west, Z up)
        let look = unit.multiplied(PointingRecord.Quaternion(x: 0, y: 0, z: -1, w: 0)).multiplied(unit.conjugate)
        let centreAlt = asin(max(-1, min(1, look.z))) * 180 / .pi
        let centreAz = (atan2(-look.y, look.x) * 180 / .pi).truncatingRemainder(dividingBy: 360)
        let azInRange = centreAz < 0 ? centreAz + 360 : centreAz
        let oppositeAz = (azInRange + 180).truncatingRemainder(dividingBy: 360)

        let inFrame = SkyMark(id: "s-in", name: "Rigel", altDeg: centreAlt, azDeg: azInRange, source: .catalog(release: "HIP"))
        let behind = SkyMark(id: "s-out", name: "Vega", altDeg: -centreAlt, azDeg: oppositeAz, source: .catalog(release: "HIP"))

        XCTAssertFalse(EarlyPose.hasDrawnMarks(laptopEarly: nil, pose: pose, phoneMarks: [behind], rowShown: true),
                       "above-horizon or sky-context marks outside the camera FOV draw 0 rings")
        XCTAssertFalse(EarlyPose.hasDrawnMarks(laptopEarly: nil, pose: pose, phoneMarks: [inFrame], rowShown: false),
                       "without a step or queued row, no early marks are drawn")
        XCTAssertTrue(EarlyPose.hasDrawnMarks(laptopEarly: nil, pose: pose, phoneMarks: [behind, inFrame], rowShown: true))
        XCTAssertEqual(EarlyPose.placed(laptopEarly: nil, pose: pose, phoneMarks: [behind, inFrame],
                                        widthPt: EarlyPose.defaultCardWidthPt, heightPt: EarlyPose.defaultCardWidthPt * 4 / 3).map(\.mark.id),
                       ["s-in"])

        let valid = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue)
        var sample = try XCTUnwrap(valid.members.first { $0.value["status"]?.stringValue == "approximate"
            && $0.value["poseEstimate"] != nil && $0.value["wcs"] != nil }?.value)
        let sha = String(repeating: "ab", count: 32)
        let wcs = try XCTUnwrap(sample["wcs"])
        let star: JSONValue = .object(JSONObject([
            "id": .string("f-rigel"), "evidenceClass": .string("predicted"), "title": .string("Rigel"),
            "skyPosition": .object(JSONObject(["raDeg": try XCTUnwrap(wcs["crvalRaDeg"]), "decDeg": try XCTUnwrap(wcs["crvalDecDeg"])])),
            "catalogRef": .object(JSONObject(["objectType": .string("star"), "catalogSourceId": .string("HIP 24436"),
                                              "apparentMagnitude": .number(JSONNumber(0.13)!)])),
        ]))
        sample["findings"] = .array([star])
        sample["sourceSha256"] = .string(JSONString(sha))
        sample["poseEstimate"]?["method"] = .string("device_attitude")
        let sky = try XCTUnwrap(PredictedEarlySky.read(sample, sha256: sha))
        let cx = try XCTUnwrap(wcs["crpixX"]?.numberValue)
        let cy = try XCTUnwrap(wcs["crpixY"]?.numberValue)
        let fitting = StoredFrame(widthPx: Int(cx * 2), heightPx: Int(cy * 2) + 1, orientation: .upright)
        let croppedOut = StoredFrame(widthPx: 100_000, heightPx: 400, orientation: .upright)

        XCTAssertTrue(EarlyPose.hasDrawnMarks(laptopEarly: (sky, fitting), pose: nil, phoneMarks: [], rowShown: true))
        XCTAssertFalse(EarlyPose.hasDrawnMarks(laptopEarly: (sky, fitting), pose: nil, phoneMarks: [], rowShown: false))
        XCTAssertFalse(EarlyPose.hasDrawnMarks(laptopEarly: (sky, croppedOut), pose: pose, phoneMarks: [inFrame], rowShown: true),
                       "once laptopEarly is served it replaces phoneMarks, and if 0 targets fall on the 3:4 card nothing is drawn")
    }
}

/// The 17:38 tag rule on the iPhone's wait (UIUX-built-1756): early marks stand only while their source line is in view
/// under a row; the line names the laptop's source when its sky is served, else C13's, and leaves with the marks.
final class EarlyLineTests: XCTestCase {
    func testTheMarksStandOnlyWithTheirLineUnderARow() {
        XCTAssertTrue(EarlyLine.marksShown(placed: 3, rowShown: true))
        XCTAssertFalse(EarlyLine.marksShown(placed: 3, rowShown: false), "no row, no line, so no marks")
        XCTAssertFalse(EarlyLine.marksShown(placed: 0, rowShown: true), "no mark inside the photo, no line")
    }

    func testTheLineNamesItsSourceAndLeavesWithTheMarks() {
        XCTAssertEqual(EarlyLine.sentence(marksShown: true, laptopSource: nil), "Placed from where your iPhone pointed: predicted until matched.")
        XCTAssertEqual(EarlyLine.sentence(marksShown: true, laptopSource: .moon), "Placed from the Moon: predicted until matched.")
        XCTAssertEqual(EarlyLine.sentence(marksShown: true, laptopSource: .pointing), EarlyPose.sourceSentence)
        XCTAssertNil(EarlyLine.sentence(marksShown: false, laptopSource: .sun))
    }

    /// AUDIT-C's sweep, item 9, as the lead ruled it (21 Nov 12:59): on a swap from the phone's own rings to the
    /// laptop's, the line keeps the phone's words until the phone's rings have gone, then names the laptop's source.
    func testOnASwapThePhonesWordsStayUntilThePhonesRingsHaveGone() {
        XCTAssertNil(EarlyLine.namedSource(laptop: .moon, phoneRingsDrawn: true, swapOver: false, reduceMotion: false),
                     "the phone's rings still leave or travel: its own words")
        XCTAssertEqual(EarlyLine.namedSource(laptop: .moon, phoneRingsDrawn: true, swapOver: true, reduceMotion: false), .moon)
        XCTAssertEqual(EarlyLine.namedSource(laptop: .sun, phoneRingsDrawn: false, swapOver: false, reduceMotion: false), .sun,
                       "no phone ring was drawn: the laptop's words come with its rings")
        XCTAssertEqual(EarlyLine.namedSource(laptop: .moon, phoneRingsDrawn: true, swapOver: false, reduceMotion: true), .moon,
                       "Reduce Motion: both sets dissolve together and the words change at once (section 6)")
        XCTAssertNil(EarlyLine.namedSource(laptop: nil, phoneRingsDrawn: true, swapOver: true, reduceMotion: false))
        XCTAssertEqual(EarlyLine.sentence(marksShown: true, laptopSource: EarlyLine.namedSource(laptop: .moon, phoneRingsDrawn: true,
                                                                                                swapOver: false, reduceMotion: false)),
                       EarlyPose.sourceSentence)
    }

    /// The words change when the swap's travel ends, the old-only rings gone on frame out before it, and that is the
    /// moment the new-only rings start (EarlyArrival), so no ring stands under another source's words.
    func testTheWordsChangeWhenTheNewRingsStart() {
        XCTAssertEqual(EarlyLine.swapWordsAfterSeconds, MotionStyle.frameIn.seconds, accuracy: 1e-12)
        XCTAssertGreaterThanOrEqual(EarlyLine.swapWordsAfterSeconds, MotionStyle.frameOut.seconds)
        let delays = EarlyArrival.delays(shown: ["a"], now: [EarlyArrival.Ring(key: "a", mag: 1), EarlyArrival.Ring(key: "b", mag: 2)],
                                         reduceMotion: false)
        XCTAssertEqual(delays["b"]!, EarlyLine.swapWordsAfterSeconds, accuracy: 1e-12)
    }
}
