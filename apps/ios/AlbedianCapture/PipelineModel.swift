import AlbedianCaptureCore
import Foundation
import Observation
import UIKit

/// The pipeline run by hand: sample sensor values, the record built from them, and the upload to a laptop address
/// the person types. Nothing here is measured: the photo is a black image the app draws (`SamplePhoto`) and the
/// readings come from `SampleCapture` (open ocean, the synthetic moment).
@MainActor
@Observable
final class PipelineModel {
    var options = SampleCapture.Options()
    private(set) var photo: Data?
    private(set) var photoSha256: String?
    private(set) var record: PointingRecord?
    private(set) var refusal: PointingBuildError?
    private(set) var issues: [PointingIssue] = []
    private(set) var json = ""
    var address = ""
    private let coordinator = UploadCoordinator()
    private let solver = SolveCoordinator()

    var upload: UploadState? { coordinator.state }

    /// The laptop's solve of the photo once it has it, started by a send that ended stored (REQ-IOS-4.2, 5.1).
    var solve: SolveState? { solver.state }

    /// When the phone asked for the solve, for elapsed time while a stage's total is not known.
    var solveStartedAt: ContinuousClock.Instant? { solver.startedAt }

    var laptop: LaptopAddress? { LaptopAddress(address.trimmingCharacters(in: .whitespaces)) }

    var isSending: Bool { coordinator.isSending }

    func build() {
        let photo = SamplePhoto.png()
        let sha256 = PhotoHash.sha256Hex(photo)
        self.photo = photo
        self.photoSha256 = sha256
        coordinator.reset()
        solver.reset()
        let capture = SampleCapture.capture(photoSha256: sha256, photoWidthPx: SamplePhoto.widthPx, photoHeightPx: SamplePhoto.heightPx,
                                            options: options)
        do throws(PointingBuildError) {
            let record = try PointingRecordBuilder.build(capture)
            self.record = record
            refusal = nil
            let wire = try? record.wireValue()
            issues = wire.map(PointingValidator.issues(in:)) ?? []
            json = wire.map(JSONText.pretty) ?? ""
        } catch {
            record = nil
            refusal = error
            issues = []
            json = ""
        }
    }

    /// Sends the photo and its record, then, once the laptop holds them, asks it to solve the photo (`design.md`,
    /// component 4: upload, record, then solve).
    func send() {
        guard let photo, let laptop else { return }
        solver.reset()
        coordinator.start(photo: photo, contentType: .png, record: record, uploader: CaptureUploader(address: laptop)) { [weak self] last in
            guard let self, case .done(let sha256, _, _) = last else { return }
            self.solver.start(sha256: sha256, client: SolveClient(address: laptop))
        }
    }
}

/// A black image the app draws as the sample photo, so a real image type reaches the laptop: not a photo.
enum SamplePhoto {
    static let widthPx = 64
    static let heightPx = 48

    static func png() -> Data {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let size = CGSize(width: widthPx, height: heightPx)
        return UIGraphicsImageRenderer(size: size, format: format).pngData { context in
            UIColor.black.setFill()
            context.fill(CGRect(origin: .zero, size: size))
        }
    }
}

#if DEBUG
extension PipelineModel {
    /// For simulator captures only: `-AlbedianPipelineNoMotion` builds with the motion stream off (no record, so the
    /// photo goes alone), `-AlbedianPipelineAddress <address>` types the laptop's address, and `-AlbedianPipelineSend`
    /// builds and sends at once.
    func runFromLaunchArguments() {
        let args = ProcessInfo.processInfo.arguments
        if args.contains("-AlbedianPipelineNoMotion") { options.motionRunning = false }
        if let index = args.firstIndex(of: "-AlbedianPipelineAddress"), args.indices.contains(index + 1) { address = args[index + 1] }
        if args.contains("-AlbedianPipelineSend") {
            build()
            send()
        }
    }
}
#endif
