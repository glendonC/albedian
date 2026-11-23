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

    /// The base reply's sky (the Moon 20 degrees up, adding light) with the Sun at `sunAlt`, its band the contract's for
    /// that altitude, or with no Sun, as a laptop that predates it answers.
    private func sky(sunAlt: Double?) throws -> SkyConditions {
        var reply: JSONValue = try XCTUnwrap(ContractFixture.load("sky_context_contract")["bases"]?["reply"])
        var block = reply["predictedSkyBrightness"]!.objectValue!
        block.set("moonAltDeg", .number(JSONNumber(20)!))
        block.set("lunarVeilDeltaMag", .number(JSONNumber(1)!))
        block.set("illuminationFraction", .number(JSONNumber(0.7)!))
        reply["predictedSkyBrightness"] = .object(block)
        if let sunAlt {
            reply["sun"]?["altDeg"] = .number(JSONNumber(sunAlt)!)
            reply["sun"]?["band"] = .string(JSONString(SkySun.band(of: sunAlt).rawValue))
        } else {
            reply["sun"] = nil
        }
        return try XCTUnwrap(SkyConditions.read(reply))
    }

    /// D29 item 5 with the lead's answer of 12:44: the Sun's depth in whole degrees, in the three twilights only; none by
    /// day, none at night, none from a laptop that predates the Sun; "1 degree" in the singular, as sky_phase writes it.
    func testTheSunsLineInTwilightOnly() throws {
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -11.175)), "The Sun is 11 degrees below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -17.6)), "The Sun is 18 degrees below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -18)), "The Sun is 18 degrees below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -5.5)), "The Sun is 6 degrees below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -50.0 / 60.0)), "The Sun is 1 degree below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -1.49)), "The Sun is 1 degree below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -1.5)), "The Sun is 2 degrees below the horizon: still twilight.")
        // A half rounds up, as sky_phase's floor(depth + 0.5) does, never to the even neighbour (AUDIT-B, coach-3's read)
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -2.5)), "The Sun is 3 degrees below the horizon: still twilight.")
        XCTAssertEqual(Coach.sunWords(try sky(sunAlt: -12.5)), "The Sun is 13 degrees below the horizon: still twilight.")
        XCTAssertNil(Coach.sunWords(try sky(sunAlt: -18.000001)), "night")
        XCTAssertNil(Coach.sunWords(try sky(sunAlt: -47.7)), "night")
        XCTAssertNil(Coach.sunWords(try sky(sunAlt: -0.5)), "day: the Sun's disc still touches the horizon")
        XCTAssertNil(Coach.sunWords(try sky(sunAlt: 30)), "day")
        XCTAssertNil(Coach.sunWords(try sky(sunAlt: nil)), "no Sun in the reply")
        XCTAssertNil(Coach.sunWords(nil))
        XCTAssertEqual(CoachLine.sun("x").icon, "sun-horizon")
    }

    /// The Sun's line takes the Moon line's place while it shows, so the line that asks the person to act keeps its own.
    func testTheSunTakesTheMoonsPlaceAndTwoLinesStayTheMost() throws {
        let twilight = try sky(sunAlt: -11.175)
        let sun = CoachLine.sun("The Sun is 11 degrees below the horizon: still twilight.")
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: twilight, moving: true), [sun, .steadiness])
        XCTAssertEqual(Coach.lines(compassAsked: true, conditions: twilight, moving: true), [.compass, sun])
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: twilight, moving: false), [sun])
        let night = try sky(sunAlt: -47.7)
        let moon = CoachLine.moon("The Moon is up, 70 percent lit: fewer stars show.")
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: night, moving: true), [moon, .steadiness])
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: try sky(sunAlt: 30), moving: false), [moon])
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

    private func motion(_ sec: Double, rate: PointingRecord.Vector3) -> MotionSample {
        MotionSample(bootClockSec: sec, referenceFrame: .xTrueNorthZVertical, quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                     gravityG: .init(x: 0, y: -0.6, z: -0.8), rotationRateRadPerSec: rate)
    }

    private func motion(_ sec: Double, rateZ: Double) -> MotionSample {
        motion(sec, rate: .init(x: 0, y: 0, z: rateZ))
    }

    /// The wrapper the app calls judges at the newest sample (AUDIT-B, coach-0-1-2 verdict): a still phone that starts to
    /// move shows the line at once, and one that comes to rest hides it after half a second, whatever came before.
    func testTheSamplesAreJudgedAtTheNewest() {
        // 1 s exposure through 1000 px: 0.002 rad/s smears 2 px, 0.0005 rad/s half a pixel
        XCTAssertTrue(CoachSteadiness.moving([motion(10, rateZ: 0.0005), motion(11, rateZ: 0.002)], exposureSec: 1, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([motion(10, rateZ: 0.002), motion(11, rateZ: 0.0005)], exposureSec: 1, focalPx: 1000))
        XCTAssertTrue(CoachSteadiness.moving([motion(10.8, rateZ: 0.002), motion(11, rateZ: 0.0005)], exposureSec: 1, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([], exposureSec: 1, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([motion(11, rateZ: 0.002)], exposureSec: nil, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([motion(11, rateZ: 0.002)], exposureSec: 1, focalPx: nil))
    }

    /// Each input of `CoachSteadiness.moving(_:exposureSec:focalPx:)` drives the smear, the half-second window and the
    /// one-pixel limit hold through the wrapper, and a non-finite sample is skipped without hiding a real smear.
    func testTheMovingWrapperCarriesExposureFocalLengthAndSkipsBadSamples() {
        let diagonal = motion(11, rate: .init(x: 0.0006, y: 0.0008, z: 0)) // 0.001 rad/s
        XCTAssertFalse(CoachSteadiness.moving([diagonal], exposureSec: 0.5, focalPx: 800), "0.4 px")
        XCTAssertTrue(CoachSteadiness.moving([diagonal], exposureSec: 2.0, focalPx: 800), "1.6 px: exposureSec used")
        XCTAssertFalse(CoachSteadiness.moving([diagonal], exposureSec: 0.8, focalPx: 500), "0.4 px")
        XCTAssertTrue(CoachSteadiness.moving([diagonal], exposureSec: 0.8, focalPx: 2000), "1.6 px: focalPx used")
        XCTAssertFalse(CoachSteadiness.moving([motion(11, rateZ: 0.001)], exposureSec: 1, focalPx: 1000), "exact 1 px limit")
        XCTAssertTrue(CoachSteadiness.moving([motion(11, rateZ: 0.001001)], exposureSec: 1, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([motion(10.5, rateZ: 0.002), motion(11.0, rateZ: 0.0005)], exposureSec: 1, focalPx: 1000),
                       "exact half-second edge has left the window")
        XCTAssertTrue(CoachSteadiness.moving([motion(10.501, rateZ: 0.002), motion(11.0, rateZ: 0.0005)], exposureSec: 1, focalPx: 1000))
        XCTAssertTrue(CoachSteadiness.moving([motion(10.8, rateZ: 0.002), motion(10.9, rateZ: .nan), motion(11.0, rateZ: 0.0005)],
                                             exposureSec: 1, focalPx: 1000), "a non-finite sample does not clear earlier smears")
        XCTAssertFalse(CoachSteadiness.moving([motion(10.9, rateZ: .infinity), motion(11.0, rateZ: 0.0005)], exposureSec: 1, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([motion(11, rateZ: 0.002)], exposureSec: 0, focalPx: 1000))
        XCTAssertFalse(CoachSteadiness.moving([motion(11, rateZ: 0.002)], exposureSec: 1, focalPx: 0))
    }

    private final class CoachMotionStandIn: DeviceMotionSource, @unchecked Sendable {
        let availableFrames: Set<AttitudeFrame> = [.xTrueNorthZVertical]
        var deliver: (@Sendable (DeviceMotionEvent) -> Void)?
        func start(frame: AttitudeFrame, intervalSec: Double, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
            self.deliver = deliver
        }
        func stop() {}
    }

    private final class CoachPlaceStandIn: PlaceAndHeadingSource, @unchecked Sendable {
        func start(headingOrientation: HeadingOrientation, deliver: @escaping @Sendable (PlaceEvent) -> Void) {}
        func updateHeadingOrientation(_ headingOrientation: HeadingOrientation) {}
        func stop() {}
    }

    /// The app's own path (`CompanionModel.coachLines`, AUDIT-B coach-0-1-2 finding): `SensorRecorder.recentMotion`
    /// hands the last half second of delivered motion to `CoachSteadiness.moving` and `Coach.lines`.
    @MainActor
    func testTheAppPathFromRecorderRecentMotionToTheSteadinessLine() throws {
        let source = CoachMotionStandIn()
        let recorder = try XCTUnwrap(SensorRecorder(motion: source, place: CoachPlaceStandIn()))
        XCTAssertEqual(recorder.recentMotion(seconds: CoachSteadiness.windowSec), [])
        _ = recorder.start(interface: .portrait)
        let send = try XCTUnwrap(source.deliver)
        func event(_ sec: Double, rateZ: Double) -> DeviceMotionEvent {
            DeviceMotionEvent(timestampSec: sec, frame: .xTrueNorthZVertical, quaternion: .init(x: 0, y: 0, z: 0, w: 1),
                              gravityG: .init(x: 0, y: 0, z: -1), rotationRateRadPerSec: .init(x: 0, y: 0, z: rateZ))
        }
        send(event(10.0, rateZ: 0.0002))
        XCTAssertFalse(CoachSteadiness.moving(recorder.recentMotion(seconds: CoachSteadiness.windowSec), exposureSec: 0.8, focalPx: 1500))
        // Still sample at 10.0, moving sample at 11.0 (0.002 * 0.8 * 1500 = 2.4 px): recentMotion keeps only (10.5, 11.0]
        send(event(11.0, rateZ: 0.002))
        XCTAssertEqual(recorder.recentMotion(seconds: CoachSteadiness.windowSec).map(\.bootClockSec), [11.0])
        let movingAt11 = CoachSteadiness.moving(recorder.recentMotion(seconds: CoachSteadiness.windowSec), exposureSec: 0.8, focalPx: 1500)
        XCTAssertTrue(movingAt11)
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: nil, moving: movingAt11), [.steadiness])
        // Still at 11.49: 11.0 is still inside the half-second window
        send(event(11.49, rateZ: 0.0002))
        XCTAssertEqual(recorder.recentMotion(seconds: CoachSteadiness.windowSec).map(\.bootClockSec), [11.0, 11.49])
        XCTAssertTrue(CoachSteadiness.moving(recorder.recentMotion(seconds: CoachSteadiness.windowSec), exposureSec: 0.8, focalPx: 1500))
        // Still at 11.5: 11.0 is exactly 0.5 s behind the newest, so it leaves recentMotion and steadiness clears
        send(event(11.5, rateZ: 0.0002))
        XCTAssertEqual(recorder.recentMotion(seconds: CoachSteadiness.windowSec).map(\.bootClockSec), [11.49, 11.5])
        let settledAt115 = CoachSteadiness.moving(recorder.recentMotion(seconds: CoachSteadiness.windowSec), exposureSec: 0.8, focalPx: 1500)
        XCTAssertFalse(settledAt115)
        XCTAssertEqual(Coach.lines(compassAsked: false, conditions: nil, moving: settledAt115), [])
        // Guarded windows return empty
        XCTAssertEqual(recorder.recentMotion(seconds: .infinity), [])
        XCTAssertEqual(recorder.recentMotion(seconds: .nan), [])
        XCTAssertEqual(recorder.recentMotion(seconds: 0), [])
        XCTAssertEqual(recorder.recentMotion(seconds: -0.5), [])
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
