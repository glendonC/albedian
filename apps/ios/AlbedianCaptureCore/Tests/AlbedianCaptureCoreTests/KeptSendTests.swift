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
        let end = await KeptSend.run(hashes, held: { _ in false }, send: { sha in log.sent.append(sha); return self.done(sha) },
                                     startSolve: { log.started.append($0); return true }, progress: { log.progress.append($0) })
        XCTAssertEqual(end, .allTried)
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
            let end = await KeptSend.run(hashes, held: { _ in false }, send: { sha in
                log.sent.append(sha)
                return sha == self.hashes[1] ? failure : self.done(sha)
            }, startSolve: { log.started.append($0); return true }, progress: { log.progress.append($0) })
            XCTAssertEqual(end, .stopped(sha256: hashes[1], last: failure), "\(failure)")
            XCTAssertEqual(log.sent, Array(hashes.prefix(2)), "the ones after it not tried: \(failure)")
            XCTAssertEqual(log.started, [hashes[0]], "no solve for the photo that did not go: \(failure)")
            XCTAssertEqual(log.progress, [1], "\(failure)")
        }
    }

    @MainActor
    func testAPhotoThatCanNoLongerBeReadIsPassedOver() async {
        let log = Log()
        let end = await KeptSend.run(hashes, held: { _ in false }, send: { sha in
            log.sent.append(sha)
            return sha == self.hashes[0] ? nil : self.done(sha)
        }, startSolve: { log.started.append($0); return true }, progress: { log.progress.append($0) })
        XCTAssertEqual(end, .allTried)
        XCTAssertEqual(log.started, Array(hashes.suffix(2)))
        XCTAssertEqual(log.progress, [1, 2])
    }

    /// The ledger kept on the phone, as the app records a counted send: an upload that went is held; the laptop taking
    /// the solve's ask makes the photo done.
    @MainActor
    private func run(_ ledger: SendLedger, _ queue: [String], _ log: Log, upload: @escaping (String) -> UploadState,
                     takes: @escaping (String) -> Bool) async -> KeptSend.End {
        await KeptSend.run(queue, held: { ledger.state(of: $0) == .stored }, send: { sha in
            log.sent.append(sha)
            let last = upload(sha)
            ledger.record(last, for: sha)
            return last
        }, startSolve: { sha in
            log.started.append(sha)
            guard takes(sha) else { return false }
            ledger.recordSolveTaken(sha)
            return true
        }, progress: { log.progress.append($0) })
    }

    private func tempFolder() throws -> URL {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("kept-send-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    /// The app closed and opened between two counted sends: the ledger says which are done, the second send takes only
    /// the rest, and every photo's solve is taken once over both.
    @MainActor
    func testAcrossTheAppClosedAndOpenedEachSolveIsAskedForOnce() async throws {
        let folder = try tempFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let log = Log()
        let fails = UploadState.failed(.networkError(phase: .photo, posixCode: 61))
        let end = await run(SendLedger(folder: folder), SendLedger(folder: folder).notSent(among: hashes), log,
                            upload: { $0 == self.hashes[1] ? fails : self.done($0) }, takes: { _ in true })
        XCTAssertEqual(end, .stopped(sha256: hashes[1], last: fails))
        let reopened = SendLedger(folder: folder)
        XCTAssertEqual(reopened.notSent(among: hashes), Array(hashes.suffix(2)), "the one done is not sent again")
        let again = await run(reopened, reopened.notSent(among: hashes), log, upload: { self.done($0) }, takes: { _ in true })
        XCTAssertEqual(again, .allTried)
        XCTAssertEqual(log.started, hashes, "each solve asked for once over both sends")
        XCTAssertEqual(SendLedger(folder: folder).notSent(among: hashes), [])
    }

    /// AUDIT-C's F1: the photo goes but the ask for its solve is not taken (the laptop gone between the two, or a refusal):
    /// it stays among those home counts, held on the laptop; after the app is closed and opened, the next counted send
    /// does not send it again, asks once, and once the laptop takes it the photo is done.
    @MainActor
    func testAnAskNotTakenLeavesThePhotoCountedAndTheNextSendAsksAgainWithoutSendingIt() async throws {
        let folder = try tempFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let log = Log()
        let end = await run(SendLedger(folder: folder), hashes, log, upload: { self.done($0) }, takes: { $0 != self.hashes[1] })
        XCTAssertEqual(end, .allTried, "every photo tried")
        XCTAssertEqual(log.progress, [1, 2], "the one whose ask was not taken is not counted done")
        let reopened = SendLedger(folder: folder)
        XCTAssertEqual(reopened.state(of: hashes[1]), .stored, "held on the laptop, its solve not taken")
        XCTAssertEqual(reopened.notSent(among: hashes), [hashes[1]], "still among those home counts")
        let sentBefore = log.sent.count
        let again = await run(reopened, reopened.notSent(among: hashes), log, upload: { self.done($0) }, takes: { _ in true })
        XCTAssertEqual(again, .allTried)
        XCTAssertEqual(log.sent.count, sentBefore, "not sent again: the laptop holds it")
        XCTAssertEqual(log.started.filter { $0 == hashes[1] }.count, 2, "asked once more, once")
        XCTAssertEqual(SendLedger(folder: folder).notSent(among: hashes), [], "done once the laptop took it")
    }

    /// The single Send's wait counts its photo done when the laptop took the ask: a job read for it, whether it is
    /// running, solved or ended without a solution; not while starting, nor after a failure before any job.
    func testTheLaptopTookTheAskOnceItAnsweredWithAJob() throws {
        let sha = hashes[0]
        let job = try XCTUnwrap(try? SolveJobReader.read(JSONText.read(Data(SolveClientTests.job(sha, "running").utf8))).get())
        XCTAssertTrue(SolveState.running(job).laptopTookTheAsk)
        XCTAssertTrue(SolveState.failed(.jobDidNotSucceed(job)).laptopTookTheAsk)
        XCTAssertFalse(SolveState.starting.laptopTookTheAsk)
        for failure in [SolveFailure.networkError(posixCode: 61), .timedOut, .connectionClosed, .notThePairedPeer, .notPermitted,
                        .refused(status: 404, detail: nil), .redirected(status: 301), .unreadableReply(nil), .jobForAnotherPhoto] {
            XCTAssertFalse(SolveState.failed(failure).laptopTookTheAsk, "\(failure)")
        }
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

    /// The app's counted send is this run: each kept photo sent as a single Send sends it unless the laptop holds it,
    /// recorded in the ledger, the pairing kept after a send that went, each ask for a solve made by the start alone and
    /// the photo done only when the laptop took it, and the screen left on the photo that did not go; the single Send's
    /// wait counts its photo done the same way (read from the source: the app's model does not run here).
    func testTheAppsCountedSendIsTheRun() throws {
        let raw = try String(contentsOf: repositoryRoot.appendingPathComponent("apps/ios/AlbedianCapture/Screens/CompanionModel.swift"), encoding: .utf8)
        let model = raw.filter { !$0.isWhitespace }
        XCTAssertTrue(model.contains("letend=awaitKeptSend.run(queue,held:{self.ledger.state(of:$0)==.stored},send:{shain"), "the counted send is KeptSend.run")
        XCTAssertTrue(model.contains("self.ledger.record(last,for:sha)"))
        XCTAssertTrue(model.contains("ifcase.solve?=SendEnd.after(last){self.keepLaptop(address)}"), "T5: the pairing kept after a send that went (through keepLaptop, firstuse-connect-one)")
        XCTAssertTrue(model.contains("startSolve:{shainguardcase.success=awaitsolver.start(sha256:sha)else{returnfalse}self.ledger.recordSolveTaken(sha)returntrue}"),
                      "each ask by the start alone, the photo done only when the laptop took it")
        XCTAssertTrue(model.contains("case.stopped(letsha256,letlast):self.shot=lastTried"), "T6: the screen on the photo that did not go")
        XCTAssertTrue(model.contains("ifstate.laptopTookTheAsk{self?.ledger.recordSolveTaken(sha256)}"), "the single Send's photo done the same way")
        XCTAssertFalse(model.contains("sendNextKept"), "no other counted send left")
    }
}
