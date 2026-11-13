import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The contract instant at the edges of the calendar: the expected texts below were computed with Python's
/// `datetime` (proleptic Gregorian, UTC) by `.lanetools/mobile-0/instant_table.py` of the mobile lane, not by this
/// code. Each instant is taken half a millisecond inside its millisecond, so the double's rounding cannot move it.
final class InstantEdgeTests: XCTestCase {
    static let table: [(Int64, String)] = [
        (-62135596800000, "0001-01-01T00:00:00.000Z"),
        (-62135596799999, "0001-01-01T00:00:00.001Z"),
        (-62104060800001, "0001-12-31T23:59:59.999Z"),
        (-62035891200000, "0004-02-29T00:00:00.000Z"),
        (-59006361600001, "0100-02-28T23:59:59.999Z"),
        (-59006361600000, "0100-03-01T00:00:00.000Z"),
        (-49539297600000, "0400-02-29T12:00:00.000Z"),
        (-12219724800000, "1582-10-10T00:00:00.000Z"),
        (-11670998400000, "1600-02-29T00:00:00.000Z"),
        (-2203891200001, "1900-02-28T23:59:59.999Z"),
        (-2203891200000, "1900-03-01T00:00:00.000Z"),
        (-28670093211, "1969-02-03T04:05:06.789Z"),
        (-1, "1969-12-31T23:59:59.999Z"),
        (0, "1970-01-01T00:00:00.000Z"),
        (1, "1970-01-01T00:00:00.001Z"),
        (946684799999, "1999-12-31T23:59:59.999Z"),
        (951868799999, "2000-02-29T23:59:59.999Z"),
        (951868800000, "2000-03-01T00:00:00.000Z"),
        (981173106789, "2001-02-03T04:05:06.789Z"),
        (1078012800000, "2004-02-29T00:00:00.000Z"),
        (1893463200000, "2030-01-01T02:00:00.000Z"),
        (4107456000000, "2100-02-28T00:00:00.000Z"),
        (4107542400000, "2100-03-01T00:00:00.000Z"),
        (13574563200000, "2400-02-29T00:00:00.000Z"),
        (253402214400000, "9999-12-31T00:00:00.000Z"),
        (253402300799999, "9999-12-31T23:59:59.999Z"),
        (978307200000, "2001-01-01T00:00:00.000Z"),
        (978307199999, "2000-12-31T23:59:59.999Z"),
        (980985600000, "2001-02-01T00:00:00.000Z"),
        (980985599999, "2001-01-31T23:59:59.999Z"),
        (983404800000, "2001-03-01T00:00:00.000Z"),
        (983404799999, "2001-02-28T23:59:59.999Z"),
        (986083200000, "2001-04-01T00:00:00.000Z"),
        (986083199999, "2001-03-31T23:59:59.999Z"),
        (988675200000, "2001-05-01T00:00:00.000Z"),
        (988675199999, "2001-04-30T23:59:59.999Z"),
        (991353600000, "2001-06-01T00:00:00.000Z"),
        (991353599999, "2001-05-31T23:59:59.999Z"),
        (993945600000, "2001-07-01T00:00:00.000Z"),
        (993945599999, "2001-06-30T23:59:59.999Z"),
        (996624000000, "2001-08-01T00:00:00.000Z"),
        (996623999999, "2001-07-31T23:59:59.999Z"),
        (999302400000, "2001-09-01T00:00:00.000Z"),
        (999302399999, "2001-08-31T23:59:59.999Z"),
        (1001894400000, "2001-10-01T00:00:00.000Z"),
        (1001894399999, "2001-09-30T23:59:59.999Z"),
        (1004572800000, "2001-11-01T00:00:00.000Z"),
        (1004572799999, "2001-10-31T23:59:59.999Z"),
        (1007164800000, "2001-12-01T00:00:00.000Z"),
        (1007164799999, "2001-11-30T23:59:59.999Z"),
    ]

    func testWritesEveryInstantOfTheTableAsPythonsCalendarDoes() {
        for (milliseconds, text) in Self.table {
            let date = Date(timeIntervalSince1970: (Double(milliseconds) + 0.5) / 1000)
            XCTAssertEqual(UTCInstant.string(from: date), text, "\(milliseconds)")
            XCTAssertTrue(UTCInstant.isValid(text), text)
        }
        // Outside years 1 to 9999, and a clock that is not a number
        for seconds in [-62_135_596_800.0005, 253_402_300_800.0005, -1e20, 1e20, .infinity, -.infinity, .nan] {
            XCTAssertNil(UTCInstant.string(from: Date(timeIntervalSince1970: seconds)), "\(seconds)")
        }
        // A part of a millisecond is dropped toward the past, on both sides of 1970
        XCTAssertEqual(UTCInstant.string(from: Date(timeIntervalSince1970: -0.0005)), "1969-12-31T23:59:59.999Z")
        XCTAssertEqual(UTCInstant.string(from: Date(timeIntervalSince1970: 0.0009)), "1970-01-01T00:00:00.000Z")
    }

    func testTakesOnlyARealDayAndTimeInEitherOfTheTwoLengths() {
        let valid = ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z", "9999-12-31T23:59:59.999Z", "2000-02-29T00:00:00Z",
                     "2400-02-29T00:00:00.000Z", "2004-02-29T00:00:00Z", "1900-02-28T00:00:00Z", "2001-01-31T00:00:00Z",
                     "2001-04-30T00:00:00Z", "2001-06-30T00:00:00Z", "2001-09-30T00:00:00Z", "2001-11-30T00:00:00Z",
                     "2001-12-31T00:00:00Z", "2001-03-31T00:00:00Z", "2001-05-31T00:00:00Z", "2001-07-31T00:00:00Z",
                     "2001-08-31T00:00:00Z", "2001-10-31T00:00:00Z", "2001-02-28T00:00:00Z", "2001-01-01T00:00:00.000Z"]
        for text in valid { XCTAssertTrue(UTCInstant.isValid(text), text) }
        let invalid = ["0000-01-01T00:00:00Z", "2001-00-01T00:00:00Z", "2001-13-01T00:00:00Z", "2001-01-00T00:00:00Z",
                       "1900-02-29T00:00:00Z", "2100-02-29T00:00:00Z", "2001-02-29T00:00:00Z", "2000-02-30T00:00:00Z",
                       "2001-04-31T00:00:00Z", "2001-06-31T00:00:00Z", "2001-09-31T00:00:00Z", "2001-11-31T00:00:00Z",
                       "2001-01-32T00:00:00Z", "2001-12-32T00:00:00Z", "2001-01-01T24:00:00Z", "2001-01-01T00:60:00Z",
                       "2001-01-01T00:00:60Z", "2001-01-01T00:00:00", "2001-01-01T00:00:00.Z", "2001-01-01T00:00:00.00Z",
                       "2001-01-01T00:00:00.0000Z", "2001-01-01T00:00:00,000Z", "2001-01-01t00:00:00Z", "2001-01-01T00:00:00z",
                       "2001-01-01 00:00:00Z", "2001/01/01T00:00:00Z", "2001-01-01T00-00-00Z", "2001-01-01T00:00:00.000",
                       "2001-01-01T00:00:00.000ZZ", "2001-01-01T00:00:00ZZ", "", "Z"]
        for text in invalid { XCTAssertFalse(UTCInstant.isValid(text), text) }
        // Every digit position refuses the units just outside the digits
        let base = Array("2001-01-01T00:00:00.000Z".utf16)
        for position in [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18, 20, 21, 22] {
            for unit: UInt16 in [0x2F, 0x3A] {
                var text = base
                text[position] = unit
                XCTAssertFalse(UTCInstant.isValid(JSONString(units: text)), "\(position) \(unit)")
            }
        }
    }

    func testRefusesAnInstantLongerThanTheContractsLongestInEveryForm() throws {
        // The lengths come from the shared fixture's own edge table: each capture time it accepts at its shortest and
        // its longest, with one unit more of any kind, is refused, as Zod and Pydantic refuse it
        let accepted = try SharedFixture.samples("edges").filter {
            ($0["name"]?.stringValue ?? "").hasPrefix("capture time") && $0["verdict"]?.stringValue == "accepted"
        }.compactMap { $0["changes"]?.arrayValue?.first?["value"]?.stringValue }
        XCTAssertEqual(accepted.map(\.utf16.count).sorted(), [20, 24])
        for text in accepted {
            XCTAssertTrue(UTCInstant.isValid(text), text)
            for extra in ["0", "Z", " ", ".", "00000", " and anything"] {
                XCTAssertFalse(UTCInstant.isValid(text + extra), text + extra)
            }
        }
    }

    func testCountsLeapYearsByTheFullGregorianRule() {
        for year in [4, 400, 1600, 2000, 2004, 2024, 2400] { XCTAssertTrue(UTCInstant.isLeap(year), "\(year)") }
        for year in [1, 3, 100, 1700, 1800, 1900, 2001, 2100, 2200, 2300] { XCTAssertFalse(UTCInstant.isLeap(year), "\(year)") }
    }
}
