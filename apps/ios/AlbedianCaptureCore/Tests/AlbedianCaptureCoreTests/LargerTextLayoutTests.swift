import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The iPhone screens at Larger Text's accessibility sizes, as UIUX-iphone-largertext-READY records them (DECIDED-BY-LEAD
/// on 24 November at 12:08, from Apple's Human Interface Guidelines on Dynamic Type): read from the screens' source, as
/// `ApprovedAssetsTests` reads them, since the app's views do not run here; the simulator captures show the rest.
final class LargerTextLayoutTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")

    /// The code of a screens file, comments and the insides of literals left out, and its literals apart.
    private func code(_ file: String) throws -> (code: String, literals: String) {
        try SourceReader.split(String(contentsOf: app.appendingPathComponent(file), encoding: .utf8), .swift)
    }

    /// The text of a top-level type from its declaration to its closing brace at the start of a line.
    private func body(of declaration: String, in code: String) throws -> String {
        let start = try XCTUnwrap(code.range(of: declaration), "no \(declaration)")
        let end = try XCTUnwrap(code.range(of: "\n}\n", range: start.upperBound..<code.endIndex), "no end of \(declaration)")
        return String(code[start.lowerBound..<end.upperBound])
    }

    private func compact(_ text: String) -> String { text.filter { !$0.isWhitespace } }

    /// largertext-m1, Aim: at accessibility sizes the line under the preview and the coach lines stand in one centred
    /// column from the preview's foot plus 16, each line 8 pt under the one above, wrapping as far as it needs and none
    /// cut, the coach's icon growing with its words, the column's text held at the first accessibility size; at the
    /// default sizes the lines stand as D8 and D29 record them; the Predicted tag is outside the column and keeps growing.
    func testAimsLinesFlowAsOneCentredColumnAtAccessibilitySizes() throws {
        let screens = try code("Screens/CompanionScreens.swift").code
        let aim = try body(of: "struct AimScreen: View", in: screens)
        let flat = compact(aim)
        XCTAssertTrue(flat.contains("@Environment(\\.dynamicTypeSize)privatevardynamicTypeSize"), "Aim reads the text size")
        XCTAssertTrue(flat.contains("ifdynamicTypeSize.isAccessibilitySize{AimLinesColumn("), "the column only at accessibility sizes")
        XCTAssertTrue(flat.contains(".dynamicTypeSize(...DynamicTypeSize.accessibility1).atTop(Arrive.photoTop+width*4/3+16)"),
                      "the column held at the first accessibility size, its top at the preview's foot plus 16")
        // The default sizes, as D8 and D29 record them: each line one line, the coach 16 pt under the source line
        let otherwise = try XCTUnwrap(flat.range(of: "}else{", range: try XCTUnwrap(flat.range(of: "AimLinesColumn(line:aim.shownLine")).upperBound..<flat.endIndex))
        let defaults = String(flat[otherwise.upperBound...])
        XCTAssertTrue(defaults.contains("AimLineText(words:words).atTop(Arrive.photoTop+width*4/3+16)"))
        XCTAssertTrue(defaults.contains(".lineLimit(1)}.frame(height:12*1.35)"))
        XCTAssertTrue(defaults.contains(".atTop(Arrive.photoTop+width*4/3+32)"))
        // The Predicted tag stands outside the held column
        let tag = try XCTUnwrap(flat.range(of: "EvidenceTag(evidence:.predicted)"))
        let column = try XCTUnwrap(flat.range(of: "AimLinesColumn(line:aim.shownLine"))
        XCTAssertLessThan(tag.lowerBound, column.lowerBound, "the tag comes before the column, not inside it")

        let lines = compact(try body(of: "private struct AimLinesColumn: View", in: screens))
        XCTAssertTrue(lines.contains("@ScaledMetric(relativeTo:.body)privatevariconSize=14.0"), "the icon grows with its words")
        XCTAssertTrue(lines.contains("VStack(spacing:8){"), "each line 8 pt under the one above")
        XCTAssertTrue(lines.contains("AlbedianIcon(rawValue:coachLine.icon)?.image(iconSize)"))
        XCTAssertTrue(lines.contains(".multilineTextAlignment(.center)"))
        XCTAssertTrue(lines.contains(".fixedSize(horizontal:false,vertical:true)"), "wrapping onto as many lines as it needs")
        XCTAssertTrue(lines.contains(".frame(maxWidth:.infinity).padding(.horizontal,24)"))
        for cut in [".lineLimit(", ".truncationMode(", ".frame(height:", ".minimumScaleFactor("] {
            XCTAssertFalse(lines.contains(cut), "no line cut, shrunk or held to a height in the column: \(cut)")
        }
        // The same words, looks and motion as the default sizes: the why line words in, the source line stands at once
        XCTAssertTrue(lines.contains("case.why(letwords)?:AimLineText(words:words).opacity(AimExit.opacity(.rest,hidden:hidden)).transition(.words(reduceMotion:reduceMotion))"))
        XCTAssertTrue(lines.contains("case.source(letwords)?:AimLineText(words:words).opacity(AimExit.opacity(.sourceLine,hidden:hidden)).transition(.identity)"))
        XCTAssertTrue(lines.contains(".foregroundStyle(Color(ControlStyle.inkSecondary)).opacity(AimExit.opacity(.rest,hidden:hidden)).transition(.words(reduceMotion:reduceMotion))"))
    }

    /// largertext-m1, Aim re-ruled 24 Nov 15:52: the preview's 3:4 frame. At default sizes the screen's width; at
    /// accessibility sizes the tallest 3:4 frame from 118 pt whose foot leaves the column's room and 16 pt either side of
    /// it above the capture button: 322 by 429 pt on the iPhone 17 Pro (402 by 874, the button's top at 736, the column's
    /// most 157 pt as measured at the first accessibility size), never wider than the screen, never less than nothing.
    func testAimsFrameIsTheRoomLeftAboveTheColumnAtAccessibilitySizes() {
        let normal = AimFrame.size(screenWidth: 402, captureButtonTop: 736, columnRoom: nil)
        XCTAssertEqual(normal.width, 402)
        XCTAssertEqual(normal.height, 536)
        let held = AimFrame.size(screenWidth: 402, captureButtonTop: 736, columnRoom: 157)
        XCTAssertEqual(held.height, 429, accuracy: 1e-9)
        XCTAssertEqual(held.width, 321.75, accuracy: 1e-9)
        XCTAssertEqual(held.width / held.height, 3.0 / 4.0, accuracy: 1e-12)
        var g = SeededGenerator(seed: propertySeed())
        for _ in 0..<2000 {
            let width = g.double(in: 300...460)
            let buttonTop = g.double(in: 560...960)
            let room = g.pick([0, g.double(in: 0...600), 5000])
            let frame = AimFrame.size(screenWidth: width, captureButtonTop: buttonTop, columnRoom: room)
            let context = "\(width) \(buttonTop) \(room)"
            XCTAssertEqual(frame.width * 4 / 3, frame.height, accuracy: 1e-9, context)
            XCTAssertLessThanOrEqual(frame.width, width + 1e-9, context)
            XCTAssertGreaterThanOrEqual(frame.height, 0, context)
            let left = buttonTop - AimFrame.gap - room - AimFrame.gap - AimFrame.top
            if left <= 0 { XCTAssertEqual(frame.height, 0, context) }
            else if left >= width * 4 / 3 { XCTAssertEqual(frame.width, width, accuracy: 1e-9, context) }
            else {
                XCTAssertEqual(frame.height, left, accuracy: 1e-9, context)
                XCTAssertEqual(AimFrame.top + frame.height + AimFrame.gap + room + AimFrame.gap, buttonTop, accuracy: 1e-9, context)
            }
        }
    }

    /// Every ring keeps its place on its sky in the smaller frame: the projection onto a 3:4 frame scales with the
    /// frame's width, so a ring lands at the same fraction of the frame as on the full one (the preview's picture is
    /// scaled the same way, aspect-fill into the same shape).
    func testRingsKeepTheirPlacesInTheSmallerFrame() {
        var g = SeededGenerator(seed: propertySeed())
        var placed = 0
        for _ in 0..<3000 {
            let raw = (0..<4).map { _ in g.double(in: -1...1) }
            let n = raw.map { $0 * $0 }.reduce(0, +).squareRoot()
            guard n > 0.1 else { continue }
            let q = PointingRecord.Quaternion(x: raw[0] / n, y: raw[1] / n, z: raw[2] / n, w: raw[3] / n)
            let alt = g.double(in: -90...90), az = g.double(in: 0...360), fov = g.double(in: 20...80)
            let small = g.pick([321.75, g.double(in: 100...402)])
            let full = SkyProjection.point(altDeg: alt, azDeg: az, attitude: q, widthPt: 402, heightPt: 536, acrossFovDeg: fov)
            let less = SkyProjection.point(altDeg: alt, azDeg: az, attitude: q, widthPt: small, heightPt: small * 4 / 3, acrossFovDeg: fov)
            XCTAssertEqual(full == nil, less == nil)
            guard let full, let less else { continue }
            placed += 1
            XCTAssertEqual(less.x / small, full.x / 402, accuracy: 1e-9)
            XCTAssertEqual(less.y / (small * 4 / 3), full.y / 536, accuracy: 1e-9)
        }
        XCTAssertGreaterThan(placed, 50)
    }

    /// The room is kept for the app's own longest words: the source line the sky context's sources make at the heading
    /// limit, and the coach's longest pair (the compass and the Sun at 18 degrees), the longest of every pair it can show.
    func testTheMostTheColumnCanShowIsTheAppsOwnLongestWords() throws {
        let marks = [SkyMark(id: "a", name: "Acrux", altDeg: 40, azDeg: 150, source: .catalog(release: "Hipparcos 2007")),
                     SkyMark(id: "b", name: "Hadar", altDeg: 35, azDeg: 160, source: .catalog(release: "Gaia DR3")),
                     SkyMark(id: "c", name: "Jupiter", altDeg: 30, azDeg: 170, source: .ephemeris)]
        XCTAssertEqual(AimFrame.mostLine, .source(try XCTUnwrap(SkyOverlayWords.sourceLine(marks, headingAccuracyDeg: PointingContract.headingAccuracyLimitDeg))))
        func conditions(moonAlt: Double, veil: Double, lit: Double, sun: SkySun?) -> SkyConditions {
            SkyConditions(skyBrightnessMagArcsec2: 21, darkSkyMagArcsec2: 22, lunarVeilDeltaMag: veil, moonIlluminationFraction: lit,
                          moonPhaseName: nil, moonAltDeg: moonAlt, sun: sun)
        }
        let deepest = conditions(moonAlt: -5, veil: 0, lit: 0, sun: SkySun(altDeg: -17.6, band: .astronomicalTwilight))
        XCTAssertEqual(AimFrame.mostCoach, Coach.lines(compassAsked: true, conditions: deepest, moving: false))
        // Every pair the coach can make, by its words' length: the Sun at every whole degree of twilight, the Moon at
        // every percent, up with no light, down; with the compass or steadiness
        var skies: [SkyConditions?] = [nil, conditions(moonAlt: -5, veil: 0, lit: 0.5, sun: nil), conditions(moonAlt: 20, veil: 0, lit: 0.5, sun: nil)]
        skies += (0...100).map { conditions(moonAlt: 20, veil: 0.3, lit: Double($0) / 100, sun: nil) }
        skies += stride(from: -17.9, through: -1, by: 0.5).map { alt in
            conditions(moonAlt: 20, veil: 0.3, lit: 1, sun: SkySun(altDeg: alt, band: alt < -12 ? .astronomicalTwilight : alt < -6 ? .nauticalTwilight : .civilTwilight))
        }
        let most = AimFrame.mostCoach.map(\.words.count).reduce(0, +)
        for sky in skies { for compass in [false, true] { for moving in [false, true] {
            let lines = Coach.lines(compassAsked: compass, conditions: sky, moving: moving)
            XCTAssertLessThanOrEqual(lines.map(\.words.count).reduce(0, +), most, "\(lines)")
        } } }
    }

    /// The Aim screen sizes its preview, rings and column from that frame, with the room measured once from the most the
    /// column can show, held at the first accessibility size as the column is, hidden, never from the lines shown.
    func testAimSizesItsFrameOnceFromTheMostTheColumnCanShow() throws {
        let aim = compact(try body(of: "struct AimScreen: View", in: try code("Screens/CompanionScreens.swift").code))
        XCTAssertTrue(aim.contains("@StateprivatevarcolumnRoom=0.0"))
        XCTAssertTrue(aim.contains("letframe=AimFrame.size(screenWidth:Double(geometry.size.width),captureButtonTop:Double(geometry.size.height)-(Arrive.referenceHeight-706-64)-64,columnRoom:dynamicTypeSize.isAccessibilitySize?columnRoom:nil)"))
        XCTAssertTrue(aim.contains("letwidth=frame.width"))
        XCTAssertTrue(aim.contains("CameraPreview(session:model.camera.session).frame(width:width,height:width*4/3).clipped().atTop(Arrive.photoTop)"),
                      "the preview whole in the frame, centred across")
        XCTAssertTrue(aim.contains("ifdynamicTypeSize.isAccessibilitySize{AimLinesColumn(line:AimFrame.mostLine,coach:AimFrame.mostCoach,hidden:0,reduceMotion:true).dynamicTypeSize(...DynamicTypeSize.accessibility1).hidden().accessibilityHidden(true).onGeometryChange(for:Double.self){Double($0.size.height)}action:{columnRoom=$0}}"),
                      "the room measured from the most the column can show, held as the column is, never shown")
        XCTAssertFalse(aim.contains("geometry.size.width*4/3"), "nothing drawn at the full width's height any more")
        // AUDIT-C's T3 on largertext-m1: the rings are placed and drawn for the frame they are given, the preview's own
        XCTAssertTrue(aim.contains("letplaced=SkyOverlayView.placed(aim.marks(model.skyMarks),attitude:model.currentAttitude,acrossFovDeg:model.previewFovDeg,width:width,height:width*4/3)"),
                      "the rings placed for the frame's width")
        XCTAssertTrue(aim.contains("PlacedSkyOverlay(placed:placed,width:width,height:width*4/3).atTop(Arrive.photoTop)"),
                      "the rings drawn in the frame's width")
        XCTAssertFalse(aim.contains("width:Double(geometry.size.width)"), "nothing placed or drawn at the screen's width")
        // AUDIT-C's T4: the Predicted tag stands with the rings' first one, a single ring included (the tag rule)
        XCTAssertTrue(aim.contains("if!placed.isEmpty{EvidenceTag(evidence:.predicted).frame(height:44).atTop(Arrive.topControls)"),
                      "the tag with any ring drawn")
    }

    /// largertext-m2, Result: the Observed and Predicted counts stay side by side, 16 pt apart, while both fit whole on
    /// one line within the side margins, and stack, each count with its tag on its own row 8 pt apart, when they do not;
    /// the same for the uncalibrated result's counts. The rows grow downward: the second row never above where it stands
    /// at the default sizes, and under the row above it when that row grows. When they would come within 16 pt of
    /// "Open", the photo card gives the room (its 3:4 crop centred), never the words; no evidence tag word is cut.
    func testResultCountsStackWhenTheyCannotFitAndTheCardGivesTheRoom() throws {
        let screens = try code("Screens/CompanionScreens.swift").code
        let result = compact(try body(of: "struct ResultScreen: View", in: screens))
        XCTAssertTrue(result.contains("privatefunccounts(_items:[(ControlStyle.Evidence,Int)])->someView{ViewThatFits(in:.horizontal){HStack(spacing:16){ForEach(items,id:\\.0){evidence,nincount(n,evidence)}}VStack(alignment:.leading,spacing:8){ForEach(items,id:\\.0){evidence,nincount(n,evidence)}}}}"),
                      "side by side first, stacked 8 pt apart second")
        XCTAssertTrue(result.contains("counts(ResultCounts.secondRow(result.readout.counts).map{($0.0==.observed?.observed:.predicted,$0.1)})"))
        XCTAssertTrue(result.contains("counts(OutcomeSentence.countsShown(result.readout).map{(Self.tag($0.0),$0.1)})"))
        XCTAssertEqual(result.components(separatedBy: ".frame(maxWidth:.infinity,alignment:.leading).padding(.horizontal,CGFloat(Arrive.side)).onGeometryChange(for:Double.self){Double($0.size.height)}action:{countsHeight=$0}").count - 1, 2,
                       "both count blocks fit within the side margins and report their height")
        XCTAssertTrue(result.contains("ViewThatFits(in:.horizontal){HStack(spacing:14){matchedCount(matched);EvidenceTag(evidence:.catalogMatched)}VStack(alignment:.leading,spacing:8){matchedCount(matched);EvidenceTag(evidence:.catalogMatched)}}"),
                      "the matched count's tag under it when both cannot fit, never cut")
        XCTAssertTrue(result.contains("{firstRowHeight=$0}.frame(maxWidth:.infinity,alignment:.leading).padding(.horizontal,CGFloat(Arrive.side))"))
        // The rows grow downward from the row above, never above their default places
        XCTAssertTrue(result.contains("letsecondTop=Arrive.photoTop+cardHeight+max(78,22+firstRowHeight+8)"))
        XCTAssertTrue(result.contains("letcountsTop=Arrive.photoTop+cardHeight+max(102,18+sentenceHeight+8)"))
        // The card gives the room still missing above "Open", and only that
        XCTAssertTrue(result.contains("letgive=max(0,Arrive.photoTop+fullCardHeight+max(78,22+firstRowHeight+8)+countsHeight+16-(Double(geometry.size.height)-Arrive.mainBottomGap-ControlStyle.mainHeight))"))
        XCTAssertTrue(result.contains("letgive=max(0,Arrive.photoTop+fullCardHeight+max(102,18+sentenceHeight+8)+countsHeight+16-(Double(geometry.size.height)-Arrive.unreachableButtonsBottomGap-ControlStyle.mainHeight))"))
        XCTAssertEqual(result.components(separatedBy: "letcardHeight=fullCardHeight-give").count - 1, 2, "each card is what is left once it has given")
        // The catalog matched marks are placed on the whole 3:4 card before it is cut down about its centre, so each stays on
        // its star when the card gives room (AUDIT-C's S1 on largertext-m2: a mark placed in the cut box would leave it)
        XCTAssertTrue(result.contains(".overlay{CatalogMarks(marks:result.readout.marks,frame:result.frame,width:cardWidth,height:fullCardHeight)}.frame(width:cardWidth,height:cardHeight)"),
                      "marks on the full card, then the card cut down")
        XCTAssertEqual(result.components(separatedBy: ".frame(width:cardWidth,height:cardHeight).clipShape(RoundedRectangle(cornerRadius:18))").count - 1, 2,
                       "the card shrinks about its centre, both results")
        let count = compact(try body(of: "    private func count(_ value: Int, _ evidence: ControlStyle.Evidence) -> some View", in: screens + "\n}\n"))
        XCTAssertFalse(count.contains(".lineLimit("), "a tag's word is never held to a line")
    }

    /// largertext-wait-m (UIUX-iphone-largertext-sweep-READY item 1, DECIDED-BY-LEAD 24 Nov 19:56): at accessibility sizes
    /// the wait's photo is drawn whole in a smaller 3:4 frame from 118, centred across, as tall as what is left above the
    /// step row and its line (30 pt under the photo, 34 pt kept at the foot), so the line stands whole at every size;
    /// at default sizes the screen's width, as before; never wider than the screen, never less than nothing.
    func testTheWaitsFrameIsTheRoomLeftAboveItsRowAndLineAtAccessibilitySizes() {
        let normal = WaitFrame.size(screenWidth: 402, screenHeight: 874, columnRoom: nil)
        XCTAssertEqual(normal.width, 402)
        XCTAssertEqual(normal.height, 536)
        let held = WaitFrame.size(screenWidth: 402, screenHeight: 874, columnRoom: 355)
        XCTAssertEqual(held.height, 337, accuracy: 1e-9)
        XCTAssertEqual(held.width, 252.75, accuracy: 1e-9)
        var g = SeededGenerator(seed: propertySeed())
        for _ in 0..<2000 {
            let width = g.double(in: 300...460)
            let height = g.double(in: 560...1000)
            let room = g.pick([0, g.double(in: 0...700), 5000])
            let frame = WaitFrame.size(screenWidth: width, screenHeight: height, columnRoom: room)
            let context = "\(width) \(height) \(room)"
            XCTAssertEqual(frame.width * 4 / 3, frame.height, accuracy: 1e-9, context)
            XCTAssertLessThanOrEqual(frame.width, width + 1e-9, context)
            XCTAssertGreaterThanOrEqual(frame.height, 0, context)
            let left = height - WaitFrame.foot - room - WaitFrame.gap - WaitFrame.top
            if left <= 0 { XCTAssertEqual(frame.height, 0, context) }
            else if left >= width * 4 / 3 { XCTAssertEqual(frame.width, width, accuracy: 1e-9, context) }
            else {
                XCTAssertEqual(frame.height, left, accuracy: 1e-9, context)
                XCTAssertEqual(WaitFrame.top + frame.height + WaitFrame.gap + room + WaitFrame.foot, height, accuracy: 1e-9, context)
            }
        }
    }

    /// The room is kept for the wait's own longest words: the longest step name the row can show, a count of five
    /// digits a side, and the longest of the early line's sentences.
    func testTheMostTheWaitsColumnCanShowIsItsLongestWords() {
        let names = SolveJob.Stage.allCases.map(WaitRow.word) + [QueuedRow.word]
        XCTAssertTrue(names.contains(WaitFrame.mostName))
        XCTAssertTrue(names.allSatisfy { $0.count <= WaitFrame.mostName.count })
        XCTAssertEqual(WaitFrame.mostCount, "88,888 of 88,888")
        let sentences = [PredictedEarlySky.Source.pointing, .moon, .sun].map(PredictedEarlySky.sentence(for:))
        XCTAssertEqual(WaitFrame.mostSentence, "Placed from where your iPhone pointed: predicted until matched.")
        XCTAssertTrue(sentences.allSatisfy { $0.count <= WaitFrame.mostSentence.count })
    }

    /// The wait sizes its photo, rings and column from that frame, with the room measured once, hidden, from the most
    /// the column can show; its row stacks the count under the name when the two do not fit on one line (the record's
    /// rule: words side by side stack, never cut; 8 pt as the Result's stacked counts), and no word of it is held.
    func testTheWaitSizesItsFrameOnceFromTheMostItsColumnCanShow() throws {
        let file = try code("Screens/AfterSendScreens.swift").code
        let wait = compact(try body(of: "struct WaitingScreen: View", in: file))
        XCTAssertTrue(wait.contains("@Environment(\\.dynamicTypeSize)privatevardynamicTypeSize"))
        XCTAssertTrue(wait.contains("@StateprivatevarcolumnRoom:Double?"), "no room until it is measured")
        XCTAssertTrue(wait.contains("letframe=WaitFrame.size(screenWidth:Double(geometry.size.width),screenHeight:Double(geometry.size.height),columnRoom:dynamicTypeSize.isAccessibilitySize?(columnRoom??0):nil)letwidth=frame.width"))
        // AUDIT-D on largertext-wait-m: the photo, its rings and the row are drawn only once the room is measured, so the
        // first frame at an accessibility size never shows the photo at a size it then leaves
        XCTAssertTrue(wait.contains("letmeasuring=dynamicTypeSize.isAccessibilitySize&&columnRoom==nil"))
        XCTAssertTrue(wait.contains("ShotPhoto(shot:model.shot,width:width).atTop(Arrive.photoTop).opacity(measuring?0:1)"))
        XCTAssertEqual(wait.components(separatedBy: ".opacity(measuring?0:1)").count - 1, 5, "the photo, both ring layers and both rows")
        XCTAssertTrue(wait.contains("ifdynamicTypeSize.isAccessibilitySize{VStack(alignment:.leading,spacing:14){StepRow(name:WaitFrame.mostName,count:WaitFrame.mostCount,fraction:nil)EarlySource(sentence:WaitFrame.mostSentence)}.hidden().accessibilityHidden(true).onGeometryChange(for:Double.self){Double($0.size.height)}action:{columnRoom=$0}}"),
                      "the room measured from the most the column can show, never shown")
        XCTAssertTrue(wait.contains("ShotPhoto(shot:model.shot,width:width).atTop(Arrive.photoTop)"), "the photo whole in the frame, centred across")
        XCTAssertTrue(wait.contains("letearly=earlyPlaced(width:width,height:width*4/3)"), "the rings placed for the frame")
        XCTAssertEqual(wait.components(separatedBy: ".atTop(Arrive.photoTop+width*4/3+30)").count - 1, 2, "the row and its line under the frame")
        XCTAssertFalse(wait.contains("geometry.size.width*4/3"), "nothing drawn at the full width's height")
        let row = compact(try body(of: "private struct StepRow: View", in: file))
        XCTAssertTrue(row.contains("ViewThatFits(in:.horizontal){HStack(alignment:.firstTextBaseline){nameText"), "side by side while both fit")
        XCTAssertTrue(row.contains("VStack(alignment:.leading,spacing:8){nameTextifletcount{countText(count)}}"), "else the count under the name")
        let source = compact(try body(of: "private struct EarlySource: View", in: file))
        XCTAssertFalse(row.contains(".lineLimit(") || source.contains(".lineLimit("), "no word of the column held to a line")
    }

    /// mainbutton-scale-m (UIUX-iphone-largertext-sweep-READY item 6, DECIDED-BY-LEAD 24 Nov 19:56): one line of a pill is
    /// its word's line at the current size with the space the default size leaves over and under it kept, so 44 pt at
    /// Large and growing with the word, never less than the control's height; the screen's margins are 24 pt.
    func testAPillsOneLineIsItsWordsLineWithTheDefaultsSpaceKept() {
        XCTAssertEqual(ButtonGeometry.margin, 24)
        XCTAssertEqual(ButtonGeometry.spaceOverUnder(lineHeightAtLarge: 20, controlHeight: 44), 12)
        XCTAssertEqual(ButtonGeometry.oneLineHeight(lineHeight: 20, lineHeightAtLarge: 20, controlHeight: 44), 44, "44 pt at Large")
        XCTAssertEqual(ButtonGeometry.oneLineHeight(lineHeight: 62, lineHeightAtLarge: 20, controlHeight: 44), 86, "the word's line and 12 over and under")
        XCTAssertEqual(ButtonGeometry.oneLineHeight(lineHeight: 16, lineHeightAtLarge: 20, controlHeight: 44), 44, "never less than the control")
        XCTAssertEqual(ButtonGeometry.spaceOverUnder(lineHeightAtLarge: 50, controlHeight: 44), 0, "never negative")
        var g = SeededGenerator(seed: propertySeed())
        for _ in 0..<2000 {
            let control = g.double(in: 30...60), atLarge = g.double(in: 10...40), line = g.double(in: 8...120)
            let one = ButtonGeometry.oneLineHeight(lineHeight: line, lineHeightAtLarge: atLarge, controlHeight: control)
            let space = ButtonGeometry.spaceOverUnder(lineHeightAtLarge: atLarge, controlHeight: control)
            let context = "\(control) \(atLarge) \(line)"
            XCTAssertGreaterThanOrEqual(one, control, context)
            XCTAssertGreaterThanOrEqual(one, line + 2 * space - 1e-9, context)
            XCTAssertEqual(one, max(control, line + 2 * space), accuracy: 1e-9, context)
            XCTAssertLessThanOrEqual(ButtonGeometry.oneLineHeight(lineHeight: line, lineHeightAtLarge: atLarge, controlHeight: control),
                                     ButtonGeometry.oneLineHeight(lineHeight: line + 1, lineHeightAtLarge: atLarge, controlHeight: control), context)
        }
    }

    /// The main button's pill and the outlined pill grow with their word: the pill one line high by that rule, the
    /// circle keeping it and its icon growing with the text, the word wrapping, centred, inside the pill rather than held
    /// to one line; a pill at the foot of a screen stands within the 24 pt margins, so it wraps rather than passing them.
    func testTheMainButtonAndTheOutlinedPillGrowWithTheirWordAndWrapPastTheMargins() throws {
        let main = compact(try code("UI/MainButton.swift").code)
        XCTAssertTrue(main.contains("@ScaledMetric(relativeTo:.body)privatevariconSize=ControlStyle.iconSize"), "the icon grows with the text")
        XCTAssertTrue(main.contains("letsize=ButtonLine.size(fontSize:ControlStyle.mainFontSize,controlHeight:ControlStyle.mainHeight,dynamicTypeSize)returnViewThatFits(in:.horizontal){row(pressed:pressed,size:size,wraps:false)row(pressed:pressed,size:size,wraps:true)}"),
                      "on one line while the button fits, else wrapped")
        XCTAssertTrue(main.contains("letheight=CGFloat(size.oneLine)letcircle=height"))
        XCTAssertTrue(main.contains("ifwraps{text.multilineTextAlignment(.center).fixedSize(horizontal:false,vertical:true).padding(.vertical,space).frame(minHeight:height)}else{text.lineLimit(1).fixedSize().frame(height:height)}"),
                      "wrapped, centred, with the default's space over and under; else exactly one line high")
        XCTAssertTrue(main.contains(".padding(.trailing,pressed?pad+circle:pad)"), "the press still docks the circle in the pill")
        XCTAssertTrue(main.contains("icon.image(iconSize)"))
        XCTAssertTrue(main.contains("UIFontMetrics(forTextStyle:.body).scaledValue(for:CGFloat(fontSize),compatibleWith:traits)"),
                      "the word's line from the font as Dynamic Type scales it")
        let pill = compact(try code("UI/OutlinePill.swift").code)
        XCTAssertTrue(pill.contains("letsize=ButtonLine.size(fontSize:fontSize,controlHeight:height,dynamicTypeSize)returnViewThatFits(in:.horizontal){pill(pressed:pressed,size:size,wraps:false)pill(pressed:pressed,size:size,wraps:true)}"))
        XCTAssertTrue(pill.contains("ifwraps{text.multilineTextAlignment(.center).fixedSize(horizontal:false,vertical:true).padding(.vertical,space).frame(minHeight:height)}else{text.lineLimit(1).fixedSize().frame(height:height)}"))
        XCTAssertTrue(pill.contains("returnword(wraps:wraps,height:oneLine,space:CGFloat(size.space)).padding(.horizontal,20)"))
        XCTAssertFalse(pill.contains(".frame(height:CGFloat(height))"), "the pill's height grows with its word")
        let screens = compact(try code("Screens/CompanionScreens.swift").code)
        XCTAssertTrue(screens.contains("funcatBottom(_gap:Double)->someView{padding(.horizontal,CGFloat(ButtonGeometry.margin)).frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.bottom).padding(.bottom,CGFloat(gap))}"),
                      "a pill at a screen's foot within the margins")
    }

    /// largertext-unreachable-m (UIUX-iphone-largertext-sweep-READY item 4, DECIDED-BY-LEAD 24 Nov 19:56): at
    /// accessibility sizes the photo of Can't reach the laptop is whole in a smaller 3:4 frame from 118, centred, as tall
    /// as what is left above the lines and the stacked buttons (16 pt under the photo, 34 pt kept at the foot), never
    /// less than 160 pt tall, past which the column scrolls; at default sizes the screen's width, as before.
    func testCantReachTheLaptopsPhotoGivesTheRoomDownToItsFloorAndThenTheColumnScrolls() {
        XCTAssertEqual(UnreachableFrame.top, 118)
        XCTAssertEqual(UnreachableFrame.gap, 16)
        XCTAssertEqual(UnreachableFrame.linesToButtons, 20)
        XCTAssertEqual(UnreachableFrame.betweenButtons, 12)
        XCTAssertEqual(UnreachableFrame.foot, 34)
        XCTAssertEqual(UnreachableFrame.floor, 160)
        let normal = UnreachableFrame.size(screenWidth: 402, screenHeight: 874, columnRoom: nil)
        XCTAssertEqual(normal.width, 402)
        XCTAssertEqual(normal.height, 536)
        let some = UnreachableFrame.size(screenWidth: 402, screenHeight: 874, columnRoom: 300)
        XCTAssertEqual(some.height, 406, accuracy: 1e-9)
        XCTAssertEqual(some.width, 304.5, accuracy: 1e-9)
        XCTAssertFalse(UnreachableFrame.scrolls(screenHeight: 874, photoHeight: some.height, columnRoom: 300))
        let tall = UnreachableFrame.size(screenWidth: 402, screenHeight: 874, columnRoom: 600)
        XCTAssertEqual(tall.height, 160, "never less tall than the floor")
        XCTAssertTrue(UnreachableFrame.scrolls(screenHeight: 874, photoHeight: tall.height, columnRoom: 600), "past it the column scrolls")
        var g = SeededGenerator(seed: propertySeed())
        for _ in 0..<2000 {
            let width = g.double(in: 300...460), height = g.double(in: 560...1000)
            let room = g.pick([0, g.double(in: 0...900), 5000])
            let frame = UnreachableFrame.size(screenWidth: width, screenHeight: height, columnRoom: room)
            let context = "\(width) \(height) \(room)"
            XCTAssertEqual(frame.width * 4 / 3, frame.height, accuracy: 1e-9, context)
            XCTAssertLessThanOrEqual(frame.width, width + 1e-9, context)
            XCTAssertGreaterThanOrEqual(frame.height, min(UnreachableFrame.floor, width * 4 / 3) - 1e-9, context)
            let left = height - UnreachableFrame.foot - room - UnreachableFrame.gap - UnreachableFrame.top
            let scrolls = UnreachableFrame.scrolls(screenHeight: height, photoHeight: frame.height, columnRoom: room)
            if left >= width * 4 / 3 { XCTAssertEqual(frame.width, width, accuracy: 1e-9, context); XCTAssertFalse(scrolls, context) }
            else if left >= UnreachableFrame.floor { XCTAssertEqual(frame.height, left, accuracy: 1e-9, context); XCTAssertFalse(scrolls, context) }
            else { XCTAssertEqual(frame.height, UnreachableFrame.floor, accuracy: 1e-9, context); XCTAssertTrue(scrolls, context) }
        }
    }

    /// The screen at accessibility sizes: one scrolling column, the photo in that frame, then the two lines wrapping,
    /// centred, the icon at the first line, then Try again and Change address stacked, each centred, within the margins;
    /// the column measured once, hidden, and drawn only once measured. At default sizes the row stands as recorded.
    func testCantReachTheLaptopStacksItsColumnAtAccessibilitySizes() throws {
        let after = compact(try body(of: "struct AfterShotScreen: View", in: try code("Screens/CompanionScreens.swift").code))
        XCTAssertTrue(after.contains("@StateprivatevarcolumnRoom:Double?"))
        XCTAssertTrue(after.contains("letstacked=model.unreachable&&dynamicTypeSize.isAccessibilitySize"))
        XCTAssertTrue(after.contains("letframe=UnreachableFrame.size(screenWidth:width,screenHeight:Double(geometry.size.height),columnRoom:columnRoom??0)"))
        XCTAssertTrue(after.contains("unreachableColumn.hidden().accessibilityHidden(true).onGeometryChange(for:Double.self){Double($0.size.height)}action:{columnRoom=$0}"),
                      "the column measured once, hidden")
        XCTAssertTrue(after.contains("ScrollView{VStack(spacing:0){ShotPhoto(shot:model.shot,width:frame.width)unreachableColumn.padding(.top,CGFloat(UnreachableFrame.gap))}.frame(maxWidth:.infinity).padding(.top,CGFloat(UnreachableFrame.top)).padding(.bottom,CGFloat(UnreachableFrame.foot))}.scrollBounceBehavior(.basedOnSize).opacity(columnRoom==nil?0:1)"),
                      "one scrolling column, the photo in its frame, nothing drawn before it is measured")
        XCTAssertTrue(after.contains("HStack(alignment:.firstTextBaseline,spacing:8){AlbedianIcon.unreachable.image(17)"), "the icon at the first line")
        XCTAssertTrue(after.contains(".multilineTextAlignment(.center)VStack(spacing:CGFloat(UnreachableFrame.betweenButtons)){MainButton("), "the lines centred, the buttons stacked")
        XCTAssertTrue(after.contains("action:model.changeAddress)}.padding(.top,CGFloat(UnreachableFrame.linesToButtons))}.padding(.horizontal,CGFloat(ButtonGeometry.margin))"))
        XCTAssertTrue(after.contains("ifmodel.unreachable&&!stacked{"), "at default sizes the row as recorded")
    }

    /// largertext-refused-m (UIUX-iphone-largertext-sweep-READY item 5, DECIDED-BY-LEAD 24 Nov 19:56): at accessibility
    /// sizes the laptop's sentence wraps whole, no ellipsis, its icon at its first line; Try again stands 16 pt under its
    /// last line in the flow; the block stands on the foot and grows upward; the photo gives the room down to the floor
    /// of Can't reach the laptop's column, past which the column scrolls; measured once, hidden, drawn once measured.
    func testTheLaptopsRefusalStandsWholeWithTryAgainInItsFlowAtAccessibilitySizes() throws {
        let stopped = compact(try body(of: "struct StoppedScreen: View", in: try code("Screens/AfterSendScreens.swift").code))
        XCTAssertTrue(stopped.contains("@StateprivatevarcolumnRoom:Double?"))
        XCTAssertTrue(stopped.contains("letframe=UnreachableFrame.size(screenWidth:width,screenHeight:Double(geometry.size.height),columnRoom:columnRoom??0)"))
        XCTAssertTrue(stopped.contains("stoppedColumn.hidden().accessibilityHidden(true).onGeometryChange(for:Double.self){Double($0.size.height)}action:{columnRoom=$0}"))
        XCTAssertTrue(stopped.contains("ScrollView{VStack(spacing:0){ShotPhoto(shot:model.shot,width:frame.width)Spacer(minLength:CGFloat(UnreachableFrame.gap))stoppedColumn}.frame(maxWidth:.infinity,minHeight:geometry.size.height-CGFloat(UnreachableFrame.top+UnreachableFrame.foot))"),
                      "the block on the foot, growing upward, the photo above it in its frame")
        XCTAssertTrue(stopped.contains(".scrollBounceBehavior(.basedOnSize).opacity(columnRoom==nil?0:1)"), "scrolls past the floor; nothing drawn before it is measured")
        XCTAssertTrue(stopped.contains("VStack(spacing:16){ifletsentence=model.stoppedSentence{sentenceLine(sentence).frame(maxWidth:.infinity,alignment:.leading)}MainButton("),
                      "Try again 16 pt under the sentence, in the flow")
        XCTAssertTrue(stopped.contains("HStack(alignment:.firstTextBaseline,spacing:8){AlbedianIcon.warning.image(18)"), "the icon at the first line")
        XCTAssertFalse(stopped.contains(".lineLimit("), "the sentence never held to a line")
        XCTAssertTrue(stopped.contains(".atTop(Arrive.photoTop+width*4/3+16)") && stopped.contains(".atBottom(Arrive.unreachableButtonsBottomGap)"),
                      "at default sizes as recorded")
    }
}
