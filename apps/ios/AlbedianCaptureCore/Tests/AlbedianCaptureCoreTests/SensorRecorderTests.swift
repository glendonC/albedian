import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The recorder between the sensor sources and the builder (tasks.md 7), run with stand-in sources that deliver
/// synthetic events: what it asks the sources for, what it keeps, and the capture it hands the builder at a photo.
/// `start` and `stop` are main-actor only, so the tests run there.
@MainActor
final class SensorRecorderTests: XCTestCase {
    final class StandInMotion: DeviceMotionSource, @unchecked Sendable {
        let availableFrames: Set<AttitudeFrame>
        private(set) var started: [(AttitudeFrame, Double)] = []
        private(set) var stops = 0
        /// Whether the stream runs now: set by start, cleared by stop.
        private(set) var running = false
        var deliver: (@Sendable (DeviceMotionEvent) -> Void)?
        init(_ frames: Set<AttitudeFrame>) { availableFrames = frames }
        func start(frame: AttitudeFrame, intervalSec: Double, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
            started.append((frame, intervalSec))
            self.deliver = deliver
            running = true
        }
        func stop() { stops += 1; running = false }
    }

    final class StandInPlace: PlaceAndHeadingSource, @unchecked Sendable {
        private(set) var orientations: [HeadingOrientation] = []
        private(set) var stops = 0
        private(set) var running = false
        var deliver: (@Sendable (PlaceEvent) -> Void)?
        func start(headingOrientation: HeadingOrientation, deliver: @escaping @Sendable (PlaceEvent) -> Void) {
            orientations.append(headingOrientation)
            self.deliver = deliver
            running = true
        }
        func stop() { stops += 1; running = false }
    }

    private let moment = SampleCapture.moment
    private let photoClock = SampleCapture.photoBootClockSec
    private let photoHash = PhotoHash.sha256Hex(Data("synthetic photo bytes".utf8))
    /// 2^-7 s.
    private let step = 0.0078125

    private func motionEvent(_ offset: Double, frame: AttitudeFrame = .xTrueNorthZVertical) -> DeviceMotionEvent {
        DeviceMotionEvent(timestampSec: photoClock + offset, frame: frame, quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                          gravityG: .init(x: 0, y: -0.6, z: -0.8), rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005))
    }

    private func headingEvent(_ offset: Double, degrees: Double = 212.5) -> PlaceEvent {
        .heading(HeadingEvent(trueHeadingDeg: degrees, headingAccuracyDeg: 12, timestamp: moment.addingTimeInterval(offset), orientation: .portrait))
    }

    private func fixEvent(_ offset: Double, lat: Double = 30) -> PlaceEvent {
        .location(LocationEvent(latDeg: lat, lonDeg: -40, horizontalAccuracyM: 8, altitudeM: 12, ellipsoidalAltitudeM: 45.5,
                                verticalAccuracyM: 3, timestamp: moment.addingTimeInterval(offset), isSimulatedBySoftware: nil))
    }

    private var camera: CameraReading { SampleCapture.capture(photoSha256: photoHash).camera }
    private var device: DeviceLabels { DeviceLabels(hardwareModel: "Synthetic1,1", systemVersion: "27.1") }

    private func capture(_ recorder: SensorRecorder, start: Double = -8 * 0.0078125, end: Double = 8 * 0.0078125) -> SensorCapture {
        recorder.capture(photoSha256: photoHash, photoBootClockSec: photoClock, photoDeviceClock: moment, device: device, camera: camera,
                         windowStartOffsetSec: start, windowEndOffsetSec: end)
    }

    func testAsksForTrueNorthAt100HzAndTheHeadingAxisOfTheInterface() throws {
        let motion = StandInMotion(Set(AttitudeFrame.allCases))
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
        XCTAssertEqual(recorder.start(interface: .landscapeLeft), .started(.xTrueNorthZVertical))
        XCTAssertEqual(motion.started.map(\.0), [.xTrueNorthZVertical])
        XCTAssertEqual(motion.started.map(\.1), [0.01])
        XCTAssertEqual(place.orientations, [.landscapeRight])
        XCTAssertTrue(recorder.status.running)
        XCTAssertEqual(recorder.status.frame, .xTrueNorthZVertical)
        recorder.stop()
        XCTAssertEqual(motion.stops, 1)
        XCTAssertEqual(place.stops, 1)
        XCTAssertFalse(recorder.status.running)
        // Unknown interface orientation: Core Location's default axis, portrait (CLLocationManager.h: unknown is ignored)
        XCTAssertEqual(recorder.start(interface: .unknown), .started(.xTrueNorthZVertical))
        XCTAssertEqual(place.orientations, [.landscapeRight, .portrait])
        // Starting twice starts nothing more
        XCTAssertEqual(recorder.start(interface: .portrait), .started(.xTrueNorthZVertical))
        XCTAssertEqual(motion.started.count, 2)
        recorder.stop()
        recorder.stop()
        XCTAssertEqual(motion.stops, 2)
        XCTAssertNil(SensorRecorder(motion: motion, place: place, ringCapacity: 0))
    }

    func testStartsOnlyPlaceAndHeadingWhenTheDeviceHasNoMotionStream() throws {
        let motion = StandInMotion([])
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
        XCTAssertEqual(recorder.start(interface: .portrait), .noDeviceMotion)
        XCTAssertTrue(motion.started.isEmpty)
        XCTAssertEqual(place.orientations, [.portrait])
        XCTAssertNil(recorder.status.frame)
        // The record then cannot be written, for the reason the builder names
        XCTAssertEqual(refusal(capture(recorder)), .noMotionSampleAtThePhoto(nearestOffsetSec: nil))
    }

    private func refusal(_ capture: SensorCapture) -> PointingBuildError? {
        do { _ = try PointingRecordBuilder.build(capture); return nil } catch { return error }
    }

    func testHandsTheBuilderWhatTheSourcesSaidAndARecordTheContractTakes() throws {
        let motion = StandInMotion(Set(AttitudeFrame.allCases))
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
        _ = recorder.start(interface: .portrait)
        let deliverMotion = try XCTUnwrap(motion.deliver)
        let deliverPlace = try XCTUnwrap(place.deliver)
        deliverPlace(.authorization(.authorizedWhenInUse, .full))
        deliverPlace(fixEvent(-2.5))
        deliverPlace(headingEvent(-1.25))
        for index in -20...20 { deliverMotion(motionEvent(Double(index) * step)) }
        XCTAssertEqual(recorder.status.samplesKept, 41)
        XCTAssertEqual(recorder.status.authorization, .authorizedWhenInUse)
        XCTAssertEqual(recorder.status.accuracy, .full)

        let captured = capture(recorder)
        XCTAssertEqual(captured.photoSha256, photoHash)
        XCTAssertEqual(captured.photoBootClockSec, photoClock)
        XCTAssertEqual(captured.photoDeviceClock, moment)
        XCTAssertEqual(captured.motion.samples.count, 17)
        XCTAssertEqual(captured.motion.samples.first?.bootClockSec, photoClock - 8 * step)
        XCTAssertEqual(captured.heading, HeadingReading(trueHeadingDeg: 212.5, headingAccuracyDeg: 12, orientation: .portrait,
                                                        deviceClock: moment.addingTimeInterval(-1.25)))
        guard case .allowed(.full, let given?) = captured.location else { return XCTFail("\(captured.location)") }
        XCTAssertEqual(given.latDeg, 30)
        XCTAssertFalse(given.isSimulatedBySoftware)

        let record = try PointingRecordBuilder.build(captured)
        guard case .measured(let measured) = record.heading else { return XCTFail("\(record.heading)") }
        XCTAssertEqual(measured.trueHeadingDeg, 212.5)
        XCTAssertEqual(record.motion.sampleOffsetFromPhotoSec, 0)
        XCTAssertEqual(record.motion.window.sampleCount, 17)
        XCTAssertEqual(PointingValidator.issues(in: try record.wireValue()), [])
    }

    func testUsesTheLastHeadingAndFixNotAfterThePhoto() throws {
        let motion = StandInMotion(Set(AttitudeFrame.allCases))
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
        _ = recorder.start(interface: .portrait)
        let deliverPlace = try XCTUnwrap(place.deliver)
        deliverPlace(.authorization(.authorizedAlways, .reduced))
        // Out of order on purpose: the choice is by the event's own time, not by arrival
        // (the newest by time before the photo arrives neither first nor last)
        deliverPlace(headingEvent(-3, degrees: 10))
        deliverPlace(headingEvent(0, degrees: 40))
        deliverPlace(headingEvent(-1, degrees: 20))
        deliverPlace(headingEvent(-2, degrees: 30))
        deliverPlace(headingEvent(0.5, degrees: 50))
        deliverPlace(fixEvent(-0.5, lat: 3))
        deliverPlace(fixEvent(-4, lat: 1))
        deliverPlace(fixEvent(0.25, lat: 2))
        let captured = capture(recorder)
        XCTAssertEqual(captured.heading?.trueHeadingDeg, 40)
        guard case .allowed(.reduced, let given?) = captured.location else { return XCTFail("\(captured.location)") }
        XCTAssertEqual(given.latDeg, 3)
        XCTAssertEqual(recorder.status.lastHeading?.trueHeadingDeg, 50)
        XCTAssertEqual(recorder.status.lastFix?.latDeg, 2)
        // Only events after the photo: none is used
        let later = try XCTUnwrap(SensorRecorder(motion: StandInMotion(Set(AttitudeFrame.allCases)), place: place))
        _ = later.start(interface: .portrait)
        let deliverLater = try XCTUnwrap(place.deliver)
        deliverLater(.authorization(.authorizedWhenInUse, .full))
        deliverLater(headingEvent(0.001))
        deliverLater(fixEvent(0.001))
        XCTAssertNil(capture(later).heading)
        XCTAssertEqual(capture(later).location, .allowed(.full, fix: nil))
    }

    func testKeepsOnlyTheNewestHeadingsAndFixesItCanHold() throws {
        XCTAssertEqual(SensorRecorder.keptEventCount, 32)
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: StandInMotion(Set(AttitudeFrame.allCases)), place: place))
        _ = recorder.start(interface: .portrait)
        let deliverPlace = try XCTUnwrap(place.deliver)
        deliverPlace(.authorization(.authorizedWhenInUse, .full))
        // 33 headings, the oldest the only one before the photo: once it is pushed out, no heading is before it
        deliverPlace(headingEvent(-1, degrees: 1))
        for index in 1...31 { deliverPlace(headingEvent(Double(index), degrees: Double(index))) }
        XCTAssertEqual(capture(recorder).heading?.trueHeadingDeg, 1)
        deliverPlace(headingEvent(32, degrees: 32))
        XCTAssertNil(capture(recorder).heading)
        // The same for fixes
        deliverPlace(fixEvent(-1, lat: 1))
        for index in 1...31 { deliverPlace(fixEvent(Double(index), lat: 2)) }
        guard case .allowed(_, let kept?) = capture(recorder).location else { return XCTFail("\(capture(recorder).location)") }
        XCTAssertEqual(kept.latDeg, 1)
        deliverPlace(fixEvent(32, lat: 2))
        XCTAssertEqual(capture(recorder).location, .allowed(.full, fix: nil))
    }

    func testTakesAReportAtThePhotosOwnTimeAndOfTwoAtOneTimeTheLaterToArrive() throws {
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: StandInMotion(Set(AttitudeFrame.allCases)), place: place))
        _ = recorder.start(interface: .portrait)
        let deliverPlace = try XCTUnwrap(place.deliver)
        deliverPlace(.authorization(.authorizedWhenInUse, .full))
        deliverPlace(fixEvent(0, lat: 7))
        deliverPlace(headingEvent(-1, degrees: 1))
        deliverPlace(headingEvent(-1, degrees: 2))
        deliverPlace(headingEvent(-2, degrees: 3))
        guard case .allowed(_, let kept?) = capture(recorder).location else { return XCTFail("\(capture(recorder).location)") }
        XCTAssertEqual(kept.latDeg, 7)
        XCTAssertEqual(capture(recorder).heading?.trueHeadingDeg, 2)
        deliverPlace(fixEvent(0, lat: 8))
        deliverPlace(fixEvent(-1, lat: 9))
        guard case .allowed(_, let later?) = capture(recorder).location else { return XCTFail() }
        XCTAssertEqual(later.latDeg, 8)
        XCTAssertEqual(SensorRecorder.newest([Int](), notAfter: moment, time: { _ in self.moment }), nil)
    }

    func testStartsAfreshKeepingNothingOfTheLastStart() throws {
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: StandInMotion(Set(AttitudeFrame.allCases)), place: place))
        _ = recorder.start(interface: .portrait)
        let firstPlace = try XCTUnwrap(place.deliver)
        firstPlace(.authorization(.authorizedWhenInUse, .full))
        firstPlace(headingEvent(-1))
        firstPlace(fixEvent(-1))
        recorder.stop()
        _ = recorder.start(interface: .portrait)
        XCTAssertNil(recorder.status.authorization)
        XCTAssertNil(recorder.status.accuracy)
        XCTAssertNil(recorder.status.lastHeading)
        XCTAssertNil(recorder.status.lastFix)
        // A report of the last start that arrives now is not kept either
        firstPlace(.authorization(.authorizedWhenInUse, .full))
        firstPlace(headingEvent(-1))
        firstPlace(fixEvent(-1))
        XCTAssertNil(recorder.status.authorization)
        XCTAssertNil(capture(recorder).heading)
        XCTAssertEqual(capture(recorder).location, .notAllowed)
        let secondPlace = try XCTUnwrap(place.deliver)
        secondPlace(.authorization(.authorizedWhenInUse, .reduced))
        XCTAssertEqual(capture(recorder).location, .allowed(.reduced, fix: nil))
    }

    func testTakesNoPlaceUntilThePersonAllowsItAndDropsItWhenTheyWithdrawIt() throws {
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: StandInMotion(Set(AttitudeFrame.allCases)), place: place))
        _ = recorder.start(interface: .portrait)
        let deliverPlace = try XCTUnwrap(place.deliver)
        deliverPlace(fixEvent(-1))
        XCTAssertEqual(capture(recorder).location, .notAllowed)
        deliverPlace(.authorization(.notDetermined, .full))
        XCTAssertEqual(capture(recorder).location, .notAllowed)
        deliverPlace(.authorization(.authorizedWhenInUse, .full))
        guard case .allowed(.full, _?) = capture(recorder).location else { return XCTFail() }
        deliverPlace(.authorization(.denied, .full))
        XCTAssertEqual(capture(recorder).location, .notAllowed)
        XCTAssertEqual(recorder.status.authorization, .denied)
    }

    func testIgnoresWhatASourceDeliversOnceStoppedOrFromAnEarlierStart() throws {
        let motion = StandInMotion(Set(AttitudeFrame.allCases))
        let place = StandInPlace()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
        _ = recorder.start(interface: .portrait)
        let firstMotion = try XCTUnwrap(motion.deliver)
        let firstPlace = try XCTUnwrap(place.deliver)
        firstMotion(motionEvent(0))
        recorder.stop()
        firstMotion(motionEvent(step))
        firstPlace(.authorization(.authorizedWhenInUse, .full))
        XCTAssertEqual(recorder.status.samplesKept, 1)
        XCTAssertNil(recorder.status.authorization)
        _ = recorder.start(interface: .portrait)
        firstMotion(motionEvent(2 * step))
        XCTAssertEqual(recorder.status.samplesKept, 0, "a new start keeps nothing of the last")
        try XCTUnwrap(motion.deliver)(motionEvent(3 * step))
        XCTAssertEqual(recorder.status.samplesKept, 1)
    }

    func testCountsTheSamplesItRefusedAndPushedOut() throws {
        let motion = StandInMotion(Set(AttitudeFrame.allCases))
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: StandInPlace(), ringCapacity: 4))
        _ = recorder.start(interface: .portrait)
        let deliver = try XCTUnwrap(motion.deliver)
        // Seven kept in time, four held: two refused (a repeated time, a time that is not a number), three pushed out
        for offset in [0, step, step, 2 * step, .nan, 3 * step, 4 * step, 5 * step, 6 * step] { deliver(motionEvent(offset)) }
        XCTAssertEqual(recorder.status.samplesKept, 4)
        XCTAssertEqual(recorder.status.samplesRefused, 2)
        XCTAssertEqual(recorder.status.samplesOverwritten, 3)
        // A sample in another frame is kept as it is, and the builder gives the reason there is no heading
        let other = try XCTUnwrap(SensorRecorder(motion: StandInMotion([.xArbitraryZVertical]), place: StandInPlace()))
        XCTAssertEqual(other.start(interface: .portrait), .started(.xArbitraryZVertical))
    }

    func testIsSafeToFeedFromSeveralThreadsAtOnce() throws {
        let motion = StandInMotion(Set(AttitudeFrame.allCases))
        let place = StandInPlace()
        // A ring of 400 for 500 samples, so the kept and the pushed-out counts differ from each other and from refused
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place, ringCapacity: 400))
        _ = recorder.start(interface: .portrait)
        let deliverMotion = try XCTUnwrap(motion.deliver)
        let deliverPlace = try XCTUnwrap(place.deliver)
        let photoClock = self.photoClock
        let moment = self.moment
        DispatchQueue.concurrentPerform(iterations: 4) { lane in
            for index in 0..<500 {
                if lane == 0 {
                    deliverMotion(DeviceMotionEvent(timestampSec: photoClock + Double(index) * 0.0078125, frame: .xTrueNorthZVertical,
                                                    quaternion: .init(x: 0, y: 0, z: 0, w: 1), gravityG: .init(x: 0, y: 0, z: -1),
                                                    rotationRateRadPerSec: .init(x: 0, y: 0, z: 0)))
                } else {
                    deliverPlace(.heading(HeadingEvent(trueHeadingDeg: Double(lane), headingAccuracyDeg: 5,
                                                       timestamp: moment.addingTimeInterval(-Double(index)), orientation: .portrait)))
                }
            }
        }
        XCTAssertEqual(recorder.status.samplesKept, 400)
        XCTAssertEqual(recorder.status.samplesOverwritten, 100)
        XCTAssertEqual(recorder.status.samplesRefused, 0)
    }

    func testLeavesNoSourceRunningAfterStopWhateverTheOrderOfStartsAndStops() async throws {
        // Many trials of start, stop and start asked for from three tasks at once; the recorder runs each on the main actor, so
        // after the last stop no source runs, and none was started late by an earlier start
        var leftRunning = 0
        for trial in 0..<3000 {
            let motion = StandInMotion(Set(AttitudeFrame.allCases))
            let place = StandInPlace()
            let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: place))
            await withTaskGroup(of: Void.self) { group in
                group.addTask { @MainActor in _ = recorder.start(interface: trial % 2 == 0 ? .portrait : .landscapeLeft) }
                group.addTask { @MainActor in recorder.stop() }
                group.addTask { @MainActor in _ = recorder.start(interface: .portrait) }
            }
            recorder.stop()
            if motion.running || place.running || recorder.status.running { leftRunning += 1 }
            XCTAssertEqual(motion.started.count - motion.stops, 0, "trial \(trial)")
            XCTAssertEqual(place.orientations.count - place.stops, 0, "trial \(trial)")
        }
        XCTAssertEqual(leftRunning, 0)
    }
}

extension SensorRecorderTests {
    func testDeclaresStartAndStopForTheMainActorOnly() throws {
        // The attributes above each entry point of SensorRecorder, read from the source: @MainActor on both
        let url = repositoryRoot.appendingPathComponent("apps/ios/AlbedianCaptureCore/Sources/AlbedianCaptureCore/SensorMapping.swift")
        let lines = try String(contentsOf: url, encoding: .utf8).components(separatedBy: "\n")
        let recorder = try XCTUnwrap(lines.firstIndex { $0.hasPrefix("public final class SensorRecorder") })
        for entry in ["public func start(", "public func stop("] {
            let at = try XCTUnwrap(lines[recorder...].firstIndex { $0.trimmingCharacters(in: .whitespaces).hasPrefix(entry) }, entry)
            var attributes: [String] = []
            var index = at - 1
            while index > recorder, lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("@") {
                attributes.append(lines[index].trimmingCharacters(in: .whitespaces))
                index -= 1
            }
            XCTAssertTrue(attributes.contains("@MainActor"), "\(entry) \(attributes)")
        }
        // and no other way in starts a source: only start calls a source's start
        let starts = lines.enumerated().filter { $0.element.contains("motion.start(") || $0.element.contains("place.start(") }.map(\.offset)
        let startAt = try XCTUnwrap(lines.firstIndex { $0.trimmingCharacters(in: .whitespaces).hasPrefix("public func start(") })
        let stopAt = try XCTUnwrap(lines.firstIndex { $0.trimmingCharacters(in: .whitespaces).hasPrefix("public func stop(") })
        XCTAssertEqual(starts.count, 2)
        XCTAssertTrue(starts.allSatisfy { $0 > startAt && $0 < stopAt }, "\(starts)")
    }
}
