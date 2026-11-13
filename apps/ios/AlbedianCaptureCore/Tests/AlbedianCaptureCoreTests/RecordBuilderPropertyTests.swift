import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// Generated sensor readings, valid and faulty: the builder either writes a record the contract takes, with the
/// heading and place reasons an oracle written from the contract's words expects and the readings carried as given,
/// or refuses with the reason the oracle expects. The generator is seeded (`propertySeed()`, ALBEDIAN_SWIFT_SEED).
final class RecordBuilderPropertyTests: XCTestCase {
    private enum Expected: Equatable {
        case refused(String)
        case built(heading: String, place: String)
    }

    private struct Drawn {
        var capture: SensorCapture
        var note: String
    }

    private func draw<G: RandomNumberGenerator>(_ g: inout G) -> Drawn {
        let hash = PhotoHash.sha256Hex(Data([UInt8(g.int(in: 0...255))]))
        var options = SampleCapture.Options()
        options.motionRunning = false
        var capture = SampleCapture.capture(photoSha256: hash, options: options)
        let photo = capture.photoBootClockSec
        let frame: MotionReferenceFrame = g.int(in: 0...4) == 0 ? .other : .xTrueNorthZVertical
        // Samples: some anywhere in the second either side of a ten-second window, and usually one near the photo
        var offsets = (0..<g.int(in: 0...12)).map { _ in g.double(in: -1.5...11.5) }
        if g.int(in: 0...4) != 0 { offsets.append(g.double(in: -0.08...0.08)) }
        func unitQuaternion() -> PointingRecord.Quaternion {
            let raw = (0..<4).map { _ in g.double(in: -1...1) }
            let norm = raw.map { $0 * $0 }.reduce(0, +).squareRoot()
            return norm < 0.1 ? .init(x: 0, y: 0, z: 0, w: 1) : .init(x: raw[0] / norm, y: raw[1] / norm, z: raw[2] / norm, w: raw[3] / norm)
        }
        let quaternionScale = g.int(in: 0...5) == 0 ? g.pick([g.double(in: 0.5...0.99), g.double(in: 1.01...2)]) : 1
        let gravityScale = g.int(in: 0...5) == 0 ? g.pick([g.double(in: 0...0.85), g.double(in: 1.15...12)]) : g.double(in: 0.95...1.05)
        capture.motion.samples = offsets.map { offset in
            let q = unitQuaternion()
            let down = PointingRecord.Vector3(x: g.double(in: -1...1), y: g.double(in: -1...1), z: g.double(in: -1...1))
            let length = max(down.length, 1e-3)
            return MotionSample(bootClockSec: photo + offset, referenceFrame: frame,
                                quaternion: .init(x: q.x * quaternionScale, y: q.y * quaternionScale, z: q.z * quaternionScale, w: q.w * quaternionScale),
                                gravityG: .init(x: down.x / length * gravityScale, y: down.y / length * gravityScale, z: down.z / length * gravityScale),
                                rotationRateRadPerSec: .init(x: g.double(in: -2...2), y: g.double(in: -2...2), z: g.double(in: -2...2)))
        }
        capture.motion.windowStartOffsetSec = g.double(in: -1...1)
        capture.motion.windowEndOffsetSec = capture.motion.windowStartOffsetSec + g.double(in: -0.5...11)
        if g.int(in: 0...4) == 0 {
            capture.heading = nil
        } else {
            capture.heading = HeadingReading(trueHeadingDeg: g.int(in: 0...9) == 0 ? -1 : g.double(in: 0...359.9),
                                             headingAccuracyDeg: g.double(in: -5...40), orientation: g.pick(HeadingOrientation.allCases),
                                             deviceClock: SampleCapture.moment.addingTimeInterval(g.double(in: -30...0)))
        }
        switch g.int(in: 0...9) {
        case 0, 1:
            capture.location = .notAllowed
        case 2:
            capture.location = .allowed(g.pick(AccuracyAuthorization.allCases), fix: nil)
        default:
            let fix = LocationFix(latDeg: g.double(in: 20...40), lonDeg: g.double(in: -50 ... -30),
                                  horizontalAccuracyM: g.int(in: 0...9) == 0 ? -1 : g.double(in: 0...3000),
                                  altitudeM: g.double(in: -10...50), ellipsoidalAltitudeM: g.double(in: 0...80),
                                  verticalAccuracyM: g.double(in: -5...10), deviceClock: SampleCapture.moment.addingTimeInterval(g.double(in: -60...1)),
                                  isSimulatedBySoftware: g.int(in: 0...9) == 0)
            capture.location = .allowed(g.pick(AccuracyAuthorization.allCases), fix: fix)
        }
        if g.int(in: 0...4) == 0 {
            capture.camera.videoFieldOfViewDeg = 0
            capture.camera.distortionCorrectedFieldOfViewDeg = 0
        } else {
            capture.camera.videoFieldOfViewDeg = g.double(in: 1...150)
            capture.camera.distortionCorrectedFieldOfViewDeg = g.double(in: 1...150)
        }
        capture.camera.geometricDistortionCorrectionEnabled = g.bool()
        return Drawn(capture: capture, note: "offsets \(offsets) q \(quaternionScale) g \(gravityScale)")
    }

    /// The outcome the contract's words call for, in the builder's documented order of checks.
    private func expected(_ capture: SensorCapture) -> Expected {
        let offsets = capture.motion.samples.map { $0.bootClockSec - capture.photoBootClockSec }
        guard let nearest = offsets.indices.min(by: { abs(offsets[$0]) < abs(offsets[$1]) }),
              abs(offsets[nearest]) <= PointingContract.motionSampleOffsetLimitSec else { return .refused("noMotionSampleAtThePhoto") }
        let sample = capture.motion.samples[nearest]
        let gravity = sample.gravityG.length
        if gravity < PointingContract.gravityLengthMinG || gravity > PointingContract.gravityLengthMaxG { return .refused("gravityOutsideBand") }
        let start = capture.motion.windowStartOffsetSec
        let end = capture.motion.windowEndOffsetSec
        if end < start { return .refused("motionWindowBackwards") }
        if !offsets.contains(where: { start <= $0 && $0 <= end }) { return .refused("motionWindowEmpty") }
        var place = "allowed"
        switch capture.location {
        case .notAllowed: place = "not_allowed"
        case .allowed(_, nil): place = "no_fix"
        case .allowed(_, let fix?):
            if fix.isSimulatedBySoftware { place = "simulated_by_software" } else if fix.horizontalAccuracyM < 0 { place = "no_fix" }
        }
        let heading: String
        if sample.referenceFrame != .xTrueNorthZVertical { heading = "no_magnetometer" }
        else if place != "allowed" { heading = "no_place" }
        else if let reading = capture.heading {
            if reading.trueHeadingDeg < 0 || reading.headingAccuracyDeg < 0 { heading = "heading_invalid" }
            else if reading.headingAccuracyDeg > PointingContract.headingAccuracyLimitDeg { heading = "accuracy_outside_limit" }
            else if abs(sample.quaternion.norm - 1) > PointingContract.unitQuaternionTolerance { return .refused("attitudeNotUnitQuaternion") }
            else { heading = "measured" }
        } else { heading = "no_heading_event" }
        return .built(heading: heading, place: place)
    }

    private func name(_ error: PointingBuildError) -> String {
        String(describing: error).split(separator: "(").first.map(String.init) ?? "?"
    }

    func testBuiltRecordsKeepEveryRuleAndRefusalsNameTheirReason() throws {
        let seed = propertySeed()
        var generator = SeededGenerator(seed: seed)
        var outcomes: [String: Int] = [:]
        for index in 0..<600 {
            let drawn = draw(&generator)
            let want = expected(drawn.capture)
            let context = "seed \(seed) case \(index): \(drawn.note)"
            switch Result(catching: { () throws(PointingBuildError) -> PointingRecord in try PointingRecordBuilder.build(drawn.capture) }) {
            case .success(let record):
                let wire = try record.wireValue()
                XCTAssertEqual(PointingValidator.issues(in: wire), [], context)
                let heading = wire["heading"]?["state"]?.stringValue == "measured" ? "measured" : (wire["heading"]?["reason"]?.stringValue ?? "?")
                let place = wire["location"]?["state"]?.stringValue == "allowed" ? "allowed" : (wire["location"]?["reason"]?.stringValue ?? "?")
                XCTAssertEqual(Expected.built(heading: heading, place: place), want, context)
                outcomes[heading, default: 0] += 1
                // Readings carried as given: the heading and its accuracy, the place, the field of view
                if case .measured(let measured) = record.heading, let reading = drawn.capture.heading {
                    XCTAssertEqual(measured.trueHeadingDeg, reading.trueHeadingDeg, context)
                    XCTAssertEqual(measured.headingAccuracyDeg, reading.headingAccuracyDeg, context)
                    XCTAssertEqual(measured.headingOrientation, reading.orientation, context)
                }
                if case .absent(.accuracyOutsideLimit(let reported)) = record.heading {
                    XCTAssertEqual(reported, drawn.capture.heading?.headingAccuracyDeg, context)
                }
                if case .allowed(let allowed) = record.location, case .allowed(let authorization, let fix?) = drawn.capture.location {
                    XCTAssertEqual(allowed.latDeg, fix.latDeg, context)
                    XCTAssertEqual(allowed.lonDeg, fix.lonDeg, context)
                    XCTAssertEqual(allowed.horizontalAccuracyM, fix.horizontalAccuracyM, context)
                    XCTAssertEqual(allowed.accuracyAuthorization, authorization, context)
                    XCTAssertEqual(allowed.altitude != nil, fix.verticalAccuracyM > 0, context)
                }
                let camera = drawn.capture.camera
                let reported = camera.geometricDistortionCorrectionEnabled ? camera.distortionCorrectedFieldOfViewDeg : camera.videoFieldOfViewDeg
                XCTAssertEqual(record.camera.fieldOfView,
                               reported == 0 ? .unknown : .reported(horizontalDeg: reported, distortionCorrected: camera.geometricDistortionCorrectionEnabled),
                               context)
            case .failure(let error):
                XCTAssertEqual(Expected.refused(name(error)), want, context)
                outcomes[name(error), default: 0] += 1
            }
        }
        // The draws reach a measured heading, every reason for none, and refusals
        for outcome in ["measured", "no_magnetometer", "no_place", "no_heading_event", "heading_invalid", "accuracy_outside_limit",
                        "noMotionSampleAtThePhoto", "gravityOutsideBand", "motionWindowBackwards"] {
            XCTAssertGreaterThan(outcomes[outcome, default: 0], 0, "seed \(seed): no \(outcome) in \(outcomes)")
        }
    }
}
