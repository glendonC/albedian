import AlbedianCaptureCore
import Foundation
import XCTest

/// The capture coach's lines (D29): their words, order and limits, and the steadiness test from the phone's motion.
final class CoachTests: XCTestCase {
    private func sky(moonAlt: Double, veil: Double, lit: Double = 0.7) throws -> SkyConditions {
        let reply: JSONValue = try XCTUnwrap(ContractFixture.load("sky_context_contract")["bases"]?["reply"])
        var copy = reply
        var block = copy["predictedSkyBrightness"]!.objectValue!
        block.set("moonAltDeg", .number(JSONNumber(moonAlt)!))
        block.set("lunarVeilDeltaMag", .number(JSONNumber(veil)!))
        block.set("illuminationFraction", .number(JSONNumber(lit)!))
        copy["predictedSkyBrightness"] = .object(block)
        return try XCTUnwrap(SkyConditions.read(copy))
    }

    func testTheMoonsThreeLinesAndNoneWithoutTheSkyContext() throws {
        XCTAssertEqual(Coach.moonWords(try sky(moonAlt: 24.1, veil: 1.35, lit: 0.7)), "The Moon is up, 70 percent lit: fewer stars show.")
        XCTAssertEqual(Coach.moonWords(try sky(moonAlt: 0.1, veil: 0.2, lit: 0.004)), "The Moon is up, 0 percent lit: fewer stars show.")
        XCTAssertEqual(Coach.moonWords(try sky(moonAlt: 5, veil: 0.2, lit: 0.995)), "The Moon is up, 100 percent lit: fewer stars show.")
        XCTAssertEqual(Coach.moonWords(try sky(moonAlt: 10, veil: 0)), "The Moon is up but adds no light to the sky.")
        XCTAssertEqual(Coach.moonWords(try sky(moonAlt: 0, veil: 0.5)), "The Moon is down.")
        XCTAssertEqual(Coach.moonWords(try sky(moonAlt: -32.5, veil: 0)), "The Moon is down.")
        XCTAssertNil(Coach.moonWords(nil))
    }

    func testTheOrderIsCompassMoonSteadinessAtMostTwoAndTheCompassTakesSteadinesssPlace() throws {
        let up = try sky(moonAlt: 20, veil: 1)
        let moon = CoachLine.moon("The Moon is up, 70 percent lit: fewer stars show.")
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: up, moving: true), [moon, .steadiness])
        XCTAssertEqual(Coach.lines(compassAsked: true, conditions: up, moving: true), [.compass, moon])
        XCTAssertEqual(Coach.lines(compassAsked: true, conditions: nil, moving: true), [.compass])
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: nil, moving: true), [.steadiness])
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: up, moving: false), [moon])
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: nil, moving: false), [])
        XCTAssertEqual(CoachLine.compass.words, "Move the phone in a figure eight to settle the compass.")
        XCTAssertEqual(CoachLine.steadiness.words, "The phone is moving: rest it on something steady.")
        XCTAssertEqual([CoachLine.compass.icon, moon.icon, CoachLine.steadiness.icon], ["compass", "moon", "hand"])
    }

    func testTheSmearIsTheRatesSizeTimesTheExposureTimesTheFocalLength() throws {
        // 0.001 rad/s about the axis (3, 4, 0) times 1 s times 3000 px
        let rate = PointingRecord.Vector3(x: 0.0006, y: 0.0008, z: 0)
        XCTAssertEqual(try XCTUnwrap(CoachSteadiness.smearPx(rotationRateRadPerSec: rate, exposureSec: 1, focalPx: 3000)), 3, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(CoachSteadiness.smearPx(rotationRateRadPerSec: PointingRecord.Vector3(x: 0, y: 0, z: -0.002),
                                                            exposureSec: 0.5, focalPx: 1000)), 1, accuracy: 1e-12)
        XCTAssertNil(CoachSteadiness.smearPx(rotationRateRadPerSec: rate, exposureSec: 0, focalPx: 3000))
        XCTAssertNil(CoachSteadiness.smearPx(rotationRateRadPerSec: rate, exposureSec: 1, focalPx: 0))
        XCTAssertNil(CoachSteadiness.smearPx(rotationRateRadPerSec: PointingRecord.Vector3(x: .nan, y: 0, z: 0), exposureSec: 1, focalPx: 1))
        // A 90 degree field across 4032 px: 2016 px
        XCTAssertEqual(try XCTUnwrap(CoachSteadiness.focalPx(longSidePx: 4032, longSideFovDeg: 90)), 2016, accuracy: 1e-9)
        XCTAssertNil(CoachSteadiness.focalPx(longSidePx: 4032, longSideFovDeg: 180))
        XCTAssertNil(CoachSteadiness.focalPx(longSidePx: 0, longSideFovDeg: 60))
    }

    func testTheLineShowsAtOnceAndHidesOnlyAfterHalfASecondBelowOnePixel() {
        var window = CoachSteadiness.Window()
        window.add(sec: 10, smearPx: 0.4)
        XCTAssertFalse(window.moving(at: 10))
        window.add(sec: 10.1, smearPx: 1.01)
        XCTAssertTrue(window.moving(at: 10.1))
        window.add(sec: 10.2, smearPx: 1.0)
        XCTAssertTrue(window.moving(at: 10.2))
        window.add(sec: 10.55, smearPx: 0.2)
        XCTAssertTrue(window.moving(at: 10.55))
        window.add(sec: 10.61, smearPx: 0.2)
        XCTAssertFalse(window.moving(at: 10.61))
        // Asked later with no new sample: the old smear has left the window
        var quiet = CoachSteadiness.Window()
        quiet.add(sec: 20, smearPx: 3)
        XCTAssertTrue(quiet.moving(at: 20.49))
        XCTAssertFalse(quiet.moving(at: 20.5))
        // Exactly one pixel is not past the limit
        var edge = CoachSteadiness.Window()
        edge.add(sec: 0, smearPx: 1)
        XCTAssertFalse(edge.moving(at: 0))
        // A sample not finite is left out
        edge.add(sec: 0.1, smearPx: .infinity)
        XCTAssertFalse(edge.moving(at: 0.1))
    }
}
