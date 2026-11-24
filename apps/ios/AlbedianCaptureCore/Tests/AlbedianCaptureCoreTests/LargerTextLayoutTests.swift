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
}
