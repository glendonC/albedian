import AlbedianCaptureCore
import SwiftUI

/// Hold still (UIUX-arrive-READY iPhone 4, the mockup `arrive/screen-6.html`): the overlay hidden, "Hold still" at the
/// top, two crosshairs in the preview's centre (a fixed one in ink, and one at 50 percent moved by the gyroscope, as in
/// the iPhone Camera's Night mode), the exposure's length under the preview, and the capture button whose ring fills
/// over that real exposure. A second tap stops.
struct HoldStillScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            let height = width * 4 / 3
            TimelineView(.animation) { timeline in
                let elapsed = model.standInElapsedSec ?? model.exposureBegan.map { timeline.date.timeIntervalSince($0) } ?? 0
                let fraction = NightExposure.ringFraction(elapsedSec: elapsed, exposureSec: model.exposureSec ?? 0)
                let shake = NightExposure.crosshairOffset(start: model.startAttitude, now: model.currentAttitude, previewWidthPt: width,
                                                          horizontalFovDeg: model.camera.previewAcrossFieldOfViewDeg ?? 0)
                ZStack {
                    CameraPreview(session: model.camera.session)
                        .frame(width: CGFloat(width), height: CGFloat(height)).clipped()
                        .overlay {
                            ZStack {
                                Crosshair().stroke(Color(ControlStyle.ink), lineWidth: 1.2)
                                if let shake {
                                    Crosshair().stroke(Color(ControlStyle.ink).opacity(0.5), lineWidth: 1.2)
                                        .offset(x: CGFloat(shake.dx), y: CGFloat(shake.dy))
                                }
                            }
                            .frame(width: 52, height: 52)
                        }
                        .atTop(Arrive.photoTop)
                    Text("Hold still").font(AlbedianFont.regular(15)).tracking(-0.01 * 15).foregroundStyle(Color(ControlStyle.ink))
                        .frame(height: 44).atTop(Arrive.topControls)
                    if let seconds = model.exposureSec, let words = NightExposure.lengthWords(seconds) {
                        Text(words).font(AlbedianFont.regular(12).monospacedDigit()).tracking(-0.005 * 12)
                            .foregroundStyle(Color(ControlStyle.inkQuiet))
                            .atTop(Arrive.photoTop + height + 16)
                    }
                    ExposureButton(fraction: fraction, action: model.stopExposure).atBottom(Arrive.referenceHeight - 706 - 64)
                }
            }
        }
        .ignoresSafeArea()
    }
}

/// One crosshair of the mockup: four strokes from 9 to 26 pt out from the centre.
struct Crosshair: Shape {
    func path(in rect: CGRect) -> Path {
        let c = CGPoint(x: rect.midX, y: rect.midY)
        var path = Path()
        for (dx, dy) in [(-1.0, 0.0), (1, 0), (0, -1), (0, 1)] {
            path.move(to: CGPoint(x: c.x + dx * 9, y: c.y + dy * 9))
            path.addLine(to: CGPoint(x: c.x + dx * 26, y: c.y + dy * 26))
        }
        return path
    }
}

/// The capture button during the exposure (the mockup's "Stop" button): the ring of radius 31 at 22 percent ink, the
/// share of the exposure passed drawn over it in ink, 2 pt with round ends from the top clockwise, and the disc with
/// Albedian's star cut out. Tapping it stops.
struct ExposureButton: View {
    let fraction: Double
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle().stroke(Color(ControlStyle.ink).opacity(0.22), lineWidth: 1.5)
                    .frame(width: CGFloat(2 * ShutterGeometry.ringRadius), height: CGFloat(2 * ShutterGeometry.ringRadius))
                Circle().trim(from: 0, to: CGFloat(fraction))
                    .stroke(Color(ControlStyle.ink), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .frame(width: CGFloat(2 * ShutterGeometry.ringRadius), height: CGFloat(2 * ShutterGeometry.ringRadius))
                ShutterDiscShape().fill(Color(ControlStyle.ink))
                    .frame(width: CGFloat(2 * ShutterGeometry.discRadius), height: CGFloat(2 * ShutterGeometry.discRadius))
            }
            .frame(width: CGFloat(ShutterGeometry.side), height: CGFloat(ShutterGeometry.side))
            .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Stop")
    }
}

/// The capture's one place (MOTION-READY-iphone section 3): aiming, holding still and after the shot, changed inside the
/// screen, never pushed. Aiming and Hold still show the same live preview and capture button in the same place, so
/// crossfading the two shows exactly the note's sequence: 0.15 s after the press the aiming tag, rings and line leave
/// (frameOut) and "Hold still", the crosshairs and the length arrive (frameIn), the preview and the button unchanged. The
/// shot taken: the hold-still pieces leave and the photo with Take again and Send arrive. The dissolve with Reduce Motion.
struct CaptureStage: View {
    let model: CompanionModel
    let screen: CompanionModel.Screen
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            switch screen {
            case .aim: AimScreen(model: model).transition(stage(inAfter: 0, outAfter: MotionStyle.captureSequenceDelaySeconds))
            case .holdStill: HoldStillScreen(model: model).transition(stage(inAfter: MotionStyle.captureSequenceDelaySeconds, outAfter: 0))
            default: AfterShotScreen(model: model).transition(stage(inAfter: 0, outAfter: 0))
            }
        }
    }

    private func stage(inAfter: Double, outAfter: Double) -> AnyTransition {
        .asymmetric(insertion: .opacity.animation(.motion(MotionStyle.frameIn, after: inAfter, reduceMotion: reduceMotion)),
                    removal: .opacity.animation(.motion(MotionStyle.frameOut, after: outAfter, reduceMotion: reduceMotion)))
    }
}
