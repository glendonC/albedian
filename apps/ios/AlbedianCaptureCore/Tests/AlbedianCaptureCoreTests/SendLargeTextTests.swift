import AlbedianCaptureCore
import Foundation
import XCTest

/// send-largetext-m (UIUX-iphone-firstuse-READY L1 and L2, DECIDED-BY-LEAD 27 Nov 12:39): after the shot the photo's
/// foot stands at least 16 pt above Send to laptop's top at every text size; where the wrapped pill would come closer,
/// the photo is drawn whole and smaller, its shape kept and centred, as tall as leaves those 16 pt, down to 160 pt; past
/// that the screen scrolls, the pill in the flow under the photo, never over it. Nothing moves while the pill leaves the
/// 16 pt (the default and first three accessibility sizes, 68 pt or more).
final class SendLargeTextTests: XCTestCase {
    private let app = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture")

    private func body(_ file: String, from start: String, to end: String) throws -> String {
        let text = try String(contentsOf: app.appendingPathComponent(file), encoding: .utf8)
        let code = try SourceReader.code(text, .swift)
        let from = try XCTUnwrap(code.range(of: start), start)
        let to = try XCTUnwrap(code.range(of: end, range: from.upperBound..<code.endIndex), end)
        return String(code[from.lowerBound..<to.lowerBound]).filter { !$0.isWhitespace }
    }

    func testTheRecordsNumbers() {
        XCTAssertEqual(SendFrame.gap, 16)
        XCTAssertEqual(SendFrame.floor, 160)
        XCTAssertEqual(SendFrame.top, 118)
    }

    func testNothingMovesWhileThePillLeavesSixteenPoints() {
        // MOBILE's measures on the iPhone 17 Pro (402 by 874): the photo's foot at 654, the pill's top 92, 81 and 68 pt under it
        for clear in [92.0, 81, 68, 16] {
            let frame = SendFrame.size(screenWidth: 402, pillTop: 654 + clear)
            XCTAssertEqual(frame.width, 402)
            XCTAssertEqual(frame.height, 536)
            XCTAssertFalse(SendFrame.scrolls(pillTop: 654 + clear))
        }
    }

    func testThePhotoGivesTheRoomWholeAndSmallerWhereThePillComesCloser() {
        // At the fourth size the wrapped pill's top came 6 pt under the foot, at the largest 4 pt onto the photo
        for clear in [6.0, -4] {
            let pillTop = 654 + clear
            let frame = SendFrame.size(screenWidth: 402, pillTop: pillTop)
            XCTAssertEqual(SendFrame.top + frame.height + SendFrame.gap, pillTop, accuracy: 1e-9, "the foot 16 pt above the pill")
            XCTAssertEqual(frame.width, frame.height * 3 / 4, accuracy: 1e-9, "its shape kept")
            XCTAssertLessThan(frame.width, 402)
        }
    }

    func testDownToTheFloorAndPastItTheScreenScrolls() {
        let atFloor = 118.0 + 160 + 16
        XCTAssertEqual(SendFrame.size(screenWidth: 402, pillTop: atFloor).height, 160, accuracy: 1e-9)
        XCTAssertFalse(SendFrame.scrolls(pillTop: atFloor))
        XCTAssertEqual(SendFrame.size(screenWidth: 402, pillTop: atFloor - 1).height, 160)
        XCTAssertEqual(SendFrame.size(screenWidth: 402, pillTop: atFloor - 1).width, 120)
        XCTAssertTrue(SendFrame.scrolls(pillTop: atFloor - 1))
    }

    /// Every width a phone gives and every pill top from far below the photo to the screen's top: never taller than the
    /// screen's 3:4, never under the floor, the shape kept; the 16 pt kept whenever the screen does not scroll; the
    /// whole frame whenever the pill leaves room for it.
    func testTheRuleHoldsOverEveryWidthAndPillTop() {
        for screenWidth in stride(from: 320.0, through: 440, by: 4) {
            let full = screenWidth * 4 / 3
            for pillTop in stride(from: 0.0, through: 1000, by: 1) {
                let frame = SendFrame.size(screenWidth: screenWidth, pillTop: pillTop)
                let scrolls = SendFrame.scrolls(pillTop: pillTop)
                XCTAssertLessThanOrEqual(frame.height, full + 1e-9)
                XCTAssertGreaterThanOrEqual(frame.height, 160 - 1e-9)
                XCTAssertEqual(frame.width, frame.height * 3 / 4, accuracy: 1e-9)
                if !scrolls { XCTAssertLessThanOrEqual(118 + frame.height + 16, pillTop + 1e-9) }
                XCTAssertEqual(scrolls, pillTop - 16 - 118 < 160 - 1e-9)
                if pillTop - 16 - 118 >= full { XCTAssertEqual(frame.height, full) }
            }
        }
    }

    func testTheScreenMeasuresThePillAndGivesThePhotoTheRoom() throws {
        let after = try body("Screens/CompanionScreens.swift", from: "struct AfterShotScreen: View", to: "private var unreachableColumn")
        // The pill measured as built, within the margins, hidden, before anything is drawn at accessibility sizes
        XCTAssertTrue(after.contains("@StateprivatevarpillHeight:Double?"))
        XCTAssertTrue(after.contains("sendPill.padding(.horizontal,CGFloat(ButtonGeometry.margin)).hidden().accessibilityHidden(true)"
            + ".onGeometryChange(for:Double.self){Double($0.size.height)}action:{pillHeight=$0}"))
        XCTAssertTrue(after.contains("letpillTop=Double(geometry.size.height)-Arrive.mainBottomGap-(pillHeight??0)"))
        XCTAssertTrue(after.contains("letsend=SendFrame.size(screenWidth:width,pillTop:pillTop)"))
        XCTAssertTrue(after.contains("letscrollsToSend=!model.unreachable&&SendFrame.scrolls(pillTop:pillTop)"))
        // Whole and smaller, centred, at the photo's top, nothing drawn at accessibility sizes before the pill is measured
        XCTAssertTrue(after.contains("ShotPhoto(shot:model.shot,width:model.unreachable?width:send.width).atTop(Arrive.photoTop)"
            + ".opacity(!model.unreachable&&dynamicTypeSize.isAccessibilitySize&&pillHeight==nil?0:1)"))
        // The pill as built, where it was, and Take again floating, only while the screen does not scroll: past the
        // floor both stand in the column's flow and its top bar, never twice
        XCTAssertTrue(after.contains("}elseif!model.unreachable&&arrived&&!scrollsToSend{"
            + "sendPill.atBottom(Arrive.mainBottomGap).transition(.words(reduceMotion:reduceMotion,after:MotionStyle.staggerSeconds))"))
        XCTAssertTrue(after.contains("ifarrived&&!stacked&&!scrollsToSend{IconButton(icon:.again,"))
        XCTAssertTrue(after.contains("privatevarsendPill:someView{MainButton(title:SendWords.mainButton,icon:.send,action:model.send)}"))
        // Past the floor: one scrolling column, the photo at the floor, the pill 16 pt under it in the flow
        XCTAssertTrue(after.contains("ifscrollsToSend{ScrollView{VStack(spacing:0){ShotPhoto(shot:model.shot,width:send.width)"
            + "sendPill.padding(.top,CGFloat(SendFrame.gap)).padding(.horizontal,CGFloat(ButtonGeometry.margin))}"
            + ".frame(maxWidth:.infinity).padding(.top,CGFloat(UnreachableFrame.top-Arrive.topBarFoot))"
            + ".padding(.bottom,CGFloat(Arrive.mainBottomGap))}.scrollBounceBehavior(.basedOnSize)"
            + ".scrollEdgeEffectStyle(.automatic,for:.top)"
            + ".safeAreaBar(edge:.top){TopBar{IconButton(icon:.again,label:,action:model.takeAgain)}}"))
    }
}
