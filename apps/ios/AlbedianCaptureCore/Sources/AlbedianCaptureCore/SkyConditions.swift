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
    /// The Sun, when the reply carries it (skycontext-3); nil from a laptop that predates it.
    public let sun: SkySun?
    public var evidence: EvidenceClass { .predicted }

    /// The contract's eight phase names.
    public static let phaseNames = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon",
                                    "Waning Gibbous", "Last Quarter", "Waning Crescent"]

    /// Read from a reply in the contract's form, or nil: a reply the overlay would refuse, a block not `predicted`, a
    /// phase name left out or not one of the eight, a value outside its range, or a Sun the contract refuses.
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
        var sun: SkySun?
        if let held = value["sun"] {
            guard let read = SkySun.read(held) else { return nil }
            sun = read
        }
        return SkyConditions(skyBrightnessMagArcsec2: sky, darkSkyMagArcsec2: dark, lunarVeilDeltaMag: veil,
                             moonIlluminationFraction: lit, moonPhaseName: name, moonAltDeg: moonAlt, sun: sun)
    }

    public static func read(_ data: Data) -> SkyConditions? {
        guard let value = try? JSONText.read(data) else { return nil }
        return read(value)
    }
}

/// The Sun's centre altitude from the place and its USNO band, as the sky-context reply states them (skycontext-3,
/// services/contracts/sky_context.py `SkyContextSun`), read here a third time: `predicted`, never measured by the phone.
public struct SkySun: Equatable, Sendable {
    /// The contract's bands, brightest first (`SKY_CONTEXT_SUN_BANDS`).
    public enum Band: String, CaseIterable, Sendable {
        case day, civilTwilight = "civil_twilight", nauticalTwilight = "nautical_twilight",
             astronomicalTwilight = "astronomical_twilight", night
    }

    public let altDeg: Double
    public let band: Band
    public var evidence: EvidenceClass { .predicted }

    /// Where each band begins (`SKY_CONTEXT_SUN_BAND_EDGES`): day above sunrise and sunset's -50 arcminutes, each
    /// twilight down to and including its floor, night below the last.
    public static let dayAboveDeg = -50.0 / 60.0
    public static let civilTwilightFromDeg = -6.0
    public static let nauticalTwilightFromDeg = -12.0
    public static let astronomicalTwilightFromDeg = -18.0
    static let keys: Set<String> = ["altDeg", "band", "evidenceClass", "method", "limitations"]
    static let textMaxUTF16 = 1024
    static let notesMax = 16

    /// The contract's band of an altitude.
    public static func band(of altDeg: Double) -> Band {
        if altDeg > dayAboveDeg { return .day }
        if altDeg >= civilTwilightFromDeg { return .civilTwilight }
        if altDeg >= nauticalTwilightFromDeg { return .nauticalTwilight }
        if altDeg >= astronomicalTwilightFromDeg { return .astronomicalTwilight }
        return .night
    }

    /// The block as the contract holds it, or nil: an unknown or missing key, not `predicted`, an altitude past 90 or a
    /// band that is not its altitude's, or a method or limitations the contract would refuse.
    static func read(_ value: JSONValue) -> SkySun? {
        guard let object = value.objectValue, Wire.onlyKeys(object, keys) == nil,
              object["evidenceClass"]?.stringValue == "predicted",
              let alt = object["altDeg"]?.numberValue, (-90...90).contains(alt),
              let word = object["band"]?.stringValue, let band = Band(rawValue: word), band == Self.band(of: alt),
              let method = object["method"]?.stringValue, isText(method),
              let notes = object["limitations"]?.arrayValue, (1...notesMax).contains(notes.count),
              notes.allSatisfy({ $0.stringValue.map(isText) ?? false }) else { return nil }
        return SkySun(altDeg: alt, band: band)
    }

    /// 1 to 1024 UTF-16 units with no control character, as the contract's text is.
    private static func isText(_ text: String) -> Bool {
        (1...textMaxUTF16).contains(text.utf16.count) && !text.unicodeScalars.contains { $0.value <= 0x1F || (0x7F...0x9F).contains($0.value) }
    }
}
