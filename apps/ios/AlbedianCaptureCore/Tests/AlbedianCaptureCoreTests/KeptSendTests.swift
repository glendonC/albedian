import Foundation
import XCTest
@testable import AlbedianCaptureCore

/// kept-send-solves-one (DECIDED-BY-LEAD 24 Nov 18:28): the counted send of kept photos starts each photo's solve on the
/// laptop, once, without waiting; a photo that does not go starts none and stays kept; across the app closed and opened
/// between two sends, each photo's solve is still asked for once.
final class KeptSendTests: XCTestCase {
    private let hashes = ["a", "b", "c"].map { PhotoHash.sha256Hex(Data($0.utf8)) }

    /// What a run did, in order.
    @MainActor
    private final class Log {
        var sent: [String] = []
        var started: [String] = []
        var progress: [Int] = []
    }

    private func done(_ sha: String) -> UploadState { .done(sha256: sha, photoAlreadyStored: false, recordAccepted: true) }

    @MainActor
    func testEachKeptPhotoThatGoesHasItsSolveStartedOnceInOrder() async {
        let log = Log()
        let end = await KeptSend.run(hashes, send: { sha in log.sent.append(sha); return self.done(sha) },
                                     startSolve: { log.started.append($0) }, progress: { log.progress.append($0) })
        XCTAssertEqual(end, .allSent)
        XCTAssertEqual(log.sent, hashes)
        XCTAssertEqual(log.started, hashes, "each solve asked for once, in the order sent")
        XCTAssertEqual(log.progress, [1, 2, 3])
    }

    @MainActor
    func testAPhotoThatDoesNotGoEndsTheRunStartsNoSolveAndStaysKept() async {
        let failures: [UploadState] = [.failed(.networkError(phase: .photo, posixCode: 61)), .failed(.timedOut(phase: .record)),
                                       .failed(.refused(phase: .photo, status: 415, detail: "not a JPEG")), .failed(.recordNamesAnotherPhoto)]
        for failure in failures {
            let log = Log()
            let end = await KeptSend.run(hashes, send: { sha in
                log.sent.append(sha)
                return sha == self.hashes[1] ? failure : self.done(sha)
            }, startSolve: { log.started.append($0) }, progress: { log.progress.append($0) })
            XCTAssertEqual(end, .stopped(sha256: hashes[1], last: failure), "\(failure)")
            XCTAssertEqual(log.sent, Array(hashes.prefix(2)), "the ones after it not tried: \(failure)")
            XCTAssertEqual(log.started, [hashes[0]], "no solve for the photo that did not go: \(failure)")
            XCTAssertEqual(log.progress, [1], "\(failure)")
        }
    }

    @MainActor
    func testAPhotoThatCanNoLongerBeReadIsPassedOver() async {
        let log = Log()
        let end = await KeptSend.run(hashes, send: { sha in
            log.sent.append(sha)
            return sha == self.hashes[0] ? nil : self.done(sha)
        }, startSolve: { log.started.append($0) }, progress: { log.progress.append($0) })
        XCTAssertEqual(end, .allSent)
        XCTAssertEqual(log.started, Array(hashes.suffix(2)))
        XCTAssertEqual(log.progress, [1, 2])
    }

    /// The app closed and opened between two counted sends: the ledger kept on the phone says which went, the second send
    /// takes only the rest, and every photo's solve is asked for once over both.
    @MainActor
    func testAcrossTheAppClosedAndOpenedEachSolveIsAskedForOnce() async throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("kept-send-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: folder) }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let log = Log()
        let first = SendLedger(folder: folder)
        let end = await KeptSend.run(first.notSent(among: hashes), send: { sha in
            let last = sha == self.hashes[1] ? UploadState.failed(.networkError(phase: .photo, posixCode: 61)) : self.done(sha)
            first.record(last, for: sha)
            return last
        }, startSolve: { log.started.append($0) }, progress: { _ in })
        XCTAssertEqual(end, .stopped(sha256: hashes[1], last: .failed(.networkError(phase: .photo, posixCode: 61))))
        // Closed and opened: a new ledger on the same folder
        let reopened = SendLedger(folder: folder)
        XCTAssertEqual(reopened.notSent(among: hashes), Array(hashes.suffix(2)), "the one that went is not sent again")
        let again = await KeptSend.run(reopened.notSent(among: hashes), send: { sha in
            let last = self.done(sha)
            reopened.record(last, for: sha)
            return last
        }, startSolve: { log.started.append($0) }, progress: { _ in })
        XCTAssertEqual(again, .allSent)
        XCTAssertEqual(log.started, hashes, "each solve asked for once over both sends")
        XCTAssertEqual(SendLedger(folder: folder).notSent(among: hashes), [])
    }

    /// The start alone: one `POST /api/ingest/{sha256}/solve?wait=false` to the paired laptop, its job read and checked to
    /// name this photo, nothing followed.
    final class OneReply: LaptopTransport, @unchecked Sendable {
        private let lock = NSLock()
        private var taken: [Data] = []
        let result: Result<HTTPReply, ExchangeFailure>
        init(_ result: Result<HTTPReply, ExchangeFailure>) { self.result = result }
        var requests: [Data] { lock.withLock { taken } }
        func exchange(_ request: Data, to endpoint: LaptopEndpoint, timeoutSec: Double,
                      onSent: @escaping @Sendable (Int64) -> Void) async -> Result<HTTPReply, ExchangeFailure> {
            lock.withLock { taken.append(request) }
            return result
        }
    }

    func testStartAsksOnceAndGivesTheJobItStarted() async throws {
        let address = try XCTUnwrap(LaptopAddress("http://127.0.0.1:8187"))
        let sha = hashes[0]
        func client(_ transport: OneReply) -> SolveClient { SolveClient(address: address, transport: transport) }
        let queued = OneReply(.success(HTTPReply(status: 202, headers: [], body: Data(SolveClientTests.job(sha, "queued").utf8))))
        let started = await client(queued).start(sha256: sha)
        guard case .success(let job) = started else { return XCTFail("\(started)") }
        XCTAssertEqual(job.sourceAssetSha256, sha)
        XCTAssertEqual(queued.requests.count, 1, "asked once, not followed")
        XCTAssertEqual(queued.requests.first, HTTPMessage.request(method: "POST", path: "/api/ingest/\(sha)/solve?wait=false",
                                                                  endpoint: address.endpoint, contentType: nil, body: Data()))
        let other = OneReply(.success(HTTPReply(status: 202, headers: [], body: Data(SolveClientTests.job(hashes[1], "queued").utf8))))
        guard case .failure(.jobForAnotherPhoto) = await client(other).start(sha256: sha) else { return XCTFail("another photo's job") }
        let refused = OneReply(.success(HTTPReply(status: 404, headers: [], body: Data("{\"detail\":\"Not Found\"}".utf8))))
        guard case .failure(.refused(status: 404, detail: "Not Found")) = await client(refused).start(sha256: sha) else { return XCTFail("refused") }
        let gone = OneReply(.failure(.network(posixCode: 61)))
        guard case .failure(.networkError(posixCode: 61)) = await client(gone).start(sha256: sha) else { return XCTFail("unreachable") }
        let unread = OneReply(.success(HTTPReply(status: 202, headers: [], body: Data("[]".utf8))))
        guard case .failure(.unreadableReply) = await client(unread).start(sha256: sha) else { return XCTFail("unreadable") }
        let never = OneReply(.failure(.closed))
        guard case .failure(.notPermitted) = await client(never).start(sha256: "not-a-hash") else { return XCTFail("not a hash") }
        XCTAssertEqual(never.requests.count, 0, "nothing sent for a name that is not a photo's hash")
    }

    /// The app's counted send is this run: each kept photo sent as a single Send sends it, recorded in the ledger, and each
    /// one that went asked to solve by the start alone (read from the source: the app's model does not run here).
    func testTheAppsCountedSendIsTheRun() throws {
        let raw = try String(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture/Screens/CompanionModel.swift"), encoding: .utf8)
        let model = raw.filter { !$0.isWhitespace }
        XCTAssertTrue(model.contains("letend=awaitKeptSend.run(queue,send:{shain"), "the counted send is KeptSend.run")
        XCTAssertTrue(model.contains("self.ledger.record(last,for:sha)"))
        XCTAssertTrue(model.contains("startSolve:{shain_=awaitsolver.start(sha256:sha)}"), "each solve asked for by the start alone")
        XCTAssertFalse(model.contains("sendNextKept"), "no other counted send left")
    }
}
