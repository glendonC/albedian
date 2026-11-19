import Foundation

/// D8, INTERIM for the owner's review (UIUX-iphone-aim-QUESTION): the small line under the aiming preview when the
/// photo's record would carry no place or no heading, in the record's own words. The place comes first: true north is
/// found from it, so without a place there is no heading either.
public enum AimNotice {
    public static func line(place: PointingRecord.Place, heading: PointingRecord.Heading?) -> String? {
        switch place {
        case .absent(.notAllowed): return "No place: location not allowed"
        case .absent(.noFix): return "No place: no fix"
        case .absent(.simulatedBySoftware): return "No place: simulated by software"
        case .allowed: break
        }
        guard case .absent(let reason)? = heading else { return nil }
        switch reason {
        case .noMagnetometer: return "No heading: no magnetometer"
        case .noPlace: return nil
        case .noHeadingEvent: return "No heading: no heading event"
        case .headingInvalid: return "No heading: heading invalid"
        case .accuracyOutsideLimit(let reported):
            // Whole degrees when the reading is whole to a tenth, else one decimal, so a reading just past the limit never reads as it
            let tenths = Int((reported * 10).rounded())
            let shown = tenths % 10 == 0 ? "\(tenths / 10)" : "\(tenths / 10).\(tenths % 10)"
            return "No heading: ±\(shown)° is past \(Int(PointingContract.headingAccuracyLimitDeg))°"
        }
    }
}
