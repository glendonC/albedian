import Foundation

/// How dark the sky is predicted to be and where the Moon is, from the sky-context reply the aim overlay already asks
/// for (`predictedSkyBrightness`, services/contracts/sky_context.py): data for the capture coach (LEAD ANSWER 10:08,
/// 19 Nov; its screen and words come from the design lane's note). Every value is the laptop's model at the zenith,
/// `predicted`, never measured by the phone; the phone does no astronomy of its own.
public struct SkyConditions: Equatable, Sendable {
    /// The predicted zenith sky brightness, and the model's dark-sky value beside it, in mag/arcsec2 (larger is darker).
    public let skyBrightnessMagArcsec2: Double
    public let darkSkyMagArcsec2: Double
    /// How much the Moon's scattered light brightens the zenith, in magnitudes (0 with the Moon down).
    public let lunarVeilDeltaMag: Double
    /// The Moon's illuminated fraction, 0 to 1, its phase name (nil within a sector edge's bound, where either neighbour
    /// could be true), and its altitude.
    public let moonIlluminationFraction: Double
    public let moonPhaseName: String?
    public let moonAltDeg: Double
    public var evidence: EvidenceClass { .predicted }

    /// The contract's eight phase names.
    public static let phaseNames = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon",
                                    "Waning Gibbous", "Last Quarter", "Waning Crescent"]

    /// Read from a reply in the contract's form, or nil: a reply the overlay would refuse, a block not `predicted`, a
    /// phase name left out or not one of the eight, or a value outside its range.
    public static func read(_ value: JSONValue) -> SkyConditions? {
        guard SkyContextReader.marks(value) != nil, let block = value["predictedSkyBrightness"]?.objectValue,
              block["evidenceClass"]?.stringValue == "predicted",
              let sky = block["skyBrightnessMagArcsec2"]?.numberValue, sky.isFinite,
              let dark = block["darkSkyMagArcsec2"]?.numberValue, dark.isFinite,
              let veil = block["lunarVeilDeltaMag"]?.numberValue, veil.isFinite, veil >= 0,
              let lit = block["illuminationFraction"]?.numberValue, (0...1).contains(lit),
              let moonAlt = block["moonAltDeg"]?.numberValue, (-90...90).contains(moonAlt),
              let phase = block["lunarPhaseName"] else { return nil }
        let name: String?
        if case .null = phase {
            name = nil
        } else if let word = phase.stringValue, phaseNames.contains(word) {
            name = word
        } else {
            return nil
        }
        return SkyConditions(skyBrightnessMagArcsec2: sky, darkSkyMagArcsec2: dark, lunarVeilDeltaMag: veil,
                             moonIlluminationFraction: lit, moonPhaseName: name, moonAltDeg: moonAlt)
    }

    public static func read(_ data: Data) -> SkyConditions? {
        guard let value = try? JSONText.read(data) else { return nil }
        return read(value)
    }
}
