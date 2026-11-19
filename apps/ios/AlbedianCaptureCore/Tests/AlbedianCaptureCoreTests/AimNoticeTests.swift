import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// D8, INTERIM (UIUX-iphone-aim-QUESTION): the line under the aiming preview when the photo's record would carry no
/// place or no heading, in the pointing record's own words, the place first since true north is found from it.
final class AimNoticeTests: XCTestCase {
    private let placed = PointingRecord.Place.allowed(PointingRecord.AllowedPlace(
        latDeg: 30, lonDeg: -40, horizontalAccuracyM: 8, accuracyAuthorization: .full, altitude: nil, fixOffsetFromPhotoSec: 0))

    func testEachMissingPlaceSaysWhy() {
        XCTAssertEqual(AimNotice.line(place: .absent(.notAllowed), heading: nil), "No place: location not allowed")
        XCTAssertEqual(AimNotice.line(place: .absent(.noFix), heading: nil), "No place: no fix")
        XCTAssertEqual(AimNotice.line(place: .absent(.simulatedBySoftware), heading: .absent(.noPlace)), "No place: simulated by software")
    }

    func testEachMissingHeadingSaysWhyOnceThereIsAPlace() {
        XCTAssertEqual(AimNotice.line(place: placed, heading: .absent(.accuracyOutsideLimit(reportedDeg: 40))), "No heading: ±40° is past 25°")
        XCTAssertEqual(AimNotice.line(place: placed, heading: .absent(.accuracyOutsideLimit(reportedDeg: 25.4))), "No heading: ±25.4° is past 25°")
        XCTAssertEqual(AimNotice.line(place: placed, heading: .absent(.noMagnetometer)), "No heading: no magnetometer")
        XCTAssertEqual(AimNotice.line(place: placed, heading: .absent(.noHeadingEvent)), "No heading: no heading event")
        XCTAssertEqual(AimNotice.line(place: placed, heading: .absent(.headingInvalid)), "No heading: heading invalid")
    }

    func testNoLineWhenBothAreThereOrTheHeadingIsNotKnownYet() {
        XCTAssertNil(AimNotice.line(place: placed, heading: nil), "no motion sample yet: nothing said")
    }
}
