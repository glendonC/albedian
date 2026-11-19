import Foundation

/// A pose's own WCS, as an approximate solution carries it (`WCSCalibrationModelSchema`,
/// packages/contracts/src/astronomy.ts): the solver's gnomonic TAN projection with parity, roll and radial distortion.
/// It places a predicted star in the stored frame exactly as services/science/wcs_models.py `WCSModel.world_to_pixel`
/// does, so the iPhone draws the laptop's early marks where the laptop placed them (lockon-m5a), never through a pose of
/// its own. Every place it gives is `predicted`.
public struct PoseWCS: Equatable, Sendable {
    public let crvalRaDeg: Double
    public let crvalDecDeg: Double
    public let crpixX: Double
    public let crpixY: Double
    public let scaleDegPerPx: Double
    public let rotationDeg: Double
    public let k1: Double
    public let k2: Double
    public let parity: Int

    public init(crvalRaDeg: Double, crvalDecDeg: Double, crpixX: Double, crpixY: Double, scaleDegPerPx: Double,
                rotationDeg: Double, k1: Double, k2: Double, parity: Int) {
        self.crvalRaDeg = crvalRaDeg
        self.crvalDecDeg = crvalDecDeg
        self.crpixX = crpixX
        self.crpixY = crpixY
        self.scaleDegPerPx = scaleDegPerPx
        self.rotationDeg = rotationDeg
        self.k1 = k1
        self.k2 = k2
        self.parity = parity
    }

    /// The contract's bound on each distortion coefficient.
    static let distortionMax = 0.25

    /// The WCS as the contract holds it, or nil: not TAN, a number missing or out of its range, a parity not 1 or -1.
    public static func read(_ value: JSONValue) -> PoseWCS? {
        func number(_ key: String) -> Double? { value[key]?.numberValue.flatMap { $0.isFinite ? $0 : nil } }
        guard value["projection"]?.stringValue == "TAN",
              let ra = number("crvalRaDeg"), (0...360).contains(ra),
              let dec = number("crvalDecDeg"), (-90...90).contains(dec),
              let x = number("crpixX"), let y = number("crpixY"),
              let scale = number("scaleDegPerPx"), scale > 0,
              let rotation = number("rotationDeg"),
              let k1 = number("radialDistortionK1"), abs(k1) <= distortionMax,
              let k2 = number("radialDistortionK2"), abs(k2) <= distortionMax,
              let parity = number("parity"), parity == 1 || parity == -1 else { return nil }
        return PoseWCS(crvalRaDeg: ra, crvalDecDeg: dec, crpixX: x, crpixY: y, scaleDegPerPx: scale, rotationDeg: rotation,
                       k1: k1, k2: k2, parity: Int(parity))
    }

    /// Where a sky direction lands in the stored frame (pixel centres at whole numbers, the detector's convention), or
    /// nil for one on the far side of the tangent plane, where the solver gives no number.
    public func pixel(raDeg: Double, decDeg: Double) -> (x: Double, y: Double)? {
        guard raDeg.isFinite, decDeg.isFinite else { return nil }
        let radian = Double.pi / 180
        let ra = raDeg.truncatingRemainder(dividingBy: 360).addingIfNegative(360) * radian
        let dec = decDeg * radian
        let ra0 = crvalRaDeg.truncatingRemainder(dividingBy: 360).addingIfNegative(360) * radian
        let dec0 = crvalDecDeg * radian
        let dra = ra - ra0
        let cosC = sin(dec0) * sin(dec) + cos(dec0) * cos(dec) * cos(dra)
        guard cosC > 1e-6 else { return nil }
        let xi = Double(parity) * (cos(dec) * sin(dra)) / cosC
        let eta = (cos(dec0) * sin(dec) - sin(dec0) * cos(dec) * cos(dra)) / cosC
        let theta = rotationDeg * radian
        let u = xi * cos(theta) - eta * sin(theta)
        let v = xi * sin(theta) + eta * cos(theta)
        let r2 = u * u + v * v
        let distort = 1 + k1 * r2 + k2 * r2 * r2
        return (crpixX + u * distort / radian / scaleDegPerPx, crpixY - v * distort / radian / scaleDegPerPx)
    }
}

private extension Double {
    /// Python's `%` for a positive divisor: a negative remainder moved into [0, divisor).
    func addingIfNegative(_ divisor: Double) -> Double { self < 0 ? self + divisor : self }
}
