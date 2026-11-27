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
    /// The semibold weight at one size whatever the text size: the logo's word, a picture and not text that grows
    /// (largertext-home-m).
    static func semiboldFixed(_ size: Double) -> Font { .custom("Geist-SemiBold", fixedSize: size) }
    /// The light weight (300) the approved titles and the result's count use, a named instance of the web's own Geist variable
    /// font (`Fonts/Geist-wght.woff2`, the same bytes as `apps/web/public/fonts/geist/Geist-wght.woff2`).
    static func light(_ size: Double) -> Font { .custom("Geist-Light", size: size, relativeTo: .largeTitle) }
    /// The command on Connect (firstuse-connect-one): the regular instance of the web's own Geist Mono variable font
    /// (`Fonts/GeistMono-wght.woff2`, the same bytes as `apps/web/public/fonts/geist/GeistMono-wght.woff2`).
    static func mono(_ size: Double) -> Font { .custom("GeistMono-Regular", size: size, relativeTo: .body) }
}

/// The nine Phosphor icons (regular weight) the approved iPhone screens use, by their file names in the asset catalog.
enum AlbedianIcon: String {
    case arrow = "arrow-right"
    case close = "x"
    case send = "paper-plane-tilt"
    case again = "arrow-counter-clockwise"
    case back = "arrow-left"
    case openLink = "arrow-square-out"
    case unreachable = "wifi-slash"
    case warning = "warning-circle"
    case cameraSlash = "camera-slash"
    // The capture coach (D29)
    case moon = "moon"
    case hand = "hand"
    case compass = "compass"
    case sunHorizon = "sun-horizon"

    func image(_ size: Double) -> some View {
        Image(rawValue).resizable().renderingMode(.template).aspectRatio(contentMode: .fit).frame(width: size, height: size)
    }
}

extension Animation {
    /// One of the approved timings; with Reduce Motion on, the note's dissolve instead, never nothing, so a change is still
    /// seen (MOTION-READY-iphone section 2).
    static func control(_ seconds: Double, _ curve: (Double, Double, Double, Double), reduceMotion: Bool) -> Animation {
        reduceMotion ? .easeInOut(duration: MotionStyle.reduceMotionSeconds) : .timingCurve(curve.0, curve.1, curve.2, curve.3, duration: seconds)
    }

    /// A progress line's width following a report (`MotionStyle.progress`); none, so at once, with Reduce Motion.
    static func progress(reduceMotion: Bool) -> Animation? {
        reduceMotion ? nil : .motion(MotionStyle.progress, reduceMotion: false)
    }

    /// A named timing of the note (`MotionStyle`), its delay added, extra waits after it; the dissolve with Reduce Motion.
    static func motion(_ timing: MotionStyle.Timing, after extra: Double = 0, reduceMotion: Bool) -> Animation {
        if reduceMotion { return .easeInOut(duration: MotionStyle.reduceMotionSeconds).delay(extra) }
        return .timingCurve(timing.x1, timing.y1, timing.x2, timing.y2, duration: timing.seconds).delay(timing.delaySeconds + extra)
    }
}

extension AnyTransition {
    /// A line of words arriving from 6 pt below and fading in (wordsIn), and leaving the same way (wordsOut); opacity alone,
    /// the dissolve, with Reduce Motion. `after` staggers a line behind another (`MotionStyle.staggerSeconds`).
    static func words(reduceMotion: Bool, after extra: Double = 0) -> AnyTransition {
        if reduceMotion { return .opacity.animation(.motion(MotionStyle.wordsIn, after: extra, reduceMotion: true)) }
        let moved = AnyTransition.opacity.combined(with: .offset(y: MotionStyle.wordsOffsetPt))
        return .asymmetric(insertion: moved.animation(.motion(MotionStyle.wordsIn, after: extra, reduceMotion: false)),
                           removal: moved.animation(.motion(MotionStyle.wordsOut, reduceMotion: false)))
    }

    /// An overlay or a block arriving (frameIn) and leaving (frameOut), fading; the dissolve with Reduce Motion.
    static func frame(reduceMotion: Bool) -> AnyTransition {
        .asymmetric(insertion: .opacity.animation(.motion(MotionStyle.frameIn, reduceMotion: reduceMotion)),
                    removal: .opacity.animation(.motion(MotionStyle.frameOut, reduceMotion: reduceMotion)))
    }
}
