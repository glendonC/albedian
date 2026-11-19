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
}

extension View {
    /// The view's top at `y` points from the screen's top, centred across.
    func atTop(_ y: Double, alignment: HorizontalAlignment = .center) -> some View {
        frame(maxWidth: .infinity, maxHeight: .infinity, alignment: Alignment(horizontal: alignment, vertical: .top)).padding(.top, CGFloat(y))
    }

    /// The view's bottom `gap` points above the screen's bottom, centred across.
    func atBottom(_ gap: Double) -> some View {
        frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom).padding(.bottom, CGFloat(gap))
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
        ZStack {
            Color(ControlStyle.field).ignoresSafeArea()
            switch model.screen {
            case .home: HomeScreen(model: model)
            case .credits: CreditsScreen(model: model)
            case .aim: AimScreen(model: model)
            case .holdStill: HoldStillScreen(model: model)
            case .afterShot: AfterShotScreen(model: model)
            case .pairing: ConnectScreen(model: model)
            case .waiting: WaitingScreen(model: model)
            case .stopped: StoppedScreen(model: model)
            case .result: ResultScreen(model: model)
            }
        }
        .preferredColorScheme(.dark)
        .sheet(item: $model.openPage) { page in SafariView(url: page.url).ignoresSafeArea() }
        #if DEBUG
        .task { model.runFromLaunchArguments() }
        #endif
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
                // linear-gradient(to top, rgba(0,0,0,.9) 0%, rgba(0,0,0,.35) 34%, rgba(0,0,0,0) 55%)
                LinearGradient(stops: [.init(color: .black.opacity(0.9), location: 0), .init(color: .black.opacity(0.35), location: 0.34),
                                       .init(color: .black.opacity(0), location: 0.55)], startPoint: .bottom, endPoint: .top)
                OutlinePill(title: "Credits", height: 36, fontSize: 15, action: model.showCredits)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                    .padding(.top, CGFloat(Arrive.topControls)).padding(.trailing, CGFloat(Arrive.edge))
                // The logo row's top at 574 on the 844 point screen
                Logo(size: 52).atBottom(0).padding(.bottom, CGFloat(Arrive.referenceHeight - 574) - 62)
                MainButton(title: "Start", icon: .arrow, action: model.startAiming).atBottom(Arrive.mainBottomGap)
            }
        }
        .ignoresSafeArea()
    }
}

/// Credits (UIUX-credit-APPROVED): back, the title, and rows divided by hairlines, each a small grey label over its
/// lines. The two rows that link out carry the open-link icon (arrow-square-out, 16 pt, secondary ink), which opens
/// the source's page in the system's Safari view (mobile-4b).
struct CreditsScreen: View {
    let model: CompanionModel
    private let rows: [(label: String, lines: [String], page: OutsidePage?)] = [
        ("Milky Way photograph", ["ESO/S. Brunier", "The Milky Way panorama, eso0932a", "CC BY 4.0"], .milkyWayPhotograph),
        ("Star catalogs", ["ESA Hipparcos, Hipparcos 2007", "ESA/Gaia/DPAC, Gaia DR3"], .starCatalogs),
        ("Type", ["Geist, SIL Open Font License"], nil),
        ("Icons", ["Phosphor Icons, MIT License"], nil),
    ]

    var body: some View {
        ZStack {
            IconButton(icon: .back, label: "Back", action: model.showHome)
                .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
            Lines(lines: ["Credits"], font: AlbedianFont.light(Arrive.titleSize), size: Arrive.titleSize, lineHeight: 1.05, tracking: Arrive.titleTracking).title().atTop(Arrive.titleTop, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
            VStack(spacing: 0) {
                ForEach(rows, id: \.label) { row in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(row.label).font(AlbedianFont.regular(13)).tracking(-0.005 * 13).foregroundStyle(Color(ControlStyle.inkQuiet))
                        Lines(lines: row.lines, font: AlbedianFont.regular(16), size: 16, lineHeight: 1.4).foregroundStyle(Color(ControlStyle.ink))
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
            .atTop(200)
        }
        .ignoresSafeArea()
    }
}

/// Aiming (arrive iPhone 2, without its overlay until mobile-5): close top left, the 3:4 preview from y 118 and the
/// capture button bottom centre. With no overlay drawn there is no "Predicted" tag and no source line.
struct AimScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                CameraPreview(session: model.camera.session)
                    .frame(width: geometry.size.width, height: geometry.size.width * 4 / 3).clipped()
                    .atTop(Arrive.photoTop)
                IconButton(icon: .close, label: "Close", action: model.showHome)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                // The 64 pt capture button's top at 706: its bottom 74 above the 844 point screen's
                CaptureButton(action: model.shoot).atBottom(Arrive.referenceHeight - 706 - 64)
            }
        }
        .ignoresSafeArea()
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

/// After the shot (arrive iPhone 5): take again, the photo as shot, and "Send". When the laptop cannot be reached
/// (UIUX-unreachable): under the photo "Can't reach the laptop" and "Your photo is kept on this phone.", then "Try
/// again" and "Change address". No byte count and no spinner; while the solve runs the screen stays as it is and the
/// result follows (arrive iPhone 5).
struct AfterShotScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            let width = Double(geometry.size.width)
            ZStack {
                ShotPhoto(shot: model.shot, width: width).atTop(Arrive.photoTop)
                IconButton(icon: .again, label: "Take again", action: model.takeAgain)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if model.unreachable {
                    VStack(spacing: 6) {
                        HStack(spacing: 8) {
                            AlbedianIcon.unreachable.image(17)
                            Text("Can't reach the laptop").font(AlbedianFont.regular(17)).tracking(-0.01 * 17)
                        }
                        .foregroundStyle(Color(ControlStyle.ink))
                        Text("Your photo is kept on this phone.").font(AlbedianFont.regular(14)).tracking(-0.005 * 14)
                            .foregroundStyle(Color(ControlStyle.inkSecondary))
                    }
                    .atTop(Arrive.photoTop + width * 4 / 3 + 16)
                    HStack(spacing: 10) {
                        MainButton(title: "Try again", icon: .send, action: model.send)
                        OutlinePill(title: "Change address", action: model.changeAddress)
                    }
                    .atBottom(Arrive.unreachableButtonsBottomGap)
                } else {
                    MainButton(title: "Send", icon: .send, action: model.send).atBottom(Arrive.mainBottomGap)
                }
            }
        }
        .ignoresSafeArea()
    }
}

/// Connect to your laptop (UIUX-connect-APPROVED): back, the title, "Address", one field with "http://" fixed in
/// front, the line under it, and "Connect". No example address is shown.
struct ConnectScreen: View {
    @Bindable var model: CompanionModel
    @FocusState private var focused: Bool

    var body: some View {
        ZStack {
            IconButton(icon: .back, label: "Back", action: model.back)
                .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
            Lines(lines: ["Connect to your", "laptop"], font: AlbedianFont.light(Arrive.titleSize), size: Arrive.titleSize, lineHeight: 1.05, tracking: Arrive.titleTracking).title().atTop(Arrive.titleTop, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
            Text("Address").font(AlbedianFont.regular(13)).foregroundStyle(Color(ControlStyle.inkQuiet))
                .atTop(238, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
            HStack(spacing: 0) {
                Text(PairingKeeper.fixedScheme).foregroundStyle(Color(ControlStyle.inkQuiet))
                TextField("", text: $model.typedAddress)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focused)
                    .foregroundStyle(Color(ControlStyle.ink))
                    .tint(Color(ControlStyle.ink))
            }
            .font(AlbedianFont.regular(20).monospacedDigit())
            .tracking(-0.01 * 20)
            .padding(.horizontal, 18)
            .frame(height: 60)
            .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Color(ControlStyle.line), lineWidth: 1))
            .padding(.horizontal, CGFloat(Arrive.side))
            .atTop(262)
            Group {
                if model.addressRefused {
                    // INTERIM (D11): the warning-circle icon and one line in ink at 15 pt, words for the owner to judge
                    HStack(alignment: .top, spacing: 8) {
                        AlbedianIcon.warning.image(18).padding(.top, 1)
                        Text("Type the address Albedian shows on the laptop.").font(AlbedianFont.regular(15)).tracking(-0.01 * 15)
                    }
                    .foregroundStyle(Color(ControlStyle.ink))
                } else {
                    Text("Albedian shows this address on the laptop when it starts.")
                        .font(AlbedianFont.regular(14)).tracking(-0.005 * 14).lineSpacing(14 * 0.2)
                        .foregroundStyle(Color(ControlStyle.inkSecondary))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, CGFloat(Arrive.side))
            .atTop(336)
            MainButton(title: "Connect", icon: .arrow, action: model.useTypedAddress).atTop(404)
        }
        .ignoresSafeArea(.container)
        .onAppear { focused = true }
    }
}

/// The result (arrive iPhone 6): close, the server's status word, the photo as a card with the catalog matched marks,
/// the catalog matched count large with its tag, then the observed and predicted counts with theirs, as the server
/// reports them, and "Open", which shows the photo's reveal on the paired laptop in the system's Safari view (mobile-4b).
struct ResultScreen: View {
    let model: CompanionModel

    var body: some View {
        GeometryReader { geometry in
            let cardWidth = Double(geometry.size.width) - 2 * Arrive.side
            let cardHeight = cardWidth * 4 / 3
            ZStack {
                IconButton(icon: .close, label: "Close", action: model.close)
                    .atTop(Arrive.topControls, alignment: .leading).padding(.leading, CGFloat(Arrive.edge))
                if let result = model.result, let sentence = OutcomeSentence.sentence(result.readout, result.facts) {
                    // A result that is not calibrated (UIUX-iphone-solve-READY, 2; mockup is-pre): no word at the top, no marks,
                    // the web's own sentence at 592, the counts it has at 676, and "Open" at 730
                    ShotPhoto(shot: model.shot, width: cardWidth).clipShape(RoundedRectangle(cornerRadius: 18)).atTop(Arrive.photoTop)
                    Text(sentence).font(AlbedianFont.light(20)).tracking(-0.025 * 20).foregroundStyle(Color(ControlStyle.ink))
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, CGFloat(Arrive.side))
                        .atTop(Arrive.photoTop + cardHeight + 18)
                    HStack(spacing: 16) {
                        ForEach(OutcomeSentence.countsShown(result.readout), id: \.0) { pair in count(pair.1, Self.tag(pair.0)) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
                    .atTop(Arrive.photoTop + cardHeight + 102)
                    MainButton(title: "Open", icon: .arrow, action: model.openReveal).atBottom(Arrive.unreachableButtonsBottomGap)
                } else if let result = model.result {
                    Text(result.readout.status.word).font(AlbedianFont.regular(15)).tracking(-0.01 * 15)
                        .foregroundStyle(Color(ControlStyle.ink))
                        .frame(height: 44).atTop(Arrive.topControls)
                    ShotPhoto(shot: model.shot, width: cardWidth)
                        .overlay { CatalogMarks(marks: result.readout.marks, frame: result.frame, width: cardWidth, height: cardHeight) }
                        .clipShape(RoundedRectangle(cornerRadius: 18))
                        .atTop(Arrive.photoTop)
                    HStack(spacing: 14) {
                        Text((result.readout.counts[.catalogMatched] ?? 0).formatted())
                            .font(AlbedianFont.light(40).monospacedDigit()).tracking(-0.045 * 40)
                        EvidenceTag(evidence: .catalogMatched)
                    }
                    .foregroundStyle(Color(ControlStyle.ink))
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
                    .atTop(Arrive.photoTop + cardHeight + 22)
                    HStack(spacing: 16) {
                        count(result.readout.counts[.observed] ?? 0, .observed)
                        count(result.readout.counts[.predicted] ?? 0, .predicted)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.leading, CGFloat(Arrive.side))
                    .atTop(Arrive.photoTop + cardHeight + 78)
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

    private func count(_ value: Int, _ evidence: ControlStyle.Evidence) -> some View {
        HStack(spacing: 8) {
            Text(value.formatted()).font(AlbedianFont.regular(15).monospacedDigit()).tracking(-0.01 * 15).foregroundStyle(Color(ControlStyle.ink))
            EvidenceTag(evidence: evidence)
        }
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
