import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The check screen's photo hashing, keeps and counts run off the main actor (mobile-2's fifth version), and the
/// screen's states (`lastOutcome`, `keptCount`, `isBusy`) stay in arrival order without holding the main actor.
final class OffMainActorTests: XCTestCase {
    private var folder: URL!

    override func setUpWithError() throws {
        folder = FileManager.default.temporaryDirectory.appendingPathComponent("albedian-offmain-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: folder)
    }

    /// Runs a main-actor task with a hard bound so no broken queue or latch can hang the suite.
    @MainActor
    private func awaitBounded(
        _ task: Task<Void, Never>,
        seconds: Double = 10,
        file: StaticString = #filePath,
        line: UInt = #line
    ) async {
        let finished = await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                await task.value
                return true
            }
            group.addTask {
                try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
                return false
            }
            let first = await group.next() ?? false
            group.cancelAll()
            return first
        }
        if !finished {
            task.cancel()
            XCTFail("the task did not end within \(seconds) s", file: file, line: line)
        }
    }

    @MainActor
    private func waitFlag(_ flag: BoundBox<Bool>, seconds: Double = 3.5) async -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while Date() < deadline {
            if flag.lock.withLock({ flag.value == true }) { return true }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        return false
    }

    /// Files that count every call and whether any call ran on the main thread, with an optional gate on `names` or `write`.
    final class WatchingFiles: CaptureFiles, @unchecked Sendable {
        let real = SystemCaptureFiles()
        let lock = NSLock()
        var calls = 0
        var onMain = 0
        var events: [String] = []
        var failNames = false
        var corruptWrites = false
        var failRemoves = false
        var onEnterNames: (@Sendable () -> Void)?
        var onEnterWrite: (@Sendable () -> Void)?

        private func note(_ event: String? = nil) {
            lock.withLock {
                calls += 1
                if Thread.isMainThread { onMain += 1 }
                if let event { events.append(event) }
            }
        }

        func write(_ data: Data, to url: URL) throws {
            note("write")
            let hook = lock.withLock { onEnterWrite }
            hook?()
            if lock.withLock({ corruptWrites }), !data.isEmpty {
                var changed = data
                changed[0] ^= 0x01
                return try real.write(changed, to: url)
            }
            try real.write(data, to: url)
        }

        func read(_ url: URL) throws -> Data {
            note()
            return try real.read(url)
        }

        func remove(_ url: URL) throws {
            note()
            if lock.withLock({ failRemoves }) {
                throw NSError(domain: NSCocoaErrorDomain, code: NSFileWriteNoPermissionError)
            }
            try real.remove(url)
        }

        func names(in folder: URL) throws -> [String] {
            note("names")
            let hook = lock.withLock { onEnterNames }
            hook?()
            if lock.withLock({ failNames }) {
                throw NSError(domain: NSCocoaErrorDomain, code: NSFileReadNoPermissionError)
            }
            return try real.names(in: folder)
        }

        func makeFolder(_ url: URL) throws {
            note()
            try real.makeFolder(url)
        }
    }

    private final class StandInMotion: DeviceMotionSource, @unchecked Sendable {
        var availableFrames: Set<AttitudeFrame> { [.xTrueNorthZVertical] }
        private let lock = NSLock()
        private var handler: (@Sendable (DeviceMotionEvent) -> Void)?
        func start(frame: AttitudeFrame, intervalSec: Double, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
            lock.withLock { handler = deliver }
        }
        func stop() { lock.withLock { handler = nil } }
        func emit(_ event: DeviceMotionEvent) { lock.withLock { handler }?(event) }
    }

    private final class StandInPlace: PlaceAndHeadingSource, @unchecked Sendable {
        func start(headingOrientation: HeadingOrientation, deliver: @escaping @Sendable (PlaceEvent) -> Void) {
            deliver(.authorization(.authorizedWhenInUse, .full))
        }
        func stop() {}
    }

    private func photo(_ seed: UInt8) -> Data {
        Data((0..<1200).map { UInt8(truncatingIfNeeded: Int($0) &* 29 &+ Int(seed)) })
    }

    @MainActor
    func testStartsReadingWithNoDiskCallAndCountsOffTheMainThread() async {
        XCTAssertTrue(Thread.isMainThread)
        let files = WatchingFiles()
        let store = CaptureStore(folder: folder, files: files)
        let state = CheckCaptureState(store: store)
        XCTAssertEqual(state.lastOutcome, "No photo yet")
        XCTAssertEqual(state.keptCount, .reading)
        XCTAssertFalse(state.isBusy)
        XCTAssertEqual(files.calls, 0, "creating the screen's state makes no disk call on the main actor")

        await awaitBounded(state.refreshCount())
        XCTAssertEqual(state.keptCount, .counted(whole: 0, notWhole: 0))
        XCTAssertGreaterThan(files.calls, 0)
        XCTAssertEqual(files.onMain, 0, "a file call ran on the main thread")

        files.lock.withLock { files.failNames = true }
        await awaitBounded(state.refreshCount())
        XCTAssertEqual(state.keptCount, .couldNotBeRead)
        XCTAssertEqual(files.onMain, 0)
    }

    @MainActor
    func testHashesBuildsKeepsAndCountsOffTheMainThreadAndMatchesSynchronousCalls() async throws {
        XCTAssertTrue(Thread.isMainThread)
        let files = WatchingFiles()
        let store = CaptureStore(folder: folder, files: files)
        let state = CheckCaptureState(store: store)

        let motion = StandInMotion()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: StandInPlace()))
        recorder.start(interface: .portrait)
        let sample = SampleCapture.capture(photoSha256: String(repeating: "0", count: 64))
        for offset in [-0.04, 0.0, 0.02, 0.06] {
            motion.emit(DeviceMotionEvent(
                timestampSec: sample.photoBootClockSec + offset,
                frame: .xTrueNorthZVertical,
                quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                gravityG: .init(x: 0, y: -0.6, z: -0.8),
                rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
            ))
        }

        let recordBuilderOnMain = BoundBox<Bool>()
        let p1 = photo(1)
        state.beginShot()
        XCTAssertEqual(state.lastOutcome, "Taking the photo")
        XCTAssertTrue(state.isBusy)
        await awaitBounded(state.keepShot(photo: p1, contentType: .heic) {
            recordBuilderOnMain.lock.withLock { recordBuilderOnMain.value = Thread.isMainThread }
            return ShotRecordStep.build(
                photoBytes: p1,
                recorder: recorder,
                photoBootClockSec: sample.photoBootClockSec,
                photoDeviceClock: sample.photoDeviceClock,
                device: sample.device,
                camera: sample.camera,
                exposureSec: 0.02
            )
        })
        XCTAssertEqual(recordBuilderOnMain.lock.withLock { recordBuilderOnMain.value }, false, "record building and hashing ran on the main thread")
        XCTAssertFalse(state.isBusy)
        XCTAssertEqual(state.lastOutcome, "Photo and pointing record kept")
        XCTAssertEqual(state.keptCount, .counted(whole: 1, notWhole: 0))
        let loaded1 = try CaptureStore(folder: folder).load(PhotoHash.sha256Hex(p1))
        XCTAssertEqual(loaded1.photo, p1)
        XCTAssertEqual(loaded1.contentType, .heic)
        let storedWire = try XCTUnwrap(loaded1.recordWire)
        XCTAssertEqual(try JSONText.read(storedWire)["motion"]?["window"]?["sampleCount"], .double(4))

        // Photo without a recorder: kept alone
        let p2 = photo(2)
        await awaitBounded(state.keepShot(photo: p2, contentType: .jpeg) {
            ShotRecordStep.build(
                photoBytes: p2,
                recorder: nil,
                photoBootClockSec: sample.photoBootClockSec,
                photoDeviceClock: sample.photoDeviceClock,
                device: sample.device,
                camera: sample.camera,
                exposureSec: nil
            )
        })
        XCTAssertEqual(state.lastOutcome, "Photo kept, no pointing record")
        XCTAssertEqual(state.keptCount, .counted(whole: 2, notWhole: 0))
        let loaded2 = try CaptureStore(folder: folder).load(PhotoHash.sha256Hex(p2))
        XCTAssertEqual(loaded2.photo, p2)
        XCTAssertEqual(loaded2.contentType, .jpeg)
        XCTAssertNil(loaded2.recordWire)

        // A built record whose JSON wire cannot be written: neither record nor photo is kept
        let p3 = photo(3)
        struct BadWire: Error {}
        await awaitBounded(state.keepShot(photo: p3, contentType: .heic) {
            ShotRecordStep.build(
                photoBytes: p3,
                recorder: recorder,
                photoBootClockSec: sample.photoBootClockSec,
                photoDeviceClock: sample.photoDeviceClock,
                device: sample.device,
                camera: sample.camera,
                exposureSec: 0.01,
                encodeRecord: { _ in throw BadWire() }
            )
        })
        XCTAssertEqual(state.lastOutcome, "Not kept: the pointing record could not be written as JSON")
        XCTAssertEqual(state.keptCount, .counted(whole: 2, notWhole: 0))
        XCTAssertThrowsError(try CaptureStore(folder: folder).load(PhotoHash.sha256Hex(p3)))

        // A failing disk whose written photo is corrupted and stays: matches synchronous keepOutcome, counted as notWhole
        let p4 = photo(4)
        files.lock.withLock {
            files.corruptWrites = true
            files.failRemoves = true
        }
        await awaitBounded(state.keepShot(photo: p4, contentType: .heic) { .none })
        let expected = CheckReadout.keepOutcome(.failure(CaptureStoreFailure(
            reason: .readBackDiffers(file: "\(PhotoHash.sha256Hex(p4)).heic"),
            afterwards: .notWhole,
            stray: []
        )))
        XCTAssertEqual(state.lastOutcome, expected)
        XCTAssertEqual(state.keptCount, .counted(whole: 2, notWhole: 1))
        XCTAssertEqual(files.onMain, 0, "a file call ran on the main thread")
        recorder.stop()
    }

    @MainActor
    func testKeepsStatesInOrderWhileSlowReadsWaitOffTheMainActor() async {
        let files = WatchingFiles()
        let store = CaptureStore(folder: folder, files: files)
        let state = CheckCaptureState(store: store)

        // Hold refreshCount inside names() on the worker queue, prove the main actor is free, and queue a keep behind it
        let enteredNames = BoundBox<Bool>()
        let releaseNames = DispatchSemaphore(value: 0)
        files.lock.withLock {
            files.onEnterNames = {
                guard !Thread.isMainThread else { return }
                enteredNames.lock.withLock { enteredNames.value = true }
                _ = releaseNames.wait(timeout: .now() + 5)
            }
        }
        let refresh = state.refreshCount()
        let reachedWorker = await waitFlag(enteredNames, seconds: 3.5)
        XCTAssertTrue(reachedWorker, "refreshCount did not reach the worker thread while the main actor waited")
        files.lock.withLock { files.onEnterNames = nil }

        let p1 = photo(11)
        state.beginShot()
        XCTAssertEqual(state.lastOutcome, "Taking the photo")
        XCTAssertTrue(state.isBusy)
        let keep1 = state.keepShot(photo: p1, contentType: .heic) { .none }
        XCTAssertTrue(state.isBusy, "isBusy stays true while keepShot is in flight")
        releaseNames.signal()
        await awaitBounded(refresh)
        await awaitBounded(keep1)
        XCTAssertFalse(state.isBusy)
        XCTAssertEqual(state.keptCount, .counted(whole: 1, notWhole: 0), "stale refreshCount overwrote keepShot's count")
        XCTAssertEqual(state.lastOutcome, "Photo kept, no pointing record")

        // Hold Shot 2 inside write(), start Shot 3 on the main actor, release Shot 2: Shot 2 must not overwrite "Taking the photo"
        let enteredWrite = BoundBox<Bool>()
        let releaseWrite = DispatchSemaphore(value: 0)
        files.lock.withLock {
            files.onEnterWrite = {
                guard !Thread.isMainThread else { return }
                enteredWrite.lock.withLock { enteredWrite.value = true }
                _ = releaseWrite.wait(timeout: .now() + 5)
            }
        }
        let p2 = photo(12)
        state.beginShot()
        let keep2 = state.keepShot(photo: p2, contentType: .heic) { .none }
        let reachedWrite = await waitFlag(enteredWrite, seconds: 3.5)
        XCTAssertTrue(reachedWrite, "keepShot did not reach write on the worker thread")
        files.lock.withLock { files.onEnterWrite = nil }

        // A new shot begins, fails in the camera while keep2 is still blocked, and then a third shot begins before keep2 finishes
        state.beginShot()
        let staleFail = state.cameraFailed("the camera did not take it")
        state.beginShot()
        XCTAssertEqual(state.lastOutcome, "Taking the photo")
        releaseWrite.signal()
        await awaitBounded(keep2)
        await awaitBounded(staleFail)
        XCTAssertEqual(state.keptCount, .counted(whole: 2, notWhole: 0))
        XCTAssertEqual(state.lastOutcome, "Taking the photo", "an earlier keep or camera failure overwrote the newer shot's state")
        XCTAssertTrue(state.isBusy, "still taking the newer shot")

        await awaitBounded(state.cameraFailed("this device has no back camera"))
        XCTAssertFalse(state.isBusy)
        XCTAssertEqual(state.lastOutcome, "No photo: this device has no back camera")
        XCTAssertEqual(files.onMain, 0, "a file call ran on the main thread")
    }

    @MainActor
    func testOffMainAndCountWholeSerializeConcurrentWorkOnTheStoresQueue() async {
        final class OverlapProbe: @unchecked Sendable {
            let lock = NSLock()
            var inside = 0
            var maxInside = 0
            func enter() {
                lock.withLock {
                    inside += 1
                    maxInside = max(maxInside, inside)
                }
            }
            func leave() {
                lock.withLock { inside -= 1 }
            }
        }

        let files = WatchingFiles()
        let store = CaptureStore(folder: folder, files: files)
        let probe = OverlapProbe()

        let enteredFirst = BoundBox<Bool>()
        let t1 = Task.detached {
            await store.offMain { _ in
                probe.enter()
                enteredFirst.lock.withLock { enteredFirst.value = true }
                Thread.sleep(forTimeInterval: 0.06)
                probe.leave()
            }
        }
        let reached1 = await waitFlag(enteredFirst)
        XCTAssertTrue(reached1)
        let t2 = Task.detached {
            await store.offMain { _ in
                probe.enter()
                Thread.sleep(forTimeInterval: 0.02)
                probe.leave()
            }
        }
        await t1.value
        await t2.value
        XCTAssertEqual(probe.maxInside, 1, "two offMain tasks ran concurrently on the same store")

        // countWhole() also runs on the same serial queue rather than bypassing offMain
        files.lock.withLock {
            files.onEnterNames = {
                probe.enter()
                Thread.sleep(forTimeInterval: 0.02)
                probe.leave()
            }
        }
        let enteredThird = BoundBox<Bool>()
        let t3 = Task.detached {
            await store.offMain { _ in
                probe.enter()
                enteredThird.lock.withLock { enteredThird.value = true }
                Thread.sleep(forTimeInterval: 0.06)
                probe.leave()
            }
        }
        let reached3 = await waitFlag(enteredThird)
        XCTAssertTrue(reached3)
        let t4 = Task.detached { _ = await store.countWhole() }
        await t3.value
        await t4.value
        files.lock.withLock { files.onEnterNames = nil }
        XCTAssertEqual(probe.maxInside, 1, "countWhole bypassed the store's serial queue")
        XCTAssertEqual(files.onMain, 0)
    }

    @MainActor
    func testShotRecordStepExactWindowBoundariesNilExposureAndUnbuildableCapture() throws {
        let motion = StandInMotion()
        let recorder = try XCTUnwrap(SensorRecorder(motion: motion, place: StandInPlace()))
        recorder.start(interface: .portrait)
        let p = photo(31)
        let sample = SampleCapture.capture(photoSha256: String(repeating: "0", count: 64))

        // Unbuildable capture (0 motion samples) returns .none so the photo is still kept alone, never .couldNotWriteJSON
        let emptyStep = ShotRecordStep.build(
            photoBytes: p,
            recorder: recorder,
            photoBootClockSec: sample.photoBootClockSec,
            photoDeviceClock: sample.photoDeviceClock,
            device: sample.device,
            camera: sample.camera,
            exposureSec: 0.20
        )
        XCTAssertEqual(emptyStep, .none)

        // Exact inclusive window boundaries [-limit, exposureSec + limit] = [-0.05, +0.25] for exposureSec = 0.20
        let boot = 100.0
        let customDevice = DeviceLabels(hardwareModel: "iPhone18,1", systemVersion: "26.5")
        let customCamera = CameraReading(
            position: .back,
            deviceType: .wideAngle,
            activeFormatWidthPx: 4032,
            activeFormatHeightPx: 3024,
            photoWidthPx: 8064,
            photoHeightPx: 6048,
            videoFieldOfViewDeg: 68.5,
            distortionCorrectedFieldOfViewDeg: 67.0,
            geometricDistortionCorrectionEnabled: true,
            videoZoomFactor: 2,
            contentAwareDistortionCorrection: false
        )
        for offset in [-0.06, -0.05, 0.0, 0.25, 0.26] {
            motion.emit(DeviceMotionEvent(
                timestampSec: boot + offset,
                frame: .xTrueNorthZVertical,
                quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                gravityG: .init(x: 0, y: -0.6, z: -0.8),
                rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
            ))
        }
        let boundedStep = ShotRecordStep.build(
            photoBytes: p,
            recorder: recorder,
            photoBootClockSec: boot,
            photoDeviceClock: sample.photoDeviceClock,
            device: customDevice,
            camera: customCamera,
            exposureSec: 0.20
        )
        guard case .wire(let wire) = boundedStep else {
            return XCTFail("expected .wire, got \(boundedStep)")
        }
        XCTAssertNoThrow(try CaptureStore.check(wire, for: PhotoHash.sha256Hex(p)))
        let parsed = try JSONText.read(wire)
        XCTAssertEqual(parsed["capturedAtDeviceClockUTC"]?.stringValue, UTCInstant.string(from: sample.photoDeviceClock))
        XCTAssertEqual(parsed["device"]?["hardwareModel"]?.stringValue, "iPhone18,1")
        XCTAssertEqual(parsed["device"]?["systemVersion"]?.stringValue, "26.5")
        XCTAssertEqual(parsed["camera"]?["photoDimensionsPx"]?["width"], .double(8064))
        XCTAssertEqual(parsed["camera"]?["photoDimensionsPx"]?["height"], .double(6048))
        XCTAssertEqual(parsed["camera"]?["videoZoomFactor"], .double(2))
        XCTAssertEqual(parsed["motion"]?["window"]?["sampleCount"], .double(3))
        XCTAssertEqual(try XCTUnwrap(parsed["motion"]?["window"]?["startOffsetSec"]?.numberValue), -PointingContract.motionSampleOffsetLimitSec, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(parsed["motion"]?["window"]?["endOffsetSec"]?.numberValue), 0.20 + PointingContract.motionSampleOffsetLimitSec, accuracy: 1e-12)
        recorder.stop()

        // Nil exposureSec uses 0 + limit = +0.05: samples at [-0.05, 0.0, +0.05] are kept and -0.06 / +0.06 are outside
        let motion2 = StandInMotion()
        let recorder2 = try XCTUnwrap(SensorRecorder(motion: motion2, place: StandInPlace()))
        recorder2.start(interface: .portrait)
        for offset in [-0.06, -0.05, 0.0, 0.05, 0.06] {
            motion2.emit(DeviceMotionEvent(
                timestampSec: boot + offset,
                frame: .xTrueNorthZVertical,
                quaternion: .init(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                gravityG: .init(x: 0, y: -0.6, z: -0.8),
                rotationRateRadPerSec: .init(x: 0.002, y: -0.001, z: 0.0005)
            ))
        }
        let nilExposureStep = ShotRecordStep.build(
            photoBytes: p,
            recorder: recorder2,
            photoBootClockSec: boot,
            photoDeviceClock: sample.photoDeviceClock,
            device: sample.device,
            camera: sample.camera,
            exposureSec: nil
        )
        guard case .wire(let wireNil) = nilExposureStep else {
            return XCTFail("expected .wire when exposureSec is nil, got \(nilExposureStep)")
        }
        let parsedNil = try JSONText.read(wireNil)
        XCTAssertEqual(parsedNil["motion"]?["window"]?["sampleCount"], .double(3))
        XCTAssertEqual(try XCTUnwrap(parsedNil["motion"]?["window"]?["startOffsetSec"]?.numberValue), -PointingContract.motionSampleOffsetLimitSec, accuracy: 1e-12)
        XCTAssertEqual(try XCTUnwrap(parsedNil["motion"]?["window"]?["endOffsetSec"]?.numberValue), PointingContract.motionSampleOffsetLimitSec, accuracy: 1e-12)
        recorder2.stop()
    }

    @MainActor
    func testPendingChainArrivalOrderAndTwoInFlightKeeps() async throws {
        let files = WatchingFiles()
        let store = CaptureStore(folder: folder, files: files)
        let state = CheckCaptureState(store: store)

        // Part A: refresh0 blocked in names(); cameraFailed clears taking at once, waits behind refresh0 in arrival order,
        // and chains multiple cameraFailed calls in arrival order.
        let enteredNames0 = BoundBox<Bool>()
        let releaseNames0 = DispatchSemaphore(value: 0)
        files.lock.withLock {
            files.onEnterNames = {
                enteredNames0.lock.withLock { enteredNames0.value = true }
                _ = releaseNames0.wait(timeout: .now() + 5)
            }
        }
        let r0 = state.refreshCount()
        let ok0 = await waitFlag(enteredNames0)
        XCTAssertTrue(ok0)
        files.lock.withLock { files.onEnterNames = nil }

        state.beginShot()
        XCTAssertTrue(state.isBusy)
        let cam1 = state.cameraFailed("first")
        XCTAssertFalse(state.isBusy, "cameraFailed clears taking immediately")
        try? await Task.sleep(nanoseconds: 40_000_000)
        XCTAssertEqual(state.lastOutcome, "Taking the photo", "cameraFailed ran ahead of pending refresh0")
        let cam2 = state.cameraFailed("second")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.04) { releaseNames0.signal() }
        await awaitBounded(cam2)
        XCTAssertEqual(state.lastOutcome, "No photo: second", "cameraFailed tasks ran out of arrival order")
        await awaitBounded(r0)
        await awaitBounded(cam1)

        // Queue rA (blocked) -> rB (waiting on rA) -> k1 (waiting on rB) -> rC (waiting on k1) to verify keepShot awaits previous
        files.lock.withLock { files.events.removeAll() }
        let enteredNamesA = BoundBox<Bool>()
        let releaseNamesA = DispatchSemaphore(value: 0)
        files.lock.withLock {
            files.onEnterNames = {
                enteredNamesA.lock.withLock { enteredNamesA.value = true }
                _ = releaseNamesA.wait(timeout: .now() + 5)
            }
        }
        let rA = state.refreshCount()
        let okA = await waitFlag(enteredNamesA)
        XCTAssertTrue(okA)
        files.lock.withLock { files.onEnterNames = nil }

        let rB = state.refreshCount()
        let pA = photo(41)
        let k1 = state.keepShot(photo: pA, contentType: .png) {
            Thread.sleep(forTimeInterval: 0.03)
            return .none
        }
        let rC = state.refreshCount()
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.03) { releaseNamesA.signal() }
        await awaitBounded(rC)
        XCTAssertFalse(state.isBusy, "refreshCount returned while keepShot was still in flight")
        XCTAssertEqual(state.keptCount, .counted(whole: 1, notWhole: 0), "refreshCount ran ahead of pending keepShot")
        await awaitBounded(rA)
        await awaitBounded(rB)
        await awaitBounded(k1)
        let recordedEvents = files.lock.withLock { files.events }
        let firstWriteIndex = try XCTUnwrap(recordedEvents.firstIndex(of: "write"))
        let namesBeforeWrite = recordedEvents[..<firstWriteIndex].filter { $0 == "names" }.count
        // rA ("names") + rB ("names") + keepSync's initial read ("names") = 3 "names" calls before k1's first "write"
        XCTAssertGreaterThanOrEqual(namesBeforeWrite, 3, "keepShot jumped onto the store queue ahead of pending refreshCount rB: \(recordedEvents)")
        XCTAssertEqual(try store.kept().first?.contentType, .png)

        // Part B: two direct keepShot calls (without beginShot) in flight together
        let state2 = CheckCaptureState(store: store)
        state2.beginShot()
        await awaitBounded(state2.cameraFailed("reset"))
        XCTAssertEqual(state2.lastOutcome, "No photo: reset")

        let enteredWrite1 = BoundBox<Bool>()
        let releaseWrite1 = DispatchSemaphore(value: 0)
        let enteredMake2 = BoundBox<Bool>()
        let releaseMake2 = DispatchSemaphore(value: 0)
        files.lock.withLock {
            files.onEnterWrite = {
                enteredWrite1.lock.withLock { enteredWrite1.value = true }
                _ = releaseWrite1.wait(timeout: .now() + 5)
            }
        }
        let d1 = state2.keepShot(photo: photo(71), contentType: .heic) { .none }
        let okW1 = await waitFlag(enteredWrite1)
        XCTAssertTrue(okW1)
        XCTAssertTrue(state2.isBusy, "a single direct keepShot sets isBusy")
        files.lock.withLock { files.onEnterWrite = nil }

        let d2 = state2.keepShot(photo: photo(72), contentType: .heic) {
            enteredMake2.lock.withLock { enteredMake2.value = true }
            _ = releaseMake2.wait(timeout: .now() + 5)
            return .couldNotWriteJSON
        }
        XCTAssertTrue(state2.isBusy, "isBusy stays true while two keepShot calls are in flight")
        releaseWrite1.signal()
        await awaitBounded(d1)
        let okM2 = await waitFlag(enteredMake2)
        XCTAssertTrue(okM2)
        XCTAssertTrue(state2.isBusy, "isBusy became false while second keepShot was still running")
        XCTAssertEqual(state2.keptCount, .counted(whole: 2, notWhole: 0), "superseded keepShot still updates keptCount")
        XCTAssertEqual(state2.lastOutcome, "No photo: reset", "first direct keepShot overwrote lastOutcome after second direct keepShot superseded it")

        DispatchQueue.global().asyncAfter(deadline: .now() + 0.04) { releaseMake2.signal() }
        await awaitBounded(d2)
        XCTAssertFalse(state2.isBusy)
        XCTAssertEqual(state2.keptCount, .counted(whole: 2, notWhole: 0))
        XCTAssertEqual(state2.lastOutcome, "Not kept: the pointing record could not be written as JSON")
    }
}
