import AlbedianCaptureCore
import Foundation
import XCTest

/// lockon-m5b: the lock-on's early marks from the job's predicted sky (platform-10b's route, an approximate solution with
/// its pose), placed through that pose's own WCS, as the web's earlyPoseOf (apps/web/src/reveal/earlyPose.ts) and
/// alignTargetOf (alignPose.ts) read it: stars to magnitude 3.2 and planets, a catalogue number is no name, brightest
/// first, and each pose's source in its decided sentence.
final class PredictedEarlySkyTests: XCTestCase {
    private let sha = String(repeating: "ab", count: 32)

    private func finding(_ id: String, title: String, ra: Double, dec: Double, type: String, mag: Double?, source: String) -> JSONValue {
        var ref = JSONObject(["objectType": .string(JSONString(type)), "catalogSourceId": .string(JSONString(source))])
        if let mag { ref["apparentMagnitude"] = .number(JSONNumber(mag)!) }
        return .object(JSONObject(["id": .string(JSONString(id)), "evidenceClass": .string("predicted"), "title": .string(JSONString(title)),
                                   "skyPosition": .object(JSONObject(["raDeg": .number(JSONNumber(ra)!), "decDeg": .number(JSONNumber(dec)!)])),
                                   "catalogRef": .object(ref)]))
    }

    /// The fixture's approximate solution with a pose, its findings replaced by the given ones, for this upload.
    private func sky(method: String = "device_attitude", _ findings: [JSONValue]) throws -> (JSONValue, PoseWCS) {
        let valid = try XCTUnwrap(ContractFixture.load("observation_solution_contract")["valid"]?.objectValue)
        var sample = try XCTUnwrap(valid.members.first { $0.value["status"]?.stringValue == "approximate" && $0.value["poseEstimate"] != nil
            && $0.value["wcs"] != nil }?.value)
        sample["findings"] = .array(findings)
        sample["sourceSha256"] = .string(JSONString(sha))
        sample["poseEstimate"]?["method"] = .string(JSONString(method))
        return (sample, try XCTUnwrap(PoseWCS.read(try XCTUnwrap(sample["wcs"]))))
    }

    func testTheTargetsAreTheBrightStarsAndPlanetsPlacedThroughThePosesWCSBrightestFirst() throws {
        let (value, wcs) = try sky([])
        let ra = wcs.crvalRaDeg, dec = wcs.crvalDecDeg
        let findings = [
            finding("f-dim", title: "Dim", ra: ra, dec: dec, type: "star", mag: 3.21, source: "HIP 2"),
            finding("f-limit", title: "Alnitak", ra: ra + 0.5, dec: dec, type: "star", mag: 3.2, source: "HIP 26727"),
            finding("f-bright", title: "Rigel", ra: ra, dec: dec + 0.5, type: "star", mag: 0.13, source: "HIP 24436"),
            finding("f-gaia", title: "Gaia DR3 3017364132641362688", ra: ra - 0.5, dec: dec, type: "star", mag: 2.5, source: "Gaia DR3 3017364132641362688"),
            finding("f-mars", title: "Mars (Predicted Ephemeris)", ra: ra, dec: dec - 0.5, type: "planet", mag: nil, source: "Mars"),
            finding("f-ngc", title: "Pleiades", ra: ra, dec: dec, type: "deep_sky", mag: 1.6, source: "Mel22"),
        ]
        var withFindings = value
        withFindings["findings"] = .array(findings)
        let read = try XCTUnwrap(PredictedEarlySky.read(withFindings, sha256: sha))
        XCTAssertEqual(read.source, .pointing)
        XCTAssertEqual(read.sentence, "Placed from where your iPhone pointed: predicted until matched.")
        XCTAssertEqual(read.targets.map(\.id), ["f-mars", "f-bright", "f-gaia", "f-limit"])
        XCTAssertEqual(read.targets.map(\.name), ["Mars", "Rigel", "", "Alnitak"])
        XCTAssertEqual(read.targets.map(\.catalogSourceId), ["Mars", "HIP 24436", "Gaia DR3 3017364132641362688", "HIP 26727"])
        let rigel = try XCTUnwrap(read.targets.first { $0.id == "f-bright" })
        let at = try XCTUnwrap(wcs.pixel(raDeg: ra, decDeg: dec + 0.5))
        XCTAssertEqual(rigel.xPx, at.x, accuracy: 1e-9)
        XCTAssertEqual(rigel.yPx, at.y, accuracy: 1e-9)
        XCTAssertTrue(read.targets.allSatisfy { $0.evidence == .predicted })
    }

    /// The targets as marks on the wait's 390 by 520 photo: placed where the oriented photo covering that frame puts
    /// their stored pixel, none off the cut; a catalogue-numbered star carries its id as its name, so D20 never draws it.
    func testTheTargetsBecomeMarksPlacedOnTheWaitsPhoto() throws {
        let (value, wcs) = try sky([])
        var withFindings = value
        withFindings["findings"] = .array([
            finding("f-rigel", title: "Rigel", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg, type: "star", mag: 0.13, source: "HIP 24436"),
            finding("f-gaia", title: "Gaia DR3 1", ra: wcs.crvalRaDeg + 0.2, dec: wcs.crvalDecDeg, type: "star", mag: 2, source: "Gaia DR3 1"),
            finding("f-mars", title: "Mars (Predicted Ephemeris)", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg + 0.2, type: "planet", mag: nil, source: "Mars"),
        ])
        let sky = try XCTUnwrap(PredictedEarlySky.read(withFindings, sha256: sha))
        let frame = StoredFrame(widthPx: Int(wcs.crpixX * 2), heightPx: Int(wcs.crpixY * 2) + 1, orientation: .upright)
        let placed = sky.placed(frame: frame, widthPt: 390, heightPt: 520)
        let rigel = try XCTUnwrap(placed.first { $0.mark.id == "f-rigel" })
        let target = try XCTUnwrap(sky.targets.first { $0.id == "f-rigel" })
        let at = frame.placeCovering(xPx: target.xPx, yPx: target.yPx, onWidth: 390, height: 520)
        XCTAssertEqual(rigel.x, at.x, accuracy: 1e-9)
        XCTAssertEqual(rigel.y, at.y, accuracy: 1e-9)
        XCTAssertEqual(rigel.mark.name, "Rigel")
        XCTAssertEqual(rigel.mark.catalogSourceId, "HIP 24436")
        XCTAssertEqual(placed.first { $0.mark.id == "f-gaia" }?.mark.name, "Gaia DR3 1")
        XCTAssertEqual(placed.first { $0.mark.id == "f-mars" }?.mark.source, .ephemeris)
        XCTAssertTrue(placed.allSatisfy { $0.mark.evidence == .predicted })
        // A frame whose 3:4 cut leaves the targets off: none placed
        let wide = StoredFrame(widthPx: 100_000, heightPx: 400, orientation: .upright)
        XCTAssertTrue(sky.placed(frame: wide, widthPt: 390, heightPt: 520).isEmpty)
    }

    func testEachPoseNamesItsSourceInItsDecidedSentence() throws {
        let cases: [(String, PredictedEarlySky.Source, String)] = [
            ("moon_camera_profile_level_head", .moon, "Placed from the Moon: predicted until matched."),
            ("moon_horizon", .moon, "Placed from the Moon: predicted until matched."),
            ("sun_horizon", .sun, "Placed from the Sun: predicted until matched."),
        ]
        for (method, source, sentence) in cases {
            var (value, _) = try sky(method: method, [])
            if method != "device_attitude" { value["poseEstimate"]?["beaconCentroidUncertaintyDeg"] = .number(JSONNumber(0.2)!) }
            let read = try XCTUnwrap(PredictedEarlySky.read(value, sha256: sha), method)
            XCTAssertEqual(read.source, source, method)
            XCTAssertEqual(read.sentence, sentence, method)
        }
    }

    func testNothingFromAnotherUploadASkyThatIsNotApproximateOrAPoseNoRecordWords() throws {
        let (value, _) = try sky([])
        XCTAssertNotNil(PredictedEarlySky.read(value, sha256: sha))
        XCTAssertNil(PredictedEarlySky.read(value, sha256: String(repeating: "cd", count: 32)), "another upload")
        var calibrated = value
        calibrated["status"] = .string("calibrated")
        XCTAssertNil(PredictedEarlySky.read(calibrated, sha256: sha))
        var noPose = value
        noPose["poseEstimate"] = nil
        XCTAssertNil(PredictedEarlySky.read(noPose, sha256: sha))
        var unknown = value
        unknown["poseEstimate"]?["method"] = .string("guesswork")
        XCTAssertNil(PredictedEarlySky.read(unknown, sha256: sha))
        var noWCS = value
        noWCS["wcs"] = nil
        XCTAssertNil(PredictedEarlySky.read(noWCS, sha256: sha))

        // Missing or all-zero sourceSha256 is refused instead of tying an untethered sky to the photo
        var missingSha = value
        missingSha["sourceSha256"] = nil
        XCTAssertNil(PredictedEarlySky.read(missingSha, sha256: sha), "missing sourceSha256")
        let zeros = String(repeating: "0", count: 64)
        var zeroSha = value
        zeroSha["sourceSha256"] = .string(JSONString(zeros))
        XCTAssertNil(PredictedEarlySky.read(zeroSha, sha256: zeros), "64 zeros")

        // A reply claiming catalog_matched or simulated findings, or a predicted finding with pixel detection or holdout, is refused
        let (baseSky, wcs) = try sky([])
        for badClass in ["catalog_matched", "simulated", "unknown"] {
            var bad = baseSky
            var f = finding("f-bad", title: "Rigel", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg, type: "star", mag: 0.13, source: "HIP 24436")
            f["evidenceClass"] = .string(JSONString(badClass))
            bad["findings"] = .array([f])
            XCTAssertNil(PredictedEarlySky.read(bad, sha256: sha), badClass)
        }
        var withDet = baseSky
        var detFinding = finding("f-det", title: "Rigel", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg, type: "star", mag: 0.13, source: "HIP 24436")
        detFinding["sourceDetection"] = .object(JSONObject(["xPx": .int(10), "yPx": .int(10)]))
        withDet["findings"] = .array([detFinding])
        XCTAssertNil(PredictedEarlySky.read(withDet, sha256: sha), "predicted finding with sourceDetection")

        var withHoldout = baseSky
        var holdFinding = finding("f-hold", title: "Rigel", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg, type: "star", mag: 0.13, source: "HIP 24436")
        holdFinding["evidence"] = .object(JSONObject(["evidenceClass": .string("predicted"), "verifiedByIndependentHoldout": .bool(true)]))
        withHoldout["findings"] = .array([holdFinding])
        XCTAssertNil(PredictedEarlySky.read(withHoldout, sha256: sha), "predicted finding claiming holdout")

        // Out-of-range sky coordinates, empty ids, and non-finite planet magnitudes are rejected
        var badTargets = baseSky
        var badPlanet = finding("f-jup", title: "Jupiter", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg, type: "planet", mag: nil, source: "Jupiter")
        badPlanet["catalogRef"]?["apparentMagnitude"] = .string("bright")
        badTargets["findings"] = .array([
            finding("", title: "EmptyId", ra: wcs.crvalRaDeg, dec: wcs.crvalDecDeg, type: "star", mag: 1.0, source: "HIP 1"),
            finding("f-ra-neg", title: "NegRa", ra: wcs.crvalRaDeg - 360, dec: wcs.crvalDecDeg, type: "star", mag: 1.0, source: "HIP 2"),
            finding("f-ra-over", title: "OverRa", ra: wcs.crvalRaDeg + 360, dec: wcs.crvalDecDeg, type: "star", mag: 1.0, source: "HIP 3"),
            finding("f-dec-over", title: "OverDec", ra: wcs.crvalRaDeg, dec: 90.1, type: "star", mag: 1.0, source: "HIP 4"),
            finding("f-dec-under", title: "UnderDec", ra: wcs.crvalRaDeg, dec: -90.1, type: "star", mag: 1.0, source: "HIP 5"),
            badPlanet,
        ])
        XCTAssertEqual(try XCTUnwrap(PredictedEarlySky.read(badTargets, sha256: sha)).targets, [])
    }
}
