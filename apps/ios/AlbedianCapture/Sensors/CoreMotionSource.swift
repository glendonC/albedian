import AlbedianCaptureCore
import CoreMotion
import Foundation

/// Device motion from `CMMotionManager`, each `CMDeviceMotion` copied field by field into the core's
/// `DeviceMotionEvent` (unit mobile-1). Isolated to `@MainActor` so `manager` is started and stopped on one actor;
/// samples arrive on one serial queue of this source's own. When `.xTrueNorthZVertical` reports
/// `CMErrorTrueNorthNotAvailable` (Location Services off or denied), it falls back to `.xArbitraryZVertical`.
@MainActor
final class CoreMotionSource: DeviceMotionSource {
    private let manager = CMMotionManager()
    private var streamGeneration = 0
    private let queue: OperationQueue = {
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        queue.name = "Albedian device motion"
        return queue
    }()

    var availableFrames: Set<AttitudeFrame> {
        guard manager.isDeviceMotionAvailable else { return [] }
        let frames = CMMotionManager.availableAttitudeReferenceFrames()
        return Set(AttitudeFrame.allCases.filter { frames.contains(Self.reference($0)) })
    }

    func start(frame: AttitudeFrame, intervalSec: Double, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
        streamGeneration &+= 1
        let current = streamGeneration
        startUpdates(frame: frame, intervalSec: intervalSec, generation: current, deliver: deliver)
    }

    private func startUpdates(frame: AttitudeFrame, intervalSec: Double, generation: Int,
                              deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
        manager.deviceMotionUpdateInterval = intervalSec
        manager.showsDeviceMovementDisplay = true
        manager.startDeviceMotionUpdates(using: Self.reference(frame), to: queue) { [weak self] motion, error in
            if let error {
                let nsError = error as NSError
                let domain = nsError.domain
                let code = nsError.code
                Task { @MainActor [weak self] in
                    self?.fallBackIfNeeded(domain: domain, code: code, requested: frame, intervalSec: intervalSec,
                                           generation: generation, deliver: deliver)
                }
                return
            }
            guard let motion else { return }
            let quaternion = motion.attitude.quaternion
            deliver(DeviceMotionEvent(
                timestampSec: motion.timestamp,
                frame: frame,
                quaternion: PointingRecord.Quaternion(x: quaternion.x, y: quaternion.y, z: quaternion.z, w: quaternion.w),
                gravityG: PointingRecord.Vector3(x: motion.gravity.x, y: motion.gravity.y, z: motion.gravity.z),
                rotationRateRadPerSec: PointingRecord.Vector3(x: motion.rotationRate.x, y: motion.rotationRate.y, z: motion.rotationRate.z)
            ))
        }
    }

    private func fallBackIfNeeded(domain: String, code: Int, requested: AttitudeFrame, intervalSec: Double,
                                  generation: Int, deliver: @escaping @Sendable (DeviceMotionEvent) -> Void) {
        guard streamGeneration == generation,
              let next = SensorMapping.fallbackFrame(afterErrorDomain: domain, code: code, requested: requested,
                                                     available: availableFrames) else { return }
        manager.stopDeviceMotionUpdates()
        startUpdates(frame: next, intervalSec: intervalSec, generation: generation, deliver: deliver)
    }

    func stop() {
        streamGeneration &+= 1
        manager.stopDeviceMotionUpdates()
    }

    nonisolated static func reference(_ frame: AttitudeFrame) -> CMAttitudeReferenceFrame {
        switch frame {
        case .xArbitraryZVertical: .xArbitraryZVertical
        case .xArbitraryCorrectedZVertical: .xArbitraryCorrectedZVertical
        case .xMagneticNorthZVertical: .xMagneticNorthZVertical
        case .xTrueNorthZVertical: .xTrueNorthZVertical
        }
    }
}
