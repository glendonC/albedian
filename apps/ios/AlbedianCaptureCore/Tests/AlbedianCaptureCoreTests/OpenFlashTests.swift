import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// open-flash-m1 (DECIDED-BY-LEAD 24 Nov 18:32, from MOBILE's open-walk-m): Open showed the page's first paint, white
/// with the Safari view's bars white beside it, for about a third of a second before the dark reveal painted, a flash in
/// a dark-adapted eye at night. The sheet now
/// stays under the app's black until the Safari view's first load has ended, its touches passing through, and the cover
/// leaves at once. Read from the source (the app's views do not run here); the film of Open shows the rest.
final class OpenFlashTests: XCTestCase {
    private let screens = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture/Screens")

    private func flat(_ file: String) throws -> String {
        try String(contentsOf: screens.appendingPathComponent(file), encoding: .utf8).filter { !$0.isWhitespace }
    }

    func testTheSafariViewStaysUnderBlackUntilItsFirstLoadEnds() throws {
        let safari = try flat("SafariView.swift")
        XCTAssertTrue(safari.contains("letcontroller=SFSafariViewController(url:url)controller.delegate=context.coordinatorreturncontroller"),
                      "the Safari view tells the app how its load went")
        XCTAssertTrue(safari.contains("funcsafariViewController(_controller:SFSafariViewController,didCompleteInitialLoaddidLoadSuccessfully:Bool){onFirstLoad()}"),
                      "the first load's end, whether it loaded or not, lifts the cover")
        XCTAssertTrue(safari.contains("structCoveredSafariView:View{leturl:URL@Stateprivatevarloaded=false"))
        XCTAssertTrue(safari.contains("ZStack{SafariView(url:url,onFirstLoad:{loaded=true})if!loaded{Color.black.ignoresSafeArea().allowsHitTesting(false)}}"),
                      "black over the sheet until then, touches passing through to its close")
        XCTAssertFalse(safari.contains(".animation(") || safari.contains("withAnimation") || safari.contains(".transition("), "the cover leaves at once")
        let companion = try flat("CompanionScreens.swift")
        XCTAssertTrue(companion.contains(".sheet(item:$model.openPage){pageinCoveredSafariView(url:page.url).ignoresSafeArea()}"),
                      "every page Open or Credits shows comes covered")
        XCTAssertFalse(companion.contains("{pageinSafariView(url:"), "no uncovered Safari view left")
    }
}
