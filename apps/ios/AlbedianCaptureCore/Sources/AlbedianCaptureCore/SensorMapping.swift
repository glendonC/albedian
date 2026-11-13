import Foundation

/// Core Motion's and Core Location's reports carried onto the core's types (tasks.md 7): every value as the device
/// gave it, nothing rounded, clamped or filled in. The builder judges the values; the mapping only names them.
public enum SensorMapping {
    /// One device-motion sample every 0.01 s, 100 Hz [proposed, tasks.md 7].
    public static let motionIntervalSec = 0.01

    /// The frame to start device motion in: true north when the device has it (the only frame that gives a heading),
    /// else the arbitrary frame, whose gravity and rotation rate are the same; nil when the device has neither.
    public static func frameToRequest(available: Set<AttitudeFrame>) -> AttitudeFrame? {
        if available.contains(.xTrueNorthZVertical) { return .xTrueNorthZVertical }
        if available.contains(.xArbitraryZVertical) { return .xArbitraryZVertical }
        return nil
    }

    public static func sample(_ event: DeviceMotionEvent) -> MotionSample {
        MotionSample(bootClockSec: event.timestampSec,
                     referenceFrame: event.frame == .xTrueNorthZVertical ? .xTrueNorthZVertical : .other,
                     quaternion: event.quaternion, gravityG: event.gravityG, rotationRateRadPerSec: event.rotationRateRadPerSec)
    }

    public static func heading(_ event: HeadingEvent) -> HeadingReading {
        HeadingReading(trueHeadingDeg: event.trueHeadingDeg, headingAccuracyDeg: event.headingAccuracyDeg,
                       orientation: event.orientation, deviceClock: event.timestamp)
    }

    /// A fix with no source information is not one the system said it simulated.
    public static func fix(_ event: LocationEvent) -> LocationFix {
        LocationFix(latDeg: event.latDeg, lonDeg: event.lonDeg, horizontalAccuracyM: event.horizontalAccuracyM, altitudeM: event.altitudeM,
                    ellipsoidalAltitudeM: event.ellipsoidalAltitudeM, verticalAccuracyM: event.verticalAccuracyM,
                    deviceClock: event.timestamp, isSimulatedBySoftware: event.isSimulatedBySoftware ?? false)
    }

    /// A place only while the person allows location (when in use or always) and the accuracy granted is known.
    public static func location(authorization: LocationAuthorization?, accuracy: AccuracyAuthorization?, fix event: LocationEvent?) -> LocationReading {
        guard let authorization, let accuracy else { return .notAllowed }
        switch authorization {
        case .authorizedWhenInUse, .authorizedAlways: return .allowed(accuracy, fix: event.map(fix))
        case .notDetermined, .restricted, .denied: return .notAllowed
        }
    }

    /// The device axis to measure the heading of, for an interface orientation: `UIInterfaceOrientation.landscapeLeft`
    /// is `UIDeviceOrientation.landscapeRight` and the other way round (UIOrientation.h), and `CLDeviceOrientation`
    /// names device orientations. Nil for an unknown one.
    public static func headingOrientation(for interface: InterfaceOrientation) -> HeadingOrientation? {
        switch interface {
        case .portrait: .portrait
        case .portraitUpsideDown: .portraitUpsideDown
        case .landscapeLeft: .landscapeRight
        case .landscapeRight: .landscapeLeft
        case .unknown: nil
        }
    }
}

/// Between the sensor sources and the builder: it starts them, keeps what they report, and at a photo hands the
/// builder a `SensorCapture`. The sources may report from their own threads: what they report is behind one lock.
/// `start` and `stop` run on the main actor only (the compiler holds every caller to it), so they never overlap: once
/// `stop` returns, no source is running, and none is started later by an earlier `start`.
public final class SensorRecorder: @unchecked Sendable {
    /// The headings and fixes kept, each the newest 32 [proposed]: they arrive about once a second or on a change,
    /// so 32 cover far more than the time between aiming and the shutter.
    public static let keptEventCount = 32

    public enum StartOutcome: Equatable, Sendable {
        /// Device motion runs in this frame.
        case started(AttitudeFrame)
        /// The device has no device-motion frame: place and heading run, and no record can be written.
        case noDeviceMotion
    }

    /// What the recorder holds now, for a check screen.
    public struct Status: Equatable, Sendable {
        public var running: Bool
        public var frame: AttitudeFrame?
        public var samplesKept: Int
        public var samplesOverwritten: Int
        public var samplesRefused: Int
        public var authorization: LocationAuthorization?
        public var accuracy: AccuracyAuthorization?
        /// The newest by arrival.
        public var lastHeading: HeadingEvent?
        public var lastFix: LocationEvent?
    }

    private let motion: any DeviceMotionSource
    private let place: any PlaceAndHeadingSource
    private let ringCapacity: Int
    private let lock = NSLock()
    private var ring: MotionRing
    /// Counts every start, so a report from an earlier start, or one after a stop, is not kept.
    private var generation = 0
    private var running = false
    private var frame: AttitudeFrame?
    private var authorization: LocationAuthorization?
    private var accuracy: AccuracyAuthorization?
    private var headings: [HeadingEvent] = []
    private var fixes: [LocationEvent] = []

    /// Nil for a ring capacity `MotionRing` does not take.
    public init?(motion: any DeviceMotionSource, place: any PlaceAndHeadingSource, ringCapacity: Int = MotionRing.defaultCapacity) {
        guard let ring = MotionRing(capacity: ringCapacity) else { return nil }
        self.motion = motion
        self.place = place
        self.ringCapacity = ringCapacity
        self.ring = ring
    }

    /// Starts both sources; a second start while running starts nothing more. The heading is measured for the axis
    /// of `interface`, or for portrait, Core Location's default, when it is unknown.
    @MainActor
    @discardableResult
    public func start(interface: InterfaceOrientation) -> StartOutcome {
        let (current, started): (Int, Bool) = lock.withLock {
            if running { return (generation, false) }
            generation += 1
            running = true
            ring = MotionRing(capacity: ringCapacity)!
            frame = SensorMapping.frameToRequest(available: motion.availableFrames)
            authorization = nil
            accuracy = nil
            headings = []
            fixes = []
            return (generation, true)
        }
        let chosen = lock.withLock { frame }
        guard started else { return chosen.map(StartOutcome.started) ?? .noDeviceMotion }
        if let chosen {
            motion.start(frame: chosen, intervalSec: SensorMapping.motionIntervalSec) { [weak self] event in
                self?.receive(event, generation: current)
            }
        }
        place.start(headingOrientation: SensorMapping.headingOrientation(for: interface) ?? .portrait) { [weak self] event in
            self?.receive(event, generation: current)
        }
        return chosen.map(StartOutcome.started) ?? .noDeviceMotion
    }

    @MainActor
    public func stop() {
        let (wasRunning, hadMotion) = lock.withLock {
            let state = (running, frame != nil)
            running = false
            return state
        }
        guard wasRunning else { return }
        if hadMotion { motion.stop() }
        place.stop()
    }

    func receive(_ event: DeviceMotionEvent, generation from: Int) {
        lock.withLock {
            guard running, from == generation else { return }
            ring.append(SensorMapping.sample(event))
        }
    }

    func receive(_ event: PlaceEvent, generation from: Int) {
        lock.withLock {
            guard running, from == generation else { return }
            switch event {
            case .authorization(let status, let granted):
                authorization = status
                accuracy = granted
            case .heading(let heading):
                headings.append(heading)
                if headings.count > Self.keptEventCount { headings.removeFirst() }
            case .location(let fix):
                fixes.append(fix)
                if fixes.count > Self.keptEventCount { fixes.removeFirst() }
            }
        }
    }

    public var status: Status {
        lock.withLock {
            Status(running: running, frame: frame, samplesKept: ring.count, samplesOverwritten: ring.overwrittenCount,
                   samplesRefused: ring.refusedCount, authorization: authorization, accuracy: accuracy, lastHeading: headings.last,
                   lastFix: fixes.last)
        }
    }

    /// What the sensors said around a photo: the ring's samples for the window, and the newest heading and fix whose
    /// own time is not after the photo's (by their time, not their arrival; of two with the same time, the one that
    /// arrived later). The camera's reading and the photo's clocks come from the camera (unit mobile-2).
    public func capture(photoSha256: String, photoBootClockSec: Double, photoDeviceClock: Date, device: DeviceLabels,
                        camera: CameraReading, windowStartOffsetSec: Double, windowEndOffsetSec: Double) -> SensorCapture {
        lock.withLock {
            let heading = Self.newest(headings, notAfter: photoDeviceClock, time: \.timestamp)
            let fix = Self.newest(fixes, notAfter: photoDeviceClock, time: \.timestamp)
            return SensorCapture(
                photoSha256: photoSha256, photoBootClockSec: photoBootClockSec, photoDeviceClock: photoDeviceClock, device: device,
                camera: camera,
                motion: ring.reading(photoBootClockSec: photoBootClockSec, windowStartOffsetSec: windowStartOffsetSec,
                                     windowEndOffsetSec: windowEndOffsetSec),
                heading: heading.map(SensorMapping.heading),
                location: SensorMapping.location(authorization: authorization, accuracy: accuracy, fix: fix)
            )
        }
    }

    /// The report with the latest time not after `moment`; of several with that time, the last to arrive.
    static func newest<Report>(_ reports: [Report], notAfter moment: Date, time: (Report) -> Date) -> Report? {
        var best: Report?
        for report in reports where time(report) <= moment {
            if let current = best, time(report) < time(current) { continue }
            best = report
        }
        return best
    }
}
