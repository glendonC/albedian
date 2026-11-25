import Foundation
import Observation

/// Why a solve did not reach its result. Each names what happened, never a value from the photo or the record.
public enum SolveFailure: Error, Equatable, Sendable {
    /// The hash is not a contract SHA-256, so no request was sent (`CaptureUploader.permits`).
    case notPermitted
    /// The connection failed, with the POSIX error's code when it was one.
    case networkError(posixCode: Int32?)
    /// No whole reply came within the time allowed.
    case timedOut
    /// The connection ended before a whole reply came.
    case connectionClosed
    /// The connected peer was not the paired address; nothing was sent.
    case notThePairedPeer
    /// The laptop answered with a status other than success, and its `detail` when it gave one (404: the laptop holds
    /// no file with that hash).
    case refused(status: Int, detail: String?)
    /// The laptop's address answered with a redirect, which nothing follows.
    case redirected(status: Int)
    /// The reply was not a job, receipt or solution the readers take, with the reader's reason when one read it.
    case unreadableReply(SolveReadRefusal?)
    /// The job the solve answered with is another photo's.
    case jobForAnotherPhoto
    /// The job ended without a solution (failed or canceled), with the job and its own sentence.
    case jobDidNotSucceed(SolveJob)
}

/// Where a solve stands. Each state is a reply the laptop gave; elapsed time, when shown, is the screen's own clock
/// from `starting` (REQ-IOS-4.2).
public enum SolveState: Equatable, Sendable {
    /// The solve was asked for; no job has been read yet.
    case starting
    /// The job as the laptop last wrote it, read beside the held solve.
    case running(SolveJob)
    /// The job succeeded and its solution was read: the status and counts as the server reports them, and the stored
    /// frame the marks are placed in, and the recording's facts the sentence of a result that is not calibrated reads.
    case solved(job: SolveJob, readout: SolutionReadout, frame: StoredFrame, facts: AssetFacts)
    case failed(SolveFailure)
}

extension SolveState {
    /// Whether the laptop took the ask for this solve: it answered with a job for the photo (running, solved, or ended
    /// without a solution). Before that, or after a failure before any job, the ask was not taken (kept-solve-retry-one).
    public var laptopTookTheAsk: Bool {
        switch self {
        case .running, .solved, .failed(.jobDidNotSucceed): true
        case .starting, .failed: false
        }
    }
}

/// Starts the laptop's solve of a stored photo and reads its result, each request on a connection of its own to the
/// paired endpoint, through the one gate (`CaptureUploader.permits`): `POST /api/ingest/{sha256}/solve?wait=false`,
/// which answers at once with the runner's job, then `GET /api/ingest/jobs/{id}` until the job ends, so the stages
/// show as the job writes them; once it succeeds, the receipt `GET /api/ingest/{sha256}` (the stored frame and facts)
/// and `GET /api/ingest/{sha256}/solution`.
public final class SolveClient: Sendable {
    /// How long the phone follows a job from its start [proposed]: the runner's whole-job bound for a solve, 420 s
    /// (the API note, platform-5b), with room for a wait in the queue.
    public static let defaultSolveTimeoutSec: Double = 600
    /// The time each read beside it may take [proposed].
    public static let defaultReadTimeoutSec: Double = 10
    /// How often the job is read while it runs: once a second, the pace the runner's API note gives the web.
    public static let defaultPollIntervalSec: Double = 1
    /// The bound on the solution's body, the one reply read past the receipts' 64 kB: 64 MiB [proposed]. A photo with a
    /// time and a place solves to about 1.3 MB (the audit of mobile-4, a synthetic 960 by 540 starfield: 1,300,568 bytes)
    /// and a 12 MP night photo to about 35 MB (the CC0 photo rp-b-06, about 32 thousand findings, read here in under a
    /// second on the Mac); past the bound the read is refused rather than held without end.
    public static let solutionMaxBytes = 64 * 1_048_576

    public let address: LaptopAddress
    private let solveTimeoutSec: Double
    private let readTimeoutSec: Double
    private let pollIntervalSec: Double
    private let transport: any LaptopTransport

    public init(address: LaptopAddress, solveTimeoutSec: Double = SolveClient.defaultSolveTimeoutSec,
                readTimeoutSec: Double = SolveClient.defaultReadTimeoutSec, pollIntervalSec: Double = SolveClient.defaultPollIntervalSec,
                transport: any LaptopTransport = NetworkLaptopTransport()) {
        self.address = address
        self.solveTimeoutSec = CaptureUploader.isValidTimeout(solveTimeoutSec) ? solveTimeoutSec : 0
        self.readTimeoutSec = CaptureUploader.isValidTimeout(readTimeoutSec) ? readTimeoutSec : 0
        self.pollIntervalSec = pollIntervalSec > 0 && pollIntervalSec.isFinite ? pollIntervalSec : Self.defaultPollIntervalSec
        self.transport = transport
    }

    /// Starts the solve, reports every new job read and the end, in order, and returns the last state.
    ///
    /// The start (`POST /api/ingest/{sha256}/solve?wait=false`) answers at once with the job: a new one (202, queued or
    /// running), the active one joined, or the current succeeded one (200). The phone then follows that job by its own
    /// id, `GET /api/ingest/jobs/{id}`, until it ends (the runner's API note, `.lead/kiro/answers/PLATFORM-runner-api.md`),
    /// and reads the receipt and the solution once it succeeded. Leaving early never cancels the job on the laptop.
    public func solve(sha256: String, onState: @escaping @Sendable (SolveState) -> Void) async -> SolveState {
        guard PhotoHash.isContractHash(sha256) else { return Self.finish(.failed(.notPermitted), onState) }
        onState(.starting)
        let began = ContinuousClock.now
        var job: SolveJob
        switch await exchange("POST", Self.startPath(sha256), timeoutSec: readTimeoutSec) {
        case .failure(let failure): return Self.finish(.failed(failure), onState)
        case .success(let reply):
            switch Self.read(reply.body, SolveJobReader.read) {
            case .failure(let failure): return Self.finish(.failed(failure), onState)
            case .success(let read): job = read
            }
        }
        guard job.sourceAssetSha256 == sha256 else { return Self.finish(.failed(.jobForAnotherPhoto), onState) }
        let reported = LastReported()
        var failedReads = 0
        while !job.hasEnded {
            if reported.isNew(job) { onState(.running(job)) }
            guard (ContinuousClock.now - began) < .seconds(solveTimeoutSec) else { return Self.finish(.failed(.timedOut), onState) }
            try? await Task.sleep(nanoseconds: UInt64(pollIntervalSec * 1_000_000_000))
            if Task.isCancelled { return Self.finish(.failed(.timedOut), onState) }
            switch await exchange("GET", "/api/ingest/jobs/\(job.id)", timeoutSec: readTimeoutSec) {
            case .failure(let failure):
                // A read the hotspot drops is tried again; a laptop gone for this many reads in a row ends the wait
                failedReads += 1
                if failedReads >= Self.followFailuresAllowed { return Self.finish(.failed(failure), onState) }
            case .success(let reply):
                switch Self.read(reply.body, SolveJobReader.read) {
                case .failure(let failure): return Self.finish(.failed(failure), onState)
                case .success(let read):
                    guard read.id == job.id, read.sourceAssetSha256 == sha256 else { return Self.finish(.failed(.jobForAnotherPhoto), onState) }
                    failedReads = 0
                    job = read
                }
            }
        }
        return Self.finish(await conclude(sha256, job), onState)
    }

    /// Asks the laptop to solve a photo it holds and gives the job it started, without following it: the request `solve`
    /// makes first, answered at once (kept-send-solves-one, the counted send of kept photos).
    public func start(sha256: String) async -> Result<SolveJob, SolveFailure> {
        guard PhotoHash.isContractHash(sha256) else { return .failure(.notPermitted) }
        switch await exchange("POST", Self.startPath(sha256), timeoutSec: readTimeoutSec) {
        case .failure(let failure): return .failure(failure)
        case .success(let reply):
            return Self.read(reply.body, SolveJobReader.read).flatMap { job in
                job.sourceAssetSha256 == sha256 ? .success(job) : .failure(.jobForAnotherPhoto)
            }
        }
    }

    /// The start's path: the answer at once, never the held form platform-7 removes.
    static func startPath(_ sha256: String) -> String { "/api/ingest/\(sha256)/solve?wait=false" }

    /// [proposed] Reads of the job that may fail in a row before the wait ends: at one a second, about ten seconds of a
    /// laptop that does not answer.
    static let followFailuresAllowed = 10
    /// [proposed] Attempts allowed for each post-solve read (the receipt and the solution) before giving up on a
    /// transient hotspot or server drop.
    static let concludeAttempts = 3

    private static func isTransient(_ failure: SolveFailure) -> Bool {
        switch failure {
        case .networkError, .timedOut, .connectionClosed: return true
        case .refused(let status, _): return (500...599).contains(status)
        default: return false
        }
    }

    private func readAfterSolve(_ path: String, bodyMaxBytes: Int = HTTPReplyLimits.bodyMaxBytes) async -> Result<HTTPReply, SolveFailure> {
        var attempt = 0
        while true {
            attempt += 1
            let outcome = await exchange("GET", path, timeoutSec: readTimeoutSec, bodyMaxBytes: bodyMaxBytes)
            guard case .failure(let failure) = outcome, Self.isTransient(failure), attempt < Self.concludeAttempts else {
                return outcome
            }
            try? await Task.sleep(nanoseconds: UInt64(pollIntervalSec * 1_000_000_000))
            if Task.isCancelled { return .failure(.timedOut) }
        }
    }

    private func conclude(_ sha256: String, _ job: SolveJob) async -> SolveState {
        guard job.state == .succeeded else { return .failed(.jobDidNotSucceed(job)) }
        let stored: StoredReceipt
        switch await readAfterSolve("/api/ingest/\(sha256)") {
        case .failure(let failure): return .failed(failure)
        case .success(let reply):
            guard let value = try? JSONText.read(reply.body), let receipt = StoredReceiptReader.read(value, sha256: sha256) else {
                return .failed(.unreadableReply(nil))
            }
            stored = receipt
        }
        switch await readAfterSolve("/api/ingest/\(sha256)/solution", bodyMaxBytes: Self.solutionMaxBytes) {
        case .failure(let failure): return .failed(failure)
        case .success(let reply):
            switch Self.read(reply.body, { SolutionReader.read($0, sha256: sha256) }) {
            case .failure(let failure): return .failed(failure)
            case .success(let readout): return .solved(job: job, readout: readout, frame: stored.frame, facts: stored.facts)
            }
        }
    }

    /// The lock-on's early sky (lockon-m5): once the running job's record names when it published the sky predicted
    /// from an early pose, that sky (`GET /api/ingest/jobs/{id}/predicted-sky`) and the stored frame its places are in
    /// (the receipt); nil when either is not served (the route answers 404 before and after the run) or not in its
    /// contract's form. Every place in it is predicted.
    public func earlySky(jobId: String, sha256: String) async -> (sky: PredictedEarlySky, frame: StoredFrame)? {
        guard case .success(let receipt) = await exchange("GET", "/api/ingest/\(sha256)", timeoutSec: readTimeoutSec),
              let value = try? JSONText.read(receipt.body), let stored = StoredReceiptReader.read(value, sha256: sha256),
              case .success(let reply) = await exchange("GET", "/api/ingest/jobs/\(jobId)/predicted-sky", timeoutSec: readTimeoutSec,
                                                        bodyMaxBytes: Self.solutionMaxBytes),
              let body = try? JSONText.read(reply.body), let sky = PredictedEarlySky.read(body, sha256: sha256) else { return nil }
        return (sky, stored.frame)
    }

    private static func read<T>(_ body: Data, _ reader: (JSONValue) -> Result<T, SolveReadRefusal>) -> Result<T, SolveFailure> {
        guard let value = try? JSONText.read(body) else { return .failure(.unreadableReply(nil)) }
        return reader(value).mapError { .unreadableReply($0) }
    }

    /// One request with no body through the gate, and its final reply: a 2xx, or the failure that names what came.
    private func exchange(_ method: String, _ path: String, timeoutSec: Double,
                          bodyMaxBytes: Int = HTTPReplyLimits.bodyMaxBytes) async -> Result<HTTPReply, SolveFailure> {
        guard CaptureUploader.isValidTimeout(timeoutSec) else { return .failure(.timedOut) }
        guard let approved = CaptureUploader.permits(method: method, path: path, to: address) else { return .failure(.notPermitted) }
        let request = HTTPMessage.request(method: approved.method, path: approved.path, endpoint: approved.endpoint, contentType: nil, body: Data())
        switch await transport.exchange(request, to: approved.endpoint, timeoutSec: timeoutSec, bodyMaxBytes: bodyMaxBytes, onSent: { _ in }) {
        case .failure(.network(let code)): return .failure(.networkError(posixCode: code))
        case .failure(.timedOut): return .failure(.timedOut)
        case .failure(.closed): return .failure(.connectionClosed)
        case .failure(.notThePairedPeer): return .failure(.notThePairedPeer)
        case .failure(.unreadable): return .failure(.unreadableReply(nil))
        case .success(let reply):
            if (300...399).contains(reply.status) { return .failure(.redirected(status: reply.status)) }
            guard (200...299).contains(reply.status) else { return .failure(.refused(status: reply.status, detail: CaptureUploader.detail(reply.body))) }
            return .success(reply)
        }
    }

    private static func finish(_ state: SolveState, _ onState: @Sendable (SolveState) -> Void) -> SolveState {
        onState(state)
        return state
    }

    /// The last job reported, so the same job is not reported twice in a row.
    private final class LastReported: @unchecked Sendable {
        private let lock = NSLock()
        private var job: SolveJob?
        func isNew(_ next: SolveJob) -> Bool { lock.withLock { defer { job = next }; return job != next } }
    }
}

/// Tracks one solve on the main actor, so starting another or resetting cancels the one before and ignores its late
/// states. `startedAt` is the phone's own clock when the solve was asked for, for a stage whose total the laptop does
/// not know, where only elapsed time may be shown (REQ-IOS-4.2).
@MainActor
@Observable
public final class SolveCoordinator {
    public private(set) var state: SolveState?
    public private(set) var startedAt: ContinuousClock.Instant?
    private var generation: UInt64 = 0
    private var task: Task<Void, Never>?

    public init() {}

    public var isSolving: Bool {
        switch state {
        case .starting?, .running?: true
        default: false
        }
    }

    public func reset() {
        generation &+= 1
        task?.cancel()
        task = nil
        state = nil
        startedAt = nil
    }

    /// - Parameter then: called on the main actor with the solve's last state, unless another start or a reset came first
    ///   (first, so a trailing closure is still `then`).
    /// - Parameter each: called on the main actor with every state as it is shown (the lock-on watches the running job).
    public func start(sha256: String, client: SolveClient, then: (@MainActor (SolveState) -> Void)? = nil,
                      each: (@MainActor (SolveState) -> Void)? = nil) {
        generation &+= 1
        task?.cancel()
        let runGeneration = generation
        state = .starting
        startedAt = ContinuousClock.now
        task = Task { @MainActor [weak self] in
            let last = await client.solve(sha256: sha256) { [weak self] next in
                Task { @MainActor [weak self] in
                    guard let self, self.generation == runGeneration else { return }
                    self.state = next
                    each?(next)
                }
            }
            guard let self, self.generation == runGeneration else { return }
            then?(last)
        }
    }
}
