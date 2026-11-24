import Foundation

/// The early pose of the lock-on on the iPhone's wait (D31, UIUX-iphone-lockon-READY, decided by the lead 12:32): the
/// phone's own pointing record, the earliest pose of all, read back from the record as it was sent: its moment, place,
/// attitude and the camera's field of view. The marks it places are the sky the laptop's sky context predicts for that
/// moment and place, drawn through that attitude: `predicted` until the solve matches them, never a measurement.
public struct EarlyPose: Equatable, Sendable {
    public let moment: Date
    public let latDeg: Double
    public let lonDeg: Double
    /// Core Motion's attitude in its `xTrueNorthZVertical` frame, as `SkyProjection` takes it.
    public let attitude: PointingRecord.Quaternion
    /// The camera format's field of view across its long side, as the record reports it.
    public let longSideFovDeg: Double

    /// C13's sentence for a pose from the iPhone's record (D25's source line), under the wait's step row.
    public static let sourceSentence = "Placed from where your iPhone pointed: predicted until matched."
    public static var evidence: EvidenceClass { .predicted }

    /// The pose from a record's wire form, or nil: not a record, a place not allowed or out of range, no measured
    /// heading or one in another frame, a field of view not reported or out of range, or a moment not an instant.
    public static func read(recordWire: Data) -> EarlyPose? {
        guard let record = try? JSONText.read(recordWire),
              let text = record["capturedAtDeviceClockUTC"]?.stringValue, let moment = UTCInstant.date(text),
              let place = record["location"], place["state"]?.stringValue == "allowed",
              let lat = place["latDeg"]?.numberValue, (-90...90).contains(lat),
              let lon = place["lonDeg"]?.numberValue, (-180...180).contains(lon),
              let heading = record["heading"], heading["state"]?.stringValue == "measured",
              heading["attitude"]?["referenceFrame"]?.stringValue == "x_true_north_z_vertical",
              let q = heading["attitude"]?["quaternion"],
              let x = q["x"]?.numberValue, let y = q["y"]?.numberValue, let z = q["z"]?.numberValue, let w = q["w"]?.numberValue,
              let fov = record["camera"]?["fieldOfView"], fov["state"]?.stringValue == "reported",
              let long = fov["horizontalDeg"]?.numberValue, long > 0, long < 180 else { return nil }
        return EarlyPose(moment: moment, latDeg: lat, lonDeg: lon, attitude: PointingRecord.Quaternion(x: x, y: y, z: z, w: w),
                         longSideFovDeg: long)
    }

    /// The field across the photo's portrait width, the same focal length over its short side.
    public var acrossPortraitWidthDeg: Double? { SkyProjection.acrossPortraitWidthDeg(longSideFovDeg: longSideFovDeg) }

    /// The approved wait card's portrait width in points (`UIUX-arrive-READY`, 390 by 520).
    public static let defaultCardWidthPt = 390.0

    /// The early marks that project wholly inside a portrait `widthPt` by `heightPt` photo frame: the laptop's early sky
    /// when served (`lockon-m5`), else the phone's own record projection (`D31`).
    public static func placed(laptopEarly: (sky: PredictedEarlySky, frame: StoredFrame)?, pose: EarlyPose?,
                              phoneMarks: [SkyMark], widthPt: Double, heightPt: Double) -> [(mark: SkyMark, x: Double, y: Double)] {
        if let laptop = laptopEarly {
            return laptop.sky.placed(frame: laptop.frame, widthPt: widthPt, heightPt: heightPt)
        }
        guard let pose, !phoneMarks.isEmpty, let across = pose.acrossPortraitWidthDeg else { return [] }
        return phoneMarks.compactMap { mark in
            SkyProjection.point(altDeg: mark.altDeg, azDeg: mark.azDeg, attitude: pose.attitude, widthPt: widthPt,
                                heightPt: heightPt, acrossFovDeg: across, marginPt: -SkyOverlayStyle.unnamedRingRadiusPt)
                .map { (mark, $0.x, $0.y) }
        }
    }

    /// Whether early marks actually stand over the wait's photo (`UIUX-built-1756` 17:38 tag rule, `D31` item 3): at
    /// least one ring placed inside the 3:4 photo frame under a visible step or queued row.
    public static func hasDrawnMarks(laptopEarly: (sky: PredictedEarlySky, frame: StoredFrame)?, pose: EarlyPose?,
                                     phoneMarks: [SkyMark], rowShown: Bool, widthPt: Double = defaultCardWidthPt) -> Bool {
        guard widthPt > 0 else { return false }
        let count = placed(laptopEarly: laptopEarly, pose: pose, phoneMarks: phoneMarks, widthPt: widthPt,
                           heightPt: widthPt * 4 / 3).count
        return EarlyLine.marksShown(placed: count, rowShown: rowShown)
    }
}
