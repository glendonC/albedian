import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The approved controls' numbers on the iPhone (`buttons-approved-2026-11-12.md` with its correction, `arrive/build.mjs`
/// and `packages/design-system/src/look.ts`), held here so a later change cannot drift from what the owner said yes to.
final class ControlStyleTests: XCTestCase {
    private func hex(_ colour: ControlStyle.Colour) -> String {
        String(format: "%02x%02x%02x", Int((colour.red * 255).rounded()), Int((colour.green * 255).rounded()), Int((colour.blue * 255).rounded()))
    }

    func testHasTheApprovedFieldInkNightLineWashAndInkLevels() {
        XCTAssertEqual(hex(ControlStyle.field), "000000")
        XCTAssertEqual(ControlStyle.field.opacity, 1)
        XCTAssertEqual(hex(ControlStyle.ink), "f4f3ef")
        XCTAssertEqual(ControlStyle.ink.opacity, 1)
        XCTAssertEqual(hex(ControlStyle.night), "0b0b0b")
        XCTAssertEqual(ControlStyle.night.opacity, 1)
        XCTAssertEqual(hex(ControlStyle.line), "f4f3ef")
        XCTAssertEqual(ControlStyle.line.opacity, 0.88)
        XCTAssertEqual(hex(ControlStyle.wash), "f4f3ef")
        XCTAssertEqual(ControlStyle.wash.opacity, 0.10)
        XCTAssertEqual(hex(ControlStyle.inkSecondary), "f4f3ef")
        XCTAssertEqual(ControlStyle.inkSecondary.opacity, 0.66)
        XCTAssertEqual(hex(ControlStyle.inkQuiet), "f4f3ef")
        XCTAssertEqual(ControlStyle.inkQuiet.opacity, 0.44)
        XCTAssertEqual(hex(ControlStyle.hairline), "f4f3ef")
        XCTAssertEqual(ControlStyle.hairline.opacity, 0.14)
    }

    func testDrawsTheMainButtonAsTheSplitCompactActionAt44AndTheIconButtonAt44() {
        // The correction of 12 Nov and arrive/build.mjs (.ab-split.ab-sz-m): 44 pt high, as wide as the word, never
        // full width or 56 pt; a filled pill (padding 0 16) with an outlined 44 pt circle touching beside it that
        // springs into the pill when pressed (trailing padding grows to 60 while the circle moves left by 44, so total
        // width never changes).
        XCTAssertEqual(ControlStyle.minTouchTarget, 44)
        XCTAssertEqual(ControlStyle.mainHeight, 44)
        XCTAssertGreaterThanOrEqual(ControlStyle.mainHeight, ControlStyle.minTouchTarget)
        XCTAssertEqual(ControlStyle.mainFontSize, 16)
        XCTAssertEqual(ControlStyle.iconSize, 17)
        XCTAssertEqual(ControlStyle.mainPillPadding, 16)
        XCTAssertEqual(ControlStyle.mainPillDockedTrailingPadding, 60)
        XCTAssertEqual(ControlStyle.mainCircleSide, 44)
        // Total width beside the word at rest (leading 16 + trailing 16 + 44 circle = 76) equals pressed (leading 16 + trailing 60 - 44 + 44 = 76)
        let restExtraWidth = 2 * ControlStyle.mainPillPadding + ControlStyle.mainCircleSide
        let pressedExtraWidth = ControlStyle.mainPillPadding + ControlStyle.mainPillDockedTrailingPadding - ControlStyle.mainCircleSide + ControlStyle.mainCircleSide
        XCTAssertEqual(restExtraWidth, 76)
        XCTAssertEqual(pressedExtraWidth, restExtraWidth)
        XCTAssertEqual(ControlStyle.iconButtonSide, 44)
        XCTAssertGreaterThanOrEqual(ControlStyle.iconButtonSide, ControlStyle.minTouchTarget)
        XCTAssertEqual(ControlStyle.outlineWidth, 1)
        XCTAssertEqual(ControlStyle.letterSpacingEm, -0.01)
        XCTAssertEqual(ControlStyle.logoLetterSpacingEm, -0.048)
        XCTAssertEqual(ControlStyle.focusWidth, 2)
        XCTAssertEqual(ControlStyle.focusOffset, 2)
    }

    func testWritesTheNameAsTheWebsitesLogo() {
        XCTAssertEqual(hex(ControlStyle.logoInk), "ffffff")
        XCTAssertEqual(ControlStyle.logoInk.opacity, 1)
        XCTAssertEqual(ControlStyle.logoWord, "albedian")
        XCTAssertEqual(ControlStyle.logoMarkScale, 0.857)
        XCTAssertEqual(ControlStyle.logoMarkLeft, -0.048)
        XCTAssertEqual(ControlStyle.logoMarkUp, -0.238)
        XCTAssertEqual(ControlStyle.logoDefaultSize, 21)
        // The mark is the shutter's own drawing: the moon's centre and the star are the logo's numbers
        XCTAssertTrue(ShutterGeometry.moonCentreInLogo == (15.2, 16.0))
    }

    func testMovesOnTheApprovedPressColourAndSpringTimings() {
        XCTAssertEqual(ControlStyle.pressScale, 0.97)
        XCTAssertEqual(ControlStyle.pressSeconds, 0.15)
        XCTAssertEqual(ControlStyle.colourSeconds, 0.2)
        XCTAssertTrue(ControlStyle.standardCurve == (0.4, 0.0, 0.2, 1.0))
        XCTAssertEqual(ControlStyle.springSeconds, 1.2)
        XCTAssertTrue(ControlStyle.springCurve == (0.175, 1.4, 0.32, 1.0))
        XCTAssertTrue(ControlStyle.springCurve == ShutterGeometry.curve, "one spring for every control")
        XCTAssertEqual(ControlStyle.arrowRestDegrees, -45)
    }

    func testTagsAllFourEvidenceClassesWithTheirApprovedWordColourAndTint() {
        XCTAssertEqual(ControlStyle.tagHeight, 28)
        XCTAssertEqual(ControlStyle.tagLeadingPadding, 9)
        XCTAssertEqual(ControlStyle.tagTrailingPadding, 12)
        XCTAssertEqual(ControlStyle.tagGap, 7)
        XCTAssertEqual(ControlStyle.tagFontSize, 13)
        XCTAssertEqual(ControlStyle.tagLetterSpacingEm, -0.005)
        XCTAssertEqual(ControlStyle.tagGlyphSize, 14)
        let expected: [(ControlStyle.Evidence, String, String, String, Double)] = [
            (.observed, "Observed", "f4f3ef", "f4f3ef", 0.10),
            (.catalogMatched, "Catalog matched", "c9daff", "c9daff", 0.14),
            (.predicted, "Predicted", "f5b83d", "f5b83d", 0.14),
            (.simulated, "Simulated", "a78bfa", "a78bfa", 0.15),
        ]
        XCTAssertEqual(ControlStyle.Evidence.allCases, expected.map(\.0))
        for (evidence, word, colour, tint, opacity) in expected {
            XCTAssertEqual(evidence.word, word)
            XCTAssertEqual(hex(evidence.colour), colour, word)
            XCTAssertEqual(evidence.colour.opacity, 1, word)
            XCTAssertEqual(hex(evidence.tint), tint, word)
            XCTAssertEqual(evidence.tint.opacity, opacity, word)
        }
    }
}
