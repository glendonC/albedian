import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The readings of Core Motion and Core Location carried onto the core's types (REQ-IOS-1.2, 1.3, REQ-IOS-6.2): every
/// value as the device gave it, the frame and the axis named as Apple names them, nothing guessed. Synthetic values.
final class SensorMappingTests: XCTestCase {
    private let moment = Date(timeIntervalSince1970: 1_893_463_200)

    func testAsksForTheTrueNorthFrameWhenTheDeviceHasItElseTheArbitraryOne() {
        XCTAssertEqual(SensorMapping.frameToRequest(available: Set(AttitudeFrame.allCases)), .xTrueNorthZVertical)
        XCTAssertEqual(SensorMapping.frameToRequest(available: [.xTrueNorthZVertical]), .xTrueNorthZVertical)
        XCTAssertEqual(SensorMapping.frameToRequest(available: [.xArbitraryZVertical, .xArbitraryCorrectedZVertical, .xMagneticNorthZVertical]),
                       .xArbitraryZVertical)
        XCTAssertEqual(SensorMapping.frameToRequest(available: [.xArbitraryZVertical]), .xArbitraryZVertical)
        // Gravity and rotation rate do not depend on the frame, but a frame other than true north gives no heading,
        // and no other frame is asked for: the record names the cause (no magnetometer)
        XCTAssertNil(SensorMapping.frameToRequest(available: [.xArbitraryCorrectedZVertical, .xMagneticNorthZVertical]))
        XCTAssertNil(SensorMapping.frameToRequest(available: []))
        // 100 Hz, as tasks.md 7 sets it [proposed]
        XCTAssertEqual(SensorMapping.motionIntervalSec, 0.01)
    }

    func testCarriesAMotionSampleAsGivenAndNamesOnlyTheTrueNorthFrameAsSuch() {
        for frame in AttitudeFrame.allCases {
            let event = DeviceMotionEvent(timestampSec: 5000.125, frame: frame, quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                                          gravityG: .init(x: 0.01, y: -0.6, z: -0.8), rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005))
            let sample = SensorMapping.sample(event)
            XCTAssertEqual(sample.bootClockSec, 5000.125)
            XCTAssertEqual(sample.referenceFrame, frame == .xTrueNorthZVertical ? .xTrueNorthZVertical : .other, "\(frame)")
            XCTAssertEqual(sample.quaternion, event.quaternion)
            XCTAssertEqual(sample.gravityG, event.gravityG)
            XCTAssertEqual(sample.rotationRateRadPerSec, event.rotationRateRadPerSec)
        }
        // Values outside every range pass through unchanged: the builder judges them, the mapping does not
        let odd = DeviceMotionEvent(timestampSec: .nan, frame: .xTrueNorthZVertical, quaternion: .init(x: 9, y: 0, z: 0, w: 0),
                                    gravityG: .init(x: .infinity, y: 0, z: 0), rotationRateRadPerSec: .init(x: -1e9, y: 0, z: 0))
        let sample = SensorMapping.sample(odd)
        XCTAssertTrue(sample.bootClockSec.isNaN)
        XCTAssertEqual(sample.quaternion.x, 9)
        XCTAssertEqual(sample.gravityG.x, .infinity)
        XCTAssertEqual(sample.rotationRateRadPerSec.x, -1e9)
    }

    func testCarriesAHeadingAsGivenWithTheAxisItWasMeasuredFor() {
        for orientation in HeadingOrientation.allCases {
            // Each pair holds two different values, so a heading carried as the accuracy (or back) fails here
            for (heading, accuracy) in [(212.5, 12.0), (-1, 4), (0, -1), (90, 0), (359.9, 180)] {
                let event = HeadingEvent(trueHeadingDeg: heading, headingAccuracyDeg: accuracy, timestamp: moment.addingTimeInterval(-1.25),
                                         orientation: orientation)
                XCTAssertEqual(SensorMapping.heading(event), HeadingReading(trueHeadingDeg: heading, headingAccuracyDeg: accuracy,
                                                                            orientation: orientation, deviceClock: moment.addingTimeInterval(-1.25)))
            }
        }
    }

    func testCarriesAFixAsGivenAndReadsNoSourceInformationAsNotSimulated() {
        for (simulated, expected) in [(Bool?.none, false), (false, false), (true, true)] {
            let event = LocationEvent(latDeg: 30, lonDeg: -40, horizontalAccuracyM: 8, altitudeM: 12, ellipsoidalAltitudeM: 45.5,
                                      verticalAccuracyM: 3, timestamp: moment.addingTimeInterval(-2.5), isSimulatedBySoftware: simulated)
            XCTAssertEqual(SensorMapping.fix(event), LocationFix(latDeg: 30, lonDeg: -40, horizontalAccuracyM: 8, altitudeM: 12,
                                                                 ellipsoidalAltitudeM: 45.5, verticalAccuracyM: 3,
                                                                 deviceClock: moment.addingTimeInterval(-2.5), isSimulatedBySoftware: expected))
        }
        // Invalid values are carried as given too, each field its own value
        let invalid = LocationEvent(latDeg: -91, lonDeg: 181, horizontalAccuracyM: -1, altitudeM: -3, ellipsoidalAltitudeM: 0,
                                    verticalAccuracyM: -2, timestamp: moment, isSimulatedBySoftware: nil)
        XCTAssertEqual(SensorMapping.fix(invalid), LocationFix(latDeg: -91, lonDeg: 181, horizontalAccuracyM: -1, altitudeM: -3,
                                                               ellipsoidalAltitudeM: 0, verticalAccuracyM: -2, deviceClock: moment,
                                                               isSimulatedBySoftware: false))
    }

    func testTakesAPlaceOnlyWhileThePersonAllowsLocation() {
        let event = LocationEvent(latDeg: 30, lonDeg: -40, horizontalAccuracyM: 8, altitudeM: 12, ellipsoidalAltitudeM: 45.5,
                                  verticalAccuracyM: 3, timestamp: moment, isSimulatedBySoftware: nil)
        let fix = SensorMapping.fix(event)
        for authorization in LocationAuthorization.allCases {
            for accuracy in AccuracyAuthorization.allCases {
                let reading = SensorMapping.location(authorization: authorization, accuracy: accuracy, fix: event)
                switch authorization {
                case .authorizedWhenInUse, .authorizedAlways:
                    XCTAssertEqual(reading, .allowed(accuracy, fix: fix), "\(authorization) \(accuracy)")
                    XCTAssertEqual(SensorMapping.location(authorization: authorization, accuracy: accuracy, fix: nil), .allowed(accuracy, fix: nil))
                case .notDetermined, .restricted, .denied:
                    XCTAssertEqual(reading, .notAllowed, "\(authorization)")
                }
            }
        }
        // Allowed, but no accuracy reported yet: no claim of either accuracy, so no place
        XCTAssertEqual(SensorMapping.location(authorization: .authorizedWhenInUse, accuracy: nil, fix: event), .notAllowed)
        XCTAssertEqual(SensorMapping.location(authorization: nil, accuracy: .full, fix: event), .notAllowed)
    }

    func testNamesTheHeadingAxisFromTheInterfaceOrientationWithLandscapeSwapped() {
        // UIOrientation.h: "UIInterfaceOrientationLandscapeLeft is equal to UIDeviceOrientationLandscapeRight (and vice
        // versa)"; CLDeviceOrientation names device orientations
        XCTAssertEqual(SensorMapping.headingOrientation(for: .portrait), .portrait)
        XCTAssertEqual(SensorMapping.headingOrientation(for: .portraitUpsideDown), .portraitUpsideDown)
        XCTAssertEqual(SensorMapping.headingOrientation(for: .landscapeLeft), .landscapeRight)
        XCTAssertEqual(SensorMapping.headingOrientation(for: .landscapeRight), .landscapeLeft)
        XCTAssertNil(SensorMapping.headingOrientation(for: .unknown))
    }
}
