import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The check screen's words (unit mobile-2): each reading as the person reads it, every value that is not a number
/// written as such and never trapping, the heading accuracy named for the magnetic heading Apple states it for, and no
/// place. Synthetic readings.
final class CheckReadoutTests: XCTestCase {
    private let moment = SampleCapture.moment

    private func heading(_ trueDeg: Double, _ accuracyDeg: Double) -> HeadingEvent {
        HeadingEvent(trueHeadingDeg: trueDeg, headingAccuracyDeg: accuracyDeg, timestamp: moment, orientation: .portrait)
    }

    private func fix(accuracy: Double) -> LocationEvent {
        LocationEvent(latDeg: 10.5, lonDeg: 20.25, horizontalAccuracyM: accuracy, altitudeM: 3, ellipsoidalAltitudeM: 4,
                      verticalAccuracyM: 5, timestamp: moment, isSimulatedBySoftware: false)
    }

    private func sample(gravity: (Double, Double, Double), quaternion: (Double, Double, Double, Double) = (0, 0, 0, 1)) -> MotionSample {
        MotionSample(bootClockSec: 1, referenceFrame: .xTrueNorthZVertical,
                     quaternion: .init(x: quaternion.0, y: quaternion.1, z: quaternion.2, w: quaternion.3),
                     gravityG: .init(x: gravity.0, y: gravity.1, z: gravity.2), rotationRateRadPerSec: .init(x: 0, y: 0, z: 0))
    }

    func testNamesEveryLocationChoiceAccuracyAndFrameInPlainWords() {
        let location: [LocationAuthorization?: String] = [
            nil: "not reported", .notDetermined: "not asked yet", .restricted: "restricted", .denied: "not allowed",
            .authorizedAlways: "allowed always", .authorizedWhenInUse: "allowed while in use",
        ]
        XCTAssertEqual(location.count, LocationAuthorization.allCases.count + 1)
        for (choice, words) in location { XCTAssertEqual(CheckReadout.location(choice), words) }
        XCTAssertEqual(CheckReadout.accuracyGranted(nil), "not reported")
        XCTAssertEqual(CheckReadout.accuracyGranted(.full), "full")
        XCTAssertEqual(CheckReadout.accuracyGranted(.reduced), "reduced")
        let frames: [AttitudeFrame?: String] = [
            nil: "not started", .xArbitraryZVertical: "arbitrary", .xArbitraryCorrectedZVertical: "arbitrary, corrected",
            .xMagneticNorthZVertical: "magnetic north", .xTrueNorthZVertical: "true north",
        ]
        XCTAssertEqual(frames.count, AttitudeFrame.allCases.count + 1)
        for (frame, words) in frames { XCTAssertEqual(CheckReadout.frame(frame), words) }
    }

    func testWritesTheTrueHeadingAndSaysWhenItCouldNotBeDetermined() {
        XCTAssertEqual(CheckReadout.trueHeading(nil), "none")
        XCTAssertEqual(CheckReadout.trueHeading(heading(0, 5)), "0.0 deg")
        XCTAssertEqual(CheckReadout.trueHeading(heading(359.94, 5)), "359.9 deg")
        XCTAssertEqual(CheckReadout.trueHeading(heading(90.26, 5)), "90.3 deg")
        XCTAssertEqual(CheckReadout.trueHeading(heading(-1, 5)), "could not be determined")
        XCTAssertEqual(CheckReadout.trueHeading(heading(-0.0001, 5)), "could not be determined")
        XCTAssertEqual(CheckReadout.trueHeading(heading(.nan, 5)), "not a number")
        XCTAssertEqual(CheckReadout.trueHeading(heading(.infinity, 5)), "infinite")
        XCTAssertEqual(CheckReadout.trueHeading(heading(-.infinity, 5)), "infinite")
    }

    func testNamesTheHeadingAccuracyForTheMagneticHeadingAndSaysWhenItIsInvalid() {
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(nil), "none")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, 0)), "\u{B1}0.0 deg")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, 4.04)), "\u{B1}4.0 deg")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, 180)), "\u{B1}180.0 deg")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, -1)), "invalid")
        // The true heading's own reading does not change the accuracy's
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(-1, 7)), "\u{B1}7.0 deg")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, .nan)), "not a number")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, .infinity)), "infinite")
        XCTAssertEqual(CheckReadout.magneticHeadingAccuracy(heading(12, -.infinity)), "infinite")
    }

    func testWritesOnlyThePlacesAccuracyNeverThePlace() {
        XCTAssertEqual(CheckReadout.placeAccuracy(nil), "none")
        XCTAssertEqual(CheckReadout.placeAccuracy(fix(accuracy: 65)), "\u{B1}65 m")
        XCTAssertEqual(CheckReadout.placeAccuracy(fix(accuracy: 4.4)), "\u{B1}4 m")
        XCTAssertEqual(CheckReadout.placeAccuracy(fix(accuracy: 0)), "\u{B1}0 m")
        // A huge radius is written out whole, never trapping as a conversion to Int would
        let huge = CheckReadout.placeAccuracy(fix(accuracy: 1e300))
        XCTAssertTrue(huge.hasPrefix("\u{B1}1000000000000000052") && huge.hasSuffix(" m") && huge.count == 304, huge)
        XCTAssertEqual(CheckReadout.placeAccuracy(fix(accuracy: -1)), "invalid")
        XCTAssertEqual(CheckReadout.placeAccuracy(fix(accuracy: .nan)), "not a number")
        XCTAssertEqual(CheckReadout.placeAccuracy(fix(accuracy: .infinity)), "infinite")
        let text = CheckReadout.placeAccuracy(fix(accuracy: 65))
        XCTAssertFalse(text.contains("10.5") || text.contains("20.25") || text.contains("20.2"))
    }

    func testWritesGravityAndTheQuaternionWithTheSignsTheSensorGave() {
        XCTAssertEqual(CheckReadout.gravity(nil), "none")
        XCTAssertEqual(CheckReadout.quaternion(nil), "none")
        XCTAssertEqual(CheckReadout.gravity(sample(gravity: (0, -0.001, -1))), "0.00, 0.00, -1.00")
        XCTAssertEqual(CheckReadout.gravity(sample(gravity: (-0.0, 0.25, -0.75))), "0.00, 0.25, -0.75")
        XCTAssertEqual(CheckReadout.quaternion(sample(gravity: (0, 0, -1), quaternion: (0.0004, -0.0004, -0.7071, 0.7071))),
                       "0.000, 0.000, -0.707, 0.707")
        XCTAssertEqual(CheckReadout.gravity(sample(gravity: (.nan, 1, 2))), "not a number, 1.00, 2.00")
    }

    func testMeasuresGravitysLengthOnlyFromNumbers() {
        XCTAssertNil(CheckReadout.gravityLength(nil))
        XCTAssertEqual(CheckReadout.gravityLength(sample(gravity: (0, 0, -1))), 1)
        XCTAssertEqual(CheckReadout.gravityLength(sample(gravity: (3, -4, 0))), 5)
        XCTAssertEqual(CheckReadout.gravityLength(sample(gravity: (0.5, 0.5, 0.5))), 0.75.squareRoot())
        for bad in [(Double.nan, 0.0, 0.0), (0, .infinity, 0), (0, 0, -.infinity)] {
            XCTAssertNil(CheckReadout.gravityLength(sample(gravity: bad)))
        }
    }

    func testWritesAFixedNumberOfDecimalsAndNoSignOnAZero() {
        XCTAssertEqual(CheckReadout.fixed(-0.0, places: 2), "0.00")
        XCTAssertEqual(CheckReadout.fixed(-0.004, places: 2), "0.00")
        XCTAssertEqual(CheckReadout.fixed(-0.006, places: 2), "-0.01")
        XCTAssertEqual(CheckReadout.fixed(0.006, places: 2), "0.01")
        XCTAssertEqual(CheckReadout.fixed(-0.4, places: 0), "0")
        XCTAssertEqual(CheckReadout.fixed(-1.4, places: 0), "-1")
        XCTAssertEqual(CheckReadout.fixed(12.3456, places: 3), "12.346")
        XCTAssertEqual(CheckReadout.fixed(.nan, places: 2), "not a number")
        XCTAssertEqual(CheckReadout.fixed(-.infinity, places: 2), "infinite")
        XCTAssertEqual(CheckReadout.fixed(.infinity, places: 0), "infinite")
    }

    func testSaysWhatAKeepEndedInAndWhatOfThePhotoIsOnThePhone() {
        let sha = String(repeating: "a", count: 64)
        XCTAssertEqual(CheckReadout.keepOutcome(.success(StoredCapture(sha256: sha, contentType: .heic, hasRecord: true))), "Photo and pointing record kept")
        XCTAssertEqual(CheckReadout.keepOutcome(.success(StoredCapture(sha256: sha, contentType: .heic, hasRecord: false))), "Photo kept, no pointing record")
        func failed(_ reason: CaptureStoreError, _ afterwards: CaptureAfterFailure, _ stray: [String] = []) -> String {
            CheckReadout.keepOutcome(.failure(CaptureStoreFailure(reason: reason, afterwards: afterwards, stray: stray)))
        }
        XCTAssertEqual(failed(.fileSystem(code: 640), .holding(.nothing)), "Not kept: the phone's storage refused (error 640); nothing of it is on the phone")
        XCTAssertEqual(failed(.readBackFailed(file: "x", code: 256), .holding(.photoAlone)),
                       "Not kept: a file just written could not be read back (error 256); the photo is on the phone without a record")
        XCTAssertEqual(failed(.readBackDiffers(file: "x"), .holding(.photoWithRecord)),
                       "Not kept: a file read back other bytes than were written; a photo and its record are on the phone, checked again before any send")
        XCTAssertEqual(failed(.fileSystem(code: 513), .holding(.nothing), ["\(sha).pointing.json"]),
                       "Not kept: the phone's storage refused (error 513); nothing of it is on the phone but a record file without its photo")
        // Nothing is said to be written, or left, unless the store looked: not read before the keep changed anything, and not
        // read after it may have
        XCTAssertEqual(failed(.fileSystem(code: 257), .notReadNothingChanged(code: 257)),
                       "Not kept: the phone's storage refused (error 257); the phone's storage could not be read, and nothing of this photo was written")
        XCTAssertEqual(failed(.fileSystem(code: 640), .notReadAfterChanges(code: 257)),
                       "Not kept: the phone's storage refused (error 640); the phone's storage could not be read afterwards, so what is left of "
                       + "this photo on the phone is not known")
        // A file there by name that does not read back whole is said as that, never as kept or as a photo on the phone
        XCTAssertEqual(failed(.storedBytesChanged(sha256: sha), .notWhole),
                       "Not kept: a photo file under this name holds other bytes; a file of this photo is on the phone but does not read "
                       + "back as it was written, so it is not kept and is never sent")
        XCTAssertEqual(CheckReadout.why(.storedRecordChanged(sha256: sha)), "the record file under this name is not this photo's record")
        XCTAssertEqual(failed(.recordNamesAnotherPhoto, .holding(.nothing)), "Not kept: the record names another photo; nothing of it is on the phone")
        // The contract's issue messages are never shown, so no value of the record can be
        let issue = PointingIssue(path: ["location", "latDeg"], message: "12.3456 is out of range")
        let words = failed(.recordNotContract([issue]), .holding(.nothing))
        XCTAssertEqual(words, "Not kept: the record is not one the contract takes; nothing of it is on the phone")
        XCTAssertFalse(words.contains("12.3456") || words.contains("latDeg"))
        for reason in [CaptureStoreError.storedBytesChanged(sha256: sha), .storedRecordChanged(sha256: sha), .notKept] {
            XCTAssertFalse(CheckReadout.why(reason).contains(sha), "\(reason)")
        }
    }

    func testListsEverySentenceAKeepCanEndInAndEveryWordOfTheReadings() {
        // The owner reads the screen's words from these lists, so each reason and each thing known afterwards is in one
        let outcomes = CheckReadout.everyKeepOutcome()
        XCTAssertEqual(outcomes.count, 15)
        XCTAssertEqual(Set(outcomes).count, outcomes.count)
        let sha = String(repeating: "0", count: 64)
        let reasons: [CaptureStoreError] = [.recordNamesAnotherPhoto, .recordNotContract([]), .readBackDiffers(file: "x"), .readBackFailed(file: "x", code: 256),
                                            .storedBytesChanged(sha256: sha), .storedRecordChanged(sha256: sha), .fileSystem(code: 640)]
        for reason in reasons { XCTAssertTrue(outcomes.contains { $0.contains(CheckReadout.why(reason)) }, "\(reason)") }
        let afterwards: [CaptureAfterFailure] = [.holding(.nothing), .holding(.photoWithRecord), .holding(.photoAlone), .notWhole,
                                                 .notReadNothingChanged(code: 257), .notReadAfterChanges(code: 257)]
        for after in afterwards {
            let tail = CheckReadout.holding(CaptureStoreFailure(reason: .notKept, afterwards: after, stray: []))
            XCTAssertTrue(outcomes.contains { $0.hasSuffix(tail) }, "\(after)")
        }
        XCTAssertTrue(outcomes.contains("Photo and pointing record kept") && outcomes.contains("Photo kept, no pointing record"))
        // A file found changed on the disk is never said with "nothing of it is on the phone"; a removal's reason is not a keep's
        XCTAssertFalse(outcomes.contains { $0.contains(CheckReadout.why(.storedBytesChanged(sha256: sha))) && $0.contains("nothing of it is on the phone") })
        XCTAssertFalse(outcomes.contains { $0.contains(CheckReadout.why(.notKept)) })
        let words = CheckReadout.everyValueWord()
        for word in ["not reported", "not asked yet", "restricted", "not allowed", "allowed always", "allowed while in use", "full", "reduced",
                     "not started", "arbitrary", "arbitrary, corrected", "magnetic north", "true north", "none", "not a number", "infinite",
                     "could not be determined", "invalid", "0.98 to 1.02"] {
            XCTAssertTrue(words.contains(word), word)
        }
    }

    func testKeepsTheSmallestAndLargestGravityLengthShown() {
        var range = GravityRange()
        XCTAssertEqual(range.text, "none")
        range.add(nil)
        range.add(.nan)
        range.add(.infinity)
        XCTAssertNil(range.smallest)
        XCTAssertNil(range.largest)
        range.add(1.0)
        XCTAssertEqual(range.text, "1.00 to 1.00")
        range.add(1.02)
        range.add(0.98)
        range.add(1.01)
        XCTAssertEqual(range.smallest, 0.98)
        XCTAssertEqual(range.largest, 1.02)
        XCTAssertEqual(range.text, "0.98 to 1.02")
    }

    func testNamesEachCountStateAndShowsNotWholeOnlyAboveZero() {
        XCTAssertEqual(CheckCount.reading.keptWords, "reading")
        XCTAssertNil(CheckCount.reading.notWholeCount)
        XCTAssertEqual(CheckCount.couldNotBeRead.keptWords, "could not be read")
        XCTAssertNil(CheckCount.couldNotBeRead.notWholeCount)
        XCTAssertEqual(CheckCount.counted(whole: 2, notWhole: -1).keptWords, "2")
        XCTAssertNil(CheckCount.counted(whole: 2, notWhole: -1).notWholeCount)
        XCTAssertEqual(CheckCount.counted(whole: 0, notWhole: 0).keptWords, "0")
        XCTAssertNil(CheckCount.counted(whole: 0, notWhole: 0).notWholeCount)
        XCTAssertEqual(CheckCount.counted(whole: 4, notWhole: 0).keptWords, "4")
        XCTAssertNil(CheckCount.counted(whole: 4, notWhole: 0).notWholeCount)
        XCTAssertEqual(CheckCount.counted(whole: 0, notWhole: 1).keptWords, "0")
        XCTAssertEqual(CheckCount.counted(whole: 0, notWhole: 1).notWholeCount, 1)
        XCTAssertEqual(CheckCount.counted(whole: 3, notWhole: 2).keptWords, "3")
        XCTAssertEqual(CheckCount.counted(whole: 3, notWhole: 2).notWholeCount, 2)
    }
}
