import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The device clock at the photo, from one pair of readings of the boot clock and the device clock taken together
/// after the shot (tasks.md 8). The photo's own time on the boot clock comes from AVFoundation, moved from the
/// session's synchronization clock with `CMSyncConvertTime` in the app. Values are synthetic powers of two, so every
/// difference is exact.
final class PhotoClockTests: XCTestCase {
    private let moment = SampleCapture.moment

    func testCountsBackFromTheReadingsToThePhoto() {
        XCTAssertEqual(PhotoClock.deviceClock(atBootClockSec: 5000, nowBootClockSec: 5000.5, nowDeviceClock: moment),
                       moment.addingTimeInterval(-0.5))
        XCTAssertEqual(PhotoClock.deviceClock(atBootClockSec: 5000, nowBootClockSec: 5000, nowDeviceClock: moment), moment)
        // Readings taken before the photo's time count forward the same way
        XCTAssertEqual(PhotoClock.deviceClock(atBootClockSec: 5000.25, nowBootClockSec: 5000, nowDeviceClock: moment),
                       moment.addingTimeInterval(0.25))
        XCTAssertEqual(PhotoClock.deviceClock(atBootClockSec: 0, nowBootClockSec: 64, nowDeviceClock: moment), moment.addingTimeInterval(-64))
    }

    func testGivesNoTimeForAReadingThatIsNotANumber() {
        for (photo, now, wall) in [(Double.nan, 1.0, 0.0), (1, .infinity, 0), (-.infinity, 1, 0), (1, 1, .nan), (1, 1, .infinity)] {
            XCTAssertNil(PhotoClock.deviceClock(atBootClockSec: photo, nowBootClockSec: now, nowDeviceClock: Date(timeIntervalSince1970: wall)),
                         "\(photo) \(now) \(wall)")
        }
    }

    func testGivesTheBuilderATimeItWritesToTheMillisecond() throws {
        let clock = try XCTUnwrap(PhotoClock.deviceClock(atBootClockSec: SampleCapture.photoBootClockSec,
                                                         nowBootClockSec: SampleCapture.photoBootClockSec + 0.125, nowDeviceClock: moment))
        XCTAssertEqual(UTCInstant.string(from: clock), "2030-01-01T01:59:59.875Z")
    }
}
