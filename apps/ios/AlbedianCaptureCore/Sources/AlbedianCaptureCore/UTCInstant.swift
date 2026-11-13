import Foundation

/// The contract's instant: UTC with a trailing Z, `yyyy-MM-ddTHH:mm:ss` with no fraction or exactly three digits of
/// one, on a real day of the Gregorian calendar (leap years by the full rule), from year 1 to 9999. This is the
/// pattern `INGEST_PATTERNS.utcInstant` that both other readings use, together with Zod's `datetime()` day check and
/// Python's `datetime.fromisoformat`, neither of which has a year 0.
public enum UTCInstant {
    /// The instant a device clock reading names, written with milliseconds, rounded down to the millisecond; nil
    /// outside years 1 to 9999. The calendar is computed here (days from the civil date, H. Hinnant's algorithm),
    /// so no locale, time zone or calendar setting of the device can change it.
    public static func string(from date: Date) -> String? {
        let seconds = date.timeIntervalSince1970
        guard seconds.isFinite else { return nil }
        let milliseconds = (seconds * 1000).rounded(.down)
        // Years 1 to 9999 lie between these instants, in milliseconds since 1970
        guard milliseconds >= -62_135_596_800_000, milliseconds < 253_402_300_800_000 else { return nil }
        let total = Int64(milliseconds)
        let millisPerDay: Int64 = 86_400_000
        let days = total >= 0 ? total / millisPerDay : (total - millisPerDay + 1) / millisPerDay
        let ofDay = total - days * millisPerDay
        let (year, month, day) = civil(fromDays: days)
        let hour = ofDay / 3_600_000
        let minute = ofDay / 60_000 % 60
        let second = ofDay / 1000 % 60
        let milli = ofDay % 1000
        func pad(_ value: Int64, _ width: Int) -> String {
            let text = String(value)
            return String(repeating: "0", count: max(0, width - text.count)) + text
        }
        return "\(pad(year, 4))-\(pad(month, 2))-\(pad(day, 2))T\(pad(hour, 2)):\(pad(minute, 2)):\(pad(second, 2)).\(pad(milli, 3))Z"
    }

    /// Whether wire text is such an instant.
    public static func isValid(_ text: JSONString) -> Bool {
        let units = text.units
        guard units.count == 20 || units.count == 24 else { return false }
        func digits(_ range: Range<Int>) -> Int? {
            var value = 0
            for index in range {
                let unit = units[index]
                guard (0x30...0x39).contains(unit) else { return nil }
                value = value * 10 + Int(unit - 0x30)
            }
            return value
        }
        func unit(_ index: Int, is expected: Character) -> Bool { units[index] == expected.utf16.first! }
        guard unit(4, is: "-"), unit(7, is: "-"), unit(10, is: "T"), unit(13, is: ":"), unit(16, is: ":"),
              let year = digits(0..<4), let month = digits(5..<7), let day = digits(8..<10),
              let hour = digits(11..<13), let minute = digits(14..<16), let second = digits(17..<19) else { return false }
        if units.count == 24 {
            guard unit(19, is: "."), digits(20..<23) != nil, unit(23, is: "Z") else { return false }
        } else {
            guard unit(19, is: "Z") else { return false }
        }
        guard year >= 1, (1...12).contains(month), hour <= 23, minute <= 59, second <= 59 else { return false }
        return (1...daysIn(month: month, year: year)).contains(day)
    }

    public static func isValid(_ text: String) -> Bool { isValid(JSONString(text)) }

    static func isLeap(_ year: Int) -> Bool { year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) }

    static func daysIn(month: Int, year: Int) -> Int {
        switch month {
        case 2: isLeap(year) ? 29 : 28
        case 4, 6, 9, 11: 30
        default: 31
        }
    }

    /// The Gregorian date of a day counted from 1970-01-01 (Hinnant, "chrono-compatible low-level date algorithms").
    static func civil(fromDays days: Int64) -> (Int64, Int64, Int64) {
        let z = days + 719_468
        let era = (z >= 0 ? z : z - 146_096) / 146_097
        let dayOfEra = z - era * 146_097
        let yearOfEra = (dayOfEra - dayOfEra / 1460 + dayOfEra / 36524 - dayOfEra / 146_096) / 365
        let dayOfYear = dayOfEra - (365 * yearOfEra + yearOfEra / 4 - yearOfEra / 100)
        let monthIndex = (5 * dayOfYear + 2) / 153
        let day = dayOfYear - (153 * monthIndex + 2) / 5 + 1
        let month = monthIndex < 10 ? monthIndex + 3 : monthIndex - 9
        return (yearOfEra + era * 400 + (month <= 2 ? 1 : 0), month, day)
    }
}
