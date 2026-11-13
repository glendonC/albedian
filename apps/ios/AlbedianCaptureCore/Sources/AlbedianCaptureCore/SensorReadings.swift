import Foundation

/// What Core Motion and Core Location hand over, field by field, before anything is chosen or mapped (unit
/// mobile-1). The app's sources (`apps/ios/AlbedianCapture/Sensors/`) copy each value from Apple's object as it is;
/// the tests make them up. Nothing here was measured.

/// `CMAttitudeReferenceFrame`, one case per frame (`coremotion/cmattitudereferenceframe`).
public enum AttitudeFrame: CaseIterable, Hashable, Sendable {
    case xArbitraryZVertical
    case xArbitraryCorrectedZVertical
    case xMagneticNorthZVertical
    /// Z vertical, X toward the geographic north pole; needs a magnetometer and location services.
    case xTrueNorthZVertical
}

/// One `CMDeviceMotion`, in the frame the stream was started in.
public struct DeviceMotionEvent: Equatable, Sendable {
    /// `CMLogItem.timestamp`: seconds since the device booted.
    public var timestampSec: Double
    public var frame: AttitudeFrame
    /// `attitude.quaternion`.
    public var quaternion: PointingRecord.Quaternion
    /// `gravity`, in g, in the device frame.
    public var gravityG: PointingRecord.Vector3
    /// `rotationRate`, in radians per second, in the device frame, bias removed.
    public var rotationRateRadPerSec: PointingRecord.Vector3

    public init(timestampSec: Double, frame: AttitudeFrame, quaternion: PointingRecord.Quaternion,
                gravityG: PointingRecord.Vector3, rotationRateRadPerSec: PointingRecord.Vector3) {
        self.timestampSec = timestampSec
        self.frame = frame
        self.quaternion = quaternion
        self.gravityG = gravityG
        self.rotationRateRadPerSec = rotationRateRadPerSec
    }
}

/// One `CLHeading`, with the `headingOrientation` the location manager held when it was measured.
public struct HeadingEvent: Equatable, Sendable {
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
    /// `timestamp`, on the device clock.
    public var timestamp: Date
    public var orientation: HeadingOrientation

    public init(trueHeadingDeg: Double, headingAccuracyDeg: Double, timestamp: Date, orientation: HeadingOrientation) {
        self.trueHeadingDeg = trueHeadingDeg
        self.headingAccuracyDeg = headingAccuracyDeg
        self.timestamp = timestamp
        self.orientation = orientation
    }
}

/// One `CLLocation`.
public struct LocationEvent: Equatable, Sendable {
    /// `coordinate.latitude` and `coordinate.longitude`, WGS 84 degrees.
    public var latDeg: Double
    public var lonDeg: Double
    /// `horizontalAccuracy`, a radius in metres; negative means invalid.
    public var horizontalAccuracyM: Double
    /// `altitude` (above mean sea level) and `ellipsoidalAltitude`, metres.
    public var altitudeM: Double
    public var ellipsoidalAltitudeM: Double
    /// `verticalAccuracy`, metres; zero or negative means both altitudes are invalid.
    public var verticalAccuracyM: Double
    /// `timestamp`, on the device clock.
    public var timestamp: Date
    /// `sourceInformation?.isSimulatedBySoftware`: nil when the system gave no source information.
    public var isSimulatedBySoftware: Bool?

    public init(latDeg: Double, lonDeg: Double, horizontalAccuracyM: Double, altitudeM: Double, ellipsoidalAltitudeM: Double,
                verticalAccuracyM: Double, timestamp: Date, isSimulatedBySoftware: Bool?) {
        self.latDeg = latDeg
        self.lonDeg = lonDeg
        self.horizontalAccuracyM = horizontalAccuracyM
        self.altitudeM = altitudeM
        self.ellipsoidalAltitudeM = ellipsoidalAltitudeM
        self.verticalAccuracyM = verticalAccuracyM
        self.timestamp = timestamp
        self.isSimulatedBySoftware = isSimulatedBySoftware
    }
}

/// `CLAuthorizationStatus`.
public enum LocationAuthorization: CaseIterable, Sendable {
    case notDetermined
    case restricted
    case denied
    case authorizedAlways
    case authorizedWhenInUse
}

/// What the location manager reports, in the order it reports it.
public enum PlaceEvent: Equatable, Sendable {
    /// `authorizationStatus` and `accuracyAuthorization`, at the start and on every change.
    case authorization(LocationAuthorization, AccuracyAuthorization)
    case location(LocationEvent)
    case heading(HeadingEvent)
}

/// `UIInterfaceOrientation`.
public enum InterfaceOrientation: CaseIterable, Sendable {
    case unknown
    case portrait
    case portraitUpsideDown
    case landscapeLeft
    case landscapeRight
}

/// A device-motion stream: `CMMotionManager` on the phone, a stand-in in the tests.
public protocol DeviceMotionSource: AnyObject, Sendable {
    /// `CMMotionManager.availableAttitudeReferenceFrames()`; empty when device motion is not available.
    var availableFrames: Set<AttitudeFrame> { get }
    /// Starts device-motion updates in `frame`, one every `intervalSec`, each passed to `deliver`.
    func start(frame: AttitudeFrame, intervalSec: Double, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void)
    func stop()
}

/// Location and heading updates: `CLLocationManager` on the phone, a stand-in in the tests.
public protocol PlaceAndHeadingSource: AnyObject, Sendable {
    /// Asks for when-in-use authorization if it is not determined, sets `headingOrientation`, starts location and
    /// heading updates, and passes every report to `deliver`.
    func start(headingOrientation: HeadingOrientation, deliver: @escaping @Sendable (PlaceEvent) -> Void)
    func stop()
}
