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

/// Starts the laptop's solve of a stored photo and reads its result, each request on a connection of its own to the
/// paired endpoint, through the one gate (`CaptureUploader.permits`). `POST /api/ingest/{sha256}/solve` is held by the
/// laptop until the solve ends (`services/ingest/router.py`); beside it the client reads the receipt, `GET
/// /api/ingest/{sha256}`, for the job's id (`solveJobId`), then `GET /api/ingest/jobs/{id}`, so the stages show as the
/// job writes them (`services/ingest/jobs.py`, `run_solve_job`). Once the job succeeds, `GET .../solution`.
public final class SolveClient: Sendable {
    /// The time the held solve may take: the laptop's own limit of 180 s (`SOLVE_TIME_LIMIT_SEC`) with room for
    /// decoding a full-size photo and the answer [proposed].
    public static let defaultSolveTimeoutSec: Double = 600
    /// The time each read beside it may take [proposed].
    public static let defaultReadTimeoutSec: Double = 10
    /// How often the job is read while the solve is held [proposed: twice a second, so a stage's count moves at a
    /// pace a person can follow without loading the laptop].
    public static let defaultPollIntervalSec: Double = 0.5
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

    /// Asks for the solve, reports every new job read and the end, in order, and returns the last state.
    public func solve(sha256: String, onState: @escaping @Sendable (SolveState) -> Void) async -> SolveState {
        guard PhotoHash.isContractHash(sha256) else { return Self.finish(.failed(.notPermitted), onState) }
        onState(.starting)
        let last = LastReported()
        let poller = Task { [self] in
            while !Task.isCancelled {
                if let job = await readJob(sha256), job.sourceAssetSha256 == sha256, !Task.isCancelled, last.isNew(job) {
                    onState(.running(job))
                }
                try? await Task.sleep(nanoseconds: UInt64(pollIntervalSec * 1_000_000_000))
            }
        }
        let posted = await exchange("POST", "/api/ingest/\(sha256)/solve", timeoutSec: solveTimeoutSec)
        poller.cancel()
        await poller.value
        let end = await conclude(sha256, posted)
        return Self.finish(end, onState)
    }

    private func conclude(_ sha256: String, _ posted: Result<HTTPReply, SolveFailure>) async -> SolveState {
        let job: SolveJob
        switch posted {
        case .failure(let failure): return .failed(failure)
        case .success(let reply):
            switch Self.read(reply.body, SolveJobReader.read) {
            case .failure(let failure): return .failed(failure)
            case .success(let read): job = read
            }
        }
        guard job.sourceAssetSha256 == sha256 else { return .failed(.jobForAnotherPhoto) }
        guard job.state == .succeeded else { return .failed(.jobDidNotSucceed(job)) }
        let stored: StoredReceipt
        switch await exchange("GET", "/api/ingest/\(sha256)", timeoutSec: readTimeoutSec) {
        case .failure(let failure): return .failed(failure)
        case .success(let reply):
            guard let value = try? JSONText.read(reply.body), let receipt = StoredReceiptReader.read(value, sha256: sha256) else {
                return .failed(.unreadableReply(nil))
            }
            stored = receipt
        }
        switch await exchange("GET", "/api/ingest/\(sha256)/solution", timeoutSec: readTimeoutSec, bodyMaxBytes: Self.solutionMaxBytes) {
        case .failure(let failure): return .failed(failure)
        case .success(let reply):
            switch Self.read(reply.body, { SolutionReader.read($0, sha256: sha256) }) {
            case .failure(let failure): return .failed(failure)
            case .success(let readout): return .solved(job: job, readout: readout, frame: stored.frame, facts: stored.facts)
            }
        }
    }

    /// The job as the laptop wrote it last, or nil while there is none yet or the read failed (the held solve's answer
    /// is the one that counts).
    private func readJob(_ sha256: String) async -> SolveJob? {
        guard case .success(let reply) = await exchange("GET", "/api/ingest/\(sha256)", timeoutSec: readTimeoutSec),
              let value = try? JSONText.read(reply.body), let id = StoredReceiptReader.read(value, sha256: sha256)?.solveJobId,
              case .success(let jobReply) = await exchange("GET", "/api/ingest/jobs/\(id)", timeoutSec: readTimeoutSec),
              let jobValue = try? JSONText.read(jobReply.body), case .success(let job) = SolveJobReader.read(jobValue) else { return nil }
        return job
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

    /// - Parameter then: called on the main actor with the solve's last state, unless another start or a reset came first.
    public func start(sha256: String, client: SolveClient, then: (@MainActor (SolveState) -> Void)? = nil) {
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
                }
            }
            guard let self, self.generation == runGeneration else { return }
            then?(last)
        }
    }
}
