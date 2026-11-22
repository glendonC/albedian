import AlbedianCaptureCore
import SwiftUI

/// Hold still (UIUX-arrive-READY iPhone 4, the mockup `arrive/screen-6.html`): the overlay hidden, "Hold still" at the
/// top, two crosshairs in the preview's centre (a fixed one in ink, and one at 50 percent moved by the gyroscope, as in
/// the iPhone Camera's Night mode), the exposure's length under the preview, and the capture button whose ring fills
/// over that real exposure. A second tap stops.
struct HoldStillScreen: View {
    let model: CompanionModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Set once the screen is up, so its words arrive by their own motion (MOTION-READY-iphone section 3)
    @State private var arrived = false

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
                    // "Hold still" and the length word in 0.15 s after the press, the length 20 ms after it
                    if arrived {
                        Text("Hold still").font(AlbedianFont.regular(15)).tracking(-0.01 * 15).foregroundStyle(Color(ControlStyle.ink))
                            .frame(height: 44).atTop(Arrive.topControls)
                            .transition(.words(reduceMotion: reduceMotion, after: MotionStyle.captureSequenceDelaySeconds))
                    }
                    if arrived, let seconds = model.exposureSec, let words = NightExposure.lengthWords(seconds) {
                        Text(words).font(AlbedianFont.regular(12).monospacedDigit()).tracking(-0.005 * 12)
                            .foregroundStyle(Color(ControlStyle.inkQuiet))
                            .atTop(Arrive.photoTop + height + 16)
                            .transition(.words(reduceMotion: reduceMotion, after: MotionStyle.captureSequenceDelaySeconds + MotionStyle.staggerSeconds))
                    }
                    ExposureButton(fraction: fraction, action: model.stopExposure).atBottom(Arrive.referenceHeight - 706 - 64)
                }
            }
        }
        .ignoresSafeArea()
        .onAppear { arrived = true }
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
/// The aim view goes and comes on the same timings, but as a live view the stage keeps drawn until its leaving ends:
/// SwiftUI does not redraw a view's contents while a transition removes it, so a removal transition could not fade the
/// rings apart from the tag (the simulator strip of 22 Nov: the rings stood at full and went at once). Its `hidden`
/// is animated instead, so its rings fade while its Predicted tag and source line stand at full until the rings' last
/// frame, and the view is then taken away at once (the lead, 21 Nov 13:48, `AimExit`). It is drawn over the stage's
/// other screens so the rings fade by their own timing, and takes no touch once the press has left aiming.
struct CaptureStage: View {
    let model: CompanionModel
    let screen: CompanionModel.Screen
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Whether the aim view is drawn: while aiming, until its leaving ends, and again from coming back
    @State private var aimDrawn: Bool
    /// How far the aim view has gone, 0 shown to 1 gone; 1 whenever it is not drawn, so coming back starts from gone
    @State private var aimHidden: Double

    init(model: CompanionModel, screen: CompanionModel.Screen) {
        self.model = model
        self.screen = screen
        _aimDrawn = State(initialValue: screen == .aim)
        _aimHidden = State(initialValue: screen == .aim ? 0 : 1)
    }

    var body: some View {
        ZStack {
            switch screen {
            case .aim: EmptyView()
            case .holdStill: HoldStillScreen(model: model).transition(stage(inAfter: MotionStyle.captureSequenceDelaySeconds, outAfter: 0))
            default: AfterShotScreen(model: model).transition(stage(inAfter: 0, outAfter: 0))
            }
            if aimDrawn {
                AimScreen(model: model, hidden: aimHidden)
                    .allowsHitTesting(screen == .aim)
                    .accessibilityHidden(screen != .aim)
                    .transition(.identity)
                    // Coming back: drawn from gone, then in on frame in at once (a no-op when the stage opens on aiming)
                    .onAppear {
                        withAnimation(.motion(MotionStyle.frameIn, reduceMotion: reduceMotion)) { aimHidden = 0 }
                    }
            }
        }
        .onChange(of: screen) { old, new in
            if new == .aim {
                if aimDrawn {
                    withAnimation(.motion(MotionStyle.frameIn, reduceMotion: reduceMotion)) { aimHidden = 0 }
                } else {
                    aimDrawn = true
                }
            } else if old == .aim {
                // Out on frame out 0.15 s after the press; taken away at the rings' last frame unless aiming came back
                withAnimation(.motion(MotionStyle.frameOut, after: MotionStyle.captureSequenceDelaySeconds, reduceMotion: reduceMotion)) {
                    aimHidden = 1
                } completion: {
                    if model.captureScreen != .aim { aimDrawn = false }
                }
            }
        }
    }

    private func stage(inAfter: Double, outAfter: Double) -> AnyTransition {
        .asymmetric(insertion: .opacity.animation(.motion(MotionStyle.frameIn, after: inAfter, reduceMotion: reduceMotion)),
                    removal: .opacity.animation(.motion(MotionStyle.frameOut, after: outAfter, reduceMotion: reduceMotion)))
    }
}
