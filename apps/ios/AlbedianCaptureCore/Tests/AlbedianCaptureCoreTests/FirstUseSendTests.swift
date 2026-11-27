import AlbedianCaptureCore
import Foundation
import XCTest

/// firstuse-send-one (UIUX-iphone-firstuse-READY item 2, DECIDED-BY-LEAD 26 Nov 23:58): Send says where it goes. The
/// main button after the shot reads "Send to laptop", and the location ask's sentence ends "only to Albedian on your
/// laptop", the term Connect uses.
final class FirstUseSendTests: XCTestCase {
    private let ios = repositoryRoot.appendingPathComponent("apps/ios")

    func testTheWordsAreTheRecords() {
        XCTAssertEqual(SendWords.mainButton, "Send to laptop")
        XCTAssertEqual(SendWords.locationReason,
                       "Albedian uses your location while you aim to find true north and the sky above you, and sends it only to Albedian on your laptop.")
    }

    func testTheSystemsLocationAskSaysTheRecordsSentence() throws {
        let data = try Data(contentsOf: ios.appendingPathComponent("AlbedianCapture-Info.plist"))
        let plist = try XCTUnwrap(PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        XCTAssertEqual(plist["NSLocationWhenInUseUsageDescription"] as? String, SendWords.locationReason)
    }

    func testTheMainButtonAfterTheShotSaysWhereTheSendGoes() throws {
        let text = try String(contentsOf: ios.appendingPathComponent("AlbedianCapture/Screens/CompanionScreens.swift"), encoding: .utf8)
        let code = try SourceReader.code(text, .swift)
        let start = try XCTUnwrap(code.range(of: "struct AfterShotScreen: View"))
        let end = try XCTUnwrap(code.range(of: "struct ConnectScreen: View"))
        let after = String(code[start.lowerBound..<end.lowerBound]).filter { !$0.isWhitespace }
        XCTAssertTrue(after.contains("MainButton(title:SendWords.mainButton,icon:.send,action:model.send)"))
        // "Try again" keeps its own word; the bare "Send" is gone from after the shot
        let raw = try XCTUnwrap(text.range(of: "struct AfterShotScreen: View").flatMap { from in
            text.range(of: "struct ConnectScreen: View").map { String(text[from.lowerBound..<$0.lowerBound]) } })
        XCTAssertFalse(raw.contains("MainButton(title: \"Send\""), "no bare Send after the shot")
        XCTAssertTrue(raw.contains("MainButton(title: \"Try again\", icon: .send, action: model.send)"))
    }
}
