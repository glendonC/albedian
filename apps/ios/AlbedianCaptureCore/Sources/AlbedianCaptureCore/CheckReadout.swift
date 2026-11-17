import Foundation
import Observation

/// The check screen's words for what the sensors said (unit mobile-2), kept in the core so they are tested on the Mac.
/// It names no place: only whether one is allowed and the accuracy stated for it. Every number is checked before it is
/// written, so a reading that is not a number reads as such and never stops the app.
public enum CheckReadout {
    public static let none = "none"

    /// The person's choice for location, in plain words.
    public static func location(_ authorization: LocationAuthorization?) -> String {
        switch authorization {
        case nil: "not reported"
        case .notDetermined?: "not asked yet"
        case .restricted?: "restricted"
        case .denied?: "not allowed"
        case .authorizedAlways?: "allowed always"
        case .authorizedWhenInUse?: "allowed while in use"
        }
    }

    public static func accuracyGranted(_ accuracy: AccuracyAuthorization?) -> String {
        switch accuracy {
        case nil: "not reported"
        case .full?: "full"
        case .reduced?: "reduced"
        }
    }

    public static func frame(_ frame: AttitudeFrame?) -> String {
        switch frame {
        case nil: "not started"
        case .xArbitraryZVertical?: "arbitrary"
        case .xArbitraryCorrectedZVertical?: "arbitrary, corrected"
        case .xMagneticNorthZVertical?: "magnetic north"
        case .xTrueNorthZVertical?: "true north"
        }
    }

    /// The true heading to a tenth of a degree; Apple's own words when it is negative ("could not be determined").
    public static func trueHeading(_ heading: HeadingEvent?) -> String {
        guard let heading else { return none }
        let value = heading.trueHeadingDeg
        guard !value.isNaN else { return "not a number" }
        guard value.isFinite else { return "infinite" }
        guard value >= 0 else { return "could not be determined" }
        return "\(fixed(value, places: 1)) deg"
    }

    /// Apple states `headingAccuracy` for the magnetic heading, so the screen names it so; negative means invalid.
    public static func magneticHeadingAccuracy(_ heading: HeadingEvent?) -> String {
        guard let heading else { return none }
        let value = heading.headingAccuracyDeg
        guard !value.isNaN else { return "not a number" }
        guard value.isFinite else { return "infinite" }
        guard value >= 0 else { return "invalid" }
        return "\u{B1}\(fixed(value, places: 1)) deg"
    }

    /// The radius of the place's stated accuracy in whole metres; negative means the fix is invalid. The place itself is
    /// not shown.
    public static func placeAccuracy(_ fix: LocationEvent?) -> String {
        guard let fix else { return none }
        let value = fix.horizontalAccuracyM
        guard !value.isNaN else { return "not a number" }
        guard value.isFinite else { return "infinite" }
        guard value >= 0 else { return "invalid" }
        return "\u{B1}\(fixed(value, places: 0)) m"
    }

    /// What a keep ended in, in plain words: kept, or why not and what is known of the photo on the phone now (read
    /// from the store's folder by the keep itself, or that it could not be read), so the words, the store's list and
    /// the files agree. No value of the record is ever written (the contract's issue messages are not shown).
    public static func keepOutcome(_ result: Result<StoredCapture, CaptureStoreFailure>) -> String {
        switch result {
        case .success(let kept): return kept.hasRecord ? "Photo and pointing record kept" : "Photo kept, no pointing record"
        case .failure(let failure): return "Not kept: " + why(failure.reason) + holding(failure)
        }
    }

    static func why(_ reason: CaptureStoreError) -> String {
        switch reason {
        case .recordNamesAnotherPhoto: "the record names another photo"
        case .recordNotContract: "the record is not one the contract takes"
        case .readBackDiffers: "a file read back other bytes than were written"
        case .readBackFailed(_, let code): "a file just written could not be read back (error \(code))"
        case .storedBytesChanged: "a photo file under this name holds other bytes"
        case .storedRecordChanged: "the record file under this name is not this photo's record"
        case .notKept: "nothing is kept under that name"
        case .fileSystem(let code): "the phone's storage refused (error \(code))"
        }
    }

    /// What is known of the photo after a failed keep, and no more: what the store read from its folder; that it could
    /// not read the folder before the keep wrote anything; or that it could not read it afterwards, so what is left is
    /// not known [proposed wording, for the owner].
    static func holding(_ failure: CaptureStoreFailure) -> String {
        switch failure.afterwards {
        case .notReadNothingChanged: "; the phone's storage could not be read, and nothing of this photo was written"
        case .notReadAfterChanges: "; the phone's storage could not be read afterwards, so what is left of this photo on the phone is not known"
        case .holding(.nothing): failure.stray.isEmpty ? "; nothing of it is on the phone" : "; nothing of it is on the phone but a record file without its photo"
        case .holding(.photoWithRecord): "; a photo and its record are on the phone, checked again before any send"
        case .holding(.photoAlone): "; the photo is on the phone without a record"
        case .notWhole: "; a file of this photo is on the phone but does not read back as it was written, so it is not kept and is never sent"
        }
    }

    /// Every sentence a keep can end in, for the owner to read [proposed wording, each]: both kept sentences; each
    /// reason a keep can give, with what it can end in (a file found changed on the disk ends not whole; the others can
    /// leave nothing); a record file left without its photo; and each thing known afterwards. `notKept` is a removal's
    /// reason and never a keep's, so it is not here.
    public static func everyKeepOutcome() -> [String] {
        let sha = String(repeating: "0", count: 64)
        let reasons: [CaptureStoreError] = [
            .recordNamesAnotherPhoto, .recordNotContract([]), .readBackDiffers(file: "x"), .readBackFailed(file: "x", code: 256), .fileSystem(code: 640),
        ]
        let changedOnTheDisk: [CaptureStoreError] = [.storedBytesChanged(sha256: sha), .storedRecordChanged(sha256: sha)]
        let afterwards: [CaptureAfterFailure] = [
            .holding(.photoWithRecord), .holding(.photoAlone), .notWhole, .notReadNothingChanged(code: 257), .notReadAfterChanges(code: 257),
        ]
        func failed(_ reason: CaptureStoreError, _ after: CaptureAfterFailure, _ stray: [String] = []) -> String {
            keepOutcome(.failure(CaptureStoreFailure(reason: reason, afterwards: after, stray: stray)))
        }
        return [true, false].map { keepOutcome(.success(StoredCapture(sha256: sha, contentType: .heic, hasRecord: $0))) }
            + reasons.map { failed($0, .holding(.nothing)) }
            + changedOnTheDisk.map { failed($0, .notWhole) }
            + [failed(.fileSystem(code: 513), .holding(.nothing), ["\(sha).pointing.json"])]
            + afterwards.map { failed(.fileSystem(code: 640), $0) }
    }

    /// Every word the readings can show, for the owner to read [proposed wording, each]: each choice and state the
    /// system can report, the words for a value that is missing, not a number, infinite or invalid, and one number of
    /// each kind as it is written.
    public static func everyValueWord() -> [String] {
        let locations: [LocationAuthorization?] = [nil, .notDetermined, .restricted, .denied, .authorizedAlways, .authorizedWhenInUse]
        let accuracies: [AccuracyAuthorization?] = [nil, .full, .reduced]
        let frames: [AttitudeFrame?] = [nil, .xArbitraryZVertical, .xArbitraryCorrectedZVertical, .xMagneticNorthZVertical, .xTrueNorthZVertical]
        var range = GravityRange()
        let emptyRange = range.text
        range.add(0.98)
        range.add(1.02)
        return locations.map(location) + accuracies.map(accuracyGranted) + frames.map(frame)
            + [none, fixed(.nan, places: 1), fixed(.infinity, places: 1), "could not be determined", "invalid", emptyRange, range.text,
               "\(fixed(123.4, places: 1)) deg", "\u{B1}\(fixed(5, places: 1)) deg", "\u{B1}\(fixed(12, places: 0)) m", fixed(0.98, places: 2)]
    }

    /// Gravity in g along the device's x, y and z, two decimals each.
    public static func gravity(_ sample: MotionSample?) -> String {
        guard let g = sample?.gravityG else { return none }
        return [g.x, g.y, g.z].map { fixed($0, places: 2) }.joined(separator: ", ")
    }

    /// The quaternion's x, y, z and w, three decimals each, so its signs can be read.
    public static func quaternion(_ sample: MotionSample?) -> String {
        guard let q = sample?.quaternion else { return none }
        return [q.x, q.y, q.z, q.w].map { fixed($0, places: 3) }.joined(separator: ", ")
    }

    /// The length of gravity in g, or nil for no sample or a component that is not a number.
    public static func gravityLength(_ sample: MotionSample?) -> Double? {
        guard let g = sample?.gravityG, g.x.isFinite, g.y.isFinite, g.z.isFinite else { return nil }
        return (g.x * g.x + g.y * g.y + g.z * g.z).squareRoot()
    }

    /// `value` with exactly `places` decimals; a value that rounds to zero is written 0 with no sign, so a sign read
    /// from the screen is a sign the sensor gave.
    public static func fixed(_ value: Double, places: Int) -> String {
        guard !value.isNaN else { return "not a number" }
        guard value.isFinite else { return "infinite" }
        let text = String(format: "%.\(places)f", value)
        return text.hasPrefix("-") && Double(text) == 0 ? String(text.dropFirst()) : text
    }
}

/// The smallest and largest of the gravity lengths the screen has shown since it last started.
public struct GravityRange: Equatable, Sendable {
    public private(set) var smallest: Double?
    public private(set) var largest: Double?

    public init() {}

    public mutating func add(_ length: Double?) {
        guard let length, length.isFinite else { return }
        smallest = min(smallest ?? length, length)
        largest = max(largest ?? length, length)
    }

    /// "smallest to largest" with two decimals, or none before the first length.
    public var text: String {
        guard let smallest, let largest else { return CheckReadout.none }
        return "\(CheckReadout.fixed(smallest, places: 2)) to \(CheckReadout.fixed(largest, places: 2))"
    }
}


/// What the check screen knows of the store's count while its reads run off the main actor.
public enum CheckCount: Equatable, Sendable {
    /// No count has finished yet.
    case reading
    /// Captures that read back whole and captures there by name that do not.
    case counted(whole: Int, notWhole: Int)
    /// The folder or a file in it could not be read.
    case couldNotBeRead

    /// What the "Kept on the phone" row shows [proposed wording, for the owner].
    public var keptWords: String {
        switch self {
        case .reading: "reading"
        case .counted(let whole, _): "\(whole)"
        case .couldNotBeRead: "could not be read"
        }
    }

    /// The count for the "On the phone, not whole" row, or nil when none is known to be not whole.
    public var notWholeCount: Int? {
        if case .counted(_, let notWhole) = self, notWhole > 0 { return notWhole }
        return nil
    }
}

/// What record bytes a shot keeps beside its photo, built off the main actor.
public enum ShotRecordStep: Equatable, Sendable {
    case none
    case wire(Data)
    case couldNotWriteJSON

    public static func build(
        photoBytes: Data,
        recorder: SensorRecorder?,
        photoBootClockSec: Double,
        photoDeviceClock: Date,
        device: DeviceLabels,
        camera: CameraReading,
        exposureSec: Double?,
        encodeRecord: (PointingRecord) throws -> Data = { try $0.wireData() }
    ) -> ShotRecordStep {
        guard let recorder else { return .none }
        // The window: the exposure the device reported, with the contract's 0.05 s on each side [proposed]
        let sha256 = PhotoHash.sha256Hex(photoBytes)
        let limit = PointingContract.motionSampleOffsetLimitSec
        let raw = recorder.capture(
            photoSha256: sha256,
            photoBootClockSec: photoBootClockSec,
            photoDeviceClock: photoDeviceClock,
            device: device,
            camera: camera,
            windowStartOffsetSec: -limit,
            windowEndOffsetSec: (exposureSec ?? 0) + limit
        )
        guard let record = try? PointingRecordBuilder.build(raw) else { return .none }
        // A record that was made and cannot be written as JSON is not dropped: the photo is then not kept either
        guard let wire = try? encodeRecord(record) else { return .couldNotWriteJSON }
        return .wire(wire)
    }
}

/// The check screen's photo and store state (mobile-2's fifth version): keeps, counts and SHA-256 checks run on the
/// store's queue off the main actor, and outcomes land on the main actor in arrival order without an older keep
/// overwriting a newer shot.
@MainActor
@Observable
public final class CheckCaptureState {
    public private(set) var lastOutcome = "No photo yet"
    public private(set) var keptCount: CheckCount = .reading
    private var taking = false
    private var keeping = 0
    public var isBusy: Bool { taking || keeping > 0 }
    private var generation: UInt64 = 0
    private var pending: Task<Void, Never>?
    private let store: CaptureStore

    public init(store: CaptureStore) {
        self.store = store
    }

    @discardableResult
    public func refreshCount() -> Task<Void, Never> {
        let previous = pending
        let store = store
        let task = Task { @MainActor [weak self] in
            await previous?.value
            let counted = await store.countWhole()
            self?.keptCount = counted
        }
        pending = task
        return task
    }

    public func beginShot() {
        generation &+= 1
        taking = true
        lastOutcome = "Taking the photo"
    }

    @discardableResult
    public func cameraFailed(_ description: String) -> Task<Void, Never> {
        taking = false
        let shotGeneration = generation
        let previous = pending
        let task = Task { @MainActor [weak self] in
            await previous?.value
            guard let self, self.generation == shotGeneration else { return }
            self.lastOutcome = "No photo: \(description)"
        }
        pending = task
        return task
    }

    @discardableResult
    public func keepShot(
        photo: Data,
        contentType: PhotoContentType,
        makeRecord: @escaping @Sendable () -> ShotRecordStep
    ) -> Task<Void, Never> {
        if !taking { generation &+= 1 }
        taking = false
        keeping += 1
        let shotGeneration = generation
        let previous = pending
        let store = store
        let task = Task { @MainActor [weak self] in
            await previous?.value
            let done: (words: String, count: CheckCount) = await store.offMain { store in
                let wire: Data?
                switch makeRecord() {
                case .couldNotWriteJSON:
                    return ("Not kept: the pointing record could not be written as JSON", store.countedSync())
                case .none:
                    wire = nil
                case .wire(let bytes):
                    wire = bytes
                }
                return (CheckReadout.keepOutcome(store.keepSync(photo: photo, contentType: contentType, recordWire: wire)), store.countedSync())
            }
            guard let self else { return }
            self.keeping -= 1
            self.keptCount = done.count
            if self.generation == shotGeneration {
                self.lastOutcome = done.words
            }
        }
        pending = task
        return task
    }
}
