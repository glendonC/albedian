import AVFoundation
import AlbedianCaptureCore
import Observation
import SwiftUI
import UIKit

/// The check screen (unit mobile-2): the sensors' readings as they arrive and a shot with the real camera, kept on the
/// phone with its record. Plain layout in the system's default style; only the capture button follows the approved
/// control. It shows no place: only whether one is allowed and its stated accuracy. On a device only for the camera
/// and the sensors; in the simulator it says there is no camera.
@MainActor
@Observable
final class CaptureCheckModel {
    private(set) var status: SensorRecorder.Status?
    private(set) var cameraProblem: CameraSession.Failure?
    let captures: CheckCaptureState
    #if DEBUG
    /// Every sentence and word the screen can show, for the owner's captures; empty unless asked for at launch.
    private(set) var samples: [String] = []
    private(set) var sampleRow: Int?
    #endif
    private(set) var gravityRange = GravityRange()
    private(set) var started = false

    let camera = CameraSession()
    private var recorder: SensorRecorder?
    private var refresh: Timer?

    init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        captures = CheckCaptureState(store: CaptureStore(folder: support.appendingPathComponent("Captures")))
        #if DEBUG
        // Only for the owner's look at the wording, in a debug build: with this launch argument the screen lists every
        // sentence and word it can show, made up, under a heading that says so
        if ProcessInfo.processInfo.arguments.contains("-AlbedianSampleSentences") {
            let failures: [CameraSession.Failure] = [.noCamera, .cameraNotUsable(code: -11800), .photoNotTaken(code: -11803), .noBytes,
                                                     .noClockToMatch, .clockNotANumber]
            let usageKeys = ["NSLocationWhenInUseUsageDescription", "NSCameraUsageDescription", "NSMotionUsageDescription"]
            let usageTexts = usageKeys.compactMap { Bundle.main.object(forInfoDictionaryKey: $0) as? String }
            samples = ["No photo yet", "Taking the photo", "Not kept: the pointing record could not be written as JSON",
                       "reading", "could not be read"]
                + failures.map { "No photo: \(Self.describe($0))" } + failures.map { "Camera: \(Self.describe($0))" }
                + CheckReadout.everyKeepOutcome() + CheckReadout.everyValueWord() + usageTexts
            let args = ProcessInfo.processInfo.arguments
            if let flag = args.firstIndex(of: "-AlbedianSampleRow"), flag + 1 < args.count {
                sampleRow = Int(args[flag + 1])
            }
        }
        #endif
    }

    func start() {
        guard !started else { return }
        started = true
        captures.refreshCount()
        gravityRange = GravityRange()
        recorder = SensorRecorder(motion: CoreMotionSource(), place: CoreLocationSource())
        recorder?.start(interface: .portrait)
        cameraProblem = camera.configure()
        if cameraProblem == nil { camera.start() }
        refresh = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.refreshStatus() }
        }
    }

    private func refreshStatus() {
        status = recorder?.status
        gravityRange.add(CheckReadout.gravityLength(status?.lastSample))
    }

    func stop() {
        refresh?.invalidate()
        recorder?.stop()
        camera.stop()
        started = false
    }

    func shoot() {
        captures.beginShot()
        camera.shoot { [weak self] result in
            Task { @MainActor in self?.finish(result) }
        }
    }

    private func finish(_ result: Result<CameraSession.Shot, CameraSession.Failure>) {
        guard case .success(let shot) = result else {
            if case .failure(let failure) = result { captures.cameraFailed(Self.describe(failure)) }
            return
        }
        let recorder = recorder
        let device = DeviceLabels(hardwareModel: Self.hardwareModel(), systemVersion: UIDevice.current.systemVersion)
        captures.keepShot(photo: shot.bytes, contentType: shot.contentType) {
            ShotRecordStep.build(
                photoBytes: shot.bytes,
                recorder: recorder,
                photoBootClockSec: shot.photoBootClockSec,
                photoDeviceClock: shot.photoDeviceClock,
                device: device,
                camera: shot.camera,
                exposureSec: shot.exposureSec
            )
        }
    }

    /// `utsname.machine`, the hardware model ("iPhone18,1"); never the name a person gave the device.
    static func hardwareModel() -> String {
        var info = utsname()
        uname(&info)
        return withUnsafeBytes(of: &info.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    }

    static func describe(_ failure: CameraSession.Failure) -> String {
        switch failure {
        case .noCamera: "this device has no back camera"
        case .cameraNotUsable(let code): "the camera could not be used" + (code.map { " (error \($0))" } ?? "")
        case .photoNotTaken(let code): "the camera did not take it" + (code.map { " (error \($0))" } ?? "")
        case .noBytes: "the camera gave no file"
        case .noClockToMatch: "the session gave no clock to match the photo with the motion"
        case .clockNotANumber: "a clock reading was not a number"
        }
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    }

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        (view.layer as? AVCaptureVideoPreviewLayer)?.session = session
        (view.layer as? AVCaptureVideoPreviewLayer)?.videoGravity = .resizeAspect
        return view
    }

    func updateUIView(_ view: PreviewView, context: Context) {}
}

struct CaptureCheckScreen: View {
    @State private var model = CaptureCheckModel()

    var body: some View {
        VStack(spacing: 12) {
            if let problem = model.cameraProblem {
                Text("Camera: \(CaptureCheckModel.describe(problem))").frame(maxWidth: .infinity, minHeight: 120)
            } else {
                CameraPreview(session: model.camera.session).aspectRatio(3.0 / 4.0, contentMode: .fit)
            }
            ScrollViewReader { reader in
                List {
                    Section("Motion") {
                        LabeledContent("Frame", value: CheckReadout.frame(model.status?.frame))
                        LabeledContent("Samples kept", value: model.status.map { "\($0.samplesKept)" } ?? CheckReadout.none)
                        LabeledContent("Refused", value: model.status.map { "\($0.samplesRefused)" } ?? CheckReadout.none)
                        LabeledContent("Pushed out", value: model.status.map { "\($0.samplesOverwritten)" } ?? CheckReadout.none)
                        LabeledContent("Gravity x, y, z (g)", value: CheckReadout.gravity(model.status?.lastSample))
                        LabeledContent("Gravity length (g)",
                                       value: CheckReadout.gravityLength(model.status?.lastSample).map { CheckReadout.fixed($0, places: 2) } ?? CheckReadout.none)
                        LabeledContent("Smallest to largest", value: model.gravityRange.text)
                        LabeledContent("Quaternion x, y, z, w", value: CheckReadout.quaternion(model.status?.lastSample))
                    }
                    Section("Heading and place") {
                        LabeledContent("Location", value: CheckReadout.location(model.status?.authorization))
                        LabeledContent("Accuracy granted", value: CheckReadout.accuracyGranted(model.status?.accuracy))
                        LabeledContent("True heading", value: CheckReadout.trueHeading(model.status?.lastHeading))
                        LabeledContent("Magnetic heading accuracy", value: CheckReadout.magneticHeadingAccuracy(model.status?.lastHeading))
                        LabeledContent("Place accuracy", value: CheckReadout.placeAccuracy(model.status?.lastFix))
                    }
                    Section("Photos") {
                        LabeledContent("Kept on the phone", value: model.captures.keptCount.keptWords)
                        if let notWhole = model.captures.keptCount.notWholeCount {
                            LabeledContent("On the phone, not whole", value: "\(notWhole)")
                        }
                        Text(model.captures.lastOutcome)
                    }
                    #if DEBUG
                    if !model.samples.isEmpty {
                        Section("Every sentence this screen can show (made up, debug only)") {
                            ForEach(Array(model.samples.enumerated()), id: \.offset) { pair in
                                Text(pair.element).id("sample-\(pair.offset)")
                            }
                        }
                    }
                    #endif
                }
                #if DEBUG
                .onAppear {
                    if let row = model.sampleRow {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                            reader.scrollTo("sample-\(row)", anchor: .top)
                        }
                    }
                }
                #endif
            }
            CaptureButton(action: model.shoot).disabled(model.cameraProblem != nil || model.captures.isBusy)
        }
        .navigationTitle("Sensors and camera")
        .onAppear { model.start() }
        .onDisappear { model.stop() }
    }
}
