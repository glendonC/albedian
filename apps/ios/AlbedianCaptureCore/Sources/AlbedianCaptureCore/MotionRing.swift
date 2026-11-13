import Foundation

/// The newest device-motion samples, in time order, kept until the shutter (tasks.md 7). A sample whose time is not
/// finite, or not after the last one kept, is refused and counted; once the ring is full each new sample pushes the
/// oldest out, and that is counted too. Every sample appended is kept, pushed out later, or refused: nothing else.
public struct MotionRing: Sendable {
    /// 6000 samples: 60 s at 100 Hz [proposed]; a photo's window needs a few seconds at most.
    public static let defaultCapacity = 6000
    public let capacity: Int
    /// Samples pushed out by newer ones.
    public private(set) var overwrittenCount = 0
    /// Samples not kept: a time that is not finite, or not after the last sample kept.
    public private(set) var refusedCount = 0
    private var storage: [MotionSample] = []
    /// Where the oldest sample is, once the storage is full.
    private var head = 0
    private var lastTime: Double?

    /// Nil for a capacity below one or above the contract's `sampleCountMax`.
    public init?(capacity: Int = MotionRing.defaultCapacity) {
        guard capacity >= 1, capacity <= PointingContract.sampleCountMax else { return nil }
        self.capacity = capacity
        storage.reserveCapacity(capacity)
    }

    /// Keeps a sample and says whether it was kept.
    @discardableResult
    public mutating func append(_ sample: MotionSample) -> Bool {
        guard sample.bootClockSec.isFinite else { refusedCount += 1; return false }
        if let lastTime, !(sample.bootClockSec > lastTime) { refusedCount += 1; return false }
        lastTime = sample.bootClockSec
        if storage.count < capacity {
            storage.append(sample)
        } else {
            storage[head] = sample
            head = (head + 1) % capacity
            overwrittenCount += 1
        }
        return true
    }

    public var count: Int { storage.count }

    /// The samples kept, oldest first.
    public var samples: [MotionSample] { Array(storage[head...] + storage[..<head]) }

    /// The samples a photo needs, with the window as given: those from the window's start to its end, and always
    /// those within the contract's offset limit on each side of the photo, so the builder can find the photo's own
    /// sample. A window end that is not finite is replaced by the limit for choosing samples only; the reading keeps
    /// it as given, so the builder names that fault.
    public func reading(photoBootClockSec: Double, windowStartOffsetSec: Double, windowEndOffsetSec: Double) -> MotionReading {
        let limit = PointingContract.motionSampleOffsetLimitSec
        let low = windowStartOffsetSec.isFinite ? min(windowStartOffsetSec, -limit) : -limit
        let high = windowEndOffsetSec.isFinite ? max(windowEndOffsetSec, limit) : limit
        let chosen = samples.filter { sample in
            let offset = sample.bootClockSec - photoBootClockSec
            return low <= offset && offset <= high
        }
        return MotionReading(samples: chosen, windowStartOffsetSec: windowStartOffsetSec, windowEndOffsetSec: windowEndOffsetSec)
    }
}
