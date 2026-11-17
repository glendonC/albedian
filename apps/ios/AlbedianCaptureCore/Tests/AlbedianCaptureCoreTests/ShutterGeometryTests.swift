import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The capture button is the approved one (mobile-2's second version): its numbers, held here, so a later change of
/// the logo or the drawing cannot shrink or move it.
final class ShutterGeometryTests: XCTestCase {
    private func close(_ a: Double, _ b: Double, _ what: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(a, b, accuracy: 1e-9, what, file: file, line: line)
    }

    func testHasTheApprovedRingAndADiscOf52PointsAtItsCentre() {
        XCTAssertEqual(ShutterGeometry.side, 64)
        XCTAssertEqual(ShutterGeometry.ringRadius, 31)
        XCTAssertEqual(ShutterGeometry.ringLineWidth, 1.5)
        XCTAssertEqual(ShutterGeometry.ringOpacity, 0.55)
        XCTAssertEqual(ShutterGeometry.discRadius * 2, 52)
        // The ring's outer edge fits the button, and the gap between ring and disc is 4.25 pt
        close(ShutterGeometry.ringRadius + ShutterGeometry.ringLineWidth / 2, 31.75, "ring's outer edge")
        close(ShutterGeometry.ringRadius - ShutterGeometry.ringLineWidth / 2 - ShutterGeometry.discRadius, 4.25, "gap")
        // The logo moon's centre is the disc's centre, the button's
        let moon = ShutterGeometry.fromLogo(x: 15.2, y: 16)
        close(moon.x, 0, "moon x"); close(moon.y, 0, "moon y")
    }

    func testCutsTheLogosStarScaled226TimesAboutTheMoonsCentre() {
        XCTAssertEqual(ShutterGeometry.starScale, 2.26)
        // The logo's own numbers (apps/web's 32 unit drawing), held exactly
        XCTAssertTrue(ShutterGeometry.moonCentreInLogo == (15.2, 16.0))
        XCTAssertTrue(ShutterGeometry.starCentreInLogo == (18.2, 13.8))
        XCTAssertEqual(ShutterGeometry.starPointsInLogo.map(\.x), [18.2, 29.8, 18.2, 6.6])
        XCTAssertEqual(ShutterGeometry.starPointsInLogo.map(\.y), [2.2, 13.8, 25.4, 13.8])
        let centre = ShutterGeometry.starCentre
        close(centre.x, 3 * 2.26, "star centre x")
        close(centre.y, -2.2 * 2.26, "star centre y")
        let points = ShutterGeometry.starPoints(turnedBy: 0)
        let expected = [(18.2, 2.2), (29.8, 13.8), (18.2, 25.4), (6.6, 13.8)].map { ((($0.0 - 15.2) * 2.26), (($0.1 - 16) * 2.26)) }
        XCTAssertEqual(points.count, 4)
        for (point, want) in zip(points, expected) { close(point.x, want.0, "x"); close(point.y, want.1, "y") }
        // The star's points reach 11.6 logo units, 26.216 pt, from its centre: past the disc's edge on three sides
        for point in points { close(hypot(point.x - centre.x, point.y - centre.y), 11.6 * 2.26, "reach") }
    }

    func testTurnsTheStar45DegreesAboutItsOwnCentreAndPressesTheDiscTo90Percent() {
        XCTAssertEqual(ShutterGeometry.pressedStarTurnDegrees, 45)
        XCTAssertEqual(ShutterGeometry.pressedDiscScale, 0.9)
        XCTAssertEqual(ShutterGeometry.discSeconds, 0.5)
        XCTAssertEqual(ShutterGeometry.starSeconds, 1.2)
        XCTAssertTrue(ShutterGeometry.curve == (0.175, 1.4, 0.32, 1.0))
        let centre = ShutterGeometry.starCentre
        let still = ShutterGeometry.starPoints(turnedBy: 0)
        let turned = ShutterGeometry.starPoints(turnedBy: 45)
        for (a, b) in zip(still, turned) {
            close(hypot(a.x - centre.x, a.y - centre.y), hypot(b.x - centre.x, b.y - centre.y), "turned about its centre")
            let angle = atan2(b.y - centre.y, b.x - centre.x) - atan2(a.y - centre.y, a.x - centre.x)
            close(remainder(angle, 2 * .pi), .pi / 4, "45 degrees clockwise on the screen")
        }
    }
}
