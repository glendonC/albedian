import Foundation

/// The runner's job record (mobile-9), read exactly as `UploadJobSchema` (`packages/contracts/src/jobs.ts`) defines it,
/// for the one kind the phone follows, `solve`: every key, value and rule of the contract, the state table included,
/// judged here a third time beside the TypeScript and Python readings of `tests/fixtures/job_contract.json`. A record of
/// another kind is refused: the phone starts and follows only solves.
public enum RunnerJob {
    static let keys: Set<String> = ["id", "sourceAssetSha256", "kind", "state", "createdAtUTC", "startedAtUTC", "finishedAtUTC",
                                    "stages", "solutionId", "error", "producer", "request", "graphId", "attemptId", "variantId",
                                    "inputSpan", "cancelRequestedAtUTC", "queueAhead"]
    static let stageKeys: Set<String> = ["stage", "unit", "done", "total", "pass"]
    /// Every stage name the contract knows (`UPLOAD_JOB_STAGES`); a solve's are `SolveJob.Stage`.
    static let allStages: Set<String> = ["store", "decode", "detect", "solve", "verify", "encode", "write", "measure", "restore"]
    /// The units each solve stage reports in (`JOB_STAGE_UNITS.solve`).
    static let solveUnits: [SolveJob.Stage: Set<SolveJob.Unit>] = [
        .store: [.bytes], .decode: [.frames], .detect: [.frames], .solve: [.candidates, .steps], .verify: [.steps],
    ]
    static let passMax = 16
    static let spanMaxSec = 86_400.0
    static let interrupted = "The server stopped before this job finished."
    /// `SOLVE_FAILURE_SENTENCES`, the last of which is the interrupted one.
    static let failureSentences: Set<String> = [
        "The decoder could not read the file.",
        "ffmpeg is not installed to decode the upload.",
        "The decoder stopped at its time limit.",
        "This ffmpeg could not report the HEIC photo's tile grid (ffprobe 7.1 or later is needed), so the photo was not decoded.",
        "The decoder could not read the HEIC photo's tiles.",
        "The file's tile grid does not cover the stored image.",
        "The decoder did not write one frame for every tile.",
        "The solve stopped on an internal error.",
        "The solve stopped at its time limit.",
        "The job's result could not be stored.",
        "The original changed while the job ran.",
        interrupted,
    ]

    /// One rule of `JOB_STATE_TABLE`: R required, F forbidden, E either, S only once started, A present exactly when started.
    enum Rule: Equatable { case r, f, e, s, a
        func allows(present: Bool, started: Bool) -> Bool {
            switch self {
            case .r: present
            case .f: !present
            case .e: true
            case .s: started || !present
            case .a: present == started
            }
        }
    }
    /// The fields the table judges, in its order (`JOB_STATE_FIELDS`).
    static let stateFields = ["startedAtUTC", "finishedAtUTC", "error", "solutionId", "attemptId", "variantId", "inputSpan",
                              "cancelRequestedAtUTC", "queueAhead", "producer", "request", "graphId", "workStages"]
    /// `JOB_STATE_TABLE.solve`, row by row as the contract writes it; `rejected` has no row for a solve.
    static let solveTable: [SolveJob.State: [Rule]] = [
        .queued:      [.f, .f, .f, .f, .f, .f, .f, .f, .e, .a, .f, .f, .f],
        .running:     [.r, .f, .f, .f, .f, .f, .e, .s, .f, .a, .f, .f, .e],
        .succeeded:   [.r, .r, .f, .r, .f, .f, .e, .s, .f, .a, .f, .f, .e],
        .failed:      [.r, .r, .r, .f, .f, .f, .e, .s, .f, .a, .f, .f, .e],
        .canceled:    [.e, .r, .f, .f, .f, .f, .s, .a, .f, .a, .f, .f, .s],
        .interrupted: [.r, .r, .r, .f, .f, .f, .e, .s, .f, .a, .f, .f, .e],
    ]

    /// `^[a-z][a-z0-9_.]{0,79}$` (`PRODUCER_NAME_PATTERN`).
    static func isProducerName(_ text: String) -> Bool {
        let bytes = Array(text.utf8)
        guard (1...80).contains(bytes.count), (0x61...0x7A).contains(bytes[0]) else { return false }
        return bytes.dropFirst().allSatisfy { (0x61...0x7A).contains($0) || (0x30...0x39).contains($0) || $0 == 0x5F || $0 == 0x2E }
    }

    /// `^sol_[0-9a-f]{16}$` (`MEDIA_ID_PATTERNS.solution`).
    static func isSolutionId(_ text: String) -> Bool {
        text.hasPrefix("sol_") && text.utf8.count == 20 && Wire.lowerHex(text.dropFirst(4))
    }
}

extension SolveJobReader {
    /// The runner's record of a solve, or the first rule of `UploadJobSchema` it breaks.
    static func readRunnerRecord(_ object: JSONObject) -> Result<SolveJob, SolveReadRefusal> {
        func refuse(_ path: String, _ reason: String) -> Result<SolveJob, SolveReadRefusal> {
            .failure(SolveReadRefusal(path: path, reason: reason))
        }
        if let extra = Wire.onlyKeys(object, RunnerJob.keys) { return refuse(extra, "not a key of the contract") }
        guard let id = object["id"]?.stringValue, Wire.isJobId(id) else { return refuse("id", "not a job id") }
        guard let sha = object["sourceAssetSha256"]?.stringValue, Wire.isSha256(sha) else { return refuse("sourceAssetSha256", "not a SHA-256") }
        guard let kind = object["kind"]?.stringValue, ["solve", "display_copy", "restoration"].contains(kind) else {
            return refuse("kind", "not a kind of the contract")
        }
        guard kind == "solve" else { return refuse("kind", "not a solve job") }
        guard let stateText = object["state"]?.stringValue,
              ["queued", "running", "succeeded", "rejected", "failed", "canceled", "interrupted"].contains(stateText) else {
            return refuse("state", "not a state of the contract")
        }
        guard let created = Wire.instant(object["createdAtUTC"]) else { return refuse("createdAtUTC", "not an instant") }
        var instants: [String: String] = [:]
        for key in ["startedAtUTC", "finishedAtUTC", "cancelRequestedAtUTC"] where object[key] != nil {
            guard let instant = Wire.instant(object[key]) else { return refuse(key, "not an instant") }
            instants[key] = instant
        }
        var solutionId: String?
        if let value = object["solutionId"] {
            guard let text = value.stringValue, RunnerJob.isSolutionId(text) else { return refuse("solutionId", "not a solution id") }
            solutionId = text
        }
        var error: String?
        if let value = object["error"] {
            guard let text = value.stringValue else { return refuse("error", "not a text") }
            error = text
        }
        var producer: SolveJob.Producer?
        if let value = object["producer"] {
            guard let fields = value.objectValue else { return refuse("producer", "not an object") }
            if let extra = Wire.onlyKeys(fields, ["name", "revision"]) { return refuse("producer.\(extra)", "not a key of the contract") }
            guard let name = fields["name"]?.stringValue, RunnerJob.isProducerName(name) else { return refuse("producer.name", "not a producer name") }
            guard let revision = Wire.whole(fields["revision"]), revision >= 1 else { return refuse("producer.revision", "not a revision") }
            producer = SolveJob.Producer(name: name, revision: revision)
        }
        var inputSpan: SolveJob.InputSpan?
        if let value = object["inputSpan"] {
            guard let fields = value.objectValue else { return refuse("inputSpan", "not an object") }
            if let extra = Wire.onlyKeys(fields, ["firstFrameIndex", "frameCount", "startSec", "endSec"]) {
                return refuse("inputSpan.\(extra)", "not a key of the contract")
            }
            guard let first = Wire.whole(fields["firstFrameIndex"]), first >= 0 else { return refuse("inputSpan.firstFrameIndex", "not a count") }
            guard let count = Wire.whole(fields["frameCount"]), count >= 1 else { return refuse("inputSpan.frameCount", "not a positive count") }
            guard let start = fields["startSec"]?.numberValue, start.isFinite, (0...RunnerJob.spanMaxSec).contains(start) else {
                return refuse("inputSpan.startSec", "not a time in the span's bounds")
            }
            guard let end = fields["endSec"]?.numberValue, end.isFinite, (0...RunnerJob.spanMaxSec).contains(end) else {
                return refuse("inputSpan.endSec", "not a time in the span's bounds")
            }
            guard end >= start else { return refuse("inputSpan", "a frame span ends no earlier than it starts") }
            inputSpan = SolveJob.InputSpan(firstFrameIndex: first, frameCount: count, startSec: start, endSec: end)
        }
        var queueAhead: Int?
        if let value = object["queueAhead"] {
            guard let ahead = Wire.whole(value), ahead >= 0 else { return refuse("queueAhead", "not a count") }
            queueAhead = ahead
        }
        // A media job's fields (request, graphId, attemptId, variantId) are refused below by the state table, which forbids
        // each in every state of a solve, whatever its value; and more stages than the contract has break the solve's order

        guard let items = object["stages"]?.arrayValue else { return refuse("stages", "not a list") }
        var stages: [SolveJob.StageProgress] = []
        for (index, item) in items.enumerated() {
            let path = "stages.\(index)"
            guard let stage = item.objectValue else { return refuse(path, "not an object") }
            if let extra = Wire.onlyKeys(stage, RunnerJob.stageKeys) { return refuse("\(path).\(extra)", "not a key of the contract") }
            guard let name = stage["stage"]?.stringValue, RunnerJob.allStages.contains(name) else { return refuse("\(path).stage", "not a stage") }
            guard let unitName = stage["unit"]?.stringValue, let unit = SolveJob.Unit(rawValue: unitName) else { return refuse("\(path).unit", "not a unit") }
            guard let done = Wire.whole(stage["done"]), done >= 0 else { return refuse("\(path).done", "not a count") }
            guard let totalValue = stage["total"] else { return refuse("\(path).total", "null when unknown, never absent") }
            var total: Int?
            if totalValue != .null {
                guard let known = Wire.whole(totalValue), known > 0 else { return refuse("\(path).total", "not a positive count") }
                guard done <= known else { return refuse("\(path).done", "done cannot exceed total") }
                total = known
            }
            var pass: SolveJob.Pass?
            if let value = stage["pass"] {
                guard let fields = value.objectValue else { return refuse("\(path).pass", "not an object") }
                if let extra = Wire.onlyKeys(fields, ["number", "label"]) { return refuse("\(path).pass.\(extra)", "not a key of the contract") }
                guard let number = Wire.whole(fields["number"]), (1...RunnerJob.passMax).contains(number) else {
                    return refuse("\(path).pass.number", "not a pass number")
                }
                guard let labelText = fields["label"]?.stringValue, let label = SolveJob.Pass.Label(rawValue: labelText) else {
                    return refuse("\(path).pass.label", "not a pass label")
                }
                pass = SolveJob.Pass(number: number, label: label)
            }
            guard let solveStage = SolveJob.Stage(rawValue: name) else { return refuse("\(path).stage", "a stage belongs to the job's kind") }
            if let last = stages.last, SolveJob.Stage.allCases.firstIndex(of: solveStage)! <= SolveJob.Stage.allCases.firstIndex(of: last.stage)! {
                return refuse("stages", "stages appear once each, in the kind's order")
            }
            guard RunnerJob.solveUnits[solveStage]!.contains(unit) else { return refuse("\(path).unit", "a stage reports in one of its units") }
            stages.append(SolveJob.StageProgress(stage: solveStage, unit: unit, done: done, total: total, pass: pass))
        }

        // The state table: which fields a solve in this state holds
        guard let state = SolveJob.State(rawValue: stateText), let row = RunnerJob.solveTable[state] else {
            return refuse("state", "the fields do not fit the job's kind and state")
        }
        let started = instants["startedAtUTC"] != nil
        var present: [String: Bool] = [:]
        for field in RunnerJob.stateFields { present[field] = object[field] != nil }
        present["workStages"] = stages.contains { $0.stage != .store }
        for (field, rule) in zip(RunnerJob.stateFields, row) where !rule.allows(present: present[field]!, started: started) {
            return refuse(field, "the fields do not fit the job's kind and state")
        }
        let startedAt = instants["startedAtUTC"]
        let finishedAt = instants["finishedAtUTC"]
        if let startedAt, startedAt < created { return refuse("startedAtUTC", "times run created, started, finished") }
        if let finishedAt, finishedAt < (startedAt ?? created) { return refuse("finishedAtUTC", "times run created, started, finished") }
        if let cancel = instants["cancelRequestedAtUTC"], let startedAt {
            if cancel < startedAt || (finishedAt.map { cancel > $0 } ?? false) {
                return refuse("cancelRequestedAtUTC", "a cancel time lies within the job's run")
            }
        }
        if let error {
            switch state {
            case .interrupted where error != RunnerJob.interrupted:
                return refuse("error", "an interrupted job says the server stopped")
            case .failed where !RunnerJob.failureSentences.contains(error) || error == RunnerJob.interrupted:
                return refuse("error", "a failure is one of the fixed sentences its kind and state allow")
            default: break
            }
        }
        return .success(SolveJob(id: id, sourceAssetSha256: sha, state: state, createdAtUTC: created, startedAtUTC: startedAt,
                                 finishedAtUTC: finishedAt, stages: stages, solutionId: solutionId, error: error, kind: kind,
                                 producer: producer, queueAhead: queueAhead, inputSpan: inputSpan,
                                 cancelRequestedAtUTC: instants["cancelRequestedAtUTC"]))
    }
}
