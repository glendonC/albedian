import AlbedianCaptureCore
import CoreLocation
import Foundation

/// Place and heading from `CLLocationManager`, each report copied field by field into the core's `PlaceEvent`
/// (unit mobile-1). It asks for when-in-use authorization only, and the best accuracy; it never turns a place into an
/// address. Create it, start it and stop it on the main thread: the manager then reports on the main thread too
/// (`corelocation/cllocationmanager`: delegate calls come on the thread the manager was created on).
final class CoreLocationSource: NSObject, PlaceAndHeadingSource, CLLocationManagerDelegate, @unchecked Sendable {
    private let manager: CLLocationManager
    private var deliver: (@Sendable (PlaceEvent) -> Void)?
    private var orientation: HeadingOrientation = .portrait

    @MainActor
    override init() {
        manager = CLLocationManager()
        super.init()
        manager.delegate = self
    }

    func start(headingOrientation: HeadingOrientation, deliver: @escaping @Sendable (PlaceEvent) -> Void) {
        self.deliver = deliver
        orientation = headingOrientation
        manager.headingOrientation = Self.device(headingOrientation)
        manager.desiredAccuracy = kCLLocationAccuracyBest
        report(manager)
        if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
        manager.startUpdatingLocation()
        if CLLocationManager.headingAvailable() { manager.startUpdatingHeading() }
    }

    func stop() {
        manager.stopUpdatingHeading()
        manager.stopUpdatingLocation()
        deliver = nil
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        report(manager)
    }

    /// A status or accuracy this code does not know is not reported, so no place is taken under it.
    private func report(_ manager: CLLocationManager) {
        guard let status = Self.authorization(manager.authorizationStatus), let accuracy = Self.accuracy(manager.accuracyAuthorization) else { return }
        deliver?(.authorization(status, accuracy))
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        for location in locations {
            deliver?(.location(LocationEvent(
                latDeg: location.coordinate.latitude,
                lonDeg: location.coordinate.longitude,
                horizontalAccuracyM: location.horizontalAccuracy,
                altitudeM: location.altitude,
                ellipsoidalAltitudeM: location.ellipsoidalAltitude,
                verticalAccuracyM: location.verticalAccuracy,
                timestamp: location.timestamp,
                isSimulatedBySoftware: location.sourceInformation?.isSimulatedBySoftware
            )))
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        deliver?(.heading(HeadingEvent(trueHeadingDeg: newHeading.trueHeading, headingAccuracyDeg: newHeading.headingAccuracy,
                                       timestamp: newHeading.timestamp, orientation: orientation)))
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: any Error) {
        // No report to pass on: the last fix and heading stay as they were, and a capture uses only those
    }

    static func device(_ orientation: HeadingOrientation) -> CLDeviceOrientation {
        switch orientation {
        case .portrait: .portrait
        case .portraitUpsideDown: .portraitUpsideDown
        case .landscapeLeft: .landscapeLeft
        case .landscapeRight: .landscapeRight
        }
    }

    static func authorization(_ status: CLAuthorizationStatus) -> LocationAuthorization? {
        switch status {
        case .notDetermined: .notDetermined
        case .restricted: .restricted
        case .denied: .denied
        case .authorizedAlways: .authorizedAlways
        case .authorizedWhenInUse: .authorizedWhenInUse
        @unknown default: nil
        }
    }

    static func accuracy(_ accuracy: CLAccuracyAuthorization) -> AccuracyAuthorization? {
        switch accuracy {
        case .fullAccuracy: .full
        case .reducedAccuracy: .reduced
        @unknown default: nil
        }
    }
}
