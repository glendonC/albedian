import AlbedianCaptureCore
import CoreMotion
import Foundation

/// Device motion from `CMMotionManager`, each `CMDeviceMotion` copied field by field into the core's
/// `DeviceMotionEvent` (unit mobile-1). The samples arrive on one serial queue of this source's own. While the device
/// needs moving to calibrate its compass, the system shows its own display (`showsDeviceMovementDisplay`).
/// On a device only: the simulator has no motion sensors, so there `availableFrames` is empty.
final class CoreMotionSource: DeviceMotionSource, @unchecked Sendable {
    // The manager is only used from start and stop, which the app calls on the main thread; Core Motion hands the
    // samples to `queue`, whose handler touches nothing of this object
    private let manager = CMMotionManager()
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
        manager.deviceMotionUpdateInterval = intervalSec
        manager.showsDeviceMovementDisplay = true
        // A sample that comes with an error, or without motion, is not delivered: the ring then holds no sample at
        // that time, and the builder says no sample was at the photo
        manager.startDeviceMotionUpdates(using: Self.reference(frame), to: queue) { motion, error in
            guard let motion, error == nil else { return }
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

    func stop() {
        manager.stopDeviceMotionUpdates()
    }

    static func reference(_ frame: AttitudeFrame) -> CMAttitudeReferenceFrame {
        switch frame {
        case .xArbitraryZVertical: .xArbitraryZVertical
        case .xArbitraryCorrectedZVertical: .xArbitraryCorrectedZVertical
        case .xMagneticNorthZVertical: .xMagneticNorthZVertical
        case .xTrueNorthZVertical: .xTrueNorthZVertical
        }
    }
}
