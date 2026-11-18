import AlbedianCaptureCore
import CoreLocation
import Foundation

/// Place and heading from `CLLocationManager`, each report copied field by field into the core's `PlaceEvent`
/// (unit mobile-1). Isolated to `@MainActor` (no `@unchecked Sendable`) so `manager`, `deliver` and `orientation` are
/// mutated and read on the main actor only; stale cached fixes and headings older than `SensorMapping`'s age bounds
/// are dropped on arrival.
@MainActor
final class CoreLocationSource: NSObject, PlaceAndHeadingSource, CLLocationManagerDelegate {
    private let manager: CLLocationManager
    private var deliver: (@Sendable (PlaceEvent) -> Void)?
    private var orientation: HeadingOrientation = .portrait

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
        reportAuthorization()
        if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
        manager.startUpdatingLocation()
        if CLLocationManager.headingAvailable() { manager.startUpdatingHeading() }
    }

    func updateHeadingOrientation(_ headingOrientation: HeadingOrientation) {
        orientation = headingOrientation
        manager.headingOrientation = Self.device(headingOrientation)
    }

    func stop() {
        manager.stopUpdatingHeading()
        manager.stopUpdatingLocation()
        deliver = nil
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if Thread.isMainThread {
            MainActor.assumeIsolated { self.reportAuthorization() }
        } else {
            Task { @MainActor [weak self] in self?.reportAuthorization() }
        }
    }

    private func reportAuthorization() {
        guard let status = Self.authorization(manager.authorizationStatus),
              let accuracy = Self.accuracy(manager.accuracyAuthorization) else { return }
        deliver?(.authorization(status, accuracy))
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let now = Date()
        let freshEvents: [LocationEvent] = locations.compactMap { location in
            guard now.timeIntervalSince(location.timestamp) <= SensorMapping.maxFixAgeSec else { return nil }
            return LocationEvent(
                latDeg: location.coordinate.latitude,
                lonDeg: location.coordinate.longitude,
                horizontalAccuracyM: location.horizontalAccuracy,
                altitudeM: location.altitude,
                ellipsoidalAltitudeM: location.ellipsoidalAltitude,
                verticalAccuracyM: location.verticalAccuracy,
                timestamp: location.timestamp,
                isSimulatedBySoftware: location.sourceInformation?.isSimulatedBySoftware
            )
        }
        guard !freshEvents.isEmpty else { return }
        if Thread.isMainThread {
            MainActor.assumeIsolated {
                for event in freshEvents { self.deliver?(.location(event)) }
            }
        } else {
            Task { @MainActor [weak self] in
                for event in freshEvents { self?.deliver?(.location(event)) }
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
        let now = Date()
        guard now.timeIntervalSince(newHeading.timestamp) <= SensorMapping.maxHeadingAgeSec else { return }
        let trueDeg = newHeading.trueHeading
        let accDeg = newHeading.headingAccuracy
        let ts = newHeading.timestamp
        if Thread.isMainThread {
            MainActor.assumeIsolated {
                self.deliver?(.heading(HeadingEvent(trueHeadingDeg: trueDeg, headingAccuracyDeg: accDeg, timestamp: ts, orientation: self.orientation)))
            }
        } else {
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.deliver?(.heading(HeadingEvent(trueHeadingDeg: trueDeg, headingAccuracyDeg: accDeg, timestamp: ts, orientation: self.orientation)))
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: any Error) {}

    nonisolated static func device(_ orientation: HeadingOrientation) -> CLDeviceOrientation {
        switch orientation {
        case .portrait: .portrait
        case .portraitUpsideDown: .portraitUpsideDown
        case .landscapeLeft: .landscapeLeft
        case .landscapeRight: .landscapeRight
        }
    }

    nonisolated static func authorization(_ status: CLAuthorizationStatus) -> LocationAuthorization? {
        switch status {
        case .notDetermined: .notDetermined
        case .restricted: .restricted
        case .denied: .denied
        case .authorizedAlways: .authorizedAlways
        case .authorizedWhenInUse: .authorizedWhenInUse
        @unknown default: nil
        }
    }

    nonisolated static func accuracy(_ accuracy: CLAccuracyAuthorization) -> AccuracyAuthorization? {
        switch accuracy {
        case .fullAccuracy: .full
        case .reducedAccuracy: .reduced
        @unknown default: nil
        }
    }
}
