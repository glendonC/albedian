import AlbedianCaptureCore
import Foundation
import XCTest

/// lockon-names-m (UIUX-iphone-firstuse-READY item 3, its second half; DECIDED-BY-LEAD 26 Nov 23:58): in the lock-on
/// the amber names ride their rings (D31 item 1) and take the matched colour with MOTION's class change (0.2 s on the
/// standard curve, `MotionStyle.colour`); a named star with no early ring gets its name with its mark. The lock-on
/// starts from the early marks exactly as the wait drew them: the same names by D20 over the same set, each ring at its
/// own radius (6.5 pt named, 4.5 pt unnamed).
final class LockOnNamesTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")

    /// The code from one declaration to the next given one, comments and literals emptied, without white space.
    private func body(_ file: String, from start: String, to end: String) throws -> String {
        let text = try String(contentsOf: app.appendingPathComponent(file), encoding: .utf8)
        let code = try SourceReader.code(text, .swift)
        let from = try XCTUnwrap(code.range(of: start), start)
        let to = try XCTUnwrap(code.range(of: end, range: from.upperBound..<code.endIndex), end)
        return String(code[from.lowerBound..<to.lowerBound]).filter { !$0.isWhitespace }
    }

    private let flags = [false, true]

    func testAnEarlyRingStaysWholeUntilThePassThenLeavesUnlessMatchedAndGoesAtRest() {
        for matched in flags { for settled in flags { for reduceMotion in flags {
            XCTAssertEqual(LockOnFade.ring(matched: matched, moved: false, settled: settled, reduceMotion: reduceMotion), 1,
                           "whole before the pass")
        } } }
        XCTAssertEqual(LockOnFade.ring(matched: true, moved: true, settled: false, reduceMotion: false), 1, "travels whole")
        XCTAssertEqual(LockOnFade.ring(matched: true, moved: true, settled: true, reduceMotion: false), 0, "the class change at rest")
        XCTAssertEqual(LockOnFade.ring(matched: false, moved: true, settled: false, reduceMotion: false), 0, "a ring the solve does not place leaves")
        for matched in flags { for settled in flags {
            XCTAssertEqual(LockOnFade.ring(matched: matched, moved: true, settled: settled, reduceMotion: true), 0, "the dissolve")
        } }
    }

    func testTheMatchedMarkComesAtRestOrInTheDissolve() {
        for settled in flags {
            XCTAssertEqual(LockOnFade.matchedMark(moved: false, settled: settled, reduceMotion: true), 0)
            XCTAssertEqual(LockOnFade.matchedMark(moved: true, settled: settled, reduceMotion: true), 1)
        }
        for moved in flags {
            XCTAssertEqual(LockOnFade.matchedMark(moved: moved, settled: false, reduceMotion: false), 0)
            XCTAssertEqual(LockOnFade.matchedMark(moved: moved, settled: true, reduceMotion: false), 1)
        }
    }

    func testANameRidesItsRingLeavesWithAnUnmatchedOneAndUnderReduceMotionGoesWhereItStood() {
        for matched in flags { for reduceMotion in flags {
            XCTAssertEqual(LockOnFade.name(matched: matched, moved: false, reduceMotion: reduceMotion), 1, "whole before the pass")
        } }
        XCTAssertEqual(LockOnFade.name(matched: true, moved: true, reduceMotion: false), 1, "rides its ring and stays at rest")
        XCTAssertEqual(LockOnFade.name(matched: false, moved: true, reduceMotion: false), 0, "leaves with its ring")
        XCTAssertEqual(LockOnFade.name(matched: true, moved: true, reduceMotion: true), 0, "goes where it stood")
        XCTAssertEqual(LockOnFade.name(matched: false, moved: true, reduceMotion: true), 0)
    }

    func testTheNameTakesTheMatchedColourFromTheAmber() {
        let amber = ControlStyle.Evidence.predicted.colour
        let matched = ControlStyle.Evidence.catalogMatched.colour
        XCTAssertEqual(LockOnFade.nameColour(0), amber)
        XCTAssertEqual(LockOnFade.nameColour(1), matched)
        XCTAssertEqual(LockOnFade.nameColour(-0.5), amber, "held at the amber")
        XCTAssertEqual(LockOnFade.nameColour(1.5), matched, "held at the matched colour")
        let half = LockOnFade.nameColour(0.5)
        XCTAssertEqual(half.red, (amber.red + matched.red) / 2, accuracy: 1e-12)
        XCTAssertEqual(half.green, (amber.green + matched.green) / 2, accuracy: 1e-12)
        XCTAssertEqual(half.blue, (amber.blue + matched.blue) / 2, accuracy: 1e-12)
        XCTAssertEqual(half.opacity, 1)
        XCTAssertEqual(LockOnFade.nameShare(matched: true, settled: true, reduceMotion: false), 1, "at rest, matched")
        XCTAssertEqual(LockOnFade.nameShare(matched: true, settled: false, reduceMotion: false), 0, "amber while it travels")
        XCTAssertEqual(LockOnFade.nameShare(matched: false, settled: true, reduceMotion: false), 0, "an unmatched name never turns")
        XCTAssertEqual(LockOnFade.nameShare(matched: true, settled: true, reduceMotion: true), 0, "under Reduce Motion the amber name only goes")
    }

    func testTheLockOnStartsFromTheEarlyMarksAsTheWaitDrewThem() throws {
        let file = "Screens/AfterSendScreens.swift"
        let lockOn = try body(file, from: "private struct LockOnLayer: View", to: "private struct MatchedMark: View")
        let early = try body(file, from: "private struct EarlyMarksLayer: View", to: "private struct ArrivingMark")
        let names = "letnames=SkyOverlayNames.names(placed.map{SkyOverlayNames.Placed(mark:$0.0,x:$0.1.x,y:$0.1.y)},widthPt:width,heightPt:height)"
        XCTAssertTrue(early.contains(names), "D20 over the current set")
        XCTAssertTrue(lockOn.contains(names), "the same names over the same set")
        XCTAssertTrue(lockOn.contains("letnamed=names[mark.id]!=nil"))
        XCTAssertTrue(lockOn.contains(".frame(width:CGFloat(2*radius),height:CGFloat(2*radius))"))
        XCTAssertTrue(lockOn.contains("letradius=named?SkyOverlayStyle.ringRadiusPt:SkyOverlayStyle.unnamedRingRadiusPt"),
                      "each ring at its own radius, as PlacedSkyOverlay draws it")
        XCTAssertTrue(lockOn.contains(".opacity(LockOnFade.ring(matched:end!=nil,moved:moved,settled:settled,reduceMotion:reduceMotion))"))
        XCTAssertTrue(lockOn.contains("MatchedMark().opacity(LockOnFade.matchedMark(moved:moved,settled:settled,reduceMotion:reduceMotion))"
            + ".position(reduceMotion?end:at)"))
    }

    func testTheNamesRideTheirRingsAndTakeTheMatchedColourWithTheClassChange() throws {
        let file = "Screens/AfterSendScreens.swift"
        let lockOn = try body(file, from: "private struct LockOnLayer: View", to: "private struct MatchedMark: View")
        // The riding name: at the ring's place, leaving with an unmatched ring on frame out, turning at rest
        XCTAssertTrue(lockOn.contains("ifletonLeft=names[mark.id]{LockOnName(name:mark.name,onLeft:onLeft,previewWidthPt:width,"
            + "share:LockOnFade.nameShare(matched:end!=nil,settled:settled,reduceMotion:reduceMotion))"
            + ".opacity(LockOnFade.name(matched:end!=nil,moved:moved,reduceMotion:reduceMotion))"
            + ".animation(end==nil&&!reduceMotion?.motion(MotionStyle.frameOut,reduceMotion:false):nil,value:moved)"
            + ".position(at)"))
        // Under Reduce Motion the name in the matched colour comes at the matched place with its mark, in the dissolve
        XCTAssertTrue(lockOn.contains("ifreduceMotion,letend{LockOnName(name:mark.name,onLeft:onLeft,previewWidthPt:width,share:1)"
            + ".opacity(LockOnFade.matchedMark(moved:moved,settled:settled,reduceMotion:true)).position(end)}"))
        // The ring's opacity and the name's share follow the travel and MOTION's class change
        XCTAssertTrue(lockOn.contains(".animation(reduceMotion?.easeInOut(duration:MotionStyle.reduceMotionSeconds):.motion(MotionStyle.frameIn,reduceMotion:false),value:moved)"))
        XCTAssertTrue(lockOn.contains(".animation(reduceMotion?nil:.motion(MotionStyle.colour,reduceMotion:false),value:settled)"))
        XCTAssertEqual(MotionStyle.colour.seconds, 0.2)
        let name = try body(file, from: "private struct LockOnName: View, Animatable", to: "private struct EarlyMarksLayer: View")
        XCTAssertTrue(name.contains("varanimatableData:Double{get{share}set{share=newValue}}"), "the colour changes frame by frame")
        XCTAssertTrue(name.contains("PlacedSkyOverlay.drawName(name,at:CGPoint(x:size.width/2,y:size.height/2),onLeft:onLeft,"
            + "colour:Color(LockOnFade.nameColour(share)),in:context)"))
        XCTAssertTrue(name.contains(".frame(width:CGFloat(SkyOverlayNames.markFrameWidthPt(name:name,named:true,previewWidthPt:previewWidthPt)),"
            + "height:CGFloat(SkyOverlayNames.markFrameHeightPt))"), "the early mark's own frame, so the name sits where it sat")
        // The early names and the aim overlay's are drawn by the same code, in the amber
        let overlay = try body("Screens/SkyOverlayView.swift", from: "struct PlacedSkyOverlay: View", to: "static func drawName(")
        XCTAssertTrue(overlay.contains("guardletonLeftelse{return}drawName(mark.name,at:at,onLeft:onLeft,colour:amber,in:context)}"))
    }

    func testANamedStarWithNoEarlyRingGetsItsNameWithItsMark() throws {
        let marks = try body("Screens/CompanionScreens.swift", from: "struct CatalogMarks: View", to: "struct Lines: View")
        // One Canvas draws every matched mark and then its name: they come together, with nothing of their own to delay either
        XCTAssertEqual(marks.components(separatedBy: "Canvas{").count - 1, 1)
        let stroke = try XCTUnwrap(marks.range(of: "context.stroke(Path(ellipseIn:"))
        let named = try XCTUnwrap(marks.range(of: "letnamed=ResultNames.names(placed,widthPt:width,heightPt:height)"))
        XCTAssertLessThan(stroke.lowerBound, named.lowerBound)
        for delay in [").opacity(", "}.opacity(", ".animation(", ".transition(", "withAnimation"] {
            XCTAssertFalse(marks.contains(delay), "\(delay) in the result's marks")
        }
    }
}
