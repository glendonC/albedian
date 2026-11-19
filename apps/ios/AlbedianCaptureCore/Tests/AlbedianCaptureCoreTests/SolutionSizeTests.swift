import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// mobile-4 v2 (the audit of 5d6dcace): a solution of real size reaches the result. A photo with a time and a place
/// solves to about 1.3 MB (the audit's synthetic starfield, 1,300,568 bytes) and a 12 MP night photo to 35 MB (the CC0
/// photo rp-b-06, 35,154,051 bytes), far past the 65,536 bytes the receipts are read within, so the solution's read has
/// a bound of its own and the receipts and jobs keep theirs.
final class SolutionSizeTests: XCTestCase {
    private func calibrated() throws -> JSONValue {
        try XCTUnwrap(try ContractFixture.load("observation_solution_contract")["valid"]?["calibrated"])
    }

    /// The calibrated fixture with its observed findings repeated until the body passes `bytes`, each a copy with its
    /// own id, so the counts are known.
    private func padded(to bytes: Int) throws -> (JSONValue, observed: Int) {
        let base = try calibrated()
        let findings = try XCTUnwrap(base["findings"]?.arrayValue)
        let observedFinding = try XCTUnwrap(findings.first { $0["evidenceClass"]?.stringValue == "observed" })
        var extra: [JSONValue] = []
        let one = JSONText.data(observedFinding).count
        for index in 0..<(bytes / one + 1) {
            var copy = observedFinding
            copy.set(["id"], .string("finding-observed-copy-\(index)"))
            extra.append(copy)
        }
        var value = base
        value.set(["findings"], .array(findings + extra))
        let observed = findings.filter { $0["evidenceClass"]?.stringValue == "observed" }.count + extra.count
        return (value, observed)
    }

    func testTheSolutionsReadHasItsOwnBoundAboveTheLargestMeasuredSolution() {
        XCTAssertEqual(HTTPReplyLimits.bodyMaxBytes, 65_536, "receipts and jobs keep their bound")
        XCTAssertEqual(SolveClient.solutionMaxBytes, 64 * 1_048_576)
        XCTAssertGreaterThan(SolveClient.solutionMaxBytes, 35_154_051)
    }

    func testTheReaderTakesABodyUpToTheBoundItIsGivenAndNotOneBytePast() {
        let body = String(repeating: "a", count: 70_000)
        let reply = Data("HTTP/1.1 200 OK\r\nContent-Length: 70000\r\n\r\n\(body)".utf8)
        if case .reply = HTTPMessage.read(reply) { XCTFail("the default bound took 70,000 bytes") }
        guard case .reply(let read) = HTTPMessage.read(reply, bodyMaxBytes: 70_000) else { return XCTFail("refused at its bound") }
        XCTAssertEqual(read.body.count, 70_000)
        if case .reply = HTTPMessage.read(reply, bodyMaxBytes: 69_999) { XCTFail("took a body one byte past its bound") }
    }

    /// The length a reply states, known as soon as its head is whole, so the connection waits for the body without
    /// reading the bytes again on every piece.
    func testTheStatedLengthIsKnownFromTheHeadAlone() {
        let head = Data("HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n".utf8)
        XCTAssertEqual(HTTPMessage.wholeLength(head + Data(repeating: 0x61, count: 10), bodyMaxBytes: 2000), head.count + 1000)
        XCTAssertNil(HTTPMessage.wholeLength(Data("HTTP/1.1 200 OK\r\nContent-Len".utf8), bodyMaxBytes: 2000), "the head is not whole")
        XCTAssertNil(HTTPMessage.wholeLength(head, bodyMaxBytes: 999), "past the bound: the full read refuses it")
        let interim = Data("HTTP/1.1 100 Continue\r\n\r\n".utf8) + head
        XCTAssertEqual(HTTPMessage.wholeLength(interim, bodyMaxBytes: 2000), interim.count + 1000)
        // A redirect is answered by its status at once, so its stated body is never waited for
        XCTAssertNil(HTTPMessage.wholeLength(Data("HTTP/1.1 302 Found\r\nContent-Length: 10\r\n\r\n".utf8), bodyMaxBytes: 2000))
        XCTAssertNil(HTTPMessage.wholeLength(Data("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 10\r\n\r\n".utf8), bodyMaxBytes: 2000))
    }

    func testASolutionOfRealSizeReachesTheResult() async throws {
        let (solution, observed) = try padded(to: 1_400_000)
        let body = JSONText.data(solution)
        XCTAssertGreaterThan(body.count, 1_300_000)
        let sha = try XCTUnwrap(solution["sourceSha256"]?.stringValue)
        let stages = "{\"stage\":\"store\",\"unit\":\"bytes\",\"done\":10,\"total\":10}"
        let job = "{\"id\":\"job_\(sha.prefix(16))\",\"sourceAssetSha256\":\"\(sha)\",\"state\":\"succeeded\",\"createdAtUTC\":\"2031-05-02T19:20:00Z\","
            + "\"startedAtUTC\":\"2031-05-02T19:20:01Z\",\"finishedAtUTC\":\"2031-05-02T19:20:41Z\",\"solutionId\":\"sol_\(sha.prefix(16))\",\"stages\":[\(stages)]}"
        let receipt = "{\"sourceAsset\":{\"sha256\":\"\(sha)\",\"width\":800,\"height\":600},\"hints\":{},\"alreadyStored\":true}"
        let server = try LoopbackHTTPServer { request in
            switch (request.method, request.path) {
            case ("POST", "/api/ingest/\(sha)/solve?wait=false"): return .json(200, job)
            case ("GET", "/api/ingest/\(sha)"): return .json(200, receipt)
            case ("GET", "/api/ingest/\(sha)/solution"): return LoopbackHTTPServer.Response(status: 200, body: body)
            default: return .json(404, "{}")
            }
        }
        defer { server.stop() }
        let address = try XCTUnwrap(LaptopAddress(server.baseURL))
        let last = await within(30, otherwise: SolveState.failed(.timedOut)) {
            await SolveClient(address: address, solveTimeoutSec: 20, readTimeoutSec: 20, pollIntervalSec: 5).solve(sha256: sha) { _ in }
        }
        guard case .solved(_, let readout, _, _) = last else { return XCTFail("\(last)") }
        XCTAssertEqual(readout.counts[.observed], observed)
        XCTAssertEqual(readout.counts[.catalogMatched], 3)
    }

    /// Audit note a: a calibrated solution whose quality does not say it passed held-out verification draws no match.
    func testAMatchOnASolutionWithNoVerificationFlagIsRefused() throws {
        let base = try calibrated()
        let sha = try XCTUnwrap(base["sourceSha256"]?.stringValue)
        var value = base
        value.set(["quality", "passedIndependentVerification"], nil)
        if case .success = SolutionReader.read(value, sha256: sha) { XCTFail("a match with the flag left out") }
        value.set(["quality", "passedIndependentVerification"], .int(1))
        if case .success = SolutionReader.read(value, sha256: sha) { XCTFail("a match with the flag written as 1") }
    }
}
