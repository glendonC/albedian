import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The builder's own bounds, each at its inside end and a step outside, with the exact outcome the builder gives
/// there. The contract's check at the end of the build would refuse most outside values too, but in other words, so
/// each test names the refusal the builder itself owes. All readings are synthetic (SampleCapture).
final class RecordBuilderEdgeTests: XCTestCase {
    private let photoHash = PhotoHash.sha256Hex(Data("synthetic photo bytes".utf8))
    private let photoClock = SampleCapture.photoBootClockSec
    /// 2^-10 s: a step that adds to and subtracts from the photo's boot clock time exactly.
    private let step = 0.0009765625

    private func capture(_ change: (inout SensorCapture) -> Void) -> SensorCapture {
        var capture = SampleCapture.capture(photoSha256: photoHash)
        change(&capture)
        return capture
    }

    private func outcome(_ capture: SensorCapture) -> Result<PointingRecord, PointingBuildError> {
        do { return .success(try PointingRecordBuilder.build(capture)) } catch { return .failure(error) }
    }

    private func record(_ capture: SensorCapture, file: StaticString = #filePath, line: UInt = #line) -> PointingRecord? {
        switch outcome(capture) {
        case .success(let record): return record
        case .failure(let error): XCTFail("not built: \(error)", file: file, line: line); return nil
        }
    }

    private func refusal(_ capture: SensorCapture) -> PointingBuildError? {
        if case .failure(let error) = outcome(capture) { error } else { nil }
    }

    /// Samples at the given offsets from the photo, all in the true north frame, with one gravity and rotation rate.
    private func samples(_ offsets: [Double], gravity: PointingRecord.Vector3 = .init(x: 0, y: -0.6, z: -0.8),
                         rate: PointingRecord.Vector3 = .init(x: 0.002, y: -0.001, z: 0.0005)) -> [MotionSample] {
        offsets.map { offset in
            MotionSample(bootClockSec: photoClock + offset, referenceFrame: .xTrueNorthZVertical,
                         quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8), gravityG: gravity, rotationRateRadPerSec: rate)
        }
    }

    func testTakesEveryCameraDimensionFromOneToItsMaximumAndNoFurther() {
        let maxSide = PointingContract.dimensionMaxPx
        XCTAssertEqual(maxSide, 32768)
        let sides: [(WritableKeyPath<SensorCapture, Int>, String)] = [
            (\.camera.activeFormatWidthPx, "activeFormatDimensionsPx"), (\.camera.activeFormatHeightPx, "activeFormatDimensionsPx"),
            (\.camera.photoWidthPx, "photoDimensionsPx"), (\.camera.photoHeightPx, "photoDimensionsPx"),
        ]
        for (side, field) in sides {
            for inside in [1, maxSide] {
                let built = record(capture { $0[keyPath: side] = inside })
                let dimensions = field == "photoDimensionsPx" ? built?.camera.photoDimensionsPx : built?.camera.activeFormatDimensionsPx
                XCTAssertTrue([dimensions?.width, dimensions?.height].contains(inside), "\(field) \(inside)")
            }
            for outside in [0, -1, maxSide + 1] {
                XCTAssertEqual(refusal(capture { $0[keyPath: side] = outside }), .cameraValueOutOfRange(field: field), "\(field) \(outside)")
            }
        }
    }

    func testTakesAFieldOfViewAboveZeroAndBelowOneHundredEightyDegrees() {
        func withFieldOfView(_ degrees: Double) -> SensorCapture {
            capture {
                $0.camera.videoFieldOfViewDeg = degrees
                $0.camera.distortionCorrectedFieldOfViewDeg = degrees
            }
        }
        for inside in [Double.leastNonzeroMagnitude, 0.5, 1, 179.5, Double(180).nextDown] {
            XCTAssertEqual(record(withFieldOfView(inside))?.camera.fieldOfView, .reported(horizontalDeg: inside, distortionCorrected: true), "\(inside)")
        }
        XCTAssertEqual(record(withFieldOfView(0))?.camera.fieldOfView, .unknown)
        for outside in [-Double.leastNonzeroMagnitude, -0.5, -1, 180, 180.5, .infinity, -.infinity, .nan] {
            XCTAssertEqual(refusal(withFieldOfView(outside)), .cameraValueOutOfRange(field: "fieldOfView.horizontalDeg"), "\(outside)")
        }
        for zoom in [1, 1.5, 1e9] { XCTAssertEqual(record(capture { $0.camera.videoZoomFactor = zoom })?.camera.videoZoomFactor, zoom) }
        for zoom in [Double(1).nextDown, 0, -1, .infinity, .nan] {
            XCTAssertEqual(refusal(capture { $0.camera.videoZoomFactor = zoom }), .cameraValueOutOfRange(field: "videoZoomFactor"), "\(zoom)")
        }
    }

    func testGivesATieForTheNearestSampleToTheEarlierOne() {
        // Two samples equally far from the photo, before and after it: the one listed first (CoreMotion's stream comes
        // in time order) is the photo's sample, whichever way they are listed
        let before = PointingRecord.Vector3(x: 0, y: -0.6, z: -0.8)
        let after = PointingRecord.Vector3(x: 0, y: -0.8, z: -0.6)
        let early = samples([-4 * step], gravity: before)
        let late = samples([4 * step], gravity: after)
        for (listed, offset, gravity) in [(early + late, -4 * step, before), (late + early, 4 * step, after)] {
            let built = record(capture {
                $0.motion = MotionReading(samples: listed, windowStartOffsetSec: -8 * step, windowEndOffsetSec: 8 * step)
            })
            XCTAssertEqual(built?.motion.sampleOffsetFromPhotoSec, offset)
            XCTAssertEqual(built?.motion.gravityDeviceG, gravity)
            XCTAssertEqual(built?.motion.window.sampleCount, 2)
        }
    }

    func testRefusesAMotionReadingThatIsNotFiniteAsSuch() {
        // At the photo's sample: gravity, then rotation rate
        for (index, keyPath) in [\PointingRecord.Vector3.x, \.y, \.z].enumerated() {
            for value in [Double.nan, .infinity, -.infinity] {
                var gravity = PointingRecord.Vector3(x: 0, y: -0.6, z: -0.8)
                gravity[keyPath: keyPath] = value
                XCTAssertEqual(refusal(capture { $0.motion.samples = samples([0], gravity: gravity) }), .motionReadingNotFinite, "gravity \(index) \(value)")
                var rate = PointingRecord.Vector3(x: 0, y: 0, z: 0)
                rate[keyPath: keyPath] = value
                XCTAssertEqual(refusal(capture { $0.motion.samples = samples([0], rate: rate) }), .motionReadingNotFinite, "rate \(index) \(value)")
            }
        }
        // At another sample of the window, whose rotation rate only goes into the window's fastest
        for value in [Double.infinity, -.infinity, .nan] {
            let refused = refusal(capture {
                $0.motion.samples = samples([0]) + samples([16 * step], rate: .init(x: value, y: 0, z: 0))
            })
            XCTAssertEqual(refused, .motionReadingNotFinite, "\(value)")
        }
    }

    func testTakesGravityFromItsLowerToItsUpperBandEdge() {
        let low = PointingContract.gravityLengthMinG
        let high = PointingContract.gravityLengthMaxG
        XCTAssertEqual([low, high], [0.9, 1.1])
        // The length of (0, 0, -g) is g exactly: the square root of a rounded square is the number itself in IEEE doubles
        for inside in [low, high, 1] {
            let gravity = PointingRecord.Vector3(x: 0, y: 0, z: -inside)
            XCTAssertEqual(gravity.length, inside)
            XCTAssertEqual(record(capture { $0.motion.samples = samples([0], gravity: gravity) })?.motion.gravityDeviceG, gravity, "\(inside)")
        }
        for outside in [low.nextDown, high.nextUp, 0, 2] {
            let gravity = PointingRecord.Vector3(x: 0, y: 0, z: -outside)
            XCTAssertEqual(refusal(capture { $0.motion.samples = samples([0], gravity: gravity) }), .gravityOutsideBand(lengthG: outside), "\(outside)")
        }
    }

    func testTakesAWindowWhoseEndsAreFiniteAndInOrderAndCountsTheSamplesOnItsEnds() {
        for (start, end) in [(-Double.infinity, 1.0), (-1, .infinity), (.nan, 1), (-1, .nan), (-.infinity, .infinity)] {
            XCTAssertEqual(refusal(capture {
                $0.motion.windowStartOffsetSec = start
                $0.motion.windowEndOffsetSec = end
            }), .offsetNotFinite, "\(start) \(end)")
        }
        // A window of no length at the photo's own sample holds that sample
        let atThePhoto = record(capture {
            $0.motion = MotionReading(samples: samples([-step, 0, step]), windowStartOffsetSec: 0, windowEndOffsetSec: 0)
        })
        XCTAssertEqual(atThePhoto?.motion.window.sampleCount, 1)
        // Samples on both ends are in it
        let ends = record(capture {
            $0.motion = MotionReading(samples: samples([-2 * step, -step, 0, step, 2 * step]), windowStartOffsetSec: -step, windowEndOffsetSec: step)
        })
        XCTAssertEqual(ends?.motion.window.sampleCount, 3)
        XCTAssertEqual(refusal(capture {
            $0.motion = MotionReading(samples: samples([0]), windowStartOffsetSec: 0, windowEndOffsetSec: Double(0).nextDown)
        }), .motionWindowBackwards)
    }

    func testCountsAWindowUpToTheContractsMaximumAndRefusesOneMore() {
        let maximum = PointingContract.sampleCountMax
        XCTAssertEqual(maximum, 360_000)
        for count in [maximum, maximum + 1] {
            let offsets = (0..<count).map { Double($0) * step }
            let window = MotionReading(samples: samples(offsets), windowStartOffsetSec: 0, windowEndOffsetSec: offsets.last!)
            switch outcome(capture { $0.motion = window }) {
            case .success(let built):
                XCTAssertEqual(count, maximum)
                XCTAssertEqual(built.motion.window.sampleCount, maximum)
            case .failure(let error):
                XCTAssertEqual(count, maximum + 1, "\(error)")
                XCTAssertEqual(error, .motionWindowTooManySamples(count: maximum + 1))
            }
        }
    }

    func testWritesAWindowOfStillSamplesWithAFastestRateOfZero() {
        let still = record(capture { $0.motion.samples = samples([-step, 0, step], rate: .init(x: 0, y: 0, z: 0)) })
        XCTAssertEqual(still?.motion.window.maxRotationRateRadPerSec, 0)
        XCTAssertEqual(still?.motion.window.maxRotationRateRadPerSec.sign, .plus)
    }

    func testTakesAPlaceOnTheEdgesOfWGS84AndAnAccuracyOfZero() {
        func withFix(_ change: @escaping (inout LocationFix) -> Void) -> SensorCapture {
            capture { capture in
                guard case .allowed(let authorization, let fix?) = capture.location else { return XCTFail("no sample fix") }
                var changed = fix
                change(&changed)
                capture.location = .allowed(authorization, fix: changed)
            }
        }
        func place(_ capture: SensorCapture) -> PointingRecord.AllowedPlace? {
            if case .allowed(let place)? = record(capture)?.location { place } else { nil }
        }
        for latitude in [-90.0, 90, 0] { XCTAssertEqual(place(withFix { $0.latDeg = latitude })?.latDeg, latitude) }
        for longitude in [-180.0, 180, 0] { XCTAssertEqual(place(withFix { $0.lonDeg = longitude })?.lonDeg, longitude) }
        for accuracy in [0.0, 0.5, 1] { XCTAssertEqual(place(withFix { $0.horizontalAccuracyM = accuracy })?.horizontalAccuracyM, accuracy) }
        let outside: [(String, (inout LocationFix) -> Void)] = [
            ("latitude below", { $0.latDeg = Double(-90).nextDown }), ("latitude above", { $0.latDeg = Double(90).nextUp }),
            ("longitude below", { $0.lonDeg = Double(-180).nextDown }), ("longitude above", { $0.lonDeg = Double(180).nextUp }),
            ("accuracy below", { $0.horizontalAccuracyM = -Double.leastNonzeroMagnitude }), ("accuracy -1", { $0.horizontalAccuracyM = -1 }),
        ]
        for (name, change) in outside {
            XCTAssertEqual(record(withFix(change))?.location, .absent(.noFix), name)
        }
    }

    func testTakesTheDeviceLabelsAtTheEndsOfTheirLetterAndDigitRuns() {
        // Every letter and digit at the ends of its run, and the unit just outside each end
        for model in ["A0,0", "Z9,9", "a1,2", "z1,2", "AZaz09,90", "Zz1,1"] { XCTAssertTrue(PointingLabels.isHardwareModel(model), model) }
        for model in ["@1,1", "[1,1", "`1,1", "{1,1", "A/,1", "A:,1", "A1,/", "A1,:", "A1,1,", "1,1", "A,1", "A1,", "A1"] {
            XCTAssertFalse(PointingLabels.isHardwareModel(model), model)
        }
        for version in ["0", "9", "1234", "1.2", "1.2.3", "1234.1234.1234", "26.5"] { XCTAssertTrue(PointingLabels.isSystemVersion(version), version) }
        for version in ["", "12345", "1.2.3.4", "1..2", ".1", "1.", "1/2", "1:2", "a", "1.12345"] {
            XCTAssertFalse(PointingLabels.isSystemVersion(version), version)
        }
    }

    func testRefusesAHeadingEventWhoseTimeGivesNoFiniteOffset() {
        XCTAssertEqual(refusal(capture {
            $0.heading?.deviceClock = Date(timeIntervalSinceReferenceDate: .infinity)
        }), .offsetNotFinite)
    }
}
