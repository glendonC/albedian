import SwiftUI
import UIKit

/// firstuse-home-result-one (UIUX-iphone-firstuse-READY item 3): the home's Milky Way, framed `overscanPt` past every
/// edge and moved by the phone's tilt up to that far each way on both axes, by the system's own tilt values
/// (`UIInterpolatingMotionEffect` on its centre, as iOS's Perspective Zoom moves a wallpaper). With `tilts` false (Reduce
/// Motion) it is the picture as before, still. It is decoration: VoiceOver does not read it.
struct TiltingBackdrop: UIViewRepresentable {
    let image: String
    let overscanPt: Double
    let tilts: Bool

    func makeUIView(context: Context) -> UIImageView {
        let view = UIImageView(image: UIImage(named: image))
        view.contentMode = .scaleAspectFill
        view.clipsToBounds = true
        view.isAccessibilityElement = false
        // The frame SwiftUI gives it decides its size, never the picture's own
        for axis in [NSLayoutConstraint.Axis.horizontal, .vertical] {
            view.setContentHuggingPriority(.defaultLow, for: axis)
            view.setContentCompressionResistancePriority(.defaultLow, for: axis)
        }
        apply(to: view)
        return view
    }

    func updateUIView(_ view: UIImageView, context: Context) { apply(to: view) }

    private func apply(to view: UIImageView) {
        view.motionEffects.forEach(view.removeMotionEffect)
        if tilts {
            let across = UIInterpolatingMotionEffect(keyPath: "center.x", type: .tiltAlongHorizontalAxis)
            across.minimumRelativeValue = -overscanPt
            across.maximumRelativeValue = overscanPt
            let down = UIInterpolatingMotionEffect(keyPath: "center.y", type: .tiltAlongVerticalAxis)
            down.minimumRelativeValue = -overscanPt
            down.maximumRelativeValue = overscanPt
            let group = UIMotionEffectGroup()
            group.motionEffects = [across, down]
            view.addMotionEffect(group)
        }
    }
}
