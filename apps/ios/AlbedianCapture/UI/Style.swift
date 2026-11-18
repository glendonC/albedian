import AlbedianCaptureCore
import SwiftUI

extension Color {
    /// A colour of the approved controls (`ControlStyle`), in sRGB.
    init(_ colour: ControlStyle.Colour) {
        self.init(.sRGB, red: colour.red, green: colour.green, blue: colour.blue, opacity: colour.opacity)
    }
}

/// The app's type: Geist, embedded (the static TrueType weights of the release the web pins), by PostScript name.
/// Sizes follow Dynamic Type from the approved size at the default setting.
enum AlbedianFont {
    static func regular(_ size: Double) -> Font { .custom("Geist-Regular", size: size, relativeTo: .body) }
    static func semibold(_ size: Double) -> Font { .custom("Geist-SemiBold", size: size, relativeTo: .title) }
}

/// The four Phosphor icons (regular weight) the approved iPhone screens use, by their file names in the asset catalog.
enum AlbedianIcon: String {
    case arrow = "arrow-right"
    case close = "x"
    case send = "paper-plane-tilt"
    case again = "arrow-counter-clockwise"

    func image(_ size: Double) -> some View {
        Image(rawValue).resizable().renderingMode(.template).aspectRatio(contentMode: .fit).frame(width: size, height: size)
    }
}

extension Animation {
    /// One of the approved timings, or none with Reduce Motion on.
    static func control(_ seconds: Double, _ curve: (Double, Double, Double, Double), reduceMotion: Bool) -> Animation? {
        reduceMotion ? nil : .timingCurve(curve.0, curve.1, curve.2, curve.3, duration: seconds)
    }
}
