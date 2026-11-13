import Foundation

/// Synthetic sensor readings for the tests and for running the pipeline by hand in the simulator: nothing here was
/// measured. The place is open ocean (30 N, 40 W) and the moment the synthetic one of the other fixtures
/// (2030-01-01T02:00:00Z); the readings are those of the shared fixture's first valid sample, so a record built
/// from the defaults is that sample but for the hash, the window's sample count and the offsets' last digits.
public enum SampleCapture {
    public struct Options: Equatable, Sendable {
        public var trueNorthFrame = true
        public var locationAllowed = true
        public var placeSimulatedBySoftware = false
        public var headingArrived = true
        public var headingAccuracyDeg = 12.0
        public var fieldOfViewReported = true
        public var motionRunning = true
        public init() {}
    }

    /// The synthetic moment, as seconds since 1970.
    public static let moment = Date(timeIntervalSince1970: 1_893_463_200)
    /// The photo's timestamp on the boot clock [synthetic].
    public static let photoBootClockSec = 5000.0

    public static func capture(photoSha256: String, photoWidthPx: Int = 4032, photoHeightPx: Int = 3024,
                               options: Options = Options()) -> SensorCapture {
        // 100 Hz from 0.504 s before the photo to 10.496 s after, so the nearest sample is 0.004 s before it
        let samples = options.motionRunning ? (0...1100).map { index in
            MotionSample(bootClockSec: photoBootClockSec - 0.504 + Double(index) * 0.01,
                         referenceFrame: options.trueNorthFrame ? .xTrueNorthZVertical : .other,
                         quaternion: PointingRecord.Quaternion(x: 0.2, y: -0.4, z: 0.4, w: 0.8),
                         gravityG: PointingRecord.Vector3(x: 0, y: -0.6, z: -0.8),
                         rotationRateRadPerSec: PointingRecord.Vector3(x: 0.002, y: -0.001, z: 0.0005))
        } : []
        let fix = LocationFix(latDeg: 30, lonDeg: -40, horizontalAccuracyM: 8, altitudeM: 12, ellipsoidalAltitudeM: 45.5,
                              verticalAccuracyM: 3, deviceClock: moment.addingTimeInterval(-2.5),
                              isSimulatedBySoftware: options.placeSimulatedBySoftware)
        return SensorCapture(
            photoSha256: photoSha256,
            photoBootClockSec: photoBootClockSec,
            photoDeviceClock: moment,
            device: DeviceLabels(hardwareModel: "Synthetic1,1", systemVersion: "27.1"),
            camera: CameraReading(position: .back, deviceType: .wideAngle, activeFormatWidthPx: 4032, activeFormatHeightPx: 3024,
                                  photoWidthPx: photoWidthPx, photoHeightPx: photoHeightPx,
                                  videoFieldOfViewDeg: options.fieldOfViewReported ? 72.0 : 0,
                                  distortionCorrectedFieldOfViewDeg: options.fieldOfViewReported ? 69.5 : 0,
                                  geometricDistortionCorrectionEnabled: true, videoZoomFactor: 1,
                                  contentAwareDistortionCorrection: false),
            motion: MotionReading(samples: samples, windowStartOffsetSec: -0.5, windowEndOffsetSec: 10.5),
            heading: options.headingArrived
                ? HeadingReading(trueHeadingDeg: 212.5, headingAccuracyDeg: options.headingAccuracyDeg, orientation: .portrait,
                                 deviceClock: moment.addingTimeInterval(-1.25))
                : nil,
            location: options.locationAllowed ? .allowed(.full, fix: fix) : .notAllowed
        )
    }
}
