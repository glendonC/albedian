import AlbedianCaptureCore
import Foundation
import XCTest

/// firstuse-home-result-one (UIUX-iphone-firstuse-READY item 3, DECIDED-BY-LEAD 26 Nov 23:58): the home's Milky Way
/// answers the phone's tilt by 12 pt each way (off under Reduce Motion; the shade, Credits, the logo, Start and the
/// credit line stay put), and the result keeps the names of its matched stars brighter than magnitude 3, by D20's rule
/// (brightest first, never into a brighter star's name or any mark), Geist 11 in the catalog matched colour with the
/// dark halo, 10 pt right of the mark's centre, else left, never past the card.
final class FirstUseHomeResultTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")

    private func mark(_ id: String, _ title: String, mag: Double?, x: Double, y: Double, catalog: String? = nil) -> ResultNames.Placed {
        ResultNames.Placed(mark: SolutionReadout.Mark(id: id, title: title, xPx: 0, yPx: 0, apparentMagnitude: mag, catalogSourceId: catalog), x: x, y: y)
    }

    func testTheNumbersAreTheRecords() {
        XCTAssertEqual(HomeTilt.maxPt, 12)
        XCTAssertTrue((8.0...16.0).contains(HomeTilt.maxPt), "the record lets it move within 8 to 16 pt beside a device")
        XCTAssertEqual(ResultNames.sizePt, 11)
        XCTAssertEqual(ResultNames.gapPt, 10)
    }

    func testOnlyAMatchedStarBrighterThanMagnitudeThreeWithANameOfItsOwnIsNamed() {
        let placed = [mark("a", "Acrux", mag: 0.8, x: 60, y: 60), mark("b", "Faint", mag: 3.0, x: 60, y: 140),
                      mark("c", "HIP 61084", mag: 1.6, x: 60, y: 220, catalog: "HIP 61084"), mark("d", "Unknown", mag: nil, x: 60, y: 300)]
        XCTAssertEqual(ResultNames.names(placed, widthPt: 354, heightPt: 472), ["a": false])
    }

    func testANameGoesRightAndLeftNearTheRightEdgeAndNeverPastTheCard() {
        let right = ResultNames.names([mark("a", "Acrux", mag: 0.8, x: 100, y: 100)], widthPt: 354, heightPt: 472)
        XCTAssertEqual(right, ["a": false])
        let left = ResultNames.names([mark("a", "Acrux", mag: 0.8, x: 340, y: 100)], widthPt: 354, heightPt: 472)
        XCTAssertEqual(left, ["a": true])
        // Too long for either side of a mark on a narrow card: no name rather than one past the card
        let none = ResultNames.names([mark("a", "Epsilon Centauri", mag: 2.3, x: 50, y: 100)], widthPt: 100, heightPt: 472)
        XCTAssertEqual(none, [:])
        // 4 pt inside the side: a right box ending 2 pt from the edge does not fit, so the name goes left
        XCTAssertEqual(ResultNames.names([mark("a", "Acrux", mag: 0.8, x: 309.2, y: 100)], widthPt: 354, heightPt: 472), ["a": true])
        // A name whose top would be above the card is not drawn
        XCTAssertEqual(ResultNames.names([mark("a", "Acrux", mag: 0.8, x: 100, y: 3)], widthPt: 354, heightPt: 472), [:])
    }

    func testTheBrighterStarIsNamedFirstAndAFainterNameNeverRunsIntoIt() {
        // Two stars side by side: the brighter keeps its name on the right; the fainter's right would run into it, so it
        // flips left
        let pair = [mark("f", "Mimosa", mag: 1.25, x: 160, y: 100), mark("b", "Acrux", mag: 0.77, x: 200, y: 100)]
        XCTAssertEqual(ResultNames.names(pair, widthPt: 354, heightPt: 472), ["b": false, "f": true])
        // Hemmed in on both sides by brighter names and marks: no name
        let hemmed = [mark("l", "Gacrux", mag: 1.6, x: 60, y: 100), mark("m", "Imai", mag: 2.7, x: 120, y: 100),
                      mark("r", "Acrux", mag: 0.8, x: 160, y: 100)]
        XCTAssertNil(ResultNames.names(hemmed, widthPt: 354, heightPt: 472)["m"])
        // A fainter star's mark does not stand in a brighter star's way (D20 as corrected): the name stays on the right
        let over = [mark("b", "Acrux", mag: 0.77, x: 100, y: 100), mark("f", "Faint", mag: 4.2, x: 125, y: 100)]
        XCTAssertEqual(ResultNames.names(over, widthPt: 354, heightPt: 472), ["b": false])
    }

    /// Over many random cards, every name kept is whole 4 pt inside the card's sides and runs into no brighter star's
    /// mark and no other name.
    func testNoNameKeptEverRunsIntoAMarkOrAnotherNameOrPastTheCard() {
        for seed in UInt64(1)...20 {
            var state = seed &* 0x9E3779B97F4A7C15
            func next() -> Double {
                state = state &* 6364136223846793005 &+ 1442695040888963407
                return Double(state >> 11) / Double(1 << 53)
            }
            let width = 354.0, height = 472.0
            let names = ["Acrux", "Mimosa", "Gacrux", "Gamma Centauri", "Epsilon Centauri", "Imai", "Hadar", "Rigil Kentaurus"]
            let placed = (0..<12).map { i in
                mark("m\(i)", names[i % names.count], mag: next() * 4, x: next() * width, y: next() * height)
            }
            let named = ResultNames.names(placed, widthPt: width, heightPt: height)
            let boxes = named.map { id, onLeft in ResultNames.box(placed.first { $0.mark.id == id }!, left: onLeft) }
            for (id, onLeft) in named {
                let p = placed.first { $0.mark.id == id }!
                XCTAssertTrue(p.mark.isNamedOnThePhone, "seed \(seed)")
                let box = ResultNames.box(p, left: onLeft)
                XCTAssertGreaterThanOrEqual(box.x0, 4, "seed \(seed)")
                XCTAssertLessThanOrEqual(box.x1, width - 4, "seed \(seed)")
                XCTAssertGreaterThanOrEqual(box.y0, 0, "seed \(seed)")
                XCTAssertLessThanOrEqual(box.y1, height, "seed \(seed)")
                for other in placed where (other.mark.apparentMagnitude ?? .infinity) < (p.mark.apparentMagnitude ?? .infinity) {
                    XCTAssertFalse(box.overlaps(ResultNames.room(other)), "seed \(seed): \(id) runs into the brighter \(other.mark.id)'s mark")
                }
                XCTAssertEqual(boxes.filter { $0.overlaps(box) }.count, 1, "seed \(seed): \(id) runs into another name")
            }
        }
    }

    /// The code, comments and literals emptied, without white space, from one declaration to the next given.
    private func body(_ file: String, from start: String, to end: String) throws -> String {
        let text = try String(contentsOf: app.appendingPathComponent(file), encoding: .utf8)
        let code = try SourceReader.code(text, .swift)
        let from = try XCTUnwrap(code.range(of: start), start)
        let to = try XCTUnwrap(code.range(of: end, range: from.upperBound..<code.endIndex), end)
        return String(code[from.lowerBound..<to.lowerBound]).filter { !$0.isWhitespace }
    }

    func testTheHomesPictureAnswersTheTiltAndNothingElseMoves() throws {
        let home = try body("Screens/CompanionScreens.swift", from: "struct HomeScreen: View", to: "struct CreditsScreen: View")
        XCTAssertTrue(home.contains("TiltingBackdrop(image:,overscanPt:HomeTilt.maxPt,tilts:!reduceMotion).frame(width:geometry.size.width+CGFloat(2*HomeTilt.maxPt),height:geometry.size.height+CGFloat(2*HomeTilt.maxPt))"),
                      "the picture 12 pt past every edge, tilting unless Reduce Motion is on")
        XCTAssertTrue(home.contains("@Environment(\\.accessibilityReduceMotion)privatevarreduceMotion"))
        XCTAssertFalse(home.contains("Image("), "the picture is drawn once, by the tilting view")
        let tilt = try SourceReader.code(String(contentsOf: app.appendingPathComponent("Screens/TiltingBackdrop.swift"), encoding: .utf8), .swift).filter { !$0.isWhitespace }
        XCTAssertTrue(tilt.contains("UIInterpolatingMotionEffect(keyPath:,type:.tiltAlongHorizontalAxis)"))
        XCTAssertTrue(tilt.contains("UIInterpolatingMotionEffect(keyPath:,type:.tiltAlongVerticalAxis)"))
        XCTAssertTrue(tilt.contains(".minimumRelativeValue=-overscanPt") && tilt.contains(".maximumRelativeValue=overscanPt"))
        XCTAssertTrue(tilt.contains("group.motionEffects=[across,down]view.addMotionEffect(group)"), "both axes move it")
        XCTAssertTrue(tilt.contains("iftilts{") && tilt.contains("view.motionEffects.forEach(view.removeMotionEffect)"),
                      "under Reduce Motion no effect is added, and one added is taken away")
    }

    func testTheResultNamesItsBrightMatchedStarsInTheMatchedColourWithTheHalo() throws {
        let marks = try body("Screens/CompanionScreens.swift", from: "struct CatalogMarks: View", to: "struct Lines: View")
        XCTAssertTrue(marks.contains("ResultNames.names(placed,widthPt:width,heightPt:height)"))
        XCTAssertTrue(marks.contains(".font(Font.custom(,fixedSize:ResultNames.sizePt))"), "Geist 11")
        XCTAssertTrue(marks.contains("letgap=ResultNames.gapPt"))
        XCTAssertTrue(marks.contains("Color.black.opacity(SkyOverlayStyle.haloOpacity)") && marks.contains("SkyOverlayStyle.haloWidthPt/2"), "the dark halo")
        XCTAssertTrue(marks.contains("context.draw(halo,at:CGPoint(x:at.x+dx*out,y:at.y+dy*out),anchor:anchor)}context.draw(name(colour),at:at,anchor:anchor)"), "drawn under the name")
        XCTAssertTrue(marks.contains("letcolour=Color(ControlStyle.Evidence.catalogMatched.colour)"))
        // AUDIT-C's T21: only the marks on the card, only the names the rule gives, the solution's own words, on the
        // side the rule chose and anchored away from the mark
        XCTAssertTrue(marks.contains("letplaced=marks.filter{frame.isOnCard(xPx:$0.xPx,yPx:$0.yPx,width:width,height:height)}.map{markin"),
                      "marks off the card are neither drawn nor named")
        XCTAssertTrue(marks.contains("forpinplaced{guardletonLeft=named[p.mark.id]else{continue}"), "a name only where the rule gives one")
        XCTAssertTrue(marks.contains("Text(p.mark.title)"), "the solution's own title, never its catalog number")
        XCTAssertFalse(marks.contains("catalogSourceId"))
        XCTAssertTrue(marks.contains("letat=CGPoint(x:onLeft?p.x-gap:p.x+gap,y:p.y)"), "10 pt right of the mark's centre, else left")
        XCTAssertTrue(marks.contains("letanchor:UnitPoint=onLeft?.trailing:.leading"), "the name runs away from its mark")
    }
}
