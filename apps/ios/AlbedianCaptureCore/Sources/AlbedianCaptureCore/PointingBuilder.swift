import Foundation

/// What the sensors said around one photo, in the units and on the clocks Apple gives them (design component 1),
/// before anything is chosen or checked. The app's sensor readers (unit mobile-1) and the camera (mobile-2) fill it;
/// the tests fill it with synthetic values. Two clocks are kept apart:
/// - the boot clock: seconds since the device booted, the timebase of `CMLogItem.timestamp`, to which the app
///   converts the photo's timestamp from the session's synchronization clock [inference: that the two count the same
///   seconds is checked on a device in mobile-1];
/// - the device clock: the wall clock, a `Date`, the timebase of `CLHeading.timestamp` and `CLLocation.timestamp`.
public struct SensorCapture: Equatable, Sendable {
    /// The photo's SHA-256 over the exact bytes the camera wrote (`PhotoHash.sha256Hex`).
    public var photoSha256: String
    /// The photo's timestamp on the boot clock.
    public var photoBootClockSec: Double
    /// The device clock's reading at the photo's timestamp.
    public var photoDeviceClock: Date
    public var device: DeviceLabels
    public var camera: CameraReading
    public var motion: MotionReading
    /// The last heading event before the shutter, or nil when none arrived.
    public var heading: HeadingReading?
    public var location: LocationReading

    public init(photoSha256: String, photoBootClockSec: Double, photoDeviceClock: Date, device: DeviceLabels,
                camera: CameraReading, motion: MotionReading, heading: HeadingReading?, location: LocationReading) {
        self.photoSha256 = photoSha256
        self.photoBootClockSec = photoBootClockSec
        self.photoDeviceClock = photoDeviceClock
        self.device = device
        self.camera = camera
        self.motion = motion
        self.heading = heading
        self.location = location
    }
}

/// `utsname.machine` and `UIDevice.systemVersion`. There is no field for the device's name, and none is ever read.
public struct DeviceLabels: Equatable, Sendable {
    public var hardwareModel: String
    public var systemVersion: String
    public init(hardwareModel: String, systemVersion: String) {
        self.hardwareModel = hardwareModel
        self.systemVersion = systemVersion
    }
}

/// The camera and its active format at the shutter, as AVFoundation reports them.
public struct CameraReading: Equatable, Sendable {
    public var position: CameraPosition
    /// The physical lens (for a virtual camera, its `activePrimaryConstituent` at the shutter).
    public var deviceType: CameraDeviceType
    public var activeFormatWidthPx: Int
    public var activeFormatHeightPx: Int
    /// `AVCaptureResolvedPhotoSettings.photoDimensions` (zero by zero for a RAW-only capture, per Apple).
    public var photoWidthPx: Int
    public var photoHeightPx: Int
    /// `activeFormat.videoFieldOfView`, horizontal, in degrees; zero when the format's field of view is unknown.
    public var videoFieldOfViewDeg: Double
    /// `activeFormat.geometricDistortionCorrectedVideoFieldOfView` (equal to the other where correction is not
    /// supported, per the SDK header).
    public var distortionCorrectedFieldOfViewDeg: Double
    /// `AVCaptureDevice.isGeometricDistortionCorrectionEnabled`.
    public var geometricDistortionCorrectionEnabled: Bool
    public var videoZoomFactor: Double
    /// `AVCapturePhotoSettings.isAutoContentAwareDistortionCorrectionEnabled`.
    public var contentAwareDistortionCorrection: Bool

    public init(position: CameraPosition, deviceType: CameraDeviceType, activeFormatWidthPx: Int,
                activeFormatHeightPx: Int, photoWidthPx: Int, photoHeightPx: Int, videoFieldOfViewDeg: Double,
                distortionCorrectedFieldOfViewDeg: Double, geometricDistortionCorrectionEnabled: Bool,
                videoZoomFactor: Double, contentAwareDistortionCorrection: Bool) {
        self.position = position
        self.deviceType = deviceType
        self.activeFormatWidthPx = activeFormatWidthPx
        self.activeFormatHeightPx = activeFormatHeightPx
        self.photoWidthPx = photoWidthPx
        self.photoHeightPx = photoHeightPx
        self.videoFieldOfViewDeg = videoFieldOfViewDeg
        self.distortionCorrectedFieldOfViewDeg = distortionCorrectedFieldOfViewDeg
        self.geometricDistortionCorrectionEnabled = geometricDistortionCorrectionEnabled
        self.videoZoomFactor = videoZoomFactor
        self.contentAwareDistortionCorrection = contentAwareDistortionCorrection
    }
}

/// The reference frame a device-motion stream ran in.
public enum MotionReferenceFrame: Equatable, Sendable {
    /// `CMAttitudeReferenceFrame.xTrueNorthZVertical`: needs a magnetometer and location services.
    case xTrueNorthZVertical
    /// Any other frame (the device has no true-north frame, or it was not available).
    case other
}

/// One `CMDeviceMotion` sample.
public struct MotionSample: Equatable, Sendable {
    /// `CMLogItem.timestamp`, on the boot clock.
    public var bootClockSec: Double
    public var referenceFrame: MotionReferenceFrame
    public var quaternion: PointingRecord.Quaternion
    /// `gravity`, in g, in the device frame.
    public var gravityG: PointingRecord.Vector3
    /// `rotationRate`, in radians per second, in the device frame, bias removed.
    public var rotationRateRadPerSec: PointingRecord.Vector3

    public init(bootClockSec: Double, referenceFrame: MotionReferenceFrame, quaternion: PointingRecord.Quaternion,
                gravityG: PointingRecord.Vector3, rotationRateRadPerSec: PointingRecord.Vector3) {
        self.bootClockSec = bootClockSec
        self.referenceFrame = referenceFrame
        self.quaternion = quaternion
        self.gravityG = gravityG
        self.rotationRateRadPerSec = rotationRateRadPerSec
    }
}

/// The device-motion samples the app kept around the shutter, and the span meant to cover the exposure, as
/// offsets from the photo's timestamp on the boot clock.
public struct MotionReading: Equatable, Sendable {
    public var samples: [MotionSample]
    public var windowStartOffsetSec: Double
    public var windowEndOffsetSec: Double
    public init(samples: [MotionSample], windowStartOffsetSec: Double, windowEndOffsetSec: Double) {
        self.samples = samples
        self.windowStartOffsetSec = windowStartOffsetSec
        self.windowEndOffsetSec = windowEndOffsetSec
    }
}

/// One `CLHeading`.
public struct HeadingReading: Equatable, Sendable {
    /// `trueHeading`, degrees from true north; "A negative value indicates that the heading could not be determined"
    /// (Apple's trueHeading page, developer.apple.com/documentation/corelocation/clheading/trueheading [verified];
    /// CLHeading.h gives only the range 0.0 to 359.9). Valid only while location updates run.
    public var trueHeadingDeg: Double
    /// `headingAccuracy`, degrees: Apple states it for the magnetic heading, "the maximum deviation ... between the
    /// reported heading and the true geomagnetic heading", a positive value being the potential error of
    /// `magneticHeading`; "A negative value means that the reported heading is invalid" (Apple's headingAccuracy page,
    /// developer.apple.com/documentation/corelocation/clheading/headingaccuracy [verified]; CLHeading.h says the same).
    /// Apple states no accuracy for the true heading itself.
    public var headingAccuracyDeg: Double
    /// The axis `CLLocationManager.headingOrientation` named while it was measured.
    public var orientation: HeadingOrientation
    /// `timestamp`, on the device clock.
    public var deviceClock: Date
    public init(trueHeadingDeg: Double, headingAccuracyDeg: Double, orientation: HeadingOrientation, deviceClock: Date) {
        self.trueHeadingDeg = trueHeadingDeg
        self.headingAccuracyDeg = headingAccuracyDeg
        self.orientation = orientation
        self.deviceClock = deviceClock
    }
}

/// One `CLLocation`.
public struct LocationFix: Equatable, Sendable {
    public var latDeg: Double
    public var lonDeg: Double
    /// `horizontalAccuracy`, a radius in metres; negative means invalid.
    public var horizontalAccuracyM: Double
    /// `altitude` (above mean sea level) and `ellipsoidalAltitude`, in metres.
    public var altitudeM: Double
    public var ellipsoidalAltitudeM: Double
    /// `verticalAccuracy`, metres; zero or negative means both altitudes are invalid.
    public var verticalAccuracyM: Double
    /// `timestamp`, on the device clock.
    public var deviceClock: Date
    /// `sourceInformation?.isSimulatedBySoftware`; false when the system gave no source information.
    public var isSimulatedBySoftware: Bool

    public init(latDeg: Double, lonDeg: Double, horizontalAccuracyM: Double, altitudeM: Double,
                ellipsoidalAltitudeM: Double, verticalAccuracyM: Double, deviceClock: Date, isSimulatedBySoftware: Bool) {
        self.latDeg = latDeg
        self.lonDeg = lonDeg
        self.horizontalAccuracyM = horizontalAccuracyM
        self.altitudeM = altitudeM
        self.ellipsoidalAltitudeM = ellipsoidalAltitudeM
        self.verticalAccuracyM = verticalAccuracyM
        self.deviceClock = deviceClock
        self.isSimulatedBySoftware = isSimulatedBySoftware
    }
}

/// Whether the person allowed location, at what accuracy, and the last fix before the shutter.
public enum LocationReading: Equatable, Sendable {
    case notAllowed
    case allowed(AccuracyAuthorization, fix: LocationFix?)
}

/// Why no pointing record can be written for a photo. The photo can still be sent alone, and the app says the
/// pointing was not recorded (design component 7). Nothing is rounded, clamped or filled in to avoid one of these.
public enum PointingBuildError: Error, Equatable, Sendable {
    /// The photo hash is not 64 lower-case hex characters, or is all zeros.
    case photoHashNotASha256
    /// A device label does not fit its pattern (the simulator's `utsname.machine`, "arm64", does not).
    case deviceLabelOutsidePattern(field: String)
    /// The device clock at the photo is outside years 1 to 9999.
    case deviceClockOutOfRange
    /// The photo's timestamp on the boot clock is not a finite number.
    case photoBootClockNotFinite
    /// No motion sample lies within the contract's offset limit of the photo: the stream was not running.
    case noMotionSampleAtThePhoto(nearestOffsetSec: Double?)
    /// The motion sample holds a number that is infinite or not a number.
    case motionReadingNotFinite
    /// The sample's gravity is not about one g long.
    case gravityOutsideBand(lengthG: Double)
    /// The sample's attitude is not a unit quaternion, so it is not an attitude.
    case attitudeNotUnitQuaternion(norm: Double)
    /// The window ends before it starts, holds no sample, or holds more than the contract's limit.
    case motionWindowBackwards
    case motionWindowEmpty
    case motionWindowTooManySamples(count: Int)
    /// A heading reading outside the values Apple documents: a true heading that is not a number or is 360 or more,
    /// or an accuracy that is not a number or is infinite. (A negative one is the device saying it is invalid.)
    case headingOutOfRange
    /// A camera value outside its documented range, named by its wire field.
    case cameraValueOutOfRange(field: String)
    /// A time offset that is not finite.
    case offsetNotFinite
    /// The record this builder wrote failed the contract (a defect in the builder: never expected).
    case contractRefused([PointingIssue])
}

/// Builds the pointing record from one capture's sensor readings, with every rule of the contract and nothing
/// guessed: the motion sample nearest the photo, a heading only with a place and an accuracy inside the limit,
/// otherwise the reason there is none; a place only when allowed, with a valid accuracy and not simulated in
/// software; a field of view the device reported as zero said as unknown. Every built record is checked by
/// `PointingValidator` before it is returned.
///
/// The checks run in this order, and the first that fails names the refusal: the hash, the device labels, the
/// clock, the camera, the motion sample nearest the photo (its offset, its numbers, its gravity), the window, then
/// the place and the heading (whose attitude is judged only when the heading is measured, since only then is it in
/// the record).
public enum PointingRecordBuilder {
    public static func build(_ capture: SensorCapture) throws(PointingBuildError) -> PointingRecord {
        guard PhotoHash.isContractHash(capture.photoSha256) else { throw .photoHashNotASha256 }
        guard PointingLabels.isHardwareModel(capture.device.hardwareModel) else { throw .deviceLabelOutsidePattern(field: "hardwareModel") }
        guard PointingLabels.isSystemVersion(capture.device.systemVersion) else { throw .deviceLabelOutsidePattern(field: "systemVersion") }
        guard let capturedAt = UTCInstant.string(from: capture.photoDeviceClock) else { throw .deviceClockOutOfRange }
        guard capture.photoBootClockSec.isFinite else { throw .photoBootClockNotFinite }
        let camera = try camera(capture.camera)
        let motion = try motion(capture.motion, photoBootClockSec: capture.photoBootClockSec)
        let place = place(capture.location, photoDeviceClock: capture.photoDeviceClock)
        let heading = try heading(capture.heading, sample: motion.sample, placed: place.isAllowed, photoDeviceClock: capture.photoDeviceClock)
        let record = PointingRecord(
            sourceAssetSha256: capture.photoSha256,
            capturedAtDeviceClockUTC: capturedAt,
            device: PointingRecord.Device(hardwareModel: capture.device.hardwareModel, systemVersion: capture.device.systemVersion),
            camera: camera,
            motion: motion.motion,
            heading: heading,
            location: place
        )
        // The contract's own judgement of what was built: a refusal here is a defect in this builder
        let wire: JSONValue
        do throws(PointingEncodingError) {
            wire = try record.wireValue()
        } catch {
            throw .contractRefused([PointingIssue(path: error.path, message: "Number must be finite")])
        }
        let issues = PointingValidator.issues(in: wire)
        guard issues.isEmpty else { throw .contractRefused(issues) }
        return record
    }

    private static func camera(_ reading: CameraReading) throws(PointingBuildError) -> PointingRecord.Camera {
        let maxSide = PointingContract.dimensionMaxPx
        func fits(_ width: Int, _ height: Int) -> Bool { (1...maxSide).contains(width) && (1...maxSide).contains(height) }
        guard fits(reading.activeFormatWidthPx, reading.activeFormatHeightPx) else { throw .cameraValueOutOfRange(field: "activeFormatDimensionsPx") }
        guard fits(reading.photoWidthPx, reading.photoHeightPx) else { throw .cameraValueOutOfRange(field: "photoDimensionsPx") }
        // The field of view the photo was taken with: the corrected one while geometric distortion correction is on
        let horizontal = reading.geometricDistortionCorrectionEnabled ? reading.distortionCorrectedFieldOfViewDeg : reading.videoFieldOfViewDeg
        let fieldOfView: PointingRecord.FieldOfView
        if horizontal == 0 {
            // AVFoundation writes zero "if the format's field of view is unknown"
            fieldOfView = .unknown
        } else if horizontal > 0, horizontal < 180 {
            fieldOfView = .reported(horizontalDeg: horizontal, distortionCorrected: reading.geometricDistortionCorrectionEnabled)
        } else {
            throw .cameraValueOutOfRange(field: "fieldOfView.horizontalDeg")
        }
        guard reading.videoZoomFactor.isFinite, reading.videoZoomFactor >= 1 else { throw .cameraValueOutOfRange(field: "videoZoomFactor") }
        return PointingRecord.Camera(
            position: reading.position,
            deviceType: reading.deviceType,
            activeFormatDimensionsPx: PointingRecord.Dimensions(width: reading.activeFormatWidthPx, height: reading.activeFormatHeightPx),
            photoDimensionsPx: PointingRecord.Dimensions(width: reading.photoWidthPx, height: reading.photoHeightPx),
            fieldOfView: fieldOfView,
            videoZoomFactor: reading.videoZoomFactor,
            contentAwareDistortionCorrection: reading.contentAwareDistortionCorrection
        )
    }

    private static func motion(_ reading: MotionReading, photoBootClockSec: Double)
        throws(PointingBuildError) -> (motion: PointingRecord.Motion, sample: MotionSample) {
        let offsets = reading.samples.map { $0.bootClockSec - photoBootClockSec }
        // A sample with no finite time is no sample: it can be neither nearest nor in the window
        let timed = offsets.indices.filter { offsets[$0].isFinite }
        guard let nearest = timed.min(by: { abs(offsets[$0]) < abs(offsets[$1]) }) else {
            throw .noMotionSampleAtThePhoto(nearestOffsetSec: nil)
        }
        let offset = offsets[nearest]
        guard abs(offset) <= PointingContract.motionSampleOffsetLimitSec else { throw .noMotionSampleAtThePhoto(nearestOffsetSec: offset) }
        let sample = reading.samples[nearest]
        let numbers = [sample.gravityG.x, sample.gravityG.y, sample.gravityG.z, sample.rotationRateRadPerSec.x,
                       sample.rotationRateRadPerSec.y, sample.rotationRateRadPerSec.z]
        guard numbers.allSatisfy(\.isFinite) else { throw .motionReadingNotFinite }
        let gravity = sample.gravityG.length
        guard gravity >= PointingContract.gravityLengthMinG, gravity <= PointingContract.gravityLengthMaxG else {
            throw .gravityOutsideBand(lengthG: gravity)
        }
        let start = reading.windowStartOffsetSec
        let end = reading.windowEndOffsetSec
        guard start.isFinite, end.isFinite else { throw .offsetNotFinite }
        guard end >= start else { throw .motionWindowBackwards }
        let inWindow = reading.samples.indices.filter { start <= offsets[$0] && offsets[$0] <= end }
        guard !inWindow.isEmpty else { throw .motionWindowEmpty }
        guard inWindow.count <= PointingContract.sampleCountMax else { throw .motionWindowTooManySamples(count: inWindow.count) }
        var fastest = 0.0
        for index in inWindow {
            let rate = reading.samples[index].rotationRateRadPerSec.length
            guard rate.isFinite else { throw .motionReadingNotFinite }
            fastest = max(fastest, rate)
        }
        let motion = PointingRecord.Motion(
            sampleOffsetFromPhotoSec: offset,
            gravityDeviceG: sample.gravityG,
            rotationRateDeviceRadPerSec: sample.rotationRateRadPerSec,
            window: PointingRecord.MotionWindow(startOffsetSec: start, endOffsetSec: end, sampleCount: inWindow.count,
                                                maxRotationRateRadPerSec: fastest)
        )
        return (motion, sample)
    }

    /// The place, when the person allowed it and the device gave a valid fix it did not simulate in software.
    private static func place(_ reading: LocationReading, photoDeviceClock: Date) -> PointingRecord.Place {
        guard case .allowed(let authorization, let fix) = reading else { return .absent(.notAllowed) }
        guard let fix else { return .absent(.noFix) }
        if fix.isSimulatedBySoftware { return .absent(.simulatedBySoftware) }
        // A negative horizontal accuracy means an invalid fix (corelocation/cllocation/horizontalaccuracy); so does a
        // coordinate outside WGS 84's ranges [inference: CLLocationCoordinate2DIsValid]
        let offset = fix.deviceClock.timeIntervalSince(photoDeviceClock)
        guard fix.horizontalAccuracyM.isFinite, fix.horizontalAccuracyM >= 0, fix.latDeg.isFinite, fix.lonDeg.isFinite,
              (-90...90).contains(fix.latDeg), (-180...180).contains(fix.lonDeg), offset.isFinite else { return .absent(.noFix) }
        // The altitudes only when the device called them valid: a positive vertical accuracy
        let altitudeValid = fix.verticalAccuracyM.isFinite && fix.verticalAccuracyM > 0 && fix.altitudeM.isFinite && fix.ellipsoidalAltitudeM.isFinite
        return .allowed(PointingRecord.AllowedPlace(
            latDeg: fix.latDeg,
            lonDeg: fix.lonDeg,
            horizontalAccuracyM: fix.horizontalAccuracyM,
            accuracyAuthorization: authorization,
            altitude: altitudeValid
                ? PointingRecord.Altitude(aboveMeanSeaLevelM: fix.altitudeM, aboveEllipsoidM: fix.ellipsoidalAltitudeM,
                                          verticalAccuracyM: fix.verticalAccuracyM)
                : nil,
            fixOffsetFromPhotoSec: offset
        ))
    }

    private static func heading(_ reading: HeadingReading?, sample: MotionSample, placed: Bool,
                                photoDeviceClock: Date) throws(PointingBuildError) -> PointingRecord.Heading {
        guard sample.referenceFrame == .xTrueNorthZVertical else { return .absent(.noMagnetometer) }
        // True north is found from the place
        guard placed else { return .absent(.noPlace) }
        guard let reading else { return .absent(.noHeadingEvent) }
        guard !reading.trueHeadingDeg.isNaN, !reading.headingAccuracyDeg.isNaN, reading.trueHeadingDeg < 360,
              reading.headingAccuracyDeg < .infinity else { throw .headingOutOfRange }
        // A negative true heading or accuracy is the device saying it is invalid
        guard reading.trueHeadingDeg >= 0, reading.headingAccuracyDeg >= 0 else { return .absent(.headingInvalid) }
        guard reading.headingAccuracyDeg <= PointingContract.headingAccuracyLimitDeg else {
            return .absent(.accuracyOutsideLimit(reportedDeg: reading.headingAccuracyDeg))
        }
        let norm = sample.quaternion.norm
        guard abs(norm - 1) <= PointingContract.unitQuaternionTolerance else { throw .attitudeNotUnitQuaternion(norm: norm) }
        let offset = reading.deviceClock.timeIntervalSince(photoDeviceClock)
        guard offset.isFinite else { throw .offsetNotFinite }
        return .measured(PointingRecord.MeasuredHeading(
            quaternion: sample.quaternion,
            trueHeadingDeg: reading.trueHeadingDeg,
            headingAccuracyDeg: reading.headingAccuracyDeg,
            headingOrientation: reading.orientation,
            eventOffsetFromPhotoSec: offset
        ))
    }
}

extension PointingRecord.Place {
    public var isAllowed: Bool { if case .allowed = self { true } else { false } }
}
