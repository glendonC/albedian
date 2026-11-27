import AlbedianCaptureCore
import SwiftUI
import UIKit

/// The approved iPhone screens' numbers, in points on the 390 by 844 screen the records draw (UIUX-arrive-READY,
/// UIUX-credit, UIUX-connect, UIUX-unreachable; the recovered mockup stylesheets `arrive/page.css`, `phone3/page.css`).
/// On another screen size the top of each screen keeps its distance from the top, the actions at the bottom keep
/// theirs from the bottom, and the photo spans the width at 3:4 [inference: the records draw one size only].
enum Arrive {
    static let referenceHeight = 844.0
    static let edge = 20.0            // icon buttons and the Credits pill from the screen's side
    static let topControls = 60.0     // icon buttons, the Credits pill, the result's status word
    static let side = 24.0            // titles, lists, the field, the result's card
    static let photoTop = 118.0       // the 3:4 photo or preview
    static let mainBottomGap = 84.0   // a main button's top at 716: 844 - 716 - 44
    static let unreachableButtonsBottomGap = 70.0 // "Try again" and "Change address" at 730
    static let titleSize = 40.0       // Credits, Connect to your laptop: Geist 300, line height 1.05, -0.045 em
    static let titleTracking = -0.045
    static let titleTop = 128.0
    /// largertext-scroll-edge-m: a top bar's foot at accessibility sizes, the controls' top and their 44 pt
    static var topBarFoot: Double { topControls + 44 }
}

/// largertext-scroll-edge-m (UIUX-scroll-under-controls-READY, ruled 25 Nov 00:53): at accessibility sizes a screen's back
/// or close button stands in a top safe-area bar over its scrolling column, at its own size and place (60 pt from the top,
/// 20 pt in), so the system draws its scroll edge effect beneath it as the column scrolls under.
struct TopBar<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        HStack(spacing: 0) {
            content
            Spacer(minLength: 0)
        }
        .padding(.leading, CGFloat(Arrive.edge))
        .padding(.top, CGFloat(Arrive.topControls))
    }
}

extension View {
    /// The view's top at `y` points from the screen's top, centred across.
    func atTop(_ y: Double, alignment: HorizontalAlignment = .center) -> some View {
        frame(maxWidth: .infinity, maxHeight: .infinity, alignment: Alignment(horizontal: alignment, vertical: .top)).padding(.top, CGFloat(y))
    }

    /// The view's bottom `gap` points above the screen's bottom, centred across.
    func atBottom(_ gap: Double) -> some View {
        // mainbutton-scale-m: within the screen's 24 pt margins, so a grown button wraps its word rather than passing them
        padding(.horizontal, CGFloat(ButtonGeometry.margin))
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom).padding(.bottom, CGFloat(gap))
    }

    func title() -> some View {
        font(AlbedianFont.light(Arrive.titleSize))
            .tracking(CGFloat(Arrive.titleTracking * Arrive.titleSize))
            .foregroundStyle(Color(ControlStyle.ink))
    }
}

/// The moment's root: one screen at a time, on black, with the system's light status bar text.
struct CompanionRoot: View {
    @State private var model = CompanionModel()

    var body: some View {
        // MOTION-READY-iphone section 1: forward screens by NavigationStack's own push, back by its pop, unstyled; its bar
        // and back button hidden, since each screen has its own close or back control (the approved screens)
        NavigationStack(path: Binding(get: { model.path }, set: { model.navigated(to: $0) })) {
            screen(.home)
                .navigationDestination(for: CompanionPlace.self) { place in
                    screen(shown(at: place))
                }
        }
        .preferredColorScheme(.dark)
        .sheet(item: $model.openPage) { page in CoveredSafariView(url: page.url).ignoresSafeArea() }
        #if DEBUG
        .task { model.runFromLaunchArguments() }
        #endif
    }

    /// What a place shows: the current screen when it is the top place, else what it showed last.
    private func shown(at place: CompanionPlace) -> CompanionModel.Screen {
        if model.screen.place == place { return model.screen }
        switch place {
        case .credits: return .credits
        case .kept: return .kept
        case .capture: return model.captureScreen
        case .pairing: return .pairing
        case .sent: return .waiting
        case .result: return model.resultScreen
        }
    }

    private func screen(_ screen: CompanionModel.Screen) -> some View {
        ZStack {
            Color(ControlStyle.field).ignoresSafeArea()
            switch screen {
            case .home: HomeScreen(model: model)
            case .credits: CreditsScreen(model: model)
            case .aim, .holdStill, .afterShot: CaptureStage(model: model, screen: screen)
            case .pairing: ConnectScreen(model: model)
            case .waiting: WaitingScreen(model: model)
            case .stopped: StoppedScreen(model: model)
            case .result: ResultScreen(model: model)
            case .kept: KeptScreen(model: model)
            }
        }
        .toolbar(.hidden, for: .navigationBar)
        .navigationBarBackButtonHidden(true)
    }
}

/// Home (arrive iPhone 1 with UIUX-credit): the Milky Way, darkened toward the bottom, the logo at 52 pt, "Start", and
/// the "Credits" pill top right.
struct HomeScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Image("milky-way-home").resizable().scaledToFill()
                    .frame(width: geometry.size.width, height: geometry.size.height).clipped()
                    .accessibilityHidden(true)
                // linear-gradient(to top, rgba(0,0,0,.9) 0%, rgba(0,0,0,.35) 34%, rgba(0,0,0,0) 55%)
                LinearGradient(stops: HomeBackdrop.shade.map { .init(color: .black.opacity($0.opacity), location: CGFloat($0.location)) },
                               startPoint: .bottom, endPoint: .top)
                // largertext-home-m (item 2): the two top pills are bar items, held at the first accessibility size, where
                // both stand whole side by side, each shown larger by Apple's large content viewer on a long press
                OutlinePill(title: "Credits", height: 36, fontSize: 15, action: model.showCredits)
                    .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                    .accessibilityShowsLargeContentViewer()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                    .padding(.top, CGFloat(Arrive.topControls)).padding(.trailing, CGFloat(Arrive.edge))
                // D13, INTERIM (ik-home): only while photos are kept that were never sent, a pill opposite "Credits"
                if !model.keptNotSent.isEmpty {
                    OutlinePill(title: KeptWords.pill(model.keptNotSent.count), height: 36, fontSize: 15, action: model.openKept)
                        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                        .accessibilityShowsLargeContentViewer()
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                        .padding(.top, CGFloat(Arrive.topControls)).padding(.leading, CGFloat(Arrive.edge))
                }
                // The logo row's top at 574 on the 844 point screen
                Logo(size: 52).atBottom(0).padding(.bottom, CGFloat(Arrive.referenceHeight - 574) - 62)
                MainButton(title: "Start", icon: .arrow, action: model.startAiming).atBottom(Arrive.mainBottomGap)
                // home-credit-m (UIUX-homecredit-READY): ESO's credit on the picture, plain text centred 16 pt under Start,
                // clear of the home indicator; INTERIM at accessibility sizes, held at the first one as the top pills are
                Text(HomeCredit.line)
                    .font(AlbedianFont.regular(HomeCredit.size)).tracking(-0.005 * HomeCredit.size)
                    .foregroundStyle(Color(ControlStyle.inkSecondary))
                    .lineLimit(1)
                    .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                    .frame(height: CGFloat(HomeCredit.boxHeight))
                    .atBottom(Arrive.mainBottomGap - HomeCredit.gapUnderStart - HomeCredit.boxHeight)
            }
        }
        .ignoresSafeArea()
        .onAppear { model.refreshKept() }
    }
}

/// Credits (UIUX-credit-APPROVED): back, the title, and rows divided by hairlines, each a small grey label over its
/// lines. The two rows that link out carry the open-link icon (arrow-square-out, 16 pt, secondary ink), which opens
/// the source's page in the system's Safari view (mobile-4b).
struct CreditsScreen: View {
    let model: CompanionModel
    // Every line a credited source's terms require (`CreditsWords`, as the web's Credits carries them)
    private let rows = CreditsWords.rows

    var body: some View {
        ZStack {
            IconButton(icon: .back, label: "Back", action: model.showHome)
                .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
            Lines(lines: ["Credits"], font: AlbedianFont.light(Arrive.titleSize), size: Arrive.titleSize, lineHeight: 1.05, tracking: Arrive.titleTracking).title().atTop(Arrive.titleTop, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
            // The rows scroll under the title when the required lines run past the screen
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(rows, id: \.label) { row in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(row.label).font(AlbedianFont.regular(13)).tracking(-0.005 * 13).foregroundStyle(Color(ControlStyle.inkQuiet))
                            // As `Lines` draws them (16 pt, 1.4 a line: Geist's own 20.8 pt line and 1.6 between), a long line wrapping
                            VStack(alignment: .leading, spacing: 0) {
                                ForEach(Array(row.lines.enumerated()), id: \.offset) { pair in
                                    Text(pair.element).font(AlbedianFont.regular(16)).tracking(-0.01 * 16).lineSpacing(1.6)
                                        .fixedSize(horizontal: false, vertical: true)
                                        .frame(minHeight: 16 * 1.4, alignment: .leading)
                                }
                            }
                            .foregroundStyle(Color(ControlStyle.ink))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 16).padding(.trailing, 36)
                        .overlay(alignment: .trailing) {
                            if let page = row.page {
                                Button { model.openCredit(page) } label: {
                                    AlbedianIcon.openLink.image(16).foregroundStyle(Color(ControlStyle.inkSecondary))
                                        .frame(width: CGFloat(ControlStyle.minTouchTarget), height: CGFloat(ControlStyle.minTouchTarget), alignment: .trailing)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel(row.label)
                            }
                        }
                        .overlay(alignment: .top) { Rectangle().fill(Color(ControlStyle.hairline)).frame(height: 1) }
                    }
                }
                .overlay(alignment: .bottom) { Rectangle().fill(Color(ControlStyle.hairline)).frame(height: 1) }
                .padding(.horizontal, CGFloat(Arrive.side))
                .padding(.bottom, 40)
            }
            .scrollBounceBehavior(.basedOnSize)
            .padding(.top, 200)
        }
        .ignoresSafeArea()
    }
}

/// Aiming (arrive iPhone 2, without its overlay until mobile-5): close top left, the 3:4 preview from y 118 and the
/// capture button bottom centre. With no overlay drawn there is no "Predicted" tag and no source line. Leaving at the
/// press or coming back, every part fades as the stage animates `hidden` but the tag and the source line, which stand
/// at full until the rings' last frame (`AimExit`).
struct AimScreen: View {
    let model: CompanionModel
    /// How far the view has gone on its stage, 0 shown to 1 gone (`CaptureStage`), animated while the view stays live
    let hidden: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// largertext-m1: the room the column under the preview needs at most at accessibility sizes, measured once from
    /// the most it can show (`AimFrame.mostLine`, `.mostCoach`), so the preview's frame never moves with the lines shown;
    /// nil until measured
    @State private var columnRoom: Double?

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                if model.cameraRefused {
                    // D8 (UIUX-iphone-aim-QUESTION, ia-cam; decided by the lead, 19 Nov): no preview; why, centred at 340, and
                    // "Open Settings" where the capture button sits. Changing the camera's permission in Settings ends the app,
                    // so it opens again from its home
                    VStack(spacing: 6) {
                        HStack(spacing: 8) {
                            AlbedianIcon.cameraSlash.image(18).accessibilityHidden(true)
                            Text("Albedian can't use the camera").font(AlbedianFont.regular(17)).tracking(-0.01 * 17)
                        }
                        .foregroundStyle(Color(ControlStyle.ink))
                        Text("Allow the camera in Settings.").font(AlbedianFont.regular(14)).tracking(-0.005 * 14)
                            .foregroundStyle(Color(ControlStyle.inkSecondary))
                    }
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(340)
                    .opacity(AimExit.opacity(.rest, hidden: hidden))
                    MainButton(title: "Open Settings", icon: .arrow, action: SettingsPage.open).atBottom(Arrive.mainBottomGap)
                        .opacity(AimExit.opacity(.rest, hidden: hidden))
                } else if model.aimCamera.showsTryAgain {
                    // camera-unusable-m1 (UIUX-iphone-cameraunusable-READY, DECIDED-BY-LEAD 24 Nov 15:34): allowed but not
                    // usable, D8's camera state as laid out, its grey line only when another app may hold the camera, and
                    // "Try again" where the capture button sits; no capture button, no preview, no tag and no rings
                    VStack(spacing: 6) {
                        HStack(spacing: 8) {
                            AlbedianIcon.cameraSlash.image(18).accessibilityHidden(true)
                            Text(AimCamera.title).font(AlbedianFont.regular(17)).tracking(-0.01 * 17)
                        }
                        .foregroundStyle(Color(ControlStyle.ink))
                        if let detail = model.aimCamera.detail {
                            Text(detail).font(AlbedianFont.regular(14)).tracking(-0.005 * 14)
                                .foregroundStyle(Color(ControlStyle.inkSecondary))
                        }
                    }
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(340)
                    .opacity(AimExit.opacity(.rest, hidden: hidden))
                    MainButton(title: "Try again", icon: .arrow, action: model.tryCameraAgain).atBottom(Arrive.mainBottomGap)
                        .opacity(AimExit.opacity(.rest, hidden: hidden))
                } else {
                    // largertext-m1 (UIUX-iphone-largertext-READY, Aim re-ruled 24 Nov 15:52): at accessibility sizes the
                    // preview and its rings whole in a smaller 3:4 frame from 118, centred across, as tall as what is left
                    // above the column; at default sizes the screen's width, as before
                    let frame = AimFrame.size(screenWidth: Double(geometry.size.width),
                                              captureButtonTop: Double(geometry.size.height) - (Arrive.referenceHeight - 706 - 64) - 64,
                                              columnRoom: dynamicTypeSize.isAccessibilitySize ? (columnRoom ?? 0) : nil)
                    let width = frame.width
                    // aim-first-frame-one (AUDIT-D's point on the wait): until that room is measured the preview, its rings,
                    // tag and column are not drawn, so the first frame never shows the preview at a size it then leaves
                    let measuring = dynamicTypeSize.isAccessibilitySize && columnRoom == nil
                    if dynamicTypeSize.isAccessibilitySize {
                        AimLinesColumn(line: AimFrame.mostLine, coach: AimFrame.mostCoach, hidden: 0, reduceMotion: true)
                            .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                            .hidden()
                            .accessibilityHidden(true)
                            .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { columnRoom = $0 }
                    }
                    CameraPreview(session: model.camera.session)
                        .frame(width: width, height: width * 4 / 3).clipped()
                        .atTop(Arrive.photoTop)
                        .opacity(measuring ? 0 : AimExit.opacity(.rest, hidden: hidden))
                    // mobile-5: the overlay over the preview, the "Predicted" tag at top centre and the source line under it,
                    // redrawn with every frame while the laptop's marks are there
                    TimelineView(.animation) { _ in
                        // One read a frame: the rings, their tag and their source line from the same set, and no ring
                        // once the line says why there is none (AUDIT-C's sweep, items 10 and 11)
                        let aim = model.aimingNow()
                        let placed = SkyOverlayView.placed(aim.marks(model.skyMarks), attitude: model.currentAttitude,
                                                           acrossFovDeg: model.previewFovDeg, width: width, height: width * 4 / 3)
                        ZStack {
                            PlacedSkyOverlay(placed: placed, width: width, height: width * 4 / 3)
                                .atTop(Arrive.photoTop)
                                .opacity(AimExit.opacity(.rings, hidden: hidden))
                            // The tag stands from the rings' first frame however they come: the sky can arrive while the
                            // view is coming back on its stage's animation, which would otherwise fade the tag in with them
                            if !placed.isEmpty {
                                EvidenceTag(evidence: .predicted).frame(height: 44).atTop(Arrive.topControls)
                                    .opacity(AimExit.opacity(.tag, hidden: hidden))
                                    .transition(.identity)
                            }
                            // largertext-m1 (UIUX-iphone-largertext-READY, proposal 1, DECIDED-BY-LEAD 24 Nov 12:08): at
                            // accessibility sizes the line under the preview and the coach lines flow as one column, its
                            // text held at the first accessibility size so it ends clear of the capture button
                            if dynamicTypeSize.isAccessibilitySize {
                                AimLinesColumn(line: aim.shownLine(drawn: placed.map(\.0), headingAccuracyDeg: model.skyHeadingAccuracyDeg),
                                               coach: model.coachLines(), hidden: hidden, reduceMotion: reduceMotion)
                                    .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                                    .atTop(Arrive.photoTop + width * 4 / 3 + 16)
                            } else {
                                // D8, INTERIM (ia-head, ia-place): with no heading or no place, one small line under the preview says why;
                                // with both and marks drawn, the overlay's sources and the heading accuracy
                                // MOTION-READY-iphone: a missing-sensor line words in; the source line stands from the rings'
                                // first frame, no words-in delay or fade from nothing (the 17:38 rule, `AimLine`)
                                switch aim.shownLine(drawn: placed.map(\.0), headingAccuracyDeg: model.skyHeadingAccuracyDeg) {
                                case .why(let words)?:
                                    AimLineText(words: words).atTop(Arrive.photoTop + width * 4 / 3 + 16)
                                        .opacity(AimExit.opacity(.rest, hidden: hidden))
                                        .transition(.words(reduceMotion: reduceMotion))
                                case .source(let words)?:
                                    AimLineText(words: words).atTop(Arrive.photoTop + width * 4 / 3 + 16)
                                        .opacity(AimExit.opacity(.sourceLine, hidden: hidden))
                                        .transition(.identity)
                                case nil:
                                    EmptyView()
                                }
                                // D29, the capture coach: up to two lines 16 pt under the source line (670 on the 390 point
                                // screen), each one line, centred, 12 pt at ink 66 percent, 1.35 a line, 1 pt apart, its 14 pt
                                // icon 6 pt before the words; each words in and out as MOTION's record has every line
                                VStack(spacing: 1) {
                                    ForEach(model.coachLines(), id: \.words) { line in
                                        HStack(spacing: 6) {
                                            AlbedianIcon(rawValue: line.icon)?.image(14).accessibilityHidden(true)
                                            Text(line.words).font(AlbedianFont.regular(12)).tracking(-0.005 * 12).lineLimit(1)
                                        }
                                        .frame(height: 12 * 1.35)
                                        .foregroundStyle(Color(ControlStyle.inkSecondary))
                                        .transition(.words(reduceMotion: reduceMotion))
                                    }
                                }
                                .frame(maxWidth: .infinity).padding(.horizontal, 24)
                                .atTop(Arrive.photoTop + width * 4 / 3 + 32)
                                .opacity(AimExit.opacity(.rest, hidden: hidden))
                            }
                        }
                        .opacity(measuring ? 0 : 1)
                    }
                    // The 64 pt capture button's top at 706: its bottom 74 above the 844 point screen's
                    CaptureButton(action: model.shoot).atBottom(Arrive.referenceHeight - 706 - 64)
                        .opacity(AimExit.opacity(.rest, hidden: hidden))
                }
                IconButton(icon: .close, label: "Close", action: model.showHome)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                    .opacity(AimExit.opacity(.rest, hidden: hidden))
                // firstuse-connect-one (UIUX-iphone-firstuse-READY item 1): while no laptop is kept, "Connect laptop" in the
                // Credits pill's own form opposite Close, held at the first accessibility size as the home's top pills
                if !model.hasKeptLaptop {
                    OutlinePill(title: ConnectWords.pill, height: 36, fontSize: 15, action: model.connectFromAim)
                        .dynamicTypeSize(...DynamicTypeSize.accessibility1)
                        .accessibilityShowsLargeContentViewer()
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                        .padding(.top, CGFloat(Arrive.topControls)).padding(.trailing, CGFloat(Arrive.edge))
                        .opacity(AimExit.opacity(.rest, hidden: hidden))
                }
            }
        }
        .ignoresSafeArea()
    }
}

/// largertext-m1: the line under the aiming preview and the coach's lines at accessibility sizes (UIUX-iphone-largertext-READY,
/// proposal 1, from Apple's Human Interface Guidelines on Dynamic Type): one column, the line first, then each coach line
/// 8 pt under the one above, every line centred and wrapping onto as many lines as it needs, none cut, the coach's icon
/// growing with its words. The same words, looks and motion as at the default sizes. The icon sits on the first line's
/// letters (its foot a fifth of its height under their baseline) [inference: the record does not say; it reads as the
/// default row does].
private struct AimLinesColumn: View {
    let line: AimLine?
    let coach: [CoachLine]
    let hidden: Double
    let reduceMotion: Bool
    @ScaledMetric(relativeTo: .body) private var iconSize = 14.0

    var body: some View {
        VStack(spacing: 8) {
            switch line {
            case .why(let words)?:
                AimLineText(words: words)
                    .opacity(AimExit.opacity(.rest, hidden: hidden))
                    .transition(.words(reduceMotion: reduceMotion))
            case .source(let words)?:
                AimLineText(words: words)
                    .opacity(AimExit.opacity(.sourceLine, hidden: hidden))
                    .transition(.identity)
            case nil:
                EmptyView()
            }
            ForEach(coach, id: \.words) { coachLine in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    AlbedianIcon(rawValue: coachLine.icon)?.image(iconSize).accessibilityHidden(true)
                        .alignmentGuide(.firstTextBaseline) { $0[.bottom] - $0.height / 5 }
                    Text(coachLine.words).font(AlbedianFont.regular(12)).tracking(-0.005 * 12)
                }
                .foregroundStyle(Color(ControlStyle.inkSecondary))
                .opacity(AimExit.opacity(.rest, hidden: hidden))
                .transition(.words(reduceMotion: reduceMotion))
            }
        }
        .multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity).padding(.horizontal, 24)
    }
}

/// The one small line under the aiming preview: 12 pt, tabular figures, ink at 48 percent.
private struct AimLineText: View {
    let words: String

    var body: some View {
        Text(words).font(AlbedianFont.regular(12).monospacedDigit()).tracking(-0.005 * 12)
            .foregroundStyle(Color(ControlStyle.inkQuiet))
    }
}

/// The photo as shot, covering the 3:4 frame from y 118 (`center / cover`).
struct ShotPhoto: View {
    let shot: KeptShot?
    let width: Double

    var body: some View {
        Group {
            if let shot, let image = UIImage(data: shot.photo) {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Color(ControlStyle.field)
            }
        }
        .frame(width: CGFloat(width), height: CGFloat(width * 4 / 3)).clipped()
    }
}

/// After the shot (arrive iPhone 5, with UIUX-iphone-firstuse-READY item 2): take again, the photo as shot, and "Send to laptop". When the laptop cannot be reached
/// (UIUX-unreachable): under the photo "Can't reach the laptop" and "Your photo is kept on this phone.", then "Try
/// again" and "Change address". No byte count and no spinner; while the solve runs the screen stays as it is and the
/// result follows (arrive iPhone 5).
struct AfterShotScreen: View {
    let model: CompanionModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    /// Set once the screen is up, so Take again and Send word in 20 ms apart (MOTION-READY-iphone section 3)
    @State private var arrived = false
    /// largertext-unreachable-m: the room the lines and the stacked buttons need at accessibility sizes, measured once
    /// from them, hidden; nil until measured, and nothing of the column is drawn before
    @State private var columnRoom: Double?

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            let stacked = model.unreachable && dynamicTypeSize.isAccessibilitySize
            ZStack {
                if stacked {
                    // largertext-unreachable-m (UIUX-iphone-largertext-sweep-READY item 4, DECIDED-BY-LEAD 24 Nov 19:56):
                    // one column: the photo whole and smaller, as tall as what is left above the lines and the stacked
                    // buttons, never under 160 pt; past that the column scrolls; nothing wider than the screen
                    let frame = UnreachableFrame.size(screenWidth: width, screenHeight: Double(geometry.size.height),
                                                      columnRoom: columnRoom ?? 0)
                    unreachableColumn
                        .hidden()
                        .accessibilityHidden(true)
                        .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { columnRoom = $0 }
                    ScrollView {
                        VStack(spacing: 0) {
                            ShotPhoto(shot: model.shot, width: frame.width)
                            unreachableColumn.padding(.top, CGFloat(UnreachableFrame.gap))
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.top, CGFloat(UnreachableFrame.top - Arrive.topBarFoot))
                        .padding(.bottom, CGFloat(UnreachableFrame.foot))
                    }
                    .scrollBounceBehavior(.basedOnSize)
                    // largertext-scroll-edge-m (UIUX-scroll-under-controls-READY): the column scrolls on under the fixed top controls
                    // with the system's scroll edge effect, automatic, so the controls stay distinct
                    .scrollEdgeEffectStyle(.automatic, for: .top)
                    .safeAreaBar(edge: .top) { TopBar { IconButton(icon: .again, label: "Take again", action: model.takeAgain) } }
                    .opacity(columnRoom == nil ? 0 : 1)
                    .transition(.words(reduceMotion: reduceMotion))
                } else {
                    ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop)
                }
                if arrived && !stacked {
                    IconButton(icon: .again, label: "Take again", action: model.takeAgain)
                        .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                        .transition(.words(reduceMotion: reduceMotion))
                }
                if model.unreachable && !stacked {
                    VStack(spacing: 6) {
                        HStack(spacing: 8) {
                            AlbedianIcon.unreachable.image(17).accessibilityHidden(true)
                            Text("Can't reach the laptop").font(AlbedianFont.regular(17)).tracking(-0.01 * 17)
                        }
                        .foregroundStyle(Color(ControlStyle.ink))
                        Text("Your photo is kept on this phone.").font(AlbedianFont.regular(14)).tracking(-0.005 * 14)
                            .foregroundStyle(Color(ControlStyle.inkSecondary))
                    }
                    .atTop(Arrive.photoTop + width * 4 / 3 + 16)
                    // MOTION-READY-iphone: the message lines word in, the two buttons 20 ms after them
                    .transition(.words(reduceMotion: reduceMotion))
                    HStack(spacing: 10) {
                        MainButton(title: "Try again", icon: .send, action: model.send)
                        OutlinePill(title: "Change address", action: model.changeAddress)
                    }
                    .atBottom(Arrive.unreachableButtonsBottomGap)
                    .transition(.words(reduceMotion: reduceMotion, after: MotionStyle.staggerSeconds))
                } else if !model.unreachable && arrived {
                    // firstuse-send-one (UIUX-iphone-firstuse-READY item 2): the button says where the send goes
                    MainButton(title: SendWords.mainButton, icon: .send, action: model.send).atBottom(Arrive.mainBottomGap)
                        .transition(.words(reduceMotion: reduceMotion, after: MotionStyle.staggerSeconds))
                }
            }
        }
        .ignoresSafeArea()
        .onAppear { arrived = true }
    }

    /// The lines and the stacked buttons at accessibility sizes: both lines wrapping, centred, the icon at the first
    /// line (item 5's rule); Try again with its circle, then Change address 12 pt under it, each centred, 20 pt under
    /// the lines; within the screen's 24 pt margins.
    private var unreachableColumn: some View {
        VStack(spacing: 0) {
            VStack(spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    AlbedianIcon.unreachable.image(17).accessibilityHidden(true)
                    Text("Can't reach the laptop").font(AlbedianFont.regular(17)).tracking(-0.01 * 17)
                }
                .foregroundStyle(Color(ControlStyle.ink))
                Text("Your photo is kept on this phone.").font(AlbedianFont.regular(14)).tracking(-0.005 * 14)
                    .foregroundStyle(Color(ControlStyle.inkSecondary))
            }
            .multilineTextAlignment(.center)
            VStack(spacing: CGFloat(UnreachableFrame.betweenButtons)) {
                MainButton(title: "Try again", icon: .send, action: model.send)
                OutlinePill(title: "Change address", action: model.changeAddress)
            }
            .padding(.top, CGFloat(UnreachableFrame.linesToButtons))
        }
        .padding(.horizontal, CGFloat(ButtonGeometry.margin))
    }
}

/// Connect to your laptop (UIUX-connect-APPROVED, with UIUX-iphone-firstuse-READY item 1): back, the title, the command
/// that starts the laptop in its box, "Address", one field with "http://" fixed in front, the refused line's room under
/// it, and "Connect". No example address is shown.
struct ConnectScreen: View {
    @Bindable var model: CompanionModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            if dynamicTypeSize.isAccessibilitySize {
                // largertext-connect-m (UIUX-iphone-largertext-sweep-READY item 3, DECIDED-BY-LEAD 24 Nov 19:56): one column
                // that scrolls: the title wraps, "Address" over the field, the field wraps its whole address with
                // "http://" grey first, the hint or the refused line wraps under it, Connect 16 pt under that in the flow,
                // never over a word; the spaces between them as the record's picture has them
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("Connect to your laptop").title()
                            .fixedSize(horizontal: false, vertical: true)
                        startLine.padding(.top, 14)
                        commandBox.padding(.top, 12)
                        addressLabel.padding(.top, 16)
                        field(wraps: true).padding(.top, 6)
                        message.padding(.top, 12)
                        MainButton(title: "Connect", icon: .arrow, action: model.useTypedAddress)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 16)
                    }
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .padding(.top, CGFloat(UnreachableFrame.top - Arrive.topBarFoot))
                    .padding(.bottom, 34)
                }
                .scrollBounceBehavior(.basedOnSize)
                // largertext-scroll-edge-m (UIUX-scroll-under-controls-READY): the column scrolls on under the fixed top controls
                // with the system's scroll edge effect, automatic, so the controls stay distinct
                .scrollEdgeEffectStyle(.automatic, for: .top)
                .safeAreaBar(edge: .top) { TopBar { IconButton(icon: .back, label: "Back", action: model.back) } }
            } else {
                Lines(lines: ["Connect to your", "laptop"], font: AlbedianFont.light(Arrive.titleSize), size: Arrive.titleSize, lineHeight: 1.05, tracking: Arrive.titleTracking).title().atTop(Arrive.titleTop, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
                // firstuse-connect-one (UIUX-iphone-firstuse-READY item 1): Expo Go's empty state in the app's type and
                // colours, the command first, then where the address goes, at the record's numbers (`ConnectLayout`)
                startLine
                    .atTop(ConnectLayout.startTop, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
                commandBox
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(ConnectLayout.boxTop)
                addressLabel
                    .atTop(ConnectLayout.addressTop, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
                field(wraps: false)
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(ConnectLayout.fieldTop)
                message
                    .padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(ConnectLayout.messageTop)
                MainButton(title: "Connect", icon: .arrow, action: model.useTypedAddress).atTop(ConnectLayout.connectTop)
            }
            if !dynamicTypeSize.isAccessibilitySize {
                IconButton(icon: .back, label: "Back", action: model.back)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
            }
        }
        .ignoresSafeArea(.container)
        .onAppear { focused = true }
    }

    private var addressLabel: some View {
        Text("Address").font(AlbedianFont.regular(13)).foregroundStyle(Color(ControlStyle.inkQuiet))
    }

    /// The line over the command, in the style of the line it replaces.
    private var startLine: some View {
        Text(ConnectWords.start).font(AlbedianFont.regular(14)).tracking(-0.005 * 14).lineSpacing(14 * 0.2)
            .foregroundStyle(Color(ControlStyle.inkSecondary))
            .fixedSize(horizontal: false, vertical: true)
    }

    /// The command in Geist Mono 16 on the ink wash, selectable so the system's Copy takes it (with Universal Clipboard,
    /// to the Mac); INTERIM at accessibility sizes, held at the first one so the command stays on one line.
    private var commandBox: some View {
        Text(ConnectWords.command).font(AlbedianFont.mono(16)).tracking(CGFloat(ConnectLayout.commandTracking))
            .foregroundStyle(Color(ControlStyle.ink))
            .textSelection(.enabled)
            .dynamicTypeSize(...DynamicTypeSize.accessibility1)
            .padding(.horizontal, CGFloat(ConnectLayout.boxInset))
            .frame(maxWidth: .infinity, minHeight: CGFloat(ConnectLayout.boxHeight), alignment: .leading)
            .background(RoundedRectangle(cornerRadius: CGFloat(ConnectLayout.boxRadius)).fill(Color(ControlStyle.wash)))
    }

    /// The address field: at default sizes one line 60 pt high, the scheme in grey before the address, as before; at
    /// accessibility sizes the scheme whole on its own first line and the address wrapping under it at the field's width.
    @ViewBuilder private func field(wraps: Bool) -> some View {
        if wraps {
            VStack(alignment: .leading, spacing: 0) {
                if !PairingKeeper.typedHasScheme(model.typedAddress) {
                    Text(PairingKeeper.fixedScheme).foregroundStyle(Color(ControlStyle.inkQuiet)).fixedSize()
                }
                addressText(wraps: true)
            }
            .font(AlbedianFont.regular(20).monospacedDigit())
            .tracking(-0.01 * 20)
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .frame(minHeight: 60)
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Color(ControlStyle.line), lineWidth: 1))
        } else {
            HStack(spacing: 0) {
                // The address reads once: no fixed scheme in front of a typed one (LEAD ANSWER 12:57)
                if !PairingKeeper.typedHasScheme(model.typedAddress) {
                    Text(PairingKeeper.fixedScheme).foregroundStyle(Color(ControlStyle.inkQuiet))
                }
                addressText(wraps: false)
            }
            .font(AlbedianFont.regular(20).monospacedDigit())
            .tracking(-0.01 * 20)
            .padding(.horizontal, 18)
            .frame(height: 60)
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Color(ControlStyle.line), lineWidth: 1))
        }
    }

    /// The field's text: one line as before, or at accessibility sizes the whole address wrapping.
    @ViewBuilder private func addressText(wraps: Bool) -> some View {
        if wraps {
            TextField("", text: $model.typedAddress, axis: .vertical)
                .accessibilityLabel("Address")
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($focused)
                .foregroundStyle(Color(ControlStyle.ink))
                .tint(Color(ControlStyle.ink))
        } else {
            TextField("", text: $model.typedAddress)
                .accessibilityLabel("Address")
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($focused)
                .foregroundStyle(Color(ControlStyle.ink))
                .tint(Color(ControlStyle.ink))
        }
    }

    private var message: some View {
        Group {
            if model.addressRefused {
                // INTERIM (D11): the warning-circle icon and one line in ink at 15 pt, words for the owner to judge
                HStack(alignment: .top, spacing: 8) {
                    AlbedianIcon.warning.image(18).padding(.top, 1).accessibilityHidden(true)
                    Text("Type the address Albedian shows on the laptop.").font(AlbedianFont.regular(15)).tracking(-0.01 * 15)
                }
                .foregroundStyle(Color(ControlStyle.ink))
                // MOTION-READY-iphone: the message words in under the field
                .transition(.words(reduceMotion: reduceMotion))
            }
            // firstuse-connect-one: no line while nothing is refused; the command over the field says where the address is
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// The result (arrive iPhone 6): close, the server's status word, the photo as a card with the catalog matched marks,
/// the catalog matched count large with its tag, then the observed and predicted counts with theirs, as the server
/// reports them, and "Open", which shows the photo's reveal on the paired laptop in the system's Safari view (mobile-4b).
struct ResultScreen: View {
    let model: CompanionModel
    // largertext-m2: the heights the rows take as laid out, so that the rows grow downward from the row above and the
    // card gives the room still missing above "Open" (UIUX-iphone-largertext-READY, proposal 2)
    @State private var firstRowHeight = 0.0
    @State private var sentenceHeight = 0.0
    @State private var countsHeight = 0.0

    var body: some View {
        GeometryReader { geometry in
            let cardWidth = Double(geometry.size.width) - 2 * Arrive.side
            let fullCardHeight = cardWidth * 4 / 3
            ZStack {
                IconButton(icon: .close, label: "Close", action: model.close)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if let result = model.result, let sentence = OutcomeSentence.sentence(result.readout, result.facts) {
                    // A result that is not calibrated (UIUX-iphone-solve-READY, 2; mockup is-pre): no word at the top, no marks,
                    // the web's own sentence at 592, the counts it has at 676, and "Open" at 730. largertext-m2: the counts
                    // stand under the sentence when it grows, and the card gives the room still missing above "Open"
                    let give = max(0, Arrive.photoTop + fullCardHeight + max(102, 18 + sentenceHeight + 8) + countsHeight + 16 - (Double(geometry.size.height) - Arrive.unreachableButtonsBottomGap - ControlStyle.mainHeight))
                    let cardHeight = fullCardHeight - give
                    let countsTop = Arrive.photoTop + cardHeight + max(102, 18 + sentenceHeight + 8)
                    ShotPhoto(shot: model.shot, width: cardWidth)
                        .frame(width: cardWidth, height: cardHeight).clipShape(RoundedRectangle(cornerRadius: 18)).atTop(Arrive.photoTop)
                    Text(sentence).font(AlbedianFont.light(20)).tracking(-0.025 * 20).foregroundStyle(Color(ControlStyle.ink))
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, CGFloat(Arrive.side))
                        .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { sentenceHeight = $0 }
                        .atTop(Arrive.photoTop + cardHeight + 18)
                    counts(OutcomeSentence.countsShown(result.readout).map { (Self.tag($0.0), $0.1) })
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, CGFloat(Arrive.side))
                        .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { countsHeight = $0 }
                        .atTop(countsTop)
                    MainButton(title: "Open", icon: .arrow, action: model.openReveal).atBottom(Arrive.unreachableButtonsBottomGap)
                } else if let result = model.result {
                    // largertext-m2: the second row under the first when it grows, and the card giving the room still
                    // missing above "Open", about its centre, so every mark stays on its star
                    let give = max(0, Arrive.photoTop + fullCardHeight + max(78, 22 + firstRowHeight + 8) + countsHeight + 16 - (Double(geometry.size.height) - Arrive.mainBottomGap - ControlStyle.mainHeight))
                    let cardHeight = fullCardHeight - give
                    let secondTop = Arrive.photoTop + cardHeight + max(78, 22 + firstRowHeight + 8)
                    Text(result.readout.status.word).font(AlbedianFont.regular(15)).tracking(-0.01 * 15)
                        .foregroundStyle(Color(ControlStyle.ink))
                        .frame(height: 44).atTop(Arrive.topControls)
                    ShotPhoto(shot: model.shot, width: cardWidth)
                        .overlay { CatalogMarks(marks: result.readout.marks, frame: result.frame, width: cardWidth, height: fullCardHeight) }
                        .frame(width: cardWidth, height: cardHeight).clipShape(RoundedRectangle(cornerRadius: 18))
                        .atTop(Arrive.photoTop)
                    // largertext-m2: the matched count and its tag side by side while they fit whole within the margins,
                    // else the tag under the count, 8 pt apart, so the tag's word is never cut (the record's rule for the
                    // row under it, applied where the largest size cuts "Catalog matched")
                    let matched = (result.readout.counts[.catalogMatched] ?? 0).formatted()
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 14) { matchedCount(matched); EvidenceTag(evidence: .catalogMatched) }
                        VStack(alignment: .leading, spacing: 8) { matchedCount(matched); EvidenceTag(evidence: .catalogMatched) }
                    }
                    .accessibilityElement(children: .combine)
                    .foregroundStyle(Color(ControlStyle.ink))
                    .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { firstRowHeight = $0 }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, CGFloat(Arrive.side))
                    .atTop(Arrive.photoTop + cardHeight + 22)
                    // A class with none is left out, as the web's legend leaves it out
                    counts(ResultCounts.secondRow(result.readout.counts).map { ($0.0 == .observed ? .observed : .predicted, $0.1) })
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, CGFloat(Arrive.side))
                        .onGeometryChange(for: Double.self) { Double($0.size.height) } action: { countsHeight = $0 }
                        .atTop(secondTop)
                    MainButton(title: "Open", icon: .arrow, action: model.openReveal).atBottom(Arrive.mainBottomGap)
                }
            }
        }
        .ignoresSafeArea()
    }

    static func tag(_ evidence: EvidenceClass) -> ControlStyle.Evidence {
        switch evidence {
        case .observed: .observed
        case .catalogMatched: .catalogMatched
        case .predicted: .predicted
        case .simulated: .simulated
        }
    }

    /// largertext-m2 (UIUX-iphone-largertext-READY, proposal 2, from Apple's Human Interface Guidelines on Dynamic Type): the
    /// counts side by side, 16 pt apart, while they all fit whole on one line, else stacked, each with its tag on its own
    /// row, 8 pt apart; no evidence tag's word is ever cut.
    private func counts(_ items: [(ControlStyle.Evidence, Int)]) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 16) { ForEach(items, id: \.0) { evidence, n in count(n, evidence) } }
            VStack(alignment: .leading, spacing: 8) { ForEach(items, id: \.0) { evidence, n in count(n, evidence) } }
        }
    }

    /// The calibrated result's matched count: Geist Light 40, tabular figures.
    private func matchedCount(_ words: String) -> some View {
        Text(words).font(AlbedianFont.light(40).monospacedDigit()).tracking(-0.045 * 40)
    }

    private func count(_ value: Int, _ evidence: ControlStyle.Evidence) -> some View {
        HStack(spacing: 8) {
            Text(value.formatted()).font(AlbedianFont.regular(15).monospacedDigit()).tracking(-0.01 * 15).foregroundStyle(Color(ControlStyle.ink))
            EvidenceTag(evidence: evidence)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The catalog matched marks on the card (the result's mockup): a 3.6 pt ring with four ticks from 5 to 7.5 pt out, 1 pt
/// of the class's colour, at each matched detection where the covering photo shows it; none for a detection the card
/// cuts off.
struct CatalogMarks: View {
    let marks: [SolutionReadout.Mark]
    let frame: StoredFrame
    let width: Double
    let height: Double

    var body: some View {
        Canvas { context, _ in
            let colour = Color(ControlStyle.Evidence.catalogMatched.colour)
            for mark in marks where frame.isOnCard(xPx: mark.xPx, yPx: mark.yPx, width: width, height: height) {
                let p = frame.placeCovering(xPx: mark.xPx, yPx: mark.yPx, onWidth: width, height: height)
                let c = CGPoint(x: p.x, y: p.y)
                context.stroke(Path(ellipseIn: CGRect(x: c.x - 3.6, y: c.y - 3.6, width: 7.2, height: 7.2)), with: .color(colour), lineWidth: 1)
                var ticks = Path()
                for (dx, dy) in [(0.0, -1.0), (0, 1), (-1, 0), (1, 0)] {
                    ticks.move(to: CGPoint(x: c.x + dx * 5, y: c.y + dy * 5))
                    ticks.addLine(to: CGPoint(x: c.x + dx * 7.5, y: c.y + dy * 7.5))
                }
                context.stroke(ticks, with: .color(colour), lineWidth: 1)
            }
        }
        .frame(width: CGFloat(width), height: CGFloat(height))
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Lines at a set line height (CSS `line-height`), each line's box that tall with its text centred in it, since
/// SwiftUI's line spacing can only add to the font's own height.
struct Lines: View {
    let lines: [String]
    let font: Font
    let size: Double
    let lineHeight: Double
    var tracking: Double = -0.01

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(lines.enumerated()), id: \.offset) { pair in
                Text(pair.element).font(font).tracking(CGFloat(tracking * size))
                    .frame(height: CGFloat(size * lineHeight))
            }
        }
    }
}
