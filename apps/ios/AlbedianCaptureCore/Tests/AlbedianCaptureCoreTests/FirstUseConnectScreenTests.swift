import AlbedianCaptureCore
import CoreText
import Foundation
import XCTest

/// firstuse-connect-one, the screens (UIUX-iphone-firstuse-READY item 1): on Aim with no laptop kept, "Connect laptop"
/// in the Credits pill's own form at the top right, opening Connect; on Connect the command that starts the laptop in a
/// box, in Expo Go's empty state's order, where the line about the address used to be; from Aim, Connect keeps the
/// address only on the health route's answer and returns to Aim, and Back returns to Aim unchanged.
final class FirstUseConnectScreenTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")

    private func split(_ file: String) throws -> (code: String, literals: String) {
        let text = try String(contentsOf: app.appendingPathComponent(file), encoding: .utf8)
        return try SourceReader.split(text, .swift)
    }

    /// The code from one declaration to the next given one, without white space.
    private func body(_ file: String, from start: String, to end: String) throws -> String {
        let code = try split(file).code
        let from = try XCTUnwrap(code.range(of: start), start)
        let to = try XCTUnwrap(code.range(of: end, range: from.upperBound..<code.endIndex), end)
        return String(code[from.lowerBound..<to.lowerBound]).filter { !$0.isWhitespace }
    }

    func testConnectsLayoutIsTheRecordsNumbers() {
        XCTAssertEqual(ConnectLayout.startTop, 238)
        XCTAssertEqual(ConnectLayout.boxTop, 238 + 14 * 1.2 + 12, accuracy: 1e-9)
        XCTAssertEqual(ConnectLayout.boxHeight, 40)
        XCTAssertEqual(ConnectLayout.boxRadius, 8)
        XCTAssertEqual(ConnectLayout.boxInset, 12)
        XCTAssertEqual(ConnectLayout.addressTop, ConnectLayout.boxTop + 40 + 16, accuracy: 1e-9)
        XCTAssertEqual(ConnectLayout.fieldTop, ConnectLayout.addressTop + 24, accuracy: 1e-9)
        XCTAssertEqual(ConnectLayout.messageTop, ConnectLayout.fieldTop + 60 + 14, accuracy: 1e-9)
        XCTAssertEqual(ConnectLayout.connectTop.rounded(), 469, "Connect 469 to 513 on the 844 pt frame")
    }

    func testAimShowsConnectLaptopInTheCreditsPillsFormOnlyWhileNoLaptopIsKept() throws {
        let aim = try body("Screens/CompanionScreens.swift", from: "struct AimScreen: View", to: "private struct AimLinesColumn")
        XCTAssertTrue(aim.contains("if!model.hasKeptLaptop{OutlinePill(title:ConnectWords.pill,height:36,fontSize:15,action:model.connectFromAim)"
            + ".dynamicTypeSize(...DynamicTypeSize.accessibility1).accessibilityShowsLargeContentViewer()"
            + ".frame(maxWidth:.infinity,maxHeight:.infinity,alignment:.topTrailing)"
            + ".padding(.top,CGFloat(Arrive.topControls)).padding(.trailing,CGFloat(Arrive.edge))"
            + ".opacity(AimExit.opacity(.rest,hidden:hidden))}"), "the Credits pill's 36 pt, Geist 15, top right opposite Close")
    }

    func testConnectNamesTheCommandInABoxWhereTheAddressLineWas() throws {
        let connect = try body("Screens/CompanionScreens.swift", from: "struct ConnectScreen: View", to: "struct ResultScreen: View")
        XCTAssertTrue(connect.contains("Text(ConnectWords.start).font(AlbedianFont.regular(14)).tracking(-0.005*14).lineSpacing(14*0.2)"
            + ".foregroundStyle(Color(ControlStyle.inkSecondary))"), "the replaced line's style")
        XCTAssertTrue(connect.contains("Text(ConnectWords.command).font(AlbedianFont.mono(16)).tracking(CGFloat(ConnectLayout.commandTracking))"
            + ".foregroundStyle(Color(ControlStyle.ink)).textSelection(.enabled)"), "Geist Mono 16 in ink, every character drawn, selectable for the system's Copy")
        XCTAssertTrue(connect.contains(".padding(.horizontal,CGFloat(ConnectLayout.boxInset)).frame(maxWidth:.infinity,minHeight:CGFloat(ConnectLayout.boxHeight),alignment:.leading)"
            + ".background(RoundedRectangle(cornerRadius:CGFloat(ConnectLayout.boxRadius)).fill(Color(ControlStyle.wash)))"))
        XCTAssertTrue(connect.contains(".atTop(ConnectLayout.startTop,alignment:.leading)") && connect.contains(".atTop(ConnectLayout.boxTop)")
            && connect.contains(".atTop(ConnectLayout.addressTop,alignment:.leading)") && connect.contains(".atTop(ConnectLayout.fieldTop)")
            && connect.contains(".atTop(ConnectLayout.messageTop)") && connect.contains(".atTop(ConnectLayout.connectTop)"), "at default sizes as recorded")
        let literals = try split("Screens/CompanionScreens.swift").literals
        XCTAssertFalse(literals.contains("Albedian shows this address on the laptop when it starts."), "the replaced line is gone")
        XCTAssertTrue(literals.contains("Type the address Albedian shows on the laptop."), "the refused line stays")
    }

    func testFromAimConnectKeepsTheAddressOnlyOnTheHealthAnswerAndBackReturnsToAim() throws {
        let model = try split("Screens/CompanionModel.swift").code.filter { !$0.isWhitespace }
        // Observed, so Aim draws the pill away the moment an address is kept; every keep goes through keepLaptop
        XCTAssertTrue(model.contains("private(set)varhasKeptLaptop=false"))
        XCTAssertTrue(model.contains("hasKeptLaptop=pairing.kept!=nil"))
        XCTAssertTrue(model.contains("privatefunckeepLaptop(_address:LaptopAddress){pairing.keep(address)hasKeptLaptop=true}"))
        XCTAssertEqual(model.components(separatedBy: "pairing.keep(").count - 1, 1, "no address kept outside keepLaptop")
        XCTAssertTrue(model.contains("funcconnectFromAim(){connectingFromAim=trueaddressRefused=falsescreen=.pairing}"))
        XCTAssertTrue(model.contains("funcback(){ifconnectingFromAim{connectingFromAim=falsescreen=.aimreturn}screen=shot==nil?.home:.afterShot}"))
        // The typed address is asked once, and kept, used for the sky and Aim shown again only when it answered as Albedian
        XCTAssertTrue(model.contains("ifconnectingFromAim{returncheckFromAim(address)}"))
        XCTAssertTrue(model.contains("letisAlbedian=awaitLaptopHealthClient(address:address).isAlbedian()"))
        XCTAssertTrue(model.contains("guardisAlbedianelse{self.addressRefused=truereturn}self.keepLaptop(address)self.connectingFromAim=falseself.screen=.aimself.startSky()"))
    }

    /// Geist Mono's contextual alternates join the space before "--" when the command is set as is (15 glyphs for its 16
    /// characters, "field--lan" on the screen); the command's hundredth of a point of tracking turns them off, so every
    /// character is a glyph of its own and the command reads as it is typed.
    func testTheCommandKeepsEveryCharacterAsAGlyphOfItsOwn() throws {
        let data = try Data(contentsOf: app.appendingPathComponent("Fonts/GeistMono-wght.woff2")) as CFData
        let descriptors = try XCTUnwrap(CTFontManagerCreateFontDescriptorsFromData(data) as? [CTFontDescriptor])
        let regular = try XCTUnwrap(descriptors.first { (CTFontDescriptorCopyAttribute($0, kCTFontNameAttribute) as? String) == "GeistMono-Regular" })
        let font = CTFontCreateWithFontDescriptor(regular, 16, nil)
        func glyphs(tracking: Double?) -> Int {
            var attributes: [NSAttributedString.Key: Any] = [NSAttributedString.Key(kCTFontAttributeName as String): font]
            if let tracking { attributes[NSAttributedString.Key(kCTTrackingAttributeName as String)] = tracking }
            return CTLineGetGlyphCount(CTLineCreateWithAttributedString(NSAttributedString(string: ConnectWords.command, attributes: attributes)))
        }
        XCTAssertEqual(glyphs(tracking: nil), ConnectWords.command.count - 1, "as is, two characters share a glyph")
        XCTAssertEqual(glyphs(tracking: ConnectLayout.commandTracking), ConnectWords.command.count)
        XCTAssertGreaterThan(ConnectLayout.commandTracking, 0)
        XCTAssertLessThanOrEqual(ConnectLayout.commandTracking, 0.01, "no tracking a person could see")
    }

    func testGeistMonoIsTheWebsOwnBytesRegisteredByItsInstanceName() throws {
        let mono = try Data(contentsOf: app.appendingPathComponent("Fonts/GeistMono-wght.woff2"))
        XCTAssertEqual(mono, try Data(contentsOf: repositoryRoot.appendingPathComponent("apps/web/public/fonts/geist/GeistMono-wght.woff2")))
        let style = try split("UI/Style.swift").code.filter { !$0.isWhitespace }
        XCTAssertTrue(style.contains("staticfuncmono(_size:Double)->Font{.custom(,size:size,relativeTo:.body)}"))
        XCTAssertTrue(try split("UI/Style.swift").literals.contains("GeistMono-Regular"))
        let record = try JSONSerialization.jsonObject(with: Data(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/notices/geist/PROVENANCE.json"))) as? [String: Any]
        let files = try XCTUnwrap(record?["files"] as? [[String: Any]])
        let entry = try XCTUnwrap(files.first { $0["path"] as? String == "../../AlbedianCapture/Fonts/GeistMono-wght.woff2" })
        XCTAssertEqual(entry["sha256"] as? String, "afaacc4c5fbba89d2ebf7a02dc4070208540874592a5504d57175782fe893101")
        XCTAssertEqual((entry["producedBy"] as? [String: Any])?["method"] as? String, "copied-from-repository")
    }
}
