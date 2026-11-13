import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The day arithmetic of the contract instant against an oracle that works another way: counting forward one day at
/// a time from 0001-01-01 with the month lengths of the full Gregorian rule, over every day of years 1 to 9999.
final class CalendarDayTests: XCTestCase {
    func testNamesEveryDayFromYearOneToYear9999AsCountingDayByDayDoes() {
        // 0001-01-01 is day -719162 counted from 1970-01-01 (Python's date(1, 1, 1).toordinal() is 1, 1970's is 719163)
        var days: Int64 = -719_162
        var year = 1, month = 1, day = 1
        var wrong: [String] = []
        let monthLengths = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        func length(_ month: Int, _ year: Int) -> Int {
            month == 2 && (year % 400 == 0 || (year % 4 == 0 && year % 100 != 0)) ? 29 : monthLengths[month - 1]
        }
        var count = 0
        while year <= 9999 {
            let (y, m, d) = UTCInstant.civil(fromDays: days)
            if (y, m, d) != (Int64(year), Int64(month), Int64(day)), wrong.count < 5 {
                wrong.append("day \(days): \(y)-\(m)-\(d), counted \(year)-\(month)-\(day)")
            }
            count += 1
            days += 1
            day += 1
            if day > length(month, year) {
                day = 1
                month += 1
                if month > 12 { month = 1; year += 1 }
            }
        }
        XCTAssertEqual(wrong, [])
        // 9999 years of 365 days, plus 2424 leap days (2499 multiples of 4, less 99 of 100, plus 24 of 400)
        XCTAssertEqual(count, 9999 * 365 + 2424)
        XCTAssertEqual(days, 2_932_897)
    }
}
