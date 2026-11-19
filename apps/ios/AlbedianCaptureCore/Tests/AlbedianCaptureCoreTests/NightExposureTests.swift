import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// mobile-7 (the lead's answer of 2026-11-18): one long exposure at the format's longest duration, its ISO carried from
/// the meter and capped at the format's range, and the approved hold-still screen's numbers (arrive iPhone 4): the
/// exposure's length, the ring filling over that real exposure, and the second crosshair moved by the gyroscope.
final class NightExposureTests: XCTestCase {
    func testTheExposureIsTheLongestAndItsIsoCarriesTheMetersLightWithinTheFormat() throws {
        // The meter's 1/60 s at ISO 800 carried to 1 s is ISO 13.3 by the same light, under a range of 25 to 6400: 25
        let low = try XCTUnwrap(NightExposure.plan(meterDurationSec: 1.0 / 60, meterISO: 800, maxDurationSec: 1, minISO: 25, maxISO: 6400))
        XCTAssertEqual(low.durationSec, 1)
        XCTAssertEqual(low.iso, 25, accuracy: 1e-9)
        // A dark sky: the meter's 1/4 s at ISO 6400 carried to 1 s is ISO 1600
        let dark = try XCTUnwrap(NightExposure.plan(meterDurationSec: 0.25, meterISO: 6400, maxDurationSec: 1, minISO: 25, maxISO: 6400))
        XCTAssertEqual(dark.iso, 1600, accuracy: 1e-9)
        // The meter already at the longest: the ISO stays
        let same = try XCTUnwrap(NightExposure.plan(meterDurationSec: 1, meterISO: 3200, maxDurationSec: 1, minISO: 25, maxISO: 6400))
        XCTAssertEqual(same.iso, 3200, accuracy: 1e-9)
        // Brighter than the longest exposure can take at the lowest ISO: the lowest ISO; darker than the highest: the highest
        let capped = try XCTUnwrap(NightExposure.plan(meterDurationSec: 2, meterISO: 6400, maxDurationSec: 1, minISO: 25, maxISO: 6400))
        XCTAssertEqual(capped.iso, 6400, accuracy: 1e-9)
    }

    func testNoPlanFromAReadingThatIsNotAPositiveNumber() {
        for bad in [0.0, -1, .nan, .infinity] {
            XCTAssertNil(NightExposure.plan(meterDurationSec: bad, meterISO: 800, maxDurationSec: 1, minISO: 25, maxISO: 6400))
            XCTAssertNil(NightExposure.plan(meterDurationSec: 0.1, meterISO: bad, maxDurationSec: 1, minISO: 25, maxISO: 6400))
            XCTAssertNil(NightExposure.plan(meterDurationSec: 0.1, meterISO: 800, maxDurationSec: bad, minISO: 25, maxISO: 6400))
            XCTAssertNil(NightExposure.plan(meterDurationSec: 0.1, meterISO: 800, maxDurationSec: 1, minISO: bad, maxISO: 6400))
        }
        XCTAssertNil(NightExposure.plan(meterDurationSec: 0.1, meterISO: 800, maxDurationSec: 1, minISO: 6400, maxISO: 25), "a range upside down")
    }

    func testTheRingFillsOverTheRealExposureAndNeverPastIt() {
        XCTAssertEqual(NightExposure.ringFraction(elapsedSec: 0, exposureSec: 1.1), 0)
        XCTAssertEqual(NightExposure.ringFraction(elapsedSec: 0.55, exposureSec: 1.1), 0.5, accuracy: 1e-12)
        XCTAssertEqual(NightExposure.ringFraction(elapsedSec: 2, exposureSec: 1.1), 1)
        XCTAssertEqual(NightExposure.ringFraction(elapsedSec: -1, exposureSec: 1.1), 0)
        XCTAssertEqual(NightExposure.ringFraction(elapsedSec: 0.5, exposureSec: 0), 0, "no length, no fill")
        XCTAssertEqual(NightExposure.ringFraction(elapsedSec: .nan, exposureSec: 1), 0)
    }

    /// The length under the preview, as the mockup writes it ("1.1 s"): tenths, a point, a space and "s".
    func testTheExposureLengthIsWrittenInTenthsOfASecond() {
        XCTAssertEqual(NightExposure.lengthWords(1.1), "1.1 s")
        XCTAssertEqual(NightExposure.lengthWords(1.0 / 3), "0.3 s")
        XCTAssertEqual(NightExposure.lengthWords(0.04), "0.0 s")
        XCTAssertEqual(NightExposure.lengthWords(0.26), "0.3 s", "to the nearest tenth")
        XCTAssertEqual(NightExposure.lengthWords(10), "10.0 s")
        XCTAssertNil(NightExposure.lengthWords(.nan))
        XCTAssertNil(NightExposure.lengthWords(-1))
    }

    private func quaternion(aboutAxis axis: (Double, Double, Double), degrees: Double) -> PointingRecord.Quaternion {
        let half = degrees * .pi / 360
        return PointingRecord.Quaternion(x: axis.0 * sin(half), y: axis.1 * sin(half), z: axis.2 * sin(half), w: cos(half))
    }

    /// The second crosshair moves by the phone's turn since the exposure began, at the preview's own scale (its width over
    /// the field of view): a turn about the device's Y axis moves it across, about its X axis up and down, about Z (the
    /// lens's axis) not at all. Which way each way goes rests on the quaternion's direction, an iPhone check [open].
    func testTheSecondCrosshairMovesByTheTurnSinceTheExposureBeganAtThePreviewsScale() throws {
        let start = quaternion(aboutAxis: (0, 0, 1), degrees: 30)
        let still = try XCTUnwrap(NightExposure.crosshairOffset(start: start, now: start, previewWidthPt: 390, horizontalFovDeg: 69.5))
        XCTAssertEqual(still.dx, 0, accuracy: 1e-9)
        XCTAssertEqual(still.dy, 0, accuracy: 1e-9)
        let focal = 195 / tan(69.5 * .pi / 360)
        // One degree about the device's Y axis, after the start
        let yawed = NightExposure.crosshairOffset(start: PointingRecord.Quaternion(x: 0, y: 0, z: 0, w: 1),
                                                  now: quaternion(aboutAxis: (0, 1, 0), degrees: 1), previewWidthPt: 390, horizontalFovDeg: 69.5)
        XCTAssertEqual(abs(try XCTUnwrap(yawed).dx), focal * tan(.pi / 180), accuracy: 1e-6)
        XCTAssertEqual(try XCTUnwrap(yawed).dy, 0, accuracy: 1e-9)
        let pitched = NightExposure.crosshairOffset(start: PointingRecord.Quaternion(x: 0, y: 0, z: 0, w: 1),
                                                    now: quaternion(aboutAxis: (1, 0, 0), degrees: 1), previewWidthPt: 390, horizontalFovDeg: 69.5)
        XCTAssertEqual(abs(try XCTUnwrap(pitched).dy), focal * tan(.pi / 180), accuracy: 1e-6)
        XCTAssertEqual(try XCTUnwrap(pitched).dx, 0, accuracy: 1e-9)
        let rolled = NightExposure.crosshairOffset(start: PointingRecord.Quaternion(x: 0, y: 0, z: 0, w: 1),
                                                   now: quaternion(aboutAxis: (0, 0, 1), degrees: 5), previewWidthPt: 390, horizontalFovDeg: 69.5)
        XCTAssertEqual(try XCTUnwrap(rolled).dx, 0, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(rolled).dy, 0, accuracy: 1e-9)
        // The same turn from another start gives the same move: only the turn since the start counts
        let fromTurned = NightExposure.crosshairOffset(start: start, now: PointingRecord.Quaternion(
            x: 0, y: 0, z: 0, w: 1).multiplied(start).multiplied(quaternion(aboutAxis: (0, 1, 0), degrees: 1)),
                                                       previewWidthPt: 390, horizontalFovDeg: 69.5)
        XCTAssertEqual(abs(try XCTUnwrap(fromTurned).dx), focal * tan(.pi / 180), accuracy: 1e-6)
    }

    func testNoCrosshairMoveWithoutAFieldOfViewOrAnAttitude() {
        let q = PointingRecord.Quaternion(x: 0, y: 0, z: 0, w: 1)
        XCTAssertNil(NightExposure.crosshairOffset(start: nil, now: q, previewWidthPt: 390, horizontalFovDeg: 69.5))
        XCTAssertNil(NightExposure.crosshairOffset(start: q, now: nil, previewWidthPt: 390, horizontalFovDeg: 69.5))
        XCTAssertNil(NightExposure.crosshairOffset(start: q, now: q, previewWidthPt: 390, horizontalFovDeg: 0))
        XCTAssertNil(NightExposure.crosshairOffset(start: q, now: q, previewWidthPt: 390, horizontalFovDeg: 180))
        // A turn of a quarter or more is no small shake: no crosshair rather than one off the preview (100 degrees)
        let big = PointingRecord.Quaternion(x: 0, y: sin(50 * .pi / 180), z: 0, w: cos(50 * .pi / 180))
        let inside = PointingRecord.Quaternion(x: 0, y: sin(40 * .pi / 180), z: 0, w: cos(40 * .pi / 180))
        XCTAssertNotNil(NightExposure.crosshairOffset(start: q, now: inside, previewWidthPt: 390, horizontalFovDeg: 69.5), "80 degrees")
        XCTAssertNil(NightExposure.crosshairOffset(start: q, now: big, previewWidthPt: 390, horizontalFovDeg: 69.5))
    }
}
