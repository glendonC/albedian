import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The ring of device-motion samples the app keeps until the shutter (tasks.md 7): the newest samples in time order,
/// a count of every sample pushed out or refused, and the samples a photo's window needs. Synthetic samples.
final class MotionRingTests: XCTestCase {
    /// 2^-7 s, near 100 Hz, so sums of steps are exact.
    private let step = 0.0078125

    private func sample(_ time: Double, gravityX: Double = 0) -> MotionSample {
        MotionSample(bootClockSec: time, referenceFrame: .xTrueNorthZVertical, quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                     gravityG: .init(x: gravityX, y: -0.6, z: -0.8), rotationRateRadPerSec: .init(x: 0, y: 0, z: 0))
    }

    func testHoldsFromOneSampleUpToTheContractsMaximumAndNoOtherSize() {
        // 6000 samples: 60 s at 100 Hz [proposed]; the window of one photo needs far less
        XCTAssertEqual(MotionRing.defaultCapacity, 6000)
        XCTAssertEqual(MotionRing()?.capacity, 6000)
        for capacity in [1, 2, 6000, PointingContract.sampleCountMax] { XCTAssertEqual(MotionRing(capacity: capacity)?.capacity, capacity) }
        for capacity in [0, -1, PointingContract.sampleCountMax + 1, Int.max, Int.min] { XCTAssertNil(MotionRing(capacity: capacity), "\(capacity)") }
    }

    func testKeepsTheNewestSamplesInTimeOrderAndCountsThoseItPushedOut() throws {
        var ring = try XCTUnwrap(MotionRing(capacity: 3))
        XCTAssertEqual(ring.count, 0)
        XCTAssertEqual(ring.samples, [])
        for index in 0..<3 { XCTAssertTrue(ring.append(sample(Double(index) * step))) }
        XCTAssertEqual(ring.samples.map(\.bootClockSec), [0, step, 2 * step])
        XCTAssertEqual(ring.overwrittenCount, 0)
        for index in 3..<8 { XCTAssertTrue(ring.append(sample(Double(index) * step))) }
        XCTAssertEqual(ring.count, 3)
        XCTAssertEqual(ring.samples.map(\.bootClockSec), [5 * step, 6 * step, 7 * step])
        XCTAssertEqual(ring.overwrittenCount, 5)
        XCTAssertEqual(ring.refusedCount, 0)
        var one = try XCTUnwrap(MotionRing(capacity: 1))
        one.append(sample(0))
        one.append(sample(step, gravityX: 0.01))
        XCTAssertEqual(one.samples, [sample(step, gravityX: 0.01)])
        XCTAssertEqual(one.overwrittenCount, 1)
    }

    func testRefusesASampleWithNoFiniteTimeOrNoLaterThanTheLastAndKeepsTheRest() throws {
        var ring = try XCTUnwrap(MotionRing(capacity: 4))
        XCTAssertTrue(ring.append(sample(10)))
        for time in [Double.nan, .infinity, -.infinity, 10, 10 - step, 0] {
            XCTAssertFalse(ring.append(sample(time)), "\(time)")
        }
        XCTAssertEqual(ring.refusedCount, 6)
        XCTAssertTrue(ring.append(sample(10.nextUp)))
        XCTAssertEqual(ring.samples.map(\.bootClockSec), [10, 10.nextUp])
        // A refused sample takes no place and pushes nothing out
        XCTAssertEqual(ring.overwrittenCount, 0)
        var first = try XCTUnwrap(MotionRing(capacity: 2))
        XCTAssertFalse(first.append(sample(.nan)))
        XCTAssertTrue(first.append(sample(-5)))
        XCTAssertEqual(first.samples.map(\.bootClockSec), [-5])
    }

    func testGivesAPhotoTheSamplesOfItsWindowAndThoseNearItsTime() throws {
        var ring = try XCTUnwrap(MotionRing(capacity: 100))
        let photo = 64.0
        for index in -40...40 { ring.append(sample(photo + Double(index) * step)) }
        // The window's samples, both ends included
        let window = ring.reading(photoBootClockSec: photo, windowStartOffsetSec: -8 * step, windowEndOffsetSec: 16 * step)
        XCTAssertEqual(window.windowStartOffsetSec, -8 * step)
        XCTAssertEqual(window.windowEndOffsetSec, 16 * step)
        XCTAssertEqual(window.samples.first?.bootClockSec, photo - 8 * step)
        XCTAssertEqual(window.samples.last?.bootClockSec, photo + 16 * step)
        XCTAssertEqual(window.samples.count, 25)
        // The span always reaches the contract's offset limit on both sides of the photo, so the builder can find
        // the photo's sample even when the window does not hold it (0.05 s: 6 steps of 2^-7 s either side)
        let limit = PointingContract.motionSampleOffsetLimitSec
        XCTAssertEqual(limit, 0.05)
        let late = ring.reading(photoBootClockSec: photo, windowStartOffsetSec: 20 * step, windowEndOffsetSec: 24 * step)
        XCTAssertEqual(late.samples.map { ($0.bootClockSec - photo) / step }, (-6...24).map(Double.init))
        let early = ring.reading(photoBootClockSec: photo, windowStartOffsetSec: -30 * step, windowEndOffsetSec: -20 * step)
        XCTAssertEqual(early.samples.map { ($0.bootClockSec - photo) / step }, (-30...6).map(Double.init))
        // A window end that is not finite is replaced by the limit for choosing samples, and kept as given in the
        // reading, so the builder names the window's fault rather than a missing sample
        for (start, end, first, last) in [(Double.nan, 16 * step, -6.0, 16.0), (-16 * step, .infinity, -16, 6), (-.infinity, .nan, -6, 6)] {
            let odd = ring.reading(photoBootClockSec: photo, windowStartOffsetSec: start, windowEndOffsetSec: end)
            XCTAssertEqual(odd.samples.map { ($0.bootClockSec - photo) / step }, (Int(first)...Int(last)).map(Double.init), "\(start) \(end)")
            XCTAssertEqual(odd.windowStartOffsetSec.isNaN, start.isNaN)
            XCTAssertEqual(odd.windowEndOffsetSec.isNaN, end.isNaN)
        }
        let empty = try XCTUnwrap(MotionRing(capacity: 4))
        XCTAssertEqual(empty.reading(photoBootClockSec: photo, windowStartOffsetSec: -1, windowEndOffsetSec: 1).samples, [])
    }

    func testKeepsTheLastSamplesOfAnyStreamAndCountsEverySample() throws {
        // Property over generated streams: each sample is either kept, pushed out, or refused, exactly once
        var generator = SeededGenerator(seed: propertySeed())
        for _ in 0..<300 {
            let capacity = generator.int(in: 1...12)
            var ring = try XCTUnwrap(MotionRing(capacity: capacity))
            var time = 0.0
            var accepted: [Double] = []
            var refused = 0
            for _ in 0..<generator.int(in: 0...40) {
                let candidate: Double
                switch generator.int(in: 0...9) {
                case 0: candidate = .nan
                case 1: candidate = time
                case 2: candidate = time - step
                default: candidate = time + Double(generator.int(in: 1...3)) * step
                }
                let kept = ring.append(sample(candidate))
                if candidate.isFinite, candidate > (accepted.last ?? -.infinity) {
                    XCTAssertTrue(kept)
                    accepted.append(candidate)
                    time = candidate
                } else {
                    XCTAssertFalse(kept)
                    refused += 1
                }
            }
            XCTAssertEqual(ring.samples.map(\.bootClockSec), Array(accepted.suffix(capacity)))
            XCTAssertEqual(ring.refusedCount, refused)
            XCTAssertEqual(ring.overwrittenCount, max(0, accepted.count - capacity))
            XCTAssertEqual(ring.count, min(accepted.count, capacity))
        }
    }
}
