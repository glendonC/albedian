import Foundation

/// Why a reply about a solve was not taken. Each names the path of what was wrong, never a value from the reply.
public struct SolveReadRefusal: Error, Equatable, Sendable, CustomStringConvertible {
    public let path: String
    public let reason: String
    public var description: String { "\(path): \(reason)" }
}

/// A solve job as the laptop reports it: the runner's record (`UploadJobSchema` of kind `solve`,
/// `packages/contracts/src/jobs.ts`), or a record the solve wrote before the runner (`SolveJobSchema`,
/// `packages/contracts/src/ingest.ts`), as the web reads both. Its state, and each stage's own count of real work,
/// `total` null until known (REQ-IOS-4.2: progress from the job's own denominators).
public struct SolveJob: Equatable, Sendable {
    /// A solve's states (`rejected` is a media job's only; `interrupted` is the runner's, for a job the server stopped).
    public enum State: String, Sendable, CaseIterable { case queued, running, succeeded, failed, canceled, interrupted }
    /// The stages in pipeline order (`JOB_STAGES`).
    public enum Stage: String, Sendable, CaseIterable { case store, decode, detect, solve, verify }
    public enum Unit: String, Sendable, CaseIterable { case bytes, frames, candidates, steps }
    public struct StageProgress: Equatable, Sendable {
        public let stage: Stage
        public let unit: Unit
        public let done: Int
        /// Nil while the server does not know it; then only elapsed time may be shown for the stage.
        public let total: Int?
        /// The search the stage is on, for a solve stage that reports one; a new pass starts its count again.
        public let pass: Pass?
        public init(stage: Stage, unit: Unit, done: Int, total: Int?, pass: Pass? = nil) {
            self.stage = stage
            self.unit = unit
            self.done = done
            self.total = total
            self.pass = pass
        }
    }
    public struct Pass: Equatable, Sendable {
        public enum Label: String, Sendable, CaseIterable { case phonePhotoHinted = "phone_photo_hinted", phonePhotoGeneric = "phone_photo_generic", triangle }
        public let number: Int
        public let label: Label
    }
    public struct Producer: Equatable, Sendable {
        public let name: String
        public let revision: Int
    }
    /// A clip's decoded frames.
    public struct InputSpan: Equatable, Sendable {
        public let firstFrameIndex: Int
        public let frameCount: Int
        public let startSec: Double
        public let endSec: Double
    }
    public let id: String
    public let sourceAssetSha256: String
    public let state: State
    public let createdAtUTC: String
    public let startedAtUTC: String?
    public let finishedAtUTC: String?
    public let stages: [StageProgress]
    public let solutionId: String?
    public let error: String?
    /// "solve" for the runner's record; nil for a record written before the runner.
    public var kind: String?
    public var producer: Producer?
    /// Queued only: the jobs ahead of it, counted when the laptop answered.
    public var queueAhead: Int?
    public var inputSpan: InputSpan?
    public var cancelRequestedAtUTC: String?

    /// Whether the job has ended, one way or another.
    public var hasEnded: Bool { state == .succeeded || state == .failed || state == .canceled || state == .interrupted }
}

/// Small checks shared by the readers, each the contract's own rule.
enum Wire {
    /// The largest whole number both JavaScript and Python carry exactly (2^53 - 1).
    static let maxSafeInteger = 9_007_199_254_740_991.0

    static func whole(_ value: JSONValue?) -> Int? {
        guard let number = value?.numberValue, number.isFinite, number.rounded(.towardZero) == number,
              abs(number) <= maxSafeInteger else { return nil }
        return Int(number)
    }

    static func lowerHex(_ text: Substring) -> Bool { text.utf8.allSatisfy { (0x30...0x39).contains($0) || (0x61...0x66).contains($0) } }

    /// `^[0-9a-f]{64}$`, the ingest pattern (64 zeros fit it; the solution's rule refuses them, `PhotoHash.isContractHash`).
    static func isSha256(_ text: String) -> Bool { text.utf8.count == 64 && lowerHex(text[...]) }

    /// `^job_[0-9a-f]{16,64}$` (`JOB_ID_PATTERN`).
    static func isJobId(_ text: String) -> Bool {
        guard text.hasPrefix("job_") else { return false }
        let hex = text.dropFirst(4)
        return (16...64).contains(hex.utf8.count) && lowerHex(hex)
    }

    /// A string of `range` UTF-16 units, well formed.
    static func string(_ value: JSONValue?, units range: ClosedRange<Int>) -> String? {
        guard case .string(let text)? = value, range.contains(text.units.count) else { return nil }
        return text.string
    }

    /// A contract instant, written so that two compare as instants by their text: a missing fraction is `.000`.
    static func instant(_ value: JSONValue?) -> String? {
        guard case .string(let text)? = value, UTCInstant.isValid(text), let string = text.string else { return nil }
        return string.utf16.count == 20 ? String(string.dropLast()) + ".000Z" : string
    }

    static func onlyKeys(_ object: JSONObject, _ allowed: Set<String>) -> String? {
        object.keys.first { !allowed.contains($0.string ?? "") }.map { $0.string ?? "?" }
    }
}

public enum SolveJobReader {
    static let keys: Set<String> = ["id", "sourceAssetSha256", "state", "createdAtUTC", "startedAtUTC", "finishedAtUTC",
                                    "stages", "solutionId", "error"]
    static let stageKeys: Set<String> = ["stage", "unit", "done", "total"]

    /// The job, or the first rule of `SolveJobSchema` it breaks.
    public static func read(_ value: JSONValue) -> Result<SolveJob, SolveReadRefusal> {
        func refuse(_ path: String, _ reason: String) -> Result<SolveJob, SolveReadRefusal> {
            .failure(SolveReadRefusal(path: path, reason: reason))
        }
        guard let object = value.objectValue else { return refuse("", "not an object") }
        // The runner's record names its kind; one written before the runner has no such key (the web reads them the same way)
        if object["kind"] != nil { return readRunnerRecord(object) }
        if let extra = Wire.onlyKeys(object, keys) { return refuse(extra, "not a key of the contract") }
        guard let id = object["id"]?.stringValue, Wire.isJobId(id) else { return refuse("id", "not a job id") }
        guard let sha = object["sourceAssetSha256"]?.stringValue, Wire.isSha256(sha) else { return refuse("sourceAssetSha256", "not a SHA-256") }
        guard let stateText = object["state"]?.stringValue, let state = SolveJob.State(rawValue: stateText) else {
            return refuse("state", "not a state of the contract")
        }
        guard let created = Wire.instant(object["createdAtUTC"]) else { return refuse("createdAtUTC", "not an instant") }
        let started = object["startedAtUTC"].map(Wire.instant)
        let finished = object["finishedAtUTC"].map(Wire.instant)
        if case .some(nil) = started { return refuse("startedAtUTC", "not an instant") }
        if case .some(nil) = finished { return refuse("finishedAtUTC", "not an instant") }
        let solutionId = object["solutionId"].map { Wire.string($0, units: 1...128) }
        if case .some(nil) = solutionId { return refuse("solutionId", "not a label") }
        let error = object["error"].map { Wire.string($0, units: 1...2000) }
        if case .some(nil) = error { return refuse("error", "not a sentence of 1 to 2000 characters") }

        guard let items = object["stages"]?.arrayValue else { return refuse("stages", "not a list") }
        var stages: [SolveJob.StageProgress] = []
        for (index, item) in items.enumerated() {
            let path = "stages.\(index)"
            guard let stage = item.objectValue else { return refuse(path, "not an object") }
            if let extra = Wire.onlyKeys(stage, stageKeys) { return refuse("\(path).\(extra)", "not a key of the contract") }
            guard let name = stage["stage"]?.stringValue, let kind = SolveJob.Stage(rawValue: name) else { return refuse("\(path).stage", "not a stage") }
            guard let unitName = stage["unit"]?.stringValue, let unit = SolveJob.Unit(rawValue: unitName) else { return refuse("\(path).unit", "not a unit") }
            guard let done = Wire.whole(stage["done"]), done >= 0 else { return refuse("\(path).done", "not a count") }
            guard let totalValue = stage["total"] else { return refuse("\(path).total", "null when unknown, never absent") }
            var total: Int?
            if totalValue != .null {
                guard let known = Wire.whole(totalValue), known > 0 else { return refuse("\(path).total", "not a positive count") }
                guard done <= known else { return refuse("\(path).done", "done cannot exceed total") }
                total = known
            }
            if let last = stages.last, SolveJob.Stage.allCases.firstIndex(of: kind)! <= SolveJob.Stage.allCases.firstIndex(of: last.stage)! {
                return refuse("stages", "stages appear once each, in pipeline order")
            }
            stages.append(SolveJob.StageProgress(stage: kind, unit: unit, done: done, total: total))
        }

        let has = (started: started != nil, finished: finished != nil, solution: solutionId != nil, error: error != nil)
        let fits: Bool
        switch state {
        case .queued: fits = !has.started && !has.finished && !has.solution && !has.error
        case .running: fits = has.started && !has.finished && !has.solution && !has.error
        case .succeeded: fits = has.started && has.finished && has.solution && !has.error
        case .failed: fits = has.started && has.finished && has.error && !has.solution
        case .canceled: fits = !has.started && has.finished && !has.solution && !has.error
        case .interrupted: fits = false  // a state of the runner's record only
        }
        guard fits else { return refuse("state", "fields do not fit a \(state.rawValue) job") }
        let startedAt = started ?? nil
        let finishedAt = finished ?? nil
        if let startedAt, startedAt < created { return refuse("finishedAtUTC", "times must run created, started, finished") }
        if let finishedAt, finishedAt < (startedAt ?? created) { return refuse("finishedAtUTC", "times must run created, started, finished") }
        return .success(SolveJob(id: id, sourceAssetSha256: sha, state: state, createdAtUTC: created, startedAtUTC: startedAt,
                                 finishedAtUTC: finishedAt, stages: stages, solutionId: solutionId ?? nil, error: error ?? nil))
    }
}

/// The four evidence classes (AGENTS.md section 1), as the wire writes them.
public enum EvidenceClass: String, Sendable, CaseIterable { case observed, catalogMatched = "catalog_matched", predicted, simulated }

/// A solution's status (`SolutionStatusSchema`, `packages/contracts/src/primitives.ts`).
public enum SolutionStatus: String, Sendable, CaseIterable {
    case pending, calibrated, approximate, predictedOnly = "predicted_only", unresolved, ambiguous
}

/// What the phone shows of a solution, as the server reports it (REQ-IOS-5.1): its status, how many findings it holds
/// in each evidence class, and the catalog matched findings with the pixel each was matched at. Nothing is computed
/// beyond counting the findings the server listed.
public struct SolutionReadout: Equatable, Sendable {
    public struct Mark: Equatable, Sendable {
        /// The iPhone names a mark only when the star is brighter than this magnitude (UIUX-reveal-READY, iPhone 1).
        public static let namedBrighterThanMagnitude = 3.0
        public let id: String
        public let title: String
        /// The matched detection's centroid in the stored frame (array indices, pixel centres at whole numbers).
        public let xPx: Double
        public let yPx: Double
        public let apparentMagnitude: Double?
        public init(id: String, title: String, xPx: Double, yPx: Double, apparentMagnitude: Double?) {
            self.id = id
            self.title = title
            self.xPx = xPx
            self.yPx = yPx
            self.apparentMagnitude = apparentMagnitude
        }
        public var isNamedOnThePhone: Bool { apparentMagnitude.map { $0 < Self.namedBrighterThanMagnitude } ?? false }
    }
    /// A pose estimate's method and how far off it may be, for the result's sentence (`skyLayerWords.ts`).
    public struct Pose: Equatable, Sendable {
        public let method: String
        public let maxCenterOffsetDeg: Double
        public let beaconCentroidUncertaintyDeg: Double
        public init(method: String, maxCenterOffsetDeg: Double, beaconCentroidUncertaintyDeg: Double) {
            self.method = method
            self.maxCenterOffsetDeg = maxCenterOffsetDeg
            self.beaconCentroidUncertaintyDeg = beaconCentroidUncertaintyDeg
        }
    }
    public let status: SolutionStatus
    /// Every class is a key, a class with no finding at 0.
    public let counts: [EvidenceClass: Int]
    public let marks: [Mark]
    /// What the sentence of a result that is not calibrated reads (mobile-8): the solver's counts, the pose estimate,
    /// whether a WCS is there, and the frame's time in the recording.
    public let detectedCandidateCount: Int?
    public let matchedInlierCount: Int?
    public let pose: Pose?
    public let hasWCS: Bool
    public let frameTimeSec: Double

    public init(status: SolutionStatus, counts: [EvidenceClass: Int], marks: [Mark], detectedCandidateCount: Int? = nil,
                matchedInlierCount: Int? = nil, pose: Pose? = nil, hasWCS: Bool = false, frameTimeSec: Double = 0) {
        self.status = status
        self.counts = counts
        self.marks = marks
        self.detectedCandidateCount = detectedCandidateCount
        self.matchedInlierCount = matchedInlierCount
        self.pose = pose
        self.hasWCS = hasWCS
        self.frameTimeSec = frameTimeSec
    }
}

/// Reads only the solution fields the phone shows. The laptop checks the whole contract before it serves a solution
/// (`services/ingest/router.py`, `JobStore.read_solution`); this reading refuses a reply whose shown fields break it,
/// and draws a catalog match only on a calibrated solution whose WCS passed held-out verification.
public enum SolutionReader {
    public static func read(_ value: JSONValue, sha256: String) -> Result<SolutionReadout, SolveReadRefusal> {
        func refuse(_ path: String, _ reason: String) -> Result<SolutionReadout, SolveReadRefusal> {
            .failure(SolveReadRefusal(path: path, reason: reason))
        }
        guard let object = value.objectValue else { return refuse("", "not an object") }
        guard let statusText = object["status"]?.stringValue, let status = SolutionStatus(rawValue: statusText) else {
            return refuse("status", "not a status of the contract")
        }
        guard PhotoHash.isContractHash(sha256), let named = object["sourceSha256"]?.stringValue, PhotoHash.isContractHash(named),
              named == sha256 else { return refuse("sourceSha256", "does not name the photo sent") }
        guard let findings = object["findings"]?.arrayValue else { return refuse("findings", "not a list") }
        let verified = status == .calibrated && object["quality"]?["passedIndependentVerification"]?.boolValue == true
            && object["wcs"]?.objectValue != nil
        var counts = Dictionary(uniqueKeysWithValues: EvidenceClass.allCases.map { ($0, 0) })
        var marks: [SolutionReadout.Mark] = []
        for (index, finding) in findings.enumerated() {
            let path = "findings.\(index)"
            guard let name = finding["evidenceClass"]?.stringValue, let evidence = EvidenceClass(rawValue: name) else {
                return refuse("\(path).evidenceClass", "not an evidence class")
            }
            counts[evidence, default: 0] += 1
            guard evidence == .catalogMatched else { continue }
            guard verified else { return refuse(path, "a catalog match needs a calibrated solution with a verified WCS") }
            guard let id = Wire.string(finding["id"], units: 1...Int.max), let title = Wire.string(finding["title"], units: 1...Int.max),
                  let x = finding["sourceDetection"]?["xPx"]?.numberValue, let y = finding["sourceDetection"]?["yPx"]?.numberValue,
                  x.isFinite, y.isFinite, x >= 0, y >= 0 else {
                return refuse(path, "a catalog match carries its name and the detection it was matched at")
            }
            let magnitude = finding["catalogRef"]?["apparentMagnitude"]?.numberValue
            if let magnitude, !magnitude.isFinite { return refuse("\(path).catalogRef.apparentMagnitude", "not finite") }
            marks.append(SolutionReadout.Mark(id: id, title: title, xPx: x, yPx: y, apparentMagnitude: magnitude))
        }
        var pose: SolutionReadout.Pose?
        if let given = object["poseEstimate"]?.objectValue {
            guard let method = given["method"]?.stringValue, let offset = given["maxCenterOffsetDeg"]?.numberValue, offset.isFinite, offset >= 0,
                  let centroid = given["beaconCentroidUncertaintyDeg"]?.numberValue, centroid.isFinite, centroid >= 0 else {
                return refuse("poseEstimate", "a pose estimate names its method and how far off it may be")
            }
            pose = SolutionReadout.Pose(method: method, maxCenterOffsetDeg: offset, beaconCentroidUncertaintyDeg: centroid)
        }
        let frameTime = object["frameTimeSec"]?.numberValue ?? 0
        guard frameTime.isFinite else { return refuse("frameTimeSec", "not finite") }
        return .success(SolutionReadout(status: status, counts: counts, marks: marks,
                                        detectedCandidateCount: Wire.whole(object["quality"]?["detectedCandidateCount"]),
                                        matchedInlierCount: Wire.whole(object["quality"]?["matchedInlierCount"]),
                                        pose: pose, hasWCS: object["wcs"]?.objectValue != nil, frameTimeSec: frameTime))
    }
}

/// How to show the stored pixels (`DisplayOrientationSchema`): mirror left to right if `mirrored`, then turn
/// clockwise a quarter at a time.
public struct DisplayOrientation: Equatable, Sendable {
    public static let upright = DisplayOrientation(quarterTurnsClockwise: 0, mirrored: false)
    public let quarterTurnsClockwise: Int
    public let mirrored: Bool
    public init(quarterTurnsClockwise: Int, mirrored: Bool) {
        self.quarterTurnsClockwise = quarterTurnsClockwise
        self.mirrored = mirrored
    }
}

/// The stored frame the laptop measured, so a detection lands on the photo where the web app puts it
/// (`orientStoredPoint` in `packages/contracts/src/media.ts`, `sourcePixelToViewport` in
/// `apps/web/src/components/sourcePhotoRect.ts`).
public struct StoredFrame: Equatable, Sendable {
    public let widthPx: Int
    public let heightPx: Int
    public let orientation: DisplayOrientation
    public init(widthPx: Int, heightPx: Int, orientation: DisplayOrientation) {
        self.widthPx = widthPx
        self.heightPx = heightPx
        self.orientation = orientation
    }

    /// The frame's size once oriented.
    public var shownSize: (widthPx: Int, heightPx: Int) {
        orientation.quarterTurnsClockwise % 2 == 1 ? (heightPx, widthPx) : (widthPx, heightPx)
    }

    /// Where a point of the stored frame is shown once oriented (image coordinates, origin top left).
    public func orient(xPx: Double, yPx: Double) -> (xPx: Double, yPx: Double) {
        var x = orientation.mirrored ? Double(widthPx) - xPx : xPx
        var y = yPx
        var width = Double(widthPx)
        var height = Double(heightPx)
        for _ in 0..<orientation.quarterTurnsClockwise {
            // A clockwise quarter turn sends (x, y) in a width by height frame to (height - y, x) in height by width
            (x, y) = (height - y, x)
            (width, height) = (height, width)
        }
        return (x, y)
    }

    /// Where a detection lands on a `width` by `height` surface that shows the whole oriented photo, as large as
    /// fits and centred: at its pixel's centre, half a pixel on, as the web app takes it.
    public func place(xPx: Double, yPx: Double, onWidth width: Double, height: Double) -> (x: Double, y: Double) {
        let shown = shownSize
        let shownW = Double(max(1, shown.widthPx))
        let shownH = Double(max(1, shown.heightPx))
        let scale = min(width / shownW, height / shownH)
        let rectW = shownW * scale
        let rectH = shownH * scale
        let point = orient(xPx: xPx + 0.5, yPx: yPx + 0.5)
        return ((width - rectW) / 2 + point.xPx / shownW * rectW, (height - rectH) / 2 + point.yPx / shownH * rectH)
    }
}

/// The recording's facts the result's sentence reads (`SkyAssetFacts` in `skyLayerWords.ts`): whether it is a clip,
/// its capture time as the receipt states it, and whether it holds a place. Never shown as they are.
public struct AssetFacts: Equatable, Sendable {
    public let isVideo: Bool
    public let capturedAtUTC: String?
    public let hasPlace: Bool
    public init(isVideo: Bool, capturedAtUTC: String?, hasPlace: Bool) {
        self.isVideo = isVideo
        self.capturedAtUTC = capturedAtUTC
        self.hasPlace = hasPlace
    }
}

/// The receipt's fields the result needs: the stored frame, the solve job's id once one exists, and the recording's facts.
public struct StoredReceipt: Equatable, Sendable {
    public let frame: StoredFrame
    public let solveJobId: String?
    public let facts: AssetFacts
}

public enum StoredReceiptReader {
    /// The receipt of the photo with this SHA-256 (`IngestReceiptSchema`), or nil when it names another photo or a
    /// field the result reads breaks the contract.
    public static func read(_ value: JSONValue, sha256: String) -> StoredReceipt? {
        guard let asset = value["sourceAsset"], asset["sha256"]?.stringValue == sha256,
              let width = Wire.whole(asset["width"]), width > 0, let height = Wire.whole(asset["height"]), height > 0 else { return nil }
        var orientation = DisplayOrientation.upright
        if let given = asset["displayOrientation"] {
            guard let turns = Wire.whole(given["quarterTurnsClockwise"]), (0...3).contains(turns),
                  let mirrored = given["mirrored"]?.boolValue else { return nil }
            orientation = DisplayOrientation(quarterTurnsClockwise: turns, mirrored: mirrored)
        }
        var jobId: String?
        if let given = value["solveJobId"] {
            guard let id = given.stringValue, Wire.isJobId(id) else { return nil }
            jobId = id
        }
        let captured = asset["capturedAtUTC"].flatMap { $0.stringValue }.flatMap { UTCInstant.isValid($0) ? $0 : nil }
        let facts = AssetFacts(isVideo: asset["mediaKind"]?.stringValue == "video", capturedAtUTC: captured, hasPlace: asset["observerLocation"]?.objectValue != nil)
        return StoredReceipt(frame: StoredFrame(widthPx: width, heightPx: height, orientation: orientation), solveJobId: jobId, facts: facts)
    }
}
