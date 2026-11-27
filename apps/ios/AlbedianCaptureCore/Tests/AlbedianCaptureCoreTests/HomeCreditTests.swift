import AlbedianCaptureCore
import Foundation
import XCTest

/// home-credit-m (UIUX-homecredit-READY, DECIDED-BY-LEAD 27 Nov 00:21): the home's Milky Way carries ESO's credit on the
/// image itself, as ESO's terms ask that it not be hidden or parted from the picture: one plain line, "Milky Way:
/// ESO/S. Brunier" (the app's name for the picture, then ESO's credit unaltered), Geist 12 at ink 66 percent, centred
/// 16 pt under Start; not a link on the phone, whose Credits row holds the link.
final class HomeCreditTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")

    func testTheLineIsThePicturesNameAndESOsCreditUnaltered() throws {
        let data = try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/notices/eso/PROVENANCE.json"))
        let root = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let eso = try XCTUnwrap((root["sources"] as? [[String: Any]])?.first { $0["id"] as? String == "eso0932a" })
        let credit = try XCTUnwrap(eso["credit"] as? String)
        XCTAssertEqual(credit, "ESO/S. Brunier")
        XCTAssertEqual(HomeCredit.line, "Milky Way: " + credit)
        XCTAssertEqual(HomeCredit.size, 12)
        XCTAssertEqual(HomeCredit.gapUnderStart, 16)
        XCTAssertEqual(HomeCredit.boxHeight, 16)
    }

    /// The home's code, comments and literals emptied, without white space.
    private func home() throws -> String {
        let text = try String(contentsOf: app.appendingPathComponent("Screens/CompanionScreens.swift"), encoding: .utf8)
        let code = try SourceReader.code(text, .swift)
        let start = try XCTUnwrap(code.range(of: "struct HomeScreen: View"))
        let end = try XCTUnwrap(code.range(of: "struct CreditsScreen: View"))
        return String(code[start.lowerBound..<end.lowerBound]).filter { !$0.isWhitespace }
    }

    func testTheHomeDrawsTheLinePlainUnderStartAndClearOfTheHomeIndicator() throws {
        let home = try home()
        let line = "Text(HomeCredit.line).font(AlbedianFont.regular(HomeCredit.size)).tracking(-0.005*HomeCredit.size)"
            + ".foregroundStyle(Color(ControlStyle.inkSecondary)).lineLimit(1)"
        XCTAssertTrue(home.contains(line), "one line of Geist 12 at ink 66 percent")
        XCTAssertTrue(home.contains(".frame(height:CGFloat(HomeCredit.boxHeight)).atBottom(Arrive.mainBottomGap-HomeCredit.gapUnderStart-HomeCredit.boxHeight)"),
                      "its 16 pt box 16 pt under Start's foot")
        // INTERIM: held at the first accessibility size, as the home's top pills, so it never reaches Start
        XCTAssertTrue(home.contains(".lineLimit(1).dynamicTypeSize(...DynamicTypeSize.accessibility1)"))
        // Plain text: no button or link on the phone, and drawn after the picture and its shade, so it stays put
        let credit = try XCTUnwrap(home.range(of: "Text(HomeCredit.line)"))
        let start = try XCTUnwrap(home.range(of: "MainButton(title:,icon:.arrow,action:model.startAiming)"))
        let picture = try XCTUnwrap(home.range(of: "TiltingBackdrop(").map(\.lowerBound))
        XCTAssertLessThan(picture, credit.lowerBound)
        XCTAssertLessThan(start.lowerBound, credit.lowerBound)
        XCTAssertFalse(home[credit.lowerBound...].prefix(400).contains("Button"))
        XCTAssertFalse(home.contains("openCredit("), "the Credits row keeps the link")
    }
}
