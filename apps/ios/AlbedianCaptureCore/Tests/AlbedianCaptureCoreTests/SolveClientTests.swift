import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The solve and its result against a laptop on 127.0.0.1 that each test starts and stops (REQ-IOS-4.2 for the
/// solve, REQ-IOS-5.1), as the job runner serves it (mobile-9): `POST /api/ingest/{sha256}/solve?wait=false` answers at
/// once with the job, which the client then follows by its own id, `GET /api/ingest/jobs/{id}`, to its end. Every state
/// it reports is a job record the laptop wrote (`UploadJobSchema`): no step, count or percentage of its own.
final class SolveClientTests: XCTestCase {
    final class Log<T: Sendable>: @unchecked Sendable {
        private let lock = NSLock()
        private var items: [T] = []
        func append(_ item: T) { lock.withLock { items.append(item) } }
        var all: [T] { lock.withLock { items } }
    }

    final class Counter: @unchecked Sendable {
        private let lock = NSLock()
        private var value = 0
        func next() -> Int { lock.withLock { value += 1; return value } }
    }

    private func calibrated() throws -> JSONValue {
        try XCTUnwrap(try ContractFixture.load("observation_solution_contract")["valid"]?["calibrated"])
    }

    static func jobId(_ sha: String) -> String { "job_" + sha.prefix(16) + "0123456789abcdef" }
    static let store = "{\"stage\":\"store\",\"unit\":\"bytes\",\"done\":10,\"total\":10}"
    static let producer = ",\"producer\":{\"name\":\"albedian.solve_chain\",\"revision\":4}"

    /// A runner's record of a solve in `state`, with the fields the job contract's state table asks for.
    static func job(_ sha: String, _ state: String, stages: String = store, id: String? = nil, extra: String = "") -> String {
        let fields: String
        switch state {
        case "queued": fields = ""
        case "running": fields = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\"" + producer
        case "failed": fields = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:02Z\",\"error\":\"The decoder could not read the file.\"" + producer
        case "interrupted": fields = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:02Z\",\"error\":\"The server stopped before this job finished.\"" + producer
        default: fields = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:41Z\",\"solutionId\":\"sol_\(sha.prefix(16))\"" + producer
        }
        return "{\"id\":\"\(id ?? jobId(sha))\",\"sourceAssetSha256\":\"\(sha)\",\"kind\":\"solve\",\"state\":\"\(state)\",\"createdAtUTC\":\"2031-05-02T19:20:00Z\"\(fields),\"stages\":[\(stages)]\(extra)}"
    }

    private static func receipt(_ sha: String) -> String {
        "{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"width\":800,\"height\":600},\"hints\":{},\"alreadyStored\":true,\"solveJobId\":\"\(jobId(sha))\"}"
    }

    static let decoding = store + ",{\"stage\":\"decode\",\"unit\":\"frames\",\"done\":0,\"total\":1}"
    static let detecting = store + ",{\"stage\":\"decode\",\"unit\":\"frames\",\"done\":1,\"total\":1},{\"stage\":\"detect\",\"unit\":\"frames\",\"done\":0,\"total\":null}"
    static let matching = store + ",{\"stage\":\"decode\",\"unit\":\"frames\",\"done\":1,\"total\":1},{\"stage\":\"detect\",\"unit\":\"frames\",\"done\":1,\"total\":1},"
        + "{\"stage\":\"solve\",\"unit\":\"candidates\",\"done\":40,\"total\":120,\"pass\":{\"number\":2,\"label\":\"phone_photo_generic\"}}"

    /// A laptop whose start answers at once with a queued job (202), which then runs: decode, detect, a search on its
    /// second pass, then `final`, one read at a time.
    private func laptop(sha: String, solution: JSONValue, final: String = "succeeded", startStatus: Int = 202,
                        startJob: String? = nil) -> LoopbackHTTPServer.Responder {
        let reads = Counter()
        let solutionText = String(decoding: JSONText.data(solution), as: UTF8.self)
        return { request in
            switch (request.method, request.path) {
            case ("POST", "/api/ingest/\(sha)/solve?wait=false"):
                return .json(startStatus, startJob ?? Self.job(sha, "queued", extra: ",\"queueAhead\":1"))
            case ("GET", "/api/ingest/\(sha)"):
                return .json(200, Self.receipt(sha))
            case ("GET", "/api/ingest/jobs/\(Self.jobId(sha))"):
                switch reads.next() {
                case 1: return .json(200, Self.job(sha, "running", stages: Self.decoding))
                case 2: return .json(200, Self.job(sha, "running", stages: Self.detecting))
                case 3: return .json(200, Self.job(sha, "running", stages: Self.matching))
                default: return .json(200, Self.job(sha, final, stages: final == "failed" ? Self.store : Self.matching))
                }
            case ("GET", "/api/ingest/\(sha)/solution"):
                return .json(200, solutionText)
            default:
                return .json(404, "{\"detail\":\"Not Found\"}")
            }
        }
    }

    private func solve(_ server: LoopbackHTTPServer, sha: String, pollSec: Double = 0.02, solveTimeoutSec: Double = 10) async throws
        -> (SolveState, [SolveState]) {
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        let log = Log<SolveState>()
        let last = await within(20, otherwise: SolveState.failed(.timedOut)) {
            await SolveClient(address: address, solveTimeoutSec: solveTimeoutSec, readTimeoutSec: 5, pollIntervalSec: pollSec)
                .solve(sha256: sha) { log.append($0) }
        }
        return (last, log.all)
    }

    func testFollowsTheJobTheStartAnswersThenShowsTheResultAsTheServerReportsIt() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution))
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        XCTAssertEqual(states.first, .starting)
        let running = states.compactMap { state -> SolveJob? in if case .running(let job) = state { job } else { nil } }
        XCTAssertEqual(running.map(\.state), [.queued, .running, .running, .running])
        XCTAssertEqual(running.first?.queueAhead, 1)
        XCTAssertEqual(running[2].stages.last?.stage, .detect)
        XCTAssertNil(running[2].stages.last?.total, "a stage whose total the server does not know stays unknown")
        XCTAssertEqual(running.last?.stages.last?.pass, SolveJob.Pass(number: 2, label: .phonePhotoGeneric))
        XCTAssertEqual(running.last?.producer, SolveJob.Producer(name: "albedian.solve_chain", revision: 4))
        XCTAssertFalse(zip(states, states.dropFirst()).contains { $0 == $1 }, "no two reports in a row are the same job")
        guard case .solved(let job, let readout, let frame, _) = last else { return XCTFail("not solved: \(last)") }
        XCTAssertEqual(job.state, .succeeded)
        XCTAssertEqual(readout.status, .calibrated)
        XCTAssertEqual(readout.counts, [.observed: 3, .catalogMatched: 3, .predicted: 1, .simulated: 1])
        XCTAssertEqual(frame, StoredFrame(widthPx: 800, heightPx: 600, orientation: .upright))
        XCTAssertEqual(states.last, last)
        let requests = server.requests
        XCTAssertEqual(requests.filter { $0.method == "POST" }.map(\.path), ["/api/ingest/\(sha)/solve?wait=false"], "one start, answered at once")
        XCTAssertTrue(requests.allSatisfy { $0.headers["user-agent"] == CaptureUploader.userAgent })
        XCTAssertTrue(requests.allSatisfy { $0.body.isEmpty })
        XCTAssertTrue(requests.filter { $0.method == "GET" }.allSatisfy { $0.headers["content-type"] == nil })
        XCTAssertEqual(requests.filter { $0.path == "/api/ingest/jobs/\(Self.jobId(sha))" }.count, 4, "the job followed by the id the start gave")
    }

    func testAFailedJobEndsWithTheJobAndItsOwnSentenceAndAsksForNoSolution() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, final: "failed"))
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        guard case .failed(.jobDidNotSucceed(let job)) = last else { return XCTFail("\(last)") }
        XCTAssertEqual(job.error, "The decoder could not read the file.")
        XCTAssertFalse(server.requests.contains { $0.path.hasSuffix("/solution") })
    }

    func testAJobTheServerStoppedEndsWithItsSentence() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, final: "interrupted"))
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        guard case .failed(.jobDidNotSucceed(let job)) = last else { return XCTFail("\(last)") }
        XCTAssertEqual(job.state, .interrupted)
        XCTAssertEqual(job.error, "The server stopped before this job finished.")
    }

    func testAJobAlreadySolvedIsTakenAsTheStartAnswersItWithNoJobRead() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, startStatus: 200,
                                                              startJob: Self.job(sha, "succeeded", stages: Self.matching)))
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        guard case .solved = last else { return XCTFail("\(last)") }
        XCTAssertFalse(states.contains { if case .running = $0 { true } else { false } })
        XCTAssertFalse(server.requests.contains { $0.path.hasPrefix("/api/ingest/jobs/") })
    }

    func testAJoinedRunningJobIsFollowedFromWhereItIs() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, startStatus: 200,
                                                              startJob: Self.job(sha, "running", stages: Self.decoding)))
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        guard case .solved = last else { return XCTFail("\(last)") }
        guard case .running(let first)? = states.dropFirst().first else { return XCTFail("\(states)") }
        XCTAssertEqual(first.state, .running)
    }

    func testARecordWrittenBeforeTheRunnerIsStillRead() async throws {
        // A succeeded solve today's code ran before the runner (a 16-character id, no kind): the web reads it too
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let old = "{\"id\":\"job_\(sha.prefix(16))\",\"sourceAssetSha256\":\"\(sha)\",\"state\":\"succeeded\",\"createdAtUTC\":\"2031-05-02T19:20:00Z\","
            + "\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:41Z\",\"solutionId\":\"sol_\(sha.prefix(16))\",\"stages\":[\(Self.store)]}"
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, startStatus: 200, startJob: old))
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        guard case .solved(let job, _, _, _) = last else { return XCTFail("\(last)") }
        XCTAssertNil(job.kind)
    }

    func testAPhotoTheLaptopDoesNotHoldIsRefusedWithItsWords() async throws {
        let sha = String(repeating: "ab", count: 32)
        let server = try LoopbackHTTPServer { _ in .json(404, "{\"detail\":\"no file with that hash is stored\"}") }
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        XCTAssertEqual(last, .failed(.refused(status: 404, detail: "no file with that hash is stored")))
    }

    func testAJobOrSolutionForAnotherPhotoIsNeverShown() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let other = String(repeating: "cd", count: 32)
        // The start answers with another photo's job
        let wrongJob = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(202, Self.job(other, "queued")) : .json(404, "{}")
        }
        defer { wrongJob.stop() }
        let (first, firstStates) = try await solve(wrongJob, sha: sha)
        XCTAssertEqual(first, .failed(.jobForAnotherPhoto))
        XCTAssertFalse(firstStates.contains { if case .running = $0 { true } else { false } })
        // The solution names another photo
        var foreign = solution
        foreign.set(["sourceSha256"], .string(other))
        let wrongSolution = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: foreign))
        defer { wrongSolution.stop() }
        let (second, _) = try await solve(wrongSolution, sha: sha)
        guard case .failed(.unreadableReply) = second else { return XCTFail("\(second)") }
    }

    func testAFollowedReadOfAnotherJobOrPhotoEndsTheWaitAndIsNeverShown() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let other = String(repeating: "cd", count: 32)
        for answer in [Self.job(other, "running", id: Self.jobId(sha)), Self.job(sha, "running", id: "job_" + String(repeating: "e", count: 32))] {
            let mine = laptop(sha: sha, solution: solution)
            let server = try LoopbackHTTPServer { request in
                request.method == "GET" && request.path == "/api/ingest/jobs/\(Self.jobId(sha))" ? .json(200, answer) : mine(request)
            }
            defer { server.stop() }
            let (last, states) = try await solve(server, sha: sha)
            XCTAssertEqual(last, .failed(.jobForAnotherPhoto))
            XCTAssertEqual(states.filter { if case .running = $0 { true } else { false } }.count, 1, "only the start's own job")
        }
    }

    func testAReceiptForAnotherPhotoGivesNoResult() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let other = String(repeating: "a", count: 64)
        let mine = laptop(sha: sha, solution: solution)
        let server = try LoopbackHTTPServer { request in
            if request.method == "GET", request.path == "/api/ingest/\(sha)" {
                return .json(200, "{\"sourceAsset\":{\"sha256\":\"\(other)\",\"width\":800,\"height\":600},\"hints\":{},\"alreadyStored\":true}")
            }
            return mine(request)
        }
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        XCTAssertEqual(last, .failed(.unreadableReply(nil)))
    }

    func testAReplyThatIsNotAJobIsUnreadable() async throws {
        let sha = String(repeating: "ab", count: 32)
        for body in ["{\"state\":\"succeeded\"}", Self.job(sha, "running", extra: ",\"graphId\":\"graph_0123456789abcdef\""),
                     Self.job(sha, "queued").replacingOccurrences(of: "\"kind\":\"solve\"", with: "\"kind\":\"restoration\"")] {
            let server = try LoopbackHTTPServer { request in request.method == "POST" ? .json(202, body) : .json(404, "{}") }
            defer { server.stop() }
            let (last, _) = try await solve(server, sha: sha)
            guard case .failed(.unreadableReply) = last else { return XCTFail("\(last)") }
        }
    }

    func testADroppedReadIsTriedAgainAndALaptopGoneForTenReadsEndsTheWait() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let reads = Counter()
        let mine = laptop(sha: sha, solution: solution)
        let flaky = try LoopbackHTTPServer { request in
            if request.path == "/api/ingest/jobs/\(Self.jobId(sha))", reads.next() <= 3 { return .json(503, "{\"detail\":\"busy\"}") }
            return mine(request)
        }
        defer { flaky.stop() }
        let (recovered, _) = try await solve(flaky, sha: sha)
        guard case .solved = recovered else { return XCTFail("\(recovered)") }
        // Six dropped reads, then one answered, again and again: never ten in a row, so the wait goes on to the end
        let spotty = Counter()
        let mine2 = laptop(sha: sha, solution: solution)
        let patchy = try LoopbackHTTPServer { request in
            if request.path == "/api/ingest/jobs/\(Self.jobId(sha))", spotty.next() % 7 != 0 { return .json(503, "{\"detail\":\"busy\"}") }
            return mine2(request)
        }
        defer { patchy.stop() }
        let (lasted, _) = try await solve(patchy, sha: sha)
        guard case .solved = lasted else { return XCTFail("\(lasted)") }
        let gone = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(202, Self.job(sha, "queued")) : .json(503, "{\"detail\":\"busy\"}")
        }
        defer { gone.stop() }
        let (last, _) = try await solve(gone, sha: sha)
        XCTAssertEqual(last, .failed(.refused(status: 503, detail: "busy")))
        XCTAssertEqual(gone.requests.filter { $0.path.hasPrefix("/api/ingest/jobs/") }.count, SolveClient.followFailuresAllowed)
    }

    func testAJobThatNeverEndsIsLeftAfterTheTimeAllowedAndNotCanceled() async throws {
        let sha = String(repeating: "ab", count: 32)
        let server = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(202, Self.job(sha, "queued")) : .json(200, Self.job(sha, "running", stages: Self.decoding))
        }
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha, pollSec: 0.05, solveTimeoutSec: 0.5)
        XCTAssertEqual(last, .failed(.timedOut))
        XCTAssertFalse(server.requests.contains { $0.path.hasSuffix("/cancel") })
    }

    func testALaptopThatCannotBeReachedSaysSo() async throws {
        let server = try LoopbackHTTPServer { _ in .json(200, "{}") }
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        server.stop()
        try await Task.sleep(nanoseconds: 100_000_000)
        let last = await within(20, otherwise: SolveState.failed(.timedOut)) {
            await SolveClient(address: address, solveTimeoutSec: 5, readTimeoutSec: 2, pollIntervalSec: 0.05)
                .solve(sha256: String(repeating: "ab", count: 32)) { _ in }
        }
        guard case .failed(.networkError) = last else { return XCTFail("\(last)") }
    }

    func testRefusesToStartForAHashThatIsNotASha256() async throws {
        let server = try LoopbackHTTPServer { _ in .json(200, "{}") }
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: String(repeating: "AB", count: 32))
        XCTAssertEqual(last, .failed(.notPermitted))
        XCTAssertEqual(states, [.failed(.notPermitted)], "not even starting is reported")
        XCTAssertTrue(server.requests.isEmpty)
    }

    /// The gate lets through the solve's four requests, each only for a contract SHA-256 or job id, and only to the
    /// paired endpoint: the start only in its at-once form; nothing else.
    func testTheGateLetsThroughOnlyTheSolvesFourRequests() throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        let sha = String(repeating: "ab", count: 32)
        for (method, path) in [("POST", "/api/ingest/\(sha)/solve?wait=false"), ("GET", "/api/ingest/\(sha)"), ("GET", "/api/ingest/\(sha)/solution"),
                               ("GET", "/api/ingest/jobs/job_\(sha.prefix(16))"), ("GET", "/api/ingest/jobs/job_\(sha)")] {
            XCTAssertEqual(CaptureUploader.permits(method: method, path: path, to: address),
                           ApprovedRequest(endpoint: address.endpoint, method: method, path: path), "\(method) \(path)")
        }
        XCTAssertEqual(SolveClient.startPath(sha), "/api/ingest/\(sha)/solve?wait=false")
        let refused: [(String, String)] = [
            ("POST", "/api/ingest/\(sha)/solve"), ("POST", "/api/ingest/\(sha)/solve?wait=true"), ("POST", "/api/ingest/\(sha)/solve?wait=false&x=1"),
            ("POST", "/api/ingest/jobs/job_\(sha.prefix(16))/cancel"),
            ("GET", "/api/ingest/\(sha)/solve"), ("POST", "/api/ingest/\(sha)/solution"), ("PUT", "/api/ingest/\(sha)/solve"),
            ("GET", "/api/ingest/\(sha.uppercased())"), ("GET", "/api/ingest/\(String(repeating: "0", count: 64))/solution"),
            ("GET", "/api/ingest/jobs/job_\(sha.prefix(15))"), ("GET", "/api/ingest/jobs/job_\(sha)0"), ("GET", "/api/ingest/jobs/JOB_\(sha.prefix(16))"),
            ("GET", "/api/ingest/jobs/job_\(sha.prefix(16).uppercased())"), ("GET", "/api/ingest/jobs/"), ("GET", "/api/ingest/jobs"),
            ("GET", "/api/ingest/\(sha)/original"), ("GET", "/api/ingest/\(sha)/pointing"), ("GET", "/api/media/discover"),
            ("GET", "/api/ingest/\(sha)?x=1"), ("GET", "/api/ingest/\(sha)/solution/x"), ("DELETE", "/api/ingest/\(sha)"),
        ]
        for (method, path) in refused { XCTAssertNil(CaptureUploader.permits(method: method, path: path, to: address), "\(method) \(path)") }
    }

    /// A GET carries no body and no Content-Type; the start's POST carries an empty body.
    func testWritesAGetWithNoBodyAndNoContentType() throws {
        let endpoint = LaptopEndpoint(host: "192.168.1.20", port: 5173)
        let get = String(decoding: HTTPMessage.request(method: "GET", path: "/api/ingest/x", endpoint: endpoint, contentType: nil, body: Data()), as: UTF8.self)
        XCTAssertEqual(get, "GET /api/ingest/x HTTP/1.1\r\nHost: 192.168.1.20:5173\r\nUser-Agent: \(CaptureUploader.userAgent)\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    }

    /// The phone follows a job longer than the runner's whole-job bound for a solve (420 s, the API note), at the pace
    /// the note gives (once a second), inside the transport's bound.
    func testTheJobIsFollowedLongerThanTheRunnersBoundAtTheNotesPace() {
        XCTAssertGreaterThan(SolveClient.defaultSolveTimeoutSec, 420)
        XCTAssertTrue(CaptureUploader.isValidTimeout(SolveClient.defaultSolveTimeoutSec))
        XCTAssertEqual(SolveClient.defaultPollIntervalSec, 1)
    }

    func testUnchangedOrDroppedJobReadsNeverReportTheSameRunningJobTwiceInARow() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let polls = Counter()
        let mine = laptop(sha: sha, solution: solution)
        let server = try LoopbackHTTPServer { request in
            if request.path == "/api/ingest/jobs/\(Self.jobId(sha))" {
                switch polls.next() {
                case 1: return .json(503, "{\"detail\":\"busy\"}")
                case 2: return .json(200, Self.job(sha, "queued", extra: ",\"queueAhead\":1"))
                case 3, 4: return .json(200, Self.job(sha, "running", stages: Self.decoding))
                default: return .json(200, Self.job(sha, "succeeded", stages: Self.matching))
                }
            }
            return mine(request)
        }
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        guard case .solved = last else { return XCTFail("\(last)") }
        XCTAssertFalse(zip(states, states.dropFirst()).contains { $0 == $1 }, "duplicate consecutive running states reported")
        let running = states.compactMap { state -> SolveJob? in if case .running(let job) = state { job } else { nil } }
        XCTAssertEqual(running.map(\.state), [.queued, .running])
    }

    func testConcludeRetriesTransientDropsOnReceiptAndSolutionAndRefusesClientErrorsAtOnce() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let receiptHits = Counter()
        let solutionHits = Counter()
        let mine = laptop(sha: sha, solution: solution, startStatus: 200, startJob: Self.job(sha, "succeeded", stages: Self.matching))
        let transient = try LoopbackHTTPServer { request in
            if request.path == "/api/ingest/\(sha)", receiptHits.next() == 1 {
                return .json(503, "{\"detail\":\"busy\"}")
            }
            if request.path == "/api/ingest/\(sha)/solution", solutionHits.next() <= 2 {
                return .json(502, "bad gateway")
            }
            return mine(request)
        }
        defer { transient.stop() }
        let (recovered, _) = try await solve(transient, sha: sha)
        guard case .solved = recovered else { return XCTFail("transient conclude drop not recovered: \(recovered)") }
        XCTAssertEqual(receiptHits.next() - 1, 2)
        XCTAssertEqual(solutionHits.next() - 1, 3)

        // Persistent 503 stops at concludeAttempts
        let persistentHits = Counter()
        let persistent = try LoopbackHTTPServer { request in
            if request.path == "/api/ingest/\(sha)/solution" {
                _ = persistentHits.next()
                return .json(503, "{\"detail\":\"busy\"}")
            }
            return mine(request)
        }
        defer { persistent.stop() }
        let (exhausted, _) = try await solve(persistent, sha: sha)
        XCTAssertEqual(exhausted, .failed(.refused(status: 503, detail: "busy")))
        XCTAssertEqual(persistentHits.next() - 1, SolveClient.concludeAttempts)

        // Client 404 is never retried
        let notFoundHits = Counter()
        let notFound = try LoopbackHTTPServer { request in
            if request.path == "/api/ingest/\(sha)/solution" {
                _ = notFoundHits.next()
                return .json(404, "{\"detail\":\"no solution\"}")
            }
            return mine(request)
        }
        defer { notFound.stop() }
        let (missing, _) = try await solve(notFound, sha: sha)
        XCTAssertEqual(missing, .failed(.refused(status: 404, detail: "no solution")))
        XCTAssertEqual(notFoundHits.next() - 1, 1)

        // Transport connection drops, timeouts and network errors on conclude are retried too
        struct FlakyTransport: LaptopTransport {
            let base = NetworkLaptopTransport()
            let counts: Counter
            func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double, bodyMaxBytes: Int,
                          onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
                let head = String(decoding: request.prefix(120), as: UTF8.self)
                if head.hasPrefix("GET /api/ingest/") {
                    switch counts.next() {
                    case 1: return .failure(.closed)
                    case 3: return .failure(.timedOut)
                    case 4: return .failure(.network(posixCode: 54))
                    default: break
                    }
                }
                return await base.exchange(request, to: endpoint, timeoutSec: timeoutSec, bodyMaxBytes: bodyMaxBytes, onSent: onSent)
            }
        }
        let cleanServer = try LoopbackHTTPServer(responder: mine)
        defer { cleanServer.stop() }
        let transportCounts = Counter()
        let client = SolveClient(address: try XCTUnwrap(LaptopAddress(cleanServer.baseURL)), solveTimeoutSec: 10,
                                 readTimeoutSec: 5, pollIntervalSec: 0.02, transport: FlakyTransport(counts: transportCounts))
        let transportEnd = await client.solve(sha256: sha) { _ in }
        guard case .solved = transportEnd else { return XCTFail("transport drop not recovered: \(transportEnd)") }
        XCTAssertEqual(transportCounts.next() - 1, 5)
    }
}
