import AlbedianCaptureCore
import XCTest

/// The aim view's exit at the press and its entry (the lead, 21 Nov 13:48, as AUDIT-C's item 5 ruled, with the 17:38
/// rule in MOTION-READY-iphone): the rings and the rest of the aim view fade on the stage's own timing, while the
/// Predicted tag and the source line stand at full from the first frame to the rings' last, so they are removed at once.
final class AimExitTests: XCTestCase {
    func testTheTagAndTheSourceLineStayAtFullWhileTheRingsFade() {
        for hidden in [0, 0.25, 0.5, 0.999, 1] {
            XCTAssertEqual(AimExit.opacity(.tag, hidden: hidden), 1, "the tag at \(hidden)")
            XCTAssertEqual(AimExit.opacity(.sourceLine, hidden: hidden), 1, "the source line at \(hidden)")
        }
    }

    func testTheRingsAndTheRestFadeOnTheStagesTiming() {
        XCTAssertEqual(AimExit.opacity(.rings, hidden: 0), 1)
        XCTAssertEqual(AimExit.opacity(.rings, hidden: 0.25), 0.75, accuracy: 1e-12)
        XCTAssertEqual(AimExit.opacity(.rings, hidden: 1), 0, "the rings' last frame: the stage then removes the view")
        XCTAssertEqual(AimExit.opacity(.rest, hidden: 0.6), 0.4, accuracy: 1e-12)
        XCTAssertEqual(AimExit.opacity(.rest, hidden: 1), 0)
    }

    /// The lead, 22 Nov 22:17: Hold still and the length word in only once the tag and the source line have gone, at
    /// the rings' last frame: frame out (0.3 s) 0.15 s after the press, or the 0.27 s dissolve under Reduce Motion.
    func testHoldStillsWordsWaitUntilTheTagAndTheSourceLineHaveGone() {
        XCTAssertEqual(AimExit.leftAfterSeconds(reduceMotion: false), 0.45, accuracy: 1e-12)
        XCTAssertEqual(AimExit.leftAfterSeconds(reduceMotion: true), 0.42, accuracy: 1e-12)
        XCTAssertEqual(AimExit.leftAfterSeconds(reduceMotion: false),
                       MotionStyle.captureSequenceDelaySeconds + MotionStyle.frameOut.delaySeconds + MotionStyle.frameOut.seconds,
                       accuracy: 1e-12, "the aim view's own leaving, as the stage animates it")
        XCTAssertGreaterThan(AimExit.leftAfterSeconds(reduceMotion: true), MotionStyle.captureSequenceDelaySeconds)
    }

    func testAnOvershootingCurveNeverPushesAnOpacityOutOfRange() {
        XCTAssertEqual(AimExit.opacity(.rings, hidden: 1.08), 0)
        XCTAssertEqual(AimExit.opacity(.rings, hidden: -0.05), 1)
        XCTAssertEqual(AimExit.opacity(.tag, hidden: 1.08), 1)
    }
}
