import Foundation

/// mainbutton-scale-m (UIUX-iphone-largertext-sweep-READY item 6, DECIDED-BY-LEAD 24 Nov 19:56): the main button's pill
/// and the outlined pill grow with their word. One line of the pill is the word's line at the current text size with the
/// space the default size leaves over and under it kept (44 pt at Large); the main button's circle keeps that one-line
/// height and its icon grows with the text; the approved padding is kept; past the screen's 24 pt margins the word wraps
/// inside the pill, centred, the pill growing in height: never cut, never pushing the screen wider.
public enum ButtonGeometry {
    /// The screen's margins the buttons keep, each side.
    public static let margin = 24.0

    /// The space over and under the word: what the control's height leaves around one line at the default size.
    public static func spaceOverUnder(lineHeightAtLarge: Double, controlHeight: Double) -> Double {
        max(0, (controlHeight - lineHeightAtLarge) / 2)
    }

    /// One line's height of the pill: the word's line now with that space over and under it, never less than the
    /// control's height.
    public static func oneLineHeight(lineHeight: Double, lineHeightAtLarge: Double, controlHeight: Double) -> Double {
        max(controlHeight, lineHeight + 2 * spaceOverUnder(lineHeightAtLarge: lineHeightAtLarge, controlHeight: controlHeight))
    }
}
