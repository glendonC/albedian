import AlbedianCaptureCore
import SwiftUI

/// The wait while the laptop works (UIUX-iphone-solve-READY, 1; mockup is-wait): close top left, the photo as shot,
/// and at 668 the step the laptop is on, its name at the left (Geist 300, 28 pt, -0.035 em), its own count at the right
/// on the same baseline (Geist 15, 66 percent ink, tabular), and 12 pt under both a 2 pt line, ink at 14 percent, filled
/// in ink by done over total. Before the first job read the row is absent; no Send while it runs; nothing moves.
/// While the job is queued (D32, UIUX-iphone-queued-READY) the row says "Queued" in the name's place and "{n} ahead"
/// in the count's, its line empty; no Cancel on the phone.
struct WaitingScreen: View {
    let model: CompanionModel
    /// The last step the running job reported, kept once the job has ended until the result comes: through the lock-on's
    /// pass (D31's il-move keeps "Matching" and the sentence while the marks travel) and the moment before it starts.
    @State private var lastRow: WaitRow?
    /// The phone's own early rings have stood over the photo on this wait, so a swap to the laptop's keeps the phone's
    /// words until they have gone (`EarlyLine.namedSource`).
    @State private var phoneRingsDrawn = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// largertext-wait-m: the room the row and its line need at most at accessibility sizes, measured once from the most
    /// they can show (`WaitFrame.mostName`, `.mostCount`, `.mostSentence`), so the photo's frame never moves with them;
    /// nil until measured
    @State private var columnRoom: Double?

    var body: some View {
        GeometryReader { geometry in
            // largertext-wait-m (UIUX-iphone-largertext-sweep-READY item 1, DECIDED-BY-LEAD 24 Nov 19:56): at accessibility
            // sizes the photo and its rings whole in a smaller 3:4 frame, centred across, as tall as what is left above the
            // row and its line; at default sizes the screen's width, as before
            let frame = WaitFrame.size(screenWidth: Double(geometry.size.width), screenHeight: Double(geometry.size.height),
                                       columnRoom: dynamicTypeSize.isAccessibilitySize ? (columnRoom ?? 0) : nil)
            let width = frame.width
            // AUDIT-D on largertext-wait-m: until that room is measured the photo, its rings and the row are not drawn, so
            // the first frame never shows the photo at a size it then leaves
            let measuring = dynamicTypeSize.isAccessibilitySize && columnRoom == nil
            // D31: the early rings that fall inside the photo: the laptop's early sky when its job serves one (lockon-m5),
            // else the phone's own projection of its record; the source sentence stands only beside at least one
            let early = earlyPlaced(width: width, height: width * 4 / 3)
            // The 17:38 tag rule: early marks only while their source line stands under a row (UIUX-built-1756)
            let rowShown = model.waitRow ?? lastRow
            let earlyDrawn = EarlyLine.marksShown(placed: early.count, rowShown: rowShown != nil || model.queuedRow != nil)
            let sentence = EarlyLine.sentence(marksShown: earlyDrawn,
                                              laptopSource: EarlyLine.namedSource(laptop: model.laptopEarly?.sky.source,
                                                                                  phoneRingsDrawn: phoneRingsDrawn,
                                                                                  swapOver: model.laptopSwapOver,
                                                                                  reduceMotion: reduceMotion))
            ZStack {
                if dynamicTypeSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 14) {
                        StepRow(name: WaitFrame.mostName, count: WaitFrame.mostCount, fraction: nil)
                        EarlySource(sentence: WaitFrame.mostSentence)
                    }
                    .hidden()
                    .accessibilityHidden(true)
                    .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { columnRoom = $0 }
                }
                ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop).opacity(measuring ? 0 : 1)
                // D31: the early marks over the photo, from the phone's own record (predicted until matched)
                if earlyDrawn {
                    if let pass = model.lockOnPass {
                        LockOnLayer(placed: early,
                                    targets: LockOnTravel.targets(early: early.map(\.0), readout: pass.readout, frame: pass.frame,
                                                                  widthPt: width, heightPt: width * 4 / 3),
                                    settled: model.lockOnSettled, width: width, height: width * 4 / 3)
                            .atTop(Arrive.photoTop)
                            .opacity(measuring ? 0 : 1)
                    } else {
                        EarlyMarksLayer(placed: early, source: model.laptopEarly == nil ? "phone" : "laptop",
                                        width: width, height: width * 4 / 3)
                            .atTop(Arrive.photoTop)
                            .opacity(measuring ? 0 : 1)
                    }
                }
                IconButton(icon: .close, label: "Close", action: model.close)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if let row = rowShown {
                    TimelineView(.periodic(from: .now, by: 1)) { _ in
                        let passed = model.solveStartedAt.map { Double((ContinuousClock.now - $0).components.seconds) } ?? 0
                        // A new step is a new row: its line starts from its own count, never sliding back from the last
                        VStack(alignment: .leading, spacing: 14) {
                            StepRow(name: WaitRow.word(row.stage), count: row.count.words(secondsPassed: passed), fraction: row.count.fraction)
                                .id(row.stage)
                            EarlySource(sentence: sentence)
                        }
                    }
                    // 30 pt under the photo: 668 on the record's 390 pt screen, where the photo ends at 638
                    .atTop(Arrive.photoTop + width * 4 / 3 + 30)
                    .opacity(measuring ? 0 : 1)
                } else if let queued = model.queuedRow {
                    VStack(alignment: .leading, spacing: 14) {
                        StepRow(name: QueuedRow.word, count: queued.aheadWords, fraction: nil)
                        EarlySource(sentence: sentence)
                    }
                    .atTop(Arrive.photoTop + width * 4 / 3 + 30)
                    .opacity(measuring ? 0 : 1)
                }
            }
            .onChange(of: earlyDrawn && model.laptopEarly == nil, initial: true) { _, phoneDrawn in
                if phoneDrawn { phoneRingsDrawn = true }
            }
        }
        .ignoresSafeArea()
        .onChange(of: model.waitRow) { _, row in if let row { lastRow = row } }
        .onAppear { lastRow = model.waitRow }
    }

    /// The early marks on the photo's frame: the laptop's early sky when served, else the phone's own projection.
    private func earlyPlaced(width: Double, height: Double) -> [(SkyMark, CGPoint)] {
        if let laptop = model.laptopEarly {
            return laptop.sky.placed(frame: laptop.frame, widthPt: width, heightPt: height).map { ($0.mark, CGPoint(x: $0.x, y: $0.y)) }
        }
        guard let pose = model.earlyPose, !model.earlyMarks.isEmpty else { return [] }
        return SkyOverlayView.placed(model.earlyMarks, attitude: pose.attitude, acrossFovDeg: pose.acrossPortraitWidthDeg,
                                     width: width, height: height)
    }
}

/// D31 item 3 with MOTION-READY-iphone section 5: each early ring the solve matched travels to its detection, all
/// together in 0.4 s on (0.16, 1, 0.3, 1), no spring; every other ring leaves on frame out (0.3 s); at rest
/// each ring becomes the result's catalog matched mark in 0.2 s on the standard curve. Under Reduce Motion nothing
/// travels: the early rings go and the matched marks come, together, in a 0.27 s dissolve.
private struct LockOnLayer: View {
    let placed: [(SkyMark, CGPoint)]
    let targets: [String: (x: Double, y: Double)]
    let settled: Bool
    let width: Double
    let height: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var moved = false

    var body: some View {
        ZStack(alignment: .topLeading) {
            ForEach(placed, id: \.0.id) { mark, start in
                let end = targets[mark.id].map { CGPoint(x: $0.x, y: $0.y) }
                let at = reduceMotion ? start : (moved ? (end ?? start) : start)
                // The early ring: amber and dashed, gone at rest (or at once under Reduce Motion), or leaving if unmatched
                Circle()
                    .stroke(Color(ControlStyle.Evidence.predicted.colour),
                            style: StrokeStyle(lineWidth: CGFloat(SkyOverlayStyle.ringLineWidthPt),
                                               dash: SkyOverlayStyle.ringDash.map { CGFloat($0) }))
                    .frame(width: CGFloat(2 * SkyOverlayStyle.ringRadiusPt), height: CGFloat(2 * SkyOverlayStyle.ringRadiusPt))
                    .opacity(moved && (end == nil || reduceMotion || settled) ? 0 : 1)
                    // A ring the solve does not place leaves on frame out (0.3 s on .4, 0, 1, 1), as the web's
                    // .lockon-mark.is-leaving does (MOTION's check, item 3)
                    .animation(end == nil && !reduceMotion ? .motion(MotionStyle.frameOut, reduceMotion: false) : nil, value: moved)
                    .position(at)
                if let end {
                    MatchedMark()
                        .opacity(reduceMotion ? (moved ? 1 : 0) : (settled ? 1 : 0))
                        .position(reduceMotion ? end : at)
                }
            }
        }
        .frame(width: CGFloat(width), height: CGFloat(height), alignment: .topLeading)
        .animation(reduceMotion ? .easeInOut(duration: MotionStyle.reduceMotionSeconds)
                                : .motion(MotionStyle.frameIn, reduceMotion: false), value: moved)
        .animation(reduceMotion ? nil : .motion(MotionStyle.colour, reduceMotion: false), value: settled)
        .onAppear { moved = true }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// The result's catalog matched mark (`CatalogMarks`): a 3.6 pt ring and four ticks in the class's colour.
private struct MatchedMark: View {
    var body: some View {
        Canvas { context, size in
            let c = CGPoint(x: size.width / 2, y: size.height / 2)
            let colour = Color(ControlStyle.Evidence.catalogMatched.colour)
            context.stroke(Path(ellipseIn: CGRect(x: c.x - 3.6, y: c.y - 3.6, width: 7.2, height: 7.2)), with: .color(colour), lineWidth: 1)
            var ticks = Path()
            for (dx, dy) in [(0.0, -1.0), (0, 1), (-1, 0), (1, 0)] {
                ticks.move(to: CGPoint(x: c.x + dx * 5, y: c.y + dy * 5))
                ticks.addLine(to: CGPoint(x: c.x + dx * 7.5, y: c.y + dy * 7.5))
            }
            context.stroke(ticks, with: .color(colour), lineWidth: 1)
        }
        .frame(width: 16, height: 16)
    }
}

/// The early marks as one view each, keyed by their catalogue id (or a body's name), so a better early pose arriving
/// is one correction, never a blink (ruled 18:05, UIUX-iphone-lockon-READY): a ring in both sets moves from the phone's
/// place to the laptop's, all together, on the lock-on's travel (0.4 s on .16, 1, .3, 1); a ring only in the old set
/// leaves on frame out and one only in the new set arrives on frame in once that travel is over, brightest first with
/// the marks' stagger, as the first set came (MOTION-READY-iphone section 6, `EarlyArrival`); under Reduce Motion the
/// old go and the new come in the 0.27 s dissolve, together. Names by D20 over the current set.
private struct EarlyMarksLayer: View {
    let placed: [(SkyMark, CGPoint)]
    /// Which early pose placed them: under Reduce Motion a new source is new marks, so nothing travels and the sets dissolve.
    let source: String
    let width: Double
    let height: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func key(_ mark: SkyMark) -> String { reduceMotion ? "\(source) \(mark.swapKey)" : mark.swapKey }

    private struct Keyed { let key: String; let mark: SkyMark; let at: CGPoint }

    /// Each mark once by its key, in the set's order (a second mark with the same key is left out).
    private var keyed: [Keyed] {
        var seen = Set<String>()
        return placed.compactMap { mark, at in seen.insert(key(mark)).inserted ? Keyed(key: key(mark), mark: mark, at: at) : nil }
    }

    /// The keys drawn before this set: empty for the first set, the old set on a swap (MOTION's section 6).
    @State private var shown: Set<String> = []

    var body: some View {
        let names = SkyOverlayNames.names(placed.map { SkyOverlayNames.Placed(mark: $0.0, x: $0.1.x, y: $0.1.y) }, widthPt: width, heightPt: height)
        let rings = keyed
        let delays = EarlyArrival.delays(shown: shown, now: rings.map { EarlyArrival.Ring(key: $0.key, mag: $0.mark.mag) },
                                         reduceMotion: reduceMotion)
        ZStack(alignment: .topLeading) {
            ForEach(rings, id: \.key) { item in
                ArrivingMark(delay: delays[item.key] ?? 0) {
                    EarlyMark(mark: item.mark, onLeft: names[item.mark.id], previewWidthPt: width)
                }
                    .position(item.at)
                    .transition(.asymmetric(insertion: .identity,
                                            removal: .opacity.animation(.motion(MotionStyle.frameOut, reduceMotion: reduceMotion))))
            }
        }
        .frame(width: CGFloat(width), height: CGFloat(height), alignment: .topLeading)
        // A ring in both sets travels to its new place, from wherever it stands (a later pose included)
        .animation(reduceMotion ? nil : .motion(MotionStyle.frameIn, reduceMotion: false),
                   value: rings.map { "\(source) \($0.key) \($0.at.x) \($0.at.y)" })
        .onAppear { shown = Set(rings.map(\.key)) }
        .onChange(of: rings.map(\.key)) { _, keys in shown = Set(keys) }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// One early ring coming in on frame in after its wait (`EarlyArrival`), or the 0.27 s dissolve under Reduce Motion.
private struct ArrivingMark<Content: View>: View {
    let delay: Double
    @ViewBuilder let content: Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var arrived = false

    var body: some View {
        content
            .opacity(arrived ? 1 : 0)
            .onAppear {
                withAnimation(.motion(MotionStyle.frameIn, after: delay, reduceMotion: reduceMotion)) { arrived = true }
            }
    }
}

/// One early mark drawn as D20 draws it, centred on its own frame so it can move as one piece without clipping long names.
private struct EarlyMark: View {
    let mark: SkyMark
    let onLeft: Bool?
    let previewWidthPt: Double

    var body: some View {
        let width = SkyOverlayNames.markFrameWidthPt(name: mark.name, named: onLeft != nil, previewWidthPt: previewWidthPt)
        Canvas { context, size in
            PlacedSkyOverlay.draw(mark, at: CGPoint(x: size.width / 2, y: size.height / 2), onLeft: onLeft, named: onLeft != nil, in: context)
        }
        .frame(width: CGFloat(width), height: CGFloat(SkyOverlayNames.markFrameHeightPt))
    }
}

private extension SkyMark {
    var swapKey: String { catalogSourceId ?? name }
}

/// D31 item 2: C13's source sentence 14 pt under the step row's line, 24 pt in from each side, 12 pt at ink 44 percent,
/// line height 1.4; nothing when no early mark is drawn. On the lock-on's pass it stays at full until the early rings'
/// last frame and leaves with the wait (the lead, 21 Nov 12:59, AUDIT-C's sweep item 5, as motion-43 did on the web).
private struct EarlySource: View {
    /// The early sky's source sentence (C13 for the iPhone's record, D25's for the Moon or the Sun), or none.
    let sentence: String?

    var body: some View {
        if let sentence {
            Text(sentence).font(AlbedianFont.regular(12)).lineSpacing(12 * 0.2)
                .foregroundStyle(Color(ControlStyle.inkQuiet))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, CGFloat(Arrive.side))
        }
    }
}

/// The wait's one row: the name at the left (Geist 300, 28 pt, -0.035 em), the count at the right on the same baseline
/// (Geist 15, 66 percent ink, tabular), and 12 pt under both the 2 pt line, filled in ink by `fraction` when there is one.
private struct StepRow: View {
    let name: String
    let count: String?
    let fraction: Double?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 12) {
            // largertext-wait-m: the name and its count side by side while both fit, else the count under the name, 8 pt
            // under it as the Result's stacked counts (the sweep record's rule: words side by side stack, never cut)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline) {
                    nameText
                    Spacer(minLength: 12)
                    if let count { countText(count) }
                }
                VStack(alignment: .leading, spacing: 8) {
                    nameText
                    if let count { countText(count) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            GeometryReader { line in
                ZStack(alignment: .leading) {
                    Rectangle().fill(Color(ControlStyle.hairline))
                    if let fraction {
                        Rectangle().fill(Color(ControlStyle.ink)).frame(width: line.size.width * CGFloat(fraction))
                            // Each report moves the fill on the progress timing, at once with Reduce Motion
                            .animation(.progress(reduceMotion: reduceMotion), value: fraction)
                    }
                }
            }
            .frame(height: 2)
            .accessibilityHidden(true)
        }
        .accessibilityElement(children: .combine)
        .padding(.horizontal, CGFloat(Arrive.side))
    }

    private var nameText: some View {
        Text(name).font(AlbedianFont.light(28)).tracking(-0.035 * 28)
            .foregroundStyle(Color(ControlStyle.ink))
    }

    private func countText(_ count: String) -> some View {
        Text(count).font(AlbedianFont.regular(15).monospacedDigit()).tracking(-0.01 * 15)
            .foregroundStyle(Color(ControlStyle.inkSecondary))
    }
}

/// A failed job (UIUX-iphone-solve-READY, 3; mockup is-fail): the approved "Can't reach the laptop" layout with the close
/// circle, the photo, centred at 654 one 17 pt line with the warning-circle icon (18 pt) before the job's own sentence
/// verbatim (or the laptop's own words for a refused photo), and "Try again" alone at 730.
struct StoppedScreen: View {
    let model: CompanionModel
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// largertext-refused-m: the room the sentence and Try again need at accessibility sizes, measured once from them,
    /// hidden; nil until measured, and nothing of the column is drawn before
    @State private var columnRoom: Double?

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            ZStack {
                if dynamicTypeSize.isAccessibilitySize {
                    // largertext-refused-m (UIUX-iphone-largertext-sweep-READY item 5, DECIDED-BY-LEAD 24 Nov 19:56): the
                    // sentence whole, its icon at its first line, Try again 16 pt under it in the flow, on the foot and
                    // growing upward; the photo gives the room down to the floor of Can't reach the laptop's column, as the
                    // record's rule has it, and past it the column scrolls
                    let frame = UnreachableFrame.size(screenWidth: width, screenHeight: Double(geometry.size.height),
                                                      columnRoom: columnRoom ?? 0)
                    stoppedColumn
                        .hidden()
                        .accessibilityHidden(true)
                        .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { columnRoom = $0 }
                    ScrollView {
                        VStack(spacing: 0) {
                            ShotPhoto(shot: model.shot, width: frame.width)
                            Spacer(minLength: CGFloat(UnreachableFrame.gap))
                            stoppedColumn
                        }
                        .frame(maxWidth: .infinity, minHeight: geometry.size.height - CGFloat(UnreachableFrame.top + UnreachableFrame.foot))
                        .padding(.top, CGFloat(UnreachableFrame.top))
                        .padding(.bottom, CGFloat(UnreachableFrame.foot))
                    }
                    .scrollBounceBehavior(.basedOnSize)
                    .opacity(columnRoom == nil ? 0 : 1)
                } else {
                    ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop)
                    if let sentence = model.stoppedSentence {
                        sentenceLine(sentence)
                            .padding(.horizontal, CGFloat(Arrive.side))
                            // 16 pt under the photo: 654 on the record's 390 pt screen
                            .atTop(Arrive.photoTop + width * 4 / 3 + 16)
                    }
                    MainButton(title: "Try again", icon: .send, action: model.tryAgainAfterStop).atBottom(Arrive.unreachableButtonsBottomGap)
                }
                IconButton(icon: .close, label: "Close", action: model.close)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
            }
        }
        .ignoresSafeArea()
    }

    private func sentenceLine(_ sentence: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            AlbedianIcon.warning.image(18).alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }.accessibilityHidden(true)
            Text(sentence).font(AlbedianFont.regular(17)).tracking(-0.01 * 17).multilineTextAlignment(.leading)
        }
        .foregroundStyle(Color(ControlStyle.ink))
    }

    /// At accessibility sizes: the sentence whole on as many lines as it needs, then Try again 16 pt under it, centred,
    /// within the screen's 24 pt margins.
    private var stoppedColumn: some View {
        VStack(spacing: 16) {
            if let sentence = model.stoppedSentence {
                sentenceLine(sentence).frame(maxWidth: .infinity, alignment: .leading)
            }
            MainButton(title: "Try again", icon: .send, action: model.tryAgainAfterStop)
        }
        .padding(.horizontal, CGFloat(ButtonGeometry.margin))
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
                                    // A photo sent moves the fill on the progress timing, at once with Reduce Motion
                                    .animation(.progress(reduceMotion: reduceMotion), value: sending.sent)
                            }
                        }
                        .frame(height: 2)
                        .accessibilityHidden(true)
                    }
                    .accessibilityElement(children: .combine)
                    .padding(.horizontal, CGFloat(Arrive.side))
                    // 30 pt under the photo: 668 on the record's 390 pt screen
                    .atTop(Arrive.photoTop + width * 4 / 3 + 30)
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
