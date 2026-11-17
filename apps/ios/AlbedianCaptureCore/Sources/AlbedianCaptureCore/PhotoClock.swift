import Foundation

/// The device clock at the photo (tasks.md 8). AVFoundation gives the photo's time on the session's synchronization
/// clock, which the app moves to the boot clock with `CMSyncConvertTime`; the device clock is then read once beside the
/// boot clock after the shot, and the difference between the two boot clock times is counted back (or forward) from it.
public enum PhotoClock {
    /// The device clock when the boot clock read `photo`, given one pair of readings taken together; nil when any
    /// reading is not a finite number.
    public static func deviceClock(atBootClockSec photo: Double, nowBootClockSec: Double, nowDeviceClock: Date) -> Date? {
        guard photo.isFinite, nowBootClockSec.isFinite, nowDeviceClock.timeIntervalSince1970.isFinite else { return nil }
        return nowDeviceClock.addingTimeInterval(photo - nowBootClockSec)
    }
}
