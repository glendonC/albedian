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
    enum Screen: Equatable { case home, credits, aim, afterShot, pairing, result }

    private(set) var screen: Screen = .home
    private(set) var shot: KeptShot?
    /// Under the photo after a send the laptop did not answer (UIUX-unreachable).
    private(set) var unreachable = false
    private(set) var result: (readout: SolutionReadout, frame: StoredFrame)?
    /// The text typed after "http://" on the connect screen.
    var typedAddress = ""
    private(set) var cameraProblem: CameraSession.Failure?

    let camera = CameraSession()
    private var recorder: SensorRecorder?
    private let store: CaptureStore
    private let pairing: PairingKeeper
    private let uploads = UploadCoordinator()
    private let solver = SolveCoordinator()
    private var shooting = false

    init(pairing: PairingKeeper = PairingKeeper()) {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        store = CaptureStore(folder: support.appendingPathComponent("Captures"))
        self.pairing = pairing
        typedAddress = pairing.kept.map { String($0.description.dropFirst(PairingKeeper.fixedScheme.count)) } ?? ""
    }

    /// A send or its solve is under way: Send and Try again take no second press.
    var isBusy: Bool { uploads.isSending || solver.isSolving }

    // MARK: Moving between screens

    func showCredits() { screen = .credits }
    func showHome() { stopAiming(); screen = .home }

    func startAiming() {
        screen = .aim
        guard recorder == nil else { return }
        recorder = SensorRecorder(motion: CoreMotionSource(), place: CoreLocationSource())
        recorder?.start(interface: .portrait)
        cameraProblem = camera.configure()
        if cameraProblem == nil { camera.start() }
    }

    private func stopAiming() {
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

    func shoot() {
        guard !shooting, cameraProblem == nil else { return }
        shooting = true
        camera.shoot { [weak self] result in
            Task { @MainActor in self?.keep(result) }
        }
    }

    private func keep(_ result: Result<CameraSession.Shot, CameraSession.Failure>) {
        guard case .success(let taken) = result else {
            shooting = false
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
            guard let kept else { return }
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
        guard let address = PairingKeeper.address(typed: typedAddress) else { return }
        screen = .afterShot
        send(to: address)
    }

    func changeAddress() { screen = .pairing }

    func back() { screen = shot == nil ? .home : .afterShot }

    private func send(to address: LaptopAddress) {
        guard let shot, !isBusy else { return }
        unreachable = false
        solver.reset()
        uploads.start(photo: shot.photo, contentType: shot.contentType, recordWire: shot.recordWire,
                      uploader: CaptureUploader(address: address)) { [weak self] last in
            guard let self else { return }
            switch SendEnd.after(last) {
            case .solve(let sha256)?:
                self.pairing.keep(address)
                self.solver.start(sha256: sha256, client: SolveClient(address: address)) { [weak self] end in self?.solved(end) }
            case .unreachable?:
                self.unreachable = true
            case .notSent?, nil:
                break
            }
        }
    }

    private func solved(_ end: SolveState) {
        switch end {
        case .solved(_, let readout, let frame):
            result = (readout, frame)
            screen = .result
        case .failed(.networkError), .failed(.timedOut), .failed(.connectionClosed), .failed(.notThePairedPeer):
            unreachable = true
        default:
            break
        }
    }

    #if DEBUG
    /// For simulator captures only (the simulator has no camera): `-AlbedianShotFile <path>` takes a photo file from the
    /// Mac as if the camera had written it, with no record, and opens the screen after the shot; `-AlbedianSendShot`
    /// then sends it. `-AlbedianOpen credits|pairing` opens that screen.
    func runFromLaunchArguments() {
        let args = ProcessInfo.processInfo.arguments
        if let index = args.firstIndex(of: "-AlbedianOpen"), args.indices.contains(index + 1) {
            screen = args[index + 1] == "credits" ? .credits : args[index + 1] == "pairing" ? .pairing : screen
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
