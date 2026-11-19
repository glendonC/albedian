import AVFoundation
import AlbedianCaptureCore
import CoreMedia
import Foundation

/// The back camera and its photos (unit mobile-2): the physical wide-angle camera, `AVCapturePhotoOutput` in HEIC
/// (else JPEG), the bytes of `fileDataRepresentation()` kept as they are, content-aware distortion correction off,
/// and the camera's values read at the shutter. The photo's timestamp is moved from the session's
/// `synchronizationClock` to the host clock with `CMSyncConvertTime`; that the host clock and Core Motion's
/// `CMLogItem.timestamp` count the same seconds is [inference], checked on a device (the owner's checklist).
/// On a device only: the simulator has no camera, so `configure` says so.
final class CameraSession: NSObject, AVCapturePhotoCaptureDelegate, @unchecked Sendable {
    /// A photo as the camera wrote it, with what the camera and the clocks said about it.
    struct Shot: Sendable {
        let bytes: Data
        let contentType: PhotoContentType
        let camera: CameraReading
        /// The photo's timestamp on the boot (host) clock, and the device clock at that time.
        let photoBootClockSec: Double
        let photoDeviceClock: Date
        /// The exposure's length the device reported at the shutter, when it reported one.
        let exposureSec: Double?
    }

    /// Why no shot came. Each says what happened.
    enum Failure: Error, Equatable, Sendable {
        case noCamera
        /// The session could not take the camera, with the system error's code when there was one.
        case cameraNotUsable(code: Int?)
        case photoNotTaken(code: Int?)
        case noBytes
        case noClockToMatch
        case clockNotANumber
    }

    let session = AVCaptureSession()
    private let output = AVCapturePhotoOutput()
    private let queue = DispatchQueue(label: "Albedian camera")
    private var device: AVCaptureDevice?
    private let lock = NSLock()
    private var pending: [Int64: (Result<Shot, Failure>) -> Void] = [:]
    private var exposureAtShutter: [Int64: Double] = [:]
    private var typeAtShutter: [Int64: PhotoContentType] = [:]
    private var beganAtShutter: (@Sendable (Double) -> Void)?

    /// Sets up the session with the back wide-angle camera.
    func configure() -> Failure? {
        guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else { return .noCamera }
        do {
            let input = try AVCaptureDeviceInput(device: device)
            session.beginConfiguration()
            defer { session.commitConfiguration() }
            session.sessionPreset = .photo
            guard session.canAddInput(input), session.canAddOutput(output) else { return .cameraNotUsable(code: nil) }
            session.addInput(input)
            session.addOutput(output)
            output.maxPhotoQualityPrioritization = .quality
            self.device = device
            return nil
        } catch {
            return .cameraNotUsable(code: (error as NSError).code)
        }
    }

    func start() { queue.async { self.session.startRunning() } }
    func stop() { queue.async { self.session.stopRunning() } }

    /// The field of view across the portrait preview: the active format's horizontal field of view (the corrected one
    /// while distortion correction is on) carried to the format's short side, which a portrait preview shows across
    /// [inference: which axis "horizontal" means is an iPhone check]. Nil without a camera or a reported field of view.
    var previewAcrossFieldOfViewDeg: Double? {
        guard let device else { return nil }
        let format = device.activeFormat
        let horizontal = Double(device.isGeometricDistortionCorrectionEnabled ? format.geometricDistortionCorrectedVideoFieldOfView : format.videoFieldOfView)
        let size = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
        guard horizontal > 0, horizontal < 180, size.width > 0, size.height > 0 else { return nil }
        let ratio = Double(min(size.width, size.height)) / Double(max(size.width, size.height))
        return 2 * atan(tan(horizontal * .pi / 360) * ratio) * 180 / .pi
    }

    /// One long exposure (mobile-7): the format's longest duration at the ISO that carries the meter's light
    /// (`NightExposure.plan`), then one photo as `shoot` takes it, then the camera's own exposure again. `began` is called
    /// with the exposure's length the device set, when the camera starts the capture.
    func shootLong(began: @escaping @Sendable (Double) -> Void, _ done: @escaping @Sendable (Result<Shot, Failure>) -> Void) {
        guard let device else { return done(.failure(.noCamera)) }
        let format = device.activeFormat
        guard let plan = NightExposure.plan(meterDurationSec: CMTimeGetSeconds(device.exposureDuration), meterISO: Double(device.iso),
                                            maxDurationSec: CMTimeGetSeconds(format.maxExposureDuration),
                                            minISO: Double(format.minISO), maxISO: Double(format.maxISO)) else {
            return done(.failure(.cameraNotUsable(code: nil)))
        }
        do {
            try device.lockForConfiguration()
        } catch {
            return done(.failure(.cameraNotUsable(code: (error as NSError).code)))
        }
        let duration = CMTime(seconds: plan.durationSec, preferredTimescale: 1_000_000)
        device.setExposureModeCustom(duration: duration, iso: Float(plan.iso)) { [weak self] _ in
            device.unlockForConfiguration()
            guard let self else { return }
            self.lock.withLock { self.beganAtShutter = began }
            self.shoot { result in
                if (try? device.lockForConfiguration()) != nil {
                    if device.isExposureModeSupported(.continuousAutoExposure) { device.exposureMode = .continuousAutoExposure }
                    device.unlockForConfiguration()
                }
                done(result)
            }
        }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, willCapturePhotoFor resolvedSettings: AVCaptureResolvedPhotoSettings) {
        let id = resolvedSettings.uniqueID
        let (began, exposure) = lock.withLock { () -> (((Double) -> Void)?, Double?) in
            let began = beganAtShutter
            beganAtShutter = nil
            return (began, exposureAtShutter[id])
        }
        if let began, let exposure, exposure.isFinite { began(exposure) }
    }

    /// Takes one photo and reports it once the camera has written it.
    func shoot(_ done: @escaping @Sendable (Result<Shot, Failure>) -> Void) {
        guard let device else { return done(.failure(.noCamera)) }
        let hevc = output.availablePhotoCodecTypes.contains(.hevc)
        let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: hevc ? AVVideoCodecType.hevc : AVVideoCodecType.jpeg])
        settings.photoQualityPrioritization = .quality
        if output.isContentAwareDistortionCorrectionSupported { settings.isAutoContentAwareDistortionCorrectionEnabled = false }
        lock.withLock {
            pending[settings.uniqueID] = done
            exposureAtShutter[settings.uniqueID] = CMTimeGetSeconds(device.exposureDuration)
            typeAtShutter[settings.uniqueID] = hevc ? .heic : .jpeg
        }
        output.capturePhoto(with: settings, delegate: self)
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: (any Error)?) {
        let id = photo.resolvedSettings.uniqueID
        let (done, exposure, type) = lock.withLock {
            (pending.removeValue(forKey: id), exposureAtShutter.removeValue(forKey: id), typeAtShutter.removeValue(forKey: id))
        }
        guard let done, let type else { return }
        if let error { return done(.failure(.photoNotTaken(code: (error as NSError).code))) }
        guard let bytes = photo.fileDataRepresentation() else { return done(.failure(.noBytes)) }
        guard let clock = session.synchronizationClock else { return done(.failure(.noClockToMatch)) }
        let host = CMClockGetHostTimeClock()
        let photoBoot = CMTimeGetSeconds(CMSyncConvertTime(photo.timestamp, from: clock, to: host))
        let nowBoot = CMTimeGetSeconds(CMClockGetTime(host))
        guard let photoWall = PhotoClock.deviceClock(atBootClockSec: photoBoot, nowBootClockSec: nowBoot, nowDeviceClock: Date()),
              let device else { return done(.failure(.clockNotANumber)) }
        let format = CMVideoFormatDescriptionGetDimensions(device.activeFormat.formatDescription)
        let size = photo.resolvedSettings.photoDimensions
        let camera = CameraReading(
            position: device.position == .front ? .front : .back,
            deviceType: .wideAngle,
            activeFormatWidthPx: Int(format.width), activeFormatHeightPx: Int(format.height),
            photoWidthPx: Int(size.width), photoHeightPx: Int(size.height),
            videoFieldOfViewDeg: Double(device.activeFormat.videoFieldOfView),
            distortionCorrectedFieldOfViewDeg: Double(device.activeFormat.geometricDistortionCorrectedVideoFieldOfView),
            geometricDistortionCorrectionEnabled: device.isGeometricDistortionCorrectionEnabled,
            videoZoomFactor: Double(device.videoZoomFactor),
            contentAwareDistortionCorrection: photo.resolvedSettings.isContentAwareDistortionCorrectionEnabled
        )
        done(.success(Shot(bytes: bytes, contentType: type, camera: camera, photoBootClockSec: photoBoot, photoDeviceClock: photoWall,
                           exposureSec: exposure.flatMap { $0.isFinite ? $0 : nil })))
    }
}
