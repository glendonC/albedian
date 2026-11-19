import Foundation

/// The wait's one row (UIUX-iphone-solve-READY, 1): the step the laptop is working on, read from the job as the
/// laptop's approved working screen reads it (`apps/web/src/reveal/arrival.ts`, `stageRows`), with its own count.
public struct WaitRow: Equatable, Sendable {
    public enum Count: Equatable, Sendable {
        /// The step's own count, "done of total".
        case of(done: Int, total: Int)
        /// The step reports no total: the seconds passed since the phone asked for the solve, in the count's place.
        case secondsPassed
        /// The name alone: a step that has not reported yet, a count of one, or a count in bytes.
        case none

        /// The count as the working screen writes it ("2,080 of 4,160"), or the whole seconds passed ("12 s").
        public func words(secondsPassed: Double) -> String? {
            switch self {
            case .of(let done, let total): return "\(Self.grouped(done)) of \(Self.grouped(total))"
            case .secondsPassed: return secondsPassed.isFinite && secondsPassed >= 0 ? "\(Int(secondsPassed)) s" : nil
            case .none: return nil
            }
        }

        /// How much of the line is filled: done over total, only for a count.
        public var fraction: Double? {
            guard case .of(let done, let total) = self, total > 0 else { return nil }
            return min(max(Double(done) / Double(total), 0), 1)
        }

        /// Thousands with a comma, as `toLocaleString('en-US')` writes them, whatever the phone's own locale.
        static func grouped(_ n: Int) -> String {
            let digits = String(abs(n))
            var out = ""
            for (index, character) in digits.enumerated() {
                if index > 0, (digits.count - index) % 3 == 0 { out.append(",") }
                out.append(character)
            }
            return n < 0 ? "-" + out : out
        }
    }

    public let stage: SolveJob.Stage
    public let count: Count

    public init(stage: SolveJob.Stage, count: Count) {
        self.stage = stage
        self.count = count
    }

    /// The laptop's approved step names (`STAGE_WORDS` in `arrival.ts`).
    public static func word(_ stage: SolveJob.Stage) -> String {
        switch stage {
        case .store: "Saved"
        case .decode: "Opened"
        case .detect: "Measured"
        case .solve: "Matching"
        case .verify: "Checking"
        }
    }

    /// The step being worked on a running job: the last stage that reported while its count is not full, else the
    /// one after it; none for a job that is not running, or when every stage is full.
    public static func current(_ job: SolveJob) -> WaitRow? {
        guard job.state == .running else { return nil }
        let order = SolveJob.Stage.allCases
        let entries = Dictionary(job.stages.map { ($0.stage, $0) }, uniquingKeysWith: { _, last in last })
        let last = order.lastIndex { entries[$0] != nil } ?? -1
        let lastFull = last >= 0 && entries[order[last]].map { $0.total != nil && $0.done >= $0.total! } == true
        let at: Int
        if !lastFull {
            at = max(0, last)
        } else if last < order.count - 1 {
            at = last + 1
        } else {
            return nil
        }
        let stage = order[at]
        guard let entry = entries[stage] else { return WaitRow(stage: stage, count: .none) }
        guard let total = entry.total else { return WaitRow(stage: stage, count: .secondsPassed) }
        guard entry.unit != .bytes, total != 1 else { return WaitRow(stage: stage, count: .none) }
        return WaitRow(stage: stage, count: .of(done: entry.done, total: total))
    }
}

/// The sentence of a result that is not calibrated (UIUX-iphone-solve-READY, 2): the web's own line for the outcome
/// (`skyLayerWords.ts`, `skyLayerSentence` with a star search run, as every solve of a sent photo is), its count with a
/// thousands comma. For "predicted only" the approved picture shows the first sentence alone, without the web's second
/// ("You can open the sky predicted ...") [inference: the picture the owner approved].
public enum OutcomeSentence {
    /// The plate solver's own minimum (`SOLVER_MIN_DETECTIONS`: below 6 it stops before matching).
    static let solverMinDetections = 6
    /// The ephemeris's span, as the web takes it: from 1800-01-01T00:00:00Z up to but not including 2051-01-01T00:00:00Z.
    static let ephemerisFrom = "1800-01-01T00:00:00.000Z"
    static let ephemerisUntil = "2051-01-01T00:00:00.000Z"

    static let poseTaken: [String: String] = [
        "moon_camera_profile_level_head": "taking a bright spot to be the Moon, with the camera's field of view and a level head",
        "moon_horizon": "taking a bright spot to be the Moon, with the skyline taken to be level",
        "sun_horizon": "taking a bright spot to be the Sun, with the skyline taken to be level",
    ]

    /// The line, or nil for a calibrated result, which keeps its approved title and count.
    public static func sentence(_ readout: SolutionReadout, _ facts: AssetFacts) -> String? {
        let kind = facts.isVideo ? "clip" : "photo"
        switch readout.status {
        case .calibrated:
            return nil
        case .pending:
            return "The sky for your \(kind) has not been worked out yet."
        case .approximate:
            guard let pose = readout.pose, readout.hasWCS else {
                return readout.pose == nil
                    ? "Lined up roughly, not checked against stars. How far off it may be is not known, so the sky is not drawn over your \(kind)."
                    : "You can open the sky predicted for your \(kind)'s time and place."
            }
            let within = Int((pose.maxCenterOffsetDeg + pose.beaconCentroidUncertaintyDeg).rounded(.up))
            let taken = poseTaken[pose.method] ?? "taking a bright spot to be the Moon or the Sun"
            return "Lined up by \(taken), within about \(within) \(within == 1 ? "degree" : "degrees"); not checked against stars."
        case .predictedOnly:
            guard let detected = readout.detectedCandidateCount else { return "The points of light could not be matched to stars." }
            if detected < solverMinDetections { return "Too few points of light to match to stars." }
            return "Found \(WaitRow.Count.grouped(detected)) points of light, but could not match them to stars for sure."
        case .unresolved, .ambiguous:
            return noSkyReason(readout, facts, kind: kind)
        }
    }

    /// Why there is no sky, in the order the server checks it (`noSkyReason`).
    static func noSkyReason(_ readout: SolutionReadout, _ facts: AssetFacts, kind: String) -> String {
        let taken = kind == "clip" ? "recorded" : "taken"
        let hasTime = facts.capturedAtUTC.map { !$0.isEmpty } ?? false
        if !hasTime || !facts.hasPlace {
            let which = !hasTime && !facts.hasPlace ? "when or where" : !hasTime ? "when" : "where"
            return "Your \(kind) does not say \(which) it was \(taken), so its sky cannot be shown."
        }
        if let captured = facts.capturedAtUTC, let start = UTCInstant.date(captured) {
            let moment = start.addingTimeInterval(readout.frameTimeSec)
            if let from = UTCInstant.date(ephemerisFrom), let until = UTCInstant.date(ephemerisUntil), !(moment >= from && moment < until) {
                return "Your \(kind) was \(taken) outside the years 1800 to 2050, the only years its sky can be worked out for, so its sky cannot be shown."
            }
        }
        return "No sky could be worked out for your \(kind)."
    }

    /// The counts the result shows beside the sentence: observed, then predicted, each only when the result has one.
    public static func countsShown(_ readout: SolutionReadout) -> [(EvidenceClass, Int)] {
        [EvidenceClass.observed, .predicted].compactMap { evidence in
            let n = readout.counts[evidence] ?? 0
            return n > 0 ? (evidence, n) : nil
        }
    }
}
