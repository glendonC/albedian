import XCTest
@testable import AlbedianCaptureCore

/// The iPhone's in-screen motion, as MOTION-READY-iphone section 2 states each timing (decided by the lead).
final class MotionStyleTests: XCTestCase {
    private func curve(_ t: MotionStyle.Timing) -> [Double] { [t.x1, t.y1, t.x2, t.y2, t.seconds, t.delaySeconds] }

    func testEachNamedTimingIsTheNotesOwn() {
        XCTAssertEqual(curve(MotionStyle.spring), [0.175, 1.4, 0.32, 1, 1.2, 0])
        XCTAssertEqual(curve(MotionStyle.disc), [0.175, 1.4, 0.32, 1, 0.5, 0])
        XCTAssertEqual(curve(MotionStyle.colour), [0.4, 0, 0.2, 1, 0.2, 0])
        XCTAssertEqual(curve(MotionStyle.wordsIn), [0.4, 0, 0.2, 1, 0.3, 0.12])
        XCTAssertEqual(curve(MotionStyle.wordsOut), [0.4, 0, 0.2, 1, 0.1, 0])
        XCTAssertEqual(curve(MotionStyle.frameIn), [0.16, 1, 0.3, 1, 0.4, 0])
        XCTAssertEqual(curve(MotionStyle.frameOut), [0.4, 0, 1, 1, 0.3, 0])
        XCTAssertEqual(MotionStyle.wordsOffsetPt, 6)
        XCTAssertEqual(MotionStyle.staggerSeconds, 0.02)
        XCTAssertEqual(MotionStyle.reduceMotionSeconds, 0.27)
        XCTAssertEqual(MotionStyle.captureSequenceDelaySeconds, 0.15)
    }

    func testTheApprovedControlsKeepTheirTimings() {
        // The spring and the standard curve the built controls already use are the same numbers
        XCTAssertEqual(MotionStyle.spring.seconds, ControlStyle.springSeconds)
        XCTAssertEqual([MotionStyle.spring.x1, MotionStyle.spring.y1, MotionStyle.spring.x2, MotionStyle.spring.y2],
                       [ControlStyle.springCurve.0, ControlStyle.springCurve.1, ControlStyle.springCurve.2, ControlStyle.springCurve.3])
        XCTAssertEqual(MotionStyle.colour.seconds, ControlStyle.colourSeconds)
    }

    func testLinesArriveInOrderAndLeaveLastToFirst() {
        XCTAssertEqual((0..<3).map { MotionStyle.staggerDelay(index: $0, count: 3, leaving: false) }, [0, 0.02, 0.04])
        XCTAssertEqual((0..<3).map { MotionStyle.staggerDelay(index: $0, count: 3, leaving: true) }, [0.04, 0.02, 0])
        XCTAssertEqual(MotionStyle.staggerDelay(index: 3, count: 3, leaving: false), 0)
        XCTAssertEqual(MotionStyle.staggerDelay(index: 0, count: 0, leaving: false), 0)
    }
}

/// The navigation path the companion's screens make (MOTION-READY-iphone section 1): forward a push, back a pop.
final class CompanionPlaceTests: XCTestCase {
    func testForwardScreensArePushedAndGoingBackPops() {
        var path: [CompanionPlace] = []
        path = CompanionPlace.path(path, to: .capture)
        XCTAssertEqual(path, [.capture], "Start: aiming pushed")
        XCTAssertEqual(CompanionPlace.path(path, to: .capture), [.capture], "hold still and after the shot stay in the screen")
        path = CompanionPlace.path(path, to: .pairing)
        XCTAssertEqual(path, [.capture, .pairing], "Send the first time: Connect pushed")
        path = CompanionPlace.path(path, to: .capture)
        XCTAssertEqual(path, [.capture], "back to after the shot: a pop")
        path = CompanionPlace.path(path, to: .sent)
        XCTAssertEqual(path, [.capture, .sent], "Send: the wait pushed")
        path = CompanionPlace.path(path, to: .result)
        XCTAssertEqual(path, [.capture, .sent, .result], "the result pushed on the wait")
        XCTAssertEqual(CompanionPlace.path(path, to: .sent), [.capture, .sent], "Try again on a stopped job: back to the wait")
        XCTAssertEqual(CompanionPlace.path(path, to: .capture), [.capture], "Take again: back to aiming, two pops")
        XCTAssertEqual(CompanionPlace.path(path, to: nil), [], "close: home")
        XCTAssertEqual(CompanionPlace.path([], to: .credits), [.credits])
        XCTAssertEqual(CompanionPlace.path([.kept], to: .pairing), [.kept, .pairing])
        XCTAssertEqual(CompanionPlace.path([.kept, .pairing], to: .kept), [.kept])
    }
}
