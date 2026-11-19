import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// The solve and its result against a laptop on 127.0.0.1 that each test starts and stops (REQ-IOS-4.2 for the
/// solve, REQ-IOS-5.1). The laptop holds `POST /api/ingest/{sha256}/solve` until the solve ends
/// (`services/ingest/router.py`), so the client reads the job beside it, through the receipt's `solveJobId`, and every
/// state it reports is a job the laptop wrote: no step, count or percentage of its own.
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

    private static func job(_ sha: String, _ state: String, stages: String) -> String {
        let id = "job_" + sha.prefix(16)
        let times: String
        switch state {
        case "queued": times = ""
        case "running": times = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\""
        case "failed": times = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:02Z\",\"error\":\"The decoder could not read the file.\""
        default: times = ",\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:41Z\",\"solutionId\":\"sol_\(sha.prefix(16))\""
        }
        return "{\"id\":\"\(id)\",\"sourceAssetSha256\":\"\(sha)\",\"state\":\"\(state)\",\"createdAtUTC\":\"2031-05-02T19:20:00Z\"\(times),\"stages\":[\(stages)]}"
    }

    private static func receipt(_ sha: String, jobKnown: Bool) -> String {
        let job = jobKnown ? ",\"solveJobId\":\"job_\(sha.prefix(16))\"" : ""
        return "{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"width\":800,\"height\":600},\"hints\":{},\"alreadyStored\":true\(job)}"
    }

    /// A laptop that answers the solve after `holdSec`, while its job moves: queued at the first read, then decode, then
    /// detect, as the counter of job reads goes.
    private func laptop(sha: String, solution: JSONValue, holdSec: Double, final: String = "succeeded",
                        solveStatus: Int = 201) -> LoopbackHTTPServer.Responder {
        let reads = Counter()
        let finalStages = "{\"stage\":\"store\",\"unit\":\"bytes\",\"done\":10,\"total\":10},{\"stage\":\"decode\",\"unit\":\"frames\",\"done\":1,\"total\":1}"
        let finalJob = Self.job(sha, final, stages: final == "failed" ? "" : finalStages)
        let solutionText = String(decoding: JSONText.data(solution), as: UTF8.self)
        return { request in
            switch (request.method, request.path) {
            case ("POST", "/api/ingest/\(sha)/solve"):
                var response = LoopbackHTTPServer.Response.json(solveStatus, finalJob)
                response.delaySec = holdSec
                return response
            case ("GET", "/api/ingest/\(sha)"):
                return .json(200, Self.receipt(sha, jobKnown: true))
            case ("GET", "/api/ingest/jobs/job_\(sha.prefix(16))"):
                switch reads.next() {
                case 1: return .json(200, Self.job(sha, "queued", stages: "{\"stage\":\"store\",\"unit\":\"bytes\",\"done\":10,\"total\":10}"))
                case 2: return .json(200, Self.job(sha, "running", stages: "{\"stage\":\"store\",\"unit\":\"bytes\",\"done\":10,\"total\":10},{\"stage\":\"decode\",\"unit\":\"frames\",\"done\":0,\"total\":1}"))
                default: return .json(200, Self.job(sha, "running", stages: "{\"stage\":\"store\",\"unit\":\"bytes\",\"done\":10,\"total\":10},{\"stage\":\"decode\",\"unit\":\"frames\",\"done\":1,\"total\":1},{\"stage\":\"detect\",\"unit\":\"frames\",\"done\":0,\"total\":null}"))
                }
            case ("GET", "/api/ingest/\(sha)/solution"):
                return .json(200, solutionText)
            default:
                return .json(404, "{\"detail\":\"Not Found\"}")
            }
        }
    }

    private func solve(_ server: LoopbackHTTPServer, sha: String, pollSec: Double = 0.05) async throws -> (SolveState, [SolveState]) {
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        let log = Log<SolveState>()
        let last = await within(20, otherwise: SolveState.failed(.timedOut)) {
            await SolveClient(address: address, solveTimeoutSec: 10, readTimeoutSec: 5, pollIntervalSec: pollSec)
                .solve(sha256: sha) { log.append($0) }
        }
        return (last, log.all)
    }

    func testReportsTheJobAsItMovesThenTheResultAsTheServerReportsIt() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, holdSec: 1.0))
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        XCTAssertEqual(states.first, .starting)
        let running = states.compactMap { state -> SolveJob? in if case .running(let job) = state { job } else { nil } }
        XCTAssertGreaterThanOrEqual(running.count, 2, "the job read beside the held solve")
        XCTAssertEqual(running.first?.state, .queued)
        XCTAssertEqual(running.last?.stages.last?.stage, .detect)
        XCTAssertNil(running.last?.stages.last?.total, "a stage whose total the server does not know stays unknown")
        // No two reports in a row are the same job
        XCTAssertFalse(zip(states, states.dropFirst()).contains { $0 == $1 })
        guard case .solved(let job, let readout, let frame, _) = last else { return XCTFail("not solved: \(last)") }
        XCTAssertEqual(job.state, .succeeded)
        XCTAssertEqual(readout.status, .calibrated)
        XCTAssertEqual(readout.counts, [.observed: 3, .catalogMatched: 3, .predicted: 1, .simulated: 1])
        XCTAssertEqual(frame, StoredFrame(widthPx: 800, heightPx: 600, orientation: .upright))
        XCTAssertEqual(states.last, last)
        let requests = server.requests
        XCTAssertEqual(requests.filter { $0.method == "POST" }.count, 1, "one solve asked for")
        XCTAssertTrue(requests.allSatisfy { $0.headers["user-agent"] == CaptureUploader.userAgent })
        XCTAssertTrue(requests.filter { $0.method == "GET" }.allSatisfy { $0.body.isEmpty && $0.headers["content-type"] == nil })
    }

    func testAFailedJobEndsWithTheJobAndItsOwnSentenceAndAsksForNoSolution() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, holdSec: 0.2, final: "failed"))
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        guard case .failed(.jobDidNotSucceed(let job)) = last else { return XCTFail("\(last)") }
        XCTAssertEqual(job.error, "The decoder could not read the file.")
        XCTAssertFalse(server.requests.contains { $0.path.hasSuffix("/solution") })
    }

    func testAJobAlreadySolvedIsTakenAsTheLaptopAnswersIt() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let server = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: solution, holdSec: 0, solveStatus: 200))
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha, pollSec: 5)
        guard case .solved = last else { return XCTFail("\(last)") }
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
        // The solve answers with another photo's job
        let wrongJob = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(201, Self.job(other, "succeeded", stages: "")) : .json(404, "{}")
        }
        defer { wrongJob.stop() }
        let (first, _) = try await solve(wrongJob, sha: sha)
        XCTAssertEqual(first, .failed(.jobForAnotherPhoto))
        // The solution names another photo
        var foreign = solution
        foreign.set(["sourceSha256"], .string(other))
        let wrongSolution = try LoopbackHTTPServer(responder: laptop(sha: sha, solution: foreign, holdSec: 0))
        defer { wrongSolution.stop() }
        let (second, _) = try await solve(wrongSolution, sha: sha, pollSec: 5)
        guard case .failed(.unreadableReply) = second else { return XCTFail("\(second)") }
    }

    /// A job read beside the solve that is another photo's is never shown, and a receipt for another photo gives no frame.
    func testAJobOrReceiptReadForAnotherPhotoIsNeverUsed() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let other = String(repeating: "a", count: 64)
        let mine = laptop(sha: sha, solution: solution, holdSec: 0.6)
        let server = try LoopbackHTTPServer { request in
            if request.method == "GET", request.path == "/api/ingest/jobs/job_\(sha.prefix(16))" {
                return .json(200, Self.job(other, "running", stages: ""))
            }
            if request.method == "GET", request.path == "/api/ingest/\(sha)" {
                // The receipt names the job as usual, and another photo as its asset
                return .json(200, "{\"sourceAsset\":{\"sha256\":\"\(other)\",\"width\":800,\"height\":600},\"hints\":{},\"alreadyStored\":true,\"solveJobId\":\"job_\(sha.prefix(16))\"}")
            }
            return mine(request)
        }
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        XCTAssertFalse(states.contains { if case .running = $0 { true } else { false } })
        XCTAssertEqual(last, .failed(.unreadableReply(nil)))
    }

    func testAJobBesideTheSolveForAnotherPhotoIsNotReported() async throws {
        let solution = try calibrated()
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let other = String(repeating: "cd", count: 32)
        let mine = laptop(sha: sha, solution: solution, holdSec: 0.6)
        let server = try LoopbackHTTPServer { request in
            if request.method == "GET", request.path == "/api/ingest/jobs/job_\(sha.prefix(16))" {
                return .json(200, Self.job(other, "running", stages: ""))
            }
            return mine(request)
        }
        defer { server.stop() }
        let (last, states) = try await solve(server, sha: sha)
        XCTAssertFalse(states.contains { if case .running = $0 { true } else { false } })
        guard case .solved = last else { return XCTFail("\(last)") }
    }

    func testAReplyThatIsNotAJobIsUnreadable() async throws {
        let sha = String(repeating: "ab", count: 32)
        let server = try LoopbackHTTPServer { request in
            request.method == "POST" ? .json(201, "{\"state\":\"succeeded\"}") : .json(404, "{}")
        }
        defer { server.stop() }
        let (last, _) = try await solve(server, sha: sha)
        guard case .failed(.unreadableReply) = last else { return XCTFail("\(last)") }
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
    /// paired endpoint; nothing else.
    func testTheGateLetsThroughOnlyTheSolvesFourRequests() throws {
        let address = try XCTUnwrap(LaptopAddress("http://192.168.1.20:5173"))
        let sha = String(repeating: "ab", count: 32)
        for (method, path) in [("POST", "/api/ingest/\(sha)/solve"), ("GET", "/api/ingest/\(sha)"), ("GET", "/api/ingest/\(sha)/solution"),
                               ("GET", "/api/ingest/jobs/job_\(sha.prefix(16))"), ("GET", "/api/ingest/jobs/job_\(sha)")] {
            XCTAssertEqual(CaptureUploader.permits(method: method, path: path, to: address),
                           ApprovedRequest(endpoint: address.endpoint, method: method, path: path), "\(method) \(path)")
        }
        let refused: [(String, String)] = [
            ("GET", "/api/ingest/\(sha)/solve"), ("POST", "/api/ingest/\(sha)/solution"), ("PUT", "/api/ingest/\(sha)/solve"),
            ("GET", "/api/ingest/\(sha.uppercased())"), ("GET", "/api/ingest/\(String(repeating: "0", count: 64))/solution"),
            ("GET", "/api/ingest/jobs/job_\(sha.prefix(15))"), ("GET", "/api/ingest/jobs/job_\(sha)0"), ("GET", "/api/ingest/jobs/JOB_\(sha.prefix(16))"),
            ("GET", "/api/ingest/jobs/job_\(sha.prefix(16).uppercased())"), ("GET", "/api/ingest/jobs/"), ("GET", "/api/ingest/jobs"),
            ("GET", "/api/ingest/\(sha)/original"), ("GET", "/api/ingest/\(sha)/pointing"), ("GET", "/api/media/discover"),
            ("GET", "/api/ingest/\(sha)?x=1"), ("GET", "/api/ingest/\(sha)/solution/x"), ("DELETE", "/api/ingest/\(sha)"),
        ]
        for (method, path) in refused { XCTAssertNil(CaptureUploader.permits(method: method, path: path, to: address), "\(method) \(path)") }
    }

    /// A GET carries no body and no Content-Type; the solve's POST carries an empty body.
    func testWritesAGetWithNoBodyAndNoContentType() throws {
        let endpoint = LaptopEndpoint(host: "192.168.1.20", port: 5173)
        let get = String(decoding: HTTPMessage.request(method: "GET", path: "/api/ingest/x", endpoint: endpoint, contentType: nil, body: Data()), as: UTF8.self)
        XCTAssertEqual(get, "GET /api/ingest/x HTTP/1.1\r\nHost: 192.168.1.20:5173\r\nUser-Agent: \(CaptureUploader.userAgent)\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    }

    /// The solve's time allowed covers the laptop's own limit with room for decoding (`SOLVE_TIME_LIMIT_SEC = 180` in
    /// `services/ingest/jobs.py`), and stays inside the transport's bound.
    func testTheSolveIsAllowedLongerThanTheLaptopsOwnLimit() {
        XCTAssertGreaterThan(SolveClient.defaultSolveTimeoutSec, 180)
        XCTAssertTrue(CaptureUploader.isValidTimeout(SolveClient.defaultSolveTimeoutSec))
        XCTAssertGreaterThan(SolveClient.defaultPollIntervalSec, 0)
    }
}
