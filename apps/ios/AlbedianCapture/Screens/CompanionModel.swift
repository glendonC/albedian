import AVFoundation
import AlbedianCaptureCore
import Foundation
import Observation
import UIKit

/// A photo the phone kept with its record (`CaptureStore`), as the camera wrote it.
struct KeptShot: Equatable {
    let photo: Data
    let contentType: PhotoContentType
    let recordWire: Data?
    let sha256: String
}

/// The approved iPhone moment "arriving and bringing a night" (UIUX-arrive-READY iPhone 1, 2, 5, 6, with
/// UIUX-credit, UIUX-connect and UIUX-unreachable), one screen at a time. Every state is what the camera, the store,
/// the connection or the laptop reported; nothing is counted or timed by the app itself.
@MainActor
@Observable
final class CompanionModel {
    enum Screen: Equatable {
        case home, credits, aim, holdStill, afterShot, pairing, waiting, stopped, result, kept

        /// Its place in the system's navigation stack (MOTION-READY-iphone section 1); nil for home, the root.
        var place: CompanionPlace? {
            switch self {
            case .home: nil
            case .credits: .credits
            case .kept: .kept
            case .aim, .holdStill, .afterShot: .capture
            case .pairing: .pairing
            case .waiting: .sent
            case .stopped, .result: .result
            }
        }
    }

    private(set) var screen: Screen = .home {
        didSet {
            path = CompanionPlace.path(path, to: screen.place)
            if screen.place == .capture { captureScreen = screen }
            if screen.place == .result { resultScreen = screen }
        }
    }
    /// The navigation stack's path: forward screens pushed, going back popped, by the system (MOTION-READY-iphone).
    private(set) var path: [CompanionPlace] = []
    /// The screen each shared place last showed, so a place below the top keeps what it showed.
    private(set) var captureScreen: Screen = .aim
    private(set) var resultScreen: Screen = .result

    /// The stack's own change of path: the back button and its swipe are hidden, so this keeps the path as the system has it.
    func navigated(to newPath: [CompanionPlace]) { path = newPath }
    private(set) var shot: KeptShot?
    /// Under the photo after a send the laptop did not answer (UIUX-unreachable).
    private(set) var unreachable = false
    private(set) var result: (readout: SolutionReadout, frame: StoredFrame, facts: AssetFacts)?
    /// The failed screen's sentence (UIUX-iphone-solve-READY, 3): the job's own, or the laptop's own words for a refusal.
    private(set) var stoppedSentence: String?
    /// What "Try again" on the failed screen does: the solve again, or the send again.
    private var retry: (sha256: String, address: LaptopAddress, solveOnly: Bool)?
    /// The text typed after "http://" on the connect screen; typing again brings the hint back.
    var typedAddress = "" { didSet { if typedAddress != oldValue { addressRefused = false } } }
    /// INTERIM (UIUX-iphone-connect-QUESTION, D11, for the owner's review): Connect was pressed with an address that is
    /// not on the local network, so nothing was sent and the hint gives way to one line.
    private(set) var addressRefused = false
    private(set) var cameraProblem: CameraSession.Failure?
    /// D8, INTERIM: the person refused the camera (or it is restricted), so aiming shows why in place of the preview.
    private(set) var cameraRefused = false
    /// The page the Safari view shows, set only for a page `SafariViewGate` allows.
    var openPage: OutsidePageItem?

    let camera = CameraSession()
    private var recorder: SensorRecorder?
    private let store: CaptureStore
    /// Each capture's send state, kept across launches (task 9).
    private let ledger: SendLedger
    private let pairing: PairingKeeper
    private let uploads = UploadCoordinator()
    private let solver = SolveCoordinator()
    private var shooting = false
    /// The long exposure under way (mobile-7): its length as the device set it, when the camera began it, and the
    /// attitude then, for the second crosshair; a second tap stops, and the photo then in flight is not kept.
    private(set) var exposureSec: Double?
    private(set) var exposureBegan: Date?
    private(set) var startAttitude: PointingRecord.Quaternion?
    private var stopRequested = false
    /// D13, INTERIM (UIUX-iphone-kept-QUESTION): photos kept from earlier sessions and never sent, oldest first, and while
    /// they go, how many of them have been sent.
    private(set) var keptNotSent: [String] = []
    private(set) var keptSending: (sent: Int, total: Int)?
    private var keptMode = false
    /// Debug builds only: the stand-in's fixed time into the exposure, so its capture holds still.
    private(set) var standInElapsedSec: Double?

    init(pairing: PairingKeeper = PairingKeeper()) {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        store = CaptureStore(folder: support.appendingPathComponent("Captures"))
        ledger = SendLedger(folder: support.appendingPathComponent("Sends"))
        self.pairing = pairing
        typedAddress = pairing.kept.map { String($0.description.dropFirst(PairingKeeper.fixedScheme.count)) } ?? ""
    }

    /// A send or its solve is under way: Send and Try again take no second press.
    var isBusy: Bool { uploads.isSending || solver.isSolving }

    /// The wait's row: the step the job is on, once a job has been read (UIUX-iphone-solve-READY, 1).
    var waitRow: WaitRow? {
        if case .running(let job)? = solver.state { return WaitRow.current(job) }
        return nil
    }

    /// When the phone asked for the solve, for a step that reports no total.
    var solveStartedAt: ContinuousClock.Instant? { solver.startedAt }

    // MARK: Moving between screens

    func showCredits() { screen = .credits }
    func showHome() { stopAiming(); screen = .home }

    func startAiming() {
        screen = .aim
        guard recorder == nil else { return }
        let place = CoreLocationSource()
        place.onCalibrationAsked = { [weak self] in self?.compassAskedAt = Date() }
        recorder = SensorRecorder(motion: CoreMotionSource(), place: place)
        recorder?.start(interface: .portrait)
        let access = AVCaptureDevice.authorizationStatus(for: .video)
        cameraRefused = access == .denied || access == .restricted
        guard !cameraRefused else { return }
        cameraProblem = camera.configure()
        if cameraProblem == nil { camera.start() }
        startSky()
    }

    /// D8, INTERIM: the line under the aiming preview, what the photo's record would say of its place and heading now.
    var aimingLine: String? { recorder?.aimingLine(at: Date()) }

    // MARK: The aim overlay (mobile-5)

    /// The marks the kept laptop last predicted for the phone's time and place, every one `predicted`; none without a
    /// kept laptop, while the laptop cannot be reached, or while the record would hold no place or no heading.
    private(set) var skyMarks: [SkyMark] = []
    /// The capture coach's sky (D29): the same reply's predicted darkness and Moon; nil whenever the marks are none.
    private(set) var skyConditions: SkyConditions?
    /// When iOS last asked for a heading calibration while aiming.
    private(set) var compassAskedAt: Date?
    /// The heading accuracy the marks were asked with, for the line under the preview.
    private(set) var skyHeadingAccuracyDeg: Double?
    private var skyTask: Task<Void, Never>?

    /// While aiming, asks the kept laptop for the sky every `SkyContextRequest.refreshSec`, and at once when the place and
    /// heading first come; clears the marks whenever the place or heading goes, or the laptop does not answer.
    private func startSky() {
        skyTask?.cancel()
        skyMarks = []
        skyConditions = nil
        skyHeadingAccuracyDeg = nil
        guard let address = pairing.kept else { return }
        let client = SkyContextClient(address: address)
        skyTask = Task { @MainActor [weak self] in
            var askedAt: Date?
            while !Task.isCancelled {
                guard let self else { return }
                let now = Date()
                if let sky = self.aimingSkyNow() {
                    if askedAt.map({ now.timeIntervalSince($0) >= SkyContextRequest.refreshSec }) ?? true {
                        askedAt = now
                        let reply = await client.context(at: now, latDeg: sky.latDeg, lonDeg: sky.lonDeg)
                        guard !Task.isCancelled else { return }
                        let marks = reply.flatMap(SkyContextReader.marks)
                        self.skyMarks = marks ?? []
                        self.skyConditions = reply.flatMap(SkyConditions.read)
                        self.skyHeadingAccuracyDeg = marks == nil ? nil : sky.headingAccuracyDeg
                    }
                } else {
                    askedAt = nil
                    self.skyMarks = []
                    self.skyConditions = nil
                    self.skyHeadingAccuracyDeg = nil
                }
                try? await Task.sleep(for: .seconds(1))
            }
        }
    }

    private func stopAiming() {
        skyTask?.cancel()
        skyTask = nil
        skyMarks = []
        skyConditions = nil
        compassAskedAt = nil
        skyHeadingAccuracyDeg = nil
        recorder?.stop()
        recorder = nil
        camera.stop()
    }

    /// Take again: back to aiming; the kept photo stays on the phone.
    func takeAgain() {
        uploads.reset()
        solver.reset()
        unreachable = false
        shot = nil
        startAiming()
    }

    func close() {
        uploads.reset()
        solver.reset()
        unreachable = false
        shot = nil
        result = nil
        showHome()
    }

    // MARK: The shot

    /// The capture button: one long exposure, with "Hold still" while it runs (arrive iPhone 4).
    func shoot() {
        guard !shooting, cameraProblem == nil else { return }
        shooting = true
        stopRequested = false
        exposureSec = nil
        exposureBegan = nil
        startAttitude = nil
        screen = .holdStill
        camera.shootLong(began: { [weak self] seconds in
            Task { @MainActor in self?.began(seconds) }
        }, { [weak self] result in
            Task { @MainActor in self?.keep(result) }
        })
    }

    private func began(_ seconds: Double) {
        exposureSec = seconds
        exposureBegan = Date()
        startAttitude = currentAttitude
    }

    /// The second tap: back to aiming; the photo the camera is taking is not kept.
    func stopExposure() {
        stopRequested = true
        screen = .aim
    }

    /// The newest attitude Core Motion reported.
    var currentAttitude: PointingRecord.Quaternion? {
        #if DEBUG
        if let standIn = standInAim { return standIn.attitude }
        #endif
        return recorder?.status.lastSample?.quaternion
    }

    /// The field of view across the aiming preview.
    var previewFovDeg: Double? {
        #if DEBUG
        if let standIn = standInAim { return standIn.fovDeg }
        #endif
        return camera.previewAcrossFieldOfViewDeg
    }

    /// The place and heading the overlay is asked with, while a photo's record would hold both.
    private func aimingSkyNow() -> AimingSky? {
        #if DEBUG
        if let standIn = standInAim { return standIn.sky }
        #endif
        return recorder?.aimingSky(at: Date())
    }

    private func keep(_ result: Result<CameraSession.Shot, CameraSession.Failure>) {
        guard !stopRequested, case .success(let taken) = result else {
            shooting = false
            if screen == .holdStill { screen = .aim }
            return
        }
        let recorder = recorder
        let device = DeviceLabels(hardwareModel: CaptureCheckModel.hardwareModel(), systemVersion: UIDevice.current.systemVersion)
        let step = ShotRecordStep.build(photoBytes: taken.bytes, recorder: recorder, photoBootClockSec: taken.photoBootClockSec,
                                        photoDeviceClock: taken.photoDeviceClock, device: device, camera: taken.camera,
                                        exposureSec: taken.exposureSec)
        let wire: Data?
        switch step {
        case .couldNotWriteJSON:
            shooting = false
            screen = .aim
            return
        case .none: wire = nil
        case .wire(let bytes): wire = bytes
        }
        let store = store
        let photo = taken.bytes
        let type = taken.contentType
        Task { @MainActor [weak self] in
            let kept: StoredCapture? = await store.offMain { store in
                do throws(CaptureStoreFailure) { return try store.keep(photo: photo, contentType: type, recordWire: wire) } catch { return nil }
            }
            guard let self else { return }
            self.shooting = false
            guard let kept, !self.stopRequested else {
                if self.screen == .holdStill { self.screen = .aim }
                return
            }
            self.show(KeptShot(photo: photo, contentType: type, recordWire: wire, sha256: kept.sha256))
        }
    }

    private func show(_ kept: KeptShot) {
        stopAiming()
        shot = kept
        unreachable = false
        screen = .afterShot
    }

    // MARK: Sending, the solve and the result

    /// Send: straight to the kept laptop, or, the first time, the screen where the address is typed (UIUX-connect).
    func send() {
        guard let address = pairing.kept else {
            screen = .pairing
            return
        }
        send(to: address)
    }

    /// "Connect": the typed address, used for this send and kept once the send goes through.
    func useTypedAddress() {
        guard let address = PairingKeeper.address(typed: typedAddress) else {
            addressRefused = true
            return
        }
        if connectsForKept { return sendKept(to: address) }
        screen = .afterShot
        send(to: address)
    }

    func changeAddress() { screen = .pairing }

    /// "Open" on the result: the photo's reveal on the paired laptop's web app (mobile-4b).
    func openReveal() {
        guard let shot, let laptop = pairing.kept, let url = RevealPage.address(of: shot.sha256, on: laptop) else { return }
        show(url)
    }

    /// A Credits row's link.
    func openCredit(_ page: OutsidePage) { show(page.address) }

    private func show(_ url: URL) {
        guard SafariViewGate.permits(url, paired: pairing.kept) else { return }
        openPage = OutsidePageItem(url: url)
    }

    func back() { screen = shot == nil ? .home : .afterShot }

    private func send(to address: LaptopAddress) {
        guard let shot, !isBusy else { return }
        unreachable = false
        solver.reset()
        uploads.start(photo: shot.photo, contentType: shot.contentType, recordWire: shot.recordWire,
                      uploader: CaptureUploader(address: address)) { [weak self] last in
            guard let self else { return }
            self.ledger.record(last, for: shot.sha256)
            switch SendEnd.after(last) {
            case .solve(let sha256)?:
                self.pairing.keep(address)
                self.startSolve(sha256: sha256, address: address)
            case .unreachable?:
                self.unreachable = true
            case .notSent?:
                // A photo the laptop refused, in its own words, follows the failed screen; a refused record's sentence is
                // review question C9, so that one leaves the screen as it is
                if case .failed(.refused(.photo, _, let detail?)) = last { self.stop(detail, sha256: shot.sha256, address: address, solveOnly: false) }
            case nil:
                break
            }
        }
    }

    private func startSolve(sha256: String, address: LaptopAddress) {
        stoppedSentence = nil
        screen = .waiting
        solver.start(sha256: sha256, client: SolveClient(address: address)) { [weak self] end in self?.solved(end, sha256: sha256, address: address) }
    }

    private func solved(_ end: SolveState, sha256: String, address: LaptopAddress) {
        switch end {
        case .solved(_, let readout, let frame, let facts):
            result = (readout, frame, facts)
            screen = .result
        case .failed(.networkError), .failed(.timedOut), .failed(.connectionClosed), .failed(.notThePairedPeer):
            unreachable = true
            screen = .afterShot
        case .failed(.jobDidNotSucceed(let job)):
            if let sentence = job.error { stop(sentence, sha256: sha256, address: address, solveOnly: true) } else { screen = .afterShot }
        case .failed(.refused(_, let detail?)):
            stop(detail, sha256: sha256, address: address, solveOnly: true)
        default:
            screen = .afterShot
        }
    }

    private func stop(_ sentence: String, sha256: String, address: LaptopAddress, solveOnly: Bool) {
        stoppedSentence = sentence
        retry = (sha256, address, solveOnly)
        screen = .stopped
    }

    /// "Try again" on the failed screen: the solve again for a job that failed, the send again for a refused photo.
    func tryAgainAfterStop() {
        guard let retry, !isBusy else { return }
        if retry.solveOnly { startSolve(sha256: retry.sha256, address: retry.address) } else { screen = .afterShot; send(to: retry.address) }
    }

    #if DEBUG
    /// `-AlbedianAimStandIn <latDeg> <lonDeg> <azDeg> <altDeg> <fovDeg>`: aiming with a phone held still toward that
    /// azimuth and altitude at that place, a heading of ±12 degrees (the record's own number) and that field of view, so
    /// the overlay can be seen in the simulator, which has no camera, motion or compass. A stand-in, never a reading.
    private(set) var standInAim: (attitude: PointingRecord.Quaternion, sky: AimingSky, fovDeg: Double)?
    #endif

    #if DEBUG
    /// `-AlbedianCoachStandIn moving` or `compass` (either or both): the capture coach as if the phone moved, or iOS asked
    /// for a heading calibration, which the simulator never does. A stand-in for captures, never a reading.
    private(set) var standInCoach: Set<String> = []
    #endif

    /// The capture coach's lines now (D29): the compass while iOS's request stands (until a heading within the record's
    /// limit arrives after it), the Moon from the sky context, and steadiness from the last half second of motion.
    func coachLines() -> [CoachLine] {
        var compassAsked = false
        if let asked = compassAskedAt {
            let settled = recorder?.status.lastHeading.map {
                $0.timestamp > asked && $0.headingAccuracyDeg >= 0 && $0.headingAccuracyDeg <= PointingContract.headingAccuracyLimitDeg
            } ?? false
            compassAsked = !settled
        }
        var moving = CoachSteadiness.moving(recorder?.recentMotion(seconds: CoachSteadiness.windowSec) ?? [],
                                            exposureSec: camera.comingExposureSec, focalPx: camera.photoFocalPx)
        #if DEBUG
        compassAsked = compassAsked || standInCoach.contains("compass")
        moving = moving || standInCoach.contains("moving")
        #endif
        return Coach.lines(compassAsked: compassAsked, conditions: skyConditions, moving: moving)
    }

    #if DEBUG
    /// For simulator captures only (the simulator has no camera): `-AlbedianShotFile <path>` takes a photo file from the
    /// Mac as if the camera had written it, with no record, and opens the screen after the shot; `-AlbedianSendShot`
    /// then sends it. `-AlbedianOpen credits|pairing` opens that screen.
    func runFromLaunchArguments() {
        let args = ProcessInfo.processInfo.arguments
        // `-AlbedianSolveStored <sha256>` follows the solve of a file the kept laptop already holds (a clip sent there by
        // other means), so the runner's records of a clip's job can be seen through the phone's reader (mobile-9)
        if let index = args.firstIndex(of: "-AlbedianSolveStored"), args.indices.contains(index + 1), let laptop = pairing.kept,
           PhotoHash.isContractHash(args[index + 1]) {
            startSolve(sha256: args[index + 1], address: laptop)
            return
        }
        for (index, flag) in args.enumerated() where flag == "-AlbedianCoachStandIn" && args.indices.contains(index + 1) {
            standInCoach.insert(args[index + 1])
        }
        if let index = args.firstIndex(of: "-AlbedianAimStandIn"), args.indices.contains(index + 5) {
            let n = args[(index + 1)...(index + 5)].compactMap(Double.init)
            if n.count == 5 {
                standInAim = (SkyProjection.uprightAttitude(towardAzDeg: n[2], pitchUpDeg: n[3]),
                              AimingSky(latDeg: n[0], lonDeg: n[1], headingAccuracyDeg: 12), n[4])
                screen = .aim
                startSky()
                // `-AlbedianSequence <photo path>` then plays the capture's two changes for a look at their motion
                // (MOTION-READY-iphone section 3): Hold still 3 s in, with the mockup's 1.1 s exposure, and after the shot
                // with that photo 2 s later. A stand-in, never a capture
                if let at = args.firstIndex(of: "-AlbedianSequence"), args.indices.contains(at + 1),
                   let photo = FileManager.default.contents(atPath: args[at + 1]) {
                    Task { @MainActor [weak self] in
                        try? await Task.sleep(for: .seconds(3))
                        guard let self else { return }
                        self.exposureSec = 1.1
                        self.exposureBegan = Date()
                        self.screen = .holdStill
                        try? await Task.sleep(for: .seconds(2))
                        self.show(KeptShot(photo: photo, contentType: .jpeg, recordWire: nil, sha256: PhotoHash.sha256Hex(photo)))
                    }
                }
                return
            }
        }
        // The hold-still layout with the mockup's own numbers (1.1 s, 0.6 s in), for the owner's look at it in the
        // simulator, which has no camera: a stand-in, never a capture
        if args.contains("-AlbedianHoldStillLayout") {
            screen = .holdStill
            exposureSec = 1.1
            exposureBegan = Date()
            standInElapsedSec = 0.6
            return
        }
        if let index = args.firstIndex(of: "-AlbedianOpen"), args.indices.contains(index + 1) {
            screen = args[index + 1] == "credits" ? .credits : args[index + 1] == "pairing" ? .pairing : screen
        }
        // `-AlbedianKeepFile <path>` (any number of times) keeps a file from the Mac in the store as a capture the camera
        // could have written, with no record, so the kept photos can be seen in the simulator
        for (index, flag) in args.enumerated() where flag == "-AlbedianKeepFile" && args.indices.contains(index + 1) {
            guard let photo = FileManager.default.contents(atPath: args[index + 1]) else { continue }
            let name = args[index + 1].lowercased()
            let type: PhotoContentType = name.hasSuffix(".heic") ? .heic : name.hasSuffix(".png") ? .png : .jpeg
            do throws(CaptureStoreFailure) { _ = try store.keep(photo: photo, contentType: type, recordWire: nil) } catch {}
        }
        if args.contains("-AlbedianKeepFile") { refreshKept() }
        // The sending row's layout with the mockup's own numbers (1 of 3), for the owner's look at it: on the Mac's own
        // loopback the photos go too fast to see it. A stand-in, never a send
        if args.contains("-AlbedianKeptSendingLayout"), let path = args.firstIndex(of: "-AlbedianShotFile").map({ args[$0 + 1] }),
           let photo = FileManager.default.contents(atPath: path) {
            shot = KeptShot(photo: photo, contentType: .jpeg, recordWire: nil, sha256: PhotoHash.sha256Hex(photo))
            keptSending = (1, 3)
            screen = .kept
            return
        }
        // `-AlbedianTypeAddress <text>` types into the connect screen's field and presses Connect, as a person would
        if let index = args.firstIndex(of: "-AlbedianTypeAddress"), args.indices.contains(index + 1) {
            typedAddress = args[index + 1]
            useTypedAddress()
        }
        guard let index = args.firstIndex(of: "-AlbedianShotFile"), args.indices.contains(index + 1),
              let photo = FileManager.default.contents(atPath: args[index + 1]) else { return }
        let name = args[index + 1].lowercased()
        let type: PhotoContentType = name.hasSuffix(".heic") ? .heic : name.hasSuffix(".png") ? .png : .jpeg
        show(KeptShot(photo: photo, contentType: type, recordWire: nil, sha256: PhotoHash.sha256Hex(photo)))
        if args.contains("-AlbedianSendShot") { send() }
    }
    #endif
}

// MARK: D13, INTERIM: photos kept from earlier sessions and never sent

extension CompanionModel {
    /// Reads which kept photos the laptop is not known to hold, oldest first (`CaptureStore.keptOldestFirst`, `SendLedger`).
    func refreshKept() {
        let store = store
        let ledger = ledger
        Task { @MainActor [weak self] in
            let hashes: [String] = await store.offMain { store in
                do throws(CaptureStoreError) { return ledger.notSent(among: try store.keptOldestFirst().map(\.sha256)) } catch { return [] }
            }
            self?.keptNotSent = hashes
        }
    }

    /// The home's pill: the after-the-shot screen on the oldest kept photo.
    func openKept() {
        guard let oldest = keptNotSent.first, let kept = load(oldest) else { return }
        shot = kept
        unreachable = false
        keptSending = nil
        screen = .kept
    }

    /// Send: every kept photo, oldest first, to the kept laptop; the first time, the connect screen first.
    func sendKept() {
        guard keptSending == nil, !isBusy else { return }
        guard let address = pairing.kept else {
            keptMode = true
            screen = .pairing
            return
        }
        sendKept(to: address)
    }

    func sendKept(to address: LaptopAddress) {
        keptMode = false
        screen = .kept
        let queue = keptNotSent
        keptSending = (0, queue.count)
        sendNextKept(queue, index: 0, address: address)
    }

    private func sendNextKept(_ queue: [String], index: Int, address: LaptopAddress) {
        guard index < queue.count else {
            keptSending = nil
            keptNotSent = []
            refreshKept()
            showHome()
            return
        }
        guard let kept = load(queue[index]) else { return sendNextKept(queue, index: index + 1, address: address) }
        uploads.start(photo: kept.photo, contentType: kept.contentType, recordWire: kept.recordWire,
                      uploader: CaptureUploader(address: address)) { [weak self] last in
            guard let self else { return }
            self.ledger.record(last, for: kept.sha256)
            switch SendEnd.after(last) {
            case .solve?:
                self.pairing.keep(address)
                self.keptSending = (index + 1, queue.count)
                self.sendNextKept(queue, index: index + 1, address: address)
            case .unreachable?:
                // The approved "Can't reach the laptop" on the photo that did not go; it stays kept
                self.keptSending = nil
                self.shot = kept
                self.unreachable = true
                self.screen = .afterShot
            case .notSent?:
                self.keptSending = nil
                self.shot = kept
                if case .failed(.refused(.photo, _, let detail?)) = last {
                    self.stop(detail, sha256: kept.sha256, address: address, solveOnly: false)
                } else {
                    self.screen = .afterShot
                }
            case nil:
                break
            }
        }
    }

    /// Whether the connect screen was opened by the kept photos' Send.
    var connectsForKept: Bool { keptMode }

    private func load(_ sha256: String) -> KeptShot? {
        do throws(CaptureStoreError) {
            let kept = try store.load(sha256)
            return KeptShot(photo: kept.photo, contentType: kept.contentType, recordWire: kept.recordWire, sha256: sha256)
        } catch {
            return nil
        }
    }
}
