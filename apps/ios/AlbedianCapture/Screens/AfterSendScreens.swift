import AlbedianCaptureCore
import SwiftUI

/// The wait while the laptop works (UIUX-iphone-solve-READY, 1; mockup is-wait): close top left, the photo as shot,
/// and at 668 the step the laptop is on, its name at the left (Geist 300, 28 pt, -0.035 em), its own count at the right
/// on the same baseline (Geist 15, 66 percent ink, tabular), and 12 pt under both a 2 pt line, ink at 14 percent, filled
/// in ink by done over total. Before the first job read the row is absent; no Send while it runs; nothing moves.
struct WaitingScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            ZStack {
                ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop)
                IconButton(icon: .close, label: "Close", action: model.close)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if let row = model.waitRow {
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        let passed = model.solveStartedAt.map { Double((ContinuousClock.now - $0).components.seconds) } ?? 0
                        VStack(spacing: 12) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(WaitRow.word(row.stage)).font(AlbedianFont.light(28)).tracking(-0.035 * 28)
                                    .foregroundStyle(Color(ControlStyle.ink))
                                Spacer(minLength: 12)
                                if let words = row.count.words(secondsPassed: passed) {
                                    Text(words).font(AlbedianFont.regular(15).monospacedDigit()).tracking(-0.01 * 15)
                                        .foregroundStyle(Color(ControlStyle.inkSecondary))
                                }
                            }
                            GeometryReader { line in
                                ZStack(alignment: .leading) {
                                    Rectangle().fill(Color(ControlStyle.hairline))
                                    if let fraction = row.count.fraction {
                                        Rectangle().fill(Color(ControlStyle.ink)).frame(width: line.size.width * CGFloat(fraction))
                                    }
                                }
                            }
                            .frame(height: 2)
                        }
                        .padding(.horizontal, CGFloat(Arrive.side))
                    }
                    .atTop(668)
                }
            }
        }
        .ignoresSafeArea()
    }
}

/// A failed job (UIUX-iphone-solve-READY, 3; mockup is-fail): the approved "Can't reach the laptop" layout with the close
/// circle, the photo, centred at 654 one 17 pt line with the warning-circle icon (18 pt) before the job's own sentence
/// verbatim (or the laptop's own words for a refused photo), and "Try again" alone at 730.
struct StoppedScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            ZStack {
                ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop)
                IconButton(icon: .close, label: "Close", action: model.close)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if let sentence = model.stoppedSentence {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        AlbedianIcon.warning.image(18).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
                        Text(sentence).font(AlbedianFont.regular(17)).tracking(-0.01 * 17).multilineTextAlignment(.leading)
                    }
                    .foregroundStyle(Color(ControlStyle.ink))
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(654)
                }
                MainButton(title: "Try again", icon: .send, action: model.tryAgainAfterStop).atBottom(Arrive.unreachableButtonsBottomGap)
            }
        }
        .ignoresSafeArea()
    }
}

/// D13, INTERIM (UIUX-iphone-kept-QUESTION; mockups ik-send and ik-going): the after-the-shot screen on the oldest
/// photo kept and never sent, with a back circle, "3 photos not sent" under the photo (12 pt, quiet ink) and "Send";
/// while they go, in the button's place the wait's row: "Sending", photos sent of photos kept, and the line they fill.
struct KeptScreen: View {
    let model: CompanionModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            ZStack {
                ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop)
                IconButton(icon: .back, label: "Back", action: model.showHome)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if let sending = model.keptSending {
                    VStack(spacing: 12) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(KeptWords.sending).font(AlbedianFont.light(28)).tracking(-0.035 * 28).foregroundStyle(Color(ControlStyle.ink))
                            Spacer(minLength: 12)
                            Text(KeptWords.count(sent: sending.sent, of: sending.total)).font(AlbedianFont.regular(15).monospacedDigit())
                                .tracking(-0.01 * 15).foregroundStyle(Color(ControlStyle.inkSecondary))
                        }
                        GeometryReader { line in
                            ZStack(alignment: .leading) {
                                Rectangle().fill(Color(ControlStyle.hairline))
                                Rectangle().fill(Color(ControlStyle.ink))
                                    .frame(width: line.size.width * CGFloat(KeptWords.fraction(sent: sending.sent, of: sending.total)))
                                    // MOTION-READY-iphone (D13): a photo sent changes the fill on the colour timing
                                    .animation(.motion(MotionStyle.colour, reduceMotion: reduceMotion), value: sending.sent)
                            }
                        }
                        .frame(height: 2)
                    }
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(668)
                } else {
                    Text(KeptWords.line(model.keptNotSent.count)).font(AlbedianFont.regular(12).monospacedDigit()).tracking(-0.005 * 12)
                        .foregroundStyle(Color(ControlStyle.inkQuiet))
                        .atTop(Arrive.photoTop + width * 4 / 3 + 16)
                    MainButton(title: "Send", icon: .send, action: model.sendKept).atBottom(Arrive.mainBottomGap)
                }
            }
        }
        .ignoresSafeArea()
    }
}
