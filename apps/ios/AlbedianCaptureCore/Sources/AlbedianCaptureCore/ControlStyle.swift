import Foundation

/// The approved controls' own numbers on the iPhone (the owner's yes of 2026-11-12, `buttons-approved-2026-11-12.md`
/// with its correction the same afternoon, `arrive/build.mjs` and `packages/design-system/src/look.ts`), so the app
/// draws its controls from them and a test holds them. Colours are sRGB components from 0 to 1. The capture button's
/// numbers are `ShutterGeometry`'s.
public enum ControlStyle {
    public struct Colour: Equatable, Sendable {
        public let red: Double, green: Double, blue: Double, opacity: Double
        init(hex: UInt32, opacity: Double = 1) {
            red = Double((hex >> 16) & 0xFF) / 255
            green = Double((hex >> 8) & 0xFF) / 255
            blue = Double(hex & 0xFF) / 255
            self.opacity = opacity
        }
    }

    /// Ink (#f4f3ef) on black (#000000), with near-black (#0b0b0b) on a filled pill; the outline is ink at 88 percent,
    /// a wash ink at 10 percent, secondary text 66 percent, quiet labels 44 percent and hairlines 14 percent.
    public static let field = Colour(hex: 0x000000)
    public static let ink = Colour(hex: 0xF4F3EF)
    public static let night = Colour(hex: 0x0B0B0B)
    public static let line = Colour(hex: 0xF4F3EF, opacity: 0.88)
    public static let wash = Colour(hex: 0xF4F3EF, opacity: 0.10)
    public static let inkSecondary = Colour(hex: 0xF4F3EF, opacity: 0.66)
    public static let inkQuiet = Colour(hex: 0xF4F3EF, opacity: 0.44)
    public static let hairline = Colour(hex: 0xF4F3EF, opacity: 0.14)

    /// Apple's Human Interface Guidelines minimum hit target (44 pt), and the iPhone's one main action per screen
    /// (`arrive/build.mjs`, `.ab-split.ab-sz-m`): 44 pt high, as wide as its word, centred at the bottom; a filled ink
    /// pill (padding 0 16) with an outlined 44 pt circle touching beside it. While pressed, the pill's trailing padding
    /// grows by 44 pt (to 60 pt) and the circle moves left by 44 pt and fills, so total width never changes.
    public static let minTouchTarget = 44.0
    public static let mainHeight = 44.0
    public static let mainFontSize = 16.0
    public static let iconSize = 17.0
    public static let mainPillPadding = 16.0
    public static var mainPillDockedTrailingPadding: Double { mainPillPadding + mainHeight }
    public static var mainCircleSide: Double { mainHeight }

    /// An icon button (close, take again): an outlined circle of the control height; pressed, it fills.
    public static let iconButtonSide = 44.0
    public static let outlineWidth = 1.0

    /// Type: Geist, weight 400, letter-spacing -0.01 em (the logo: weight 600, -0.048 em).
    public static let letterSpacingEm = -0.01
    public static let logoLetterSpacingEm = -0.048

    /// Pressing scales a button to 0.97 in 0.15 s; colour changes on an icon button take 0.2 s; both on the standard
    /// curve. The spring (the circle docking into the main pill and an arrow turning from -45 degrees) is 1.2 s on
    /// (0.175, 1.4, 0.32, 1). With Reduce Motion there is no transition.
    public static let pressScale = 0.97
    public static let pressSeconds = 0.15
    public static let colourSeconds = 0.2
    public static let standardCurve = (0.4, 0.0, 0.2, 1.0)
    public static let springSeconds = 1.2
    public static let springCurve = (0.175, 1.4, 0.32, 1.0)
    public static let arrowRestDegrees = -45.0

    /// The name, always as the logo (`apps/web`'s `look-logo`): "albedian" in white Geist 600 at -0.048 em, the mark
    /// (the moon with its star cut out, the logo's 32 unit drawing) beside it at 0.857 of the type's size, moved left
    /// by 0.048 and up by 0.238 of it (default 21 pt).
    public static let logoInk = Colour(hex: 0xFFFFFF)
    public static let logoWord = "albedian"
    public static let logoMarkScale = 0.857
    public static let logoMarkLeft = -0.048
    public static let logoMarkUp = -0.238
    public static let logoDefaultSize = 21.0

    /// Focus: a 2 pt ink outline, 2 pt out.
    public static let focusWidth = 2.0
    public static let focusOffset = 2.0

    /// Evidence tags are never buttons: 28 pt high, padding 0 12 0 9, a 7 pt gap, 13 pt type at -0.005 em, the glyph
    /// 14 pt beside the word, on a tint of the class's colour.
    public static let tagHeight = 28.0
    public static let tagLeadingPadding = 9.0
    public static let tagTrailingPadding = 12.0
    public static let tagGap = 7.0
    public static let tagFontSize = 13.0
    public static let tagLetterSpacingEm = -0.005
    public static let tagGlyphSize = 14.0

    /// All four scientific evidence classes (`packages/design-system/src/look.ts`), each with its word, colour and tint.
    public enum Evidence: CaseIterable, Sendable {
        case observed, catalogMatched, predicted, simulated

        public var word: String {
            switch self {
            case .observed: "Observed"
            case .catalogMatched: "Catalog matched"
            case .predicted: "Predicted"
            case .simulated: "Simulated"
            }
        }

        public var colour: Colour {
            switch self {
            case .observed: ink
            case .catalogMatched: Colour(hex: 0xC9DAFF)
            case .predicted: Colour(hex: 0xF5B83D)
            case .simulated: Colour(hex: 0xA78BFA)
            }
        }

        public var tint: Colour {
            switch self {
            case .observed: Colour(hex: 0xF4F3EF, opacity: 0.10)
            case .catalogMatched: Colour(hex: 0xC9DAFF, opacity: 0.14)
            case .predicted: Colour(hex: 0xF5B83D, opacity: 0.14)
            case .simulated: Colour(hex: 0xA78BFA, opacity: 0.15)
            }
        }
    }
}
