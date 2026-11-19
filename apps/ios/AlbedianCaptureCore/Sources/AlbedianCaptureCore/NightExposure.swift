import Foundation

/// One long exposure (unit mobile-7, the lead's answer of 2026-11-18; `design.md` 8.1) and the numbers of the approved
/// hold-still screen (UIUX-arrive-READY, iPhone 4).
public enum NightExposure {
    /// The exposure: the format's longest duration (`activeFormat.maxExposureDuration`), and the ISO that gives the
    /// meter's light at that duration (the meter's ISO times its duration over the longest), within the format's range
    /// (`minISO` to `maxISO`) [proposed: the same light the camera's own meter chose, carried to the longest exposure].
    /// Nil when a reading is not a positive number or the range is upside down.
    public static func plan(meterDurationSec: Double, meterISO: Double, maxDurationSec: Double, minISO: Double,
                            maxISO: Double) -> (durationSec: Double, iso: Double)? {
        let readings = [meterDurationSec, meterISO, maxDurationSec, minISO, maxISO]
        guard readings.allSatisfy({ $0.isFinite && $0 > 0 }), minISO <= maxISO else { return nil }
        let carried = meterISO * meterDurationSec / maxDurationSec
        return (maxDurationSec, min(max(carried, minISO), maxISO))
    }

    /// How much of the capture button's ring is filled: the share of the real exposure that has passed, 0 to 1.
    public static func ringFraction(elapsedSec: Double, exposureSec: Double) -> Double {
        guard elapsedSec.isFinite, exposureSec.isFinite, exposureSec > 0 else { return 0 }
        return min(max(elapsedSec / exposureSec, 0), 1)
    }

    /// The exposure's length as the screen writes it under the preview ("1.1 s"), or nil for no length.
    public static func lengthWords(_ seconds: Double) -> String? {
        guard seconds.isFinite, seconds >= 0 else { return nil }
        let tenths = Int((seconds * 10).rounded())
        return "\(tenths / 10).\(tenths % 10) s"
    }

    /// Where the second crosshair sits from the fixed one, in points on a preview `previewWidthPt` wide that shows
    /// `horizontalFovDeg` across: the phone's turn since the exposure began (the attitude then and now, from Core
    /// Motion), about the device's Y axis across and its X axis up and down, at the preview's own scale; a turn about
    /// the lens's axis does not move it. Which way each way goes rests on the quaternion's direction, which only an
    /// iPhone settles (mobile-1's check) [open]. Nil without both attitudes or a field of view, or for a turn of a
    /// quarter or more, which is no shake.
    public static func crosshairOffset(start: PointingRecord.Quaternion?, now: PointingRecord.Quaternion?, previewWidthPt: Double,
                                       horizontalFovDeg: Double) -> (dx: Double, dy: Double)? {
        guard let start, let now, horizontalFovDeg > 0, horizontalFovDeg < 180, previewWidthPt.isFinite, previewWidthPt > 0 else { return nil }
        var turn = start.conjugate.multiplied(now)
        if turn.w < 0 { turn = PointingRecord.Quaternion(x: -turn.x, y: -turn.y, z: -turn.z, w: -turn.w) }
        let sine = (turn.x * turn.x + turn.y * turn.y + turn.z * turn.z).squareRoot()
        let angle = 2 * atan2(sine, turn.w)
        guard angle.isFinite, angle < .pi / 2 else { return nil }
        let focal = previewWidthPt / 2 / tan(horizontalFovDeg * .pi / 360)
        guard sine > 0 else { return (0, 0) }
        let aboutX = turn.x / sine * angle
        let aboutY = turn.y / sine * angle
        return (focal * tan(aboutY), focal * tan(aboutX))
    }
}

extension PointingRecord.Quaternion {
    /// The Hamilton product, this one first.
    public func multiplied(_ other: PointingRecord.Quaternion) -> PointingRecord.Quaternion {
        PointingRecord.Quaternion(x: w * other.x + x * other.w + y * other.z - z * other.y,
                                  y: w * other.y - x * other.z + y * other.w + z * other.x,
                                  z: w * other.z + x * other.y - y * other.x + z * other.w,
                                  w: w * other.w - x * other.x - y * other.y - z * other.z)
    }

    public var conjugate: PointingRecord.Quaternion { PointingRecord.Quaternion(x: -x, y: -y, z: -z, w: w) }
}
