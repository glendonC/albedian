import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// Building the pointing record from sensor readings (REQ-IOS-1.2, 1.3, REQ-IOS-6.2): every rule of the contract,
/// the readings carried as the device gave them, and nothing guessed. All readings are synthetic (SampleCapture).
final class RecordBuilderTests: XCTestCase {
    private let photoHash = PhotoHash.sha256Hex(Data("synthetic photo bytes".utf8))
    private let tolerance = 1e-9

    private func capture(_ change: (inout SampleCapture.Options) -> Void = { _ in }) -> SensorCapture {
        var options = SampleCapture.Options()
        change(&options)
        return SampleCapture.capture(photoSha256: photoHash, options: options)
    }

    private func built(_ capture: SensorCapture) throws -> PointingRecord {
        do {
            return try PointingRecordBuilder.build(capture)
        } catch {
            XCTFail("not built: \(error)")
            throw error
        }
    }

    private func refusal(_ capture: SensorCapture) -> PointingBuildError? {
        do {
            _ = try PointingRecordBuilder.build(capture)
            return nil
        } catch {
            return error
        }
    }

    func testBuildsAMeasuredHeadingAndPlaceWithTheReadingsAsGiven() throws {
        let record = try built(capture())
        XCTAssertEqual(record.sourceAssetSha256, photoHash)
        XCTAssertEqual(record.capturedAtDeviceClockUTC, "2030-01-01T02:00:00.000Z")
        XCTAssertEqual(record.device, PointingRecord.Device(hardwareModel: "Synthetic1,1", systemVersion: "27.1"))
        guard case .measured(let heading) = record.heading else { return XCTFail("\(record.heading)") }
        XCTAssertEqual(heading.quaternion, PointingRecord.Quaternion(x: 0.2, y: -0.4, z: 0.4, w: 0.8))
        XCTAssertEqual(heading.trueHeadingDeg, 212.5)
        XCTAssertEqual(heading.headingAccuracyDeg, 12)
        XCTAssertEqual(heading.headingOrientation, .portrait)
        XCTAssertEqual(heading.eventOffsetFromPhotoSec, -1.25, accuracy: tolerance)
        guard case .allowed(let place) = record.location else { return XCTFail("\(record.location)") }
        XCTAssertEqual(place.latDeg, 30)
        XCTAssertEqual(place.lonDeg, -40)
        XCTAssertEqual(place.horizontalAccuracyM, 8)
        XCTAssertEqual(place.accuracyAuthorization, .full)
        XCTAssertEqual(place.altitude, PointingRecord.Altitude(aboveMeanSeaLevelM: 12, aboveEllipsoidM: 45.5, verticalAccuracyM: 3))
        XCTAssertEqual(place.fixOffsetFromPhotoSec, -2.5, accuracy: tolerance)
        XCTAssertEqual(record.motion.sampleOffsetFromPhotoSec, -0.004, accuracy: tolerance)
        XCTAssertEqual(record.motion.gravityDeviceG, PointingRecord.Vector3(x: 0, y: -0.6, z: -0.8))
        XCTAssertEqual(record.motion.rotationRateDeviceRadPerSec, PointingRecord.Vector3(x: 0.002, y: -0.001, z: 0.0005))
        XCTAssertEqual(record.motion.window.startOffsetSec, -0.5)
        XCTAssertEqual(record.motion.window.endOffsetSec, 10.5)
        XCTAssertEqual(record.motion.window.sampleCount, 1100)
        XCTAssertEqual(record.motion.window.maxRotationRateRadPerSec, (0.002 * 0.002 + 0.001 * 0.001 + 0.0005 * 0.0005).squareRoot(),
                       accuracy: tolerance)
        XCTAssertEqual(record.camera.fieldOfView, .reported(horizontalDeg: 69.5, distortionCorrected: true))
        XCTAssertEqual(record.camera.activeFormatDimensionsPx, PointingRecord.Dimensions(width: 4032, height: 3024))
        XCTAssertEqual(record.camera.photoDimensionsPx, PointingRecord.Dimensions(width: 4032, height: 3024))
        XCTAssertEqual(record.camera.videoZoomFactor, 1)
        XCTAssertFalse(record.camera.contentAwareDistortionCorrection)
    }

    func testWritesWireJSONTheContractTakesInTheShapeOfTheSharedSample() throws {
        let wire = try built(capture()).wireValue()
        XCTAssertEqual(PointingValidator.issues(in: wire), [])
        // The same keys, in the same order, at every level as the fixture's first valid sample
        func keys(_ value: JSONValue) -> [String] {
            guard let object = value.objectValue else { return [] }
            return object.members.flatMap { member in [member.key.string ?? "?"] + keys(member.value).map { "\(member.key.string ?? "?").\($0)" } }
        }
        XCTAssertEqual(keys(wire), keys(try SharedFixture.base()))
        XCTAssertEqual(wire["evidence"]?["statement"]?.stringValue, PointingContract.statement)
        XCTAssertEqual(wire["evidence"]?["role"]?.stringValue, "pointing_hint")
        XCTAssertEqual(wire["heading"]?["attitude"]?["referenceFrame"]?.stringValue, "x_true_north_z_vertical")
        // Whole numbers as integers, read back unchanged from the bytes
        let text = String(decoding: try built(capture()).wireData(), as: UTF8.self)
        XCTAssertTrue(text.contains("\"width\":4032,\"height\":3024"), text)
        XCTAssertTrue(text.contains("\"formatVersion\":1,"), text)
        XCTAssertEqual(try JSONText.read(Data(text.utf8)), wire)
    }

    func testKeepsAHeadingAtTheAccuracyLimitAndLeavesItOutPastItWithTheReportedAccuracy() throws {
        let limit = PointingContract.headingAccuracyLimitDeg
        guard case .measured(let atLimit) = try built(capture { $0.headingAccuracyDeg = limit }).heading else { return XCTFail() }
        XCTAssertEqual(atLimit.headingAccuracyDeg, limit)
        XCTAssertEqual(try built(capture { $0.headingAccuracyDeg = limit.nextUp }).heading,
                       .absent(.accuracyOutsideLimit(reportedDeg: limit.nextUp)))
        XCTAssertEqual(try built(capture { $0.headingAccuracyDeg = 0 }).heading.isMeasured, true)
    }

    func testSaysWhyThereIsNoHeading() throws {
        XCTAssertEqual(try built(capture { $0.headingArrived = false }).heading, .absent(.noHeadingEvent))
        XCTAssertEqual(try built(capture { $0.headingAccuracyDeg = -1 }).heading, .absent(.headingInvalid))
        var undetermined = capture()
        undetermined.heading?.trueHeadingDeg = -1
        XCTAssertEqual(try built(undetermined).heading, .absent(.headingInvalid))
        XCTAssertEqual(try built(capture { $0.trueNorthFrame = false }).heading, .absent(.noMagnetometer))
        // No place: no heading, even with a heading event in hand, because true north needs the place
        XCTAssertEqual(try built(capture { $0.locationAllowed = false }).heading, .absent(.noPlace))
        XCTAssertEqual(try built(capture { $0.placeSimulatedBySoftware = true }).heading, .absent(.noPlace))
        // No true-north frame comes first: it is why there is no heading even with a place
        XCTAssertEqual(try built(capture { $0.trueNorthFrame = false; $0.locationAllowed = false }).heading, .absent(.noMagnetometer))
    }

    func testCarriesAPlaceOnlyWhenAllowedValidAndNotSimulated() throws {
        XCTAssertEqual(try built(capture { $0.locationAllowed = false }).location, .absent(.notAllowed))
        XCTAssertEqual(try built(capture { $0.placeSimulatedBySoftware = true }).location, .absent(.simulatedBySoftware))
        var noFix = capture()
        noFix.location = .allowed(.full, fix: nil)
        XCTAssertEqual(try built(noFix).location, .absent(.noFix))
        let changes: [(inout LocationFix) -> Void] = [{ $0.horizontalAccuracyM = -1 }, { $0.latDeg = 91 }, { $0.lonDeg = -180.5 },
                                                     { $0.latDeg = .nan }, { $0.horizontalAccuracyM = .infinity }]
        for change in changes {
            var invalid = capture()
            guard case .allowed(let authorization, var fix?) = invalid.location else { return XCTFail() }
            change(&fix)
            invalid.location = .allowed(authorization, fix: fix)
            XCTAssertEqual(try built(invalid).location, .absent(.noFix))
            XCTAssertEqual(try built(invalid).heading, .absent(.noPlace))
        }
        // A simulated place is said as simulated, not as no fix, even when its accuracy is also invalid
        var simulatedInvalid = capture { $0.placeSimulatedBySoftware = true }
        guard case .allowed(let authorization, var fix?) = simulatedInvalid.location else { return XCTFail() }
        fix.horizontalAccuracyM = -1
        simulatedInvalid.location = .allowed(authorization, fix: fix)
        XCTAssertEqual(try built(simulatedInvalid).location, .absent(.simulatedBySoftware))
    }

    func testKeepsReducedAccuracyWithItsRadiusAndAltitudesOnlyWhenValid() throws {
        var reduced = capture()
        guard case .allowed(_, var fix?) = reduced.location else { return XCTFail() }
        fix.horizontalAccuracyM = 3000
        reduced.location = .allowed(.reduced, fix: fix)
        guard case .allowed(let place) = try built(reduced).location else { return XCTFail() }
        XCTAssertEqual(place.accuracyAuthorization, .reduced)
        XCTAssertEqual(place.horizontalAccuracyM, 3000)
        for vertical in [0.0, -1, .nan] {
            var noAltitude = capture()
            fix.verticalAccuracyM = vertical
            noAltitude.location = .allowed(.full, fix: fix)
            guard case .allowed(let place) = try built(noAltitude).location else { return XCTFail() }
            XCTAssertNil(place.altitude, "\(vertical)")
        }
    }

    func testSaysAnUnknownFieldOfViewAsUnknownAndTakesTheOneTheCorrectionUsed() throws {
        XCTAssertEqual(try built(capture { $0.fieldOfViewReported = false }).camera.fieldOfView, .unknown)
        var uncorrected = capture()
        uncorrected.camera.geometricDistortionCorrectionEnabled = false
        XCTAssertEqual(try built(uncorrected).camera.fieldOfView, .reported(horizontalDeg: 72, distortionCorrected: false))
        for bad in [-1.0, 180, 200, .nan, .infinity] {
            var outside = capture()
            outside.camera.distortionCorrectedFieldOfViewDeg = bad
            XCTAssertEqual(refusal(outside), .cameraValueOutOfRange(field: "fieldOfView.horizontalDeg"), "\(bad)")
        }
    }

    func testRefusesCameraValuesOutsideTheirRangeRatherThanClampingThem() {
        var zoom = capture()
        zoom.camera.videoZoomFactor = 0.5
        XCTAssertEqual(refusal(zoom), .cameraValueOutOfRange(field: "videoZoomFactor"))
        var raw = capture()
        raw.camera.photoWidthPx = 0
        raw.camera.photoHeightPx = 0
        XCTAssertEqual(refusal(raw), .cameraValueOutOfRange(field: "photoDimensionsPx"))
        var huge = capture()
        huge.camera.activeFormatWidthPx = PointingContract.dimensionMaxPx + 1
        XCTAssertEqual(refusal(huge), .cameraValueOutOfRange(field: "activeFormatDimensionsPx"))
    }

    func testTakesTheMotionSampleNearestThePhotoOnlyWithinTheLimit() throws {
        XCTAssertEqual(refusal(capture { $0.motionRunning = false }), .noMotionSampleAtThePhoto(nearestOffsetSec: nil))
        var late = capture()
        late.motion.samples = late.motion.samples.filter { $0.bootClockSec - SampleCapture.photoBootClockSec > 0.055 }
        guard case .noMotionSampleAtThePhoto(let nearest?)? = refusal(late) else { return XCTFail("\(String(describing: refusal(late)))") }
        XCTAssertEqual(nearest, 0.056, accuracy: tolerance)
        // The nearest of two, either side
        var pair = capture()
        pair.motion.samples = [pair.motion.samples[0], pair.motion.samples[0]]
        pair.motion.samples[0].bootClockSec = SampleCapture.photoBootClockSec - 0.03
        pair.motion.samples[1].bootClockSec = SampleCapture.photoBootClockSec + 0.02
        pair.motion.samples[1].gravityG = PointingRecord.Vector3(x: 0, y: 0, z: -1)
        let record = try built(pair)
        XCTAssertEqual(record.motion.sampleOffsetFromPhotoSec, 0.02, accuracy: tolerance)
        XCTAssertEqual(record.motion.gravityDeviceG, PointingRecord.Vector3(x: 0, y: 0, z: -1))
        // A sample whose time is not a number is no sample: it neither stands nearest nor hides the one that is
        var untimed = capture()
        untimed.motion.samples[0].bootClockSec = .nan
        XCTAssertEqual(try built(untimed).motion.sampleOffsetFromPhotoSec, -0.004, accuracy: tolerance)
        // At the limit, taken; just past it, refused
        var edge = capture()
        edge.motion.samples = [edge.motion.samples[0]]
        edge.motion.samples[0].bootClockSec = 0.05
        edge.photoBootClockSec = 0
        edge.motion.windowStartOffsetSec = 0
        edge.motion.windowEndOffsetSec = 0.05
        XCTAssertNotNil(try? PointingRecordBuilder.build(edge))
        edge.motion.samples[0].bootClockSec = 0.05.nextUp
        XCTAssertNotNil(refusal(edge))
    }

    func testRefusesAGravityOffOneGAndAnAttitudeThatIsNotAUnitQuaternion() {
        var metres = capture()
        metres.motion.samples = metres.motion.samples.map { var sample = $0; sample.gravityG = .init(x: 0, y: -5.886, z: -7.848); return sample }
        guard case .gravityOutsideBand(let length)? = refusal(metres) else { return XCTFail() }
        XCTAssertEqual(length, 9.81, accuracy: 1e-9)
        var scaled = capture()
        scaled.motion.samples = scaled.motion.samples.map { var sample = $0; sample.quaternion = .init(x: 0.4, y: -0.8, z: 0.8, w: 1.6); return sample }
        guard case .attitudeNotUnitQuaternion(let norm)? = refusal(scaled) else { return XCTFail() }
        XCTAssertEqual(norm, 2, accuracy: 1e-9)
        // Without a heading the attitude is not in the record, so it is not judged
        scaled.location = .notAllowed
        XCTAssertNotNil(try? PointingRecordBuilder.build(scaled))
        var spinning = capture()
        spinning.motion.samples[50].rotationRateRadPerSec.x = .nan
        XCTAssertEqual(refusal(spinning), .motionReadingNotFinite)
    }

    func testCountsTheWindowsSamplesAndItsFastestRotation() throws {
        var fast = capture()
        fast.motion.samples[700].rotationRateRadPerSec = .init(x: 0.3, y: 0.4, z: 0)
        XCTAssertEqual(try built(fast).motion.window.maxRotationRateRadPerSec, 0.5, accuracy: tolerance)
        // A faster sample outside the window does not count
        var outside = capture()
        outside.motion.samples[0].rotationRateRadPerSec = .init(x: 3, y: 4, z: 0)
        XCTAssertLessThan(try built(outside).motion.window.maxRotationRateRadPerSec, 0.01)
        var backwards = capture()
        backwards.motion.windowEndOffsetSec = -0.6
        XCTAssertEqual(refusal(backwards), .motionWindowBackwards)
        var empty = capture()
        empty.motion.windowStartOffsetSec = 20
        empty.motion.windowEndOffsetSec = 21
        XCTAssertEqual(refusal(empty), .motionWindowEmpty)
    }

    func testTakesTheDeviceLabelsOnlyAsTheirPatternsSoNoNameIsEverSent() {
        for (model, version, field) in [("My iPhone", "27.1", "hardwareModel"), ("arm64", "27.1", "hardwareModel"),
                                        ("iPhone18,1", "27 beta", "systemVersion"), ("iPhone18,1", "", "systemVersion")] {
            var named = capture()
            named.device = DeviceLabels(hardwareModel: model, systemVersion: version)
            XCTAssertEqual(refusal(named), .deviceLabelOutsidePattern(field: field), model)
        }
    }

    func testRefusesAHashThatIsNotASha256AndAClockOutsideTheContractsYears() {
        for bad in [String(repeating: "0", count: 64), "ABC", String(repeating: "A", count: 64)] {
            var sample = self.capture()
            sample.photoSha256 = bad
            XCTAssertEqual(refusal(sample), .photoHashNotASha256)
        }
        var future = capture()
        future.photoDeviceClock = Date(timeIntervalSince1970: 253_402_300_800)
        XCTAssertEqual(refusal(future), .deviceClockOutOfRange)
        var untimedPhoto = capture()
        untimedPhoto.photoBootClockSec = .nan
        XCTAssertEqual(refusal(untimedPhoto), .photoBootClockNotFinite)
    }

    func testRefusesAHeadingReadingOutsideAppleValuesAndCallsOnlyANegativeOneInvalid() throws {
        let readings: [(Double, Double, PointingBuildError?, PointingRecord.Heading?)] = [
            (.nan, 12, .headingOutOfRange, nil), (360, 12, .headingOutOfRange, nil), (212.5, .nan, .headingOutOfRange, nil),
            (212.5, .infinity, .headingOutOfRange, nil), (-1, 12, nil, .absent(.headingInvalid)),
            (-.infinity, 12, nil, .absent(.headingInvalid)), (212.5, -1, nil, .absent(.headingInvalid)),
            ((360.0).nextDown, 12, nil, nil), (0, 12, nil, nil),
        ]
        for (trueHeading, accuracy, refused, heading) in readings {
            var sample = capture()
            sample.heading?.trueHeadingDeg = trueHeading
            sample.heading?.headingAccuracyDeg = accuracy
            XCTAssertEqual(refusal(sample), refused, "\(trueHeading) \(accuracy)")
            if refused == nil {
                let built = try built(sample).heading
                if let heading { XCTAssertEqual(built, heading) } else { XCTAssertTrue(built.isMeasured, "\(trueHeading)") }
            }
        }
    }
}

extension PointingRecord.Heading {
    var isMeasured: Bool { if case .measured = self { true } else { false } }
}
